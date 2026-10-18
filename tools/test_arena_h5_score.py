"""tools/arena_h5_score.py: the arena H5 scorer on the DEC-031 deciding stack. Synthetic engine fixtures (stdlib only), plus one replay of an hour of exploration tape (skipped where
pandas or the tape is absent)."""
from __future__ import annotations

import collections
import contextlib
import copy
import io
import json
import math
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools import arena_h5_cost as cost
from tools import arena_h5_score as sc
from tools import h5_shadow as h5
from tools.test_h5_shadow_fresh_forward import SHA, fresh_engine, pool_tape, run_pools

T0 = 1_800_000_000  # the synthetic pools' block time (2027-01-15T08:00:00Z)
START_MS = T0 * 1000 - 5_000
V1, V2 = "h5v1-boostfloor-a1", "h5v2-boostclock-a1"


def records(n=1, start_ms=START_MS, mutate=None, sha=SHA, **start_kw):
    """The fresh-forward engine's records for n synthetic pools, behind a start record."""
    eng, out = fresh_engine(start_ms)
    run_pools(eng, *[pool_tape(f"Pool{i:04d}", 1000 + 5000 * i, T0 + 1600 * i, filler=True) for i in range(n)])
    start = {"type": "start", "t_ms": start_ms, "start_ms": start_ms, "code_sha": sha, "fresh_forward": "ARENA", "dirty": False, **start_kw}
    recs = [start] + out
    if mutate:
        mutate(recs)
    return recs


def entry(**kw):
    return {"frozen_sha": SHA, "clock_start_ms": START_MS, "registered_at_ms": START_MS - 1_000, "window_end_ms": None, **kw}


def of(recs, typ, pool=None):
    return [r for r in recs if r["type"] == typ and (pool is None or r.get("pool") == pool) and r.get("variant", "pv") == "pv"]


class Base(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(dir="/tmp", prefix="arena-score-")).resolve()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        p = mock.patch.object(h5, "ARENA_ROOT", str(self.root / "arena"))
        p.start()
        self.addCleanup(p.stop)
        self.arena = self.root / "arena"
        self.ledger = self.arena / "ledger"

    def shadow_dir(self, recs, name="h5-shadow-test"):
        d = self.arena / name
        d.mkdir(parents=True, exist_ok=True)
        with open(d / "h5-shadow-2027-01-15T08.jsonl", "w") as fh:
            for r in recs:
                fh.write(json.dumps(r) + "\n")
        return d

    def book(self, recs, bot=V1, ent=None, name="h5-shadow-test"):
        counts = collections.Counter()
        d = sc.ShadowDir(self.shadow_dir(recs, name), counts)
        return sc.score_book(bot, ent or entry(), [d])

    def rows(self, recs, bot=V1, **kw):
        return self.book(recs, bot, **kw).rows


def legs_of(rows, stake=0.3):
    return next(r for r in rows if r["stake_sol"] == stake)["legs"]


