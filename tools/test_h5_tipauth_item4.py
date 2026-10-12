"""Tests for tools/h5_tipauth_item4.py (count-only item-4 read of the H5 shadow's tipauth field). Synthetic records only.

  python3 -m unittest tools.test_h5_tipauth_item4
"""
from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from tools import h5_tipauth_item4 as T

T0 = 1_760_000_000_000 - (1_760_000_000_000 % 3_600_000)  # an hour boundary
H = 3_600_000
MID = T0 + 30 * 60_000  # mid-hour, far from a boundary


def bt(**kw):
    d = {"status": "ok", "dropped_n": 1, "dropped_first_slot": 105, "dropped_last_slot": 105, "cover_lo": 100, "cover_hi": 150,
         "kept_ws_only_before_cover": 0, "kept_ws_only_above_cover": 0, "kept_ws_only_sig_on_tip": 0, "dropped_boost_n": 0, "dropped_sells_n": 1,
         "orphaned_by_drop_n": 0, "tipauth_nosettle": 0, "dropped": [["sigA", 0, 105]], "margin_slots": 4}
    d.update(kw)
    return d


def bu(**kw):
    d = {"status": "ok", "ws_only": 1, "tip_only": 0, "both": 5, "both_diff": 0, "dup": 0, "tip_first_slot": 100, "tip_hw_slot_global": 154}
    d.update(kw)
    return d


def trig(t=MID, pool="P1", variant="pv", ws=1, uval=1, tval=0, gap=False, slot=200, boost_src="pda", btd=None, bud=None, **extra):
    r = {"type": "trigger", "variant": variant, "pool": pool, "t_ms": t, "gap": gap, "slot": slot, "boost_src": boost_src,
         "base_breaks_unresolved_settled": ws, T.UNION_FIELD: uval, T.TIPAUTH_FIELD: tval,
         "base_union": bu() if bud is None else bud, "base_tipauth": bt() if btd is None else btd,
         # outcome-ish keys that the tool must never touch
         "q_trigger_sol": 123.456, "pnl_sol": 777.0, "exit_ladder_trigger_slots": {"60": 1}}
    r.update(extra)
    return r


def pool(t=MID + 1000, pool="P1", ws=0, uval=0, tval=0, gap=False, btd=None, bud=None, **extra):
    r = {"type": "pool", "reason": "horizon", "pool": pool, "t_ms": t, "gap": gap, "sealed": False, "base_breaks_unresolved_settled": ws,
         T.UNION_FIELD: uval, T.TIPAUTH_FIELD: tval, "base_union": bu() if bud is None else bud, "base_tipauth": bt(dropped_n=0, dropped=[], dropped_first_slot=None,
                                                                                                                      dropped_last_slot=None) if btd is None else btd,
         "min_q_pv_sol": 9.9, "triggered": ["pv"]}
    r.update(extra)
    return r


SHA = "5db78a663a0617527daacb22f5ce6c1a7e92dfa3"


def start(t=T0 + 1000, sha="abc", rule="h5-v1"):
    return {"type": "start", "t_ms": t, "code_sha": sha, "rule": rule, "argv": ["x"]}


def hb(t, rss=500_000, ue=0, est=1.5):
    return {"type": "hb", "t_ms": t, "counters": {"union_errors": ue, "gaps": 0}, "pnl": 5,
            "tip_union": {"status": "ok", "mem": {"rss_kb": rss, "rss_anon_kb": rss - 10, "hwm_kb": rss + 10, "est_mb": 2.0, "est_tipauth_mb": est,
                                                  "tipauth_wsonly": 3, "tipauth_tip_sigs": 4, "union_members": 7}}}


