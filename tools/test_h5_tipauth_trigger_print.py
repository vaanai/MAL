"""Tests for tools/h5_tipauth_trigger_print.py (item 8, trigger print on the tip). Synthetic shadow and tip directories only.

  python3 -m unittest tools.test_h5_tipauth_trigger_print
"""
from __future__ import annotations

import gzip
import io
import json
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from tools import h5_tipauth_item4 as I4
from tools import h5_tipauth_trigger_print as P
from tools.test_h5_tipauth_item4 import H, MID, T0, hb, pool as pool_rec, start, trig, write

SINCE = T0
UNTIL = SINCE + 24 * H  # a scheduled instant (+24 h)
SHA = "5db78a663a0617527daacb22f5ce6c1a7e92dfa3"
SLOT = 200
HW_OK = SLOT + 4  # tip high-water minus 4 == trigger slot: decidable


def recording_hook(seen: set):
    class R(dict):
        def get(self, k, d=None):
            seen.add(k)
            return dict.get(self, k, d)

        def __getitem__(self, k):
            seen.add(k)
            return dict.__getitem__(self, k)

        def __contains__(self, k):
            seen.add(k)
            return dict.__contains__(self, k)

        def items(self):
            seen.add("<items>")
            return dict.items(self)

        def keys(self):
            seen.add("<keys>")
            return dict.keys(self)

        def values(self):
            seen.add("<values>")
            return dict.values(self)

        def __iter__(self):
            seen.add("<iter>")
            return dict.__iter__(self)

    return lambda pairs: R(pairs)


def trg(sig="SIGA", pool="P1", ei=3, q=5_000, b=7_000, v=17_000, t=MID, slot=SLOT, **kw):
    """A would-pass pv trigger record carrying the trigger print's own keys (tools/h5_shadow.py:1941,1947)."""
    return trig(t=t, pool=pool, slot=slot, signature=sig, event_index=ei, real_quote_pre=q, base_pre=b, v_print=v, **kw)


def tip_row(sig="SIGA", pool="P1", ei=3, q=5_000, b=7_000, v=17_000, slot=SLOT, **kw):
    r = {"v": 1, "type": "trade", "venue": "pumpswap", "side": "buy", "quote_reserve": q, "base_reserve": b, "pool": pool, "slot": slot, "signature": sig,
         "event_index": ei, "pnl_decoy": 99}
    if v is not None:
        r["virtual_quote_reserves"] = v
    r.update(kw)
    return r


def hbhw(t, hw):
    r = hb(t)
    r["tip_union"]["hw_slot"] = hw
    return r


