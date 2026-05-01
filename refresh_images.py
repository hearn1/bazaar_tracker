"""Refresh local card image assets from Bazaar Unity bundles."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Iterable, Optional

import app_paths
import db
from extract_bazaar_bundle_pngs import UNITY_VERSION, _process_bundle_cards_only
from web.card_images import normalize_card_name


def _dedupe_paths(paths: Iterable[Path]) -> list[Path]:
    seen = set()
    result = []
    for path in paths:
        resolved = path.expanduser()
        key = str(resolved).lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(resolved)
    return result


def _steam_library_roots() -> list[Path]:
    candidates = []
    program_x86 = os.environ.get("ProgramFiles(x86)")
    program_files = os.environ.get("ProgramFiles")
    if program_x86:
        candidates.append(Path(program_x86) / "Steam")
    if program_files:
        candidates.append(Path(program_files) / "Steam")
    candidates.append(Path(r"C:\Program Files (x86)\Steam"))
    candidates.append(Path(r"C:\Program Files\Steam"))

    libraries = []
    for steam_root in _dedupe_paths(candidates):
        if steam_root.is_dir():
            libraries.append(steam_root / "steamapps" / "common")
        library_file = steam_root / "steamapps" / "libraryfolders.vdf"
        if not library_file.is_file():
            continue
        try:
            text = library_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for raw in text.splitlines():
            raw = raw.strip()
            if not raw.startswith('"path"'):
                continue
            parts = raw.split('"')
            if len(parts) >= 4:
                libraries.append(Path(parts[3].replace("\\\\", "\\")) / "steamapps" / "common")
    return _dedupe_paths(libraries)


def _standalone_dir_from_install_root(root: Path) -> Optional[Path]:
    root = root.expanduser()
    candidates = [
        root,
        root / "TheBazaar_Data" / "StreamingAssets" / "aa" / "StandaloneWindows64",
        root / "The Bazaar" / "TheBazaar_Data" / "StreamingAssets" / "aa" / "StandaloneWindows64",
    ]
    for candidate in candidates:
        if candidate.is_dir() and (
            any(candidate.glob("*.bundle")) or (candidate / "catalog.json").exists()
        ):
            return candidate
    return None


def detect_install_roots() -> list[Path]:
    roots = []
    for library in _steam_library_roots():
        roots.extend(
            [
                library / "The Bazaar",
                library / "TheBazaar",
            ]
        )
    found = []
    for root in _dedupe_paths(roots):
        standalone = _standalone_dir_from_install_root(root)
        if standalone:
            found.append(standalone)
    return _dedupe_paths(found)


def detect_cache_roots() -> list[Path]:
    roots = []
    userprofile = os.environ.get("USERPROFILE")
    localappdata = os.environ.get("LOCALAPPDATA")
    if userprofile:
        roots.append(Path(userprofile) / "AppData" / "LocalLow" / "Unity" / "Tempo Storm_The Bazaar")
    if localappdata:
        roots.append(Path(localappdata).parent / "LocalLow" / "Unity" / "Tempo Storm_The Bazaar")
    return [path for path in _dedupe_paths(roots) if path.is_dir()]


def bundle_files(root: Path) -> list[Path]:
    root = root.expanduser()
    if root.is_file():
        return [root]
    return sorted(
        {
            p
            for p in (
                *root.rglob("__data"),
                *root.rglob("*.bundle"),
            )
            if p.is_file()
        }
    )


def load_image_manifest(image_dir: Optional[Path] = None) -> dict:
    path = (image_dir or app_paths.image_cache_dir()) / "manifest.json"
    if not path.is_file():
        return {"by_card_key": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("by_card_key"), dict):
            return data
    except (OSError, json.JSONDecodeError):
        pass
    return {"by_card_key": {}}


def card_cache_names(card_type: str = "TCardItem") -> list[str]:
    """Return distinct non-empty card names from card_cache, filtered by card_type.

    Defaults to TCardItem because only playable items have card art in Unity
    bundles. Skills, encounters, events, etc. are excluded from coverage
    calculations to avoid inflating the denominator.
    """
    db.init_db()
    conn = db.get_conn()
    try:
        rows = conn.execute(
            "SELECT DISTINCT name FROM card_cache "
            "WHERE card_type = ? AND name IS NOT NULL AND name != '' AND name != 'Unknown'",
            (card_type,),
        ).fetchall()
        return [str(row["name"]) for row in rows]
    finally:
        conn.close()


def coverage_report(image_dir: Optional[Path] = None, *, limit: int = 25) -> dict:
    from web.card_images import NAME_ALIASES
    manifest = load_image_manifest(image_dir)
    by_card_key = manifest.get("by_card_key") or {}
    names = card_cache_names()
    normalized_names = {
        normalize_card_name(name): name
        for name in names
        if normalize_card_name(name)
    }
    # A name hits if the manifest has a direct key match OR an alias match.
    def resolves(key: str) -> bool:
        return key in by_card_key or (key in NAME_ALIASES and NAME_ALIASES[key] in by_card_key)

    hits = sorted(k for k in normalized_names if resolves(k))
    missing_keys = sorted(k for k in normalized_names if not resolves(k))
    missing = [
        {"card_key": key, "name": normalized_names[key]}
        for key in missing_keys[:limit]
    ]
    return {
        "image_dir": str(image_dir or app_paths.image_cache_dir()),
        "manifest_entries": len(by_card_key),
        "card_cache_names": len(normalized_names),
        "coverage_count": len(hits),
        "coverage_percent": round((len(hits) / len(normalized_names)) * 100, 1) if normalized_names else 0,
        "missing_count": len(missing_keys),
        "missing_sample": missing,
    }


def _prepare_output_dir(out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for stale in out_dir.glob("*.png"):
        try:
            stale.unlink()
        except OSError:
            pass
    manifest = out_dir / "manifest.json"
    if manifest.is_file():
        try:
            manifest.unlink()
        except OSError:
            pass


def refresh_images(
    *,
    install_root: Optional[Path] = None,
    cache_root: Optional[Path] = None,
    out_dir: Optional[Path] = None,
    min_width: int = 16,
    min_height: int = 16,
    UnityPy_module=None,
) -> dict:
    """Extract card images from detected/explicit Unity bundle roots."""
    if UnityPy_module is None:
        try:
            import UnityPy
            UnityPy.config.FALLBACK_UNITY_VERSION = UNITY_VERSION
            UnityPy_module = UnityPy
        except ImportError as exc:
            raise RuntimeError("UnityPy is not installed. Install UnityPy and pillow to refresh images.") from exc

    roots: list[Path] = []
    explicit_roots = []
    if install_root:
        standalone = _standalone_dir_from_install_root(install_root)
        if standalone is None:
            raise FileNotFoundError(f"Install root does not contain Bazaar bundle files: {install_root}")
        explicit_roots.append(standalone)
    if cache_root:
        if not cache_root.is_dir():
            raise FileNotFoundError(f"Cache root not found: {cache_root}")
        explicit_roots.append(cache_root)

    if explicit_roots:
        roots = explicit_roots
    else:
        roots = [*detect_install_roots(), *detect_cache_roots()]

    roots = _dedupe_paths(roots)
    if not roots:
        raise FileNotFoundError(
            "No Bazaar install or Addressables cache roots found. Use --install-root or --cache-root."
        )

    out_dir = out_dir or app_paths.image_cache_dir()
    _prepare_output_dir(out_dir)

    by_card_key: dict[str, dict] = {}
    counts = {
        "Sprite": 0,
        "Texture2D": 0,
        "skipped": 0,
        "errors": 0,
        "bundle_errors": 0,
        "collisions": 0,
    }
    root_summaries = []
    bundles_ok = 0
    bundles_total = 0

    for root in roots:
        bundles = bundle_files(root)
        bundles_total += len(bundles)
        ok_for_root = 0
        for bundle in bundles:
            ok = _process_bundle_cards_only(
                bundle,
                out_dir,
                by_card_key,
                counts,
                min_width=min_width,
                min_height=min_height,
                cache_root=root,
                UnityPy_module=UnityPy_module,
            )
            if ok:
                bundles_ok += 1
                ok_for_root += 1
        root_summaries.append({
            "root": str(root),
            "bundles_found": len(bundles),
            "bundles_loaded": ok_for_root,
        })

    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps({"by_card_key": by_card_key}, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    coverage = coverage_report(out_dir)
    return {
        "image_dir": str(out_dir),
        "manifest_path": str(manifest_path),
        "roots": root_summaries,
        "bundles_found": bundles_total,
        "bundles_loaded": bundles_ok,
        "counts": counts,
        "manifest_entries": len(by_card_key),
        "coverage": coverage,
    }


def print_summary(summary: dict) -> None:
    coverage = summary.get("coverage") or {}
    print("[Images] Refresh summary:")
    print(f"  Output: {summary.get('image_dir')}")
    print(f"  Manifest: {summary.get('manifest_path')}")
    print(f"  Bundles loaded: {summary.get('bundles_loaded')}/{summary.get('bundles_found')}")
    print(f"  Images exported: {summary.get('manifest_entries')}")
    print(
        "  Coverage: "
        f"{coverage.get('coverage_count', 0)}/{coverage.get('card_cache_names', 0)} "
        f"({coverage.get('coverage_percent', 0)}%)"
    )
    if coverage.get("missing_sample"):
        print("  Missing sample:")
        for item in coverage["missing_sample"][:10]:
            print(f"    {item['name']} ({item['card_key']})")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Refresh Bazaar card images from local Unity bundles")
    parser.add_argument("--install-root", type=Path, default=None,
                        help="Bazaar install root or StandaloneWindows64 asset bundle directory")
    parser.add_argument("--cache-root", type=Path, default=None,
                        help="Unity Addressables cache root")
    parser.add_argument("--out", type=Path, default=None,
                        help="Image output directory (default: centralized image cache)")
    parser.add_argument("--coverage-only", action="store_true",
                        help="Only report current manifest coverage")
    parser.add_argument("--min-width", type=int, default=16)
    parser.add_argument("--min-height", type=int, default=16)
    args = parser.parse_args(argv)

    if args.coverage_only:
        print_summary({
            "image_dir": str(args.out or app_paths.image_cache_dir()),
            "manifest_path": str((args.out or app_paths.image_cache_dir()) / "manifest.json"),
            "bundles_loaded": 0,
            "bundles_found": 0,
            "manifest_entries": load_image_manifest(args.out).get("by_card_key", {}).__len__(),
            "coverage": coverage_report(args.out),
        })
        return 0

    try:
        summary = refresh_images(
            install_root=args.install_root,
            cache_root=args.cache_root,
            out_dir=args.out,
            min_width=args.min_width,
            min_height=args.min_height,
        )
    except Exception as exc:
        print(f"[Images] ERROR: {exc}")
        return 2
    print_summary(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
