# Automated Builds Refresh Pipeline - Subtask 1 Signal Spec

*Session: 2026-05-05. Subtask 1 deliverable for ROADMAP "Automated Builds Refresh Pipeline". Written spec only; no pipeline code, tests, prompt-file location, workflow YAML, stats sidecar schema, or full diff schema in this session.*

---

## 1. Source-Disagreement Resolution Rule

**Decision: bazaardb wins inclusion/exclusion when it is healthy and has current-patch evidence for the matching hero + archetype. Mobalytics and bazaar-builds.net can trigger add candidates or block removals, but cannot override a healthy bazaardb absence into `carry` or `core`.**

The threshold evaluator applies source precedence only after each source is marked healthy or unhealthy for the run (see Section 3).

| Case | Result |
|---|---|
| Healthy bazaardb shows item present in matching archetype | Item is treated as statistically present. It may clear add thresholds and may be classified as `carry`, `core`, or `support` by the LLM stage. |
| Healthy bazaardb shows archetype present | Archetype is treated as statistically present. It may clear new-archetype thresholds. |
| Healthy bazaardb shows item absent, Mobalytics or bazaar-builds.net shows present | No remove candidate while secondary evidence is current, but classification is capped at `support`. The disagreement is surfaced to the diff generator as `secondary_present_bazaardb_absent`. |
| Healthy bazaardb shows archetype absent, Mobalytics shows current meta build | No archetype removal candidate. The disagreement is surfaced as `mobalytics_present_bazaardb_absent`. |
| Healthy bazaardb shows item absent, Mobalytics absent, bazaar-builds.net absent | Eligible for absence counting toward the item-removal threshold. |
| Healthy bazaardb shows archetype absent, Mobalytics absent, bazaar-builds.net absent | Eligible for absence counting toward the archetype-removal threshold. |
| bazaardb unhealthy | This run does not count toward bazaardb presence or absence thresholds. Secondary sources may still produce add candidates and removal blockers, but not removal evidence. |
| Secondary source unhealthy | That source contributes no presence or absence signal this run. Its missing data does not count as absence. |

Short-form evaluator rule:

```text
if bazaardb.healthy:
    if bazaardb.present:
        canonical_presence = present
        classification_ceiling = carry_core_support
    else if any healthy secondary source is present:
        canonical_presence = disputed_present
        classification_ceiling = support
        removal_blocked = true
    else:
        canonical_presence = absent
        removal_absence_window = true
else:
    canonical_presence = unknown
    removal_absence_window = false
    secondary_adds_allowed = true
```

**Rejected: weighted cross-source vote**

The architecture design already rejected weighted blending. A vote would let two secondary sources outvote the canonical statistical source and would blur why a proposal appeared.

**Rejected: secondary sources can force `carry` / `core`**

The source-quality gate in the locked design requires bazaardb confirmation for scarce buckets. Secondary-only items stay `support` even when editorially important.

**Unresolved for downstream subtasks:**

- Cross-source deduplication remains a dry-run sanity check. Subtask 1 does not define a dedupe algorithm.

---

## 2. Patch-Detection Mechanism

**Decision: read the current bazaardb patch label from the rendered DOM and compare it to `pipeline_state.json` when an expected label is present. DOM is authoritative; state is a curator guardrail and PR-provenance field.**

The cron/evaluator needs the source's current patch window, not a calendar window. The implementation should extract:

| Field | Source | Required |
|---|---|---|
| `bazaardb_patch_label` | Rendered bazaardb DOM, from the patch-notes link text or adjacent patch indicator | Yes for a healthy bazaardb run |
| `bazaardb_patch_notes_url` | Rendered bazaardb DOM link target | Yes when the link is present; null only if the label was extracted from nearby text |
| `expected_bazaardb_patch_label` | `pipeline_state.json` | Optional |
| `patch_label` | `pipeline_state.json`, free-form curator provenance | Optional |

If `expected_bazaardb_patch_label` is non-null and does not match the DOM label, bazaardb is marked unhealthy for threshold purposes for that run. The run may still emit a health failure artifact; it must not count the window as an absence window.

`patch_label` remains free-form provenance. It is not used to infer the active bazaardb window.

**Why both:** bazaardb owns the actual reporting window. The state-file check catches selector drift, stale Cloudflare/browser output, or curator expectation mismatches without letting a manually edited label silently redefine the data window.

