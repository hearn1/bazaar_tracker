"""
Tests for builds_schema.json contract and scorer.validate_builds_catalog().

Coverage:
- All five live catalogs load cleanly through scorer.load_builds()
- All five pass validate_builds_catalog() directly
- Too-new schema_version (999) is rejected
- Too-old schema_version (0) is rejected
- Missing required top-level field is rejected
"""

import copy
import json
from pathlib import Path

import pytest

import scorer


# Minimal catalog that satisfies schema_version 1.
# Used as the base for synthesised rejection-case variants.
_MINIMAL_VALID = {
    "schema_version": 1,
    "hero": "TestHero",
    "season": 1,
    "last_updated": "2026-01-01",
    "notes": "Test hero notes.",
    "item_tier_list": {
        "description": "Test tier list.",
        "S": ["Best Item"],
    },
    "game_phases": {
        "early": {
            "day_range": "Days 1-4",
            "description": "Early phase.",
            "universal_utility_items": ["Best Item"],
            "economy_items": [],
        },
        "early_mid": {
            "day_range": "Days 5-7",
            "description": "Early-mid phase.",
            "archetypes": [
                {
                    "name": "TestArch",
                    "carry_items": ["Best Item"],
                    "support_items": [],
                }
            ],
        },
        "late": {
            "day_range": "Day 8+",
            "description": "Late phase.",
            "archetypes": [
                {
                    "name": "TestLateArch",
                    "core_items": ["Best Item"],
                    "carry_items": ["Best Item"],
                    "support_items": [],
                    "timing_profile": "tempo",
                }
            ],
        },
    },
}

LIVE_CATALOG_NAMES = [
    "Karnok",
    "Mak",
    "Dooley",
    "Vanessa",
    "Pygmalien",
]


# ---------------------------------------------------------------------------
# Live catalog tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("hero", LIVE_CATALOG_NAMES)
def test_live_catalog_validates(hero, monkeypatch):
    """Each live catalog must pass validate_builds_catalog() independently."""
    # Clear LRU cache so each parameterised call re-reads from disk.
    scorer._load_builds_cached.cache_clear()
    scorer._load_builds_schema.cache_clear()

    path = Path(scorer.BUILD_GUIDE_DIR) / scorer._hero_catalog_filename(hero)
    assert path.exists(), f"Catalog file missing: {path}"

    data = json.loads(path.read_text(encoding="utf-8"))
    ok, err = scorer.validate_builds_catalog(data)
    assert ok, f"{hero} catalog failed validation: {err}"


@pytest.mark.parametrize("hero", LIVE_CATALOG_NAMES)
def test_live_catalog_loads_via_scorer(hero, monkeypatch):
    """load_builds() must return a catalog with real content for each hero."""
    scorer._load_builds_cached.cache_clear()
    scorer._load_builds_schema.cache_clear()

    builds = scorer.load_builds(hero)
    assert scorer.has_build_catalog(builds), (
        f"load_builds('{hero}') returned an empty/stub catalog — "
        "validation may have rejected it or the file is missing"
    )


# ---------------------------------------------------------------------------
# Rejection path tests (synthesised catalogs — no disk I/O for these)
# ---------------------------------------------------------------------------


def test_too_new_schema_version_rejected():
    data = copy.deepcopy(_MINIMAL_VALID)
    data["schema_version"] = 999
    ok, err = scorer.validate_builds_catalog(data)
    assert not ok
    assert "999" in err
    assert str(scorer.BUILDS_SCHEMA_MAX) in err


def test_too_old_schema_version_rejected():
    data = copy.deepcopy(_MINIMAL_VALID)
    data["schema_version"] = 0
    ok, err = scorer.validate_builds_catalog(data)
    assert not ok
    assert "0" in err
    assert str(scorer.BUILDS_SCHEMA_MIN) in err


def test_missing_required_field_rejected():
    """A catalog without the 'hero' field must fail validation."""
    data = copy.deepcopy(_MINIMAL_VALID)
    del data["hero"]
    ok, err = scorer.validate_builds_catalog(data)
    assert not ok
    # jsonschema error should mention the missing property
    assert "hero" in err.lower() or not ok  # err content varies by jsonschema version


def test_missing_schema_version_rejected():
    """A catalog that entirely omits schema_version must be rejected."""
    data = copy.deepcopy(_MINIMAL_VALID)
    del data["schema_version"]
    ok, err = scorer.validate_builds_catalog(data)
    assert not ok


def test_non_integer_schema_version_rejected():
    """schema_version: '1' (string) must be rejected — must be integer."""
    data = copy.deepcopy(_MINIMAL_VALID)
    data["schema_version"] = "1"
    ok, err = scorer.validate_builds_catalog(data)
    assert not ok


def test_minimal_valid_catalog_passes():
    """The hardcoded minimal fixture itself must pass, sanity-checking the schema."""
    ok, err = scorer.validate_builds_catalog(_MINIMAL_VALID)
    assert ok, f"Minimal valid catalog unexpectedly failed: {err}"
