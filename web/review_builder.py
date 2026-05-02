"""
web/review_builder.py — Overlay review row assembly for the Bazaar Tracker.

Builds the list of decision rows shown in the overlay's Review tab.

Primary path: structure-aware matching (item → archetype role) via
``_select_overlay_review_entry``.

Fallback path: score-label-based row via ``_fallback_overlay_review_entry``,
used when no archetype match was found and ``prefer_scored_fallback=True``.
"""

from typing import Optional

import scorer
from name_resolver import is_unresolved, make_resolver
from web.build_helpers import (
    load_builds,
    extract_skip_relevant_items,
    extract_list_items_from_notes,
)


# ── Name resolution helpers ───────────────────────────────────────────────────

def _resolve_readable_names(names: list[str]) -> list[str]:
    """Filter a list of names to only human-readable entries (not raw IDs)."""
    resolver = make_resolver()
    return resolver.get_readable_names(names)


def _normalize_review_label(decision: dict) -> str:
    """Collapse scorer/live labels into the overlay's review badge taxonomy."""
    label = (decision.get("score_label") or "").strip().lower()
    dtype = decision.get("decision_type") or ""
    notes = decision.get("score_notes") or ""

    if dtype == "skip" and "missed:" in notes.lower():
        return "missed"

    if label == "warning":
        label = "suboptimal"
    if label == "info":
        return ""

    if label in ("optimal", "good", "situational", "suboptimal", "missed"):
        return label
    return ""


def _fallback_review_title(decision: dict) -> Optional[str]:
    dtype = decision.get("decision_type") or ""
    notes = decision.get("score_notes") or ""

    if dtype == "skip":
        missed_items = decision.get("resolved_offered") or extract_skip_relevant_items(notes)
        return missed_items[0] if missed_items else None

    if decision.get("chosen_name"):
        return decision["chosen_name"]

    rejected = decision.get("resolved_rejected") or extract_list_items_from_notes(notes, "Passed on:")
    return rejected[0] if rejected else None


def _fallback_overlay_review_entry(decision: dict) -> Optional[dict]:
    """Fallback review row when structure-based review matching yields nothing."""
    dtype = decision.get("decision_type") or ""
    if dtype in ("skill", "free_reward", "event_choice"):
        return None

    offered_raw = decision.get("offered_raw") or []
    if dtype in ("item", "companion") and scorer._is_event_or_loot_purchase(decision, offered_raw):
        return None

    label = _normalize_review_label(decision)
    title = _fallback_review_title(decision)
    if not title:
        return None

    if not label:
        if dtype not in ("item", "companion"):
            return None
        label = "unscored"

    notes = (decision.get("score_notes") or "").strip() or None
    return {
        "decision_seq": decision["decision_seq"],
        "decision_type": dtype,
        "chosen_name": decision.get("chosen_name"),
        "review_title": title,
        "review_detail": notes,
        "derived_score_label": label,
    }


# ── Archetype-aware review matching ──────────────────────────────────────────

def _review_late_archetypes(
    *,
    build_data: Optional[dict] = None,
    hero: Optional[str] = None,
) -> list[dict]:
    if build_data is None:
        build_data, _relevant_items = load_builds(hero)
    return (build_data or {}).get("game_phases", {}).get("late", {}).get("archetypes", [])


def _review_archetype_state(overlap: dict) -> str:
    core_needed = scorer._majority_count(len(overlap["core"]))
    enabled = bool(overlap["core"]) and len(overlap["core_hits"]) >= core_needed
    if not enabled:
        return "nothing"
    if overlap["carry_hits"]:
        return "carried"
    return "enabled"


def _enabled_review_archetypes(
    board_names: list[str],
    late_archetypes: list[dict],
) -> list[dict]:
    ranked = []
    for arch in late_archetypes:
        overlap = scorer._get_archetype_overlap(arch, board_names)
        review_state = _review_archetype_state(overlap)
        if review_state == "nothing":
            continue
        ranked.append((
            (
                {"enabled": 1, "carried": 2}.get(review_state, 0),
                len(overlap["core_hits"]),
                len(overlap["carry_hits"]),
                len(overlap["support_hits"]),
                overlap["total_hits"],
            ),
            arch,
        ))
    ranked.sort(key=lambda entry: entry[0], reverse=True)
    return [arch for _rank, arch in ranked]


