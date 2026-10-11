// Tests for phone/capture-queue.js (the queue and sync logic, no browser needed).
//
// Run (Node 20+):
//   npm install --no-save fake-indexeddb
//   node --test tests/phone/*.test.js
//
// Optional: the Python test-suite does not depend on these.
const test = require("node:test");
const assert = require("node:assert/strict");
const { IDBFactory } = require("fake-indexeddb");
const { createQueue, localDate } = require("../../phone/capture-queue.js");

const DAY1 = new Date(2026, 9, 9, 10, 0, 0).getTime();    // local time, so localDate() is stable anywhere
const DAY2 = new Date(2026, 9, 10, 10, 0, 0).getTime();

const photo = (name, ms = DAY1, type = "image/jpeg", bytes = 8) =>
  new File([new Uint8Array(bytes).fill(7)], name, { type, lastModified: ms });

const json = (status, body) => ({ ok: status >= 200 && status < 300, status, json: async () => body });

/** A pretend laptop. `photos(call)` decides the reply to each upload. */
function laptop(overrides = {}) {
  const calls = { walks: [], uploads: [] };
  const fetch = async (url, init = {}) => {
    if (overrides.down) throw new TypeError("network down");
    if (url === "/api/health") return json(200, { ok: true });
    if (url === "/api/walks") {
      const day = JSON.parse(init.body).date;
      calls.walks.push(day);
      if (overrides.walks) return overrides.walks(day);
      return json(200, { walk: { id: 100 + calls.walks.length, date: day } });
    }
    const m = url.match(/^\/api\/walks\/(\d+)\/photos$/);
    if (m) {
      const f = init.body.get("files");
      calls.uploads.push({ walkId: Number(m[1]), name: f.name, captured: init.body.get("captured_ms") });
      if (overrides.photos) return overrides.photos(calls.uploads.length, f.name);
      return json(200, { results: [{ status: "added", name: f.name }] });
    }
    throw new Error("unexpected " + url);
  };
  return { fetch, calls };
}

const make = (srv) => createQueue({ idb: new IDBFactory(), fetch: srv.fetch });
const statuses = async (q) => (await q.list()).map((i) => [i.name, i.status]);

test("add accepts photos and refuses non-photos, empty, huge and repeated files", async () => {
  const q = make(laptop());
  assert.equal((await q.add(photo("a.jpg"))).ok, true);
  assert.equal((await q.add(photo("a.jpg"))).reason, "Already added.");
  assert.equal((await q.add(photo("n.txt", DAY1, "text/plain"))).ok, false);
  assert.equal((await q.add(photo("e.jpg", DAY1, "image/jpeg", 0))).ok, false);
  const big = { type: "image/jpeg", size: 31 * 1024 * 1024, name: "b.jpg", lastModified: 1, arrayBuffer: async () => new ArrayBuffer(0) };
  assert.equal((await q.add(big)).reason, "That photo is too large.");
  assert.equal((await q.list()).length, 1);
});

test("a photo is stored as a queued copy with its own capture time", async () => {
  const q = make(laptop());
  await q.add(photo("a.jpg", DAY1));
  const [i] = await q.list();
  assert.equal(i.status, "queued");
  assert.equal(i.capturedAt, DAY1);
  assert.ok(i.blob && i.blob.size === 8);
});

test("sync marks photos sent only after the laptop confirms, and frees the photo data", async () => {
  const srv = laptop();
  const q = make(srv);
  await q.add(photo("a.jpg", DAY1));
  await q.add(photo("b.jpg", DAY1 + 1000));
  assert.deepEqual(await q.sync(), { status: "done", sent: 2, failed: 0 });
  const items = await q.list();
  assert.deepEqual(items.map((i) => i.status), ["synced", "synced"]);
  assert.ok(items.every((i) => i.blob === null && i.syncedAt));
});

test("photos go to the walk for their own day, one walk lookup per day", async () => {
  const srv = laptop();
  const q = make(srv);
  await q.add(photo("a.jpg", DAY1));
  await q.add(photo("b.jpg", DAY2));
  await q.add(photo("c.jpg", DAY1 + 5000));
  await q.sync();
  assert.deepEqual(srv.calls.walks, [localDate(DAY1), localDate(DAY2)]);
  const byName = Object.fromEntries(srv.calls.uploads.map((u) => [u.name, u]));
  assert.equal(byName["a.jpg"].walkId, byName["c.jpg"].walkId);
  assert.notEqual(byName["a.jpg"].walkId, byName["b.jpg"].walkId);
  assert.equal(byName["a.jpg"].captured, String(DAY1));
});

