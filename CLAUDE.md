# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A tracker/coach plugin for The Bazaar (a PvP autobattler card game by Tempo Storm). It captures every decision during a run into a local SQLite database, scores them against known build guides, and shows live coaching via an in-game overlay. The project has hero-aware build catalogs for Karnok plus an initial Mak slice. Distributed as a Windows installer; first alpha release published at https://github.com/hearn1/bazaar_tracker (tag: v0.1-alpha.1).

## Common commands

```bash
# Install runtime + test dependencies (Python 3.10+)
pip install -r requirements.txt

# Setup/status. Normal app startup does not block on CDN refresh.
venv312\Scripts\python.exe tracker.py setup-status
venv312\Scripts\python.exe tracker.py setup --refresh-content never

# Refresh static content when online. Re-run after major game patches.
venv312\Scripts\python.exe tracker.py refresh-content

# Refresh latest published build catalogs without reinstalling.
venv312\Scripts\python.exe tracker.py refresh-builds

# Refresh/report card image cache coverage.
venv312\Scripts\python.exe tracker.py refresh-images
venv312\Scripts\python.exe tracker.py refresh-images --coverage-only

# Diagnostics bundle for support.
venv312\Scripts\python.exe tracker.py doctor
venv312\Scripts\python.exe tracker.py export-diagnostics

# Full one-command workflow: log watcher + Flask dashboard + Mono capture
# subprocess + PyWebView overlay. Decisions are scored live as they insert.
venv312\Scripts\python.exe tracker.py
venv312\Scripts\python.exe tracker.py --no-mono       # skip Frida/Mono subprocess
venv312\Scripts\python.exe tracker.py --no-overlay    # headless (watcher + Flask only)
venv312\Scripts\python.exe tracker.py --log "PATH"    # override Player.log autodetect

# Watcher in isolation (debugging)
venv312\Scripts\python.exe watcher.py
venv312\Scripts\python.exe watcher.py --parse-only    # one-shot parse of an existing log
venv312\Scripts\python.exe watcher.py --log "PATH"

# Tests live in tests/ and pytest.ini sets pythonpath/testpaths
venv312\Scripts\python.exe -m pytest -q
venv312\Scripts\python.exe -B -m py_compile tracker.py first_run.py update_checker.py doctor.py refresh_builds.py refresh_images.py settings.py card_cache.py content_manifest.py web/server.py
```

The dashboard is served on `http://127.0.0.1:5555` (`DEFAULT_WEB_PORT` in `tracker.py`). Each tracker session writes a UTF-8 mirror of stdout/stderr to `logs/tracker_YYYYMMDD_HHMMSS.log` — easiest file to share for debugging.

Default Player.log location (auto-detected): `C:\Users\<You>\AppData\LocalLow\Tempo Storm\The Bazaar\Player.log`. Project is Windows-targeted at runtime — `frida`, `watchdog`, and `pywebview` in requirements are unpinned because they're Windows-venv- or game-build-dependent.

## Architecture

```
tracker.py                 # single entrypoint - launches everything below
  |- watcher.py            # tails Player.log in real time
  |    |- parser.py        # regex -> structured event dicts
  |    `- run_state.py     # state machine -> assembles decisions -> db.py
  |         |- board_state.py    # single source of truth for player inventory
  |         |- shop_session.py   # explicit shop visit state machine
  |         `- name_resolver.py  # centralized instance_id -> human name resolution
  |- capture_mono.py       # Frida + Mono hooks -> live game state snapshots -> db.py
  |- web/server.py         # Flask routes only (~400 lines)
  |    |- web/overlay_state.py   # /api/overlay/state payload assembly
  |    |- web/review_builder.py  # overlay review row construction
  |    |- web/build_helpers.py   # build catalog loading, archetype scoring, phase notes
  |    |- web/static/index.html  # dashboard (self-contained, inline JS)
  |    `- web/static/overlay.html # overlay UI (self-contained, inline JS)
  `- overlay.py            # PyWebView always-on-top launcher

Manual diagnostics:
  bridge.py                # manual-only correlation report for log/Mono snapshots
  scorer.py                # LiveScorer evaluates decisions during the run; CLI prints manual reports
```

## Data Flow

- **Pipeline A (Player.log -> watcher -> run_state)**: Source of truth for decisions - offered/chosen/rejected sets, shops, skills, events, skips, sells. BoardState snapshots inventory at each decision. LiveScorer writes score_label immediately.
- **Pipeline B (capture_mono.py -> Frida)**: Enrichment - HP, gold, day/hour, PvP record, card template IDs for name resolution via NameResolver.notify_template()
- **Live Mono context**: `RunState` looks up the latest/nearest Mono snapshot as each decision is recorded and stores day/gold/health/phase/offered names/templates on the decision before scoring.
- **scorer.py**: Phase-aware scoring against build archetypes in hero-specific build JSON catalogs. It prefers refreshed writable catalogs in `app_paths.data_dir()/builds`, then falls back to bundled repo/installer catalogs. `LiveScorer` scores at decision time; stored live scores are authoritative in normal app flow.

## Key Design Decisions (Post-Refactor)

