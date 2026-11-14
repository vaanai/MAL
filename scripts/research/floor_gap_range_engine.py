"""floor_gap_range_engine: research-side ArenaEngine subclass for the FLOOR gap-range replay. PAPER, in-sample research; no pinned or frozen file is edited.

What it changes, and only this:
  * gap_exclusion=False: the feed_gap exclusion (a data_loss gap overlapping decision-to-exit) is NOT applied; the position is priced through the hole with the
    prints the tape has (exit = first visible TP print, else the 24 h time stop, as the engine finds it). guard_ref_missing and missing_v exclusions are unchanged.
    gap_exclusion=True keeps the engine's behaviour exactly.
  * With gap_exclusion=False ONLY (default True: rows are byte-identical to the plain engine's), every trade / excluded row carries `gap_overlap` (bool: a data_loss gap of the position's program overlaps its span) and `gap_holes` ([[t_lo_ms, t_hi_ms], ...]).
  * ts_variant=True: for every gap-overlap position that did NOT exit by the 24 h time stop, a shadow position (bot_id + "-ts", position_id + "-ts") is priced at the
    first feed row at or after decision_ms + HOLD_MS (the rule's own time-stop instant), written under ledger/<bot_id>-ts/. The shadow never enters the rule's view
    (HostV2 filters engine.positions by its own bot ids). It is priced with the same price_leg code as a real time exit.
  * gap_exclusion="hold" (twin books; QP ruling 2026-11-14): the feed_gap exclusion is kept ONLY for a data_loss gap (matched by time OR slot, by program, as the engine's `_gap_hit`) that
    overlaps (a) the ENTRY window [decision_ms - NEARBY_WINDOW_S*1000, decision_ms + (d + max_lat k + 1)*sps_entry*1000] / slots [min(call_feed_slot, sd) - round(NEARBY_WINDOW_S / sps_entry),
    sd + d + max_lat k + 1] -> excluded `feed_gap_entry`; or (b) the EXIT window, from the pool's last print at or below xs + d + exit_lag (time: min(exit_ms, that print's time)) up to `need`
    (time: exit_ms + (exit_lag + 1)*sps_exit*1000; a derived_last exit extends both ends to the resolution instant, as `_span`) -> excluded `feed_gap_exit`. A gap elsewhere in the hold does not
    exclude. A priced trade row whose hold (`_span`: decision_ms .. exit window end) overlaps at least one gap carries gap_in_hold=true, blind_s (union of the overlapping gaps clipped to the hold, seconds)
    gap_count and gap_holes ([[t_lo_ms, t_hi_ms], ..] per gap, clipped to the hold; only gaps with a positive time overlap count, a match by slot alone or a zero-length gap shows as slot_only_gaps, see _hold_note); a row with no gap in its hold carries nothing extra (byte-equal to the strict book's row). An exit_invalid trade whose hold has a gap stays priced (strand) and is counted
    `exit_invalid_across_gap` (engine counter; the board derives its own count from the twin's rows). Take-profit: the host decides on the rows it has; nothing is added here (Q3). guard_ref_missing and missing_v are unchanged ("on"/"off" rows are byte-identical to before).
Exit-window time form: for a print whose order key carries no receive time (tx_index form) the print's time is estimated from the slot clock; that can narrow the time form, but every Gap also carries a slot range
and the window's slot form [ps, need] uses the print's true slot, so a gap in that stretch matches by slot whatever the estimate (QP comment on PR #1380, m1).
The decisions of the rule do not depend on the exclusion (the host does not read the engine's trade/excluded rows), so entries are identical with and without it.
"""
import dataclasses
import numpy as np
from tools import arena_rule_l1 as L
from tools.arena_paper import ArenaEngine, F, LATS

HOLD = "hold"  # gap_exclusion value of a "gap-in-hold twin" book (QP RULING 2026-11-14 board-gap-in-hold, Q1/Q2/Q3)


