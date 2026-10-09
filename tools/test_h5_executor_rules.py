"""Offline tests for tools/h5_executor.py, part 2: the exit, the live-halt rules, the EXP-022 seal guard, the start conditions, safety.
Fixtures come from test_h5_executor (the probe's fake RPC, a fake clock, throwaway Keypairs). No network, no real key."""

from __future__ import annotations

import base64
import contextlib
import io
import json
import os
import statistics
import time
import unittest
from pathlib import Path
from unittest import mock

from solders.keypair import Keypair

from tools import h5_executor as h
from tools import probe_executor as pe
from tools import probe_live as pl
from tools import pumpswap_tx as tx
from tools.test_h5_executor import BASE0, POOL, QREAL, RENT, S0, SPS, TRIG_SLOT, V, Case, Env, MINT, T0, row, sell_args, trig


def quote_out(tokens: int) -> int:
    q, b = QREAL + V, BASE0
    return tx.cp_sell_out(tokens, q, b, pe.fee_ppm_for(q, b))


N = h.BOOST_MEDIAN_MIN_POOLS  # pools a day needs before its BOOST median counts
SLIPPAGE_ERR = {"InstructionError": [3, {"Custom": 6004}]}


# --- the exit: precomputed, slot-timed, retried, hard-deadlined ------------------------------------------------------------


