"""H5 lane executor (tools/h5_lane_executor.py, DEC-035 s2). One test per limit, the PR #1207 review items, and the guarantee that
tools/h5_executor.py is untouched and that with `lane` absent decisions are those of H5Executor. Fixtures: test_h5_executor."""

from __future__ import annotations

import hashlib
import json
import re
import os
from pathlib import Path
from unittest import mock

from tools import h5_arena_executor as arena
from tools import h5_executor as h
from tools import h5_lane_executor as L
from tools import pumpswap_tx as tx
from tools.test_h5_executor import MINT, T0, Case, Env, buy_args
from tools.test_h5_executor_shadow import shadow_trigger
from tools.test_probe_live import RENT, meta_result

LANE_STAKE = 25_000_000
BUDGET = 300_000_000
DAYMS = 86_400_000
RESERVE = L.LANE_MIN_FEE_RESERVE
BOT = "h5-lane-test"
DISCLOSURE = "HOLDOUT_LEDGER DEC-035 h5-lane-test 0123abc"


class LaneEnv(Env):
    def build(self):
        ex = L.H5LaneExecutor(self.rpc, self.conf, self.kp, now_ms=self.clock, pick_oracle=self.oracle, root=self.root)
        self.seed_clock(ex)
        return ex


class LaneCase(Case):
    tier = "T1"  # the lane only lowers the tier's limits, so its 0.025 SOL stake needs a tier whose stake is at least that

    def setUp(self):
        super().setUp()
        if self.tier:
            h.TIER_FILE_PATH.write_text(self.tier + "\n")
            os.chmod(h.TIER_FILE_PATH, 0o644)

    def lane_cfg(self, **kw) -> dict:
        d = self.tmp / "lane"
        base = dict(ledger_path=str(d / "ledger.jsonl"), stop_path=str(d / "STOP"), bot_id=BOT, stake_lamports=LANE_STAKE, max_open=3,
                    budget_lamports=BUDGET, daily_loss_lamports=100_000_000, fee_reserve_lamports=RESERVE, mark_stale_s=60)
        return {**base, **kw}

    def lane_env(self, lane: dict | None = None, sub: str | None = None, **kw) -> LaneEnv:
        tmp = self.tmp
        if sub:
            tmp = self.tmp / sub
            tmp.mkdir()
            lane = lane or self.lane_cfg(ledger_path=str(tmp / "l.jsonl"), stop_path=str(tmp / "STOP"))
        e = LaneEnv(tmp, lane=lane if lane is not None else self.lane_cfg(), **kw)
        e.rpc.balance = 10 * 10**9
        return e

    @staticmethod
    def rows(e: Env, ev: str | None = None) -> list[dict]:
        p = e.ex.lane.path
        out = [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []
        return [r for r in out if ev is None or r["ev"] == ev]

    @staticmethod
    def view(e: Env, marks=None) -> L.LaneView:
        with e.ex.lane.locked():
            return L.lane_fold(e.ex.lane.read(), marks or {}, e.clock(), 60, BOT)

    @staticmethod
    def lose(e: Env, lamports: int, mint: str = "seed-loss", ago_ms: int = 0) -> None:
        """Book a fee-only loss straight into the lane ledger (a stand-in for earlier lane trades)."""
        e.ex.lane.record("fee", mint, e.ex._lane_marks, e.clock() - ago_ms, sol_out=lamports, fee_network=lamports)
        if ago_ms:  # the loss is from an earlier day: today's 00:00Z snapshot already includes it (the executor's start row took one before it)
            with e.ex.lane.locked():
                v = L.lane_fold(e.ex.lane.read(), {}, e.clock(), 60, BOT)
                e.ex.lane.append({"ev": "snap", "ts_ms": e.clock(), "day": h.day_key(e.clock()), "pnl_lamports": v.pnl, "pnl_cost_lamports": v.cost})

    @staticmethod
    def stop(e: Env) -> Path:
        return e.ex.lane.cfg.stop_path

    def full_trip(self, e: LaneEnv, proceeds: int = 21_000_000) -> None:
        e.open_position()
        plan = e.plan()
        e.at_slot(plan["send_slot"])
        e.land_sell(plan["exit_slot"], proceeds=proceeds)


class UnchangedTests(Case):
    def test_h5_executor_bytes_are_untouched(self):
        # arena and C1-NF pin this file; the lane lives in its own module (PR #1207 first head broke 233 of their tests by editing it)
        data = Path(h.__file__).read_bytes()
        self.assertEqual(hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()[:12], "03995837317f")
        self.assertEqual(arena.executor_blob()[:12], "03995837317f")
        self.assertIsNone(arena.sell_pin_problem())
        self.assertFalse(hasattr(h, "LaneConfig"))

    def test_without_lane_the_subclass_is_the_base(self):
        e = LaneEnv(self.tmp)
        self.assertIsNone(e.ex.lane)
        self.assertEqual(e.ex.h5, h.H5Limits.from_config(e.conf))
        e.open_position()
        plan = e.plan()
        e.at_slot(plan["send_slot"])
        e.land_sell(plan["exit_slot"])
        self.assertNotIn(MINT, e.ex.state.open)
        self.assertNotIn("lane", e.ledger("start")[0])
        self.assertEqual([p.name for p in self.tmp.iterdir() if "lane" in p.name], [])
        self.assertEqual(e.ex._lane_marks, {})


class ConfigTests(LaneCase):
    def test_stake_outside_the_bounds_refuses_to_start(self):
        for stake in (19_999_999, 30_000_001, 0, 300_000_000):
            with self.assertRaisesRegex(SystemExit, "lane refused: lane.stake_lamports"):
                self.lane_env(self.lane_cfg(stake_lamports=stake))
        for stake in (20_000_000, 30_000_000):  # the bounds are inclusive
            e = self.lane_env(sub=f"s{stake}", lane=None)
            self.assertIsNotNone(e.ex.lane)

    def test_a_tier_whose_stake_is_below_the_floor_cannot_start_a_lane(self):
        self.tier = None
        h.TIER_FILE_PATH.unlink(missing_ok=True)
        e = self.lane_env()  # T0: stake min(0.02, 0.025) = 0.02, inside the bounds
        self.assertEqual(e.ex.h5.stake_lamports, 20_000_000)
        with mock.patch.dict(h.TIERS, {"T0": {**h.TIERS["T0"], "stake_lamports": 19_000_000}}):
            with mock.patch.dict(h.H5_DEFAULT, {"stake_lamports": 19_000_000}), mock.patch.dict(h.H5_MAX, {"stake_lamports": 19_000_000}):
                with self.assertRaisesRegex(SystemExit, "effective stake"):
                    self.lane_env(sub="x", lane=self.lane_cfg(ledger_path=str(self.tmp / "x" / "l"), stop_path=str(self.tmp / "x" / "S")))

    def test_a_top_level_stake_that_disagrees_refuses(self):
        with self.assertRaisesRegex(SystemExit, "top-level stake_lamports"):
            self.lane_env(stake_lamports=20_000_000)

    def test_config_can_only_lower_the_dec_035_maxima(self):
        for bad in ({"max_open": 4}, {"budget_lamports": BUDGET + 1}, {"daily_loss_lamports": 100_000_001}, {"mark_stale_s": 61}, {"mark_stale_s": 0},
                    {"fee_reserve_lamports": RESERVE - 1}, {"surprise": 1}, {"stop_path": ""}, {"bot_id": "a:b"}):
            with self.assertRaises(SystemExit, msg=str(bad)):
                self.lane_env(self.lane_cfg(**bad))

    def test_the_fee_reserve_floor_covers_rent_buy_fee_and_ten_escalated_sells(self):
        self.assertEqual(RESERVE, 2_500_000 + 5_000 + 55_000 + 10 * (5_000 + 150_000))
        self.assertEqual(RESERVE, 4_110_000)

    def test_lane_values_only_lower_the_tier_limits(self):
        e = self.lane_env()  # T1: stake 0.10, open 3, daily 0.40, total 0.60 -> the lane's 0.025 / 3 / 0.10 / 0.30
        e.ex._refresh_tier(e.clock())  # (the tier file is read before every buy)
        lim = e.ex.h5
        self.assertEqual((lim.stake_lamports, lim.max_open, lim.daily_loss_lamports, lim.total_loss_lamports), (LANE_STAKE, 3, 100_000_000, BUDGET))
        self.tier = None
        h.TIER_FILE_PATH.unlink()
        e = self.lane_env(sub="t0", lane=None)  # T0: 0.02 / 2 / 0.08 / 0.12 stay as they are; the lane never raises them
        lim = e.ex.h5
        self.assertEqual((lim.stake_lamports, lim.max_open, lim.daily_loss_lamports, lim.total_loss_lamports), (20_000_000, 2, 80_000_000, 120_000_000))

    def test_the_wallet_cap_and_the_rule_cap_stay(self):
        e = self.lane_env()
        e.ex._refresh_tier(e.clock())
        e.ex.counters.tier_state["wallet_lamports"] = 400_000_000
        self.assertEqual(e.ex._total_stop_lamports(), 140_000_000)  # min(0.30 SOL, 35% of the wallet at tier start)
        self.assertEqual(h.RULE_Q_MAX_LAMPORTS, 40 * 10**9)
        e.fire(q_lamports=40 * 10**9 + 1)
        self.assertEqual(e.refusals(), ["q_above_rule_max"])


class LedgerTests(LaneCase):
    def test_intent_fill_and_exit_are_ledgered_with_sol_fees_and_join_keys(self):
        e = self.lane_env()
        sent_after = []
        e.rpc.send_hook = lambda _p: sent_after.append(len(self.rows(e, "send_intent")))  # the intent row exists when the transaction leaves
        e.fire()
        self.assertEqual(sent_after, [1])
        intent = self.rows(e, "send_intent")[0]
        sig = e.ex.state.pending[MINT]["signature"]
        self.assertEqual((intent["mint"], intent["stake_lamports"], intent["bot_id"], intent["signature"]), (MINT, LANE_STAKE, BOT, sig))
        e.land_buy()
        fill = self.rows(e, "fill")[0]
        self.assertEqual((fill["ok"], fill["signature"], fill["estimated"]), (True, sig, False))
        self.assertEqual(fill["sol_out_lamports"], LANE_STAKE + 60_000 + RENT)  # stake, fee and the token-account rent paid up front
        self.assertEqual((fill["fee_network_lamports"], fill["fee_priority_lamports"], fill["fee_tip_lamports"]),
                         (tx.BASE_FEE_PER_SIGNATURE, 55_000, 0))
        self.assertEqual(fill["rent_paid_lamports"], RENT)
        plan = e.plan()
        e.at_slot(plan["send_slot"])
        sell_sig = e.ex.state.pending[MINT]["signature"]
        e.land_sell(plan["exit_slot"], proceeds=21_000_000)
        ex = self.rows(e, "exit")[0]
        self.assertEqual((ex["sol_in_lamports"], ex["sol_out_lamports"], ex["rent_refunded_lamports"], ex["signature"]), (21_000_000 + RENT, 60_000, RENT, sell_sig))
        v = self.view(e)
        self.assertEqual((v.open, v.cash), ({}, -(LANE_STAKE + 60_000 + RENT) + (21_000_000 - 60_000 + RENT)))  # the wallet's net change over the trip
        start = self.rows(e, "start")[0]
        self.assertEqual((start["bot_id"], start["executor_ledger"]), (BOT, str(e.ex.fills.path)))
        self.assertEqual(len(start["code_sha256"]), 64)
        self.assertTrue(all(r["estimated"] is False for r in self.rows(e) if r["ev"] in ("send_intent", "fill", "exit")))

    def test_failed_buy_costs_its_fee_and_closes_the_slot(self):
        e = self.lane_env()
        e.fire()
        p = e.ex.state.pending[MINT]
        err = {"InstructionError": [4, {"Custom": 6004}]}
        e.rpc.statuses[p["signature"]] = {"slot": 1, "confirmationStatus": "confirmed", "err": err}
        e.rpc.txs[p["signature"]] = meta_result(e.ex, MINT, delta=-60_000, fee=60_000, err=err, slot=1, logs=["Program log: slippage"])
        e.ex.advance_pending()
        fill = self.rows(e, "fill")[0]
        self.assertEqual((fill["ok"], fill["sol_out_lamports"]), (False, 60_000))
        v = self.view(e)
        self.assertEqual((v.cash, v.open), (-60_000, {}))

    def test_a_failed_sell_books_its_fee_and_the_position_stays_open(self):
        e = self.lane_env()
        e.open_position()
        plan = e.plan()
        e.at_slot(plan["send_slot"])
        e.land_sell(plan["exit_slot"], err={"InstructionError": [4, {"Custom": 6004}]})
        fee = self.rows(e, "fee")[0]
        self.assertEqual((fee["reason"], fee["sol_out_lamports"], fee["estimated"]), ("sell_failed", 60_000, False))
        self.assertEqual(len(self.view(e).open), 1)

    def test_superseded_and_unresolved_sell_fees_are_booked_and_flagged_estimated(self):
        e = self.lane_env()
        e.open_position()
        e.ex._lane_last_fee_sig = None
        entry = {"signature": "SUPERSEDED", "h5": {"priority": 150_000}}
        e.ex._book_fee(MINT, e.ex._sell_fee(entry), "superseded_sell_failed")
        f1 = self.rows(e, "fee")[0]
        self.assertEqual((f1["reason"], f1["sol_out_lamports"], f1["signature"], f1["estimated"]), ("superseded_sell_failed", 155_000, "SUPERSEDED", True))
        # a prior with no status when the sell resolved may still land and fail: its fee is booked now
        e.rpc.statuses = {}
        with mock.patch.object(type(e.rpc), "__call__", lambda self, m, p: {"value": [None]} if m == "getSignatureStatuses" else None):
            e.ex._settle_priors(MINT, [{"signature": "PRIOR", "h5": {"priority": 150_000}}])
        f2 = self.rows(e, "fee")[-1]
        self.assertEqual((f2["reason"], f2["signature"], f2["estimated"], f2["sol_out_lamports"]), ("prior_unresolved", "PRIOR", True, 155_000))

    def test_an_abandoned_position_is_written_off(self):
        e = self.lane_env()
        pos = e.open_position()
        pos["sell_attempts"] = e.ex.sell_max_attempts - 1
        e.ex._sell_failed(pos)
        self.assertTrue(pos["abandoned"])
        self.assertEqual([r["reason"] for r in self.rows(e, "writeoff")], ["sell_abandoned"])
        self.assertEqual(self.view(e).open, {})

    def test_an_expired_buy_is_written_off(self):
        e = self.lane_env()
        e.fire()
        e.ex._resolve_expired(MINT, e.ex.state.pending[MINT])
        w = self.rows(e, "writeoff")[0]
        self.assertEqual((w["reason"], w["signature"]), ("buy_expired", self.rows(e, "send_intent")[0]["signature"]))
        self.assertEqual(self.view(e).open, {})

    def test_the_ledger_is_append_only_and_locked(self):
        e = self.lane_env()
        self.lose(e, 1_000)
        before = e.ex.lane.path.read_text()
        self.lose(e, 2_000)
        self.assertTrue(e.ex.lane.path.read_text().startswith(before))
        with mock.patch.object(L, "LANE_LOCK_WAIT_S", 0.05), e.ex.lane.locked():  # another holder of the lock: a second taker times out
            self.assertEqual(e.ex.lane.reserve("m", "sig", {}, e.clock(), LANE_STAKE), "lane_ledger_error")
        self.assertEqual(self.rows(e, "send_intent"), [])


class AtaRentTests(LaneCase):
    def test_an_estimated_buy_is_charged_the_ata_rent_it_will_be_refunded(self):
        e = self.lane_env()
        e.fire()
        p = e.ex.state.pending[MINT]
        m = {"slot": 5, "fee": 60_000, "ata_rent_pre": 0, "ata_rent_post": 0, "logs": [], "estimated": True, "err": None, "sol_delta": -(LANE_STAKE + 60_000),
             "token_delta": p["q_tokens"]}
        e.rpc.token_balance = p["q_tokens"]
        e.rpc.accounts = {p["base_ata"]: None}
        with mock.patch.object(type(e.rpc), "__call__", lambda self, mth, prm: ({"value": 2_039_280} if mth == "getBalance" and prm[0] == p["base_ata"]
                                                                                  else {"value": {"amount": str(p["q_tokens"])}})):
            e.ex._finish_buy(MINT, p, m)
        fill = self.rows(e, "fill")[0]
        self.assertEqual((fill["estimated"], fill["rent_paid_lamports"], fill["sol_out_lamports"]), (True, 2_039_280, LANE_STAKE + 60_000 + 2_039_280))

    def test_an_unreadable_ata_balance_charges_the_rent_reserve_never_zero(self):
        e = self.lane_env()
        e.fire()
        p = e.ex.state.pending[MINT]
        with mock.patch.object(type(e.rpc), "__call__", lambda self, m, prm: (_ for _ in ()).throw(RuntimeError("rpc"))):
            self.assertEqual(e.ex._ata_rent_estimate(p), 2_500_000)


class BudgetTests(LaneCase):
    def test_pre_send_check_counts_open_stakes_the_new_stake_and_the_reserve_per_position(self):
        e = self.lane_env()
        self.lose(e, 240_000_000, ago_ms=DAYMS)
        self.assertIsNone(e.ex.lane.reserve("m1", "s1", {}, e.clock(), LANE_STAKE))  # -240 - (0 + 25 + 4.11) = -269.1: reserved, open, unconfirmed
        # the second: -240 - (25 open + 25 new + 2 x 4.11) = -298.2 still fits; a third entry would not
        self.assertIsNone(e.ex.lane.reserve("m2", "s2", {}, e.clock(), LANE_STAKE))
        self.assertEqual(e.ex.lane.reserve("m3", "s3", {}, e.clock(), LANE_STAKE), "lane_budget_presend")  # -240 - (50 + 25 + 3 x 4.11) = -327
        self.assertFalse(self.stop(e).exists())  # not at the budget yet: no STOP, just no send

    def test_the_reserve_scales_with_open_positions(self):
        e = self.lane_env()
        self.lose(e, 200_000_000, ago_ms=DAYMS)
        one = L.lane_fold([], {}, 0, 60)
        rows_two_open = [{"ev": "send_intent", "bot_id": "x", "mint": f"m{i}", "stake_lamports": LANE_STAKE, "ts_ms": 1} for i in range(2)]
        two = L.lane_fold(rows_two_open, {}, 0, 60)
        two.pnl = -200_000_000
        one.pnl = -200_000_000
        self.assertIsNone(e.ex.lane.evaluate(one, 0, LANE_STAKE))
        # with two open: -200 - (50 + 25 + 3 x 4.11) = -287.3 -> fits; with a 20 SOL-lamport-larger reserve per position it must not
        big = L.LaneLedger(L.LaneConfig(**{**e.ex.lane.cfg.__dict__, "fee_reserve_lamports": 15_000_000}))
        self.assertEqual(big.evaluate(two, 0, LANE_STAKE), "lane_budget_presend")

    def test_a_refused_pre_send_check_sends_nothing(self):
        e = self.lane_env()
        self.lose(e, 270_000_000, ago_ms=DAYMS)  # -270 - (25 + 4.11) = -299.1: inside; -271 would not be
        self.lose(e, 2_000_000, ago_ms=DAYMS, mint="y")
        e.fire()
        self.assertEqual(e.refusals(), ["lane_budget_presend"])
        self.assertEqual((e.rpc.sent, e.ex.state.attempts, self.rows(e, "send_intent")), ([], 0, []))

    def test_budget_stop_writes_the_stop_file_with_a_reason_and_blocks_entries(self):
        e = self.lane_env()
        self.lose(e, BUDGET)  # lane P&L = -0.30 SOL exactly
        self.assertTrue(self.stop(e).read_text().startswith("lane_budget_stop pnl_lamports=-300000000 budget_lamports=300000000"))
        e.fire()
        self.assertEqual(e.refusals(), ["lane_stop_file"])
        self.assertEqual(e.rpc.sent, [])
        self.stop(e).write_text("manager note\n")  # an existing file is never overwritten by the budget stop
        self.lose(e, 1)
        self.assertEqual(self.stop(e).read_text(), "manager note\n")

    def test_reserve_itself_writes_the_stop_file_when_the_budget_is_gone(self):
        e = self.lane_env()
        with e.ex.lane.locked():  # rows written by another executor
            e.ex.lane.append({"ev": "fee", "ts_ms": e.clock(), "mint": "x", "sol_in_lamports": 0, "sol_out_lamports": BUDGET + 5, "estimated": False})
        self.assertEqual(e.ex.lane.reserve(MINT, "sig", {}, e.clock(), LANE_STAKE), "lane_budget_stop")
        self.assertIn("lane_budget_stop", self.stop(e).read_text())


class StakeTests(LaneCase):
    def test_a_stake_outside_the_bounds_is_refused_at_the_send_too(self):
        e = self.lane_env()
        empty = L.lane_fold([], {}, e.clock(), 60)
        for stake in (19_999_999, 30_000_001):
            self.assertEqual(e.ex.lane.evaluate(empty, e.clock(), stake), "lane_stake_out_of_bounds")
        self.assertIsNone(e.ex.lane.evaluate(empty, e.clock(), LANE_STAKE))

    def test_the_buy_is_sent_at_the_lane_stake(self):
        e = self.lane_env()
        e.fire()
        self.assertEqual(buy_args(e.sent()[0])[0], LANE_STAKE)


class MaxOpenTests(LaneCase):
    def test_at_most_three_open_read_from_the_shared_ledger(self):
        e = self.lane_env()
        for i in range(3):  # three sends, none confirmed: each counts as open (also if another executor wrote them)
            self.assertIsNone(e.ex.lane.reserve(f"other{i}", f"s{i}", {}, e.clock(), LANE_STAKE))
        e.fire()
        self.assertEqual(e.refusals(), ["lane_max_open"])
        self.assertEqual(e.rpc.sent, [])

    def test_a_landed_exit_or_a_writeoff_frees_the_slot(self):
        e = self.lane_env()
        for i in range(3):
            e.ex.lane.reserve(f"other{i}", f"s{i}", {}, e.clock(), LANE_STAKE)
        e.ex.lane.record("exit", "other0", {}, e.clock(), sol_in=1)
        e.ex.lane.record("writeoff", "other1", {}, e.clock(), reason="test")
        self.assertEqual(len(self.view(e).open), 1)
        e.fire()
        self.assertEqual(len(e.rpc.sent), 1)

    def test_two_executors_share_one_ledger(self):
        a = self.lane_env()
        other = L.LaneLedger(L.LaneConfig(**{**a.ex.lane.cfg.__dict__, "bot_id": "other-bot"}))
        for i in range(2):
            self.assertIsNone(other.reserve(f"o{i}", f"s{i}", {}, a.clock(), LANE_STAKE))
        a.fire()
        self.assertEqual(len(a.rpc.sent), 1)  # the third slot
        self.assertEqual(other.reserve("o9", "s9", {}, a.clock(), LANE_STAKE), "lane_max_open")


class DailyStopTests(LaneCase):
    def test_minus_0_10_sol_from_the_midnight_snapshot_stops_entries_until_the_next_00_00z(self):
        e = self.lane_env()
        self.lose(e, 70_000_000)
        e.fire()
        self.assertEqual(len(e.rpc.sent), 1)  # -70 with 25 + 4 more reserved: the worst case -99.1 is inside the day's 0.10 SOL
        e = self.lane_env(sub="d")
        self.lose(e, 100_000_000)
        e.fire()
        self.assertEqual(e.refusals(), ["lane_daily_stop"])
        self.assertEqual(self.rows(e, "snap")[0]["pnl_lamports"], 0)  # the day's baseline was taken before the first loss
        self.assertFalse(self.stop(e).exists())  # the daily stop is not the budget stop: no STOP file
        e.jump(DAYMS - (T0 % DAYMS))  # 00:00Z of the next day: the baseline is today's P&L
        e.fire()
        self.assertEqual(len(e.rpc.sent), 1)

    def test_the_budget_never_resets(self):
        e = self.lane_env()
        self.lose(e, BUDGET)
        e.jump(DAYMS - (T0 % DAYMS))
        e.fire()
        self.assertEqual(e.rpc.sent, [])
        self.assertTrue(self.stop(e).exists())

    def test_a_position_open_at_the_snapshot_with_no_mark_cannot_hide_its_loss(self):
        # An open filled position, a restart (no marks), then a new day's snapshot values it at 0. It exits for nothing: the cost-basis measure
        # still sees the loss of its stake against the snapshot.
        e = self.lane_env()
        lane = e.ex.lane
        lane.reserve("o1", "s1", {}, e.clock(), LANE_STAKE)
        lane.record("fill", "o1", {}, e.clock(), ok=True, sol_out=LANE_STAKE + 60_000 + RENT, signature="s1")
        e.jump(DAYMS - (T0 % DAYMS))
        lane.record("fee", "other", {}, e.clock(), sol_out=1)  # the new day's snapshot: pnl = cash, o1 valued 0 (no mark)
        snap = [r for r in self.rows(e, "snap")][-1]
        self.assertEqual(snap["pnl_cost_lamports"] - snap["pnl_lamports"], LANE_STAKE)
        lane.record("exit", "o1", {}, e.clock(), sol_in=0, sol_out=60_000, signature="s2")  # sold for ~nothing
        for stake_loss in range(3):
            lane.record("fee", f"f{stake_loss}", {}, e.clock(), sol_out=25_000_000)
        v = self.view(e)
        self.assertEqual(lane.evaluate(v, e.clock(), LANE_STAKE), "lane_daily_stop")  # -25 -3 x 25 against the cost baseline

    def test_the_daily_worst_case_never_exceeds_the_stop(self):
        # Take every trade the lane lets in and lose each one in full (stake, rent and fees): the day's loss stays inside 0.10 SOL.
        e = self.lane_env()
        lane, n, start_cash = e.ex.lane, 0, 0
        for i in range(12):
            if lane.reserve(f"w{i}", f"s{i}", {}, e.clock(), LANE_STAKE):
                break
            lane.record("fill", f"w{i}", {}, e.clock(), ok=True, sol_out=LANE_STAKE + 60_000 + RENT, signature=f"s{i}")
            lane.record("exit", f"w{i}", {}, e.clock(), sol_in=0, sol_out=60_000, signature=f"x{i}")
            n += 1
        v = self.view(e)
        self.assertGreaterEqual(n, 3)
        self.assertLess(n, 12)  # the pre-send worst-case check stopped it
        self.assertGreater(v.cash, -100_000_000)
        # and with positions still open when the day's limit bites: reserve refuses while open stakes could take the day past 0.10
        e2 = self.lane_env(sub="o")
        l2 = e2.ex.lane
        self.assertIsNone(l2.reserve("a", "sa", {}, e2.clock(), LANE_STAKE))
        self.assertIsNone(l2.reserve("b", "sb", {}, e2.clock(), LANE_STAKE))
        self.assertIsNone(l2.reserve("c", "sc", {}, e2.clock(), LANE_STAKE))  # 3 x 25 + 3 x reserve of worst case: -87.3 < 0.10 SOL
        l2.record("exit", "a", {}, e2.clock(), sol_in=0)
        self.assertEqual(self.view(e2).open.keys() == {f"{BOT}:b", f"{BOT}:c"}, True)


class MarkStaleTests(LaneCase):
    ROWS = [{"ev": "send_intent", "bot_id": "b", "mint": "m", "stake_lamports": 25, "ts_ms": 1, "sol_in_lamports": 0, "sol_out_lamports": 0},
            {"ev": "fill", "bot_id": "b", "mint": "m", "ok": True, "sol_in_lamports": 0, "sol_out_lamports": 25}]

    def test_a_fresh_mark_counts_and_a_stale_or_missing_one_is_zero(self):
        now = 1_000_000
        fresh = L.lane_fold(self.ROWS, {"m": (20, now - 60_000)}, now, 60, "b")
        self.assertEqual((fresh.cash, fresh.pnl, fresh.cost), (-25, -5, 0))  # exactly mark_stale_s old is still fresh
        self.assertEqual(L.lane_fold(self.ROWS, {"m": (20, now - 60_001)}, now, 60, "b").pnl, -25)
        self.assertEqual(L.lane_fold(self.ROWS, {}, now, 60, "b").pnl, -25)
        self.assertEqual(L.lane_fold(self.ROWS, {"m": (20, now - 10_000)}, now, 5, "b").pnl, -25)  # a shorter configured limit applies
        self.assertEqual(L.lane_fold(self.ROWS, {"m": (20, now + 1)}, now, 60, "b").pnl, -25)  # a mark from the future is not trusted
        self.assertEqual(L.lane_fold(self.ROWS, {"m": (20, now)}, now, 60, "other").pnl, -25)  # another executor's position is not marked here

    def test_the_executor_marks_an_open_position_from_its_exit_quote(self):
        e = self.lane_env()
        e.open_position()
        e.at_slot(e.plan()["arm_slot"])
        self.assertIn(MINT, e.ex._lane_marks)
        value, ts = e.ex._lane_marks[MINT]
        self.assertGreater(value, 0)
        self.assertEqual(ts, e.clock())


class StopFileTests(LaneCase):
    def test_a_stop_file_refuses_every_entry_and_sends_nothing(self):
        e = self.lane_env()
        self.stop(e).parent.mkdir(parents=True, exist_ok=True)
        self.stop(e).write_text("manager\n")
        e.fire()
        self.assertEqual(e.refusals(), ["lane_stop_file"])
        self.assertEqual((e.rpc.sent, e.ex.state.attempts), ([], 0))
        self.stop(e).unlink()
        e.fire()
        self.assertEqual(len(e.rpc.sent), 1)

    def test_a_stop_file_created_during_the_build_is_caught_before_the_send(self):
        e = self.lane_env()

        def make_stop(method, params, _o=type(e.rpc).__call__):
            if method == "getLatestBlockhash":
                self.stop(e).parent.mkdir(parents=True, exist_ok=True)
                self.stop(e).write_text("late\n")
            return _o(e.rpc, method, params)

        with mock.patch.object(type(e.rpc), "__call__", lambda self, m, p: make_stop(m, p)):
            e.fire()
        self.assertEqual(e.refusals(), ["lane_stop_file"])
        self.assertEqual((e.rpc.sent, self.rows(e, "send_intent")), ([], []))

    def test_exits_continue_under_the_stop_file(self):
        e = self.lane_env()
        e.open_position()
        self.stop(e).write_text("manager\n")
        plan = e.plan()
        e.at_slot(plan["send_slot"])
        self.assertEqual(e.ex.state.pending[MINT]["kind"], "sell")
        e.land_sell(plan["exit_slot"])
        self.assertNotIn(MINT, e.ex.state.open)
        self.assertEqual(len(self.rows(e, "exit")), 1)

    def test_an_unreadable_stop_path_blocks(self):
        e = self.lane_env()
        with mock.patch("os.stat", side_effect=PermissionError):
            self.assertTrue(e.ex.lane.stop_present())  # fail closed


class MalformedLedgerTests(LaneCase):
    def append_raw(self, e, text: str) -> None:
        with e.ex.lane.path.open("a") as f:
            f.write(text)

    def test_every_kind_of_bad_row_refuses_entries_and_never_crashes(self):
        for i, bad in enumerate(('{"ev": "send_intent", "bot_id": "x", "mint": "m"', "[1, 2]", "42", '"text"', "null", '{"no_ev": 1}',
                                 '{"ev": "send_intent", "bot_id": "x", "mint": "m", "stake_lamports": null, "ts_ms": 1}',
                                 '{"ev": "fee", "bot_id": "x", "mint": "m", "sol_out_lamports": "5"}',
                                 '{"ev": "fee", "bot_id": "x", "mint": 7, "sol_out_lamports": 5}')):
            e = self.lane_env(sub=f"b{i}")
            self.lose(e, 1_000)
            self.append_raw(e, bad + "\n")
            e.fire()
            self.assertEqual(e.refusals(), ["lane_ledger_error"], bad)
            self.assertEqual(e.rpc.sent, [], bad)

    def test_exits_continue_with_a_bad_ledger_and_the_exit_row_waits_in_the_backlog(self):
        e = self.lane_env()
        e.open_position()
        self.append_raw(e, "[1, 2]\n")
        plan = e.plan()
        e.at_slot(plan["send_slot"])
        e.land_sell(plan["exit_slot"])
        self.assertNotIn(MINT, e.ex.state.open)  # the exit went through
        self.assertTrue(e.ex.lane.backlog_pending())  # its row is safe on disk


class BacklogTests(LaneCase):
    def test_a_failed_write_survives_a_restart_and_entries_are_refused_until_it_lands(self):
        e = self.lane_env()
        e.fire()
        with mock.patch.object(L, "LANE_LOCK_WAIT_S", 0.05), e.ex.lane.locked():  # the fill row cannot be appended: the lock is busy
            e.land_buy()
            self.assertEqual(self.rows(e, "fill"), [])
            self.assertTrue(e.ex.lane.backlog_pending())
            self.assertTrue(e.alerts("lane_ledger_write_failed"))
            e2 = self.rebuild(e)  # a restart while the lock is still busy: the backlog is on disk, the memory flag is gone
            self.assertEqual(e2.ex.lane.check({}, e2.clock(), LANE_STAKE), "lane_backlog_pending")
        e2.ex.tick()  # the lock is free: the backlog lands
        self.assertFalse(e2.ex.lane.backlog_pending())
        self.assertEqual(len(self.rows(e2, "fill")), 1)
        self.assertEqual(self.view(e2).cash, -(LANE_STAKE + 60_000 + RENT))  # exact

    def test_a_restart_with_a_free_lock_drains_the_backlog_at_start(self):
        e = self.lane_env()
        e.fire()
        with mock.patch.object(L, "LANE_LOCK_WAIT_S", 0.05), e.ex.lane.locked():
            e.land_buy()
        self.assertTrue(e.ex.lane.backlog_pending())
        e2 = self.rebuild(e)
        self.assertFalse(e2.ex.lane.backlog_pending())
        self.assertEqual(len(self.rows(e2, "fill")), 1)

    def test_a_row_in_both_the_backlog_and_the_ledger_is_counted_once(self):
        e = self.lane_env()
        self.lose(e, 5_000)
        row = json.loads(e.ex.lane.path.read_text().splitlines()[-1])
        e.ex.lane.backlog_path.write_text(json.dumps(row) + "\n")  # a crash after the append, before the backlog was removed
        e.ex.lane.drain()
        self.assertEqual(self.view(e).cash, -5_000)
        self.assertFalse(e.ex.lane.backlog_pending())

    @staticmethod
    def rebuild(e):
        e.ex.release_lane_lock()  # the old process is gone
        e.ex = e.build()
        return e


class WriteAheadTests(LaneCase):
    def test_an_intent_without_state_is_open_at_full_stake_until_resolved_or_ten_minutes(self):
        e = self.lane_env()
        lane = e.ex.lane
        lane.reserve(MINT, "ORPHAN", {}, e.clock(), LANE_STAKE)  # the row went in, the state save never happened
        e.rpc.statuses.clear()
        e.ex._lane_reconcile(e.clock(), force=True)
        self.assertEqual(e.ex._lane_orphans, {MINT})  # unknown: counted open, entries refused
        self.assertEqual(len(self.view(e).open), 1)
        e.fire()
        self.assertEqual(e.refusals(), ["lane_unresolved_intent"])
        e.jump(L.LANE_UNRESOLVED_MS + 1)
        e.ex._lane_reconcile(e.clock(), force=True)
        w = self.rows(e, "writeoff")[-1]
        self.assertEqual((w["reason"], w["estimated"]), ("unresolved_intent_10min", True))
        self.assertEqual(w["sol_out_lamports"], LANE_STAKE + 2_500_000 + 5_000 + 55_000)  # booked at its full cost
        self.assertEqual(self.view(e).open, {})
        self.assertEqual(e.ex._lane_orphans, set())

    def test_an_intent_that_failed_on_chain_books_its_fee_on_restart(self):
        e = self.lane_env()
        e.ex.lane.reserve(MINT, "FAILED", {}, e.clock(), LANE_STAKE)
        err = {"InstructionError": [0, "x"]}
        e.rpc.statuses["FAILED"] = {"slot": 1, "confirmationStatus": "confirmed", "err": err}
        e.rpc.txs["FAILED"] = {"meta": {"fee": 60_000, "preBalances": [10**9], "postBalances": [10**9 - 60_000]}}
        e.ex._lane_reconcile(e.clock(), force=True)
        fill = self.rows(e, "fill")[-1]
        self.assertEqual((fill["ok"], fill["sol_out_lamports"]), (False, 60_000))
        self.assertEqual(self.view(e).open, {})

    def test_a_rebuilt_executor_runs_the_reconcile_at_start(self):
        e = self.lane_env()
        e.ex.lane.reserve(MINT, "ORPHAN", {}, e.clock(), LANE_STAKE)
        e.ex.release_lane_lock()
        e.ex = e.build()
        self.assertEqual(e.ex._lane_orphans, {MINT})


class CliTests(LaneCase):
    def test_lane_writeoff_appends_under_the_lock(self):
        e = self.lane_env()
        e.ex.lane.reserve(MINT, "S", {}, e.clock(), LANE_STAKE)
        self.assertEqual(L.lane_writeoff({"lane": e.conf["lane"]}, MINT, "closed by hand", now_ms=e.clock()), 0)
        w = self.rows(e, "writeoff")[-1]
        self.assertEqual((w["reason"], w["mint"]), ("manual:closed by hand", MINT))
        self.assertEqual(self.view(e).open, {})
        self.assertEqual(L.lane_writeoff({"lane": e.conf["lane"]}, MINT, "again", now_ms=e.clock()), 1)  # not open: nothing written

    def test_main_refuses_a_config_without_lane(self):
        p = self.tmp / "c.json"
        p.write_text(json.dumps({"mode": "dryrun"}))
        with self.assertRaisesRegex(SystemExit, "no `lane` key"):
            L.main(["--config", str(p)])
        self.assertIs(h.H5Executor.__module__, h.__name__)
        self.assertIsNot(h.H5Executor, L.H5LaneExecutor)  # main restores the global it swapped

    def test_sample_config_parses(self):
        cfg = json.loads((Path(h.__file__).parent.parent / "scripts/mal-fast/h5-lane-live.json.example").read_text())
        lc = L.LaneConfig.parse(cfg["lane"])
        self.assertEqual((lc.stake_lamports, lc.max_open, lc.budget_lamports, lc.daily_loss_lamports), (25_000_000, 3, 300_000_000, 100_000_000))
        self.assertTrue(str(lc.ledger_path).startswith("/var/lib/mal-live/h5-lane/"))
        h.H5Limits.from_config(cfg)


class RestartAndTwinTests(LaneCase):
    def test_a_buy_resent_after_a_restart_is_not_refused_by_its_own_row(self):
        e = self.lane_env()
        e.fire()  # intent row written, transaction sent
        p = e.ex.state.pending[MINT]
        # another bot holds two slots: this buy's own open intent is the third
        other = L.LaneLedger(L.LaneConfig(**{**e.ex.lane.cfg.__dict__, "bot_id": "other-bot"}))
        for i in range(2):
            self.assertIsNone(other.reserve(f"o{i}", f"s{i}", {}, e.clock(), LANE_STAKE))
        before = len(self.rows(e, "send_intent"))
        # the restart path: pending with sends == 0 is sent again, through reserve
        p["sends"] = 0
        e.ex._send(p, MINT)
        self.assertIn(MINT, e.ex.state.pending)  # not popped as lane_max_open
        self.assertEqual(len(self.rows(e, "send_intent")), before)  # and no second intent row
        self.assertEqual(e.ex.lane.reserve(MINT, p["signature"], {}, e.clock(), LANE_STAKE), None)
        self.assertEqual(e.ex.lane.reserve(MINT, "ANOTHER-SIG", {}, e.clock(), LANE_STAKE), "lane_max_open")  # a different send is still checked
        e.land_buy()  # it landed: the position exists and gets its exit
        self.assertIn(MINT, e.ex.state.open)

    def test_a_second_process_with_the_same_bot_id_is_refused(self):
        e = self.lane_env()
        lane = self.lane_cfg()
        with self.assertRaisesRegex(SystemExit, "another process holds bot_id"):
            self.lane_env(sub="twin", lane=lane)
        e.ex.release_lane_lock()
        self.lane_env(sub="twin2", lane=lane)  # free once the first has gone
        # a different bot_id on the same ledger is fine
        self.lane_env(sub="other", lane=self.lane_cfg(bot_id="other-bot"))

    def test_reconcile_waits_for_a_pending_backlog(self):
        e = self.lane_env()
        e.fire()
        e.land_buy()
        with mock.patch.object(L, "LANE_LOCK_WAIT_S", 0.05), e.ex.lane.locked():
            e.ex.state.open.pop(MINT)  # looks orphaned while its row waits in the backlog
            e.ex.lane._write_line(e.ex.lane.backlog_path, {"row_id": "x", "ev": "start"})
        e.ex._lane_reconcile(e.clock(), force=True)
        self.assertEqual(self.rows(e, "writeoff"), [])


class StartupTests(LaneCase):
    def test_the_entry_point_starts_in_dry_mode_and_ticks_with_the_lane_limits(self):
        """tools.h5_lane_executor.main -> h.main -> H5LaneExecutor.__init__ (the path that recursed when the property read h.H5Executor) -> one tick."""
        from tools.test_h5_executor import H5Rpc
        from tools.test_probe_executor import Clock
        state = self.tmp / "dry-state"
        state.mkdir()
        (state / "TIER").write_text("T1\n")
        cfg = {"mode": "dryrun", "intents_file": str(self.tmp / "intents.jsonl"), "state_dir": str(state), "poll_s": 5.0,
               "lane": self.lane_cfg()}
        path = self.tmp / "dry.json"
        path.write_text(json.dumps(cfg))
        rpc = H5Rpc(Clock(), quote=20 * 10**9)
        made = []
        orig_init = L.H5LaneExecutor.__init__

        def spy(me, *a, **kw):
            orig_init(me, *a, **kw)
            made.append(me)

        class FakeProbeRpc:
            def __init__(self, url):
                pass

            def __call__(self, method, params):
                return rpc(method, params)

        with mock.patch.dict(os.environ, {"FAKE_RPC": "http://stub.invalid"}), mock.patch.object(L.H5LaneExecutor, "__init__", spy), \
                mock.patch.object(L.pe, "ProbeRpc", FakeProbeRpc), mock.patch.object(L.h.sim, "load_rpc_url", lambda url, *_a, **_k: url):
            rc = L.main(["--config", str(path), "--dry-run", "--once", "--rpc-env", "FAKE_RPC"])
        self.assertEqual(rc, 0)
        self.assertIs(h.H5Executor.__module__ == h.__name__ and h.H5Executor is not L.H5LaneExecutor, True)  # the global was restored
        ex = made[0]
        self.assertTrue(ex.dry_run)
        ex._refresh_tier(ex.now_ms())
        lim = ex.h5
        self.assertEqual((ex.tier, lim.stake_lamports, lim.max_open), ("T1", 25_000_000, 3))
        self.assertIsInstance(ex.lane, L.LaneLedger)


class SealTests(LaneCase):
    def test_lane_on_means_no_seal_refusal_inside_the_cap_pick_window(self):
        self.assertEqual(h.SEAL_REASONS, {"seal_window_no_oracle", "seal_pick", "seal_oracle_error"})  # the waived set is exactly the parent's
        for oracle in (None, lambda m: True, lambda m: "x"):
            e = self.lane_env(sub=f"o{id(oracle)}", oracle=oracle, lane=self.lane_cfg(seal_disclosure=DISCLOSURE, ledger_path=str(self.tmp / f"o{id(oracle)}" / "l"),
                                                                                    stop_path=str(self.tmp / f"o{id(oracle)}" / "S")))
            e.set_time(h.SEAL_START_MS + 3_600_000)
            e.fire()
            self.assertNotIn("seal_window_no_oracle", e.refusals())
            self.assertFalse([r for r in e.refusals() if r in h.SEAL_REASONS], e.refusals())
            self.assertEqual(e.ex.counters.seal_skips, 0)

    def test_without_a_disclosure_the_seal_refuses_as_before(self):
        for bad in ("", "   "):
            e = self.lane_env(sub=f"n{len(bad)}", lane=self.lane_cfg(seal_disclosure=bad, ledger_path=str(self.tmp / f"n{len(bad)}" / "l"),
                                                                     stop_path=str(self.tmp / f"n{len(bad)}" / "S")))
            e.set_time(h.SEAL_START_MS + 3_600_000)
            e.fire()
            self.assertEqual(e.rpc.sent, [])
            self.assertEqual(e.ex.counters.seal_skips, 1)
            self.assertEqual(e.ex._seal_reason(None, e.clock()), "seal_window_no_oracle")
        e = self.lane_env(sub="nokey")  # key absent
        self.assertEqual(e.ex.lane.cfg.seal_disclosure, "")
        e.set_time(h.SEAL_START_MS + 3_600_000)
        self.assertEqual(e.ex._seal_reason(None, e.clock()), "seal_window_no_oracle")
        with self.assertRaises(SystemExit):
            self.lane_env(sub="bad", lane=self.lane_cfg(seal_disclosure=5, ledger_path=str(self.tmp / "bad" / "l"), stop_path=str(self.tmp / "bad" / "S")))

    def test_the_disclosure_is_logged_in_the_start_records(self):
        e = self.lane_env(sub="log", lane=self.lane_cfg(seal_disclosure=DISCLOSURE, ledger_path=str(self.tmp / "log" / "l"), stop_path=str(self.tmp / "log" / "S")))
        self.assertEqual(self.rows(e, "start")[0]["seal_disclosure"], DISCLOSURE)

    def test_lane_on_still_passes_through_other_refusals(self):
        e = self.lane_env(sub="pt", lane=self.lane_cfg(seal_disclosure=DISCLOSURE, ledger_path=str(self.tmp / "pt" / "l"), stop_path=str(self.tmp / "pt" / "S")))
        e.set_time(h.SEAL_START_MS + 3_600_000)
        with mock.patch.object(h.H5Executor, "_seal_reason", lambda self, trg, now: "some_other_reason"):
            self.assertEqual(e.ex._seal_reason(None, e.clock()), "some_other_reason")

    def test_lane_off_the_parent_is_unchanged(self):
        e = LaneEnv(self.tmp)
        e.set_time(h.SEAL_START_MS + 3_600_000)
        e.fire()
        self.assertEqual(e.refusals(), [])  # skips are counted only
        self.assertEqual(e.ex.counters.seal_skips, 1)
        self.assertEqual(e.ex._seal_reason(None, e.clock()), "seal_window_no_oracle")

class PoolGapWaiverTests(LaneCase):
    """waive_pool_gap: fail closed. Only a shadow row whose `gaps` are all feed-loss kinds (L.LANE_FEED_GAP_KINDS) is waived."""

    ABSENT = object()
    FEED = [{"kind": "slot_jump", "from_slot": 1, "to_slot": 99, "missed_slots": 97}]

    def env(self, waive, sub):
        d = self.tmp / sub
        d.mkdir()
        lane = self.lane_cfg(ledger_path=str(d / "l.jsonl"), stop_path=str(d / "S"))
        if waive is not self.ABSENT:
            lane["waive_pool_gap"] = waive
        e = self.lane_env(sub=None, lane=lane)
        Path(e.conf["intents_file"]).write_text("")
        e.ex.intent_tick()  # the tail starts here
        return e

    def feed_row(self, e, drop=(), **kw):
        row = shadow_trigger(e.clock, **kw)
        for k in drop:
            row.pop(k, None)
        with Path(e.conf["intents_file"]).open("a") as fh:
            fh.write(json.dumps(row) + "\n")
        return e.ex.intent_tick()

    @staticmethod
    def tags(e, ev):
        rows = LaneCase.rows(e, ev) if ev == "send_intent" else e.ledger(ev)
        return [r.get("pool_gap_waived") for r in rows]

    def assertRefusedGap(self, e):
        self.assertEqual(e.refusals()[-1:], ["feed_gap"])
        self.assertEqual(e.rpc.sent, [])
        self.assertEqual(self.rows(e, "send_intent"), [])

    def test_kinds_are_the_shadows_feed_loss_kinds(self):
        src = (Path(__file__).parent / "h5_shadow.py").read_text()
        emitted = set(re.findall(r'\._gap\(\s*"([a-z_0-9]+)"', src))  # every kind Engine._gap is called with
        self.assertTrue(emitted)
        self.assertTrue(L.LANE_FEED_GAP_KINDS <= emitted, L.LANE_FEED_GAP_KINDS - emitted)
        integrity = set(re.findall(r'p\.gaps\.append\(\{"kind": "([a-z_0-9]+)"', src))  # entries appended directly, not feed loss
        self.assertTrue({"ambiguous_mint", "announced_before_gap", "slot_below_s0"} <= integrity, integrity)
        self.assertFalse(L.LANE_FEED_GAP_KINDS & integrity)
        self.assertEqual(L.LANE_FEED_GAP_KINDS, {"slot_jump", "silence", "feed_restart", "socket_reconnect"})

    def test_a_full_gaps_list_is_refused_because_the_shadow_cuts_at_five(self):
        self.assertEqual(L.LANE_GAPS_RECORD_MAX, 5)
        e = self.env(True, "five")
        self.feed_row(e, gap=True, gaps=[{"kind": "slot_jump"}] * 5)
        self.assertRefusedGap(e)
        e = self.env(True, "four")
        self.feed_row(e, gap=True, gaps=[{"kind": "slot_jump"}] * 4)
        self.assertEqual(len(e.rpc.sent), 1)  # (the executor ledger is shared with the env above, so its refusal rows are still there)
        self.assertEqual(self.tags(e, "send_intent"), [True])

    def test_a_pre_unlinked_trigger_is_not_waived(self):
        e = self.env(True, "unl")
        self.feed_row(e, gap=True, gaps=self.FEED, base_breaks_unresolved=1, base_breaks_unresolved_settled=0)
        self.assertRefusedGap(e)
        e = self.env(True, "lnk")  # linked (unresolved 0): waived as usual
        self.feed_row(e, gap=True, gaps=self.FEED, base_breaks_unresolved=0, base_breaks_unresolved_settled=0)
        self.assertEqual(len(e.rpc.sent), 1)
        self.assertEqual(self.tags(e, "send_intent"), [True])

    def test_default_false_refuses_a_gap_pool_as_before(self):
        for i, waive in enumerate((self.ABSENT, False)):
            e = self.env(waive, f"d{i}")
            self.feed_row(e, gap=True, gaps=self.FEED)
            self.assertRefusedGap(e)

    def test_shadow_row_with_feed_loss_gaps_passes_and_is_tagged(self):
        e = self.env(True, "w")
        orig = h.parse_shadow_trigger
        gaps = self.FEED + [{"kind": "silence"}, {"kind": "feed_restart"}, {"kind": "socket_reconnect"}]
        self.assertEqual(self.feed_row(e, gap=True, gaps=gaps), 1)
        self.assertEqual(e.refusals(), [])
        self.assertEqual(len(e.rpc.sent), 1)
        self.assertEqual(self.tags(e, "send_intent"), [True])
        self.assertEqual(self.tags(e, "decision"), [True])
        dec = e.ledger("decision")[0]
        self.assertEqual(dec["pool_gap_kinds"], ["slot_jump", "silence", "feed_restart", "socket_reconnect"])
        self.assertIs(dec["gap"], True)  # the logged trigger keeps the detector's flag
        self.assertIs(e.ex.state.pending[MINT]["h5"]["trigger"]["gap"], True)  # and so does the stored one
        self.assertEqual(e.ex._pool_gap_waived, {})  # the per-mint record is consumed by the decision row
        self.assertIs(h.parse_shadow_trigger, orig)  # the module's parser is restored

    def test_integrity_kinds_and_bad_lists_are_refused(self):
        cases = {"announced": [{"kind": "announced_before_gap"}], "ambiguous": [{"kind": "ambiguous_mint"}], "below": [{"kind": "slot_below_s0", "s0": 1, "slot": 0}],
                 "unknown": [{"kind": "novel_gap"}], "mixed": self.FEED + [{"kind": "announced_before_gap"}], "mixed2": [{"kind": "ambiguous_mint"}] + self.FEED,
                 "empty": [], "string": "slot_jump", "nokind": [{"x": 1}], "notdict": ["slot_jump"], "nonstrkind": [{"kind": 5}], "none": None}
        for name, gaps in cases.items():
            e = self.env(True, f"r{name}")
            self.feed_row(e, gap=True, gaps=gaps)
            self.assertRefusedGap(e)

    def test_missing_gaps_field_is_refused(self):
        e = self.env(True, "m")
        self.feed_row(e, drop=("gaps",), gap=True)
        self.assertRefusedGap(e)

    def test_a_trigger_not_read_from_a_shadow_row_is_refused(self):
        e = self.env(True, "n")
        e.fire(gap=True)  # handed in directly: no captured gap record
        self.assertRefusedGap(e)

    def test_gap_false_trigger_is_tagged_false(self):
        e = self.env(True, "c")
        self.feed_row(e, gap=False, gaps=[])
        self.assertEqual(self.tags(e, "send_intent"), [False])
        self.assertEqual(self.tags(e, "decision"), [False])
        self.assertNotIn("pool_gap_kinds", e.ledger("decision")[0])

    def test_current_feed_gap_still_refuses(self):
        e = self.env(True, "f")
        e.ex.on_feed_status(True)
        self.feed_row(e, gap=True, gaps=self.FEED)
        self.assertRefusedGap(e)

    def test_inside_the_gap_hold_still_refuses(self):
        e = self.env(True, "h")
        e.ex._gap_until_ms = e.clock() + 5_000
        self.feed_row(e, gap=True, gaps=self.FEED)
        self.assertRefusedGap(e)

    def test_waived_still_hits_other_refusals(self):
        e = self.env(True, "s")
        self.feed_row(e, gap=True, gaps=self.FEED, q_trigger_sol=(40 * 10**9 + 1) / 1e9)
        self.assertEqual(e.refusals(), ["q_above_rule_max"])
        self.assertEqual(e.rpc.sent, [])

    def test_a_later_clean_trigger_on_the_same_mint_is_not_tagged_waived(self):
        e = self.env(True, "two")
        self.feed_row(e, gap=True, gaps=self.FEED, q_trigger_sol=(40 * 10**9 + 1) / 1e9)  # waived, then refused for q: no decision row, the record is replaced next
        self.assertEqual(MINT in e.ex._pool_gap_waived, True)
        self.feed_row(e, gap=False, gaps=[])
        self.assertEqual(self.tags(e, "decision"), [False])
        self.assertEqual(self.tags(e, "send_intent"), [False])
        self.assertEqual(e.ex._pool_gap_waived, {})

    def test_non_bool_config_refuses(self):
        for i, bad in enumerate(("true", 1, 0, None)):
            with self.assertRaises(SystemExit):
                self.env(bad, f"b{i}")


# -- the tipauth path inside the lane (QP-RULING-1103-lane-tipauth, step e). Test-only: no executor or shadow code changes. -----------------------
from tools.test_h5_executor_tipauth_gate import SHADOW_SHA, WS, tip_fields, watch_doc  # noqa: E402

FROZEN_BLOBS = {  # git blob ids (git hash-object) of the four files this PR must not change
    "tools/h5_executor.py": "03995837317f65f62db2fb6b238ecaca956d01cb",
    "tools/h5_lane_executor.py": "c04b8e31362142cfe669b28b0506162d4bea990a",
    "tools/h5_shadow.py": "f23cf492211fd79287fa6ac82fd382f1f3e39a3d",
    "tools/h5_lane_shadow.py": "ea262dfd7718fa6a24665b8d2574624f63686fc2",
}


class TipauthBlobTests(LaneCase):
    def test_the_four_h5_files_are_byte_identical_to_main(self):
        root = Path(h.__file__).parent.parent
        for rel, want in FROZEN_BLOBS.items():
            data = (root / rel).read_bytes()
            self.assertEqual(hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest(), want, rel)


class TipauthLaneCase(LaneCase):
    """H5LaneExecutor with the tipauth switch on (watch state ok) or off, fed shadow rows through intent_tick."""

    def tip_env(self, on: bool = True, sub: str = "t", watch: dict | None = None, lane: dict | None = None, **kw) -> LaneEnv:
        base = self.tmp / sub
        base.mkdir(exist_ok=True)
        lane = lane or self.lane_cfg(ledger_path=str(base / "l.jsonl"), stop_path=str(base / "STOP"))
        conf = {"tipauth_gate": "DEC-024-Am8", "tipauth_watch_file": str(base / "watch.json"), "tipauth_shadow_sha": SHADOW_SHA} if on else {}
        e = LaneEnv(base, lane=lane, **conf, **kw)
        e.rpc.balance = 10 * 10**9
        if on:
            self.put_watch(e, watch if watch is not None else watch_doc(e.clock()))
        Path(e.conf["intents_file"]).write_text("")
        e.ex.intent_tick()
        return e

    @staticmethod
    def rebuild(e: LaneEnv) -> L.H5LaneExecutor:
        """A restart on the same state dir and lane ledger (the old process's bot lock is released first)."""
        e.ex.release_lane_lock()
        e.ex = e.build()
        return e.ex

    @staticmethod
    def put_watch(e: Env, doc: dict | None) -> None:
        wf = Path(e.conf["tipauth_watch_file"])
        if doc is None:
            wf.unlink(missing_ok=True)
        else:
            wf.write_text(json.dumps(doc))
        e.ex._tipauth_watch_cache = None  # forget the cached read

    @staticmethod
    def feed(e: Env, tipless: bool = False, **over) -> dict:
        r = shadow_trigger(e.clock)
        if not tipless:
            r.update(tip_fields())
        r.update(over)
        with Path(e.conf["intents_file"]).open("a") as fh:
            fh.write(json.dumps(r) + "\n")
        e.ex.intent_tick()
        return r

    @staticmethod
    def skips(e: Env) -> list[tuple[str, str | None]]:
        return [(r["reason"], r.get("tipauth_reason")) for r in e.ledger("skip")]


class TipauthLimitTests(TipauthLaneCase):
    def test_control_a_passing_tagged_trigger_is_sent_at_0_02_the_min_of_class_cap_and_lane_stake(self):
        e = self.tip_env()
        self.assertEqual(e.ex._tipauth_availability(), "")
        self.feed(e)
        self.assertEqual(e.refusals(), [])
        dec = e.ledger("decision")[0]
        self.assertEqual((dec["gate_path"], dec["stake_lamports"]), ("tipauth", 20_000_000))
        self.assertEqual(len(e.rpc.sent), 1)
        self.assertEqual(self.rows(e, "send_intent")[0]["stake_lamports"], 20_000_000)
        self.assertEqual(e.ex.h5.stake_lamports, LANE_STAKE)  # outside a tagged trigger the lane stake stands; 0.02 applies only while one is handled

    def test_the_class_cap_wins_over_a_larger_lane_stake_and_ties_with_an_equal_one(self):
        for sub, stake in (("a", 30_000_000), ("b", 20_000_000)):
            e = self.tip_env(sub=sub, lane=self.lane_cfg(ledger_path=str(self.tmp / f"{sub}_l.jsonl"), stop_path=str(self.tmp / f"{sub}_STOP"), stake_lamports=stake))
            self.feed(e)
            self.assertEqual(e.ledger("decision")[0]["stake_lamports"], 20_000_000, stake)  # min(0.02, lane)
            self.assertEqual(self.rows(e, "send_intent")[0]["stake_lamports"], 20_000_000, stake)

    def test_an_untagged_ws0_trigger_keeps_the_lane_stake_with_the_switch_on(self):
        e = self.tip_env()
        self.feed(e, base_breaks_unresolved_settled=0)
        dec = e.ledger("decision")[0]
        self.assertNotIn("gate_path", dec)
        self.assertEqual(dec["stake_lamports"], LANE_STAKE)
        self.assertEqual(self.rows(e, "send_intent")[0]["stake_lamports"], LANE_STAKE)

    def test_the_lane_stop_file_refuses_a_tagged_trigger_and_sends_nothing(self):
        e = self.tip_env()
        self.stop(e).write_text("manager\n")
        self.feed(e)
        self.assertEqual(e.refusals(), ["lane_stop_file"])
        self.assertEqual((e.rpc.sent, self.rows(e, "send_intent"), e.ex.state.attempts), ([], [], 0))
        self.stop(e).unlink()
        self.feed(e, mint=MINT)
        self.assertEqual(len(e.rpc.sent), 1)

    def test_a_stop_file_created_during_the_build_is_caught_before_the_send_on_the_tagged_path(self):
        e = self.tip_env()

        def make_stop(method, params, _o=type(e.rpc).__call__):
            if method == "getLatestBlockhash":
                self.stop(e).write_text("late\n")
            return _o(e.rpc, method, params)

        with mock.patch.object(type(e.rpc), "__call__", lambda self, m, p: make_stop(m, p)):
            self.feed(e)
        self.assertEqual(e.refusals(), ["lane_stop_file"])
        self.assertEqual((e.rpc.sent, self.rows(e, "send_intent")), ([], []))

    def test_max_open_three_refuses_a_tagged_trigger(self):
        e = self.tip_env()
        for i in range(3):
            self.assertIsNone(e.ex.lane.reserve(f"other{i}", f"s{i}", {}, e.clock(), LANE_STAKE))
        self.feed(e)
        self.assertEqual(e.refusals(), ["lane_max_open"])
        self.assertEqual(e.rpc.sent, [])

    def test_the_budget_pre_send_inequality_refuses_a_tagged_trigger_at_the_tagged_stake(self):
        e = self.tip_env()
        self.lose(e, 240_000_000, ago_ms=DAYMS)
        self.assertIsNone(e.ex.lane.reserve("m1", "s1", {}, e.clock(), LANE_STAKE))
        self.assertIsNone(e.ex.lane.reserve("m2", "s2", {}, e.clock(), LANE_STAKE))
        # -240 - (50 open + 20 new + 3 x 4.11 reserve) = -322.3 < -300: refused even at the smaller tagged stake
        self.feed(e)
        self.assertEqual(e.refusals(), ["lane_budget_presend"])
        self.assertEqual(e.rpc.sent, [])
        self.assertEqual(len(self.rows(e, "send_intent")), 2)  # only the two seeded rows
        self.assertFalse(self.stop(e).exists())

    def test_the_budget_pre_send_inequality_lets_a_tagged_trigger_through_when_it_fits(self):
        e = self.tip_env()
        self.lose(e, 200_000_000, ago_ms=DAYMS)
        self.assertIsNone(e.ex.lane.reserve("m1", "s1", {}, e.clock(), LANE_STAKE))
        self.feed(e)  # -200 - (25 + 20 + 2 x 4.11) = -253.2, inside 300
        self.assertEqual(e.refusals(), [])
        self.assertEqual(len(e.rpc.sent), 1)

    def test_the_daily_stop_refuses_a_tagged_trigger(self):
        e = self.tip_env()
        self.lose(e, 100_000_000)
        self.feed(e)
        self.assertEqual(e.refusals(), ["lane_daily_stop"])
        self.assertEqual(e.rpc.sent, [])
        self.assertFalse(self.stop(e).exists())


class TipauthWriteAheadTests(TipauthLaneCase):
    def test_the_send_intent_row_is_on_disk_and_fsynced_before_the_send_and_the_tag_rides_the_executor_rows(self):
        e = self.tip_env()
        seen: dict = {}
        synced: list = []
        orig_call, orig_fsync = type(e.rpc).__call__, os.fsync

        def spy(self_, method, params):
            if method == "sendTransaction":
                seen["rows_at_send"] = [r["ev"] for r in self.rows(e)]
                seen["fsyncs_before_send"] = len(synced)
            return orig_call(self_, method, params)

        def fsync(fd):
            synced.append(fd)
            return orig_fsync(fd)

        with mock.patch.object(type(e.rpc), "__call__", spy), mock.patch("os.fsync", fsync):
            self.feed(e)
        self.assertIn("send_intent", seen["rows_at_send"])  # on disk before the transaction left
        self.assertGreaterEqual(seen["fsyncs_before_send"], 1)  # and fsynced
        intent = self.rows(e, "send_intent")[0]
        self.assertEqual((intent["mint"], intent["stake_lamports"]), (MINT, 20_000_000))
        # Observed at this head: the LANE ledger's send_intent row has no gate_path key (LaneLedger.reserve takes no tag); the "tipauth" tag rides the
        # executor's own ledger rows for the mint (decision and every later row). Pinned so a change shows. See the PR body.
        self.assertNotIn("gate_path", intent)
        tagged = [r for r in e.ledger() if r.get("mint") == MINT and r.get("gate_path") == "tipauth"]
        self.assertTrue(any(r["kind"] == "decision" for r in tagged))

    def test_a_refused_reserve_writes_no_intent_and_no_send_for_a_tagged_trigger(self):
        e = self.tip_env()
        self.stop(e).write_text("x\n")
        self.feed(e)
        self.assertEqual((self.rows(e, "send_intent"), e.rpc.sent), ([], []))


class TipauthClassStateTests(TipauthLaneCase):
    def test_the_class_latch_fires_at_minus_0_04_through_the_subclass_and_refuses_the_next_tagged_trigger(self):
        e = self.tip_env()
        self.assertIsInstance(e.ex, L.H5LaneExecutor)
        e.ex._tipauth_admit("M1")
        e.ex._book_realized(-39_999_999, "M1")
        self.assertEqual(e.ex._tipauth_availability(), "")
        e.ex._book_realized(-1, "M1")
        self.assertEqual([r["reason"] for r in e.ledger("tipauth_off")], ["class_loss_budget"])
        self.assertEqual(e.ex._tipauth_availability(), "tipauth:latched_off")
        self.feed(e)
        self.assertEqual(self.skips(e)[-1], (WS, "tipauth:latched_off"))
        self.assertEqual((e.rpc.sent, self.rows(e, "send_intent")), ([], []))
        self.assertEqual(self.rebuild(e)._tipauth_availability(), "tipauth:latched_off")  # persists across a restart of the subclass

    def test_a_landed_tagged_buy_enters_the_class_state_through_the_subclass(self):
        e = self.tip_env()
        self.feed(e)
        e.land_buy()
        self.assertEqual((e.ex.counters.tipauth["mints"], e.ex.counters.tipauth["n_landed"]), ([MINT], 1))

    def test_the_r8_looks_at_25_50_100_fire_and_pause_through_the_subclass_until_the_exact_ack(self):
        e = self.tip_env()
        for i in range(1, 25):
            e.ex._note_tipauth_landed(f"M{i}")
        self.assertEqual((e.ledger("tipauth_look"), e.ex._tipauth_availability()), ([], ""))
        e.ex._note_tipauth_landed("M25")
        self.assertEqual([r["look"] for r in e.ledger("tipauth_look")], [25])
        self.assertEqual(e.ex._tipauth_availability(), "tipauth:look_pending")
        self.feed(e)
        self.assertEqual(self.skips(e)[-1], (WS, "tipauth:look_pending"))
        self.assertEqual(e.rpc.sent, [])
        e.conf["tipauth_look_ack"] = 25
        ex = self.rebuild(e)
        self.assertEqual(ex._tipauth_availability(), "")
        for i in range(26, 51):
            ex._note_tipauth_landed(f"M{i}")
        self.assertEqual(ex._tipauth_availability(), "tipauth:look_pending")  # the stale ack 25 does not satisfy look 50
        e.conf["tipauth_look_ack"] = 50
        ex = self.rebuild(e)
        for i in range(51, 101):
            ex._note_tipauth_landed(f"M{i}")
        self.assertEqual([r["look"] for r in e.ledger("tipauth_look")], [25, 50, 100])
        self.assertEqual(ex._tipauth_availability(), "tipauth:look_pending")
        self.assertIsInstance(ex, L.H5LaneExecutor)


class TipauthWatchRefusalTests(TipauthLaneCase):
    def test_ws_path_settled_ge_1_with_the_switch_on_and_a_watch_not_ok_or_stale_is_refused_with_the_tipauth_reason_and_not_traded(self):
        cases = [
            ("tipauth:watch_hold", lambda now: watch_doc(now, verdict="hold")),
            ("tipauth:watch_stale", lambda now: watch_doc(now - 180_001)),
            ("tipauth:watch_missing", lambda now: None),
            ("tipauth:watch_sha", lambda now: watch_doc(now, shadow_sha="f" * 40)),
            ("tipauth:watch_bad", lambda now: watch_doc(now + 1)),
        ]
        for i, (want, mk) in enumerate(cases):
            with self.subTest(want=want):
                e = self.tip_env(sub=f"w{i}")
                self.put_watch(e, mk(e.clock()))
                self.assertGreaterEqual(shadow_trigger(e.clock)["slot"], 1)
                self.feed(e, base_breaks_unresolved_settled=2, base_breaks_unresolved_settled_union=2)
                self.assertEqual(self.skips(e), [(WS, want)])  # main's reason string, the tipauth cause beside it
                self.assertEqual((e.rpc.sent, e.ledger("decision"), self.rows(e, "send_intent")), ([], [], []))

    def test_the_watch_off_verdict_latches_the_path_in_the_lane(self):
        e = self.tip_env()
        self.put_watch(e, watch_doc(e.clock(), verdict="off", reason="dropped_on_tip"))
        self.feed(e)
        self.assertEqual(self.skips(e)[-1][0], WS)
        self.assertEqual(e.rpc.sent, [])
        self.assertEqual(self.rebuild(e)._tipauth_availability(), "tipauth:latched_off")

    def test_a_ws0_trigger_is_unaffected_by_a_bad_watch(self):
        e = self.tip_env(watch=watch_doc(T0, verdict="hold"))
        self.feed(e, base_breaks_unresolved_settled=0)
        self.assertEqual(e.refusals(), [])
        self.assertEqual(len(e.rpc.sent), 1)


# the fixed synthetic trigger set of the md5 decision-equivalence proof: (label, overrides on shadow_trigger + tip_fields)
EQUIV_SET = [
    ("ws0", dict(base_breaks_unresolved_settled=0)),
    ("ws1_tip_ok", dict()),
    ("ws2", dict(base_breaks_unresolved_settled=2, base_breaks_unresolved_settled_union=2)),
    ("ws_none", dict(base_breaks_unresolved_settled=None)),
    ("ws0_gap", dict(base_breaks_unresolved_settled=0, gap=True)),
    ("ws3_union0_missed_print", dict(base_breaks_unresolved_settled=3, base_breaks_unresolved_settled_union=0)),
    ("ws1_tipauth_nosettle", dict(base_tipauth={**tip_fields()["base_tipauth"], "tipauth_nosettle": 1})),
]


def decision_digest(rows: list[dict], with_tipauth_reason: bool) -> str:
    """md5 over the decision and skip rows of the executor ledger, projected to the fields that carry the decision."""
    keep = ("kind", "mint", "reason", "stake_lamports", "variant", "gate_path") + (("tipauth_reason",) if with_tipauth_reason else ())
    out = [{k: r[k] for k in keep if k in r} for r in rows if r.get("kind") in ("decision", "skip")]
    return hashlib.md5(json.dumps(out, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class TipauthEquivalenceTests(TipauthLaneCase):
    n = 0

    def run_set(self, conf: dict, on: bool = False, tipless: bool = False) -> list[dict]:
        TipauthEquivalenceTests.n += 1
        rows: list[dict] = []
        for i, (_label, over) in enumerate(EQUIV_SET):
            base = self.tmp / f"eq{TipauthEquivalenceTests.n}_{i}"
            base.mkdir()
            lane = self.lane_cfg(ledger_path=str(base / "l.jsonl"), stop_path=str(base / "STOP"))
            kw = {"tipauth_gate": "DEC-024-Am8", "tipauth_watch_file": str(base / "w.json"), "tipauth_shadow_sha": SHADOW_SHA} if on else dict(conf)
            e = LaneEnv(base, lane=lane, seed=bytes([7]) * 32, **kw)
            e.rpc.balance = 10 * 10**9
            if on:
                self.put_watch(e, watch_doc(e.clock()))
            Path(e.conf["intents_file"]).write_text("")
            e.ex.intent_tick()
            self.feed(e, tipless=tipless, **over)
            rows += [r for r in e.ledger() if r.get("kind") in ("decision", "skip")]
        return rows

    def test_switch_off_decisions_are_byte_identical_to_the_h5_lane_2_path_md5(self):
        # h5-lane-2's config has no tipauth keys. Switch-off variants: keys present but not the enable token, or the token spelled wrong.
        ref = self.run_set({})
        md5_ref = decision_digest(ref, True)
        for conf in ({"tipauth_gate": False}, {"tipauth_gate": "off"}, {"tipauth_gate": "DEC-024-am8"},
                     {"tipauth_gate": "", "tipauth_watch_file": "/nonexistent/w.json", "tipauth_shadow_sha": SHADOW_SHA}):
            rows = self.run_set(conf)
            self.assertEqual(decision_digest(rows, True), md5_ref, conf)
            self.assertEqual(rows, ref, conf)  # not only the projection: the rows themselves
        self.assertEqual(len(ref), len(EQUIV_SET))  # one decision or skip row per synthetic trigger
        self.assertFalse(any("tipauth_reason" in r or "gate_path" in r for r in ref))
        print(f"\nTIPAUTH_EQUIV switch_off_vs_lane2_path md5={md5_ref} rows={len(ref)}")

    def test_switch_on_over_triggers_with_no_union_fields_decides_as_off_md5(self):
        # QP-RULING-1103 step e: the lane1 shadow files carry no union fields, so every tipauth candidate refuses tipauth:union_missing and the
        # decision (kind, mint, reason, stake) is the OFF one; the skip rows only gain the tipauth_reason key.
        off = self.run_set({}, tipless=True)
        on = self.run_set({}, on=True, tipless=True)
        self.assertEqual(decision_digest(on, False), decision_digest(off, False))
        causes = {r.get("tipauth_reason") for r in on if r["kind"] == "skip"}
        self.assertTrue(causes <= {"tipauth:union_missing", None}, causes)
        self.assertFalse(any(r.get("gate_path") for r in on))
        print(f"TIPAUTH_EQUIV switch_on_vs_off_no_union md5={decision_digest(off, False)} rows={len(off)}")

    def test_switch_on_changes_exactly_the_union_consistent_ws_refusal_and_nothing_else(self):
        off, on = self.run_set({}), self.run_set({}, on=True)
        self.assertEqual(len(off), len(on))
        same_decision = [decision_digest([a], False) == decision_digest([b], False) for a, b in zip(off, on)]
        self.assertEqual([i for i, ok in enumerate(same_decision) if not ok], [1, 2])  # only the two ws>=1 records with union == ws and tipauth 0 change
        self.assertEqual((off[1]["kind"], off[1]["reason"]), ("skip", WS))
        self.assertEqual((on[1]["kind"], on[1]["gate_path"], on[1]["stake_lamports"]), ("decision", "tipauth", 20_000_000))
        self.assertEqual((on[2]["kind"], on[2]["gate_path"]), ("decision", "tipauth"))
        self.assertEqual((on[5]["kind"], on[5]["reason"]), ("skip", WS))  # union 0 != ws 3: a missed real print is still refused (QP F2)
