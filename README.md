# Explora

A local-first nature-walk journal. You photograph things on a walk, Explora keeps the photos in one SQLite file on your own machine, and a local Gemma model (run through [Ollama](https://ollama.com)) **suggests** what each photo shows. The model only suggests. You decide: confirm it, correct it, say "Not This", or save the photo unidentified.

> **Status (2026-10-10): early development, evaluation not finished.**
> No accuracy numbers are published here because none have been verified yet. See [docs/evaluation.md](docs/evaluation.md) for what has and has not been measured.

## Current status

| Area | State | Notes |
| --- | --- | --- |
| Config, SQLite schema, photo import | Code written | `server/config.py`, `server/db.py`, `server/importer.py` |
| Identification through Ollama | Code written | `server/identify.py`; shared by the app and the evaluation |
| Walks, confirm/correct/reject, collection counts | Code written | `server/walks.py` |
| Confidence wording, safety warnings | Code written | `server/models.py`, `server/safety.py` |
| Automated tests | Written, **not yet run for this documentation** | importer, walks, eval ground-truth handling |
| Evaluation harness | Code written | `eval/run_eval.py`, `eval/score.py` |
| Evaluation results | **Pending** | One errored run is the only result row reviewed so far |
| Nine-photo field test | **Pending** | Ground truth exists; no results collected |
| User interface / web layer | **Not seen** | No UI or HTTP code was among the files reviewed |
| Journal pages, polaroids, quests, "firsts" | **Tables only** | Schema exists; no logic reviewed |
| License | **Not chosen** | `LICENSE` is a placeholder |

"Code written" means the code exists and has tests. It does not mean it has been verified end to end on a real walk.

## What it does today

- **Walks.** One walk per day, with optional typed area, habitat (`park`, `garden`, `campus`, `trail`, `backyard`) and duration. An empty walk is valid. Nothing tracks completeness. Streets and water edges are deliberately not habitats.
- **Photo import.** Duplicates are skipped by SHA-256 of the original bytes. Capture date comes from EXIF `DateTimeOriginal`, then EXIF `DateTime`, then the file's modified time, then now. The source is recorded and the date is editable. Only a resized JPEG copy is stored, re-encoded without EXIF, so GPS coordinates never reach the data folder.
- **Identification.** A local Gemma model returns a category, a name (as specific as it can honestly be, possibly only a group like "a swallowtail butterfly"), a confidence of High, Medium or Low, a short explanation and an optional general fact. Malformed output becomes an honest "not sure".
- **Honest wording.** High is "Looks like a Blue Jay.", Medium is "Might be a Blue Jay.", Low or unknown is "Not sure what this is." Percentages are never shown. A Low suggestion cannot be confirmed.
- **Person decides.** Confirm, correct (typed name, counts as confirmed by the user), Not This, Save Anyway, Try Again (not after confirming), plus a note and an editable capture date.
- **App-side safety.** Fungi, reptiles, unknown organisms, malformed results and Low-confidence living things get an app-added warning regardless of what the model said. Unsafe reassurance in generated text (such as "safe to eat") is dropped; warnings such as "not safe to eat" are kept.
- **Collection.** Plain counts of confirmed discoveries by category. No percentage, target, rank or progress.
- **Reproducibility.** Every identification stores model, settings (JSON) and prompt hash.

## Not implemented (as far as the reviewed code shows)

A user interface or server entry point, journal page and polaroid logic, quests and "firsts" logic, and the contents of `server/content/banned_phrases.txt`. The database tables for the journal, polaroids, quests and firsts already exist so later modules need no migration.

## Architecture in brief

```text
photo -> importer (dedupe, strip EXIF, resize) -> SQLite + data/photos/
      -> identify (local Ollama, Gemma) -> safety checks -> suggestion
      -> person confirms / corrects / rejects -> collection counts
```

`identify()` is the single identification path. The app and `eval/run_eval.py` both call it, so the evaluation tests what ships. Details: [docs/architecture.md](docs/architecture.md).

## Setup

These steps are for Windows PowerShell, which is what the evaluation runs so far were made on (see the error row in [docs/evaluation.md](docs/evaluation.md)).

```powershell
# from the repository root
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

Python 3.10 or newer is assumed. Confirm with `python --version`.

### Local Gemma through Ollama

1. Install Ollama from <https://ollama.com> and make sure it is running. By default it listens on `http://localhost:11434`.
2. Pull the models used in the evaluation. The tags are the ones the code and evaluation use:

   ```powershell
   ollama pull gemma4:e2b
   ollama pull gemma4:e4b
   ollama list
   ```

3. Pulling a model needs a network connection once. After that, identification talks only to your local Ollama.

### Configuration

All settings are optional environment variables.

| Variable | Default | Meaning |
| --- | --- | --- |
| `EXPLORA_DATA` | `<repo>/data` | Where the database and photos live |
| `EXPLORA_MODEL` | `gemma4:e2b` | Ollama model tag. The default is provisional until the evaluation picks a model |
| `EXPLORA_OLLAMA` | `http://localhost:11434` | Ollama URL |
| `EXPLORA_PROMPT` | `eval/prompts/frozen.txt` | Prompt file. In development it falls back to `v1.txt` beside it |

```powershell
$env:EXPLORA_MODEL = "gemma4:e4b"
```

Model defaults sent to Ollama: thinking off, longest image side 768 px, `num_predict` 400, `keep_alive` 10 minutes, JSON output format. Temperature, top_p and top_k are left at the model's own defaults.

## Usage

No command line or UI entry point was among the reviewed files. The modules can be used from Python. This snippet was assembled from the function signatures and has not been run:

```python
from server.config import load_config
from server.db import connect
from server import importer, walks

cfg = load_config()
conn = connect(cfg)

walk = walks.get_or_create_walk(conn)                  # today's walk
res = importer.import_path(conn, cfg, walk["id"], r"C:\photos\IMG_0001.jpg")
if res.status == "added":
    d = walks.identify_discovery(conn, cfg, res.discovery_id)
    print(d["suggestion_text"], d["warning"])
    walks.confirm(conn, res.discovery_id)              # only possible for High or Medium
```

`walks.identify_discovery` raises `walks.IdentifyError` when Ollama is unreachable or the model errors. That is different from a "not sure" answer, and the discovery's status is left unchanged.

## Tests

```powershell
pytest
```

The tests use fake identification results, so they need no Ollama. They cover walks, import, wording, confirm/correct rules, safety text handling, and the evaluation script's ground-truth handling. They have not been run as part of writing this documentation.

## Evaluation methodology

The model is judged against a pass bar fixed before scoring (PRD section 15.4, as implemented in `eval/score.py`):

- One run is one model times one condition: `gemma4:e2b` or `gemma4:e4b`, on `original` or `compressed` (sent through a messaging app) photos.
- Scoring uses a **frozen prompt** (`eval/prompts/frozen.txt`). `run_eval.py` refuses scored runs with any other prompt, and refuses to append to a results file that holds rows from a different prompt hash.
- Results are graded by hand (Exact, Close, Wrong; explanation Useful, OK, Not useful; factual flag), then `eval/score.py` reports gates for honesty, usefulness, explanations, compression, speed, safety probes and group-level accuracy. Counts are shown as "n of N" because with a small set one photo moves the percentage by several points.
- The original evaluation and the nine-photo field test are kept in separate results folders and never mixed.

Full detail and current results status: [docs/evaluation.md](docs/evaluation.md) and [docs/field-test.md](docs/field-test.md).

## Limitations

- Ground truth for the evaluation set is visual-only and mostly group-level (for example "large tree"), not independently verified species. Many rows say full-resolution verification is still needed.
- The evaluation set is small. With 26 scored photos, one photo is about 4 percentage points.
- A hash catches only byte-identical duplicates. A re-saved or re-compressed copy of the same photo is not detected.
- A Windows runner crash (HTTP 500, exit status `0xc0000409`) was observed on the first evaluation run. It is not diagnosed yet.
- Several mismatches between `eval/score.py` and the ground-truth file still need fixing before the gates can give a verdict. See "Known issues" in [docs/evaluation.md](docs/evaluation.md).
- The safety rules live in two places that currently match (`identify.needs_safety_warning` and `safety.needs_warning`). They should be merged.
- The model can be confidently wrong. The app's wording and the person's confirmation step exist for that reason, but their effect on real users has not been tested.

## Documentation

- [docs/architecture.md](docs/architecture.md): components, data flow, model integration, offline-first design
- [docs/decisions.md](docs/decisions.md): design decisions and known limitations
- [docs/evaluation.md](docs/evaluation.md): dataset, frozen-prompt procedure, scoring, results status
- [docs/field-test.md](docs/field-test.md): nine-photo field test procedure

## License

Not chosen yet. See `LICENSE` and decision D12 in [docs/decisions.md](docs/decisions.md).