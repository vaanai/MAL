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
        return row

    def gap(self, frac):
        """An untaped drain: the vault falls by `frac` with no print."""
        self.q = int(self.q * (1 - frac))


VMAP = {POOL: V}


def keyof(rows, row):
    for r, k in rg.stamp_rows(rows):
        if r is row:
            return k
    raise KeyError


def label(ch, entry, exit_, **kw):
    kw.setdefault("vmap", VMAP)
    return rg.label_trade(ch.rows, migration_pool=POOL, entry_key=keyof(ch.rows, entry), exit_key=keyof(ch.rows, exit_), **kw)


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

    def test_gate_verifies_the_fed_rows_not_a_flag(self):
        nch = Chain(mint="N")
        nch.trade(30)
        raw = {"M": self._mixed(), "N": nch.rows}
        pools = {"M": POOL, "N": POOL}
        with self.assertRaises(rg.PoolAttributionRefusal):  # fed unrestricted
            rg.pool_attribution_gate(raw, raw, pools)
        fed = {m: list(rg.restrict_rows_to_migration_pool(r, pools[m])) for m, r in raw.items()}
        rep = rg.pool_attribution_gate(raw, fed, pools)
        self.assertEqual((rep["mints_with_foreign_pool_prints"], rep["pools"]), (1, {"M": ["OTHER"]}))
        clean = rg.pool_attribution_gate({"N": raw["N"]}, {"N": raw["N"]}, {"N": POOL})
        self.assertEqual(clean["mints_with_foreign_pool_prints"], 0)
        # a mint with no migration pool must be fed no PumpSwap rows at all
        with self.assertRaises(rg.PoolAttributionRefusal):
            rg.pool_attribution_gate(raw, {"N": raw["N"]}, {})

    def test_ambiguous_fill_key_is_refused(self):
        ch = Chain()
        ch.trade(10)
        ch.trade(10, pool="OTHER")
        a, b = ch.rows
        b.update(slot=10, tx_index=0, event_index=a["event_index"], t_recv_ms=a["t_recv_ms"] + 5, signature="other-sig")
        fill = {"t_recv_ms": 1, "slot": 10, "tx_index": 0, "event_index": a["event_index"]}  # collapsed-time fill
        with self.assertRaises(rg.PoolAttributionRefusal):
            rg.check_migration_pool_only(ch.rows, POOL, [fill])
        exact = dict(fill, t_recv_ms=a["t_recv_ms"])
        self.assertEqual(rg.check_migration_pool_only(ch.rows, POOL, [exact])["fills_checked"], 1)


class CountingTests(unittest.TestCase):
    def test_counts(self):
        mig = [{"mint": "A", "pool": "p1"}, {"mint": "B", "pool": None}, {"mint": "C"}, {"mint": "D", "pool": "p4"}]
        self.assertEqual(rg.count_no_pool_mints(mig), ["B", "C"])
        self.assertEqual(rg.migration_pool_map(mig), {"A": "p1", "D": "p4"})
        vmap = {"p1": 5, "p2": None, "p3": -7, "p4": None, "p5": 0}
        with self.assertRaises(TypeError):
            rg.count_unpriced_pools(["p1"], vmap)  # closed is required
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
          "quote_reserve": 30_000_000_000}  # the backfill stores the starting virtual SOL as quote_reserve
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


def oracle_row(ch, slot, sig, ev, t, side, sol, tok=10**11):
    """A PumpSwap row as the Oracle tape has it: no tx_index, receive time, read order = list order."""
    row = {"venue": "pumpswap", "mint": "M", "trader": "W", "side": side, "sol_lamports": sol, "token_raw": tok, "quote_reserve": ch.q,
           "base_reserve": ch.b, "pool": POOL, "slot": slot, "event_index": ev, "t_recv_ms": t, "quote_is_wsol": True, "signature": sig}
    post = pumpswap_post_trade_reserves(side=side, quote_reserve=ch.q, base_reserve=ch.b, sol_lamports=sol, token_raw=tok,
                                        fee_ppm=venue_fee_ppm("pumpswap", ch.q / (ch.b * 1000) * 1e9))
    ch.q, ch.b = post
    ch.rows.append(row)
    return row


