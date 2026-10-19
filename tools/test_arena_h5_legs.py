"""Tests for tools/arena_h5_legs.py. SYNTHETIC fixtures only, shaped by the code that writes H5 rows (arena_h5_score.price_trade / ledger_rows / write_ledger) and the shadow's strip
record (h5_shadow, as arena_h5_score.Strip reads it). No real H5 path is opened. Needs tools/arena_report_legs.py (PR #747) for slot_states, worst_index and end_minus_worst.

Hand-picked states: the fixture pools PA and PB below list their prints so the expected worst-in-slot picks can be read off the table in each docstring. The expected P&L of a hand-picked
state comes from `ref_trip`, a plain restatement of the trip (own buy re-injected into the exit state, haircut, E1 floor) that uses only cost.fee_ppm for the fee tier.
"""

from __future__ import annotations

import collections
import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools import arena_h5_cost as cost
from tools import arena_h5_legs as L
from tools import arena_h5_score as h5s
from tools import arena_report_legs as RL

SHA = "a" * 40
BT0 = 1_800_000_000  # 2027-01-15T08:00:00Z, the block time of the first synthetic pool
START_MS = BT0 * 1000 - 5_000
V1 = "h5v1-boostfloor-a1"
START = {"type": "start", "t_ms": START_MS, "start_ms": START_MS, "code_sha": SHA, "fresh_forward": "ARENA", "dirty": False}
SEND, RENT = 505_000, 2_039_280
TSLOT, SPS, XTRIG = 1200, 0.4, 1800  # land 1p3 = 1200 + ceil(1.3/0.4) = 1204, 1p9 = 1205; exit landing = 1800 + max(2, ceil(0.55/0.4)) = 1802
BASE_PRE, SELL_RAW = 1.5e14, 2.5e13  # spot = 35e9 / 1.75e14 = 2.0e-4 lamports per raw token


def pool_records(pool: str, rows: list, post_last: list, *, bt: int, buys: list | None = None) -> tuple[dict, dict, dict]:
    """(trigger, outcome, strip) records of one pool with the fields price_trade reads. rows = [slot, q_pv_pre, q_fv_pre, base_pre] per print, in tape order."""
    trig = {"type": "trigger", "variant": "pv", "pool": pool, "mint": "Mint" + pool, "slot": TSLOT, "block_time": bt, "t_detect_ms": bt * 1000, "q_trigger_sol": 35.0,
            "base_pre": BASE_PRE, "sell_token_raw": SELL_RAW, "gap": False, "gaps": []}
    out = {"type": "outcome", "variant": "pv", "pool": pool, "mint": "Mint" + pool, "s0": 1000, "trigger_slot": TSLOT, "sps": SPS, "complete": True, "legs": {"primary": {"net": None}},
           "exit": {"trigger_slot": XTRIG, "q_sol": 44.5, "base": 1.7e14}, "chain_breaks": 0, "base_breaks": 0, "gap": False, "gaps": []}
    strip = {"type": "strip", "pool": pool, "mint": "Mint" + pool, "s0": 1000, "from_slot": TSLOT, "cols": ["slot", "q_pv_pre", "q_fv_pre", "base_pre"], "rows": rows, "truncated": False,
             "post_last": post_last, "buys_cols": ["slot", "sol_lamports"], "buys": buys or [], "buys_truncated": False}
    return trig, out, strip


def trade_rows(trig: dict, out: dict, strip: dict) -> list[dict]:
    priced = h5s.price_trade(V1, out, trig, strip, None, None, START)
    return h5s.ledger_rows(V1, out, trig, priced, START, None, None)


def r(slot: int, q: float, b: float) -> list:
    return [slot, q, q, b]


