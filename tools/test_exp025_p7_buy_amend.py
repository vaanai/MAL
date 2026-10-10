"""Tests for ARTIFACTS/exp025/p7_buy_amend.py: the amended buy side of EXP-025 P7 line 1 (quant-proof ruling 2026-10-10, section 2).

Synthetic events follow the integer laws of event_v_map.py; the real-fixture tests decode public getTransaction results kept in the repo
(tools/fixtures/walk2_event_v, the same files tools/test_exp025.py::RawEventP7 uses). No network, no /data/mal, no forward or walk row."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
ART = os.path.join(ROOT, "ARTIFACTS", "exp025")
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def load(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def sums() -> dict:
    return {ln.split(None, 1)[1].strip(): ln.split(None, 1)[0] for ln in open(os.path.join(ART, "SHA256SUMS")).read().splitlines() if ln.strip()}


class P7BuyAmend(unittest.TestCase):
    V0 = 17_585_000_000
    BASE = 1_000_000_000_000_000
    VAULT = 60_000_000_000
    PENDING = 2_300_000_000          # about 3% of the effective quote, kept in the vault and subtracted from stored V

    @classmethod
    def setUpClass(cls):
        cls.m = load("exp025_p7_buy_amend_test", os.path.join(ART, "p7_buy_amend.py"))
        cls.ev = load("exp025_event_v_map_test", os.path.join(ART, "event_v_map.py"))

    # -- synthetic raw events ---------------------------------------------------------------------------------------------------------------
    def _q(self, pending=None):
        """The effective quote Q = vault + V(t) = adapter column + V0."""
        return self.VAULT + self.V0 - (self.PENDING if pending is None else pending)

    def _base(self, side, i=0, pending=None):
        pending = self.PENDING if pending is None else pending
        return {"venue": "pumpswap", "pool": "POOL", "side": side, "slot": 1000 + i, "tx_index": 7 + i, "signature": f"sig{i}", "event_index": i % 3,
                "quote_reserve": self.VAULT, "base_reserve": self.BASE, "virtual_quote_reserves": self.V0 - pending}

    def _sell(self, i=0, pending=None):
        r = self._base("sell", i, pending)
        base_in = 3_000_000_000_000 + i * 1_000_000_000
        gross = self._q(pending) * base_in // (self.BASE + base_in)
        r.update(token_raw=base_in, pool_quote_amount=gross, sol_lamports=gross * 9_925 // 10_000)
        return r

    def _buy(self, i=0, pending=None, ix_name="buy", token_raw=None):
        """An exact-out buy (the `buy` instruction): the user names token_raw and pays the program's ceil(Q * t / (B - t))."""
        r = self._base("buy", i, pending)
        t = 5_000_000_000_000 + i * 1_000_000 if token_raw is None else token_raw
        q = self._q(pending)
        qin = -((-q * t) // (self.BASE - t))
        r.update(token_raw=t, pool_quote_amount=qin, sol_lamports=qin * 10_125 // 10_000, ix_name=ix_name)
        return r

    def _buy_exact_in(self, qin, pending=None, ix_name="buy"):
        """An exact-in buy: the user names qin and receives floor(B * qin / (Q + qin))."""
        r = self._base("buy", 0, pending)
        t = self.BASE * qin // (self._q(pending) + qin)
        r.update(token_raw=t, pool_quote_amount=qin, sol_lamports=qin, ix_name=ix_name)
        return r

    def _dust_exact_out(self, qin):
        """The exact-out buy that pays `qin` and whose token_raw is the smallest that costs qin: the forward law overstates it the most."""
        t = self.ev.cp_buy_token_out(self._q() - self.V0, self.V0, self.BASE, qin - 1) + 1
        raw = self._buy(token_raw=t)
        self.assertEqual(raw["pool_quote_amount"], qin)
        return raw

    @staticmethod
    def _tape(raw):
        return {k: v for k, v in raw.items() if k not in ("pool_quote_amount", "lp_fee", "protocol_fee", "creator_fee")}

    def _adapter(self, raw, v0=None, gross=False, **over):
        v0 = self.V0 if v0 is None else v0
        q = raw["quote_reserve"] if gross else self.ev.map_quote_reserve(raw["quote_reserve"], raw["virtual_quote_reserves"], v0)
        row = {"pool": raw["pool"], "slot": raw["slot"], "tx_index": raw.get("tx_index"), "event_index": raw["event_index"],
               "quote_reserve": q, "base_reserve": raw["base_reserve"], "token_raw": raw["token_raw"]}
        row.update(over)
        return row

    def _check(self, raw, adapter="correct", mod=None, v0=None, **kw):
        mod = mod or self.m
        v0 = self.V0 if v0 is None else v0
        row = self._adapter(raw, v0) if adapter == "correct" else (self._adapter(raw, v0, gross=True) if adapter == "gross" else adapter)
        return mod.p7_raw_check(self._tape(raw), raw, row, v0, **kw)

    def _real(self, name):
        from observe.trade_decode import records_from_logs
        from observe.trade_store import stored_trade

        with open(os.path.join(ROOT, "tools", "fixtures", "walk2_event_v", name), encoding="utf-8") as fh:
            d = json.load(fh)
        (rec,) = records_from_logs(d["meta"]["logMessages"], slot=d["slot"], signature=d["signature"], t_recv_ms=0, commitment="confirmed", feed="t", event_v=True)
        rec = dict(rec, tx_index=5)
        return rec, dict(stored_trade(rec), tx_index=5)

    # -- pins and reuse -----------------------------------------------------------------------------------------------------------------------
    def test_event_v_map_is_untouched_and_pinned_by_this_module(self):
        path = os.path.join(ART, "event_v_map.py")
        got = hashlib.sha256(open(path, "rb").read()).hexdigest()
        self.assertEqual(got, self.m.EVENT_V_MAP_SHA256)
        self.assertEqual(sums()["event_v_map.py"], self.m.EVENT_V_MAP_SHA256)
        b = open(path, "rb").read()
        self.assertEqual(hashlib.sha1(b"blob %d\0" % len(b) + b).hexdigest(), "9771ec333046e065ce66921e88f4a893bd557aec")   # the #555 P3 E0 blob
        # this module itself is listed in SHA256SUMS at its current bytes
        self.assertEqual(sums()["p7_buy_amend.py"], hashlib.sha256(open(os.path.join(ART, "p7_buy_amend.py"), "rb").read()).hexdigest())

    def test_refuses_an_edited_event_v_map(self):
        with tempfile.TemporaryDirectory() as d:
            shutil.copy(os.path.join(ART, "p7_buy_amend.py"), d)
            with open(os.path.join(ART, "event_v_map.py")) as src, open(os.path.join(d, "event_v_map.py"), "w") as dst:
                dst.write(src.read() + "\n# edited\n")
            with self.assertRaises(ImportError):
                load("p7_buy_amend_edited", os.path.join(d, "p7_buy_amend.py"))

    def test_reuses_frame_main_draw_tolerance_and_pass_unchanged(self):
        ev = self.m.EV
        for name in ("within_tolerance", "p7_raw_frame", "p7_raw_main_draw", "p7_raw_pass"):
            self.assertIs(getattr(self.m, name), getattr(ev, name), name)
        self.assertEqual((ev.P7_CP_TOLERANCE_BP, ev.P7_CP_TOLERANCE_UNITS, ev.P7_CP_SELL_MIN, ev.P7_CP_BUY_MIN), (1.0, 2, 0.99, 0.99))
        self.assertEqual((self.m.P7_SAMPLE, self.m.P7_RAW_BUY_MIN_COMPARABLE), (1000, 100))
        self.assertEqual(self.m.P7_RAW_REASONS, self.ev.P7_RAW_REASONS)
        self.assertEqual(self.m.P7_BUY_IX_WHITELIST, ("buy", "buy_v2"))
        self.assertEqual(self.m.P7_RAW_EXCLUSIONS, ("zero_sol", "not_buy_or_sell", "buy_exact_quote_in", "no_ix_name", "ix_not_listed"))

    # -- the law ------------------------------------------------------------------------------------------------------------------------------
    def test_dust_exact_out_buy_misses_the_forward_law_and_hits_the_inverse(self):
        for qin in (1_660, 4_898):
            raw = self._dust_exact_out(qin)
            self.assertEqual(self._check(raw, mod=self.ev), ("buy", "miss", None), qin)       # the pinned forward law
            self.assertEqual(self._check(raw), ("buy", "hit", None), qin)                     # the amended inverse law
            fwd = self.ev.cp_buy_token_out(self._q() - self.V0, self.V0, self.BASE, qin)
            miss_bp = (fwd - raw["token_raw"]) * 10_000 / raw["token_raw"]
            self.assertGreater(miss_bp, 1.0, qin)
            self.assertLessEqual(miss_bp, 10_000 / qin + 0.01, qin)                           # the exact-out rounding bound, about 1/qin
            self.assertEqual(self.m.cp_buy_quote_in(self._q() - self.V0, self.V0, self.BASE, raw["token_raw"]), qin)

    def test_real_exact_out_buy_hits_exactly_whatever_v0_is(self):
        rec, tape = self._real("buy_v1_481_wsol.json")
        self.assertEqual(tape["ix_name"], "buy")
        self.assertEqual(self.m.p7_raw_line(tape), "buy")
        for v0 in (self.V0, 17_500_000_000, 17_700_000_000):
            row = self._adapter(rec, v0)
            self.assertEqual(self.m.cp_buy_quote_in(row["quote_reserve"], v0, rec["base_reserve"], rec["token_raw"]), rec["pool_quote_amount"])
            self.assertEqual(self.m.p7_raw_check(tape, rec, row, v0), ("buy", "hit", None), v0)

    def test_exact_in_buy_hits_when_a_base_unit_costs_under_a_lamport(self):
        self.assertLess(self._q() / self.BASE, 1)
        for qin in (1_660, 4_898, 20_000, 1_000_000, 400_000_000, 9_000_000_000):
            raw = self._buy_exact_in(qin)
            self.assertEqual(self._check(raw), ("buy", "hit", None), qin)
            self.assertLessEqual(qin - self.m.cp_buy_quote_in(self._q() - self.V0, self.V0, self.BASE, raw["token_raw"]), 1, qin)
        # real exact-in buys (fee-free BOOST buys, buy_exact_quote_in): the law matches them, though the check excludes the family by name
        for name in ("boost_buy_and_burn.json", "boost_buy_and_burn_oct.json"):
            rec, tape = self._real(name)
            row = self._adapter(rec)
            self.assertTrue(self.m.p7_raw_hit("buy", rec, row["quote_reserve"], self.V0), name)
            self.assertEqual(self.m.p7_raw_check(tape, rec, row, self.V0), (None, "excluded", "buy_exact_quote_in"), name)

    def test_inverse_law_replaces_the_forward_law_it_is_not_an_alternative(self):
        """An exact-in buy where one base unit costs far more than a lamport: the forward law hits it exactly, the inverse law misses by 4.1955 bp.
        Kills an 'either law' implementation (inverse hit OR forward hit), guarded or not."""
        q, b, qin = 75_000_000_000, 1_000_000, 100_000_000
        q_mapped = q - self.V0
        self.assertGreater(q // b, 1)                                                        # a base unit costs about 75,000 lamports
        t = b * qin // (q + qin)
        self.assertEqual(t, 1331)
        raw = dict(self._base("buy"), quote_reserve=q_mapped + self.PENDING, base_reserve=b, token_raw=t, pool_quote_amount=qin,
                   sol_lamports=qin, ix_name="buy")
        row = self._adapter(raw)
        self.assertEqual(row["quote_reserve"], q_mapped)
        self.assertTrue(self.ev.p7_raw_hit("buy", raw, q_mapped, self.V0))                   # the forward law hits
        self.assertEqual(self.ev.p7_raw_check(self._tape(raw), raw, row, self.V0), ("buy", "hit", None))
        self.assertEqual(self.m.cp_buy_quote_in(q_mapped, self.V0, b, t), 99_958_045)        # 41,955 lamports short: 4.1955 bp of actual
        self.assertFalse(self.m.p7_raw_hit("buy", raw, q_mapped, self.V0))                   # the inverse law alone decides
        self.assertEqual(self.m.p7_raw_check(self._tape(raw), raw, row, self.V0), ("buy", "miss", None))

    def test_inverse_law_is_an_exact_integer_ceil(self):
        """Integer ceil, not float: math.ceil(q * t / (b - t)) gives 2**59 here."""
        self.assertEqual(self.m.cp_buy_quote_in(2 ** 60 + 1, 0, 3, 1), 2 ** 59 + 1)
        self.assertEqual(self.m.cp_buy_quote_in(20, 0, 7, 2), 8)                             # divisible: no +1
        self.assertEqual(self.m.cp_buy_quote_in(21, 0, 7, 2), 9)                             # not divisible: rounds up

    def test_one_bp_tolerance_is_of_actual(self):
        """base_reserve 2, token_raw 1, v0 0: the model is q_mapped. A gap of 100,000 is 1 bp of 1e9 but more than 1 bp of 999,900,000 (99,990)."""
        raw = {"base_reserve": 2, "token_raw": 1}
        self.assertTrue(self.m.p7_raw_hit("buy", dict(raw, pool_quote_amount=1_000_000_000), 999_900_000, 0))   # gap = 1 bp of actual
        self.assertFalse(self.m.p7_raw_hit("buy", dict(raw, pool_quote_amount=999_900_000), 1_000_000_000, 0))  # gap > 1 bp of actual

    def test_a_buy_with_5_bp_of_fee_inside_pool_quote_amount_misses(self):
        for ix in ("buy", "buy_v2"):
            raw = self._buy(ix_name=ix)
            law = raw["pool_quote_amount"]
            self.assertGreater(law, 100_000_000)
            for bp, want in ((0, "hit"), (0.9, "hit"), (-0.9, "hit"), (2, "miss"), (5, "miss")):
                moved = dict(raw, pool_quote_amount=law + int(round(law * bp / 10_000)))
                self.assertEqual(self._check(moved), ("buy", want, None), (ix, bp))
            # on a dust buy 5 bp is under 2 lamports, which the unchanged unit tolerance allows (the allowance sells have)
            dust = self._dust_exact_out(1_660)
            self.assertEqual(self._check(dict(dust, pool_quote_amount=1_660 + 2))[1], "hit")
            self.assertEqual(self._check(dict(dust, pool_quote_amount=1_660 + 3))[1], "miss")

    def test_gross_vault_column_and_lp_sized_offset_still_fail(self):
        for raw in (self._buy(), self._dust_exact_out(1_660), self._buy_exact_in(400_000_000)):
            self.assertEqual(self._check(raw), ("buy", "hit", None))
            gross = self._adapter(raw, gross=True)
            self.assertEqual(self._check(raw, adapter=gross), ("buy", "miss", None))
            model = self.m.cp_buy_quote_in(gross["quote_reserve"], self.V0, self.BASE, raw["token_raw"])
            self.assertGreater(abs(model - raw["pool_quote_amount"]) * 10_000 / raw["pool_quote_amount"], 200)     # 2-4%, not marginal
            stale = self._adapter(raw, quote_reserve=self._adapter(raw)["quote_reserve"] - 1_000_000_000)        # a vault chained across an LP deposit
            self.assertEqual(self._check(raw, adapter=stale)[1], "miss")
        # with no pending fees the gross vault IS the effective quote, so the September column is right there
        self.assertEqual(self._check(self._buy(pending=0), adapter="gross")[1], "hit")
        # a whole sample on the gross column fails line 1; the pinned adapter passes
        for adapter, want in (("correct", True), ("gross", False)):
            pairs = []
            for i in range(100):
                for raw in (self._sell(i), self._buy(i, ix_name="buy" if i % 2 else "buy_v2")):
                    pairs.append((self._tape(raw), self._check(raw, adapter=adapter)))
            t = self.m.p7_raw_tally(pairs)
            self.assertEqual((t["sell_n"], t["buy_n"]), (100, 100))
            self.assertEqual(self.m.p7_raw_pass(t), want, adapter)
        # the real exact-out buy: an adapter that writes the gross vault is right only if V0 happens to equal V(t)
        rec, tape = self._real("buy_v1_481_wsol.json")
        self.assertEqual(self.m.p7_raw_check(tape, rec, self._adapter(rec, gross=True), 16_000_000_000)[1], "miss")

    def test_base_reserve_at_or_below_token_raw_is_a_miss(self):
        for t in (self.BASE, self.BASE + 1, self.BASE * 2):
            raw = dict(self._buy(), token_raw=t)
            self.assertIsNone(self.m.cp_buy_quote_in(self._q() - self.V0, self.V0, self.BASE, t))
            for qin in (0, 1, raw["pool_quote_amount"], 10 ** 30):
                self.assertEqual(self._check(dict(raw, pool_quote_amount=qin)), ("buy", "miss", None), (t, qin))
        self.assertIsNotNone(self.m.cp_buy_quote_in(self._q() - self.V0, self.V0, self.BASE, self.BASE - 1))

    # -- comparability and the check ----------------------------------------------------------------------------------------------------------
    def test_multi_hop_and_unknown_names_are_excluded_before_fetch_and_counted_per_name(self):
        names = ["multi_hop_swap"] * 3 + ["something_new"] * 2 + ["Buy", "buy ", "buy_v3", "sell"]
        for name in set(names):
            raw = self._buy(ix_name=name)
            tape = self._tape(raw)
            self.assertEqual(self.m.p7_raw_exclusion(tape), "ix_not_listed", name)
            self.assertIsNone(self.m.p7_raw_line(tape), name)
            # decided from the tape row alone: no raw record, no adapter row, even a failed fetch
            self.assertEqual(self.m.p7_raw_check(tape, None, None, self.V0, fetch_failed=True), (None, "excluded", "ix_not_listed"), name)
        # the pinned module counted them comparable (blacklist); the amendment does not (whitelist)
        self.assertEqual(self.ev.p7_raw_line(self._tape(self._buy(ix_name="multi_hop_swap"))), "buy")
        # the other causes, unchanged and in order (zero_sol first)
        self.assertEqual(self.m.p7_raw_exclusion(self._tape(self._buy(ix_name="buy_exact_quote_in_v2"))), "buy_exact_quote_in")
        for nameless in ({k: v for k, v in self._tape(self._buy()).items() if k != "ix_name"}, self._tape(self._buy(ix_name=None)),
                         self._tape(self._buy(ix_name=""))):
            self.assertEqual(self.m.p7_raw_exclusion(nameless), "no_ix_name")
        self.assertEqual(self.m.p7_raw_exclusion(dict(self._tape(self._buy(ix_name="multi_hop_swap")), zero_sol=True)), "zero_sol")
        self.assertEqual(self.m.p7_raw_exclusion({"side": "transfer"}), "not_buy_or_sell")
        self.assertIsNone(self.m.p7_raw_exclusion({"side": "sell"}))                         # sells carry no ix_name and need none
        for ok in self.m.P7_BUY_IX_WHITELIST:
            self.assertIsNone(self.m.p7_raw_exclusion(self._tape(self._buy(ix_name=ok))), ok)
        # tally: per cause and, within ix_not_listed, per name; denominators unchanged by them
        base = [(self._tape(r), self._check(r)) for r in [self._sell(i) for i in range(100)] + [self._buy(i) for i in range(100)]]
        extra = [(self._tape(r), self._check(r)) for r in [self._buy(i, ix_name=n) for i, n in enumerate(names)]]
        extra += [(self._tape(r), self._check(r)) for r in (self._buy(ix_name="buy_exact_quote_in"), self._buy(ix_name="buy_exact_quote_in_v2"),
                                                             self._buy(ix_name="buy_exact_quote_in_v2"), self._buy(ix_name=None))]
        a, b = self.m.p7_raw_tally(base), self.m.p7_raw_tally(base + extra)
        for k in ("sell_n", "sell_ok", "buy_n", "buy_ok", "buy_by_ix", "unresolved"):
            self.assertEqual(a[k], b[k], k)
        self.assertEqual(b["excluded"], len(names) + 4)
        self.assertEqual(b["excluded_by"], {"zero_sol": 0, "not_buy_or_sell": 0, "buy_exact_quote_in": 3, "no_ix_name": 1, "ix_not_listed": len(names)})
        self.assertEqual(b["ix_not_listed_by"], {"multi_hop_swap": 3, "something_new": 2, "Buy": 1, "buy ": 1, "buy_v3": 1, "sell": 1})
        self.assertEqual(b["buy_exact_quote_in_by"], {"buy_exact_quote_in": 1, "buy_exact_quote_in_v2": 2})
        # the pinned tally cannot count the new cause: hence this module's own
        with self.assertRaises(KeyError):
            self.ev.p7_raw_tally([r for _, r in extra])

    def test_unresolved_reasons_and_order_match_the_pinned_check(self):
        raw = self._buy()
        tape = self._tape(raw)
        ad = self._adapter(raw)
        cases = {
            "fetch_failed": (tape, None, ad, True),
            "no_record": (tape, None, ad, False),
            "no_adapter_row": (tape, raw, None, False),
            "slot_mismatch": (tape, dict(raw, slot=raw["slot"] + 1), ad, False),
            "identity_mismatch": (tape, dict(raw, base_reserve=raw["base_reserve"] + 1), ad, False),
            "field_missing": (dict(tape, virtual_quote_reserves=None), {k: v for k, v in raw.items() if k != "virtual_quote_reserves"}, ad, False),
        }
        self.assertEqual(set(cases), set(self.m.P7_RAW_REASONS))
        more = [(tape, dict(raw, event_index=raw["event_index"] + 1), ad, False), (tape, dict(raw, signature="other"), ad, False),
                (tape, raw, dict(ad, pool="OTHER"), False), (tape, raw, dict(ad, token_raw=raw["token_raw"] + 1), False),
                (tape, dict(raw, ix_name="buy_v2"), ad, False), (tape, raw, dict(ad, quote_reserve=None), False),
                (tape, None, None, True)]                                                    # fetch_failed is tested before no_record
        for reason, (t, r, a, ff) in cases.items():
            self.assertEqual(self.m.p7_raw_check(t, r, a, self.V0, fetch_failed=ff), ("buy", "unresolved", reason), reason)
        for t, r, a, ff in list(cases.values()) + more:
            self.assertEqual(self.m.p7_raw_check(t, r, a, self.V0, fetch_failed=ff), self.ev.p7_raw_check(t, r, a, self.V0, fetch_failed=ff))
        # unresolved = miss: it stays in the buy denominator and in its name's
        pairs = [(c[0], self.m.p7_raw_check(c[0], c[1], c[2], self.V0, fetch_failed=c[3])) for c in cases.values()]
        t = self.m.p7_raw_tally(pairs)
        self.assertEqual((t["buy_n"], t["buy_ok"], t["buy_by_ix"]["buy"]), (6, 0, {"n": 6, "ok": 0}))
        self.assertEqual(t["unresolved"], {r: 1 for r in self.m.P7_RAW_REASONS})
        for bad in (None, "17585000000", 1.5, True):
            with self.assertRaises(ValueError):
                self.m.p7_raw_check(tape, raw, ad, bad)

    def test_sells_are_unchanged(self):
        for i in range(50):
            raw = self._sell(i)
            for moved in (raw, dict(raw, pool_quote_amount=raw["pool_quote_amount"] * 10_002 // 10_000), dict(raw, token_raw=1_000_000)):
                for adapter in ("correct", "gross"):
                    self.assertEqual(self._check(moved, adapter=adapter), self._check(moved, adapter=adapter, mod=self.ev))
        rec, tape = self._real("sell_v2_kept.json")
        self.assertEqual(self.m.p7_raw_check(tape, rec, self._adapter(rec), self.V0), ("sell", "hit", None))
        self.assertEqual(self.m.p7_raw_check(dict(tape, zero_sol=True), rec, None, self.V0), (None, "excluded", "zero_sol"))
        with self.assertRaises(ValueError):
            self.m.p7_raw_hit("transfer", rec, 0, 0)

    # -- the sample -----------------------------------------------------------------------------------------------------------------------------
    @staticmethod
    def _frame_rows(n, buy_every, pool="P0"):
        """n tape rows with unique keys; every `buy_every`-th is a buy whose name cycles through comparable and not-comparable kinds."""
        kinds = ({"ix_name": "buy"}, {"ix_name": "multi_hop_swap"}, {"ix_name": "buy_v2"}, {"ix_name": "buy_exact_quote_in"},
                 {"ix_name": "something_new"}, {"ix_name": "buy", "zero_sol": True}, {}, {"ix_name": "buy_exact_quote_in_v2"})
        rows = []
        for i in range(n):
            r = dict({"side": "buy"}, **kinds[(i // buy_every) % len(kinds)]) if i % buy_every == 0 else {"side": "sell"}
            r.update(pool=pool, slot=5000 + i, tx_index=i % 9, event_index=i % 3, sol_lamports=1000 + i)
            rows.append(r)
        return rows

    def test_top_up_uses_only_whitelisted_buys(self):
        rows = self._frame_rows(100_000, 211)
        d = self.m.p7_raw_draw(rows, {"P0": 1})
        old = self.ev.p7_raw_draw(rows, {"P0": 1})
        self.assertEqual((d["frame_n"], d["main"]), (old["frame_n"], old["main"]))           # frame and main draw are unchanged
        self.assertEqual(d, self.m.p7_raw_draw(rows, {"P0": 1}))                            # deterministic
        have = sum(1 for r in d["main"] if self.m.p7_raw_line(r) == "buy")
        self.assertLess(have, 100)
        self.assertEqual(len(d["topup"]), 100 - have)
        for r in d["topup"]:
            self.assertIn(r["ix_name"], self.m.P7_BUY_IX_WHITELIST)
            self.assertFalse(r.get("zero_sol"))
        self.assertEqual({r["ix_name"] for r in d["topup"]}, {"buy", "buy_v2"})
        self.assertIn("multi_hop_swap", {r.get("ix_name") for r in old["topup"]})           # the pinned top-up would have drawn them
        self.assertEqual(sum(1 for r in d["main"] + d["topup"] if self.m.p7_raw_line(r) == "buy"), 100)
        # the stride over the amended candidates, offset half a stride
        main_keys = {(r["slot"], r["tx_index"], r["event_index"]) for r in d["main"]}
        cand = [r for r in self.m.p7_raw_frame(rows, {"P0": 1}) if self.m.p7_raw_line(r) == "buy" and (r["slot"], r["tx_index"], r["event_index"]) not in main_keys]
        need = 100 - have
        self.assertEqual(d["topup"], [cand[((2 * k + 1) * len(cand)) // (2 * need)] for k in range(need)])
        # fewer than 100 comparable buys in the hours: all of them, and none of the rest
        few = self._frame_rows(20_000, 401)
        got = [r for r in (lambda x: x["main"] + x["topup"])(self.m.p7_raw_draw(few, {"P0": 1})) if self.m.p7_raw_line(r) == "buy"]
        self.assertEqual(sorted(r["slot"] for r in got), sorted(r["slot"] for r in self.m.p7_raw_frame(few, {"P0": 1}) if self.m.p7_raw_line(r) == "buy"))
        self.assertLess(len(got), 100)
        self.assertEqual(self.m.p7_raw_draw(rows, {"P0": None}), {"frame_n": 0, "main": [], "topup": []})

    def test_tally_keys(self):
        pairs = [(self._tape(r), self._check(r)) for r in (self._sell(0), self._sell(1), self._buy(0), self._buy(1, ix_name="buy_v2"))]
        bad_v2 = self._buy(2, ix_name="buy_v2")
        bad_v2["pool_quote_amount"] = bad_v2["pool_quote_amount"] * 10_005 // 10_000
        pairs.append((self._tape(bad_v2), self._check(bad_v2)))
        miss = self._tape(self._buy(3, ix_name="multi_hop_swap"))
        pairs.append((miss, self.m.p7_raw_check(miss, None, None, self.V0)))
        t = self.m.p7_raw_tally(pairs)
        self.assertEqual(set(t), {"sell_n", "sell_ok", "buy_n", "buy_ok", "excluded", "excluded_by", "ix_not_listed_by", "buy_exact_quote_in_by",
                                  "buy_by_ix", "unresolved"})
        self.assertEqual(tuple(t["excluded_by"]), self.m.P7_RAW_EXCLUSIONS)
        self.assertEqual(tuple(t["unresolved"]), self.m.P7_RAW_REASONS)
        self.assertEqual((t["sell_n"], t["sell_ok"], t["buy_n"], t["buy_ok"], t["excluded"]), (2, 2, 3, 2, 1))
        self.assertEqual(t["buy_by_ix"], {"buy": {"n": 1, "ok": 1}, "buy_v2": {"n": 2, "ok": 1}})
        self.assertEqual(t["ix_not_listed_by"], {"multi_hop_swap": 1})
        self.assertEqual(t["buy_exact_quote_in_by"], {})
        self.assertFalse(self.m.p7_raw_pass(t))                                              # 2 of 3 buys
        empty = self.m.p7_raw_tally([])
        self.assertEqual((empty["sell_n"], empty["buy_n"], empty["excluded"]), (0, 0, 0))
        self.assertFalse(self.m.p7_raw_pass(empty))                                          # a side with none fails

    def test_module_does_no_io_in_its_functions(self):
        """The module reads event_v_map.py once at import (the sha check); its functions open no file and run no process."""
        import builtins

        calls = []
        real_open, real_run = builtins.open, subprocess.run
        builtins.open = lambda *a, **k: calls.append(a) or real_open(*a, **k)
        subprocess.run = lambda *a, **k: calls.append(a) or real_run(*a, **k)
        try:
            raw = self._buy()
            pairs = [(self._tape(raw), self.m.p7_raw_check(self._tape(raw), raw, self._adapter(raw), self.V0))]
            self.m.p7_raw_tally(pairs)
            self.m.p7_raw_draw(self._frame_rows(5_000, 7), {"P0": 1})
        finally:
            builtins.open, subprocess.run = real_open, real_run
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
