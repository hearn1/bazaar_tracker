# Build Enricher Split — Architecture Design

*Session: 2026-05-04. Pre-implementation deep-dive. No code moves until subtask 2 (contract) lands first.*

---

## 1. Repo Layout

**Decision: two repos.**

- `bazaar_tracker` — runtime, installer, `<hero>_builds.json` (stays here)
- `bazaar-builds` — `bazaar_build_enricher.py`, `probe_*.py`, enricher CI (no `artifacts/` — see §7)

*Note on naming*: `bazaar-builds` is one letter off from `bazaar-builds.net`, the site the enricher scrapes. Acceptable but worth knowing when reading code that references both.

**Rejected: three repos (tracker + enricher + bazaar-builds data repo)**

A dedicated data repo only pays off when multiple independent consumers need the catalog on separate release trains (e.g., a web API and the tracker diverging). That's not the current situation. A third repo adds submodule or package plumbing, a third CI pipeline, and three-way coordination on every schema change — overhead that doesn't earn its keep for a solo curator workflow.

**Why builds stay in the tracker repo, not the enricher repo**

The catalog must ship inside the installer for offline play (see §6). That means it either lives in the tracker repo (zero extra step) or gets vendored from the enricher repo at installer build time (extra CI step, URL dependency). The tracker is also the contract owner (§3) — the consumer defines what it needs, and PRs that change the schema land where the tests live. Keeping builds in the tracker also means the automated pipeline (ROADMAP: Automated Builds Refresh Pipeline) opens PRs against the tracker, giving the curator a single repo to review.

---

## 2. Source of Truth and Distribution

**Source of truth: `<hero>_builds.json` checked into `bazaar_tracker`, at repo root (current location).**

**How downstream gets them:**

| Path | Mechanism |
|---|---|
| Installer / offline play | Bundled by PyInstaller at release time — no change from today |
| Running from source | Files are already in the checkout |
| Curator updating the catalog | Opens a PR against `bazaar_tracker` from the enricher repo (manual today, automated later) |
| Player refreshing to latest mid-install | `refresh-builds` fetches from `main` branch raw GitHub URLs (see §6) |

**Rejected: builds live in enricher repo, tracker fetches from there**

Would require the installer build to pull files from a second repo at package time, or bundle a stale snapshot and rely on `refresh-content` for currency. Adds a URL/release-tag dependency on the enricher repo. No benefit over keeping files in the tracker.

**Rejected: git submodule**

Submodules are a persistent source of friction (detached HEAD confusion, clone `--recursive` requirements, CI boilerplate). The data is small JSON; there's no reason to pay that cost.

---

## 3. Data Contract Enforcement

**Decision: JSON Schema file checked in alongside the catalog files in the tracker repo, validated independently by both producer (enricher CI) and consumer (tracker tests).**

File: `builds_schema.json` at tracker repo root.

The enricher CI runs `jsonschema` (or equivalent) against every generated catalog before publishing. The tracker's `scorer.py:load_builds()` validates at load time (soft warning in production, hard failure in tests). No shared package, no import dependency between repos.

**Rejected: consumer-owned loader imported by enricher CI**

To import a Python module from the tracker repo inside enricher CI you need either a PyPI package (release overhead for a ~50-line loader) or a git submodule/subtree. The schema file is language-agnostic and requires zero cross-repo import machinery.

**Rejected: schema in enricher repo only**

The consumer should own the contract. If the schema lives only in the enricher, the tracker tests can't catch a breaking catalog change until `refresh-builds` runs on a user machine.

**Unresolved sub-question:** Should the schema be strict (additionalProperties: false) or permissive? Recommend permissive on unknown keys (`additionalProperties: true`) so the enricher can add new catalog fields without a coordinated schema bump. Required fields are strict; new optional fields are free.

---

## 4. Schema Versioning

**Decision: `schema_version` integer field in each `<hero>_builds.json`, validated at load time.**

- Current value: `1` (add to all existing catalogs as part of subtask 2)
- Tracker defines `BUILDS_SCHEMA_MIN = 1`, `BUILDS_SCHEMA_MAX = 1` constants in `scorer.py`
- At load time: if `schema_version` is outside `[MIN, MAX]` (either too old OR too new) → refuse to load that catalog, fall back to the bundled copy, log a clear error. The writable copy from `refresh-builds` is *additive* — bundled catalog is always the safety net.
- Enricher CI validates that generated catalogs carry the correct version

