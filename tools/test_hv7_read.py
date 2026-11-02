"""Synthetic-only tests for tools/hv7_read.py (HV7 chunk C5). No tape, no drill or job output, no arena path."""
import collections
import functools
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

os.environ["MAL_ARENA_TEST"] = "1"

from tools import arena_paper as AP  # noqa: E402
from tools import hv7_read as X  # noqa: E402
from tools import hv7_runner as R  # noqa: E402

DAY = 86_400_000
T0 = 1_792_000_000_000 // DAY * DAY  # an arbitrary synthetic 00:00Z
HALT = T0 + 10 * DAY
SEND = 505_000
STACK = {"send": SEND, "rent": 2_039_280, "h_in": 0.002608, "h_out": 0.0016, "k_rounding": "ceil", "exit_lag_s": 0.55, "guard": 1.15, "portal_ppm": 0,
         "press_intercept_provisional": -1.42, "strand_rule": "both", "e1_floor_per_005": 384_022, "sps_entry": 0.4, "sps_exit": 0.4}
SHA = "abc1234"
STAKES = (0.25, 0.5, 1.0)
E1_OK = {"a_l": 40, "a_l_lt_20": False, "fallback_share": 0.0, "fallback_gt_10pct": False, "count_sha256": "x"}
FB_OK = {"L_not_evaluable": False, "excluded_cap_not_decidable": False, "reason": None, "fallback_share": 0.0, "attempts_L": 10, "rank_rows": 10,
         "entry_vs_rank_row_difference": False}
LATE = 10**15  # a synthetic "wall clock" far after every synthetic instant


@functools.lru_cache(maxsize=None)
def _legs(pnl, rule):
    flat, press, _, _ = X.AS._legs(rule, np.array([pnl]), np.array([True]), np.array([False]), np.array([0.0]), np.array([0.0]), float(SEND))
    return float(flat[0]), float(press[0])


def start_row(bot):
    return {"schema": "arena_trade_v1", "kind": "start", "trade_id": f"start-eng1-{bot}", "engine_id": "eng1", "bot_id": bot, "frozen_sha": SHA,
            "decision_ms": {"entry": T0 + 1000, "exit": None}, "code_head": SHA, "code_dirty": False, "stack": dict(STACK)}


def open_row(bot, pid, ms, lag=0):
    return {"schema": "arena_trade_v1", "kind": "open", "trade_id": f"{pid}-open", "position_id": pid, "bot_id": bot, "frozen_sha": SHA,
            "decision_ms": {"entry": ms + lag, "exit": None}, "stamp_ms": {"entry": ms, "exit": None}, "stack": dict(STACK), "code_head": SHA, "code_dirty": False,
            "engine_id": "eng1"}


def trade_row(bot, pid, ms, pool, pnl, stake, lag=0, rules=("total", "sc")):
    legs = {r: {} for r in rules}
    by = {}
    for rule in rules:
        flat, press = _legs(float(pnl), rule)
        for lat in ("1p3", "1p9"):
            legs[rule][f"flat_{lat}"] = {"state": "filled", "net_lamports": flat}
            legs[rule][f"press_{lat}"] = {"state": "filled", "net_lamports": press}
        by[rule] = {"pnl_nofail": float(pnl), "filled": True, "state": "filled", "strand": False, "e1_extra": 0.0, "p_press_provisional": 0.2}
    calc = {lat: {"landing_slot": 1, "Qe": 1.0, "Be": 1.0, "spot": 1.0, "ssb": 0.0, "nearby_lamports": 0.0, "Qx": 1.0, "Bx": 1.0, "by_rule": {k: dict(v) for k, v in by.items()}}
            for lat in ("1p3", "1p9")}
    return {"schema": "arena_trade_v1", "kind": "trade", "trade_id": f"{pid}-s{int(stake * 1000)}", "position_id": pid, "bot_id": bot, "frozen_sha": SHA,
            "decision_ms": {"entry": ms + lag, "exit": ms + lag + 5000}, "stamp_ms": {"entry": ms, "exit": None}, "stake_sol": stake, "prog": "pumpswap",
            "state_src": "tape", "stack": dict(STACK), "legs": legs, "calc": calc, "pool": pool, "v_src": {"entry": "row", "exit": "row", "span": {"row": 5}}}


def default_orders(arm, ms_list, pools="abcd"):
    """Decision-log order ids for bare instants, shaped like arm_book position ids: the n-th entry at one instant is pool pools[n]."""
    seen = collections.Counter()
    out = []
    for m in ms_list:
        d, rem = divmod(m - T0, DAY)
        i = rem // 180_000 - 1
        out.append((m, f"hv7.0t-{arm}-{d}-{i}-{pools[seen[m]]}"))
        seen[m] += 1
    return out


def ledger_orders(rows, ms_list, arm):
    """Order ids for explicit instants: each takes the next ledger position id stamped at that instant, else a synthetic id the ledger does not hold."""
    avail = collections.defaultdict(list)
    for r in rows:
        if r["kind"] in ("open", "refused"):
            avail[r["stamp_ms"]["entry"]].append(r["position_id"])
    return [(m, avail[m].pop(0) if avail[m] else f"declog-only-{arm}-{m}") for m in ms_list]


def periods(days, per_day):
    return [T0 + d * DAY + (i + 1) * 180_000 for d in range(days) for i in range(per_day)]


def arm_book(bot, days, per_day, pnl_fn, pools_fn=lambda d, i: ("a",), stakes=STAKES, lag=0, rules=("total", "sc")):
    """start row + open + one trade row per stake per position. pnl_fn(d, i, pool) -> lamports."""
    rows = [start_row(bot)]
    for d in range(days):
        for i in range(per_day):
            ms = T0 + d * DAY + (i + 1) * 180_000
            for pool in pools_fn(d, i):
                pid = f"{bot}-{d}-{i}-{pool}"
                rows.append(open_row(bot, pid, ms, lag))
                for st in stakes:
                    rows.append(trade_row(bot, pid, ms, pool, pnl_fn(d, i, pool), st, lag, rules))
    return rows


def win(d, i, pool="a"):
    return 4_000_000 + ((d * 7 + i * 3) % 5) * 1_000_000


class World:
    """A tmp registry (four HV7 books + hv7_halt lines), ledger and decision logs."""

    def __init__(self, tmp, halt=HALT, clock=T0):
        self.root = Path(tmp)
        self.reg = str(self.root / "bots.json")
        self.dec = self.root / "hv7root"
        self.ledger = str(self.dec / "engine-ledger")
        for arm in R.ARM_ORDER:
            AP.register_bot(R.bot_id(arm), SHA, clock, self.reg, deciding_stake=0.25, model_ids=(["m"] if arm == "L" else []), now_ms=0, verify_sha=False)
            AP._append_line(Path(self.reg), {"kind": "hv7_halt", "bot_id": R.bot_id(arm), "entries_halt_ms": halt})
        self.led = AP.Ledger(self.ledger)

    def add_rows(self, rows):
        for r in rows:
            self.led.append(r)

    def declog(self, arm, ms_list, ranks=(), pools="abcd"):
        d = self.dec / "decisions" / arm
        d.mkdir(parents=True, exist_ok=True)
        by_day = collections.defaultdict(list)
        orders = default_orders(arm, ms_list, pools) if all(isinstance(m, int) for m in ms_list) else list(ms_list)
        for m, oid in orders:
            by_day[X.utc_day(m)].append({"kind": "entry", "ms": m, "order_id": oid})
        for r in ranks:
            by_day[X.utc_day(r["ms"])].append(r)
        for day, rs in by_day.items():
            (d / f"{day}.jsonl").write_text("\n".join(json.dumps(r) for r in rs) + "\n")