**Rejected: infer patch solely from `pipeline_state.json`**

That would make a manual bookkeeping field the source of truth for a source-scoped statistical window.

**Rejected: read only the DOM with no consistency check**

The DOM is authoritative, but the pipeline needs a cheap human-controlled tripwire on patch days.

---

## 3. Health-Check Definition

**Decision: a source is healthy only when fetch, parse, identity, and plausibility checks all pass. Unhealthy runs contribute no absence evidence.**

Health is source-scoped for the run, with optional hero-level notes. A healthy source can still have no evidence for a particular hero or archetype; that is absence only where the source's shape supports absence.

| Source | Healthy when all are true | Unhealthy when any are true |
|---|---|---|
| `bazaardb` | Headless browser returns the real page, not a Cloudflare challenge; patch label is extracted; at least one item/archetype evidence group is parsed from the page; item names come from non-empty `img[alt]` values; run/frequency text parses for at least one item; no `expected_bazaardb_patch_label` mismatch. | Browser fails or times out; challenge/error page is detected; patch label missing; zero item/archetype groups parsed; all parsed items lack run/frequency context; expected patch mismatch. |
| `mobalytics_meta_builds` | HTTP 200; `window.__PRELOADED_STATE__` exists and parses as JSON; document metadata includes a non-empty `version`; at least one `TheBazaarDocumentCmWidgetBoardCreatorV1` node is found; each retained build has title plus at least one `cards[].name`; duplicate/empty card names are dropped without emptying every build. | HTTP failure; PRELOADED_STATE missing or invalid; document/version missing; zero board-creator nodes; all retained builds have empty card lists; schema path changes so extraction relies on fallback text. |
| `mobalytics_build_articles` | For every article attempted: HTTP 200; PRELOADED_STATE parses; at least one `NgfDocumentCmWidgetGameDataCardGridV2` node with `items[].title` is found. Source health for this optional sub-source is `skipped` if no article slugs are configured for the run. | Any attempted article cannot be fetched or parsed; article contains no card-grid item lists; listing/article extraction changes shape. A skipped optional sub-source contributes no presence or absence. |
| `bazaar_builds_net` | Category page fetch succeeds; individual post fetches are attempted with `--fetch-posts`; at least one retained post has a parsed `datePublished` or category date; retained posts obey the requested 30-day/since filter; known-item matching runs against a non-empty known-items list; output has either at least one dated retained record or a clean zero-record result caused by no recent posts. | Category fetch fails; post fetches are not attempted; all retained records are undated; date filter cannot be proven operative; known-items list is empty; parser emits records but no title/url/date identity for them. |
| `in_house_tracker` | SQLite file opens read-only; expected tables can be queried; query returns zero or more rows without error for the requested hero/window. | Database missing when requested; open/query failure; schema mismatch; hero filter cannot be applied. |

Absence-counting rules:

| Source | Can count presence | Can count absence | Notes |
|---|---:|---:|---|
| `bazaardb` | Yes | Yes, when healthy | Canonical absence source for remove thresholds. |
| `mobalytics_meta_builds` | Yes | Yes as a removal blocker check only when healthy and document version is current | Editorial omission does not increment consecutive absence by itself. |
| `mobalytics_build_articles` | Yes | No | Optional supplemental source; skipped is not unhealthy. |
| `bazaar_builds_net` | Yes | Yes as a removal blocker check only when healthy and the 30-day window is dated | No recent posts means "no blocker", not canonical removal absence. |
| `in_house_tracker` | Yes | No | PR-body sanity check only. |

**Rejected: zero rows always means unhealthy**

For bazaar-builds.net, a clean dated 30-day query can legitimately have no recent posts for a hero. That is healthy no-evidence, not a scrape failure.

**Rejected: undated bazaar-builds.net records are allowed in cron**

The research doc found silent date regressions. Cron health must fail if the 30-day window cannot be proven from parsed dates.

---

## 4. Per-Hero Threshold Pin

**Decision: thresholds stay the same across all heroes for v1.**

The initial threshold pins remain:

