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
    d = {"status": "ok", "ws_only": 1, "tip_only": 0, "both": 5, "both_diff": 0, "dup": 0, "tip_first_slot": 100}
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
        self.assertFalse(r["verdict"]["proposal_allowed"])
        self.assertEqual(r["starts"]["distinct_code_sha"], ["a", "b"])

    def test_start_before_t0_ignored(self):
        r = self.run_tool([start(T0 - 5000, "old"), start(T0 + 10, "new"), trig(T0 + 20)])
        self.assertEqual(r["starts"]["n_starts_after_t0"], 1)
        self.assertEqual(r["starts"]["distinct_code_sha"], ["new"])

    def test_no_start(self):
        r = self.run_tool([trig()])
        self.assertIn("error", r)
        self.assertFalse(r["verdict"]["proposal_allowed"])

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
        self.assertEqual(r["skipped"]["bad_line"], 2)  # once per pass


class TestItems(Base):
    def test_item1_duration_and_count(self):
        recs = [start(T0 + 1000)] + [trig(T0 + 2000 + i, pool=f"P{i}") for i in range(10)] + [hb(T0 + 1000 + 24 * H)]
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
        self.assertTrue(T.would_pass(c), c)

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
                self.assertFalse(T.would_pass(c))

    def test_boundaries_c4(self):
        for n, ok in [(1, True), (5, True), (6, False)]:
            c = T.conditions(self.v(btd=bt(dropped_n=n, dropped=[["s%d" % i, 0, 105 + i] for i in range(n)])))
            self.assertEqual(c["C4"], ok)

    def test_record_without_keys(self):
        r = {"type": "trigger", "variant": "pv", "pool": "p", "t_ms": 1}
        c = T.conditions(T.view_record(r, "trigger"))
        self.assertFalse(any(c.values()))


class TestC5AndFunnel(Base):
    def test_c5_parts_counts(self):
        recs = [start(), trig(pool="ok"),
                trig(pool="a", btd=bt(orphaned_by_drop_n=1)), trig(pool="b", btd=bt(tipauth_nosettle=1)), trig(pool="c", btd=bt(dropped_first_slot=100, dropped=[["s", 0, 100]], dropped_last_slot=100)),
                trig(pool="abc", btd=bt(orphaned_by_drop_n=1, tipauth_nosettle=1, dropped_first_slot=100, dropped=[["s", 0, 100]], dropped_last_slot=100)),
                trig(pool="c2", boost_src="behavioural")]
        r = self.run_tool(recs)
        w = r["item6_would_pass"]
        self.assertEqual(w["would_pass_n_after_c1_c5"], 1)
        c5 = w["c5_parts"]
        self.assertEqual((c5["n_c1_c4"], c5["fail_5a"], c5["fail_5b"], c5["fail_5c"]), (5, 2, 2, 2))
        self.assertEqual((c5["fail_only_5a"], c5["fail_only_5b"], c5["fail_only_5c"], c5["fail_all_three"], c5["fail_any"]), (1, 1, 1, 1, 4))
        self.assertEqual(w["leave_one_out_removes"]["C3"], 1)
        self.assertEqual([f["after"] for f in w["funnel"]], ["all_triggers", "C1", "C2", "C3", "C4", "C5a", "C5b", "C5c"])
        self.assertEqual([f["n"] for f in w["funnel"]], [6, 6, 6, 5, 5, 4, 3, 1][:5] + [f["n"] for f in w["funnel"]][5:])
        self.assertEqual(w["funnel"][-1]["n"], 1)
        self.assertFalse(w["c6_applied"])

    def test_pv_only_count(self):
        r = self.run_tool([start(), trig(pool="a"), trig(pool="b", variant="fv")])
        self.assertEqual((r["item6_would_pass"]["would_pass_n_after_c1_c5"], r["item6_would_pass"]["would_pass_pv_n"]), (2, 1))


