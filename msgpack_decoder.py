# -*- coding: utf-8 -*-
"""
msgpack_decoder.py - Decodes Bazaar API responses using the full schema
discovered via ILSpy decompilation of BazaarGameShared.dll.

Usage:
    python msgpack_decoder.py --test          # decode sample payload
    python msgpack_decoder.py --test --debug  # show raw structure too
    python msgpack_decoder.py --b64 "<str>"   # decode a specific payload
"""

import base64
import json
import sys
from typing import Optional

import msgpack

# -- Import enums from schema (or define inline as fallback) -------------------
try:
    from schema import (
        E_RUN_STATE, E_HERO, E_PLAYER_ATTRIBUTE, E_CARD_TYPE,
        E_CARD_SIZE, E_TIER, E_COMBATANT, E_INVENTORY_SECTION,
    )
except ImportError:
    E_RUN_STATE = {0:"Choice",1:"Combat",2:"Encounter",3:"EndRunDefeat",
                   4:"EndRunVictory",5:"LevelUp",6:"Loot",7:"NewRun",
                   8:"Pedestal",9:"PVPCombat",10:"Shutdown"}
    E_HERO = {0:"Common",1:"Pygmalien",2:"Vanessa",3:"Dooley",4:"Jules",5:"Stelle",6:"Mak",7:"Karnok"}
    E_PLAYER_ATTRIBUTE = {0:"Burn",1:"CritChance",2:"DamageCrit",3:"Experience",
                          4:"Gold",5:"Income",6:"Joy",8:"JoyCrit",9:"Prestige",
                          10:"Health",11:"HealthMax",12:"HealthRegen",13:"HealAmount",
                          14:"HealCrit",15:"Level",16:"Poison",17:"RerollCostModifier",
                          19:"Shield",21:"ShieldCrit",22:"FlatDamageReduction",
                          23:"PercentDamageReduction",24:"Custom_0",25:"Custom_1",
                          26:"Custom_2",27:"Custom_3",28:"Custom_4",29:"Custom_5",
                          30:"Custom_6",31:"Custom_7",32:"Custom_8",33:"Custom_9",
                          34:"Rage",35:"RageMax",36:"Enraged",37:"EnragedDuration",
                          38:"EnragedDurationMax"}
    E_CARD_TYPE = {0:"Item",1:"Skill",2:"Companion",3:"SocketEffect",4:"Encounter"}
    E_CARD_SIZE = {0:"Small",1:"Medium",2:"Large"}
    E_TIER = {0:"Bronze",1:"Silver",2:"Gold",3:"Diamond",4:"Legendary"}
    E_COMBATANT = {0:"Player",1:"Opponent"}
    E_INVENTORY_SECTION = {0:"Hand",1:"Stash"}


# -- msgpack decoding ----------------------------------------------------------

def _decode_bytes(obj):
    """Recursively decode bytes to strings where possible."""
    if isinstance(obj, bytes):
        try:
            return obj.decode("utf-8")
        except Exception:
            pass
        try:
            ascii_part = obj.split(b"\x00")[0]
            decoded = ascii_part.decode("ascii").strip()
            decoded = decoded.rstrip("".join(chr(i) for i in range(32)))
            if len(decoded) >= 4 and "_" in decoded:
                return decoded
        except Exception:
            pass
        return obj.hex()
    elif isinstance(obj, list):
        return [_decode_bytes(i) for i in obj]
    elif isinstance(obj, dict):
        return {str(_decode_bytes(k)): _decode_bytes(v) for k, v in obj.items()}
    return obj


def decode_raw(data: bytes) -> Optional[list]:
    """Decode raw msgpack bytes into a list of top-level Python objects."""
    try:
        def pairs_hook(pairs):
            return [["__map__", [[k, v] for k, v in pairs]]]

        unpacker = msgpack.Unpacker(
            raw=True,
            strict_map_key=False,
            object_pairs_hook=pairs_hook,
        )
        unpacker.feed(data)
        objects = []
        for obj in unpacker:
            objects.append(_decode_bytes(obj))
        return objects
    except Exception as e:
        print(f"[Decoder] Raw decode failed: {e}")
        return None