class ExitTests(Case):
    def test_sell_is_built_at_arm_slot_and_sent_at_send_slot_to_land_at_the_exit_slot(self):
        e = self.env()
        pos = e.open_position()
        plan = e.plan()
        self.assertEqual((plan["arm_slot"], plan["send_slot"], plan["land_slot"], plan["exit_slot"]), (S0 + 1637, S0 + 1647, S0 + 1650, S0 + 1650))
        e.at_slot(plan["arm_slot"] - 1)
        self.assertEqual((len(e.rpc.sent), e.ex.armed), (1, {}))  # nothing is built early
        e.at_slot(plan["arm_slot"])
        self.assertIn(MINT, e.ex.armed)  # signed and held, not sent
        self.assertEqual(len(e.rpc.sent), 1)
        e.at_slot(plan["send_slot"] - 1)
        self.assertEqual(len(e.rpc.sent), 1)
        e.rpc.calls.clear()
        e.at_slot(plan["send_slot"])
        self.assertEqual(len(e.rpc.sent), 2)
        before_send = e.rpc.calls[: e.rpc.calls.index("sendTransaction")]
        self.assertTrue(set(before_send) <= {"getSlot"}, before_send)  # the precomputed sell needs no state read to go out
        d = e.sent()[1]
        tokens, min_out = sell_args(d)
        self.assertEqual(tokens, pos["tokens"])
        self.assertEqual(min_out, quote_out(tokens) * 8500 // 10_000)
        self.assertEqual((d["closes"], d["priority"]), (2, 55_000))  # the WSOL account and the token account are both closed in the same tx
        self.assertEqual(e.ledger("sell_sent")[0]["level"], 0)

    def test_sell_lands_and_the_rent_refund_and_exit_error_are_recorded(self):
        e = self.env()
        e.open_position()
        plan = e.plan()
        e.at_slot(plan["send_slot"])
        e.land_sell(plan["land_slot"] + 1)
        self.assertNotIn(MINT, e.ex.state.open)
        sell = e.ledger("sell")[0]
        self.assertTrue(sell["landed"])
        self.assertEqual(sell["rent_refunded_lamports"], RENT)
        land = e.ledger("exit_landing")[0]
        self.assertEqual((land["landed_slot"], land["error_slots"], land["late"]), (plan["land_slot"] + 1, 1, False))
        self.assertEqual((e.ex.counters.sells_landed, e.ex.counters.sells_late), (1, 0))
        self.assertEqual(e.ex.state.realized_lamports, e.ex.counters.day(h.day_key(e.clock()))["realized"])

    def test_retry_ladder_fresh_blockhash_then_wider_guard_and_150k(self):
        e = self.env()
        e.open_position()
        plan = e.plan()
        e.at_slot(plan["send_slot"])
        sigs = [e.sent()[1]["signature"]]
        e.land_sell(plan["land_slot"], err=SLIPPAGE_ERR)
        self.assertIn(MINT, e.ex.state.open)
        e.at_slot(plan["send_slot"] + 2)  # retry 1: fresh quote and blockhash, same guard and priority
        d1 = e.sent()[2]
        sigs.append(d1["signature"])
        self.assertEqual(sell_args(d1)[1], quote_out(sell_args(d1)[0]) * 8500 // 10_000)
        self.assertEqual(d1["priority"], 55_000)
        e.land_sell(plan["land_slot"] + 2, err=SLIPPAGE_ERR)
        e.at_slot(plan["send_slot"] + 4)  # retry 2: 0.65 and the escalated priority
        d2 = e.sent()[3]
        sigs.append(d2["signature"])
        self.assertEqual(sell_args(d2)[1], quote_out(sell_args(d2)[0]) * 6500 // 10_000)
        self.assertEqual(d2["priority"], 150_000)
        self.assertEqual(len(set(sigs)), 3)  # each retry is a new transaction
        self.assertEqual([r["level"] for r in e.ledger("sell_sent")], [0, 1, 2])

    def test_past_345_seconds_the_sell_starts_at_the_escalated_level(self):
        e = self.env()
        e.open_position()
        e.at_slot(e.plan()["escalate_slot"])
        d = e.sent()[1]
        self.assertEqual(d["priority"], 150_000)
        self.assertEqual(sell_args(d)[1], quote_out(sell_args(d)[0]) * 6500 // 10_000)

    def test_emergency_market_sell_at_the_400s_deadline_then_stuck_halt(self):
        e = self.env()
        e.open_position()
        plan = e.plan()
        e.at_slot(plan["deadline_slot"])
        d = e.sent()[1]
        self.assertEqual(sell_args(d)[1], h.EMERGENCY_MIN_OUT)  # a market sell, but never min_out 0
        self.assertEqual(d["priority"], 150_000)
        kinds = [r["kind"] for r in e.ledger() if r["kind"] in ("sell_sent", "halt_latched")]
        self.assertEqual(kinds, ["sell_sent", "halt_latched"])  # the emergency sell goes out first, then the stuck halt
        self.assertEqual(e.ledger("halt_latched")[0]["reason"], "stuck_position")
        self.assertIn("stuck_position", e.ex.counters.halts)
        e.ex.state.pending.pop(MINT)
        now_slot = e.rpc.slot  # a fresh trigger on the chain as it is now
        e.fire(mint=str(Keypair().pubkey()), trigger_slot=now_slot - 5, s0_slot=now_slot - 505)
        self.assertEqual(e.refusals(), ["halt_latched:stuck_position"])

    def test_never_sell_blind_before_the_deadline_but_do_at_the_deadline(self):
        e = self.env()
        e.open_position()
        plan = e.plan()
        e.rpc.state_fails = True  # the pool cannot be read
        e.at_slot(plan["send_slot"])
        self.assertEqual(len(e.rpc.sent), 1)  # no quote, no sell
        self.assertTrue(any(r.get("alert") == "unpriced_position" for r in e.ledger("alert")))
        e.at_slot(plan["deadline_slot"])
        self.assertEqual(len(e.rpc.sent), 2)
        self.assertEqual(sell_args(e.sent()[1])[1], h.EMERGENCY_MIN_OUT)  # built from the cached pool accounts

    def test_overdue_exit_fires_immediately_after_a_restart(self):
        e = self.env()
        e.open_position()
        plan = e.plan()
        e.ex = e.build()  # a new process on the same state files
        self.assertIn(MINT, e.ex.state.open)
        self.assertEqual(e.ex.state.open[MINT]["h5"]["plan"], plan)
        e.at_slot(plan["send_slot"] + 6)
        self.assertEqual(len(e.rpc.sent), 2)
        self.assertEqual(sell_args(e.sent()[1])[0], e.ex.state.open[MINT]["tokens"])

    def test_halt_file_freezes_sells_and_stop_file_does_not(self):
        e = self.env()
        e.open_position()
        plan = e.plan()
        Path(e.conf["halt_file"]).write_text("")
        e.at_slot(plan["send_slot"])
        self.assertEqual(len(e.rpc.sent), 1)
        Path(e.conf["halt_file"]).unlink()
        Path(e.conf["stop_file"]).write_text("")  # STOP stops new buys only: the exit still goes out
        e.at_slot(plan["send_slot"])
        self.assertEqual(len(e.rpc.sent), 2)

    def test_live_ok_removed_mid_run_does_not_strand_a_position(self):
        e = self.env()
        e.open_position()
        Path(e.conf["live_ok_file"]).unlink()
        e.at_slot(e.plan()["send_slot"])
        self.assertEqual(len(e.rpc.sent), 2)

    def test_rebroadcast_the_same_tx_every_400ms_until_it_expires(self):
        e = self.env()
        e.open_position()
        e.at_slot(e.plan()["send_slot"])
        n = len(e.rpc.sent)
        e.clock.t += 300
        e.ex.housekeeping(e.clock())
        self.assertEqual(len(e.rpc.sent), n)
        e.clock.t += 150
        e.ex.housekeeping(e.clock())
        self.assertEqual(len(e.rpc.sent), n + 1)
        self.assertEqual(e.rpc.sent[-1][1], e.rpc.sent[-2][1])  # the identical signed tx
        e.rpc.height = 5_000  # past lastValidBlockHeight: stop rebroadcasting
        e.clock.t += 600
        e.ex.housekeeping(e.clock())
        m = len(e.rpc.sent)
        e.clock.t += 600
        e.ex.housekeeping(e.clock())
        self.assertEqual(len(e.rpc.sent), m)

    def test_slot_clock_failure_is_retried_but_not_every_pass(self):
        e = self.env()
        e.open_position()
        e.rpc.slot_fails = True
        e.rpc.calls.clear()
        e.clock.t += 21_000
        for _ in range(5):
            e.ex.exit_tick(e.clock())
        self.assertEqual(e.rpc.calls.count("getSlot"), 1)  # same millisecond: one try
        self.assertEqual(len(e.rpc.sent), 1)

    def test_unsafe_priority_is_refused_by_the_signer_allowlist(self):
        e = self.env()
        e.fire()
        ps, _err, _snap = e.ex._static_for(trig(e.clock), e.clock())
        msg = e.ex._buy_message(ps, e.ex.user, 1_000, e.ex.bh.get()[0])
        with self.assertRaises(pl.UnsafeTx):
            e.ex._sign(msg, ps, MINT, cap=10_000)  # the message pays 55,000: over the cap it was given


# --- the four live-halt rules -----------------------------------------------------------------------------------------------


class HaltRuleTests(Case):
    @staticmethod
    def feed(e: Env, secs, prefix: str = "P") -> list[str]:
        """One closed pool per value, each with its own mint."""
        mints = [f"{prefix}{i:04d}" + "1" * 30 for i, _ in enumerate(secs)]
        for m, s in zip(mints, secs):
            e.ex.on_boost_row(m, S0, SPS, None, s)
        return mints

    def day(self, e: Env, offset_days: int = 0) -> dict:
        return e.ex.counters.days[h.day_key(T0 + offset_days * 86_400_000)]

    def test_27_percent_of_pools_under_335_with_a_median_of_341_does_not_halt(self):
        e = self.env()
        self.feed(e, [341.0] * 73 + [330.0] * 27)  # the shape of the shadow feed: 27% of pools early, a healthy median
        self.assertEqual(e.ex.counters.halts, {})
        self.assertEqual(self.day(e)["boost_median"], 341.0)
        self.assertEqual(len(e.ledger("boost_last_slice")), 100)  # every pool is still ledgered
        e.fire()
        self.assertEqual(len(e.rpc.sent), 1)

    def test_the_running_median_is_order_sensitive_which_is_why_the_minimum_pool_count_matters(self):
        """Documents a known property, not a wish: the median is re-evaluated as each pool closes, so a cluster of early pools at the START of a
        UTC day can latch the halt even when the whole day's median is healthy (the same 100 pools in the other order do not: the test above).
        Simulated on the shadow feed's quantiles this false-latches about 11.6% of days at a minimum of 10 pools, 3.6% at 20, 0.9% at 30."""
        e = self.env()
        n = h.BOOST_MEDIAN_MIN_POOLS
        self.feed(e, [330.0] * n + [341.0] * (73 + 27 - n))
        self.assertEqual(list(e.ex.counters.halts), ["boost_median_lt_335"])

    def test_a_single_early_pool_never_halts(self):
        e = self.env()
        self.feed(e, [334.9])
        self.feed(e, [301.0, 335.5, 336.9], prefix="Q")
        self.assertEqual(e.ex.counters.halts, {})  # no per-pool latch: below 10 pools there is no median either

    def test_day_median_below_335_halts_once_10_pools_have_closed(self):
        e = self.env()
        self.feed(e, [334.0] * (N - 1))
        self.assertEqual(e.ex.counters.halts, {})  # nine pools: no median yet
        self.feed(e, [334.0], prefix="Z")
        self.assertEqual(list(e.ex.counters.halts), ["boost_median_lt_335"])
        rows = e.ledger("halt_latched")
        self.assertEqual((len(rows), rows[0]["reason"], rows[0]["median_s"]), (1, "boost_median_lt_335", 334.0))
        e.fire()
        self.assertEqual(e.refusals(), ["halt_latched:boost_median_lt_335"])
        self.assertEqual(e.rpc.sent, [])

    def test_median_of_exactly_335_is_not_below_335_and_one_day_under_337_is_not_twice(self):
        e = self.env()
        self.feed(e, [335.0] * N)
        self.assertEqual(e.ex.counters.halts, {})  # < 337 on one day only
        self.assertEqual(self.day(e)["boost_median"], 335.0)

    def test_median_under_337_on_two_utc_days_halts_consecutive_or_not(self):
        for gap_days in (1, 3):
            sub = self.tmp / f"g{gap_days}"
            sub.mkdir()
            e = Env(sub)
            self.feed(e, [336.0] * N)
            self.assertEqual(e.ex.counters.halts, {})
            e.ex = e.build()  # the first day's median is persisted, a restart does not lose it
            e.clock.t += gap_days * 24 * 3_600_000
            self.feed(e, [336.5] * N, prefix="D")
            self.assertEqual(e.ex.counters.halts, {}, gap_days)  # the second low day is still TODAY: only completed UTC days are counted
            e.clock.t += 24 * 3_600_000
            self.feed(e, [345.0], prefix="F")  # the next day's first pool completes it
            self.assertEqual(list(e.ex.counters.halts), ["boost_median_lt_337_twice"], gap_days)
            row = e.ledger("halt_latched")[0]
            self.assertEqual(row["medians"], [336.0, 336.5])

    def test_a_transient_dip_today_does_not_count_toward_the_two_days(self):
        e = self.env()
        self.feed(e, [336.0] * N)  # one low day ...
        e.clock.t += 24 * 3_600_000
        self.feed(e, [330.0] * N, prefix="D")  # ... and today's running median dips under 337 (and under 335: that rule is today's own)
        self.assertEqual(list(e.ex.counters.halts), ["boost_median_lt_335"])  # (the same-day rule); the two-day rule needs a completed second day
        self.assertNotIn("boost_median_lt_337_twice", e.ex.counters.halts)

    def test_a_healthy_day_between_two_low_days_does_not_reset_and_two_healthy_days_do_not_halt(self):
        e = self.env()
        self.feed(e, [338.0] * N)
        e.clock.t += 24 * 3_600_000
        self.feed(e, [336.0] * N, prefix="D")
        self.assertEqual(e.ex.counters.halts, {})  # 338 does not count; one low day, and it is not complete yet
        e.clock.t += 24 * 3_600_000
        self.feed(e, [336.9] * N, prefix="E")
        self.assertEqual(e.ex.counters.halts, {})  # the first low day is complete, the second is today
        e.clock.t += 24 * 3_600_000
        self.feed(e, [345.0], prefix="G")
        self.assertEqual(list(e.ex.counters.halts), ["boost_median_lt_337_twice"])  # both low days are complete now

    def test_a_pool_counts_once_a_day_however_often_it_is_reported(self):
        e = self.env()
        for _ in range(12):
            e.ex.on_boost_row(MINT, S0, SPS, None, 320.0)
        self.assertEqual((e.ex.counters.halts, len(self.day(e)["boost_s"])), ({}, 1))

    def test_structure_floor_three_pools_under_300_in_a_day(self):
        e = self.env()
        self.feed(e, [250.0, 280.0])
        self.assertEqual(e.ex.counters.halts, {})
        self.feed(e, [299.9], prefix="Q")
        self.assertEqual(list(e.ex.counters.halts), ["boost_structure_lt_300_x3"])
        sub = self.tmp / "days"
        sub.mkdir()
        e2 = Env(sub)
        self.feed(e2, [250.0, 280.0])
        e2.clock.t += 24 * 3_600_000
        self.feed(e2, [250.0], prefix="Q")
        self.assertEqual(e2.ex.counters.halts, {})  # two on one day, one on the next: not three in a day

    def sell_and_boost(self, e: Env, boost_secs, *, boost_first=False) -> None:
        """Our sell lands at 330.0 s after s0 on every pool; BOOST's last slice for that pool is the given figure."""
        plan = h.exit_plan(S0, SPS, e.ex.h5).public()
        for i, b in enumerate(boost_secs):
            m = f"S{i:04d}" + "1" * 30
            if boost_first:
                e.ex.on_boost_row(m, S0, SPS, None, b)
            e.ex._note_sell_landing(m, plan, plan["land_slot"], False)  # lands at exactly s0 + 330 s
            if not boost_first:
                e.ex.on_boost_row(m, S0, SPS, None, b)

    def test_boost_finished_before_our_sell_above_15_percent_halts_from_20_sells(self):
        e = self.env()
        self.sell_and_boost(e, [330.0] * 3 + [340.0] * 17)  # 3 of 20 = 15.0%: not above
        self.assertEqual(e.ex.counters.halts, {})
        last = e.ledger("boost_vs_sell")[-1]
        self.assertEqual((last["n"], last["before_n"], last["share"]), (20, 3, 0.15))  # the running share is ledgered
        sub = self.tmp / "b"
        sub.mkdir()
        e2 = Env(sub)
        self.sell_and_boost(e2, [330.0] * 4 + [340.0] * 16)  # 4 of 20 = 20%
        self.assertEqual(list(e2.ex.counters.halts), ["boost_before_sell_gt_15pct"])

    def test_boost_at_or_before_the_landing_counts_and_after_does_not(self):
        e = self.env()
        self.sell_and_boost(e, [330.0, 330.001, 329.0])
        c = e.ex.counters
        self.assertEqual((c.bvs_n, c.bvs_before), (3, 2))  # 330.0 and 329.0 are at or before 330.0; 330.001 is after

    def test_fewer_than_20_paired_sells_are_not_judged(self):
        e = self.env()
        self.feed(e, [345.0] * 40, prefix="U")  # untraded pools keep the day median healthy, so only rule (b) can speak
        self.sell_and_boost(e, [320.0] * 19)  # every traded pool finished before our sell, but there are only 19
        self.assertEqual((e.ex.counters.bvs_n, e.ex.counters.bvs_before, e.ex.counters.halts), (19, 19, {}))
        sub = self.tmp / "twenty"
        sub.mkdir()
        e2 = Env(sub)
        self.feed(e2, [345.0] * 40, prefix="U")
        self.sell_and_boost(e2, [320.0] * 20)
        self.assertEqual(list(e2.ex.counters.halts), ["boost_before_sell_gt_15pct"])

    def test_pairing_does_not_depend_on_which_side_arrives_first(self):
        e = self.env()
        self.sell_and_boost(e, [330.0] * 4 + [340.0] * 16, boost_first=True)
        self.assertEqual(list(e.ex.counters.halts), ["boost_before_sell_gt_15pct"])
        self.assertEqual((e.ex.counters.bvs_n, e.ex.counters.bvs_before), (20, 4))

    def test_a_pool_we_did_not_trade_is_not_paired(self):
        e = self.env()
        self.feed(e, [320.0] * 5)
        self.assertEqual((e.ex.counters.bvs_n, e.ex.counters.sell_land_s), (0, {}))

    def test_boost_seconds_from_slots_and_from_the_feed_file(self):
        e = self.env()
        e.ex.on_boost_row(MINT, S0, 0.2, S0 + 1669, None)  # 333.8 s from the slot count
        sub = self.tmp / "f"
        sub.mkdir()
        e2 = Env(sub)
        path = Path(e2.conf["intents_file"])
        path.write_text("")
        e2.ex.intent_tick()
        with path.open("a") as fh:
            fh.write(json.dumps({"schema": "h5_boost_v1", "mint": MINT, "s0_slot": S0, "sps": 0.2, "last_slice_slot": S0 + 1600}) + "\n")
        e2.ex.intent_tick()
        self.assertEqual([r["seconds_after_s0"] for r in e.ledger("boost_last_slice")], [333.8])
        self.assertEqual([r["seconds_after_s0"] for r in e2.ledger("boost_last_slice")], [320.0])
        self.assertEqual((e.ex.counters.halts, e2.ex.counters.halts), ({}, {}))  # one pool is a ledger row, not a halt

    def test_malformed_boost_rows_do_not_halt(self):
        e = self.env()
        for sec in (float("nan"), -5.0, 0.0, 5_000.0, True):
            e.ex.on_boost_row(MINT, S0, SPS, None, sec)
        e.ex.on_boost_row(MINT, None, None, None, None)
        self.assertEqual(e.ex.counters.halts, {})

    def test_more_than_5_percent_late_sells_halts(self):
        e = self.env()
        plan = h.exit_plan(S0, SPS, e.ex.h5).public()
        for _ in range(19):
            e.ex._note_sell_landing(MINT, plan, plan["late_slot"], False)  # landing exactly at s0 + 335 s is on time
        e.ex._note_sell_landing(MINT, plan, plan["late_slot"] + 1, False)  # 1 late of 20 = 5.0%: not MORE than 5%
        self.assertEqual(e.ex.counters.halts, {})
        e.ex._note_sell_landing(MINT, plan, plan["late_slot"] + 1, False)  # 2 of 21 = 9.5%
        self.assertEqual(list(e.ex.counters.halts), ["late_sells_gt_5pct"])

    def test_first_sell_late_halts_literally_and_min_sample_can_be_raised(self):
        e = self.env()
        plan = h.exit_plan(S0, SPS, e.ex.h5).public()
        e.ex._note_sell_landing(MINT, plan, plan["late_slot"] + 9, False)
        self.assertIn("late_sells_gt_5pct", e.ex.counters.halts)
        self.assertEqual(h.LATE_SELL_MIN_N, 1)  # a code constant: the config cannot raise it to loosen the halt
        with self.assertRaises(ValueError):
            h.H5Limits.from_config({"late_sell_min_n": 10})

    def test_a_late_sell_through_the_real_landing_path(self):
        e = self.env()
        e.open_position()
        plan = e.plan()
        e.at_slot(plan["send_slot"])
        e.land_sell(plan["late_slot"] + 3)
        self.assertEqual((e.ex.counters.sells_late, list(e.ex.counters.halts)), (1, ["late_sells_gt_5pct"]))

    def test_median_trigger_to_landing_over_3s_halts_on_the_last_10(self):
        e = self.env()
        for _ in range(9):
            e.ex._note_buy_landing(TRIG_SLOT, TRIG_SLOT + 40, SPS)  # 8 s each, but only 9 fills so far
        self.assertEqual(e.ex.counters.halts, {})
        e.ex._note_buy_landing(TRIG_SLOT, TRIG_SLOT + 40, SPS)
        self.assertEqual(list(e.ex.counters.halts), ["landing_median_gt_3s"])

    def test_median_exactly_3s_is_fine_and_only_the_last_10_count(self):
        e = self.env()
        for slots in (10, 10, 10, 10, 10, 20, 20, 20, 20, 20):  # 2 s x5 and 4 s x5: median 3.0
            e.ex._note_buy_landing(TRIG_SLOT, TRIG_SLOT + slots, SPS)
        self.assertEqual(statistics.median(e.ex.counters.landing_s[-10:]), 3.0)
        self.assertEqual(e.ex.counters.halts, {})
        sub = self.tmp / "w"
        sub.mkdir()
        e2 = Env(sub)
        for _ in range(50):
            e2.ex._note_buy_landing(TRIG_SLOT, TRIG_SLOT + 5, SPS)
        self.assertEqual(e2.ex.counters.halts, {})
        for _ in range(10):
            e2.ex._note_buy_landing(TRIG_SLOT, TRIG_SLOT + 20, SPS)
        self.assertIn("landing_median_gt_3s", e2.ex.counters.halts)

    def test_landing_is_taken_from_the_real_buy_landing(self):
        e = self.env()
        e.open_position()
        self.assertEqual(e.ex.counters.landing_s, [1.6])  # 8 slots x 0.2 s

    def test_halts_survive_a_restart_and_clear_only_by_the_manual_command(self):
        e = self.env()
        self.feed(e, [334.0] * N)  # a day median under 335 s
        e.ex = e.build()
        self.assertIn("boost_median_lt_335", e.ex.counters.halts)
        e.fire()
        self.assertEqual(e.refusals(), ["halt_latched:boost_median_lt_335"])
        cp = self.tmp / "c.json"
        cp.write_text(json.dumps(e.conf))
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(h.main(["--config", str(cp), "--live", "--clear-halt", "nope"]), 1)
            self.assertEqual(h.main(["--config", str(cp), "--live", "--clear-halt", "boost_median_lt_335"]), 0)
        e.ex = e.build()
        self.assertEqual(e.ex.counters.halts, {})
        self.assertEqual(e.ledger("halt_cleared")[0]["reason"], "boost_median_lt_335")

    def test_dry_run_records_a_latched_halt_as_would_halt(self):
        e = self.env(live=False)
        self.feed(e, [334.0] * N)
        e.fire()
        self.assertEqual(e.ledger("decision")[0]["would_have_halted"], "halt_latched:boost_median_lt_335")


# --- EXP-022 seal guard ----------------------------------------------------------------------------------------------------------


class Oracle:
    def __init__(self, result):
        self.result, self.calls = result, []

    def __call__(self, mint):
        self.calls.append(mint)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


T_SEAL = h.SEAL_START_MS + 60_000  # 2026-10-16T01:01Z
T_AFTER_FINAL = h.ORACLE_EARLIEST_MS + 1_800_000  # 02:30Z


def time_utc(ms: int) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ms / 1000))


class SealTests(Case):
    def sealed(self, when: int, oracle=None, marker: bool = True, sub: str = "", **cfg) -> Env:
        d = self.tmp / sub if sub else self.tmp
        d.mkdir(exist_ok=True)
        e = Env(d, oracle=oracle, **cfg)
        e.set_time(when)
        if marker:
            Path(e.conf["final_marker_file"]).write_text("")
        return e

    def test_constants(self):
        self.assertEqual(time_utc(h.SEAL_START_MS), "2026-10-16T01:00:00Z")
        self.assertEqual(time_utc(h.ORACLE_EARLIEST_MS), "2026-10-16T02:00:00Z")
        self.assertEqual(time_utc(h.SEAL_END_DEFAULT_MS), "2026-11-06T02:00:00Z")

    def test_before_the_window_the_oracle_is_not_consulted(self):
        o = Oracle(True)
        e = self.sealed(h.SEAL_START_MS - 1, o)
        e.fire()
        self.assertEqual((o.calls, len(e.rpc.sent)), ([], 1))

    def test_in_the_window_without_an_oracle_every_buy_is_refused(self):
        e = self.sealed(T_SEAL, None)
        e.fire()
        self.assertEqual(e.rpc.sent, [])
        self.assertEqual(e.ex.counters.seal_skips, 1)

    def test_before_the_final_the_oracle_is_unavailable_even_if_configured(self):
        for i, (when, marker) in enumerate(((T_SEAL, True), (h.ORACLE_EARLIEST_MS - 1, True), (T_AFTER_FINAL, False))):
            o = Oracle(False)
            e = self.sealed(when, o, marker, sub=f"u{i}")
            e.fire()
            self.assertEqual((o.calls, e.rpc.sent, e.ex.counters.seal_skips), ([], [], 1), (when, marker))

    def test_after_the_final_a_non_pick_trades_and_a_pick_does_not(self):
        o = Oracle(False)
        e = self.sealed(T_AFTER_FINAL, o, sub="np")
        e.fire()
        self.assertEqual((o.calls, len(e.rpc.sent), e.ex.counters.seal_skips), ([MINT], 1, 0))
        e2 = self.sealed(T_AFTER_FINAL, Oracle(True), sub="pk")
        e2.fire()
        self.assertEqual((e2.rpc.sent, e2.ex.counters.seal_skips), ([], 1))

    def test_an_oracle_error_or_a_non_boolean_refuses_the_buy(self):
        for i, result in enumerate((RuntimeError("boom"), h.OracleUndecided("undecided"), None, 1, "no", 0.0)):
            e = self.sealed(T_AFTER_FINAL, Oracle(result), sub=f"x{i}")
            e.fire()
            self.assertEqual((e.rpc.sent, e.ex.counters.seal_skips), ([], 1), repr(result))

    def test_after_the_seal_end_the_oracle_is_not_consulted(self):
        o = Oracle(True)
        e = self.sealed(h.SEAL_END_DEFAULT_MS, o)
        e.fire()
        self.assertEqual((o.calls, len(e.rpc.sent)), ([], 1))

    def test_config_can_extend_the_seal_but_not_shorten_it(self):
        early = self.sealed(T_SEAL, None, sub="s", seal_end_ms=h.SEAL_START_MS + 1)  # an end before the default is ignored
        self.assertEqual(early.ex.seal_end_ms, h.SEAL_END_DEFAULT_MS)
        early.fire()
        self.assertEqual(early.rpc.sent, [])
        later = self.sealed(T_SEAL, None, sub="l", seal_end_ms=h.SEAL_END_DEFAULT_MS + 86_400_000)
        self.assertEqual(later.ex.seal_end_ms, h.SEAL_END_DEFAULT_MS + 86_400_000)

    def test_only_a_count_of_seal_skips_is_ever_logged_never_a_mint(self):
        o = Oracle(True)
        e = self.sealed(T_AFTER_FINAL, o)
        for _ in range(3):
            e.fire()
        e.clock.t += 61_000
        e.ex.prewarm()
        counts = e.ledger("seal_count")
        self.assertEqual([(r["seal_skips"], r["mint"]) for r in counts], [(3, "")])
        self.assertEqual(e.refusals(), [])  # no per-mint skip row
        text = Path(e.ex.fills.path).read_text() + Path(e.ex.counters_path).read_text()
        self.assertNotIn(MINT, text)
        self.assertNotIn("seal_pick", text)

    def test_seal_applies_to_a_dry_run_too(self):
        e = self.sealed(T_SEAL, None, live=False)
        e.fire()
        self.assertEqual((e.ledger("decision"), e.ex.counters.seal_skips), ([], 1))

    def test_exits_are_not_sealed(self):
        e = self.env()
        e.open_position()
        e.set_time(T_SEAL)  # far past every stage of the plan: the exit goes out, the seal does not touch it
        e.ex.exit_tick(e.clock())
        self.assertEqual(len(e.rpc.sent), 2)


class PickOracleTests(Case):
    SCORE = "0.987654321"
    PNL = "-123456789"

    def test_reads_only_the_boolean_and_never_holds_a_score_or_pnl(self):
        a, b = "A" * 43 + "1", "B" * 43 + "2"
        f = self.tmp / "picks.jsonl"
        f.write_text(json.dumps({"mint": a, "pick": True, "score": float(self.SCORE), "pnl_sol": int(self.PNL), "positions": [1, 2]}) + "\n"
                     + json.dumps({"pick": False, "mint": b, "net": self.PNL}) + "\n")
        o = h.JsonlPickOracle(f)
        self.assertIs(o(a), True)
        self.assertIs(o(b), False)
        held = repr(vars(o)) + repr(o._flags)
        for sentinel in (self.SCORE, self.PNL, "positions"):
            self.assertNotIn(sentinel, held)
        self.assertEqual(set(o._flags.values()), {True, False})

    def test_unknown_mint_and_ambiguous_rows_are_undecided_not_false(self):
        f = self.tmp / "picks.jsonl"
        c = "C" * 43 + "3"
        f.write_text(json.dumps({"mint": c, "pick": True, "other": {"pick": False}}) + "\n")
        o = h.JsonlPickOracle(f)
        with self.assertRaises(h.OracleUndecided):
            o(c)  # two pick tokens on one line: not trusted
        with self.assertRaises(h.OracleUndecided):
            o("D" * 43 + "4")

    def test_incremental_partial_line_and_rotation(self):
        f = self.tmp / "picks.jsonl"
        a, b = "A" * 43 + "1", "B" * 43 + "2"
        f.write_text(json.dumps({"mint": a, "pick": False}) + "\n" + '{"mint": "' + b + '", "pi')
        o = h.JsonlPickOracle(f)
        self.assertIs(o(a), False)
        with self.assertRaises(h.OracleUndecided):
            o(b)  # half-written
        with f.open("a") as fh:
            fh.write('ck": true}\n')
        self.assertIs(o(b), True)
        f.unlink()
        f.write_text(json.dumps({"mint": b, "pick": False}) + "\n")  # a new file: the old flags are dropped
        self.assertIs(o(b), False)
        with self.assertRaises(h.OracleUndecided):
            o(a)

    def test_missing_file_raises_so_the_executor_refuses(self):
        with self.assertRaises(Exception):
            h.JsonlPickOracle(self.tmp / "nope.jsonl")(MINT)


# --- key hygiene helpers ----------------------------------------------------------------------------------------------------------


def key_forms(kp: Keypair) -> list[str]:
    raw, sec = bytes(kp), bytes(kp.secret())
    return [str(kp), raw.hex(), sec.hex(), base64.b64encode(raw).decode(), base64.b64encode(sec).decode(), json.dumps(list(raw)),
            json.dumps(list(raw), separators=(",", ":")), ", ".join(map(str, raw)), ",".join(map(str, sec))]


def no_key_in(tc: unittest.TestCase, kp: Keypair, root: Path, *texts: str) -> None:
    blobs = list(texts)
    for p in Path(root).rglob("*"):
        if p.is_file():
            blobs.append(p.read_bytes().decode("utf-8", "replace"))
    for form in key_forms(kp):
        for blob in blobs:
            tc.assertNotIn(form, blob)


# --- start conditions ---------------------------------------------------------------------------------------------------------------


class StartTests(Case):
    def cfg(self, **kw):
        return {"state_dir": str(self.tmp / "state"), "end_ms": T0 + 86_400_000, **kw}

    def root(self, with_file=True) -> Path:
        r = self.tmp / "repo"
        (r / "EXP").mkdir(parents=True, exist_ok=True)
        p = r / h.EXP024_PART1
        if with_file:
            p.write_text("# prereg\n")
        elif p.exists():
            p.unlink()
        return r

    def test_live_start_needs_live_ok_exp024_and_an_explicit_end(self):
        r = self.root()
        self.assertEqual(h.start_refusal(self.cfg(), r), "live_ok_missing")
        self.make_live_ok()
        self.assertIsNone(h.start_refusal(self.cfg(), r))
        self.assertEqual(h.start_refusal(self.cfg(halt_file="/tmp/elsewhere/HALT"), r), "config_path_override:halt_file")  # pinned in live
        self.assertEqual(h.start_refusal(self.cfg(end_ms=None), r), "end_ms_missing")
        self.assertEqual(h.start_refusal(self.cfg(), self.root(with_file=False)), "exp024_part1_missing")

    def test_exp024_file_must_be_a_real_nonempty_file(self):
        r = self.root(with_file=False)
        self.assertFalse(h.exp024_part1_present(r))
        p = r / h.EXP024_PART1
        p.write_text("")
        self.assertFalse(h.exp024_part1_present(r))
        p.unlink()
        other = self.tmp / "elsewhere.md"
        other.write_text("x")
        p.symlink_to(other)
        self.assertFalse(h.exp024_part1_present(r))
        self.assertEqual(h.EXP024_PART1, "EXP/EXP-024-h5-boostfloor-part1-prereg.md")

    def test_live_ok_is_pinned_to_etc_mal_h5(self):
        src = Path(h.__file__).read_text()  # the shipped constants, read from source (the tests patch the module attributes)
        self.assertIn('LIVE_OK_PATH = Path("/etc/mal-h5/LIVE_OK")', src)
        self.assertIn("LIVE_OK_UID = 0", src)

    def run_live_main(self, e: Env, root: Path):
        cp = self.tmp / "c.json"
        cp.write_text(json.dumps(e.conf))
        Path(e.conf["intents_file"]).write_text("")
        loaded = mock.Mock(side_effect=AssertionError("key loaded"))
        with mock.patch.object(h, "repo_root", return_value=root), mock.patch.object(pl, "load_probe_key", loaded), \
                mock.patch.object(pl, "harden_process"), mock.patch.object(pe, "rpc_env_problem", return_value=None), \
                mock.patch.object(pe, "ProbeRpc", lambda url: e.rpc), mock.patch.object(h.sim, "load_rpc_url", return_value="http://x"), \
                mock.patch.dict(os.environ, {"HELIUS_API_KEY": "k"}), contextlib.redirect_stdout(io.StringIO()) as out:
            rc = h.main(["--config", str(cp), "--live", "--once"])
        return rc, out.getvalue(), loaded

    def test_cli_live_refuses_before_touching_the_key(self):
        e = self.env(live_ok=False, end_ms=T0 + 10**9)
        rc, out, loaded = self.run_live_main(e, self.root())
        self.assertEqual((rc, "live_ok_missing" in out), (2, True))
        loaded.assert_not_called()
        self.make_live_ok()
        rc, out, loaded = self.run_live_main(e, self.root(with_file=False))
        self.assertEqual((rc, "exp024_part1_missing" in out), (2, True))
        loaded.assert_not_called()

    def test_cli_live_without_an_end_instant_refuses(self):
        e = self.env()
        rc, out, loaded = self.run_live_main(e, self.root())
        self.assertEqual((rc, "end_ms_missing" in out), (2, True))
        loaded.assert_not_called()

    def test_cli_live_loads_the_key_only_from_the_systemd_credential(self):
        kp = Keypair.from_seed(bytes(range(32)))
        cred = self.tmp / "cred"
        cred.mkdir()
        (cred / pl.CREDENTIAL_NAME).write_text(json.dumps(list(bytes(kp))))
        os.chmod(cred / pl.CREDENTIAL_NAME, 0o400)
        e = self.env(end_ms=T0 + 10**9)
        cp = self.tmp / "c.json"
        cp.write_text(json.dumps(e.conf))
        Path(e.conf["intents_file"]).write_text("")
        with mock.patch.object(h, "repo_root", return_value=self.root()), mock.patch.object(pl, "harden_process"), \
                mock.patch.object(pe, "rpc_env_problem", return_value=None), mock.patch.object(pe, "ProbeRpc", lambda url: e.rpc), \
                mock.patch.object(h.sim, "load_rpc_url", return_value="http://x"), \
                mock.patch.dict(os.environ, {"HELIUS_API_KEY": "k", "CREDENTIALS_DIRECTORY": str(cred)}), \
                contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()) as err:
            rc = h.main(["--config", str(cp), "--live", "--once"])
        self.assertEqual(rc, 0)
        self.assertIn("mode=live", out.getvalue())
        self.assertIn(str(kp.pubkey()), out.getvalue())  # only the public key is ever printed
        no_key_in(self, kp, self.tmp / "state", out.getvalue(), err.getvalue())

    def test_cli_has_no_key_path_override(self):
        e = self.env(end_ms=T0 + 10**9, key_path="/etc/mal-probe/probe-wallet.json")
        cp = self.tmp / "c.json"
        cp.write_text(json.dumps(e.conf))
        Path(e.conf["intents_file"]).write_text("")
        with mock.patch.object(h, "repo_root", return_value=self.root()), mock.patch.object(pl, "harden_process"), \
                mock.patch.object(pe, "rpc_env_problem", return_value=None), mock.patch.object(pl, "load_probe_key", side_effect=AssertionError), \
                mock.patch.dict(os.environ, {"HELIUS_API_KEY": "k"}), self.assertRaises(SystemExit):
            h.main(["--config", str(cp), "--live", "--once"])

    def test_live_buy_is_refused_if_exp024_or_live_ok_disappear_mid_run(self):
        e = self.env()
        (e.root / h.EXP024_PART1).unlink()
        e.fire()
        self.assertEqual(e.refusals(), ["exp024_part1_missing"])
        (e.root / h.EXP024_PART1).write_text("x")
        Path(e.conf["live_ok_file"]).unlink()
        e.fire()
        self.assertEqual(e.refusals(), ["exp024_part1_missing", "live_ok_missing"])
        self.assertEqual(e.rpc.sent, [])

    def test_dry_run_needs_neither(self):
        e = self.env(live=False, exp024=False)
        e.fire()
        self.assertEqual(e.refusals(), [])
        self.assertEqual(len(e.ledger("decision")), 1)


# --- single instance, anti-reset, key hygiene -----------------------------------------------------------------------------------------------


class SafetyTests(Case):
    def test_single_instance_lock(self):
        p = self.tmp / "state" / "h5-executor.lock"
        fd = h.acquire_lock(p)
        with self.assertRaises(SystemExit) as cm:
            h.acquire_lock(p)
        self.assertIn("another instance", str(cm.exception))
        os.close(fd)
        os.close(h.acquire_lock(p))

    def test_cli_second_instance_refuses(self):
        e = self.env(live=False)
        cp = self.tmp / "c.json"
        cp.write_text(json.dumps(e.conf))
        fd = h.acquire_lock(Path(e.conf["state_dir"]) / "h5-executor.lock")
        try:
            with self.assertRaises(SystemExit):
                h.main(["--config", str(cp), "--once"])
        finally:
            os.close(fd)

    def test_deleting_the_counters_cannot_reset_the_live_limits(self):
        e = self.env()
        e.fire()
        Path(e.ex.counters_path).unlink()
        with self.assertRaises(SystemExit):
            e.build()

    def test_a_restart_cannot_reset_attempts_or_the_loss(self):
        e = self.env()
        e.fire()
        e.ex.state.realized_lamports = -7
        e.ex.save()
        e.ex = e.build()
        self.assertEqual((e.ex.state.attempts, e.ex.state.realized_lamports), (1, -7))
        self.assertEqual(e.ex.counters.day(h.day_key(T0))["trades"], 1)

    def test_the_key_is_never_logged_stored_or_printed(self):
        e = Env(self.tmp, seed=bytes(range(32, 64)))
        kp = e.kp
        with contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()) as err:
            e.ex.prewarm()
            e.open_position()
            plan = e.plan()
            e.at_slot(plan["send_slot"])
            e.land_sell(plan["land_slot"], err=SLIPPAGE_ERR)
            e.at_slot(plan["deadline_slot"])  # a failed sell, an emergency sell, a halt: every alert path prints
            e.ex.on_boost_row(MINT, S0, SPS, None, 300.0)
            print(repr(e.ex), str(e.ex.h5), h.status_report(e.conf))
        self.assertIn(str(kp.pubkey()), Path(e.ex.fills.path).read_text())  # the public key is fine
        self.assertTrue(e.ledger("halt_latched"))
        no_key_in(self, kp, self.tmp, out.getvalue(), err.getvalue())
        self.assertNotIn("Keypair", repr(e.ex))

    def test_the_sentinel_check_would_catch_a_leak(self):
        kp = Keypair.from_seed(bytes(range(64, 96)))
        leak = self.tmp / "state"
        leak.mkdir()
        (leak / "oops.json").write_text(json.dumps({"k": str(kp)}))
        with self.assertRaises(AssertionError):
            no_key_in(self, kp, self.tmp)

    def test_status_report_makes_no_rpc_and_shows_no_secret(self):
        e = self.env()
        e.fire()
        out = h.status_report(e.conf)
        self.assertIn("[live] attempts=1/120", out)
        self.assertNotIn("http", out)

    def test_feed_gap_and_heartbeat(self):
        e = self.env(feed_heartbeat_max_age_ms=5_000)
        e.fire()
        self.assertEqual(e.refusals(), ["feed_stale"])  # no heartbeat yet
        e.ex.on_feed_status(False)
        e.fire()
        self.assertEqual(len(e.rpc.sent), 1)
        sub = self.tmp / "g"
        sub.mkdir()
        e2 = Env(sub)
        path = Path(e2.conf["intents_file"])
        path.write_text("")
        e2.ex.intent_tick()
        with path.open("a") as fh:
            fh.write(json.dumps({"schema": "h5_feed_v1", "gap": True}) + "\n" + json.dumps(row(e2.clock)) + "\n")
        e2.ex.intent_tick()
        self.assertEqual(e2.refusals(), ["feed_gap"])
        self.assertEqual(e2.rpc.sent, [])

    def test_watch_row_prefetches_the_pool(self):
        e = self.env()
        path = Path(e.conf["intents_file"])
        path.write_text("")
        e.ex.intent_tick()
        with path.open("a") as fh:
            fh.write(json.dumps({"schema": "h5_watch_v1", "mint": MINT, "pool": POOL}) + "\n")
        e.ex.intent_tick()
        self.assertIn(MINT, e.ex.pool_cache)

    def test_shipped_configs(self):
        root = Path(__file__).parent.parent
        dry = json.loads((root / "scripts/mal-fast/h5-executor.json").read_text())
        live = json.loads((root / "scripts/mal-fast/h5-executor-live.json").read_text())
        self.assertEqual(dry["mode"], "dryrun")
        self.assertEqual(live["mode"], "live")
        self.assertNotIn("end_ms", dry)
        self.assertEqual(live["end_ms"], 1792110600000)  # 2026-10-16T00:30:00Z: ends before the EXP-022 seal window opens
        self.assertLess(live["end_ms"], h.SEAL_START_MS)
        self.assertEqual((live["intents_file"], dry["intents_file"]), ("/srv/mal-h5-shadow", "/srv/mal-h5-shadow"))
        self.assertEqual((live["feed_heartbeat_max_age_ms"], dry["feed_heartbeat_max_age_ms"]), (60000, 60000))
        for c in (dry, live):
            for fixed in ("sell_priority_lamports", "escalated_priority_lamports", "late_sell_min_n"):
                self.assertNotIn(fixed, c)  # not configurable
        self.assertNotIn("key_path", live)
        for c in (dry, live):
            h.H5Limits.from_config(c)
            self.assertIs(c.get("jito_enabled", False), False)
            self.assertEqual(c.get("jito_tip_lamports", 0), 0)


