"""H5 executor, lane mode (DEC-035 s2). One test per limit: budget, stake bounds, max open, daily stop, mark staleness, lane STOP file, the
append-only ledger; and a test that with `lane` absent nothing changes. Fixtures: test_h5_executor."""

from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

from tools import h5_executor as h
from tools import pumpswap_tx as tx
from tools.test_h5_executor import MINT, T0, Case, Env
from tools.test_probe_live import RENT

LANE_STAKE = 25_000_000
BUDGET = 300_000_000
DAY = h.day_key(T0)
DAYMS = 86_400_000


class LaneCase(Case):
    def lane_cfg(self, **kw) -> dict:
        d = self.tmp / "lane"
        base = dict(ledger_path=str(d / "ledger.jsonl"), stop_path=str(d / "STOP"), bot_id="h5-lane-test", stake_lamports=LANE_STAKE, max_open=3,
                    budget_lamports=BUDGET, daily_loss_lamports=100_000_000, fee_reserve_lamports=3_000_000, mark_stale_s=60)
        return {**base, **kw}

    def lane_env(self, lane: dict | None = None, **kw) -> Env:
        return self.env(lane=lane if lane is not None else self.lane_cfg(), **kw)

    @staticmethod
    def rows(e: Env, ev: str | None = None) -> list[dict]:
        p = e.ex.lane.path
        out = [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []
        return [r for r in out if ev is None or r["ev"] == ev]

    @staticmethod
    def lose(e: Env, lamports: int, mint: str = "seed-loss", ago_ms: int = 0) -> None:
        """Book a fee-only loss straight into the lane ledger (a stand-in for earlier lane trades)."""
        e.ex.lane.record("fee", mint, e.ex._lane_marks, e.clock() - ago_ms, sol_out=lamports, fee_network=lamports)

    def stop_path(self, e: Env) -> Path:
        return e.ex.lane.cfg.stop_path


class ConfigTests(LaneCase):
    def test_stake_outside_the_bounds_refuses_to_start(self):
        for stake in (19_999_999, 30_000_001, 0, 300_000_000):
            with self.assertRaisesRegex(SystemExit, "lane refused: lane.stake_lamports"):
                self.lane_env(self.lane_cfg(stake_lamports=stake))
        for stake in (20_000_000, 25_000_000, 30_000_000):  # the bounds are inclusive
            sub = self.tmp / f"s{stake}"
            sub.mkdir()
            e = Env(sub, lane=self.lane_cfg(stake_lamports=stake, ledger_path=str(sub / "l.jsonl"), stop_path=str(sub / "STOP")))
            self.assertEqual(e.ex.h5.stake_lamports, stake)

    def test_a_top_level_stake_that_disagrees_refuses(self):
        with self.assertRaisesRegex(SystemExit, "top-level stake_lamports"):
            self.lane_env(stake_lamports=20_000_000)

    def test_config_can_only_lower_the_dec_035_maxima(self):
        for bad in ({"max_open": 4}, {"budget_lamports": BUDGET + 1}, {"daily_loss_lamports": 100_000_001}, {"mark_stale_s": 61}, {"mark_stale_s": 0},
                    {"fee_reserve_lamports": -1}, {"surprise": 1}, {"stop_path": ""}):
            with self.assertRaises(SystemExit, msg=str(bad)):
                self.lane_env(self.lane_cfg(**bad))

    def test_lane_limits_replace_the_tier_table_but_the_wallet_cap_stays(self):
        e = self.lane_env()
        lim = e.ex.h5
        self.assertEqual((lim.stake_lamports, lim.max_open, lim.daily_loss_lamports, lim.total_loss_lamports), (LANE_STAKE, 3, 100_000_000, BUDGET))
        self.assertEqual(h.TIER_WALLET_FRAC, 0.35)
        e.ex.counters.tier_state["wallet_lamports"] = 400_000_000
        self.assertEqual(e.ex._total_stop_lamports(), 140_000_000)  # min(0.30 SOL, 35% of the wallet at tier start)
        self.assertEqual(h.RULE_Q_MAX_LAMPORTS, 40 * 10**9)


class LedgerTests(LaneCase):
    def test_send_fill_and_exit_are_ledgered_with_sol_and_fees(self):
        e = self.lane_env()
        e.fire()
        send = self.rows(e, "send")
        self.assertEqual([(r["mint"], r["stake_lamports"], r["bot_id"]) for r in send], [(MINT, LANE_STAKE, "h5-lane-test")])
        self.assertEqual(send[0]["signature"], e.ex.state.pending[MINT]["signature"])  # written before the send went out
        e.land_buy()
        fill = self.rows(e, "fill")[0]
        self.assertTrue(fill["ok"])
        self.assertEqual(fill["sol_out_lamports"], LANE_STAKE + 60_000 + RENT)  # stake, fee and the up-front token-account rent
        self.assertEqual((fill["fee_network_lamports"], fill["fee_priority_lamports"]), (tx.BASE_FEE_PER_SIGNATURE, 60_000 - tx.BASE_FEE_PER_SIGNATURE))
        self.assertEqual(fill["fee_tip_lamports"], 0)
        plan = e.plan()
        e.at_slot(plan["send_slot"])
        e.land_sell(plan["exit_slot"], proceeds=21_000_000)
        self.assertNotIn(MINT, e.ex.state.open)
        ex = self.rows(e, "exit")[0]
        self.assertEqual((ex["sol_in_lamports"], ex["sol_out_lamports"]), (21_000_000 + RENT, 60_000))  # gross proceeds with the rent back; the sell's fee out
        view = h.lane_fold(self.rows(e), {}, e.clock(), 60, "h5-lane-test")
        self.assertEqual(view.open, {})
        self.assertEqual(view.cash, -(LANE_STAKE + 60_000 + RENT) + (21_000_000 - 60_000 + RENT))  # equals the wallet's net change over the round trip

    def test_failed_buy_costs_its_fee_and_closes_the_slot(self):
        e = self.lane_env()
        e.fire()
        p = e.ex.state.pending[MINT]
        from tools.test_probe_live import meta_result
        err = {"InstructionError": [4, {"Custom": 6004}]}
        e.rpc.statuses[p["signature"]] = {"slot": 1, "confirmationStatus": "confirmed", "err": err}
        e.rpc.txs[p["signature"]] = meta_result(e.ex, MINT, delta=-60_000, fee=60_000, err=err, slot=1, logs=["Program log: slippage"])
        e.ex.advance_pending()
        fill = self.rows(e, "fill")[0]
        self.assertEqual((fill["ok"], fill["sol_out_lamports"]), (False, 60_000))
        view = h.lane_fold(self.rows(e), {}, e.clock(), 60, "h5-lane-test")
        self.assertEqual((view.cash, view.open), (-60_000, {}))

    def test_an_expired_buy_is_written_off(self):
        e = self.lane_env()
        e.fire()
        e.ex._resolve_expired(MINT, e.ex.state.pending[MINT])
        self.assertEqual([r["reason"] for r in self.rows(e, "writeoff")], ["buy_expired"])
        self.assertEqual(h.lane_fold(self.rows(e), {}, e.clock(), 60, "h5-lane-test").open, {})

    def test_the_ledger_is_append_only_and_locked(self):
        e = self.lane_env()
        self.lose(e, 1_000)
        before = e.ex.lane.path.read_text()
        self.lose(e, 2_000)
        self.assertTrue(e.ex.lane.path.read_text().startswith(before))
        with mock.patch.object(h, "LANE_LOCK_WAIT_S", 0.05):
            with e.ex.lane.locked():  # another holder of the lock: a second taker times out, and a send is refused (fail closed)
                self.assertEqual(e.ex.lane.reserve("m", "sig", {}, e.clock(), LANE_STAKE), "lane_ledger_error")
        self.assertEqual(self.rows(e, "send"), [])


class BudgetTests(LaneCase):
    def test_pre_send_check_counts_open_stakes_the_new_stake_and_the_fee_reserve(self):
        e = self.lane_env()
        self.lose(e, 247_000_000, ago_ms=DAYMS)  # -247.0: with 25 + 3 reserved the send leaves -275, inside the budget
        e.fire()
        self.assertEqual(len(e.rpc.sent), 1)
        # the same ledger, one more loss: -248.0 - (25 open + 25 new + 3) = -301 is not inside the budget
        sub = self.tmp / "b"
        sub.mkdir()
        e2 = Env(sub, lane=self.lane_cfg(ledger_path=str(sub / "l.jsonl"), stop_path=str(sub / "STOP")))
        self.lose(e2, 248_000_000, ago_ms=DAYMS)
        self.assertEqual(e2.ex.lane.reserve("m1", "s1", {}, e2.clock(), LANE_STAKE), None)  # -248 - (25 + 3) = -276: reserved, now open and unconfirmed
        self.assertEqual(e2.ex.lane.reserve("m2", "s2", {}, e2.clock(), LANE_STAKE), "lane_budget_presend")  # -248 - (25 + 25 + 3) = -301
        self.assertFalse(self.stop_path(e2).exists())  # not yet at the budget: no STOP, just no send

    def test_a_refused_pre_send_check_sends_nothing(self):
        e = self.lane_env()
        self.lose(e, 273_000_000, ago_ms=DAYMS)  # -273 - (25 + 3) = -301
        e.fire()
        self.assertEqual(e.refusals(), ["lane_budget_presend"])
        self.assertEqual((e.rpc.sent, e.ex.state.attempts, self.rows(e, "send")), ([], 0, []))

    def test_budget_stop_writes_the_stop_file_with_a_reason_and_blocks_entries(self):
        e = self.lane_env()
        self.lose(e, BUDGET)  # lane P&L = -0.30 SOL exactly
        stop = self.stop_path(e)
        self.assertTrue(stop.exists())
        self.assertTrue(stop.read_text().startswith("lane_budget_stop pnl_lamports=-300000000 budget_lamports=300000000"))
        e.fire()
        self.assertEqual(e.refusals(), ["lane_stop_file"])
        self.assertEqual(e.rpc.sent, [])
        e.ex.lane.cfg.stop_path.write_text("manager note\n")  # an existing file is never overwritten by the budget stop
        self.lose(e, 1)
        self.assertEqual(stop.read_text(), "manager note\n")

    def test_reserve_itself_writes_the_stop_file_when_the_budget_is_gone(self):
        e = self.lane_env()
        with e.ex.lane.locked():  # rows written by another executor
            e.ex.lane.append({"ev": "fee", "ts_ms": e.clock(), "mint": "x", "sol_in_lamports": 0, "sol_out_lamports": BUDGET + 5})
        self.assertEqual(e.ex.lane.reserve(MINT, "sig", {}, e.clock(), LANE_STAKE), "lane_budget_stop")
        self.assertIn("lane_budget_stop", self.stop_path(e).read_text())


class StakeTests(LaneCase):
    def test_a_stake_outside_the_bounds_is_refused_at_the_send_too(self):
        e = self.lane_env()
        for stake in (19_999_999, 30_000_001):
            self.assertEqual(e.ex.lane.evaluate(h.lane_fold([], {}, e.clock(), 60), e.clock(), stake), "lane_stake_out_of_bounds")
        self.assertIsNone(e.ex.lane.evaluate(h.lane_fold([], {}, e.clock(), 60), e.clock(), LANE_STAKE))

    def test_the_buy_is_sent_at_the_lane_stake(self):
        e = self.lane_env()
        e.fire()
        from tools.test_h5_executor import buy_args
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
        self.assertEqual(len(h.lane_fold(self.rows(e), {}, e.clock(), 60, "h5-lane-test").open), 1)
        e.fire()
        self.assertEqual(len(e.rpc.sent), 1)


class DailyStopTests(LaneCase):
    def test_minus_0_10_sol_from_the_midnight_snapshot_stops_entries_until_the_next_00_00z(self):
        e = self.lane_env()
        self.lose(e, 99_999_999)
        e.fire()
        self.assertEqual(len(e.rpc.sent), 1)  # one lamport short of the stop
        sub = self.tmp / "d"
        sub.mkdir()
        e = Env(sub, lane=self.lane_cfg(ledger_path=str(sub / "l.jsonl"), stop_path=str(sub / "STOP")))
        self.lose(e, 100_000_000)
        e.fire()
        self.assertEqual(e.refusals(), ["lane_daily_stop"])
        self.assertEqual(self.rows(e, "snap")[0]["pnl_lamports"], 0)  # the day's baseline was taken before the first loss
        self.assertFalse(self.stop_path(e).exists())  # the daily stop is not the budget stop: no STOP file
        e.jump(86_400_000 - (T0 % 86_400_000))  # 00:00Z of the next day: the baseline is today's P&L
        e.fire()
        self.assertEqual(len(e.rpc.sent), 1)
        self.assertEqual(sorted(r["day"] for r in self.rows(e, "snap")), [DAY, h.day_key(e.clock())])

    def test_the_budget_never_resets(self):
        e = self.lane_env()
        self.lose(e, BUDGET)
        e.jump(86_400_000 - (T0 % 86_400_000))
        e.fire()
        self.assertEqual(e.rpc.sent, [])
        self.assertTrue(self.stop_path(e).exists())


class MarkStaleTests(LaneCase):
    ROWS = [{"ev": "send", "bot_id": "b", "mint": "m", "stake_lamports": 25, "sol_in_lamports": 0, "sol_out_lamports": 0},
            {"ev": "fill", "bot_id": "b", "mint": "m", "ok": True, "sol_in_lamports": 0, "sol_out_lamports": 25}]

    def test_a_fresh_mark_counts_and_a_stale_or_missing_one_is_zero(self):
        now = 1_000_000
        fresh = h.lane_fold(self.ROWS, {"m": (20, now - 60_000)}, now, 60, "b")
        self.assertEqual((fresh.cash, fresh.pnl), (-25, -5))  # exactly mark_stale_s old is still fresh
        stale = h.lane_fold(self.ROWS, {"m": (20, now - 60_001)}, now, 60, "b")
        self.assertEqual(stale.pnl, -25)
        self.assertEqual(h.lane_fold(self.ROWS, {}, now, 60, "b").pnl, -25)
        self.assertEqual(h.lane_fold(self.ROWS, {"m": (20, now - 10_000)}, now, 5, "b").pnl, -25)  # a shorter configured limit applies
        self.assertEqual(h.lane_fold(self.ROWS, {"m": (20, now + 1)}, now, 60, "b").pnl, -25)  # a mark from the future is not trusted
        self.assertEqual(h.lane_fold(self.ROWS, {"m": (20, now)}, now, 60, "other").pnl, -25)  # another executor's position is not marked here

    def test_the_budget_check_uses_the_stale_rule(self):
        e = self.lane_env()
        self.lose(e, 200_000_000, ago_ms=DAYMS)
        e.ex.lane.reserve("m", "s", {}, e.clock(), LANE_STAKE)
        e.ex.lane.record("fill", "m", {}, e.clock(), ok=True, sol_out=LANE_STAKE)
        self.assertEqual(self.pnl(e, {"m": (LANE_STAKE, e.clock())}), -200_000_000)  # a fresh mark at cost: the position is worth its stake
        self.assertEqual(self.pnl(e, {"m": (LANE_STAKE, e.clock() - 60_001)}), -225_000_000)  # stale: worth 0

    def pnl(self, e: Env, marks) -> int:
        with e.ex.lane.locked():
            return h.lane_fold(e.ex.lane.read(), marks, e.clock(), 60, "h5-lane-test").pnl

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
        self.stop_path(e).parent.mkdir(parents=True, exist_ok=True)
        self.stop_path(e).write_text("manager\n")
        e.fire()
        self.assertEqual(e.refusals(), ["lane_stop_file"])
        self.assertEqual((e.rpc.sent, e.ex.state.attempts), ([], 0))
        self.stop_path(e).unlink()
        e.fire()
        self.assertEqual(len(e.rpc.sent), 1)

    def test_a_stop_file_created_during_the_build_is_caught_before_the_send(self):
        e = self.lane_env()

        def make_stop(method, params, _o=type(e.rpc).__call__):
            if method == "getLatestBlockhash":
                self.stop_path(e).parent.mkdir(parents=True, exist_ok=True)
                self.stop_path(e).write_text("late\n")
            return _o(e.rpc, method, params)

        with mock.patch.object(type(e.rpc), "__call__", lambda self, m, p: make_stop(m, p)):
            e.fire()
        self.assertEqual(e.refusals(), ["lane_stop_file"])
        self.assertEqual((e.rpc.sent, self.rows(e, "send")), ([], []))

    def test_exits_continue_under_the_stop_file(self):
        e = self.lane_env()
        e.open_position()
        self.stop_path(e).write_text("manager\n")
        plan = e.plan()
        e.at_slot(plan["send_slot"])
        self.assertEqual(e.ex.state.pending[MINT]["kind"], "sell")
        e.land_sell(plan["exit_slot"])
        self.assertNotIn(MINT, e.ex.state.open)
        self.assertEqual(len(self.rows(e, "exit")), 1)

    def test_a_lane_stop_file_in_an_unreadable_directory_blocks(self):
        e = self.lane_env()
        with mock.patch("os.stat", side_effect=PermissionError):
            self.assertTrue(e.ex.lane.stop_present())  # fail closed


class LaneAbsentTests(Case):
    def test_without_lane_nothing_changes(self):
        e = self.env()
        self.assertIsNone(e.ex.lane)
        self.assertEqual(e.ex.h5, h.H5Limits.from_config(e.conf))  # the tier table, no lane override
        self.assertEqual(e.ex.h5.stake_lamports, 20_000_000)
        e.open_position()
        plan = e.plan()
        e.at_slot(plan["send_slot"])
        e.land_sell(plan["exit_slot"])
        self.assertNotIn(MINT, e.ex.state.open)
        self.assertEqual(e.refusals(), [])
        start = e.ledger("start")[0]
        self.assertNotIn("lane", start)
        self.assertEqual(e.ex._lane_marks, {})
        self.assertEqual([p.name for p in self.tmp.iterdir() if "lane" in p.name], [])  # no lane file or directory was made

    def test_lane_free_refusals_have_no_lane_reason(self):
        e = self.env()
        Path(e.conf["stop_file"]).write_text("")
        e.fire()
        self.assertEqual(e.refusals(), ["stop_file"])