| Candidate | Threshold |
|---|---|
| Add item to existing archetype | bazaardb present in >=2 of last 3 patches, OR current Mobalytics build lists it, OR bazaar-builds.net present in >=2 of last 3 dated 30-day windows with latest frequency >=0.4 and latest sample_count >=3 |
| Add new archetype | bazaardb archetype tag present in >=2 of last 3 patches, OR Mobalytics meta-builds lists it as current |
| Remove item | healthy bazaardb absence for K=4 consecutive patches AND >=21 elapsed calendar days across those patches, AND no current Mobalytics evidence, AND no healthy bazaar-builds.net evidence in the last 30 days |
| Remove archetype | healthy bazaardb absence for K=6 consecutive patches AND >=45 elapsed calendar days across those patches, AND no Mobalytics meta-build, AND no healthy bazaar-builds.net evidence |

Rationale: per-hero tuning before shadow data exists would be premature and hard to review. Low-volume heroes may produce fewer proposals, but that is safer than making hero-specific thresholds without observed false-positive rates.

**Rejected: lower thresholds for low-volume heroes now**

Pygmalien and other lower-volume heroes may starve the pipeline, but subtask 1 has no historical bazaardb data to calibrate hero-specific exceptions.

**Unresolved for downstream review:**

- Revisit hero-specific thresholds after Phase 2 shadow output shows repeated starvation or noise for a specific hero.

---

## 5. Threshold-Evaluator Output Schema

**Decision: the threshold evaluator emits one JSON document containing per-source health plus normalized per-(hero, archetype, item) rows. It does not embed rolling stats-sidecar internals or the full diff JSON shape.**

Top-level shape:

```json
{
  "schema_version": 1,
  "generated_at": "2026-05-05T12:00:00Z",
  "run_id": "2026-05-05T120000Z",
  "hero": "Karnok",
  "bazaardb_patch": {
    "label": "Apr 29",
    "patch_notes_url": "https://...",
    "expected_label": "Apr 29",
    "matched_expected": true
  },
  "source_health": [
    {
      "source": "bazaardb",
      "status": "healthy",
      "window_id": "bazaardb:Apr 29",
      "checked_at": "2026-05-05T12:00:00Z",
      "details": []
    }
  ],
  "rows": []
}
```

Per-row shape:

```json
{
  "hero": "Karnok",
  "phase": "late",
  "archetype": "Anaconda",
  "archetype_status": "existing",
  "item": "Karst",
  "catalog_membership": "missing",
  "source_presence": {
    "bazaardb": "present",
    "mobalytics_meta_builds": "absent",
    "mobalytics_build_articles": "skipped",
    "bazaar_builds_net": "present",
    "in_house_tracker": "skipped"
  },
  "canonical_presence": "present",
  "classification_ceiling": "carry_core_support",
  "threshold_result": "add_candidate",
  "threshold_reason": "bazaardb_present_2_of_3_patches",
  "removal_blocked_by": [],
  "disagreement": "none",
  "llm_input_required": true,
  "evidence_refs": [
    {
      "source": "bazaardb",
      "artifact_ref": "artifacts/bazaardb/karnok-2026-05-05.json",
      "summary": "present in matching archetype in latest patch"
    }
  ]
}
```

Field contract:

| Field | Type | Values / notes |
|---|---|---|
| `schema_version` | integer | Starts at `1`. |
| `generated_at` | string | UTC ISO-8601 timestamp. |
| `run_id` | string | Stable identifier for this evaluator run. |
| `hero` | string | One supported hero per document. |
| `bazaardb_patch` | object or null | Present when bazaardb was attempted. |
| `source_health[]` | array | One row per attempted source/sub-source. |
| `source_health[].source` | string enum | `bazaardb`, `mobalytics_meta_builds`, `mobalytics_build_articles`, `bazaar_builds_net`, `in_house_tracker`. |
| `source_health[].status` | string enum | `healthy`, `unhealthy`, `skipped`. |
| `source_health[].window_id` | string or null | Source-native window id, not a stats-sidecar key contract. |
| `source_health[].details[]` | array of strings | Short health notes or failure reasons. |
| `rows[]` | array | Per candidate/evaluated item rows. |
| `rows[].phase` | string or null | Catalog phase when known; null for unmatched new archetype candidates. |
| `rows[].archetype` | string | Catalog archetype name or normalized candidate tag. |
| `rows[].archetype_status` | string enum | `existing`, `candidate_new`, `unknown`. |
| `rows[].item` | string or null | Null only for archetype-level rows. |
| `rows[].catalog_membership` | string enum | `present`, `missing`, `not_applicable`. |
| `rows[].source_presence.*` | string enum | `present`, `absent`, `unknown`, `unhealthy`, `skipped`. |
| `rows[].canonical_presence` | string enum | `present`, `absent`, `disputed_present`, `unknown`. |
| `rows[].classification_ceiling` | string enum | `carry_core_support`, `support_only`, `not_applicable`. |
| `rows[].threshold_result` | string enum | `add_candidate`, `remove_candidate`, `no_change`, `blocked`, `insufficient_history`, `health_blocked`. |
| `rows[].threshold_reason` | string enum | `bazaardb_present_2_of_3_patches`, `mobalytics_current_build`, `bazaar_builds_net_2_of_3_windows`, `bazaardb_absent_4_patches_21_days`, `bazaardb_absent_6_patches_45_days`, `secondary_present_bazaardb_absent`, `source_unhealthy`, `not_enough_windows`, `none`. |
| `rows[].removal_blocked_by[]` | array of string enum | Any of `mobalytics_meta_builds`, `mobalytics_build_articles`, `bazaar_builds_net`, `freeze_removals`. |
| `rows[].disagreement` | string enum | `none`, `secondary_present_bazaardb_absent`, `bazaardb_present_secondary_absent`, `source_unhealthy_conflict`. |
| `rows[].llm_input_required` | boolean | True for rows that should enter carry/core/support classification. |
| `rows[].evidence_refs[]` | array | Pointers/summaries only; not the stats sidecar schema. |

Archetype-level rows use `item: null`, `catalog_membership: not_applicable`, and `classification_ceiling: not_applicable`.

**Rejected: include rolling counters in this schema**

Counters such as windows seen, first/last seen, and per-window sample breakdown belong to subtask 2's stats sidecar. This schema only names the evaluator contract that downstream stages read.

**Rejected: make this the full diff JSON**

Subtask 3 owns diff JSON shape and PR rendering. This output is the threshold stage's normalized handoff.

---

## 6. `pipeline_state.json` Schema

**Decision: `pipeline_state.json` is curator-edited run state. Subtask 1 defines fields only; subtask 4 owns how workflow code consumes them.**

Schema:

```json
{
  "schema_version": 1,
  "phase": "local_dry_run",
  "dry_run": true,
  "patch_label": "13.4",
  "expected_bazaardb_patch_label": "Apr 29",
  "freeze_removals_until": "2026-05-18",
  "hero_freezes": {
    "Karnok": {
      "freeze_removals_until": "2026-05-18",
      "notes": "Post-patch freeze for Karnok item changes"
    }
  },
  "notes": "Set after Tempo Storm 13.4 dropped 2026-05-04"
}
```

Field contract:

| Field | Type | Required | Notes |
|---|---|---:|---|
| `schema_version` | integer | Yes | Starts at `1`. |
| `phase` | string enum | Yes | `implementation`, `local_dry_run`, `shadow_cron`, `live_cron`. |
| `dry_run` | boolean | Yes | Manual gate for no-PR operation. |
| `patch_label` | string or null | Yes | Free-form curator provenance for summaries. Not used to infer bazaardb's active patch. |
| `expected_bazaardb_patch_label` | string or null | Yes | Optional consistency check against the DOM-extracted bazaardb label. |
| `freeze_removals_until` | string date or null | Yes | Global removal freeze date in `YYYY-MM-DD`. |
| `hero_freezes` | object | Yes | Per-hero overrides keyed by hero display name. Empty object when unused. |
| `hero_freezes.*.freeze_removals_until` | string date or null | Yes inside entry | Per-hero removal freeze date. |
| `hero_freezes.*.notes` | string | No | Curator note. |
| `notes` | string | No | Free-form curator note for the run state. |

If both global and per-hero freezes exist, subtask 4 will decide evaluation behavior. This spec only reserves both fields so the state file can represent either choice.

**Rejected: catalog-level patch fields**

The architecture design rejected adding patch pins to `<hero>_builds.json`; the runtime tracker does not need them.

---

## 7. LLM Prompt Classification Rules Text

**Decision: the prompt rules classify only `carry`, `core`, and `support`. `condition_items` are catalog state, not an LLM output bucket for v1.**