class StackedPricingTests(Base):
    def test_rows_fields_and_both_books(self):
        recs = records()
        for bot in (V1, V2):
            res = self.book(recs, bot)
            self.assertEqual(len(res.rows), 3)  # three stakes of one pool
            trig = of(recs, "trigger")[0]
            for r in res.rows:
                self.assertEqual(r["trade_id"], f"{bot}:Pool0000:{'%.2f' % r['stake_sol']}")
                self.assertEqual((r["bot_id"], r["frozen_sha"], r["prog"], r["pool"], r["mint"]), (bot, SHA, "pumpswap", "Pool0000", "MintPool0000"))
                self.assertEqual(r["decision_ms"], trig["t_detect_ms"])
                self.assertGreater(r["exit_ms"], r["decision_ms"])
                self.assertEqual(set(r["legs"]), {"flat_1p3", "flat_1p9", "press_1p3", "press_1p9"})
                json.dumps(r, allow_nan=False)
            self.assertEqual(sorted({r["stake_sol"] for r in res.rows}), [0.1, 0.25, 0.3])
            self.assertEqual(res.counts["counted"], 1)

    def test_the_deciding_legs_are_the_dec_031_stack_repriced_from_the_strip(self):
        recs = records()
        o, trig, strip = of(recs, "outcome")[0], of(recs, "trigger")[0], of(recs, "strip")[0]
        rows = self.rows(recs)
        sps, el = float(o["sps"]), sc.exit_lag_slots(float(o["sps"]))
        spot = float(trig["q_trigger_sol"]) * 1e9 / (float(trig["base_pre"]) + float(trig["sell_token_raw"]))
        for r in rows:
            for shadow_leg, sfx, lat in sc.LEGS:
                land = o["trigger_slot"] + math.ceil(lat / sps - 1e-9)  # age 0 here
                xtrig = o["exit"]["trigger_slot"]
                qe, be = next((x[1], x[3]) for x in strip["rows"] if x[0] > land)
                qx, bx = next((x[1], x[3]) for x in strip["rows"] if x[0] > xtrig + el)
                want = cost.deciding_round_trip(r["stake_sol"] * 1e9, qe, be, spot, qx, bx)
                leg = r["legs"][f"flat_{sfx}"]
                self.assertAlmostEqual(leg["nofail_lamports"], want["pnl"], delta=1e-6)
                self.assertEqual(leg["state"], want["state"])
                self.assertAlmostEqual(leg["net_lamports"], cost.flat_leg(want["pnl"], want["state"]), delta=1e-6)
                self.assertEqual(set(leg["sc"]), {"net_lamports", "nofail_lamports", "state"})  # the second strand rule rides along
                press = r["legs"][f"press_{sfx}"]
                self.assertTrue(press["provisional"])
                self.assertEqual(press["intercept_kind"], "frozen_report_only")

    def test_at_age_zero_the_landing_slots_and_pressure_equal_the_shadows_own(self):
        recs = records()
        o, strip = of(recs, "outcome")[0], Strip_of(recs)
        trig = of(recs, "trigger")[0]
        age = sc.trigger_age_ms(trig)
        self.assertEqual(age, 0)
        for shadow_leg, sfx, lat in sc.LEGS:
            land = sc.landing_slot(o["trigger_slot"], o["sps"], age, lat)
            self.assertEqual(land, o["legs"][shadow_leg]["landing_slot"])
            ssb, nb = strip.pressure_at(land, o["sps"])
            self.assertEqual((ssb, nb), (o["legs"][shadow_leg]["ssb"], o["legs"][shadow_leg]["nb_lamports"]))
            q, b, _ = strip.state_at(land)
            self.assertAlmostEqual(q / 1e9, o["legs"][shadow_leg]["q_sol"], places=6)

    def test_landing_is_measured_from_the_decision_instant_not_the_trigger_print(self):
        def late(recs):
            for r in recs:
                if r["type"] == "trigger":
                    r["t_detect_ms"] = r["block_time"] * 1000 + 1_300
        base, aged = records(), records(mutate=late)
        a, b = self.rows(base)[0], self.rows(aged)[0]
        self.assertEqual(sc.trigger_age_ms(of(aged, "trigger")[0]), 1_300)
        self.assertGreater(b["meta"]["landing_slots"]["1p9"], a["meta"]["landing_slots"]["1p9"])
        sps = of(base, "outcome")[0]["sps"]
        t = of(base, "outcome")[0]["trigger_slot"]
        self.assertEqual(b["meta"]["landing_slots"], {"1p3": t + math.ceil((1300 + 1300) / (sps * 1000) - 1e-9), "1p9": t + math.ceil((1300 + 1900) / (sps * 1000) - 1e-9)})
        self.assertEqual(sc.landing_slot(1000, 0.4, 0, 1.3), 1004)  # ceil(3.25)
        self.assertEqual(sc.landing_slot(1000, 0.2, 0, 1.3), 1007)  # ceil(6.5): never earlier than the stated latency
        self.assertEqual(sc.landing_slot(1000, 0.4, 3_500, 1.9), 1000 + math.ceil(5.4 / 0.4 - 1e-9))

    def test_exit_lag_is_ceil_and_at_least_two_slots(self):
        self.assertEqual([sc.exit_lag_slots(s) for s in (0.15, 0.2, 0.275, 0.3, 0.4, 0.55, 0.59)], [4, 3, 2, 2, 2, 2, 2])
        self.assertEqual(sc.exit_lag_slots(0.55), 2)  # ceil(1.0) = 1, raised to 2

    def test_the_55k_h5_stack_is_report_only_and_equals_the_shadow_at_its_two_stakes(self):
        recs = records()
        o = of(recs, "outcome")[0]
        for r in self.rows(recs):
            ro = r["report_only"]["h5_frozen_55k"]
            self.assertEqual(set(ro), {"flat_1p3", "flat_1p9", "press_1p3", "press_1p9"})
            self.assertNotIn("h5_frozen_55k", r["legs"])
            if r["stake_sol"] in (0.1, 0.25):
                for shadow_leg, sfx, _ in sc.LEGS:
                    want = o["legs"][shadow_leg]["net"]["%.2f" % r["stake_sol"] if r["stake_sol"] != 0.1 else "0.1"]["pnl_nofail_lamports"]
                    self.assertAlmostEqual(ro[f"flat_{sfx}"]["nofail_lamports"], want, delta=1e-3)
            self.assertNotAlmostEqual(r["legs"]["flat_1p9"]["nofail_lamports"], ro["flat_1p9"]["nofail_lamports"], delta=1.0)  # the deciding legs are a different stack

    def test_v2_recomputes_its_exit_at_the_deciding_landing_and_equals_the_shadows_at_age_zero(self):
        recs = records()
        bx = of(recs, "outcome_bx10")[0]
        rows = self.rows(recs, V2)
        r = rows[0]
        self.assertEqual(r["meta"]["exit_rule"], "bx10")
        self.assertEqual(r["meta"]["exit_trigger_slots"]["1p9"], bx["legs"]["binding"]["exit_trigger_slot"])
        self.assertEqual(r["meta"]["exit_trigger_slots"]["1p3"], bx["legs"]["primary"]["exit_trigger_slot"])
        v1 = self.rows(recs, V1)[0]
        self.assertLess(r["meta"]["exit_trigger_slots"]["1p9"], v1["meta"]["exit_trigger_slots"]["1p9"])  # the fixture fires early

    def test_a_bx10_error_or_a_missing_bx10_record_is_priced_at_v1s_exit_not_skipped(self):
        def err(recs):
            for r in recs:
                if r["type"] == "outcome_bx10":
                    r.pop("legs")
                    r["error"] = "RuntimeError('x')"
        def gone(recs):
            recs[:] = [r for r in recs if r["type"] != "outcome_bx10"]
        v1 = self.rows(records(), V1)
        for mut in (err, gone):
            res = self.book(records(mutate=mut), V2)
            self.assertEqual(len(res.rows), 3)
            self.assertEqual(res.counts["bx10_error_priced_v1"], 1)
            self.assertEqual(res.rows[0]["meta"]["exit_rule"], "v1_exit_after_bx10_error")
            for a, b in zip(res.rows, v1):
                self.assertEqual(a["legs"]["flat_1p9"]["nofail_lamports"], b["legs"]["flat_1p9"]["nofail_lamports"])
                self.assertEqual(a["meta"]["exit_trigger_slots"], b["meta"]["exit_trigger_slots"])