def with_world(test):
    @functools.wraps(test)
    def inner(self):
        with tempfile.TemporaryDirectory() as tmp:
            w = World(tmp)
            old = R.READ_ROOTS
            R.READ_ROOTS = tuple(old) + (str(w.dec),)
            try:
                with mock.patch("tools.arena_paper.shutil.disk_usage", lambda p: collections.namedtuple("U", "total used free")(1000, 500, 500)):
                    test(self, w)
            finally:
                R.READ_ROOTS = old
    return inner


def gate(w, arm, rows, ems, now, **kw):
    kw.setdefault("e1", E1_OK)
    kw.setdefault("n_books", 30)
    regn = X.load_registration(w.reg)
    orders = kw.pop("entry_orders", None)
    if orders is None:
        orders = default_orders(arm, ems)
    return X.gate_read(arm, now_ms=now, regn=regn, rows=rows, entry_decisions_ms=[m for m, _ in orders], entry_orders=orders, **kw)


class SignFlip(unittest.TestCase):
    def test_d5_min_p(self):
        self.assertEqual(X.sign_flip_min_p(5), 0.03125)
        self.assertEqual(X.sign_flip_p([1.0] * 5), 0.03125)
        self.assertGreater(X.sign_flip_p([1.0] * 5), X.CONTRAST_ALPHA)
        self.assertEqual(X.sign_flip_p([1.0] * 6), 0.015625)

    def test_zero_days_do_not_change_p(self):
        self.assertEqual(X.sign_flip_p([1.0] * 6 + [0.0] * 4), 0.015625)


class Helpers(unittest.TestCase):
    def test_day_block_boot_constant_and_blocks(self):
        days = ["d1"] * 4 + ["d2"] * 4 + ["d3"] * 4
        self.assertAlmostEqual(X.day_block_boot_lo([0.01] * 12, days), 0.01)
        x2 = [0.1] * 4 + [-0.001] * 8
        self.assertLess(X.day_block_boot_lo(x2, days), 0.0)
        self.assertEqual(X.day_block_boot_lo(x2, days), X.day_block_boot_lo(x2, days))

    def test_mde_line_quotes_design(self):
        s = X.mde_line(10)
        self.assertIn("1.9%", s)
        self.assertIn("rank IC about 0.17;", s)
        self.assertIn("no skill detected", s)
        nine = X.mde_line(9)
        self.assertIn("not tabulated", nine)
        self.assertIn("not tabulated at D = 9", nine)
        self.assertIn("larger than at D = 10", nine)
        fourteen = X.mde_line(14)                       # the D of every clock pinned on 2026-10-30 (H = 14 days): more days, smaller MDE
        self.assertIn("not tabulated at D = 14", fourteen)
        self.assertIn("smaller than at D = 10", fourteen)
        self.assertNotIn("larger", fourteen)
        self.assertAlmostEqual(X.mde_ic(1.9, 8), 0.1665, places=3)

    def test_holm_block(self):
        h = X.holm_block(30, {"a": 0.01, "b": 0.2})
        self.assertAlmostEqual(h["plain_line_1_minus_0.95_pow_N"], 1 - 0.95 ** 30)
        self.assertEqual(h["holm"]["a"]["rank"], 1)
        self.assertAlmostEqual(h["holm"]["a"]["holm_level"], 0.05 / 2)
        self.assertEqual(X.holm_block(30, None)["holm_text"], "Holm rank: not computed, no p list given")

    def test_t_e_offset_comes_from_the_runner(self):
        self.assertEqual(X.T_E_MINUS_T_D_MS, R.T_E_OFFSET_MS - R.T_D_OFFSET_MS)

    def test_priced_trades_filters_the_deciding_stake(self):
        rows = arm_book("hv7.0t-L", 6, 6, win)  # 36 positions x 3 stakes
        tr, info = X.priced_trades(rows, T0, None, 0.25)
        self.assertEqual(len(tr), 36)
        self.assertEqual(info["priced"], 36)
        self.assertEqual({r["stake_sol"] for r in tr}, {0.25})


class Registration(unittest.TestCase):
    @with_world
    def test_timing_comes_from_the_registry(self, w):
        regn = X.load_registration(w.reg)
        self.assertEqual((regn["clock_start_ms"], regn["halt_ms"], regn["D"], regn["stake"]), (T0, HALT, 10, 0.25))
        with tempfile.TemporaryDirectory() as t2:
            w2 = World(t2, halt=T0 + 9 * DAY)
            self.assertEqual(X.load_registration(w2.reg)["D"], 9)

    @with_world
    def test_missing_or_disagreeing_halt_refused(self, w):
        AP._append_line(Path(w.reg), {"kind": "hv7_halt", "bot_id": "hv7.0t-U", "entries_halt_ms": HALT + DAY})
        with self.assertRaises(X.Refused):
            X.load_registration(w.reg)
        with tempfile.TemporaryDirectory() as t2:
            p = Path(t2) / "bots.json"
            AP.register_bot("hv7.0t-L", SHA, T0, p, deciding_stake=0.25, model_ids=[], now_ms=0, verify_sha=False)
            with self.assertRaises(X.Refused):
                X.load_registration(p)


