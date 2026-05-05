# Bazaar Tracker - Roadmap

Active work tracker. Project context, architecture, and stable design notes live in `CLAUDE.md`.

Status labels:
- `Partial`: useful foundation exists, but the feature is not complete enough to close.
- `Open`: not yet implemented.
- `On Hold`: blocked on an external dependency or prerequisite.

## Open Feature Work

### Multi-Hero Support - On Hold

Goal: add Jules and Stelle hero catalogs while keeping existing Karnok/Mak/Dooley/Vanessa/Pygmalien behavior stable.

Status: Karnok, Mak, Dooley, Vanessa, and Pygmalien have populated catalogs. Jules and Stelle are on hold — not yet purchased.

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
- Use the enricher fetch + compare workflow in [bazaar-builds](https://github.com/hearn1/bazaar-builds) to populate initial archetypes before writing the catalog here.

How to test:
- Start a run on the new hero and confirm `runs.hero` is correct in SQLite.
- Verify `scorer.py` loads the new catalog instead of falling back to no-score behavior.
- Verify overlay Coach tab displays the new hero's archetypes and condition items.

### Build Enricher Repo Split - Done

Goal: separate the build-enrichment toolchain from the runtime tracker. The enricher is a curator-side tool that consumes external sources and produces `<hero>_builds.json` updates; it does not need to ship with the installer or run on player machines.

Status: Steps 1-3 done. `bazaar_build_enricher.py`, `probe_*.py`, and `artifacts/` have been extracted to [github.com/hearn1/bazaar-builds](https://github.com/hearn1/bazaar-builds). `<hero>_builds.json` and `builds_schema.json` stay here (consumer owns the contract). `tracker.py refresh-builds` now pulls the latest published catalogs from `raw.githubusercontent.com/hearn1/bazaar_tracker/main/` into the writable data directory without requiring a reinstall.

Relevant files (this repo):
- `<hero>_builds.json` — build catalogs consumed by `scorer.py`, `web/build_helpers.py`
- `builds_schema.json` — JSON Schema contract; validated by tracker tests and enricher CI

Enricher toolchain (bazaar-builds repo):
- `bazaar_build_enricher.py` — main enricher entry point (now has `--catalog-dir` / `--names-file` args)
- `probe_bundle_coverage.py`, `probe_install_card_bundle.py`, `probe_catalog_guids.py` — bundle/manifest probes
- `.github/workflows/validate-schema.yml` — CI validates any `*_builds.json` against tracker schema

Completed subtasks:
1. ✅ **Architecture deep-dive** — design doc at `docs/build-enricher-split-design.md`
2. ✅ **Define the builds-data contract** — `builds_schema.json` added, `schema_version` field in all catalogs, tracker validation in `scorer.py` and pytest
3. ✅ **Extract enricher repo** — `bazaar_build_enricher.py`, `probe_*.py`, `artifacts/` moved to `bazaar-builds`; `app_paths` dependency replaced with `--catalog-dir`/`--names-file` CLI args

4. Done: **Tracker-side ingestion** - `refresh-builds` fetches `<hero>_builds.json` from tracker `main`, validates each catalog, writes atomically to `app_paths.data_dir()/builds`, and `scorer.py` prefers compatible writable catalogs before bundled catalogs.

Follow-up:
- Surface `refresh-builds` in player-facing docs/UI as the lightweight catalog-pull mechanism for players. CLI support is wired; no automatic startup refresh and no UI button yet.

How to test:
- A tracker install with no network access still loads the bundled builds for every supported hero.
- `refresh-builds` with GitHub unreachable exits non-zero and logs a warning without touching the writable copy.
- Schema validation failure on a fetched file discards the fetch and leaves the existing writable copy in place.


### Automated Builds Refresh Pipeline - Open

Goal: a scheduled job (daily) that fetches fresh build data, regenerates `<hero>_builds.json`, and opens a PR with the diff for human review. Long-term the curator's role becomes "review the PR" instead of "run the enricher and edit JSON".

Status: today the loop is fully manual — run `bazaar_build_enricher.py` (in the [bazaar-builds](https://github.com/hearn1/bazaar-builds) repo), eyeball the `*_build_update_proposal.md` artifact, hand-edit `<hero>_builds.json` here, commit. No automation, no signal-based pruning.

Depends on: **Build Enricher Repo Split** (above, now Done) — automation should live in the `bazaar-builds` repo.

Open questions (needs its own deep-dive session before implementation):
- Add vs. remove signals: how do we decide an item or archetype belongs in the catalog and when it should be dropped? Need a usage / win-rate / recency signal that survives a single run-day's noise.
- Source of truth for "what's winning right now": community ladder data, BazaarDB, in-house aggregated runs from this tracker, or a mix? Each has different freshness, coverage, and licensing tradeoffs.
- Threshold tuning: how aggressive should auto-add / auto-remove be? Probably auto-add liberal, auto-remove conservative, with a "deprecated" interim state before deletion.
- Schema for tracking item "usability": per-item rolling stats (sample size, win rate, archetype association) embedded in catalog, kept in a sidecar file, or recomputed each run?
- Patch-day handling: when Tempo Storm pushes a balance patch, half the catalog can become stale overnight. Does the pipeline detect this and pause auto-removals, or run as normal and rely on the PR review?
- PR mechanics: one PR per hero or one combined? Auto-merge on green CI, or always require human approval?

Suggested subtasks (each its own session). Renumbered 2026-05-05 from 5 to 7 — see `docs/automated-builds-pipeline-design.md` §11 for the rationale.
1. **Signal design deep-dive** — answer the add/remove question. Output: a written spec for what signals feed catalog deltas and the thresholds that gate them. ✅ Done — see `docs/automated-builds-pipeline-subtask1-signal-spec.md`.
2. **Stats sidecar / persistence layer** — implement whatever data store the signal design calls for (rolling per-item stats, sample counts, last-seen patch). ✅ Done — see `bazaar-builds/automated_builds_pipeline/stats.py`.
3. **Source fetchers** — bazaardb (Playwright), Mobalytics (PRELOADED_STATE), bazaar-builds.net (existing-enricher wrapper + the two date-extraction fixes from the research note). Each emits a `WindowObservation` plus per-source health.
4. **Threshold evaluator** — engine that consumes fetcher output + sidecar history + `pipeline_state.json` + current catalog, applies the §2 threshold rules, emits the per-row schema from subtask 1 §5.
5. **Diff generator + LLM** — given threshold rows + catalog + LLM classification, emit a structured proposed-change set (adds, removes, archetype reshuffles) richer than today's `*_build_update_proposal.md`.
6. **GitHub Actions workflow** — cron, runs the fetchers + evaluator + diff generator, opens or updates a single rolling PR per hero with the changes plus a human-readable summary.
7. **Review tooling** — small dashboard or PR-comment template that surfaces the supporting stats for each proposed add/remove so the reviewer doesn't have to dig.

How to test (per subtask):
- Signal design: dry-run against historical artifacts and confirm the proposed deltas match what the curator would have done by hand.
- Workflow: trigger the action manually on a fork; confirm a PR appears, contains a sane diff, and CI passes.
- End-to-end: skip a day of runs, then trigger the job; confirm the resulting PR is empty or near-empty (no spurious churn from low sample size).


### Build Archetype Images - Open

Goal: show a single representative image per build archetype in the overlay/dashboard rather than attempting per-card inline images. Drop the per-card image pipeline.

Relevant files:
- `<hero>_builds.json` — add optional `image` field per archetype
- `web/build_helpers.py` — expose image field when loading archetypes
- `web/static/overlay.html` — render archetype image in Coach tab
- `web/static/index.html` — render archetype image in dashboard build section

Direction:
- Each archetype entry in `*_builds.json` gets an optional `image` field (URL or local filename).
- Images can be sourced manually (curator picks one representative card art or build screenshot) or downloaded automatically during `refresh-content` / `update-builds`.
- Downloaded images are stored in `static_cache/images/builds/` — keyed by hero + archetype slug.
- Overlay Coach tab shows the archetype image (or a placeholder) alongside the archetype name and checklist.
- Remove inline per-card image rendering from review/overlay once build images are in place. Existing `web/card_images.py`, manifest, and extraction scripts can be archived or deleted once this replaces them.
- BazaarDB outreach is still open — if they respond, their images could be used as the source for archetype art.

Implementation notes:
- Keep it simple: one image per archetype, not per card. No manifest, no quality diagnostics.
- Image field is optional — archetypes without one just show no image (no fallback chain needed).
- If downloading during refresh: respect rate limits, store locally, never rehost.

How to test:
- Add an `image` field to one archetype in `karnok_builds.json` and confirm overlay Coach tab renders it.
- Confirm archetypes without an image field display cleanly with no broken-image placeholder.
- If auto-download is implemented, run `refresh-content` and confirm images land in `static_cache/images/builds/`.
