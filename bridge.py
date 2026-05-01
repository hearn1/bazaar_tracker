"""
bridge.py — Post-hoc enrichment bridge between the two capture pipelines.

Pipeline A (Player.log):  runs, decisions, combat_results
Pipeline B (Mono capture): api_game_states, api_cards

This module enriches Pipeline A's decision records with Pipeline B's richer data:
  Step 1: Run Correlation     — match api_game_states to runs by time + hero
  Step 2: Decision Enrichment — fill day/gold/health/phase_actual for NULL rows
  Step 3: Combat Enrichment   — resolve PvP outcomes from victory/defeat diffs

Usage:
    python bridge.py                       # enrich most recent run
    python bridge.py --run-id 3            # enrich specific run
    python bridge.py --run-id 3 --dry-run  # preview without DB writes
    python bridge.py --all                 # enrich all runs
    python bridge.py --score               # enrich + re-score most recent run

Architecture:
    bridge.py reads from both table sets in bazaar_runs.db, correlates them,
    and writes enrichment data back into Pipeline A tables. Scorer runs
    unchanged afterward, but now has access to:
      - Full offered set with resolved names for every decision
      - Actual day/hour for phase detection
      - PvP win/loss from victory/defeat count diffs
      - Gold/HP at every decision point
      - Board state snapshots
"""

import json
import sqlite3
import argparse
from datetime import datetime, timedelta
from typing import Any, Optional

import db
import card_cache

# ── Schema migrations for enrichment columns ────────────────────────────────

def _ts_diff_seconds(ts_a: str, ts_b: str) -> float:
    """Return absolute difference in seconds between two ISO-8601 timestamps.
    Returns a large number if either timestamp is missing or unparseable."""
    try:
        from datetime import timezone
        a = datetime.fromisoformat(ts_a.replace("Z", "+00:00"))
        b = datetime.fromisoformat(ts_b.replace("Z", "+00:00"))
        return abs((a - b).total_seconds())
    except Exception:
        return 1e18


def ensure_enrichment_schema(conn: Optional[sqlite3.Connection] = None):
    """Compatibility wrapper; DB migrations now own enrichment schema."""
    return db.ensure_schema(conn)


# ═══════════════════════════════════════════════════════════════════════════════
#  STEP 1: Run Correlation
# ═══════════════════════════════════════════════════════════════════════════════

def correlate_run(conn: sqlite3.Connection, run_id: int) -> list[dict]:
    """
    Find api_game_states rows that belong to a given Pipeline A run.

    Correlation strategy:
      1. Get the run's hero and time window from the runs table
      2. Find api_game_states with matching hero within a generous time window
      3. If the run has decision timestamps, narrow the window to
         first_decision - 5min .. last_decision + 5min
      4. Return matched game states ordered by captured_at

    Returns list of api_game_states rows as dicts.
    """
    run = conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
    if not run:
        print(f"[Bridge] Run {run_id} not found.")
        return []

    hero = run["hero"]

    # Get the time window from decisions
    first_dec = conn.execute(
        "SELECT MIN(timestamp) as t FROM decisions WHERE run_id=?", (run_id,)
    ).fetchone()
    last_dec = conn.execute(
        "SELECT MAX(timestamp) as t FROM decisions WHERE run_id=?", (run_id,)
    ).fetchone()

    # Pipeline A timestamps are time-only (HH:MM:SS.fff) from Player.log
    # Pipeline B timestamps are ISO 8601 (full datetime)
    # We need to match by hero + approximate time
    #
    # Observation: mid-run the Mono hook occasionally loses the hero field
    # (records come back with hero=NULL/'') for long stretches, but these
    # NULL-hero states still belong to the Karnok run that Player.log sees.
    # Filtering strictly by hero=? excludes these recent-but-hero-less
    # segments and forces a fallback to a much older segment. Include
    # blank-hero rows as candidates and let segment scoring pick the right
    # one.

    api_states = conn.execute("""
        SELECT * FROM api_game_states
        WHERE hero = ?
           OR hero IS NULL
           OR hero = ''
        ORDER BY id
    """, (hero,)).fetchall()

    if not api_states:
        # Fallback: no hero match and no blank-hero rows — pull everything.
        api_states = conn.execute("""
            SELECT * FROM api_game_states
            ORDER BY id
        """).fetchall()

    if not api_states:
        print(f"[Bridge] No api_game_states found for correlation.")
        return []

    # If run has started_at and ended_at, use those as anchors
    # Otherwise use the full set of api_states
    api_states = [dict(s) for s in api_states]

    # Group api_states into "runs" by detecting day resets or large time gaps
    runs_in_api = _segment_api_runs(api_states)

    # Find the best matching API run segment
    best_segment = _find_best_segment(conn, run_id, run, runs_in_api)

    if best_segment:
        print(f"[Bridge] Correlated run {run_id} ({hero}) with "
              f"{len(best_segment)} api_game_states "
              f"(days {best_segment[0].get('day', '?')}-{best_segment[-1].get('day', '?')})")
    else:
        print(f"[Bridge] Could not correlate run {run_id} with any api_game_states.")

    return best_segment or []


