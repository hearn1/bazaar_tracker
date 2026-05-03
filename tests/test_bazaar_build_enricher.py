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