# PA: severals prints in the landing slots. Prices q / b (x 1e-4):
#   slot 1204 (the 1p3 landing slot): r1 pre 2.0000, r2 pre 41e9/1.95e14 = 2.1026, END = r3 pre (first print later than 1204) 40.5e9/1.98e14 = 2.0455  -> worst buy = r2 (END is not the worst)
#   slot 1205 (the 1p9 landing slot): r3 pre 2.0455, r4 pre 40.2e9/2.0e14 = 2.0100, END = r5 pre (first print in 1207) 41.6e9/1.99e14 = 2.0905      -> worst buy = END r5 (END is included)
#   slot 1802 (the exit landing slot): r6 pre 46e9/2.0e14 = 2.3000, r7 pre 45e9/2.05e14 = 2.1951, END = r8 pre (slot 1803) 46.5e9/2.02e14 = 2.3020  -> worst sell = r7
PA_ROWS = [r(1200, 40e9, 2.00e14), r(1204, 40e9, 2.00e14), r(1204, 41e9, 1.95e14), r(1205, 40.5e9, 1.98e14), r(1205, 40.2e9, 2.00e14), r(1207, 41.6e9, 1.99e14),
           r(1802, 46e9, 2.00e14), r(1802, 45e9, 2.05e14), r(1803, 46.5e9, 2.02e14)]
PA_POST = [1900, 46.5e9, 46.5e9, 2.02e14]
PA_STATES = {"1p3": {"end_entry": (40.5e9, 1.98e14), "worst_entry": (41e9, 1.95e14)}, "1p9": {"end_entry": (41.6e9, 1.99e14), "worst_entry": (41.6e9, 1.99e14)},
             "end_exit": (46.5e9, 2.02e14), "worst_exit": (45e9, 2.05e14)}

# PB: the guard-rescue pool. slot 1205 has a spike print r2 (50e9/2.0e14 = 2.5e-4 = 1.25 x spot: the 1.15 guard reverts it); END = r3 pre 2.0e-4. The exit price 1.8e-4 has a lower k than
# the entry: under "total" the END trip is exit_invalid (C8, k-fall), under "sc" it is filled at a loss. Slot 1204 holds no print: its only state is END (r1 pre).
PB_ROWS = [r(1200, 40e9, 2.0e14), r(1205, 40e9, 2.0e14), r(1205, 50e9, 2.0e14), r(1207, 40e9, 2.0e14), r(1802, 36e9, 2.0e14), r(1803, 36e9, 2.0e14)]
PB_POST = [1900, 36e9, 36e9, 2.0e14]

# Variety pools, one trip each: (QE, BE, QX, BX), the state the END-bound trip has under (total, sc).
VARIETY = {
    "win": ((40e9, 2.0e14, 46e9, 2.0e14), ("filled", "filled")),
    "win2": ((41e9, 2.0e14, 47e9, 2.0e14), ("filled", "filled")),
    "loss": ((40e9, 2.0e14, 41e9, 2.0e14), ("filled", "filled")),
    "kfall": ((40e9, 2.0e14, 38e9, 2.0e14), ("exit_invalid", "filled")),  # C8 under "total" only
    "guard": ((50e9, 2.0e14, 46e9, 2.0e14), ("guarded", "guarded")),
    "entry_invalid": ((17.0e9, 2.0e14, 46e9, 2.0e14), ("entry_invalid", "entry_invalid")),
    "exit_c2": ((40e9, 2.0e14, 46e9, 1.0e9), ("exit_invalid", "exit_invalid")),
}
BUYS = [[[1205, 10**9], [1205, 5 * 10**8], [1203, 2 * 10**9]], [[1205, 10**9]], [[1204, 3 * 10**9], [1201, 10**9]], [], [[1205, 10**7], [1206, 10**9], [1205, 10**9], [1204, 10**9]]]


def variety_pool(name: str, idx: int, day: int) -> tuple[dict, dict, dict]:
    (qe, be, qx, bx), _ = VARIETY[name]
    rows = [r(1200, 40e9, 2.0e14), r(1206, qe, be), r(1803, qx, bx)]
    return pool_records(f"V{idx:02d}{name}", rows, [1900, qx, qx, bx], bt=BT0 + day * 86400 + idx * 60, buys=BUYS[idx % len(BUYS)])


