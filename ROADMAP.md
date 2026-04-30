# Bazaar Tracker - Roadmap

Active work tracker. Project context, architecture, and stable design notes live in `CLAUDE.md`.

Status labels:
- `Done`: implemented and expected to stay closed.
- `Implemented - verify live`: code exists, but needs confirmation on one or more real runs.
- `Partial`: useful foundation exists, but the feature is not complete enough to close.
- `Open`: not yet implemented.
- `Deprioritized`: low priority feature, leaving as a potential enhancement.

## Open Feature Work

### Testing Bugs / Live Run Findings - Open

Reference log for this batch: `logs/tracker_20260429_200345.log` (latest tracker session log at time of entry). Latest diagnostic bundle at time of entry: `logs/bazaar_tracker_diagnostics_20260429_202235.zip`.

1. Passed item names are not rendering.
   - Observed while running the app after the prod-readiness setup/package work.
   - Investigate whether the review/overlay payload still contains the passed item identifiers and whether name resolution is failing in `web/review_builder.py`, `web/overlay_state.py`, `name_resolver.py`, or the frontend templates.
   - Expected behavior: passed item rows should display readable item names, not blank/IDs/missing labels.

2. PVP record is no longer being tracked.
   - Check whether combat result capture still classifies PVP fights and updates/derives record data from `combat_results`, `runs`, or API game-state snapshots.
   - Confirm whether this regressed in capture/parsing, database persistence, or dashboard aggregation.
   - Expected behavior: the app should continue showing current run PVP win/loss record as fights are captured.

3. No images are rendering.
   - Related to the existing `Add Card Images` roadmap item below.
   - Investigate whether the frontend is receiving image URLs, whether `/cards/<filename>` returns image bytes for manifest entries, and whether `web/card_images.py` is loading the same image cache path populated by `refresh-images`.
   - Expected behavior: cards with manifest entries should render images in review/overlay views, while missing images should fall back gracefully.

### Prod-Readiness Review Follow-Ups - Done

These are follow-ups from the prod-readiness review after the first implementation pass. The repository hygiene and packaging portability items are now complete, so the repo is ready for a first git/GitHub release flow.

Verified:
- CDN/static-content failures no longer block normal startup; normal launch uses first-run setup with `refresh_content="never"`.
- Setup/content refresh failures are warnings, not fatal setup failures.
- `doctor` reports a consistent fresh-profile DB state after schema initialization.
- Root tests have moved into `tests/`, `pytest.ini` points pytest there, and PyInstaller excludes tests.
- GitHub update checks are disabled by default, never call `example.com`, and malformed `updates.github_repo` returns a JSON error payload instead of raising through `/api/updates/status`.
- Verification run: `venv312\Scripts\python.exe -m pytest -q` passed with 25 tests, and py_compile passed for touched modules.
- First-commit `.gitignore` hygiene is in place for local runtime/build/private artifacts while leaving source, tests, docs, packaging scripts, requirements files, build catalogs, and roadmap files trackable.
- `packaging/pyinstaller/build_portable.ps1` accepts `-PythonExe`, uses `.\venv312\Scripts\python.exe` only when present, otherwise falls back to the active `python` on PATH, and prints the interpreter it selected.

No remaining pre-GitHub prod-readiness blockers are currently tracked in this section.

### Multi-Hero Support - Partial

Goal: add more heroes while keeping existing Karnok/Mak behavior stable.

Relevant files:
- `<hero>_builds.json` files
- `scorer.py`
- `web/build_helpers.py`
- `web/overlay_state.py`
- `capture_mono.py` and `msgpack_decoder.py` hero enum mappings

Implementation notes:
- Add one hero at a time as a new build JSON catalog.
- Keep build schema compatible with existing `game_phases`, `archetypes`, `scoring_weights`, and `timing_profile` fields.
- Make sure new hero names match the names emitted by Mono capture and stored on `runs.hero`.

How to test:
- Start a run on the new hero and confirm `runs.hero` is correct in SQLite.
- Verify `scorer.py` loads the new catalog instead of falling back to no-score behavior.
- Verify overlay Coach tab displays the new hero's archetypes and condition items.

### Event Choice Scoring - Deprioritized

Goal: rank map node / encounter decisions relative to build direction instead of only recording that a map node was chosen.

Relevant files:
- `run_state.py`
- `parser.py`
- `scorer.py`
- `web/review_builder.py`
- `web/static/index.html`
- `web/static/overlay.html`

Implementation notes:
- Identify the available event/map options in the log or Mono payload, not only the chosen node.
- Store offered event choices in `decisions.offered` and selected choice in `chosen_id` / `chosen_name` shape compatible with existing review code.
- Extend `scorer.py` to score event choices using phase, current board, current archetype, gold/health, and likely rewards.
- Keep event-choice scoring separate from normal item scoring so event rewards are not mislabeled as shop mistakes.

How to test:
- Capture a run with multiple event/map choices.
- Confirm the DB has `decision_type='event_choice'` rows with both offered and chosen data.
- Confirm dashboard/review show the event choice with a useful note.
- Confirm normal event loot/free reward rows are still excluded from suboptimal shop scoring.

### Add Card Images - Partial

Goal: improve image coverage beyond the currently partial manifest.

Relevant files:
- `extract_bazaar_bundle_pngs.py`
- `probe_bundle_coverage.py`
- `probe_install_card_bundle.py`
- `web/card_images.py`
- `web/server.py`
- `web/review_builder.py`
- `web/static/overlay.html`
- `static_cache/images/manifest.json`