class GapRangeEngine(ArenaEngine):
    def __init__(self, *a, gap_exclusion=True, ts_variant=False, **k):
        super().__init__(*a, **k)
        self.hold = gap_exclusion == HOLD
        self.gap_exclusion, self.ts_variant = (True if self.hold else gap_exclusion), ts_variant
        self._hold_info = {}  # position_id -> {"gap_count", "blind_s"} (hold mode only)
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

    def _gap_matches(self, prog, span):
        """the gaps `_gap_hit` would match (program, then time OR slot overlap), all of them"""
        t_lo, t_hi, s_lo, s_hi = span
        return [g for g in self.gaps if not (g.prog not in (None, prog, "all") and not (g.prog == "pumpfun" and prog == "pump_bonding"))
                and ((g.t_lo <= t_hi and g.t_hi >= t_lo) or (g.s_lo <= s_hi and g.s_hi >= s_lo))]

    def _entry_window(self, pos):
        sps, d = pos.sps_entry, self._check_delay_set()
        kmax = max(self._k(lat, sps) for lat in LATS)
        t_hi = pos.decision_ms + int((d + kmax + 1) * sps * 1000)
        s_hi = pos.sd + d + kmax + 1
        s_lo = min(pos.call_feed_slot, pos.sd) - int(round(F.NEARBY_WINDOW_S / sps))
        return pos.decision_ms - int(F.NEARBY_WINDOW_S * 1000), t_hi, s_lo, s_hi

    def _exit_window(self, pos, h, src, need):
        d, lag = self._check_delay_set(), F.exit_lag_slots(pos.sps_exit)
        x_state = pos.xs + d + lag
        sl = h.arrays()["sl"]
        j = int(np.searchsorted(sl, x_state, "right")) - 1  # the pool's last print at or below the exit-state slot
        if j >= 0:
            ps = int(sl[j])
            key = h.keys[j]
            pt = int(key[2]) if len(key) > 2 and key[1] == 1 else pos.exit_ms - int(round((pos.xs - ps) * pos.sps_exit * 1000))  # receive-order keys carry t_recv_ms; else the slot clock
        else:
            ps, pt = x_state, pos.exit_ms
        _, t_hi, _, s_hi = self._span(pos, need, src)  # exit_ms + (lag + 1) * sps_exit; a derived_last exit extends to the resolution instant
        return min(pos.exit_ms, pt), t_hi, min(ps, need), s_hi

    def _hold_exclusion(self, pos, h, src, need):
        if pos.call_feed_slot is None:
            return "guard_ref_missing"
        if self._gap_matches(pos.prog, self._entry_window(pos)):
            return "feed_gap_entry"
        if self._gap_matches(pos.prog, self._exit_window(pos, h, src, need)):
            return "feed_gap_exit"
        saved, self.gaps = self.gaps, []  # missing_v only
        try:
            return super()._exclusion(pos, h, src, need)
        finally:
            self.gaps = saved

    def _hold_note(self, pos, src, need):
        """Flags of a priced trade. gap_count and gap_holes count only the matching gaps whose time interval overlaps the hold by more than zero ms; a matching gap with no such overlap (a
        zero-length gap, or a match by slot alone) is not a hole: it is counted in `slot_only_gaps` (field present only when > 0), so a flagged row can have blind_s 0, gap_holes [] and
        slot_only_gaps > 0 (the recon's tp_in_sim_gap needs a hole and never fires for it). The row is still flagged gap_in_hold, as the strict book leaves it out."""
        span = self._span(pos, need, src)
        gs = self._gap_matches(pos.prog, span)
        t_lo, t_hi = span[0], span[1]
        iv = sorted((max(g.t_lo, t_lo), min(g.t_hi, t_hi)) for g in gs if min(g.t_hi, t_hi) > max(g.t_lo, t_lo))
        blind, cur_lo, cur_hi = 0, None, None
        for lo, hi in iv:
            if cur_hi is None or lo > cur_hi:
                if cur_hi is not None:
                    blind += cur_hi - cur_lo
                cur_lo, cur_hi = lo, hi
            else:
                cur_hi = max(cur_hi, hi)
        if cur_hi is not None:
            blind += cur_hi - cur_lo
        self._hold_info[pos.position_id] = {"matched": len(gs), "gap_count": len(iv), "slot_only_gaps": len(gs) - len(iv), "blind_s": round(blind / 1000.0, 3),
                                            "gap_holes": [[lo, hi] for lo, hi in iv]}  # [t_lo_ms, t_hi_ms] per gap, clipped to the hold; times only

    def _exclusion(self, pos, h, src, need):
        if self.hold and not pos.position_id.endswith("-ts"):
            self._hold_note(pos, src, need)
            return self._hold_exclusion(pos, h, src, need)
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
            info = self._hold_info.get(pos.position_id) if self.hold and kind == "trade" else None
            if info and info["matched"]:
                row["gap_in_hold"], row["blind_s"], row["gap_count"] = True, info["blind_s"], info["gap_count"]
                row["gap_holes"] = info["gap_holes"]
                if info["slot_only_gaps"]:
                    row["slot_only_gaps"] = info["slot_only_gaps"]
            if not self.gap_exclusion:  # only the research replay (--gap-exclusion off) adds the fields; default rows are the plain engine's
                holes = self._ov.get(pos.position_id, [])
                row["gap_overlap"] = bool(holes)
                row["gap_holes"] = holes
        else:
            row["ts_variant"] = True
        return row

    def build_rows(self, pos, h, src):
        rows = super().build_rows(pos, h, src)
        if self.hold and (self._hold_info.get(pos.position_id) or {}).get("matched") and any(
                v.get("state") == "exit_invalid" for r in rows for leg in r["legs"].values() for v in leg.values()):
            self._count("exit_invalid_across_gap")
        return rows

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