def _match_review_item_to_arch(
    item_name: Optional[str],
    board_names: list[str],
    arch: dict,
) -> Optional[dict]:
    if not item_name or is_unresolved(item_name):
        return None

    core_items = scorer._clean_archetype_items(arch, "core_items")
    carry_items = scorer._clean_archetype_items(arch, "carry_items")
    support_items = scorer._clean_archetype_items(arch, "support_items")

    bucket = None
    if item_name in core_items:
        bucket = "core"
    elif item_name in carry_items:
        bucket = "carry"
    elif item_name in support_items:
        bucket = "support"
    if bucket is None:
        return None

    board_after = list(board_names or []) + [item_name]
    overlap_before = scorer._get_archetype_overlap(arch, board_names)
    overlap_after = scorer._get_archetype_overlap(arch, board_after)
    committed_after, _ = scorer.check_commit_threshold(arch, board_after)
    state_before = _review_archetype_state(overlap_before)
    state_after = _review_archetype_state(overlap_after)

    core_gain = len(overlap_after["core_hits"]) - len(overlap_before["core_hits"])
    carry_gain = len(overlap_after["carry_hits"]) - len(overlap_before["carry_hits"])
    support_gain = len(overlap_after["support_hits"]) - len(overlap_before["support_hits"])
    total_gain = overlap_after["total_hits"] - overlap_before["total_hits"]

    if state_before == "nothing":
        if bucket != "core":
            return None
        if state_after == "nothing" or core_gain <= 0:
            return None
        kind = "enable"
    elif state_before == "enabled":
        if bucket != "carry" or carry_gain <= 0:
            return None
        kind = "carry"
    else:
        if bucket != "support" or support_gain <= 0:
            return None
        kind = "support"

    bucket_rank = {"core": 3, "carry": 2, "support": 1}[bucket]
    kind_rank = {"enable": 1, "carry": 2, "support": 3}[kind]

    return {
        "item_name": item_name,
        "arch_name": arch["name"],
        "bucket": bucket,
        "kind": kind,
        "rank": (
            kind_rank,
            bucket_rank,
            1 if committed_after else 0,
            core_gain,
            carry_gain,
            support_gain,
            total_gain,
            len(overlap_after["core_hits"]),
            len(overlap_after["carry_hits"]),
            overlap_after["total_hits"],
        ),
    }


def _pick_best_review_match(
    item_names: list[str],
    board_names: list[str],
    archetypes: list[dict],
) -> Optional[dict]:
    best = None
    for item_name in item_names or []:
        for arch in archetypes or []:
            candidate = _match_review_item_to_arch(item_name, board_names, arch)
            if candidate and (best is None or candidate["rank"] > best["rank"]):
                best = candidate
    return best


def _review_detail_for_match(match: dict, *, missed: bool) -> str:
    arch_name = match["arch_name"]
    if missed:
        if match["kind"] == "enable":
            return f"Would enable {arch_name}."
        if match["kind"] == "carry":
            return f"Would add a carry to {arch_name}."
        return f"Would add support to {arch_name}."
    if match["kind"] == "enable":
        return f"Enabled {arch_name}."
    if match["kind"] == "carry":
        return f"Added a carry to {arch_name}."
    return f"Added support to {arch_name}."



