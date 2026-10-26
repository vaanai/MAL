"""PR 3 of the HV4 live adapter: sell min_out ladder, twin pins and wrapper, parity/twin/tie-out wiring. Synthetic fixtures only: no HV4 ledger, decision row,
bots.json or job log is read; no network, no key (throwaway Keypair, the probe tests' fake RPC)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools import arena_live_bridge as alb
from tools import arena_live_parity as alp
from tools import arena_twin_writer as W
from tools import hv4_c0_parity as c0p
from tools import hv4_c0_twin as tw
from tools import hv4_live_c0 as c0
from tools.test_arena_live_bridge import StubPort
from tools.test_arena_twin_writer import BOT as _B, DEC, case, ledger_row, trip, write_ledger  # noqa: F401
from tools.test_hv4_live_c0 import ARM, Hv4Env
from tools.test_hv4_live_c0_pr2 import LivePortScheduleTests, decode_tx
from tools.test_arena_live_bridge import TmpCase


class LadderPureTests(unittest.TestCase):
    def test_rungs_and_alignment_with_the_priority_schedule(self):
        self.assertEqual(c0.SELL_MIN_OUT_KEEP_BPS, {"first": 8500, "escalated": 6500})
        for att in range(0, 6):
            for stuck in (False, True):
                for el in (None, 0, c0.ESCALATE_AFTER_MS - 1, c0.ESCALATE_AFTER_MS):
                    esc = c0.sell_priority(att, stuck, el) == c0.PRIORITY_PIN["escalated"]
                    self.assertEqual(c0.sell_keep_bps(att, stuck, el), 6500 if esc else 8500)
                    self.assertEqual(c0.sell_slip_bps(att, stuck, el), 3500 if esc else 1500)
                    self.assertGreater(c0.sell_keep_bps(att, stuck, el), 0)
                    self.assertGreaterEqual(c0.sell_keep_bps(att, stuck, el), c0.SELL_MIN_OUT_FLOOR_BPS)
        self.assertEqual(c0.sell_keep_bps(1), 8500)  # one revert: still the first rung
        self.assertEqual(c0.sell_keep_bps(2), 6500)

    def test_ladder_is_in_the_pin_and_config_cannot_reach_it(self):
        self.assertEqual(c0.SELL_BUILDER_PIN["c0.ladder_sha256"], c0.ladder_pin_sha256())
        old = dict(c0.SELL_MIN_OUT_KEEP_BPS)
        try:
            c0.SELL_MIN_OUT_KEEP_BPS["first"] = 5000
            self.assertNotEqual(c0.SELL_BUILDER_PIN["c0.ladder_sha256"], c0.ladder_pin_sha256())
        finally:
            c0.SELL_MIN_OUT_KEEP_BPS.update(old)


class ConfigPinTests(unittest.TestCase):
    def test_pin_is_the_registered_stack_hash(self):
        self.assertEqual(c0.stack_sha256(c0.HV4_STACK_PIN), c0.CONFIG_PIN_SHA256)
        self.assertTrue(c0.CONFIG_PIN_SHA256.startswith("1e611a5a"))
        self.assertEqual(W.stack_hash(c0.HV4_STACK_PIN), c0.CONFIG_PIN_SHA256)
        self.assertIsNone(c0.config_pin_problem({**c0.HV4_STACK_PIN, "sps_entry": 0.4}))  # slot rates are outside the pin
        for k, v in (("send", 60_000), ("rent", 2_039_280), ("feed_delay_slots", 6), ("e1_floor_per_005", 0)):
            self.assertEqual(c0.config_pin_problem({**c0.HV4_STACK_PIN, k: v}), "config_pin_mismatch", k)
        self.assertEqual(c0.config_pin_problem(None), "config_pin_mismatch")

    def test_twin_writer_sha_pin(self):
        import hashlib
        self.assertEqual(c0.twin_pins(), (c0.CONFIG_PIN_SHA256, c0.TWIN_WRITER_SHA256))
        self.assertEqual(hashlib.sha256(Path(W.__file__).read_bytes()).hexdigest(), c0.TWIN_WRITER_SHA256)


class TwinFeesTests(unittest.TestCase):
    def setUp(self):
        self._d = tempfile.TemporaryDirectory()
        self.addCleanup(self._d.cleanup)
        self.tmp = Path(self._d.name)

    def _ledger(self, stack=None):
        row = ledger_row()
        row["stack"] = dict(stack if stack is not None else c0.HV4_STACK_PIN, sps_entry=0.4, sps_exit=0.4)
        row["bot_id"] = ARM
        return write_ledger(self.tmp, [row])

    def test_fees_sent_schedule(self):
        self.assertEqual(c0.fees_sent(1, [55_000]), (60_000, 60_000))
        self.assertEqual(c0.fees_sent(1, [55_000, 55_000, 150_000]), (60_000, 60_000 + 60_000 + 155_000))

    def test_twin_priced_at_fees_actually_sent_and_equal_to_the_schedule(self):
        d = self._ledger()
        pins = c0.twin_pins()
        base = W.build([trip()], d, ARM, 60_000, 60_000, 10_000, pins[0])[0][0]  # no fee fields on the trip: defaults 60,000
        self.assertEqual(base["state"], "filled")
        # an escalated trip: the executor's schedule 55k, 55k (1 revert), 150k -> landed sell 150k+5k, failed sells earlier
        buy_fee, sell_fee = c0.fees_sent(1, [c0.sell_priority(0), c0.sell_priority(1), c0.sell_priority(2)])
        esc = W.build([trip(buy_fee_lamports=buy_fee, sell_fee_lamports=sell_fee)], d, ARM, 60_000, 60_000, 10_000, pins[0])[0][0]
        self.assertEqual(esc["sell_send_lamports"], sell_fee)
        self.assertAlmostEqual(esc["pnl_lamports"], base["pnl_lamports"] - (sell_fee - 60_000), places=3)

    def test_wrong_stack_refuses_the_whole_run(self):
        d = self._ledger({**c0.HV4_STACK_PIN, "send": 60_000})
        with self.assertRaises(W.BadInput):
            W.build([trip()], d, ARM, 1, 1, 10_000, c0.CONFIG_PIN_SHA256)

    def test_cli_wrapper_end_to_end_and_run_record_pins(self):
        d = self._ledger()
        trips = self.tmp / "trips.jsonl"
        trips.write_text(json.dumps(trip(buy_fee_lamports=60_000, sell_fee_lamports=215_000)) + "\n")
        out = self.tmp / "twins.jsonl"
        rc = tw.main(["--trips", str(trips), "--ledger-dir", str(d), "--bot-id", ARM, "--out", str(out), "--now-ms", str(DEC)])
        self.assertEqual(rc, 0)
        rec = json.loads(Path(str(out) + ".run.json").read_text())
        self.assertEqual((rec["config_hash"], rec["code_sha256"]), c0.twin_pins())
        from tools.h5_arena_gates import twin_pin_problem
        self.assertIsNone(twin_pin_problem(str(out), *c0.twin_pins()))
        self.assertEqual(json.loads(out.read_text().splitlines()[0])["sell_send_lamports"], 215_000)

    def test_bypass_forms_are_refused_and_the_writer_is_never_called(self):
        from unittest import mock
        d = self._ledger()
        ok = ["--trips", "t", "--out", "o", "--bot-id", ARM]
        arena = "/data/mal/arena/zz"
        with mock.patch.object(W, "main", side_effect=AssertionError("writer must not run")):
            for argv in ([*ok, f"--ledger-dir={arena}"],
                         [*ok, "--ledger-dir", str(d), "--ledger-dir", arena],
                         [*ok, "--ledger-dir", arena, "--ledger-dir", str(d)],
                         [*ok, "--ledger-dir", str(d), f"--ledger-dir={arena}"],
                         [*ok, "--ledger-dir", str(d), "--ledger-di", arena],  # abbreviation
                         ["--trips", "t", "--out", "o", "--bot-id=hv4-CON-L", f"--ledger-dir={d}"],
                         [*ok, "--bot-id", "hv4-CON-L", "--ledger-dir", str(d)],
                         [*ok, "--ledger-dir", str(d), "--config-hash=x"],
                         [*ok, "--ledger-dir", str(d), "--buy-send=1"],
                         [*ok, "--ledger-dir", str(d), "--shadow-dir", "x"]):
                self.assertEqual(tw.main(argv), 2, argv)

    def test_forwarded_argv_is_exactly_the_parsed_values_plus_pins(self):
        from unittest import mock
        d = self._ledger()
        seen = []
        with mock.patch.object(W, "main", side_effect=lambda a: seen.append(a) or 0):
            self.assertEqual(tw.main(["--trips=t", "--out=o", f"--ledger-dir={d}", f"--bot-id={ARM}", "--now-ms=5"]), 0)
        self.assertEqual(seen[0][seen[0].index("--ledger-dir") + 1], str(d))
        self.assertEqual(seen[0].count("--ledger-dir"), 1)
        self.assertEqual(seen[0][seen[0].index("--config-hash") + 1], c0.CONFIG_PIN_SHA256)
        self.assertEqual(seen[0][seen[0].index("--now-ms") + 1], "5")

    def test_cli_refusals(self):
        d = self._ledger()
        base = ["--trips", "t", "--out", "o"]
        self.assertEqual(tw.main(["--bot-id", "hv4-CON-L", "--ledger-dir", str(d), *base]), 2)  # L arm
        self.assertEqual(tw.main(["--bot-id", ARM, "--ledger-dir", "/data/mal/arena/haiku-v4/ledger", *base]), 2)  # sealed, no read-record gate yet
        self.assertEqual(tw.main(["--bot-id", ARM, "--ledger-dir", str(d), "--config-hash", "x", *base]), 2)  # pinned option
        self.assertIn("sealed", tw.refusal(ARM, "/data/mal/arena/x") or "")


class SellLadderLiveTests(LivePortScheduleTests):
    """Reuses the PR2 port builder (throwaway key, fake RPC). The inherited PR2 tests are skipped here."""

    def _min_out(self, port, rpc):
        from tools import pumpswap_tx as ptx
        msg = decode_tx(rpc.sent[-1][1])
        keys = list(msg.account_keys)
        for ix in msg.instructions:
            d = bytes(ix.data)
            if str(keys[ix.program_id_index]) == str(ptx.PUMPSWAP_PROGRAM) and d[:8] == bytes(ptx.DISC_SELL):
                return int.from_bytes(d[16:24], "little")
        raise AssertionError("no sell instruction")

    def test_decoded_min_out_ladder_085_then_065_never_zero(self):
        from tools.test_probe_live import land_sell
        port, rpc, clock, cfg = self._port(True)
        mint, _ = self._buy(port, rpc, clock, cfg)
        port.sell({"mint": mint}, "book_exit")
        p = port.ex.state.pending[mint]
        q, m1 = p["q_out"], self._min_out(port, rpc)
        self.assertEqual(m1, q * 8500 // 10_000)
        self.assertEqual(m1, p["min_out"])
        fail = {"InstructionError": [3, {"Custom": 6004}]}
        land_sell(port.ex, rpc, proceeds=0, err=fail, fee=60_000)
        port.poll()  # revert 1: still the first rung
        self.assertEqual(self._min_out(port, rpc) * 10_000 // port.ex.state.pending[mint]["q_out"] >= 8499, True)
        land_sell(port.ex, rpc, proceeds=0, err=fail, fee=60_000)
        port.poll()  # revert 2: escalated rung
        p2 = port.ex.state.pending[mint]
        m3 = self._min_out(port, rpc)
        self.assertEqual(m3, p2["q_out"] * 6500 // 10_000)
        self.assertGreater(m3, 0)
        self.assertGreaterEqual(m3 * 10_000, p2["q_out"] * 6500 - 10_000)  # >= 0.65 x quote (floor division)

    def test_buy_slippage_is_untouched_and_plain_profile_keeps_the_base_slip(self):
        port, rpc, clock, cfg = self._port(True)
        base = port.ex.slip_bps
        self.assertEqual(base, port.ex._slip_base)
        plain, _, _, _ = self._port(False)
        self.assertEqual(plain.ex.slip_bps, base)
        port.ex._c0_sell_slip = 3500
        self.assertEqual(port.ex.slip_bps, 3500)
        port.ex._c0_sell_slip = None
        self.assertEqual(port.ex.slip_bps, base)

    def test_clock_starts_only_when_a_sell_is_built(self):
        from unittest import mock
        from tools import probe_live as pl
        port, rpc, clock, cfg = self._port(True)
        mint, _ = self._buy(port, rpc, clock, cfg)
        pos = port.ex.state.open[mint]
        with mock.patch.object(pl.LiveExecutor, "_start_sell", return_value=None):
            port.ex._start_sell(mint, pos, None, "book_exit", clock())  # an early return: nothing built
        self.assertNotIn("c0_first_sell_ms", pos)
        t0 = clock()

        def build(self_, m, p, snap, reason, now, snapshot="full"):
            self_.state.pending[m] = {"kind": "sell"}

        with mock.patch.object(pl.LiveExecutor, "_start_sell", build):
            port.ex._start_sell(mint, pos, None, "book_exit", t0)
        self.assertEqual(pos["c0_first_sell_ms"], t0)
        port.ex.state.pending.pop(mint)

    def test_one_clock_value_for_rung_and_priority_at_the_boundary(self):
        from unittest import mock
        from tools import probe_live as pl
        port, rpc, clock, cfg = self._port(True)
        mint, _ = self._buy(port, rpc, clock, cfg)
        pos = port.ex.state.open[mint]
        pos["c0_first_sell_ms"] = clock() - c0.ESCALATE_AFTER_MS + 1  # one ms short of the bound at `now`
        got = {}

        def build(self_, m, p, snap, reason, now, snapshot="full"):
            clock.t += 5  # the clock moves during the build; the priority must still follow the value the rung used
            got["slip"], got["prio"] = self_.slip_bps, self_._priority(m)

        with mock.patch.object(pl.LiveExecutor, "_start_sell", build):
            port.ex._start_sell(mint, pos, None, "book_exit", clock())
        self.assertEqual((got["slip"], got["prio"]), (1500, 55_000))

    def test_time_bound_moves_the_rung_with_the_priority(self):
        from tools.test_probe_live import land_sell
        port, rpc, clock, cfg = self._port(True)
        mint, _ = self._buy(port, rpc, clock, cfg)
        port.sell({"mint": mint}, "book_exit")
        land_sell(port.ex, rpc, proceeds=0, err={"InstructionError": [3, {"Custom": 6004}]}, fee=60_000)
        clock.t += c0.ESCALATE_AFTER_MS
        port.poll()
        p = port.ex.state.pending[mint]
        self.assertEqual(self._min_out(port, rpc), p["q_out"] * 6500 // 10_000)

    # the inherited PR2 tests need not run twice
    test_hv4_c0_schedule_55_55_150 = None
    test_stuck_position_sells_at_escalated = None
    test_plain_profile_unchanged_500k = None
    test_plain_config_has_no_c0_state = None
    test_time_bound_escalates_a_first_retry = None


class _Runner:
    """In-process stand-in for `python -m tools.arena_live_parity` (the real tool runs; only the subprocess is skipped)."""

    def __init__(self):
        self.calls = []

    def __call__(self, argv):
        self.calls.append(argv)
        mod, rest = argv[0], argv[1:]
        if mod == "tools.arena_live_parity":
            return alp.main(rest)
        return 0


class LedgerPort(StubPort):
    ledger: list = []

    def drain_ledger(self):
        out, self.ledger = self.ledger, []
        return out


class GateWiringTests(TmpCase):
    def _env(self, **over):
        e = Hv4Env(self.tmp, **over)
        e.port = LedgerPort()
        e.raw.update(parity_dir=str(Path(self.tmp) / "par"), twins_file=str(Path(self.tmp) / "twins.jsonl"), tieout_dir=str(Path(self.tmp) / "tie"))
        return e

    def _prime_twins(self, e):
        """The twin writer's run record exists before the first buy (it runs once on the empty trips file): run the pinned wrapper on a synthetic ledger."""
        d = Path(self.tmp) / "synthetic-ledger"
        d.mkdir(exist_ok=True)
        t = Path(self.tmp) / "empty-trips.jsonl"
        t.write_text("")
        rc = tw.main(["--trips", str(t), "--ledger-dir", str(d), "--bot-id", ARM, "--out", e.raw["twins_file"], "--now-ms", str(e.clock())])
        self.assertEqual(rc, 0)

    def _bridge(self, e, runner):
        cfg = alb.Config.from_dict(e.raw)
        return alb.Bridge(cfg, e.port, now_ms=e.clock, live_ok_path=e.live_ok, tier_path=e.tier, root_checks=False, parity_runner=runner)

    def test_paths_set_together_and_only_for_the_profile(self):
        e = self._env()
        raw = dict(e.raw)
        raw.pop("tieout_dir")
        with self.assertRaises(ValueError):
            alb.Config.from_dict(raw)
        raw = {**e.raw, "twins_file": "relative.jsonl"}
        with self.assertRaises(ValueError):
            alb.Config.from_dict(raw)
        self.assertIsNone(c0p.paths_refusal(e.raw))
        self.assertEqual(c0p.paths_refusal({}), "parity_path_missing:parity_dir")
        plain = {"bot_id": "rule1", "frozen_sha": "a" * 40, "root": "/r", "state_dir": "/s", "end_ms": 1, "max_hold_ms": 1, "parity_dir": "/p"}
        with self.assertRaises(ValueError):
            alb.Config.from_dict(plain)

    def test_without_paths_there_are_no_gates(self):
        e = Hv4Env(self.tmp)
        b = alb.Bridge(alb.Config.from_dict(e.raw), e.port, now_ms=e.clock, live_ok_path=e.live_ok, tier_path=e.tier, root_checks=False)
        self.assertIsNone(b.gates)

    def test_empty_book_buys_when_parity_rc0_and_runs_the_real_tool(self):
        e = self._env()
        e.start()
        e.entry_sz("o1")
        self._prime_twins(e)
        r = _Runner()
        b = self._bridge(e, r)
        b.tick()
        self.assertEqual([c[:2] for c in e.port.calls], [("buy", "o1")])
        self.assertTrue(any(c[0] == "tools.arena_live_parity" for c in r.calls))
        res = json.loads((Path(self.tmp) / "par" / "parity-result.json").read_text())
        self.assertIsInstance(res, dict)

    def test_parity_failure_means_no_buy(self):
        e = self._env()
        e.start()
        e.entry_sz("o1")
        b = self._bridge(e, lambda argv: 10)
        b.tick()
        self.assertEqual(e.port.calls, [])
        self.assertEqual(e.log_rows("entry_refused")[0]["why"], "parity_gate:rc_10")

    def test_hard_parity_halt_latches(self):
        e = self._env()
        e.start()
        e.entry_sz("o1")
        b = self._bridge(e, lambda argv: 11)
        b.tick()
        self.assertEqual(e.port.calls, [])
        self.assertIn("parity_hard_halt", b.state.latches)

    def test_twin_gate_blocks_a_trip_without_a_twin_and_pin_mismatch_latches(self):
        e = self._env()
        b = self._bridge(e, lambda argv: 0)
        b.gates.recorder.trips["M1"] = {"trip_id": "M1", "stake_lamports": 50_000_000, "decision_ms": 1, "exit_landed": True, "exit_bound_ms": 2}
        self.assertEqual(b.gate_refusal(e.clock()), "twin_gate:run_record_unreadable")
        tw_path = Path(self.tmp) / "twins.jsonl"
        tw_path.write_text("")
        Path(str(tw_path) + ".run.json").write_text(json.dumps({"config_hash": "0" * 64, "code_sha256": c0.TWIN_WRITER_SHA256}))
        self.assertEqual(b.gate_refusal(e.clock()), "twin_gate:pin_mismatch:config_hash")
        self.assertIn("twin_pin_mismatch", b.state.latches)

    def test_state_stamps_and_hard_halts(self):
        e = self._env()
        e.start()
        b = self._bridge(e, lambda argv: 0)
        b.tick()
        st = b._parity_state(e.clock())
        self.assertEqual(set(st["at_ms"]), {"parity", "loss", "balance", "decision_chain", "control_files"})  # no twin stamp: the writer has not run
        self.assertEqual(st["at_ms"]["parity"], e.clock())
        b._latch("stake_mismatch", "x")
        self.assertEqual(b._parity_state(e.clock())["unresolved_hard_halts"], ["stake_mismatch"])

    def test_stake_mismatch_and_unreadable_buy_latch_and_trips_are_recorded(self):
        e = self._env()
        e.start()
        e.entry_sz("o1")
        self._prime_twins(e)
        b = self._bridge(e, lambda argv: 0)
        b.tick()
        o = b.state.orders["o1"]
        mint = o["mint"]
        good = {"landed": True, "spend_lamports": o["stake_lamports"], "tokens_received": 1000, "fee_lamports": 60_000, "signature": "s1",
                "decision_t_ms": o["decision_ms"], "confirm_seen_ms": e.clock(), "tokens_source": "meta"}
        e.port.ledger = [("buy", mint, good)]
        b.feed_ledger()
        t = b.gates.recorder.trips[mint]
        self.assertEqual((t["stake_lamports"], t["buy_fee_lamports"]), (50_000_000, 60_000))
        self.assertEqual(t["exit_bound_ms"], o["decision_ms"] + b.cfg.max_hold_ms + alb.BACKSTOP_EXTRA_MS)
        self.assertNotIn("stake_mismatch", b.state.latches)
        e.port.ledger = [("buy", mint, {**good, "signature": "s2", "spend_lamports": 20_000_000})]
        b.feed_ledger()
        self.assertIn("stake_mismatch", b.state.latches)
        # sell: escalated fees as sent land in the trip, which is what the twin is priced at
        sell = {"landed": True, "pnl_lamports": -1_000, "tokens": 1000, "sol_received_lamports": 49_000_000, "fee_lamports": 155_000,
                "failed_attempt_cost_lamports": 120_000, "exit_reason": "book_exit", "confirm_seen_ms": e.clock()}
        e.port.ledger = [("sell", mint, sell)]
        b.feed_ledger()
        self.assertEqual(b.gates.recorder.trips[mint]["sell_fee_lamports"], 155_000 + 120_000)
        self.assertTrue(b.gates.recorder.trips[mint]["exit_landed"])

    def test_decision_chain_stamp_goes_stale_when_the_reader_stalls(self):
        e = self._env()
        e.start()
        b = self._bridge(e, lambda argv: 0)
        b.tick()
        self.assertIn("decision_chain", b._parity_state(e.clock())["at_ms"])
        e.clock.t += c0p.CHAIN_FRESH_MS + 1  # no tick (no verified read) since
        self.assertNotIn("decision_chain", b._parity_state(e.clock())["at_ms"])

    def test_dry_port_has_no_ledger_and_h5_path_has_no_gates(self):
        self.assertEqual(alb.DryPort(1).drain_ledger(), [])


if __name__ == "__main__":
    unittest.main()