def Strip_of(recs):
    return sc.Strip(of(recs, "strip")[0])


class FeedDelayTests(Base):
    """quant-proof (#708): a feed at confirmed commitment lags the chain, so landing must be measured on the CHAIN clock (the trigger print's block_time), not from the receipt
    of the print: the feed delay is part of the age, recorded per row with the uncorrected and the corrected slots."""

    @staticmethod
    def detect_at(delta_ms):
        def m(recs):
            for r in recs:
                if r["type"] == "trigger":
                    r["t_detect_ms"] = r["block_time"] * 1000 + delta_ms
        return m

    def test_a_feed_two_seconds_behind_the_chain_lands_later_and_both_slots_are_recorded(self):
        recs = records(mutate=self.detect_at(2_000))
        o = of(recs, "outcome")[0]
        t, sps = o["trigger_slot"], o["sps"]
        m = self.rows(recs)[0]["meta"]
        self.assertEqual((m["feed_delay_ms"], m["age_raw_ms"], m["age_clamped"], m["age_clock"]), (2_000, 2_000, False, "block_time_s"))
        self.assertAlmostEqual(m["feed_delay_slots"], 2.0 / sps)  # d = 5 slots at 0.4 s
        self.assertEqual(m["landing_slots_uncorrected"], {"1p3": t + 4, "1p9": t + 5})  # from the trigger print: ceil(1.3 / 0.4), ceil(1.9 / 0.4)
        self.assertEqual(m["landing_slots"], {"1p3": t + math.ceil(3_300 / 400 - 1e-9), "1p9": t + math.ceil(3_900 / 400 - 1e-9)})  # 9 and 10: d = 5 slots later, ceil(latency) on top
        for sfx in ("1p3", "1p9"):
            self.assertGreaterEqual(m["landing_slots"][sfx] - m["landing_slots_uncorrected"][sfx], int(m["feed_delay_slots"]))
        # the book does not get the delay for free: the same trade with the print received instantly lands earlier
        m0 = self.rows(records(mutate=self.detect_at(0)))[0]["meta"]
        self.assertEqual(m0["landing_slots"], m0["landing_slots_uncorrected"])
        self.assertTrue(all(m["landing_slots"][k] > m0["landing_slots"][k] for k in m["landing_slots"]))

    def test_the_corrected_landing_is_never_earlier_than_the_uncorrected_one(self):
        for delta in (0, 1, 399, 400, 1_000, 1_999, 3_500):
            rows = self.book(records(mutate=self.detect_at(delta)), name=f"h5-shadow-d{delta}").rows
            m = rows[0]["meta"]
            for sfx in ("1p3", "1p9"):
                self.assertGreaterEqual(m["landing_slots"][sfx], m["landing_slots_uncorrected"][sfx], (delta, sfx))

    def test_a_negative_age_is_host_versus_chain_clock_skew_it_is_clamped_flagged_and_counted(self):
        res = self.book(records(mutate=self.detect_at(-700)))
        m = res.rows[0]["meta"]
        self.assertEqual((m["age_raw_ms"], m["feed_delay_ms"], m["age_clamped"]), (-700, 0, True))
        self.assertEqual(m["landing_slots"], m["landing_slots_uncorrected"])  # floored at the uncorrected slot, never earlier
        self.assertEqual(res.counts["age_clamped_to_zero"], 1)
        self.assertEqual(sc.summarize(V1, res, 0)["age_clamped_to_zero"], 1)

    def test_without_a_block_time_the_receipt_is_the_clock_and_the_row_and_the_counts_say_so(self):
        def nobt(recs):
            for r in recs:
                if r["type"] == "trigger":
                    r["block_time"] = None
                    r["t_detect_ms"] = r["t_recv_ms"] + 50
        res = self.book(records(mutate=nobt))
        m = res.rows[0]["meta"]
        self.assertEqual((m["age_clock"], m["feed_delay_ms"]), ("t_recv_ms", 50))
        self.assertEqual(res.counts["age_no_block_time"], 1)

    def test_the_trigger_age_limit_uses_the_same_chain_clock(self):
        self.assertEqual(sc.trigger_age_ms({"t_detect_ms": 10_000_000, "block_time": 9_996, "t_recv_ms": 9_999_999}), 4_000)
        self.assertEqual(sc.raw_trigger_age_ms({"t_detect_ms": 10_000_000, "block_time": 10_001, "t_recv_ms": 0}), -1_000)
        self.assertEqual(sc.trigger_age_ms({"t_detect_ms": 10_000_000, "block_time": 10_001, "t_recv_ms": 0}), 0)