def _emit_shop_visit_missed_entry(
    anchor_decision: dict,
    leftover_names: list[str],
    board_names: list[str],
    committed_arch: Optional[dict],
    late_archetypes: list[dict],
    build_data: Optional[dict] = None,
) -> Optional[dict]:
    """Return one 'missed' review row for a shop-close, or None."""
    focused_archetypes = [committed_arch] if committed_arch else _enabled_review_archetypes(
        board_names, late_archetypes,
    )
    missed_match = None
    if focused_archetypes:
        missed_match = _pick_best_review_match(leftover_names, board_names, focused_archetypes)
        if missed_match is None:
            missed_match = _pick_best_review_match(leftover_names, board_names, late_archetypes)
    else:
        missed_match = _pick_best_review_match(leftover_names, board_names, late_archetypes)

    dtype = anchor_decision.get("decision_type") or ""

    if missed_match:
        return {
            "decision_seq": anchor_decision["decision_seq"],
            "decision_type": dtype,
            "chosen_name": anchor_decision.get("chosen_name"),
            "review_title": missed_match["item_name"],
            "review_detail": _review_detail_for_match(missed_match, missed=True),
            "review_build_name": missed_match["arch_name"],
            "review_kind": missed_match["kind"],
            "derived_score_label": "missed",
        }

    # Fallback: economy/utility items that don't belong to a scoreable archetype yet.
    economy_names: set[str] = set()
    utility_names: set[str] = set()
    for phase_data in (build_data or {}).get("game_phases", {}).values():
        economy_names.update(phase_data.get("economy_items", []))
        utility_names.update(phase_data.get("universal_utility_items", []))

    for n in leftover_names:
        if n in economy_names:
            detail = "Economy item — strong pickup regardless of archetype."
            kind = "economy"
        elif n in utility_names:
            detail = "Universal utility — strong pickup regardless of archetype."
            kind = "utility"
        else:
            continue
        return {
            "decision_seq": anchor_decision["decision_seq"],
            "decision_type": dtype,
            "chosen_name": anchor_decision.get("chosen_name"),
            "review_title": n,
            "review_detail": detail,
            "review_build_name": None,
            "review_kind": kind,
            "derived_score_label": "missed",
        }

    return None


def _select_overlay_review_entry(
    decision: dict,
    board_names: list[str],
    committed_arch: Optional[dict],
    late_archetypes: list[dict],
) -> Optional[dict]:
    dtype = decision.get("decision_type") or ""
    if dtype in ("skill", "free_reward", "event_choice", "skip"):
        return None

    offered_raw = decision.get("offered_raw") or []
    if dtype in ("item", "companion") and scorer._is_event_or_loot_purchase(decision, offered_raw):
        return None

    focused_archetypes = [committed_arch] if committed_arch else _enabled_review_archetypes(
        board_names, late_archetypes,
    )
    if not focused_archetypes:
        return None

    chosen_match = None
    if decision.get("chosen_name"):
        chosen_match = _pick_best_review_match(
            [decision["chosen_name"]], board_names, focused_archetypes,
        )

    if not chosen_match:
        return None

    return {
        "decision_seq": decision["decision_seq"],
        "decision_type": dtype,
        "chosen_name": decision.get("chosen_name"),
        "review_title": decision.get("chosen_name") or chosen_match["item_name"],
        "review_detail": _review_detail_for_match(chosen_match, missed=False),
        "review_build_name": chosen_match["arch_name"],
        "review_kind": chosen_match["kind"],
        "derived_score_label": "optimal" if chosen_match["kind"] in ("enable", "carry") else "good",
    }


# ── Name resolution for overlay decisions ────────────────────────────────────

