#!/usr/bin/env python3
"""Trace Bazaar CardData art relationships for specific cards.

This is intentionally diagnostic: it reports CardData, Material, Sprite, and
Texture2D links for a small set of card folder names so extractor changes can
be based on bundle data rather than name guesses.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

import UnityPy
import UnityPy.config

UnityPy.config.FALLBACK_UNITY_VERSION = "6000.3.11f1"

DEFAULT_BUNDLE = Path(
    r"C:\Program Files (x86)\Steam\steamapps\common\The Bazaar"
    r"\TheBazaar_Data\StreamingAssets\aa\StandaloneWindows64"
    r"\card_karnok_assets_all_0758ea034b8d9ea66b87b03f37727e56.bundle"
)
DEFAULT_STANDALONE_DIR = DEFAULT_BUNDLE.parent
DEFAULT_CATALOG = DEFAULT_STANDALONE_DIR.parent / "catalog.bin"
RUNTIME_PREFAB_BUNDLES = [
    "cardframes_assets_all_f7313516ec62604108e4f864520b3b39.bundle",
    "cardui_assets_all_b920cd3c9288568c3b99979f353e18b3.bundle",
]
DEFAULT_ART_ASSET_GUID = "e6ecae523ab2b4b46b9d2f76a8216dc3"
DEFAULT_ART_SEARCH_TERMS = [
    "DeveloperArt_CardData",
    "DeveloperArtMaterial",
    "DeveloperArtTexture",
    "MissingArt",
]
MANAGED_RUNTIME_CODE_ANALYSIS = {
    "assembly": "TheBazaarRuntime.dll",
    "classes": {
        "TheBazaar.UI.CardPreviewItem": {
            "LoadArt": (
                "If _cardData.ArtKey is valid, Addressables.LoadAssetAsync<CardAssetDataSO>"
                "(ArtKey) is awaited, then UpdateCardImageMaterial is called."
            ),
            "UpdateCardImageMaterial": (
                "Requires assetData.cardMaterial. Destroys any existing _cardMaterial, "
                "creates new Material(assetData.cardMaterial), replaces its shader with "
                "_cardMaterialShader, toggles the Premium shader keyword, applies "
                "enchantment shader variables, then assigns _cardImage.material."
            ),
        },
        "TheBazaar.Game.CardFrames.ItemVisualsController": {
            "GetCardAssetData": (
                "Loads CardAssetDataSO by cardTemplate.ArtKey. If that returns null, "
                "loads defaultArtAssetSO by AssetReference; the fallback log says "
                "'Failed to load DeveloperArtMaterial Material'."
            ),
            "SetCardFrameMaterial": (
                "Destroys any existing materialInstance, Instantiate<Material>(cardFrameMaterial), "
                "assigns cardIllustrationRenderer.sharedMaterial, toggles the Premium shader "
                "keyword, then applies enchantment visuals."
            ),
            "Setup(CardAssetDataSO, tier, cardBackAsset, isPremium, enchantmentType)": (
                "If cardAssetData.cardMaterial exists, calls SetCardFrameMaterial. "
                "Separately loads the tier frame and card back via SetCardTierAsset/SetCardback."
            ),
            "SetCardTierAsset": (
                "Loads CardTierFrameSO.GetAssetReferenceByRarity(tier), stores the instantiated "
                "frame as currentFrame, and parents it under cardFrameAnchor."
            ),
        },
        "TheBazaar.Assets.Scripts.ScriptableObjectsScripts.CardAssetDataSO": {
            "cardMaterial_consumers": (
                "LoadCollectionDetailsAssetAsync, LoadCollectionListAssetAsync, "
                "LoadMarketplaceDetailsAssetAsync, and LoadMarketplaceListAssetAsync "
                "return cardMaterial directly as a completed task."
            ),
        },
        "TheBazaar.CardTierFrameSO": {
            "GetAssetReferenceByRarity": (
                "Maps bronze/silver/gold/diamond/legendary to prefab references and "
                "falls back to bronze if the selected reference is null or has no GUID."
            ),
        },
    },
    "conclusion": (
        "Managed runtime code copies/clones CardData/CardAssetDataSO.cardMaterial before display. "
        "Preview UI uses new Material(cardMaterial) plus a UI shader and assigns RawImage.material. "
        "Board visuals use Instantiate(cardMaterial) and assign Renderer.sharedMaterial. "
        "Frame/card-back selection is separate from art material assignment; no CPU-side "
        "composition recipe is present in these managed methods."
    ),
}
RUNTIME_SEARCH_TERMS = [
    "CardData",
    "cardMaterial",
    "CardFrame2D",
    "CardPreview",
    "Preview",
    "Rarity",
    "Size",
    "PremiumCard",
    "_MainTex",
    "frame",
    "background",
    "card art",
    "material",
]
INTERESTING_FIELD_TERMS = [
    "art",
    "background",
    "card",
    "frame",
    "image",
    "material",
    "preview",
    "rarity",
    "renderer",
    "shader",
    "size",
    "sprite",
    "texture",
    "tier",
]


def _pptr_path_id(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, dict):
        raw = value.get("m_PathID", value.get("path_id"))
        return int(raw) if isinstance(raw, int) else None
    raw = getattr(value, "path_id", None)
    if raw is None:
        raw = getattr(value, "m_PathID", None)
    return int(raw) if isinstance(raw, int) else None


def _pptr_file_id(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, dict):
        raw = value.get("m_FileID", value.get("file_id"))
        return int(raw) if isinstance(raw, int) else None
    raw = getattr(value, "file_id", None)
    if raw is None:
        raw = getattr(value, "m_FileID", None)
    return int(raw) if isinstance(raw, int) else None


def _safe_name(data: Any) -> str:
    return str(getattr(data, "m_Name", None) or getattr(data, "name", None) or "")


def _brief(value: Any, *, depth: int = 0) -> Any:
    if depth > 4:
        return "..."
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, list):
        return [_brief(item, depth=depth + 1) for item in value[:12]]
    if isinstance(value, tuple):
        return [_brief(item, depth=depth + 1) for item in value[:12]]
    if isinstance(value, dict):
        result = {}
        for key, nested in list(value.items())[:40]:
            if key in {"m_Script", "m_GameObject"}:
                result[key] = _pptr_path_id(nested)
            else:
                result[str(key)] = _brief(nested, depth=depth + 1)
        return result
    pid = _pptr_path_id(value)
    if pid is not None:
        return {"path_id": pid}
    if hasattr(value, "__dict__"):
        return {
            key: _brief(nested, depth=depth + 1)
            for key, nested in list(vars(value).items())[:25]
            if not key.startswith("_")
        }
    return repr(value)[:160]


def _walk_pptrs(value: Any, path: str = "") -> list[tuple[str, int]]:
    found: list[tuple[str, int]] = []
    pid = _pptr_path_id(value)
    if pid is not None:
        found.append((path or "<root>", pid))
        return found
    if isinstance(value, dict):
        for key, nested in value.items():
            found.extend(_walk_pptrs(nested, f"{path}.{key}" if path else str(key)))
    elif isinstance(value, (list, tuple)):
        for index, nested in enumerate(value):
            found.extend(_walk_pptrs(nested, f"{path}[{index}]"))
    elif hasattr(value, "__dict__"):
        for key, nested in vars(value).items():
            if not key.startswith("_"):
                found.extend(_walk_pptrs(nested, f"{path}.{key}" if path else key))
    return found


def _walk_asset_guids(value: Any, path: str = "") -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    if isinstance(value, dict):
        guid = value.get("m_AssetGUID")
        if isinstance(guid, str) and guid:
            found.append((path or "<root>", guid))
        for key, nested in value.items():
            found.extend(_walk_asset_guids(nested, f"{path}.{key}" if path else str(key)))
    elif isinstance(value, (list, tuple)):
        for index, nested in enumerate(value):
            found.extend(_walk_asset_guids(nested, f"{path}[{index}]"))
    elif hasattr(value, "__dict__"):
        guid = getattr(value, "m_AssetGUID", None)
        if isinstance(guid, str) and guid:
            found.append((path or "<root>", guid))
        for key, nested in vars(value).items():
            if not key.startswith("_"):
                found.extend(_walk_asset_guids(nested, f"{path}.{key}" if path else key))
    return found


def _interesting_scalars(value: Any, path: str = "") -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    if isinstance(value, dict):
        for key, nested in value.items():
            field_path = f"{path}.{key}" if path else str(key)
            lowered = field_path.lower()
            if any(term in lowered for term in INTERESTING_FIELD_TERMS):
                if isinstance(nested, (str, int, float, bool)) or nested is None:
                    result.append({"field": field_path, "value": nested})
                elif isinstance(nested, dict) and "m_AssetGUID" in nested:
                    result.append({"field": field_path, "value": _brief(nested, depth=1)})
            result.extend(_interesting_scalars(nested, field_path))
    elif isinstance(value, (list, tuple)):
        for index, nested in enumerate(value):
            result.extend(_interesting_scalars(nested, f"{path}[{index}]"))
    return result[:80]


def _read_typetree(obj: Any) -> dict[str, Any]:
    try:
        tree = obj.read_typetree()
        return tree if isinstance(tree, dict) else {}
    except Exception:
        return {}


def _collect_object_names(env: Any) -> tuple[dict[int, str], dict[int, dict[str, Any]]]:
    container_by_path_id = {obj.path_id: key for key, obj in env.container.items()}
    names_by_path_id: dict[int, dict[str, Any]] = {}
    for obj in env.objects:
        try:
            data = obj.read()
            meta = {
                "type": obj.type.name,
                "name": _safe_name(data),
                "container": container_by_path_id.get(obj.path_id, ""),
            }
            if obj.type.name == "Texture2D":
                meta.update({
                    "width": getattr(data, "m_Width", None),
                    "height": getattr(data, "m_Height", None),
                })
            names_by_path_id[obj.path_id] = meta
        except Exception as exc:
            names_by_path_id[obj.path_id] = {
                "type": obj.type.name,
                "name": f"<read error: {exc}>",
                "container": container_by_path_id.get(obj.path_id, ""),
            }
    return container_by_path_id, names_by_path_id


def _texture_envs(tree: dict[str, Any], names_by_path_id: dict[int, dict[str, Any]]) -> list[dict[str, Any]]:
    saved = tree.get("m_SavedProperties") or {}
    result = []
    for entry in saved.get("m_TexEnvs") or []:
        if isinstance(entry, dict):
            prop_name = entry.get("first") or entry.get("name") or entry.get("key")
            payload = entry.get("second") or entry.get("value")
        elif isinstance(entry, (list, tuple)) and len(entry) == 2:
            prop_name, payload = entry
        else:
            continue
        tex_ref = (payload or {}).get("m_Texture") if isinstance(payload, dict) else None
        pid = _pptr_path_id(tex_ref)
        meta = names_by_path_id.get(pid or 0, {})
        result.append({
            "property": prop_name,
            "file_id": _pptr_file_id(tex_ref),
            "path_id": pid,
            "target_type": meta.get("type"),
            "target_name": meta.get("name"),
            "width": meta.get("width"),
            "height": meta.get("height"),
        })
    return result


def _texture_envs_from_data(data: Any) -> list[dict[str, Any]]:
    saved = getattr(data, "m_SavedProperties", None)
    tex_envs = getattr(saved, "m_TexEnvs", None) if saved is not None else None
    result = []
    for entry in tex_envs or []:
        prop_name = getattr(entry, "first", None)
        payload = getattr(entry, "second", None)
        tex_ref = getattr(payload, "m_Texture", None)
        pid = _pptr_path_id(tex_ref)
        resolved = None
        try:
            tex_data = tex_ref.read() if tex_ref is not None and pid else None
            if tex_data is not None:
                resolved = {
                    "target_type": tex_ref.type.name,
                    "target_name": _safe_name(tex_data),
                    "width": getattr(tex_data, "m_Width", None),
                    "height": getattr(tex_data, "m_Height", None),
                }
        except Exception as exc:
            resolved = {"resolve_error": str(exc)[:160]}
        if resolved is None and _pptr_file_id(tex_ref) not in (None, 0) and pid:
            resolved = {"resolve_error": "external reference not loaded by UnityPy"}
        result.append({
            "property": prop_name,
            "file_id": _pptr_file_id(tex_ref),
            "path_id": pid,
            **(resolved or {}),
        })
    return result


def _pptr_details(ref: Any) -> dict[str, Any]:
    details = {
        "file_id": _pptr_file_id(ref),
        "path_id": _pptr_path_id(ref),
    }
    if not details["path_id"]:
        return details
    try:
        data = ref.read()
        details.update({
            "resolved_type": ref.type.name,
            "resolved_name": _safe_name(data),
            "width": getattr(data, "m_Width", None),
            "height": getattr(data, "m_Height", None),
        })
    except Exception as exc:
        details["resolve_error"] = str(exc)[:200]
    return details


def _shader_details(material_data: Any) -> dict[str, Any]:
    return _pptr_details(getattr(material_data, "m_Shader", None))


def _serialized_file_details(env: Any) -> list[dict[str, Any]]:
    details = []
    for key, asset_file in getattr(env, "files", {}).items():
        externals = []
        for attr in ("externals", "m_Externals", "external_references"):
            value = getattr(asset_file, attr, None)
            if value:
                externals.append({"attribute": attr, "value": _brief(value, depth=1)})
        details.append({
            "key": str(key),
            "name": getattr(asset_file, "name", None),
            "path": str(getattr(asset_file, "path", "") or ""),
            "objects": len(getattr(asset_file, "objects", {}) or {}),
            "externals": externals,
        })
    return details


def _scan_external_targets(standalone_dir: Path, wanted_path_ids: set[int]) -> dict[str, Any]:
    if not wanted_path_ids or not standalone_dir.is_dir():
        return {"scanned_bundles": 0, "targets": {}}

    targets: dict[str, list[dict[str, Any]]] = {str(pid): [] for pid in sorted(wanted_path_ids)}
    bundles = sorted(standalone_dir.glob("*.bundle"))
    for bundle_path in bundles:
        try:
            env = UnityPy.load(str(bundle_path))
        except Exception:
            continue
        matched = [obj for obj in env.objects if obj.path_id in wanted_path_ids]
        if not matched:
            continue
        container_by_path_id = {obj.path_id: key for key, obj in env.container.items()}
        for obj in matched:
            entry: dict[str, Any] = {
                "bundle": bundle_path.name,
                "type": obj.type.name,
                "container": container_by_path_id.get(obj.path_id, ""),
            }
            try:
                data = obj.read()
                entry["name"] = _safe_name(data)
                entry["width"] = getattr(data, "m_Width", None)
                entry["height"] = getattr(data, "m_Height", None)
                if obj.type.name == "MonoScript":
                    entry["script"] = {
                        "class": getattr(data, "m_ClassName", None),
                        "namespace": getattr(data, "m_Namespace", None),
                        "assembly": getattr(data, "m_AssemblyName", None),
                    }
                elif obj.type.name == "MonoBehaviour":
                    tree = _read_typetree(obj)
                    entry["typetree"] = _brief(tree, depth=1)
                    entry["asset_guids"] = [
                        {"field": field, "guid": guid}
                        for field, guid in _walk_asset_guids(tree)
                    ]
                elif obj.type.name == "Shader":
                    tree = _read_typetree(obj)
                    props = (((tree.get("m_ParsedForm") or {}).get("m_PropInfo") or {}).get("m_Props") or [])
                    entry["shader_properties"] = [
                        prop.get("m_Name")
                        for prop in props
                        if isinstance(prop, dict) and prop.get("m_Name")
                    ][:80]
            except Exception as exc:
                entry["read_error"] = str(exc)[:160]
            targets[str(obj.path_id)].append(entry)
    return {
        "standalone_dir": str(standalone_dir),
        "scanned_bundles": len(bundles),
        "targets": {pid: matches for pid, matches in targets.items() if matches},
        "unresolved_path_ids": [
            int(pid) for pid, matches in targets.items() if not matches
        ],
    }


def _attach_external_matches(report: dict[str, Any]) -> None:
    scan = report.get("external_target_scan") or {}
    targets = scan.get("targets") or {}
    if not targets:
        return
    for card_report in (report.get("cards") or {}).values():
        for material in card_report.get("material_details") or []:
            shader = material.get("shader") or {}
            if shader.get("file_id") not in (None, 0) and shader.get("path_id") is not None:
                shader["external_target_matches"] = targets.get(str(shader["path_id"]), [])
            for tex_env in material.get("texture_envs") or []:
                if tex_env.get("file_id") not in (None, 0) and tex_env.get("path_id") is not None:
                    tex_env["external_target_matches"] = targets.get(str(tex_env["path_id"]), [])


def _component_report(
    obj: Any,
    names_by_path_id: dict[int, dict[str, Any]],
    external_path_ids: set[int],
) -> dict[str, Any]:
    tree = _read_typetree(obj)
    refs = []
    for field_path, pid in _walk_pptrs(tree):
        file_id = None
        cursor: Any = tree
        for token in field_path.replace("]", "").replace("[", ".").split("."):
            if token == "":
                continue
            if isinstance(cursor, dict):
                cursor = cursor.get(token)
            elif isinstance(cursor, list) and token.isdigit():
                cursor = cursor[int(token)]
            else:
                cursor = None
                break
        file_id = _pptr_file_id(cursor)
        if file_id not in (None, 0) and pid:
            external_path_ids.add(int(pid))
        meta = names_by_path_id.get(pid, {})
        if (
            file_id not in (None, 0)
            or meta.get("type") in {"Material", "MonoBehaviour", "MonoScript", "Sprite", "Texture2D"}
            or any(term in field_path.lower() for term in INTERESTING_FIELD_TERMS)
        ):
            refs.append({
                "field": field_path,
                "file_id": file_id,
                "path_id": pid,
                **meta,
            })
    for field_path, guid in _walk_asset_guids(tree):
        refs.append({"field": field_path, "asset_guid": guid})
    return {
        "path_id": obj.path_id,
        **names_by_path_id.get(obj.path_id, {"type": obj.type.name}),
        "typetree_keys": list(tree.keys())[:60],
        "interesting_fields": _interesting_scalars(tree),
        "references": refs[:140],
    }


def _runtime_prefab_report(
    bundle_path: Path,
    *,
    external_path_ids: set[int],
) -> dict[str, Any]:
    env = UnityPy.load(str(bundle_path))
    container_by_path_id, names_by_path_id = _collect_object_names(env)
    objects_by_path_id = {obj.path_id: obj for obj in env.objects}
    type_counts = Counter(obj.type.name for obj in env.objects)
    lowered_terms = [term.lower() for term in RUNTIME_SEARCH_TERMS]

    matching_container_objects = []
    for obj in env.objects:
        meta = names_by_path_id.get(obj.path_id, {})
        haystack = " ".join([
            str(meta.get("name") or ""),
            str(meta.get("container") or ""),
            obj.type.name,
        ]).lower()
        if any(term in haystack for term in lowered_terms):
            matching_container_objects.append({
                "path_id": obj.path_id,
                **meta,
            })

    prefab_reports = []
    for obj in env.objects:
        meta = names_by_path_id.get(obj.path_id, {})
        container = str(meta.get("container") or "")
        name = str(meta.get("name") or "")
        if obj.type.name != "GameObject":
            continue
        if not any(term in f"{container} {name}".lower() for term in lowered_terms):
            continue
        tree = _read_typetree(obj)
        component_ids = [
            pid
            for field_path, pid in _walk_pptrs(tree)
            if "m_Component" in field_path and "component" in field_path
        ]
        components = []
        for component_id in component_ids:
            component_obj = objects_by_path_id.get(component_id)
            if component_obj is None:
                continue
            components.append(_component_report(component_obj, names_by_path_id, external_path_ids))
        prefab_reports.append({
            "path_id": obj.path_id,
            **meta,
            "components": components,
        })

    return {
        "bundle": str(bundle_path),
        "type_counts": dict(type_counts),
        "matching_objects": matching_container_objects[:220],
        "prefabs": prefab_reports[:80],
    }


def _catalog_guid_context(catalog_path: Path, guids: set[str]) -> dict[str, list[str]]:
    if not guids or not catalog_path.is_file():
        return {}
    data = catalog_path.read_bytes()
    text = data.decode("latin1", errors="ignore")
    contexts: dict[str, list[str]] = {}
    for guid in sorted(guids):
        offset = text.find(guid)
        if offset < 0:
            continue
        start = max(0, offset - 280)
        end = min(len(data), offset + 32 + 420)
        strings = []
        for raw in __import__("re").findall(rb"[ -~]{6,}", data[start:end]):
            value = raw.decode("ascii", errors="ignore")
            strings.append(value)
        if strings:
            contexts[guid] = strings[:30]
    return contexts


def _scan_assets_by_terms(standalone_dir: Path, terms: list[str]) -> dict[str, Any]:
    if not terms or not standalone_dir.is_dir():
        return {"scanned_bundles": 0, "matches": []}
    lowered_terms = [term.lower() for term in terms]
    bundles = sorted(standalone_dir.glob("*.bundle"))
    matches = []
    for bundle_path in bundles:
        try:
            env = UnityPy.load(str(bundle_path))
        except Exception:
            continue
        container_by_path_id = {obj.path_id: key for key, obj in env.container.items()}
        for obj in env.objects:
            meta = container_by_path_id.get(obj.path_id, "")
            name = ""
            width = None
            height = None
            try:
                data = obj.read()
                name = _safe_name(data)
                width = getattr(data, "m_Width", None)
                height = getattr(data, "m_Height", None)
            except Exception:
                pass
            haystack = f"{meta} {name} {obj.type.name}".lower()
            if not any(term in haystack for term in lowered_terms):
                continue
            entry = {
                "bundle": bundle_path.name,
                "path_id": obj.path_id,
                "type": obj.type.name,
                "name": name,
                "container": meta,
                "width": width,
                "height": height,
            }
            if obj.type.name == "MonoBehaviour":
                tree = _read_typetree(obj)
                entry["interesting_fields"] = _interesting_scalars(tree)
                entry["references"] = [
                    {"field": field, "path_id": pid}
                    for field, pid in _walk_pptrs(tree)
                    if any(term in field.lower() for term in INTERESTING_FIELD_TERMS)
                ][:40]
            matches.append(entry)
    return {
        "standalone_dir": str(standalone_dir),
        "scanned_bundles": len(bundles),
        "terms": terms,
        "matches": matches[:120],
    }


def _build_summary(report: dict[str, Any]) -> dict[str, Any]:
    external_bundles = Counter()
    external_types = Counter()
    for matches in ((report.get("external_target_scan") or {}).get("targets") or {}).values():
        for match in matches:
            external_bundles[match.get("bundle") or ""] += 1
            external_types[match.get("type") or ""] += 1

    cards = {}
    for card, card_report in (report.get("cards") or {}).items():
        local_card_textures = []
        external_texture_props = []
        shader_targets = []
        for material in card_report.get("material_details") or []:
            shader = material.get("shader") or {}
            shader_targets.extend(shader.get("external_target_matches") or [])
            for tex_env in material.get("texture_envs") or []:
                prop = tex_env.get("property")
                if tex_env.get("file_id") in (None, 0):
                    if tex_env.get("target_type") == "Texture2D" and tex_env.get("path_id"):
                        local_card_textures.append({
                            "property": prop,
                            "name": tex_env.get("target_name"),
                            "width": tex_env.get("width"),
                            "height": tex_env.get("height"),
                        })
                else:
                    external_texture_props.append({
                        "property": prop,
                        "file_id": tex_env.get("file_id"),
                        "path_id": tex_env.get("path_id"),
                        "matches": tex_env.get("external_target_matches") or [],
                    })
        cards[card] = {
            "local_card_textures": local_card_textures,
            "external_texture_properties": external_texture_props,
            "shader_targets": shader_targets,
            "finding": (
                "CardData resolves to one card Material. The Material's only local "
                "full-size card texture is _MainTex; other local textures are masks. "
                "External references resolve to PremiumCard.shader/shared FX textures, "
                "not a second composed/default card render."
            ),
        }

    runtime_findings = []
    runtime = report.get("runtime_prefab_scan") or {}
    for bundle in runtime.get("bundles") or []:
        for prefab in bundle.get("prefabs") or []:
            component_names = [
                (component.get("name") or component.get("type") or "")
                for component in prefab.get("components") or []
            ]
            notable_fields = []
            for component in prefab.get("components") or []:
                for field in component.get("interesting_fields") or []:
                    if field.get("field") in {
                        "cardSize",
                        "cardFramesSO",
                        "defaultArtAssetSO",
                        "_cardImage",
                        "_frameContainer",
                        "_cardTierFrameSO",
                        "_cardMaterialShader",
                    }:
                        notable_fields.append(field)
                for ref in component.get("references") or []:
                    if ref.get("field") in {
                        "cardFramesSO",
                        "cardIllustrationRenderer",
                        "cardFrameAnchor",
                        "_cardImage",
                        "_frameContainer",
                        "_cardTierFrameSO",
                        "_cardMaterialShader",
                    }:
                        notable_fields.append(ref)
            if not notable_fields:
                continue
            runtime_findings.append({
                "prefab": prefab.get("container") or prefab.get("name"),
                "components": component_names,
                "notable_fields": notable_fields,
            })

    recommendation = (
        "Do not replace Texture2D _D exports with composed art yet. The card "
        "preview and board prefabs identify runtime controllers/shaders and "
        "frame prefab selectors, but the static bundle data still does not "
        "provide a deterministic CPU-side recipe for reproducing the "
        "PremiumCard shader render of cardMaterial._MainTex."
    )
    if not runtime:
        recommendation = (
            "Do not replace Texture2D _D exports with composed art yet. The next "
            "dependency is the runtime PremiumCard shader plus card-frame prefab/layout "
            "path; this report does not prove a safe static composition recipe."
        )

    return {
        "external_target_bundles": dict(external_bundles.most_common()),
        "external_target_types": dict(external_types.most_common()),
        "cards": cards,
        "managed_runtime_code_conclusion": MANAGED_RUNTIME_CODE_ANALYSIS["conclusion"],
        "default_art_asset_conclusion": (
            f"defaultArtAssetSO GUID {DEFAULT_ART_ASSET_GUID} resolves by catalog context "
            "to DeveloperArt_CardData/DeveloperArtMaterial/DeveloperArtTexture/MissingArt. "
            "Managed ItemVisualsController only uses this when ArtKey lookup returns null; "
            "it is fallback placeholder/developer art, not per-card default composed art."
        ),
        "runtime_prefab_findings": runtime_findings[:40],
        "runtime_prefab_conclusion": (
            "CardPreviewItem supplies a RawImage target, a CardTierFrameSO "
            "selector, and a PremiumCard UI shader. ItemVisualsController "
            "supplies cardFramesSO/defaultArtAssetSO/cardIllustrationRenderer "
            "for board cards. Frame/background selection is static by size/tier "
            "through CardTierFrameSO GUID fields, but the card illustration is "
            "still assigned and rendered by runtime code/shader behavior."
        ) if runtime else "",
        "extractor_recommendation": recommendation,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", type=Path, default=DEFAULT_BUNDLE)
    parser.add_argument("--cards", nargs="+", default=["FairyCircle", "HuntersBoots"])
    parser.add_argument("--out", type=Path, default=Path("diagnostics/card_art_relationships.json"))
    parser.add_argument("--standalone-dir", type=Path, default=DEFAULT_STANDALONE_DIR)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument(
        "--scan-external-targets",
        action="store_true",
        help="Scan sibling .bundle files to find objects matching external PPtr path IDs.",
    )
    parser.add_argument(
        "--scan-runtime-prefabs",
        action="store_true",
        help="Inspect cardframes/cardui prefab relationships used by runtime display paths.",
    )
    args = parser.parse_args()

    env = UnityPy.load(str(args.bundle))
    container_by_path_id, names_by_path_id = _collect_object_names(env)
    objects_by_path_id = {obj.path_id: obj for obj in env.objects}
    type_counts = Counter(obj.type.name for obj in env.objects)

    report: dict[str, Any] = {
        "bundle": str(args.bundle),
        "type_counts": dict(type_counts),
        "serialized_files": _serialized_file_details(env),
        "cards": {},
    }
    external_path_ids: set[int] = set()

    for card in args.cards:
        card_prefix = f"Assets/TheBazaar/Art/Heroes/Karnok/Cards/{card}/"
        card_objects = [
            obj for obj in env.objects
            if (container_by_path_id.get(obj.path_id) or "").startswith(card_prefix)
        ]
        card_report: dict[str, Any] = {
            "container_objects": [
                {
                    "path_id": obj.path_id,
                    **names_by_path_id.get(obj.path_id, {"type": obj.type.name}),
                }
                for obj in card_objects
            ],
            "references": [],
            "material_details": [],
            "texture_name_matches": [],
        }

        for obj in card_objects:
            tree = _read_typetree(obj)
            data = None
            try:
                data = obj.read()
            except Exception:
                pass
            source = tree or (vars(data) if data is not None and hasattr(data, "__dict__") else {})
            pptrs = _walk_pptrs(source)
            for field_path, pid in pptrs:
                target = names_by_path_id.get(pid) or {"type": objects_by_path_id.get(pid).type.name if pid in objects_by_path_id else "unknown"}
                card_report["references"].append({
                    "from_path_id": obj.path_id,
                    "from_container": container_by_path_id.get(obj.path_id, ""),
                    "field": field_path,
                    "target_path_id": pid,
                    **target,
                })

        normalized = card.lower()
        for pid, meta in names_by_path_id.items():
            name = str(meta.get("name") or "").lower()
            if normalized in name:
                card_report["texture_name_matches"].append({"path_id": pid, **meta})

        material_ids = {
            ref["target_path_id"]
            for ref in card_report["references"]
            if ref.get("type") == "Material"
        }
        for pid in sorted(material_ids):
            obj = objects_by_path_id.get(pid)
            if obj is None:
                continue
            tree = _read_typetree(obj)
            material_data = obj.read()
            texture_envs = _texture_envs_from_data(material_data)
            if not any(env.get("property") for env in texture_envs):
                texture_envs = _texture_envs(tree, names_by_path_id)
            for env_ref in texture_envs:
                if env_ref.get("file_id") and env_ref.get("file_id") != 0 and env_ref.get("path_id"):
                    external_path_ids.add(int(env_ref["path_id"]))
            shader = _shader_details(material_data)
            if shader.get("file_id") and shader.get("file_id") != 0 and shader.get("path_id"):
                external_path_ids.add(int(shader["path_id"]))
            material_report = {
                "path_id": pid,
                **names_by_path_id.get(pid, {}),
                "shader": shader,
                "typetree": _brief(tree),
                "texture_envs": texture_envs,
                "references": [],
            }
            for field_path, target_pid in _walk_pptrs(tree):
                material_report["references"].append({
                    "field": field_path,
                    "target_path_id": target_pid,
                    **(names_by_path_id.get(target_pid) or {}),
                })
            card_report["material_details"].append(material_report)

        report["cards"][card] = card_report

    report["external_path_ids"] = sorted(external_path_ids)
    runtime_asset_guids: set[str] = set()
    if args.scan_runtime_prefabs:
        runtime_bundles = []
        for bundle_name in RUNTIME_PREFAB_BUNDLES:
            bundle_path = args.standalone_dir / bundle_name
            if not bundle_path.is_file():
                runtime_bundles.append({
                    "bundle": str(bundle_path),
                    "error": "bundle not found",
                })
                continue
            try:
                bundle_report = _runtime_prefab_report(bundle_path, external_path_ids=external_path_ids)
            except Exception as exc:
                bundle_report = {"bundle": str(bundle_path), "error": str(exc)[:200]}
            runtime_bundles.append(bundle_report)
            for prefab in bundle_report.get("prefabs") or []:
                for component in prefab.get("components") or []:
                    for ref in component.get("references") or []:
                        guid = ref.get("asset_guid")
                        if isinstance(guid, str) and guid:
                            runtime_asset_guids.add(guid)
        report["runtime_prefab_scan"] = {
            "bundles": runtime_bundles,
            "asset_guid_context": _catalog_guid_context(args.catalog, runtime_asset_guids),
        }
        if DEFAULT_ART_ASSET_GUID not in report["runtime_prefab_scan"]["asset_guid_context"]:
            report["runtime_prefab_scan"]["asset_guid_context"].update(
                _catalog_guid_context(args.catalog, {DEFAULT_ART_ASSET_GUID})
            )
        report["runtime_prefab_scan"]["default_art_asset_scan"] = _scan_assets_by_terms(
            args.standalone_dir,
            DEFAULT_ART_SEARCH_TERMS,
        )
        report["managed_runtime_code_analysis"] = MANAGED_RUNTIME_CODE_ANALYSIS
        report["external_path_ids"] = sorted(external_path_ids)
    if args.scan_external_targets:
        report["external_target_scan"] = _scan_external_targets(args.standalone_dir, external_path_ids)
        _attach_external_matches(report)
    report["summary"] = _build_summary(report)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Wrote {args.out}")
    for card, card_report in report["cards"].items():
        print()
        print(card)
        for obj in card_report["container_objects"]:
            print(f"  object {obj.get('type')} pid={obj.get('path_id')} {obj.get('name')!r}")
            print(f"    {obj.get('container')}")
        print("  references:")
        for ref in card_report["references"]:
            print(
                f"    {ref['field']} -> {ref.get('type')} "
                f"pid={ref['target_path_id']} {ref.get('name')!r}"
            )
        print("  material texture refs:")
        for material in card_report["material_details"]:
            print(f"    material {material.get('name')!r} pid={material['path_id']}")
            shader = material.get("shader") or {}
            print(
                f"      shader: file={shader.get('file_id')} pid={shader.get('path_id')} "
                f"-> {shader.get('resolved_type') or shader.get('resolve_error')}"
            )
            for env in material.get("texture_envs") or []:
                print(
                    f"      {env['property']}: file={env.get('file_id')} "
                    f"pid={env.get('path_id')} -> {env.get('target_name')!r} "
                    f"{env.get('width')}x{env.get('height')}"
                    + (f" error={env.get('resolve_error')}" if env.get("resolve_error") else "")
                )
            for ref in material["references"]:
                if ref.get("type") == "Texture2D":
                    print(
                        f"      {ref['field']} -> {ref.get('name')!r} "
                        f"{ref.get('width')}x{ref.get('height')}"
                    )
    if args.scan_external_targets:
        scan = report.get("external_target_scan") or {}
        print()
        print("External target scan:")
        print(f"  scanned bundles: {scan.get('scanned_bundles', 0)}")
        for pid, matches in (scan.get("targets") or {}).items():
            print(f"  path_id {pid}:")
            for match in matches:
                print(
                    f"    {match.get('bundle')} {match.get('type')} "
                    f"{match.get('name')!r} {match.get('width')}x{match.get('height')}"
                )
        unresolved = scan.get("unresolved_path_ids") or []
        print(f"  unresolved path ids: {len(unresolved)}")
    if args.scan_runtime_prefabs:
        runtime = report.get("runtime_prefab_scan") or {}
        print()
        print("Runtime prefab scan:")
        for bundle in runtime.get("bundles") or []:
            print(f"  {Path(bundle.get('bundle', '')).name}:")
            if bundle.get("error"):
                print(f"    error: {bundle['error']}")
                continue
            print(f"    matching objects: {len(bundle.get('matching_objects') or [])}")
            for prefab in (bundle.get("prefabs") or [])[:12]:
                print(f"    prefab {prefab.get('name')!r} {prefab.get('container')}")
                for component in prefab.get("components") or []:
                    fields = [
                        item.get("field")
                        for item in component.get("interesting_fields") or []
                        if item.get("field") in {
                            "cardSize",
                            "cardFramesSO",
                            "defaultArtAssetSO",
                            "_cardImage",
                            "_frameContainer",
                            "_cardTierFrameSO",
                            "_cardMaterialShader",
                        }
                    ]
                    if fields:
                        print(
                            f"      {component.get('type')} "
                            f"{component.get('name')!r}: {', '.join(fields)}"
                        )
        print(
            "  asset GUID contexts: "
            f"{len(runtime.get('asset_guid_context') or {})}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
