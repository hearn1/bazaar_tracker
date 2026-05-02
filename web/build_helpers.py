"""
web/build_helpers.py — Build catalog helpers for the Bazaar Tracker web layer.

Covers:
  - Build catalog loading (per-hero, cached)
  - Relevant-item set extraction from catalog
  - Archetype scoring against current board
  - Phase-notes assembly
  - Run-tier classification
  - Insight extraction from score_notes

All functions are pure or read-only — no DB writes, no Flask routes.
"""

import re
from functools import lru_cache
from typing import Optional

import scorer

# ── Constants ─────────────────────────────────────────────────────────────────

_BUILD_SCORE_WEIGHTS = {
    "core_items": 0.50,
    "carry_items": 0.35,
    "support_items": 0.15,
}


# ── Build catalog loading ─────────────────────────────────────────────────────

def collect_build_relevant_items(build_data: Optional[dict]) -> set[str]:
    """Return the union of every named item that appears in the catalog."""
    items = set()
    build_data = build_data or {}
    for tier, tier_items in build_data.get("item_tier_list", {}).items():
        if tier == "description":
            continue
        if isinstance(tier_items, list):
            items.update(tier_items)
    for phase in build_data.get("game_phases", {}).values():
        for arch in phase.get("archetypes", []):
            for key in ("core_items", "carry_items", "support_items"):
                items.update(i for i in arch.get(key, []) if not i.startswith("TODO"))
        items.update(phase.get("universal_utility_items", []))
        items.update(phase.get("economy_items", []))
    return items


@lru_cache(maxsize=None)
def _build_catalog_for_hero(hero_name: str) -> tuple[dict, frozenset[str]]:
    build_data = scorer.load_builds(hero_name)
    relevant_items = collect_build_relevant_items(build_data)
    return build_data, frozenset(relevant_items)


def load_builds(hero: Optional[str] = None) -> tuple[dict, set[str]]:
    """Return (build_data, relevant_items_set) for the given hero."""
    hero_name = scorer.normalize_hero_name(hero) or scorer.DEFAULT_HERO
    build_data, relevant_items = _build_catalog_for_hero(hero_name)
    return build_data, set(relevant_items)


def clean_build_items(items: list[str]) -> list[str]:
    """Strip TODO placeholders and deduplicate, preserving order."""
    cleaned = []
    seen = set()
    for item in items or []:
        if not item or str(item).startswith("TODO") or item in seen:
            continue
        seen.add(item)
        cleaned.append(item)
    return cleaned


def condition_items_for_archetype(
    archetype: Optional[dict],
    *,
    relevant_items: Optional[set[str]] = None,
    hero: Optional[str] = None,
) -> list[str]:
    """Return clean condition-item metadata from the shared build JSON."""
    if relevant_items is None:
        _build_data, relevant_items = load_builds(hero)
    known_items = relevant_items or set()
    cleaned = []
    seen = set()
    for item in (archetype or {}).get("condition_items", []):
        if not isinstance(item, str) or not item or item.startswith("TODO"):
            continue
        if item not in known_items or item in seen:
            continue
        seen.add(item)
        cleaned.append(item)
    return cleaned


# ── Archetype scoring ─────────────────────────────────────────────────────────