def resolve_overlay_decision_names(conn, decision: dict, *, resolve_fn, safe_json_fn) -> dict:
    """Resolve chosen/offered/rejected names for overlay review rows.

    ``resolve_fn(conn, template_id) -> str`` and
    ``safe_json_fn(raw) -> list|dict`` are injected by the caller to avoid
    circular imports.
    """
    resolver = make_resolver()
    row = dict(decision)
    notes = row.get("score_notes") or ""

    chosen_template = row.get("chosen_template") or ""
    if not chosen_template:
        offered_templates = safe_json_fn(row.get("offered_templates"))
        if isinstance(offered_templates, dict):
            chosen_template = offered_templates.get(row.get("chosen_id"), "") or ""

    # ── Chosen name ──────────────────────────────────────────────────────
    chosen_name = None
    if chosen_template:
        chosen_name = resolve_fn(conn, chosen_template)
        if is_unresolved(chosen_name):
            chosen_name = None
    elif row.get("chosen_id"):
        resolved = resolver.resolve(row["chosen_id"])
        chosen_name = resolved if not is_unresolved(resolved) else None

    # ── Offered names ────────────────────────────────────────────────────
    offered_raw = safe_json_fn(row.get("offered"))
    offered_names = safe_json_fn(row.get("offered_names"))

    if not offered_names or all(is_unresolved(name) for name in offered_names):
        resolved_map = resolver.bulk_resolve(offered_raw)
        if any(not is_unresolved(v) for v in resolved_map.values()):
            offered_names = [resolved_map.get(oid, oid) for oid in offered_raw]

    if row.get("decision_type") == "skip" and not _resolve_readable_names(offered_names):
        note_offered = extract_skip_relevant_items(notes)
        if note_offered:
            offered_names = note_offered

    if not offered_names:
        offered_names = []
        for oid in offered_raw:
            if oid == row.get("chosen_id") and chosen_template:
                offered_names.append(resolve_fn(conn, chosen_template))
            else:
                offered_names.append(oid)

    # ── Rejected names ───────────────────────────────────────────────────
    rejected_raw = safe_json_fn(row.get("rejected"))
    rejected_names = []
    for rejected_id in rejected_raw:
        matched = False
        for raw_id, name in zip(offered_raw, offered_names):
            if raw_id == rejected_id:
                rejected_names.append(name)
                matched = True
                break
        if not matched:
            rejected_names.append(rejected_id)

    still_raw = [name for name in rejected_names if is_unresolved(name)]
    if still_raw:
        resolved_map = resolver.bulk_resolve(still_raw)
        rejected_names = [
            resolved_map.get(name, name) if is_unresolved(name) else name
            for name in rejected_names
        ]

    still_raw_after = [name for name in rejected_names if is_unresolved(name)]
    if still_raw_after:
        note_rejected = extract_list_items_from_notes(notes, "Passed on:")
        if note_rejected:
            note_iter = iter(note_rejected)
            patched = []
            for name in rejected_names:
                if is_unresolved(name):
                    replacement = next(note_iter, None)
                    patched.append(replacement if replacement else name)
                else:
                    patched.append(name)
            rejected_names = patched
    elif not _resolve_readable_names(rejected_names):
        note_rejected = extract_list_items_from_notes(notes, "Passed on:")
        if note_rejected:
            rejected_names = note_rejected

    return {
        "chosen_template": chosen_template,
        "chosen_name": chosen_name,
        "offered_raw": offered_raw,
        "offered_names": offered_names,
        "rejected_names": rejected_names,
        "resolved_offered": _resolve_readable_names(offered_names),
        "resolved_rejected": _resolve_readable_names(rejected_names),
    }


# ── Review row list assembly ──────────────────────────────────────────────────

