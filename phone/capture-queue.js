/* Explora phone: capture queue (PRD FR-24, stretch).
 *
 * Photos wait in this phone's browser storage (IndexedDB) until the person taps
 * "Send to laptop". A photo counts as sent only after the laptop's reply says it
 * was added (or was already there). Anything else leaves it queued, so a dropped
 * connection can never lose a photo.
 *
 * It reuses the laptop's existing API; nothing is re-implemented here:
 *   GET  /api/health                 is the laptop reachable?
 *   POST /api/walks {date}           the walk for the photo's day (one walk per day)
 *   POST /api/walks/{id}/photos      import; the laptop de-duplicates by content hash,
 *                                    so a retry after a lost reply is harmless
 *
 * Rules kept from the PRD:
 * - The phone never identifies anything. Queued photos read "Waiting to be
 *   identified. Take it home to learn more."
 * - Originals are sent unchanged (no resizing here), so the laptop's duplicate
 *   check still recognises a photo that also arrives by USB or Nearby Share.
 * - No counters, progress bars or "incomplete" language. The phone's own gallery
 *   keeps the originals; this queue is a copy.
 * - No background sending: the phone starts the connection, when the person asks.
 * - No external requests, fonts or libraries. Works offline.
 *
 * Browser use:  ExploraCapture.mount(element)
 * Node tests:   const { createQueue } = require("./capture-queue.js")
 */