def _parse_captured_at(value: Any) -> Optional[datetime]:
    """Parse a captured_at value that may be ISO 8601 or raw epoch ms.

    The capture path writes some rows as ISO strings and others as epoch-ms
    integers (or numeric strings), so we have to handle both forms. The
    returned datetime is always normalised to naive UTC so callers can
    compare rows regardless of source format.
    """
    from datetime import timezone

    if value is None:
        return None
    dt: Optional[datetime] = None
    if isinstance(value, (int, float)):
        try:
            dt = datetime.fromtimestamp(value / 1000.0, tz=timezone.utc)
        except (ValueError, OSError, OverflowError):
            return None
    elif isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        if stripped.isdigit() or (stripped.startswith("-") and stripped[1:].isdigit()):
            try:
                dt = datetime.fromtimestamp(int(stripped) / 1000.0, tz=timezone.utc)
            except (ValueError, OSError, OverflowError):
                return None
        else:
            try:
                dt = datetime.fromisoformat(stripped.replace("Z", "+00:00"))
            except ValueError:
                return None
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    # Return naive UTC so subtraction between two parsed rows always works.
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def _segment_api_runs(api_states: list[dict]) -> list[list[dict]]:
    """
    Segment api_game_states into likely separate runs.
    A new run starts when:
      - Day resets to a lower value
      - There's a gap > 30 minutes between snapshots
      - Hero changes (ignoring NULL/blank hero, which happens mid-run when the
        Mono hook loses the hero field — we do NOT want to split on those)
    """
    if not api_states:
        return []

    segments = []
    current = [api_states[0]]

    for i in range(1, len(api_states)):
        prev = api_states[i - 1]
        curr = api_states[i]

        new_run = False

        # Day reset (new run started)
        if curr.get("day") is not None and prev.get("day") is not None and curr["day"] < prev["day"]:
            new_run = True

        # Hero changed — only when BOTH sides have real hero values.
        prev_hero = prev.get("hero") or ""
        curr_hero = curr.get("hero") or ""
        if prev_hero and curr_hero and prev_hero != curr_hero:
            new_run = True

        # Large time gap (>30 min) — handles mixed ISO / epoch-ms formats.
        t_prev = _parse_captured_at(prev.get("captured_at"))
        t_curr = _parse_captured_at(curr.get("captured_at"))
        if t_prev and t_curr and (t_curr - t_prev) > timedelta(minutes=30):
            new_run = True

        # End-of-run marker: split only when we cross OUT of the terminal
        # state. Mono often emits the same EndRunVictory / EndRunDefeat
        # snapshot 2-3 times in a row (dedupe/coalesce doesn't always
        # catch these); we must not shatter those into one-row segments.
        if (
            prev.get("run_state") in ("EndRunDefeat", "EndRunVictory")
            and curr.get("run_state") not in ("EndRunDefeat", "EndRunVictory")
        ):
            new_run = True

        if new_run:
            segments.append(current)
            current = [curr]
        else:
            current.append(curr)

    if current:
        segments.append(current)

    return segments


