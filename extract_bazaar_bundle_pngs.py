#!/usr/bin/env python3
"""
extract_bazaar_bundle_pngs_v2.py

Exports Sprite / Texture2D images from one Bazaar Unity cache bundle.

Install once:
    py -m pip install UnityPy pillow

Run:
    py extract_bazaar_bundle_pngs_v2.py
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
from pathlib import Path
from typing import Optional

import app_paths
from PIL import Image as _PIL_Image

DEFAULT_BUNDLE = (
    r"C:\Users\Matt\AppData\LocalLow\Unity\Tempo Storm_The Bazaar"
    r"\1da6bc7e26414a6732f91f89588f2946"
    r"\b1214a7a5eb7c28a4094d56974612130"
    r"\__data"
)

DEFAULT_CACHE_ROOT = (
    r"C:\Users\Matt\AppData\LocalLow\Unity\Tempo Storm_The Bazaar"
)

UNITY_VERSION = "6000.3.11f1"


def _normalize_card_name(value: str) -> str:
    """Lowercase + strip non-alphanumerics. Used to build manifest keys."""
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


# Card art textures in Bazaar bundles follow the naming convention:
#   CF_<L|M|S|XL>_<HERO>_<CardFolderName>_D
# e.g. CF_M_JUL_CheeseWheel_D, CF_S_VAN_CrubbyLobster_D, CF_L_ADV_Wand_D.
# Hero codes seen so far include ADV, JUL, MAK, KAR, PYG, STE, VAN, DOO, COM,
# NEU, and NTR. Some shipped textures use mixed-case hero codes, numbered _D
# variants, trailing whitespace after _D, or no suffix at all.
CARD_NAME_RE = re.compile(
    r"^CF_[A-Z]+_[A-Za-z]{2,5}_(.+?)(?:_D\d?|_D\s+|)\s*$"
)
CARD_D_SUFFIX_RE = re.compile(r"_D(?:\d|\s*)\s*$")


def _parse_card_texture_name(name: str) -> Optional[str]:
    """Return the card folder name if ``name`` looks like card art, else None.

    Example: 'CF_M_JUL_CheeseWheel_D' -> 'CheeseWheel'.

    This filter lets us identify cards by texture name alone, without relying
    on Addressables container paths. Shipped install bundles strip container
    metadata; user-data cache bundles preserve it. Names are stable across
    both, so we filter by name pattern.
    """
    if not name:
        return None
    match = CARD_NAME_RE.match(name)
    if match is None:
        return None
    card_folder = match.group(1)
    if not _card_texture_has_d_suffix(name):
        card_folder = re.sub(r"\d+$", "", card_folder)
    return card_folder or None


def _card_texture_has_d_suffix(name: str) -> bool:
    return bool(CARD_D_SUFFIX_RE.search(name or ""))


def _fix_alpha_if_broken(path: Path) -> None:
    """Force alpha=255 if image has rich RGB but near-zero alpha everywhere."""
    try:
        img = _PIL_Image.open(path).convert("RGBA")
        data = img.tobytes()
        total = img.width * img.height
        if total <= 0:
            return

        alpha_zero = sum(1 for i in range(3, len(data), 4) if data[i] == 0)
        if alpha_zero / total < 0.90:
            return

        r_vals = data[0::4]
        r_mean = sum(r_vals) / total
        r_std = (sum((x - r_mean) ** 2 for x in r_vals) / total) ** 0.5
        if r_std < 20:
            return

        r, g, b, _ = img.split()
        opaque = _PIL_Image.new("L", img.size, 255)
        _PIL_Image.merge("RGBA", (r, g, b, opaque)).save(path)
    except Exception:
        pass


def _image_quality(path: Path) -> dict:
    """Return cheap visual diagnostics for spotting raw/incomplete layers."""
    try:
        img = _PIL_Image.open(path).convert("RGBA")
    except Exception as exc:
        return {
            "quality_flags": ["image_unreadable"],
            "quality_suspect": True,
            "quality_note": str(exc)[:160],
        }

    total = img.width * img.height
    if total <= 0:
        return {"quality_flags": ["image_empty"], "quality_suspect": True}

    alpha_hist = img.getchannel("A").histogram()
    visible = total - alpha_hist[0]
    opaque = sum(alpha_hist[250:])
    visible_ratio = visible / total
    opaque_ratio = opaque / total

    flags = []
    if visible_ratio < 0.55:
        flags.append("low_visible_alpha")
    if opaque_ratio < 0.03:
        flags.append("low_opaque_alpha")

    # A raw mask/layer can have visible alpha but almost no useful RGB signal.
    if visible:
        sample = img.copy()
        sample.thumbnail((128, 128))
        sample_data = sample.tobytes()
        sample_visible = 0
        rgb_nonblack = 0
        rgb_varied = 0
        for i in range(0, len(sample_data), 4):
            alpha = sample_data[i + 3]
            if alpha <= 0:
                continue
            sample_visible += 1
            r, g, b = sample_data[i], sample_data[i + 1], sample_data[i + 2]
            if max(r, g, b) > 8:
                rgb_nonblack += 1
            if max(r, g, b) - min(r, g, b) > 8:
                rgb_varied += 1
        rgb_nonblack_ratio = rgb_nonblack / sample_visible if sample_visible else 0.0
        rgb_varied_ratio = rgb_varied / sample_visible if sample_visible else 0.0
        if rgb_nonblack_ratio < 0.25:
            flags.append("mostly_black_visible_pixels")
        if rgb_varied_ratio < 0.10:
            flags.append("low_color_variation")
    else:
        rgb_nonblack_ratio = 0.0
        rgb_varied_ratio = 0.0
        flags.append("fully_transparent")

    severe_flags = {"image_unreadable", "image_empty", "fully_transparent", "mostly_black_visible_pixels"}
    quality_suspect = (
        visible_ratio < 0.30
        or opaque_ratio < 0.01
        or any(flag in severe_flags for flag in flags)
    )

    return {
        "alpha_visible_percent": round(visible_ratio * 100, 2),
        "alpha_opaque_percent": round(opaque_ratio * 100, 2),
        "visible_rgb_nonblack_percent": round(rgb_nonblack_ratio * 100, 2),
        "visible_rgb_varied_percent": round(rgb_varied_ratio * 100, 2),
        "quality_flags": flags,
        "quality_suspect": quality_suspect,
    }


def _entry_quality_score(entry: dict) -> tuple[int, float, float, int]:
    flags = entry.get("quality_flags") or []
    visible = float(entry.get("alpha_visible_percent") or 0)
    opaque = float(entry.get("alpha_opaque_percent") or 0)
    pixels = (entry.get("width") or 0) * (entry.get("height") or 0)
    return (0 if flags else 1, visible, opaque, pixels)


def safe_filename(value: str, fallback: str) -> str:
    value = (value or "").strip() or fallback
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value[:180] or fallback


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path

    for i in range(2, 10000):
        candidate = path.with_name(f"{path.stem}_{i}{path.suffix}")
        if not candidate.exists():
            return candidate

    raise RuntimeError(f"Could not generate unique filename for {path}")


def classify_image(width: int, height: int) -> str:
    if width == 1024 and height == 1024:
        return "likely_card_art"
    if width >= 512 and height >= 512:
        return "large_art"
    if width <= 128 and height <= 128:
        return "small_icon"
    if height > width * 1.5:
        return "portrait_or_tall_art"
    return "misc"


def export_image(image, out_dir: Path, filename_base: str) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = unique_path(out_dir / f"{filename_base}.png")
    image.save(path)
    return path


def write_manifests(out_dir: Path, rows: list[dict]) -> None:
    json_path = out_dir / "manifest.json"
    csv_path = out_dir / "manifest.csv"

    json_path.write_text(json.dumps(rows, indent=2), encoding="utf-8")

    if rows:
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)

    print("Manifest JSON:", json_path)
    print("Manifest CSV: ", csv_path)


def _process_bundle_cards_only(
    bundle_path: Path,
    out_dir: Path,
    by_card_key: dict[str, dict],
    counts: dict[str, int],
    *,
    min_width: int,
    min_height: int,
    cache_root: Optional[Path] = None,
    UnityPy_module=None,
) -> bool:
    """Export card-art Texture2D objects from one bundle into ``out_dir``.

    Mutates ``by_card_key`` and ``counts`` in place. Returns True if the bundle
    parsed and ran to completion, False if UnityPy.load failed.

    Cards are identified by texture name pattern (CF_<size>_<HERO>_<...>_D)
    rather than container path, because shipped install bundles strip
    Addressables container metadata.

    On collision (same normalized card key already in ``by_card_key``), keeps
    whichever entry has the higher pixel count; original wins on tie.
    """
    try:
        env = UnityPy_module.load(str(bundle_path))
    except Exception as exc:
        print(f"[ScanAll] failed to load {bundle_path}: {exc}")
        counts["bundle_errors"] = counts.get("bundle_errors", 0) + 1
        return False

    container_by_path_id: dict[int, str] = {}
    for key, obj_ref in env.container.items():
        try:
            if obj_ref.type.name == "Texture2D":
                container_by_path_id[obj_ref.path_id] = key
        except Exception:
            continue

    # Bundle path recorded in manifest is relative to cache_root when possible,
    # so the manifest stays portable across machines that share the same root.
    if cache_root is not None:
        try:
            bundle_record = str(bundle_path.relative_to(cache_root))
        except ValueError:
            bundle_record = str(bundle_path)
    else:
        bundle_record = str(bundle_path)

    for obj in env.objects:
        try:
            if obj.type.name != "Texture2D":
                continue
        except Exception:
            continue

        # Read the object so we can inspect its m_Name. Card detection now
        # filters on texture name, not container path -- shipped install
        # bundles have ~400 textures with empty container entries, all of
        # which we'd miss if we only trusted env.container.
        try:
            data = obj.read()
        except Exception as e:
            counts["errors"] += 1
            print(f"Skipped Texture2D path_id={getattr(obj, 'path_id', '?')}: {e}")
            continue

        tex_name = (
            getattr(data, "m_Name", None)
            or getattr(data, "name", None)
            or ""
        )
        card_folder = _parse_card_texture_name(tex_name)
        if card_folder is None:
            counts["skipped"] += 1
            continue
        has_d_suffix = _card_texture_has_d_suffix(tex_name)

        # Container path is informational only when present; useful for
        # manual inspection but not required.
        container_key = container_by_path_id.get(obj.path_id, "")
        image_file = f"{tex_name}.png"

        try:
            image = getattr(data, "image", None)
            if image is None:
                counts["skipped"] += 1
                continue
            width, height = image.width, image.height
            if width < min_width or height < min_height:
                counts["skipped"] += 1
                continue
            if not has_d_suffix and (width != 1024 or height != 1024 or "_" in card_folder):
                counts["skipped"] += 1
                continue

            normalized = _normalize_card_name(card_folder)
            out_dir.mkdir(parents=True, exist_ok=True)
            path = unique_path(out_dir / image_file)
            image.save(path)
            _fix_alpha_if_broken(path)
            quality = _image_quality(path)

            entry = {
                "card_folder": card_folder,
                "image_file": path.name,
                "has_d_suffix": has_d_suffix,
                "container_key": container_key,
                "texture_path_id": obj.path_id,
                "width": width,
                "height": height,
                "bundle": bundle_record,
            }
            entry.update(quality)

            existing = by_card_key.get(normalized)
            if existing is not None:
                # Prefer explicit _D variants, then visually healthier images,
                # then higher resolution. This keeps bad raw layers from
                # winning just because they share the standard 1024x1024 size.
                existing_has_d_suffix = bool(existing.get("has_d_suffix"))
                keep_existing = False
                if existing_has_d_suffix and not has_d_suffix:
                    keep_existing = True
                elif not existing_has_d_suffix and has_d_suffix:
                    keep_existing = False
                else:
                    keep_existing = _entry_quality_score(existing) >= _entry_quality_score(entry)

                counts["collisions"] = counts.get("collisions", 0) + 1
                if keep_existing:
                    print(
                        f"[ScanAll] collision: {normalized!r} - keeping "
                        f"{existing['image_file']} score={_entry_quality_score(existing)}, "
                        f"ignoring {path.name} score={_entry_quality_score(entry)}"
                    )
                    try:
                        path.unlink()
                    except OSError:
                        pass
                    continue

                # New entry wins. Remove the old PNG so we don't leave orphans.
                old_path = out_dir / existing["image_file"]
                if old_path.is_file():
                    try:
                        old_path.unlink()
                    except OSError:
                        pass
                print(
                    f"[ScanAll] collision: {normalized!r} - replacing "
                    f"{existing['image_file']} score={_entry_quality_score(existing)} "
                    f"with {path.name} score={_entry_quality_score(entry)}"
                )

            counts["Texture2D"] += 1
            if entry.get("quality_flags"):
                counts["suspect"] = counts.get("suspect", 0) + 1
            by_card_key[normalized] = entry

            flags = ",".join(entry.get("quality_flags") or [])
            suffix = f"  flags={flags}" if flags else ""
            print(f"EXPORTED  {width:4d}x{height:<4d}  {path.name}{suffix}")
        except Exception as e:
            counts["errors"] += 1
            print(f"Skipped Texture2D path_id={getattr(obj, 'path_id', '?')}: {e}")

    return True


def main() -> int:
    try:
        import UnityPy
        UnityPy.config.FALLBACK_UNITY_VERSION = UNITY_VERSION
    except ImportError:
        print("Missing UnityPy.")
        print("Run: py -m pip install UnityPy pillow")
        return 2

    desktop = Path(os.environ["USERPROFILE"]) / "Desktop"
    script_dir = Path(__file__).resolve().parent

    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", default=DEFAULT_BUNDLE)
    parser.add_argument("--out", default=None)
    parser.add_argument("--min-width", type=int, default=16)
    parser.add_argument("--min-height", type=int, default=16)
    parser.add_argument(
        "--cards-only",
        action="store_true",
        help="Export only card-art Texture2D entries (CF_<size>_<HERO>_..._D) to a flat dir with by_card_key manifest.",
    )
    parser.add_argument(
        "--scan-all",
        action="store_true",
        help="Walk the cache root for every Unity asset bundle (__data and *.bundle). Requires --cards-only.",
    )
    parser.add_argument(
        "--cache-root",
        default=DEFAULT_CACHE_ROOT,
        help=f"Cache root for --scan-all (default: {DEFAULT_CACHE_ROOT}).",
    )
    args = parser.parse_args()

    if args.scan_all and not args.cards_only:
        print("[ScanAll] --scan-all requires --cards-only.")
        return 2

    if args.out is None:
        if args.cards_only:
            default_out = app_paths.image_cache_dir()
        else:
            default_out = desktop / "bazaar_card_art_test_v2"
        args.out = str(default_out)

    bundle_path = Path(args.bundle)
    out_dir = Path(args.out)
    sprites_dir = out_dir / "sprites"
    textures_dir = out_dir / "textures"

    out_dir.mkdir(parents=True, exist_ok=True)

    if args.scan_all:
        cache_root = Path(args.cache_root)
        if not cache_root.is_dir():
            print(f"[ScanAll] cache root not found: {cache_root}")
            return 2

        print(f"[ScanAll] scanning cache root: {cache_root}")
        # Find both Unity bundle naming conventions:
        # - "__data" files: used in the user-data Addressables cache
        #   (%USERPROFILE%\AppData\LocalLow\...)
        # - "*.bundle" files: used in the Steam install's StreamingAssets
        #   (e.g. <install>\TheBazaar_Data\StreamingAssets\aa\StandaloneWindows64\)
        # UnityPy.load() handles both formats identically.
        bundle_paths = sorted(
            {
                p
                for p in (
                    *cache_root.rglob("__data"),
                    *cache_root.rglob("*.bundle"),
                )
                if p.is_file()
            }
        )
        print(f"[ScanAll] found {len(bundle_paths)} bundle file(s)")

        # Clear any stale PNGs / manifest from a prior run so we don't leave
        # orphans behind. unique_path() guards against in-run collisions but
        # not against pre-existing files.
        cleared = 0
        for stale in out_dir.glob("*.png"):
            try:
                stale.unlink()
                cleared += 1
            except OSError:
                pass
        stale_manifest = out_dir / "manifest.json"
        if stale_manifest.is_file():
            try:
                stale_manifest.unlink()
            except OSError:
                pass
        if cleared:
            print(f"[ScanAll] cleared {cleared} stale PNG(s) from prior run")

        by_card_key: dict[str, dict] = {}
        counts = {
            "Sprite": 0, "Texture2D": 0, "skipped": 0, "errors": 0,
            "bundle_errors": 0, "collisions": 0,
        }
        bundles_ok = 0

        for i, bp in enumerate(bundle_paths, start=1):
            print(f"\n[ScanAll] [{i}/{len(bundle_paths)}] {bp}")
            ok = _process_bundle_cards_only(
                bp, out_dir, by_card_key, counts,
                min_width=args.min_width, min_height=args.min_height,
                cache_root=cache_root, UnityPy_module=UnityPy,
            )
            if ok:
                bundles_ok += 1

        manifest_path = out_dir / "manifest.json"
        manifest_path.write_text(
            json.dumps({"by_card_key": by_card_key}, indent=2, sort_keys=True),
            encoding="utf-8",
        )

        print()
        print(f"[ScanAll] bundles scanned:    {bundles_ok}/{len(bundle_paths)}")
        print(f"[ScanAll] bundle load errors: {counts['bundle_errors']}")
        print(f"[ScanAll] textures exported:  {counts['Texture2D']}")
        print(f"[ScanAll] manifest entries:   {len(by_card_key)}")
        print(f"[ScanAll] collisions:         {counts['collisions']}")
        print(f"[ScanAll] skipped (filters):  {counts['skipped']}")
        print(f"[ScanAll] per-object errors:  {counts['errors']}")
        print(f"[ScanAll] manifest:           {manifest_path}")
        print(f"[ScanAll] output:             {out_dir}")
        return 0

    print("Loading:", bundle_path)
    env = UnityPy.load(str(bundle_path))
    print("\n=== CONTAINER ENTRIES ===")
    for key, obj in env.container.items():
        try:
            print(key, "=>", obj.type.name, obj.path_id)
        except Exception as e:
            print(key, "=>", e)

    container_by_path_id: dict[int, str] = {}
    if args.cards_only:
        for key, obj_ref in env.container.items():
            try:
                if obj_ref.type.name == "Texture2D":
                    container_by_path_id[obj_ref.path_id] = key
            except Exception:
                continue

    manifest: list[dict] = []
    by_card_key: dict[str, dict] = {}
    counts = {"Sprite": 0, "Texture2D": 0, "skipped": 0, "errors": 0}

    for obj in env.objects:
        type_name = obj.type.name

        if args.cards_only:
            if type_name != "Texture2D":
                continue

            try:
                data = obj.read()
            except Exception as e:
                counts["errors"] += 1
                print(f"Skipped Texture2D path_id={getattr(obj, 'path_id', '?')}: {e}")
                continue

            tex_name = (
                getattr(data, "m_Name", None)
                or getattr(data, "name", None)
                or ""
            )
            card_folder = _parse_card_texture_name(tex_name)
            if card_folder is None:
                continue
            has_d_suffix = _card_texture_has_d_suffix(tex_name)

            container_key = container_by_path_id.get(obj.path_id, "")
            image_file = f"{tex_name}.png"

            try:
                image = getattr(data, "image", None)
                if image is None:
                    counts["skipped"] += 1
                    continue
                width, height = image.width, image.height
                if width < args.min_width or height < args.min_height:
                    counts["skipped"] += 1
                    continue
                if not has_d_suffix and (width != 1024 or height != 1024 or "_" in card_folder):
                    counts["skipped"] += 1
                    continue

                out_dir.mkdir(parents=True, exist_ok=True)
                path = unique_path(out_dir / image_file)
                image.save(path)
                _fix_alpha_if_broken(path)
                counts["Texture2D"] += 1

                normalized = _normalize_card_name(card_folder)
                existing = by_card_key.get(normalized)
                if existing is not None and existing.get("has_d_suffix") and not has_d_suffix:
                    existing = by_card_key[normalized]
                    print(
                        f"[CardsOnly] collision: {normalized!r} - keeping "
                        f"{existing['image_file']} (_D variant), ignoring "
                        f"{image_file} (no _D suffix)"
                    )
                elif existing is not None and (not existing.get("has_d_suffix") and has_d_suffix):
                    by_card_key[normalized] = {
                        "card_folder": card_folder,
                        "image_file": path.name,
                        "has_d_suffix": has_d_suffix,
                        "container_key": container_key,
                        "texture_path_id": obj.path_id,
                        "width": width,
                        "height": height,
                    }
                    print(
                        f"[CardsOnly] collision: {normalized!r} - replacing "
                        f"{existing['image_file']} (no _D suffix) with {image_file} (_D variant)"
                    )
                elif existing is not None:
                    print(
                        f"[CardsOnly] collision: {normalized!r} - keeping "
                        f"{existing['image_file']}, ignoring {image_file}"
                    )
                else:
                    by_card_key[normalized] = {
                        "card_folder": card_folder,
                        "image_file": path.name,
                        "has_d_suffix": has_d_suffix,
                        "container_key": container_key,
                        "texture_path_id": obj.path_id,
                        "width": width,
                        "height": height,
                    }

                print(f"EXPORTED  {width:4d}x{height:<4d}  {path.name}")
            except Exception as e:
                counts["errors"] += 1
                print(f"Skipped Texture2D path_id={getattr(obj, 'path_id', '?')}: {e}")
            continue

        if type_name not in ("Sprite", "Texture2D"):
            continue

        try:
            data = obj.read()
            name = getattr(data, "name", "") or f"{type_name}_{obj.path_id}"
            image = getattr(data, "image", None)

            if image is None:
                counts["skipped"] += 1
                continue

            width, height = image.width, image.height
            if width < args.min_width or height < args.min_height:
                counts["skipped"] += 1
                continue

            classification = classify_image(width, height)

            filename_base = safe_filename(name, f"{type_name}_{obj.path_id}")
            target_dir = sprites_dir if type_name == "Sprite" else textures_dir
            path = export_image(image, target_dir, filename_base)

            counts[type_name] += 1

            row = {
                "png_file": path.name,
                "relative_path": str(path.relative_to(out_dir)),
                "asset_name": name,
                "type": type_name,
                "path_id": obj.path_id,
                "width": width,
                "height": height,
                "classification": classification,
                "bundle": str(bundle_path),
            }
            manifest.append(row)

            print(
                f"EXPORTED {type_name:9s} "
                f"{width:4d}x{height:<4d} "
                f"{classification:18s} "
                f"{path.name}"
            )

        except Exception as e:
            counts["errors"] += 1
            print(f"Skipped {type_name} path_id={getattr(obj, 'path_id', '?')}: {e}")

    if args.cards_only:
        manifest_path = out_dir / "manifest.json"
        manifest_path.write_text(
            json.dumps({"by_card_key": by_card_key}, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        print()
        print(f"[CardsOnly] exported {len(by_card_key)} card images")
        print(f"[CardsOnly] manifest: {manifest_path}")
        print(f"[CardsOnly] output:   {out_dir}")
    else:
        write_manifests(out_dir, manifest)

        print()
        print("Done.")
        print("Sprites exported: ", counts["Sprite"])
        print("Textures exported:", counts["Texture2D"])
        print("Skipped:          ", counts["skipped"])
        print("Errors:           ", counts["errors"])
        print("Output:           ", out_dir)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