- **Board state**: `BoardState` class owned by `RunState`. Snapshots written as `board_snapshot_json` column on every `insert_decision`. Overlay reads the snapshot — no replay, no divergence.
- **Name resolution**: `NameResolver` with in-memory cache + `_UNRESOLVED` sentinel for lazy retry. Single service used by RunState (live) and server.py (per-request). Falls through: cache → template_map → api_cards → mark for retry.
- **Scoring**: `LiveScorer` instantiated per run. `score_decision()` called after each `insert_decision`, writes score_label/score_notes immediately. Normal run completion does not bridge, rescore, or rewrite stored scores. No per-poll scoring.
- **Server split**: server.py is routes only. Business logic in overlay_state.py, review_builder.py, build_helpers.py.
- **Shop tracking**: `ShopSession` class encapsulates shop visit state (offered/purchased/disposed/rerolls/decisions). RunState delegates via `self._shop`.

## Tech Stack

- Python 3.10+, SQLite (`bazaar_runs.db`), Flask + waitress, PyWebView
- Frida for Mono managed-memory hooks (injected JS agent in capture_mono.py)
- No frontend build step - index.html and overlay.html are self-contained with inline CSS/JS
- Google Fonts: Syne (display), DM Sans (body), IBM Plex Mono (data/labels)

## Features

**Core Pipeline**: Log parsing, decision recording, state machine, combat tracking, card cache (playthebazaar.com static data), live Mono context attachment, phase-aware scoring with archetype detection, skip analysis, rejected-set tracking, PvP record from terminal Mono snapshot.

**Multi-hero support**: Build loading is hero-aware end-to-end for Karnok, Mak, Dooley, Vanessa, and Pygmalien. The shared scorer/server/overlay paths resolve the active run hero's catalog, preferring the writable copy created by `refresh-builds` and falling back to the bundled copy for offline play or incompatible refreshes. To add a new hero, use the fetch + compare workflow in the [bazaar-builds](https://github.com/hearn1/bazaar-builds) repo to populate initial archetypes, then hand-edit the new `<hero>_builds.json` here.

**Mono Capture**: Frida hooks on HandleMessage for GameSim/CombatSim/GameStateSync/RunInitialized. Optimized to 39ms median hook latency via direct memory reads replacing all NativeFunction calls. Key optimizations: `readGameSimFast` single-pass reader, `_fastReadPlayerAttrs` with cached dict layout, `_directReadMonoString` (UTF-16 direct read), content-hash SelectionSet cache, vtable->klass double-deref, hint-trusting in getSnapshotMatches. Gated behind `FAST_GAMESIM_PATH = true` flag.

**Dashboard**: Dark HUD-style UI with run history, stat strip (PvP/PvE/Decisions/Archetype/Flagged), key moments with severity-colored cards, phase-divider timeline with score-colored borders, expandable decision detail, combat grid.

**Overlay**: PyWebView frameless always-on-top window with three tabs — Coach (live archetype detection + item checklist from the active hero's build catalog), Review (last 10 decisions with score badges), Run (PvP/PvE record + phase guidance). F8 toggle collapse, drag-to-move, idle state handling. Live header stats sourced from latest Mono snapshot during active runs, EndRun snapshot for completed runs. Scores written at decision time via LiveScorer — overlay reads stored scores, no per-poll recomputation.

**Infrastructure**: Waitress production WSGI server, session logging to `logs/`, DB writer queue for non-blocking writes, centralized app/settings/cache paths, schema/settings migrations, content/image refresh commands, diagnostics/export support, pytest coverage under `tests/`, Windows installer via PyInstaller + Inno Setup.

## Known Quirks (Not Blocking)

- Mono can be absent or late; decisions still insert and score using fallback heuristics, then future decisions use live context once snapshots arrive.
- `fast_dict_fail` rate is ~41% - managed dict is genuinely mid-update when hook fires. JS-side `_lastGoodAttrs` cache covers the gaps (Gold missing = 0%).
- SelectionSet content-hash cache: `selset_hits` may show 0 if no action-card states were encountered in a run. Cache is ready but triggers only during Choice/Loot/LevelUp states.
- `_directReadMonoString` auto-detects chars offset on first call (12 or 16 depending on Mono build).
- `api_game_states.captured_at` mixed formats: some rows ISO 8601, some Unix milliseconds. Any time-range query must handle both.
- `combat_results` has no `timestamp` column. Ratio-based estimate (`i * total_combats / total_decisions`) is the correct fallback pattern for combat-count-at-decision queries.

## Key Technical Notes for Capture Mono

- Frida agent is a Python raw string template: `FRIDA_MONO_AGENT = r"""`
- Hook source must contain `"dynamic-data"` for Python-side `_merge_partial_snapshot` to carry forward player attrs
- Dict layout cache: `entriesOff=24, countOff=64, entrySize=16, hashOff=0, keyOff=8, valueOff=12, headerAdj=16` - field offsets from `getFields()` include 16-byte MonoObject header; subtracted for value-type array entries
- `FAST_GAMESIM_PATH = false` reverts all optimizations to the safe NativeFunction path

## Catalog Curation

`<hero>_builds.json` files (and `builds_schema.json`) live in this repo and ship with the installer. Players can run `tracker.py refresh-builds` to pull the latest published catalogs from the tracker repo's `main` branch into the writable data directory without reinstalling; incompatible or malformed refreshed catalogs are ignored in favor of the bundled copy. The curator toolchain that produces them — `bazaar_build_enricher.py` and `probe_*.py` — has been extracted to a separate repo:

**[https://github.com/hearn1/bazaar-builds](https://github.com/hearn1/bazaar-builds)**

That repo contains the enricher, probe scripts, CI schema validation, and usage instructions. When updating a hero catalog: run the enricher there, review the proposal markdown it produces, then hand-edit the appropriate `<hero>_builds.json` here and open a PR.

See `ROADMAP.md` for open bugs and planned features.
