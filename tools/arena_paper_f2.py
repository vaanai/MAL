#!/usr/bin/env python3
"""arena_paper_f2: the F2 (repair before exclude) engine for tip-source books, DEC-031 Amendment 26. PAPER only.

A SUBCLASS in a NEW FILE. `tools/arena_paper.py` stays byte-identical to main (sha256 9d2fdb04...), so every existing section 1.4 pin (the HV4 registration, the p29 and kcurve run pins,
test_arena_rule_l1) keeps holding for every book and run registered before the amendment merged. A book registered AFTER it merged and naming Amendment 26 pins this file's sha256 too
(Amendment 26 item 1). With no filled marker in the stream the subclass delegates to the base engine and writes the base engine's rows (proof P1 of Amendment 26 item 11).

What it adds (tools/arena_tip_gapfill.py writes the markers):
  * a gap row with filled=true and data_loss=false (and back_slots) is a FILLED range: counted, remembered, never in `gaps`. A marker it cannot read is a data_loss gap (fail closed).
    Before it is remembered, the engine checks the stretch between the last row it saw and the range start (the slot-jump rule of the stack, gap_slots): a hole there that the follower
    did not log as filled stays a gap (engine_slot_jump). A fill removes only the range it names.
  * a repair row (fill: true) does not trigger the engine's own silence or slot-jump detection; a live row after a filled range measures its jump from the end of the range.
  * mark-through-gap (QP-RULING-1030-power-n 5(d)): an exit whose pricing slot falls in [lo, hi] executes at the first print at or after hi + 1. A conservative timing rule, not equivalence.
  * an ENTRY leg landing in [lo - back_slots, hi], or an EXIT leg landing in [lo - back_slots, lo), keeps the feed_gap exclusion; a position already priced when the marker reaches the engine
    is flagged by a `late_print` row with gap_overlap true (the scorer excludes it under the gap rule). back_slots is the follower's --backlog-slots for a backlog_jump, else 0.
  * a marked-through row carries `mark_through`. There is no strand rule for filled ranges (the pool history through the range is complete: the ordinary readiness rule applies).
"""

from __future__ import annotations

import dataclasses
import hashlib
from typing import Any, Mapping, Sequence

from tools import arena_paper as base
from tools.arena_paper import ArenaEngine, LedgerCorrupt

F = base.F
LATS = base.LATS
PRUNE_AFTER_SLOTS = 2 * base.GRACE_SLOTS + base.LATE_KEEP_SLOTS  # a filled range this far behind the high-water slot can no longer touch a pending or watched position