def score_archetypes(
    owned_names: set[str],
    *,
    build_data: Optional[dict] = None,
    hero: Optional[str] = None,
    limit: Optional[int] = None,
) -> list[dict]:
    """Return archetype overlap scores for the current board.

    By default returns all archetypes sorted by score descending.
    Pass ``limit=3`` to reproduce the old top-3 behaviour explicitly.
    """
    if build_data is None:
        build_data, _relevant_items = load_builds(hero)
    if not scorer.has_build_catalog(build_data):
        return []

    results = []
    for phase in ["early_mid", "late"]:
        phase_data = (build_data or {}).get("game_phases", {}).get(phase, {})
        for arch in phase_data.get("archetypes", []):
            components = []
            matched_items = []
            for key, label in (
                ("core_items", "core"),
                ("carry_items", "carry"),
                ("support_items", "support"),
            ):
                items = clean_build_items(arch.get(key, []))
                if not items:
                    continue
                matches = [item for item in items if item in owned_names]
                ratio = len(matches) / len(items)
                components.append({
                    "label": label,
                    "weight": _BUILD_SCORE_WEIGHTS[key],
                    "matches": len(matches),
                    "total": len(items),
                    "ratio": ratio,
                })
                matched_items.extend(matches)

            if not components:
                continue
            active_weight = sum(c["weight"] for c in components)
            raw_score = (
                sum(c["weight"] * c["ratio"] for c in components) / active_weight
                if active_weight
                else 0.0
            )
            breakdown = {c["label"]: c for c in components}
            results.append({
                "name": arch["name"],
                "score": round(raw_score, 2),
                "phase": phase,
                "matches": len(set(matched_items)),
                "core_matches": breakdown.get("core", {}).get("matches", 0),
                "core_total": breakdown.get("core", {}).get("total", 0),
                "carry_matches": breakdown.get("carry", {}).get("matches", 0),
                "carry_total": breakdown.get("carry", {}).get("total", 0),
                "support_matches": breakdown.get("support", {}).get("matches", 0),
                "support_total": breakdown.get("support", {}).get("total", 0),
                "raw_score": raw_score,
            })

    ranked = sorted(
        results,
        key=lambda row: (
            row["raw_score"],
            row["core_matches"],
            row["carry_matches"],
            row["support_matches"],
            row["matches"],
        ),
        reverse=True,
    )
    return ranked[:limit] if limit is not None else ranked


def infer_archetype_from_decisions(
    conn,
    run_id: int,
    *,
    build_data: Optional[dict] = None,
    resolve_fn=None,
) -> tuple[Optional[str], Optional[int]]:
    """Fallback archetype detection when no explicit COMMITTED note was written.

    ``resolve_fn(conn, template_id) -> str`` should be the caller's template
    resolver (e.g. ``db_helpers.resolve``).  Passed explicitly to avoid a
    circular import with server.py.
    """
    if build_data is None:
        run = conn.execute("SELECT hero FROM runs WHERE id=?", (run_id,)).fetchone()
        build_data = scorer.load_builds(run["hero"] if run else None)
    if not scorer.has_build_catalog(build_data):
        return None, None

    decisions = conn.execute(
        """
        SELECT decision_seq, decision_type, chosen_id, chosen_template
        FROM decisions
        WHERE run_id=?
        ORDER BY decision_seq
        """,
        (run_id,),
    ).fetchall()

    board = {}
    for row in decisions:
        if row["decision_type"] not in ("item", "companion", "free_reward"):
            continue

        chosen_template = row["chosen_template"] or ""
        if resolve_fn and chosen_template:
            item_name = resolve_fn(conn, chosen_template)
        else:
            item_name = row["chosen_id"] or "Unknown"
        board[row["chosen_id"]] = item_name

        arch, _reason = scorer.find_committed_archetype(list(board.values()), build_data)
        if arch:
            return arch["name"], row["decision_seq"]

    return None, None


# ── Phase notes ───────────────────────────────────────────────────────────────

def get_phase_notes(
    day: Optional[int],
    *,
    build_data: Optional[dict] = None,
    hero: Optional[str] = None,
) -> dict:
    """Return phase guidance dict for the given day."""
    if build_data is None:
        build_data, _relevant_items = load_builds(hero)

    if day is None or day <= 4:
        phase_key = "early"
    elif day <= 7:
        phase_key = "early_mid"
    else:
        phase_key = "late"

    phase_data = (build_data or {}).get("game_phases", {}).get(phase_key, {})
    return {
        "phase": phase_key,
        "day_range": phase_data.get("day_range"),
        "description": phase_data.get("description"),
        "notes": phase_data.get("notes"),
        "economy_items": phase_data.get("economy_items", []),
        "universal_utility_items": phase_data.get("universal_utility_items", []),
    }


# ── Run-tier classification ───────────────────────────────────────────────────