Current state:
- `extract_bazaar_bundle_pngs.py` can export card-looking `Texture2D` assets and write a manifest.
- `web/card_images.py` loads `static_cache/images/manifest.json`.
- `web/server.py` serves card images through `/cards/<filename>`.
- Review/overlay code can attach image URLs when manifest entries exist.

Known issue:
- Image coverage is partial. Some card art appears to be created, packed, or referenced through Unity asset structures that the current Texture2D name/path filter does not fully discover.
- Potential bug / follow-up: `refresh-images --coverage-only` currently reports low raw coverage against all `card_cache` names (for example 799/2733, 29.2%) even after scanning the Steam install successfully. This may be a denominator/classification issue because `card_cache` includes monsters, challenges, levelups, tooltips, events, and other static entries that may not have normal card art. Add a better "expected art" denominator before treating the raw percentage as extraction failure.
- Testing bug / follow-up: no card images rendered during the latest app run. Reference `logs/tracker_20260429_200345.log` and verify whether this is a manifest path mismatch, missing image URLs in review/overlay payloads, failed `/cards/<filename>` serving, or frontend rendering issue.

Implementation notes:
- Use `probe_install_card_bundle.py` against representative bundles to inspect `Texture2D`, `Sprite`, atlas, and container path relationships.
- Update extractor to follow Sprite-to-Texture references and export sprite crops if full card art is packed into atlases.
- Keep the manifest keyed by normalized card name so `web/card_images.py` does not need to know Unity internals.
- Add coverage reporting: total card names from `card_cache` vs manifest hits.
- Add aliases in `web/card_images.py` only for true naming mismatches, not missing extraction logic.

How to test:
- Run image extraction against both user-data Addressables cache and Steam install bundles.
- Confirm `static_cache/images/manifest.json` has increased entry count and no stale entries.
- Start `python tracker.py --no-mono`, open dashboard/overlay, and confirm cards with manifest entries render through `/cards/<filename>`.
- Spot-check missing common cards and use probe scripts to determine whether they are atlas sprites, generated textures, or naming mismatches.

## Prod Readiness / Packaging - Done

Completed prod-readiness items were removed from this roadmap after verification. Implemented pieces include centralized app paths, app/content/schema manifests, DB/settings migrations, doctor/export-diagnostics commands, refresh-content, refresh-images, first-run setup foundation, PyInstaller onedir packaging, Inno Setup scaffolding, non-blocking default startup setup, disabled-by-default update checks, GitHub update-check bad-config handling, clean-profile doctor consistency, a dedicated `tests/` directory, first-commit `.gitignore` hygiene, and portable build script Python selection.

No remaining prod-readiness packaging blockers are currently tracked before creating the first GitHub repo.

### Repository Hygiene / First Commit Safety - Done

Goal: prevent local runtime/build/private artifacts from being committed to GitHub.

Relevant files:
- new `.gitignore`
- `README.md`
- `ROADMAP.md`

Current state:
- Root `.gitignore` exists.
- The working directory currently contains generated/private artifacts including `venv312/`, `build/`, `dist/`, `.pytest_cache/`, `__pycache__/`, `logs/`, `static_cache/`, `bazaar_runs.db*`, `settings.json`, and `sqlite3.Connection`, and those paths are ignored.

Implementation details:
- Add a Windows/Python-focused `.gitignore`.
- Ignore build outputs, virtual environments, test caches, Python bytecode, local DB/WAL files, logs, diagnostics zips, local settings, and generated static/image cache content.
- Do not ignore source files, tests, docs, packaging scripts, requirements files, or build catalogs.

How to test:
- If Git is initialized, run `git status --ignored`.
- Confirm generated/runtime artifacts are ignored.
- Confirm source, docs, tests, and packaging files remain visible for commit.

### GitHub Release Update Checks - Done

Goal: notify users of tracker updates without blocking local play.

Status: implemented and covered by tests.

Relevant files:
- `update_checker.py`
- `settings.py`
- `web/server.py`
- `web/static/index.html`
- `packaging/installer/README.md`

Current state:
- No dedicated hosted website is required.
- Default update checking is disabled/no-op.
- Placeholder URLs such as `example.com` have been removed from production defaults.
- GitHub Releases can be checked with `updates.github_repo` in `owner/repo` form.
- Local/file manifests are supported for tests and custom release channels.
- Keep dashboard update notices non-blocking and dismissible.

How to test:
- With no manifest/repo configured, dashboard boot should make no external request and show no update error.
- Use a local fake release manifest and confirm newer versions show a dismissible notice.
- Set `updates.enabled=true` with malformed `updates.github_repo`; update status should return a non-throwing error payload.
- Simulate network failure; it should not block dashboard load or normal tracker startup.
- Verified in tests: `venv312\Scripts\python.exe -m pytest -q` passed with 25 tests.

### Portable Build Script Portability - Done

Goal: make packaging commands work on a fresh clone without requiring a venv named `venv312`.

Relevant files:
- `packaging/pyinstaller/build_portable.ps1`
- `packaging/pyinstaller/requirements-build.txt`
- `packaging/installer/README.md`
- `README.md`

Current state:
- PyInstaller onedir spec exists.
- Build requirements are listed in `packaging/pyinstaller/requirements-build.txt`.
- `packaging/pyinstaller/build_portable.ps1` accepts `-PythonExe`, uses `.\venv312\Scripts\python.exe` only when present, otherwise falls back to active PATH `python`, prints the selected interpreter, and keeps `-NoClean` behavior.

How to test:
- Run `packaging\pyinstaller\build_portable.ps1 -PythonExe <path-to-python>`.
- Activate a differently named venv, install runtime/build requirements, and run the script without `venv312`.
- Run `packaging\pyinstaller\smoke_test_portable.py` against `dist\BazaarTracker\BazaarTracker.exe`.