**Why refuse too-new catalogs (not just too-old):** an old tracker cannot safely parse a future schema. A "warn but load" policy on too-new versions silently corrupts the run if structure changed. Refusing on both sides means a player who runs `refresh-builds` after a schema bump (but before updating their installer) keeps working off the bundled v1 catalog instead of crashing on a v2 catalog they can't read.

**Schema evolution protocol** (avoid breaking refreshes for users on older installers):

1. Schema bump lands in tracker repo first (PR adds new fields, bumps `BUILDS_SCHEMA_MAX`, adds load-time migration if needed).
2. Tracker installer release ships with the new tracker code.
3. *Only then* does the enricher start emitting catalogs at the new `schema_version`.

If the enricher ever produces a `schema_version=N+1` catalog before the tracker can read it, every player's `refresh-builds` falls back to the bundled copy (per the refusal rule above) — not catastrophic, but stale until they update.

**Rejected: content hash**

Useful for staleness detection, not for compatibility. Doesn't tell the tracker whether it can parse the structure.

**Rejected: semver of a data repo**

Only meaningful if the data had its own repo (§1 rejected that).

**Rejected: reuse `content_manifest.py` schema**

`content_manifest.py` tracks CDN content freshness (card images, static data). Builds versioning is a different concern — schema compatibility, not staleness. Mixing them would couple unrelated refresh mechanisms.

**Note on `last_updated` field**: already present in `karnok_builds.json`. Keep it; it's human-readable provenance, not a machine-compatibility signal.

---

## 5. Probe Scripts Placement

**Decision: move `probe_*.py` to the enricher repo.**

`probe_bundle_coverage.py`, `probe_install_card_bundle.py`, `probe_catalog_guids.py` are curator-only dev tools. They parse local Unity bundle cache files (hardcoded paths to `C:\Users\Matt\AppData\...`). They don't need to ship in the installer and have no runtime callers.

The surface-level similarity to `capture_mono.py` (both touch game internals) is misleading: `capture_mono.py` runs live on every player machine via Frida; the probe scripts are one-shot curator analysis tools. Coupling them to the runtime repo gains nothing and keeps installer-irrelevant tooling in the distribution.

**Rejected: stay near `capture_mono.py`**

No shared imports between the probe scripts and the runtime capture pipeline. The only shared knowledge is Unity bundle format, which doesn't require co-location. If a future probe script does need to share game-internals knowledge with the runtime (e.g., hero enum definitions), that constant should live in a small shared data file, not keep both tools in the same repo.

---

## 6. Offline Play + Catalog Refresh

**Decision: two independent paths that don't conflict.**

**Path A — Installer bundle (offline baseline)**

PyInstaller bundles all `<hero>_builds.json` files at release time. `app_paths.bundled_root()` already points at these. This is the current behavior; no change needed.

**Path B — Runtime refresh (latest published)**

Add a `refresh-builds` command (or extend `refresh-content`) that fetches each `<hero>_builds.json` from a known GitHub raw URL (pinned to latest release tag) and writes it to `app_paths.data_dir()` — the writable per-user directory, separate from the read-only bundle root.

At load time, `scorer.py:load_builds()` prefers the writable copy when it exists and has a compatible `schema_version`; falls back to the bundled copy. This means a freshly installed tracker works offline immediately, and a player who runs `refresh-builds` gets the latest curator-approved catalog without waiting for a full installer update.

**`refresh-builds` failure-mode requirements** (acceptance criteria for Step 3):

- GitHub unreachable → silent fall-back to bundled copy, log a warning, exit non-zero so scripts can detect the failure.
- Single hero's file 404 or malformed → skip that hero, continue with others, surface in `doctor` / diagnostics output.
- Schema validation fails on a fetched file → discard the fetch, do *not* overwrite the writable copy. Old refresh stays in place.
- Partial write mid-fetch → use atomic write (temp file + rename) so a half-written catalog never lands on disk.
- Network success but unchanged content → no-op (compare hash or `last_updated` before writing) to avoid spurious file mtime churn.

**Resolved:** use `main` branch raw URL (`raw.githubusercontent.com/hearn1/bazaar_tracker/main/<hero>_builds.json`). Catalog updates are independent of installer releases; release-tag pinning would make `refresh-builds` useless between installer cuts. `refresh-builds` is the lightweight catalog pull mechanism for players — no additional roadmap item needed beyond surfacing it in docs/UI once wired.