def _find_best_segment(conn: sqlite3.Connection, run_id: int,
                       run: sqlite3.Row, segments: list[list[dict]]) -> Optional[list[dict]]:
    """
    Find the API segment that best matches a Pipeline A run.

    Matching criteria (scored):
      - Hero name match (tolerant of blank-hero rows — see correlate_run)
      - Outcome alignment with terminal run_state
      - Combat count alignment (victories + defeats vs combat_results)
      - Recency: both pipelines insert monotonically, so the N-th most
        recent run should map to the N-th most recent outcome-matching
        segment. We use api_game_states.id as the recency anchor since
        captured_at is stored inconsistently (ISO strings vs epoch ms).
    """
    if not segments:
        return None

    combats = conn.execute(
        "SELECT COUNT(*) as cnt FROM combat_results WHERE run_id=?", (run_id,)
    ).fetchone()
    combat_count = combats["cnt"] if combats else 0

    decisions = conn.execute(
        "SELECT COUNT(*) as cnt FROM decisions WHERE run_id=?", (run_id,)
    ).fetchone()
    decision_count = decisions["cnt"] if decisions else 0

    hero = run["hero"]
    outcome = run["outcome"] if "outcome" in run.keys() else None

    # Build an ordinal map for run recency so we can match the N-th most
    # recent run to the N-th most recent outcome-matching segment. Older
    # runs keep whatever older segment is still unclaimed relative to them.
    outcome_ordinal = _run_outcome_ordinal(conn, run_id, outcome) if outcome else None

    # Precompute the ordering of candidate segments by max(id) within
    # segment — this is our recency anchor, robust to captured_at mixed
    # formats.
    candidate_entries: list[tuple[list[dict], int]] = []
    for seg in segments:
        terminal_indexes = [
            idx for idx, state in enumerate(seg)
            if state.get("run_state") in ("EndRunDefeat", "EndRunVictory")
        ] or [len(seg) - 1]
        for end_idx in terminal_indexes:
            candidate = seg[: end_idx + 1]
            max_id = max(
                (s.get("id") or 0) for s in candidate
            ) if candidate else 0
            candidate_entries.append((candidate, max_id))

    # For the ordinal/recency heuristic, collapse multi-terminal clusters
    # (e.g. EndRunVictory repeated 3x by Mono) into a single canonical
    # candidate — the LAST terminal of the cluster. Without this collapse,
    # three identical terminal writes would consume three ordinal slots
    # and cause older runs to correlate to a "shifted" terminal within
    # the same physical run.
    def _matches_outcome(cand: list[dict]) -> bool:
        last_state = cand[-1]
        ts = last_state.get("run_state")
        if outcome == "victory":
            return ts == "EndRunVictory"
        if outcome == "defeat":
            return ts == "EndRunDefeat"
        return True

    outcome_ranked_pairs = sorted(
        [c for c in candidate_entries if _matches_outcome(c[0])],
        key=lambda c: c[1],
        reverse=True,
    )
    distinct_segments: list[tuple[list[dict], int]] = []
    for cand, mid in outcome_ranked_pairs:
        if distinct_segments:
            prev_cand, prev_mid = distinct_segments[-1]
            # Same segment cluster if they share the same first-state id,
            # which is our proxy for "same underlying run segment, just
            # ending at a different terminal row within it".
            if cand[0].get("id") == prev_cand[0].get("id"):
                # Keep the highest max_id (last terminal) as canonical.
                if mid > prev_mid:
                    distinct_segments[-1] = (cand, mid)
                continue
        distinct_segments.append((cand, mid))
    preferred_segment_id = None
    if outcome_ordinal is not None and 0 <= outcome_ordinal < len(distinct_segments):
        preferred_segment_id = distinct_segments[outcome_ordinal][1]

    best_score = float("-inf")
    best_seg = None

    for candidate, max_id in candidate_entries:
        score = 0.0

        seg_hero = next((s.get("hero") for s in candidate if s.get("hero")), None)
        if seg_hero and hero:
            if seg_hero.lower() == hero.lower():
                score += 10
            elif seg_hero in hero or hero in seg_hero:
                score += 5
        elif hero and not seg_hero:
            # Blank-hero segment — neutral, not penalised. This is the
            # documented "Mono lost the hero mid-run" case.
            score += 0

        last_state = candidate[-1]
        api_combats = (last_state.get("victories") or 0) + (last_state.get("defeats") or 0)
        if combat_count > 0 and api_combats > 0:
            ratio = min(combat_count, api_combats) / max(combat_count, api_combats)
            score += ratio * 8

        if len(candidate) > 3:
            score += 2

        terminal_state = last_state.get("run_state")
        if outcome == "victory" and terminal_state == "EndRunVictory":
            score += 10
        elif outcome == "defeat" and terminal_state == "EndRunDefeat":
            score += 10
        elif terminal_state in ("EndRunVictory", "EndRunDefeat"):
            score -= 4

        # Age bonus — captured_at may be ISO or epoch ms. _parse_captured_at
        # normalises both forms to naive UTC.
        t_last = _parse_captured_at(last_state.get("captured_at"))
        if t_last is not None:
            try:
                from datetime import timezone
                age_hours = (
                    datetime.now(timezone.utc).replace(tzinfo=None) - t_last
                ).total_seconds() / 3600
                if age_hours < 1:
                    score += 3
                elif age_hours < 24:
                    score += 1
            except (ValueError, TypeError):
                pass

        # Run-ordinal recency heuristic: if this segment's max_id matches
        # our predicted recency ordinal slot, award a decisive bonus. This
        # is the fix for "latest run silently correlated to a 2-day-old
        # segment" when the recent segment has blank hero.
        if preferred_segment_id is not None and max_id == preferred_segment_id:
            score += 50

        if score > best_score:
            best_score = score
            best_seg = candidate

    return best_seg