def debug_structure(obj, indent=0, max_depth=4):
    """Print the type/shape of a decoded object tree for debugging."""
    prefix = "  " * indent
    if indent > max_depth:
        print(f"{prefix}...")
        return
    if isinstance(obj, list):
        print(f"{prefix}list[{len(obj)}]")
        for i, item in enumerate(obj[:6]):
            print(f"{prefix}  [{i}]:", end=" ")
            if isinstance(item, list):
                print(f"list[{len(item)}]")
                if indent < max_depth - 1:
                    for j, sub in enumerate(item[:4]):
                        print(f"{prefix}    [{j}]:", end=" ")
                        if isinstance(sub, list):
                            print(f"list[{len(sub)}]")
                        else:
                            print(repr(sub)[:80])
            else:
                print(repr(item)[:80])
        if len(obj) > 6:
            print(f"{prefix}  ... ({len(obj) - 6} more)")
    else:
        print(f"{prefix}{type(obj).__name__}: {repr(obj)[:80]}")


# -- Schema-aware parsing ------------------------------------------------------

def _map_to_dict(obj):
    """Convert a __map__ encoded object to a plain dict."""
    if isinstance(obj, list) and obj and obj[0] == "__map__":
        return {k: v for k, v in obj[1]}
    return {}


def parse_card(arr) -> dict:
    """
    Parse a CardSnapshotDTO.
    Source: BazaarGameShared.Infra.Messages.CardSnapshotDTO [Key(0)..[Key(12)]
    """
    if not isinstance(arr, list):
        return {}

    card = {}

    def get(i): return arr[i] if len(arr) > i else None

    # [0] InstanceId (string)
    if get(0): card["instance_id"] = get(0)

    # [1] TemplateId (Guid string)
    if get(1): card["template_id"] = get(1)

    # [2] Attributes (map: ECardAttributeType -> int)
    attrs_raw = get(2)
    if attrs_raw:
        attrs = _map_to_dict(attrs_raw)
        if attrs:
            card["card_attributes"] = {
                E_PLAYER_ATTRIBUTE.get(k, f"attr_{k}"): v
                for k, v in attrs.items()
            }

    # [3] Enchantment (EEnchantmentType? int)
    if get(3) is not None: card["enchantment"] = get(3)

    # [7] Tier (ETier int)
    tier = get(7)
    if tier is not None: card["tier"] = E_TIER.get(tier, tier)

    # [8] Type (ECardType int)
    ctype = get(8)
    if ctype is not None: card["type"] = E_CARD_TYPE.get(ctype, ctype)

    # [9] Size (ECardSize int)
    size = get(9)
    if size is not None: card["size"] = E_CARD_SIZE.get(size, size)

    # [10] Owner (ECombatantId? int) — null = offered in shop
    if len(arr) > 10:
        owner = arr[10]
        card["owner"] = None if owner is None else E_COMBATANT.get(owner, owner)

    # [11] Socket (EContainerSocketId? int)
    socket = get(11)
    if socket is not None: card["socket"] = socket

    # [12] Section (EInventorySection? int) — null = skill
    if len(arr) > 12:
        section = arr[12]
        card["section"] = None if section is None else E_INVENTORY_SECTION.get(section, section)

    return card


def parse_player(arr) -> dict:
    """
    Parse a PlayerSnapshotDTO.
    Source: BazaarGameShared.Infra.Messages.PlayerSnapshotDTO [Key(0)..[Key(2)]
    """
    if not isinstance(arr, list):
        return {}

    player = {}
    def get(i): return arr[i] if len(arr) > i else None

    # [0] Hero (EHero int)
    hero = get(0)
    if hero is not None: player["hero"] = E_HERO.get(hero, hero)

    # [1] Attributes (map: EPlayerAttributeType -> int)
    attrs_raw = get(1)
    if attrs_raw:
        attrs = _map_to_dict(attrs_raw)
        for k, v in attrs.items():
            name = E_PLAYER_ATTRIBUTE.get(k, f"attr_{k}")
            player[name] = v

    # [2] UnlockedSlots (ushort)
    slots = get(2)
    if slots is not None: player["unlocked_slots"] = slots

    return player


