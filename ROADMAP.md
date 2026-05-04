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
- Use the enricher fetch + compare workflow to populate initial archetypes before writing the catalog.

How to test:
- Start a run on the new hero and confirm `runs.hero` is correct in SQLite.
- Verify `scorer.py` loads the new catalog instead of falling back to no-score behavior.
- Verify overlay Coach tab displays the new hero's archetypes and condition items.

### Build Enricher Repo Split - Open

Goal: separate the build-enrichment toolchain (`bazaar_build_enricher.py`, `probe_*.py`, `artifacts/`) from the runtime tracker. The enricher is a curator-side tool that consumes external sources and produces `<hero>_builds.json` updates; it does not need to ship with the installer or run on player machines.

Status: enricher and probe scripts currently live in this repo and run manually. Artifacts under `artifacts/` are checked in. `<hero>_builds.json` files are the only outputs the runtime actually reads.

Relevant files (current home):
- `bazaar_build_enricher.py` — main enricher entry point
- `probe_bundle_coverage.py`, `probe_install_card_bundle.py`, `probe_catalog_guids.py` — bundle/manifest probes
- `artifacts/<hero>_bazaar_builds_summary.json`, `artifacts/<hero>_build_update_proposal.md`
- `<hero>_builds.json` — consumed by `scorer.py`, `web/build_helpers.py`

Open architectural questions (needs its own deep-dive session before any code moves):
- Where do the canonical `<hero>_builds.json` files live? Options: stay in tracker repo, move to enricher repo, or new shared `bazaar-builds` data repo consumed by both.
- If shared: git submodule, npm/PyPI-style package, raw-URL fetch in `refresh-content`, or vendored-on-release?
- Does the enricher need any tracker code (board/scorer schema, hero-name normalization), and can that be factored into a small shared package or duplicated?
- Probe scripts touch the local Bazaar install and Unity bundles — do those belong in enricher or stay near `capture_mono.py`? They're curator-only either way.
- Versioning: how does the tracker know it has a recent-enough builds catalog? Reuse `content_manifest.py` schema or add a new one?
- Distribution: tracker installer needs builds embedded for offline play, but should also be able to refresh against latest published builds.

Suggested subtasks (each its own session):
1. **Architecture deep-dive** — answer the questions above, pick a split, write down the chosen layout and migration plan.
2. **Define the builds-data contract** — lock the `<hero>_builds.json` schema as a versioned interface between the two repos. Add schema validation in both producer (enricher) and consumer (tracker). Decide how the contract is shared (consumer-owned loader imported by enricher CI, vs. checked-in `builds_schema.json` co-located with the data) and add a `schema_version` field. **Must land before extraction** — the contract is the acceptance criterion the extracted enricher has to satisfy in CI on day one.
3. **Extract enricher repo** — move `bazaar_build_enricher.py`, `probe_*.py`, `artifacts/` to the new repo with a clean README. Leave a stub or doc pointer in this repo. Enricher CI runs the contract validator from subtask 2 against every generated catalog before publish.
4. **Tracker-side ingestion** — wire `refresh-content` (or a new `refresh-builds` command) to pull the latest published catalogs from the chosen distribution channel.

How to test (per subtask):
- After extraction, the tracker repo's pytest suite still passes with the enricher gone.
- Running the enricher in its new home produces the same artifact diffs as today against a known input.
- A tracker install with no network access still loads the embedded builds for every supported hero.


### Automated Builds Refresh Pipeline - Open

Goal: a scheduled job (daily) that fetches fresh build data, regenerates `<hero>_builds.json`, and opens a PR with the diff for human review. Long-term the curator's role becomes "review the PR" instead of "run the enricher and edit JSON".

Status: today the loop is fully manual — run `bazaar_build_enricher.py`, eyeball `artifacts/<hero>_build_update_proposal.md`, hand-edit `<hero>_builds.json`, commit. No automation, no signal-based pruning.

Depends on: **Build Enricher Repo Split** (above) — automation should live in whichever repo ends up owning the enricher.

Open questions (needs its own deep-dive session before implementation):
- Add vs. remove signals: how do we decide an item or archetype belongs in the catalog and when it should be dropped? Need a usage / win-rate / recency signal that survives a single run-day's noise.
- Source of truth for "what's winning right now": community ladder data, BazaarDB, in-house aggregated runs from this tracker, or a mix? Each has different freshness, coverage, and licensing tradeoffs.
- Threshold tuning: how aggressive should auto-add / auto-remove be? Probably auto-add liberal, auto-remove conservative, with a "deprecated" interim state before deletion.
- Schema for tracking item "usability": per-item rolling stats (sample size, win rate, archetype association) embedded in catalog, kept in a sidecar file, or recomputed each run?
- Patch-day handling: when Tempo Storm pushes a balance patch, half the catalog can become stale overnight. Does the pipeline detect this and pause auto-removals, or run as normal and rely on the PR review?
- PR mechanics: one PR per hero or one combined? Auto-merge on green CI, or always require human approval?

Suggested subtasks (each its own session):
1. **Signal design deep-dive** — answer the add/remove question. Output: a written spec for what signals feed catalog deltas and the thresholds that gate them.
2. **Stats sidecar / persistence layer** — implement whatever data store the signal design calls for (rolling per-item stats, sample counts, last-seen patch).
3. **Diff generator** — given current `<hero>_builds.json` + fresh signals, emit a structured proposed-change set (adds, removes, archetype reshuffles) richer than today's `*_build_update_proposal.md`.
4. **GitHub Actions workflow** — daily cron, runs the enricher, applies the diff generator, opens or updates a single rolling PR per hero with the changes plus a human-readable summary.
5. **Review tooling** — small dashboard or PR-comment template that surfaces the supporting stats for each proposed add/remove so the reviewer doesn't have to dig.

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