def _run_outcome_ordinal(conn: sqlite3.Connection, run_id: int,
                         outcome: str) -> Optional[int]:
    """Return the zero-based ordinal of `run_id` among runs with the same
    outcome, counting from most recent. The most recent defeat run has
    ordinal 0, the second most recent has 1, etc.

    Runs are assumed to be inserted monotonically (id DESC == time DESC).
    Used as a recency anchor so we can line up Pipeline A runs against
    Pipeline B terminal segments without relying on captured_at, which is
    stored inconsistently across rows (ISO strings vs raw epoch ms).
    """
    if not outcome:
        return None
    try:
        row = conn.execute("""
            SELECT COUNT(*) AS cnt
            FROM runs
            WHERE outcome = ?
              AND id > ?
        """, (outcome, run_id)).fetchone()
    except sqlite3.OperationalError:
        return None
    return int(row["cnt"]) if row is not None else None


# ═══════════════════════════════════════════════════════════════════════════════
#  STEP 2: Decision Enrichment
# ═══════════════════════════════════════════════════════════════════════════════

def enrich_decisions(conn: sqlite3.Connection, run_id: int,
                     api_states: list[dict], dry_run: bool = False) -> int:
    """
    For each decision in the run where day/gold/health/phase_actual are NULL,
    find the nearest api_game_states row by timestamp and fill those columns.

    Returns count of decision rows updated.
    """
    decisions = conn.execute("""
        SELECT id, timestamp FROM decisions
        WHERE run_id = ?
          AND (day IS NULL OR gold IS NULL OR health IS NULL OR phase_actual IS NULL)
        ORDER BY decision_seq
    """, (run_id,)).fetchall()

    if not decisions or not api_states:
        return 0

    enriched = 0
    for d in decisions:
        d_ts = d["timestamp"] or ""
        # Find nearest api_game_state by timestamp (simple linear scan;
        # list is small enough that binary search is not needed).
        best_gs = min(
            api_states,
            key=lambda s: abs(
                _ts_diff_seconds(d_ts, s.get("captured_at") or "")
            ),
        )

        day = best_gs.get("day")
        hour = best_gs.get("hour")
        gold = best_gs.get("gold")
        health = best_gs.get("health")
        health_max = best_gs.get("health_max")
        phase_actual = _day_to_phase(day) if day else None

        if not dry_run:
            conn.execute("""
                UPDATE decisions SET
                    day = COALESCE(day, ?),
                    hour = COALESCE(hour, ?),
                    gold = COALESCE(gold, ?),
                    health = COALESCE(health, ?),
                    health_max = COALESCE(health_max, ?),
                    api_game_state_id = COALESCE(api_game_state_id, ?),
                    phase_actual = COALESCE(phase_actual, ?)
                WHERE id = ?
            """, (day, hour, gold, health, health_max,
                  best_gs["id"], phase_actual,
                  d["id"]))
        enriched += 1

    if not dry_run and enriched:
        conn.commit()

    tag = "DRY" if dry_run else "   "
    print(f"  [{tag}] Filled {enriched} decision row(s) from api_game_states")
    return enriched