class Gate(unittest.TestCase):
    def setup_book(self, w, arm="L", days=6, per_day=20, pnl=win, **kw):
        rows = arm_book(R.bot_id(arm), days, per_day, pnl, **kw)
        ems = periods(days, per_day)
        w.add_rows(rows)
        return rows, ems

    @with_world
    def test_pass_known_answer_and_stake_filter(self, w):
        rows, ems = self.setup_book(w)
        b = X.AS.blind_read_instant(ems, T0)
        self.assertEqual(b, T0 + 5 * DAY)
        r = gate(w, "L", w.led.scan_bot("hv7.0t-L")[0], ems, b + DAY)
        self.assertEqual(r["status"], "PASS", r.get("cells"))
        c = r["cells"]["flat_1p9_sc"]
        self.assertEqual((c["n"], c["days"], c["days_pos"]), (100, 5, 5))  # 3 stake rows per position do not triple n
        self.assertEqual(r["priced_trades"], 100)
        self.assertIn("screen pass; confirmation owed", r["status_text"])
        self.assertTrue(r["no_book_has_cleared_the_promotion_gate"])
        self.assertFalse(r["boundary_difference"])
        self.assertTrue(any(c["stake"] != 0.25 or "_1p3_" in c["cell"] for c in r["report_only_cells"]))

    @with_world
    def test_multiplicity_block_in_the_gate(self, w):
        rows, ems = self.setup_book(w)
        b = T0 + 5 * DAY
        r = gate(w, "L", w.led.scan_bot("hv7.0t-L")[0], ems, b + DAY, n_books=30)
        m = r["multiplicity"]
        self.assertEqual(m["N"], 30)
        self.assertAlmostEqual(m["plain_line_1_minus_0.95_pow_N"], 1 - 0.95 ** 30)
        self.assertEqual(m["holm_text"], "Holm rank: not computed, no p list given")
        r = gate(w, "L", w.led.scan_bot("hv7.0t-L")[0], ems, b + DAY, other_ps={"other": 0.4})
        self.assertEqual(r["multiplicity"]["holm"]["hv7.0t-L"]["rank"], 1)
        self.assertEqual(r["multiplicity"]["holm"]["other"]["rank"], 2)

    @with_world
    def test_refusals_before_b_and_before_b_plus_exit_window(self, w):
        rows, ems = self.setup_book(w)
        b = T0 + 5 * DAY
        rows = w.led.scan_bot("hv7.0t-L")[0]
        with self.assertRaises(X.Refused):
            gate(w, "L", rows, [m for m in ems if m < T0 + 4 * DAY], b + DAY)
        with self.assertRaises(X.Refused):
            gate(w, "L", rows, ems, b + X.EXIT_WINDOW_MS - 1)
        gate(w, "L", rows, ems, b + X.EXIT_WINDOW_MS)
        with self.assertRaises(X.Refused):
            gate(w, "U", rows, ems, b + DAY)

    @with_world
    def test_losing_top3_and_minority_days_fail(self, w):
        rows, ems = self.setup_book(w, pnl=lambda d, i, p: -2_000_000)
        r = gate(w, "L", w.led.scan_bot("hv7.0t-L")[0], ems, T0 + 8 * DAY)
        self.assertEqual(r["status"], "FAIL")
        with tempfile.TemporaryDirectory() as t2:
            w2 = World(t2)
            rows2, ems2 = arm_book("hv7.0t-L", 6, 20, lambda d, i, p: 4e8 if d == 0 else -1_000_000), periods(6, 20)
            w2.add_rows(rows2)
            r = gate(w2, "L", w2.led.scan_bot("hv7.0t-L")[0], ems2, T0 + 8 * DAY)
            self.assertEqual(r["status"], "FAIL")
            self.assertTrue(any("not a majority" in m for c in r["cells"].values() for m in c["misses"]))

    @with_world
    def test_excluded_share_over_5pct_is_not_decidable(self, w):
        rows, ems = self.setup_book(w)
        # exclude 6 of the 100 positions before B (6% > 5%): positions 0-5 of day 0
        ex = {f"hv7.0t-L-0-{i}-a" for i in range(6)}
        rows = [dict(r, v_src={"entry": "none", "exit": "row", "span": {}}) if (r["kind"] == "trade" and r["position_id"] in ex) else r for r in w.led.scan_bot("hv7.0t-L")[0]]
        r = gate(w, "L", rows, ems, T0 + 8 * DAY)
        self.assertEqual(r["status"], "NOT_DECIDABLE")
        self.assertTrue(any("excluded" in m for m in r["reasons"]), r["reasons"])

    @with_world
    def test_orphan_is_pending_then_not_decidable(self, w):
        rows, ems = self.setup_book(w)
        last = f"hv7.0t-L-4-19-a"
        rows = [r for r in w.led.scan_bot("hv7.0t-L")[0] if not (r["kind"] == "trade" and r["position_id"] == last)]
        b = T0 + 5 * DAY
        with self.assertRaises(X.Refused):  # younger than 24 h: PENDING, not read
            gate(w, "L", rows, ems, b + 3_600_000)
        r = gate(w, "L", rows, ems, b + 30 * 3_600_000)  # older: restart orphan
        self.assertEqual(r["status"], "NOT_DECIDABLE")
        self.assertEqual(r["restart_orphans"], 1)

    @with_world
    def test_incident_before_b_is_not_decidable(self, w):
        rows, ems = self.setup_book(w)
        inc = [{"kind": "buy_refused", "at_ms": T0 + 2 * DAY, "day_file": "x"}]
        r = gate(w, "L", w.led.scan_bot("hv7.0t-L")[0], ems, T0 + 8 * DAY, incidents=inc)
        self.assertEqual(r["status"], "NOT_DECIDABLE")
        self.assertTrue(any("incident" in m for m in r["reasons"]))

    @with_world
    def test_missing_strand_rule_is_report_only(self, w):
        rows, ems = self.setup_book(w, rules=("total",))
        r = gate(w, "L", w.led.scan_bot("hv7.0t-L")[0], ems, T0 + 8 * DAY)
        self.assertEqual(r["status"], "REPORT_ONLY")

    @with_world
    def test_stored_flat_leg_mismatch_refused(self, w):
        rows, ems = self.setup_book(w)
        rows = w.led.scan_bot("hv7.0t-L")[0]
        for r in rows:
            if r["kind"] == "trade":
                r["legs"]["sc"]["flat_1p9"]["net_lamports"] += 1.0
                break
        with self.assertRaises(X.AS.Refused):
            gate(w, "L", rows, ems, T0 + 8 * DAY)

    @with_world
    def test_e1_entry_rate_makes_every_read_not_decidable(self, w):
        rows, ems = self.setup_book(w)
        e1 = dict(E1_OK, a_l=19, a_l_lt_20=True)
        r = gate(w, "L", w.led.scan_bot("hv7.0t-L")[0], ems, T0 + 8 * DAY, e1=e1)
        self.assertEqual(r["status"], "NOT_DECIDABLE")
        self.assertTrue(r["reasons"][0].startswith("E1_entry_rate"))

    @with_world
    def test_e1_fallback_binds_l_only_even_if_window_share_is_low(self, w):
        rows, ems = self.setup_book(w)
        e1 = dict(E1_OK, fallback_gt_10pct=True, fallback_share=0.2)
        fb = dict(FB_OK, fallback_share=0.05)  # the [T0, B) share is under 10%
        r = gate(w, "L", w.led.scan_bot("hv7.0t-L")[0], ems, T0 + 8 * DAY, e1=e1, fallback=fb)
        self.assertEqual(r["status"], "NOT_EVALUABLE")
        self.assertTrue(r["reasons"][0].startswith("E1_fallback"))

    @with_world
    def test_e1_missing_is_refused(self, w):
        rows, ems = self.setup_book(w)
        with self.assertRaises(X.Refused):
            gate(w, "L", w.led.scan_bot("hv7.0t-L")[0], ems, T0 + 8 * DAY, e1=None)

    @with_world
    def test_excluded_period_cap_is_named_not_decidable(self, w):
        rows, ems = self.setup_book(w)
        r = gate(w, "L", w.led.scan_bot("hv7.0t-L")[0], ems, T0 + 8 * DAY, fallback=dict(FB_OK, excluded_cap_not_decidable=True))
        self.assertEqual(r["status"], "NOT_DECIDABLE")
        self.assertTrue(any("10% cap (NOT_DECIDABLE)" in m for m in r["reasons"]))

    @with_world
    def test_b_after_horizon_not_decidable(self, w):
        r = gate(w, "L", [], [m for m in periods(3, 20)], T0 + 12 * DAY)
        self.assertEqual(r["status"], "NOT_DECIDABLE")
        self.assertEqual(r["reason"], "B_not_reached_by_T0_plus_H")
        with self.assertRaises(X.Refused):
            gate(w, "L", [], periods(3, 20), T0 + 9 * DAY)

    def book_ledger(self, w, per_day, days=6):
        w.add_rows(arm_book("hv7.0t-L", days, per_day, win))
        return w.led.scan_bot("hv7.0t-L")[0]

    @with_world
    def test_p9_ledger_has_extra_attempts_is_final_not_decidable(self, w):
        rows = self.book_ledger(w, 20)  # ledger: 6 days, natural instant day 5
        ems = [m for m in periods(6, 20) if m >= T0 + DAY]  # the decision log lacks day 0: its B is day 6
        for now in (T0 + 8 * DAY, T0 + 40 * DAY):
            r = gate(w, "L", rows, ems, now)
            self.assertEqual(r["status"], "NOT_DECIDABLE")
            self.assertIn("never ordered", r["reasons"][0])
            rc = r["attempt_reconciliation"]
            self.assertEqual((rc["decision_log_attempts_before_b"], rc["ledger_attempts_before_b"], rc["lost_attempts"]), (100, 120, 0))
            self.assertEqual(rc["extra_ledger_attempts"], 20)
            self.assertEqual(rc["lost_share"], 0.0)
            self.assertIn("multiplicity", r)

    @with_world
    def test_p10_ledger_never_reaches_its_instant(self, w):
        rows = self.book_ledger(w, 10)  # half the attempts are in the ledger
        ems = periods(6, 20)
        r = gate(w, "L", rows, ems, T0 + 8 * DAY)
        self.assertEqual(r["status"], "NOT_DECIDABLE")
        self.assertEqual(r["attempt_reconciliation"]["lost_attempts"], 50)
        self.assertIn("lost", r["reasons"][0])

    @with_world
    def test_p10b_small_loss_waits_then_final_not_decidable(self, w):
        w.add_rows(arm_book("hv7.0t-L", 5, 20, win))  # the ledger holds days 0-4 only: 97 attempts once three are lost, so it has no natural instant
        rows = [r for r in w.led.scan_bot("hv7.0t-L")[0] if not r.get("position_id", "").startswith(("hv7.0t-L-1-0-", "hv7.0t-L-1-1-", "hv7.0t-L-1-2-"))]
        ems = periods(6, 20)
        with self.assertRaises(X.Refused):
            gate(w, "L", rows, ems, T0 + 8 * DAY)
        r = gate(w, "L", rows, ems, HALT + X.EXIT_WINDOW_MS)
        self.assertEqual(r["status"], "NOT_DECIDABLE")
        self.assertIn("never reached", r["reasons"][0])
        self.assertEqual(r["attempt_reconciliation"]["lost_attempts"], 3)

    @with_world
    def test_lost_attempts_join_the_5pct_numerator(self, w):
        rows = self.book_ledger(w, 20)
        r = gate(w, "L", rows, periods(6, 21), T0 + 8 * DAY)  # 105 decided, 100 in the ledger: 5 lost = 4.76%
        self.assertEqual(r["status"], "PASS")
        self.assertEqual(r["attempt_reconciliation"]["lost_attempts"], 5)
        r = gate(w, "L", rows, periods(6, 22), T0 + 8 * DAY)  # 110 decided: 10 lost = 9.09%
        self.assertEqual(r["status"], "NOT_DECIDABLE")
        self.assertTrue(any("lost" in m for m in r["reasons"]))
        self.assertEqual(r["scorer_status_before_hv7_rules"], "PASS")
        self.assertIn("not a verdict", r["cells_not_a_verdict"])

    @with_world
    def test_coverage_separates_outage_from_no_trade_hours(self, w):
        hrs = [h for h in range(48) if h not in (5, 6, 7)]  # hours 5-7: the runner was down
        w.declog("L", [T0 + h * 3_600_000 + 60_000 for h in hrs if h != 9])  # hour 9: runner up, L did not trade
        d = w.dec / "decisions" / "SCAN"
        d.mkdir(parents=True)
        by = collections.defaultdict(list)
        for h in hrs:
            m = T0 + h * 3_600_000 + 60_000
            by[X.utc_day(m)].append({"kind": "period", "ms": m})
        for day, rs in by.items():
            (d / f"{day}.jsonl").write_text("\n".join(json.dumps(r) for r in rs) + "\n")
        cv = X.coverage_check(w.dec, T0, T0 + 2 * DAY)
        self.assertEqual((cv["window_hours"], cv["outage_hours"], cv["runner_up_hours"]), (48, 3, 45))
        self.assertEqual(cv["L"]["no_trade_hours"], 1)
        self.assertEqual(cv["R"]["no_trade_hours"], 45)
        self.assertTrue(cv["report_only"])

    @with_world
    def test_gate_swapped_pools_same_count_is_final_not_decidable(self, w):
        # QP round 4: the right count, the wrong identity. The log ordered pool d at every instant, the ledger holds pool a at the same instants.
        rows = self.book_ledger(w, 20)
        orders = [(m, f"hv7.0t-L-{(m - T0) // DAY}-{((m - T0) % DAY) // 180_000 - 1}-d") for m in periods(6, 20)]
        for now in (T0 + 8 * DAY, T0 + 40 * DAY):
            r = gate(w, "L", rows, [m for m, _ in orders], now, entry_orders=orders)
            self.assertEqual(r["status"], "NOT_DECIDABLE")
            self.assertIn("never ordered", r["reasons"][0])
            rc = r["attempt_reconciliation"]
            self.assertEqual((rc["decision_log_attempts_before_b"], rc["ledger_attempts_before_b"], rc["lost_attempts"], rc["foreign_ledger_positions"]), (100, 100, 100, 100))

    @with_world
    def test_gate_phantom_instants_same_count_is_final_not_decidable(self, w):
        # the ledger holds attempts at instants (+90 s) the decision log never logged; the counts are equal
        rows = self.book_ledger(w, 20)
        orders = [(m + 90_000, f"phantom-{m}") for m in periods(6, 20)]
        r = gate(w, "L", rows, [m for m, _ in orders], T0 + 8 * DAY, entry_orders=orders)
        self.assertEqual(r["status"], "NOT_DECIDABLE")
        self.assertTrue(any("never ordered" in m for m in r["reasons"]))

    @with_world
    def test_gate_lost_and_foreign_both_printed(self, w):
        rows = self.book_ledger(w, 20)
        orders = default_orders("L", periods(6, 21))  # 5 logged-only ids (lost) and none foreign
        r = gate(w, "L", rows, [m for m, _ in orders], T0 + 8 * DAY, entry_orders=orders)
        rc = r["attempt_reconciliation"]
        self.assertEqual((rc["lost_attempts"], rc["foreign_ledger_positions"], rc["extra_ledger_attempts"]), (5, 0, 0))

    @with_world
    def test_gate_requires_order_ids_that_match_the_instants(self, w):
        rows = self.book_ledger(w, 20)
        with self.assertRaises(X.Refused):
            X.gate_read("L", now_ms=T0 + 8 * DAY, regn=X.load_registration(w.reg), rows=rows, entry_decisions_ms=periods(6, 20), e1=E1_OK, n_books=30)
        with self.assertRaises(X.Refused):
            X.gate_read("L", now_ms=T0 + 8 * DAY, regn=X.load_registration(w.reg), rows=rows, entry_decisions_ms=periods(6, 20), e1=E1_OK, n_books=30,
                        entry_orders=default_orders("L", periods(6, 19)))

    @with_world
    def test_gate_trade_rows_only_at_another_stake_count_as_lost(self, w):
        # QP #1044 (p3 shape): ids all match, but k positions keep trade rows at stakes 0.5/1.0 only; the scorer treats them as closed, the deciding book drops them
        rows = self.book_ledger(w, 20)
        gone = {f"hv7.0t-L-{d}-{i}-a" for d in range(3) for i in range(2)}

        def drop(k):
            ids = set(sorted(gone)[:k])
            return [r for r in rows if not (r.get("kind") == "trade" and r["position_id"] in ids and abs(r["stake_sol"] - 0.25) < 1e-12)]
        r5 = gate(w, "L", drop(5), periods(6, 20), T0 + 8 * DAY)
        rc = r5["attempt_reconciliation"]
        self.assertEqual((rc["lost_attempts"], rc["foreign_ledger_positions"], rc["no_deciding_stake_row"]), (5, 0, 5))
        self.assertEqual(r5["status"], "NOT_DECIDABLE")  # QP p3 exactly: 5 moved (4.545%) no longer reads PASS; an integrity failure, not only the 5% cap
        self.assertTrue(any("closing rows missing at the deciding stake" in m for m in r5["reasons"]))
        r6 = gate(w, "L", drop(6), periods(6, 20), T0 + 8 * DAY)
        self.assertEqual(r6["status"], "NOT_DECIDABLE")
        self.assertTrue(any("lost" in m for m in r6["reasons"]))
        self.assertEqual(r6["attempt_reconciliation"]["lost_attempts"], 6)
        self.assertEqual(gate(w, "L", rows, periods(6, 20), T0 + 8 * DAY)["attempt_reconciliation"]["no_deciding_stake_row"], 0)

    @with_world
    def test_gate_row_without_stamp_ms_entry_refused_then_final_not_decidable(self, w):
        rows = self.book_ledger(w, 20)
        rows = [dict(r) for r in rows]
        for r in rows:
            if r.get("kind") == "open":
                r["stamp_ms"] = {"entry": None, "exit": None}
                break
        with self.assertRaises(X.Refused):
            gate(w, "L", rows, periods(6, 20), T0 + 8 * DAY)
        r = gate(w, "L", rows, periods(6, 20), HALT + X.EXIT_WINDOW_MS)
        self.assertEqual(r["status"], "NOT_DECIDABLE")
        self.assertTrue(any("stamp_ms.entry" in m for m in r["reasons"]))

    @with_world
    def test_worst_case_line_when_attempts_are_lost(self, w):
        rows = self.book_ledger(w, 20)
        r = gate(w, "L", rows, periods(6, 21), T0 + 8 * DAY)  # 5 lost, still reads
        wc = r["lost_attempts_worst_case"]
        self.assertEqual((wc["lost"], wc["report_only"]), (5, True))
        self.assertEqual(wc["cells"]["flat_1p9_sc"]["n"], 105)
        self.assertIn("per_trade_cells_survive", wc)
        self.assertNotIn("pass_survives", wc)
        self.assertFalse(wc["day_rule_imputed"])
        self.assertAlmostEqual(wc["imputed_pnl_sol_each"], -0.25 - 2 * SEND / 1e9)  # -stake - round-trip fees (2 x send; deciding rent 0)
        self.assertIn("NOT imputed", wc["note"])
        self.assertIn("0.0015 SOL", wc["note"])
        r0 = gate(w, "L", rows, periods(6, 20), T0 + 8 * DAY)
        self.assertNotIn("lost_attempts_worst_case", r0)

    @with_world
    def test_lost_check_runs_without_a_deciding_stake_book(self, w):
        w.add_rows(arm_book("hv7.0t-L", 6, 20, win, stakes=(0.5,)))  # no trade rows at the deciding stake
        r = gate(w, "L", w.led.scan_bot("hv7.0t-L")[0], periods(6, 24), T0 + 8 * DAY)  # 120 decided before B vs 100 in the ledger
        self.assertEqual(r["status"], "NOT_DECIDABLE")
        self.assertTrue(any("lost" in m for m in r["reasons"]))


