# Bazaar Tracker - Roadmap

Active work tracker. Project context, architecture, and stable design notes live in `CLAUDE.md`.

Status labels:
- `Done`: implemented and expected to stay closed.
- `Implemented - verify live`: code exists, but needs confirmation on one or more real runs.
- `Partial`: useful foundation exists, but the feature is not complete enough to close.
- `Open`: not yet implemented.
- `Deprioritized`: low priority feature, leaving as a potential enhancement.

## Open Feature Work

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
- Manifest entries now include image-quality diagnostics: alpha visibility, opaque-alpha coverage, RGB signal checks, `quality_flags`, and `quality_suspect`.
- `web/card_images.py` loads `static_cache/images/manifest.json`.
- `web/server.py` serves card images through `/cards/<filename>`.
- Review/overlay code can attach image URLs when manifest entries exist.
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
    * Probe update 2026-05-01: `probe_card_art_relationships.py` traced `FairyCircle` and `HuntersBoots` in `card_karnok_assets_all_0758ea034b8d9ea66b87b03f37727e56.bundle`. The bundle has 379 `Texture2D`, 118 `MonoBehaviour`, 118 `Material`, and only 1 `Sprite` object; that Sprite is `KAR_Purchase_BG_TUI`, not a card-art sprite or atlas crop. Each target card has a `*_CardData` MonoBehaviour whose only card-art reference is `cardMaterial`.
    * `FairyCircle_CardData.asset` -> `cardMaterial` `CF_L_KAR_FairyCircle`; material `_MainTex` -> `CF_L_KAR_FairyCircle_D` (1024x1024), `_EnchantmentMask` -> `CF_L_KAR_FairyCircle_EnchantmentMask` (256x256), `_FXMask` -> `CF_L_KAR_FairyCircle_PremiumMask` (256x256).
    * `HuntersBoots_CardData.asset` -> `cardMaterial` `CF_M_KAR_HuntersBoots`; material `_MainTex` -> `CF_M_KAR_HuntersBoots_D` (1024x1024), `_EnchantmentMask` -> `CF_M_KAR_HuntersBoots_EnchantmentMask` (256x256), `_FXMask` -> `CF_M_KAR_HuntersBlind_PremiumMask` (128x128).
    * Diagnostic output path: `diagnostics/card_art_relationships.json`.
    * Next exact dependency to follow: the card shader/default material render path, especially external/shared material texture slots (`file_id` 2/3/4 in material `m_SavedProperties.m_TexEnvs`) and any runtime card-frame/background pass. The local CardData/Material relationship does not identify a second full-card art layer, so do not replace `_D` exports until the shader/frame dependency is proven.
    * Probe update 2026-05-01, second pass: `probe_card_art_relationships.py --scan-external-targets` now resolves all external material PPtr path IDs across the Steam `StandaloneWindows64` bundles and writes a summary into `diagnostics/card_art_relationships.json`. The material shader resolves to `Assets/TheBazaar/Art/FX/Shaders/PremiumShader/PremiumCard.shader` in `shaders_cards_assets_all_8d4b199c968fff401f93a3eea7da7ba6.bundle`; shared material texture slots resolve mostly to `textures_fx_assets_all_e05204c5f42543ee977d12a6028777a7.bundle`, plus one Dooley flowmap. The external dependencies are shader/FX inputs, not a second composed/default card render.
    * `cardframes_assets_all_f7313516ec62604108e4f864520b3b39.bundle` contains the frame/prefab layer (`Card_Frame_*`, `CardFrame2D_*_PV.prefab`, etc.), and `cardui_assets_all_b920cd3c9288568c3b99979f353e18b3.bundle` contains preview frame UI assets. These are not directly referenced by the per-card CardData material, so a safe static exporter still needs the runtime card prefab/layout path before composing frames/backgrounds locally.
    * Probe update 2026-05-01, runtime prefab pass: `probe_card_art_relationships.py --scan-external-targets --scan-runtime-prefabs` now inspects `cardframes`/`cardui` prefab relationships. `CardPreviewItem_*_PV.prefab` is backed by `TheBazaar.UI.CardPreviewItem` and has `_cardImage`, `_frameContainer`, `_cardTierFrameSO`, and `_cardMaterialShader` fields. Size variants choose `ItemCardFrames_Small/Medium/Large_Sprite.asset`, whose `CardTierFrameSO` fields map bronze/silver/gold/diamond/legendary to `CardFrame2D_*_PV` prefab GUIDs. Board card prefabs use `TheBazaar.Game.CardFrames.ItemVisualsController` with `cardSize`, `cardFramesSO`, `defaultArtAssetSO`, `cardIllustrationRenderer`, and `cardFrameAnchor`.
    * Runtime prefab conclusion: frame/background selection is now traceable by size/tier, but the card illustration is still assigned to a RawImage/MeshRenderer and rendered through PremiumCard shader/runtime behavior. There is no proven static CPU-side recipe for turning bad raw `_D` layers into final default display art, so do not patch `extract_bazaar_bundle_pngs.py` yet.
    * Probe update 2026-05-01, managed runtime pass: `TheBazaarRuntime.dll` confirms `TheBazaar.UI.CardPreviewItem.LoadArt` loads `CardAssetDataSO` by `ArtKey`; `UpdateCardImageMaterial` creates `new Material(assetData.cardMaterial)`, swaps in `_cardMaterialShader`, toggles premium/enchantment shader state, and assigns `_cardImage.material`. `TheBazaar.Game.CardFrames.ItemVisualsController.Setup` passes `cardAssetData.cardMaterial` into `SetCardFrameMaterial`, which `Instantiate<Material>` clones into `materialInstance` and assigns to `cardIllustrationRenderer.sharedMaterial`. `CardTierFrameSO.GetAssetReferenceByRarity` only selects the tier frame prefab. This proves `cardMaterial` is cloned/mutated/rendered at runtime, not statically composed from separate bundle layers.
    * `defaultArtAssetSO` GUID `e6ecae523ab2b4b46b9d2f76a8216dc3` resolves to fallback programmer/developer art in `board_prefabs_assets_all_7459041b15f853f2759829cc9acf6736.bundle`: `Assets/Prefabs/BoardPrefabs/ProgrammerArt/MissingArt/DeveloperArt_CardData.asset`, `DeveloperArtMaterial.mat`, `DeveloperArtTexture.png` (512x512), and `MissingArt.prefab`. Managed code uses it only when an `ArtKey` lookup returns null; it is not per-card default art.
    * Current recommendation: do not patch `extract_bazaar_bundle_pngs.py` for composed/default art. The exact static composition recipe is still not proven. Product-side fallback should hide or avoid serving `quality_suspect` images by default while preserving manifest diagnostics and coverage reporting.