class IntraSlotOrderTests(unittest.TestCase):
    def test_oracle_two_sells_in_one_slot_order_by_read_not_event_index(self):
        # quant-proof's case: X (event_index 4) is read first, E 100 -> ~69.6; Y (event_index 0) second, E ~70 -> ~44.7.
        ch = Chain(q=100_000_000_000, v=0)
        entry = oracle_row(ch, 9, "e", 0, 999, "buy", 10**8)
        ch.q = 100_000_000_000  # restart the pool at E = 100 for the slot under test
        x = oracle_row(ch, 10, "X", 4, 1000, "sell", 30_000_000_000)
        y = oracle_row(ch, 10, "Y", 0, 1000, "sell", 25_000_000_000)
        pr = rg.price_rows(ch.rows, POOL, {POOL: 0})
        self.assertEqual([p.side for p in pr], ["buy", "sell", "sell"])
        self.assertEqual(len({p.key for p in pr}), 3)  # unique keys
        self.assertEqual([p.e_before for p in pr[1:]][0], x["quote_reserve"])  # X first, then Y
        self.assertLess(abs(pr[1].e_after / 1e9 - 69.7), 0.2)
        self.assertLess(abs(pr[2].e_after / 1e9 - 44.7), 0.5)
        res = rg.label_trade(ch.rows, migration_pool=POOL, entry_key=pr[0].key, exit_key=pr[2].key, vmap={POOL: 0})
        self.assertTrue(res.rug)
        self.assertEqual(res.event, "A")
        self.assertAlmostEqual(res.drop, 0.553, delta=0.01)

    def test_order_follows_t_recv_then_first_read_for_oracle_rows(self):
        ch = Chain(q=100_000_000_000, v=0)
        a = oracle_row(ch, 10, "A", 7, 1000, "buy", 10**8)
        b = oracle_row(ch, 10, "B", 0, 1000, "buy", 10**8)
        c = oracle_row(ch, 10, "C", 3, 1001, "buy", 10**8)
        order = [r["signature"] for r, _ in rg.stamp_rows([c, b, a])]
        self.assertEqual(order, ["B", "A", "C"])  # t first, then first read (b before a)

    def test_rows_without_receive_time_are_dropped_and_counted(self):
        ch = Chain()
        ch.trade(9)
        bad = dict(ch.rows[0], t_recv_ms=None, block_time=None, event_index=99)
        gb = dict(ch.rows[0], t_recv_ms=None, block_time=7, event_index=98, signature="gb")
        pc = rg.PriceCounts()
        out = rg.stamp_rows([ch.rows[0], bad, gb], pc)
        self.assertEqual((pc.no_t, len(out)), (1, 2))
        self.assertEqual(out[1][1][0], 7000)  # block_time * 1000, as latency_curve.run_holdout

    def test_repeated_key_raises_and_exact_duplicate_is_dropped(self):
        ch = Chain()
        ch.trade(9)
        r = ch.rows[0]
        pc = rg.PriceCounts()
        self.assertEqual(len(rg.stamp_rows([r, dict(r)], pc)), 1)
        self.assertEqual(pc.dup, 1)
        with self.assertRaises(ValueError):
            rg.stamp_rows([r, dict(r, quote_reserve=r["quote_reserve"] + 1)])


class SlotInversionTests(unittest.TestCase):
    """Oracle rows sort by receive time first, so slots can invert across the window; the window end is the last
    print in stamped order with slot <= s + 2, not a bisect on the slot list."""

    def rows(self):
        ch = Chain(q=100_000_000_000, v=0)
        entry = oracle_row(ch, 9, "e", 0, 999, "buy", 10**8)
        ch.q = 100_000_000_000
        oracle_row(ch, 10, "X", 4, 1000, "sell", 30_000_000_000)  # E 100 -> ~70
        oracle_row(ch, 14, "Z", 0, 1001, "buy", 10**8)  # slot 14, read early
        oracle_row(ch, 11, "Y", 0, 1002, "sell", 25_000_000_000)  # E ~70 -> ~45
        last = oracle_row(ch, 20, "L", 0, 1003, "buy", 10**8)
        return ch.rows, entry, last

    def test_label_sees_the_dump_across_a_slot_inversion(self):
        rows, entry, last = self.rows()
        pr = rg.price_rows(rows, POOL, {POOL: 0})
        self.assertEqual([p.slot for p in pr], [9, 10, 14, 11, 20])
        res = rg.label_trade(rows, migration_pool=POOL, entry_key=pr[0].key, exit_key=pr[-1].key, vmap={POOL: 0})
        self.assertTrue(res.rug)
        self.assertEqual(res.event, "A")
        self.assertAlmostEqual(res.worst_ratio, 0.45, delta=0.02)

    def test_dump_steps_across_a_slot_inversion(self):
        rows, _e, _l = self.rows()
        steps = rg.find_dump_steps(rg.price_rows(rows, POOL, {POOL: 0}))
        self.assertIn(10, [st.slot for st in steps])
        self.assertIn("W", next(st for st in steps if st.slot == 10).sellers)

    def test_last_within_matches_a_scan(self):
        rng = random.Random(3)
        for _ in range(200):
            sl = [rng.randint(0, 12) for _ in range(rng.randint(0, 9))]
            for lim in range(-1, 14):
                want = max((i for i, v in enumerate(sl) if v <= lim), default=-1)
                self.assertEqual(rg._last_within(sl, lim), want)

    def test_count_slot_inversions(self):
        rows, _e, _l = self.rows()
        self.assertEqual(rg.count_slot_inversions(rows), 1)  # slot 11 after slot 14
        ch = Chain()
        for s in (5, 6, 7):
            ch.trade(s)
        self.assertEqual(rg.count_slot_inversions(ch.rows), 0)


