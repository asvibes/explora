# Architecture

Last reviewed: 2026-10-10, against the source files supplied for this documentation. The PRD itself was not available; section and requirement numbers (FR-xx, section 10.5, 12, 13, 15) are taken from references in the code comments.

## Components

| Module | Purpose | PRD reference in code | State |
| --- | --- | --- | --- |
| `server/config.py` | `Config` dataclass and `load_config()`; paths, model tag, Ollama URL, image sizes | none | Written |
| `server/db.py` | SQLite connection and the full schema; `get_setting` / `set_setting` | section 12 | Written |
| `server/importer.py` | Import photos, dedupe, read capture date, store a resized copy without EXIF | FR-01 to FR-03 | Written |
| `server/identify.py` | Prompt loading, image preparation, Ollama call, output parsing, `IdentifyResult` | design rules in docstring | Written |
| `server/models.py` | `Status`, `HABITATS`, `USER_CATEGORIES`, `suggestion_text()` | section 10.5 | Written |
| `server/safety.py` | App warnings, banned-phrase scanning, unsafe-reassurance detection | section 13, FR-07 | Written; needs `server/content/banned_phrases.txt` (not reviewed) |
| `server/walks.py` | Walks, discoveries, identify/confirm/correct/reject, notes, collection counts | FR-04 to FR-06, FR-08, FR-11 | Written |
| `eval/run_eval.py` | Runs one model x one condition over a ground-truth CSV | section 15 | Written |
| `eval/score.py` | Scores results against the fixed pass bar | sections 15.4, 15.5 | Written |
| UI / HTTP layer | Not among the reviewed files | | **Unknown** |
| Journal, polaroids, quests, firsts logic | Tables exist, no logic reviewed | | **Not seen** |

## Data flow

```text
 photo file(s)
     |
     v
 importer.import_bytes
     |  SHA-256 of original bytes -> skip if already in discoveries.content_hash
     |  read capture time (EXIF DateTimeOriginal > DateTime > file mtime > now)
     |  resize to <= 1024 px long side, re-encode JPEG without EXIF
     v
 data/photos/<walk_id>/<first-16-hash-chars>.jpg  +  discoveries row (status = imported)
     |
     v
 walks.identify_discovery
     |  prompt = eval/prompts/frozen.txt (falls back to v1.txt in development)
     v
 identify.identify  --POST /api/chat-->  local Ollama (Gemma)
     |  parse + validate JSON; malformed -> "not sure" result
     v
 safety.unsafe_reassurance on fact and explanation  (drop and flag if found)
     v
 ai_* columns written, status = identified
     v
 person: confirm | correct | Not This | Save Anyway | Try Again
     v
 final_* columns, status = confirmed / rejected / saved_unidentified
     v
 walks.collection_counts  (confirmed only, by category)
```

At read time (`walks._present`) each discovery also gets `suggestion_text` (from `models.suggestion_text`) and `warning` (from `safety.warning_text`). Neither is stored. After confirmation the warning is computed from the final category.

### Discovery states

```text
imported -> identified -> confirmed            (confirm, or correct)
                       -> rejected             (Not This)
                       -> saved_unidentified   (Save Anyway)
```

Rules enforced in code: a confirmed discovery cannot be re-identified; `confirm` requires `ai_status == "ok"`, a non-empty identification and High or Medium confidence; `correct` needs a label of at most 120 characters and a category from `USER_CATEGORIES` (the model-only categories `unknown` and `not_nature` are excluded); a hard identification error raises `IdentifyError` and leaves the status as it was.

## Data model

SQLite, one file (`<data>/explora.db`), WAL mode, foreign keys on. The whole PRD data model is created up front.

| Table | Used by reviewed code | Notes |
| --- | --- | --- |
| `settings` | `get_setting`, `set_setting` | key/value |
| `walks` | yes | `date` is UNIQUE (one walk per day); area is typed, never GPS |
| `discoveries` | yes | photo path, content hash, capture date and source, `ai_*` result and reproducibility fields, `final_*` fields, note |
| `journal_pages` | schema only | handwritten page photo, never read or OCR'd |
| `polaroids` | schema only | two slots per walk |
| `quest_completions` | schema only | quests not done are simply not stored |
| `firsts` | schema only | one row per category (UNIQUE) |