def hour_name(ms):
    return time.strftime("%Y-%m-%dT%H", time.gmtime(ms // 1000))


class Base(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.sd = Path(self._td.name) / "shadow"
        self.td = Path(self._td.name) / "sealed-tip"
        self.sd.mkdir()
        self.td.mkdir()

    def tip(self, rows, hour_ms=T0, gz=False):
        name = f"trades-{hour_name(hour_ms)}.jsonl"
        data = "".join(json.dumps(r, separators=(",", ":")) + "\n" for r in rows).encode()
        if gz:
            (self.td / (name + ".gz")).write_bytes(gzip.compress(data))
        else:
            (self.td / name).write_bytes(data)

    def clear_tip(self):
        for f in self.td.iterdir():
            f.unlink()

    def run_(self, recs, hw=HW_OK, until=UNTIL, hook=None, sha=SHA):
        recs = [start(T0 + 1000, sha=sha)] + list(recs)
        if hw is not None:
            recs.append(hbhw(T0 + 40 * 60_000, hw))
        write(self.sd, recs)
        return P.analyze(self.sd, self.td, SINCE, until, SHA, object_hook=hook)


class TestClassesAndOutcomes(Base):
    def test_fixture_would_pass(self):
        self.assertTrue(I4.would_pass(trg())[0])
        self.assertFalse(I4.would_pass(trg(ws=0))[0])

    def test_each_class_each_outcome(self):
        trigs, rows = [], []
        specs = {"would_pass": dict(), "ws_pass": dict(ws=0, uval=0), "refused": dict(boost_src="none")}
        for ci, kw in enumerate(specs.values()):
            for oi, outcome in enumerate(("eq", "diff", "absent", "undet")):
                sig, pl = f"S{ci}{oi}", f"P{ci}{oi}"
                trigs.append(trg(sig=sig, pool=pl, slot=SLOT + (100 if outcome == "undet" else 0), **kw))
                if outcome == "eq":
                    rows.append(tip_row(sig=sig, pool=pl))
                elif outcome == "diff":
                    rows.append(tip_row(sig=sig, pool=pl, b=7_001))
        self.tip(rows)
        r = self.run_(trigs)
        for cls in P.CLASSES:
            d = r["classes"][cls]
            self.assertEqual((d["n"], d["present_equal"], d["present_different"], d["absent"], d["undetermined"]), (4, 1, 1, 1, 1), cls)
            self.assertEqual(d["undetermined_reasons"], {"tip_not_far_enough": 1})
        self.assertIs(r["thresholds"]["would_pass_all_present_and_equal"]["met"], False)
        self.assertTrue(r["ws_pass_owner_flag"]["flag"])
        self.assertIs(r["trigger_print_rules_clear"], False)

    def test_quote_difference_and_bool_unequal(self):
        self.tip([tip_row(sig="Q", q=5_001), tip_row(sig="B", pool="P2", b=True)])
        r = self.run_([trg(sig="Q"), trg(sig="B", pool="P2", b=1)])
        self.assertEqual(r["classes"]["would_pass"]["present_different"], 2)

    def test_all_equal(self):
        self.tip([tip_row(sig="A"), tip_row(sig="B", pool="P2", ei=0)])
        r = self.run_([trg(sig="A"), trg(sig="B", pool="P2", ei=0)])
        self.assertIs(r["thresholds"]["would_pass_all_present_and_equal"]["met"], True)
        self.assertIs(r["thresholds"]["would_pass_n_ge_1"]["met"], True)
        self.assertEqual(r["tip_scan"]["signatures_searched"], 2)

    def test_v_is_informational_only(self):
        self.tip([tip_row(sig="A", v=1), tip_row(sig="B", pool="P2", v=None)])
        r = self.run_([trg(sig="A"), trg(sig="B", pool="P2")])
        d = r["classes"]["would_pass"]
        self.assertEqual(d["present_equal"], 2)
        self.assertEqual(d["v_among_present"], {"v_different": 1, "v_tip_key_absent": 1})

    def test_wrong_pool_or_event_index_is_not_a_match(self):
        self.tip([tip_row(sig="A", pool="OTHER"), tip_row(sig="B", pool="P2", ei=4)])
        r = self.run_([trg(sig="A"), trg(sig="B", pool="P2", ei=3)])
        self.assertEqual(r["classes"]["would_pass"]["absent"], 2)
        self.assertIs(r["thresholds"]["would_pass_all_present_and_equal"]["met"], False)

    def test_same_signature_other_event_index_alongside_match(self):
        self.tip([tip_row(sig="A", ei=2, b=1), tip_row(sig="A", ei=3)])
        self.assertEqual(self.run_([trg(sig="A", ei=3)])["classes"]["would_pass"]["present_equal"], 1)

    def test_duplicate_rows_all_must_be_equal(self):
        self.tip([tip_row(sig="A"), tip_row(sig="A", b=1)])
        self.assertEqual(self.run_([trg(sig="A")])["classes"]["would_pass"]["present_different"], 1)

    def test_signature_inside_another_field_is_not_a_match(self):
        other = tip_row(sig="ZZZ", pool="P9")
        other["note"] = '"signature":"SIGA"'  # JSON-escaped inside a string: not the byte pattern
        self.tip([other])
        self.assertEqual(self.run_([trg(sig="SIGA")])["classes"]["would_pass"]["absent"], 1)


class TestUndetermined(Base):
    def test_tip_not_far_enough(self):
        self.tip([tip_row()])
        self.assertEqual(self.run_([trg()], hw=SLOT + 3)["classes"]["would_pass"]["undetermined"], 1)

    def test_exactly_at_margin_is_decided(self):
        self.tip([tip_row()])
        self.assertEqual(self.run_([trg()], hw=SLOT + 4)["classes"]["would_pass"]["present_equal"], 1)

    def test_only_the_triggers_own_high_water_counts(self):
        r = self.run_([trg()], hw=None)  # the record's tip_hw_slot_global is 154, below 204
        self.assertEqual(r["classes"]["would_pass"]["undetermined"], 1)
        self.assertIsNone(r["thresholds"]["would_pass_all_present_and_equal"]["met"])
        self.assertIsNone(r["trigger_print_rules_clear"])

    def test_high_water_from_pool_close_counts(self):
        self.tip([tip_row()])
        pc = pool_rec(t=MID + 5000)
        pc["base_union"]["tip_hw_slot"] = HW_OK
        self.assertEqual(self.run_([trg(), pc], hw=None)["classes"]["would_pass"]["present_equal"], 1)

    def test_sealed_pool_close_high_water_ignored(self):
        pc = pool_rec(t=MID + 5000, sealed=True)
        pc["base_union"]["tip_hw_slot"] = HW_OK
        self.assertEqual(self.run_([trg(), pc], hw=None)["classes"]["would_pass"]["undetermined"], 1)

    def test_bad_trigger_fields(self):
        self.tip([tip_row()])
        bad = [trg(sig=None), trg(sig="B", pool="P2", ei="3"), trg(sig="C", pool="P3", q=None), trg(sig="D", pool="P4", b=True)]
        d = self.run_(bad)["classes"]["would_pass"]
        self.assertEqual((d["n"], d["undetermined"], d["absent"]), (4, 4, 0))
        self.assertEqual(d["undetermined_reasons"], {"bad_trigger_fields": 4})

    def test_compressed_only_hour_is_undetermined_not_absent(self):
        self.tip([tip_row()], gz=True)
        d = self.run_([trg()])["classes"]["would_pass"]
        self.assertEqual((d["absent"], d["undetermined"]), (0, 1))
        self.assertEqual(d["undetermined_reasons"], {"tip_file_compressed_unsupported": 1})

    def test_plain_file_wins_over_compressed_sibling(self):
        self.tip([tip_row()])
        self.tip([tip_row(b=1)], gz=True)
        self.assertEqual(self.run_([trg()])["classes"]["would_pass"]["present_equal"], 1)


class TestHourLookup(Base):
    def _one(self, hour_ms, t):
        self.clear_tip()
        self.tip([tip_row()], hour_ms=hour_ms)
        for f in self.sd.iterdir():
            f.unlink()
        return self.run_([trg(t=t)])["classes"]["would_pass"]

    def test_print_in_next_two_hours_found(self):
        late = T0 + 55 * 60_000
        for k in (0, 1, 2):
            with self.subTest(k=k):
                self.assertEqual(self._one(T0 + k * H, late)["present_equal"], 1)

    def test_previous_hour_and_third_hour_not_searched(self):
        for hour in (T0 - H, T0 + 3 * H):
            with self.subTest(hour=hour):
                self.assertEqual(self._one(hour, MID)["absent"], 1)

    def test_wanted_hours(self):
        self.assertEqual(P.wanted_hours(T0 + 23 * H + 5), [hour_name(T0 + 23 * H), hour_name(T0 + 24 * H), hour_name(T0 + 25 * H)])


class TestEmptyAndThresholds(Base):
    def test_empty_class_is_null_never_pass(self):
        r = self.run_([trg(ws=0, uval=0)])
        self.assertEqual(r["classes"]["would_pass"]["n"], 0)
        self.assertIsNone(r["thresholds"]["would_pass_all_present_and_equal"]["met"])
        self.assertIsNone(r["thresholds"]["would_pass_n_ge_1"]["met"])
        self.assertIsNot(r["trigger_print_rules_clear"], True)
        self.assertEqual(r["classes"]["refused"]["n"], 0)

    def test_no_triggers_at_all(self):
        r = self.run_([])
        self.assertIsNone(r["thresholds"]["would_pass_all_present_and_equal"]["met"])
        self.assertIsNot(r["trigger_print_rules_clear"], True)

    def test_undetermined_blocks_even_if_rest_equal(self):
        self.tip([tip_row(sig="A")])
        r = self.run_([trg(sig="A"), trg(sig="B", pool="P2", slot=SLOT + 50)])
        self.assertIsNone(r["thresholds"]["would_pass_all_present_and_equal"]["met"])

    def test_failure_beats_undetermined(self):
        r = self.run_([trg(sig="A"), trg(sig="B", pool="P2", slot=SLOT + 50)])
        self.assertIs(r["thresholds"]["would_pass_all_present_and_equal"]["met"], False)

    def test_wrong_code_sha_is_false(self):
        self.tip([tip_row()])
        r = self.run_([trg()], sha="abc")
        self.assertIs(r["thresholds"]["origin_code_sha_matches"]["met"], False)
        self.assertIs(r["trigger_print_rules_clear"], False)

    def test_clear_true_only_when_everything_holds(self):
        self.tip([tip_row()])
        r = self.run_([trg()])
        self.assertIs(r["trigger_print_rules_clear"], True)
        self.assertEqual(r["blocking_reasons"], [])
        self.assertFalse(r["interim"])

    def test_interim_never_clear(self):
        self.tip([tip_row()])
        r = self.run_([trg()], until=SINCE + 6 * H)
        self.assertTrue(r["interim"])
        self.assertEqual(r["interim_label"], "interim, not the deciding read")
        self.assertIsNot(r["trigger_print_rules_clear"], True)
        self.assertIn("interim_not_deciding_read", r["blocking_reasons"])

    def test_interim_with_a_failure_is_false(self):
        self.assertIs(self.run_([trg()], until=SINCE + 6 * H)["trigger_print_rules_clear"], False)

    def test_no_proposal_key_and_label(self):
        r = self.run_([trg()])
        self.assertNotIn("proposal_allowed", I4.dumps(r))
        self.assertIn("(NOT a proposal verdict)", P.table(r))

    def test_two_starts_block(self):
        write(self.sd, [start(T0 + 1000, sha=SHA), start(T0 + 2000, sha=SHA), trg(t=T0 + 3000), hbhw(T0 + 40 * 60_000, HW_OK)])
        r = P.analyze(self.sd, self.td, SINCE, UNTIL, SHA)
        self.assertFalse(r["thresholds"]["single_start_after_t0"]["met"])
        self.assertIsNot(r["trigger_print_rules_clear"], True)

    def test_no_start(self):
        write(self.sd, [trg()])
        r = P.analyze(self.sd, self.td, SINCE, UNTIL, SHA)
        self.assertIn("error", r)
        self.assertIs(r["trigger_print_rules_clear"], False)


class TestSelection(Base):
    def test_sealed_fv_and_out_of_window_skipped(self):
        self.tip([tip_row(sig="A")])
        recs = [trg(sig="A"), trg(sig="S1", pool="S1", h5_look2_sealed=True), trg(sig="S2", pool="S2", sealed=True), trg(sig="F", pool="F", variant="fv"),
                trg(sig="E", pool="E", t=T0 + 500), trg(sig="L", pool="L", t=UNTIL + 1000)]
        r = self.run_(recs)
        self.assertEqual(sum(r["classes"][c]["n"] for c in P.CLASSES), 1)
        self.assertEqual(r["skipped"]["sealed_trigger"], 2)

    def test_gap_true_ws0_is_ws_pass_and_counted(self):
        d = self.run_([trg(ws=0, uval=0, gap=True)])["classes"]["ws_pass"]
        self.assertEqual((d["n"], d["ws_pass_gap_not_false"]), (1, 1))

    def test_ws_pass_failure_is_owner_flag_not_threshold(self):
        self.tip([tip_row(sig="W", pool="PW"), tip_row()])
        r = self.run_([trg(sig="W", pool="PW", ws=0, uval=0, b=1), trg()])
        self.assertTrue(r["ws_pass_owner_flag"]["flag"])
        self.assertNotIn("ws_pass", " ".join(r["thresholds"]))
        self.assertIs(r["trigger_print_rules_clear"], True)

    def test_refused_reported_not_in_threshold(self):
        self.tip([])
        r = self.run_([trg(sig="R", pool="PR", boost_src="none"), trg()])
        self.assertEqual(r["classes"]["refused"]["absent"], 1)
        self.assertNotIn("refused", " ".join(r["thresholds"]))


class TestReadSafety(Base):
    def test_allow_list_shadow_and_tip_keys(self):
        seen: set = set()
        self.tip([tip_row(sig="A"), tip_row(sig="NOTMINE", pool="X"), tip_row(sig="B", pool="P2")])
        recs = [trg(sig="A", q_trigger_sol=1.5, outcome_bx10={"pnl": 5}, exit_ladder=[1]), trg(sig="B", pool="P2", ws=0, uval=0),
                pool_rec(t=MID + 100), pool_rec(t=MID + 200, sealed=True, outcome=3)]
        r = self.run_(recs, hook=recording_hook(seen))
        self.assertEqual(r["classes"]["would_pass"]["present_equal"], 1)
        extra = seen - P.ALLOWED_KEYS - P.TIP_ROW_KEYS
        self.assertFalse(extra, sorted(extra))
        self.assertFalse({"<items>", "<keys>", "<values>", "<iter>"} & seen)  # no iteration over a record
        self.assertTrue({"signature", "event_index", "real_quote_pre", "base_pre", "quote_reserve", "base_reserve"} <= seen)
        for k in ("pnl_decoy", "q_trigger_sol", "outcome", "outcome_bx10", "exit_ladder", "pnl"):
            self.assertNotIn(k, P.ALLOWED_KEYS | P.TIP_ROW_KEYS)
            self.assertNotIn(k, seen)

    def test_only_trigger_print_rows_are_parsed(self):
        parsed = []
        inner = recording_hook(set())

        def hook(pairs):
            d = dict(pairs)
            if d.get("venue") == "pumpswap":
                parsed.append(d["signature"])
            return inner(pairs)

        self.tip([tip_row(sig="NOT1", pool="X"), tip_row(sig="A"), tip_row(sig="NOT2", pool="Y"), tip_row(sig="B", pool="Z")])
        self.run_([trg(sig="A")], hook=hook)
        self.assertEqual(parsed, ["A"])

    def test_tip_dir_untouched(self):
        self.tip([tip_row()])
        before = {p.name: p.read_bytes() for p in self.td.iterdir()}
        self.run_([trg()])
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.td.iterdir()})

    def test_partial_last_line_not_parsed(self):
        (self.td / f"trades-{hour_name(T0)}.jsonl").write_bytes(json.dumps(tip_row(), separators=(",", ":")).encode())  # no trailing newline
        r = self.run_([trg()])
        self.assertEqual(r["classes"]["would_pass"]["absent"], 1)
        self.assertEqual(r["tip_scan"]["tip_partial_last_line"], 1)

    def test_empty_tip_file(self):
        (self.td / f"trades-{hour_name(T0)}.jsonl").write_bytes(b"")
        self.assertEqual(self.run_([trg()])["classes"]["would_pass"]["absent"], 1)

    def test_bad_tip_line_counted(self):
        (self.td / f"trades-{hour_name(T0)}.jsonl").write_bytes(b'{"signature":"SIGA", broken\n')
        r = self.run_([trg()])
        self.assertEqual((r["classes"]["would_pass"]["absent"], r["tip_scan"]["tip_bad_line"]), (1, 1))

    def test_field_names_exist_in_source(self):
        here = Path(__file__).resolve().parent
        src = (here / "h5_shadow.py").read_text()
        for k in P.TRIGGER_KEYS:
            self.assertIn(f'"{k}":', src, k)
        tsrc = (here.parent / "observe" / "trade_decode.py").read_text()
        for k in ("pool", "slot", "signature", "event_index", "quote_reserve", "base_reserve"):
            self.assertIn(f'"{k}":', tsrc, k)
        self.assertEqual(P.FIELD_NAME_MISMATCH, [])


class TestDeterminismAndCli(Base):
    def _cli(self, *extra, now=UNTIL + P.SETTLE_MS, until=UNTIL):
        if not any(self.sd.iterdir()):
            write(self.sd, [start(T0 + 1000, sha=SHA), trg(), hbhw(T0 + 40 * 60_000, HW_OK)])
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = P.main(["--shadow-dir", str(self.sd), "--tip-dir", str(self.td), "--since-ms", str(SINCE), "--until-ms", str(until),
                           "--expect-code-sha", SHA, *extra], now_ms=now)
        return code, json.loads(buf.getvalue())

    def test_deterministic_json(self):
        self.tip([tip_row()])
        write(self.sd, [start(T0 + 1000, sha=SHA), trg(), hbhw(T0 + 40 * 60_000, HW_OK)])
        a = I4.dumps(P.analyze(self.sd, self.td, SINCE, UNTIL, SHA))
        b = I4.dumps(P.analyze(self.sd, self.td, SINCE, UNTIL, SHA))
        self.assertEqual(a, b)
        json.loads(a)

    def test_runs_at_settle(self):
        self.tip([tip_row()])
        code, res = self._cli()
        self.assertEqual((code, res["trigger_print_rules_clear"]), (0, True))

    def test_refuses_before_settle(self):
        code, res = self._cli(now=UNTIL + P.SETTLE_MS - 1)
        self.assertEqual((code, res["blocking_reasons"]), (4, ["until_ms_not_reached"]))

    def test_refuses_off_schedule(self):
        code, res = self._cli(until=UNTIL + H)
        self.assertEqual((code, res["blocking_reasons"]), (4, ["until_ms_not_on_schedule"]))

    def test_until_required(self):
        with self.assertRaises(SystemExit), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            P.main(["--shadow-dir", str(self.sd), "--tip-dir", str(self.td), "--since-ms", "1", "--expect-code-sha", SHA])

    def test_out_exclusive_create(self):
        self.tip([tip_row()])
        out = Path(self._td.name) / "out.json"
        code, _ = self._cli("--out", str(out))
        self.assertEqual(code, 0)
        first = out.read_text()
        code2, res = self._cli("--out", str(out))
        self.assertEqual((code2, res["blocking_reasons"]), (5, ["out_exists"]))
        self.assertEqual(out.read_text(), first)

    def test_missing_dir(self):
        shutil_sd = self.sd / "no"
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = P.main(["--shadow-dir", str(shutil_sd), "--tip-dir", str(self.td), "--since-ms", str(SINCE), "--until-ms", str(UNTIL), "--expect-code-sha", SHA],
                          now_ms=UNTIL + P.SETTLE_MS)
        self.assertEqual(code, 3)


if __name__ == "__main__":
    unittest.main()