def _day_to_phase(day: int) -> str:
    """Convert in-game day number to phase label."""
    if day <= 4:
        return "early"
    elif day <= 7:
        return "early_mid"
    else:
        return "late"


def enrich_offered_names(
    conn: sqlite3.Connection,
    run_id: int,
    api_states: list[dict],
    dry_run: bool = False,
) -> int:
    """
    Backfill decisions.offered_names and decisions.offered_templates using
    api_cards instance→template mappings from correlated api_game_states.

    Only fills rows where offered_names is currently NULL.
    Returns count of decision rows updated.
    """
    if not api_states:
        return 0

    state_ids = [s["id"] for s in api_states if s.get("id")]
    if not state_ids:
        return 0

    placeholders = ",".join("?" * len(state_ids))
    rows = conn.execute(
        f"""
        SELECT DISTINCT instance_id, template_id
        FROM api_cards
        WHERE game_state_id IN ({placeholders})
          AND instance_id IS NOT NULL AND instance_id != ''
          AND template_id IS NOT NULL AND template_id != ''
        """,
        state_ids,
    ).fetchall()

    api_template_map: dict[str, str] = {}
    for r in rows:
        iid = r["instance_id"]
        tid = r["template_id"]
        if iid and tid and iid not in api_template_map:
            if not card_cache.is_suspicious_template_id(tid):
                api_template_map[iid] = tid

    if not api_template_map:
        return 0

    decisions = conn.execute(
        """
        SELECT id, offered
        FROM decisions
        WHERE run_id = ? AND offered_names IS NULL
        ORDER BY decision_seq
        """,
        (run_id,),
    ).fetchall()

    updated = 0
    for d in decisions:
        offered_raw: list[str] = json.loads(d["offered"] or "[]")
        if not offered_raw:
            continue

        names = []
        templates: dict[str, str] = {}
        for iid in offered_raw:
            tid = api_template_map.get(iid, "")
            name = card_cache.resolve_template_id(tid) if tid else ""
            names.append(name or iid)
            if tid:
                templates[iid] = tid

        if not dry_run:
            conn.execute(
                """
                UPDATE decisions
                SET offered_names = ?,
                    offered_templates = COALESCE(offered_templates, ?)
                WHERE id = ?
                """,
                (json.dumps(names), json.dumps(templates) if templates else None, d["id"]),
            )
        updated += 1

    if not dry_run and updated:
        conn.commit()

    tag = "DRY" if dry_run else "   "
    print(f"  [{tag}] Filled offered_names for {updated} decision row(s) from api_cards")
    return updated


# ═══════════════════════════════════════════════════════════════════════════════
#  STEP 3: Combat Enrichment
# ═══════════════════════════════════════════════════════════════════════════════