---

## 7. Migration Plan

**Order of operations** — each step is independently mergeable; later steps depend on earlier ones.

### Step 1 — Schema contract (subtask 2, lands in tracker repo first)

1. Add `schema_version: 1` to all `<hero>_builds.json`.
2. Write `builds_schema.json` at tracker repo root (JSON Schema draft-7, required fields only).
3. Add validation in `scorer.py:load_builds()`: warn on incompatible version, log on schema mismatch.
4. Add pytest test: load all `<hero>_builds.json` through the validator; fail on schema error.
5. Commit and merge. **This is the acceptance criterion the enricher has to satisfy on day one.**

### Step 2 — Extract enricher repo (subtask 3)

1. Create `bazaar-builds` repo.
2. Copy `bazaar_build_enricher.py`, `probe_*.py`, `.gitignore`, minimal `README.md`. **Do not copy `artifacts/`** — they're stale outputs from the old workflow; regenerate fresh in the new repo as needed.
3. Replace tracker imports with CLI args. Today `bazaar_build_enricher.py:870` calls `app_paths.repo_dir()` and `load_known_items` reads both `*_builds.json` and `card_cache_names.txt` from that root. After the split:
   - `--catalog-dir <path>` — directory containing `<hero>_builds.json` files (points at the tracker checkout)
   - `--names-file <path>` — explicit path to `card_cache_names.txt`
   - Both default to the curator's local tracker checkout for ergonomics, but are explicit so the automated pipeline (future) can pass other paths.
   - **Live read, never copy:** `card_cache_names.txt` is generated by `refresh_images.py:241` and changes whenever the curator refreshes image coverage. The CLI arg must point at the live tracker checkout — never vendor a copy into the enricher repo, it'll go stale.
4. Add enricher CI: schema validation only on day one. For every `*_builds.json` produced by the enricher in CI, run `jsonschema` against `builds_schema.json` fetched from the tracker repo's `main` branch. Behavior tests for the enricher itself are nice-to-have, not blocking the extraction.
5. Delete `bazaar_build_enricher.py`, `probe_*.py`, and `artifacts/` from the tracker repo. Add a note in CLAUDE.md pointing to the new `bazaar-builds` repo.
6. Verify tracker `pytest` still passes with the enricher gone.

**"Done" criteria for the extraction PR:**
- `pytest -q` in tracker repo passes with enricher files deleted
- `bazaar-builds` CI passes independently (schema validation against tracker `main`)
- `tracker.py` has no import of the enricher module
- No `artifacts/`, `bazaar_build_enricher.py`, or `probe_*.py` in tracker repo

### Step 3 — Tracker-side ingestion (subtask 4)

1. Add `refresh-builds` command (or extend `refresh-content --component builds`).
2. Implement fetch: for each hero in `CATALOG_FILENAMES`, fetch from `raw.githubusercontent.com/hearn1/bazaar_tracker/main/<hero>_builds.json`, validate `schema_version`, write atomically to `app_paths.data_dir()`. Honor the failure-mode requirements in §6.
3. Update `scorer.py:load_builds()` to check writable data dir before bundled root.
4. Confirm installer build still bundles the baseline catalogs from repo root.

### What does NOT move

- `<hero>_builds.json` — stay in tracker repo
- `builds_schema.json` — tracker repo (consumer owns the contract)
- `content_manifest.py`, `card_cache.py` — unrelated to this split
- `scorer.py`, `web/build_helpers.py` — runtime consumers, untouched

---

## Resolved Questions (2026-05-04)

1. **Enricher repo name**: `bazaar-builds`. Output-focused name; matches the `*_builds.json` artifact.
2. **`artifacts/` disposition**: delete from tracker repo on extraction. No archival — they're intermediate enricher outputs with no runtime or reference value.
3. **Raw URL pinning**: use `main` branch raw URL (`raw.githubusercontent.com/hearn1/bazaar_tracker/main/<hero>_builds.json`). Catalog updates should be frequent and independent of installer releases; tying to release tags would mean players only get new catalogs on full reinstalls. The `refresh-builds` command (Step 3) IS the lightweight catalog pull mechanism for players — no separate roadmap item needed. A ROADMAP note to surface `refresh-builds` as a user-visible feature (docs/UI) is the only follow-up.
4. **`card_cache_names.txt` access**: `--names-file` CLI arg. The enricher runs only on the curator's machine; no path normalization needed. Callers provide the path explicitly.
