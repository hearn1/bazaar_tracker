"""
board_state.py — Single source of truth for player inventory during a run.

Replaces the four separate board projections that previously existed across
run_state.py (in-memory dicts), server.py (action event replay),
scorer.py (action fallback), and bridge.py (board population).

RunState owns a BoardState instance, calls its mutators on every inventory-
changing event, and asks for a JSON snapshot at each decision insert.
The overlay reads `board_snapshot_json` from the latest decision row instead
of replaying action events.

Usage within RunState:
    self.board = BoardState()
    self.board.buy(instance_id, template_id, socket, category)
    self.board.sell(instance_id)
    self.board.move(instance_id, to_socket, to_category)
    self.board.transform(from_id, to_id, template_id, category, socket)
    snapshot_json = self.board.snapshot_json()   # for DB column
    names = self.board.owned_names()             # for overlay
"""

import json
from typing import Optional

import card_cache


# Categories that count as player-owned inventory
PLAYER_CATEGORIES = {"player_board", "player_stash", "player_skills"}
# Categories that contribute to owned_names (excludes skills)
TRACKED_ITEM_CATEGORIES = {"player_board", "player_stash"}


class BoardState:
    """Authoritative, sell/move/transform-aware inventory tracker."""

    def __init__(self):
        # instance_id -> card record dict
        self._cards: dict[str, dict] = {}
        # Snapshot history: decision_seq -> frozen snapshot dict
        self._history: dict[int, dict] = {}

    # ── Mutators ──────────────────────────────────────────────────────────────

    def buy(
        self,
        instance_id: str,
        template_id: str = "",
        socket: str = "",
        category: str = "player_board",
        name: str = "",
    ):
        """Record a card acquisition (purchase, reward, spawn)."""
        if not instance_id:
            return
        resolved_name = name or (
            card_cache.resolve_template_id(template_id) if template_id else ""
        ) or instance_id
        # Remove from any previous category first (handles re-buys / respawns)
        self._remove_from_all(instance_id)
        self._cards[instance_id] = {
            "instance_id": instance_id,
            "template_id": template_id,
            "socket": socket,
            "category": category,
            "name": resolved_name,
        }

    def sell(self, instance_id: str):
        """Remove a card from inventory (sold or disposed)."""
        self._cards.pop(instance_id, None)

    def move(
        self,
        instance_id: str,
        to_socket: str = "",
        to_category: Optional[str] = None,
    ):
        """Move a card to a new socket/category."""
        card = self._cards.get(instance_id)
        if card is None:
            return
        if to_socket:
            card["socket"] = to_socket
        if to_category and to_category in PLAYER_CATEGORIES:
            card["category"] = to_category

    def transform(
        self,
        from_id: str,
        to_id: str,
        template_id: str = "",
        category: Optional[str] = None,
        socket: Optional[str] = None,
        name: str = "",
    ):
        """Replace one card with its transformed version."""
        old_card = self._cards.pop(from_id, None)
        resolved_category = category or (old_card or {}).get("category", "player_board")
        resolved_socket = socket if socket is not None else (old_card or {}).get("socket", "")
        resolved_template = template_id or (old_card or {}).get("template_id", "")
        resolved_name = name or (
            card_cache.resolve_template_id(resolved_template) if resolved_template else ""
        ) or to_id
        self._cards[to_id] = {
            "instance_id": to_id,
            "template_id": resolved_template,
            "socket": resolved_socket,
            "category": resolved_category,
            "name": resolved_name,
        }

    # ── Queries ───────────────────────────────────────────────────────────────

    def lookup(self, instance_id: str) -> tuple[Optional[str], Optional[dict]]:
        """Find a card by instance_id. Returns (category, card_record) or (None, None)."""
        card = self._cards.get(instance_id)
        if card is None:
            return None, None
        return card.get("category"), dict(card)

    def pop(self, instance_id: str) -> tuple[Optional[str], Optional[dict]]:
        """Remove and return a card. Returns (category, card_record) or (None, None)."""
        card = self._cards.pop(instance_id, None)
        if card is None:
            return None, None
        return card.get("category"), dict(card)

    def owned_names(self) -> list[str]:
        """Sorted list of resolved human-readable names for board+stash cards."""
        names = set()
        for card in self._cards.values():
            if card.get("category") not in TRACKED_ITEM_CATEGORIES:
                continue
            name = card.get("name", "")
            if name and not _is_unresolved(name):
                names.add(name)
        return sorted(names)

    def cards_by_category(self) -> dict[str, list[dict]]:
        """Cards grouped by category, sorted by socket then name."""
        result: dict[str, list[dict]] = {
            "player_board": [],
            "player_stash": [],
            "player_skills": [],
        }
        for card in self._cards.values():
            category = card.get("category", "")
            if category in result:
                result[category].append(dict(card))
        for cards in result.values():
            cards.sort(key=lambda c: (
                str(c.get("socket") or ""),
                str(c.get("name") or c.get("instance_id") or ""),
            ))
        return result

    # ── Snapshots ─────────────────────────────────────────────────────────────

    def snapshot(self) -> dict:
        """Return a JSON-serializable snapshot of current inventory state.

        Structure:
        {
            "cards": {instance_id: {instance_id, template_id, socket, category, name}, ...},
            "owned_names": ["Card A", "Card B", ...],
        }
        """
        return {
            "cards": {iid: dict(card) for iid, card in self._cards.items()},
            "owned_names": self.owned_names(),
        }

    def snapshot_json(self) -> str:
        """JSON string of the current snapshot, ready for the DB column."""
        return json.dumps(self.snapshot())

    def record_snapshot(self, decision_seq: int):
        """Store a snapshot in history keyed by decision_seq."""
        self._history[decision_seq] = self.snapshot()

    # ── Combat board export ───────────────────────────────────────────────────

    def player_board_list(self) -> list[dict]:
        """Card dicts for the player_board category (used for combat records)."""
        return [
            dict(card) for card in self._cards.values()
            if card.get("category") == "player_board"
        ]

    # ── Internal ──────────────────────────────────────────────────────────────

    def _remove_from_all(self, instance_id: str):
        """Remove instance_id from cards dict if present."""
        self._cards.pop(instance_id, None)

    # ── Restore from snapshot ─────────────────────────────────────────────────

    @classmethod
    def from_snapshot_json(cls, snapshot_json: str) -> "BoardState":
        """Reconstruct a BoardState from a stored snapshot JSON string."""
        board = cls()
        if not snapshot_json:
            return board
        try:
            data = json.loads(snapshot_json)
            if isinstance(data, dict) and "cards" in data:
                for iid, card in data["cards"].items():
                    board._cards[iid] = dict(card)
        except (json.JSONDecodeError, TypeError):
            pass
        return board

    @staticmethod
    def owned_names_from_snapshot_json(snapshot_json: str) -> list[str]:
        """Extract owned_names directly from a snapshot JSON string.

        Fast path for the overlay — avoids reconstructing a full BoardState.
        """
        if not snapshot_json:
            return []
        try:
            data = json.loads(snapshot_json)
            if isinstance(data, dict):
                names = data.get("owned_names")
                if isinstance(names, list):
                    return names
        except (json.JSONDecodeError, TypeError):
            pass
        return []

    @staticmethod
    def cards_by_category_from_snapshot_json(snapshot_json: str) -> dict[str, list[dict]]:
        """Extract cards_by_category from snapshot JSON.

        Fast path for the overlay — avoids reconstructing a full BoardState.
        """
        result: dict[str, list[dict]] = {
            "player_board": [],
            "player_stash": [],
            "player_skills": [],
        }
        if not snapshot_json:
            return result
        try:
            data = json.loads(snapshot_json)
            if isinstance(data, dict) and "cards" in data:
                for card in data["cards"].values():
                    category = card.get("category", "")
                    if category in result:
                        result[category].append(dict(card))
                for cards in result.values():
                    cards.sort(key=lambda c: (
                        str(c.get("socket") or ""),
                        str(c.get("name") or c.get("instance_id") or ""),
                    ))
        except (json.JSONDecodeError, TypeError):
            pass
        return result

    @staticmethod
    def board_map_from_snapshot_json(snapshot_json: str) -> dict[str, str]:
        """Extract instance_id -> name map for board+stash cards.

        Used by scorer for archetype overlap computation.
        """
        board: dict[str, str] = {}
        if not snapshot_json:
            return board
        try:
            data = json.loads(snapshot_json)
            if isinstance(data, dict) and "cards" in data:
                for iid, card in data["cards"].items():
                    category = card.get("category", "")
                    if category in TRACKED_ITEM_CATEGORIES:
                        board[iid] = card.get("name") or iid
        except (json.JSONDecodeError, TypeError):
            pass
        return board


def _is_unresolved(name: str) -> bool:
    """Check if a name is still a raw instance ID rather than a resolved name."""
    return bool(name) and (
        name.startswith("itm_")
        or name.startswith("enc_")
        or name.startswith("skl_")
        or name.startswith("[")
        or name.startswith("ste_")
        or name.startswith("com_")
        or name.startswith("ped_")
    )