def classify_run_tier(
    pvp_wins: int,
    pvp_losses: int,
    health: Optional[int] = None,
    health_max: Optional[int] = None,
) -> dict:
    wins = int(pvp_wins or 0)
    losses = int(pvp_losses or 0)
    has_full_prestige = (
        health is not None
        and health_max is not None
        and health_max > 0
        and health >= health_max
    )

    if wins >= 10 and losses == 0 and has_full_prestige:
        return {"label": "Perfect", "tone": "perfect", "detail": "10 wins without losing prestige."}
    if wins >= 10:
        return {"label": "Gold", "tone": "gold", "detail": "Run won."}
    if wins >= 7:
        return {"label": "Silver", "tone": "silver", "detail": "Above the ranked threshold."}
    if wins >= 4:
        return {"label": "Bronze", "tone": "bronze", "detail": "Reached the first ranked tier."}
    return {"label": "Unranked", "tone": "unranked", "detail": "Below the medal thresholds."}


# ── Insight extraction ────────────────────────────────────────────────────────

def build_run_summary(
    conn,
    run_id: int,
    *,
    resolve_fn=None,
) -> dict:
    """Assemble the full summary payload for /api/runs/<id>/summary.

    ``resolve_fn(conn, template_id) -> str`` injected to avoid circular import.
    """
    import json as _json

    run = conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
    if not run:
        return {"error": "Run not found"}
    run = dict(run)

    build_data, _relevant_items = load_builds(run.get("hero"))

    # PvP / PvE
    from web.overlay_state import _get_pvp_record, _get_pve_record, _get_run_end_snapshot
    pvp_w, pvp_l = _get_pvp_record(conn, run_id, run)
    pve_w, pve_l = _get_pve_record(conn, run_id)
    end_snap = _get_run_end_snapshot(conn, run)
    run_tier = classify_run_tier(
        pvp_w, pvp_l,
        end_snap.get("health") if end_snap else None,
        end_snap.get("health_max") if end_snap else None,
    )

    scores = conn.execute("""
        SELECT score_label, COUNT(*) as cnt
        FROM decisions WHERE run_id=? AND score_label IS NOT NULL
        GROUP BY score_label
    """, (run_id,)).fetchall()
    score_dist = {r["score_label"]: r["cnt"] for r in scores}

    dec_count = conn.execute(
        "SELECT COUNT(*) as cnt FROM decisions WHERE run_id=?", (run_id,)
    ).fetchone()["cnt"]

    # Archetype
    archetype = archetype_committed_at = None
    arch_rows = conn.execute("""
        SELECT decision_seq, score_notes FROM decisions
        WHERE run_id=? AND score_notes LIKE '%COMMITTED%'
        ORDER BY decision_seq
    """, (run_id,)).fetchall()
    if arch_rows:
        m = re.search(r'COMMITTED to ([\w\s\-]+?)(?:\s*\(|\.)', arch_rows[0]["score_notes"])
        if m:
            candidate = m.group(1).strip()
            placeholder_lowers = {"", "none", "null", "unknown", "no archetype fit", "no fit"}
            if candidate and candidate.lower() not in placeholder_lowers:
                archetype = candidate
                archetype_committed_at = arch_rows[0]["decision_seq"]
    if not archetype:
        inferred_name, inferred_seq = infer_archetype_from_decisions(
            conn, run_id, build_data=build_data, resolve_fn=resolve_fn,
        )
        if inferred_name:
            s = str(inferred_name).strip()
            placeholder_lowers = {"", "none", "null", "unknown", "no archetype fit", "no fit"}
            if s and s.lower() not in placeholder_lowers:
                archetype = s
                archetype_committed_at = inferred_seq

    flagged = conn.execute("""
        SELECT COUNT(*) as cnt FROM decisions
        WHERE run_id=?
          AND (
               score_label IN ('suboptimal', 'warning')
               OR (decision_type='skip' AND score_notes LIKE '%missed:%')
          )
    """, (run_id,)).fetchone()["cnt"]

    insight_rows = conn.execute("""
        SELECT decision_type, score_label, score_notes, chosen_template, chosen_id
        FROM decisions WHERE run_id=?
        ORDER BY decision_seq
    """, (run_id,)).fetchall()
    all_insights = []
    for row in insight_rows:
        row_dict = dict(row)
        notes = row_dict.get("score_notes") or ""
        dtype = row_dict.get("decision_type") or ""
        try:
            parsed = _json.loads(notes)
            if isinstance(parsed, dict):
                notes = ""
        except (_json.JSONDecodeError, TypeError):
            pass
        chosen_template = row_dict.get("chosen_template") or ""
        item_name = (
            resolve_fn(conn, chosen_template) if (resolve_fn and chosen_template)
            else (row_dict.get("chosen_id") or "Unknown")
        )
        row_insights = extract_insights(
            notes, row_dict.get("score_label") or "", dtype, item_name, []
        )
        if dtype == "skip":
            skip_items = extract_skip_relevant_items(notes)
            if skip_items:
                row_insights.append({
                    "type": "missed_in_skip",
                    "severity": "high",
                    "message": f"Skipped build-relevant: {', '.join(skip_items)}",
                })
        all_insights.extend(row_insights)

    seen: set = set()
    deduped_insights = []
    for ins in all_insights:
        key = (ins.get("severity"), ins.get("message", "")[:60])
        if key not in seen:
            seen.add(key)
            deduped_insights.append(ins)

    return {
        "hero": run["hero"], "outcome": run["outcome"],
        "pvp_wins": pvp_w, "pvp_losses": pvp_l,
        "pve_wins": pve_w, "pve_losses": pve_l,
        "decision_count": dec_count, "scores": score_dist,
        "run_tier": run_tier,
        "archetype": archetype, "archetype_committed_at": archetype_committed_at,
        "flagged_count": flagged,
        "insights": deduped_insights,
    }


