"""Offline tests for tools/exp016_rug.py (EXP-016 plan section 11 P1-P4). Fixtures only; no /data/mal."""

from __future__ import annotations

import random
import unittest

import tools.exp016_rug as rg
import tools.latency_curve as lc
from tools import pumpswap_virtual_adapter as ad
from tools.paper_curve_math import venue_fee_ppm
from tools.paper_price_path import pumpswap_post_trade_reserves, print_from_trade_row

POOL = "POOLM"
V = 17_580_000_000
Q0 = 67_410_000_000
B0 = 206_900_000_000_000


class Chain:
    """A consistent PumpSwap row chain: each row carries the vault BEFORE the trade, updated with the same helper."""

    def __init__(self, pool=POOL, q=Q0, b=B0, v=V, mint="M"):
        self.pool, self.q, self.b, self.v, self.mint, self.n, self.rows = pool, q, b, v, mint, 0, []

    def trade(self, slot, side="buy", sol=100_000_000, tok=10**11, trader="W", pool=None, tx=0):
        self.n += 1
        row = {"venue": "pumpswap", "mint": self.mint, "trader": trader, "side": side, "sol_lamports": sol, "token_raw": tok,
               "quote_reserve": self.q, "base_reserve": self.b, "pool": pool or self.pool, "slot": slot, "tx_index": tx,
               "event_index": self.n, "t_recv_ms": 1000 + self.n, "quote_is_wsol": True, "signature": f"sig{self.n}"}
        if (pool or self.pool) == self.pool:
            mcap = (self.q + max(self.v, 0)) / (self.b * 1000) * 1e9
            post = pumpswap_post_trade_reserves(side=side, quote_reserve=self.q, base_reserve=self.b, sol_lamports=sol, token_raw=tok,
                                                fee_ppm=venue_fee_ppm("pumpswap", mcap))
            if post:
                self.q, self.b = post
        self.rows.append(row)
        return rg.row_key(row)

    def gap(self, frac):
        """An untaped drain: the vault falls by `frac` with no print."""
        self.q = int(self.q * (1 - frac))


VMAP = {POOL: V}


def label(ch, entry, exit_, **kw):
    kw.setdefault("vmap", VMAP)
    return rg.label_trade(ch.rows, migration_pool=POOL, entry_key=entry, exit_key=exit_, **kw)


