"""
parser.py — Regex patterns and event extraction for The Bazaar's Player.log.

All patterns verified against a real Player.log session.
Returns structured dicts that the watcher feeds into the DB.
"""

import re
from typing import Optional

# ── Timestamp ────────────────────────────────────────────────────────────────
RE_TIMESTAMP = re.compile(r'\[(\d{2}:\d{2}:\d{2}\.\d+)\]')

# ── Run lifecycle ─────────────────────────────────────────────────────────────
RE_SESSION_ID   = re.compile(r'Captured session id:\s*([0-9a-f\-]+)', re.IGNORECASE)
RE_ACCOUNT_ID   = re.compile(
    r'AccountId:\s*([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})',
    re.IGNORECASE,
)
RE_HERO         = re.compile(r'RunConfigurationCache: Changing EHero to (\w+)')
RE_RUN_START    = re.compile(r'Starting new run\.\.\.')
RE_RUN_INIT_OK  = re.compile(r'Run initialization complete\.')

# ── App state transitions ─────────────────────────────────────────────────────
RE_STATE_CHANGE = re.compile(r'AppState.*?State changed from \[(\w+)\] to \[(\w+)\]')

# ── Cards offered / disposed ──────────────────────────────────────────────────
RE_CARDS_DEALT    = re.compile(r'Cards Dealt: (.+)')
RE_CARDS_DISPOSED = re.compile(r'Cards Disposed:\s*(.*)')
RE_CARDS_SPAWNED  = re.compile(r'Cards Spawned: (.+)')

# ── Card purchased ────────────────────────────────────────────────────────────
RE_CARD_PURCHASED = re.compile(
    r'Card Purchased:\s*InstanceId:\s*(\S+)\s*-\s*TemplateId:?\s*([a-f0-9\-]+)\s*-\s*Target:\s*(\S+)\s*-\s*Section:?\s*(\w+)',
    re.IGNORECASE,
)

# ── Skill selected ────────────────────────────────────────────────────────────
RE_SKILL_SELECTED = re.compile(r'Selected skill (\S+) to socket (\S+)')

# ── Card transformed ─────────────────────────────────────────────────────────
RE_CARD_TRANSFORMED = re.compile(r'Transformed:\s*(\S+)\s+into:\s*(\S+)')

# ── Item moved on board ───────────────────────────────────────────────────────
# Two distinct "Successfully moved card" line shapes are emitted by CardOperationUtility:
#
#   Short form (socket-only, player-initiated moves):
#     [ts] [CardOperationUtility] Successfully moved card itm_Di-1CYk to Socket_6
#
#   Verbose form (destination zone + socket, often emitted on deals/post-buy placement
#   or when the server broadcasts the authoritative destination):
#     [ts] [CardOperationUtility] Successfully moved card to: [itm_fKMvIn3 [Player] [Hand] [Socket_5] [Small]
#     [ts] [CardOperationUtility] Successfully moved card to: [itm_Di-1CYk [Player] [Stash] [Socket_0] [Small]
#
# The verbose form carries the destination zone (Hand/Stash) that lets us attribute
# to_category reliably without substring-matching the socket name. The short form
# only gives the raw Socket_N identifier; destination zone has to be inferred from
# in-memory state because the log line itself does not name it.
RE_CARD_MOVED_SOCKET = re.compile(r'Successfully moved card (\S+) to (\S+)')
RE_CARD_MOVED_VERBOSE = re.compile(
    r'Successfully moved card to:\s*\[(\S+)\s*'
    r'\[(\w+)\]\s*'   # side, e.g. Player / Opponent
    r'\[(\w+)\]\s*'   # zone, e.g. Hand / Stash / Board
    r'\[(\w+)\]'      # destination socket, e.g. Socket_5
)
RE_INVENTORY_ITEM_REMOVED = re.compile(r"Successfully removed item (\S+) from player's inventory")
RE_CARD_SOLD = re.compile(r'Sold Card (\S+) for (\d+) gold\.')

# ── Combat ────────────────────────────────────────────────────────────────────
RE_COMBAT_START    = re.compile(r'Combat started!')
RE_COMBAT_DIED     = re.compile(r'Combatant died')
RE_REROLL          = re.compile(r'RerollCommand')
RE_COMBAT_COMPLETE = re.compile(r'Combat simulation completed in ([\d.]+)s')

# ── Run end ───────────────────────────────────────────────────────────────────
RE_RUN_DEFEAT  = re.compile(r'EndRunDefeatState')
RE_RUN_VICTORY = re.compile(r'EndRunVictoryState')

# ── Commands sent to server ───────────────────────────────────────────────────
RE_COMMAND_SENT = re.compile(r'Sending (\w+Command) to /(\w+)')


