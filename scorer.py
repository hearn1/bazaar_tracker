"""
scorer.py — Live scoring engine and manual report tooling for Bazaar tracker.

LiveScorer evaluates each decision against the active hero's build catalog as
RunState records it:
  1. Determines game phase at time of decision (early / early_mid / late)
  2. Identifies which archetypes were viable given the board state
  3. Detects when a build was committed to
  4. Scores each decision: optimal / good / situational / suboptimal
  5. Writes score_label + score_notes back to the decisions table immediately

Usage:
    python scorer.py                    # print manual report for most recent run
    python scorer.py --run-id 3         # print manual report for a specific run
"""

import json
import sqlite3
import argparse
import re
from collections import Counter
from functools import lru_cache
from pathlib import Path
from typing import Optional

import app_paths
import db
import card_cache
from board_state import BoardState

BUILD_GUIDE_DIR = app_paths.bundled_root()
DEFAULT_HERO = "Karnok"
CATALOG_FILENAMES = {
    "dooley": "dooley_builds.json",
    "karnok": "karnok_builds.json",
    "mak": "mak_builds.json",
    "pygmalien": "pygmalien_builds.json",
    "vanessa": "vanessa_builds.json",
}

SIZE_SLOTS = {"Small": 1, "Medium": 2, "Large": 3}

# ── Timing profile schema ──────────────────────────────────────────────────
# Each late (and optionally early_mid) archetype declares ONE timing_profile
# value describing when its payoff comes online relative to the run timeline.
#   tempo   — peaks early; loses value as the run drags on (e.g. Sustain, Sled)
#   scaling — ramps with the run; wants the back half (e.g. Slow-Ammo, Burn)
#   exodia  — only "online" when the full piece set is assembled (Calc/Retort,
#             Max-HP-Sigil, Satchel-Regen)
#   neutral — payoff is roughly flat across the run
VALID_TIMING_PROFILES = ("tempo", "scaling", "exodia", "neutral")

# Linear weight curves keyed by profile.  ``progress`` is in [0.0, 1.0] where
# 0.0 = Day 1 / very early, 1.0 = Day 13+ / very late.  weight = base + slope *
# progress.  These multiply into the existing rank tuple in
# _rank_late_item_matches and into the threshold comparisons in
# score_early_mid_decision / score_late_decision.
#
# Anchors (computed by hand to match the live numbers in scorer.py before
# this refactor):
#   tempo:   1.20 @ progress=0.0  →  0.85 @ progress=1.0
#   scaling: 0.82 @ progress=0.0  →  1.15 @ progress=1.0
#   exodia:  0.72 @ progress=0.0  →  1.20 @ progress=1.0
#   neutral: 1.00 flat
TIMING_PROFILE_CURVES = {
    "tempo":   {"base": 1.20, "slope": -0.35},
    "scaling": {"base": 0.82, "slope":  0.33},
    "exodia":  {"base": 0.72, "slope":  0.48},
    "neutral": {"base": 1.00, "slope":  0.00},
}

# Weight-band thresholds the score_*_decision functions use to pick a
# label/notes pair.  Centralising these means tweaking the curves never
# requires editing copy in scorer.py.
TIMING_WEIGHT_HIGH = 1.08   # >= this → "lines up with current stage"
TIMING_WEIGHT_LOW  = 0.90   # <= this → "ramps later" / "would be stronger later"

# Day thresholds for phase detection (approximate — log doesn't have day numbers
# directly, so we infer from decision count and combat count)
PHASE_EARLY_MAX_DECISIONS = 6      # first ~6 picks = early game
PHASE_EARLY_MID_MAX_DECISIONS = 25  # decisions 7-25 = early_mid when no day data


def normalize_hero_name(hero: Optional[str]) -> str:
    return (hero or "").strip()


def _hero_catalog_filename(hero: str) -> str:
    alias = CATALOG_FILENAMES.get(hero.casefold())
    if alias:
        return alias
    slug = re.sub(r"[^a-z0-9]+", "_", hero.casefold()).strip("_")
    if not slug:
        return CATALOG_FILENAMES[DEFAULT_HERO.casefold()]
    return f"{slug}_builds.json"


def _builds_path(hero: Optional[str] = None) -> Path:
    hero_name = normalize_hero_name(hero) or DEFAULT_HERO
    return BUILD_GUIDE_DIR / _hero_catalog_filename(hero_name)


def _empty_builds(hero: str) -> dict:
    return {
        "hero": hero,
        "season": None,
        "last_updated": None,
        "notes": f"Build catalog not available yet for {hero}.",
        "item_tier_list": {},
        "pivot_signals": {"signals": []},
        "scoring_weights": {"core": 0.50, "carry": 0.35, "support": 0.15},
        "game_phases": {
            "early": {
                "day_range": "Days 1-4",
                "description": "No guide catalog available yet.",
                "notes": "Build-aware coaching is unavailable for this hero.",
                "universal_utility_items": [],
                "economy_items": [],
            },
            "early_mid": {
                "day_range": "Days 5-7",
                "description": "No guide catalog available yet.",
                "notes": "Build-aware coaching is unavailable for this hero.",
                "archetypes": [],
            },
            "late": {
                "day_range": "Day 8+",
                "description": "No guide catalog available yet.",
                "notes": "Build-aware coaching is unavailable for this hero.",
                "archetypes": [],
            },
        },
    }


@lru_cache(maxsize=None)
def _load_builds_cached(hero_name: str) -> dict:
    path = _builds_path(hero_name)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return _empty_builds(hero_name)


def load_builds(hero: Optional[str] = None) -> dict:
    hero_name = normalize_hero_name(hero) or DEFAULT_HERO
    return _load_builds_cached(hero_name)


def has_build_catalog(builds: dict) -> bool:
    for tier, items in builds.get("item_tier_list", {}).items():
        if tier == "description":
            continue
        if isinstance(items, list) and items:
            return True

    for phase_name in ("early", "early_mid", "late"):
        phase = builds.get("game_phases", {}).get(phase_name, {})
        if phase.get("universal_utility_items") or phase.get("economy_items"):
            return True
        if phase.get("archetypes"):
            return True
    return False


def get_item_tier(builds: dict, item_name: str) -> Optional[str]:
    """Return S/A/B/C/D/F tier for an item, or None if not in tier list."""
    for tier, items in builds["item_tier_list"].items():
        if item_name in items:
            return tier
    return None


