"""tools/h5_lane_oc_checks.py. Synthetic fixtures only: nothing here reads /data/mal, /var/lib/mal, a shadow dir, a ledger or a real row."""
from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from tools import h5_lane_oc_checks as C
from tools import test_h5_lane_oc as T
from tools.test_h5_executor_shadow import shadow_trigger

BUDGET = 17_585_000_000
FAKE_MINT = "MINTSENTINEL111111111111111111111111111111"
FAKE_POOL = "POOLSENTINEL111111111111111111111111111111"


def wr(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(r, separators=(",", ":")) + "\n" for r in rows))


def trig(i: int, slot: int = 1000, settled=0, spent: float = 1.0, rem="auto", **kw) -> dict:
    rem = (BUDGET - round(spent * 1e9)) / 1e9 if rem == "auto" else rem
    return {"type": "trigger", "variant": "pv", "mint": f"{FAKE_MINT}{i}", "pool": f"{FAKE_POOL}{i}", "slot": slot, "s0": slot - 50,
            "boost_spent_sol": spent, "boost_vault_remaining_sol": rem, "base_breaks_unresolved_settled": settled, **kw}


def sl(i: int, slot: int) -> dict:
    return {"type": "boost_slice", "pool": f"{FAKE_POOL}{i}", "mint": f"{FAKE_MINT}{i}", "slot": slot, "lamports": 5, "price": 9.9}


def rc(i: int, amount: int, slot: int = 1010, key: str = "oc_boost_slot", ok: bool = True, **kw) -> dict:
    r = {"kind": "oc_recheck", "mint": f"{FAKE_MINT}{i}", "ok": ok, "reason": "ok" if ok else "oc_boost_exhausted", "oc_boost_remaining_lamports": amount,
         "oc_l6_a": "agree", "oc_l6_b": "ok", "oc_l6_b_diff_lamports": 0, key: slot}
    return {**r, **kw}