class ExclusionTests(Base):
    def excluded(self, mutate, reason, bot=V1):
        res = self.book(records(mutate=mutate), bot)
        self.assertEqual(res.rows, [], reason)
        self.assertEqual([e["reason"] for e in res.exclusions], [reason])
        self.assertEqual(res.counts[f"excluded_{reason}"], 1)
        self.assertEqual(len(res.decisions), 1)  # an excluded trade is still an attempt for the read instant
        return res

    def test_a_gap_flagged_pool_is_excluded_and_counted_not_scored(self):
        def flag(gaps, where=("outcome",)):
            def m(recs):
                for r in recs:
                    if (r["type"] == "trigger" and "trigger" in where or r["type"] == "outcome" and "outcome" in where) and r["variant"] == "pv":
                        r["gap"], r["gaps"] = True, gaps
            return m
        inside = [{"kind": "slot_jump", "from_slot": 1430, "to_slot": 1500, "missed_slots": 69}]
        self.excluded(flag(inside), "gap_flag")  # between the decision (slot 1200) and the exit landing (1827)
        self.excluded(flag([{"kind": "slot_jump", "from_slot": 1100, "to_slot": 1190}]), "gap_flag")  # before the decision: the pool's state at the decision is suspect
        self.excluded(flag([{"kind": "socket_reconnect", "delta_reconnects": 1}]), "gap_flag")  # no slot recorded: counted as inside
        self.excluded(flag([]), "gap_flag")  # flagged, no record of where
        self.excluded(flag(inside, ("trigger",)), "gap_flag")  # flagged already at the trigger
        after = [{"kind": "slot_jump", "from_slot": 1900, "to_slot": 2300}]  # the pool's last seconds, after the exit landing: not in the span
        self.assertEqual(len(self.rows(records(mutate=flag(after)))), 3)
        self.assertEqual(len(self.rows(records(mutate=lambda recs: [r.__setitem__("gap", True) for r in recs if r["type"] == "pool"]))), 3)  # a pool-record flag alone is not read

    def test_base_breaks_unresolved_settled_not_zero_is_excluded(self):
        for v in (1, 3, None):
            def b(recs, v=v):
                for r in recs:
                    if r["type"] == "trigger":
                        r["base_breaks_unresolved_settled"] = v
            self.excluded(b, "base_breaks")

    def test_a_trigger_older_than_3500_ms_is_excluded_at_3501_but_not_at_3500(self):
        def age(ms):
            def m(recs):
                for r in recs:
                    if r["type"] == "trigger":
                        r["t_detect_ms"] = r["block_time"] * 1000 + ms
            return m
        self.excluded(age(3_501), "trigger_age")
        self.assertEqual(len(self.rows(records(mutate=age(3_500)))), 3)

    def test_a_missing_or_truncated_strip_is_a_counted_exclusion(self):
        self.excluded(lambda recs: recs.__setitem__(slice(None), [r for r in recs if r["type"] != "strip"]), "strip")
        def trunc(recs):
            for r in recs:
                if r["type"] == "strip":
                    r["truncated"] = True
        self.excluded(trunc, "strip")
        def btrunc(recs):
            for r in recs:
                if r["type"] == "strip":
                    r["buys_truncated"] = True
        self.excluded(btrunc, "strip")
        def nobuys(recs):
            for r in recs:
                if r["type"] == "strip":
                    r.pop("buys")
        self.excluded(nobuys, "strip")  # a default-mode strip has no buys: the pressure inputs cannot be recomputed

    def test_incomplete_outcomes_are_a_counted_exclusion(self):
        def inc(recs):
            for r in recs:
                if r["type"] in ("outcome", "outcome_bx10"):
                    r["complete"] = False
        self.excluded(inc, "incomplete")

    def test_an_exit_not_after_the_deciding_landing_is_excluded(self):
        def early(recs):
            for r in recs:
                if r["type"] == "outcome":
                    r["exit"]["trigger_slot"] = r["trigger_slot"] + 2
        self.excluded(early, "exit_before_landing")

    def test_the_cap_is_five_percent_of_decided_attempts(self):
        res = sc.BookResult()
        res.counts["counted"] = 19
        res.exclusions = [{"reason": "gap_flag"}]
        res.decisions = list(range(20))
        s = sc.summarize(V1, res, 0)
        self.assertEqual((s["excluded"], s["excluded_share"], s["not_decidable"]), (1, 0.05, False))  # exactly 5% is not above it
        res.counts["counted"], res.exclusions = 18, [{"reason": "gap_flag"}, {"reason": "strip"}]
        s = sc.summarize(V1, res, 0)
        self.assertTrue(s["not_decidable"])
        self.assertEqual(s["excluded_by_reason"], {})  # (counts keys are written by score_book; the share is what decides)

    def test_exclusions_flow_into_a_report_the_reader_can_use(self):
        recs = records(2, mutate=lambda recs: [r.update({"gap": True, "gaps": []}) for r in recs if r["type"] == "trigger" and r.get("pool") == "Pool0000"])
        res = self.book(recs)
        s = sc.summarize(V1, res, 0)
        self.assertEqual((s["counted"], s["excluded"], s["attempts"]), (1, 1, 2))
        self.assertEqual(s["excluded_by_reason"], {"gap_flag": 1})
        self.assertTrue(s["not_decidable"])  # 50% excluded