def _load_json_list(raw_value: str) -> list:
    """Parse a JSON list field defensively."""
    if not raw_value:
        return []
    try:
        value = json.loads(raw_value)
        return value if isinstance(value, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


def _load_json_dict(raw_value: str) -> dict:
    """Parse a JSON object field defensively."""
    if not raw_value:
        return {}
    try:
        value = json.loads(raw_value)
        return value if isinstance(value, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def _filter_resolved_names(names: list) -> list[str]:
    """Keep human-readable names and drop unresolved ids/placeholders."""
    filtered = []
    for name in names:
        if not isinstance(name, str):
            continue
        if name.startswith(("itm_", "enc_", "skl_", "[")):
            continue
        filtered.append(name)
    return filtered


def _resolve_offered_names(decision, offered_raw: list[str], api_template_map: dict | None = None) -> list[str]:
    """Resolve offered names, preferring stored live decision context."""
    offered_names = []
    enriched_names = _load_json_list(decision["offered_names"]) if "offered_names" in decision.keys() else []

    if enriched_names:
        return enriched_names

    for oid in offered_raw:
        if oid == decision["chosen_id"] and decision["chosen_template"]:
            tid = decision["chosen_template"]
        elif api_template_map:
            tid = api_template_map.get(oid, "")
        else:
            tid = ""
        name = card_cache.resolve_template_id(tid) if tid else ""
        offered_names.append(name or oid)
    return offered_names


def _resolve_rejected_names(decision, offered_raw: list[str], offered_names: list[str]) -> list[str]:
    """Map rejected instance ids back to readable names using offered ordering."""
    rejected_raw = _load_json_list(decision["rejected"])
    if not rejected_raw:
        return []

    remaining = Counter(rejected_raw)
    rejected_names = []
    for raw_id, resolved_name in zip(offered_raw, offered_names):
        if remaining[raw_id] <= 0:
            continue
        rejected_names.append(resolved_name)
        remaining[raw_id] -= 1

    for raw_id, count in remaining.items():
        rejected_names.extend([raw_id] * count)

    return rejected_names


def _resolve_ids_via_cache(names: list[str], api_template_map: dict) -> list[str]:
    """Final-pass resolver: for any entry that still looks like a raw instance ID
    (e.g. itm_xxx, enc_xxx), try api_template_map -> card_cache.resolve_template_id()
    before giving up."""
    out = []
    for name in names:
        if isinstance(name, str) and re.match(r'^[a-z]{2,4}_', name):
            tid = api_template_map.get(name, "")
            resolved = card_cache.resolve_template_id(tid) if tid else ""
            out.append(resolved or name)
        else:
            out.append(name)
    return out


def _find_missed_flags(
    offered_names: list[str],
    phase: str,
    board_names: list[str],
    committed_arch: Optional[dict],
    builds: dict,
) -> list[str]:
    """Find actual mistake candidates for skips and passed shop options."""
    offered_names = _filter_resolved_names(offered_names)
    if not offered_names:
        return []

    missed_flags = []
    board_set = set(board_names or [])
    early_phase = builds["game_phases"].get("early", {})

    if phase == "early":
        utility = set(early_phase.get("universal_utility_items", []))
        relevant = _ordered_unique([name for name in offered_names if name in utility])
        if relevant:
            missed_flags.append(f"Universal utility: {relevant}")

        early_carries = set()
        for arch in builds["game_phases"].get("early_mid", {}).get("archetypes", []):
            early_carries.update(
                item for item in arch.get("carry_items", []) if not item.startswith("TODO")
            )
        relevant = _ordered_unique([name for name in offered_names if name in early_carries])
        if relevant:
            missed_flags.append(f"Early carry: {relevant}")

    if phase in ("early", "early_mid"):
        economy = set(early_phase.get("economy_items", []))
        relevant = _ordered_unique([name for name in offered_names if name in economy])
        if relevant:
            missed_flags.append(f"Economy: {relevant}")

    # Late archetype miss flags.
    #
    # Committed branch: a single committed arch is already known — flag the
    # support items the player skipped that belong to it.
    #
    # Not-committed branches (not-signaled / signaled): scan every late
    # archetype whose core or carry list contains each offered item.  The old
    # single-best-arch selection caused items relevant to *other* archetypes to
    # be silently ignored when the board hadn't accumulated enough overlap with
    # their arch yet (e.g. Spear at decision ~11-12 when no Spear & Friends
    # core items were on the board yet, so a different arch won the overlap
    # race and Spear was never evaluated).
    #
    # Progression model (applied per-arch for each offered item):
    #   Committed  (caller-supplied committed_arch)  → flag offered support items
    #   Signaled   (>50% core hits, board already built) → flag offered carry items
    #   Partial    (≤50% core hits)                  → flag offered core items

    late_archetypes = builds["game_phases"].get("late", {}).get("archetypes", [])

    if committed_arch is not None:
        # Committed case: evaluate only the committed arch, flag support items.
        overlap = _get_archetype_overlap(committed_arch, board_names)
        relevant = _ordered_unique(
            [name for name in offered_names
             if name in overlap["support"] and name not in board_set]
        )
        if relevant:
            missed_flags.append(f"Support for {committed_arch['name']}: {relevant}")
    else:
        # Not-committed: per-item scan across all late archetypes.
        # Collect one flag per (arch, kind) pair to avoid duplicate notes.
        flagged_pairs: set[tuple[str, str]] = set()

        for offered_item in offered_names:
            if offered_item in board_set:
                continue  # already owned; skipping it isn't a mistake

            for arch in late_archetypes:
                overlap = _get_archetype_overlap(arch, board_names)
                core_needed = _majority_count(len(overlap["core"]))
                is_signaled = len(overlap["core_hits"]) >= core_needed

                if is_signaled:
                    # Signaled: flag carry items for this arch
                    if offered_item in overlap["carry"]:
                        key = (arch["name"], "carry")
                        if key not in flagged_pairs:
                            flagged_pairs.add(key)
                            missed_flags.append(
                                f"Carry for {arch['name']}: [{offered_item}]"
                            )
                else:
                    # Partial: flag core items for this arch
                    if offered_item in overlap["core"]:
                        key = (arch["name"], "core")
                        if key not in flagged_pairs:
                            flagged_pairs.add(key)
                            missed_flags.append(
                                f"Core for {arch['name']}: [{offered_item}]"
                            )

    return missed_flags


def _summarize_names(names: list[str], limit: int = 3) -> str:
    """Compact note display for rejected alternatives."""
    display = names[:limit]
    suffix = "..." if len(names) > limit else ""
    return f"{display}{suffix}"


def _is_event_or_loot_purchase(decision, offered_raw: list[str]) -> bool:
    """Detect item rows that were likely event/loot rewards, not real shop buys.

    A decision is treated as an event/loot grant (and excluded from archetype
    scoring) when ANY of the following is true:

    1. Definitive non-shop game_state (ChoiceState / LevelUpState / LootState)
       AND exactly one item was offered — no real choice was made.

    2. game_state is NULL AND exactly one item was offered.
       Early-run decisions often arrive before Mono capture has written the
       game_state row, so NULL is ambiguous.  Single-offer + no state is almost
       always a free event loot drop (e.g. Blackmamba at decision #1).
       The Forest Cloud #33 false-positive is safe here because Forest Cloud had
       a non-NULL game_state (ShopState), which falls through to the False return.

    3. The offered instance IDs carry a non-item prefix (enc_/ste_/ped_) that
       confirms an encounter/event context regardless of state.
    """
    if decision["decision_type"] not in ("item", "companion"):
        return False

    game_state = (decision.get("game_state") if hasattr(decision, "get") else decision["game_state"]) or ""

    # Case 1: confirmed non-shop state + single offer
    if len(offered_raw) == 1 and game_state in ("ChoiceState", "LevelUpState", "LootState"):
        return True

    # Case 2: NULL game_state + single offer -> treat as likely event/loot grant.
    # This only applies to older rows without live Mono decision context.
    if len(offered_raw) == 1 and not game_state:
        return True

    # Case 3: encounter/event instance ID prefix
    offer_prefixes = {
        raw_id.split("_", 1)[0]
        for raw_id in offered_raw
        if isinstance(raw_id, str) and "_" in raw_id
    }
    if offer_prefixes & {"enc", "ste", "ped"}:
        return True

    return False


def _get_terminal_pvp_row(conn: sqlite3.Connection, run) -> Optional[sqlite3.Row]:
    """Prefer a terminal Mono snapshot linked to this run's decision context."""
    if not run:
        return None
    latest = conn.execute(
        """
        SELECT api_game_state_id
        FROM decisions
        WHERE run_id = ? AND api_game_state_id IS NOT NULL
        ORDER BY decision_seq DESC, id DESC
        LIMIT 1
        """,
        (run["id"],),
    ).fetchone()
    if not latest:
        return None

    outcome_to_state = {
        "victory": "EndRunVictory",
        "defeat": "EndRunDefeat",
    }
    desired_state = outcome_to_state.get(run["outcome"]) if "outcome" in run.keys() else None

    queries = []
    if desired_state:
        queries.append((
            """
            SELECT victories, defeats
            FROM api_game_states
            WHERE id >= ?
              AND run_state = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (latest["api_game_state_id"], desired_state),
        ))

    queries.append((
        """
        SELECT victories, defeats
        FROM api_game_states
        WHERE id >= ?
          AND run_state IN ('EndRunDefeat', 'EndRunVictory')
        ORDER BY id DESC
        LIMIT 1
        """,
        (latest["api_game_state_id"],),
    ))

    queries.append((
        """
        SELECT victories, defeats
        FROM api_game_states
        WHERE id >= ?
          AND victories IS NOT NULL
        ORDER BY id DESC
        LIMIT 1
        """,
        (latest["api_game_state_id"],),
    ))

    for sql, params in queries:
        row = conn.execute(sql, params).fetchone()
        if row and row["victories"] is not None:
            return row

    return None


def _resolve_api_template_for_instance(
    conn: sqlite3.Connection,
    instance_id: str,
) -> str:
    """Look up the newest trustworthy template id for a runtime instance."""
    if not instance_id:
        return ""

    rows = conn.execute(
        """
        SELECT template_id
        FROM api_cards
        WHERE instance_id = ?
          AND template_id IS NOT NULL
          AND template_id != ''
        ORDER BY id DESC
        """,
        (instance_id,),
    ).fetchall()

    suspicious = getattr(card_cache, "is_suspicious_template_id", lambda _value: False)
    fallback_tid = ""
    for row in rows:
        candidate = row["template_id"]
        if not candidate:
            continue
        if not fallback_tid:
            fallback_tid = candidate
        if not suspicious(candidate):
            return candidate
    return fallback_tid


def _resolve_board_card_name(
    conn: sqlite3.Connection,
    template_id: str,
    instance_id: str,
) -> str:
    """Resolve a tracked board/stash card to a stable display name."""
    resolved_template = template_id or _resolve_api_template_for_instance(conn, instance_id)
    if resolved_template:
        name = card_cache.resolve_template_id(resolved_template)
        if name:
            return name
    return instance_id


def _decision_owned_category(decision: sqlite3.Row) -> Optional[str]:
    """Map a decision row to the owned category it mutates, if any."""
    decision_type = decision["decision_type"] or ""
    if decision_type == "skill":
        return "player_skills"

    board_section = decision["board_section"] or ""
    if board_section == "Player":
        return "player_board"
    if board_section == "Storage":
        return "player_stash"
    return None


def _build_action_fallback_board_map(
    conn: sqlite3.Connection,
    run_id: int,
) -> dict[int, dict[str, str]]:
    """Legacy fallback: reconstruct player_board history from decisions.

    Used only for runs that predate board_snapshot_json. New runs should
    always have the snapshot column populated by RunState/BoardState.
    """
    decisions = conn.execute(
        """
        SELECT id, timestamp, decision_type, chosen_id, chosen_template, board_section
        FROM decisions
        WHERE run_id = ?
        ORDER BY decision_seq
        """,
        (run_id,),
    ).fetchall()
    if not decisions:
        return {}

    board: dict[str, str] = {}
    board_map: dict[int, dict[str, str]] = {}

    for decision in decisions:
        board_map[decision["id"]] = dict(board)
        category = _decision_owned_category(decision)
        if category in ("player_board", "player_stash"):
            instance_id = decision["chosen_id"] or ""
            if instance_id:
                card_name = _resolve_board_card_name(conn, decision["chosen_template"] or "", instance_id)
                board[instance_id] = card_name

    return board_map


def _load_board_snapshot_map(conn: sqlite3.Connection, run_id: int) -> dict[int, dict[str, str]]:
    """Load decision_id -> current player board.

    Reads board_snapshot_json from the decisions table (written at decision
    time by RunState/BoardState). Falls back to the legacy decision-replay
    approach for older runs that don't have the column populated.
    """
    rows = conn.execute(
        """
        SELECT id, board_snapshot_json
        FROM decisions
        WHERE run_id = ?
        ORDER BY decision_seq
        """,
        (run_id,),
    ).fetchall()

    snapshot_map: dict[int, dict[str, str]] = {}
    has_any_snapshot = False

    for row in rows:
        snapshot_json = row["board_snapshot_json"] if "board_snapshot_json" in row.keys() else None
        if snapshot_json:
            has_any_snapshot = True
            snapshot_map[row["id"]] = BoardState.board_map_from_snapshot_json(snapshot_json)

    if has_any_snapshot:
        return snapshot_map

    # Legacy fallback: pre-BoardState run, reconstruct from action events.
    return _build_action_fallback_board_map(conn, run_id)


def detect_phase(decision_seq: int, combat_count_so_far: int,
                 day: int = None, phase_actual: str = None) -> str:
    """
    Infer game phase. Prefers live Mono day/phase data on the decision
    when available; falls back to decision-count heuristic.
    """
    if phase_actual:
        return phase_actual
    if day is not None:
        if day <= 4:
            return "early"
        elif day <= 7:
            return "early_mid"
        else:
            return "late"
    if decision_seq <= PHASE_EARLY_MAX_DECISIONS:
        return "early"
    # Without day data, use combat count + decision seq as proxies.
    # Require BOTH a high combat count (>=6, roughly day 7+) AND seq past the
    # early_mid ceiling before classifying as "late" — combat_count alone was
    # too aggressive and pushed day-3 decisions into late scoring.
    if combat_count_so_far >= 6 and decision_seq > PHASE_EARLY_MID_MAX_DECISIONS:
        return "late"
    elif decision_seq <= PHASE_EARLY_MID_MAX_DECISIONS:
        return "early_mid"
    else:
        # seq past ceiling but not enough combats to confirm late game;
        # stay in early_mid to avoid penalising items without a day anchor.
        return "early_mid"

def get_archetype_all_items(archetype: dict) -> set:
    """All items relevant to an archetype (core + carry + support)."""
    items = set()
    for key in ("core_items", "carry_items", "support_items"):
        for item in archetype.get(key, []):
            if not item.startswith("TODO"):
                items.add(item)
    return items


def _ordered_unique(items: list[str]) -> list[str]:
    """Preserve offered order while removing duplicates."""
    seen = set()
    ordered = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        ordered.append(item)
    return ordered


def _clean_archetype_items(archetype: dict, key: str) -> list[str]:
    return [item for item in archetype.get(key, []) if not item.startswith("TODO")]


def _majority_count(total: int) -> int:
    """Return the strict majority needed for commit checks."""
    if total <= 0:
        return 0
    return (total // 2) + 1


def _get_archetype_overlap(archetype: dict, board_names: list[str]) -> dict:
    """Compute board overlap against a late archetype."""
    board_set = set(board_names or [])
    core = _clean_archetype_items(archetype, "core_items")
    carry = _clean_archetype_items(archetype, "carry_items")
    support = _clean_archetype_items(archetype, "support_items")
    core_hits = [item for item in core if item in board_set]
    carry_hits = [item for item in carry if item in board_set]
    support_hits = [item for item in support if item in board_set]
    total_hits = len(set(core_hits + carry_hits + support_hits))
    return {
        "core": core,
        "carry": carry,
        "support": support,
        "core_hits": core_hits,
        "carry_hits": carry_hits,
        "support_hits": support_hits,
        "total_hits": total_hits,
    }


def check_commit_threshold(archetype: dict, board_names: list) -> tuple[bool, str]:
    """
    Check whether a build is committed using the live board state.
    Returns (committed: bool, reason: str)
    """
    overlap = _get_archetype_overlap(archetype, board_names)
    core_needed = _majority_count(len(overlap["core"]))

    if not overlap["core"] or not overlap["carry"]:
        return False, "Need core items and a carry item"

    if len(overlap["core_hits"]) >= core_needed and overlap["carry_hits"]:
        return True, f"{len(overlap['core_hits'])}/{len(overlap['core'])} core + {overlap['carry_hits'][0]}"

    return (
        False,
        f"Need {core_needed}/{len(overlap['core'])} core + carry. "
        f"Have core: {overlap['core_hits']}, carry: {overlap['carry_hits']}",
    )


def find_committed_archetype(board_names: list[str], builds: dict) -> tuple[Optional[dict], str]:
    """Pick the best-matching committed late archetype, if any."""
    best_arch = None
    best_reason = ""
    best_rank = None

    for arch in builds["game_phases"].get("late", {}).get("archetypes", []):
        committed, reason = check_commit_threshold(arch, board_names)
        if not committed:
            continue
        overlap = _get_archetype_overlap(arch, board_names)
        rank = (
            len(overlap["core_hits"]),
            len(overlap["carry_hits"]),
            overlap["total_hits"],
        )
        if best_rank is None or rank > best_rank:
            best_arch = arch
            best_reason = reason
            best_rank = rank

    return best_arch, best_reason


def _timing_progress(day: Optional[int], phase: str) -> float:
    """Map a decision's run-timeline position to a [0.0, 1.0] progress value.

    Day data from live decision context wins when present.  Otherwise we fall
    back to phase-based anchors that approximate the median day each phase
    covers (early ≈ day 2, early_mid ≈ day 6, late ≈ day 10).  Used as the
    independent variable in TIMING_PROFILE_CURVES.
    """
    if day is not None:
        if day <= 1:
            return 0.0
        if day >= 13:
            return 1.0
        return (day - 1) / 12.0
    return {
        "early": 0.15,
        "early_mid": 0.45,
        "late": 0.8,
    }.get(phase, 0.5)


def _late_archetype_timing_weight(
    archetype: dict,
    *,
    day: Optional[int],
    phase: str,
) -> float:
    """Weight late archetypes by how early/late their payoff comes online.

    Reads ``archetype["timing_profile"]`` (one of VALID_TIMING_PROFILES).
    Unknown / missing values fall back to ``neutral`` (constant 1.0).
    """
    raw = str(archetype.get("timing_profile") or "neutral").strip().lower()
    profile = raw if raw in VALID_TIMING_PROFILES else "neutral"
    curve = TIMING_PROFILE_CURVES[profile]
    progress = _timing_progress(day, phase)
    return curve["base"] + curve["slope"] * progress


def _rank_late_item_matches(
    item_name: str,
    late_archetypes: list[dict],
    *,
    day: Optional[int],
    phase: str,
) -> list[dict]:
    ranked = []
    for arch in late_archetypes:
        core = [i for i in arch.get("core_items", []) if not i.startswith("TODO")]
        carry = [i for i in arch.get("carry_items", []) if not i.startswith("TODO")]
        support = [i for i in arch.get("support_items", []) if not i.startswith("TODO")]

        bucket = None
        if item_name in carry:
            bucket = "carry"
        elif item_name in core:
            bucket = "core"
        elif item_name in support:
            bucket = "support"
        if bucket is None:
            continue

        timing_weight = _late_archetype_timing_weight(
            arch,
            day=day,
            phase=phase,
        )
        bucket_rank = {"carry": 3, "core": 2, "support": 1}[bucket]
        ranked.append({
            "name": arch["name"],
            "bucket": bucket,
            "timing_profile": str(arch.get("timing_profile") or "neutral"),
            "timing_weight": timing_weight,
            "rank": (
                timing_weight,
                bucket_rank,
                len(core),
                len(carry),
                len(support),
            ),
        })

    return sorted(ranked, key=lambda row: row["rank"], reverse=True)


def _is_catalog_item(item_name: str, builds: dict) -> bool:
    """Return True if the item appears anywhere in the build catalog.

    An item is "known" if it has a tier entry OR appears in any archetype
    list OR is listed as a universal utility / economy item.  Items that
    fail all three checks are completely outside the guide — loot drops,
    hero-specific quest items, companions we haven't catalogued, etc. —
    and should be skipped rather than scored suboptimal.
    """
    if not item_name:
        return False
    if get_item_tier(builds, item_name) is not None:
        return True
    for phase_data in builds.get("game_phases", {}).values():
        if item_name in phase_data.get("universal_utility_items", []):
            return True
        if item_name in phase_data.get("economy_items", []):
            return True
        for arch in phase_data.get("archetypes", []):
            for key in ("core_items", "carry_items", "support_items"):
                if item_name in arch.get(key, []):
                    return True
    return False


def score_early_decision(item_name: str, builds: dict, offered_names: list) -> tuple[str, str]:
    """Score a decision in the early phase."""
    utility = builds["game_phases"]["early"]["universal_utility_items"]
    economy = builds["game_phases"]["early"]["economy_items"]
    tier = get_item_tier(builds, item_name)

    if item_name in utility or item_name in economy:
        return "optimal", f"Core early item ({tier} tier). Always good to pick up."

    if tier in ("S", "A"):
        return "good", f"{tier}-tier item. Strong pickup even without build direction."

    if tier in ("B",):
        return "situational", f"{tier}-tier item. Fine early if it fits your opener."

    if tier in ("C", "D", "F") or tier is None:
        t = tier or "unranked"
        return "suboptimal", f"{t}-tier item early. Usually better to hold for stronger options."

    return "situational", f"No tier data for {item_name}."


def score_early_mid_decision(
    item_name: str,
    board_names: list,
    builds: dict,
    *,
    day: Optional[int] = None,
) -> tuple[str, str]:
    """Score a decision in the early_mid phase."""
    archetypes = builds["game_phases"]["early_mid"]["archetypes"]
    late_archetypes = builds["game_phases"]["late"]["archetypes"]
    tier = get_item_tier(builds, item_name)

    matching = []
    for arch in archetypes:
        all_items = (arch.get("carry_items", []) + arch.get("support_items", []))
        all_items = [i for i in all_items if not i.startswith("TODO")]
        if item_name in all_items:
            matching.append(arch["name"])

    late_matches = _rank_late_item_matches(
        item_name,
        late_archetypes,
        day=day,
        phase="early_mid",
    )
    late_core_matches = [match for match in late_matches if match["bucket"] in ("core", "carry")]
    late_core_names = [match["name"] for match in late_core_matches]

    # Count how many archetypes this pick overlaps with
    overlap = len(matching)

    # Strong late enabler picked early — upgrade the score
    if late_core_matches and overlap == 0:
        top_match = late_core_matches[0]
        if top_match["timing_weight"] >= TIMING_WEIGHT_HIGH:
            if len(late_core_matches) >= 2 and late_core_matches[1]["timing_weight"] >= 1.0:
                return "optimal", (
                    f"Core/carry item for tempo-friendly late archetypes: "
                    f"{', '.join(late_core_names[:3])}. Excellent early pickup."
                )
            return "good", (
                f"Core/carry item for {top_match['name']} late build. "
                f"Strong early pickup that enables late game."
            )
        if top_match["timing_weight"] <= TIMING_WEIGHT_LOW:
            return "situational", (
                f"Late payoff for {top_match['name']}. "
                f"Stronger later once the rest of the shell is online."
            )
        if len(late_core_matches) >= 2:
            return "good", (
                f"Core/carry item for late archetypes: {', '.join(late_core_names[:3])}. "
                f"Useful bridge item."
            )
        return "good", (
            f"Core/carry item for {top_match['name']} late build. "
            f"Useful bridge toward late game."
        )
    if late_core_matches and overlap >= 1:
        top_match = late_core_matches[0]
        if top_match["timing_weight"] <= TIMING_WEIGHT_LOW:
            return "good", (
                f"Fits early archetype(s), but the late payoff {top_match['name']} "
                f"ramps later. Good if your current board can carry it."
            )
        return "optimal", (
            f"Fits early archetype(s) AND is a core/carry for late: "
            f"{', '.join(late_core_names[:3])}. Very flexible."
        )

    if overlap >= 3:
        return "optimal", f"Fits {overlap} archetypes ({', '.join(matching)}). High-value flexible pick."
    elif overlap == 2:
        return "good", f"Fits {overlap} archetypes ({', '.join(matching)}). Solid pick that keeps options open."
    elif overlap == 1:
        # Check if it matches what's already on the board (exclude current item to avoid self-match)
        arch = next(a for a in archetypes if a["name"] == matching[0])
        arch_items = set(arch.get("carry_items", []) + arch.get("support_items", []))
        board_overlap = [b for b in board_names if b in arch_items and b != item_name]
        if len(board_overlap) >= 1:
            return "good", f"Reinforces {matching[0]} direction (already have: {board_overlap}). Solid."
        # Check if current item is a duplicate (for upgrading)
        if item_name in board_names:
            return "good", f"Duplicate {item_name} — upgrading to next tier for {matching[0]} build."
        return "situational", f"Only fits {matching[0]} and board has no other {matching[0]} items yet."
    else:
        if tier in ("S", "A"):
            return "situational", f"Not in any archetype list but {tier}-tier. Could be worth it for raw power."
        return "suboptimal", f"Doesn't fit any tracked archetype and is {tier or 'unranked'}-tier. Hard to justify."


def score_late_decision(
    item_name: str,
    board_names: list,
    committed_arch: Optional[dict],
    builds: dict,
    *,
    day: Optional[int] = None,
) -> tuple[str, str]:
    """Score a decision in the late phase."""
    tier = get_item_tier(builds, item_name)
    late_archetypes = builds["game_phases"]["late"]["archetypes"]

    if committed_arch:
        arch_name = committed_arch["name"]
        core = [i for i in committed_arch.get("core_items", []) if not i.startswith("TODO")]
        carry = [i for i in committed_arch.get("carry_items", []) if not i.startswith("TODO")]
        support = [i for i in committed_arch.get("support_items", []) if not i.startswith("TODO")]

        if item_name in carry:
            return "optimal", f"Carry item for committed build ({arch_name})."
        if item_name in core:
            return "optimal", f"Core enabler for committed build ({arch_name})."
        if item_name in support:
            return "good", f"Support item for committed build ({arch_name})."

        # Check if it fits any other late archetype as a pivot signal
        other_matches = []
        for arch in late_archetypes:
            if arch["name"] == arch_name:
                continue
            all_items = get_archetype_all_items(arch)
            if item_name in all_items:
                other_matches.append(arch["name"])

        if other_matches:
            return "suboptimal", (
                f"Not in {arch_name} item list. Fits {other_matches} instead. "
                f"Consider pivoting or selling this."
            )
        if tier in ("S", "A"):
            return "situational", f"Not in {arch_name} list but {tier}-tier. Assess if it genuinely helps."
        return "suboptimal", (
            f"Doesn't fit committed build ({arch_name}) and is {tier or 'unranked'}-tier. "
            f"Likely wasted pick."
        )

    else:
        # Not yet committed — score against all late archetypes
        late_matches = _rank_late_item_matches(
            item_name,
            late_archetypes,
            day=day,
            phase="late",
        )
        matching = [match["name"] for match in late_matches]

        if len(matching) >= 2:
            top_weight = late_matches[0]["timing_weight"]
            if top_weight <= TIMING_WEIGHT_LOW:
                return "situational", (
                    f"Fits {len(matching)} late archetypes ({', '.join(matching[:3])}), "
                    f"but those payoffs ramp later."
                )
            if top_weight >= TIMING_WEIGHT_HIGH:
                return "good", (
                    f"Fits {len(matching)} late archetypes ({', '.join(matching[:3])}). "
                    f"Strong at this point in the run."
                )
            return "good", f"Fits {len(matching)} late archetypes ({', '.join(matching[:3])}). Keeps options open."
        elif len(matching) == 1:
            top_match = late_matches[0]
            if top_match["timing_weight"] <= TIMING_WEIGHT_LOW:
                return "situational", (
                    f"Fits {top_match['name']}, but that payoff usually ramps later. "
                    f"Starts committing you to that direction."
                )
            if top_match["timing_weight"] >= TIMING_WEIGHT_HIGH:
                return "good", (
                    f"Fits {top_match['name']} and lines up with the current stage of the run."
                )
            return "situational", f"Fits {top_match['name']} only. Starts committing you to that direction."
        else:
            if tier in ("S", "A"):
                return "situational", f"Not in any late archetype but {tier}-tier. Raw power pick."
            return "suboptimal", f"Doesn't fit any late archetype and is {tier or 'unranked'}-tier."


def _score_loaded_run(
    conn: sqlite3.Connection,
    run_id: int,
    decisions,
    combats,
    builds: dict,
) -> list[dict]:
    scored = []
    board = {}  # instance_id -> item_name
    board_snapshots = _load_board_snapshot_map(conn, run_id)
    committed_arch = None

    for d in decisions:
        dtype = d["decision_type"]
        item_name = card_cache.resolve_template_id(d["chosen_template"]) if d["chosen_template"] else d["chosen_id"]
        _d_ts = d["timestamp"] if "timestamp" in d.keys() else None
        phase = detect_phase(
            d["decision_seq"],
            len([c for c in combats
                 if _d_ts and "timestamp" in c.keys() and c["timestamp"] <= _d_ts]),
            day=d["day"] if "day" in d.keys() else None,
            phase_actual=d["phase_actual"] if "phase_actual" in d.keys() else None,
        )
        offered_raw = _load_json_list(d["offered"])
        api_template_map = _load_json_dict(d["offered_templates"]) if "offered_templates" in d.keys() else {}
        snapshot_board = board_snapshots.get(d["id"])
        if snapshot_board is not None:
            board = dict(snapshot_board)
        if committed_arch is None:
            committed_arch, _ = find_committed_archetype(list(board.values()), builds)

        if dtype == "skip":
            pre_resolved = []
            try:
                stored = json.loads(d.get("score_notes") or "{}")
                if isinstance(stored, dict):
                    pre_resolved = stored.get("resolved_names", [])
                    skip_rerolls = stored.get("rerolls", 0)
            except (json.JSONDecodeError, TypeError):
                skip_rerolls = 0

            named_offered = [n for n in pre_resolved if n]
            live_names = _resolve_offered_names(d, offered_raw, api_template_map)
            if live_names:
                named_offered = _filter_resolved_names(live_names)

            missed_flags = _find_missed_flags(
                named_offered,
                phase,
                list(board.values()),
                committed_arch,
                builds,
            )

            reroll_note = f" after {skip_rerolls} reroll(s)" if skip_rerolls else ""
            skip_label = "warning" if missed_flags else "info"
            if missed_flags:
                notes = f"Skipped{reroll_note} - missed: {'; '.join(missed_flags)}"
            elif named_offered:
                notes = f"Skipped{reroll_note}: {named_offered}"
            else:
                notes = f"Skipped shop{reroll_note} ({len(offered_raw)} items, names unresolved)"

            scored.append({
                "decision_id": d["id"],
                "seq": d["decision_seq"],
                "phase": phase,
                "decision_type": "skip",
                "item_name": "(skipped shop)",
                "label": skip_label,
                "notes": notes,
                "board": dict(board),
                "game_state": d["game_state"],
            })
            continue

        if dtype == "free_reward":
            if d["board_section"] == "Player" and d["chosen_template"]:
                board[d["chosen_id"]] = item_name
            scored.append({
                "decision_id": d["id"],
                "seq": d["decision_seq"],
                "phase": phase,
                "decision_type": "free_reward",
                "item_name": item_name,
                "label": "info",
                "notes": "Free reward (no choice).",
                "board": dict(board),
                "game_state": d["game_state"],
            })
            continue

        if dtype == "event_choice":
            scored.append({
                "decision_id": d["id"],
                "seq": d["decision_seq"],
                "phase": phase,
                "decision_type": "event_choice",
                "item_name": item_name,
                "label": "info",
                "notes": "Chose map node.",
                "board": dict(board),
                "game_state": d["game_state"],
            })
            continue

        offered_names = _resolve_offered_names(d, offered_raw, api_template_map)
        rejected_names = _resolve_rejected_names(d, offered_raw, offered_names)
        # Final-pass: resolve any remaining raw IDs via api_template_map + card_cache
        rejected_names = _resolve_ids_via_cache(rejected_names, api_template_map)
        resolved_rejected = _filter_resolved_names(rejected_names)
        rejected_for_notes = resolved_rejected or rejected_names

        if _is_event_or_loot_purchase(d, offered_raw):
            board[d["chosen_id"]] = item_name
            scored.append({
                "decision_id": d["id"],
                "seq": d["decision_seq"],
                "phase": phase,
                "decision_type": dtype,
                "item_name": item_name,
                "label": "info",
                "notes": "Event/loot item. Excluded from archetype-fit scoring.",
                "board": dict(board),
                "game_state": d["game_state"],
            })
            continue

        purchase_missed_flags = _find_missed_flags(
            resolved_rejected,
            phase,
            list(board.values()),
            committed_arch,
            builds,
        )

        if d["decision_type"] == "skill":
            label = None
            notes = None
        elif dtype in ("item", "companion") and not _is_catalog_item(item_name, builds):
            # Item is completely unknown to the build guide — loot drops,
            # uncatalogued companions, hero-specific quest items, etc.
            # Don't score it; a suboptimal label here is false noise.
            label = None
            notes = f"Not in {builds.get('hero', 'hero')} catalog — no score assigned."
        elif phase == "early":
            label, notes = score_early_decision(item_name, builds, offered_names)
        elif phase == "early_mid":
            label, notes = score_early_mid_decision(
                item_name,
                list(board.values()),
                builds,
                day=d["day"] if "day" in d.keys() else None,
            )
        else:
            label, notes = score_late_decision(
                item_name,
                list(board.values()),
                committed_arch,
                builds,
                day=d["day"] if "day" in d.keys() else None,
            )

        if d["decision_type"] != "skill":
            board[d["chosen_id"]] = item_name

        if committed_arch is None:
            newly_committed_arch, reason = find_committed_archetype(list(board.values()), builds)
            if newly_committed_arch:
                committed_arch = newly_committed_arch
                # This item triggered the commit — upgrade label regardless of what
                # score_late_decision returned, since it was evaluating a pre-commit board.
                if dtype in ("item", "companion") and phase == "late":
                    label = "optimal"
                    notes = f"Commits {committed_arch['name']} ({reason})." + (
                        f" {notes}" if notes else ""
                    )
                else:
                    notes = (notes or "") + f" COMMITTED to {newly_committed_arch['name']} ({reason})."

        if phase == "late" and committed_arch is None and d["decision_type"] != "skill":
            arch_counts = {}
            board_item_list = list(board.values())
            for arch in builds["game_phases"]["late"]["archetypes"]:
                all_arch = set(
                    [i for i in arch.get("core_items", []) if not i.startswith("TODO")] +
                    [i for i in arch.get("carry_items", []) if not i.startswith("TODO")] +
                    [i for i in arch.get("support_items", []) if not i.startswith("TODO")]
                )
                overlap = [b for b in board_item_list if b in all_arch]
                if len(overlap) >= 2:
                    arch_counts[arch["name"]] = overlap
            if arch_counts:
                top = max(arch_counts, key=lambda k: len(arch_counts[k]))
                if len(arch_counts[top]) >= 2:
                    notes = (notes or "") + f" Board converging on {top} ({arch_counts[top]})."
        elif phase == "late" and committed_arch is not None and d["decision_type"] != "skill":
            carry = [c for c in committed_arch.get("carry_items", []) if not c.startswith("TODO")]
            if item_name in carry:
                notes = notes.replace("Fits 3 late", f"Carry item for {committed_arch['name']}! ")
                notes = notes.replace("Keeps options open", f"Build now has carry")

        if dtype in ("item", "companion") and rejected_for_notes:
            notes = (notes or "") + f" Passed on: {_summarize_names(rejected_for_notes)}."
            if purchase_missed_flags:
                notes += f" Missed alternatives: {'; '.join(purchase_missed_flags)}."

        scored.append({
            "decision_id": d["id"],
            "seq": d["decision_seq"],
            "phase": phase,
            "decision_type": dtype,
            "item_name": item_name,
            "label": label,
            "notes": notes,
        })

    return scored


class LiveScorer:
    """Score decisions incrementally as they are recorded.

    RunState instantiates one LiveScorer per run and calls ``score_decision``
    immediately after each ``db.insert_decision``.  The scorer writes
    ``score_label`` and ``score_notes`` back to the DB via
    ``db.update_decision_score`` so the overlay always reads stored scores
    instead of re-computing them on every poll.

    Usage::

        scorer_instance = LiveScorer(hero_name, conn)
        decision_id = db.insert_decision(...)
        scorer_instance.score_decision(decision_dict, decision_id)

    ``decision_dict`` must contain the same fields that ``_score_loaded_run``
    reads from the DB (decision_seq, decision_type, offered, chosen_id,
    chosen_template, rejected, board_section, game_state, day, phase_actual,
    offered_names, score_notes).
    """

    def __init__(self, hero: Optional[str], conn: sqlite3.Connection):
        self.builds = load_builds(hero)
        self._has_catalog = has_build_catalog(self.builds)
        self.conn = conn
        # Incremental state — mirrors _score_loaded_run's loop variables.
        self._board: dict[str, str] = {}
        self._committed_arch: Optional[dict] = None
        self._combats_so_far: int = 0  # updated via notify_combat()

    def notify_combat(self) -> None:
        """Call whenever a combat result is recorded (increments phase heuristic)."""
        self._combats_so_far += 1

    def score_decision(self, decision: dict, decision_id: int) -> dict:
        """Score a single decision and write the result to the DB.

        ``decision`` is a plain dict with the same column names as the
        decisions table.  Returns the scored result dict (label, notes).
        """
        if not self._has_catalog:
            return {"label": None, "notes": None}

        # Wrap in a single-element list so _score_loaded_run's loop runs once,
        # starting from the current incremental board/committed_arch state.
        # We snapshot and restore board state so the scorer's loop doesn't
        # interfere with our own tracking — _score_loaded_run mutates a local
        # board copy, so we re-read its output board after the call.
        result = _score_single_decision(
            self.conn,
            decision,
            board=self._board,
            committed_arch=self._committed_arch,
            combat_count=self._combats_so_far,
            builds=self.builds,
        )
        label = result.get("label")
        notes = result.get("notes") or ""

        # Advance incremental state.
        self._board = result["board"]
        self._committed_arch = result["committed_arch"]

        db.update_decision_score(decision_id, label, notes)
        return result


def _score_single_decision(
    conn: sqlite3.Connection,
    decision: dict,
    *,
    board: dict[str, str],
    committed_arch: Optional[dict],
    combat_count: int,
    builds: dict,
) -> dict:
    """Score one decision dict against the current incremental board state.

    Returns a dict with ``label``, ``notes``, ``board`` (updated), and
    ``committed_arch`` (updated).  This is a thin wrapper around the batch
    scorer's logic, restructured to operate on a single decision with
    externally-supplied board state.
    """
    import card_cache as _cc

    dtype = decision.get("decision_type") or ""
    chosen_template = decision.get("chosen_template") or ""
    chosen_id = decision.get("chosen_id") or ""
    item_name = _cc.resolve_template_id(chosen_template) if chosen_template else chosen_id

    decision_seq = decision.get("decision_seq") or 0
    phase = detect_phase(
        decision_seq,
        combat_count,
        day=decision.get("day"),
        phase_actual=decision.get("phase_actual"),
    )

    offered_raw = _load_json_list(decision.get("offered"))

    board = dict(board)  # work on a copy

    # ── Skip ────────────────────────────────────────────────────────────────
    if dtype == "skip":
        pre_resolved: list[str] = []
        skip_rerolls = 0
        try:
            stored = json.loads(decision.get("score_notes") or "{}")
            if isinstance(stored, dict):
                pre_resolved = stored.get("resolved_names", [])
                skip_rerolls = stored.get("rerolls", 0)
        except (json.JSONDecodeError, TypeError):
            skip_rerolls = 0

        named_offered = [n for n in pre_resolved if n]
        enriched_names = _load_json_list(decision.get("offered_names"))
        if enriched_names:
            named_offered = _filter_resolved_names(enriched_names)

        missed_flags = _find_missed_flags(
            named_offered, phase, list(board.values()), committed_arch, builds
        )
        reroll_note = f" after {skip_rerolls} reroll(s)" if skip_rerolls else ""
        label = "warning" if missed_flags else "info"
        if missed_flags:
            notes = f"Skipped{reroll_note} - missed: {'; '.join(missed_flags)}"
        elif named_offered:
            notes = f"Skipped{reroll_note}: {named_offered}"
        else:
            notes = f"Skipped shop{reroll_note} ({len(offered_raw)} items, names unresolved)"
        return {"label": label, "notes": notes, "board": board, "committed_arch": committed_arch}

    # ── Free reward ─────────────────────────────────────────────────────────
    if dtype == "free_reward":
        if decision.get("board_section") == "Player" and chosen_template:
            board[chosen_id] = item_name
        return {
            "label": "info",
            "notes": "Free reward (no choice).",
            "board": board,
            "committed_arch": committed_arch,
        }

    # ── Event choice ────────────────────────────────────────────────────────
    if dtype == "event_choice":
        return {
            "label": "info",
            "notes": "Chose map node.",
            "board": board,
            "committed_arch": committed_arch,
        }

    # ── Item / companion / skill ────────────────────────────────────────────
    offered_names = _load_json_list(decision.get("offered_names")) or [
        _cc.resolve_template_id(oid) or oid for oid in offered_raw
    ]
    rejected_raw = _load_json_list(decision.get("rejected"))
    rejected_names = _resolve_rejected_names(
        decision, offered_raw, offered_names
    ) if rejected_raw else []
    resolved_rejected = _filter_resolved_names(rejected_names)

    if _is_event_or_loot_purchase(decision, offered_raw):
        board[chosen_id] = item_name
        return {
            "label": "info",
            "notes": "Event/loot item. Excluded from archetype-fit scoring.",
            "board": board,
            "committed_arch": committed_arch,
        }

    purchase_missed_flags = _find_missed_flags(
        resolved_rejected, phase, list(board.values()), committed_arch, builds
    )

    label: Optional[str] = None
    notes: Optional[str] = None

    if dtype in ("item", "companion") and not _is_catalog_item(item_name, builds):
        notes = f"Not in {builds.get('hero', 'hero')} catalog — no score assigned."
    elif phase == "early":
        label, notes = score_early_decision(item_name, builds, offered_names)
    elif phase == "early_mid":
        label, notes = score_early_mid_decision(
            item_name, list(board.values()), builds,
            day=decision.get("day"),
        )
    else:
        label, notes = score_late_decision(
            item_name, list(board.values()), committed_arch, builds,
            day=decision.get("day"),
        )

    if dtype != "skill":
        board[chosen_id] = item_name

    if committed_arch is None:
        newly_committed_arch, reason = find_committed_archetype(list(board.values()), builds)
        if newly_committed_arch:
            committed_arch = newly_committed_arch
            if dtype in ("item", "companion") and phase == "late":
                label = "optimal"
                notes = f"Commits {committed_arch['name']} ({reason})." + (
                    f" {notes}" if notes else ""
                )
            elif notes is not None:
                notes = (notes or "") + f" COMMITTED to {newly_committed_arch['name']} ({reason})."

    if phase == "late" and committed_arch is None and dtype != "skill":
        arch_counts = {}
        board_item_list = list(board.values())
        for arch in builds["game_phases"]["late"]["archetypes"]:
            all_arch = set(
                [i for i in arch.get("core_items", []) if not i.startswith("TODO")] +
                [i for i in arch.get("carry_items", []) if not i.startswith("TODO")] +
                [i for i in arch.get("support_items", []) if not i.startswith("TODO")]
            )
            overlap = [b for b in board_item_list if b in all_arch]
            if len(overlap) >= 2:
                arch_counts[arch["name"]] = overlap
        if arch_counts:
            top = max(arch_counts, key=lambda k: len(arch_counts[k]))
            if len(arch_counts[top]) >= 2 and notes is not None:
                notes = (notes or "") + f" Board converging on {top} ({arch_counts[top]})."
    elif phase == "late" and committed_arch is not None and dtype != "skill":
        carry = [c for c in committed_arch.get("carry_items", []) if not c.startswith("TODO")]
        if item_name in carry and notes:
            notes = notes.replace("Fits 3 late", f"Carry item for {committed_arch['name']}! ")
            notes = notes.replace("Keeps options open", "Build now has carry")

    if dtype in ("item", "companion") and (resolved_rejected or rejected_names):
        rejected_for_notes = resolved_rejected or rejected_names
        notes = (notes or "") + f" Passed on: {_summarize_names(rejected_for_notes)}."
        if purchase_missed_flags:
            notes += f" Missed alternatives: {'; '.join(purchase_missed_flags)}."

    return {"label": label, "notes": notes, "board": board, "committed_arch": committed_arch}


def score_run(run_id: int, dry_run: bool = True) -> list:
    """Compute a manual report for a run without mutating stored scores."""
    conn = db.get_conn()
    try:
        run = conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if not run:
            print(f"[Scorer] Run {run_id} not found")
            return []

        hero_name = run["hero"] if "hero" in run.keys() else None
        builds = load_builds(hero_name)
        if not has_build_catalog(builds):
            print(
                f"[Scorer] No build catalog available for hero "
                f"{hero_name or DEFAULT_HERO}; skipping scoring for run {run_id}"
            )
            return []

        decisions = conn.execute(
            """SELECT * FROM decisions WHERE run_id=?
               AND decision_type IN ('item','companion','skill','skip','free_reward')
               ORDER BY decision_seq""",
            (run_id,),
        ).fetchall()
        if not decisions:
            print(f"[Scorer] No decisions found for run {run_id}")
            return []

        combats = conn.execute(
            "SELECT * FROM combat_results WHERE run_id=? ORDER BY id",
            (run_id,),
        ).fetchall()

        print(f"\n[Scorer] Manual report for run {run_id} | Hero: {run['hero']} | {len(decisions)} decisions")
        scored = _score_loaded_run(conn, run_id, decisions, combats, builds)
        if not dry_run:
            print("[Scorer] Stored scores are live-only; manual score_run does not write to DB.")
        return scored
    finally:
        conn.close()


def print_report(scored: list, run_id: int):
    """Print a human-readable scoring report."""
    conn = db.get_conn()
    try:
        run = conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        combats = conn.execute(
            "SELECT outcome, combat_type FROM combat_results WHERE run_id=?", (run_id,)
        ).fetchall()

        pvp_wins = sum(
            1 for c in combats
            if c["outcome"] == "opponent_died" and (c["combat_type"] or "pve") == "pvp"
        )
        pvp_losses = sum(
            1 for c in combats
            if c["outcome"] == "player_died" and (c["combat_type"] or "pve") == "pvp"
        )
        pve_wins = sum(
            1 for c in combats
            if c["outcome"] == "opponent_died" and (c["combat_type"] or "pve") == "pve"
        )
        pve_losses = sum(
            1 for c in combats
            if c["outcome"] == "player_died" and (c["combat_type"] or "pve") == "pve"
        )

        if run:
            try:
                terminal_row = _get_terminal_pvp_row(conn, run)
                if terminal_row and terminal_row["victories"] is not None:
                    pvp_wins = terminal_row["victories"]
                    pvp_losses = terminal_row["defeats"] or 0
            except sqlite3.Error as exc:
                print(f"[Scorer] Terminal PvP lookup failed: {exc}")

        unresolved_pvp = sum(
            1 for c in combats
            if (c["combat_type"] or "pve") == "pvp_unknown"
        )

        label_counts = {}
        for s in scored:
            label_counts[s["label"]] = label_counts.get(s["label"], 0) + 1

        print("\n" + "═" * 70)
        print(f"  SCORING REPORT  |  Hero: {run['hero']}  |  Run ID: {run_id}")
        print(f"  PvP: {pvp_wins}W / {pvp_losses}L  |  PvE: {pve_wins}W / {pve_losses}L  |  Decisions: {len(scored)}")
        if unresolved_pvp:
            print(
                f"  Note: {unresolved_pvp} combat(s) still marked pvp_unknown "
                f"(log-only PvP outcome; no terminal Mono context was linked).\n"
            )
        if pvp_wins >= 10 and pvp_losses == 0:
            print("  Perfect finish - 10 wins without losing prestige")
        elif pvp_wins >= 10:
            print("  Gold finish - run won")
        elif pvp_wins >= 7:
            print("  Silver finish - above MMR threshold")
        elif pvp_wins >= 4:
            print("  Bronze finish - reached the first ranked tier")
        print("═" * 70)

        label_icons = {
            "optimal": "✅",
            "good": "👍",
            "situational": "⚠️ ",
            "suboptimal": "❌",
            "warning": "🔶",
            "info": "ℹ️ ",
        }

        phase_order = ["early", "early_mid", "late"]
        for phase in phase_order:
            phase_decisions = [s for s in scored if s["phase"] == phase]
            if not phase_decisions:
                continue
            print(f"\n  [{phase.upper().replace('_', ' ')}]")
            for s in phase_decisions:
                dtype = s.get("decision_type", "")
                # Suppress free_rewards — board state only, no scoring value
                if dtype == "free_reward":
                    continue
                # Suppress skips with no resolved names — can't score them, just noise
                if dtype == "skip" and "names unresolved" in s.get("notes", ""):
                    continue
                icon = label_icons.get(s["label"], "  ")
                print(f"  {icon} #{s['seq']:>2}  {s['item_name']:<30}")
                print(f"       {s['notes']}")

        print(f"\n  SUMMARY: ", end="")
        for label in ["optimal", "good", "situational", "suboptimal", "warning"]:
            count = label_counts.get(label, 0)
            if count == 0:
                continue
            icon = label_icons.get(label, "")
            print(f"{icon} {label}: {count}  ", end="")
        print("\n" + "═" * 70 + "\n")
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(
        description="Print a manual Bazaar tracker scoring report without rewriting stored scores"
    )
    parser.add_argument(
        "--run-id",
        type=int,
        default=None,
        help="Run ID to report on (default: most recent run)",
    )
    args = parser.parse_args()

    db.init_db()

    run_id = args.run_id
    if run_id is None:
        conn = db.get_conn()
        try:
            row = conn.execute("SELECT id FROM runs ORDER BY id DESC LIMIT 1").fetchone()
            if not row:
                print("[Scorer] No runs found in database.")
                return
            run_id = row["id"]
        finally:
            conn.close()

    scored = score_run(run_id)
    print_report(scored, run_id)


if __name__ == "__main__":
    main()
