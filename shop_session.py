"""
shop_session.py — Self-contained shop state machine for RunState.

Replaces the ~12 interacting boolean/string flags that previously tracked
shop state inline in RunState. Each ShopSession instance owns exactly one
shop window (one EncounterState visit). RunState creates a new ShopSession
when EncounterState is entered and calls finalize() when it is exited.

States
------
  idle              Initial state; no offers seen yet.
  browsing          Cards have been offered; player is browsing.
  selecting         A SelectItemCommand has been observed; a purchase is
                    expected imminently.
  awaiting_refresh  A reroll was accepted; waiting for the next offer row.

Public interface used by RunState
----------------------------------
  on_cards_offered(instance_ids)   → bool   True if ids were accepted as shop offers.
  on_purchase(instance_id)
  on_reroll()                      Returns True so caller can increment its counter.
  on_select_command()
  on_disposed_offers(instance_ids)
  finalize() → ShopResult          Produces the summary RunState writes to the DB.

ShopResult fields
-----------------
  offered           list[str]  — all instance_ids offered across the whole window
  purchased         list[str]  — instance_ids that were bought
  disposed_offers   list[str]  — offered ids disposed (used for consumed-offer calc)
  decision_ids      list[int]  — DB decision ids written during this window
  reroll_count      int
  leftovers         list[str]  — offered ids that were never consumed
  select_command_seen bool
  last_inferred_purchase_id           Optional[str]
  last_inferred_purchase_decision_id  Optional[int]
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Optional


# ── State constants ────────────────────────────────────────────────────────────

IDLE              = "idle"
BROWSING          = "browsing"
SELECTING         = "selecting"
AWAITING_REFRESH  = "awaiting_refresh"


# ── Result ─────────────────────────────────────────────────────────────────────

@dataclass
class ShopResult:
    offered:                             list[str]
    purchased:                           list[str]
    disposed_offers:                     list[str]
    decision_ids:                        list[int]
    reroll_count:                        int
    leftovers:                           list[str]
    select_command_seen:                 bool
    last_inferred_purchase_id:           Optional[str]
    last_inferred_purchase_decision_id:  Optional[int]


# ── ShopSession ────────────────────────────────────────────────────────────────

class ShopSession:
    """Tracks one shop window from EncounterState entry to exit."""

    def __init__(self, window_id: int = 0):
        self.window_id:    int  = window_id
        self.state:        str  = IDLE
        self.reroll_count: int  = 0

        self._offered:           list[str]       = []
        self._purchased:         list[str]       = []
        self._disposed_offers:   list[str]       = []
        self._decision_ids:      list[int]       = []
        self._select_command:    bool            = False
        self._last_inferred_id:  Optional[str]  = None
        self._last_inferred_did: Optional[int]  = None

    # ── Transitions ───────────────────────────────────────────────────────────

    def on_cards_offered(self, instance_ids: list[str]) -> bool:
        """
        Accept a batch of offers into the current page.

        Detects an implicit reroll when a completely new set of ids arrives
        while we already have offers.  Returns True so the caller knows the
        ids were accepted (vs. belonging to a different context like LootState).
        """
        if not instance_ids:
            return False

        if self.state == AWAITING_REFRESH or (
            self.state == BROWSING and self._offered
            and not (set(instance_ids) & set(self._offered))
            and len(instance_ids) >= 2
        ):
            # Implicit reroll: new page with no overlap
            if self.state == BROWSING:
                self.reroll_count += 1
            self._start_new_page(instance_ids)
            return True

        if self.state == IDLE:
            self._start_new_page(instance_ids)
            return True

        # BROWSING: extend the current page
        for iid in instance_ids:
            if iid not in self._offered:
                self._offered.append(iid)
        return True

    def on_purchase(self, instance_id: str):
        """Record a confirmed purchase on the current page."""
        self._purchased.append(instance_id)
        self._select_command   = False
        self._last_inferred_id = None
        self._last_inferred_did = None
        if self.state in (IDLE, AWAITING_REFRESH):
            self.state = BROWSING

    def add_decision_id(self, decision_id: int):
        """Called by RunState right after inserting a decision row."""
        self._decision_ids.append(decision_id)

    def set_inferred_purchase(self, instance_id: str, decision_id: Optional[int]):
        self._last_inferred_id  = instance_id
        self._last_inferred_did = decision_id

    def clear_inferred_purchase(self):
        self._last_inferred_id  = None
        self._last_inferred_did = None

    def on_reroll(self):
        """Explicit reroll command (RerollCommand / reroll event)."""
        self.reroll_count += 1
        self.state = AWAITING_REFRESH

    def on_select_command(self):
        self._select_command = True
        if self.state == BROWSING:
            self.state = SELECTING

    def on_disposed_offers(self, instance_ids: list[str]):
        for iid in instance_ids:
            if iid in self._offered and iid not in self._disposed_offers:
                self._disposed_offers.append(iid)

    # ── Accessors ─────────────────────────────────────────────────────────────

    @property
    def offered(self) -> list[str]:
        return list(self._offered)

    @property
    def purchased(self) -> list[str]:
        return list(self._purchased)

    @property
    def decision_ids(self) -> list[int]:
        return list(self._decision_ids)

    @property
    def select_command_seen(self) -> bool:
        return self._select_command

    @property
    def last_inferred_purchase_id(self) -> Optional[str]:
        return self._last_inferred_id

    @property
    def last_inferred_purchase_decision_id(self) -> Optional[int]:
        return self._last_inferred_did

    # ── Finalization ──────────────────────────────────────────────────────────

    def consumed_offer_ids(self) -> set[str]:
        """
        Which offered ids were actually consumed by purchases.

        Some purchases keep the original offered instance id; others create
        a new inventory id and dispose the offered one.  Use disposed offers
        to fill any unmatched purchases.
        """
        consumed: list[str] = []
        seen: set[str]      = set()

        for iid in self._purchased:
            if iid in self._offered and iid not in seen:
                consumed.append(iid)
                seen.add(iid)

        unmatched = max(0, len(self._purchased) - len(consumed))
        if unmatched:
            for iid in self._disposed_offers:
                if iid not in self._offered or iid in seen:
                    continue
                consumed.append(iid)
                seen.add(iid)
                unmatched -= 1
                if unmatched == 0:
                    break

        return set(consumed)

    def finalize(self) -> ShopResult:
        consumed  = self.consumed_offer_ids()
        leftovers = [iid for iid in self._offered if iid not in consumed]

        return ShopResult(
            offered                            = list(self._offered),
            purchased                          = list(self._purchased),
            disposed_offers                    = list(self._disposed_offers),
            decision_ids                       = list(self._decision_ids),
            reroll_count                       = self.reroll_count,
            leftovers                          = leftovers,
            select_command_seen                = self._select_command,
            last_inferred_purchase_id          = self._last_inferred_id,
            last_inferred_purchase_decision_id = self._last_inferred_did,
        )

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _start_new_page(self, instance_ids: list[str]):
        self._offered           = list(instance_ids)
        self._purchased         = []
        self._disposed_offers   = []
        self._decision_ids      = []
        self._select_command    = False
        self._last_inferred_id  = None
        self._last_inferred_did = None
        self.state = BROWSING

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"ShopSession(window={self.window_id}, state={self.state}, "
            f"offered={len(self._offered)}, purchased={len(self._purchased)}, "
            f"rerolls={self.reroll_count})"
        )