class IdentityTests(Base):
    def count(self, recs, ent=None):
        res = self.book(recs, V1, ent)
        return res

    def test_a_dir_with_a_null_mismatched_or_dirty_sha_is_refused_whole(self):
        for kw, why in (({"sha": None}, "null_code_sha"), ({"sha": "b" * 40}, "sha_mismatch"), ({"dirty": True}, "dirty"), ({"fresh_forward": None}, "not_fresh_forward")):
            sha = kw.pop("sha", SHA) if "sha" in kw else SHA
            recs = records(sha=sha, **kw)
            if "fresh_forward" in kw:
                recs[0].pop("fresh_forward")
            res = self.book(recs, name=f"h5-shadow-{why}")
            self.assertEqual((res.rows, res.counts["refused_dirs"]), ([], 1), why)
            self.assertEqual(list(res.refused.values()), [why])

    def test_a_process_that_started_before_the_registration_is_refused(self):
        res = self.book(records(), ent=entry(registered_at_ms=START_MS + 1))
        self.assertEqual(list(res.refused.values()), ["started_before_registration"])
        self.assertEqual(res.rows, [])

    def test_a_restart_at_the_same_sha_continues_the_book_with_the_downtime_counted(self):
        first = records()
        second = records(start_ms=START_MS + 600_000)
        for r in second:
            if r.get("pool"):
                r["pool"] = r["pool"].replace("Pool0000", "PoolXXXX")
                if "mint" in r:
                    r["mint"] = r["mint"].replace("Pool0000", "PoolXXXX")
        second[0]["t_ms"] = second[0]["start_ms"]
        counts = collections.Counter()
        d = self.shadow_dir(first + second)
        res = sc.score_book(V1, entry(), [sc.ShadowDir(d, counts)])
        self.assertEqual(res.restarts, 1)
        self.assertGreater(res.downtime_ms, 0)
        self.assertEqual(res.counts["refused_dirs"], 0)

    def test_a_restart_at_another_sha_is_a_new_book_so_the_whole_dir_is_refused(self):
        res = self.book(records() + records(start_ms=START_MS + 600_000, sha="c" * 40))
        self.assertEqual((res.rows, list(res.refused.values())), ([], ["sha_mismatch"]))

    def test_the_clock_and_the_window_end(self):
        recs = records()
        d = sc.ShadowDir(self.shadow_dir(recs), collections.Counter())
        t = of(recs, "trigger")[0]["t_detect_ms"]
        before = sc.score_book(V1, entry(clock_start_ms=t + 1), [d])
        self.assertEqual((before.rows, before.counts["skipped_before_clock"], len(before.decisions)), ([], 1, 0))
        at_end = sc.score_book(V1, entry(window_end_ms=t), [d])
        self.assertEqual((at_end.rows, at_end.counts["skipped_after_window"], len(at_end.decisions)), ([], 1, 0))  # nothing at or after the A3 instant counts
        inside = sc.score_book(V1, entry(window_end_ms=t + 1), [d])
        self.assertEqual(len(inside.rows), 3)

    def test_a_sealed_decision_only_trigger_is_not_part_of_the_book(self):
        def sealed(recs):
            for r in recs:
                if r["type"] == "trigger":
                    r["h5_look2_sealed"] = True
        recs = records(mutate=sealed)
        recs[:] = [r for r in recs if r["type"] not in ("outcome", "outcome_bx10", "strip")]
        res = self.book(recs)
        self.assertEqual((res.rows, res.decisions, res.counts["sealed_decision_only"]), ([], [], 1))


class RegistryTests(Base):
    def test_register_writes_both_books_before_the_clock(self):
        path = self.arena / "bots.json"
        out = sc.register_books(path, frozen_sha=SHA, sockets=3, endpoints=["wss://a/", "wss://b/"], host="mal-fast-0", now_ms=1_000, clock_start_ms=5_000)
        doc = json.loads(path.read_text())["bots"]
        self.assertEqual(set(doc), {V1, V2})
        for bot, e in doc.items():
            self.assertEqual((e["frozen_sha"], e["registered_at_ms"], e["clock_start_ms"], e["deciding_stake_sol"], e["deciding_cell"], e["sockets"], e["endpoints"], e["host"]),
                             (SHA, 1_000, 5_000, 0.3, "1p9", 3, ["wss://a/", "wss://b/"], "mal-fast-0"))
            self.assertEqual(e["stack"], "DEC-031-rev2")
            self.assertIsNone(e["window_end_ms"])
        self.assertEqual(doc[V2]["freeze_sha256"], sc.FREEZE_SHA256)
        self.assertNotIn("freeze_sha256", doc[V1])
        self.assertEqual(sc.register_books(path, frozen_sha=SHA, sockets=3, endpoints=["x"], host="h", now_ms=9_999, clock_start_ms=5_000), out)  # again: the old lines stay
        self.assertEqual(json.loads(path.read_text())["bots"][V1]["registered_at_ms"], 1_000)

    def test_a_changed_sha_clock_or_stake_is_refused_and_a_bad_sha_too(self):
        path = self.arena / "bots.json"
        sc.register_books(path, frozen_sha=SHA, sockets=3, endpoints=["e"], host="h", now_ms=1_000, clock_start_ms=5_000)
        for kw in ({"frozen_sha": "d" * 40}, {"clock_start_ms": 6_000}, {"stake": "0.25"}):
            args = {"frozen_sha": SHA, "sockets": 3, "endpoints": ["e"], "host": "h", "now_ms": 1_000, "clock_start_ms": 5_000, **kw}
            with self.assertRaises(ValueError):
                sc.register_books(path, **args)
        for bad in ("", "abc", "G" * 40, SHA[:-1]):
            with self.assertRaises(ValueError):
                sc.register_books(self.arena / "other.json", frozen_sha=bad, sockets=3, endpoints=["e"], host="h")
        with self.assertRaises(ValueError):
            sc.register_books(self.arena / "other.json", frozen_sha=SHA, sockets=3, endpoints=["e"], host="h", now_ms=10, clock_start_ms=5)

    def test_an_a3_halt_ends_both_windows_once_and_never_removes_a_line(self):
        path = self.arena / "bots.json"
        sc.register_books(path, frozen_sha=SHA, sockets=3, endpoints=["e"], host="h", now_ms=1_000)
        sc.set_window_end(path, 50_000)
        doc = json.loads(path.read_text())["bots"]
        self.assertEqual({b: e["window_end_ms"] for b, e in doc.items()}, {V1: 50_000, V2: 50_000})
        self.assertEqual({e["stopped_ms"] for e in doc.values()}, {50_000})
        sc.set_window_end(path, 90_000)  # a later flag never moves the end later
        self.assertEqual({e["window_end_ms"] for e in json.loads(path.read_text())["bots"].values()}, {50_000})
        sc.set_window_end(path, 40_000)  # an earlier instant does
        self.assertEqual({e["window_end_ms"] for e in json.loads(path.read_text())["bots"].values()}, {40_000})
        self.assertEqual(set(json.loads(path.read_text())["bots"]), {V1, V2})
        with self.assertRaises(ValueError):
            sc.set_window_end(self.arena / "none.json", 1)