class CreateFieldTests(unittest.TestCase):
    def test_dump_step_starting_at_the_first_curve_print_fires(self):
        cr = dict(CREATE, quote_reserve=30_000_000_000)
        first = curve(101, "sell", "S", T, 0, "f1", 0, q=18_000_000_000)  # 18/30 = 0.6 <= 0.6325 against the create row's start
        first.update(t_recv_ms=5, event_index=0)
        rec = rg.build_mint_record("M", cr, [first])
        self.assertEqual([st.slot for st in rec.dump_steps], [101])
        # without the starting reserve the first print has no "before" and cannot fire
        self.assertEqual(rg.build_mint_record("M", dict(CREATE), [first]).dump_steps, [])
        # the old, wrong field name is not read
        self.assertEqual(rg.build_mint_record("M", dict(CREATE, virtual_sol_reserves=30_000_000_000), [first]).dump_steps, [])


class MigrationClockTests(unittest.TestCase):
    def rows(self):
        bond = {"venue": "pump_bonding", "mint": "M", "trader": "A", "side": "buy", "sol_lamports": 10**9, "token_raw": T, "quote_reserve": 80 * 10**9,
                "base_reserve": 10**14, "slot": 100, "event_index": 0, "tx_index": 0, "t_recv_ms": 1000, "signature": "b0"}
        ch = Chain()
        ch.trade(110, pool="OTHER")  # foreign-pool print first
        ch.trade(115)  # then the migration pool
        return [bond] + ch.rows

    def clock(self, rows):
        mint = lc._Mint(1, 0, 0, None)
        for r in rows:
            got = print_from_trade_row(r)
            if got:
                mint.add(got[1])
        return mint.mig_slot, mint.mig_ms

    def test_restriction_shifts_the_migration_clock_when_a_foreign_print_comes_first(self):
        rows = self.rows()
        self.assertEqual(self.clock(rows)[0], 110)  # latency_curve takes the first PumpSwap print of ANY pool
        kept = list(rg.restrict_rows_to_migration_pool(rows, POOL))
        self.assertEqual(self.clock(kept)[0], 115)
        self.assertGreater(self.clock(kept)[1], self.clock(rows)[1])

    def test_counter_of_foreign_first_mints(self):
        good = Chain(mint="N")
        good.trade(120)
        bond = dict(self.rows()[0], mint="N")
        raw = {"M": self.rows(), "N": [bond] + good.rows}
        self.assertEqual(rg.count_foreign_first_mints(raw, {"M": POOL, "N": POOL}), ["M"])
        rep = rg.pool_attribution_gate(raw, {m: list(rg.restrict_rows_to_migration_pool(r, POOL)) for m, r in raw.items()}, {"M": POOL, "N": POOL})
        self.assertEqual(rep["foreign_first_mints"], ["M"])