def extract_timestamp(line: str) -> Optional[str]:
    m = RE_TIMESTAMP.search(line)
    return m.group(1) if m else None


def parse_id_list(raw: str) -> list[str]:
    """
    Parse a pipe-separated list of instance IDs from Cards Dealt / Disposed lines.
    Input:  "[enc_GItEozl [Medium] | [enc_gh4PSSs [Medium] | "
    Output: ["enc_GItEozl", "enc_gh4PSSs"]
    """
    ids = []
    for token in raw.split('|'):
        token = token.strip().lstrip('[')
        parts = token.split()
        if parts:
            candidate = parts[0].strip()
            if candidate:
                ids.append(candidate)
    return ids


def parse_line(line: str) -> Optional[dict]:
    """
    Parse a single log line into a structured event dict, or None if not relevant.
    """
    ts = extract_timestamp(line)

    if RE_RUN_START.search(line):
        return {"event": "run_start", "ts": ts}

    if RE_RUN_INIT_OK.search(line):
        return {"event": "run_init_complete", "ts": ts}

    m = RE_SESSION_ID.search(line)
    if m:
        return {"event": "session_id", "ts": ts, "session_id": m.group(1)}

    m = RE_ACCOUNT_ID.search(line)
    if m:
        return {"event": "account_id", "ts": ts, "account_id": m.group(1)}

    m = RE_HERO.search(line)
    if m:
        return {"event": "hero", "ts": ts, "hero": m.group(1)}

    m = RE_STATE_CHANGE.search(line)
    if m:
        return {"event": "state_change", "ts": ts,
                "from_state": m.group(1), "to_state": m.group(2)}

    m = RE_CARDS_DEALT.search(line)
    if m:
        return {"event": "cards_dealt", "ts": ts, "instance_ids": parse_id_list(m.group(1))}

    m = RE_CARDS_SPAWNED.search(line)
    if m:
        return {"event": "cards_spawned", "ts": ts, "instance_ids": parse_id_list(m.group(1))}

    m = RE_CARD_PURCHASED.search(line)
    if m:
        return {
            "event": "card_purchased", "ts": ts,
            "instance_id": m.group(1), "template_id": m.group(2),
            "target_socket": m.group(3), "section": m.group(4),
        }

    m = RE_CARDS_DISPOSED.search(line)
    if m:
        return {"event": "cards_disposed", "ts": ts, "instance_ids": parse_id_list(m.group(1))}

    m = RE_INVENTORY_ITEM_REMOVED.search(line)
    if m:
        return {"event": "inventory_item_removed", "ts": ts, "instance_id": m.group(1)}

    m = RE_CARD_SOLD.search(line)
    if m:
        return {
            "event": "card_sold",
            "ts": ts,
            "instance_id": m.group(1),
            "gold_earned": int(m.group(2)),
        }

    m = RE_SKILL_SELECTED.search(line)
    if m:
        return {"event": "skill_selected", "ts": ts,
                "instance_id": m.group(1), "socket": m.group(2)}

    m = RE_CARD_TRANSFORMED.search(line)
    if m:
        return {"event": "card_transformed", "ts": ts,
                "from_instance_id": m.group(1), "to_instance_id": m.group(2)}

    # Verbose form first — it carries destination zone info that the short form
    # cannot provide. The short-form regex would otherwise also match these lines
    # (capturing "to:" as the instance_id), so order matters here.
    m = RE_CARD_MOVED_VERBOSE.search(line)
    if m:
        return {
            "event": "card_moved",
            "ts": ts,
            "instance_id": m.group(1),
            "to_side": m.group(2),
            "to_zone": m.group(3),
            "to_socket": m.group(4),
        }

    m = RE_CARD_MOVED_SOCKET.search(line)
    if m and 'Successfully moved card' in line:
        return {"event": "card_moved", "ts": ts,
                "instance_id": m.group(1), "to_socket": m.group(2)}

    if RE_COMBAT_START.search(line):
        return {"event": "combat_start", "ts": ts}

    if RE_RUN_DEFEAT.search(line):
        return {"event": "run_defeat", "ts": ts}

    if RE_RUN_VICTORY.search(line):
        return {"event": "run_victory", "ts": ts}

    if RE_COMBAT_DIED.search(line):
        return {"event": "combatant_died", "ts": ts}

    m = RE_COMBAT_COMPLETE.search(line)
    if m:
        return {"event": "combat_complete", "ts": ts,
                "duration_secs": float(m.group(1))}

    if RE_REROLL.search(line) and "Sending" in line:
        return {"event": "reroll", "ts": ts}

    m = RE_COMMAND_SENT.search(line)
    if m:
        return {"event": "command_sent", "ts": ts,
                "command": m.group(1), "endpoint": m.group(2)}

    return None