class ArenaEngineF2(ArenaEngine):
    def __init__(self, *a: Any, **k: Any) -> None:
        super().__init__(*a, **k)
        self.fills: list[tuple[str | None, int, int, int]] = []  # (prog, lo, hi, back_slots) of every filled range seen: not gaps
        self._marks: dict[str, tuple[int, int, int]] = {}  # position_id -> (xs before, lo, hi) of its mark-through
        self._legs: dict[str, tuple[list[int], int]] = {}  # position_id -> (entry landing slots, exit pricing slot before any mark-through), noted when it is priced

    # ------------------------------------------------------------------------------------------------ the marker
    def on_gap(self, row: Mapping[str, Any]) -> None:
        if row.get("filled") is True and not row.get("data_loss"):
            lo, hi, back = row.get("from_slot"), row.get("to_slot"), row.get("back_slots")
            if all(isinstance(x, int) and not isinstance(x, bool) for x in (lo, hi)) and hi >= lo and row.get("t_recv_ms") is not None:
                self._on_filled(row.get("prog"), lo, hi, int(row["t_recv_ms"]), back if isinstance(back, int) and not isinstance(back, bool) and back >= 0 else 0)
                return
            self._count("gap_fill_marker_bad")  # a marker the engine cannot read is a data_loss gap (fail closed)
            row = dict(row, data_loss=True)
        super().on_gap(row)

    def _on_filled(self, prog: str | None, lo: int, hi: int, at_ms: int, back: int) -> None:
        c = self.cfg
        if self.hw_ms is not None and c.gap_slots is not None and lo - self._through_fills(self.hw_slot) > c.gap_slots:
            # the stretch between the last row seen and the start of the range is not the range: the follower did not log it as filled, so it stays a gap
            self.gaps.append(base.Gap(None, self.hw_ms, at_ms, self.hw_slot, lo, "engine_slot_jump"))
            self._count("gap_engine_slot_jump")
        self.fills = [f for f in self.fills if f[2] + PRUNE_AFTER_SLOTS >= self.hw_slot]
        self.fills.append((prog, lo, hi, back))
        self._count("gap_filled")
        self.counters["gap_filled_slots"] = self.counters.get("gap_filled_slots", 0) + (hi - lo + 1)
        if self.hw_ms is not None and at_ms > self.hw_ms:
            self.hw_ms, self.hw_ms_slot = at_ms, max(self.hw_ms_slot, hi)
        self._flag_priced_by_fill(prog, lo, hi, back, at_ms)

    def _through_fills(self, slot: int) -> int:
        """`slot` moved up through every filled range that touches it (chained ranges too): a slot jump that a fill covers is not a jump."""
        moved = True
        while moved:
            moved = False
            for _p, lo, hi, _b in self.fills:
                if lo <= slot + 1 and hi > slot:
                    slot, moved = hi, True
        return slot

    # ------------------------------------------------------------------------------------------------ rows
    def on_row(self, row: Mapping[str, Any]) -> None:
        if row.get("fill"):  # a repair row: the range it sits in is filled, so the engine's own silence / slot-jump rules do not apply to it
            saved = self.cfg
            self.cfg = dataclasses.replace(saved, gap_slots=None, gap_silence_ms=None)
            try:
                super().on_row(row)
            finally:
                self.cfg = saved
            return
        if self.fills and self.hw_slot >= 0 and row.get("type", "trade") == "trade":
            self.hw_slot = max(self.hw_slot, self._through_fills(self.hw_slot))  # the jump to this row is measured from the end of the filled range
        super().on_row(row)

    # ------------------------------------------------------------------------------------------------ legs, mark-through, exclusion
    def _legs_of(self, pos: base.Position) -> tuple[list[int], int]:
        """(entry landing slots for both latencies, the exit leg's pricing slot before any mark-through shift)."""
        d = self._check_delay_set()
        lands = [pos.sd + d + self._k(lat, pos.sps_entry) for lat in LATS]
        m = self._marks.get(pos.position_id)
        return lands, (m[0] if m is not None else pos.xs) + d + F.exit_lag_slots(pos.sps_exit) + 1  # type: ignore[operator,arg-type]

    @staticmethod
    def _legs_hit(lands: Sequence[int], x_trig: int, lo: int, hi: int, back: int) -> bool:
        return any(lo - back <= x <= hi for x in lands) or lo - back <= x_trig < lo

    def _prog_matches(self, prog: str | None, pos_prog: str) -> bool:
        return prog in (None, pos_prog, "all") or (prog == "pumpfun" and pos_prog == "pump_bonding")

    def _mark_through(self, pos: base.Position) -> None:
        """An exit whose pricing slot (xs + d + exit lag + 1) falls inside a FILLED range executes at the first print at or after hi + 1. Applied once; it moves xs."""
        if pos.position_id in self._marks or not self.fills or pos.xs is None:
            return
        trig = pos.xs + self._check_delay_set() + F.exit_lag_slots(pos.sps_exit) + 1  # type: ignore[operator,arg-type]
        for prog, lo, hi, _b in sorted(self.fills, key=lambda f: (f[1], f[2])):
            if self._prog_matches(prog, pos.prog) and lo <= trig <= hi:
                self._marks[pos.position_id] = (pos.xs, lo, hi)
                pos.xs += hi + 1 - trig
                self._count("mark_through")
                return

    def _needed_slot(self, pos: base.Position) -> int:
        self._mark_through(pos)
        return super()._needed_slot(pos)

    def _fill_hit(self, pos: base.Position) -> bool:
        if not self.fills or pos.xs is None:
            return False
        lands, x_trig = self._legs_of(pos)
        return any(self._prog_matches(prog, pos.prog) and self._legs_hit(lands, x_trig, lo, hi, back) for prog, lo, hi, back in self.fills)

    def _exclusion(self, pos: base.Position, h: Any, src: str, need: int) -> str | None:
        if pos.call_feed_slot is not None and pos.xs is not None:
            self._legs[pos.position_id] = self._legs_of(pos)
            if self._fill_hit(pos):
                return "feed_gap"
        return super()._exclusion(pos, h, src, need)

    def _base_row(self, pos: base.Position, kind: str, s_sol: float, src: str | None) -> dict[str, Any]:
        row = super()._base_row(pos, kind, s_sol, src)
        m = self._marks.get(pos.position_id)
        if m is not None:  # only a marked-through exit carries the key: every other row is what the base engine writes
            row["mark_through"] = {"xs_before": m[0], "filled_from_slot": m[1], "filled_to_slot": m[2], "xs": pos.xs}
        return row

    def _flag_priced_by_fill(self, prog: str | None, lo: int, hi: int, back: int, at_ms: int) -> None:
        """A position priced BEFORE the marker reached the engine (its legs sit in the late-delivered stretch [lo - back, lo)) is flagged the way a late print is: a late_print row with
        gap_overlap true, which the scorer excludes under the gap rule (priced rows are write-once). Once per position and range."""
        for lst in self.priced.values():
            for e in lst:
                legs = self._legs.get(e["pid"])
                if legs is None or self.hw_slot > e["need"] + base.LATE_KEEP_SLOTS or e["bot_id"] in self.halted or not self._prog_matches(prog, e["prog"]):
                    continue
                if not self._legs_hit(legs[0], legs[1], lo, hi, back):
                    continue
                lid = hashlib.sha256(f"{e['pid']}|fill|{lo}|{hi}".encode()).hexdigest()[:16]
                try:
                    self.ledger.append({"schema": base.SCHEMA, "kind": "late_print", "trade_id": f"{e['pid']}-late-{lid}", "position_id": e["pid"], "bot_id": e["bot_id"],
                                        "decision_ms": {"entry": e["entry_ms"], "exit": None}, "slot": lo, "signature": None, "t_recv_ms": at_ms, "gap_overlap": True,
                                        "fill_margin": {"from_slot": lo, "to_slot": hi, "back_slots": back}, "written_ms": self.now_fn()})
                except LedgerCorrupt as exc:
                    self._count("ledger_torn_tail")
                    self._count("late_prints_lost")
                    self._halt(e["bot_id"], f"fill-margin flag write refused: {str(exc)[:120]}")
                    continue
                self._count("fill_margin_flagged")


__all__ = ["ArenaEngineF2", "PRUNE_AFTER_SLOTS"]
