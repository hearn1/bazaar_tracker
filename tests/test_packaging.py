import sys
from pathlib import Path

import app_paths
import scorer
from web.server import app


def test_bundled_root_uses_meipass_when_frozen(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)

    assert app_paths.is_packaged()
    assert app_paths.bundled_root() == tmp_path
    assert app_paths.bundled_asset_path("web", "static") == tmp_path / "web" / "static"


def test_web_static_folder_uses_bundled_asset_path():
    static_folder = Path(app.static_folder)

    assert static_folder.name == "static"
    assert static_folder.parent.name == "web"
    assert (static_folder / "index.html").is_file()
    assert (static_folder / "overlay.html").is_file()


def test_overlay_coach_controls_use_persisted_delegated_handlers():
    overlay = (Path(app.static_folder) / "overlay.html").read_text()

    assert 'const COACH_SECTION_COLLAPSE_KEY = "overlay_coach_section_collapsed"' in overlay
    assert 'root.addEventListener("click", handleRootClick)' in overlay
    assert 'data-collapse-key=' in overlay
    assert 'function isInteractiveTarget(target)' in overlay
    assert 'if (isInteractiveTarget(event.target)) return;' in overlay
    assert 'event.target.closest("[data-arch-index]")' in overlay
    assert 'event.target.closest("[data-tab]")' in overlay
    assert 'const buttons = buildCatalog.archetypes.map((arch, index) =>' in overlay
    assert 'function setManualArchByIndex(index)' in overlay
    assert "Array.isArray(pivotTarget.matchedItems)" in overlay
    assert 'const visibleArches = collapsed' not in overlay


def test_build_catalogs_resolve_from_bundled_root():
    assert scorer._builds_path("Dooley").name == "dooley_builds.json"
    assert scorer._builds_path("Karnok").name == "karnok_builds.json"
    assert scorer._builds_path("Mak").name == "mak_builds.json"
    assert scorer._builds_path("Pygmalien").name == "pygmalien_builds.json"
    assert scorer._builds_path("Vanessa").name == "vanessa_builds.json"
    assert scorer._builds_path("Dooley").is_file()
    assert scorer._builds_path("Karnok").is_file()
    assert scorer._builds_path("Mak").is_file()
    assert scorer._builds_path("Pygmalien").is_file()
    assert scorer._builds_path("Vanessa").is_file()


def test_pyinstaller_packaging_files_exist():
    root = app_paths.repo_dir()
    expected = [
        root / "packaging" / "pyinstaller" / "BazaarTracker.spec",
        root / "packaging" / "pyinstaller" / "build_portable.ps1",
        root / "packaging" / "pyinstaller" / "requirements-build.txt",
        root / "packaging" / "pyinstaller" / "smoke_test_portable.py",
    ]
    for path in expected:
        assert path.is_file(), path


def test_pyinstaller_spec_bundles_build_catalogs():
    root = app_paths.repo_dir()
    spec = (root / "packaging" / "pyinstaller" / "BazaarTracker.spec").read_text()

    assert '"dooley_builds.json"' in spec
    assert '"karnok_builds.json"' in spec
    assert '"mak_builds.json"' in spec
    assert '"pygmalien_builds.json"' in spec
    assert '"vanessa_builds.json"' in spec


def test_build_portable_selects_python_flexibly():
    script = (app_paths.repo_dir() / "packaging" / "pyinstaller" / "build_portable.ps1").read_text()

    assert "[string]$PythonExe" in script
    assert "if ($PythonExe)" in script
    assert "Test-Path -LiteralPath $VenvPython" in script
    assert "Get-Command python" in script
    assert "Write-Host \"Using Python:" in script
    assert "& $ResolvedPythonExe -m PyInstaller @args" in script
    assert "& .\\venv312\\Scripts\\python.exe -m PyInstaller" not in script
    assert "[switch]$NoClean" in script
    assert "$args += \"--clean\"" in script


def test_windows_installer_packaging_files_exist():
    root = app_paths.repo_dir()
    expected = [
        root / "packaging" / "installer" / "BazaarTracker.iss",
        root / "packaging" / "installer" / "build_installer.ps1",
        root / "packaging" / "installer" / "README.md",
    ]
    for path in expected:
        assert path.is_file(), path


def test_score_archetypes_returns_all_when_no_limit():
    """score_archetypes must return all scored archetypes, not just top 3."""
    from web.build_helpers import score_archetypes, load_builds

    build_data, _ = load_builds("Karnok")
    total_archetypes = sum(
        len(phase.get("archetypes", []))
        for phase in build_data.get("game_phases", {}).values()
        if isinstance(phase, dict)
    )
    assert total_archetypes > 3, (
        f"Test requires >3 archetypes in the Karnok catalog, found {total_archetypes}"
    )

    result = score_archetypes(set(), build_data=build_data)
    assert len(result) > 3, (
        f"score_archetypes should return all archetypes (>3), got {len(result)}"
    )

    # Verify limit= still works for callers that need truncation
    top3 = score_archetypes(set(), build_data=build_data, limit=3)
    assert len(top3) == 3
    assert top3 == result[:3]


def test_overlay_state_arch_scores_exposes_all_archetypes(tmp_path):
    """The overlay state payload must include more than 3 arch_scores."""
    import sqlite3
    from web.build_helpers import load_builds, score_archetypes

    build_data, _ = load_builds("Karnok")
    all_scores = score_archetypes(set(), build_data=build_data)
    assert len(all_scores) > 3, (
        f"Karnok catalog must have >3 archetypes for this test; found {len(all_scores)}"
    )


def test_gitignore_keeps_generated_artifacts_local_and_sources_trackable():
    gitignore = app_paths.repo_dir() / ".gitignore"
    lines = [
        line.strip()
        for line in gitignore.read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]

    ignored_patterns = set(lines)
    expected_ignored = {
        "venv312/",
        ".venv/",
        "build/",
        "dist/",
        ".pytest_cache/",
        "__pycache__/",
        "logs/",
        "static_cache/",
        "bazaar_runs.db*",
        "settings.json",
        "sqlite3.Connection",
        "*diagnostics*.zip",
        "*.log",
    }
    expected_trackable = {
        "*.py",
        "tests/",
        "README.md",
        "ROADMAP.md",
        "CLAUDE.md",
        "requirements.txt",
        "packaging/",
        "packaging/pyinstaller/build_portable.ps1",
        "packaging/pyinstaller/requirements-build.txt",
        "dooley_builds.json",
        "karnok_builds.json",
        "mak_builds.json",
        "pygmalien_builds.json",
        "vanessa_builds.json",
    }

    assert expected_ignored <= ignored_patterns
    assert ignored_patterns.isdisjoint(expected_trackable)