class Base(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.tmp = Path(self._td.name)
        self.shadow = self.tmp / "shadow"
        self.shadow.mkdir()
        self.ledger = self.tmp / "dry.jsonl"

    def run_l2l3(self, ledger_rows, shadow_rows):
        wr(self.ledger, ledger_rows)
        wr(self.shadow / "h5-shadow-2026-11-04T00.jsonl", shadow_rows)
        return C.l2l3(str(self.ledger), str(self.shadow))


class L2Tests(Base):
    def cleans(self, n: int, nonzero_at=None):
        trs, led = [], []
        for i in range(n):
            trs.append(trig(i, rem=9.0))
            led.append(rc(i, 9_000_000_000 + (7 if i == nonzero_at else 0)))
        return led, trs

    def test_pass_needs_five_clean_all_zero(self):
        led, trs = self.cleans(5)
        r = self.run_l2l3(led, trs)
        self.assertEqual((r["n_cmp"], r["n_strict"], r["n_zero"], r["l2"]), (5, 5, 5, "PASS"))

    def test_four_clean_is_not_yet(self):
        led, trs = self.cleans(4)
        self.assertEqual(self.run_l2l3(led, trs)["l2"], "NOT_YET")

    def test_any_clean_nonzero_fails_even_below_five(self):
        led, trs = self.cleans(3, nonzero_at=1)
        r = self.run_l2l3(led, trs)
        self.assertEqual((r["l2"], r["nz"]), ("FAIL", [7]))
        led, trs = self.cleans(6, nonzero_at=5)
        self.assertEqual(self.run_l2l3(led, trs)["l2"], "FAIL")

    def test_strict_boundary_is_closed_at_both_ends(self):
        # trigger slot 1000, read slot 1010: a slice anywhere in [1000, 1010] makes the read not strict-clean
        cases = {"slice_after_trigger_inside": ([sl(0, 1005)], False), "slice_at_read_slot": ([sl(0, 1010)], False),
                 "slice_one_past_read": ([sl(0, 1011)], True), "slice_at_trigger_slot": ([sl(0, 1000)], False),
                 "slice_before_trigger": ([sl(0, 990)], True), "slice_at_trigger_plus_one": ([sl(0, 1001)], False), "no_slices": ([], True),
                 "other_pool_slice": ([sl(1, 1005)], True)}
        for name, (slices, want) in cases.items():
            r = self.run_l2l3([rc(0, 9_000_000_000)], [trig(0, rem=9.0), *slices])
            self.assertEqual(r["n_strict"], int(want), name)
            self.assertEqual(r["n_cmp"], 1, name)

    def test_not_clean_comparisons_do_not_count_toward_the_verdict(self):
        led, trs = self.cleans(5)
        led[0]["oc_boost_remaining_lamports"] += 123  # a disagreement ...
        r = self.run_l2l3(led, trs + [sl(0, 1005)])  # ... but a slice sits inside [trigger, read]: not clean
        self.assertEqual((r["n_cmp"], r["n_strict"], r["l2"]), (5, 4, "NOT_YET"))

    def test_loose_proxy_is_report_only_and_never_fails(self):
        # a slice at the trigger slot: loose-clean (it is the "last event"), not strict-clean; its nonzero diff is counted but is no FAIL
        r = self.run_l2l3([rc(0, 9_000_000_123)], [trig(0, rem=9.0), sl(0, 1000)])
        self.assertEqual((r["loose_n"], r["loose_not_strict_nz"], r["n_strict"], r["l2"]), (1, 1, 0, "NOT_YET"))

    def test_oc_slot_only_rows_never_count_toward_pass_or_fail(self):
        led = [rc(i, 9_000_000_000 + (5 if i == 0 else 0), key="oc_slot") for i in range(6)]
        r = self.run_l2l3(led, [trig(i, rem=9.0) for i in range(6)])
        self.assertEqual((r["n_fallback"], r["n_strict"], r["fb_n"], len(r["fb_nz"]), r["l2"]), (6, 0, 6, 1, "NOT_YET"))
        led += [rc(10 + i, 9_000_000_000) for i in range(5)]
        r = self.run_l2l3(led, [trig(i, rem=9.0) for i in range(6)] + [trig(10 + i, rem=9.0) for i in range(5)])
        self.assertEqual((r["n_strict"], r["l2"]), (5, "PASS"))

    def test_refused_rows_that_reached_the_vault_read_are_compared_and_others_are_not(self):
        led = [rc(0, 9_000_000_000, ok=False), {"kind": "oc_recheck", "mint": f"{FAKE_MINT}1", "ok": False, "reason": "oc_q_above_40"}]
        r = self.run_l2l3(led, [trig(0, rem=9.0), trig(1, rem=9.0)])
        self.assertEqual((len(r["led"]["reads"]), r["n_cmp"]), (1, 1))
        self.assertEqual(dict(r["led"]["reasons"]), {"oc_boost_exhausted": 1, "oc_q_above_40": 1})

    def test_unjoined_rows_are_counted(self):
        r = self.run_l2l3([rc(0, 1), rc(1, 1)], [trig(0, rem=None)])  # a null shadow value, and no trigger at all
        self.assertEqual((r["n_cmp"], r["unjoined"]), (0, 2))

    def test_tallies_and_signed_diff_stats(self):
        led = [rc(0, 9_000_000_000, oc_l6_b_diff_lamports=-5), rc(1, 9_000_000_000, oc_l6_b_diff_lamports=3, oc_l6_a="not_clean", oc_l6_b="stale_high"),
               rc(2, 9_000_000_000, oc_l6_b_diff_lamports=9), {"kind": "oc_l6", "which": "a"}, {"kind": "oc_l6", "which": "b"}, {"kind": "oc_l6", "which": "a"}]
        r = self.run_l2l3(led, [])
        self.assertEqual(dict(r["led"]["which"]), {"a": 2, "b": 1})
        self.assertEqual(dict(r["led"]["l6a"]), {"agree": 2, "not_clean": 1})
        self.assertEqual(C._stats(r["led"]["b_diffs"]), "n=3 min=-5 median=3 max=9")

    def test_a_ledger_dir_is_read_and_exactly_one_shadow_dir_is_allowed(self):
        d = self.tmp / "ldir"
        d.mkdir()
        wr(d / "a.jsonl", [rc(0, 9_000_000_000)])
        wr(d / "b.jsonl", [rc(1, 9_000_000_000)])
        wr(self.shadow / "h5-shadow-1.jsonl", [trig(0, rem=9.0), trig(1, rem=9.0)])
        r = C.l2l3(str(d), str(self.shadow))
        self.assertEqual((r["n_cmp"], r["n_strict"]), (2, 2))
        s2 = self.tmp / "shadow2"
        s2.mkdir()
        with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(C.main(["l2l3", "--dry-ledger", str(d), "--shadow-dir", str(self.shadow), "--shadow-dir", str(s2)]), 2)


class JoinTests(Base):
    def test_duplicate_identical_trigger_is_one_trigger(self):
        r = self.run_l2l3([rc(0, 9_000_000_000)], [trig(0, rem=9.0), trig(0, rem=9.0)])
        self.assertEqual((r["n_cmp"], r["nz"], r["l2"]), (1, [], "NOT_YET"))

    def test_read_joins_the_latest_trigger_at_or_before_its_slot(self):
        # same mint and pool, two triggers: slot 1000 (rem 8.0) and slot 1100 (rem 9.0); each read matches the trigger it answers
        trs = [trig(0, slot=1000, rem=8.0), trig(0, slot=1100, rem=9.0)]
        led = [rc(0, 8_000_000_000, slot=1010), rc(0, 9_000_000_000, slot=1110)]
        r = self.run_l2l3(led, trs)
        self.assertEqual((r["n_cmp"], r["n_strict"], r["nz"], r["n_distinct"], r["l2"]), (2, 2, [], 2, "NOT_YET"))

    def test_same_pool_and_slot_with_different_vault_values_is_counted(self):
        r = self.run_l2l3([rc(0, 9_000_000_000)], [trig(0, rem=9.0), trig(0, rem=8.0)])
        self.assertEqual((r["n_conflict"], r["n_cmp"], r["nz"]), (1, 1, []))  # the first is kept

    def test_five_rows_for_one_mint_do_not_pass(self):
        r = self.run_l2l3([rc(0, 9_000_000_000) for _ in range(5)], [trig(0, rem=9.0)])
        self.assertEqual((r["n_cmp"], r["n_strict"], r["n_distinct"], r["n_distinct_strict"], r["l2"]), (5, 5, 1, 1, "NOT_YET"))

    def test_a_repeat_row_that_disagrees_still_fails(self):
        r = self.run_l2l3([rc(0, 9_000_000_000), rc(0, 9_000_000_001)], [trig(0, rem=9.0)])
        self.assertEqual(r["l2"], "FAIL")

    def test_read_below_the_trigger_slot_is_not_clean_under_either_rule(self):
        r = self.run_l2l3([rc(0, 9_000_000_000, slot=990)], [trig(0, rem=9.0), sl(0, 995)])
        self.assertEqual((r["n_cmp"], r["n_strict"], r["loose_n"], r["n_before"]), (1, 0, 0, 1))
        r = self.run_l2l3([rc(0, 9_000_000_000, slot=1000)], [trig(0, rem=9.0)])  # read at the trigger slot is fine
        self.assertEqual((r["n_strict"], r["n_before"]), (1, 0))


class MalformedTests(Base):
    def test_unhashable_or_non_string_keys_are_counted_not_fatal(self):
        led = [rc(0, 9_000_000_000, mint=["x"]), rc(1, 9_000_000_000, mint={"a": 1}), rc(2, 9_000_000_000)]
        trs = [trig(2, rem=9.0), trig(3, mint=["y"]), trig(4, pool={"p": 1}), {"type": "boost_slice", "pool": ["z"], "slot": 1}]
        r = self.run_l2l3(led, trs)
        self.assertEqual((r["n_cmp"], r["unjoined"], r["bad_keys"]), (1, 2, 4))

    def test_unparsable_lines_are_counted_and_printed(self):
        wr(self.ledger, [rc(0, 1)])
        with self.ledger.open("a") as fh:
            fh.write('{"kind":"oc_recheck" broken\n[{"kind":"oc_recheck"}]\n')
        wr(self.shadow / "h5-shadow-a.jsonl", [trig(0)])
        with (self.shadow / "h5-shadow-a.jsonl").open("a") as fh:
            fh.write('{"type":"trigger" not json\n')
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(C.main(["l2l3", "--dry-ledger", str(self.ledger), "--shadow-dir", str(self.shadow)]), 0)
        self.assertIn("ledger {'unparsable_or_non_object': 2}, shadow {'unparsable_or_non_object': 1}", buf.getvalue())


class PrefilterTests(Base):
    def test_only_exact_token_lines_are_ever_parsed(self):
        outcome = '{"type":"outcome","variant":"pv","trigger_slot":1000,"pnl_sol":1.5}'
        pool = '{"type":"pool","pool":"P","boost_slices":3,"boost_vault_remaining_sol":9.0}'
        tagged = '{"kind":"decision","mint":"M","oc_l6_a":"agree","oc_boost_remaining_lamports":1,"gate_path":"onchain_recheck"}'
        wr(self.ledger, [])
        self.ledger.write_text(tagged + "\n" + json.dumps(rc(0, 9_000_000_000), separators=(",", ":")) + "\n")
        (self.shadow / "h5-shadow-a.jsonl").write_text("\n".join([outcome, pool, json.dumps(trig(0, rem=9.0), separators=(",", ":"))]) + "\n")
        seen: list[str] = []
        real = C.json.loads
        C.json.loads = lambda line, *a, **k: (seen.append(line), real(line, *a, **k))[1]
        try:
            r = C.l2l3(str(self.ledger), str(self.shadow))
        finally:
            C.json.loads = real
        self.assertEqual(r["n_cmp"], 1)
        self.assertTrue(seen)
        for line in seen:
            self.assertTrue('"type":"trigger"' in line or '"type":"boost_slice"' in line or '"kind":"oc_recheck"' in line or '"kind":"oc_l6"' in line, line)
            self.assertNotIn("pnl_sol", line)
            self.assertNotIn("gate_path", line)
            self.assertNotIn("boost_slices", line)


class L3Tests(Base):
    def l3(self, *rems_vs_spent):
        trs = [trig(i, spent=sp, rem=rem) for i, (sp, rem) in enumerate(rems_vs_spent)]
        return self.run_l2l3([], trs)

    def test_exact_identity_is_zero(self):
        r = self.l3((2.0, 15.585), (3.0, 14.585))
        self.assertEqual((r["l3"], r["l3_verdict"]), ([0, 0], "OK"))

    def test_threshold_is_one_floor_inclusive(self):
        below = self.l3((2.0, (BUDGET - 2_000_000_000 - 17_584_999) / 1e9))
        self.assertEqual((max(below["l3"]), below["l3_verdict"]), (17_584_999, "OK"))
        at = self.l3((2.0, (BUDGET - 2_000_000_000 - 17_585_000) / 1e9))
        self.assertEqual((max(at["l3"]), at["l3_verdict"]), (17_585_000, "FAIL"))

    def test_only_settled_0_pv_triggers_with_numbers_count(self):
        trs = [trig(0, spent=2.0, rem=15.585), trig(1, settled=1, rem=1.0), trig(2, settled=True, rem=1.0), trig(3, settled=0.0, rem=1.0), trig(4, variant="fv", rem=1.0),
               trig(5, rem=None), trig(6, suppressed=True, rem=1.0)]
        r = self.run_l2l3([], trs)
        self.assertEqual((len(r["l3"]), len(r["l3_pv"]), r["l3_unusable"]), (2, 1, 1))  # pv and fv count; pv-only is report-only

    def test_median_and_max(self):
        r = self.l3(*[(1.0, (BUDGET - 1_000_000_000 - d) / 1e9) for d in (0, 10, 30)])
        self.assertEqual((len(r["l3"]), sorted(r["l3"])), (3, [0, 10, 30]))

    def test_no_triggers_is_not_yet(self):
        self.assertEqual(self.run_l2l3([], [])["l3_verdict"], "NOT_YET")


class OutputTests(Base):
    def test_no_mint_pool_or_price_is_printed(self):
        led = [rc(i, 9_000_000_000, oc_q_lamports=123456789, fill_price=0.5) for i in range(6)]
        trs = [trig(i, rem=9.0, q_trigger_sol=33.3, pnl=7) for i in range(6)] + [sl(0, 1005)]
        wr(self.ledger, led)
        wr(self.shadow / "h5-shadow-x.jsonl", trs)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(C.main(["l2l3", "--dry-ledger", str(self.ledger), "--shadow-dir", str(self.shadow)]), 0)
        out = buf.getvalue()
        for bad in ("SENTINEL", "123456789", "33.3", "pnl", "price", "q_trigger", "mint", "fill"):
            self.assertNotIn(bad, out, bad)
        self.assertIn("L2 VERDICT", out)
        self.assertIn("L3 VERDICT", out)

    def test_exit_codes(self):
        self.assertEqual(C.main(["l2l3", "--dry-ledger", str(self.tmp / "nope.jsonl"), "--shadow-dir", str(self.shadow)]), 2)
        self.assertEqual(C.main(["l2l3", "--dry-ledger", str(self.ledger), "--shadow-dir", str(self.tmp / "nodir")]), 2)
        self.assertEqual(C.main(["l5"]), 2)
        self.assertEqual(C.main([]), 2)

    def test_no_writes_except_stdout(self):
        wr(self.ledger, [rc(0, 1)])
        before = sorted(p.name for p in self.tmp.rglob("*"))
        with contextlib.redirect_stdout(io.StringIO()):
            C.main(["l2l3", "--dry-ledger", str(self.ledger), "--shadow-dir", str(self.shadow)])
        self.assertEqual(before, sorted(p.name for p in self.tmp.rglob("*")))


class L5Tests(Base):
    SYNTH_MD5 = "26735c24214f598e1477d46aa63d104c"

    def test_the_harness_reproduces_the_pinned_synthetic_md5_on_the_same_17_triggers(self):
        variants = T.Settled0EquivalenceTests.VARIANTS
        self.assertEqual(len(variants), 17)

        def maker(v):
            def make(e):
                kw = dict(v)
                if kw.get("t_detect_ms") == -4_000:
                    kw["t_detect_ms"] = e.clock() - 4_000
                return shadow_trigger(e.clock, **{"base_breaks_unresolved_settled": 0, **kw})
            return make
        res = C.md5_of_rows([maker(v) for v in variants])
        self.assertEqual((res["n"], res["oc"], res["lane2"]), (17, self.SYNTH_MD5, self.SYNTH_MD5))

    def real_rows(self, n: int) -> list[dict]:
        base = shadow_trigger(lambda: 1_000_000)
        out = []
        for i in range(n):
            r = {**base, "type": "trigger", "variant": "pv", "mint": f"{FAKE_MINT}{i}", "pool": f"{FAKE_POOL}{i}", "base_breaks_unresolved_settled": 0,
                 "s0": 5_000 + i, "slot": 5_000 + i + 120, "q_trigger_sol": 30.0 + i % 3, "t_detect_ms": 123 + i}
            out.append(r)
        return out

    def test_equal_over_synthetic_real_shaped_rows_and_only_settled_0_pv_are_fed(self):
        rows = self.real_rows(4)
        other = [{**rows[0], "base_breaks_unresolved_settled": 1}, {**rows[0], "variant": "fv"}, {**rows[0], "suppressed": True}, {"type": "pool"}]
        wr(self.shadow / "h5-shadow-a.jsonl", rows + other)
        res = C.l5([str(self.shadow)])
        self.assertEqual((res["n"], res["per_dir"]), (4, [4]))
        self.assertEqual(res["oc"], res["lane2"])
        self.assertRegex(res["oc"], r"^[0-9a-f]{32}$")

    def test_two_shadow_dirs_and_the_md5_depends_on_the_rule_fields(self):
        rows = self.real_rows(2)
        s2 = self.tmp / "shadow2"
        s2.mkdir()
        wr(self.shadow / "h5-shadow-a.jsonl", rows[:1])
        wr(s2 / "h5-shadow-b.jsonl", rows[1:])
        both = C.l5([str(self.shadow), str(s2)])
        self.assertEqual((both["n"], both["per_dir"]), (2, [1, 1]))
        wr(s2 / "h5-shadow-b.jsonl", [{**rows[1], "gap": True}])
        gapped = C.l5([str(self.shadow), str(s2)])
        self.assertNotEqual(both["oc"], gapped["oc"])  # not vacuous: a rule field moves the md5
        self.assertEqual(gapped["oc"], gapped["lane2"])

    def test_n_zero_is_not_yet_not_equal(self):
        wr(self.shadow / "h5-shadow-a.jsonl", [{**self.real_rows(1)[0], "base_breaks_unresolved_settled": 2}])
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(C.main(["l5", "--shadow-dir", str(self.shadow)]), 0)
        self.assertIn("RESULT: NOT_YET", buf.getvalue())
        self.assertNotIn("RESULT: EQUAL", buf.getvalue())

    def test_disclosures_and_pins_are_printed(self):
        wr(self.shadow / "h5-shadow-a.jsonl", self.real_rows(1))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            C.main(["l5", "--shadow-dir", str(self.shadow)])
        out = buf.getvalue()
        for want in ("FRESH lane", "max_open", "t_detect_ms", "tools/test_h5_executor_shadow.py=", "tools/test_probe_executor.py=", "tools/h5_lane_oc_checks.py="):
            self.assertIn(want, out)

    def test_diff_is_reported_when_the_bots_differ(self):
        rows = self.real_rows(1)
        wr(self.shadow / "h5-shadow-a.jsonl", rows)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(C.main(["l5", "--shadow-dir", str(self.shadow)]), 0)
        out = buf.getvalue()
        self.assertIn("RESULT: EQUAL", out)
        self.assertIn("stake field is NOT a trigger field", out)
        for bad in ("SENTINEL", "pool:", "mint:"):
            self.assertNotIn(bad, out)
        C.print_l5({"n": 1, "oc": "a" * 32, "lane2": "b" * 32, "per_dir": [1], "pins": {}}, buf)
        self.assertIn("RESULT: DIFF", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