def build_overlay_review_rows(
    conn,
    run_id: int,
    decisions=None,
    *,
    build_data: Optional[dict] = None,
    hero: Optional[str] = None,
    prefer_scored_fallback: bool = False,
    resolve_fn=None,
    safe_json_fn=None,
    lookup_image_by_name_fn=None,
) -> list[dict]:
    """Assemble the ordered list of review rows for the overlay's Review tab.

    ``resolve_fn`` and ``safe_json_fn`` are injected by the caller (server.py)
    to avoid a circular import.
    """
    if decisions is None:
        decisions = conn.execute(
            """
            SELECT id, decision_seq, decision_type, game_state, board_section,
                   chosen_id, chosen_template, offered, offered_names, rejected,
                   score_label, score_notes
            FROM decisions
            WHERE run_id=?
            ORDER BY decision_seq
            """,
            (run_id,),
        ).fetchall()

    if build_data is None:
        build_data, _relevant_items = load_builds(hero)
    late_archetypes = _review_late_archetypes(build_data=build_data)
    board_snapshots = scorer._load_board_snapshot_map(conn, run_id)
    board = {}
    committed_arch = None
    review_rows = []
    resolved_rows = []
    shop_buffer: list[tuple[dict, list[str]]] = []
    shop_rejected_key: Optional[str] = None

    def _attach_image(entry: Optional[dict]) -> Optional[dict]:
        if entry is None:
            return None
        if lookup_image_by_name_fn:
            entry["image"] = lookup_image_by_name_fn(entry.get("chosen_name") or "")
        else:
            entry["image"] = None
        return entry

    def _flush_buffer() -> None:
        nonlocal shop_buffer, shop_rejected_key
        if not shop_buffer:
            return
        anchor_row, anchor_board_names = shop_buffer[-1]
        leftover_names = anchor_row.get("resolved_rejected") or []
        if leftover_names:
            missed_entry = _emit_shop_visit_missed_entry(
                anchor_row, leftover_names, anchor_board_names, committed_arch, late_archetypes,
                build_data=build_data,
            )
            if missed_entry:
                review_rows.append(_attach_image(missed_entry))
        shop_buffer = []
        shop_rejected_key = None

    for decision in decisions:
        row = dict(decision)
        row.update(resolve_overlay_decision_names(
            conn, row,
            resolve_fn=resolve_fn,
            safe_json_fn=safe_json_fn,
        ))
        resolved_rows.append(dict(row))

        snapshot_board = board_snapshots.get(row["id"])
        if snapshot_board is not None:
            board = dict(snapshot_board)

        board_names = _resolve_readable_names(list(board.values()))
        if committed_arch is None:
            committed_arch, _ = scorer.find_committed_archetype(board_names, build_data)

        dtype = row.get("decision_type") or ""
        current_rejected_key = row.get("rejected") if dtype == "item" else None
        if dtype != "item" or current_rejected_key != shop_rejected_key:
            _flush_buffer()
        if dtype == "item":
            shop_rejected_key = current_rejected_key

        review_entry = _select_overlay_review_entry(
            row, board_names, committed_arch, late_archetypes,
        )
        if review_entry:
            review_rows.append(_attach_image(review_entry))
        elif prefer_scored_fallback:
            fallback_entry = _fallback_overlay_review_entry(row)
            if fallback_entry:
                review_rows.append(_attach_image(fallback_entry))

        if dtype == "item":
            shop_buffer.append((row, list(board_names)))
        elif dtype == "skip":
            leftover_names = row.get("resolved_offered") or []
            if leftover_names:
                missed_entry = _emit_shop_visit_missed_entry(
                    row, leftover_names, board_names, committed_arch, late_archetypes,
                    build_data=build_data,
                )
                if missed_entry:
                    review_rows.append(_attach_image(missed_entry))

        if row.get("decision_type") in ("item", "companion") and row.get("chosen_id"):
            board[row["chosen_id"]] = row.get("chosen_name") or row["chosen_id"]
        elif (
            row.get("decision_type") == "free_reward"
            and row.get("board_section") == "Player"
            and row.get("chosen_id")
            and row.get("chosen_template")
        ):
            board[row["chosen_id"]] = row.get("chosen_name") or row["chosen_id"]

        if committed_arch is None:
            updated_board_names = _resolve_readable_names(list(board.values()))
            committed_arch, _ = scorer.find_committed_archetype(updated_board_names, build_data)

    _flush_buffer()

    # Suppress missed rows for items the player acquired later in the same run.
    # Covers the case where an item was passed on in a shop but then received as
    # a free reward (or bought from a subsequent shop) on a later decision.
    acquired_names = {
        row.get("chosen_name")
        for row in resolved_rows
        if row.get("chosen_name")
    }
    review_rows = [
        r for r in review_rows
        if not (
            r.get("derived_score_label") == "missed"
            and r.get("review_title") in acquired_names
        )
    ]

    if review_rows:
        return list(reversed(review_rows))

    fallback_rows = []
    for row in resolved_rows:
        fallback_entry = _fallback_overlay_review_entry(row)
        if fallback_entry:
            fallback_rows.append(_attach_image(fallback_entry))

    return list(reversed(fallback_rows))


# ── Dashboard decision formatter ─────────────────────────────────────────────