test("a duplicate reply counts as received (a retry after a lost reply is harmless)", async () => {
  const q = make(laptop({ photos: (n, name) => json(200, { results: [{ status: "duplicate", name }] }) }));
  await q.add(photo("a.jpg"));
  assert.equal((await q.sync()).sent, 1);
  assert.deepEqual(await statuses(q), [["a.jpg", "synced"]]);
});

test("laptop unreachable: nothing changes", async () => {
  const q = make(laptop({ down: true }));
  await q.add(photo("a.jpg"));
  assert.equal((await q.sync()).status, "unreachable");
  assert.deepEqual(await statuses(q), [["a.jpg", "queued"]]);
  assert.ok((await q.list())[0].blob);
});

test("connection drops part-way: confirmed photos stay sent, the rest stay queued", async () => {
  const q = make(laptop({
    photos: (n, name) => { if (n === 2) throw new TypeError("dropped"); return json(200, { results: [{ status: "added", name }] }); },
  }));
  for (const [i, n] of ["a.jpg", "b.jpg", "c.jpg"].entries()) await q.add(photo(n, DAY1 + i * 1000));
  const r = await q.sync();
  assert.deepEqual([r.status, r.sent], ["stopped", 1]);
  assert.deepEqual(await statuses(q), [["a.jpg", "synced"], ["b.jpg", "queued"], ["c.jpg", "queued"]]);
});

test("a photo the laptop cannot read is marked failed, and the others still go", async () => {
  const q = make(laptop({
    photos: (n, name) => json(200, { results: [name === "bad.jpg"
      ? { status: "error", name, reason: "not a readable image (UnidentifiedImageError)" } : { status: "added", name }] }),
  }));
  await q.add(photo("bad.jpg", DAY1));
  await q.add(photo("ok.jpg", DAY1 + 1000));
  assert.deepEqual(await q.sync(), { status: "done", sent: 1, failed: 1 });
  const [bad, ok] = await q.list();
  assert.deepEqual([bad.status, ok.status], ["failed", "synced"]);
  assert.match(bad.reason, /not a readable image/);
  await q.retry(bad.id);
  assert.equal((await q.list())[0].status, "queued");
});

test("a server error or an unclear reply never marks a photo as sent", async () => {
  for (const reply of [() => json(500, {}), () => json(200, {}), () => json(200, { results: [] })]) {
    const q = make(laptop({ photos: reply }));
    await q.add(photo("a.jpg"));
    assert.equal((await q.sync()).status, "stopped");
    assert.deepEqual(await statuses(q), [["a.jpg", "queued"]]);
  }
});

test("a rejected request (4xx) fails that photo with the laptop's message", async () => {
  const q = make(laptop({ walks: () => json(400, { detail: "That date is out of range." }) }));
  await q.add(photo("a.jpg"));
  assert.deepEqual(await q.sync(), { status: "done", sent: 0, failed: 1 });
  const [i] = await q.list();
  assert.deepEqual([i.status, i.reason], ["failed", "That date is out of range."]);
});

test("two syncs at once: the second is told to wait and nothing is sent twice", async () => {
  const srv = laptop();
  const q = make(srv);
  await q.add(photo("a.jpg"));
  const [a, b] = await Promise.all([q.sync(), q.sync()]);
  assert.deepEqual([a.status, b.status].sort(), ["busy", "done"]);
  assert.equal(srv.calls.uploads.length, 1);
});

test("remove and prune", async () => {
  let t = DAY1;
  const q = createQueue({ idb: new IDBFactory(), fetch: laptop().fetch, now: () => t });
  const { id } = await q.add(photo("a.jpg"));
  await q.add(photo("b.jpg", DAY1 + 1000));
  await q.remove(id);
  assert.deepEqual((await q.list()).map((i) => i.name), ["b.jpg"]);
  await q.sync();
  t += 8 * 24 * 3600 * 1000;
  await q.prune();
  assert.equal((await q.list()).length, 0);
});

test("nothing outside the laptop's own address is ever requested", async () => {
  const urls = [];
  const srv = laptop();
  const q = createQueue({ idb: new IDBFactory(), fetch: (u, i) => { urls.push(u); return srv.fetch(u, i); } });
  await q.add(photo("a.jpg"));
  await q.sync();
  assert.ok(urls.every((u) => u.startsWith("/api/")));
});