# --- running as a plain user on the host: no dependence on the probe's files, and an RPC URL for the dry run ---------------------------------


@unittest.skipIf(os.geteuid() == 0, "root ignores directory modes")
class ProbeIndependenceTests(Case):
    def locked_live_dir(self) -> Path:
        d = self.tmp / "mal-live"  # stands in for /var/lib/mal-live: mal-live 0700, unreadable to the job user
        d.mkdir()
        (d / "state-live-dec020.json").write_text("{}")
        os.chmod(d, 0)
        self.addCleanup(os.chmod, d, 0o700)
        return d

    def test_a_dry_run_is_built_while_the_probes_directory_is_unreadable(self):
        locked = self.locked_live_dir()
        with mock.patch.object(pe, "LIVE_DIR", locked):
            with self.assertRaises(PermissionError):  # the failure seen on mal-fast-0: the probe's own precheck cannot stat its dec020 file
                pe.profile_precheck({"mode": "live"}, "live")
            sub = self.tmp / "job"
            sub.mkdir()
            e = Env(sub, live=False)
            e.fire()
        self.assertEqual(e.ex.run_mode, "dryrun")
        self.assertEqual(len(e.ledger("decision")), 1)
        self.assertIsNot(pe.profile_precheck, h.h5_precheck)  # the probe's function is put back after construction

    def test_the_probes_own_precheck_is_not_weakened(self):
        d = self.tmp / "probe-live"
        d.mkdir()
        pe.State(open={"m": {"x": 1}}, mode="live").save(pe.state_path_for(d, "live", "dec020"))
        sub = self.tmp / "job"
        sub.mkdir()
        with mock.patch.object(pe, "LIVE_DIR", d):
            Env(sub, live=False)  # H5 builds without looking at the probe's state
            with self.assertRaises(SystemExit):
                pe.profile_precheck({"mode": "live"}, "live")  # dec019 still refuses while a dec020 position is open

    def test_h5_state_that_cannot_be_read_fails_closed_in_both_modes(self):
        for live in (True, False):
            sub = self.tmp / f"s{live}"
            sub.mkdir()
            e = Env(sub, live=live)
            mode_dir = Path(e.conf["state_dir"]) / e.ex.run_mode
            os.chmod(mode_dir, 0)
            self.addCleanup(os.chmod, mode_dir, 0o700)
            with self.assertRaises(SystemExit) as cm:
                e.build()
            self.assertIn("fail closed", str(cm.exception))

    def test_h5_state_file_that_cannot_be_read_fails_closed(self):
        e = self.env()
        e.fire()
        os.chmod(e.ex.state_path, 0)
        self.addCleanup(os.chmod, e.ex.state_path, 0o600)
        with self.assertRaises(SystemExit):
            e.build()


