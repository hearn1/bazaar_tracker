# Bazaar Tracker - Roadmap

Active work tracker. Project context, architecture, and stable design notes live in `CLAUDE.md`.

Status labels:
- `Done`: implemented and expected to stay closed.
- `Implemented - verify live`: code exists, but needs confirmation on one or more real runs.
- `Partial`: useful foundation exists, but the feature is not complete enough to close.
- `Open`: not yet implemented.
- `Deprioritized`: low priority feature, leaving as a potential enhancement.

## Open Feature Work

### Overlay Coach/Run UI Refactor - Open

Goal: implement the `claudeDesign/PIVOTS_HANDOFF.md` direction for the live overlay. Adopt Direction B ("Build-first, two-zone") and use the Variant D pivot pane ("Hot list + chips") as the canonical way to show pivot distance.

Current state verified from local code/prototypes:
- `claudeDesign/PIVOTS_HANDOFF.md` chooses Direction B and Variant D.
- `claudeDesign/tracker-direction-b.jsx` and `claudeDesign/tracker-pivots.jsx` contain the prototype behavior and visual structure.
- `web/static/overlay.html` still renders Coach as active build, find card, build override, checklist, coach prompts.
- `web/static/overlay.html` still shows PvP/Tier in the global header even though this is run-context.
- `web/static/overlay.html` already has a useful Run tab foundation with current snapshot, phase guidance, economy priorities, hero reminder, and pivot signals.
- `web/build_helpers.py` `score_archetypes` currently returns only the top 3 scored archetypes. The new pivot UI needs the full archetype list scored so strong/possible/long-shot buckets can be shown honestly.

Relevant files:
- `claudeDesign/PIVOTS_HANDOFF.md` - source-of-truth design handoff
- `claudeDesign/tracker-direction-b.jsx` - Direction B prototype
- `claudeDesign/tracker-pivots.jsx` - pivot visualization variants, especially `PivotsHotChip`
- `web/static/overlay.html` - primary UI implementation
- `web/build_helpers.py` - archetype scoring helper currently capped to top 3
- `web/overlay_state.py` - `/api/overlay/state` payload, including `arch_scores`
- `web/server.py` - build catalog API used by the overlay
- `tests/test_packaging.py` - static assertions around delegated overlay handlers

Task breakdown:
- Refactor pivot scoring data:
    * Change `score_archetypes` to support returning all archetype scores, either via a `limit` argument or a separate helper.
    * Keep top-3 behavior only where it is explicitly needed; the overlay state should expose all scored archetypes for the pivot pane.
    * Include enough row metadata for the UI: `name`, `score`/`raw_score`, phase, match counts, and matched item names if available.
    * Make sure the active auto-build selection still uses the highest-scored archetype.
- Restructure the overlay frame:
    * Remove the PvP/Tier stat strip from `renderHeader`.
    * Keep the header focused on status, run id, hero, leave-run, and quit controls.
    * Render the active build hero strip only when `activeTab === "coach"` and the overlay is not idle.
    * Keep tabs hidden in idle state.
- Rebuild the Coach tab around Direction B:
    * Move active build summary text out of the active build hero and into a Notes sub-tab.
    * Make Item Checklist the first scrollable Coach content.
    * Keep Core and Carry expanded by default.
    * Collapse Support by default into a chip line, with a toggle to show rows.
    * Replace the standalone Build Override section with a sub-tab card.
    * Add Coach sub-tabs: `Pivots`, `Find card`, `Coach`, `Notes`.
    * Default the Coach sub-tab to `Pivots`.
- Implement the Pivots pane:
    * Use Variant D ("Hot list + chips").
    * Exclude the active build from the pivot list.
    * Bucket by initial thresholds: strong `>= 0.30`, possible `>= 0.05`, long-shot `< 0.05`.
    * Render strong/possible builds as full-width hot rows with inline progress fill.
    * Render long shots as a collapsible chip cloud.
    * Preserve manual build selection by clicking a pivot row/chip.
    * Keep an Auto control available so the user can clear manual selection.
- Move shared/run-context material to Run:
    * Confirm PvP, PvE, Tier, and decision count are only shown in the Run tab snapshot card.
    * Keep phase guidance and hero fundamentals in Run.
    * Keep pivot signal/watch-out guidance in Run, separate from the Coach pivot-distance UI.
    * Keep economy/universal utility pickups in Run unless the design is revised.