def contrast_rows(days, per_day, l_pick, pnl, lag=0, extra_u_pools=("a", "b", "c", "d"), r_pool="c", s_pool="b"):
    mk = lambda bot, pools: arm_book(bot, days, per_day, lambda d, i, p: pnl[p], lambda d, i: pools, stakes=(0.25,), lag=lag)  # noqa: E731
    return {"L": mk("hv7.0t-L", (l_pick,)), "R": mk("hv7.0t-R", (r_pool,)), "S": mk("hv7.0t-S", (s_pool,)), "U": mk("hv7.0t-U", tuple(extra_u_pools))}


PNL = {"a": 3e7, "b": 1e7, "c": -1e7, "d": -3e7}


class Contrast(unittest.TestCase):
    def regn(self, days=10):
        return {"clock_start_ms": T0, "halt_ms": T0 + days * DAY, "D": days, "stake": 0.25, "registry": {}}

    def read(self, rows, days=10, now=None, fb=FB_OK, e1=E1_OK, ranks=(), ems=None, **kw):
        h = T0 + days * DAY
        if ems is None:  # by default the decision log agrees with the ledger
            ords = {a: [(r["stamp_ms"]["entry"], r["position_id"]) for r in rs if r["kind"] in ("open", "refused")] for a, rs in rows.items()}
            ems = {a: [m for m, _ in o] for a, o in ords.items()}
            kw.setdefault("entry_orders_by_arm", ords)
        kw.setdefault("entry_ms_by_arm", ems)
        if "entry_orders_by_arm" not in kw:
            kw["entry_orders_by_arm"] = {a: ledger_orders(rows[a], ms, a) for a, ms in kw["entry_ms_by_arm"].items()}
        return X.contrast_read(now_ms=h + X.EXIT_WINDOW_MS if now is None else now, regn=self.regn(days), rows_by_arm=rows, l_rank_rows=ranks, fallback=fb, e1=e1,
                               n_books=30, **kw)

    def build(self, days, pick, lag=0, per_day=4):
        return contrast_rows(days, per_day, pick, PNL, lag=lag)

    def test_refused_before_h_plus_exit_window(self):
        rows = self.build(10, "a")
        h = T0 + 10 * DAY
        for now in (h, h + X.EXIT_WINDOW_MS - 1):
            with self.assertRaises(X.Refused):
                self.read(rows, now=now)
        self.read(rows, now=h + X.EXIT_WINDOW_MS + 30 * 3_600_000)

    def test_pending_refused_orphan_not_decidable(self):
        rows = self.build(10, "a")
        rows["L"].append(open_row("hv7.0t-L", "hv7.0t-L-late", T0 + 10 * DAY - 3_600_000))  # decided 1 h before the window end, never closed
        with self.assertRaises(X.Refused):
            self.read(rows)  # about 2 h old at the read: PENDING (< 24 h)
        res = self.read(rows, now=T0 + 10 * DAY + 40 * 3_600_000)
        self.assertEqual(res["status"], "NOT_DECIDABLE")
        self.assertTrue(any("orphan" in m for m in res["reasons"]))

    def test_e1_entry_rate_not_decidable(self):
        res = self.read(self.build(10, "a"), e1=dict(E1_OK, a_l=3, a_l_lt_20=True))
        self.assertEqual(res["status"], "NOT_DECIDABLE")
        self.assertTrue(res["reasons"][0].startswith("E1_entry_rate"))
        self.assertIn("N", res["multiplicity"])

    def test_e1_fallback_blocks_l_even_when_window_share_is_low(self):
        res = self.read(self.build(10, "a"), e1=dict(E1_OK, fallback_gt_10pct=True), fb=dict(FB_OK, fallback_share=0.01))
        self.assertEqual(res["status"], "NOT_EVALUABLE")
        self.assertEqual(res["contrasts"]["L-U"]["reason"], "E1_fallback")
        self.assertIn(res["contrasts"]["S-U"]["status"], ("PASS", "NOT_PASS", "NOT_DECIDABLE"))

    def test_excluded_share_over_5pct_arm_not_decidable(self):
        rows = self.build(10, "a")
        k = 0
        for r in rows["R"]:
            if r["kind"] == "trade" and k < 5:
                r["v_src"] = {"entry": "none", "exit": "row", "span": {}}
                k += 1
        res = self.read(rows)  # 5 of 40 = 12.5%
        self.assertEqual(res["status"], "NOT_DECIDABLE")
        self.assertTrue(any("excluded" in m for m in res["reasons"]))

    def test_incident_in_window_not_decidable(self):
        res = self.read(self.build(10, "a"), incidents_by_arm={"S": [{"kind": "buy_refused", "at_ms": T0 + DAY, "day_file": "x"}]})
        self.assertEqual(res["status"], "NOT_DECIDABLE")

    def test_skill_known_answer_and_stake_filter(self):
        rows = self.build(10, "a")
        for a in rows:  # add the other stakes: they must not enter
            extra = []
            for r in rows[a]:
                if r["kind"] == "trade":
                    for st in (0.5, 1.0):
                        extra.append(dict(r, stake_sol=st, trade_id=r["trade_id"] + f"x{st}", calc=json.loads(json.dumps(r["calc"]))))
            rows[a] += extra
        res = self.read(rows)
        lu = res["contrasts"]["L-U"]
        self.assertEqual((res["status"], res["D"], lu["min_nonzero_days"]), ("PASS", 10, 10))
        for s in lu["subcells"].values():
            self.assertEqual(s["p"], 2.0 ** -10)
            self.assertEqual(s["periods"], 40)
            self.assertEqual((s["periods_dropped_a"], s["periods_dropped_b"]), (0, 0))
        self.assertIn("not a promotion", res["verdict_text"])
        self.assertIn("confirmation owed", res["verdict_text"])
        self.assertTrue(res["a_skill_pass_is_not_a_promotion"])

    def test_dropped_periods_reported_per_pair(self):
        rows = self.build(10, "a")
        rows["S"] = [r for r in rows["S"] if r.get("position_id", "").split("-")[-3:-1] != ["9", "3"]]  # S misses one period
        res = self.read(rows)
        s = res["contrasts"]["L-S"]["subcells"]["flat_1p9_sc"]
        self.assertEqual((s["periods"], s["periods_dropped_a"], s["periods_dropped_b"]), (39, 1, 0))

    def test_worse_than_random_and_few_days(self):
        res = self.read(self.build(10, "d"))
        self.assertEqual(res["status"], "NOT_PASS")
        self.assertEqual(res["verdict_text"], "no skill detected; MDE IC about 0.17")
        res = self.read(self.build(9, "d"), days=9)
        self.assertIn("not tabulated", res["verdict_text"])
        self.assertNotIn("about 0.17", res["verdict_text"])
        self.assertIn("larger than at D = 10", res["verdict_text"])
        res = self.read(self.build(14, "d"), days=14)          # H = 14 days at the clocks pinned on 2026-10-30
        self.assertEqual(res["status"], "NOT_PASS")
        self.assertIn("not tabulated and is smaller than at D = 10", res["verdict_text"])
        res = self.read(self.build(7, "a"), days=7)
        self.assertEqual((res["status"], res["reason"]), ("NOT_DECIDABLE", "contrast_days"))
        res = self.read(self.build(5, "a"), days=5)
        self.assertEqual(res["status"], "NOT_DECIDABLE")

    def test_secondaries_holm_and_flags(self):
        res = self.read(self.build(10, "a"))
        c = res["contrasts"]
        self.assertEqual(set(c), {"L-U", "L-R", "L-S", "S-U", "R-U"})
        self.assertEqual(set(res["holm"]["holm"]), {"L-R", "L-S", "S-U", "R-U"})
        self.assertEqual(res["multiplicity"]["holm_text"], "Holm rank: not computed, no p list given")
        res = self.read(self.build(7, "a"), days=7)
        self.assertEqual(set(res["holm"]["flags"]), {"L-R", "L-S", "S-U", "R-U"})

    def test_same_pool_zero_difference_no_fault(self):
        rows = self.build(10, "a")
        rows["R"] = contrast_rows(10, 4, "a", PNL, r_pool="c")["R"]
        rows["R"] = arm_book("hv7.0t-R", 10, 4, lambda d, i, p: PNL[p], lambda d, i: ("a", "b", "c", "d"), stakes=(0.25,))
        res = self.read(rows)
        for s in res["contrasts"]["R-U"]["subcells"].values():
            self.assertAlmostEqual(s["total_diff_sol"], 0.0, places=12)
        self.assertFalse(res["R_minus_U_fault_flag"])

    def test_fault_check_is_two_tailed(self):
        res = self.read(self.build(10, "a"))  # R (pool c) is far BELOW U in every day
        self.assertLess(res["contrasts"]["R-U"]["subcells"]["flat_1p9_sc"]["mean_daily_diff_sol"], 0)
        self.assertTrue(res["R_minus_U_fault_flag"])
        res = self.read(contrast_rows(10, 4, "a", PNL, r_pool="a"))  # R far above U
        self.assertTrue(res["R_minus_U_fault_flag"])

    def test_rank_ic_perfect_order_and_write_lag(self):
        def ranks():
            return [{"kind": "rank", "ms": m - X.T_E_MINUS_T_D_MS, "fallback": False, "order": ["a", "b", "c", "d"]} for m in periods(10, 4)]

        res = self.read(self.build(10, "a"), ranks=ranks())
        self.assertAlmostEqual(res["rank_ic"]["mean_daily_ic"], 1.0)
        self.assertEqual(res["rank_ic"]["periods"], 40)
        res = self.read(self.build(10, "a", lag=37), ranks=ranks())  # decision_ms is the write instant, 37 ms after the stamp
        self.assertEqual(res["rank_ic"]["periods"], 40)
        self.assertEqual(res["rank_ic"]["p"], 2.0 ** -10)
        self.assertEqual(res["contrasts"]["L-U"]["subcells"]["flat_1p9_sc"]["periods"], 40)

    def test_rank_row_vs_entry_count_flagged(self):
        res = self.read(self.build(10, "a"), fb=dict(FB_OK, attempts_L=41, rank_rows=40, entry_vs_rank_row_difference=True))
        self.assertTrue(res["rank_ic"]["entry_vs_rank_row_difference"])
        self.assertEqual((res["rank_ic"]["l_entry_rows"], res["rank_ic"]["l_rank_rows"]), (41, 40))

    def test_no_u_trades_clear_refusal(self):
        rows = self.build(10, "a")
        rows["U"] = [rows["U"][0]]
        with self.assertRaisesRegex(X.Refused, "U priced trades"):
            self.read(rows)

    def test_contrast_lost_attempts_are_not_decidable_not_a_pass(self):
        # QP probe CONTRAST-LOST: L takes the worst pool once a day; the full book is NOT_PASS
        def pick(d, i):
            return ("d",) if i < 3 else ("a",)

        full = {"L": arm_book("hv7.0t-L", 10, 4, lambda d, i, p: PNL[p], pick, stakes=(0.25,))}
        rows = contrast_rows(10, 4, "a", PNL)
        rows["L"] = full["L"]
        ems = {a: [r["stamp_ms"]["entry"] for r in rs if r["kind"] == "open"] for a, rs in rows.items()}
        res = self.read(rows)
        self.assertEqual(res["status"], "NOT_PASS")
        # the same book with the worst-pick positions (30 of L's 40 periods) missing from the ledger but present in the decision log
        short = dict(rows, L=[r for r in rows["L"] if not r.get("position_id", "").endswith("-d")])
        res = self.read(short, ems=ems)
        self.assertEqual(res["status"], "NOT_DECIDABLE")
        self.assertTrue(any("lost" in m for m in res["reasons"]))
        il = res["integrity"]["L"]
        self.assertEqual((il["decision_log_attempts"], il["ledger_attempts"], il["lost_attempts"]), (40, 10, 30))
        # a small loss (2 of 40 = 5.0% is not above the cap) still reads
        few = dict(rows, L=[r for r in rows["L"] if r.get("position_id") not in ("hv7.0t-L-0-0-d", "hv7.0t-L-1-0-d")])
        res = self.read(few, ems=ems)
        self.assertNotEqual(res["status"], "NOT_DECIDABLE")
        self.assertEqual(res["integrity"]["L"]["lost_attempts"], 2)

    def test_contrast_ledger_with_extra_attempts_is_final(self):
        rows = self.build(10, "a")
        ems = {a: [r["stamp_ms"]["entry"] for r in rs if r["kind"] == "open"] for a, rs in rows.items()}
        ems["S"] = ems["S"][:-3]
        res = self.read(rows, ems=ems)
        self.assertEqual(res["status"], "NOT_DECIDABLE")
        self.assertTrue(any("never ordered" in m for m in res["reasons"]))
        self.assertEqual(res["integrity"]["S"]["extra_ledger_attempts"], 3)

    def test_contrast_needs_decision_log_instants(self):
        with self.assertRaises(X.Refused):
            X.contrast_read(now_ms=HALT + X.EXIT_WINDOW_MS, regn=self.regn(), rows_by_arm=self.build(10, "a"), l_rank_rows=(), fallback=FB_OK, e1=E1_OK, n_books=3)

    def test_missing_strand_rule_is_report_only(self):
        rows = contrast_rows(10, 4, "a", PNL)
        rows = {a: [dict(r, legs={"total": r["legs"]["total"]}, calc={k: dict(v, by_rule={"total": v["by_rule"]["total"]}) for k, v in r["calc"].items()}) if r["kind"] == "trade" else r
                    for r in rs] for a, rs in rows.items()}
        res = self.read(rows)
        self.assertEqual(res["status"], "REPORT_ONLY")
        self.assertIn("not priced under both strand rules", res["reasons"][0])

    def test_bonding_row_is_report_only(self):
        rows = self.build(10, "a")
        rows["S"] = [dict(r, prog="pump_bonding") if r["kind"] == "trade" else r for r in rows["S"]]
        self.assertEqual(self.read(rows)["status"], "REPORT_ONLY")

    def test_stored_flat_leg_mismatch_refused(self):
        rows = self.build(10, "a")
        for r in rows["R"]:
            if r["kind"] == "trade":
                r["legs"]["sc"]["flat_1p9"]["net_lamports"] += 1.0
                break
        with self.assertRaisesRegex(X.Refused, "stored flat leg"):
            self.read(rows)

    def test_contrast_trade_rows_only_at_another_stake_count_as_lost(self):
        rows = self.build(10, "a")
        for r in rows["L"]:  # 3 of L's 40 positions: the deciding-stake trade row moved to stake 0.5
            if r["kind"] == "trade" and r["position_id"] in ("hv7.0t-L-0-0-a", "hv7.0t-L-1-0-a", "hv7.0t-L-2-0-a"):
                r["stake_sol"] = 0.5
        res = self.read(rows)
        il = res["integrity"]["L"]
        self.assertEqual((il["lost_attempts"], il["no_deciding_stake_row"], il["foreign_ledger_positions"]), (3, 3, 0))
        self.assertEqual(res["status"], "NOT_DECIDABLE")
        self.assertTrue(any("closing rows missing at the deciding stake" in m for m in res["reasons"]))

    def test_contrast_one_moved_row_in_any_arm_is_final(self):
        for arm in ("L", "R", "S", "U"):  # one position of 40 (2.5%, under the 5% cap) still fails the integrity check
            rows = self.build(10, "a")
            for r in rows[arm]:
                if r["kind"] == "trade":
                    r["stake_sol"] = 0.5
                    break
            res = self.read(rows)
            self.assertEqual(res["status"], "NOT_DECIDABLE", arm)
            self.assertTrue(any("closing rows missing at the deciding stake" in m for m in res["reasons"]), arm)

    def test_contrast_swapped_pools_same_count_is_final(self):
        # QP round 4 R4-5(a): L's decision log says pool d (the worst), the ledger holds pool a at the same instants
        rows = self.build(10, "a")
        ords = {a: [(r["stamp_ms"]["entry"], r["position_id"]) for r in rs if r["kind"] == "open"] for a, rs in rows.items()}
        ords["L"] = [(m, pid[:-1] + "d") for m, pid in ords["L"]]
        res = self.read(rows, entry_orders_by_arm=ords, ems={a: [m for m, _ in o] for a, o in ords.items()})
        self.assertEqual(res["status"], "NOT_DECIDABLE")
        self.assertTrue(any("never ordered" in m for m in res["reasons"]))
        il = res["integrity"]["L"]
        self.assertEqual((il["lost_attempts"], il["foreign_ledger_positions"], il["ledger_attempts"]), (40, 40, 40))

    def test_contrast_phantom_instants_same_count_is_final(self):
        # QP round 4 R4-5(b): 10 of L's ledger positions sit at instants the decision log never logged and replace 10 logged ones
        rows = self.build(10, "a")
        ords = {a: [(r["stamp_ms"]["entry"], r["position_id"]) for r in rs if r["kind"] == "open"] for a, rs in rows.items()}
        keep = [r for r in rows["L"] if not r.get("position_id", "").endswith("-3-a")]
        for d in range(10):
            ms = T0 + d * DAY + 4 * 180_000 + 90_000
            pid = f"hv7.0t-L-{d}-x-a"
            keep += [open_row("hv7.0t-L", pid, ms), trade_row("hv7.0t-L", pid, ms, "a", PNL["a"], 0.25)]
        res = self.read(dict(rows, L=keep), entry_orders_by_arm=ords, ems={a: [m for m, _ in o] for a, o in ords.items()})
        self.assertEqual(res["status"], "NOT_DECIDABLE")
        self.assertTrue(any("never ordered" in m for m in res["reasons"]))
        self.assertEqual(res["integrity"]["L"]["foreign_ledger_positions"], 10)
        self.assertEqual(res["integrity"]["L"]["lost_attempts"], 10)

    def test_contrast_requires_order_ids(self):
        rows = self.build(10, "a")
        ems = {a: [r["stamp_ms"]["entry"] for r in rs if r["kind"] == "open"] for a, rs in rows.items()}
        with self.assertRaises(X.Refused):
            X.contrast_read(now_ms=HALT + X.EXIT_WINDOW_MS, regn=self.regn(), rows_by_arm=rows, l_rank_rows=(), fallback=FB_OK, e1=E1_OK, n_books=30, entry_ms_by_arm=ems)

    def test_missing_stamp_refused(self):
        rows = self.build(10, "a")
        ems = {a: [r["stamp_ms"]["entry"] for r in rs if r["kind"] == "open"] for a, rs in rows.items()}
        ords = {a: ledger_orders(rows[a], ems[a], a) for a in rows}
        for r in rows["L"]:
            r.pop("stamp_ms", None)
        with self.assertRaises(X.Refused):
            self.read(rows, ems=ems, entry_orders_by_arm=ords)