(function (root, factory) {
  const api = factory(root);
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.ExploraCapture = api;
})(typeof globalThis !== "undefined" ? globalThis : (typeof self !== "undefined" ? self : this), function (root) {
  "use strict";

  const DB_NAME = "explora-capture";
  const STORE = "queue";
  const MAX_BYTES = 30 * 1024 * 1024;           // same limit as the laptop (server/main.py)
  const KEEP_SENT_MS = 7 * 24 * 3600 * 1000;    // sent records (no photo data) are tidied after a week
  const HEALTH_TIMEOUT_MS = 5000;
  const UPLOAD_TIMEOUT_MS = 120000;
  const WAITING_TEXT = "Waiting to be identified. Take it home to learn more.";

  // ------------------------------------------------------------------ helpers

  const req = (r) => new Promise((resolve, reject) => {
    r.onsuccess = () => resolve(r.result);
    r.onerror = () => reject(r.error);
  });

  const txDone = (tx) => new Promise((resolve, reject) => {
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
    tx.onabort = () => reject(tx.error || new Error("transaction aborted"));
  });

  function newId() {
    // crypto.randomUUID only exists on secure pages; the fallback keeps the queue working on plain http.
    if (root.crypto && typeof root.crypto.randomUUID === "function") return root.crypto.randomUUID();
    return Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 10);
  }

  function localDate(ms) {
    const d = new Date(ms);
    const p = (n) => String(n).padStart(2, "0");
    return d.getFullYear() + "-" + p(d.getMonth() + 1) + "-" + p(d.getDate());
  }

  function fingerprint(file) {
    return [file.name, file.size, file.lastModified].join("|");
  }

  async function detail(res) {
    try {
      const j = await res.json();
      if (j && typeof j.detail === "string") return j.detail;
    } catch (_) { /* not JSON */ }
    return "The laptop could not accept this photo (" + res.status + ").";
  }

  // ------------------------------------------------------------------ the queue

  function createQueue(opts) {
    opts = opts || {};
    const idb = opts.idb || root.indexedDB;
    const doFetch = opts.fetch || (root.fetch && root.fetch.bind(root));
    const now = opts.now || Date.now;
    const base = opts.base || "";
    let dbPromise = null;
    let syncing = false;

    function db() {
      if (!dbPromise) {
        dbPromise = new Promise((resolve, reject) => {
          const open = idb.open(DB_NAME, 1);
          open.onupgradeneeded = () => {
            open.result.createObjectStore(STORE, { keyPath: "id" });
          };
          open.onsuccess = () => resolve(open.result);
          open.onerror = () => reject(open.error);
        });
      }
      return dbPromise;
    }

    async function all() {
      const d = await db();
      const items = await req(d.transaction(STORE, "readonly").objectStore(STORE).getAll());
      return items.sort((a, b) => a.capturedAt - b.capturedAt || a.addedAt - b.addedAt);
    }

    async function put(item) {
      const d = await db();
      const tx = d.transaction(STORE, "readwrite");
      tx.objectStore(STORE).put(item);
      await txDone(tx);
    }

    async function get(id) {
      const d = await db();
      return req(d.transaction(STORE, "readonly").objectStore(STORE).get(id));
    }

    async function remove(id) {
      const d = await db();
      const tx = d.transaction(STORE, "readwrite");
      tx.objectStore(STORE).delete(id);
      await txDone(tx);
    }

    /** Add one photo. Returns {ok:true,id} or {ok:false,reason} (never throws for bad input). */
    async function add(file) {
      if (!file || !/^image\//.test(file.type || "")) return { ok: false, reason: "That is not a photo." };
      if (!file.size) return { ok: false, reason: "That photo is empty." };
      if (file.size > MAX_BYTES) return { ok: false, reason: "That photo is too large." };
      const fp = fingerprint(file);
      if ((await all()).some((i) => i.fingerprint === fp)) return { ok: false, reason: "Already added." };
      // Copy the bytes now: a File can stop being readable if the original is moved or deleted.
      const blob = new Blob([await file.arrayBuffer()], { type: file.type });
      const item = {
        id: newId(), fingerprint: fp, name: String(file.name || "photo").slice(0, 200), type: file.type,
        size: file.size, capturedAt: file.lastModified || now(), addedAt: now(),
        status: "queued", reason: null, blob: blob, syncedAt: null,
      };
      await put(item);
      return { ok: true, id: item.id };
    }

    /** Put a failed photo back in line. */
    async function retry(id) {
      const item = await get(id);
      if (item && item.status === "failed") await put(Object.assign(item, { status: "queued", reason: null }));
    }

    /** Tidy old "sent" records. They hold no photo data. */
    async function prune() {
      const cutoff = now() - KEEP_SENT_MS;
      for (const i of await all()) if (i.status === "synced" && i.syncedAt && i.syncedAt < cutoff) await remove(i.id);
    }

    async function timedFetch(url, init, ms) {
      const ctl = typeof AbortController !== "undefined" ? new AbortController() : null;
      const timer = ctl ? setTimeout(() => ctl.abort(), ms) : null;
      try {
        return await doFetch(base + url, Object.assign({}, init, ctl ? { signal: ctl.signal } : {}));
      } finally {
        if (timer) clearTimeout(timer);
      }
    }

    /**
     * Send queued photos to the laptop, one at a time. Resolves with
     *   {status: "done" | "unreachable" | "stopped" | "busy", sent, failed}
     * "unreachable": the laptop could not be reached; nothing changed.
     * "stopped": the connection or laptop failed part-way; what was confirmed stays sent, the rest stays queued.
     */
    async function sync() {
      if (syncing) return { status: "busy", sent: 0, failed: 0 };
      syncing = true;
      let sent = 0, failed = 0;
      try {
        try {
          const h = await timedFetch("/api/health", {}, HEALTH_TIMEOUT_MS);
          if (!h.ok) throw new Error("health " + h.status);
        } catch (_) {
          return { status: "unreachable", sent, failed };
        }

        const walkIds = new Map();                       // day -> walk id, one lookup per day per run
        for (const item of (await all()).filter((i) => i.status === "queued")) {
          const fail = async (reason) => {
            await put(Object.assign(item, { status: "failed", reason: reason }));
            failed++;
          };
          try {
            const day = localDate(item.capturedAt);
            let walkId = walkIds.get(day);
            if (walkId === undefined) {
              const wr = await timedFetch("/api/walks", {
                method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ date: day }),
              }, HEALTH_TIMEOUT_MS);
              if (wr.status >= 500) return { status: "stopped", sent, failed };
              if (!wr.ok) { await fail(await detail(wr)); continue; }
              walkId = (await wr.json()).walk.id;
              walkIds.set(day, walkId);
            }

            const form = new FormData();
            form.append("files", item.blob, item.name);
            form.append("captured_ms", String(item.capturedAt));
            const res = await timedFetch("/api/walks/" + walkId + "/photos", { method: "POST", body: form }, UPLOAD_TIMEOUT_MS);
            if (res.status >= 500) return { status: "stopped", sent, failed };
            if (!res.ok) { await fail(await detail(res)); continue; }

            // The only thing that marks a photo as sent: the laptop saying it has it.
            const data = await res.json();
            const r = data && Array.isArray(data.results) ? data.results[0] : null;
            if (r && (r.status === "added" || r.status === "duplicate")) {
              await put(Object.assign(item, { status: "synced", reason: null, blob: null, syncedAt: now() }));
              sent++;
            } else if (r && r.status === "error") {
              await fail(r.reason || "The laptop could not read this photo.");
            } else {
              return { status: "stopped", sent, failed };    // reply not understood: not confirmed, stays queued
            }
          } catch (_) {
            return { status: "stopped", sent, failed };      // network dropped: stays queued
          }
        }
        return { status: "done", sent, failed };
      } finally {
        syncing = false;
      }
    }

    return { add, list: all, remove, retry, prune, sync, WAITING_TEXT };
  }

  // ------------------------------------------------------------------ offline support

  async function registerOffline() {
    if (!root.navigator || !("serviceWorker" in root.navigator)) {
      return { ok: false, reason: root.isSecureContext === false ? "insecure" : "unsupported" };
    }
    try {
      await root.navigator.serviceWorker.register("/phone/service-worker.js", { scope: "/phone/" });
      return { ok: true };
    } catch (_) {
      return { ok: false, reason: "failed" };
    }
  }

  // ------------------------------------------------------------------ small UI

  const CSS = [
    ":root{--bg:#f6f1e7;--fg:#2b2a26;--muted:#6d675a;--card:#fffdf8;--line:#e0d8c6;--accent:#3f6b3a}",
    "@media (prefers-color-scheme:dark){:root{--bg:#1f211c;--fg:#ece7da;--muted:#a49e8e;--card:#2a2d26;--line:#3b3f35;--accent:#8fbf86}}",
    "body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.5 Georgia,'Times New Roman',serif;",
    "padding:env(safe-area-inset-top) env(safe-area-inset-right) env(safe-area-inset-bottom) env(safe-area-inset-left)}",
    ".ex{max-width:32rem;margin:0 auto;padding:1.25rem}",
    ".ex h1{font-size:1.6rem;margin:.2rem 0}.ex p{margin:.4rem 0}.ex .muted{color:var(--muted);font-size:.92rem}",
    ".ex .row{display:flex;flex-wrap:wrap;gap:.6rem;margin:1rem 0}",
    ".ex button,.ex label.btn{font:inherit;color:var(--fg);background:var(--card);border:1px solid var(--line);",
    "border-radius:.5rem;padding:.65rem 1rem;min-height:2.75rem;box-sizing:border-box;cursor:pointer}",
    ".ex .primary{background:var(--accent);color:var(--bg);border-color:var(--accent)}",
    ".ex button:disabled{opacity:.5}.ex input[type=file]{display:none}",
    ".ex ul{list-style:none;padding:0;margin:.8rem 0}",
    ".ex li{display:flex;gap:.8rem;align-items:flex-start;background:var(--card);border:1px solid var(--line);",
    "border-radius:.5rem;padding:.6rem;margin:.5rem 0}",
    ".ex li img{width:4.5rem;height:4.5rem;object-fit:cover;border-radius:.3rem;flex:none}",
    ".ex li .t{flex:1;min-width:0;word-break:break-word}.ex li .a{display:flex;gap:.4rem;margin-top:.3rem}",
    ".ex li .a button{padding:.3rem .7rem;min-height:2.25rem}",
    ".ex [role=status]{min-height:1.5rem;margin:.6rem 0}",
  ].join("");

  function h(tag, attrs, children) {
    const el = document.createElement(tag);
    Object.keys(attrs || {}).forEach((k) => {
      if (attrs[k] === null || attrs[k] === undefined) return;
      if (k === "text") el.textContent = attrs[k];
      else if (k.slice(0, 2) === "on") el.addEventListener(k.slice(2), attrs[k]);
      else el.setAttribute(k, attrs[k]);
    });
    (children || []).forEach((c) => el.appendChild(c));
    return el;
  }

  function mount(el, opts) {
    const q = createQueue(opts);
    const style = document.createElement("style");
    style.textContent = CSS;
    document.head.appendChild(style);
    el.classList.add("ex");

    let urls = [];
    let message = "";
    let offlineNote = "";
    let busy = false;

    const say = (text) => { message = text; render(); };

    async function addFiles(input) {
      const files = Array.from(input.files || []);
      input.value = "";
      let skipped = 0;
      for (const f of files) if (!(await q.add(f)).ok) skipped++;
      say(skipped ? "Some photos were skipped (not a photo, too large, or already here)." : "");
    }

    async function send() {
      busy = true;
      say("Sending to your laptop…");
      const r = await q.sync();
      busy = false;
      if (r.status === "unreachable") say("Couldn't reach your laptop. Your photos are safe on this phone. Try again on your home Wi-Fi.");
      else if (r.status === "stopped") say("The connection dropped. Anything not confirmed is still waiting here.");
      else if (r.status === "busy") say("Already sending.");
      else if (r.sent) say("Sent. Open Explora on your laptop to see them.");
      else say(r.failed ? "Some photos could not be sent." : "");
    }

    let renderSeq = 0;
    async function render() {
      const mine = ++renderSeq;
      const items = await q.list();
      if (mine !== renderSeq) return;                 // a newer render is already on its way
      urls.forEach((u) => URL.revokeObjectURL(u));
      urls = [];
      const waiting = items.filter((i) => i.status !== "synced");
      const sent = items.filter((i) => i.status === "synced");

      const picker = (label, capture, multiple) => {
        const attrs = { type: "file", accept: "image/*", onchange: (e) => addFiles(e.target) };
        if (capture) attrs.capture = "environment";
        if (multiple) attrs.multiple = "multiple";
        return h("label", { class: "btn" }, [document.createTextNode(label), h("input", attrs)]);
      };

      const list = waiting.map((i) => {
        const url = i.blob ? URL.createObjectURL(i.blob) : "";
        if (url) urls.push(url);
        const actions = [h("button", { type: "button", text: "Remove", onclick: async () => { await q.remove(i.id); render(); } })];
        if (i.status === "failed") actions.unshift(h("button", { type: "button", text: "Try again", onclick: async () => { await q.retry(i.id); render(); } }));
        return h("li", {}, [
          h("img", { src: url, alt: i.name }),
          h("div", { class: "t" }, [
            h("div", { text: i.name }),
            h("div", { class: "muted", text: i.status === "failed" ? i.reason : WAITING_TEXT }),
            h("div", { class: "a" }, actions),
          ]),
        ]);
      });

      const kids = [
        h("h1", { text: "Explora" }),
        h("p", { text: "Photos wait here until you send them home to your laptop." }),
        h("p", { class: "muted", text: "Nothing is identified on this phone. Sending uses your home Wi-Fi and isn't encrypted, so only send on a network you trust." }),
        h("div", { class: "row" }, [
          picker("Take a photo", true, false),
          picker("Add from gallery", false, true),
          h("button", { type: "button", class: "primary", text: "Send to laptop", onclick: send, disabled: (busy || !waiting.some((i) => i.status === "queued")) ? "disabled" : null }),
        ]),
        h("div", { role: "status", "aria-live": "polite", text: message }),
        waiting.length ? h("ul", {}, list) : h("p", { class: "muted", text: "Nothing waiting. Your phone's gallery keeps your originals." }),
      ];
      if (sent.length) {
        kids.push(h("details", {}, [
          h("summary", { text: "Sent to your laptop" }),
          h("ul", {}, sent.map((i) => h("li", {}, [h("div", { class: "t muted", text: i.name })]))),
        ]));
      }
      if (offlineNote) kids.push(h("p", { class: "muted", text: offlineNote }));
      el.replaceChildren.apply(el, kids);
    }

    // Ask the browser not to evict the queue, then make the page available offline.
    if (root.navigator && root.navigator.storage && root.navigator.storage.persist) {
      root.navigator.storage.persist().catch(() => {});
    }
    registerOffline().then((r) => {
      if (!r.ok) {
        offlineNote = r.reason === "insecure"
          ? "This address isn't a secure page, so this phone can't keep Explora open offline. Your queue still works while the page is open."
          : "This browser can't keep Explora open offline. Your queue still works while the page is open.";
        render();
      }
    });
    q.prune().catch(() => {}).then(render);
    return q;
  }

  return { createQueue, mount, registerOffline, localDate, WAITING_TEXT };
});