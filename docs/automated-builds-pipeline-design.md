# Automated Builds Refresh Pipeline — Architecture Design

*Session: 2026-05-04. Pre-implementation deep-dive for ROADMAP "Automated Builds Refresh Pipeline". No code in this session. Builds on `build-enricher-split-design.md`; both `<hero>_builds.json` and `builds_schema.json` remain stable contracts.*

---

## 1. Signal Source(s)

**Decision: bazaar-builds.net stays primary. In-house tracker SQLite is a secondary sanity check, not an independent trigger. BazaarDB and community ladder data are parked.**

The current enricher already scrapes bazaar-builds.net and is the only source with usable coverage today. Switching the primary off it without a validated replacement throws away the existing dry-run baseline (subtask 1's test plan: replay against historical artifacts).

In-house runs from `bazaar_runs.db` (one curator, occasionally a few opt-in players if multi-player upload ever lands) cannot drive add/remove decisions on their own — sample size is too small to distinguish a meta shift from one bad week of runs. Use them only as a confirmation lens in the PR body ("this candidate-remove item appeared in N tracker runs in the same window"), never as a gate.

**Rejected: BazaarDB API as primary**

Status unknown. The "Build Archetype Images" ROADMAP entry already notes outreach is open. Until BazaarDB confirms a public API and licensing terms, treat it as research-only. **Unresolved:** confirm whether BazaarDB exposes a stable, documented API and what its terms allow.

**Rejected: community ladder data**

Tempo Storm doesn't publish a public ladder feed that I'm aware of. **Unresolved:** confirm — if there's an undocumented endpoint or third-party aggregator with terms allowing redistribution, this could become a primary source on its own merit (richer than scraped build posts).

**Rejected: weighted blend across sources from day one**

A weighting scheme (e.g., 70% scrape + 30% tracker) requires per-source calibration that's hard to validate with current sample volume. Defer until at least one secondary source has been observed to disagree with the primary in a useful way.

**Unresolved sub-questions:**
- Licensing posture for ongoing automated scraping of bazaar-builds.net. Manual enricher use is presumably tolerated; a daily-or-weekly bot is more visible. Worth a courtesy email to the site owner before the cron goes live.
- Whether the in-house tracker should aggregate runs across heroes for cross-validation (cross-hero item appearance is sometimes informative — e.g., a generic utility item).

---

## 2. Add vs. Remove Signal Asymmetry

**Push back on the framing.** "Auto-add liberal, auto-remove conservative" is the right shape for a system that auto-applies. This pipeline doesn't auto-apply — every change goes through curator PR review (§5). The pipeline's job is *evidence assembly*, not catalog mutation. Both sides should be conservative on the *proposal* threshold so the curator's review queue stays signal-rich.

**Decision: both sides conservative. Add and remove are *proposed* with concrete thresholds; the curator is the liberal/conservative axis.**

Initial thresholds (subject to subtask 1 dry-run validation):

| Direction | Threshold | Notes |
|---|---|---|
| Add candidate (existing archetype, missing item) | item appears in ≥2 of last 3 windows AND freq ≥0.4 in latest window AND latest window sample_count ≥3 | Mirrors today's enricher's `core_threshold`/`support_threshold` logic but applied across windows, not within one |
| Add candidate (new archetype) | tag appears in ≥2 of last 3 windows with sample_count ≥2 each AND ≥1 candidate core item carries across | Avoids one-off hot-take posts spinning up archetypes |
| Remove candidate (item) | item present in catalog but absent from observed builds for last 4 consecutive windows | Window cadence ties to §6 |
| Remove candidate (archetype) | archetype name unmatched by any tag for last 6 consecutive windows | Higher bar — archetype removal is more disruptive |

"Candidate" means *surfaced in the PR body*, not applied. Removals never auto-mutate the catalog file; they appear in the diff JSON's `*_removal_candidates` slot for curator action.

**No "deprecated" interim state in the catalog.** The schema is a stable contract (per scope). A removal candidate that survives N windows could be auto-promoted from "soft suggest" to "strong suggest" in the PR body's wording, but the catalog JSON itself stays clean.

**Rejected: auto-apply removes after K windows of absence**

Catalog ships to all players via `refresh-builds`. Auto-removal means a single source outage (bazaar-builds.net down for a week, or scrape selectors break silently) drops items from the player-facing contract without a human ever seeing it.

**Unresolved:**
- Window cadence (§6) directly determines what "4 consecutive windows" means in calendar time. Pin those together.
- Whether thresholds should differ per hero (some heroes have lower scrape volume — Pygmalien posts ≪ Dooley posts). Default no; revisit if dry-run shows a hero starves the pipeline.

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

Runtime budget: today's enricher fetches a category index page plus a handful of post pages per hero. Empirically <2 min per hero. Five heroes × 2 min ≈ 10 min total, well under GitHub Actions' 6-hour cap. Schedule cadence is not runtime-constrained.

Concurrent-run handling: `concurrency: group: pipeline-${{ github.workflow }}, cancel-in-progress: false`. At weekly cadence, an in-progress run when the next cron fires is essentially impossible barring a hung scrape; queueing is fine.

**Rejected: daily**

See above.

**Rejected: on-demand only (no cron)**

Defeats the purpose of automation. The whole work item is "make the curator's role 'review the PR' instead of 'run the enricher.'" Removing the trigger turns this back into a manual workflow with extra YAML.

**Unresolved:**
- Day-of-week / time-of-day for the cron. Bias toward off-peak (e.g., Sunday 06:00 UTC) so the curator has a fresh PR ready Monday morning. Defer to subtask 4.
- Whether the cron should also fire on push to bazaar-builds `main` (i.e., when the enricher itself changes). Probably yes for safety — a bug fix in the scraper should re-run against fresh data without waiting up to a week.

---

## 7. Cross-Repo PR Permissions

**Recommendation: fine-grained Personal Access Token, scoped to bazaar_tracker, stored in the bazaar-builds repo's Actions secrets as `TRACKER_PR_TOKEN`. Rotate on a fixed cadence (~90 days).**

The default `GITHUB_TOKEN` issued to a workflow is scoped to its own repo and cannot push or open PRs against a different repo. Three credible alternatives:

| Mechanism | Pros | Cons |
|---|---|---|
| Fine-grained PAT | Simple, per-repo scoping, low setup | Tied to a user identity (curator's account); manual rotation; PAT revocation is the single recovery action if leaked |
| GitHub App | Repo-scoped install, no user identity, audit logs, automatic short-lived tokens | More setup (app registration, install, permission config); overkill for a solo curator workflow |
| Deploy key | Push-only, no API surface for PR creation | Doesn't authorize `gh pr create` / REST PR endpoints — would need a hybrid (deploy key + something else for PR API) |

For a solo-curator workflow with one bot identity, the PAT's downsides (tied to the curator's account, manual rotation) are tolerable. The GitHub App is the "right answer" the moment the project has more than one human or needs audit logging — flag for revisit then.

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
        "missing_core": [{"item": "X", "windows_seen": 3, "freq_latest": 0.71, "first_seen_window": "..."}],
        "missing_support": [...]
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
