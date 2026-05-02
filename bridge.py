"""
bridge.py — Manual diagnostics for comparing Player.log decisions with Mono capture.

Pipeline A (Player.log):  runs, decisions, combat_results
Pipeline B (Mono capture): api_game_states, api_cards

RunState now attaches live Mono context before each decision is scored.
This module is kept as a manual diagnostic/reporting tool and does not mutate
decisions, combats, runs, score_label, or score_notes.

Usage:
    python bridge.py                       # inspect most recent run correlation
    python bridge.py --run-id 3            # inspect specific run
    python bridge.py --all                 # inspect all runs
    python bridge.py --report              # print stored live context coverage

Architecture:
    bridge.py reads from both table sets in bazaar_runs.db and reports
    correlation candidates. Normal tracker flow never calls this module.
"""

import json
import sqlite3
import argparse
from datetime import datetime, timedelta
from typing import Any, Optional

import db
import card_cache

def ensure_enrichment_schema(conn: Optional[sqlite3.Connection] = None):
    """Compatibility wrapper; DB migrations own live-context schema."""
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

# ═══════════════════════════════════════════════════════════════════════════════
#  STEP 3: Combat Enrichment
# ═══════════════════════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════════════════════
#  Main orchestrator
# ═══════════════════════════════════════════════════════════════════════════════

def enrich_run(run_id: int, dry_run: bool = False) -> dict:
    """
    Manual-only compatibility wrapper.

    Older versions used this function to mutate runs/decisions/combats after a
    run ended. Normal scoring is live-only now, so this function only reports
    correlation candidates and leaves stored scores/context untouched.
    """
    conn = db.get_conn()
    try:
        ensure_enrichment_schema(conn)
        result = {"correlated": 0}

        # Step 1: Run Correlation
        api_states = correlate_run(conn, run_id)
        result["correlated"] = len(api_states)

        if not api_states:
            print(f"[Bridge] No API data to correlate for run {run_id}. "
                  f"Run capture_mono.py alongside watcher.py to capture API data.")
            return result

        # Summary
        print(f"\n{'─' * 60}")
        print(f"  Bridge Diagnostic Summary for Run {run_id}")
        print(f"  API snapshots correlated: {result['correlated']}")
        print(f"  Mode: manual diagnostics only — no DB changes")
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
        description="Bridge diagnostics: compare Player.log runs with Mono capture without writing"
    )
    parser.add_argument("--run-id", type=int, default=None,
                        help="Run ID to inspect (default: most recent)")
    parser.add_argument("--all", action="store_true",
                        help="Inspect all runs in the database")
    parser.add_argument("--dry-run", action="store_true",
                        help="Compatibility no-op; bridge diagnostics never write")
    parser.add_argument("--report", action="store_true",
                        help="Print enrichment report for a run")
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
        enrich_run(rid, dry_run=True)

if __name__ == "__main__":
    main()