- Update Review:
    * Add filter chips for `All`, `Good`, `Suboptimal`, and `Missed`.
    * Filter client-side from `state.decisions` using `derived_score_label || score_label`.
    * Keep decision rows dense: small thumbnail, compact badge, two-line detail clamp.
- Preserve existing behavior:
    * Keep delegated click handling from `root.addEventListener("click", handleRootClick)`.
    * Keep manual archetype persistence via `overlay_manual_arch`.
    * Keep card search behavior and result rendering, but move it into the Find card sub-tab.
    * Keep collapse/resize/move behavior untouched unless layout requires a narrow adjustment.
    * Do not remove idle completed-run review behavior.

How to test:
- Run `python -B -m py_compile web/build_helpers.py web/overlay_state.py web/server.py`.
- Run `pytest -q tests/test_packaging.py` and any overlay/build-helper tests added for all-archetype scoring.
- Add or update a focused test proving the overlay state can expose more than three `arch_scores` when the hero catalog has more than three archetypes.
- Start `python tracker.py --no-mono`, open `/overlay`, and confirm:
    * Header no longer shows PvP/Tier.
    * Coach shows active build strip, then checklist, then sub-tabs.
    * Pivots tab shows hot rows and long-shot chips.
    * Clicking a pivot manually selects that build; Auto clears it.
    * Find card, Coach, and Notes sub-tabs render the moved content.
    * Run tab owns PvP/PvE/Tier/decision count and watch-outs.
    * Review filters change the visible decision rows.
    * Idle state still hides tabs and can reopen the last completed run.
- Spot-check on both a Karnok and Mak run because their catalogs have different archetype distributions.

### RunState Restart Board Init - Done

After a tracker restart mid-run, `RunState` starts with an empty `BoardState`. New decisions are written with empty `board_snapshot_json`, which causes the Coach tab to show 0% for all archetypes until enough new purchases rebuild the board in memory.

**Current workaround:** `web/overlay_state.py` `_build_owned_inventory_projection` prefers the latest decision with a non-empty `owned_names` snapshot instead of the absolute latest. This fixes the display but doesn't fix the root cause — post-restart decisions still record incorrect empty board snapshots in the DB.

**Real fix:** On startup, `RunState` should detect that it is rejoining an in-progress run (run already exists in DB with `outcome = NULL`) and seed `self._board` from the latest `board_snapshot_json` before processing any new events.

Relevant files:
- `run_state.py` — `RunState.__init__` or wherever the run is first identified
- `board_state.py` — `BoardState` has `owned_names_from_snapshot_json` as a static helper, can be used to deserialise the snapshot
- `db.py` — query for latest `board_snapshot_json` on the active run
- `web/overlay_state.py` — workaround in `_build_owned_inventory_projection` can be removed once this is fixed

Implementation notes:
- The run is identified by `session_id` matching an existing DB run. The seed should happen after `run_id` is established but before any decisions are inserted.
- Use `json_array_length(json_extract(board_snapshot_json, '$.owned_names')) > 0` to find the latest non-empty snapshot (same condition as the workaround query).
- `BoardState` stores items as `{instance_id: name}`. The snapshot format stores both; deserialise into the same in-memory structure so subsequent purchases append correctly.
- After this fix, remove the workaround fallback query from `_build_owned_inventory_projection` and restore the simple `ORDER BY decision_seq DESC LIMIT 1` query.

How to test:
- Start a run, buy several items, restart the tracker mid-run, buy one more item.
- Check `board_snapshot_json` on the post-restart decision — it should include all items bought before and after the restart.
- Confirm the Coach tab shows the correct full board immediately after restart without needing to buy anything first.

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

Goal: improve image coverage beyond the currently partial manifest. This work is currently on hold while waiting for BazaarDB guidance/permission on using their hosted item images as an optional local user-side cache source.

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
- Manifest entries now include image-quality diagnostics: alpha visibility, opaque-alpha coverage, RGB signal checks, `quality_flags`, and `quality_suspect`.
- `web/card_images.py` loads `static_cache/images/manifest.json`.
- `web/server.py` serves card images through `/cards/<filename>`.
- Review/overlay code can attach image URLs when manifest entries exist.
- Matt has contacted BazaarDB at `hello@bazaardb.gg` to ask whether the app may optionally download BazaarDB item images for local user-side caching only.
    * Proposed constraints: no rehosting, include attribution, provide a setting to disable it, and follow any preferred API/CDN format/cache policy/usage limits.
    * If BazaarDB is not comfortable being used as a source, Matt asked whether they can share how they solved image generation for their database.