def parse_run(arr) -> dict:
    """
    Parse a RunSnapshotDTO.
    Source: BazaarGameShared.Infra.Messages.RunSnapshotDTO [Key(0)..[Key(6)]
    """
    if not isinstance(arr, list):
        return {}

    def get(i): return arr[i] if len(arr) > i else None

    return {
        k: v for k, v in {
            "game_mode_id":   get(0),
            "day":            get(1),
            "hour":           get(2),
            "victories":      get(3),
            "defeats":        get(4),
            "visited_fates":  get(5),
            "data_version":   get(6),
        }.items() if v is not None
    }


def parse_run_state(arr) -> dict:
    """
    Parse a RunStateSnapshotDTO.
    Source: BazaarGameShared.Infra.Messages.RunStateSnapshotDTO [Key(0)..[Key(6)]
    """
    if not isinstance(arr, list):
        return {}

    def get(i): return arr[i] if len(arr) > i else None

    state = {}

    # [0] ERunState
    s = get(0)
    if s is not None: state["state"] = E_RUN_STATE.get(s, f"Unknown({s})")

    # [1] CurrentEncounterId
    if get(1): state["current_encounter_id"] = get(1)

    # [2] Board
    if get(2): state["board"] = get(2)

    # [3] RerollCost
    if get(3) is not None: state["reroll_cost"] = get(3)

    # [4] RerollsRemaining
    if get(4) is not None: state["rerolls_remaining"] = get(4)

    # [5] SelectionSet (List<string> offered instance IDs)
    sel = get(5)
    if sel and isinstance(sel, list): state["selection_set"] = sel

    return state


def _is_run_snapshot(arr) -> bool:
    """Heuristic check: does this look like a RunSnapshotDTO?"""
    if not isinstance(arr, list) or len(arr) < 4:
        return False
    # Day and Hour should be small non-negative ints
    day = arr[1] if len(arr) > 1 else None
    hour = arr[2] if len(arr) > 2 else None
    victories = arr[3] if len(arr) > 3 else None
    return (isinstance(day, int) and 0 <= day <= 100 and
            isinstance(hour, int) and 0 <= hour <= 24 and
            (victories is None or isinstance(victories, int)))


def _is_game_state_snapshot(arr) -> bool:
    """
    Heuristic check: does this look like a GameStateSnapshotDTO [4 elements]?
    [0] RunSnapshotDTO (list), [1] RunStateSnapshotDTO (list/None),
    [2] PlayerSnapshotDTO (list), [3] Cards (list)
    """
    if not isinstance(arr, list) or len(arr) != 4:
        return False
    run = arr[0]
    state = arr[1]
    player = arr[2]
    cards = arr[3]
    return (isinstance(run, list) and
            (state is None or isinstance(state, list)) and
            isinstance(player, list) and
            isinstance(cards, list) and
            _is_run_snapshot(run))


def find_game_state_snapshot(obj, depth=0) -> Optional[list]:
    """
    Recursively search for a GameStateSnapshotDTO array in any structure.
    Returns the 4-element snapshot array if found, else None.
    """
    if depth > 12:
        return None
    if _is_game_state_snapshot(obj):
        return obj
    if isinstance(obj, list):
        for item in obj:
            result = find_game_state_snapshot(item, depth + 1)
            if result is not None:
                return result
    return None