def extract_insights(notes: str, label: str, dtype: str, item_name: str, rejected_resolved: list) -> list[dict]:
    """Parse structured insight signals from scorer's score_notes."""
    insights = []
    if not notes:
        return insights

    m = re.search(r'Missed alternatives?:\s*(.+?)(?:\.\s*|$)', notes)
    if m:
        insights.append({
            "type": "missed_enabler",
            "severity": "high",
            "message": f"Build-relevant items passed on: {m.group(1).strip()}",
        })

    m = re.search(r'Board converging on ([\w\s\-]+?)(?:\s*\()', notes)
    if m:
        insights.append({
            "type": "convergence",
            "severity": "info",
            "message": f"Board converging → {m.group(1).strip()}",
        })

    m = re.search(r'COMMITTED to ([\w\s\-]+?)(?:\s*\()', notes)
    if m:
        insights.append({
            "type": "committed",
            "severity": "milestone",
            "message": f"Build locked: {m.group(1).strip()}",
        })

    m = re.search(r'Still need carry:\s*\[([^\]]+)\]', notes)
    if m:
        insights.append({
            "type": "missing_carry",
            "severity": "medium",
            "message": f"Still need carry: {m.group(1).strip()}",
        })

    if "Doesn't fit committed build" in notes or "Likely wasted pick" in notes:
        insights.append({
            "type": "off_build",
            "severity": "high",
            "message": "Off-build pick — doesn't fit committed archetype",
        })
    elif "Doesn't fit any" in notes and label in ("suboptimal",):
        insights.append({
            "type": "no_archetype_fit",
            "severity": "medium",
            "message": "No archetype fit",
        })

    if "Consider pivoting" in notes:
        insights.append({
            "type": "pivot_signal",
            "severity": "medium",
            "message": "Fits a different archetype — consider pivoting or selling",
        })

    return insights


def extract_skip_relevant_items(notes: str) -> list[str]:
    """Mirror scorer skip warnings — extract missed item names from score_notes."""
    import ast
    if not notes or "missed:" not in notes:
        return []

    items = []
    for match in re.finditer(r"(\[[^\]]*\])", notes):
        try:
            parsed = ast.literal_eval(match.group(1))
        except (SyntaxError, ValueError):
            continue
        if not isinstance(parsed, list):
            continue
        for item in parsed:
            if isinstance(item, str) and item not in items:
                items.append(item)
    return items


def extract_list_items_from_notes(notes: str, marker: str) -> list[str]:
    """Extract a list literal following ``marker`` from score_notes."""
    import ast
    if not notes or marker not in notes:
        return []

    items = []
    pattern = rf"{re.escape(marker)}\s*(\[[^\]]*\])"
    for match in re.finditer(pattern, notes):
        try:
            parsed = ast.literal_eval(match.group(1))
        except (SyntaxError, ValueError):
            continue
        if not isinstance(parsed, list):
            continue
        for item in parsed:
            if isinstance(item, str) and item not in items:
                items.append(item)
    return items