def enrich_combat(conn: sqlite3.Connection, run_id: int,
                  api_states: list[dict], dry_run: bool = False) -> int:
    """
    Resolve PvP outcomes by diffing consecutive victory/defeat counts
    in api_game_states around combat transitions.

    Returns count of resolved combats.
    """
    combats = conn.execute("""
        SELECT * FROM combat_results
        WHERE run_id = ?
        ORDER BY id
    """, (run_id,)).fetchall()

    if not combats or not api_states:
        return 0

    # Find combat transitions in api_states (state changes into/out of Combat/PVPCombat)
    combat_transitions = []
    for i in range(1, len(api_states)):
        prev_state = api_states[i - 1].get("run_state", "")
        curr_state = api_states[i].get("run_state", "")

        # Detect transition OUT of combat (combat just ended)
        if prev_state in ("Combat", "PVPCombat", "Replay") and curr_state not in ("Combat", "PVPCombat", "Replay"):
            combat_transitions.append({
                "pre_combat": api_states[i - 1],
                "post_combat": api_states[i],
                "combat_type": "pvp" if "PVP" in prev_state else "pve",
                "index": i,
            })

    # Also check action_events for state_change events
    if not combat_transitions:
        # Fall back to diffing victories/defeats across all states
        combat_transitions = _infer_combats_from_vd_diffs(api_states)

    resolved = 0
    transition_cursor = 0

    # Dedup protection — a single combat transition (pre_state.id,
    # post_state.id) must never be attributed to more than one
    # combat_results row. _match_combat_to_transition already advances its
    # cursor but, across retries / partial runs, the same cursor slot could
    # be handed out twice. Track explicitly to make this invariant stick.
    consumed_transition_keys: set[tuple] = set()

    # Also dedup the target: a single combat_results row should never be
    # flipped more than once per bridge invocation, even if the outer
    # transition scan somehow loops. (Belt-and-braces — the row
    # already-resolved guards below should make this unreachable.)
    updated_combat_ids: set[int] = set()

    for combat_row in combats:
        combat_row = dict(combat_row)

        if combat_row.get("pvp_resolved"):
            continue  # already resolved

        if combat_row.get("outcome") in ("opponent_died", "player_died"):
            continue  # already definitively classified by run_state

        if combat_row.get("combat_type") not in ("pvp", "pvp_unknown"):
            # PvE outcomes are already determined upstream by Player.log; nothing to enrich here.
            continue

        if combat_row["id"] in updated_combat_ids:
            continue

        # Find the closest combat transition, skipping any that a previous
        # iteration already consumed.
        while True:
            transition_cursor, best_ct = _match_combat_to_transition(
                combat_row,
                combat_transitions,
                transition_cursor,
            )
            if not best_ct:
                break
            transition_key = (
                best_ct.get("pre_combat", {}).get("id"),
                best_ct.get("post_combat", {}).get("id"),
            )
            if transition_key in consumed_transition_keys:
                # Skip over a duplicate and try the next compatible one.
                continue
            consumed_transition_keys.add(transition_key)
            break

        if not best_ct:
            continue

        pre = best_ct.get("pre_combat", {})
        post = best_ct.get("post_combat", {})

        pre_v = pre.get("victories") or 0
        pre_d = pre.get("defeats") or 0
        post_v = post.get("victories") or 0
        post_d = post.get("defeats") or 0

        outcome = None
        if post_v > pre_v:
            outcome = "opponent_died"  # we won
        elif post_d > pre_d:
            outcome = "player_died"    # we lost
        # else: no change detected, leave as-is

        if outcome and not dry_run:
            # Guard against no-op churn: only write if something would
            # actually change. This keeps repeated bridge runs idempotent
            # and avoids the "duplicate/misattributed entries from bridge
            # enrichment" pattern called out in the roadmap.
            prev_outcome = combat_row.get("outcome")
            prev_type = combat_row.get("combat_type")
            if prev_outcome == outcome and prev_type == "pvp" and combat_row.get("pvp_resolved"):
                continue
            conn.execute("""
                UPDATE combat_results
                SET outcome = ?, combat_type = 'pvp', pvp_resolved = 1
                WHERE id = ? AND pvp_resolved = 0
            """, (outcome, combat_row["id"]))
            updated_combat_ids.add(combat_row["id"])
            resolved += 1
            icon = "✅ WIN" if outcome == "opponent_died" else "❌ LOSS"
            print(f"  [PVP] {icon} (V: {pre_v}→{post_v}, D: {pre_d}→{post_d})")
        elif outcome and dry_run:
            icon = "✅ WIN" if outcome == "opponent_died" else "❌ LOSS"
            print(f"  [DRY PVP] {icon} (V: {pre_v}→{post_v}, D: {pre_d}→{post_d})")
            resolved += 1

    if not dry_run:
        conn.commit()

    return resolved


