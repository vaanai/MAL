"""Tests for the DEC-019 Amendment 2 fee-arm profile: arm assignment, the HARD 0.15 SOL cost cap, code maxima, ledger tagging.
Offline: fake RPC, throwaway Keypair, no network, no key."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools import probe_executor as pe
from tools.test_probe_executor import MINT, fills
from tools import probe_live as pl
from tools.test_probe_executor import sig as mk_sig
from tools.test_probe_live import land_sell, make_live, open_position, sell_stuck_helper, signal_buy

CAP = pe.FEEARM_SPEND_CAP_LAMPORTS


class PureTests(unittest.TestCase):
    def test_cap_is_a_code_maximum_015_sol(self):
        self.assertEqual(CAP, 150_000_000)
        lim = pe.Limits.from_config({"limits_profile": "feearm", "end_ms": 9 * 10**15, "arm_seed": 1, "spend_cap_lamports": 10**12,
                                     "loss_cap_lamports": 10**12, "max_sends": 10**6, "max_attempts": 10**6, "size_lamports": 10**9,
                                     "max_open": 9, "max_days": 99})
        self.assertEqual((lim.spend_cap_lamports, lim.loss_cap_lamports, lim.max_sends, lim.max_attempts), (CAP, CAP, 300, 150))
        self.assertEqual((lim.size_lamports, lim.max_open, lim.max_days, lim.end_ms), (5_000_000, 2, 5, pe.FEEARM_END_MS))
        low = pe.Limits.from_config({"limits_profile": "feearm", "end_ms": 5, "arm_seed": 1, "spend_cap_lamports": 1_000_000})
        self.assertEqual((low.spend_cap_lamports, low.end_ms), (1_000_000, 5))  # config may lower
        with self.assertRaises(ValueError):
            pe.Limits(profile="feearm", spend_cap_lamports=0, end_ms=5)

    def test_bad_config_refused(self):
        base = {"limits_profile": "feearm", "end_ms": pe.FEEARM_END_MS}
        for extra in ({}, {"arm_seed": True}, {"arm_seed": -1}, {"arm_seed": 1.5}, {"arm_seed": 1, "arms": [1]},
                      {"arm_seed": 1, "priority_lamports": 600_000}, {"arm_seed": 1, "priority_lamports": 155_000},
                      {"arm_seed": 1, "third_arm": "yes"}, {"arm_seed": 1, "feearm_sell_attempts": 4},
                      {"arm_seed": 1, "dec020_stops": {}}):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                pe.Limits.from_config({**base, **extra})
        pe.Limits.from_config({**base, "arm_seed": 0, "priority_lamports": 500_000})
        with self.assertRaises(ValueError):  # end instant must be explicit
            pe.Limits.from_config({"limits_profile": "feearm", "arm_seed": 1})

    def test_arm_assignment_deterministic_and_balanced(self):
        seq = [pe.feearm_pick(42, i)[0] for i in range(200)]
        self.assertEqual(seq, [pe.feearm_pick(42, i)[0] for i in range(200)])
        self.assertNotEqual(seq, [pe.feearm_pick(43, i)[0] for i in range(200)])
        self.assertEqual({a: seq.count(a) for a in set(seq)}, {"control": 100, "test": 100})
        for i in range(0, 200, 2):
            self.assertEqual(sorted(seq[i:i + 2]), ["control", "test"])  # exact balance in every block
        self.assertEqual({pe.feearm_pick(1, i) for i in range(10)}, {("control", 500_000), ("test", 155_000)})
        self.assertNotEqual(seq, ["control", "test"] * 100)  # randomised, not alternating

    def test_third_arm_default_off_and_balanced_when_on(self):
        self.assertNotIn("low", {pe.feearm_pick(3, i)[0] for i in range(60)})
        seq = [pe.feearm_pick(3, i, True) for i in range(300)]
        names = [s[0] for s in seq]
        self.assertEqual({a: names.count(a) for a in ("control", "test", "low")}, {"control": 100, "test": 100, "low": 100})
        self.assertIn(("low", 15_000), seq)

    def test_cap_refuses_the_send_that_would_cross_it_including_open_position(self):
        lim = pe.Limits.from_config({"limits_profile": "feearm", "end_ms": pe.FEEARM_END_MS, "arm_seed": 1})
        spend, pr = lim.size_lamports, 500_000
        wc = pe.feearm_new_buy_worst_case(spend, pr)
        self.assertEqual(wc, 5_000_000 + 505_000 + 2_500_000 + 3 * 505_000)
        st = pe.State(mode="live")
        st.open["A"] = {"buy_cost_lamports": 12_500_000, "sell_attempts": 0, "arm_priority": 155_000}
        base = pe.feearm_committed_cost(st)
        self.assertEqual(base, 12_500_000 + 3 * 160_000)
        st.realized_lamports = -(CAP - base - wc)  # exactly at the cap: allowed
        self.assertIsNone(pe.feearm_buy_refusal(lim, st, spend, pr))
        st.realized_lamports -= 1  # one lamport over: refused
        self.assertEqual(pe.feearm_buy_refusal(lim, st, spend, pr), "spend_cap")
        self.assertIsNone(pe.feearm_buy_refusal(lim, st, spend, 155_000))  # the cheaper arm's worst case still fits
        st.pending["B"] = {"kind": "buy", "spend": spend, "arm_priority": 500_000}  # an in-flight buy counts too
        self.assertEqual(pe.feearm_buy_refusal(lim, st, spend, 155_000), "spend_cap")

    def test_net_gain_does_not_buy_headroom_and_fees_not_double_counted(self):
        st = pe.State(mode="live", realized_lamports=+50_000_000)
        self.assertEqual(pe.feearm_committed_cost(st), 0)
        st.realized_lamports = -1_010_000  # one closed control round trip's fees: counted once
        self.assertEqual(pe.feearm_committed_cost(st), 1_010_000)

    def test_sends_cap(self):
        lim = pe.Limits.from_config({"limits_profile": "feearm", "end_ms": pe.FEEARM_END_MS, "arm_seed": 1})
        st = pe.State(mode="live", feearm={"sends": 298})
        self.assertIsNone(pe.feearm_buy_refusal(lim, st, 5_000_000, 155_000))
        st.feearm["sends"] = 299
        self.assertEqual(pe.feearm_buy_refusal(lim, st, 5_000_000, 155_000), "max_sends")

    def test_expected_cost_arithmetic_quoted_in_amendment_2(self):
        rt_control, rt_test = 2 * (5_000 + 500_000), 2 * (5_000 + 155_000)
        self.assertEqual((rt_control, rt_test), (1_010_000, 320_000))
        price_pool = 244_500  # 0.005 SOL x 4.89% (probe-final mean loss 6.9% of stake less 2.02% fees)
        pair = rt_control + rt_test + 2 * price_pool
        self.assertEqual(pair, 1_819_000)
        self.assertEqual(CAP // pair, 82)  # pairs the cap pays for in expectation
        last_buy = pe.feearm_new_buy_worst_case(5_000_000, 500_000)
        self.assertEqual(last_buy, 9_520_000)
        self.assertEqual((CAP - last_buy) // pair, 77)  # about 75 to 77 per arm once the last buy's reservation is held back


class ExecTests(unittest.TestCase):
    def setUp(self):
        self._d = tempfile.TemporaryDirectory()
        self.tmp = Path(self._d.name).resolve()
        p = mock.patch.object(pe, "LIVE_DIR", self.tmp)
        p.start()
        self.addCleanup(p.stop)
        pe.State(mode="live", bought=["OLD"]).save(self.tmp / "state-live.json")
        self.cfg = {"limits_profile": "feearm", "end_ms": pe.FEEARM_END_MS, "arm_seed": 7,
                    "fill_log": str(self.tmp / pe.FEEARM_FILL_LOG), "sell_retries": 2}

    def tearDown(self):
        self._d.cleanup()

    def mk(self, **kw):
        return make_live(self.tmp, **{**self.cfg, **kw})

    def test_arm_logged_and_durable_before_first_send_and_priority_matches(self):
        ex, rpc, clock, kp, conf = self.mk()
        seen = {}

        def hook(_params):
            seen["state_arm"] = json.loads((self.tmp / "state-live-feearm.json").read_text())["feearm"]["arms"].get(MINT)
            seen["rows"] = [r["kind"] for r in fills(conf)]

        rpc.send_hook = hook
        p = signal_buy(ex, clock)
        want = pe.feearm_pick(7, 0)
        self.assertEqual(seen["state_arm"], {"arm": want[0], "priority": want[1], "index": 0})
        self.assertEqual(seen["rows"][-1], "arm_assigned")
        self.assertEqual(p["arm_priority"], want[1])
        self.assertEqual(ex.state.feearm["sends"], 1)

    def test_every_ledger_row_carries_the_arm_and_endpoints(self):
        ex, rpc, clock, kp, conf = self.mk()
        open_position(ex, rpc, clock)
        clock.t += 31 * 60_000
        ex.poll_positions()
        land_sell(ex, rpc, proceeds=9_800_000, fee=160_000)
        rows = fills(conf)
        self.assertEqual([r["kind"] for r in rows if r["mint"] == MINT], ["arm_assigned", "buy", "sell"])
        want = pe.feearm_pick(7, 0)
        for r in rows:
            self.assertIn("fee_arm", r)
            self.assertEqual(r["arm_seed"], 7)
            self.assertEqual(r["limits_profile"], "feearm")
            if r["mint"] == MINT:
                self.assertEqual((r["fee_arm"], r["arm_priority_lamports"]), want)
        buy = [r for r in rows if r["kind"] == "buy"][0]
        self.assertEqual((buy["outcome"], buy["slots_between"] is not None, buy["fee_lamports"], buy["priority_lamports"]),
                         ("landed", True, 500_000, want[1]))
        self.assertEqual(ex.state.feearm["fees_paid"], 500_000 + 160_000)  # the shared land_buy helper pays 500_000

    def test_stop_file_honoured_and_dropped_outcome(self):
        ex, rpc, clock, kp, conf = self.mk()
        (self.tmp / "STOP").write_text("")
        signal_buy(ex, clock)
        r = fills(conf)[-1]
        self.assertEqual((r["reason"], r["fee_arm"]), ("limit:stop_file", None))
        self.assertEqual(rpc.sent, [])
        (self.tmp / "STOP").unlink()
        signal_buy(ex, clock)
        rpc.height = 5_000  # past lastValidBlockHeight: never landed
        for _ in range(3):
            clock.t += 6_000
            ex.advance_pending()
        b = [x for x in fills(conf) if x["kind"] == "buy"][0]
        self.assertEqual((b["outcome"], b["fail_class"], b["fee_arm"]), ("dropped", "expired", pe.feearm_pick(7, 0)[0]))
        self.assertNotIn(MINT, ex.state.pending)

    def test_executor_cap_refuses_before_any_send(self):
        ex, rpc, clock, kp, conf = self.mk()
        arm = pe.feearm_pick(7, 0)
        wc = pe.feearm_new_buy_worst_case(ex.limits.size_lamports, arm[1], ex.sell_max_attempts)
        ex.state.realized_lamports = -(CAP - wc + 1)
        self.assertIsNone(signal_buy(ex, clock))
        self.assertEqual(fills(conf)[-1]["reason"], "limit:spend_cap")
        self.assertEqual((rpc.sent, ex.state.attempts), ([], 0))
        ex.state.realized_lamports = -(CAP - wc)  # exactly at the cap is allowed
        self.assertIsNotNone(signal_buy(ex, clock))
        self.assertEqual(len(rpc.sent), 1)

    def test_send_guard_blocks_first_send_of_buy_over_cap(self):
        ex, rpc, clock, kp, conf = self.mk()
        p = {"kind": "buy", "spend": 5_000_000, "arm_priority": 500_000, "sends": 0}
        ex.state.pending["X"] = p
        ex.state.realized_lamports = -CAP
        ex._send(p, "X")
        self.assertEqual((rpc.sent, p["sends"], ex.state.feearm.get("sends", 0)), ([], 0, 0))

    def test_attempt_cap_and_sell_attempts_reserved(self):
        ex, rpc, clock, kp, conf = self.mk()
        self.assertEqual(ex.sell_max_attempts, 3)
        ex.state.attempts = 150
        signal_buy(ex, clock)
        self.assertEqual(fills(conf)[-1]["reason"], "limit:max_attempts")

    def test_seed_cannot_change_and_wallet_must_be_flat(self):
        ex, rpc, clock, kp, conf = self.mk()
        signal_buy(ex, clock)
        with self.assertRaises(SystemExit):
            self.mk(arm_seed=8)
        ex.state.pending.clear()
        ex.state.save(ex.state_path)
        (self.tmp / "h5").mkdir()
        pe.State(mode="live", open={"M": {}}).save(self.tmp / "h5" / "state-live.json")
        with self.assertRaises(SystemExit):
            self.mk()

    def test_fill_log_and_stop_files_are_fixed(self):
        with self.assertRaises(SystemExit):
            self.mk(fill_log=str(self.tmp / "probe-fills.jsonl"))
        with self.assertRaises(SystemExit):
            self.mk(stop_file=str(self.tmp / "elsewhere"))

    def test_other_profiles_refuse_while_feearm_has_a_position(self):
        pe.State(mode="live", open={"M": {}}).save(self.tmp / "state-live-feearm.json")
        with self.assertRaises(SystemExit):
            make_live(self.tmp)  # dec019 profile

    def test_estimated_meta_counts_the_rent_reserve_so_two_positions_cannot_cross_the_cap(self):
        ex, rpc, clock, kp, conf = self.mk()
        p = signal_buy(ex, clock)
        rpc.statuses[p["signature"]] = {"slot": 5, "confirmationStatus": "confirmed", "err": None}
        rpc.token_balance = 999
        ex.advance_pending()
        clock.t += pl.META_FALLBACK_MS
        ex.advance_pending()  # no meta for 60 s: booked from the estimate
        pos = ex.state.open[MINT]
        fee = 5_000 + p["arm_priority"]
        self.assertEqual(pos["buy_cost_lamports"], p["spend"] + fee + pe.FEEARM_RENT_RESERVE_LAMPORTS)
        self.assertTrue([r for r in fills(conf) if r["kind"] == "buy"][-1]["estimated"])
        committed = pe.feearm_committed_cost(ex.state, ex.sell_max_attempts)
        arm1 = pe.feearm_pick(7, 1)
        wc = pe.feearm_new_buy_worst_case(ex.limits.size_lamports, arm1[1], ex.sell_max_attempts)
        # Without the reserve the position would look 2.5M cheaper: this realized loss puts the old accounting exactly AT the cap
        # (admitted), so the real accounting is 2.5M over it and the second buy must be refused.
        ex.state.realized_lamports = -(CAP - wc - (committed - pe.FEEARM_RENT_RESERVE_LAMPORTS))
        pos["buy_cost_lamports"] -= pe.FEEARM_RENT_RESERVE_LAMPORTS  # the old (rent-blind) booking: exactly at the cap, admitted
        self.assertIsNone(pe.feearm_buy_refusal(ex.limits, ex.state, ex.limits.size_lamports, arm1[1], ex.sell_max_attempts))
        pos["buy_cost_lamports"] += pe.FEEARM_RENT_RESERVE_LAMPORTS  # the fixed booking: over the cap by the reserve, refused
        self.assertEqual(pe.feearm_buy_refusal(ex.limits, ex.state, ex.limits.size_lamports, arm1[1], ex.sell_max_attempts), "spend_cap")

    def test_stuck_then_abandoned_sells_stay_inside_the_cap_and_attempts_are_the_reserved_three(self):
        ex, rpc, clock, kp, conf = self.mk(sell_retries=99, sell_max_attempts=99)
        self.assertEqual((ex.sell_max_attempts, ex.sell_retries), (3, 3))  # config cannot exceed the 3 reserved
        pos = open_position(ex, rpc, clock)
        buy_cost = pos["buy_cost_lamports"]
        clock.t += 31 * 60_000
        sent = sell_stuck_helper(ex, rpc, clock, 100)
        self.assertEqual(sent, 3)  # never more than the 3 reserved attempts
        pos = ex.state.open[MINT]
        self.assertEqual((pos["sell_attempts"], pos["stuck"], pos["abandoned"]), (3, True, True))
        alerts = [r.get("alert") for r in fills(conf) if r["kind"] == "alert"]
        self.assertIn("sell_stuck", alerts)
        self.assertIn("sell_abandoned", alerts)
        self.assertEqual(ex.state.realized_lamports, -3 * 505_000)  # each landed failed sell's fee, once
        committed = pe.feearm_committed_cost(ex.state, ex.sell_max_attempts)
        self.assertEqual(committed, buy_cost + 3 * 505_000)  # no double count, no sell reserve left on the abandoned position
        arm1 = pe.feearm_pick(7, 1)
        size = ex.limits.size_lamports
        wc = pe.feearm_new_buy_worst_case(size, arm1[1], ex.sell_max_attempts)
        ex.state.realized_lamports = -(CAP - wc - buy_cost)  # committed + worst case == CAP exactly
        self.assertEqual(pe.feearm_committed_cost(ex.state, 3) + wc, CAP)
        self.assertIsNone(pe.feearm_buy_refusal(ex.limits, ex.state, size, arm1[1], 3))
        ex.state.realized_lamports -= 1
        self.assertEqual(pe.feearm_buy_refusal(ex.limits, ex.state, size, arm1[1], 3), "spend_cap")
        ex.handle_signal(mk_sig(ex, mint="11111111111111111111111111111112", t=clock()))  # an abandoned position halts new buys too
        self.assertEqual(fills(conf)[-1]["reason"], "limit:sell_stuck")


if __name__ == "__main__":
    unittest.main()
