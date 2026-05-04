# Bazaar Run Tracker

Captures every decision you make in The Bazaar into a local SQLite database,
structured for future scoring and AI-assisted analysis.

## What It Captures

- Every card/item offered vs. what you picked
- Every skill offered vs. what you picked
- Board state after each decision (player + opponent)
- Item repositioning (move events)
- Combat outcomes (who died, duration)
- Full run metadata (hero, session ID, timestamps)

## Requirements

- Python 3.10+
- `pip install -r requirements.txt`
- Optional packaging build requirements: `pip install -r packaging/pyinstaller/requirements-build.txt`

## Setup

**1. Install dependencies:**
```
pip install -r requirements.txt
```

**2. Run setup/status checks:**
```
python tracker.py setup-status
python tracker.py setup --refresh-content never
```
Normal tracker startup does not block on CDN content refresh. It initializes local paths/settings/DB and reports missing static content as a warning.

**3. Refresh static content when online** (run after major Bazaar patches):
```
python tracker.py refresh-content
```
This hits `data.playthebazaar.com/static`, keeps the previous local cache active if refresh fails, and records endpoint/card diffs in the content manifest.

**4. Refresh card images when needed:**
```
python tracker.py refresh-images
python tracker.py refresh-images --coverage-only
```
Image coverage is still partial; see `ROADMAP.md` for the remaining Unity asset extraction work.
Image work is currently paused while waiting for BazaarDB guidance on optional local user-side image caching.

**5. Parse a completed run** (the log file from a previous session):
```
python watcher.py --parse-only
```

**6. Watch in real time** while playing (start before or during a run):
```
python watcher.py
```

**7. Run the full one-command workflow** (watch log + launch Mono capture + live scoring):
```
<pathToTracker>\venv312\Scripts\python tracker.py
```
This replaces the old three-terminal workflow of `watcher.py`, `capture_mono.py --db`, and manual bridge runs.
Each run also writes a UTF-8 session log to `logs/tracker_YYYYMMDD_HHMMSS.log`, which is the easiest file to share for debugging.

The current supported workflow lives in `tracker.py`, `watcher.py`, and the modules they invoke.
Legacy transport-capture experiments have been removed from the project root so the active pipeline is easier to navigate.

## Tests

Tests live in `tests/` and are configured through `pytest.ini`.

```
python -m pytest -q
python -B -m py_compile tracker.py first_run.py update_checker.py doctor.py refresh_images.py settings.py card_cache.py content_manifest.py
```

Latest verified status: 25 tests passing after the prod-readiness/update-check cleanup.

## Diagnostics

```
python tracker.py doctor
python tracker.py export-diagnostics
```

`doctor` intentionally initializes/verifies the DB schema before reporting DB-dependent summaries, so a clean profile should not report contradictory DB state.

## Packaging

Portable builds use PyInstaller and installer builds use Inno Setup 6.

```
pip install -r packaging/pyinstaller/requirements-build.txt
powershell -ExecutionPolicy Bypass -File packaging\pyinstaller\build_portable.ps1
powershell -ExecutionPolicy Bypass -File packaging\pyinstaller\build_portable.ps1 -PythonExe C:\Path\To\python.exe
powershell -ExecutionPolicy Bypass -File packaging\installer\build_installer.ps1
```

`build_portable.ps1` accepts `-PythonExe` for fresh clones or custom virtual environments. If omitted, it uses `.\venv312\Scripts\python.exe` when present, otherwise it falls back to the active `python` on PATH and prints the selected interpreter.

## GitHub Prep

First-commit repository hygiene is in place. The root `.gitignore` keeps local runtime/build/private artifacts out of Git, including `venv312/`, `.venv/`, `build/`, `dist/`, `.pytest_cache/`, `__pycache__/`, `logs/`, `static_cache/`, `bazaar_runs.db*`, `settings.json`, `sqlite3.Connection`, diagnostics zips, PyInstaller outputs, and editor/OS noise. Source files, tests, docs, packaging scripts, requirements files, build catalogs, and roadmap files remain trackable.

## Updates and Distribution

The app does not require a dedicated hosted website. Update checks are disabled by default and should be configured for GitHub Releases through settings when a repo exists. The dashboard update check must remain non-blocking and should never call placeholder URLs.

## Log File Location

The tracker auto-detects the log at:
```
C:\Users\<YourUsername>\AppData\LocalLow\Tempo Storm\The Bazaar\Player.log
```

Or specify manually:
```
python watcher.py --log "C:\Users\Matt\AppData\LocalLow\Tempo Storm\The Bazaar\Player.log"
```

## Querying the Database

The database is a standard SQLite file: `bazaar_runs.db`

You can query it with any SQLite tool (DB Browser for SQLite is great), or Python:

```python
import sqlite3, json
conn = sqlite3.connect("bazaar_runs.db")
conn.row_factory = sqlite3.Row

# All decisions in your last run
run = conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()
decisions = conn.execute(
    "SELECT * FROM decisions WHERE run_id=? ORDER BY decision_seq",
    (run["id"],)
).fetchall()

for d in decisions:
    offered = json.loads(d["offered"])
    print(f"#{d['decision_seq']} [{d['game_state']}] {d['decision_type']} — chose {d['chosen_template']}")
    print(f"  Offered {len(offered)}, rejected {len(json.loads(d['rejected']))}")
```

## Database Schema

| Table | Purpose |
|-------|---------|
| `runs` | One row per run (hero, session, outcome) |
| `decisions` | Every pick: offered set, chosen card, rejected cards |
| `combat_results` | Combat outcomes with board state at fight time |
| `card_cache` | Local copy of card names/tiers from the game's CDN |

The `decisions` table has live scoring columns:
- `score_label` — 'optimal' | 'suboptimal' | 'waste'
- `score_notes` — explanation text written at decision time

## Roadmap

See `ROADMAP.md` for current open work. Current major statuses:
- Prod readiness / packaging: ready for first GitHub upload; `.gitignore` hygiene and portable build script Python selection are complete.
- Remove post-run scoring: complete; live scoring is authoritative and bridge is manual diagnostics only.
- Multi-hero support: partial.
- Mak depth: partial.
- Card images: partial; paused pending BazaarDB response about optional local caching.
- Event choice scoring: deprioritized.

## Architecture

```
Player.log (Unity output)
    │
    └─► watcher.py          # tails log file in real time
            │
            └─► parser.py   # regex patterns → structured event dicts
                    │
                    └─► run_state.py   # state machine → assembles decisions
                                │
                                └─► db.py   # writes to bazaar_runs.db (SQLite)
```