class LabelTests(unittest.TestCase):
    def test_three_slot_dump_fires_A(self):
        ch = Chain()
        e = ch.trade(9)
        for s in (10, 11, 12):
            x = ch.trade(s, "sell", sol=13_000_000_000, tok=4 * 10**13)
        res = label(ch, e, x)
        self.assertTrue(res.rug)
        self.assertEqual(res.event, "A")
        self.assertGreaterEqual(res.drop, 0.3675)
        self.assertEqual(res.v_source, "map")

    def test_slow_decline_does_not_fire(self):
        ch = Chain()
        e = ch.trade(9)
        for s in (10, 20, 30, 40):
            x = ch.trade(s, "sell", sol=13_000_000_000, tok=4 * 10**13)
        res = label(ch, e, x)
        self.assertFalse(res.rug)
        self.assertIsNone(res.event)
        self.assertGreater(res.worst_ratio, rg.RUG_RATIO)

    def test_untaped_drain_fires_B_only(self):
        ch = Chain()
        e = ch.trade(9)
        ch.trade(10)
        ch.gap(0.6)
        x = ch.trade(40, "sell", sol=10**8)
        res = label(ch, e, x)
        self.assertTrue(res.rug)
        self.assertEqual(res.event, "B")

    def test_untaped_drain_with_no_later_print_is_invisible(self):
        ch = Chain()
        e = ch.trade(9)
        x = ch.trade(10)
        ch.gap(0.6)
        self.assertFalse(label(ch, e, x).rug)

    def test_dump_step_by_last_print_in_window_fires_A(self):
        ch = Chain()
        e = ch.trade(9)
        ch.trade(10)
        x = ch.trade(30, "sell", sol=33_000_000_000, tok=1_300_000_000_000_00)  # one taped step, last print
        res = label(ch, e, x)
        self.assertTrue(res.rug)
        self.assertEqual(res.event, "A")

    def test_dump_after_exit_does_not_fire(self):
        ch = Chain()
        e = ch.trade(9)
        x = ch.trade(10, "buy")
        ch.trade(11, "sell", sol=40_000_000_000, tok=10**14)
        self.assertFalse(label(ch, e, x).rug)

    def test_censored_and_miss_have_no_label(self):
        ch = Chain()
        e = ch.trade(9)
        x = ch.trade(10)
        c = label(ch, e, x, deadline_ms=2_000_000, tape_through_ms=1_000_000)
        self.assertEqual((c.status, c.rug), ("CENSORED", None))
        ok = label(ch, e, x, deadline_ms=1_000_000, tape_through_ms=1_000_000)  # deadline == tape end: not censored
        self.assertEqual(ok.status, "LABELLED")
        self.assertEqual(label(ch, e, x, filled=False).status, "MISS")

    def test_probe_wallet_row_is_ignored(self):
        ch = Chain()
        e = ch.trade(9)
        ch.trade(10, "buy", trader=rg.PROBE_WALLET, sol=50_000_000, tok=10**10)
        x = ch.trade(11, "sell", sol=10**8)
        res = label(ch, e, x)
        self.assertEqual(res.n_prints, 2)
        self.assertFalse(res.rug)
        pc = rg.PriceCounts()
        rg.price_rows(ch.rows, POOL, VMAP, counts=pc)
        self.assertEqual(pc.probe_dropped, 1)
        self.assertEqual(len(rg.price_rows(ch.rows, POOL, VMAP, probe_wallet=None)), 3)

    def test_drain_none_case_sets_E_plus_to_Vp(self):
        ch = Chain(q=60_000_000_000)
        e = ch.trade(9, "buy")
        x = ch.trade(10, "sell", sol=70_000_000_000, tok=10**14)  # pool delta >= vault: helper returns None
        p = rg.price_rows(ch.rows, POOL, VMAP)[-1]
        self.assertEqual((p.outcome, p.e_after), ("drain", V))
        res = label(ch, e, x)
        self.assertTrue(res.rug)
        self.assertEqual(res.n_drain, 1)

    def test_other_none_case_is_noop(self):
        ch = Chain()
        e = ch.trade(9)
        x = ch.trade(10, "sell", sol=10**8, tok=0)  # malformed amount
        p = rg.price_rows(ch.rows, POOL, VMAP)[-1]
        self.assertEqual((p.outcome, p.e_after), ("noop", p.e_before))
        res = label(ch, e, x)
        self.assertEqual((res.rug, res.n_noop), (False, 1))

    def test_rug70_is_report_only(self):
        ch = Chain()
        e = ch.trade(9)
        x = ch.trade(10, "sell", sol=40_000_000_000, tok=10**14)  # one slot, ratio ~0.5
        res = label(ch, e, x)
        self.assertTrue(res.rug)
        self.assertTrue(res.rug70_1)

    def test_endpoints_must_be_canonical_pool_prints(self):
        ch = Chain()
        e = ch.trade(9)
        foreign = ch.trade(10, pool="OTHER")
        with self.assertRaises(rg.PoolAttributionRefusal):
            label(ch, e, foreign)
        x = ch.trade(11)
        self.assertEqual(label(ch, e, x).n_foreign_in_window, 1)  # reported, never filled

    def test_no_pool_mint_is_not_labelled(self):
        with self.assertRaises(ValueError):
            rg.label_trade([], migration_pool=None, entry_key=(1, 0, 0), exit_key=(2, 0, 0), vmap={})


class PricingParityTests(unittest.TestCase):
    """P1/P4: the label prices a print exactly as the simulator path (adapter.correct_print) does."""

    def _sim_post_quote(self, row, v):
        _, pr = ad.make_wrapper(print_from_trade_row, {POOL: v})(row)
        return pr.quote_reserve

    def test_label_E_plus_equals_simulator_quote_for_each_v_sign(self):
        for side in ("buy", "sell"):
            for v, src in ((V, "map"), (0, "nonpositive"), (-1_500_000, "nonpositive")):
                ch = Chain(v=v)
                ch.trade(5, side, sol=2_000_000_000, tok=10**12)
                row = ch.rows[0]
                vp, got_src = rg.vp_of(POOL, {POOL: v})
                self.assertEqual(got_src, src)
                self.assertEqual(rg.price_row(row, vp).e_after, self._sim_post_quote(row, v), (side, v))

    def test_v_none_pool_is_vault_only_in_both(self):
        ch = Chain(v=0)
        ch.trade(5, "buy", sol=2_000_000_000, tok=10**12)
        row = ch.rows[0]
        _, pr = ad.make_wrapper(print_from_trade_row, {})(row)  # pool not in the map: left unchanged
        self.assertEqual(rg.price_row(row, rg.vp_of(POOL, {})[0]).e_after, pr.quote_reserve)
        self.assertEqual(rg.vp_of(POOL, {POOL: None}), (0, "none"))
        self.assertEqual(rg.vp_of(POOL, {POOL: None}, {POOL: 123}), (123, "fallback"))

    def test_unpriceable_row_matches_simulator(self):
        row = Chain().rows or Chain()
        ch = Chain()
        ch.trade(5)
        bad = dict(ch.rows[0], quote_reserve=0)
        self.assertIsNone(rg.price_row(bad, V))
        self.assertIsNone(print_from_trade_row(bad))
        self.assertIsNone(rg.price_row(dict(ch.rows[0], quote_is_wsol=None), V))