class Files(unittest.TestCase):
    def test_fallback_check_entry_vs_rank_and_e1(self):
        with tempfile.TemporaryDirectory() as tmp:
            w = World(tmp)
            old = R.READ_ROOTS
            R.READ_ROOTS = tuple(old) + (str(w.dec),)
            self.addCleanup(lambda: setattr(R, "READ_ROOTS", old))
            ms = periods(1, 20)
            ranks = [{"kind": "rank", "ms": m - 45_000, "fallback": i < 3} for i, m in enumerate(ms)]  # 3/20 = 15%
            w.declog("L", ms, ranks)
            self.assertEqual(len(X.entry_ms(w.dec, "L")), 20)
            fb = X.fallback_check(w.dec, T0, T0 + DAY)
            self.assertTrue(fb["L_not_evaluable"])
            self.assertEqual(fb["reason"], "E_fallback")
            self.assertFalse(fb["entry_vs_rank_row_difference"])
            e1 = X.e1_check(w.dec, T0)
            self.assertFalse(e1["a_l_lt_20"])
            self.assertTrue(e1["fallback_gt_10pct"])
            self.assertEqual(len(e1["count_sha256"]), 64)
            w.declog("L", ms[:19], ranks[:19])
            for f in (w.dec / "decisions" / "L").glob("*.jsonl"):
                f.unlink()
            w.declog("L", ms[:19], ranks)  # 19 entries, 20 rank rows
            self.assertTrue(X.e1_check(w.dec, T0)["a_l_lt_20"])
            self.assertTrue(X.fallback_check(w.dec, T0, T0 + DAY)["entry_vs_rank_row_difference"])

    def test_record_once_is_per_bot_and_kind(self):
        with tempfile.TemporaryDirectory() as tmp:
            X.record_once(tmp, "hv7.0t-L_gate", {"a": 1})
            X.record_once(tmp, "hv7.0t-S_gate", {"a": 1})
            X.record_once(tmp, "hv7.0t-L_contrast", {"a": 1})
            with self.assertRaises(X.Refused):
                X.record_once(tmp, "hv7.0t-L_gate", {"a": 2})


