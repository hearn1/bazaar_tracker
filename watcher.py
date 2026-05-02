"""
watcher.py — Tails Player.log in real time and drives the full pipeline.

Usage:
    python watcher.py                          # auto-detect log path
    python watcher.py --log "C:/path/to/Player.log"
    python watcher.py --parse-only             # parse existing log and exit (no watching)

The log path auto-detection looks at the path printed in Player.log itself:
    Persistent Data Path: C:/Users/Matt/AppData/LocalLow/Tempo Storm/The Bazaar
"""

import argparse
import time
import os
from pathlib import Path
from typing import Optional

import db
import parser as log_parser
from run_state import RunState

# ── Default log location (Windows) ───────────────────────────────────────────
# Matches what's in YOUR Player.log:
#   Persistent Data Path: C:/Users/Matt/AppData/LocalLow/Tempo Storm/The Bazaar
DEFAULT_LOG_PATHS = [
    Path(os.environ.get("LOCALAPPDATA", ""), "..", "LocalLow", "Tempo Storm", "The Bazaar", "Player.log"),
    Path(os.environ.get("USERPROFILE", "C:/Users/User"), "AppData", "LocalLow", "Tempo Storm", "The Bazaar", "Player.log"),
]


def find_log_path() -> Path:
    for p in DEFAULT_LOG_PATHS:
        resolved = p.resolve()
        if resolved.exists():
            return resolved
    # Last resort: current directory
    local = Path("Player.log")
    if local.exists():
        return local
    return DEFAULT_LOG_PATHS[1].resolve()  # return the expected path even if missing


def parse_existing(log_path: Path, state: RunState, verbose_runs: int = 3) -> int:
    """
    Parse the log file from the beginning.
    Silently replays old runs (DB dedup guards prevent re-inserting),
    prints output only for the most recent `verbose_runs` runs.
    Returns the file byte position after reading.
    """
    print(f"[Watcher] Scanning existing log: {log_path}")
    if not log_path.exists():
        print(f"[Watcher] ERROR: Log file not found at {log_path}")
        return 0

    import re, builtins
    session_re = re.compile(r'Captured session id: ([a-f0-9\-]+)')

    # First pass: find all session IDs in order
    sessions_in_log = []
    with open(log_path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            m = session_re.search(line)
            if m:
                sid = m.group(1)
                if not sessions_in_log or sessions_in_log[-1] != sid:
                    sessions_in_log.append(sid)

    total_runs = len(sessions_in_log)
    verbose_session_ids = set(sessions_in_log[-verbose_runs:]) if sessions_in_log else set()

    if total_runs > verbose_runs:
        print(f"[Watcher] {total_runs} runs in log — silently replaying {total_runs - verbose_runs} old run(s), printing last {verbose_runs}.")
    else:
        print(f"[Watcher] {total_runs} run(s) in log.")

    _real_print = builtins.print
    pos = 0
    previous_emit = state.emit_completion_callbacks
    state.emit_completion_callbacks = False
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                m = session_re.search(line)
                if m:
                    sid = m.group(1)
                    if sid in verbose_session_ids:
                        builtins.print = _real_print
                    else:
                        builtins.print = lambda *a, **kw: None
                event = log_parser.parse_line(line)
                if event:
                    state.process(event)
            pos = f.tell()
    finally:
        builtins.print = _real_print
        state.emit_completion_callbacks = previous_emit

    state.print_summary()
    print(f"[Watcher] Replay complete. Watching for new activity...")
    return pos


def build_run_complete_handler():
    def _handle_run_complete(info: dict):
        run_id = info["run_id"]
        hero = info.get("hero") or "Unknown"
        print(f"\n[Watcher] Run {run_id} finished for {hero}.")
        db.flush()
        print("[Watcher] Run closed; pending writes flushed.")

    return _handle_run_complete


def tail_log(log_path: Path, state: RunState, start_pos: int = 0):
    """
    Tail the log file in real time — reads new lines as the game writes them.
    Works on Windows (unlike `tail -f`).
    start_pos: byte offset to start from (pass result of parse_existing to avoid re-reading).
    """
    print(f"[Watcher] Watching: {log_path}")
    print(f"[Watcher] Waiting for game activity... (Ctrl+C to stop)\n")

    if not log_path.exists():
        print(f"[Watcher] Log file not found yet at {log_path}")
        print(f"[Watcher] Will start watching when the game creates it...")

    last_size = start_pos

    while True:
        try:
            if not log_path.exists():
                time.sleep(1)
                continue

            current_size = log_path.stat().st_size

            # File was truncated/replaced (new game session)
            if current_size < last_size:
                print("[Watcher] Log file reset — new game session detected")
                last_size = 0

            if current_size > last_size:
                with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                    f.seek(last_size)
                    new_content = f.read()
                    last_size = f.tell()

                for line in new_content.splitlines():
                    event = log_parser.parse_line(line)
                    if event:
                        state.process(event)
                db.flush_if_stale(max_age_secs=2.0)

            time.sleep(0.3)  # poll every 300ms — responsive but not hammering disk

        except KeyboardInterrupt:
            print("\n[Watcher] Stopped.")
            try:
                state.print_summary()
            except KeyboardInterrupt:
                print("[Watcher] Summary interrupted.")
            break
        except Exception as e:
            print(f"[Watcher] Error: {e}")
            time.sleep(1)


def run_watcher(log_path: Optional[Path] = None, parse_only: bool = False):
    # Init DB
    db.init_db()
    db.start_writer()

    # Auto-populate card cache if empty (only needs to run once, or after patches)
    import card_cache as cc
    if not cc.is_cache_populated():
        print("[Watcher] Card cache is empty — fetching from playthebazaar.com...")
        cc.refresh_cache()
    else:
        conn = db.get_conn()
        try:
            count = conn.execute("SELECT COUNT(*) FROM card_cache").fetchone()[0]
        finally:
            conn.close()
        print(f"[Watcher] Card cache ready ({count} entries). Run 'python card_cache.py --force' after a patch.")

    # Resolve log path
    log_path = log_path.resolve() if log_path else find_log_path()
    print(f"[Watcher] Log path: {log_path}")
    if not log_path.exists():
        print(f"[Watcher] WARNING: File does not exist yet.")

    # Init run state machine
    state = RunState(
        str(log_path),
        on_run_complete=build_run_complete_handler(),
    )

    if parse_only:
        parse_existing(log_path, state)
    else:
        # First, catch up on existing content (in case game is already running)
        start_pos = 0
        if log_path.exists():
            print("[Watcher] Catching up on existing log content...")
            start_pos = parse_existing(log_path, state)
            print("[Watcher] Now watching for new events...\n")
        tail_log(log_path, state, start_pos=start_pos)


def main():
    parser = argparse.ArgumentParser(
        description="Bazaar Run Tracker — watches Player.log and records decisions to SQLite"
    )
    parser.add_argument(
        "--log",
        type=str,
        default=None,
        help="Path to Player.log (auto-detected if not specified)"
    )
    parser.add_argument(
        "--parse-only",
        action="store_true",
        help="Parse the existing log file and exit (don't watch for new events)"
    )
    args = parser.parse_args()

    try:
        run_watcher(
            log_path=Path(args.log) if args.log else None,
            parse_only=args.parse_only,
        )
    finally:
        db.close_shared_conn()


if __name__ == "__main__":
    main()