def _infer_combats_from_vd_diffs(api_states: list[dict]) -> list[dict]:
    """
    Infer combat boundaries from changes in victory/defeat counts.
    """
    transitions = []
    for i in range(1, len(api_states)):
        prev = api_states[i - 1]
        curr = api_states[i]

        prev_v = prev.get("victories") or 0
        prev_d = prev.get("defeats") or 0
        curr_v = curr.get("victories") or 0
        curr_d = curr.get("defeats") or 0

        if curr_v > prev_v or curr_d > prev_d:
            transitions.append({
                "pre_combat": prev,
                "post_combat": curr,
                "combat_type": "pvp",  # V/D changes only happen in PvP
                "index": i,
            })

    return transitions


def _match_combat_to_transition(combat: dict, transitions: list[dict],
                                start_idx: int = 0) -> tuple[int, Optional[dict]]:
    """Match a combat_results row to the next compatible combat transition."""
    if not transitions:
        return start_idx, None

    combat_type = combat.get("combat_type")
    wanted_type = None if combat_type in (None, "pvp_unknown") else combat_type

    for idx in range(start_idx, len(transitions)):
        transition = transitions[idx]
        if wanted_type is None or transition.get("combat_type") == wanted_type:
            return idx + 1, transition

    return len(transitions), None


# ═══════════════════════════════════════════════════════════════════════════════
#  Main orchestrator
# ═══════════════════════════════════════════════════════════════════════════════

def enrich_run(run_id: int, dry_run: bool = False) -> dict:
    """
    Run the full enrichment pipeline for a single run.

    Returns a summary dict:
      {correlated: int, decisions_enriched: int, combats_resolved: int}
    """
    conn = db.get_conn()
    try:
        ensure_enrichment_schema(conn)
        result = {"correlated": 0, "decisions_enriched": 0, "combats_resolved": 0, "names_filled": 0}

        # Step 1: Run Correlation
        api_states = correlate_run(conn, run_id)
        result["correlated"] = len(api_states)

        if not api_states:
            print(f"[Bridge] No API data to correlate for run {run_id}. "
                  f"Run capture_mono.py alongside watcher.py to capture API data.")
            return result

        if not dry_run:
            conn.execute("""
                UPDATE runs
                SET api_time_start = ?, api_time_end = ?
                WHERE id = ?
            """, (
                api_states[0].get("captured_at"),
                api_states[-1].get("captured_at"),
                run_id,
            ))
            conn.commit()

        # Step 2: Decision Enrichment (includes Steps 4 & 5: phase + gold/HP)
        print(f"\n[Bridge] Step 2: Enriching decisions...")
        result["decisions_enriched"] = enrich_decisions(conn, run_id, api_states, dry_run)

        # Step 3: Combat Enrichment
        print(f"\n[Bridge] Step 3: Resolving combat outcomes...")
        result["combats_resolved"] = enrich_combat(conn, run_id, api_states, dry_run)

        # Step 4: Offered Names Backfill
        print(f"\n[Bridge] Step 4: Backfilling offered item names...")
        result["names_filled"] = enrich_offered_names(conn, run_id, api_states, dry_run)

        # Summary
        print(f"\n{'─' * 60}")
        print(f"  Bridge Summary for Run {run_id}")
        print(f"  API snapshots correlated: {result['correlated']}")
        print(f"  Decisions enriched:       {result['decisions_enriched']}")
        print(f"  PvP combats resolved:     {result['combats_resolved']}")
        print(f"  Offered names filled:     {result['names_filled']}")
        mode = "DRY RUN — no DB changes" if dry_run else "Changes written to DB"
        print(f"  Mode: {mode}")
        print(f"{'─' * 60}\n")

        return result
    finally:
        conn.close()