def run_cli(w, kind, extra=(), wall=LATE, arm="L", now=None, record=None):
    out = io.StringIO()
    argv = [kind, "--root", str(w.dec), "--ledger-root", w.ledger, "--registry", w.reg, "--n-books", "30", "--arm", arm]
    if now is not None:
        argv += ["--now-ms", str(now)]
    if record is not None:
        argv += ["--record-dir", str(record)]
    rc = X.main(argv + list(extra), out=out, _wall_ms=wall)
    return rc, out.getvalue()


class Cli(unittest.TestCase):
    def full_world(self, w):
        for arm in ("L", "S"):
            rows = arm_book(R.bot_id(arm), 6, 20, win)
            w.add_rows(rows)
        ms = periods(6, 20)
        ranks = [{"kind": "rank", "ms": m - 45_000, "fallback": False, "order": ["a"]} for m in ms]
        w.declog("L", ms, ranks)
        w.declog("S", ms)

    def with_roots(self, w):
        old = R.READ_ROOTS
        R.READ_ROOTS = tuple(old) + (str(w.dec),)
        self.addCleanup(lambda: setattr(R, "READ_ROOTS", old))

    def test_gate_happy_path_end_to_end_and_once_per_bot(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch("tools.arena_paper.shutil.disk_usage", lambda p: collections.namedtuple("U", "total used free")(1000, 500, 500)):
            w = World(tmp)
            self.with_roots(w)
            self.full_world(w)
            rec = Path(tmp) / "reads"
            now = T0 + 7 * DAY
            rc, out = run_cli(w, "gate", now=now, record=rec)
            self.assertEqual(rc, 0, out)
            res = json.loads(out)
            self.assertEqual(res["status"], "PASS")
            self.assertEqual(res["cells"]["flat_1p9_sc"]["n"], 100)
            self.assertEqual(res["e1"]["a_l"], 40)  # E1 window = the first two days, 20 a day
            self.assertIn("holm_text", res["multiplicity"])
            self.assertTrue((rec / "hv7_read_hv7.0t-L_gate.json").exists())
            self.assertEqual(run_cli(w, "gate", now=now, record=rec)[0], 1)  # L again: refused
            rc, out = run_cli(w, "gate", arm="S", now=now, record=rec)  # S is its own bot: allowed
            self.assertEqual(rc, 0, out)
            self.assertTrue((rec / "hv7_read_hv7.0t-S_gate.json").exists())

    def test_contrast_happy_path_end_to_end(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch("tools.arena_paper.shutil.disk_usage", lambda p: collections.namedtuple("U", "total used free")(1000, 500, 500)):
            w = World(tmp)
            self.with_roots(w)
            for arm, rows in contrast_rows(10, 12, "a", PNL).items():
                w.add_rows(rows)
            ms4 = periods(10, 12)
            w.declog("L", ms4, [{"kind": "rank", "ms": m - 45_000, "fallback": False, "order": ["a", "b", "c", "d"]} for m in ms4])
            for arm, k, pools in (("R", 1, "c"), ("S", 1, "b"), ("U", 4, "abcd")):  # U enters every one of the four screened pools
                w.declog(arm, ms4 * k, pools=pools)
            rec = Path(tmp) / "reads"
            rc, out = run_cli(w, "contrast", now=HALT + X.EXIT_WINDOW_MS, record=rec)
            self.assertEqual(rc, 0, out)
            res = json.loads(out)
            self.assertEqual((res["status"], res["D"]), ("PASS", 10))
            self.assertTrue((rec / "hv7_read_hv7.0t-L_contrast.json").exists())
            self.assertIn("coverage", res)
            self.assertEqual(run_cli(w, "contrast", now=HALT + X.EXIT_WINDOW_MS, record=rec)[0], 1)

    def test_future_now_ms_refused_and_flags_are_test_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            w = World(tmp)
            self.with_roots(w)
            rec = Path(tmp) / "reads"
            rc, out = run_cli(w, "contrast", now=HALT + DAY, wall=HALT, record=rec)
            self.assertEqual(rc, 1)
            self.assertEqual(out, "")
            self.assertFalse(rec.exists())
            with mock.patch.dict(os.environ, {"MAL_ARENA_TEST": "0"}):
                self.assertEqual(run_cli(w, "gate", now=T0, wall=LATE)[0], 1)
                self.assertEqual(run_cli(w, "gate", record=rec, wall=LATE)[0], 1)

    def test_ledger_path_is_inside_the_hv7_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "hv7"
            root.mkdir()
            self.assertEqual(X.resolve_ledger_root(root, None), str(root.resolve() / "engine-ledger"))
            self.assertEqual(X.resolve_ledger_root(root, str(root / "engine-ledger")), str(root.resolve() / "engine-ledger"))
            for bad in (X.LEDGER_ROOT, X.LEDGER_ROOT + "/hv7.0t-L", str(Path(tmp) / "elsewhere"), str(root / ".." / "x")):
                with self.assertRaises(X.Refused):
                    X.resolve_ledger_root(root, bad)

    def test_cli_refuses_shared_ledger(self):
        with tempfile.TemporaryDirectory() as tmp:
            w = World(tmp)
            self.with_roots(w)
            out = io.StringIO()
            rc = X.main(["gate", "--root", str(w.dec), "--ledger-root", X.LEDGER_ROOT, "--registry", w.reg, "--n-books", "3"], out=out, _wall_ms=LATE)
            self.assertEqual((rc, out.getvalue()), (1, ""))

    def test_beside_never_reads_production_reads_in_test_mode(self):
        with mock.patch.object(X.AS, "load_recorded_read", side_effect=AssertionError("production reads dir read")):
            self.assertIsNone(X._beside("hv7.0t-L", True))

    def test_old_timing_flags_do_not_exist(self):
        with self.assertRaises(SystemExit):
            X.main(["gate", "--horizon-days", "7", "--n-books", "3"])
        with self.assertRaises(SystemExit):
            X.main(["gate", "--window-end-ms", "1", "--n-books", "3"])

    def test_contrast_before_window_complete_refused_with_wall_clock(self):
        with tempfile.TemporaryDirectory() as tmp:
            w = World(tmp)
            self.with_roots(w)
            # the real clock is day 8 of 10: days 9 and 10 do not exist, so the read is refused whatever data is on disk
            rc, _ = run_cli(w, "contrast", wall=T0 + 8 * DAY, record=Path(tmp) / "reads")
            self.assertEqual(rc, 1)

    def test_gate_refused_before_b_via_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            w = World(tmp)
            self.with_roots(w)
            w.declog("L", [T0 + 3_600_000])
            rc, out = run_cli(w, "gate", now=T0 + 9 * DAY, record=Path(tmp) / "reads")
            self.assertEqual((rc, out), (1, ""))


if __name__ == "__main__":
    unittest.main()