class RpcOptionTests(Case):
    URL = "https://rpc.example.test/?api-key=SECRETKEY123"

    def cfg_file(self, e: Env) -> str:
        cp = self.tmp / "c.json"
        cp.write_text(json.dumps(e.conf))
        Path(e.conf["intents_file"]).write_text("")
        return str(cp)

    def test_dry_run_uses_the_given_url_and_never_the_env_file_and_redacts_it(self):
        e = self.env(live=False)
        seen = []

        def probe_rpc(url):
            seen.append(url)
            return e.rpc

        argv = ["--config", self.cfg_file(e), "--dry-run", "--once", "--rpc-env", "H5_TEST_RPC", "--env-file", str(self.tmp / "unreadable.env")]
        self.assertFalse(any("SECRETKEY123" in a for a in argv))  # the secret is in the environment, never on the command line
        with mock.patch.object(pe, "ProbeRpc", probe_rpc), mock.patch.object(pl, "load_probe_key", side_effect=AssertionError("key")), \
                mock.patch.dict(os.environ, {"H5_TEST_RPC": self.URL}), contextlib.redirect_stdout(io.StringIO()) as out, \
                contextlib.redirect_stderr(io.StringIO()) as err:
            os.environ.pop("HELIUS_API_KEY", None)
            rc = h.main(argv)
        self.assertEqual((rc, seen), (0, [self.URL]))  # the RPC object gets the real URL; nothing else does
        self.assertIn("mode=dryrun", out.getvalue())
        self.assertIn("rpc=https://rpc.example.test ", out.getvalue())  # scheme://host and nothing more
        blobs = [out.getvalue(), err.getvalue(), *(p.read_text() for p in self.tmp.rglob("*") if p.is_file())]
        for blob in blobs:
            self.assertNotIn("SECRETKEY123", blob)
            self.assertNotIn("api-key", blob)
        start = [json.loads(x) for x in Path(e.ex.fills.path).read_text().splitlines() if '"start"' in x]
        self.assertTrue(any(r.get("rpc") == "https://rpc.example.test" for r in start), start)

    def test_rpc_env_variable_must_exist_and_must_be_a_name_not_a_url(self):
        e = self.env(live=False)
        with contextlib.redirect_stderr(io.StringIO()) as err, self.assertRaises(SystemExit) as cm:
            h.main(["--config", self.cfg_file(e), "--once", "--rpc-env", self.URL])  # someone pastes the URL: refused, and not echoed
        self.assertEqual(cm.exception.code, 2)
        self.assertNotIn("SECRETKEY123", err.getvalue())
        with mock.patch.dict(os.environ, {}, clear=False), self.assertRaises(SystemExit) as cm2:
            os.environ.pop("H5_UNSET_RPC", None)
            h.main(["--config", self.cfg_file(e), "--once", "--rpc-env", "H5_UNSET_RPC"])
        self.assertIn("not set", str(cm2.exception))

    def test_rpc_env_without_dry_run_flag_is_still_a_dry_run(self):
        e = self.env(live=True)  # config says live, no --live flag: a dry run, so --rpc-env is allowed
        with mock.patch.object(pe, "ProbeRpc", lambda url: e.rpc), mock.patch.object(pl, "load_probe_key", side_effect=AssertionError("key")), \
                mock.patch.dict(os.environ, {"H5_TEST_RPC": self.URL}), contextlib.redirect_stdout(io.StringIO()) as out:
            rc = h.main(["--config", self.cfg_file(e), "--once", "--rpc-env", "H5_TEST_RPC"])
        self.assertEqual(rc, 0)
        self.assertIn("mode=dryrun", out.getvalue())

    def test_rpc_env_is_refused_with_live_before_anything_is_touched(self):
        e = self.env(end_ms=T0 + 10**9)
        built = mock.Mock(side_effect=AssertionError("rpc built"))
        with mock.patch.object(pe, "ProbeRpc", built), mock.patch.object(pl, "load_probe_key", side_effect=AssertionError("key")), \
                mock.patch.object(pl, "harden_process", side_effect=AssertionError("hardened")), contextlib.redirect_stderr(io.StringIO()) as err, \
                mock.patch.dict(os.environ, {"H5_TEST_RPC": self.URL}), self.assertRaises(SystemExit) as cm:
            h.main(["--config", self.cfg_file(e), "--live", "--once", "--rpc-env", "H5_TEST_RPC"])
        self.assertEqual(cm.exception.code, 2)
        self.assertIn("dry-run only", err.getvalue())
        self.assertNotIn("SECRETKEY123", err.getvalue())
        self.assertFalse((Path(e.conf["state_dir"]) / "h5-executor.lock").exists())  # refused before the lock

    def test_live_still_reads_the_key_from_the_environment_only(self):
        kp = Keypair.from_seed(bytes(range(32)))
        cred = self.tmp / "cred"
        cred.mkdir()
        (cred / pl.CREDENTIAL_NAME).write_text(json.dumps(list(bytes(kp))))
        os.chmod(cred / pl.CREDENTIAL_NAME, 0o400)
        e = self.env(end_ms=T0 + 10**9)
        root = self.tmp / "repo"
        lru = mock.Mock(return_value="http://x")
        with mock.patch.object(h, "repo_root", return_value=root), mock.patch.object(pl, "harden_process"), \
                mock.patch.object(pe, "rpc_env_problem", return_value=None), mock.patch.object(pe, "ProbeRpc", lambda url: e.rpc), \
                mock.patch.object(h.sim, "load_rpc_url", lru), \
                mock.patch.dict(os.environ, {"HELIUS_API_KEY": "k", "CREDENTIALS_DIRECTORY": str(cred)}), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(h.main(["--config", self.cfg_file(e), "--live", "--once"]), 0)
        lru.assert_called_once()
        self.assertEqual(lru.call_args.args[0], None)  # no explicit URL
        self.assertEqual(lru.call_args.kwargs, {"use_env_file": False})  # and no env file


if __name__ == "__main__":
    unittest.main()