class PoolAttributionTests(unittest.TestCase):
    def test_simulator_never_reads_pool(self):
        # Characterisation of the code today: the finding the PR reports.
        self.assertFalse(rg.source_reads_pool(lc))
        self.assertFalse(rg.source_reads_pool(print_from_trade_row))
        self.assertTrue(rg.source_reads_pool("def f(row):\n    return row['pool']\n"))
        self.assertFalse(rg.source_reads_pool('def f(row):\n    "pool in a docstring"\n    return 1\n'))

    def _mixed(self):
        ch = Chain()
        ch.trade(10)
        ch.trade(11, pool="OTHER")  # a print from a second pool of the same mint, better placed in time
        ch.trade(12)
        return ch.rows

    def _fills(self, rows):
        mint = lc._Mint(1, 0, 0, None)
        for r in rows:
            got = print_from_trade_row(r)
            mint.add(got[1])
        return mint.fillable(migrate=False)

    def test_unrestricted_simulator_can_fill_from_another_pool_and_the_check_refuses(self):
        rows = self._mixed()
        fills = self._fills(rows)
        self.assertEqual(len(fills), 3)  # latency_curve does not tell the pools apart
        with self.assertRaises(rg.PoolAttributionRefusal):
            rg.check_migration_pool_only(rows, POOL, fills)

    def test_restricted_rows_fill_only_from_the_migration_pool(self):
        rows = self._mixed()
        kept = list(rg.restrict_rows_to_migration_pool(rows, POOL))
        fills = self._fills(kept)
        self.assertEqual(len(fills), 2)
        rep = rg.check_migration_pool_only(rows, POOL, fills)
        self.assertEqual((rep["fills_checked"], rep["foreign_pools"], rep["n_foreign_pool_rows"]), (2, ["OTHER"], 1))

    def test_no_pool_mint_has_no_pumpswap_rows_after_restriction(self):
        self.assertEqual(list(rg.restrict_rows_to_migration_pool(self._mixed(), None)), [])

    def test_gate_refuses_before_any_try_unless_restricted(self):
        rows = {"M": self._mixed(), "N": Chain(mint="N").rows}
        with self.assertRaises(rg.PoolAttributionRefusal):
            rg.pool_attribution_gate(rows, {"M": POOL, "N": POOL}, restricted=False)
        rep = rg.pool_attribution_gate(rows, {"M": POOL, "N": POOL}, restricted=True)
        self.assertEqual((rep["mints_with_foreign_pool_prints"], rep["pools"]), (1, {"M": ["OTHER"]}))
        clean = rg.pool_attribution_gate({"N": Chain(mint="N").rows}, {"N": POOL}, restricted=False)
        self.assertEqual(clean["mints_with_foreign_pool_prints"], 0)


class CountingTests(unittest.TestCase):
    def test_counts(self):
        mig = [{"mint": "A", "pool": "p1"}, {"mint": "B", "pool": None}, {"mint": "C"}, {"mint": "D", "pool": "p4"}]
        self.assertEqual(rg.count_no_pool_mints(mig), ["B", "C"])
        self.assertEqual(rg.migration_pool_map(mig), {"A": "p1", "D": "p4"})
        vmap = {"p1": 5, "p2": None, "p3": -7, "p4": None, "p5": 0}
        res = rg.count_unpriced_pools(["p1", "p2", "p3", "p4", "p5", "p6"], vmap, closed=["p4"])
        self.assertEqual(res, {"closed": ["p4"], "parse_fail": ["p2"], "unmapped": ["p6"]})
        cells = [{"id": 1, "deadline_ms": 10, "tape_through_ms": 9}, {"id": 2, "deadline_ms": 9, "tape_through_ms": 9},
                 {"id": 3, "deadline_ms": 10, "tape_through_ms": 9, "filled": False}]
        self.assertEqual(rg.count_censored(cells), [1])

    def test_silent_pool_cells(self):
        cells = [{"id": "a", "pool": "p", "deadline_ms": 100_000}, {"id": "b", "pool": "q", "deadline_ms": 100_000},
                 {"id": "c", "pool": "p", "deadline_ms": 100_000, "filled": False}]
        prints = {"p": [30_000, 39_000], "q": [40_000, 100_000]}
        self.assertEqual(rg.count_silent_pool_cells(cells, prints), ["a"])