Prompt input context required per archetype:

| Input | Purpose |
|---|---|
| Hero name | Keep hero-specific item/archetype context explicit. |
| Phase | Helps compare early/mid/late expectations. |
| Archetype name or candidate tag | Classification unit. |
| Existing catalog buckets | Include `condition_items`, `carry_items`, `core_items`, and `support_items`. |
| Known item names | Guardrail; output items must be from this set or from the provided evidence rows. |
| Threshold-evaluator rows | The items that cleared deterministic gating plus source presence and classification ceiling. |
| Mobalytics editorial text | Context for item roles when available. |
| Source summaries | Compact bazaardb / Mobalytics / bazaar-builds.net evidence, with unhealthy sources marked explicitly. |

Prompt rules text:

```text
You classify Bazaar build items for one hero archetype.

Return only classifications for items provided in the input evidence or already present in the existing catalog state. Do not invent item names. If an item name is not in the known-items list, mark it as invalid instead of correcting it.

Use exactly these output buckets:
- carry: the primary win condition or main damage/scaling engine. A build usually has 1 carry. A small list of carry alternatives is allowed when the evidence clearly frames them as interchangeable win conditions.
- core: items that are central to making the archetype function and are expected in most successful versions. A build usually has 2-3 core items.
- support: useful, situational, enabling, defensive, economy, or flex items. Use support for anything with weaker or secondary-only evidence.

Hard source-quality gate:
- An item may be classified as carry or core only when the threshold row says `classification_ceiling` is `carry_core_support` and latest-window bazaardb evidence is present for the matching hero + archetype.
- If bazaardb is healthy and absent while Mobalytics or bazaar-builds.net is present, classify the item as support.
- If bazaardb is unhealthy or skipped for this archetype, classify new or disputed items as support unless they are already cataloged as carry/core; for existing carry/core items, keep the current bucket and flag the missing bazaardb confirmation in the rationale.

Condition item rule:
- Existing `condition_items` are hand-maintained requirements for an archetype. Do not move items into or out of `condition_items`.
- If an existing condition item appears in the evidence, mention it only in the rationale when it affects the role of other items.

Catalog stability rules:
- Prefer the existing catalog bucket when evidence is consistent with it.
- Do not demote an existing carry/core item solely because a secondary source omits it.
- Do not promote a secondary-only item above support.
- Use the source disagreement labels from the threshold rows; do not re-decide inclusion or removal thresholds.

Bucket-size guidance:
- Prefer 1 carry.
- Prefer 2-3 core items.
- Put the rest in support.
- If the evidence strongly justifies a larger carry/core set, keep it and explain why in the rationale.

Rationale requirements:
- Cite the source mix in plain language.
- Mention when bazaardb gates or caps the classification.
- Keep rationale short enough for a PR review table.

Confidence:
- high: sources agree and bazaardb confirms the role.
- medium: bazaardb confirms presence but role is inferred mostly from editorial/contextual evidence.
- low: evidence is thin, conflicting, or secondary-only.
```

Expected output slots from the LLM call are intentionally minimal for this subtask:

| Field | Values |
|---|---|
| `item` | Known item name from input. |
| `classification` | `carry`, `core`, `support`, `invalid`. |
| `confidence` | `high`, `medium`, `low`. |
| `rationale` | Short text. |

**Rejected: LLM extracts Mobalytics item names**

The research doc resolved that Mobalytics item names are structured in PRELOADED_STATE. The LLM only classifies.

**Rejected: LLM edits `condition_items`**

The locked design says `condition_items` are hand-maintained unless confirmed otherwise. V1 leaves them untouched.

**Unresolved for downstream subtasks:**

- Subtask 3 still owns prompt file location, prompt versioning, model choice, call wiring, and hallucination validation mechanics.

---

## 8. Phase-2 Calendar Floor

**Decision: Phase 2 exits only after at least 6 distinct bazaardb patch windows AND at least 60 elapsed calendar days of shadow cron output.**

The locked design's ">=6 patches OR >=6 weeks" is too permissive during rapid patch cadence. Sixty days gives the pipeline at least two months of selector stability, health-check behavior, and curator review samples while still keeping the rollout finite.

Exit criterion:

```text
phase_2_complete = distinct_healthy_bazaardb_patch_windows >= 6
                   AND elapsed_shadow_days >= 60
                   AND curator manually flips dry_run/phase
```

No automatic promotion to live cron. The curator flips the state file after reviewing the shadow output.

**Rejected: six patches alone**

Rapid balance patches can compress six windows into a short turbulence period.

**Rejected: calendar time alone**

Sixty quiet days with few patches does not validate patch-window remove thresholds.

---

## 9. Phase-1 Local Dry-Run Plan

**Decision: Phase 1 validates Mobalytics and bazaar-builds.net signal logic against existing source-shape samples and current enricher compare artifacts before any cron PR behavior exists. bazaardb only produces the first patch snapshot in this phase.**

Artifacts to use:

| Artifact | Purpose |
|---|---|
| `bazaar-builds/research/samples/mobalytics/meta-builds-preloaded-state-builds.json` | Validate Mobalytics structured item extraction and hero/build matching. |
| `bazaar-builds/research/samples/mobalytics/tortuga-vanessa-kripp-build.json` | Validate article-style phase item extraction for one known article shape. |
| `bazaar-builds/research/samples/bazaar_builds_net/karnok_smoke_test_with_posts.json` | Validate bazaar-builds.net item grouping and expose the known date-health failure until enricher date fixes land. |
| `bazaar-builds/research/samples/bazaar_builds_net/anaconda-karnok-post-sample.json` | Validate per-post JSON-LD date + item extraction expectations. |
| `bazaar-builds/research/samples/bazaardb/meta-page-sample.json` | Validate first bazaardb snapshot parsing shape; not historical threshold behavior. |
| `bazaar_tracker/<hero>_builds.json` | Existing catalog comparison target. |
| `bazaar_tracker/card_cache_names.txt` | Known-item guardrail. |

Validation shape:

1. Run the current enricher against bazaar-builds.net with dated post fetching once the two date fixes from the research doc are in place:

```powershell
python bazaar_build_enricher.py https://bazaar-builds.net/category/builds/karnok-builds/ --hero Karnok --days 30 --fetch-posts --catalog-dir ../bazaar_tracker --names-file ../bazaar_tracker/card_cache_names.txt --output artifacts/karnok_bazaar_builds_summary.json
```

2. Compare the artifact against the current catalog using the existing compare command:

```powershell
python bazaar_build_enricher.py compare artifacts/karnok_bazaar_builds_summary.json ../bazaar_tracker/karnok_builds.json --output artifacts/karnok_build_update_proposal.md
```

3. Run the threshold evaluator locally in dry-run mode once subtasks 2-4 provide the command surface. The expected command shape is:

```powershell
python -m automated_builds_pipeline.thresholds --hero Karnok --state pipeline_state.json --catalog-dir ../bazaar_tracker --source-artifacts research/samples --dry-run --output artifacts/karnok_threshold_rows.json
```

4. Compare outputs:

| Compare | Expected result |
|---|---|
| Existing enricher proposal vs. threshold rows | Bazaar-builds.net repeated item candidates should appear as `add_candidate` or `no_change`, not disappear silently. |
| Research Mobalytics sample vs. threshold rows | Items in current Mobalytics meta builds should appear as add candidates or removal blockers for matching archetypes. |
| Known date-broken bazaar-builds.net sample vs. health rows | Source should be `unhealthy` because retained records are undated. |
| Current catalog buckets vs. LLM prompt input | Existing `condition_items` are present as context but not emitted as a writable classification bucket. |
| Bazaardb first snapshot vs. threshold rows | Rows may show presence/add candidates, but remove thresholds should be `insufficient_history`. |

5. Curator review pass:

The curator checks the generated threshold rows and proposal markdown side by side with the existing manual proposal artifact. The pass is successful when Mobalytics and bazaar-builds.net add/removal-blocking behavior matches what the curator would have reviewed manually, and bazaardb produces a healthy first snapshot without any removal candidates.

**Rejected: require full pipeline commands in this doc**

The pipeline command surface does not exist yet. This plan names the validation shape and artifacts without assigning workflow implementation to subtask 1.

**Unresolved for downstream subtasks:**

- The concrete threshold command name/flags belong to the implementation subtasks.
- Subtask 2 must decide how the Phase 1 threshold rows are persisted into the stats sidecar after validation.
