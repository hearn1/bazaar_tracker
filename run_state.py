"""
run_state.py — Stateful assembler that turns a stream of parsed log events
into complete Decision records ready to write to the DB.

Decision types:
  item          - bought an item (PlayerSocket)
  companion     - bought a companion
  skill         - chose a skill (instance ID only until Fiddler resolves template)
  event_choice  - chose which map node/encounter to visit
  skip          - left a shop without buying anything (scored as missed opportunity)
  free_reward   - forced single-item reward (0 alternatives, not scored)
  unknown       - prefix not recognized

Combat outcomes:
  PvE win:  ReplayState → LootState       → opponent_died
  PvE loss: ReplayState → ChoiceState etc → player_died
  PvP:      Both wins AND losses go to ChoiceState/EncounterState after ReplayState.
            Cannot distinguish from state transitions alone — needs API data.
            Stored as pvp_unknown until resolved.
"""

import json

import db
import card_cache
import scorer as _scorer
from board_state import BoardState
from name_resolver import NameResolver
from shop_session import ShopSession
from typing import Callable, Optional


class RunState:

    def __init__(self, log_path: str, on_run_complete: Optional[Callable[[dict], None]] = None):
        self.log_path = log_path
        self.on_run_complete = on_run_complete
        self.emit_completion_callbacks = True
        self._reset_run_state()

    def _reset_run_state(self):
        self.run_id: Optional[int] = None
        self.session_id: Optional[str] = None
        self.account_id: Optional[str] = None
        self.hero: Optional[str] = None
        self.run_start_ts: Optional[str] = None
        self._run_closed: bool = False

        self.current_state: str = "Unknown"

        self.pending_offered: list[str] = []
        self.decision_seq: int = 0
        self._shop_window_id: int = 0
        self._in_shop: bool = False
        self._encounter_mode: str = "unknown"
        self._shop: ShopSession = ShopSession(window_id=0)

        # Single source of truth for player inventory
        self.board = BoardState()

        self.combat_start_ts: Optional[str] = None
        self._pending_combat: Optional[dict] = None
        self._max_persisted_seq: int = 0
        self._current_combat_type: str = "pve"

        self._pending_event_choices: list[str] = []
        self._pending_sell_commands: list[dict] = []
        # Centralized name resolution with lazy retry
        self.resolver = NameResolver()

        # Live scorer — instantiated when hero is known, scores each decision
        # immediately after insert so the overlay always reads stored scores.
        self._live_scorer: Optional[_scorer.LiveScorer] = None

    # ── Public entry point ────────────────────────────────────────────────────

    def process(self, event: dict):
        etype = event.get("event")
        ts = event.get("ts", "")

        if etype == "run_start":
            self._on_run_start(ts)
        elif etype == "session_id":
            self._on_session_id(ts, event["session_id"])
        elif etype == "account_id":
            self.account_id = event["account_id"]
            self._try_init_run(ts)
        elif etype == "hero":
            self.hero = event["hero"]
            self._try_init_run(ts)
        elif etype == "state_change":
            self._on_state_change(event)
        elif etype == "cards_dealt":
            self._on_cards_offered(event["instance_ids"])
        elif etype == "cards_spawned":
            self._on_cards_spawned(event["instance_ids"])
        elif etype == "card_transformed":
            self._on_card_transformed(event)
        elif etype == "card_purchased":
            self._on_card_purchased(event)
        elif etype == "cards_disposed":
            self._on_cards_disposed(event)
        elif etype == "card_sold":
            self._on_card_sold(event)
        elif etype == "command_sent":
            self._on_command_sent(event)
        elif etype == "reroll":
            if (
                self._in_shop
                and self.current_state == "EncounterState"
                and self._encounter_mode == "shop"
            ):
                self._shop.on_reroll()
                self._finalize_shop_page(ts)
        elif etype == "skill_selected":
            self._on_skill_selected(event)
        elif etype == "card_moved":
            self._on_card_moved(event)
        elif etype == "combat_start":
            self.combat_start_ts = ts
        elif etype == "combat_complete":
            self._on_combat_complete(event)
        elif etype == "run_defeat":
            self._on_run_end(ts, "defeat")
        elif etype == "run_victory":
            self._on_run_end(ts, "victory")

    # ── Internal handlers ─────────────────────────────────────────────────────

    def _on_run_start(self, ts: str):
        saved_account = self.account_id
        saved_hero = self.hero
        self._reset_run_state()
        self.account_id = saved_account
        self.hero = saved_hero
        self.run_start_ts = ts
        print(f"[RunState] New run detected at {ts}")

    def _on_session_id(self, ts: str, session_id: str):
        """
        Session ids are the strongest run boundary signal in Player.log.
        If a new session id appears while a run is still open, close the old
        run defensively so subsequent decisions cannot bleed across runs.
        """
        if self.session_id and session_id != self.session_id:
            prev_session = self.session_id
            if self.run_id is not None and not self._run_closed:
                print(
                    f"[RunState] Session changed {prev_session[:8]}… -> {session_id[:8]}… "
                    "before explicit end-state; closing prior run as interrupted."
                )
                self._on_run_end(ts, "interrupted")
            saved_account = self.account_id
            saved_hero = self.hero
            self._reset_run_state()
            self.account_id = saved_account
            self.hero = saved_hero
            self.run_start_ts = ts

        self.session_id = session_id
        self._try_init_run(ts)

    def _try_init_run(self, ts: str):
        if self.run_id is not None:
            return
        if not (self.session_id and self.account_id):
            return

        self.run_start_ts = self.run_start_ts or ts
        self.run_id = db.upsert_run(
            session_id=self.session_id,
            account_id=self.account_id,
            hero=self.hero or "Unknown",
            started_at=self.run_start_ts,
            log_path=self.log_path,
        )
        self.resolver.set_run_id(self.run_id)

        # Instantiate LiveScorer now that the hero is known.  A new conn is
        # opened here only briefly for the scorer's DB queries; the scorer
        # subsequently fires score writes through db.update_decision_score which
        # uses the background writer queue (fire-and-forget, no blocking I/O).
        try:
            _conn = db.get_conn()
            self._live_scorer = _scorer.LiveScorer(self.hero, _conn)
            _conn.close()
        except Exception as exc:
            print(f"[RunState] LiveScorer init failed: {exc}")
            self._live_scorer = None

        conn = db.get_conn()
        try:
            row = conn.execute(
                "SELECT COUNT(*) as cnt, MAX(decision_seq) as max_seq FROM decisions WHERE run_id=?",
                (self.run_id,)
            ).fetchone()
        finally:
            conn.close()

        existing = row["cnt"]
        self._max_persisted_seq = row["max_seq"] or 0
        self.decision_seq = self._max_persisted_seq

        if existing > 0:
            print(
                f"[RunState] Run id={self.run_id} already has {existing} decisions "
                f"— resuming from seq {self._max_persisted_seq}"
            )
        else:
            self._max_persisted_seq = 0
            print(
                f"[RunState] Run record created: id={self.run_id} "
                f"hero={self.hero} session={self.session_id[:8]}…"
            )

    def _on_state_change(self, event: dict):
        prev = self.current_state
        self.current_state = event["to_state"]
        ts = event.get("ts", "")
        print(f"[RunState] State: {prev} → {self.current_state}")

        # ── Leaving EncounterState: finalize shop ────────────────────────────
        if prev == "EncounterState" and self._in_shop:
            self._finalize_shop_page(ts)
            self._in_shop = False
            self._encounter_mode = "unknown"
        if not self._in_shop:
            self._encounter_mode = "unknown"

        # ── Entering EncounterState: open a new shop window ─────────────────
        if self.current_state == "EncounterState":
            self._in_shop = False
            self._encounter_mode = "unknown"
            self._shop_window_id += 1
            self._shop = ShopSession(window_id=self._shop_window_id)

        # ── Entering ChoiceState: capture map node options ──────────────────
        if self.current_state == "ChoiceState":
            self._pending_event_choices = list(self.pending_offered)

        # ── Entering combat: clear buffers ──────────────────────────────────
        if self.current_state in ("CombatState", "PVPCombatState"):
            self.pending_offered.clear()
            self._current_combat_type = "pvp" if self.current_state == "PVPCombatState" else "pve"
            self._in_shop = False
            self._shop = ShopSession(window_id=self._shop_window_id)
            self._clear_pending_sell_commands("entered combat")

        # ── Combat outcome from state after ReplayState ─────────────────────
        if prev == "ReplayState":
            if self.current_state in ("LootState", "EndRunVictoryState", "LevelUpState"):
                self._resolve_last_combat_outcome("opponent_died")
            elif self.current_state == "EndRunDefeatState":
                self._resolve_last_combat_outcome("player_died")
            elif self._pending_combat and self._pending_combat.get("combat_type") == "pve":
                self._resolve_last_combat_outcome("player_died")
            else:
                self._resolve_last_combat_outcome("pvp_unknown")

        if self.current_state == "EndRunDefeatState":
            self._clear_pending_sell_commands("run defeat")
            self._on_run_end(ts, "defeat")
        elif self.current_state == "EndRunVictoryState":
            self._clear_pending_sell_commands("run victory")
            self._on_run_end(ts, "victory")

        # Do not force a blocking flush on every state change.
        # End-of-run still performs a hard flush.
        db.flush_if_stale(5.0)

    def _resolve_instance_name(self, instance_id: str) -> str:
        """Resolve a runtime instance id to a readable card name when possible."""
        return self.resolver.resolve(instance_id)

    def _format_name_list(self, names: list[str], limit: int = 4) -> str:
        """Compact list display for live logs."""
        display = names[:limit]
        suffix = "..." if len(names) > limit else ""
        return f"{display}{suffix}"

    def _queue_sell_command(self, ts: str):
        if self._pending_sell_commands:
            print(
                f"[RunState] SellCardCommand while another sell is pending — "
                f"clearing {len(self._pending_sell_commands)} stale signal(s)."
            )
            self._pending_sell_commands.clear()
        self._pending_sell_commands.append({
            "ts": ts,
            "state": self.current_state,
        })
        print(
            f"[RunState] SellCardCommand seen in {self.current_state} "
            f"(pending={len(self._pending_sell_commands)})"
        )

    def _clear_pending_sell_commands(self, reason: str):
        if not self._pending_sell_commands:
            return
        print(f"[RunState] Clearing {len(self._pending_sell_commands)} pending sell signal(s): {reason}")
        self._pending_sell_commands.clear()

    def _record_sell_disposal(
        self,
        *,
        timestamp: str,
        instance_id: str,
        category: str,
        card_record: dict,
        matched_owned_ids: list[str],
        disposed_batch: list[str],
        sell_signal: str = "sell_command_plus_dispose",
        gold_earned: Optional[int] = None,
    ):
        sell_command = self._pending_sell_commands.pop(0) if self._pending_sell_commands else {}
        template_id = card_record.get("template_id") or ""
        from_socket = card_record.get("socket")
        name = card_record.get("name") or card_cache.resolve_template_id(template_id) or instance_id
        print(
            f"[Action] SELL {name} | {category}:{from_socket} | "
            f"signal={sell_signal} gold={gold_earned}"
        )

    def _ids_look_like_shop_offers(self, instance_ids: list[str]) -> bool:
        prefixes = {value.split("_", 1)[0] if "_" in value else value for value in instance_ids if value}
        if not prefixes:
            return False
        return prefixes.issubset({"itm", "com"})

    def _ids_look_like_event_choices(self, instance_ids: list[str]) -> bool:
        prefixes = {value.split("_", 1)[0] if "_" in value else value for value in instance_ids if value}
        if not prefixes:
            return False
        return prefixes.issubset({"enc", "ste", "com", "ped", "pvp"})

    def _section_to_action_category(self, section: str, decision_type: str = "") -> Optional[str]:
        if decision_type == "skill":
            return "player_skills"
        if section == "Player":
            return "player_board"
        if section == "Storage":
            return "player_stash"
        return None

    def _category_from_socket_name(self, socket_name: str) -> Optional[str]:
        if not socket_name:
            return None
        normalized = str(socket_name)
        if "Storage" in normalized:
            return "player_stash"
        if "Skill" in normalized:
            return "player_skills"
        if "PlayerSocket" in normalized:
            return "player_board"
        return None

    def _category_from_move_zone(self, side: str, zone: str) -> Optional[str]:
        """
        Map the (side, zone) tuple emitted on the verbose 'Successfully moved card to:'
        line to an internal category. Only Player-side zones are mapped; opponent
        moves are ignored here because we don't track opponent sockets as ours.
        """
        if not zone:
            return None
        side_norm = (side or "").strip()
        zone_norm = zone.strip()
        if side_norm and side_norm != "Player":
            return None
        if zone_norm in ("Hand", "Board"):
            return "player_board"
        if zone_norm == "Stash":
            return "player_stash"
        if zone_norm == "Skills":
            return "player_skills"
        return None

    def _can_record_event_choice(self, instance_id: str) -> bool:
        offered = list(self._pending_event_choices) if self._pending_event_choices else list(self.pending_offered)
        if not offered:
            return False
        if instance_id in offered:
            return True
        offered_prefixes = {
            value.split("_", 1)[0] if "_" in value else value
            for value in offered
            if value
        }
        choice_prefixes = {"enc", "ste", "com", "ped", "pvp"}
        return bool(offered_prefixes) and offered_prefixes.issubset(choice_prefixes)

    def _normalize_event_choice_offered(self, offered: list[str], instance_id: str) -> list[str]:
        """Drop stale PvP placeholder offers when the chosen node is the only reliable signal."""
        if not offered or instance_id in offered:
            return offered
        if len(offered) == 1 and offered[0].startswith("pvp_"):
            chosen_prefix = instance_id.split("_", 1)[0] if "_" in instance_id else instance_id
            if chosen_prefix in {"enc", "ste", "com", "ped"}:
                return [instance_id]
        return offered

    def _finalize_shop_page(self, ts: str):
        """Close out the current shop page: write rejected sets or log a skip."""
        result = self._shop.finalize()
        leftovers = result.leftovers
        preserve_inferred_purchase = False

        if result.decision_ids:
            for decision_id in result.decision_ids:
                db.update_decision_rejected(decision_id, leftovers)
            purchased_names = [self._resolve_instance_name(iid) for iid in result.purchased]
            rejected_names  = [self._resolve_instance_name(iid) for iid in leftovers]
            reroll_str = f" [r{result.reroll_count}]" if result.reroll_count else ""
            print(
                f"[ShopPage]{reroll_str} Bought: {self._format_name_list(purchased_names)}"
                f" | Passed on: {self._format_name_list(rejected_names)}"
            )
            self._encounter_mode = "unknown"
            return

        # No purchases on this page
        if leftovers and self.run_id:
            if result.select_command_seen and len(leftovers) == 1:
                self.pending_offered = list(leftovers)
                self._log_inferred_shop_purchase(ts)
                preserve_inferred_purchase = bool(self._shop.last_inferred_purchase_id)
            elif result.select_command_seen:
                offered_names   = [self._resolve_instance_name(iid) for iid in result.offered]
                purchased_names = [self._resolve_instance_name(iid) for iid in result.purchased]
                disposed_names  = [self._resolve_instance_name(iid) for iid in result.disposed_offers]
                leftover_names  = [self._resolve_instance_name(iid) for iid in leftovers]
                print(
                    f"[RunState] Shop select command seen, but purchase line was missing "
                    f"for {len(leftovers)} offered cards. window={self._shop_window_id} "
                    f"state={self.current_state} encounter_mode={self._encounter_mode} "
                    f"rerolls={result.reroll_count}"
                )
                print(
                    f"[RunState]   Offered: {self._format_name_list(offered_names)}"
                    f" | Purchased: {self._format_name_list(purchased_names)}"
                    f" | Disposed offers: {self._format_name_list(disposed_names)}"
                    f" | Leftovers: {self._format_name_list(leftover_names)}"
                )
            else:
                self.pending_offered = list(leftovers)
                self._log_skip(ts)

        self.pending_offered.clear()
        self._encounter_mode = "unknown"
        if not preserve_inferred_purchase:
            self._shop.clear_inferred_purchase()

    def _score_and_write(self, decision_id: int, decision_dict: dict) -> None:
        """Score a just-inserted decision and write the result to the DB.

        Delegates to LiveScorer which maintains incremental board state and
        calls db.update_decision_score (fire-and-forget via the writer queue).
        Silently skips if the scorer wasn't initialised (no build catalog).
        """
        if self._live_scorer is None or decision_id is None:
            return
        try:
            self._live_scorer.score_decision(decision_dict, decision_id)
        except Exception as exc:
            print(f"[RunState] LiveScorer.score_decision failed for decision {decision_id}: {exc}")

    def _log_skip(self, ts: str):
        """Log a shop exit with no purchase as a 'skip' decision."""
        offered = list(self.pending_offered)
        if not offered:
            return

        names = [self._resolve_instance_name(iid) for iid in offered]

        self.decision_seq += 1
        if self.decision_seq > self._max_persisted_seq:
            score_notes_payload = json.dumps({"resolved_names": names, "rerolls": self._shop.reroll_count})
            decision_id = db.insert_decision(
                run_id=self.run_id,
                seq=self.decision_seq,
                timestamp=ts,
                game_state="EncounterState",
                decision_type="skip",
                offered=offered,
                chosen_id="",
                chosen_template="",
                rejected=offered,
                board_section="",
                target_socket="",
                score_notes=score_notes_payload,
                board_snapshot_json=self.board.snapshot_json(),
            )
            self.board.record_snapshot(self.decision_seq)
            self._score_and_write(decision_id, {
                "decision_seq": self.decision_seq,
                "decision_type": "skip",
                "offered": json.dumps(offered),
                "chosen_id": "",
                "chosen_template": "",
                "rejected": json.dumps(offered),
                "board_section": "",
                "game_state": "EncounterState",
                "day": None,
                "phase_actual": None,
                "offered_names": None,
                "score_notes": score_notes_payload,
            })
            display_names = names[:4]
            suffix = "…" if len(offered) > 4 else ""
            reroll_str = f" (rerolled {self._shop.reroll_count}x)" if self._shop.reroll_count else ""
            print(f"[Decision #{self.decision_seq}] ⏭  SKIP{reroll_str} | Passed on: {display_names}{suffix}")
        self.pending_offered.clear()

    def _on_command_sent(self, event: dict):
        """Use outbound commands as a fallback signal for missing purchase lines."""
        command = event.get("command")
        if command == "SellCardCommand":
            self._queue_sell_command(event.get("ts", ""))
            return
        if command == "SelectItemCommand":
            offered = list(self._shop.offered) if self._shop.offered else list(self.pending_offered)
            if (
                self.current_state == "EncounterState"
                and self._ids_look_like_shop_offers(offered)
            ):
                self._in_shop = True
                self._encounter_mode = "shop"
                self._shop.on_select_command()
            return
        if not self._in_shop:
            return

    def _on_cards_spawned(self, instance_ids: list[str]):
        non_item_ids = []
        for instance_id in instance_ids:
            if not instance_id:
                continue
            prefix = instance_id.split("_", 1)[0] if "_" in instance_id else ""
            if prefix == "itm":
                existing_category, _ = self.board.lookup(instance_id)
                if existing_category is None:
                    self.board.buy(
                        instance_id=instance_id,
                        template_id="",
                        socket="",
                        category="player_stash",
                        name="",
                    )
            else:
                non_item_ids.append(instance_id)
        if non_item_ids:
            self._on_cards_offered(non_item_ids)

    def _on_card_transformed(self, event: dict):
        from_id = event["from_instance_id"]
        to_id = event["to_instance_id"]
        category, card_record = self.board.pop(from_id)
        if category is None:
            print(f"[RunState] card_transformed: from_instance_id {from_id!r} not in inventory — skipping {to_id!r}")
            return
        new_record = dict(card_record)
        old_template_id = card_record.get("template_id", "")
        new_template_id = old_template_id
        if new_template_id:
            self.resolver.notify_template(to_id, new_template_id)
        new_record["instance_id"] = to_id
        new_record["template_id"] = new_template_id
        resolved_name = self.resolver.resolve(to_id, template_id=new_template_id) if new_template_id else ""
        if resolved_name and not resolved_name.startswith("[") and not resolved_name.startswith("itm_"):
            new_record["name"] = resolved_name
        self.board.buy(
            instance_id=to_id,
            template_id=new_record.get("template_id", ""),
            socket=new_record.get("socket", ""),
            category=category,
            name=new_record.get("name", ""),
        )

    def _on_cards_offered(self, instance_ids: list[str]):
        if not instance_ids:
            return

        deduped = []
        seen = set()
        for instance_id in instance_ids:
            if instance_id and instance_id not in seen:
                deduped.append(instance_id)
                seen.add(instance_id)

        if self._ids_look_like_event_choices(deduped):
            self.pending_offered = list(deduped)
            self._pending_event_choices = list(deduped)
            self._encounter_mode = "event"
            self._in_shop = False
            self._shop = ShopSession(window_id=self._shop_window_id)
            return

        if self.current_state == "EncounterState" and self._ids_look_like_shop_offers(deduped):
            self._encounter_mode = "shop"
            self._in_shop = True

        # Delegate offer tracking to ShopSession (handles implicit reroll detection internally)
        if self._in_shop and self.current_state == "EncounterState" and self._encounter_mode == "shop":
            self._shop.on_cards_offered(deduped)
            self.pending_offered = list(self._shop.offered)
            return

        for instance_id in deduped:
            if instance_id not in self.pending_offered:
                self.pending_offered.append(instance_id)

    def _on_cards_disposed(self, event: dict):
        instance_ids = event["instance_ids"]
        if not instance_ids:
            if self._pending_sell_commands:
                self._pending_sell_commands.pop(0)
                print(
                    "[RunState] Cards Disposed was empty while a sell was pending — "
                    "dropped one stale SellCardCommand."
                )
            return
        matched_owned_ids = []
        for instance_id in instance_ids:
            category, _card = self.board.lookup(instance_id)
            if category:
                matched_owned_ids.append(instance_id)
        if self._in_shop:
            self._shop.on_disposed_offers(instance_ids)
        if self._pending_sell_commands:
            if len(matched_owned_ids) == 1:
                sold_instance_id = matched_owned_ids[0]
                category, card_record = self.board.pop(sold_instance_id)
                if category and card_record is not None:
                    self._record_sell_disposal(
                        timestamp=event.get("ts", ""),
                        instance_id=sold_instance_id,
                        category=category,
                        card_record=card_record,
                        matched_owned_ids=matched_owned_ids,
                        disposed_batch=list(instance_ids),
                    )
            elif len(matched_owned_ids) > 1:
                print(
                    "[RunState] SellCardCommand matched multiple owned disposals; "
                    f"not emitting semantic sell yet: {matched_owned_ids}"
                )
                self._clear_pending_sell_commands("ambiguous disposal batch after sell command")
            else:
                print(
                    "[RunState] SellCardCommand seen, but disposal batch did not match "
                    f"known owned cards: {instance_ids}"
                )
        if not self.pending_offered:
            return
        disposed = set(instance_ids)
        self.pending_offered = [iid for iid in self.pending_offered if iid not in disposed]

    def _on_card_sold(self, event: dict):
        instance_id = event["instance_id"]
        gold_earned = event.get("gold_earned")
        category, card_record = self.board.pop(instance_id)
        if category is None or card_record is None:
            print(
                f"[RunState] Sold Card line seen, but no known owned card matched {instance_id}"
            )
            if self._pending_sell_commands:
                self._clear_pending_sell_commands(
                    "sold line could not match owned card; clearing stale sell queue"
                )
            return
        signal = "sell_command_plus_sold_line" if self._pending_sell_commands else "sold_line"
        self._record_sell_disposal(
            timestamp=event.get("ts", ""),
            instance_id=instance_id,
            category=category,
            card_record=card_record,
            matched_owned_ids=[instance_id],
            disposed_batch=[],
            sell_signal=signal,
            gold_earned=gold_earned,
        )

    def _is_shop_offer(self, instance_id: str) -> bool:
        if self._in_shop or self.current_state == "EncounterState":
            return True
        if self._shop.select_command_seen:
            return True
        return instance_id == self._shop.last_inferred_purchase_id

    def _classify_purchase(self, instance_id: str) -> str:
        prefix = instance_id.split("_", 1)[0] if "_" in instance_id else ""
        is_shop = self._is_shop_offer(instance_id)

        if prefix == "itm":
            return "item" if is_shop else "free_reward"
        if prefix == "com":
            return "companion" if is_shop else "free_reward"
        if prefix == "skl":
            return "skill"
        return "unknown"

    def _has_logged_skill_decision(self, instance_id: str) -> bool:
        if not self.run_id or not instance_id:
            return False
        if "_" in instance_id and not instance_id.startswith("skl"):
            return False

        conn = db.get_conn()
        try:
            row = conn.execute(
                """
                SELECT 1
                FROM decisions
                WHERE run_id = ?
                AND decision_type = 'skill'
                AND chosen_id = ?
                LIMIT 1
                """,
                (self.run_id, instance_id),
            ).fetchone()
            return row is not None
        finally:
            conn.close()

    def _log_inferred_shop_purchase(self, ts: str):
        """Recover a one-card shop purchase when the Card Purchased line is missing."""
        if not self.run_id or len(self.pending_offered) != 1:
            return

        instance_id = self.pending_offered[0]
        template_id = self.resolver._template_map.get(instance_id, "")
        offered = [instance_id]
        dtype = self._classify_purchase(instance_id)
        if dtype == "skill":
            return
        self.board.buy(
            instance_id=instance_id,
            template_id=template_id,
            socket="",
            category="player_board",
            name=card_cache.resolve_template_id(template_id) or instance_id,
        )

        self._shop.set_inferred_purchase(instance_id, None)
        self.decision_seq += 1
        if self.decision_seq > self._max_persisted_seq:
            decision_id = db.insert_decision(
                run_id=self.run_id,
                seq=self.decision_seq,
                timestamp=ts,
                game_state="EncounterState",
                decision_type=dtype,
                offered=offered,
                chosen_id=instance_id,
                chosen_template=template_id,
                rejected=[],
                board_section="Player",
                target_socket="",
                score_notes='{"inferred_purchase": true}',
                board_snapshot_json=self.board.snapshot_json(),
            )
            self.board.record_snapshot(self.decision_seq)
            self._shop.set_inferred_purchase(instance_id, decision_id)
            self._score_and_write(decision_id, {
                "decision_seq": self.decision_seq,
                "decision_type": dtype,
                "offered": json.dumps(offered),
                "chosen_id": instance_id,
                "chosen_template": template_id,
                "rejected": "[]",
                "board_section": "Player",
                "game_state": "EncounterState",
                "day": None,
                "phase_actual": None,
                "offered_names": None,
                "score_notes": '{"inferred_purchase": true}',
            })
            name = card_cache.resolve_template_id(template_id) or instance_id
            reroll_str = f" [r{self._shop.reroll_count}]" if self._shop.reroll_count else ""
            print(f"[Decision #{self.decision_seq}] 🛒 {name}{reroll_str} "
                  "| Inferred from shop select command")

        self.pending_offered.clear()

    def _on_card_purchased(self, event: dict):
        if not self.run_id:
            return

        instance_id   = event["instance_id"]
        template_id   = event["template_id"]
        target_socket = event["target_socket"]
        section       = event["section"]
        is_player_side = section in ("Player", "Storage")

        self.resolver.notify_template(instance_id, template_id)

        card_record = {
            "instance_id": instance_id,
            "template_id": template_id,
            "socket": target_socket,
            "name": card_cache.resolve_template_id(template_id),
        }

        # Skip if this was already handled by inferred purchase
        if instance_id == self._shop.last_inferred_purchase_id:
            if is_player_side:
                cat = self._section_to_action_category(section, "item") or "player_board"
                self.board.buy(instance_id=instance_id, template_id=template_id,
                               socket=target_socket, category=cat,
                               name=card_record["name"])
            if self._shop.last_inferred_purchase_decision_id is not None:
                db.update_decision_purchase_details(
                    self._shop.last_inferred_purchase_decision_id,
                    template_id, section, target_socket, chosen_id=instance_id,
                )
                print(
                    f"[RunState] Reconciled inferred purchase {instance_id} "
                    f"with template={template_id} section={section} socket={target_socket}"
                )
            else:
                print(
                    f"[RunState] Inferred purchase {instance_id} matched an authoritative "
                    f"purchase line, but no decision_id was available to patch."
                )
            self._shop.clear_inferred_purchase()
            return
        if (
            is_player_side
            and self._shop.last_inferred_purchase_id
            and self._shop.last_inferred_purchase_decision_id is not None
        ):
            inferred_instance_id = self._shop.last_inferred_purchase_id
            inferred_category, _ = self.board.pop(inferred_instance_id)
            if inferred_category:
                print(
                    f"[RunState] Inferred purchase instance alias {inferred_instance_id} "
                    f"resolved to authoritative purchase {instance_id}"
                )
            cat = self._section_to_action_category(section, "item") or "player_board"
            self.board.buy(instance_id=instance_id, template_id=template_id,
                           socket=target_socket, category=cat, name=card_record["name"])
            db.update_decision_purchase_details(
                self._shop.last_inferred_purchase_decision_id,
                template_id, section, target_socket, chosen_id=instance_id,
            )
            print(
                f"[RunState] Reconciled inferred purchase alias {inferred_instance_id} -> "
                f"{instance_id} with template={template_id} section={section} "
                f"socket={target_socket}"
            )
            self._shop.clear_inferred_purchase()
            return

        if is_player_side:
            cat = self._section_to_action_category(section) or "player_board"
            self.board.buy(instance_id=instance_id, template_id=template_id,
                           socket=target_socket, category=cat, name=card_record["name"])

        prefix = instance_id.split("_")[0] if "_" in instance_id else ""

        # ── Map node choice ──────────────────────────────────────────────────
        if (
            section == "Opponent"
            and prefix in ("enc", "ste", "com", "ped")
            and self.current_state == "ChoiceState"
            and self._can_record_event_choice(instance_id)
        ):
            self._record_event_choice(event, instance_id, template_id)
            self.pending_offered.clear()
            return

        # Use the dedicated skill_selected event as the authoritative logging
        # path so skill picks do not double-log through both handlers.
        if prefix == "skl":
            if self._has_logged_skill_decision(instance_id):
                self.pending_offered = [x for x in self.pending_offered if x != instance_id]
            return

        # ── Player-side purchase ─────────────────────────────────────────────
        if not is_player_side:
            return

        dtype = self._classify_purchase(instance_id)
        offered = list(self._shop.offered) if self._shop.offered else list(self.pending_offered)
        if dtype == "free_reward" and instance_id and instance_id not in offered:
            offered.append(instance_id)

        self._shop.on_purchase(instance_id)

        self.decision_seq += 1
        if self.decision_seq > self._max_persisted_seq:
            decision_id = db.insert_decision(
                run_id=self.run_id,
                seq=self.decision_seq,
                timestamp=event["ts"],
                game_state=self.current_state,
                decision_type=dtype,
                offered=offered,
                chosen_id=instance_id,
                chosen_template=template_id,
                rejected=[],
                board_section=section,
                target_socket=target_socket,
                board_snapshot_json=self.board.snapshot_json(),
            )
            self.board.record_snapshot(self.decision_seq)
            self._shop.add_decision_id(decision_id)
            self._score_and_write(decision_id, {
                "decision_seq": self.decision_seq,
                "decision_type": dtype,
                "offered": json.dumps(offered),
                "chosen_id": instance_id,
                "chosen_template": template_id,
                "rejected": "[]",
                "board_section": section,
                "game_state": self.current_state,
                "day": None,
                "phase_actual": None,
                "offered_names": None,
                "score_notes": None,
            })
            name = card_cache.resolve_template_id(template_id) or instance_id
            tag = "🎁" if dtype == "free_reward" else "🛒"
            reroll_str = f" [r{self._shop.reroll_count}]" if self._shop.reroll_count else ""
            print(f"[Decision #{self.decision_seq}] {tag} {name}{reroll_str} | Pending shop close | {self.current_state}")

        self.pending_offered = [x for x in self.pending_offered if x != instance_id]

    def _record_event_choice(self, event: dict, instance_id: str, template_id: str):
        """Record which map encounter node the player chose."""
        offered = list(self._pending_event_choices) if self._pending_event_choices else list(self.pending_offered)
        normalized_offered = self._normalize_event_choice_offered(offered, instance_id)
        if normalized_offered != offered:
            print(
                f"[RunState] Normalized event choice offer list "
                f"{offered} -> {normalized_offered}"
            )
        offered = normalized_offered
        rejected = [x for x in offered if x != instance_id]

        self.decision_seq += 1
        if self.decision_seq > self._max_persisted_seq:
            decision_id = db.insert_decision(
                run_id=self.run_id,
                seq=self.decision_seq,
                timestamp=event["ts"],
                game_state="ChoiceState",
                decision_type="event_choice",
                offered=offered,
                chosen_id=instance_id,
                chosen_template=template_id,
                rejected=rejected,
                board_section="Opponent",
                target_socket=event["target_socket"],
                board_snapshot_json=self.board.snapshot_json(),
            )
            self.board.record_snapshot(self.decision_seq)
            self._score_and_write(decision_id, {
                "decision_seq": self.decision_seq,
                "decision_type": "event_choice",
                "offered": json.dumps(offered),
                "chosen_id": instance_id,
                "chosen_template": template_id,
                "rejected": json.dumps(rejected),
                "board_section": "Opponent",
                "game_state": "ChoiceState",
                "day": None,
                "phase_actual": None,
                "offered_names": None,
                "score_notes": None,
            })
            name = card_cache.resolve_template_id(template_id) or instance_id
            print(f"[Decision #{self.decision_seq}] 🗺  Event: {name} | Skipped {len(rejected)} others")
        self._pending_event_choices.clear()

    def _on_skill_selected(self, event: dict):
        if not self.run_id:
            return
        instance_id = event["instance_id"]
        socket      = event["socket"]
        template_id = self.resolver._template_map.get(instance_id, "")
        offered     = list(self.pending_offered)
        rejected    = [x for x in offered if x != instance_id]
        self.board.buy(
            instance_id=instance_id,
            template_id=template_id,
            socket=socket,
            category="player_skills",
            name=card_cache.resolve_template_id(template_id) if template_id else instance_id,
        )

        if self._has_logged_skill_decision(instance_id):
            self.pending_offered = [x for x in self.pending_offered if x != instance_id]
            return

        self.decision_seq += 1
        if self.decision_seq > self._max_persisted_seq:
            decision_id = db.insert_decision(
                run_id=self.run_id,
                seq=self.decision_seq,
                timestamp=event["ts"],
                game_state=self.current_state,
                decision_type="skill",
                offered=offered,
                chosen_id=instance_id,
                chosen_template=template_id,
                rejected=rejected,
                board_section="Player",
                target_socket=socket,
                board_snapshot_json=self.board.snapshot_json(),
            )
            self.board.record_snapshot(self.decision_seq)
            self._score_and_write(decision_id, {
                "decision_seq": self.decision_seq,
                "decision_type": "skill",
                "offered": json.dumps(offered),
                "chosen_id": instance_id,
                "chosen_template": template_id,
                "rejected": json.dumps(rejected),
                "board_section": "Player",
                "game_state": self.current_state,
                "day": None,
                "phase_actual": None,
                "offered_names": None,
                "score_notes": None,
            })
        name = card_cache.resolve_template_id(template_id) if template_id else instance_id
        print(f"[Decision #{self.decision_seq}] 🔮 Skill: {name} | Rejected {len(rejected)}")
        self.pending_offered.clear()

    def _on_card_moved(self, event: dict):
        if not self.run_id:
            return
        instance_id = event["instance_id"]
        to_socket = event["to_socket"]

        # Source attribution: pull from in-memory inventory. This gives us the
        # from_category (player_board / player_stash / player_skills) and the
        # from_socket that the card is leaving.
        from_category: Optional[str] = None
        from_socket: Optional[str] = None
        category, card_record = self.board.lookup(instance_id)
        if category and card_record is not None:
            from_category = category
            from_socket = card_record.get("socket")

        # Destination attribution: prefer explicit zone info from the verbose line
        # (to_side / to_zone). Fall back to socket-name heuristics (handles the
        # older Storage/PlayerSocket/Skill labels), then to "same category as the
        # source" because moves within a single log event do not cross board/stash
        # boundaries in the short-form output we have seen.
        to_category = self._category_from_move_zone(
            event.get("to_side", ""), event.get("to_zone", "")
        )
        if not to_category:
            to_category = self._category_from_socket_name(to_socket)
        if not to_category and from_category:
            to_category = from_category

        if card_record is not None:
            updated = dict(card_record)
            updated["socket"] = to_socket
            dest = to_category or category
            self.board.buy(
                instance_id=instance_id,
                template_id=updated.get("template_id", ""),
                socket=to_socket,
                category=dest,
                name=updated.get("name", ""),
            )

    def _on_combat_complete(self, event: dict):
        self._pending_combat = {
            "ts": event["ts"],
            "duration_secs": event["duration_secs"],
            "player_board": self.board.player_board_list(),
            "opponent_board": [],
            "combat_type": self._current_combat_type,
        }

    def _resolve_last_combat_outcome(self, outcome: str):
        if not self.run_id or not self._pending_combat:
            return
        pending = self._pending_combat
        combat_type = pending.get("combat_type", "pve")
        db.insert_combat(
            run_id=self.run_id,
            timestamp=pending["ts"],
            outcome=outcome,
            combat_type=combat_type,
            duration_secs=pending["duration_secs"],
            player_board=pending["player_board"],
            opponent_board=pending["opponent_board"],
        )
        icons = {"opponent_died": "✅ WIN", "player_died": "❌ LOSS", "pvp_unknown": "❓ PVP(?)"}
        type_label = "PVP" if combat_type == "pvp" else "PvE"
        label = icons.get(outcome, outcome)
        print(f"[Combat] {label} | {type_label} | {pending['duration_secs']}s")
        self._pending_combat = None
        if self._live_scorer is not None:
            self._live_scorer.notify_combat()
        db.flush()

    def _on_run_end(self, ts: str, result: str):
        if not self.run_id or self._run_closed:
            return
        self._clear_pending_sell_commands(f"run ended ({result})")
        self._run_closed = True
        finished_run_id = self.run_id
        if self._pending_combat:
            combat_type = self._pending_combat.get("combat_type", "pve")
            if combat_type != "pvp":
                outcome = "player_died"
            elif result == "victory":
                outcome = "opponent_died"
            elif result == "defeat":
                outcome = "player_died"
            else:
                outcome = "pvp_unknown"
            self._resolve_last_combat_outcome(outcome)
        db.close_run(finished_run_id, ts, result)
        print(f"[Run] Ended: {result}")
        db.flush()
        if self.emit_completion_callbacks and callable(self.on_run_complete):
            try:
                self.on_run_complete({
                    "run_id": finished_run_id,
                    "hero": self.hero,
                    "session_id": self.session_id,
                    "ended_at": ts,
                    "result": result,
                })
            except Exception as exc:
                print(f"[Run] Completion hook failed for run {finished_run_id}: {exc}")

    # ── Summary ───────────────────────────────────────────────────────────────

    def print_summary(self):
        if not self.run_id:
            print("[Summary] No run data captured yet.")
            return

        conn = db.get_conn()
        try:
            decisions = conn.execute(
                "SELECT * FROM decisions WHERE run_id=? ORDER BY decision_seq",
                (self.run_id,)
            ).fetchall()
            combats = conn.execute(
                "SELECT * FROM combat_results WHERE run_id=? ORDER BY id",
                (self.run_id,)
            ).fetchall()
        finally:
            conn.close()

        print("\n" + "═" * 60)
        print(f"  RUN SUMMARY  |  Hero: {self.hero}  |  Run ID: {self.run_id}")
        print("═" * 60)
        print(f"  Session: {self.session_id}")
        print(f"  Decisions: {len(decisions)}  |  Combats: {len(combats)}")
        print()

        if decisions:
            icons = {
                "item": "🛒",
                "skip": "⏭",
                "free_reward": "🎁",
                "event_choice": "🗺",
                "skill": "🔮",
                "companion": "🐾",
                "unknown": "❓",
            }
            print("  DECISIONS:")
            for d in decisions:
                offered = json.loads(d["offered"]) if d["offered"] else []
                rejected = json.loads(d["rejected"]) if d["rejected"] else []
                name = (
                    card_cache.resolve_template_id(d["chosen_template"])
                    if d["chosen_template"]
                    else d["chosen_id"]
                )
                icon = icons.get(d["decision_type"], "·")
                print(
                    f"  #{d['decision_seq']:>3}  {icon} [{d['game_state']:<16}]  "
                    f"{d['decision_type']:<12}  {name:<30}  "
                    f"({len(offered)} offered, {len(rejected)} rejected)"
                )

        if combats:
            print("\n  COMBATS:")
            for c in combats:
                icon = (
                    "✅" if c["outcome"] == "opponent_died"
                    else ("❌" if c["outcome"] == "player_died" else "❓")
                )
                print(
                    f"  {icon} {c['combat_type'].upper():<4}  "
                    f"{c['outcome']:<14}  {c['duration_secs']}s"
                )

        print("═" * 60 + "\n")