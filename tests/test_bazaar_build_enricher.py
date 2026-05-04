import json

import bazaar_build_enricher as enricher


def test_manual_json_records_normalize_item_aliases_and_group_candidates():
    known = {"Launcher Core", "Terry-Dactyl", "YLW-M4NT1S", "Nanobot", "Levitation Pad"}
    records = enricher.records_from_json(
        json.dumps([
            {
                "title": "Terry-Dactyl Build Guide",
                "date": "2026-05-01",
                "items": ["Launcher Core", "Terry-Dactyl", "YLW-MANTIS", "Nanobots"],
            },
            {
                "title": "Terry-Dactyl Build",
                "date": "2026-05-02",
                "items": "Launcher Core\nTerry-Dactyl\nLevitation Pad\nNanobot",
            },
        ]),
        hero="Dooley",
        known_items=known,
    )

    summary = enricher.build_summary(
        records,
        category_url="https://bazaar-builds.net/category/builds/dooley-builds/",
        hero="Dooley",
        since=None,
        days=None,
        known_items=known,
        warnings=[],
    )

    group = summary["groups"][0]
    assert group["tag"] == "Terry-Dactyl Build"
    assert group["sample_count"] == 2
    assert group["candidate_core_items"] == ["Launcher Core", "Terry-Dactyl", "Nanobot"]
    assert "YLW-M4NT1S" in records[0].items
    assert "Nanobot" in records[0].items


def test_category_parser_keeps_placeholder_when_post_fetch_is_not_used():
    html = """
    <html><body>
      <article>
        <a href="/builds/dooley-terry-dactyl-build/">Dooley Terry-Dactyl Build</a>
        <time datetime="2026-05-01T12:00:00Z">May 1, 2026</time>
      </article>
    </body></html>
    """

    records = enricher.extract_category_records(
        "https://bazaar-builds.net/category/builds/dooley-builds/",
        html,
        hero="Dooley",
        limit=10,
    )

    assert len(records) == 1
    assert records[0].url == "https://bazaar-builds.net/builds/dooley-terry-dactyl-build/"
    assert records[0].date == "2026-05-01"
    assert records[0].items == []
    assert records[0].fetch_status == "not_attempted"


def test_missing_manual_file_warns_instead_of_crashing(tmp_path):
    result = enricher.load_manual_records(
        [str(tmp_path / "missing.txt")],
        hero="Dooley",
        known_items={"Launcher Core"},
    )

    assert result.records == []
    assert result.warnings == [f"Manual fallback file not found: {tmp_path / 'missing.txt'}"]


def test_canonical_group_merges_win_record_variants():
    known = {"Terry-Dactyl", "Launcher Core", "Freefall Simulator", "Double Whammy"}
    records = enricher.records_from_json(
        __import__("json").dumps([
            {
                "title": "Terry-Dactyl Dooley 10-3 Build",
                "date": "2026-04-01",
                "items": ["Terry-Dactyl", "Launcher Core"],
            },
            {
                "title": "Terry-Dactyl Dooley 10-4 Build",
                "date": "2026-04-15",
                "items": ["Terry-Dactyl", "Freefall Simulator", "Double Whammy"],
            },
        ]),
        hero="Dooley",
        known_items=known,
    )

    summary = enricher.build_summary(
        records,
        category_url="https://bazaar-builds.net/category/builds/dooley-builds/",
        hero="Dooley",
        since=None,
        days=None,
        known_items=known,
        warnings=[],
    )

    assert len(summary["groups"]) == 1
    group = summary["groups"][0]
    assert group["tag"] == "Terry-Dactyl Build"
    assert group["sample_count"] == 2


def test_hero_filter_drops_cross_hero_records():
    from datetime import date as _date

    cross_hero_records = [
        enricher.BuildRecord(url="https://example.com/karnok-build", title="karnok Build", hero="Dooley", tag="karnok Build"),
        enricher.BuildRecord(url="https://example.com/mak-vial", title="Mak Vial Build", hero="Dooley", tag="Mak Vial Build"),
        enricher.BuildRecord(url="https://example.com/terry", title="Terry-Dactyl Build", hero="Dooley", tag="Terry-Dactyl Build"),
    ]

    kept = enricher.filter_records(cross_hero_records, since=None, hero="Dooley")

    assert len(kept) == 1
    assert kept[0].title == "Terry-Dactyl Build"


def test_compare_finds_missing_items():
    catalog = {
        "hero": "Dooley",
        "game_phases": {
            "late": {
                "archetypes": [
                    {"name": "Terry-Dactyl", "core_items": ["Terry-Dactyl"], "support_items": ["Launcher Core"]},
                ]
            }
        },
    }
    artifact = {
        "filters": {"hero": "Dooley"},
        "groups": [
            {
                "tag": "Terry-Dactyl Build",
                "sample_count": 2,
                "candidate_core_items": ["Terry-Dactyl", "Freefall Simulator"],
                "candidate_support_items": ["Double Whammy"],
                "item_frequencies": [
                    {"item": "Terry-Dactyl", "count": 2, "frequency": 1.0},
                    {"item": "Freefall Simulator", "count": 2, "frequency": 1.0},
                    {"item": "Double Whammy", "count": 1, "frequency": 0.5},
                ],
            }
        ],
    }

    result = enricher.compare_artifact_to_catalog(artifact, catalog)

    assert len(result["updates"]) == 1
    update = result["updates"][0]
    assert update["archetype"] == "Terry-Dactyl"
    assert "Freefall Simulator" in update["missing_core"]
    assert "Terry-Dactyl" not in update["missing_core"]
    assert "Double Whammy" in update["missing_support"]
    assert result["new_archetypes"] == []


def test_compare_identifies_new_archetype_candidates():
    catalog = {
        "hero": "Dooley",
        "game_phases": {
            "late": {
                "archetypes": [
                    {"name": "Terry-Dactyl", "core_items": ["Terry-Dactyl"]},
                ]
            }
        },
    }
    artifact = {
        "filters": {"hero": "Dooley"},
        "groups": [
            {
                "tag": "C.O.R.A. Build",
                "sample_count": 1,
                "candidate_core_items": ["C.O.R.A.", "Dooltron Mainframe"],
                "candidate_support_items": [],
                "item_frequencies": [],
            }
        ],
    }

    result = enricher.compare_artifact_to_catalog(artifact, catalog)

    assert result["updates"] == []
    assert len(result["new_archetypes"]) == 1
    assert result["new_archetypes"][0]["group"] == "C.O.R.A. Build"
    assert "C.O.R.A." in result["new_archetypes"][0]["candidate_core"]