- `Open`: GUID ArtKey diagnostics show the local catalog knows the missing items, but the current probe does not resolve the dependency chain from GUID/CardData to final bundle/texture.
    * `probe_catalog_guids.py` found 237/237 unique missing GUID ArtKeys in `catalog.bin`.
    * The 200-byte neighborhoods expose adjacent CardData/material/card-folder strings, but no `.bundle` names, so the next probe needs to parse Addressables catalog structure rather than relying only on nearby readable strings.

Implementation notes:
- Next session focus: inspect representative bad cards through Sprite -> Texture2D, Material, mask, atlas, and CardData relationships, then export the composed/default display image rather than the raw `_D` layer.
- Use `probe_install_card_bundle.py` against representative bundles to inspect `Texture2D`, `Sprite`, atlas, and container path relationships.
- Update extractor to follow Sprite-to-Texture references and export sprite crops if full card art is packed into atlases.
- Keep the manifest keyed by normalized card name so `web/card_images.py` does not need to know Unity internals.
- Keep coverage reporting split into total manifest hits, usable hits, visually suspect hits, and missing names.

How to test:
- Run image extraction against both user-data Addressables cache and Steam install bundles.
- Run `python tracker.py refresh-images --coverage-only` and confirm suspect counts move down when composed/default images replace raw layers.
- Confirm `static_cache/images/manifest.json` has increased entry count and no stale entries.
- Start `python tracker.py --no-mono`, open dashboard/overlay, and confirm cards with manifest entries render through `/cards/<filename>`.
- Spot-check missing common cards and use probe scripts to determine whether they are atlas sprites, generated textures, or naming mismatches.