- Images do not appear to be complete. Some have large black portions in the image like something is missing.
    * likely some type of building happens and thats how enhancements look different. We need to build the "default" non enhanced image
- Current manifest after Steam bundle extraction: 1,072 entries.
- Current Item coverage from `card_cache`: 1,043/1,123 (92.9%) manifest hits.
- Current usable coverage estimate after diagnostics: 486/1,123 (43.3%), with 557 visually suspect hits and 750 quality-warning hits.

Known bugs to report:
- `Done`: Coverage reporting can now distinguish missing images from present-but-visually-suspect images.
    * `refresh-images --coverage-only` reports coverage, usable coverage, suspect count/sample, and missing sample.
    * `extract_bazaar_bundle_pngs.py` prefers visually healthier candidates on same-card collisions instead of only comparing dimensions.
- `Open`: Some extracted card images are valid manifest hits but visually blank or mostly empty because the extractor exports a raw `Texture2D` layer instead of the composed card art.
    * Examples from live overlay screenshot: `Hunter's Boots` -> `CF_M_KAR_HuntersBoots_D.png`; `Fairy Circle` -> `CF_L_KAR_FairyCircle_D.png`.
    * Root-cause evidence: both cards resolve in `static_cache/images/manifest.json` and both PNGs are 1024x1024, but their useful alpha coverage is low/partial compared with good examples. Current diagnostics show `Fairy Circle` has ~28% visible alpha and is `quality_suspect`; `Hunter's Boots` has ~53% visible alpha and a `low_visible_alpha` warning.
    * Next fix direction: stop treating every matching `_D` texture as final card art. Use Unity Sprite/Material relationships, masks, and/or atlas crop metadata to export the composed/default display art.
- `Open`: GUID ArtKey diagnostics show the local catalog knows the missing items, but the current probe does not resolve the dependency chain from GUID/CardData to final bundle/texture.
    * `probe_catalog_guids.py` found 237/237 unique missing GUID ArtKeys in `catalog.bin`.
    * The 200-byte neighborhoods expose adjacent CardData/material/card-folder strings, but no `.bundle` names, so the next probe needs to parse Addressables catalog structure rather than relying only on nearby readable strings.

Implementation notes:
- Do not spend the next session on local extraction while BazaarDB response is pending unless explicitly requested.
- If BazaarDB approves usage, add an install/update-time optional image cache step that downloads images to the user's local cache only.
- Include attribution in-app/docs if BazaarDB images are used.
- Add a user setting to disable third-party image downloads.
- Respect any BazaarDB-preferred API/CDN format, cache policy, and usage limits.
- If BazaarDB declines or cannot help, resume the local extraction path: inspect representative bad cards through Sprite -> Texture2D, Material, mask, atlas, and CardData relationships, then export the composed/default display image rather than the raw `_D` layer.
- Use `probe_install_card_bundle.py` against representative bundles to inspect `Texture2D`, `Sprite`, atlas, and container path relationships.
- Update extractor to follow Sprite-to-Texture references and export sprite crops if full card art is packed into atlases.
- Keep the manifest keyed by normalized card name so `web/card_images.py` does not need to know Unity internals.
- Keep coverage reporting split into total manifest hits, usable hits, visually suspect hits, and missing names.

How to test:
- If BazaarDB approves usage, run install/update image caching against a small sample first and confirm images are stored locally, attributed, and disabled when the setting is off.
- Run image extraction against both user-data Addressables cache and Steam install bundles.
- Run `python tracker.py refresh-images --coverage-only` and confirm suspect counts move down when composed/default images replace raw layers.
- Confirm `static_cache/images/manifest.json` has increased entry count and no stale entries.
- Start `python tracker.py --no-mono`, open dashboard/overlay, and confirm cards with manifest entries render through `/cards/<filename>`.
- Spot-check missing common cards and use probe scripts to determine whether they are atlas sprites, generated textures, or naming mismatches.
