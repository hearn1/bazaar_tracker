# Automated Builds Refresh Pipeline — Architecture Design

*Session: 2026-05-04. Pre-implementation deep-dive for ROADMAP "Automated Builds Refresh Pipeline". No code in this session. Builds on `build-enricher-split-design.md`; both `<hero>_builds.json` and `builds_schema.json` remain stable contracts.*

---

## 1. Signal Source(s)

**Decision: four-source pipeline with assigned roles. `bazaardb.gg/run/meta` is the primary statistical baseline. Mobalytics curated guides corroborate. `bazaar-builds.net` per-run posts enrich late-game/archetype-internal item evidence. In-house tracker SQLite is a tertiary sanity check.**

**Priority order (highest to lowest, per curator direction):** bazaardb → Mobalytics → bazaar-builds.net.

**Window model (revised 2026-05-05 after source-shape probe — see [`automated-builds-pipeline-research.md`](./automated-builds-pipeline-research.md)):** windows are *per-source*, not uniform. The previous "fixed last-30-days window" assumption only holds for bazaar-builds.net. bazaardb is patch-scoped; Mobalytics is editorial-cadence with a version field for change detection.

| Source | Priority | Role | Shape (resolved) | Window |
|---|---|---|---|---|
| `bazaardb.gg/run/meta` | 1 | **primary** — statistical baseline | Next.js App Router SSR; Cloudflare-protected (needs Playwright); item evidence via DOM (`img[alt]` + "N runs · X%" text) | **Patch-scoped** — page covers "since the last numbered patch." No calendar window control |
| `mobalytics.gg/the-bazaar/guides/meta-builds` | 2 | **secondary** — curated archetype confirmation (~21 builds across heroes per season) | React SPA; **fully structured** `window.__PRELOADED_STATE__` JSON with explicit `cards[].name` per build. Plain HTTP fetch, no JS render | Editorial cadence; doc has a `version` field — skip re-parse if unchanged since last run |
| `mobalytics.gg/the-bazaar/builds` | 2 | **secondary** — guide-cadence supplemental | Same PRELOADED_STATE pattern; uses `NgfDocumentCmWidgetGameDataCardGridV2` nodes with phase-level item lists per article. Listing page only exposes 5 SSR slugs | Per-article editorial cadence |
| `bazaar-builds.net/category/builds/` | 3 | **tertiary** — late-game item enrichment, per-run granularity | Plain WordPress HTML; today's enricher works modulo two date-extraction regressions (see §1 unresolved) | **Last 30 days** via per-post `datePublished` (JSON-LD), once the enricher fixes land |
| in-house `bazaar_runs.db` | sanity check | "did the meta really shift?" confirmation lens in the PR body | Local SQLite | N/A |

The sources have different *shapes*, not just different freshness — bazaardb is pre-aggregated ranking (DOM-extracted), Mobalytics is structured JSON (PRELOADED_STATE) with editorial description text alongside, bazaar-builds.net is raw per-run posts. The pipeline doesn't blend them into a single weighted score; it attributes evidence per-source in the diff JSON (§8) and uses the priority order to break ties (§2). Per-item core/carry/support classification is delegated to an LLM stage (§9).

**Rejected: weighted blend across sources from day one**

A scoring formula (e.g., 0.5·bazaardb + 0.3·mobalytics + 0.2·bazaar-builds) requires per-source calibration that's hard to validate before any source has been observed disagreeing with another in a useful way. Cleaner: thresholds are *per-source* (§2), and the proposal surfaces evidence from each source separately. The curator does the implicit weighting at review time.

**Rejected: keep bazaar-builds.net as primary**

Today's enricher uses it because it's the only source the prior workflow integrated. Now that bazaardb.gg/run/meta is on the table, demoting bazaar-builds.net to "late-game item enrichment" matches its actual shape — per-run posts are good for "which late-game items did winners pick" but noisier than an aggregate for "which archetypes are dominant."

**Rejected: in-house tracker as a primary signal**

Sample size from one curator's account, even with occasional opt-in player uploads, is far below what's needed to distinguish meta shifts from week-to-week noise.

**Unresolved sub-questions:**
- **Source-disagreement resolution**: priority order (bazaardb > Mobalytics > bazaar-builds.net) settles ties when sources contradict, but a structured rule is still needed for the diff JSON. Default: bazaardb's statistical signal wins on inclusion/exclusion; Mobalytics adds editorial context; bazaar-builds.net contributes per-archetype item details. Pin exact rule in subtask 1.
- **Deduplication between sources**: a single winning run could appear as a bazaar-builds.net post *and* feed into bazaardb's aggregate. Probably fine — they're at different aggregation levels — but worth a sanity check during dry-run.
- Whether the in-house tracker should aggregate runs across heroes for cross-validation (cross-hero item appearance is sometimes informative — e.g., a generic utility item).

**Pre-implementation research (resolved 2026-05-05):** see [`automated-builds-pipeline-research.md`](./automated-builds-pipeline-research.md). Key findings folded into this design:

- **bazaardb has no 30-day window** — it's patch-scoped. The §2 threshold rules use patch-windows for bazaardb specifically. Cron infra needs a headless browser (Playwright) — Cloudflare blocks plain HTTP. Affects §6 runtime budget.
- **Mobalytics is fully structured JSON** via `window.__PRELOADED_STATE__`. Item names come directly from `cards[].name`; no LLM extraction pass needed for item lists. §9 simplifies to a single classification pass that consumes the structured items + the `descriptionTheBazaarBoardCreator` text for editorial context.
- **bazaar-builds.net has two silent date-filter regressions** in the existing enricher (category-page `<time>` attrs dropped, JSON-LD `datePublished` not propagated when items are absent). Item extraction is intact. Both are small localized fixes to land before the cron goes unattended; tracked as a separate task in the bazaar-builds repo, not blocking subtask 1.

---

## 2. Add vs. Remove Signal Asymmetry

**Push back on the framing.** "Auto-add liberal, auto-remove conservative" is the right shape for a system that auto-applies. This pipeline doesn't auto-apply — every change goes through curator PR review (§5). The pipeline's job is *evidence assembly*, not catalog mutation. Both sides should be conservative on the *proposal* threshold so the curator's review queue stays signal-rich.

**Decision: both sides conservative. bazaardb is the canonical statistical source (admit/remove gate); Mobalytics + bazaar-builds.net add detail; the LLM stage (§9) handles the carry/core/support classification.**

The pipeline is proposal-only — every change goes through curator PR review (§5) — so the threshold's job is "what shows up in the curator's queue," not "what mutates the catalog." With bazaardb prioritized as the canonical signal, the rules simplify:

"Window" means different things per source (§1): bazaardb = patch, Mobalytics = guide-version (change-detection only), bazaar-builds.net = 30 calendar days.

| Direction | Trigger |
|---|---|
| Add candidate (existing archetype, missing item) | bazaardb shows item in archetype context for ≥2 of last 3 **patches**, **OR** Mobalytics current build for matching archetype lists the item, **OR** bazaar-builds.net shows item in ≥2 of last 3 30-day windows with freq ≥0.4 in latest and sample_count ≥3 |
| Add candidate (new archetype) | bazaardb shows the archetype-tag for ≥2 of last 3 patches, **OR** Mobalytics meta-builds page lists it as a current meta build |
| Remove candidate (item) | item present in catalog but **bazaardb shows item absent from archetype for last K consecutive patches AND ≥21 calendar days have elapsed across those patches** (initial K=4; calendar floor prevents rapid-patch turbulence from triggering removes too fast) AND no presence in current Mobalytics builds AND no bazaar-builds.net evidence in last 30 days |
| Remove candidate (archetype) | same as item-remove but K=6 patches AND ≥45 calendar days, AND no Mobalytics meta-build covers it AND no bazaar-builds.net evidence |