def write(d: Path, recs, hour_ms=T0):
    import time
    name = "h5-shadow-" + time.strftime("%Y-%m-%dT%H", time.gmtime(hour_ms // 1000)) + ".jsonl"
    with open(d / name, "a", encoding="utf-8") as fh:
        for r in recs:
            fh.write(json.dumps(r) + "\n")


class Base(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.d = Path(self._td.name)
        self.addCleanup(self._td.cleanup)

    def run_tool(self, recs, since=T0, until=None, hook=None, extra_hours=None):
        write(self.d, recs)
        for h, rr in (extra_hours or {}).items():
            write(self.d, rr, hour_ms=h)
        return T.analyze(self.d, since, until, object_hook=hook)


class TestWindowAndSealed(Base):
    def test_origin_is_last_start_and_counted(self):
        r = self.run_tool([start(T0 + 1000, "a"), trig(T0 + 2000), start(T0 + 5000, "b"), trig(T0 + 6000, pool="P2")])
        self.assertEqual(r["starts"]["n_starts_after_t0"], 2)
        self.assertEqual(r["starts"]["origin_t_ms"], T0 + 5000)
        self.assertEqual(r["records"]["triggers"], 1)
        self.assertFalse(r["verdict"]["thresholds"]["single_start_after_t0"]["met"])
        self.assertFalse(r["verdict"]["tool_rules_clear"])
        self.assertEqual(r["starts"]["distinct_code_sha"], ["a", "b"])

    def test_start_before_t0_ignored(self):
        r = self.run_tool([start(T0 - 5000, "old"), start(T0 + 10, "new"), trig(T0 + 20)])
        self.assertEqual(r["starts"]["n_starts_after_t0"], 1)
        self.assertEqual(r["starts"]["distinct_code_sha"], ["new"])

    def test_no_start(self):
        r = self.run_tool([trig()])
        self.assertIn("error", r)
        self.assertFalse(r["verdict"]["tool_rules_clear"])

    def test_sealed_records_skipped_and_counted(self):
        recs = [start(), trig(), trig(pool="S1", h5_look2_sealed=True), trig(pool="S2", sealed=True), pool(pool="P1"),
                pool(pool="S3", h5_look2_sealed=True), pool(pool="S4", sealed=True), pool(pool="S5", sealed=True, h5_look2_sealed=True)]
        recs[3]["sealed"] = True
        r = self.run_tool(recs)
        self.assertEqual(r["records"]["triggers"], 1)
        self.assertEqual(r["records"]["plain_pool_closes"], 1)
        s = r["skipped"]
        self.assertEqual((s["h5_look2_sealed_trigger"], s["sealed_trigger"], s["h5_look2_sealed_pool"], s["sealed_pool"]), (1, 1, 1, 2))
        self.assertEqual(s["both_flags"], 1)

    def test_until_ms_and_pre_origin_records(self):
        r = self.run_tool([start(T0 + 1000), trig(T0 + 500, pool="early"), trig(T0 + 2000), trig(T0 + 90_000, pool="late")], until=T0 + 50_000)
        self.assertEqual(r["records"]["triggers"], 1)

    def test_second_hour_file_read(self):
        r = self.run_tool([start(), trig()], extra_hours={T0 + H: [trig(T0 + H + 60_000 * 20, pool="P9")]})
        self.assertEqual(r["records"]["triggers"], 2)

    def test_bad_line_counted(self):
        write(self.d, [start()])
        name = next(self.d.iterdir())
        with open(name, "a") as fh:
            fh.write("{not json\n")
        r = T.analyze(self.d, T0)
        self.assertEqual(r["skipped"]["bad_line"], 1)  # counted once (second pass only)


class TestItems(Base):
    def test_item1_duration_and_count(self):
        recs = [start(T0 + 1000)] + [trig(T0 + 2000 + i, pool=f"P{i}") for i in range(10)] + [hb(T0 + 1000 + k * 600_000) for k in range(1, 145)]
        r = self.run_tool(recs, until=T0 + 2000 + 24 * H)
        t = r["verdict"]["thresholds"]["duration_24h_and_10_nogap_pv_ws_ge1"]
        self.assertEqual(r["item1_duration"]["n_nogap_pv_ws_ge1"], 10)
        self.assertTrue(t["met"])

    def test_item1_short(self):
        recs = [start(), trig(), hb(T0 + 2 * H)]
        r = self.run_tool(recs, until=T0 + 3 * H)
        self.assertFalse(r["verdict"]["thresholds"]["duration_24h_and_10_nogap_pv_ws_ge1"]["met"])

    def test_item1_excludes_gap_fv_ws0(self):
        recs = [start(), trig(pool="a", gap=True), trig(pool="b", variant="fv"), trig(pool="c", ws=0), trig(pool="d", gap=None), trig(pool="e")]
        self.assertEqual(self.run_tool(recs)["item1_duration"]["n_nogap_pv_ws_ge1"], 1)

    def test_item2_coverage_and_statuses(self):
        pend = bt(status="tip_pending", dropped_n=None, dropped_first_slot=None, dropped_last_slot=None, dropped=None, orphaned_by_drop_n=None, tipauth_nosettle=None,
                  cover_lo=None, cover_hi=None, kept_ws_only_sig_on_tip=None, dropped_boost_n=None)
        recs = [start()] + [trig(pool=f"o{i}") for i in range(9)] + [trig(pool="n", tval=None, btd=pend)]
        r = self.run_tool(recs)
        c = r["item2_coverage"]
        self.assertEqual(c["tipauth_status_all_triggers"], {"ok": 9, "tip_pending": 1})
        self.assertEqual(c["tipauth_ok_share_all"], 0.9)
        self.assertTrue(r["verdict"]["thresholds"]["tipauth_ok_ge_90pct"]["met"])
        r2 = self.run_tool([trig(pool="z", tval=None, btd=pend)] , since=T0)  # appended to same dir: 9/11 ok
        self.assertEqual(r2["item2_coverage"]["tipauth_ok_n_all"], 9)
        self.assertFalse(r2["verdict"]["thresholds"]["tipauth_ok_ge_90pct"]["met"])

    def test_item2_ok_status_with_null_value_not_ok_and_big_not_ok(self):
        big = bt(status="big", dropped_n=None, dropped_first_slot=None, dropped_last_slot=None, dropped=None, orphaned_by_drop_n=None, tipauth_nosettle=None)
        r = self.run_tool([start(), trig(pool="a", tval=None), trig(pool="b", tval=None, btd=big)])
        self.assertEqual(r["item2_coverage"]["tipauth_ok_n_all"], 0)

    def test_item2_hour_boundary(self):
        pend = bt(status="tip_pending", dropped=None)
        recs = [start(T0 - 1000 + H), trig(T0 + H + 30_000, tval=None, btd=pend), trig(T0 + H + 100_000, pool="b"), trig(T0 + H + 20 * 60_000, pool="c", tval=None, btd=pend)]
        r = self.run_tool(recs, since=T0 + H - 5000)
        c = r["item2_coverage"]
        self.assertEqual((c["boundary_triggers_n"], c["boundary_tip_pending_n"]), (2, 1))
        self.assertEqual(c["boundary_tip_pending_share"], 0.5)

    def test_item3_crosstab_all_cells_with_nulls(self):
        recs = [start(), trig(pool="a"), trig(pool="b", uval=None, tval=None), trig(pool="c", variant="fv", ws=0, uval=2, tval=1, gap=True),
                trig(pool="d", bud=bu(tip_first_slot=None)), pool(pool="a")]
        x = self.run_tool(recs)["item3_crosstab"]
        self.assertEqual(x["pv_triggers"]["ws=1+|union=1+|tipauth=0|gap=F"], 2)
        self.assertEqual(x["pv_triggers"]["ws=1+|union=1+|tipauth=0|gap=F|tip_first_slot=set"], 1)
        self.assertEqual(x["pv_triggers"]["ws=1+|union=1+|tipauth=0|gap=F|tip_first_slot=null"], 1)
        self.assertEqual(x["pv_triggers"]["ws=1+|union=null|tipauth=null|gap=F"], 1)
        self.assertEqual(x["fv_triggers"]["ws=0|union=1+|tipauth=1+|gap=T"], 1)
        self.assertEqual(x["plain_pool_closes"]["ws=0|union=0|tipauth=0|gap=F"], 1)
        self.assertEqual(sum(1 for k in x["pv_triggers"] if "|gap=" in k and "|ws=" not in k), len(x["pv_triggers"]))
        self.assertEqual(len([k for k in x["pv_triggers"] if k.count("|") == 3]), 36)

    def test_item3_c1_c4_subtab(self):
        recs = [start(), trig(pool="a"), trig(pool="b", btd=bt(orphaned_by_drop_n=1)), trig(pool="c", btd=bt(dropped_first_slot=100, dropped=[["s", 0, 100]], dropped_last_slot=100))]
        s = self.run_tool(recs)["item3_c1_c4_subtab"]
        self.assertEqual(s["n_c1_c4"], 3)
        self.assertEqual(s["cells"]["orphaned=0|nosettle=0|dropped_first_slot_eq_cover_lo=False"], 1)
        self.assertEqual(s["cells"]["orphaned=1+|nosettle=0|dropped_first_slot_eq_cover_lo=False"], 1)
        self.assertEqual(s["cells"]["orphaned=0|nosettle=0|dropped_first_slot_eq_cover_lo=True"], 1)


class TestConditions(unittest.TestCase):
    def v(self, **kw):
        return T.view_record(trig(**kw), "trigger")

    def test_pass(self):
        c = T.conditions(self.v())
        self.assertTrue(T.would_pass_view(c), c)

    def test_each_single_fail(self):
        cases = {
            "C1": [dict(gap=True), dict(gap=None), dict(ws=0), dict(tval=1), dict(tval=None), dict(tval=False), dict(bud=bu(status="no_cover")),
                   dict(btd=bt(status="tip_pending")), dict(ws=None)],
            "C2": [dict(bud=bu(tip_only=1)), dict(bud=bu(both_diff=2)), dict(bud=bu(tip_only=None))],
            "C3": [dict(btd=bt(dropped_boost_n=1)), dict(boost_src="behavioural"), dict(boost_src=None), dict(btd=bt(dropped_boost_n=None))],
            "C4": [dict(btd=bt(dropped_n=0)), dict(btd=bt(dropped_n=6)), dict(btd=bt(kept_ws_only_sig_on_tip=1)), dict(btd=bt(dropped_n=None))],
            "C5a": [dict(btd=bt(orphaned_by_drop_n=1)), dict(btd=bt(orphaned_by_drop_n=None))],
            "C5b": [dict(btd=bt(tipauth_nosettle=1)), dict(btd=bt(tipauth_nosettle=None))],
            "C5c": [dict(btd=bt(dropped_first_slot=100)), dict(btd=bt(dropped_first_slot=99)), dict(btd=bt(dropped_first_slot=None))],
        }
        for cond, variants in cases.items():
            for kw in variants:
                c = T.conditions(self.v(**kw))
                self.assertFalse(c[cond], (cond, kw))
                self.assertFalse(T.would_pass_view(c))

    def test_boundaries_c4(self):
        for n, ok in [(1, True), (5, True), (6, False)]:
            c = T.conditions(self.v(btd=bt(dropped_n=n, dropped=[["s%d" % i, 0, 105 + i] for i in range(n)])))
            self.assertEqual(c["C4"], ok)

    def test_record_without_keys(self):
        r = {"type": "trigger", "variant": "pv", "pool": "p", "t_ms": 1}
        c = T.conditions(T.view_record(r, "trigger"))
        self.assertFalse(any(v for k, v in c.items() if k not in ("C7f",)))
        self.assertFalse(T.would_pass_view(c))
        self.assertEqual(T.would_pass(r), (False, "ws_not_ge1"))


class TestC5AndFunnel(Base):
    def test_c5_parts_counts(self):
        recs = [start(), trig(pool="ok"),
                trig(pool="a", btd=bt(orphaned_by_drop_n=1)), trig(pool="b", btd=bt(tipauth_nosettle=1)), trig(pool="c", btd=bt(dropped_first_slot=100, dropped=[["s", 0, 100]], dropped_last_slot=100)),
                trig(pool="abc", btd=bt(orphaned_by_drop_n=1, tipauth_nosettle=1, dropped_first_slot=100, dropped=[["s", 0, 100]], dropped_last_slot=100)),
                trig(pool="c2", boost_src="behavioural")]
        r = self.run_tool(recs)
        w = r["item6_would_pass"]
        self.assertEqual(w["would_pass_n"], 1)
        c5 = w["c5_parts"]
        self.assertEqual((c5["n_c1_c4"], c5["fail_5a"], c5["fail_5b"], c5["fail_5c"]), (5, 2, 2, 2))
        self.assertEqual((c5["fail_only_5a"], c5["fail_only_5b"], c5["fail_only_5c"], c5["fail_all_three"], c5["fail_any"]), (1, 1, 1, 1, 4))
        self.assertEqual(w["leave_one_out_removes"]["C3"], 1)
        self.assertEqual([f["after"] for f in w["funnel"]], ["all_triggers"] + T.COND_NAMES)
        self.assertEqual(w["funnel"][-1]["n"], 1)
        self.assertFalse(w["c6_applied"])

    def test_pv_only_count(self):
        r = self.run_tool([start(), trig(pool="a"), trig(pool="b", variant="fv")])
        self.assertEqual((r["item6_would_pass"]["would_pass_n"], r["item6_would_pass"]["would_pass_pv_n"]), (1, 1))
        self.assertEqual(r["item6_would_pass"]["fv_not_eligible_variant_n"], 1)


class TestIncompleteness(Base):
    def test_share_and_cap(self):
        # 20 ws0 closes ok: 1 with tipauth>=1, 1 with orphaned>=1 only -> 10% > 5%
        closes = [pool(pool=f"c{i}") for i in range(90)]
        closes += [pool(pool=f"t{i}", uval=1, tval=1, btd=bt(dropped_n=1)) for i in range(5)]
        closes += [pool(pool=f"o{i}", btd=bt(orphaned_by_drop_n=1, dropped_n=1)) for i in range(5)]
        r = self.run_tool([start()] + closes)
        i5 = r["item5_tip_incompleteness"]["plain_pool_closes"]
        self.assertEqual((i5["n_ok"], i5["incomplete_n"], i5["incomplete_share_of_ok"]), (100, 10, 0.1))
        self.assertEqual(i5["dropped_n_ge1_n"], 10)
        self.assertFalse(r["verdict"]["thresholds"]["tip_incompleteness_le_5pct"]["met"])
        self.assertFalse(r["verdict"]["tool_rules_clear"])

    def test_at_cap_is_met_and_nulls_do_not_dilute(self):
        none_bt = bt(status="no_tip_rows", dropped_n=None, dropped=None, dropped_first_slot=None, dropped_last_slot=None, orphaned_by_drop_n=None, tipauth_nosettle=None)
        closes = [pool(pool=f"c{i}") for i in range(95)] + [pool(pool=f"t{i}", uval=1, tval=1, btd=bt(dropped_n=1)) for i in range(5)]
        closes += [pool(pool=f"n{i}", tval=None, btd=none_bt) for i in range(100)]
        r = self.run_tool([start()] + closes)
        i5 = r["item5_tip_incompleteness"]["plain_pool_closes"]
        self.assertEqual(i5["incomplete_share_of_ok"], 0.05)
        self.assertEqual(i5["incomplete_share_of_all_ws0_nogap"], 0.025)
        self.assertTrue(r["verdict"]["thresholds"]["tip_incompleteness_le_5pct"]["met"])

    def test_gap_and_ws1_closes_excluded(self):
        r = self.run_tool([start(), pool(pool="g", gap=True), pool(pool="w", ws=1)])
        self.assertEqual(r["item5_tip_incompleteness"]["plain_pool_closes"]["n_ws0_nogap"], 0)
        self.assertIsNone(r["verdict"]["thresholds"]["tip_incompleteness_le_5pct"]["met"])


class TestConsistency(Base):
    def checks(self, recs, hbs=()):
        r = self.run_tool([start()] + list(recs) + list(hbs))
        return {k: c["n"] for k, c in r["item4_consistency"]["checks"].items() if c["n"]}, r

    def test_clean(self):
        c, r = self.checks([trig(), pool()], [hb(MID, ue=0)])
        self.assertEqual(c, {})
        self.assertTrue(r["verdict"]["thresholds"]["consistency_all_zero"]["met"])

    def test_each_violation(self):
        nulls = dict(dropped_n=None, dropped_first_slot=None, dropped_last_slot=None, dropped=None, orphaned_by_drop_n=None, tipauth_nosettle=None)
        cases = {
            "dropped_n0_tipauth_differs_from_union": trig(uval=2, tval=1, btd=bt(dropped_n=0, dropped=[], dropped_first_slot=None, dropped_last_slot=None)),
            "nonnull_tipauth_status_not_ok": trig(btd=bt(status="big", **nulls)),
            "nosettle_below_tipauth": trig(tval=2, uval=2, btd=bt(tipauth_nosettle=1)),
            "orphaned_ge1_with_dropped_n0": trig(uval=0, tval=0, btd=bt(orphaned_by_drop_n=1, dropped_n=0, dropped=[], dropped_first_slot=None, dropped_last_slot=None)),
            "ok_with_null_orphaned_nosettle_or_dropped_n": trig(btd=bt(orphaned_by_drop_n=None)),
            "notok_with_nonnull_orphaned_nosettle_or_dropped_n": trig(tval=None, btd=bt(status="no_cover", dropped=None, dropped_first_slot=None, dropped_last_slot=None)),
            "ok_dropped_not_list_of_min_dropped_n_20": trig(btd=bt(dropped_n=2)),
            "notok_with_nonnull_dropped": trig(tval=None, btd=bt(status="tip_pending", dropped_n=None, orphaned_by_drop_n=None, tipauth_nosettle=None)),
            "entry_slot_outside_cover": trig(btd=bt(dropped=[["s", 0, 99]], dropped_first_slot=99, dropped_last_slot=99)),
            "trigger_entry_above_slot_minus_margin": trig(slot=108, btd=bt(dropped=[["s", 0, 105]])),
            "first_entry_slot_ne_dropped_first_slot": trig(btd=bt(dropped_first_slot=104)),
            "last_entry_slot_ne_dropped_last_slot": trig(btd=bt(dropped_last_slot=106)),
            "repeated_signature_event_index": trig(btd=bt(dropped_n=2, dropped=[["s", 0, 105], ["s", 0, 105]])),
            "dropped_entry_malformed": trig(btd=bt(dropped=[["s", 0]])),
        }
        for name, rec in cases.items():
            c, _ = self.checks([rec])
            self.assertIn(name, c, (name, c))
            self.tearDown()
            self.setUp()

    def test_without_dropped_key_and_exception_record(self):
        b = bt()
        del b["dropped"]
        c, _ = self.checks([trig(btd=b)])
        self.assertEqual(c.get("base_tipauth_without_dropped_key"), 1)
        self.setUp()
        exc = {"status": "error", "margin_slots": 4}
        r = self.run_tool([start(), trig(tval=None, uval=None, btd=exc, bud={"status": "error"})] + [hb(MID, ue=1)])
        ch = r["item4_consistency"]["checks"]
        self.assertEqual(ch["base_tipauth_without_dropped_key"]["n"], 1)
        self.assertEqual(ch["union_errors"]["n"], 1)
        self.assertEqual(r["item4_consistency"]["error_status_records"], 1)

    def test_union_errors_is_max_over_hb(self):
        c, _ = self.checks([], [hb(MID, ue=0), hb(MID + 60_000, ue=3), hb(MID + 120_000, ue=3)])
        self.assertEqual(c, {"union_errors": 3})

    def test_would_pass_not_fully_enumerated(self):
        # dropped_n 1 but list empty: also fails length check
        c, _ = self.checks([trig(btd=bt(dropped=[]))])
        self.assertIn("ok_dropped_not_list_of_min_dropped_n_20", c)

    def test_not_fully_enumerated_flag(self):
        v = T.view_record(trig(btd=bt(dropped_n=5, dropped=[["s", 0, 105]])), "trigger")
        self.assertFalse(T.fully_enumerated(v))
        v = T.view_record(trig(), "trigger")
        self.assertTrue(T.fully_enumerated(v))


class TestCensusInput(Base):
    def test_signatures_listed_no_network(self):
        recs = [start(), trig(pool="a", btd=bt(dropped_n=2, dropped=[["sigB", 0, 105], ["sigA", 1, 106]], dropped_last_slot=106)), trig(pool="b"), trig(pool="x", ws=0)]
        r = self.run_tool(recs)
        c = r["item7_census_input"]
        self.assertEqual(c["would_pass_signatures"], ["sigA", "sigB"])
        self.assertEqual(c["network_calls"], 0)
        self.assertFalse(c["census_performed"])
        self.assertEqual(c["would_pass_fully_enumerated_n"], 2)

    def test_close_sample_list_marks_partial(self):
        big = bt(dropped_n=25, dropped=[[f"s{i}", 0, 101 + i] for i in range(20)], dropped_first_slot=101, dropped_last_slot=130, orphaned_by_drop_n=0, tipauth_nosettle=0)
        r = self.run_tool([start(), pool(pool="c", ws=1, uval=1, tval=0, btd=big)])
        rows = r["item7_census_input"]["tipauth0_ws_ge1_nogap_closes"]
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["partial"])


class TestMargin(Base):
    def run_pair(self, trig_entries, close_dropped, close_n, wp=True, close_cover_hi=250, close_status="ok"):
        self.tearDown()
        self.setUp()
        t = trig(btd=bt(dropped_n=len(trig_entries), dropped=trig_entries, dropped_first_slot=trig_entries[0][2], dropped_last_slot=trig_entries[-1][2]))
        c = pool(ws=1, uval=1, tval=0, btd=bt(dropped_n=close_n, dropped=close_dropped, cover_hi=close_cover_hi, status=close_status,
                                              dropped_first_slot=close_dropped[0][2] if close_dropped else None,
                                              dropped_last_slot=close_dropped[-1][2] if close_dropped else None))
        return self.run_tool([start(), t, c])["item10_margin_test"]

    def test_still_dropped(self):
        m = self.run_pair([["s", 0, 105]], [["s", 0, 105]], 1)
        self.assertEqual((m["still_dropped"], m["left_D"], m["undetermined"]), (1, 0, 0))
        self.assertEqual(m["rows"][0]["trigger_slot_minus_entry_slot"], 95)

    def test_left_d_small_close_list(self):
        m = self.run_pair([["s", 0, 105]], [], 0)
        self.assertEqual(m["left_D"], 1)
        self.assertEqual(m["would_pass_left_D"], 1)

    def test_left_d_big_close_list_requires_slot_below_last(self):
        lst = [[f"x{i}", 0, 110 + i] for i in range(20)]
        m = self.run_pair([["s", 0, 105]], lst, 30)
        self.assertEqual(m["left_D"], 1)
        m = self.run_pair([["s", 0, 140]], lst, 30)  # slot above the last listed entry (129): cannot say
        self.assertEqual(m["undetermined"], 1)
        self.assertEqual(m["left_D"], 0)

    def test_undetermined_when_no_usable_close(self):
        m = self.run_pair([["s", 0, 105]], [["s", 0, 105]], 1, close_cover_hi=190)  # cover_hi 190 < trigger slot 200
        self.assertEqual(m["undetermined"], 1)
        m2 = self.run_pair([["s", 0, 105]], [["s", 0, 105]], 1, close_status="big")
        self.assertEqual(m2["still_dropped"], 0)

    def test_event_index_matters(self):
        m = self.run_pair([["s", 1, 105]], [["s", 0, 105]], 1)
        self.assertEqual(m["left_D"], 1)

    def test_thresholds(self):
        # fewer than 100 tested, one left D -> fires
        m = self.run_pair([["s", 0, 105]], [], 0)
        self.assertEqual(m["tested"], 1)

    def test_verdict_margin_share(self):
        base = {"item1_duration": {"covered_h": 30, "elapsed_h": 30, "n_nogap_pv_ws_ge1": 12}, "expect_code_sha": "a" * 40,
                "item2_coverage": {"tipauth_ok_share_all": 1.0, "tipauth_ok_share_nogap_pv_ws_ge1": 1.0, "union_ok_share_all": 1.0},
                "item4_consistency": {"total": 0, "checks": {}},
                "item5_tip_incompleteness": {"plain_pool_closes": {"incomplete_share_of_ok": 0.0, "incomplete_share_of_all_ws0_nogap": 0.0, "n_ok": 100}},
                "item9_timing_memory": {"rss_kb_max": 1000}, "starts": {"n_starts_after_t0": 1, "distinct_code_sha": ["a"], "origin_code_sha": "a" * 40},
                "item7_census_input": {"would_pass_fully_enumerated_n": 3, "would_pass_n": 3}, "item6_would_pass": {"would_pass_pv_n": 3}}

        def v(tested, left, wpl=0, td=None, ld=None, wpt=3):
            td = tested if td is None else td
            ld = left if ld is None else ld
            r = dict(base, item10_margin_test={"tested": tested, "left_D": left, "left_D_share_of_tested": None if not tested else left / tested, "undetermined": 0,
                                                "would_pass_left_D": wpl, "would_pass_entries": 3, "would_pass_tested": wpt, "would_pass_undetermined": 0,
                                                "tested_distinct": td, "left_D_distinct": ld})
            return T.verdict(r)
        k = "margin_left_D_share_le_1pct"
        self.assertTrue(v(200, 2)["thresholds"][k]["met"])  # exactly 1%
        self.assertFalse(v(200, 3)["thresholds"][k]["met"])
        self.assertFalse(v(99, 1)["thresholds"][k]["met"])
        self.assertTrue(v(99, 0)["thresholds"][k]["met"])
        self.assertIsNone(v(0, 0)["thresholds"][k]["met"])
        # distinct counts also fire; duplicates cannot dilute the 100 floor
        self.assertFalse(v(121, 1, td=61, ld=1)["thresholds"][k]["met"])
        self.assertFalse(v(200, 0, wpl=1)["tool_rules_clear"])
        ok = v(200, 0)
        self.assertTrue(ok["tool_rules_clear"])
        self.assertEqual(ok["blocking_reasons"], [])
        self.assertIn("item7_rpc_census", ok["outside_tool_pending"])
        # no pass on an empty would-pass class
        self.assertIsNone(v(200, 0, wpt=0)["thresholds"]["margin_no_would_pass_left_D"]["met"])
        self.assertFalse(v(200, 0, wpt=0)["tool_rules_clear"])

    def test_non_would_pass_left_d_reported_not_counted_as_wp(self):
        t = trig(ws=1, btd=bt(orphaned_by_drop_n=1, dropped=[["s", 0, 105]]))
        c = pool(ws=1, uval=1, tval=0, btd=bt(dropped_n=0, dropped=[], dropped_first_slot=None, dropped_last_slot=None, cover_hi=250))
        m = self.run_tool([start(), t, c])["item10_margin_test"]
        self.assertEqual((m["left_D"], m["would_pass_left_D"]), (1, 0))


class TestMemoryAndVerdict(Base):
    def test_hb_figures(self):
        r = self.run_tool([start(), hb(MID, rss=400_000, est=1.0), hb(MID + 60_000, rss=900_000, est=2.5), hb(MID + 120_000, rss=800_000, est=2.0)])
        m = r["item9_timing_memory"]
        self.assertEqual((m["rss_kb_max"], m["rss_kb_last"], m["est_tipauth_mb_max"], m["est_tipauth_mb_last"], m["hb_n"]), (900_000, 800_000, 2.5, 2.0, 3))
        self.assertTrue(r["verdict"]["thresholds"]["rss_below_1gb"]["met"])
        self.assertIsNone(m["decision_to_send_ms"])
        r2 = self.run_tool([hb(MID + 180_000, rss=1_000_000)])
        self.assertFalse(r2["verdict"]["thresholds"]["rss_below_1gb"]["met"])

    def test_no_hb_is_short(self):
        r = self.run_tool([start(), trig()])
        self.assertIsNone(r["verdict"]["thresholds"]["rss_below_1gb"]["met"])
        self.assertFalse(r["verdict"]["tool_rules_clear"])

    def test_no_forbidden_word(self):
        r = self.run_tool([start(), trig()])
        self.assertNotIn("profitable", T.dumps(r).lower())

    def test_exception_records_lacking_dropped_do_not_crash(self):
        recs = [start(), trig(btd={"status": "error", "margin_slots": 4}, bud={"status": "error"}, tval=None, uval=None), pool(btd={"status": "error", "margin_slots": 4}, tval=None, uval=None)]
        r = self.run_tool(recs)
        self.assertEqual(r["item2_coverage"]["tipauth_status_all_triggers"], {"error": 1})

    def test_trigger_without_tipauth_keys(self):
        t = {"type": "trigger", "variant": "pv", "pool": "p", "t_ms": MID, "gap": False, "slot": 5, WS_FIELD_NAME: 1}
        r = self.run_tool([start(), t])
        self.assertEqual(r["item2_coverage"]["tipauth_status_all_triggers"], {"missing": 1})
        self.assertEqual(r["item6_would_pass"]["would_pass_n"], 0)


WS_FIELD_NAME = T.WS_FIELD


class Tracking(dict):
    """A dict that records every key looked at (get, [], in, iteration, items, keys, values, copy, len)."""
    log: set = set()

    def __getitem__(self, k):
        Tracking.log.add(k)
        return super().__getitem__(k)

    def get(self, k, default=None):
        Tracking.log.add(k)
        return super().get(k, default)

    def __contains__(self, k):
        Tracking.log.add(k)
        return super().__contains__(k)

    def __iter__(self):
        Tracking.log.add("<ITER>")
        return super().__iter__()

    def keys(self):
        Tracking.log.add("<KEYS>")
        return super().keys()

    def values(self):
        Tracking.log.add("<VALUES>")
        return super().values()

    def items(self):
        Tracking.log.add("<ITEMS>")
        return super().items()

    def copy(self):
        Tracking.log.add("<COPY>")
        return super().copy()

    def __len__(self):
        Tracking.log.add("<LEN>")
        return super().__len__()

    def pop(self, *a):
        Tracking.log.add("<POP>")
        return super().pop(*a)

    def setdefault(self, *a):
        Tracking.log.add("<SETDEFAULT>")
        return super().setdefault(*a)


POISON = ["q_trigger_sol", "pnl_sol", "exit_ladder_trigger_slots", "min_q_pv_sol", "triggered", "argv", "reason", "pnl"]


class TestAllowList(Base):
    def full_fixture(self):
        recs = [start(), trig(), trig(pool="P2", variant="fv", gap=True), trig(pool="S", h5_look2_sealed=True), pool(ws=1, uval=1, tval=0), pool(pool="S", sealed=True),
                {"type": "gap", "kind": "feed_restart", "open_pools": 3, "pnl_sol": 1.0}, hb(MID), {"type": "boost_slice", "pool": "P1", "t_ms": MID, "sol": 5.0},
                {"type": "excluded", "pool": "E", "t_ms": MID, "slot": 1}, {"type": "outcome", "pool": "P1", "t_ms": MID, "net_sol": 3.0, "pnl_sol": 3.0},
                {"type": "tip_union_start", "t_ms": MID}]
        return recs

    def test_accessed_keys_subset_of_allow_list(self):
        Tracking.log = set()
        r = self.run_tool(self.full_fixture(), hook=Tracking)
        self.assertGreater(len(Tracking.log), 20)
        outside = Tracking.log - T.ALLOWED_KEYS
        self.assertEqual(outside, set(), f"keys read outside the allow-list: {sorted(outside, key=str)}")
        for k in POISON:
            self.assertNotIn(k, Tracking.log)
        self.assertNotIn("<ITER>", Tracking.log)
        self.assertEqual(r["records"]["triggers"], 2)

    def test_allow_list_has_no_outcome_keys(self):
        bad = {k for k in T.ALLOWED_KEYS if any(w in k for w in ("pnl", "net_", "fill", "outcome", "min_q", "q_trigger", "_sol", "sol_", "ladder", "return", "exit_"))}
        self.assertEqual(bad, set())

    def test_tracking_wrapper_detects_a_violation(self):
        Tracking.log = set()
        d = Tracking({"a": 1})
        d.get("pnl_sol")
        list(d)
        self.assertTrue({"pnl_sol", "<ITER>"} <= Tracking.log)

    def test_poison_values_never_reach_output(self):
        recs = self.full_fixture()
        recs[1]["q_trigger_sol"] = 987654.321
        recs[1]["pnl_sol"] = 424242.5
        out = T.dumps(self.run_tool(recs))
        for s in ("987654.321", "424242.5", "q_trigger_sol", "pnl_sol"):
            self.assertNotIn(s, out)


class TestDeterminismAndCli(Base):
    def test_same_input_identical_bytes(self):
        recs = [start(), hb(MID)] + [trig(pool=f"P{i}", btd=bt(orphaned_by_drop_n=i % 2)) for i in range(7)] + [pool(pool=f"P{i}", ws=1, uval=1, tval=0) for i in range(3)]
        write(self.d, recs)
        a = T.dumps(T.analyze(self.d, T0))
        b = T.dumps(T.analyze(self.d, T0))
        self.assertEqual(a, b)
        json.loads(a)

    def test_file_order_does_not_matter_for_content(self):
        w1 = [start(), trig(pool="A"), trig(pool="B")]
        write(self.d, w1)
        a = T.dumps(T.analyze(self.d, T0))
        td2 = tempfile.TemporaryDirectory()
        self.addCleanup(td2.cleanup)
        write(Path(td2.name), [start(), trig(pool="B"), trig(pool="A")])
        b = T.analyze(Path(td2.name), T0)
        self.assertEqual(json.loads(a)["item3_crosstab"], json.loads(T.dumps(b))["item3_crosstab"])

    def test_cli_out_and_table(self):
        import contextlib
        import io
        write(self.d, [start(), trig(), hb(MID)])
        out = self.d / "result.json"
        so, se = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(so), contextlib.redirect_stderr(se):
            rc = T.main(["--shadow-dir", str(self.d), "--since-ms", str(T0), "--until-ms", str(T0 + 24 * H), "--expect-code-sha", SHA, "--out", str(out), "--table"], now_ms=T0 + 100 * H)
        self.assertEqual(rc, 0)
        self.assertEqual(so.getvalue(), out.read_text())
        self.assertIn("tool_rules_clear", se.getvalue())
        self.assertIn("NOT a proposal verdict", se.getvalue())
        self.assertIn("ambiguities", json.loads(so.getvalue()))

    def test_ambiguities_listed_with_unique_ids(self):
        ids = [a[0] for a in T.AMBIGUITIES]
        self.assertEqual(len(ids), len(set(ids)))


class TestRound2(Base):
    def analyze(self, recs, since=T0, until=None, sha=None):
        write(self.d, recs)
        return T.analyze(self.d, since, until, expect_code_sha=sha)

    def reset(self):
        self.tearDown()
        self.setUp()

    def th(self, r, name):
        return r["verdict"]["thresholds"][name]

    # change 1
    def test_code_sha_threshold(self):
        self.assertTrue(self.th(self.analyze([start(sha=SHA), trig()], sha=SHA), "origin_code_sha_matches")["met"])
        self.reset()
        r = self.analyze([start(sha="deadbeef"), trig()], sha=SHA)
        self.assertFalse(self.th(r, "origin_code_sha_matches")["met"])
        self.assertFalse(r["verdict"]["tool_rules_clear"])
        self.reset()
        self.assertIsNone(self.th(self.analyze([start(sha=SHA)], sha=None), "origin_code_sha_matches")["met"])

    def test_cli_requires_sha_and_until(self):
        import contextlib
        import io
        write(self.d, [start(sha=SHA)])
        for args in (["--shadow-dir", str(self.d), "--since-ms", str(T0), "--until-ms", str(T0 + 24 * H)],
                     ["--shadow-dir", str(self.d), "--since-ms", str(T0), "--expect-code-sha", SHA]):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as cm:
                T.main(args, now_ms=T0 + 100 * H)
            self.assertEqual(cm.exception.code, 2)

    # change 2
    def test_item5_null_below_100_ok_closes(self):
        none_bt = bt(status="no_tip_rows", dropped_n=None, dropped=None, dropped_first_slot=None, dropped_last_slot=None, orphaned_by_drop_n=None, tipauth_nosettle=None)
        r = self.analyze([start()] + [pool(pool=f"n{i}", tval=None, btd=none_bt) for i in range(40)])
        self.assertEqual(r["item5_tip_incompleteness"]["plain_pool_closes"]["n_ok"], 0)
        self.assertIsNone(self.th(r, "tip_incompleteness_le_5pct")["met"])
        self.reset()
        r = self.analyze([start()] + [pool(pool=f"c{i}") for i in range(99)])
        self.assertIsNone(self.th(r, "tip_incompleteness_le_5pct")["met"])
        self.reset()
        r = self.analyze([start()] + [pool(pool=f"c{i}") for i in range(100)])
        self.assertTrue(self.th(r, "tip_incompleteness_le_5pct")["met"])

    # change 3
    def test_no_proposal_allowed_string(self):
        r = self.analyze([start(), trig(), pool()])
        self.assertNotIn("proposal_allowed", T.dumps(r))
        self.assertIn("tool_rules_clear", r["verdict"])
        self.assertIn("NOT a proposal verdict", T.table(r))

    # change 4
    def test_empty_classes_do_not_pass(self):
        r = self.analyze([start(), trig(ws=0)])  # 0 would-pass
        self.assertIsNone(self.th(r, "would_pass_pv_n_ge_1")["met"])
        self.assertIsNone(self.th(r, "margin_no_would_pass_left_D")["met"])
        self.assertIsNone(self.th(r, "would_pass_all_fully_enumerated")["met"])
        self.assertFalse(r["verdict"]["tool_rules_clear"])
        self.reset()
        r = self.analyze([start(), trig()])
        self.assertTrue(self.th(r, "would_pass_pv_n_ge_1")["met"])
        self.assertTrue(self.th(r, "would_pass_all_fully_enumerated")["met"])
        self.assertIsNone(self.th(r, "margin_no_would_pass_left_D")["met"])  # would-pass entries never tested (no close)
        self.assertEqual(r["item10_margin_test"]["would_pass_undetermined"], 1)

    def test_would_pass_left_d_false_and_tested_true(self):
        close = pool(ws=1, uval=1, tval=0, btd=bt(dropped_n=0, dropped=[], dropped_first_slot=None, dropped_last_slot=None, cover_hi=250))
        r = self.analyze([start(), trig(), close])
        self.assertFalse(self.th(r, "margin_no_would_pass_left_D")["met"])
        self.reset()
        r = self.analyze([start(), trig(), pool(ws=1, uval=1, tval=0, btd=bt(cover_hi=250))])
        self.assertTrue(self.th(r, "margin_no_would_pass_left_D")["met"])
        self.assertEqual(r["item10_margin_test"]["would_pass_tested"], 1)

    # change 5
    def test_distinct_entries_pv_fv_same_print(self):
        closes = [pool(pool=f"P{i}", ws=1, uval=1, tval=0, btd=bt(cover_hi=250)) for i in range(60)]
        trigs = []
        for i in range(60):
            trigs += [trig(pool=f"P{i}", variant="pv"), trig(pool=f"P{i}", variant="fv")]
        trigs.append(trig(pool="L"))  # one pv-only pool whose entry left D
        closes.append(pool(pool="L", ws=1, uval=1, tval=0, btd=bt(dropped_n=0, dropped=[], dropped_first_slot=None, dropped_last_slot=None, cover_hi=250)))
        r = self.analyze([start()] + trigs + closes)
        m = r["item10_margin_test"]
        self.assertEqual((m["tested"], m["left_D"], m["tested_distinct"], m["left_D_distinct"]), (121, 1, 61, 1))
        self.assertFalse(self.th(r, "margin_left_D_share_le_1pct")["met"])  # distinct count < 100: one left D fires

    def test_distinct_same_print_counted_once(self):
        c = pool(ws=1, uval=1, tval=0, btd=bt(dropped_n=0, dropped=[], dropped_first_slot=None, dropped_last_slot=None, cover_hi=250))
        m = self.analyze([start(), trig(variant="pv"), trig(variant="fv"), c])["item10_margin_test"]
        self.assertEqual((m["entries"], m["distinct_entries"], m["left_D_distinct"]), (2, 1, 1))

    def test_fire_rule(self):
        self.assertTrue(T.fire(99, 1))
        self.assertFalse(T.fire(99, 0))
        self.assertFalse(T.fire(200, 2))
        self.assertTrue(T.fire(200, 3))

    # change 6
    def test_sealed_flags_fail_closed(self):
        recs = [start(), trig(pool="a", sealed=1), trig(pool="b", sealed="yes"), trig(pool="c", h5_look2_sealed="true"), trig(pool="d", h5_look2_sealed=1),
                trig(pool="e", sealed=False), trig(pool="f", sealed=None), pool(pool="g", sealed="yes"), pool(pool="h", sealed=0)]
        r = self.analyze(recs)
        self.assertEqual(r["records"]["triggers"], 2)
        self.assertEqual(r["records"]["plain_pool_closes"], 0)
        self.assertEqual((r["skipped"]["sealed_trigger"], r["skipped"]["h5_look2_sealed_trigger"]), (2, 2))
        self.assertFalse(T.flag(None) or T.flag(False))
        self.assertTrue(all(T.flag(x) for x in (True, 1, 0, "", "yes", [], {})))

    # change 7
    def test_coverage_three_valued(self):
        r = self.analyze([start(), trig(pool="f", variant="fv", tval=None, btd=bt(status="tip_pending", dropped=None))])
        self.assertIsNone(r["item2_coverage"]["tipauth_ok_share_nogap_pv_ws_ge1"])
        self.assertFalse(self.th(r, "tipauth_ok_ge_90pct")["met"])  # known 0.0 fails though the other share is unknown
        self.reset()
        r = self.analyze([start(), trig(pool="f", variant="fv")])
        self.assertIsNone(self.th(r, "tipauth_ok_ge_90pct")["met"])
        self.assertIn("tipauth_ok_ge_90pct:short", r["verdict"]["blocking_reasons"])

    # change 8
    def test_non_str_pool_counted_and_left_out(self):
        r = self.analyze([start(), trig(pool=["x"]), trig(pool=5), trig(pool={}), pool(pool=None), trig(pool="ok")])
        self.assertEqual(r["records"]["triggers"], 1)
        self.assertEqual(r["records"]["plain_pool_closes"], 0)
        self.assertEqual(r["item4_consistency"]["checks"]["record_pool_not_str"]["n"], 4)
        self.assertFalse(self.th(r, "consistency_all_zero")["met"])

    def test_union_status_unhashable_and_entry_shapes(self):
        r = self.analyze([start(), trig(bud=bu(status=["x"])), trig(pool="q", btd=bt(dropped=[["s", [1], 5]], dropped_n=1))])
        self.assertEqual(r["item2_coverage"]["union_status_all_triggers"], {"['x']": 1, "ok": 1})
        self.assertGreaterEqual(r["item4_consistency"]["checks"]["dropped_entry_malformed"]["n"], 1)

    def test_margin_uses_first_close_only(self):
        unusable = pool(t=MID + 1000, ws=1, uval=1, tval=0, btd=bt(cover_hi=190))  # cover_hi < trigger slot 200
        usable = pool(t=MID + 2000, ws=1, uval=1, tval=0, btd=bt(cover_hi=250))
        m = self.analyze([start(), trig(), unusable, usable])["item10_margin_test"]
        self.assertEqual((m["undetermined"], m["still_dropped"]), (1, 0))

    def test_malformed_close_last_entry_undetermined(self):
        close = pool(ws=1, uval=1, tval=0, btd=bt(dropped_n=30, dropped=[["x", 0, 110], "tail"], cover_hi=250))
        m = self.analyze([start(), trig(), close])["item10_margin_test"]
        self.assertEqual((m["undetermined"], m["left_D"]), (1, 0))

    def test_junk_fixtures_never_raise(self):
        import random
        rng = random.Random(1)
        junk = [None, True, 0, -1, 1.5, "x", [], [1], [[1, 2, 3]], [["s", [1], 5]], {}, [["s", 0, 105], "tail"], 10 ** 30, float("nan"), float("inf")]

        def paths(o):
            out = []
            if isinstance(o, dict):
                for k, v in o.items():
                    out.append((o, k))
                    out += paths(v)
            return out
        fpath = self.d / "h5-shadow-2025-10-09T08.jsonl"
        n_junk = 0
        for i in range(5000):
            base = copy.deepcopy([start(), trig(pool="A"), trig(pool="A", variant="fv"),
                                  pool(pool="A", ws=1, uval=1, tval=0, btd=bt(dropped_n=30, dropped=[["sigA", 0, 105]], cover_hi=250)),
                                  pool(pool="B"), hb(MID), {"type": "gap", "kind": "x"}])
            for _ in range(rng.randint(1, 3)):
                rec = rng.choice(base[1:])
                c, k = rng.choice([(c, k) for c, k in paths(rec) if k in T.ALLOWED_KEYS])
                c[k] = copy.deepcopy(rng.choice(junk))
                n_junk += 1
            fpath.write_text("".join(json.dumps(r) + "\n" for r in base))
            try:
                res = T.analyze(self.d, T0, MID + H)
                T.dumps(res)
                T.table(res)
            except Exception as e:  # noqa: BLE001
                self.fail(f"fixture {i} raised {e!r}: {json.dumps(base)[:600]}")
        self.assertGreater(n_junk, 5000)

    # change 9
    def test_emit_lag(self):
        pre = [trig(T0 - 3600_000, pool="old", t_detect_ms=T0 - 3600_000 - 40), trig(T0 - 2 * 86_400_000, pool="older", t_detect_ms=0),
               trig(T0 - 1000, pool="sealedold", t_detect_ms=T0 - 1000 - 9, sealed=True)]
        recs = [start()] + pre + [trig(pool="a", t_detect_ms=MID - 25), trig(pool="b", t_detect_ms=MID - 75, tval=None, btd=bt(status="tip_pending", dropped=None)),
                                  trig(pool="c", variant="fv", t_detect_ms=MID - 1)]
        m = self.analyze(recs)["item9_timing_memory"]
        self.assertEqual((m["emit_lag_ms"]["all_pv"]["n"], m["emit_lag_ms"]["all_pv"]["max"]), (2, 75))
        self.assertEqual(m["emit_lag_ms"]["tipauth_ok"]["max"], 25)
        self.assertEqual(m["emit_lag_ms"]["tipauth_not_ok"]["max"], 75)
        self.assertEqual((m["emit_lag_ms_24h_before_origin"]["n"], m["emit_lag_ms_24h_before_origin"]["max"]), (1, 40))
        self.assertIn("t_detect_ms", T.ALLOWED_KEYS)

    # change 10
    def test_covered_hours_subtracts_holes(self):
        recs = [start(T0 + 1000), hb(T0 + 1000 + 60_000), trig(T0 + 2000 + 60_000), hb(T0 + 1000 + 25 * H)]
        r = self.analyze(recs, until=T0 + 1000 + 25 * H)
        w = r["window"]
        self.assertAlmostEqual(w["elapsed_h"], 25.0, 3)
        self.assertLess(w["covered_h"], 0.2)
        self.assertEqual(w["n_holes"], 1)
        self.assertFalse(self.th(r, "duration_24h_and_10_nogap_pv_ws_ge1")["met"])

    def test_covered_hours_full_and_boundary(self):
        w = T.covered(T0, T0 + 24 * H, [T0 + k * 300_000 for k in range(1, 289)])
        self.assertEqual((w["covered_h"], w["n_holes"], w["hb_max_gap_ms"]), (24.0, 0, 300_000))
        w = T.covered(T0, T0 + 1_200_001, [T0 + 600_000, T0 + 1_200_000])  # 600,000 is not a hole; the last 1 ms is not either
        self.assertEqual(w["n_holes"], 0)
        w = T.covered(T0, T0 + 600_001, [])
        self.assertEqual(w["n_holes"], 1)

    # change 11 and the schedule
    def test_schedule_and_off_schedule_refused(self):
        import contextlib
        import io
        self.assertEqual(T.schedule(1000)[:5], [1000 + 6 * H, 1000 + 24 * H, 1000 + 36 * H, 1000 + 48 * H, 1000 + 60 * H])
        for u, ok in [(6, True), (24, True), (36, True), (48, True), (60, True), (72, True), (12, False), (30, False), (18, False), (25, False)]:
            self.assertEqual(T.on_schedule(T0, T0 + u * H), ok, u)
        self.assertFalse(T.on_schedule(T0, T0 + 24 * H + 1))
        write(self.d, [start(sha=SHA)])
        so = io.StringIO()
        with contextlib.redirect_stdout(so):
            rc = T.main(["--shadow-dir", str(self.d), "--since-ms", str(T0), "--until-ms", str(T0 + 7 * H), "--expect-code-sha", SHA], now_ms=T0 + 100 * H)
        self.assertEqual(rc, 4)
        self.assertFalse(json.loads(so.getvalue())["verdict"]["tool_rules_clear"])
        so = io.StringIO()
        with contextlib.redirect_stdout(so):
            rc = T.main(["--schedule", "--since-ms", "1791797643640"])
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(so.getvalue())["until_ms_instants"][:4], [1791819243640, 1791884043640, 1791927243640, 1791970443640])

    def test_instant_not_yet_120s_in_the_past_refused(self):
        import contextlib
        import io
        write(self.d, [start(sha=SHA), trig(), hb(MID)])
        u = T0 + 24 * H
        args = ["--shadow-dir", str(self.d), "--since-ms", str(T0), "--until-ms", str(u), "--expect-code-sha", SHA]
        out = self.d / "early.json"
        so = io.StringIO()
        with contextlib.redirect_stdout(so):
            rc = T.main(args + ["--out", str(out)], now_ms=u + T.SETTLE_MS - 1)
        self.assertEqual(rc, 4)
        self.assertFalse(out.exists())
        j = json.loads(so.getvalue())
        self.assertEqual(j["verdict"]["blocking_reasons"], ["until_ms_not_reached"])
        so = io.StringIO()
        with contextlib.redirect_stdout(so):
            rc = T.main(args + ["--out", str(out)], now_ms=u + T.SETTLE_MS)
        self.assertEqual(rc, 0)
        self.assertTrue(out.exists())

    def test_sealed_part_of_c7f_fails_closed(self):
        for k, vals in (("sealed", [1, 0, "yes", True]), ("h5_look2_sealed", ["true", 1, True])):
            for v in vals:
                r = trig()
                r[k] = v
                self.assertEqual(T.would_pass(r), (False, "sealed_record"), (k, v))
        for v in (False, None):
            for k in ("sealed", "h5_look2_sealed"):
                r = trig()
                r[k] = v
                self.assertEqual(T.would_pass(r), (True, "ok"), (k, v))
        self.assertEqual(T.would_pass(trig()), (True, "ok"))

    def test_earlier_start_before_t0_is_not_a_second_start(self):
        t0, early = 1791797643640, 1791796005847
        write(self.d, [start(early, SHA)], hour_ms=early - early % H)
        write(self.d, [start(t0, SHA), trig(t0 + 5000)], hour_ms=t0 - t0 % H)
        r = T.analyze(self.d, t0, t0 + 6 * H, expect_code_sha=SHA)
        self.assertEqual(r["starts"]["n_starts_after_t0"], 1)
        self.assertEqual(r["starts"]["origin_t_ms"], t0)
        self.assertTrue(self.th(r, "single_start_after_t0")["met"])
        self.assertTrue(self.th(r, "origin_code_sha_matches")["met"])

    def test_exit_codes_no_start_and_missing_dir(self):
        import contextlib
        import io
        so = io.StringIO()
        with contextlib.redirect_stdout(so):
            rc = T.main(["--shadow-dir", str(self.d / "nope"), "--since-ms", str(T0), "--until-ms", str(T0 + 24 * H), "--expect-code-sha", SHA], now_ms=T0 + 100 * H)
        self.assertEqual(rc, 3)
        self.assertFalse(json.loads(so.getvalue())["verdict"]["tool_rules_clear"])
        write(self.d, [trig()])
        so = io.StringIO()
        with contextlib.redirect_stdout(so):
            rc = T.main(["--shadow-dir", str(self.d), "--since-ms", str(T0), "--until-ms", str(T0 + 24 * H), "--expect-code-sha", SHA], now_ms=T0 + 100 * H)
        self.assertEqual(rc, 2)
        j = json.loads(so.getvalue())
        self.assertIn("error", j)
        self.assertFalse(j["verdict"]["tool_rules_clear"])

    # change 12
    def test_boost_src_split(self):
        recs = [start(), trig(pool="a", boost_src="pda"), trig(pool="b", boost_src="none"), trig(pool="c", boost_src="none", variant="fv"), trig(pool="d", boost_src="behavioural")]
        w = self.analyze(recs)["item6_would_pass"]
        self.assertEqual(w["would_pass_by_boost_src"], {"pda": 1})
        self.assertEqual(w["would_pass_pv_by_boost_src"], {"pda": 1})
        self.assertFalse(T.conditions(T.view_record(trig(boost_src="none"), "trigger"))["C3"])

    # reviewer notes
    def test_non_utf8_line_counted_not_raised(self):
        write(self.d, [start()])
        with open(next(self.d.iterdir()), "ab") as fh:
            fh.write(b'{"type": "hb", "t_ms": 1, "x": "\xff\xfe"}\n')
        r = T.analyze(self.d, T0)
        self.assertEqual((r["skipped"]["bad_line"], r["skipped"]["bad_line_not_utf8"]), (1, 1))

    def test_snapshot_ignores_later_appends(self):
        import collections
        write(self.d, [start(), trig()])
        snap = T.snapshot(T.hour_files(self.d, T0, None))
        write(self.d, [start(T0 + 9999, "late"), trig(pool="late")])
        self.assertEqual(len(list(T.iter_records(snap, collections.Counter(), None))), 2)

    def test_union_errors_absent_counter_is_zero_sample(self):
        h1 = hb(MID)
        del h1["counters"]["union_errors"]
        r = self.analyze([start(), h1, {"type": "hb", "t_ms": MID + 1000}, hb(MID + 2000, ue=2)])
        c = r["item4_consistency"]
        self.assertEqual((c["union_errors_hb_samples"], c["union_errors_hb_absent_counter_as_0"], c["checks"]["union_errors"]["n"]), (2, 1, 2))


class TestRound3(Base):
    """PR #613 round 3: quant-proof PR #614 comment 6121641942 R1, R2 (C7 a-f), R12 (one importable predicate)."""

    def wp(self, **kw):
        return T.would_pass(trig(**kw))

    def test_base_record_passes(self):
        self.assertEqual(self.wp(), (True, "ok"))

    def test_c3_allowlist(self):
        for src in ("pda", "event_authority"):
            self.assertEqual(self.wp(boost_src=src), (True, "ok"))
        for src in ("none", "behavioural", "x", "", None, 1, True):
            self.assertEqual(self.wp(boost_src=src), (False, "boost_src"), src)
        r = trig()
        del r["boost_src"]
        self.assertEqual(T.would_pass(r), (False, "boost_src"))

    def test_c7_each_subcondition_alone(self):
        d2 = [["sigA", 0, 105], ["sigB", 1, 106]]
        cases = {
            "margin": [dict(btd=bt(margin_slots=3)), dict(btd=bt(margin_slots=5)), dict(btd=bt(margin_slots=None)), dict(btd=bt(margin_slots=4.0)),
                       dict(btd=bt(margin_slots=True)), dict(btd=bt(margin_slots="4"))],
            "slots": [dict(slot=153), dict(slot=None), dict(slot=True), dict(btd=bt(cover_lo=-1), bud=bu(tip_first_slot=-1)),
                      dict(btd=bt(cover_hi=None)), dict(btd=bt(dropped_last_slot=None)), dict(btd=bt(dropped_last_slot=151, dropped=[["sigA", 0, 105]])),
                      dict(btd=bt(dropped_last_slot=104, dropped_n=1))],
            "cover": [dict(bud=bu(tip_first_slot=99)), dict(bud=bu(tip_first_slot=None)), dict(bud=bu(tip_hw_slot_global=155)), dict(bud=bu(tip_hw_slot_global=None)),
                      dict(bud=bu(tip_hw_slot_global=True))],
            "dropped_list": [dict(btd=bt(dropped=None)), dict(btd=bt(dropped=[])), dict(btd=bt(dropped=[["sigA", 0, 105], ["sigB", 0, 105]])),
                             dict(btd=bt(dropped=[["", 0, 105]])), dict(btd=bt(dropped=[[1, 0, 105]])), dict(btd=bt(dropped=[["sigA", "0", 105]])),
                             dict(btd=bt(dropped=[["sigA", 0, 105, 9]])), dict(btd=bt(dropped=[["sigA", 0, True]])), dict(btd=bt(dropped=("sigA", 0, 105))),
                             dict(btd=bt(dropped=[["sigA", 0, 104]])), dict(btd=bt(dropped=[["sigA", 0, 106]])),
                             dict(btd=bt(dropped=[["sigA", 0, 105], ["sigA", 0, 105]], dropped_n=2, dropped_last_slot=105)),
                             dict(btd=bt(dropped=[["sigA", 0, 105], ["sigB", 0, 106]], dropped_n=2, dropped_last_slot=107)),
                             dict(btd=bt(dropped=[["sigA", 0, 106], ["sigB", 0, 105]], dropped_n=2, dropped_last_slot=106))],
            "union_ne_ws": [dict(uval=2), dict(uval=0), dict(uval=None), dict(uval=True), dict(uval=1.0)],
            "sealed_record": [dict(h5_look2_sealed=True), dict(sealed=True)],
            "variant_not_pv": [dict(variant="fv"), dict(variant=None), dict(variant="PV")],
        }
        for reason, variants in cases.items():
            for kw in variants:
                ok, why = self.wp(**kw)
                self.assertFalse(ok, (reason, kw))
                self.assertEqual(why, reason, kw)
        # a consistent two-entry list passes; event_index None is a value
        self.assertEqual(self.wp(btd=bt(dropped=d2, dropped_n=2, dropped_last_slot=106)), (True, "ok"))
        self.assertEqual(self.wp(btd=bt(dropped=[["sigA", None, 105]])), (True, "ok"))

    def test_slot_boundaries(self):
        self.assertEqual(self.wp(slot=154, btd=bt(cover_hi=150), bud=bu(tip_hw_slot_global=154)), (True, "ok"))
        self.assertEqual(self.wp(slot=154, btd=bt(cover_hi=151), bud=bu(tip_hw_slot_global=155))[1], "slots")  # cover_hi = slot - 3
        self.assertEqual(self.wp(btd=bt(dropped_first_slot=105, dropped_last_slot=150, dropped=[["a", 0, 105], ["b", 0, 150]], dropped_n=2)), (True, "ok"))
        # the tip high-water below the trigger slot sets cover_hi
        self.assertEqual(self.wp(slot=500, btd=bt(cover_hi=150), bud=bu(tip_hw_slot_global=154)), (True, "ok"))
        self.assertEqual(self.wp(slot=500, btd=bt(cover_hi=150), bud=bu(tip_hw_slot_global=160))[1], "cover")

    def test_fv_never_would_pass(self):
        self.assertEqual(self.wp(variant="fv"), (False, "variant_not_pv"))
        r = self.run_tool([start(), trig(pool="a"), trig(pool="b", variant="fv"), trig(pool="c", variant="fv")])
        w = r["item6_would_pass"]
        self.assertEqual((w["would_pass_n"], w["would_pass_pv_n"], w["fv_not_eligible_variant_n"]), (1, 1, 2))

    def test_reason_order_is_first_failing_in_executor_order(self):
        self.assertEqual(T.GATE_REASONS[1:], ("ws_not_ge1", "gap", "union_missing", "tipauth_missing", "union_status", "tipauth_status", "value_not_zero",
                                              "tip_only", "both_diff", "dropped_boost", "boost_src", "dropped_n", "kept_ws_only_sig_on_tip", "orphaned_by_drop",
                                              "nosettle", "dropped_first_slot", "margin", "slots", "cover", "dropped_list", "union_ne_ws", "sealed_record",
                                              "variant_not_pv"))
        self.assertEqual(T.would_pass(None), (False, "not_a_record"))
        self.assertEqual(T.would_pass([1]), (False, "not_a_record"))
        self.assertEqual(self.wp(ws=0, gap=True, variant="fv", boost_src="none")[1], "ws_not_ge1")
        self.assertEqual(self.wp(gap=True, variant="fv", boost_src="none")[1], "gap")
        self.assertEqual(self.wp(variant="fv", boost_src="none", btd=bt(margin_slots=3))[1], "boost_src")
        self.assertEqual(self.wp(variant="fv", btd=bt(margin_slots=3), uval=9)[1], "margin")
        self.assertEqual(self.wp(variant="fv", uval=9)[1], "union_ne_ws")
        self.assertEqual(self.wp(variant="fv", h5_look2_sealed=True)[1], "sealed_record")
        r = trig()
        r["base_union"] = None
        self.assertEqual(T.would_pass(r)[1], "union_missing")
        r = trig()
        r["base_tipauth"] = []
        self.assertEqual(T.would_pass(r)[1], "tipauth_missing")
        self.assertEqual(self.wp(bud=bu(status="x"))[1], "union_status")
        self.assertEqual(self.wp(btd=bt(status="x"))[1], "tipauth_status")
        self.assertEqual(self.wp(tval=1)[1], "value_not_zero")
        for kw, why in [(dict(bud=bu(tip_only=1)), "tip_only"), (dict(bud=bu(both_diff=1)), "both_diff"), (dict(btd=bt(dropped_boost_n=1)), "dropped_boost"),
                        (dict(btd=bt(dropped_n=6)), "dropped_n"), (dict(btd=bt(kept_ws_only_sig_on_tip=1)), "kept_ws_only_sig_on_tip"),
                        (dict(btd=bt(orphaned_by_drop_n=1)), "orphaned_by_drop"), (dict(btd=bt(tipauth_nosettle=1)), "nosettle"),
                        (dict(btd=bt(dropped_first_slot=100)), "dropped_first_slot")]:
            self.assertEqual(self.wp(**kw)[1], why, kw)

    def test_view_and_function_agree_and_deterministic(self):
        import random
        rng = random.Random(1)
        junk = [None, True, 0, 1, 4, -1, 99, 105, 150, 154, 200, "pda", "none", [], {}, [["sigA", 0, 105]], 1.5]
        keys = ["gap", "slot", "boost_src", "variant", "base_breaks_unresolved_settled", T.UNION_FIELD, T.TIPAUTH_FIELD, "h5_look2_sealed", "sealed"]
        for _ in range(3000):
            r = copy.deepcopy(trig())
            for _ in range(rng.randint(0, 3)):
                if rng.random() < 0.5:
                    r[rng.choice(keys)] = rng.choice(junk)
                else:
                    sub = r[rng.choice(["base_union", "base_tipauth"])]
                    sub[rng.choice(sorted(sub))] = rng.choice(junk)
            ok, why = T.would_pass(r)
            c = T.conditions(T.view_record(r, "trigger"))
            self.assertEqual(ok, T.would_pass_view(c))
            self.assertEqual(ok, why == "ok")
            self.assertEqual((ok, why), T.would_pass(copy.deepcopy(r)))

    def test_funnel_and_c7_parts_report(self):
        recs = [start(), trig(pool="ok"),
                trig(pool="m", btd=bt(margin_slots=3)),
                trig(pool="u", uval=2),
                trig(pool="mu", btd=bt(margin_slots=3), uval=2),
                trig(pool="n", boost_src="none"),
                trig(pool="fv", variant="fv"),
                trig(pool="dl", btd=bt(dropped=[["sigA", 0, 104]]))]
        w = self.run_tool(recs)["item6_would_pass"]
        self.assertEqual(w["would_pass_n"], 1)
        self.assertEqual([f["after"] for f in w["funnel"]], ["all_triggers"] + T.COND_NAMES)
        self.assertEqual(w["funnel"][-1]["n"], 1)
        c7 = w["c7_parts"]
        self.assertEqual(c7["n_c1_c5"], 6)  # ok, m, u, mu, fv, dl (the boost_src none trigger is removed at C3)
        self.assertEqual((c7["fail_C7a"], c7["fail_C7e"], c7["fail_C7d"], c7["fail_C7f"]), (2, 2, 1, 1))
        self.assertEqual((c7["fail_only_C7a"], c7["fail_only_C7e"], c7["fail_only_C7d"], c7["fail_only_C7f"]), (1, 1, 1, 1))
        self.assertEqual(c7["fail_any"], 5)
        self.assertEqual(c7["fail_all_six"], 0)
        self.assertEqual((w["leave_one_out_removes"]["C3"], w["leave_one_out_removes"]["C7a"], w["leave_one_out_removes"]["C7f"]), (1, 1, 1))

    def test_sealed_records_still_skipped_by_the_tool(self):
        r = self.run_tool([start(), trig(pool="s", h5_look2_sealed=True), trig(pool="t", sealed=True), trig(pool="ok")])
        self.assertEqual(r["item6_would_pass"]["would_pass_n"], 1)
        self.assertEqual(r["skipped"]["h5_look2_sealed_trigger"] + r["skipped"]["sealed_trigger"], 2)

    def test_junk_never_raises_in_predicate(self):
        for junk in (None, 0, "x", [], {}, {"base_union": 5, "base_tipauth": "x"}, {"base_union": {}, "base_tipauth": {"dropped": [None, 3, [1]]}},
                     trig(btd=bt(dropped=[[["x"], 0, 1]])), trig(btd=bt(dropped=[["a", [1], 1]]))):
            self.assertFalse(T.would_pass(junk)[0])

    def test_c7_key_in_allow_list(self):
        self.assertIn("tip_hw_slot_global", T.ALLOWED_KEYS)
        Tracking.log = set()
        self.run_tool([start(), trig()], hook=Tracking)
        self.assertEqual(Tracking.log - T.ALLOWED_KEYS, set())
        self.assertIn("tip_hw_slot_global", Tracking.log)


if __name__ == "__main__":
    unittest.main()