# ---- features ----------------------------------------------------------------------------------------------------

MIG = 300


def curve(slot, side, w, tok, sol, sig, bt, q=30_000_000_000):
    return {"venue": "pump_bonding", "mint": "M", "trader": w, "side": side, "token_raw": tok, "sol_lamports": sol, "slot": slot,
            "signature": sig, "block_time": bt, "event_index": 0, "tx_index": 0, "quote_reserve": q}


CREATE = {"type": "create", "mint": "M", "creator": "C", "trader": "C", "slot": 100, "signature": "cs", "block_time": 1000, "tx_index": 0}
T = 10**13


def base_rows():
    return [
        curve(100, "buy", "C", 5 * T, 10**9, "cs", 1000),
        curve(100, "buy", "A1", 3 * T, 10**9, "a1", 1000),
        curve(101, "buy", "A2", 2 * T, 2 * 10**9, "a2", 1001),
        curve(101, "buy", "B1", 1 * T, 10**9, "b1", 1002),
        curve(120, "buy", "C", 1 * T, 10**9, "c2", 1048),
        curve(150, "sell", "A1", 1 * T, 0, "s1", 1060),
        curve(160, "sell", "C", 1 * T, 0, "s2", 1070),
        curve(200, "buy", "D", 4 * T, 3 * 10**9, "d1", 1100),
    ]


class FeatureTests(unittest.TestCase):
    def feats(self, rows, history=None, mig=MIG):
        return rg.features("M", create_row=CREATE, rows=rows, migration_slot=mig, history=history)

    def test_values(self):
        f = self.feats(base_rows())
        self.assertEqual(list(f), rg.FEATURE_NAMES)
        exp = {
            "n_launch_buyers": 3, "launch_supply_bought": 0.06, "launch_supply_held": 0.05, "n_create_slot_buyers": 1,
            "max_slot_cohort_held": 0.03, "creator_launch_share": 0.05, "creator_n_buys_after": 1, "creator_n_sells": 1,
            "creator_sold_frac": 1 / 6, "creator_share": 0.05, "sniper_buy_share": 5 / 9, "launch_sol_share": 5 / 9,
            "top3_share": 0.08, "n_buyers": 5,
            "serial_launch_held": 0, "creator_prior_dumps": 0, "prior_dumper_held": 0, "creator_buyer_recurrence": 0,
        }
        for k, v in exp.items():
            self.assertAlmostEqual(f[k], v, msg=k)

    def test_shuffled_future_events_do_not_change_features(self):
        base = base_rows()
        ref = self.feats(base)
        rng = random.Random(1)
        future = [curve(MIG, "buy", "X1", 9 * T, 9 * 10**9, "f1", 1001), curve(MIG, "sell", "A1", 2 * T, 0, "f2", 1001),
                  curve(MIG + 1, "buy", "C", 3 * T, 10**9, "f3", 1001), curve(MIG + 50, "sell", "C", 5 * T, 0, "f4", 1001)]
        for _ in range(20):
            rows = base + future
            rng.shuffle(rows)
            self.assertEqual(self.feats(rows), ref)  # includes the migration slot itself, and c1 / e2 at the slot cutoff
        # an early block_time on a future row must not leak into c1 either
        self.assertEqual(self.feats(base + [curve(MIG, "buy", "Z", T, 5 * 10**9, "z", 1000)]), ref)

    def test_probe_wallet_rows_are_dropped(self):
        ref = self.feats(base_rows())
        self.assertEqual(self.feats(base_rows() + [curve(105, "buy", rg.PROBE_WALLET, 9 * T, 9 * 10**9, "p", 1001)]), ref)

    def test_pumpswap_rows_are_not_curve_features(self):
        ch = Chain()
        ch.trade(150)
        self.assertEqual(self.feats(base_rows() + ch.rows), self.feats(base_rows()))