Whichever signal triggers an add, the **classification** (carry / core / support) is decided by the LLM stage (§9) using the full per-source evidence as input, not by the threshold logic. Frequency-based core/support split (today's enricher behavior) is not used for proposals — it remains only as evidence input the LLM consumes.

**Source-quality gate on classification (`carry`/`core` require bazaardb confirmation):**

`carry` and `core` are scarce by intent (1 carry, 2-3 core per archetype per §9); `support` is the broader bucket. To prevent weak signals from inflating the scarce buckets:

- An item proposed as **carry** or **core** must have bazaardb evidence in the matching archetype within the latest window.
- If bazaardb shows the item absent but Mobalytics still cites it (or only bazaar-builds.net has it), the item is classified as **support** — never carry or core.
- This applies to *new adds* (don't promote) and to existing catalog items that come up in classification review (don't auto-demote, but flag for the curator if currently catalog'd as carry/core without bazaardb backing).

This makes the source-disagreement remove-block (§1 default rule) more nuanced: bazaardb-absent + Mobalytics-present doesn't propose removal *and* doesn't promote — the item stays as support. The curator sees the disagreement explicitly in the PR body.

"Candidate" means *surfaced in the PR body*, not applied. Removals never auto-mutate the catalog file; they appear in the diff JSON's `*_removal_candidates` slot for curator action.

**No "deprecated" interim state in the catalog.** The schema is a stable contract (per scope). A removal candidate that survives N windows could be auto-promoted from "soft suggest" to "strong suggest" in the PR body's wording, but the catalog JSON itself stays clean.

**Rejected: auto-apply removes after K windows of absence**

Catalog ships to all players via `refresh-builds`. Auto-removal means a single source outage (bazaar-builds.net down for a week, or scrape selectors break silently) drops items from the player-facing contract without a human ever seeing it.

**Unresolved:**
- **Patch-window thresholds** are an initial guess. K=4 patches + 21 calendar days for item removes; K=6 patches + 45 calendar days for archetype removes. Subtask 1 should validate these against historical bazaardb snapshots once a few patches have been observed.
- **Patch detection**: the cron needs to know what patch bazaardb is currently reporting on. The page header shows the patch-notes link ("Apr 29" → patch notes URL); subtask 1 should pin whether to read this from the DOM, infer from `pipeline_state.json` `patch_label` (§4), or both with a consistency check.
- Whether thresholds should differ per hero (some heroes have lower volume — Pygmalien evidence ≪ Dooley evidence across all sources). Default no; revisit if dry-run shows a hero starves the pipeline.
- **Health-checked source priority for removes**: a scrape regression on bazaardb would silently flip every catalog item into "absent for N patches." Need a heartbeat check — if bazaardb returns zero data or fails parsing in a window, that window is marked unhealthy and doesn't count toward the consecutive-absence threshold. Same logic for Mobalytics/bazaar-builds in their secondary roles.

---

## 3. Stats Persistence

**Decision: sidecar `<hero>_stats.json` stored in the bazaar-builds repo, written and committed by the cron itself.**

Layout (rough — finalize in subtask 2):

```
{
  "schema_version": 1,
  "hero": "Karnok",
  "windows": [{"window_id": "2026-W18", "started_at": "...", "scrape_artifact": "..."}, ...],  // bounded history
  "items": {
    "Hunting Knife": {
      "first_seen_window": "2026-W04",
      "last_seen_window": "2026-W18",
      "windows_seen": 14,
      "windows_observed": 15,
      "per_window": [{"window_id": "2026-W18", "appearances": 7, "sample_count": 12, "archetypes_seen": ["Axe", "Wide Weapons"]}, ...]
    }
  }
}
```

Lives in bazaar-builds repo, not the tracker repo. Three reasons:
1. Catalog stays pristine — curator-reviewed PRs don't get cluttered with stat-row noise on every cron run.
2. Stats are toolchain state, not consumer contract. The tracker doesn't need them at runtime.
3. The cron job already runs in the bazaar-builds repo with write access; updating an in-repo file is a normal `git commit`. No cross-repo write needed for stats (cross-repo write is only for the proposal PR — §7).

**Rejected: embed stats in `<hero>_builds.json`**

Pollutes the consumer contract. Every cron run rewrites every line. PR diffs become unreviewable. The runtime tracker has no use for the data at load time.

**Rejected: reconstruct from raw signals each run**

Only viable if raw scrape artifacts are themselves archived (which is the same persistence problem). And bazaar-builds.net rotates posts off the front page — historical evidence is not reconstructable on demand.

**Rejected: separate stats repo**

Same argument as the prior design rejected a third "data" repo. Solo curator, no independent consumer of the stats file.

**Unresolved:**
- History window: keep last N windows (~6 months at weekly cadence?) or unbounded? Lean toward bounded with a configurable `--retain-windows` flag — disk is cheap but unbounded growth eventually makes the file unwieldy in PR diffs of bazaar-builds itself.
- Whether stats commits to bazaar-builds go on `main` directly or via PR. Direct push is fine — the file is bot-written, no review value, just provenance.
- File-locking / concurrent-run safety. Probably moot if §6's concurrency control prevents overlapping runs, but worth a mention in the implementation spec.
- **Multi-source schema**: per-item entries need per-source breakdowns (e.g., `per_source: {bazaardb: {...}, mobalytics: {...}, bazaar_builds_net: {...}}`) so the diff generator can attribute evidence in the PR body. One file per (hero, source) is also viable but loses cross-source joinability. Lean toward single file per hero with nested per-source.

---

## 4. Patch-Day Handling

**Decision: manual freeze toggle in a `pipeline_state.json` file in the bazaar-builds repo. Curator flips it on patch day; cron suppresses *removal* proposals while frozen, continues *add* proposals normally.**

Add proposals stay live during a freeze because new patches surface new items the catalog should learn about. Removal proposals are the dangerous side: post-patch, the meta hasn't stabilized, and absences in the first week reflect "nobody's posted a fresh build yet" not "this item is gone."

```
{
  "freeze_removals_until": "2026-05-18",   // 14-day window post-patch; null when not frozen
  "patch_label": "13.4",                    // free-form provenance for the PR body
  "notes": "Set after Tempo Storm 13.4 dropped 2026-05-04"
}
```

The PR body surfaces the freeze state ("Removal proposals suppressed: post-patch freeze through 2026-05-18") so the curator doesn't wonder why removes vanished.

**In-flight signals (windows straddling the patch):** don't auto-discard. The pre-patch window's stats stay in the file; the freeze period is the buffer that lets fresh post-patch data accumulate before removes resume. If a major patch invalidates everything, curator runs a one-shot reset (e.g., `bazaar_build_enricher.py reset-stats --hero karnok --before-date 2026-05-04`). That CLI doesn't exist yet — flagged for subtask 2.

**Rejected: explicit `patch_version` pin in the catalog JSON**

Adds a field the runtime tracker doesn't consume, requires curator to know patch version semantics, and goes stale silently when forgotten.

**Rejected: automatic anomaly detection on signal volume**

Tempting but too clever for the dataset. Sample sizes are small enough that one-week dips are common (holiday weekends, site outages, scrape selector regression). False-positive freezes would erode trust in the pipeline.

**Rejected: trust the human reviewer alone, no freeze**

Viable, but means the curator wakes up to a 30-archetype removal PR the day after a patch when the meta is genuinely in flux. The freeze is cheap insurance.

**Unresolved:**
- Whether freeze should auto-trigger if catalog `last_updated` was edited within the last 7 days (proxy for "curator just touched this hero, presumably for a patch"). Probably no — false positives on routine edits.
- Whether the freeze should be per-hero or global. Lean per-hero; some patches only touch one hero's items.
- Source-specific freeze nuance: bazaardb's aggregate re-stabilizes within days post-patch; Mobalytics guides update on editorial cadence (slower). A 14-day blanket freeze is conservative for bazaardb and possibly too short for Mobalytics. Default to one global window for simplicity; revisit if dry-runs show systematic mis-timing.

---

## 5. PR Mechanics

**Decision: one rolling PR per hero. Force-push updates on each cron run. Empty-diff runs short-circuit (don't open or push). Never auto-merge.**

| Choice | Direction | Why |
|---|---|---|
| Per-hero vs combined | per-hero | Heroes review independently; combined PR forces all-or-nothing on heterogeneous evidence |
| Fresh PR vs rolling | rolling | Open PRs accumulate review comments and curator notes; replacing them every run loses that history |
| Auto-merge | never | Catalog ships to all players via `refresh-builds`. A scrape artifact (e.g., site flooded with one item, scrape selector regression) auto-merging would corrupt the production contract |
| Empty diff | short-circuit | If new run produces zero adds/removes, don't push or open; if an open PR's latest run goes empty, close with explanatory comment |

Branch naming: `pipeline/<hero>` (e.g., `pipeline/karnok`). Cron checks for existing open PR on that branch before pushing — open or update, never duplicate.

**Rejected: combined PR across all heroes**

A reviewer who's only ready to think about Karnok this morning shouldn't have to ack Vanessa's diff to merge. PR-per-hero also means a bad signal source for one hero (e.g., bazaar-builds.net's Pygmalien category goes 404) doesn't block the others.

**Rejected: fresh PR every run**

Discards review conversation. Curator comments on a removal candidate ("not yet, give it another two weeks") evaporate when next run opens a new PR.

**Rejected: auto-merge on green CI**

Schema validation passing means "the JSON parses and matches the contract", not "the catalog change is correct." The whole point of this pipeline is human review.

**Unresolved (research):**
- GitHub force-push to a PR branch sends a notification to all subscribers on every push. Weekly cadence (§6) keeps this tolerable but not great. Mitigation candidates: GitHub doesn't expose a clean "suppress force-push notification" API. Possible workarounds: post a single weekly digest comment with stats deltas instead of relying on force-push pings; or have the cron check if the diff is *substantively different* from the prior push (not just hash-different) and skip when not. Worth investigating which exists.
- CI on the proposal PR: schema validation against `builds_schema.json` is the obvious bar. Anything beyond that (e.g., synthetic scorer-doesn't-crash test) is nice-to-have.
- Whether the pipeline should also leave a structured PR comment (separate from PR body) summarizing per-window stats deltas. Probably yes — keeps the PR body stable while supporting tooling (subtask 5) reads from structured comments.

---

## 6. Trigger and Runtime

**Decision: weekly cron in the bazaar-builds repo. `concurrency: cancel-in-progress: false` so overlap (unlikely at this cadence) queues rather than races. Manual `workflow_dispatch` enabled for ad-hoc runs.**

**Push back on "daily-ish" from the ROADMAP.** Daily on a low-volume scraping source is mostly noise. Most days will produce empty diffs (short-circuited per §5), and the days that don't will produce single-item churn the curator can't usefully act on (1-window add candidates won't meet §2 thresholds anyway). Weekly aligns with how often new build posts actually appear on bazaar-builds.net and how often a curator is realistically going to review.

Runtime budget (revised after research findings):

- bazaardb fetch needs Playwright (Cloudflare blocks plain HTTP per §1). Headless Chromium adds ~5-10 s per page render plus ~150 MB to the runner image; GitHub Actions' `ubuntu-latest` supports it directly via `microsoft/playwright-github-action`. Per-hero render time is dominated by the Cloudflare challenge wait, not the page itself.
- Mobalytics is a single plain HTTP fetch + JSON parse (one document covers all 21 builds across heroes); near-zero runtime.
- bazaar-builds.net per the existing enricher: <2 min per hero, plus per-post fetches with `--fetch-posts`.

Total expected runtime: 5-10 min for the full pipeline, still well under the 6-hour cap. Schedule cadence is not runtime-constrained.

Concurrent-run handling: `concurrency: group: pipeline-${{ github.workflow }}, cancel-in-progress: false`. At weekly cadence, an in-progress run when the next cron fires is essentially impossible barring a hung scrape; queueing is fine.

**Rejected: daily**

See above.

**Rejected: on-demand only (no cron)**

Defeats the purpose of automation. The whole work item is "make the curator's role 'review the PR' instead of 'run the enricher.'" Removing the trigger turns this back into a manual workflow with extra YAML.

**Unresolved:**
- Day-of-week / time-of-day for the cron. Bias toward off-peak (e.g., Sunday 06:00 UTC) so the curator has a fresh PR ready Monday morning. Defer to subtask 4.
- Whether the cron should also fire on push to bazaar-builds `main` (i.e., when the enricher itself changes). Probably yes for safety — a bug fix in the scraper should re-run against fresh data without waiting up to a week.
- **Playwright + Cloudflare reliability**: Cloudflare's challenge can occasionally fail to clear in headless mode. Subtask 4 needs a retry policy (e.g., 3 attempts with backoff) and a fallback "skip bazaardb this run, defer adds/removes" path so a one-off Cloudflare hiccup doesn't either crash the cron or trigger removes via the health-check rule (§2).

---

## 7. Cross-Repo PR Permissions

**Decision: fine-grained Personal Access Token for v1; migrate to a GitHub App once the pipeline is stable. PAT stored in the bazaar-builds repo's Actions secrets as `TRACKER_PR_TOKEN`. Rotate on a fixed cadence (~90 days).**

The default `GITHUB_TOKEN` issued to a workflow is scoped to its own repo and cannot push or open PRs against a different repo. Three credible alternatives:

| Mechanism | Pros | Cons |
|---|---|---|
| Fine-grained PAT | Simple, per-repo scoping, low setup | Tied to a user identity (curator's account); manual rotation; PAT revocation is the single recovery action if leaked |
| GitHub App | Repo-scoped install, no user identity, audit logs, automatic short-lived tokens | More setup (app registration, install, permission config); overkill for a solo curator workflow |
| Deploy key | Push-only, no API surface for PR creation | Doesn't authorize `gh pr create` / REST PR endpoints — would need a hybrid (deploy key + something else for PR API) |

PAT is the v1 mechanism (simplest setup, lowest blocker for getting the cron live). Migrate to a GitHub App as a follow-up once the pipeline is stable — cleaner identity (PRs authored by `bazaar-builds-bot` not the curator), no manual rotation, audit logs. Migration is a workflow-config change only, not a redesign.

Token scope (fine-grained PAT):
- Repository access: `bazaar_tracker` only.
- Permissions: `Contents: Read & Write` (to push the branch), `Pull requests: Read & Write` (to open/update PRs).
- Nothing else. Specifically not workflows, not actions, not metadata-write.

**Unresolved (research):**
- Confirm fine-grained PAT cross-repo PR creation is fully supported as of 2026 — GitHub has been pushing toward GitHub Apps as the recommended pattern for bot identities, and there were earlier limitations on fine-grained PATs interacting with required-review branch protection. Verify before subtask 4 picks the mechanism.
- Whether the cron should use `git push` + `gh pr create` (PAT-via-CLI) or REST API directly. CLI is simpler; REST is more controllable for things like "find existing PR and update body in place." Lean CLI for v1.
- Where the PAT's owning-user identity will surface in PRs (commit author, PR author). The bot should commit as a clearly-named identity (e.g., `bazaar-builds-bot <noreply@…>`) to keep curator git history readable. Setup: configure `git config user.name/email` in the workflow.
- Token-leak posture: the PAT is in repo secrets, so leakage requires either the curator's account or repo-admin-on-bazaar-builds. Rotation cadence is a defense in depth measure, not a primary control.

---

## 8. Diff Generator Output

**Decision: structured `<hero>_diff.json` is the canonical artifact. The existing `*_build_update_proposal.md` is *wrapped*, not replaced — it's regenerated from the diff JSON, and continues to be the curator's review surface (now with richer evidence).**

Two artifacts per run:

1. **`<hero>_diff.json`** — machine-readable, drives the PR body, future review tooling (subtask 5), and any cross-window analytics. Schema version starts at 1.
2. **`<hero>_build_update_proposal.md`** — the rendered PR body. Today's `compare` markdown extended with new sections for removal candidates and rolling stats provenance.

Diff JSON shape (finalize in subtask 3):

```
{
  "schema_version": 1,
  "hero": "Karnok",
  "generated_at": "2026-05-04T06:00:00Z",
  "window_id": "2026-W18",
  "source_window": {"start": "2026-04-21", "end": "2026-05-04", "n_windows_history": 15},
  "freeze_state": {"removals_frozen": false, "patch_label": null},
  "proposed_changes": {
    "archetype_updates": [
      {
        "phase": "early_mid", "archetype": "Axe", "sample_count_latest": 7,
        "missing_items": [{
          "item": "X",
          "llm_classification": "core",
          "llm_rationale": "Appears as a primary damage source in 2/3 Mobalytics builds and ranks #4 in bazaardb meta...",
          "llm_confidence": "high",
          "windows_seen": 3, "first_seen_window": "...",
          "evidence_by_source": {
            "bazaardb": {"rank": 4, "windows_in_top_k": 3},
            "mobalytics": {"in_guide_count": 2, "guide_urls": ["..."]},
            "bazaar_builds_net": {"freq_latest": 0.71, "appearances_latest": 5, "sample_count_latest": 7}
          }
        }]
      }
    ],
    "archetype_additions": [
      {"tag": "...", "candidate_phase": "late", "candidate_core": [...], "candidate_support": [...],
       "evidence": {"windows_seen": 3, "sample_count_total": 11}}
    ],
    "archetype_removal_candidates": [
      {"phase": "...", "archetype": "...", "reason": "absent_6_consecutive_windows", "last_seen_window": "..."}
    ],
    "item_removal_candidates": [
      {"phase": "...", "archetype": "...", "item": "...", "reason": "freq_below_0.1_for_4_windows", "windows_seen_recently": 0}
    ],
    "archetype_reshuffles": []   // reserved; current enricher doesn't model item migration between archetypes
  },
  "noise": [...]   // carried through from existing enricher noise list
}
```

The proposal markdown renders sections directly from this JSON — same headings as today's `compare` output (`## Existing Archetype Updates`, `## New Archetype Candidates`, `## Noise / No Evidence`) plus a new `## Removal Candidates` section and a `## Pipeline State` header (window range, freeze state, source link to the scrape artifact in bazaar-builds).

**Why wrap rather than replace the proposal markdown:** the existing format is what the curator already knows how to review. Removing it forces a workflow change on top of an automation change. Wrapping means the proposal markdown grows new sections additively; a curator who only reads the first two sections still gets the same info as today.

**Rejected: drop the markdown, render the PR body directly from JSON inline**

GitHub's PR body is markdown either way. The intermediate file (`*_build_update_proposal.md`) serves two purposes: (a) human-readable diff artifact when reviewing the bazaar-builds repo's history independent of any tracker PR, (b) shared rendering between PR body and any future review-tooling preview (subtask 5). Worth keeping.

**Rejected: emit only the diff JSON, no markdown at all**

Forces every reviewer to run a renderer locally. The PR body has to come from somewhere; if it's not rendered by the pipeline, it's rendered ad-hoc.

**Unresolved:**
- Archetype reshuffles: today's enricher's `compare` doesn't model the case where an item migrates from one archetype to another (e.g., a Karnok item moving from "Axe" to "Wide Weapons" as the meta evolves). The diff JSON reserves a slot; subtask 3 either implements detection or leaves the slot empty for v1.
- Whether the diff JSON should embed the per-window stats deltas it relied on, or just point at `<hero>_stats.json`. Embedding makes the PR review self-contained but inflates the file. Lean on pointing — review tooling (subtask 5) loads stats separately if needed.
- Whether the `*_bazaar_builds_summary.json` artifact (current enricher output) should be committed to bazaar-builds alongside the diff/stats files for full provenance, or left ephemeral. Probably commit it — small files, useful for postmortems when a removal candidate is later disputed.

---

## 9. LLM-Aided Classification

**Decision: a single LLM classification pass sits between evidence gathering and diff generation. It classifies items into `carry` / `core` / `support` per archetype, given all per-source evidence as input. Item *extraction* is fully deterministic across all three sources (revised 2026-05-05 after research findings — Mobalytics turned out to be structured JSON, not freeform HTML).**

The pipeline flow per archetype:

1. **Evidence gathering** — fetch each source per its window model (§1). bazaardb (Playwright) yields DOM-extracted item names + frequencies. Mobalytics yields `cards[].name` + the editorial `descriptionTheBazaarBoardCreator` text per build, both directly from `window.__PRELOADED_STATE__`. bazaar-builds.net yields the existing enricher's per-post item lists.
2. **Aggregation** — merge per-source evidence into per-(archetype, item) rows: which sources cite this item in this archetype, with what frequency / rank / editorial weight.
3. **LLM classification pass** — given the catalog's existing archetype state, the aggregated structured evidence, and the Mobalytics editorial text, the LLM proposes the canonical classification per item: `carry`, `core`, or `support`, with rationale.
4. **Diff generation** — write LLM output into the diff JSON (§8) `llm_classification` + `llm_rationale` + `llm_confidence` fields.

**Classification constraints in the prompt:**

> A build typically has 1 `carry` (sometimes a small set of alternatives), 2-3 `core` items, and the rest as `support`. `carry_items` may be a list of viable alternatives.
>
> **Source-quality gate (hard rule):** an item may only be classified as `carry` or `core` if bazaardb evidence in the matching archetype is present in the latest window. If only Mobalytics or only bazaar-builds.net cites the item, classify as `support`.

The bucket-size constraints (1 carry, 2-3 core) are guidance; the LLM may propose alternatives or a 4-core archetype with rationale, and the curator decides at PR review. The source-quality gate is a hard rule — `support` is the catch-all for weak signal; `carry`/`core` require statistical confirmation.

**Why an LLM rather than deterministic rules:**

- **Carry vs. core vs. support is contextual.** Frequency alone (today's enricher's `core_threshold` / `support_threshold`) misses semantics: an item might appear in 80% of builds but be the *enabler* (carry) in some and a *buff* (support) in others. Cross-source synthesis benefits from a model that can weigh editorial framing (Mobalytics' build description text) alongside statistical frequency (bazaardb's run counts).
- **Catalog wording quality.** The LLM rationale field becomes the per-item explanation in the PR body, saving the curator from cross-referencing multiple artifacts to understand why a proposal landed.

**Determinism / noise control:**

LLM output isn't deterministic, and weekly cron runs producing PR diffs on the same evidence set should produce *the same* classification, or curator review becomes noisy ("why did this item flip from support to core when nothing changed upstream?"). Mitigations:

- Set `temperature = 0`.
- Use prompt caching for the static prompt prefix (catalog state + classification rubric); only the per-archetype evidence varies between calls. Reduces both cost and variance.
- Return a `confidence` enum (`high` / `medium` / `low`). Treat only `high` confidence as "strong enough to surface as a top-line proposal"; `medium` lands in a separate "weaker signals" section of the PR body; `low` is logged but suppressed from the proposal.
- Never let the LLM mutate the catalog directly — it only writes into the diff JSON, which goes through curator PR review (§5).

**Model choice:**

Default to **Claude Sonnet 4.6** (`claude-sonnet-4-6`) for the classification pass. Rationale: editorial-context judgment with structured input is well within Sonnet's range; Haiku might miss nuance on classification. Cost is negligible at expected volume — ~30-40 archetypes × weekly classification calls, each with ~2-5k tokens of evidence + Mobalytics description. At Sonnet pricing this rounds to pennies per cron run.

**Rejected: deterministic frequency-based classification (today's enricher behavior)**

The existing `core_threshold` / `support_threshold` logic in `bazaar_build_enricher.py:589-590` is fine as evidence input but doesn't generalize across sources of different shapes. Mobalytics has no "frequency" — it's editorial inclusion. bazaardb has aggregated rank, not raw appearance counts. A unified classifier needs to weigh all of these together.

**Rejected: LLM call per item (instead of per archetype)**

Per-item calls scale linearly with catalog size and lose the cross-item context the LLM needs ("this is the carry, so these other items are supports buffing it"). Per-archetype is the right unit — one call gets the full item list and classifies them as a coherent set.

**Rejected: LLM does the add/remove threshold decisions too**

Thresholds (§2) stay deterministic. The LLM should not be the gate for *which* items show up in the proposal — that's a sample-size / consistency question with deterministic answers. The LLM only classifies items that have already cleared the threshold. Keeps the system auditable: a curator can reproduce "why did this item appear in the PR?" from the threshold logic alone.

**Unresolved:**
- **Catalog schema mismatch**: existing archetype entries have `condition_items`, `core_items`, `carry_items`, `support_items` (four buckets); the curator's classification spec uses three (carry / core / support). What does the LLM do with `condition_items`? Default: leave `condition_items` untouched by the pipeline — they're hand-maintained for archetypes that *require* a specific item to function (e.g., a class-specific weapon). The LLM only proposes into the other three. Confirm in subtask 1.
- **Hallucination guardrail**: LLM might invent items that don't exist in the catalog or in `card_cache_names.txt`. Pipeline should validate every LLM-output item name against the known-items list (today's enricher already loads this) and drop / flag unknowns rather than emit them into the proposal.
- **Prompt iteration cadence**: prompt changes can flip classifications across the entire catalog overnight. Treat the prompt as a versioned artifact in bazaar-builds (`llm_prompts/classifier_v{N}.txt`), bumped explicitly with a re-classification dry-run before going live.
- **Cost monitoring**: even at pennies per run, anomalous prompt-token usage (e.g., an unusually long Mobalytics article slipping in raw) is worth alerting on. Decision deferred to subtask 4.

---

## 10. Validation & Rollout Phasing

**Decision: phased rollout. bazaar-builds.net thresholds validate immediately against existing enricher artifacts. bazaardb thresholds enter "shadow mode" first — cron runs and writes stats, but no PRs open until enough patch history accumulates to validate K=4/K=6.**

The ROADMAP's "dry-run against historical artifacts" test plan was written assuming a single source. With the multi-source picture (§1), bazaardb has no historical data — the very first cron run *produces* the first snapshot, and "absent for 4 patches" can't be checked against history that doesn't exist. Phased rollout solves this without blocking implementation:

| Phase | What runs | What opens PRs | Exit criteria |
|---|---|---|---|
| 0 — Implementation | Subtasks 1-4 land; nothing fires | None | Subtask 4 lands the workflow, including a `dry_run` flag and `phase` field in `pipeline_state.json` |
| 1 — Local dry-run | Curator runs the pipeline locally against current source state; reviews diff JSON output | None | Mobalytics + bazaar-builds.net signal logic validated against existing enricher artifacts the curator already has. bazaardb produces its first snapshot |
| 2 — Shadow cron | Scheduled cron fires, writes stats sidecar, emits diff JSON as a workflow artifact | None — proposal markdown is uploaded to the workflow run, not PR'd | ≥6 patches OR ≥6 weeks accumulated. Curator periodically reviews "what would have been proposed." K=4 / K=6 thresholds revisited based on observed false-positive rate |
| 3 — Live cron | Same workflow with PR-opening enabled | Yes, rolling PR per hero per §5 | Steady state |

The `dry_run: true` flag in `pipeline_state.json` (or equivalent workflow input) gates phase 2 → phase 3. Curator flips it manually after reviewing accumulated shadow output. No auto-promotion based on date or patch count — solo-curator workflow, manual gate is fine.

bazaar-builds.net signal logic does **not** wait for shadow mode. The curator already has historical `*_bazaar_builds_summary.json` artifacts from manual enricher runs; those drive immediate dry-run validation of the 30-day window thresholds in phase 1.

**Rejected: skip shadow mode, go live with starting thresholds**

Even though humans gate every PR (§5), a noisy first month of removal-PRs based on bazaardb shape mis-estimation creates curator review fatigue at exactly the time when judgment is most important — and erodes trust in the pipeline before it earns any.

**Rejected: extend shadow to all sources**

bazaar-builds.net has historical artifacts; immediate dry-run is faster and information-equivalent. Forcing shadow mode on it adds delay without value.

**Rejected: bootstrap a synthetic bazaardb history**

bazaardb is patch-scoped with no archive. There's no source to bootstrap from. (The Wayback Machine has occasional captures but they're sparse and pre-Cloudflare-challenge era; not a reliable history.)

**Unresolved:**
- **Phase 2 duration**: "≥6 patches OR ≥6 weeks" is a guess. If patch cadence is rapid (3-day patches during a meta turbulence period), 6 patches may be too short to feel confident. Subtask 1 should pick a calendar-floor lower bound (e.g., "≥6 patches AND ≥45 days").
- **What "would have been proposed" means in shadow mode**: the workflow uploads the diff JSON as a build artifact. Curator either downloads it from the workflow run or the workflow comments the proposal markdown on a long-running tracking issue. Lean tracking issue — easier to scan in a browser. Pin in subtask 4.

---

## 11. Subtask Boundaries

**Decision: each ROADMAP subtask owns specific design sections. Cross-cutting sections (§4, §5, §9) split as below. Each subtask resolves the unresolved sub-questions in its owned sections; it MAY flag new unresolveds for downstream subtasks, but MAY NOT redesign locked decisions in earlier sections without explicit curator approval.**

**Renumbered 2026-05-05:** §11 originally listed 5 subtasks per the ROADMAP, but planning subtask 3 surfaced two unscoped components — source fetchers and the threshold evaluator — neither of which fit cleanly into the diff-generator subtask without scope creep, and neither into the workflow subtask without making it impossibly large. Both became their own subtasks. Total subtask count: 7.

| ROADMAP Subtask | Owns design sections | What this subtask produces |
|---|---|---|
| 1 — Signal design deep-dive | §1, §2, §10 (validation strategy detail), §4 schema only, §9 prompt content only | Source-disagreement rule (§1), patch-detection mechanism (§2), health-check definition (§2), per-hero threshold pin (§2), threshold-evaluator output schema. `pipeline_state.json` schema fields needed for thresholds + freezes (§4 file structure, not workflow behavior). The LLM prompt's classification rules text (§9 content, not call infrastructure). Phase-2 calendar-floor (§10) |
| 2 — Stats sidecar / persistence | §3 | Sidecar JSON schema with per-source breakdown, retention policy, write atomicity. Provides the read/write API the threshold evaluator (subtask 4) and source fetchers (subtask 3) call |
| 3 — Source fetchers (NEW) | §1 ingestion shapes (per the research note); subtask 1 §3 health-check definition | Three fetcher modules: bazaardb (Playwright + DOM extraction), Mobalytics (HTTP + `window.__PRELOADED_STATE__` traversal), bazaar-builds.net (wrap existing enricher + the two date-extraction fixes from the research note). Each fetcher emits a `WindowObservation` per subtask 2's API plus a per-source health status. Includes the patch-label extraction for bazaardb (subtask 1 §2 picked DOM-as-authoritative) |
| 4 — Threshold evaluator (NEW) | §2 threshold rules, §1 source-disagreement rule, §10 phase logic | The engine that consumes fetcher output (subtask 3) + sidecar history (subtask 2) + `pipeline_state.json` + current catalog and emits the threshold-evaluator output schema from subtask 1 §5. Implements the §2 add/remove threshold rules with patch-window semantics for bazaardb and 30-day windows for bazaar-builds.net. Implements the source-quality gate (`carry`/`core` require bazaardb confirmation). Honors freeze toggles and phase 2 dry-run |
| 5 — Diff generator + LLM (was 3) | §8, §9 LLM call infrastructure | Diff JSON shape, proposal-markdown rendering, archetype-reshuffle handling (or explicit defer), LLM call wiring (model, prompt loading, hallucination validation). Subtask 1's prompt rules text is loaded by this subtask's call code |
| 6 — GitHub Actions workflow (was 4) | §5, §6, §7, §4 cron behavior, §10 phase mechanics | Workflow YAML, cron schedule, concurrency control, PAT setup, rolling-PR-per-hero, freeze evaluation at run time, `dry_run`/`phase` flag handling, retry policy for Cloudflare/Playwright |
| 7 — Review tooling (was 5) | (no current section — flagged for new design pass if non-trivial) | Whatever surfaces per-proposal stats; consumes subtasks 2 + 5 outputs |

Concrete scope rules for subtask 1 (historical reference; subtask 1 is complete and merged):

- **In scope**: §1 + §2 + the §4 / §9 / §10 carve-outs above. Output is a written spec, not code.
- **Out of scope**: §3 stats sidecar shape, §8 full diff JSON shape (subtask 5 owns), §5 PR mechanics, §6 cron schedule, §7 PAT, §9 LLM call infrastructure (model selection, prompt-file location, version bumping).

**Rejected: one subtask per section**

Five subtasks vs. nine sections forces an arbitrary mapping. Cross-cutting sections (§4 patch-day, §5 PR mechanics, §9 LLM) genuinely span multiple implementation chunks; the table above splits them along their natural seams (schema vs. behavior, content vs. call infrastructure).

**Rejected: leave subtask boundaries implicit**

Without an explicit map, the natural failure mode is subtask 1 producing a full pipeline spec (because §1 and §2 reference §3 / §8 / §9) and subtasks 2-4 becoming code-only sessions with no design surface to push back on. The curator loses the per-subtask review checkpoint.

**Unresolved:**
- **LLM prompt artifact ownership**: subtask 1 wrote the prompt's classification rules; subtask 5 owns where the prompt file lives and how versioning works.
- **Subtask 7 design**: the "review tooling" subtask has no current design section. If it grows beyond a PR-comment template, it likely deserves its own design pass before implementation. Defer until subtasks 1-6 are done and the gap is concrete.