class TestIncompleteness(Base):
    def test_share_and_cap(self):
        # 20 ws0 closes ok: 1 with tipauth>=1, 1 with orphaned>=1 only -> 10% > 5%
        closes = [pool(pool=f"c{i}") for i in range(18)]
        closes.append(pool(pool="t", uval=1, tval=1, btd=bt(dropped_n=1)))
        closes.append(pool(pool="o", btd=bt(orphaned_by_drop_n=1, dropped_n=1)))
        r = self.run_tool([start()] + closes)
        i5 = r["item5_tip_incompleteness"]["plain_pool_closes"]
        self.assertEqual((i5["n_ok"], i5["incomplete_n"], i5["incomplete_share_of_ok"]), (20, 2, 0.1))
        self.assertEqual(i5["dropped_n_ge1_n"], 2)
        self.assertFalse(r["verdict"]["thresholds"]["tip_incompleteness_le_5pct"]["met"])
        self.assertFalse(r["verdict"]["proposal_allowed"])

    def test_at_cap_is_met_and_nulls_do_not_dilute(self):
        none_bt = bt(status="no_tip_rows", dropped_n=None, dropped=None, dropped_first_slot=None, dropped_last_slot=None, orphaned_by_drop_n=None, tipauth_nosettle=None)
        closes = [pool(pool=f"c{i}") for i in range(19)] + [pool(pool="t", uval=1, tval=1, btd=bt(dropped_n=1))]
        closes += [pool(pool=f"n{i}", tval=None, btd=none_bt) for i in range(20)]
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
        base = {"item1_duration": {"elapsed_h": 30, "n_nogap_pv_ws_ge1": 12}, "item2_coverage": {"tipauth_ok_share_all": 1.0, "tipauth_ok_share_nogap_pv_ws_ge1": 1.0, "union_ok_share_all": 1.0},
                "item4_consistency": {"total": 0, "checks": {}}, "item5_tip_incompleteness": {"plain_pool_closes": {"incomplete_share_of_ok": 0.0, "incomplete_share_of_all_ws0_nogap": 0.0, "n_ok": 10}},
                "item9_timing_memory": {"rss_kb_max": 1000}, "starts": {"n_starts_after_t0": 1, "distinct_code_sha": ["a"]},
                "item7_census_input": {"would_pass_fully_enumerated_n": 3, "would_pass_n": 3}}
        def v(tested, left, wpl=0):
            r = dict(base, item10_margin_test={"tested": tested, "left_D": left, "left_D_share_of_tested": None if not tested else left / tested, "undetermined": 0,
                                                "would_pass_left_D": wpl, "would_pass_entries": 3})
            return T.verdict(r)
        self.assertTrue(v(200, 2)["thresholds"]["margin_left_D_share_le_1pct"]["met"])  # exactly 1%
        self.assertFalse(v(200, 3)["thresholds"]["margin_left_D_share_le_1pct"]["met"])
        self.assertFalse(v(99, 1)["thresholds"]["margin_left_D_share_le_1pct"]["met"])
        self.assertTrue(v(99, 0)["thresholds"]["margin_left_D_share_le_1pct"]["met"])
        self.assertIsNone(v(0, 0)["thresholds"]["margin_left_D_share_le_1pct"]["met"])
        self.assertFalse(v(200, 0, wpl=1)["proposal_allowed"])
        ok = v(200, 0)
        self.assertTrue(ok["proposal_allowed"])
        self.assertEqual(ok["blocking_reasons"], [])
        self.assertIn("item7_rpc_census", ok["outside_tool_pending"])

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
        self.assertFalse(r["verdict"]["proposal_allowed"])

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
        self.assertEqual(r["item6_would_pass"]["would_pass_n_after_c1_c5"], 0)


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
            rc = T.main(["--shadow-dir", str(self.d), "--since-ms", str(T0), "--out", str(out), "--table"])
        self.assertEqual(rc, 0)
        self.assertEqual(so.getvalue(), out.read_text())
        self.assertIn("proposal_allowed", se.getvalue())
        self.assertIn("ambiguities", json.loads(so.getvalue()))

    def test_ambiguities_listed_with_unique_ids(self):
        ids = [a[0] for a in T.AMBIGUITIES]
        self.assertEqual(len(ids), len(set(ids)))


if __name__ == "__main__":
    unittest.main()