def parse_game_state(raw_objects: list, debug: bool = False) -> Optional[dict]:
    """
    Parse a full decoded msgpack response into a structured game state dict.

    Handles both clean nested responses and fragmented streams by
    recursively searching for the GameStateSnapshotDTO signature.
    """
    if not raw_objects:
        return None

    if debug:
        print(f"\n[Debug] Top-level objects: {len(raw_objects)}")
        debug_structure(raw_objects)

    snapshot_arr = find_game_state_snapshot(raw_objects)

    if snapshot_arr is None:
        print(f"[Decoder] GameStateSnapshotDTO not found in response.")
        print(f"[Decoder] {len(raw_objects)} top-level objects decoded.")
        print(f"[Decoder] Run with --debug to inspect the raw structure.")
        return None

    run_arr    = snapshot_arr[0]
    state_arr  = snapshot_arr[1]
    player_arr = snapshot_arr[2]
    cards_raw  = snapshot_arr[3]

    run    = parse_run(run_arr)
    state  = parse_run_state(state_arr) if state_arr else {}
    player = parse_player(player_arr)

    # Parse all cards
    cards = []
    if isinstance(cards_raw, list):
        for card_arr in cards_raw:
            card = parse_card(card_arr)
            if card:
                cards.append(card)

    # Categorize cards using GameStateSnapshotDTO computed property logic:
    # GetPlayerHandCards  : Owner==Player, Section==Hand
    # GetPlayerStashCards : Owner==Player, Section==Stash
    # GetPlayerSkillsCards: Owner==Player, Section==null
    # Offered             : Owner==null, Type!=Encounter
    offered       = [c for c in cards if c.get("owner") is None
                     and c.get("type") != "Encounter"]
    player_board  = [c for c in cards if c.get("owner") == "Player"
                     and c.get("section") == "Hand"]
    player_stash  = [c for c in cards if c.get("owner") == "Player"
                     and c.get("section") == "Stash"]
    player_skills = [c for c in cards if c.get("owner") == "Player"
                     and c.get("section") is None
                     and c.get("type") == "Skill"]
    opponent_board = [c for c in cards if c.get("owner") == "Opponent"]

    return {
        "run":            run,
        "state":          state,
        "player":         player,
        "offered":        offered,
        "player_board":   player_board,
        "player_stash":   player_stash,
        "player_skills":  player_skills,
        "opponent_board": opponent_board,
        "all_cards":      cards,
    }


# -- Human-readable report -----------------------------------------------------

def print_game_state(gs: dict):
    """Print a human-readable summary of the parsed game state."""
    if not gs:
        return

    run    = gs.get("run", {})
    state  = gs.get("state", {})
    player = gs.get("player", {})

    print("\n" + "=" * 60)
    print(f"  STATE  : {state.get('state', '?')}")
    print(f"  Hero   : {player.get('hero', '?')}  "
          f"Day {run.get('day', '?')}  Hour {run.get('hour', '?')}")
    print(f"  PvP    : {run.get('victories', 0)}W / {run.get('defeats', 0)}L")
    print(f"  Gold   : {player.get('Gold', '?')}")
    print(f"  HP     : {player.get('Health', '?')} / {player.get('HealthMax', '?')}")

    if state.get("reroll_cost") is not None:
        print(f"  Reroll : {state['reroll_cost']} gold  "
              f"({state.get('rerolls_remaining', '?')} remaining)")

    offered = gs.get("offered", [])
    if offered:
        print(f"\n  OFFERED ({len(offered)}):")
        for c in offered:
            print(f"    {c.get('instance_id', '?'):<18}  "
                  f"template={c.get('template_id', '?')}  "
                  f"{c.get('type','?')}  {c.get('size','?')}  {c.get('tier','?')}")

    sel = state.get("selection_set", [])
    if sel:
        print(f"\n  SELECTION SET: {sel}")

    board = gs.get("player_board", [])
    if board:
        print(f"\n  PLAYER BOARD ({len(board)}):")
        for c in board:
            print(f"    [sock={c.get('socket','?')}]  "
                  f"{c.get('instance_id','?'):<18}  "
                  f"template={c.get('template_id','?')}  "
                  f"{c.get('tier','?')}")

    skills = gs.get("player_skills", [])
    if skills:
        print(f"\n  SKILLS ({len(skills)}):")
        for c in skills:
            print(f"    {c.get('instance_id','?'):<18}  "
                  f"template={c.get('template_id','?')}")

    opp = gs.get("opponent_board", [])
    if opp:
        print(f"\n  OPPONENT BOARD ({len(opp)}):")
        for c in opp:
            print(f"    [sock={c.get('socket','?')}]  "
                  f"{c.get('instance_id','?'):<18}  "
                  f"template={c.get('template_id','?')}  "
                  f"{c.get('tier','?')}")

    print("=" * 60 + "\n")


