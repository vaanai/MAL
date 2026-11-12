"""floor_gap_range_engine: research-side ArenaEngine subclass for the FLOOR gap-range replay. PAPER, in-sample research; no pinned or frozen file is edited.

What it changes, and only this:
  * gap_exclusion=False: the feed_gap exclusion (a data_loss gap overlapping decision-to-exit) is NOT applied; the position is priced through the hole with the
    prints the tape has (exit = first visible TP print, else the 24 h time stop, as the engine finds it). guard_ref_missing and missing_v exclusions are unchanged.
    gap_exclusion=True keeps the engine's behaviour exactly.
  * Every trade / excluded row carries `gap_overlap` (bool: a data_loss gap of the position's program overlaps its span) and `gap_holes` ([[t_lo_ms, t_hi_ms], ...]).
  * ts_variant=True: for every gap-overlap position that did NOT exit by the 24 h time stop, a shadow position (bot_id + "-ts", position_id + "-ts") is priced at the
    first feed row at or after decision_ms + HOLD_MS (the rule's own time-stop instant), written under ledger/<bot_id>-ts/. The shadow never enters the rule's view
    (HostV2 filters engine.positions by its own bot ids). It is priced with the same price_leg code as a real time exit.
The decisions of the rule do not depend on the exclusion (the host does not read the engine's trade/excluded rows), so entries are identical with and without it.
"""
import dataclasses
from tools import arena_rule_l1 as L
from tools.arena_paper import ArenaEngine


class GapRangeEngine(ArenaEngine):
    def __init__(self, *a, gap_exclusion=True, ts_variant=False, **k):
        super().__init__(*a, **k)
        self.gap_exclusion, self.ts_variant = gap_exclusion, ts_variant
        self._ov = {}        # position_id -> list of [t_lo, t_hi] of overlapping data_loss gaps
        self._shadows = []   # [due_ms, shadow_id]

    def _holes(self, pos, src, need):
        t_lo, t_hi, s_lo, s_hi = self._span(pos, need, src)
        out = []
        for g in self.gaps:
            if g.prog not in (None, pos.prog, "all") and not (g.prog == "pumpfun" and pos.prog == "pump_bonding"):
                continue
            if (g.t_lo <= t_hi and g.t_hi >= t_lo) or (g.s_lo <= s_hi and g.s_hi >= s_lo):
                out.append([g.t_lo, g.t_hi])
        return out

    def _exclusion(self, pos, h, src, need):
        if pos.position_id.endswith("-ts"):  # shadow: priced through holes by construction
            saved, self.gaps = self.gaps, []
            try:
                return super()._exclusion(pos, h, src, need)
            finally:
                self.gaps = saved
        self._ov[pos.position_id] = self._holes(pos, src, need)
        if self.gap_exclusion:
            return super()._exclusion(pos, h, src, need)
        saved, self.gaps = self.gaps, []
        try:
            return super()._exclusion(pos, h, src, need)
        finally:
            self.gaps = saved

    def _base_row(self, pos, kind, s_sol, src):
        row = super()._base_row(pos, kind, s_sol, src)
        if not pos.position_id.endswith("-ts"):
            holes = self._ov.get(pos.position_id, [])
            row["gap_overlap"] = bool(holes)
            row["gap_holes"] = holes
        else:
            row["ts_variant"] = True
        return row

    def _price_and_write(self, pos, h, src):
        super()._price_and_write(pos, h, src)
        if not self.ts_variant or pos.position_id.endswith("-ts") or not self._ov.get(pos.position_id):
            return
        if pos.reason == L.REASON_TIME:
            return
        sid = pos.position_id + "-ts"
        sh = dataclasses.replace(pos, position_id=sid, bot_id=pos.bot_id + "-ts", exit_ms=None, exit_slot=None, xs=None, sps_exit=None, reason=None,
                                 exit_stamp_ms=None, exit_call_ms=None)
        self.positions[sid] = sh  # keeps the pool's history from being pruned or trimmed until it is priced
        self._shadows.append([pos.decision_ms + L.HOLD_MS, sid])

    def on_row(self, row):
        super().on_row(row)
        if not self._shadows or self.hw_ms is None:
            return
        for item in list(self._shadows):
            due, sid = item
            if self.hw_ms < due:
                continue
            sps = self.sps()
            if sps is None:
                continue
            self._shadows.remove(item)
            sh = self.positions[sid]
            instant = self.hw_ms
            xs = self.slot_at(instant, sps)
            sh.exit_ms, sh.exit_slot, sh.xs, sh.sps_exit, sh.reason = instant, xs, xs, float(sps), L.REASON_TIME
            sh.exit_stamp_ms = sh.exit_call_ms = instant
            self.pending.setdefault(sh.pool, []).append(sid)
            self._try_pending(sh.pool, final=False)

    def ts_open(self):
        """shadows not yet priced at the end of the tape (not yet due, or due but pending): reported by the replay, never silently dropped"""
        return sum(1 for pid in self.positions if pid.endswith("-ts"))