def print_enrichment_report(run_id: int):
    """Print a summary of what enrichment data exists for a run."""
    conn = db.get_conn()
    try:
        decisions = conn.execute("""
            SELECT d.decision_seq, d.decision_type, d.chosen_template,
                   d.offered_names, d.day, d.hour, d.gold, d.health,
                   d.phase_actual, d.api_game_state_id
            FROM decisions d
            WHERE d.run_id = ?
            ORDER BY d.decision_seq
        """, (run_id,)).fetchall()

        run = conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()

        if not run:
            print(f"Run {run_id} not found.")
            return

        print(f"\n{'═' * 70}")
        print(f"  ENRICHMENT REPORT  |  Hero: {run['hero']}  |  Run {run_id}")
        print(f"{'═' * 70}")

        enriched_count = 0
        for d in decisions:
            has_day = d["day"] is not None
            has_gold = d["gold"] is not None
            has_names = d["offered_names"] is not None

            if has_day or has_gold or has_names:
                enriched_count += 1

            item_name = card_cache.resolve_template_id(d["chosen_template"]) if d["chosen_template"] else "(skip/unknown)"

            names_display = ""
            if has_names:
                names = json.loads(d["offered_names"])
                names_display = f"offered=[{', '.join(names[:3])}{'…' if len(names) > 3 else ''}]"

            phase = d["phase_actual"] or "?"
            print(f"  #{d['decision_seq']:>2} {d['decision_type']:<12} {item_name:<25} "
                  f"phase={phase:<9} day={d['day'] or '?'} gold={d['gold'] or '?':>3} "
                  f"hp={d['health'] or '?':>4}  {names_display}")

        enriched_pct = (enriched_count / len(decisions) * 100) if decisions else 0
        print(f"\n  {enriched_count}/{len(decisions)} decisions enriched ({enriched_pct:.0f}%)")
        print(f"{'═' * 70}\n")
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════════════════════
#  CLI
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Bridge: enrich Pipeline A decisions with Pipeline B capture data"
    )
    parser.add_argument("--run-id", type=int, default=None,
                        help="Run ID to enrich (default: most recent)")
    parser.add_argument("--all", action="store_true",
                        help="Enrich all runs in the database")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview enrichment without writing to DB")
    parser.add_argument("--report", action="store_true",
                        help="Print enrichment report for a run")
    parser.add_argument("--score", action="store_true",
                        help="Re-score the run after enrichment")
    args = parser.parse_args()

    db.init_db()

    conn = db.get_conn()
    try:
        ensure_enrichment_schema(conn)

        # Resolve run ID
        if args.run_id is None and not args.all:
            row = conn.execute("SELECT id FROM runs ORDER BY id DESC LIMIT 1").fetchone()
            if not row:
                print("No runs found in database.")
                return
            run_id = row["id"]
        else:
            run_id = args.run_id

        if args.report:
            print_enrichment_report(run_id or 1)
            return

        if args.all:
            runs = conn.execute("SELECT id FROM runs ORDER BY id").fetchall()
            run_ids = [r["id"] for r in runs]
        else:
            run_ids = [run_id]

    finally:
        conn.close()

    for rid in run_ids:
        result = enrich_run(rid, dry_run=args.dry_run)

        if args.score and result["decisions_enriched"] > 0 and not args.dry_run:
            print("[Bridge] Re-scoring with enriched data...")
            import scorer
            scored = scorer.score_run(rid)
            scorer.print_report(scored, rid)

if __name__ == "__main__":
    main()