# -- CLI -----------------------------------------------------------------------

SAMPLE_B64 = (
    "kZLSAAAAA5KXmwkA+AACkqtpdG1fMWZYMzZGRQAUAHJNZzBMMkZjFACRF5ajRF9m"
    "oTKRLwChSTNqc3JQRcCQkTEAKQGRFwABFAADWQADKgACRQBEHZLAwB0AsmVuY19V"
    "WERzNFJ2HQBCDpMBAh4AMhSSAAkAIQGTJwDxH3psaEI2cUTZJDMyM2UyYzA1LWYw"
    "NzctNDc3NC04NGE1LTllODlkOGQxYzliOQRhAAM6APked3Q3c1lLRdkkMDYyM2Nl"
    "ZGQtZjI0Yi00MTE3LWE2YjgtNWZjNDEwNjRhMDBkOgD4KHNZNF9xNFjZJDEyYTUw"
    "YjJkLTM0MGYtNDYwMy1hNWE0LWEzZDYzZDgwYzUwNASSAIEDA5IBgIQeARiaDQBi"
    "gRqSAADAAQAYANYAGJoNACCBKCYAspEAkJPAwMACkQsBKwADxwACKwADDQAVgCcA"
    "IpACJgADswACJgADDQAHJgDwAQEAlwIBAQDCAKU1LjAuMJeOAAlgAQGJAANeAAEM"
    "AANEANDAlsLCwsDCwJCjZ01o"
)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Decode a Bazaar API msgpack response"
    )
    parser.add_argument("--b64",   type=str,       help="Base64-encoded response body")
    parser.add_argument("--test",  action="store_true", help="Run with sample payload")
    parser.add_argument("--debug", action="store_true", help="Show raw decoded structure")
    parser.add_argument("--json",  action="store_true", help="Output structured JSON")
    args = parser.parse_args()

    if args.test:
        raw_bytes = base64.b64decode("".join(SAMPLE_B64.split()))
        print("[Decoder] Using sample payload from Fiddler capture.")
        print("[Decoder] NOTE: This is a partial/stripped response — "
              "the outer INetMessage envelope may be missing.")
        print("[Decoder] Full responses from live proxy will parse more completely.\n")
    elif args.b64:
        raw_bytes = base64.b64decode(args.b64)
    else:
        parser.print_help()
        sys.exit(1)

    raw_objects = decode_raw(raw_bytes)
    if raw_objects is None:
        print("[Decoder] Failed to decode msgpack bytes.")
        sys.exit(1)

    print(f"[Decoder] Decoded {len(raw_objects)} top-level object(s).")

    if args.debug:
        print("\n=== RAW STRUCTURE ===")
        print(json.dumps(raw_objects, indent=2, default=str))

    gs = parse_game_state(raw_objects, debug=args.debug)
    print_game_state(gs)

    if gs and args.json:
        print("=== STRUCTURED JSON ===")
        output = {k: v for k, v in gs.items() if k != "all_cards"}
        print(json.dumps(output, indent=2, default=str))

    if gs is None and not args.debug:
        print("[Decoder] Tip: run with --debug to inspect the raw decoded structure.")
        print("[Decoder] The sample payload may be a fragment — "
              "capture a full response from the proxy for best results.")