class D1LookAheadTests(unittest.TestCase):
    def test_launch_buys_at_or_after_the_cutoff_cannot_complete_a_serial_launch_buyer(self):
        recs = []
        for nm, s in (("N1", 50), ("N2", 60)):
            cr, rows = other_mint(nm, s, ["A1"])
            recs.append(rg.build_mint_record(nm, cr, rows))
        base = rg.BlockHistory(recs)
        f0 = rg.features("M", create_row=CREATE, rows=base_rows(), migration_slot=MIG, history=base)
        self.assertEqual(f0["serial_launch_held"], 0.0)  # A1 launch-bought on only 2 other mints
        # N3 is created before the cutoff, but its launch buy is at the cutoff slot: it must not count
        cr, rows = other_mint("N3", MIG - 1, ["A1"])
        self.assertEqual(rows[0]["slot"], MIG)
        leaked = rg.BlockHistory(recs + [rg.build_mint_record("N3", cr, rows)])
        f1 = rg.features("M", create_row=CREATE, rows=base_rows(), migration_slot=MIG, history=leaked)
        self.assertEqual(f1["serial_launch_held"], 0.0)
        # and one slot earlier it does count (the test can fail)
        cr, rows = other_mint("N3", MIG - 2, ["A1"])
        early = rg.BlockHistory(recs + [rg.build_mint_record("N3", cr, rows)])
        f2 = rg.features("M", create_row=CREATE, rows=base_rows(), migration_slot=MIG, history=early)
        self.assertAlmostEqual(f2["serial_launch_held"], 0.02)


class ParityScopeTests(unittest.TestCase):
    def test_noop_print_label_equals_simulator(self):
        ch = Chain(v=V)
        ch.trade(5, "sell", sol=10**8, tok=0)  # malformed amount: the post-trade helper returns None
        row = ch.rows[0]
        _, pr = ad.make_wrapper(print_from_trade_row, {POOL: V})(row)
        p = rg.price_row(row, V)
        self.assertEqual(p.outcome, "noop")
        self.assertEqual(p.e_after, pr.quote_reserve)

    def test_one_merged_map_gives_label_and_simulator_the_same_v(self):
        ch = Chain(v=0)
        ch.trade(5, "buy", sol=2_000_000_000, tok=10**12)
        row = ch.rows[0]
        fb = {POOL: 12_000_000_000}
        merged = rg.merge_v_map({POOL: None}, fb)
        self.assertEqual(merged, {POOL: 12_000_000_000})
        vp, src = rg.vp_of(POOL, merged)
        _, pr = ad.make_wrapper(print_from_trade_row, merged)(row)
        self.assertEqual(rg.price_row(row, vp).e_after, pr.quote_reserve)
        self.assertEqual(rg.merge_v_map({POOL: -5}, fb), {POOL: -5})  # a readable stored V <= 0 stays vault-only


def _reference_query(records, mint, creators, create_slot, cutoff, my_launch, held):
    """The pre-index implementation of BlockHistory.query (full scan), kept verbatim as the byte-identity oracle."""
    win = [r for r in records if r.mint != mint and create_slot - rg.SLOTS_24H <= r.create_slot < cutoff]
    n_other: dict = {}
    for r in win:
        for s, w in r.launch_buys:
            if s < cutoff and w in my_launch:
                n_other.setdefault(w, set()).add(r.mint)
    serial = [w for w, ms in n_other.items() if len(ms) >= 3]
    earlier = [r for r in win if r.create_slot < create_slot and r.creators & creators]

    def valid(r):
        return [st for st in r.dump_steps if st.slot + rg.WINDOW_SLOTS < cutoff]

    d2 = sum(1 for r in earlier if valid(r))
    dumpers = {w for r in win for st in valid(r) for w in st.sellers}
    prior_buyers = {w for r in earlier for s, w in r.launch_buys if s < cutoff}
    return {
        "serial_launch_held": sum(held.get(w, 0) for w in serial) / rg.SUPPLY_RAW,
        "creator_prior_dumps": float(d2),
        "prior_dumper_held": sum(held.get(w, 0) for w in dumpers) / rg.SUPPLY_RAW,
        "creator_buyer_recurrence": float(len(my_launch & prior_buyers)),
    }