class ReadGatingTests(Base):
    DAY = sc.DAY_MS
    D0 = (T0 // 86400) * 86400_000  # 2027-01-15T00:00Z

    def spread(self, per_day, days, start=0):
        return [self.D0 + (start + d) * self.DAY + 3_600_000 + i * 1000 for d in range(days) for i in range(per_day)]

    def test_first_midnight_with_100_attempts_on_5_dates(self):
        ds = self.spread(25, 5)  # 125 attempts over 5 dates (days 0..4)
        boundary = self.D0 + 5 * self.DAY
        self.assertEqual(sc.read_instant(ds, boundary), boundary)
        self.assertIsNone(sc.read_instant(ds, boundary - 1))  # the boundary is not in the past yet
        # 100 are reached on day 3 already, but only 4 dates: the boundary waits for the 5th date
        ds = self.spread(40, 6)
        self.assertEqual(sc.read_instant(ds, self.D0 + 10 * self.DAY), self.D0 + 5 * self.DAY)
        # the 100th attempt on day 4 but 5 dates present: the boundary after day 4
        ds = self.spread(20, 5)
        self.assertEqual(sc.read_instant(ds, self.D0 + 9 * self.DAY), self.D0 + 5 * self.DAY)

    def test_not_enough_attempts_or_dates(self):
        self.assertIsNone(sc.read_instant(self.spread(19, 5), self.D0 + 99 * self.DAY))  # 95
        self.assertIsNone(sc.read_instant(self.spread(100, 4), self.D0 + 99 * self.DAY))  # 4 dates
        self.assertIsNone(sc.read_instant([], 10**15))

    def test_it_takes_timestamps_only(self):
        import inspect

        self.assertEqual(list(inspect.signature(sc.read_instant).parameters), ["decisions_ms", "now_ms"])
        self.assertIsNone(sc.read_instant([float("nan")] * 0, 0))
        code = sc.read_instant.__code__
        names = set(code.co_names) | set(code.co_varnames)
        self.assertEqual(sorted(n for n in names if any(w in n.lower() for w in ("lamports", "pnl", "net", "leg", "outcome", "ledger", "row"))), [])  # nothing but timestamps is touched

    def test_attempts_include_excluded_ones(self):
        res = self.book(records(2, mutate=lambda recs: [r.update({"gap": True, "gaps": []}) for r in recs if r["type"] == "trigger"]))
        self.assertEqual((len(res.decisions), res.counts["counted"]), (2, 0))


class BoostTimingTests(Base):
    def test_share_of_trades_whose_boost_last_slice_precedes_the_exit_and_the_15_percent_rule(self):
        def boost(frac_early):
            def m(recs):
                pools = [r for r in recs if r["type"] == "pool"]
                for i, r in enumerate(pools):
                    r["boost_last_slice_s"] = 10.0 if i < frac_early else 1_000.0
            return m
        res = self.book(records(6, mutate=boost(1)))  # 1 of 6 = 16.7%
        s = sc.summarize(V1, res, 0)
        self.assertEqual((s["boost_known"], round(s["boost_before_exit_share"], 3)), (6, 0.167))
        self.assertTrue(s["boost_not_proposed_for_live"])
        res = self.book(records(6, mutate=boost(0)), name="h5-shadow-b")
        s = sc.summarize(V1, res, 0)
        self.assertEqual(s["boost_before_exit_share"], 0.0)
        self.assertFalse(s["boost_not_proposed_for_live"])
        res = self.book(records(6, mutate=lambda recs: [r.__setitem__("boost_last_slice_s", None) for r in recs if r["type"] == "pool"]), name="h5-shadow-c")
        self.assertIsNone(sc.summarize(V1, res, 0)["boost_before_exit_share"])


class LedgerTests(Base):
    def test_write_once_day_files_and_a_separate_exclusions_file(self):
        recs = records(3, mutate=lambda recs: [r.update({"gap": True, "gaps": []}) for r in recs if r["type"] == "trigger" and r.get("pool") == "Pool0001"])
        res = self.book(recs)
        counts = collections.Counter()
        sc.write_ledger(V1, res.rows, res.exclusions, self.ledger, counts)
        self.assertEqual((counts["written_trades"], counts["written_exclusions"]), (6, 1))
        day = sc.utc_day(res.rows[0]["decision_ms"])
        f = self.ledger / V1 / f"trades-{day}.jsonl"
        before = f.read_bytes()
        self.assertEqual(len(before.decode().splitlines()), 6)
        self.assertEqual(len(list((self.ledger / V1).glob("exclusions-*.jsonl"))), 1)
        self.assertNotIn("Pool0001", before.decode())  # an excluded trade is not in the trades file
        c2 = collections.Counter()
        sc.write_ledger(V1, res.rows, res.exclusions, self.ledger, c2)
        self.assertEqual((c2["duplicate_trades"], c2["written_trades"], c2["duplicate_exclusions"]), (6, 0, 1))
        self.assertEqual(f.read_bytes(), before)
        for line in before.decode().splitlines():
            json.loads(line, parse_constant=lambda c: (_ for _ in ()).throw(ValueError(c)))

    def test_a_changed_row_never_replaces_an_existing_id(self):
        res = self.book(records())
        sc.write_ledger(V1, res.rows, [], self.ledger, collections.Counter())
        changed = copy.deepcopy(res.rows)
        for r in changed:
            r["legs"]["flat_1p9"]["net_lamports"] = 123.0
        sc.write_ledger(V1, changed, [], self.ledger, collections.Counter())
        written = [json.loads(x) for f in (self.ledger / V1).glob("trades-*.jsonl") for x in f.read_text().splitlines()]
        self.assertEqual(len(written), 3)
        self.assertTrue(all(w["legs"]["flat_1p9"]["net_lamports"] != 123.0 for w in written))

    def test_a_non_finite_number_is_refused(self):
        res = self.book(records())
        res.rows[0]["legs"]["flat_1p9"]["net_lamports"] = float("nan")
        with self.assertRaises(ValueError):
            sc.write_ledger(V1, res.rows[:1], [], self.ledger, collections.Counter())
        self.assertEqual(list((self.ledger / V1).glob("trades-*.jsonl")), [])

    def test_a_torn_line_is_counted_and_stops_the_write(self):
        res = self.book(records())
        sc.write_ledger(V1, res.rows[:1], [], self.ledger, collections.Counter())
        f = next((self.ledger / V1).glob("trades-*.jsonl"))
        with open(f, "a") as fh:
            fh.write('{"trade_id": "h5v1-boostfloor-a1:Poo')  # a torn tail
        counts = collections.Counter()
        sc.write_ledger(V1, res.rows, [], self.ledger, counts)
        self.assertEqual((counts["bad_ledger_lines"], counts["write_stopped_bad_ledger"], counts["written_trades"]), (1, 1, 0))
        self.assertEqual(len(f.read_text().splitlines()), 2)  # nothing was appended after the tear

    def test_dry_run_writes_nothing(self):
        res = self.book(records())
        counts = collections.Counter()
        sc.write_ledger(V1, res.rows, res.exclusions, self.ledger, counts, dry_run=True)
        self.assertEqual(counts["written_trades"], 3)
        self.assertFalse(self.ledger.exists())

    def test_bad_shadow_lines_are_counted(self):
        d = self.shadow_dir(records())
        with open(d / "h5-shadow-2027-01-15T08.jsonl", "a") as fh:
            fh.write("{not json\n{\"x\": NaN}\n")
        counts = collections.Counter()
        sc.ShadowDir(d, counts)
        self.assertEqual(counts["bad_shadow_lines"], 2)


class RefitTests(Base):
    def fake_rows(self, days, per_day, bot=V1):
        rows = []
        for d in range(days):
            for i in range(per_day):
                ms = ReadGatingTests.D0 + d * sc.DAY_MS + 3_600_000 + i * 1000
                leg = {"net_lamports": 0.0, "nofail_lamports": -1e6, "state": "filled" if i % 7 else "guarded", "ssb": i % 5, "nb_lamports": 10**9 * (i % 9), "p_fail": 0.3}
                rows.append({"trade_id": f"{bot}:P{d}-{i}:0.10", "bot_id": bot, "decision_ms": ms, "stake_sol": 0.1, "legs": {"press_1p3": dict(leg), "press_1p9": dict(leg), "flat_1p3": {}, "flat_1p9": {}}})
        return rows

    def test_the_cell_intercept_gives_a_mean_fail_probability_of_0_289_over_the_filled_sends(self):
        rows = self.fake_rows(3, 40)
        cells = sc.refit_cells(rows)
        self.assertEqual(set(cells), {"0.10|1p3", "0.10|1p9"})
        legs = [r["legs"]["press_1p9"] for r in rows]
        c = cells["0.10|1p9"]["intercept"]
        ps = [cost.press_p(c, l["ssb"], l["nb_lamports"]) for l in legs if l["state"] == "filled"]
        self.assertAlmostEqual(sum(ps) / len(ps), 0.289, places=9)
        self.assertEqual(cells["0.10|1p9"]["n"], 120)
        self.assertLess(cells["0.10|1p9"]["n_filled"], 120)  # guarded rows are not in the mean

    def test_order_of_operations_clock_then_read_boundary_then_exclusions_then_refit(self):
        # 6 days x 20 attempts: the read instant is the midnight after day 4 (100 attempts, 5 dates); the day-5 rows are after it
        rows = self.fake_rows(6, 20)
        bot_dir = self.ledger / V1
        bot_dir.mkdir(parents=True)
        by_day = collections.defaultdict(list)
        for r in rows:
            by_day[sc.utc_day(r["decision_ms"])].append(r)
        for day, rs in by_day.items():
            (bot_dir / f"trades-{day}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rs))
        sc.register_books(self.arena / "bots.json", frozen_sha=SHA, sockets=3, endpoints=["e"], host="h", now_ms=1, clock_start_ms=1)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(sc.main(["refit", "--arena-root", str(self.arena), "--now-ms", str(ReadGatingTests.D0 + 5 * sc.DAY_MS - 1)]), 0)
        self.assertIn("read instant not reached", out.getvalue())  # before the boundary: refused
        self.assertEqual(list(bot_dir.glob("refit-*.json")), [])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(sc.main(["refit", "--arena-root", str(self.arena), "--now-ms", str(ReadGatingTests.D0 + 6 * sc.DAY_MS)]), 0)
        (f,) = bot_dir.glob("refit-*.json")
        doc = json.loads(f.read_text())
        self.assertEqual(doc["read_instant_ms"], ReadGatingTests.D0 + 5 * sc.DAY_MS)
        self.assertEqual(doc["cells"]["0.10|1p9"]["n"], 100)  # day 5's 20 attempts decided after the read instant are not in the fit
        self.assertNotIn("net_lamports", out.getvalue())  # counts only on stdout


class CliTests(Base):
    def main(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = sc.main(list(argv))
        return rc, out.getvalue(), err.getvalue()

    def setup_book(self, recs):
        d = self.shadow_dir(recs, "h5-shadow-2027-01-15")
        sc.register_books(self.arena / "bots.json", frozen_sha=SHA, sockets=3, endpoints=["e"], host="h", now_ms=START_MS - 1_000, clock_start_ms=START_MS)
        return d

    def test_refusals(self):
        d = self.setup_book(records())
        for argv in (["score", "--arena-root", str(self.arena), "--shadow-dir", "/srv/mal-h5-shadow"],
                     ["score", "--arena-root", str(self.arena), "--shadow-dir", "/tmp/x"],
                     ["score", "--arena-root", str(self.arena), "--shadow-dir", str(d), "--ledger-root", "/tmp/ledger"],
                     ["score", "--arena-root", str(self.arena), "--shadow-dir", str(d), "--ledger-root", str(self.ledger / ".." / "x")],
                     ["score", "--arena-root", "/tmp"], ["halt", "--arena-root", "/tmp", "--at-ms", "1"]):
            rc, _, err = self.main(*argv)
            self.assertEqual(rc, 2, argv)
            self.assertIn("REFUSED", err)

    def test_a_run_prints_counts_only_writes_the_ledger_and_the_default_is_score(self):
        d = self.setup_book(records())
        for argv in (["--arena-root", str(self.arena), "--shadow-dir", str(d)], ["score", "--arena-root", str(self.arena), "--shadow-dir", str(d), "--dry-run"]):
            rc, out, _ = self.main(*argv)
            self.assertEqual(rc, 0)
            res = json.loads(out)
            for bot in (V1, V2):
                self.assertEqual((res["books"][bot]["counted"], res["books"][bot]["rows_scored"]), (1, 3))
            self.assertNotIn("lamports", out)
            self.assertNotIn("net_", out)
        self.assertEqual(len(list(self.ledger.glob("*/trades-*.jsonl"))), 2)  # the first (real) run only

    def test_an_unregistered_book_scores_nothing(self):
        d = self.shadow_dir(records(), "h5-shadow-2027-01-15")
        rc, out, _ = self.main("--arena-root", str(self.arena), "--shadow-dir", str(d))
        self.assertEqual(json.loads(out)["books"][V1], {"registered": False})
        self.assertFalse(self.ledger.exists())

    def test_the_default_dirs_are_the_arena_shadow_dirs_only(self):
        self.shadow_dir(records(), "h5-shadow-2027-01-15")
        self.shadow_dir(records(), "h5-smoke-1")
        self.assertEqual([Path(p).name for p in sc.default_shadow_dirs(str(self.arena))], ["h5-shadow-2027-01-15"])

    def test_register_and_halt_commands(self):
        rc, out, _ = self.main("register", "--arena-root", str(self.arena), "--frozen-sha", SHA, "--sockets", "3", "--endpoint", "wss://a/", "--endpoint", "wss://b/", "--host", "mal-fast-0")
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out)["registered"], [V1, V2])
        rc, out, _ = self.main("halt", "--arena-root", str(self.arena), "--at-ms", "123")
        self.assertEqual(json.loads(out)["window_end_ms"], {V1: 123, V2: 123})
        rc, _, err = self.main("register", "--arena-root", str(self.arena), "--frozen-sha", "e" * 40, "--sockets", "3")
        self.assertEqual(rc, 2)


TAPE = "/data/mal/audit-1008/tape"
HOUR = "2026-09-20T12"


def _have_replay() -> bool:
    try:
        import pandas  # noqa: F401
        import pyarrow  # noqa: F401
    except ImportError:
        return False
    return Path(f"{TAPE}/trades/{HOUR}.parquet").exists() and Path(h5.WORK_DIR).exists()


@unittest.skipUnless(_have_replay(), "needs pandas and the exploration tape on this host")
class ExplorationReplayTests(Base):
    """One hour of exploration tape through the same engine configuration as the live fresh-forward run, repriced from its strips at age 0: the landing slots, the pressure
    inputs and the report-only 55k legs equal the shadow's own outcome records."""

    def test_strip_repricing_reproduces_the_shadows_landing_pressure_and_55k_numbers(self):
        rows_in, meta, sps_pool = h5.replay_rows(TAPE, h5.WORK_DIR, [HOUR])
        recs: list[dict] = []
        eng = h5.Engine(recs.append, boost_mode="auto", sps_fn=lambda p: sps_pool.get(p.pool), classifier=h5.PreEventClassifier(), bx10_enabled=True, fresh_start_ms=0,
                        seal_start_ms=None, h5_look2_start_ms=None)
        h5.replay(rows_in, meta, eng)
        recs.insert(0, {"type": "start", "t_ms": 0, "start_ms": 0, "code_sha": SHA, "fresh_forward": "ARENA", "dirty": False})
        res = self.book(recs, V1, ent=entry(clock_start_ms=0, registered_at_ms=0))
        outcomes = {r["pool"]: r for r in recs if r["type"] == "outcome" and r["variant"] == "pv" and r["complete"]}
        self.assertGreater(len(outcomes), 0)
        n_checked = 0
        for r in res.rows:
            o = outcomes[r["pool"]]
            for shadow_leg, sfx, lat in sc.LEGS:
                if o["legs"][shadow_leg].get("net") is None:
                    continue
                self.assertEqual(r["meta"]["landing_slots"][sfx], o["legs"][shadow_leg]["landing_slot"])
                if r["stake_sol"] in (0.1, 0.25):
                    lab = "0.1" if r["stake_sol"] == 0.1 else "0.25"
                    self.assertAlmostEqual(r["report_only"]["h5_frozen_55k"][f"flat_{sfx}"]["nofail_lamports"], o["legs"][shadow_leg]["net"][lab]["pnl_nofail_lamports"], delta=0.01)
                    n_checked += 1
        self.assertGreater(n_checked, 0)


if __name__ == "__main__":
    unittest.main()