def other_mint(name, create_slot, launch_wallets, *, creators=("X",), dump_slot=None, sellers=("S",)):
    cr = {"type": "create", "mint": name, "creator": creators[0], "trader": creators[-1], "slot": create_slot, "signature": f"{name}cs",
          "virtual_sol_reserves": 30_000_000_000}
    rows = []
    q = 30_000_000_000
    for i, w in enumerate(launch_wallets):
        q += 10**8
        r = curve(create_slot + 1, "buy", w, T, 10**8, f"{name}l{i}", 0, q=q)
        r["mint"], r["event_index"] = name, i
        rows.append(r)
    if dump_slot is not None:
        for i, w in enumerate(sellers):
            q = int(q * 0.5)
            r = curve(dump_slot + i, "sell", w, T, 0, f"{name}d{i}", 0, q=q)
            r["mint"], r["event_index"] = name, 100 + i
            rows.append(r)
    return cr, rows


class ClusterFeatureTests(unittest.TestCase):
    def history(self, dump_slot, extra=()):
        recs = []
        # three earlier mints where A1 and A2 are launch buyers; the first is by the same creator and has a dump step
        cr, rows = other_mint("N1", 50, ["A1", "A2"], creators=("C",), dump_slot=dump_slot)
        recs.append(rg.build_mint_record("N1", cr, rows))
        for nm, s in (("N2", 60), ("N3", 70)):
            cr, rows = other_mint(nm, s, ["A1"])
            recs.append(rg.build_mint_record(nm, cr, rows))
        for cr, rows in extra:
            recs.append(rg.build_mint_record(cr["mint"], cr, rows))
        return rg.BlockHistory(recs)

    def feats(self, hist):
        return rg.features("M", create_row=CREATE, rows=base_rows(), migration_slot=MIG, history=hist)

    def test_d_group_values(self):
        f = self.feats(self.history(dump_slot=MIG - 3))  # s + 2 = MIG - 1 < MIG: counts
        self.assertAlmostEqual(f["serial_launch_held"], 0.02)  # A1 launched on N1, N2, N3; held 2e13
        self.assertEqual(f["creator_prior_dumps"], 1)  # N1 is by creator C and has a dump step
        self.assertAlmostEqual(f["prior_dumper_held"], 0.0)  # seller "S" holds nothing on M
        self.assertEqual(f["creator_buyer_recurrence"], 2)  # A1, A2 (B1 never launched on N1)

    def test_dump_step_counts_only_if_s_plus_2_before_migration_slot(self):
        late = self.feats(self.history(dump_slot=MIG - 2))  # s + 2 == MIG: window not closed before the cutoff
        self.assertEqual(late["creator_prior_dumps"], 0)
        self.assertEqual(self.feats(self.history(dump_slot=MIG - 3))["creator_prior_dumps"], 1)

    def test_prior_dumper_held(self):
        hist = self.history(dump_slot=MIG - 10, extra=())
        cr, rows = other_mint("N9", 80, ["Q"], dump_slot=MIG - 20, sellers=("A2",))
        hist.records.append(rg.build_mint_record("N9", cr, rows))
        self.assertAlmostEqual(self.feats(hist)["prior_dumper_held"], 0.02)  # A2 dumped on N9, holds 2e13 on M

    def test_other_mints_events_at_or_after_cutoff_never_change_features(self):
        ref = self.feats(self.history(dump_slot=MIG - 3))
        # N4 is created at/after the migration slot, with launch buys by A1/A2 and a dump by A2: invisible
        cr, rows = other_mint("N4", MIG, ["A1", "A2"], creators=("C",), dump_slot=MIG + 1, sellers=("A2",))
        # a mint created before the cutoff whose launch buys / dump fall at or after it
        cr5, rows5 = other_mint("N5", MIG - 1, ["A1", "A2"], creators=("C",), dump_slot=MIG, sellers=("A2",))
        got = self.feats(self.history(dump_slot=MIG - 3, extra=[(cr, rows), (cr5, rows5)]))
        self.assertEqual(got, ref)
        # shuffling the records changes nothing either
        h = self.history(dump_slot=MIG - 3, extra=[(cr, rows), (cr5, rows5)])
        random.Random(2).shuffle(h.records)
        self.assertEqual(self.feats(h), ref)

    def test_history_without_block_pass_zeroes_d(self):
        f = rg.features("M", create_row=CREATE, rows=base_rows(), migration_slot=MIG)
        self.assertEqual([f[k] for k in rg.FEATURE_NAMES[12:16]], [0.0] * 4)


if __name__ == "__main__":
    unittest.main()