def ref_trip(stake: float, qe: float, be: float, qx: float, bx: float, rent: float = RENT) -> float:
    """Plain restatement of one filled, unguarded, no-C8 trip: fee tier, own buy re-injected into the exit state (qa = qx + net, ba = bx - tokens), +26.08/-16 bps haircut, two sends, rent,
    and the E1 floor (the larger of the haircut and 384,022 per 0.05 SOL of stake)."""
    def pnl(hin: float, hout: float) -> float:
        net = stake * (1 - cost.fee_ppm(qe, be) / 1e6)
        tok = be * net / (qe + net)
        tokh = tok / (1 + hin)
        qa, ba = qx + net, bx - tok
        gross = tokh * qa / (ba + tokh)
        return gross * (1 - cost.fee_ppm(qa, ba) / 1e6) * (1 - hout) - stake - 2 * SEND - rent

    t, t0 = pnl(0.002608, 0.0016), pnl(0.0, 0.0)
    return t - max(0.0, 384_022 * stake / 50_000_000 - (t0 - t))


class Base(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="arena-h5-legs-")).resolve()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.ledger = self.root / "arena" / "ledger"
        self.shadow = self.root / "arena" / "h5-fixture"

    def book(self, pools: list[tuple[dict, dict, dict]], *, write_strips: bool = True, open_before: int = 0) -> dict:
        """Write a fixture ledger, its read-instant record (counts only, as record_read_instant writes it) and the shadow dir with the strips. Returns the instants."""
        rows = [x for t, o, s in pools for x in trade_rows(t, o, s)]
        h5s.write_ledger(V1, rows, [], self.ledger, collections.Counter())
        last = max(x["decision_ms"] for x in rows)
        ri = (last // h5s.DAY_MS + 1) * h5s.DAY_MS
        doc = {"bot_id": V1, "read_instant_ms": ri, "attempts_before": len(pools), "dates": 5, "open_before_read": open_before, "refused_before": 0, "refused_by_reason_before": {},
               "triggers_before": len(pools), "attempt_definition": "fixture"}
        h5s._atomic_write_json(self.ledger / V1 / h5s.READ_INSTANT_FILE, doc)
        if write_strips:
            self.shadow.mkdir(parents=True, exist_ok=True)
            with open(self.shadow / "h5-shadow-2027-01-15T08.jsonl", "w") as fh:
                fh.write(json.dumps(START) + "\n")
                for _, _, s in pools:
                    fh.write(json.dumps(s) + "\n")
        return {"ri": ri, "rows": rows}

    def run_cli(self, *argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = L.main(list(argv))
        return rc, out.getvalue(), err.getvalue()

    def read_args(self, ri: int, **kw) -> list[str]:
        a = ["--bot", V1, "--ledger-root", str(self.ledger), "--read-instant", str(ri), "--i-am-the-read", "--now-ms", str(ri + 1000)]
        return a + ["--shadow-dir", str(self.shadow)] if kw.get("shadow", True) else a


def full_fixture_pools() -> list:
    pools = [pool_records("PA", PA_ROWS, PA_POST, bt=BT0, buys=[[1205, 10**9], [1204, 10**9]]), pool_records("PB", PB_ROWS, PB_POST, bt=BT0 + 3600, buys=[[1205, 2 * 10**9]])]
    for i, name in enumerate(["win", "win2", "loss", "kfall", "guard", "entry_invalid", "exit_c2", "win", "loss", "win2", "kfall", "win"]):
        pools.append(variety_pool(name, i, day=i % 3))
    return pools


class Rent0LegTests(Base):
    def test_the_fixture_has_every_state_and_the_rule_split_the_tests_rely_on(self):
        for name, (_, want) in VARIETY.items():
            row = next(x for x in trade_rows(*variety_pool(name, 0, 0)) if x["stake_sol"] == 0.3)
            self.assertEqual((L.leg_state(row, "1p9", "total")[1], L.leg_state(row, "1p9", "sc")[1]), want, name)

    def test_rent_0_leg_equals_a_full_reprice_at_rent_0_with_heterogeneous_p_i(self):
        pools = full_fixture_pools()
        rows = [x for t, o, s in pools for x in trade_rows(t, o, s)]
        strips = {s["pool"]: s for _, _, s in pools}
        refit = h5s.refit_cells(rows)
        for stake in ("0.10", "0.25", "0.30"):
            rs = [x for x in rows if f"{x['stake_sol']:.2f}" == stake]
            for sfx in L.SFXS:
                items = L.items_from_rows(rs, sfx)
                c = refit[f"{stake}|{sfx}"]["intercept"]
                ps = {round(cost.press_p(c, it["ssb"], it["nb"]), 6) for it in items}
                self.assertGreater(len(ps), 3, "p_i must be heterogeneous or this test cannot catch a mean-p bug")
                # full re-price of every trip at rent 0, from the strip, then the same fail legs; the intercept refit on the unchanged filled set
                rep = []
                for x in rs:
                    st = h5s.Strip(strips[x["pool"]])
                    m = x["meta"]
                    qe, be, _ = st.state_at(m["landing_slots"][sfx])
                    qx, bx, _ = st.state_at(m["exit_trigger_slots"][sfx] + m["exit_lag_slots"])
                    lam = int(round(x["stake_sol"] * 1e9))
                    rep.append({ru: cost.deciding_round_trip(lam, qe, be, m["spot"], qx, bx, rent=0, strand="dec031" if ru == "total" else "sc") for ru in L.RULES})
                c0 = cost.press_intercept([it["ssb"] for it in items], [it["nb"] for it in items], [cost.is_filled(t["total"]["state"]) for t in rep])
                self.assertAlmostEqual(c0, c, places=12)
                for ru in L.RULES:
                    v = L.cell_values(items, ru, c, rent0=True)
                    for i, it in enumerate(items):
                        t = rep[i][ru]
                        p = cost.press_p(c0, it["ssb"], it["nb"])
                        self.assertAlmostEqual(v["flat"][i], cost.flat_leg(t["pnl"], t["state"]), places=3, msg=f"{stake} {sfx} {ru} {it['trade_id']}")
                        self.assertAlmostEqual(v["press"][i], cost.press_leg(t["pnl"], t["state"], p), places=3, msg=f"{stake} {sfx} {ru} {it['trade_id']}")
                    self.assertEqual(int(v["credited"].sum()), sum(1 for t in rep if t[ru]["state"] == "filled"))

    def test_the_selector_is_the_leg_state_not_the_flags_so_sc_invalid_rows_are_never_credited(self):
        c = -1.6
        mk = lambda st, nf=-1e8: {"trade_id": st, "pnl": {"total": (nf, st), "sc": (nf, st)}, "ssb": 2, "nb": 10**9, "day": "2027-01-15", "derived_last": False}  # noqa: E731
        items = [mk(s) for s in ("filled", "guarded", "stranded", "entry_invalid", "exit_invalid")]
        for ru in L.RULES:
            base, r0 = L.cell_values(items, ru, c), L.cell_values(items, ru, c, rent0=True)
            self.assertEqual(r0["credited"].tolist(), [True, False, False, False, False])
            p = cost.press_p(c, 2, 10**9)
            self.assertAlmostEqual(r0["flat"][0] - base["flat"][0], 0.85 * RENT, places=6)  # 1,733,388
            self.assertAlmostEqual(r0["press"][0] - base["press"][0], (1 - p) * RENT, places=6)
            for i in range(1, 5):
                self.assertEqual(r0["flat"][i], base["flat"][i])
                self.assertEqual(r0["press"][i], base["press"][i])

    def test_a_stranded_total_row_is_blended_like_h5_prices_it_and_a_k_fall_differs_between_the_rules(self):
        row = next(x for x in trade_rows(*variety_pool("kfall", 0, 0)) if x["stake_sol"] == 0.3)
        nf_t, st_t = L.leg_state(row, "1p9", "total")
        self.assertEqual(st_t, "exit_invalid")
        self.assertAlmostEqual(nf_t, -300_000_000 - SEND - RENT, places=3)
        nf_s, st_s = L.leg_state(row, "1p9", "sc")
        self.assertEqual(st_s, "filled")
        v = L.cell_values(L.items_from_rows([row], "1p9"), "sc", -1.6, rent0=True)
        self.assertEqual(v["credited"].tolist(), [True])
        self.assertEqual(L.cell_values(L.items_from_rows([row], "1p9"), "total", -1.6, rent0=True)["credited"].tolist(), [False])

    def test_the_cli_rent_0_cells_differ_from_the_rent_charged_ones_by_exactly_the_credit(self):
        b = self.book(full_fixture_pools())
        rc, out, err = self.run_cli(*self.read_args(b["ri"]), "--json", str(self.root / "legs.json"))
        self.assertEqual(rc, 0, err)
        self.assertIn("report-only, added after the clock, never deciding", out)
        rep = json.loads((self.root / "legs.json").read_text())
        for stake, bk in rep["books"].items():
            for k, c0 in bk["rent_0"].items():
                c1 = bk["as_scored_rent_charged"][k]
                self.assertEqual(c0["n"], c1["n"])
                if k.startswith("flat_"):
                    self.assertAlmostEqual(c0["total_sol"] - c1["total_sol"], 0.85 * RENT * c0["n_credited"] / 1e9, places=9, msg=f"{stake} {k}")
                else:
                    self.assertGreater(c0["total_sol"] - c1["total_sol"], 0.0 if c0["n_credited"] else -1e-12)
                    self.assertLessEqual(c0["total_sol"] - c1["total_sol"], RENT * c0["n_credited"] / 1e9 + 1e-12)
        self.assertEqual(rep["deciding_rent_lamports"], RENT)


class WorstInSlotTests(Base):
    def row_of(self, pools, name, stake=0.3):
        t, o, s = next(p for p in pools if p[0]["pool"] == name)
        return next(x for x in trade_rows(t, o, s) if x["stake_sol"] == stake), s

    def test_pa_entry_at_the_highest_price_state_exit_at_the_lowest_end_included_own_buy_reinjected(self):
        pools = full_fixture_pools()
        row, strip = self.row_of(pools, "PA")
        for sfx in L.SFXS:
            res, d = L.worst_trip(row, sfx, strip)
            self.assertEqual((d["state_mismatch"], d["pnl_mismatch"]), ([], []))
            for ru in L.RULES:
                st = PA_STATES[sfx]
                end_pnl, end_state = res[ru]["end"]
                self.assertEqual(end_state, "filled")
                self.assertAlmostEqual(end_pnl, ref_trip(3e8, *st["end_entry"], *PA_STATES["end_exit"]), places=3)
                w_pnl, w_state = res[ru]["worst"]
                self.assertEqual(w_state, "filled")
                self.assertAlmostEqual(w_pnl, ref_trip(3e8, *st["worst_entry"], *PA_STATES["worst_exit"]), places=3)
                self.assertLess(w_pnl, end_pnl)
            self.assertEqual(d["entry_moved"], 1 if sfx == "1p3" else 0)  # 1p9: the END state IS the highest-price state of its slot
            self.assertEqual((d["exit_moved"], d["entry_multi_print"], d["exit_multi_print"]), (1, 1, 1))
        # the own buy is re-injected: pricing the same exit state WITHOUT the buy's reserves gives a different, higher number
        qx, bx = PA_STATES["worst_exit"]
        qe, be = PA_STATES["1p3"]["worst_entry"]
        net = 3e8 * (1 - cost.fee_ppm(qe, be) / 1e6)
        tok = be * net / (qe + net)
        no_reinject = tok / 1.002608 * qx / (bx + tok / 1.002608)
        with_reinject = tok / 1.002608 * (qx + net) / ((bx - tok) + tok / 1.002608)
        self.assertNotAlmostEqual(no_reinject, with_reinject, delta=1e6)

    def test_pb_the_guard_rescue_flips_the_gap_and_the_companion_keeps_the_end_row(self):
        pools = full_fixture_pools()
        row, strip = self.row_of(pools, "PB")
        res, _ = L.worst_trip(row, "1p9", strip)
        self.assertEqual(res["total"]["end"][1], "exit_invalid")  # C8 under total
        self.assertEqual(res["sc"]["end"][1], "filled")
        for ru in L.RULES:
            self.assertEqual(res[ru]["worst"], (-float(SEND), "guarded"))
            self.assertEqual(res[ru]["companion"], res[ru]["end"])  # filled-at-END -> guarded-at-worst keeps its END row
        res13, _ = L.worst_trip(row, "1p3", strip)  # slot 1204 holds no print: its one state is END
        self.assertEqual(res13["total"]["worst"], res13["total"]["end"])
        # whole-book cells for PB alone, by hand: flat total at END = 0.85 x (-stake - send - rent) - 0.15 x send; at worst the guarded trip costs one send
        w = L.worst_in_slot([x for x in trade_rows(*next(p for p in pools if p[0]["pool"] == "PB")) if x["stake_sol"] == 0.3], {"PB": strip})
        self.assertTrue(w["measured"])
        s = w["stakes"]["0.30"]
        end_flat = 0.85 * (-300_000_000 - SEND - RENT) - 0.15 * SEND
        self.assertAlmostEqual(s["cells"]["flat_1p9_total"]["mean_sol"], -SEND / 1e9, places=12)
        self.assertAlmostEqual(s["end_minus_worst"]["flat_1p9_total"]["mean_sol"], end_flat / 1e9 + SEND / 1e9, places=9)
        self.assertLess(s["end_minus_worst"]["flat_1p9_total"]["mean_sol"], 0)  # the plain leg is BETTER than END: not a bound
        self.assertAlmostEqual(s["end_minus_companion"]["flat_1p9_total"]["mean_sol"], 0.0, places=12)
        self.assertEqual(w["diag"]["transitions"]["0.30|1p9|total"], {"exit_invalid->guarded": 1})
        self.assertEqual(w["diag"]["transitions"]["0.30|1p9|sc"], {"filled->guarded": 1})
        low = s["lower_of_two"]["flat_1p9_total"]["mean_sol"]
        self.assertAlmostEqual(low, end_flat / 1e9, places=9)  # the lower of the two legs is the companion (= END)

    def test_a_tie_takes_the_first_state_in_strip_order(self):
        sa = {"sl": __import__("numpy").array([5, 5, 6]), "QX": __import__("numpy").array([40e9, 40e9, 40e9, 40e9]), "BX": __import__("numpy").array([2e14, 2e14, 2e14, 2e14])}
        idx, je = RL.slot_states(sa, 5)
        self.assertEqual((idx.tolist(), je), ([0, 1, 2], 2))
        self.assertEqual(RL.worst_index(sa, idx, "buy"), 0)
        self.assertEqual(RL.worst_index(sa, idx, "sell"), 0)

    def test_an_end_mismatch_replaces_every_worst_in_slot_figure_with_the_message_and_exits_4(self):
        b = self.book(full_fixture_pools())
        f = self.shadow / "h5-shadow-2027-01-15T08.jsonl"
        recs = [json.loads(x) for x in f.read_text().splitlines()]
        next(x for x in recs if x.get("pool") == "PA")["rows"][5][1] += 5e9  # the strip no longer is the one the scorer priced
        f.write_text("".join(json.dumps(x) + "\n" for x in recs))
        rc, out, err = self.run_cli(*self.read_args(b["ri"]), "--json", str(self.root / "legs.json"))
        self.assertEqual(rc, 4)
        self.assertIn("worst-in-slot not measured: END consistency failed", out)
        self.assertNotIn("WORST-IN-SLOT leg", out)
        rep = json.loads((self.root / "legs.json").read_text())
        self.assertFalse(rep["worst_in_slot_measured"])
        for bk in rep["books"].values():
            self.assertTrue(bk["worst_in_slot"].startswith("worst-in-slot not measured: END consistency failed"))
            self.assertTrue(bk["rent_0"])  # the rent-0 leg still prints

    def test_no_shadow_dir_says_not_measured_and_exits_0(self):
        b = self.book(full_fixture_pools(), write_strips=False)
        rc, out, _ = self.run_cli(*self.read_args(b["ri"], shadow=False))
        self.assertEqual(rc, 0)
        self.assertIn("worst-in-slot not measured (no --shadow-dir)", out)

    def test_the_cli_prints_every_worst_in_slot_item_the_qp_asked_for(self):
        b = self.book(full_fixture_pools())
        rc, out, err = self.run_cli(*self.read_args(b["ri"]), "--json", str(self.root / "legs.json"))
        self.assertEqual(rc, 0, err)
        rep = json.loads((self.root / "legs.json").read_text())
        w = rep["books"]["0.30"]["worst_in_slot"]
        for k in ("cells", "companion_no_guard_rescue", "lower_of_two", "end_minus_worst", "end_minus_companion", "end_minus_worst_ex_derived_last", "rent_0_stacked", "intercepts"):
            self.assertIn(k, w)
        self.assertTrue(rep["worst_in_slot_diag"]["parity_ok"])
        self.assertIn("transitions", rep["worst_in_slot_diag"])
        self.assertIn("derived_last", rep["worst_in_slot_diag"])
        for token in ("END minus worst-in-slot", "LOWER OF THE TWO", "pressure intercepts", "no guard rescue"):
            self.assertIn(token, out)
        self.assertEqual(rep["books"]["0.30"]["as_scored_rent_charged"]["flat_1p9_total"]["n"], rep["books"]["0.30"]["worst_in_slot"]["cells"]["flat_1p9_total"]["n"])


class SealTests(Base):
    def test_refused_without_the_read_flag_or_the_read_instant_and_before_any_row_is_opened(self):
        b = self.book(full_fixture_pools())
        with mock.patch.object(h5s, "ledger_attempts_before", side_effect=AssertionError("a ledger row was opened")):
            base = ["--bot", V1, "--ledger-root", str(self.ledger), "--now-ms", str(b["ri"] + 1000)]
            rc, _, err = self.run_cli(*base, "--read-instant", str(b["ri"]))
            self.assertEqual(rc, 3)
            self.assertIn("--i-am-the-read is required", err)
            rc, _, err = self.run_cli(*base, "--i-am-the-read")
            self.assertEqual(rc, 3)
            self.assertIn("--read-instant is required", err)

    def test_refused_when_read_instant_or_now_is_before_the_recorded_one_and_allowed_at_or_after(self):
        b = self.book(full_fixture_pools())
        ri = b["ri"]
        with mock.patch.object(h5s, "ledger_attempts_before", side_effect=AssertionError("a ledger row was opened")):
            rc, _, err = self.run_cli("--bot", V1, "--ledger-root", str(self.ledger), "--read-instant", str(ri - 1), "--i-am-the-read", "--now-ms", str(ri + 10**6))
            self.assertEqual(rc, 3)
            self.assertIn("before the book's recorded read instant", err)
            rc, _, err = self.run_cli("--bot", V1, "--ledger-root", str(self.ledger), "--read-instant", str(ri), "--i-am-the-read", "--now-ms", str(ri - 1))
            self.assertEqual(rc, 3)
            self.assertIn("now ", err)
        for at in (ri, ri + 86_400_000):  # equal, and after
            rc, out, err = self.run_cli("--bot", V1, "--ledger-root", str(self.ledger), "--read-instant", str(at), "--i-am-the-read", "--now-ms", str(at + 1))
            self.assertEqual(rc, 0, err)
            self.assertIn("ARENA H5 REPORT LEGS", out)

    def test_refused_when_no_read_instant_is_recorded_or_attempts_are_still_open(self):
        b = self.book(full_fixture_pools(), open_before=2)
        rc, _, err = self.run_cli(*self.read_args(b["ri"], shadow=False))
        self.assertEqual(rc, 3)
        self.assertIn("still open", err)
        (self.ledger / V1 / h5s.READ_INSTANT_FILE).unlink()
        rc, _, err = self.run_cli(*self.read_args(b["ri"], shadow=False))
        self.assertEqual(rc, 3)
        self.assertIn("no recorded read instant", err)

    def test_a_ledger_that_is_not_the_scorers_is_refused(self):
        b = self.book(full_fixture_pools())
        f = next((self.ledger / V1).glob("trades-*.jsonl"))
        lines = [json.loads(x) for x in f.read_text().splitlines()]
        lines[0]["legs"]["flat_1p9"]["net_lamports"] += 1000.0
        f.write_text("".join(json.dumps(x) + "\n" for x in lines))
        rc, _, err = self.run_cli(*self.read_args(b["ri"], shadow=False))
        self.assertEqual(rc, 3)
        self.assertIn("not the scorer's rows", err)

    def test_forbidden_paths_and_ids_are_refused_before_the_filesystem_is_touched(self):
        ri = 1_800_000_000_000
        for bad in ("probe", "canary", "_shakedown"):
            rc, _, err = self.run_cli("--bot", V1, "--ledger-root", f"/nonexistent/{bad}-x/ledger", "--read-instant", str(ri), "--i-am-the-read", "--now-ms", str(ri))
            self.assertEqual((rc, bad in err), (3, True), (bad, err))
            rc, _, err = self.run_cli("--bot", f"h5v1-{bad}-a1", "--ledger-root", "/nonexistent/ledger", "--read-instant", str(ri), "--i-am-the-read")
            self.assertEqual((rc, bad in err), (3, True), (bad, err))
            rc, _, err = self.run_cli("--bot", V1, "--ledger-root", "/nonexistent/ledger", "--read-instant", str(ri), "--i-am-the-read", "--json", f"/tmp/{bad}/out.json")
            self.assertEqual((rc, bad in err), (3, True), (bad, err))
        rc, _, err = self.run_cli("--bot", V1, "--ledger-root", "/nonexistent/ledger", "--read-instant", str(ri), "--i-am-the-read", "--shadow-dir", "/home/x/data/h5-shadow")
        self.assertEqual(rc, 3)  # the ledger is absent here, so this one is refused by the missing record; the live-shadow name itself is checked next
        with self.assertRaises(L.Refused):
            L.check_path("/home/x/data/h5-shadow", "shadow dir")
        with self.assertRaises(L.Refused):
            L.check_path("/srv/mal-h5-shadow/h5-shadow-2026-10-18T05.jsonl", "shadow dir")

    def test_a_shadow_dir_or_a_symlink_into_a_forbidden_dir_is_refused_once_the_gate_passes(self):
        b = self.book(full_fixture_pools())
        for name in ("_shakedown-a", "canary-b", "probe-c"):
            rc, _, err = self.run_cli(*self.read_args(b["ri"], shadow=False), "--shadow-dir", str(self.root / name))
            self.assertEqual(rc, 3, err)
        hidden = self.root / "probe-real"
        hidden.mkdir()
        link = self.root / "innocent"
        os.symlink(hidden, link)
        rc, _, err = self.run_cli(*self.read_args(b["ri"], shadow=False), "--shadow-dir", str(link))
        self.assertEqual(rc, 3)
        self.assertIn("resolved", err)

    def test_the_tool_depends_on_pr_747_by_these_names(self):
        for n in ("slot_states", "worst_index", "end_minus_worst"):
            self.assertTrue(callable(getattr(RL, n)), n)

    def test_no_pinned_arena_file_is_imported_for_writing_and_the_tool_writes_nothing_but_json(self):
        b = self.book(full_fixture_pools())
        before = sorted(str(p) for p in self.root.rglob("*") if p.is_file())
        rc, _, err = self.run_cli(*self.read_args(b["ri"]))
        self.assertEqual(rc, 0, err)
        self.assertEqual(before, sorted(str(p) for p in self.root.rglob("*") if p.is_file()))


if __name__ == "__main__":
    unittest.main()
