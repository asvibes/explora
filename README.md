# Explora

**A local-first nature-walk journal.** Photograph things on a walk, put the phone away, and at home let a local Gemma model (run through [Ollama](https://ollama.com)) *suggest* what each photo shows. You confirm, correct or set aside every suggestion, then arrange a scrapbook-style journal page for the walk.

> *The world is worth noticing.* Explora records curiosity instead of measuring productivity. There are no streaks, scores, percentages or "incomplete" walks.

- **Private by construction.** Photos and the database stay on your laptop. Identification runs on your own machine. The page footer says so: "Identified on your laptop. Nothing uploaded."
- **Honest about uncertainty.** The model only suggests. Wording follows its confidence, and no percentages are ever shown.
- **Works offline.** After you install the Python packages and pull a model, nothing needs a network. The web app loads no fonts, scripts or images from the internet.

> **Status.** The app works end to end, but the formal model evaluation is not finished. This README publishes **no accuracy numbers** because none have been verified. See [Evaluation](#evaluation) and [Limitations](#limitations).

---

## Contents

1. [Features](#features)
2. [How it works](#how-it-works)
3. [Tech stack](#tech-stack)
4. [Project structure](#project-structure)
5. [Setup](#setup)
6. [Local Gemma with Ollama](#local-gemma-with-ollama)
7. [Running the app](#running-the-app)
8. [Using the phone capture page (optional)](#using-the-phone-capture-page-optional)
9. [HTTP API](#http-api)
10. [Safety and privacy](#safety-and-privacy)
11. [Testing](#testing)
12. [Evaluation](#evaluation)
13. [Limitations](#limitations)
14. [Documentation](#documentation)
15. [License](#license)

---

## Features

## Photo Import: Laptop and Mobile

Explora supports two ways to bring photos into your nature journal: directly from your laptop or through the optional mobile capture page.

### Laptop — Main Journal

Import photos directly into Explora from your laptop.

* **Choose photos:** Select images using the file picker or drag and drop them into the web app.
* **Import multiple photos:** Upload up to 100 photos at once, with a maximum file size of 30 MB per photo.
* **Automatic duplicate detection:** Explora uses SHA-256 hashes of the original files to skip exact duplicates.
* **Privacy-conscious processing:** Imported photos are resized and re-encoded as JPEGs without EXIF metadata, including GPS coordinates.
* **Local identification:** Ask your locally running Gemma model to suggest what each photo shows.

### Mobile — Capture and Send

Use Explora's optional phone interface to capture photos while exploring outdoors and transfer them to your laptop later.

* **Take or select photos:** Use your phone's camera or choose existing photos from its gallery.
* **Queue photos locally:** Photos wait in the phone browser's IndexedDB storage until you choose to send them.
* **Send when ready:** Connect to your laptop and tap **Send to laptop** to transfer queued photos.
* **Handle interruptions:** A photo remains queued until the laptop confirms receipt, allowing you to retry after a failed transfer.
* **Identify at home:** The phone does not run the identification model. Photos are identified by Gemma on your laptop after import.

### How the two interfaces work together

1. Take photos during your walk using your phone.
2. Keep them in the mobile capture queue until you're ready to transfer them.
3. Connect your phone to your laptop using a supported connection method.
4. Send the photos to Explora running on your laptop.
5. Open the main journal, identify the imported photos with local Gemma, and confirm or correct the suggestions.
6. Arrange your discoveries on the journal page.

**The distinction:** The laptop runs the main journal, database, and local AI model. The phone page is a companion for capturing and transferring photos, not a separate copy of the complete journal.

**Privacy note:** The phone-to-laptop connection over ordinary Wi-Fi is unencrypted. Use it only on a trusted private network. Photos are stored on the laptop after import; the phone browser maintains its own capture queue.


**Walks.** One walk per day, with an optional typed place (never GPS), a habitat (`park`, `garden`, `campus`, `trail`, `backyard`) and a duration of 1 to 1440 minutes. A walk with no photos and no writing is perfectly valid. Streets and water edges are deliberately not offered as habitats.

**Photo import.**
- Pick files or drag and drop, up to 100 photos per upload and 30 MB each.
- Duplicates are skipped using a SHA-256 hash of the original bytes.
- Capture time comes from EXIF `DateTimeOriginal`, then EXIF `DateTime`, then the browser's `File.lastModified`, then the file's modified time, then now. The source is recorded and the date can be edited.
- Only a resized JPEG copy is stored (longest side 1024 px), re-encoded **without EXIF**, so GPS coordinates in your originals never enter the data folder.

**Identification (local Gemma via Ollama).**
- Photos are identified one at a time so each card updates as it finishes.
- Each result has a category, a name (as specific as the model can honestly be, possibly only a group such as "a swallowtail butterfly"), a confidence of High, Medium or Low, a short explanation and an optional "Did you know?" fact.
- Malformed output is never dropped or treated as confident. It becomes "Not sure what this is."

| Confidence | What you see | What you can do |
| --- | --- | --- |
| High | "Looks like a Blue Jay." | Confirm, Correct, Not this |
| Medium | "Might be a Blue Jay." | Confirm, Correct, Try again, Not this |
| Low, unknown or malformed | "Not sure what this is." | Try again, Save anyway, Not this |

A Low or unidentified result cannot be confirmed. Correcting a photo with your own words counts as confirmed by you. A confirmed photo is never re-identified.

**Journal.** An open-book spread for each walk:
- *Left page:* a photo of your own handwritten page. Upload, replace, rotate or remove it. Explora never reads, OCRs or scans it. Rotation is only a stored number, and the file is never rewritten.
- *Right page:* two Polaroid slots. Each holds one of that walk's photos and a caption of up to 200 characters (too long is refused, never silently cut). Either slot can be removed and brought back, so two, one or none are all fine. Tilt is up to 3 degrees and is derived from a stable id, so the page looks identical after every reload.
- *Navigation:* previous and next walk by date, with a short fade.

**Calendar.** A Sunday-first month grid on Home. Days with a walk show a small tilted Polaroid. If a walk has more than one photo, a "+N" badge opens a picker to choose which photo represents the day. The choice is saved in the database and survives restarts. When you haven't chosen, the thumbnail falls back to the photo in Polaroid slot 1, then the first confirmed photo, then the first photo.

**Firsts.** A quiet note when a *confirmed* photo is the first of its category (for example "First butterfly"). Category level only, one per category, never reassigned, and `other` never earns one. There are no points, levels or "next First" hints.

**Optional walk ideas.** Suggestions come from a fixed, reviewed list of photograph-only templates (`server/content/quest_templates.json`). Nothing is awarded automatically. An idea is done only when you tap "I did this", and it appears only on that walk. If the template file is missing or fails the safety scan, ideas switch off and the rest of the app keeps working.

**Collection.** Plain counts of confirmed discoveries by category. No percentage, target, rank or progress.

**Light and dark themes.** Cream paper on a green desk, or charcoal paper on a near-black desk. The choice is saved in the `theme` setting.

**Phone capture queue (optional).** A small installable page at `/phone` that holds photos on the phone until you tap "Send to laptop". The phone never identifies anything.

---

## How it works

```
 photos (file picker, drag and drop, or the phone page)
   |
   v
 importer.import_bytes
   |  SHA-256 of the original bytes -> skip if already imported
   |  capture time: EXIF > browser timestamp > file time > now
   |  resize to <= 1024 px, re-encode as JPEG with no EXIF
   v
 data/photos/<walk_id>/<hash>.jpg  +  a row in SQLite (status: imported)
   |
   v
 identify.identify --POST /api/chat--> local Ollama (Gemma)
   |  parse and validate JSON; malformed -> "not sure"
   v
 safety: unsafe reassurance removed from fact and explanation
   v
 status: identified   (wording and app warning are added when read)
   |
   v
 you: confirm | correct | Not this | Save anyway | Try again
   v
 confirmed -> collection counts, possible First, journal and calendar
```

Discovery states:

```
imported -> identified -> confirmed            (confirm, or correct)
                       -> rejected             (Not this)
                       -> saved_unidentified   (Save anyway)
```

`identify()` in `server/identify.py` is the **single path to the model**. The app and the evaluation scripts both call it, so the evaluation tests exactly what ships.

---

## Tech stack

| Layer | What is used |
| --- | --- |
| Language | Python 3.10 or newer |
| Web server | [FastAPI](https://fastapi.tiangolo.com/) served by [Uvicorn](https://www.uvicorn.org/); `python-multipart` for uploads |
| Storage | SQLite, one file (`explora.db`), WAL mode, foreign keys on; resized photos on disk |
| Images | [Pillow](https://python-pillow.org/) (EXIF rotation, resizing, re-encoding) |
| Model | Gemma through Ollama, called over HTTP with the Python standard library only (`urllib`) |
| Front end | Plain HTML, CSS and JavaScript ES modules. No framework, no build step, no libraries, no network calls |
| Phone page | Plain JavaScript, IndexedDB, and a service worker for offline use |
| Tests | `pytest`, with `httpx` for HTTP tests. Tests replace the model with a fake, so Ollama is not needed |

---

## Project structure

```text
explora/
├── README.md, LICENSE, pyproject.toml, .gitignore
├── data/                  # your database and photos (never committed)
├── docs/                  # architecture, decisions, evaluation, field test
├── eval/
│   ├── ground_truth.csv
│   ├── run_eval.py        # runs one model x one condition
│   ├── score.py           # scores results against the fixed pass bar
│   ├── prompts/           # v1.txt (development), frozen.txt (scored runs)
│   └── results/           # per-model, per-condition result CSVs
├── phone/                 # optional capture page: index.html, capture-queue.js,
│                          #   service-worker.js, manifest.json, icons/
├── samples/               # safe demo photos
├── server/
│   ├── main.py            # HTTP layer; wires everything together
│   ├── config.py          # Config and load_config() (paths, model, URLs)
│   ├── db.py              # SQLite connection, full schema, settings
│   ├── importer.py        # import, dedupe, capture time, resize
│   ├── identify.py        # prompt, image prep, Ollama call, output parsing
│   ├── walks.py           # walks, discoveries, confirm/correct/reject, collection
│   ├── journal.py         # left page and two Polaroids
│   ├── thumbnails.py      # calendar thumbnails
│   ├── firsts.py          # category-level Firsts
│   ├── quests.py          # optional walk ideas from reviewed templates
│   ├── safety.py          # app warnings and banned-phrase scans
│   ├── models.py          # statuses, habitats, confidence wording
│   └── content/           # quest_templates.json, banned_phrases.txt
├── tests/
└── web/
    ├── index.html         # shell, navigation, router mount
    ├── css/theme.css      # tokens for both themes, paper, Polaroids, journal
    ├── js/                # main (router), ui (shared helpers and photo picker),
    │                      #   dashboard, walk, journal
    └── assets/fonts/      # bundled fonts
```

---

## Setup

You need Python 3.10 or newer and [Ollama](https://ollama.com). Check Python with `python --version`.

**Windows (PowerShell)**

```powershell
git clone https://github.com/asvibes/explora.git
cd explora
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

**macOS or Linux**

```bash
git clone https://github.com/asvibes/explora.git
cd explora
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

The server needs `fastapi`, `uvicorn`, `python-multipart` and `Pillow`. The tests also need `httpx` and `pytest`. If `pip install -e ".[dev]"` does not pull these in, check `pyproject.toml` and install what is missing.

### Configuration

Everything is optional. Set these as environment variables:

| Variable | Default | Meaning |
| --- | --- | --- |
| `EXPLORA_DATA` | `<repo>/data` | Where the database and photos live |
| `EXPLORA_MODEL` | `gemma4:e2b` | Ollama model tag. Provisional until the evaluation picks a model |
| `EXPLORA_OLLAMA` | `http://localhost:11434` | Ollama address |
| `EXPLORA_PROMPT` | `eval/prompts/frozen.txt` | Prompt file. Falls back to `v1.txt` beside it if the frozen file is missing |

```powershell
$env:EXPLORA_MODEL = "gemma4:e4b"      # PowerShell
```
```bash
export EXPLORA_MODEL=gemma4:e4b        # macOS / Linux
```

---

## Local Gemma with Ollama

1. Install Ollama from <https://ollama.com> and start it. By default it listens on `http://localhost:11434`.
2. Pull a model. These are the two candidates the code and evaluation use:

   ```bash
   ollama pull gemma4:e2b
   ollama pull gemma4:e4b
   ollama list
   ```

   Pulling needs a network connection once. After that, identification talks only to your local Ollama.

**Which one?** `e2b` is smaller and most likely to fit on a small GPU. `e4b` is larger and may split between GPU and system RAM, which is slower but works with enough RAM. The project was built on an RTX 4050 with 6 GB of VRAM and 24 GB of RAM. The evaluation is designed to choose between them.

**What the app sends to Ollama** (`POST /api/chat`, `stream: false`):

- One user message containing the prompt and one base64 JPEG (EXIF-rotated, converted to RGB, longest side at most 768 px, JPEG quality 90).
- `format: "json"`, `think: false`, `keep_alive: "10m"`, and `options.num_predict: 400`. Temperature, `top_p` and `top_k` are left at the model's own defaults.
- If an Ollama build rejects the `think` field (HTTP 400 mentioning it), the call is retried once without it and the setting is recorded as `not-supported-by-server`.

**What gets stored with every result:** the model name, the settings as JSON, a prompt hash (first 12 hex characters of the prompt's SHA-256) and the time taken. This makes a result reproducible.

**If Ollama is not running:** the app still opens. Importing, the journal and the calendar work, and the Today's walk page shows a quiet banner saying the local model isn't running and your photos are safe. A photo whose identification fails keeps its status and offers "Try again". This is different from a "not sure" answer.

> `EXPLORA_OLLAMA` can point anywhere. If you set it to a remote host, your photos are sent there. The default is local.

---

## Running the app

From the repository root, with the virtual environment active:

```bash
python -m server.main              # http://127.0.0.1:8000, this laptop only
python -m server.main --port 8080
```

Open <http://127.0.0.1:8000>. The server binds to `127.0.0.1` and rejects other `Host` headers, which blocks DNS-rebinding tricks from web pages.

**A typical session**

1. Open **Today's walk**. Optionally type where you went, pick a habitat and a duration.
2. Click **Add photos** (or drag files onto the page). The summary says how many were added, skipped as duplicates, or could not be read.
3. Click **Identify new photos**. Cards update one by one. The first photo can take longer while the model loads.
4. Confirm, correct, try again, or keep photos without a name. Use **Put on the journal page** on a confirmed photo to fill a free Polaroid slot.
5. Open **Journal** to add a photo of your handwritten page and type captions.
6. Use **Home** to browse the calendar and choose which photo represents each day.

Data is saved in `data/explora.db` and `data/photos/` (or wherever `EXPLORA_DATA` points). It is private, so keep it out of version control.

---

## Using the phone capture page (optional)

The phone page (`/phone`) is a stretch feature. It lets you take or add photos on an Android phone and send them to the laptop later. Photos wait in the phone browser's storage (IndexedDB), and a photo counts as *sent* only after the laptop confirms it received it, so a dropped connection never loses a photo. The phone's own gallery keeps your originals. The phone never identifies anything: queued photos read "Waiting to be identified. Take it home to learn more."

Two ways to reach the laptop:

- **Over USB (recommended, secure).** With Android debugging enabled, run `adb reverse tcp:8000 tcp:8000`, then open `http://localhost:8000/phone/` on the phone. This counts as a secure context, so the service worker can register and the page opens offline.
- **Over home Wi-Fi.** Start the server with `python -m server.main --host 0.0.0.0` and open `http://<laptop-ip>:8000/phone/`. This turns the `Host` check off, and traffic is **unencrypted**, so use it only on a network you trust. Browsers refuse service workers on plain `http://<ip>` pages, so the page won't open offline there, but the queue still works while the page is open.

Originals are sent unchanged, and the laptop's duplicate check recognises a photo that also arrives some other way (USB copy, Nearby Share). The walk a photo lands in is chosen from the day it was taken.

---

## HTTP API

The web app is a client of this API. All routes are under `/api`. Errors return a plain-language `detail` message: 404 for something missing, 400 for bad input, 409 when identification is already running, 503 when the model or a needed file is unavailable.

| Area | Routes |
| --- | --- |
| Status and settings | `GET /health`, `GET /settings`, `PUT /settings` (`name`, `preferred_name`, `theme`, `journal_name`) |
| Walks | `GET /walks`, `POST /walks/today`, `POST /walks` (body `{date}`, used by the phone), `GET /walks/{id}`, `PATCH /walks/{id}` |
| Import | `POST /walks/{id}/photos` (multipart `files`, plus optional `captured_ms` matched by position), `GET /discoveries/{id}/photo` |
| Identification | `POST /walks/{id}/identify`, `POST /discoveries/{id}/identify` (also "Try again") |
| Decisions | `POST /discoveries/{id}/confirm`, `/correct` (`label`, optional `category`), `/reject`, `/save`; `PUT /discoveries/{id}/note`, `PUT /discoveries/{id}/captured-at` |
| Collection | `GET /collection` |
| Walk ideas | `GET /walks/{id}/quests`, `POST` and `DELETE /walks/{id}/quests/{template_id}/done` |
| Journal | `GET /walks/{id}/journal`; `GET`, `PUT`, `DELETE /walks/{id}/journal/left-page`; `POST .../left-page/rotate`; `PUT .../journal/polaroids/{slot}/photo`; `PUT .../polaroids/{slot}/caption`; `DELETE .../polaroids/{slot}`; `POST .../polaroids/{slot}/restore` |
| Firsts | `GET /firsts`, `GET /walks/{id}/firsts` |
| Calendar | `GET /calendar/YYYY-MM`, `PUT /walks/{id}/thumbnail` (body `{discovery_id}`) |

The `.../polaroids/...` journal routes sit under `/api/walks/{id}/journal/`. The handwritten page's URL includes a version taken from its file name, so a replaced page is never served from cache. `captured_ms` values outside the range from the year 2000 to one day in the future are ignored, and the import falls back to the file date.

---

## Safety and privacy

**Safety covers what Explora generates, not what you write.**

- **Prompt rules:** be honest about uncertainty, never invent a species, never call anything safe to eat or touch, never suggest touching or approaching.
- **Parsing:** malformed or contradictory output becomes "not sure". A result with no identification is forced to Low.
- **App-added warnings,** independent of what the model wrote, for fungi, reptiles, unknown organisms, malformed results, and Low-confidence living things. The front end shows the backend's warning text as-is and never hides or rewords it.
- **Reassurance scan:** phrases such as "safe to eat" are removed from the generated fact and explanation. Warnings such as "not safe to eat" pass.
- **Walk ideas** come from reviewed templates, checked against a banned-phrase list at load time. Nothing involves touching or approaching wildlife, roads, fungi, snakes or tasting anything.
- **Your journal is never scanned.** This is a deliberate non-goal: a journal that watches its owner contradicts privacy.

**Privacy.**

- No accounts, no cloud, no analytics, no GPS. The walk place is text you type.
- Stored photos carry no EXIF. Hashes are used only to find duplicates, not to prove a photo is genuine.
- Moving photos to the laptop is your choice. Messaging apps and cloud drives route photos through third-party servers, so a USB cable or Nearby Share is better. Phone-to-laptop traffic over Wi-Fi is unencrypted unless you set up HTTPS yourself.
- The evaluation scripts read original photos, which may contain EXIF and GPS. Keep `eval/photos/` out of git and out of shared folders.

---

## Testing

```bash
pytest
```

The tests use a fake in place of the model, so they need no Ollama. They cover walks, import and capture timestamps, wording and the confirm/correct rules, safety text handling, the journal and calendar APIs, the phone interface, and the evaluation script's ground-truth handling. The suite last passed with **228 tests and 1 warning**. Rerun it after any change.

Passing tests show the code behaves as designed. They say nothing about how well the real model identifies real photos. That is what the evaluation is for.

---

## Evaluation

The goal is to learn whether a small local model is reliable enough for Explora, not to chase a benchmark. The pass bar is **fixed before any scored run** (`eval/score.py`, implementing PRD section 15.4), so it cannot be bent to fit the results.

**How it works**

- One run is one model times one condition: `gemma4:e2b` or `gemma4:e4b`, on `original` or `compressed` photos (a copy sent through a messaging app).
- Scored runs use the **frozen prompt** (`eval/prompts/frozen.txt`). The harness refuses any other prompt, and refuses to append to a results file that holds a different prompt hash. If the prompt changes, everything is rerun.
- Each result is graded by hand: correctness (Exact, Close or Wrong), explanation (Useful, OK or Not useful) and a flag for any false or unverifiable claim. `score.py` then reports the gates: honesty, usefulness, explanations, compression, speed, safety probes and group-level accuracy.
- Counts are reported as "n of N" because with a small set one photo moves a percentage by several points. A gate with nothing to evaluate shows `n/a`, never PASS.
- The nine-photo field test is kept in its own folder and never mixed with the original set.

**Running it**

```powershell
python eval/run_eval.py --model gemma4:e2b --condition original
python eval/run_eval.py --model gemma4:e2b --condition compressed
python eval/run_eval.py --model gemma4:e4b --condition original
python eval/run_eval.py --model gemma4:e4b --condition compressed
python eval/score.py --md eval/results/summary.md
```

Useful flags: `--splits`, `--limit` (smoke test), `--max-side` (default 768), `--think`, `--timeout`.

**Where things stand** (from `docs/evaluation.md`, dated 2026-10-10): the harness and scoring code are written, the original ground truth has 27 rows (26 scored, 1 tune), and **scored results and gate verdicts are still pending**. Update this section when results exist.

---

## Limitations

- **No verified accuracy yet.** Evaluation results are pending, so no claim is made about how often the model is right.
- **Ground truth is visual-only and mostly group-level** (for example "large tree"), not independently verified species. Until it is verified, "Exact" means "matches the visual group label", not "correct species".
- **The test set is small.** With 26 scored photos, one photo is about 4 percentage points.
- **Known evaluation mismatches** (for example the clear-photo gates look for a `clear` difficulty label the ground-truth file doesn't use) mean some gates would show `n/a` until fixed. See "Known issues" in `docs/evaluation.md`.
- **An undiagnosed runner crash** (HTTP 500, exit status `0xc0000409` on Windows) occurred on the first evaluation run.
- **The model can be confidently wrong.** The wording and the confirmation step exist for that reason, but their effect on real users has not been tested.
- **Duplicate detection is exact-bytes only.** A re-saved or re-compressed copy of a photo is treated as new. A photo can also belong to only one walk, because the content hash is unique across the whole database.
- **One walk per day,** and at most two Polaroids on a journal page.
- **The safety scan is phrase-based,** so a clever rewording can slip through. The same warning rule exists in two places (`identify.needs_safety_warning` and `safety.needs_warning`) and should be merged.
- **The front end is a first pass.** The journal page turn is a simple fade, not a 3D animation. A rotated handwritten page is scaled down to fit rather than sized exactly. The handwriting font (Caveat) must be added to `web/assets/fonts/` and its licence checked. Until then a system handwriting font is used.
- **No schema migrations.** The schema is created at startup and `PRAGMA user_version` is set to 1. A future schema change will need a migration path.
- **Not a scientific tool.** Explora is not a field guide and gives no edibility or safety advice.

---

## Documentation

- [`docs/architecture.md`](docs/architecture.md): components, data flow, model integration, offline-first design
- [`docs/decisions.md`](docs/decisions.md): design decisions and known limitations
- [`docs/evaluation.md`](docs/evaluation.md): dataset, frozen-prompt procedure, scoring, results status
- [`docs/field-test.md`](docs/field-test.md): nine-photo field test procedure

## License

Explora is released under the [MIT License](LICENSE). Gemma model weights and Ollama are separate software with their own licences, and this repository does not bundle them.