class BlockHistoryIndexTests(unittest.TestCase):
    def test_indexed_query_equals_full_scan_on_random_fixtures(self):
        import random

        rnd = random.Random(16)
        wallets = [f"W{i}" for i in range(12)]
        creators_pool = [f"C{i}" for i in range(6)]
        n_mints = 400
        span = 3 * rg.SLOTS_24H
        recs = []
        for i in range(n_mints):
            cs = rnd.randrange(0, span)
            if i % 7 == 0 and recs:
                cs = recs[-1].create_slot  # duplicate create slots
            lb = [(cs + rnd.randrange(0, 4), rnd.choice(wallets)) for _ in range(rnd.randrange(0, 5))]
            ds = [rg.DumpStep(cs + rnd.randrange(1, 200), frozenset(rnd.sample(wallets, rnd.randrange(0, 4)))) for _ in range(rnd.randrange(0, 3))]
            recs.append(rg.MintRecord(f"R{i}", frozenset(rnd.sample(creators_pool, rnd.randrange(1, 3))), cs, lb, ds))
        hist = rg.BlockHistory(recs)
        checked = nonzero = 0
        for r in recs:
            mig = r.create_slot + rnd.randrange(1, 300)
            cutoffs = [mig]
            # edge cases: cutoffs at s + 2 and s + 3 of dump steps (s + 2 < cutoff flips), and at launch-buy slots (events in the migration slot itself)
            for other in rnd.sample(recs, 5):
                for st in other.dump_steps:
                    cutoffs += [st.slot + rg.WINDOW_SLOTS, st.slot + rg.WINDOW_SLOTS + 1]
                for s, _w in other.launch_buys:
                    cutoffs += [s, s + 1]
            for cutoff in cutoffs[:12]:
                my_launch = set(rnd.sample(wallets, rnd.randrange(0, 8)))
                held = {w: rnd.randrange(0, 10**15) for w in wallets}
                args = (r.mint, r.creators, r.create_slot, cutoff, my_launch, held)
                got, want = hist.query(*args), _reference_query(recs, *args)
                self.assertEqual(repr(got), repr(want))
                checked += 1
                nonzero += any(want.values())
        self.assertGreater(checked, 1000)
        self.assertGreater(nonzero, 100)  # the oracle is not vacuous

    def test_indexed_query_equals_full_scan_boundaries_duplicates_growth(self):
        """Many seeds: boundary records at create_slot - SLOTS_24H (+-1) and cutoff +-1, duplicate mint names with the query mint present,
        creators that are also launch wallets / dump sellers, launch-buy slots below create_slot, records appended between queries."""
        import random

        H = rg.SLOTS_24H
        checked = nonzero = 0
        for seed in range(150):
            rnd = random.Random(seed)
            W = [f"W{i}" for i in range(rnd.randrange(2, 10))]
            C = [f"C{i}" for i in range(rnd.randrange(1, 5))] + W[:2]
            names = [f"R{i}" for i in range(rnd.randrange(5, 60))]
            base = rnd.randrange(H, 2 * H)
            recs = []
            for _ in range(rnd.randrange(1, 120)):
                cs = rnd.choice([base, base - H, base - H - 1, base - H + 1, base - 1, base + 1, rnd.randrange(base - H - 50, base + 50)])
                lb = [(cs + rnd.randrange(-2, 6), rnd.choice(W)) for _ in range(rnd.randrange(0, 6))]
                ds = [rg.DumpStep(cs + rnd.randrange(-3, 60), frozenset(rnd.sample(W + C, rnd.randrange(0, 4)))) for _ in range(rnd.randrange(0, 3))]
                recs.append(rg.MintRecord(rnd.choice(names + ["Q"]), frozenset(rnd.sample(C, rnd.randrange(0, 3))), cs, lb, ds))
            h = rg.BlockHistory(list(recs))
            for q in range(40):
                if q == 20:  # records appended after earlier queries
                    extra = [rg.MintRecord("Q" if rnd.random() < .3 else rnd.choice(names), frozenset({rnd.choice(C)}), rnd.choice([base, base - H]),
                                           [(base - 1, rnd.choice(W))], []) for _ in range(3)]
                    h.records.extend(extra)
                    recs.extend(extra)
                mint = rnd.choice(["Q", "Z"] + names)
                cutoff = rnd.choice([base, base + 1, base - 1, base + rnd.randrange(-5, 60)])
                args = (mint, frozenset(rnd.sample(C, rnd.randrange(0, len(C) + 1))), base, cutoff, set(rnd.sample(W, rnd.randrange(0, len(W) + 1))),
                        {w: rnd.randrange(0, 10**15) for w in W + C})
                got, want = h.query(*args), _reference_query(recs, *args)
                self.assertEqual(repr(got), repr(want), (seed, q))
                checked += 1
                nonzero += any(want.values())
        self.assertGreater(checked, 5000)
        self.assertGreater(nonzero, 500)

    def test_empty_history_and_cutoff_before_everything(self):
        args = ("M", frozenset({"C"}), 100, 50, {"W"}, {})
        self.assertEqual(rg.BlockHistory([]).query(*args), _reference_query([], *args))



if __name__ == "__main__":
    unittest.main()