def format_decision_row(
    d: dict,
    *,
    resolve_fn,
    get_tier_fn,
    safe_json_fn,
    resolve_instance_ids_fn,
    is_unresolved_fn,
    resolve_image_fn=None,
) -> dict:
    """Format one decisions-table row for /api/runs/<id>/decisions.

    All DB lookups are performed via injected callables to avoid circular
    imports with server.py.
    """
    import json as _json

    dtype = d["decision_type"]
    chosen_template = d.get("chosen_template") or ""
    offered_templates = safe_json_fn(d.get("offered_templates"))
    if not isinstance(offered_templates, dict):
        offered_templates = {}
    if not chosen_template:
        chosen_template = offered_templates.get(d.get("chosen_id"), "") or ""

    item_name = "Unknown"
    if chosen_template:
        item_name = resolve_fn(chosen_template)
    elif d.get("chosen_id"):
        item_name = d["chosen_id"]
    if dtype == "skip":
        item_name = "(skipped shop)"

    offered_raw = safe_json_fn(d.get("offered"))
    offered_names = safe_json_fn(d.get("offered_names"))

    if resolve_image_fn:
        offered_images = [
            resolve_image_fn(offered_templates.get(oid, "")) for oid in offered_raw
        ]
    else:
        offered_images = [None] * len(offered_raw)

    chosen_image = (
        resolve_image_fn(chosen_template)
        if (resolve_image_fn and chosen_template)
        else None
    )

    if not offered_names or all(is_unresolved_fn(n) for n in offered_names):
        api_map = resolve_instance_ids_fn(offered_raw)
        if api_map:
            offered_names = [api_map.get(oid, oid) for oid in offered_raw]
        else:
            try:
                parsed = _json.loads(d.get("score_notes") or "{}")
                if isinstance(parsed, dict) and parsed.get("resolved_names"):
                    offered_names = parsed["resolved_names"]
            except (_json.JSONDecodeError, TypeError):
                pass

    if not offered_names:
        offered_names = []
        for oid in offered_raw:
            if oid == d.get("chosen_id") and chosen_template:
                offered_names.append(resolve_fn(chosen_template))
            else:
                offered_names.append(oid)

    rejected_raw = safe_json_fn(d.get("rejected"))
    rejected_names = []
    for rid_val in rejected_raw:
        found = False
        for raw_id, name in zip(offered_raw, offered_names):
            if raw_id == rid_val:
                rejected_names.append(name)
                found = True
                break
        if not found:
            rejected_names.append(rid_val)

    phase = d.get("phase_actual")
    if not phase:
        day = d.get("day")
        if day:
            phase = "early" if day <= 4 else "early_mid" if day <= 7 else "late"
        else:
            seq = d.get("decision_seq", 0)
            phase = "early" if seq <= 6 else "early_mid" if seq <= 14 else "late"

    notes = d.get("score_notes") or ""
    try:
        parsed = _json.loads(notes)
        if isinstance(parsed, dict):
            notes = ""
    except (_json.JSONDecodeError, TypeError):
        pass

    if not notes:
        if dtype == "skill":
            notes = "Skill choice tracked; scoring is not implemented yet."
        elif dtype == "free_reward":
            notes = "Reward choice tracked."
        elif dtype == "event_choice":
            notes = "Event choice tracked."
        elif dtype == "skip":
            notes = "Skipped shop."
        elif dtype in ("item", "companion"):
            notes = (
                "Tracked purchase awaiting name resolution and scoring."
                if is_unresolved_fn(item_name)
                else "Tracked purchase awaiting scoring."
            )

    skip_relevant_items = extract_skip_relevant_items(notes) if dtype == "skip" else []
    skip_has_relevant = bool(skip_relevant_items)

    from build_helpers import extract_insights
    insights = extract_insights(
        notes, d.get("score_label", ""), dtype, item_name,
        [n for n in rejected_names if not is_unresolved_fn(n)],
    )
    if dtype == "skip" and skip_has_relevant:
        insights.append({
            "type": "missed_in_skip",
            "severity": "high",
            "message": f"Skipped build-relevant: {', '.join(skip_relevant_items)}",
        })

    seen: set = set()
    deduped = []
    for ins in insights:
        key = (ins.get("severity"), ins.get("message", "")[:60])
        if key not in seen:
            seen.add(key)
            deduped.append(ins)

    return {
        "seq": d["decision_seq"], "decision_type": dtype,
        "item_name": item_name, "phase": phase,
        "day": d.get("day"), "gold": d.get("gold"),
        "health": d.get("health"),
        "chosen_template": chosen_template,
        "chosen_image": chosen_image,
        "score_label": None if dtype == "skill" else d.get("score_label"),
        "score_notes": notes,
        "offered": offered_names, "rejected": rejected_names,
        "offered_images": offered_images,
        "offered_count": len(offered_raw),
        "rejected_count": len(rejected_raw),
        "game_state": d.get("game_state"),
        "tier": get_tier_fn(chosen_template),
        "insights": deduped,
        "skip_has_relevant": skip_has_relevant,
        "skip_relevant_items": skip_relevant_items,
    }


def summarize_overlay_review_rows(decision_rows: list[dict]) -> dict:
    summary = {
        "optimal": 0, "good": 0, "situational": 0,
        "suboptimal": 0, "missed": 0, "unscored": 0,
    }
    for row in decision_rows:
        label = (row.get("derived_score_label") or row.get("score_label") or "").strip().lower()
        if label == "warning":
            label = "suboptimal"
        if label in summary:
            summary[label] += 1
    return summary