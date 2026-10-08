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
        e.at_slot(plan["send_slot"] - 1, advance_ms=300)
        self.assertEqual(len(e.rpc.sent), 1)
        e.rpc.calls.clear()
        e.at_slot(plan["send_slot"], advance_ms=300)
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
        e.at_slot(plan["send_slot"] + 1)  # retry 1: fresh quote and blockhash, same guard and priority
        d1 = e.sent()[2]
        sigs.append(d1["signature"])
        self.assertEqual(sell_args(d1)[1], quote_out(sell_args(d1)[0]) * 8500 // 10_000)
        self.assertEqual(d1["priority"], 55_000)
        e.land_sell(plan["land_slot"] + 2, err=SLIPPAGE_ERR)
        e.at_slot(plan["send_slot"] + 2)  # retry 2: 0.65 and the escalated priority
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
        e.fire(mint=str(Keypair().pubkey()))
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
    def test_boost_last_slice_before_335s_halts_new_buys_and_says_why(self):
        e = self.env()
        e.ex.on_boost_row(MINT, S0, SPS, None, 334.9)
        self.assertIn("boost_last_slice_lt_335", e.ex.counters.halts)
        rows = e.ledger("halt_latched")
        self.assertEqual((len(rows), rows[0]["reason"]), (1, "boost_last_slice_lt_335"))
        e.fire()
        self.assertEqual(e.refusals(), ["halt_latched:boost_last_slice_lt_335"])
        self.assertEqual(e.rpc.sent, [])

    def test_boost_335_exactly_does_not_trip_the_first_rule(self):
        e = self.env()
        e.ex.on_boost_row(MINT, S0, SPS, None, 335.0)
        self.assertEqual(e.ex.counters.halts, {})
        e.ex.on_boost_row(MINT, S0, SPS, None, 337.0)  # not < 337
        e.ex.on_boost_row(MINT, S0, SPS, None, 400.0)
        self.assertEqual(e.ex.counters.halts, {})

    def test_boost_under_337_on_two_pools_in_a_day_halts(self):
        e = self.env()
        a, b = "PoolA" + "1" * 38, "PoolB" + "1" * 38
        e.ex.on_boost_row(a, S0, SPS, None, 336.0)
        e.ex.on_boost_row(a, S0, SPS, None, 336.5)  # the same pool twice is one pool
        self.assertEqual(e.ex.counters.halts, {})
        e.ex.on_boost_row(b, S0, SPS, None, 336.9)
        self.assertEqual(list(e.ex.counters.halts), ["boost_last_slice_lt_337_twice"])

    def test_boost_337_rule_does_not_span_days(self):
        e = self.env()
        e.ex.on_boost_row("PoolA" + "1" * 38, S0, SPS, None, 336.0)
        e.clock.t += 24 * 3_600_000
        e.ex.on_boost_row("PoolB" + "1" * 38, S0, SPS, None, 336.0)
        self.assertEqual(e.ex.counters.halts, {})

    def test_boost_seconds_from_slots_and_from_the_feed_file(self):
        e = self.env()
        e.ex.on_boost_row(MINT, S0, 0.2, S0 + 1669, None)  # 333.8 s
        self.assertIn("boost_last_slice_lt_335", e.ex.counters.halts)
        sub = self.tmp / "f"
        sub.mkdir()
        e2 = Env(sub)
        path = Path(e2.conf["intents_file"])
        path.write_text("")
        e2.ex.intent_tick()
        with path.open("a") as fh:
            fh.write(json.dumps({"schema": "h5_boost_v1", "mint": MINT, "s0_slot": S0, "sps": 0.2, "last_slice_slot": S0 + 1600}) + "\n")
        e2.ex.intent_tick()
        self.assertIn("boost_last_slice_lt_335", e2.ex.counters.halts)

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
        sub = self.tmp / "m"
        sub.mkdir()
        e2 = Env(sub, late_sell_min_n=10)
        e2.ex._note_sell_landing(MINT, plan, plan["late_slot"] + 9, False)
        self.assertEqual(e2.ex.counters.halts, {})

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
        e.ex.on_boost_row(MINT, S0, SPS, None, 300.0)
        e.ex = e.build()
        self.assertIn("boost_last_slice_lt_335", e.ex.counters.halts)
        e.fire()
        self.assertEqual(e.refusals(), ["halt_latched:boost_last_slice_lt_335"])
        cp = self.tmp / "c.json"
        cp.write_text(json.dumps(e.conf))
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(h.main(["--config", str(cp), "--live", "--clear-halt", "nope"]), 1)
            self.assertEqual(h.main(["--config", str(cp), "--live", "--clear-halt", "boost_last_slice_lt_335"]), 0)
        e.ex = e.build()
        self.assertEqual(e.ex.counters.halts, {})
        self.assertEqual(e.ledger("halt_cleared")[0]["reason"], "boost_last_slice_lt_335")

    def test_dry_run_records_a_latched_halt_as_would_halt(self):
        e = self.env(live=False)
        e.ex.on_boost_row(MINT, S0, SPS, None, 300.0)
        e.fire()
        self.assertEqual(e.ledger("decision")[0]["would_have_halted"], "halt_latched:boost_last_slice_lt_335")


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
        e.clock.t = when
        if marker:
            (d / "FINAL_WRITTEN").write_text("")
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
        e.clock.t = T_SEAL
        e.at_slot(e.plan()["send_slot"], advance_ms=0)
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
        return {"state_dir": str(self.tmp / "state"), "live_ok_file": str(self.tmp / "LIVE_OK"), "end_ms": T0 + 86_400_000, **kw}

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
        (self.tmp / "LIVE_OK").write_text("")
        self.assertIsNone(h.start_refusal(self.cfg(), r))
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

    def test_live_ok_must_be_a_real_file(self):
        self.assertFalse(h.live_ok_present(self.tmp / "LIVE_OK"))
        (self.tmp / "LIVE_OK").mkdir()
        self.assertFalse(h.live_ok_present(self.tmp / "LIVE_OK"))

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
        (self.tmp / "LIVE_OK").write_text("")
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
        self.assertNotIn("end_ms", live)  # a live start needs a deliberate end instant
        self.assertNotIn("key_path", live)
        for c in (dry, live):
            h.H5Limits.from_config(c)
            self.assertIs(c.get("jito_enabled", False), False)
            self.assertEqual(c.get("jito_tip_lamports", 0), 0)


if __name__ == "__main__":
    unittest.main()