There is no completeness field anywhere, and a test guards that.

## Model integration

`identify.identify()` is the only path to the model. The app and the evaluation both call it.

Request to `POST {host}/api/chat`:

- `stream: false`, `format: "json"`
- one user message containing the prompt and one base64 JPEG
- `think` sent as a boolean (default off)
- `keep_alive` (default `10m`) and `options` (default `num_predict: 400`; temperature, top_p, top_k only when passed)

Image preparation: fix EXIF rotation, convert to RGB, shrink so the longest side is at most `max_side` (default 768), JPEG quality 90.

Outcomes:

| Outcome | `status` | What the app does |
| --- | --- | --- |
| Valid JSON with a known category and confidence | `ok` | Stored; wording follows confidence |
| Not valid result JSON, or unknown category or confidence | `malformed` | Stored as category `unknown`, Low, no identification: "Not sure what this is." plus an app warning |
| Connection failure, timeout, HTTP error, unreadable image | `error` | `IdentifyError`; nothing stored; status unchanged |

Parsing details: JSON is extracted from the first `{` to the last `}`, so code fences are tolerated. The words "null", "none", "unknown" and "n/a" as an identification become no identification. A result with no identification is forced to Low confidence, because a confident answer with nothing identified is contradictory.

If Ollama answers HTTP 400 and the body mentions `think`, the call is retried once without the field and `settings.think` is recorded as `"not-supported-by-server"`.

Reproducibility: `IdentifyResult` carries model, the final settings dict, prompt hash (first 12 hex characters of SHA-256 of the prompt text), wall-clock seconds and Ollama's reported model-load seconds. The app stores them in `ai_model`, `ai_settings`, `ai_prompt_hash` and `ai_time_s`. The evaluation CSV has the same fields.

### Differences between the app and the evaluation

Both use the same function with the same defaults (768 px, thinking off, 400 tokens). Two small differences:

- The evaluation reads the original or compressed photo straight from `eval/photos/`. The app reads its stored copy, which was already resized to 1024 px and re-encoded once, then resized again to 768 px.
- `eval/run_eval.py` calls `identify.needs_safety_warning`, a placeholder. The app calls `safety.warning_text`. The logic is identical today, but it is duplicated and could drift.

## Safety layers

1. **Prompt rules.** Be honest about uncertainty, never invent a species, never say anything is safe to eat or touch, never suggest touching or approaching.
2. **Parsing.** Malformed or contradictory output becomes "not sure", never confident.
3. **App warnings (FR-07).** Independent of the model's text. Warn for category `fungi`, `reptile` or `unknown`, for any non-`ok` result, and for Low confidence on living categories. Three message variants (fungi, animal, generic).
4. **Reassurance scan.** Phrases from the `[facts]` section of `banned_phrases.txt` are removed from the fact and explanation, with `ai_fact_blocked` set. Negated phrases ("not safe to eat") pass.
5. **Quest scan.** `safety.quest_violations` checks quest text against the `[quests]` section. No quest code was reviewed.
6. **Habitat limits.** Streets and water edges are not offered as habitats.

The person's private journal is never scanned. That is a deliberate non-goal.

## Offline-first design

- **Local data.** One SQLite file and a folder of resized photos under `EXPLORA_DATA`. No server, no accounts.
- **Local model.** `identify.py` uses only the standard library (`urllib`) to talk to Ollama at `http://localhost:11434`. Nothing in the reviewed code contacts any other host.
- **Privacy by construction.** Stored photos carry no EXIF, so GPS in the original never enters the data folder. Walk area is typed text, never GPS. The content hash is for duplicates only, not authenticity.
- **What needs a network.** Installing Python packages and pulling a model into Ollama. After that, import and identification work without a connection.
- **Caveat.** `EXPLORA_OLLAMA` can point anywhere. If it is set to a remote host, photos are sent there. The default is local.
- **Caveat.** The evaluation scripts read original photos directly, and originals may still contain EXIF and GPS. Keep `eval/photos/` out of git and out of any shared folder.