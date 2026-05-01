# Bazaar Tracker - Roadmap

Active work tracker. Project context, architecture, and stable design notes live in `CLAUDE.md`.

Status labels:
- `Done`: implemented and expected to stay closed.
- `Implemented - verify live`: code exists, but needs confirmation on one or more real runs.
- `Partial`: useful foundation exists, but the feature is not complete enough to close.
- `Open`: not yet implemented.
- `Deprioritized`: low priority feature, leaving as a potential enhancement.

## Open Feature Work

### Remove Post-Run Scoring - Open

Goal: make the live scoring path the only scoring path. The bridge should enrich captured decisions with Mono context, but it should no longer trigger a separate post-run rescore.

Relevant files:
- `bridge.py`
- `scorer.py`
- `watcher.py`
- `run_state.py`
- `web/review_builder.py`
- `web/overlay_state.py`
- tests covering scoring, bridge enrichment, and run completion behavior

Why this is next:
- Over time it has become clear that scoring should happen when the decision is recorded via `LiveScorer`.
- Post-run scoring creates two paths that can disagree, makes debugging harder, and can rewrite live coaching decisions after the fact.
- The bridge still matters for enrichment, but its role should be correlation and metadata fill-in, not scoring authority.

Implementation notes:
- Remove or disable bridge-triggered post-run scoring/rescoring.
- Keep `bridge.py` responsible for correlating Player.log decisions with Mono snapshots and filling day/hour/gold/health/combat context where available.
- Treat stored `score_label` and `score_notes` from the live path as authoritative.
- Audit `scorer.py` for duplicate live-vs-post-run code paths and collapse shared helpers only where it keeps the live path simple.
- Update dashboard/review assumptions so enriched metadata can appear after bridge runs without changing the original score.
- Keep a manual/dev-only rescore command only if it is clearly useful for catalog development, and document that it is not part of normal run completion.

How to test:
- Complete a run and confirm watcher still triggers bridge enrichment.
- Confirm decisions keep the `score_label`/`score_notes` written by `LiveScorer`.
- Confirm enriched day/hour/gold/health fields can update without changing scores.
- Confirm dashboard and overlay still show completed run review correctly after bridge finishes.
- Run the scoring/bridge-related pytest suite and a py_compile pass over touched modules.

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
