"""QP 10-23 conditions on PR #826 (comment 6369377259): the twin stamp is the writer's run, the buy-2 window is closed, the age is re-judged at the socket. Fixtures and stubs only."""

from __future__ import annotations

import json
from pathlib import Path

from tools import arena_live_parity as alp
from tools import h5_arena_executor as ax
from tools import h5_arena_gates as gt
from tools.test_h5_arena_exec_wiring import LiveCase, gt_write_twin_run
from tools.test_h5_executor import MINT


class TwinStampTests(LiveCase):
    def stamp(self, e):
        return e.ex._parity_state(e.clock())["at_ms"].get("twin")

    def test_no_run_record_means_no_stamp_even_with_a_parsing_file(self):
        e = self.live()
        e.rpc.balance = 10**10
        Path(e.conf["parity_twins_file"]).write_text('{"trip_id": "x"}\n')
        self.assertIsNone(self.stamp(e))

    def test_the_stamp_is_the_writers_run_time_not_now(self):
        e = self.live()
        e.rpc.balance = 10**10
        t0 = e.clock()
        gt_write_twin_run(e, run_ms=t0 - 45_000)
        self.assertEqual(self.stamp(e), t0 - 45_000)
        e.clock.t += 20_000  # the writer has not run again: the stamp stays put and is now older than 60 s
        self.assertEqual(self.stamp(e), t0 - 45_000)
        now = e.clock()
        st = {"at_ms": {n: now for n in alp.STATE_NAMES}, "unresolved_hard_halts": []}
        st["at_ms"]["twin"] = self.stamp(e)
        self.assertIn("twin older than 60 s", alp.state_problems(st, now))

    def test_a_stalled_writer_makes_the_real_parity_tool_p7(self):
        e = self.live()
        e.rpc.balance = 10**10
        gt_write_twin_run(e, run_ms=e.clock())
        e.clock.t += 61_000
        e.ex.gates.state_builder = e.ex._parity_state
        e.ex.gates.runner = gt.default_runner
        rc = e.ex.gates.run_parity(e.clock())
        self.assertEqual(rc, alp.EXIT_SOFT)  # twin stamp 61 s old (the other stamps are fresh)

    def test_a_record_that_does_not_cover_every_live_trip_or_does_not_match_the_file_is_no_stamp(self):
        e = self.live()
        e.rpc.balance = 10**10
        e.open_position()
        gt_write_twin_run(e, covered=[])  # the writer read the trips file before this trip landed
        self.assertIsNone(self.stamp(e))
        gt_write_twin_run(e)
        self.assertIsNotNone(self.stamp(e))
        Path(e.conf["parity_twins_file"]).write_text('{"trip_id": "tampered"}\n')  # the file is not the one the run wrote
        self.assertIsNone(self.stamp(e))

    def test_bad_records_are_no_stamp(self):
        e = self.live()
        e.rpc.balance = 10**10
        gt_write_twin_run(e)
        p = gt.twin_run_path(e.conf["parity_twins_file"])
        good = json.loads(p.read_text())
        for bad in ({"schema": "other"}, {"run_ms": True}, {"run_ms": "5"}, {"run_ms": None}, {"covered_trip_ids": None}, {"twins_sha256": "0"}):
            p.write_text(json.dumps({**good, **bad}))
            self.assertIsNone(self.stamp(e), bad)
        p.write_text("{not json")
        self.assertIsNone(self.stamp(e))
        p.unlink()
        self.assertIsNone(self.stamp(e))

    def test_the_stamp_must_postdate_the_last_landed_tx(self):
        e = self.live()
        e.rpc.balance = 10**10
        gt_write_twin_run(e, run_ms=e.clock())
        e.open_position()  # a landed buy after the writer ran
        gt_write_twin_run(e, run_ms=e.clock() - 5_000)  # (the run covers it but began before the landing)
        now = e.clock()
        st = e.ex._parity_state(now)
        st["last_landed_ms"] = e.ex.gates.recorder.last_landed_ms
        self.assertIn("twin older than the last landed tx", alp.state_problems(st, now))


class Buy2WindowTests(LiveCase):
    def setUp(self):
        super().setUp()
        self.calls = []

    def live2(self):
        e = self.live()
        e.ex.gates.runner = lambda argv: 0
        return e

    def test_no_trip_no_gate(self):
        e = self.live2()
        self.assertIsNone(e.ex.gates.twin_refusal(e.clock()))

    def test_an_open_trip_without_a_twin_holds_the_cap_at_one(self):
        e = self.live2()
        e.open_position()
        self.assertEqual(e.ex.gates.twin_refusal(e.clock()), "twin_gate:open_untwinned_cap_1")
        sent = len(e.rpc.sent)
        e.ex.state.open.clear()  # (keep the recorder's trip; free the executor's own slot so the same pool can be triggered again through the full buy path)
        e.ex.state.bought.clear()
        e.fire()
        self.assertEqual(e.refusals()[-1], "twin_gate:open_untwinned_cap_1")
        self.assertEqual(len(e.rpc.sent), sent)  # no second buy went out

    def test_a_trip_past_its_exit_without_a_twin_is_its_own_refusal(self):
        e = self.live2()
        e.open_position()
        e.ex.gates.recorder.trips[MINT]["exit_bound_ms"] = e.clock() - 1
        self.assertEqual(e.ex.gates.twin_refusal(e.clock()), "twin_gate:past_exit_untwinned")
        e.ex.gates.recorder.trips[MINT].update(exit_bound_ms=e.clock() + 10**9, exit_landed=True)
        self.assertEqual(e.ex.gates.twin_refusal(e.clock()), "twin_gate:past_exit_untwinned")

    def test_a_priced_twin_row_for_every_trip_opens_the_gate(self):
        e = self.live2()
        e.open_position()
        gt_write_twin_run(e, rows=[{"trip_id": MINT, "pnl_lamports": -5.25, "entry_price": 1.0, "exit_price": 0.9}])
        self.assertIsNone(e.ex.gates.twin_refusal(e.clock()))

    def test_a_price_failed_twin_row_is_a_missing_twin(self):
        e = self.live2()
        e.open_position()
        gt_write_twin_run(e, rows=[{"trip_id": MINT, "price_failed": True, "reason": "paper_excluded:gap"}])
        self.assertEqual(e.ex.gates.twin_refusal(e.clock()), "twin_gate:open_untwinned_cap_1")
        e.ex.gates.recorder.trips[MINT].update(exit_landed=True)
        self.assertEqual(e.ex.gates.twin_refusal(e.clock()), "twin_gate:past_exit_untwinned")
        e.ex.state.open.clear()
        e.ex.state.bought.clear()
        sent = len(e.rpc.sent)
        e.fire()
        self.assertEqual(e.refusals()[-1], "twin_gate:past_exit_untwinned")
        self.assertEqual(len(e.rpc.sent), sent)  # fail closed: no buy

    def test_a_row_without_a_price_is_not_priced(self):
        e = self.live2()
        e.open_position()
        for row in ({"trip_id": MINT}, {"trip_id": MINT, "pnl_lamports": None}, {"trip_id": MINT, "pnl_lamports": True}, {"trip_id": MINT, "pnl_lamports": 1, "price_failed": True}):
            gt_write_twin_run(e, rows=[row])
            self.assertIsNotNone(e.ex.gates.twin_refusal(e.clock()), row)

    def test_an_unreadable_twins_file_is_no_buy(self):
        e = self.live2()
        e.open_position()
        Path(e.conf["parity_twins_file"]).write_text("{garbage\n")
        self.assertEqual(e.ex.gates.twin_refusal(e.clock()), "twin_gate:twins_unreadable")
        Path(e.conf["parity_twins_file"]).unlink()
        self.assertEqual(e.ex.gates.twin_refusal(e.clock()), "twin_gate:twins_unreadable")


class SocketWriteAgeTests(LiveCase):
    def test_the_age_is_judged_again_at_the_sendtransaction_call(self):
        e = self.live()
        e.ex.gates.runner = lambda argv: 0
        e.fire()
        self.assertEqual(len(e.rpc.sent), 1)
        base_rpc = e.ex.rpc
        p = dict(e.ex.state.pending[MINT], sends=0, decision_t_ms=e.clock() - 4_000)  # a first send whose signal went stale after the earlier checks
        e.ex.state.pending[MINT] = p
        e.ex._send_guarded(p, MINT)  # the socket-time guard on its own (the earlier pre-check is a separate test)
        self.assertEqual(len(e.rpc.sent), 1)  # no second write reached the chain
        self.assertNotIn(MINT, e.ex.state.pending)
        self.assertEqual([r["reason"] for r in e.ledger("send_blocked")], ["stale_trigger_at_write"])
        self.assertIs(e.ex.rpc, base_rpc)  # the guard wrapper is removed

    def test_a_fresh_first_send_goes_through_the_guard_and_is_written(self):
        e = self.live()
        e.ex.gates.runner = lambda argv: 0
        e.fire()
        self.assertEqual(len(e.rpc.sent), 1)
        self.assertEqual(e.ledger("send_blocked"), [])

    def test_a_fresh_signal_is_written_and_rebroadcasts_are_not_judged(self):
        e = self.live()
        e.ex.gates.runner = lambda argv: 0
        e.fire()
        self.assertEqual(len(e.rpc.sent), 1)
        p = e.ex.state.pending[MINT]
        e.clock.t += 4_000
        e.ex._send(p, MINT)
        self.assertEqual(e.ledger("send_blocked"), [])
        self.assertTrue(callable(e.ex.rpc))


class TwinPinTests(LiveCase):
    """QP 10-23 blocker 2 (P5): the run record's config_hash and code_sha256 are checked against the book's compiled pins."""

    def live2(self, **kw):
        e = self.live(**kw)
        e.ex.gates.runner = lambda argv: 0
        return e

    def test_the_pins_are_the_recomputed_values(self):
        import hashlib
        import subprocess
        from tools import arena_twin_writer as tw
        self.assertEqual(ax.TWIN_WRITER_SHA256, hashlib.sha256(Path(tw.__file__).read_bytes()).hexdigest())
        cost = hashlib.sha256((Path(tw.__file__).parent / "arena_h5_cost.py").read_bytes()).hexdigest()
        self.assertEqual(set(ax.H5_COST_SHA256.values()), {cost})
        for reg, want in ax.H5_COST_SHA256.items():  # the registration shas: eb8b32d (a2), a5f4137 (Q50)
            r = subprocess.run(["git", "show", f"{reg}:tools/arena_h5_cost.py"], capture_output=True, cwd=Path(tw.__file__).parent.parent)
            if r.returncode == 0:
                self.assertEqual(hashlib.sha256(r.stdout).hexdigest(), want, reg)
        self.assertEqual(ax.h5_twin_config_hash(cost), tw.h5_config_hash(cost))
        for b in ax.BOOKS.values():
            self.assertEqual(ax.twin_pins(b), (tw.h5_config_hash(cost), ax.TWIN_WRITER_SHA256))
        self.assertEqual(ax.TWIN_STACK_ID, tw.H5_STACK_ID)
        self.assertEqual(ax.TWIN_ADAPTER, tw.H5_ADAPTER)

    def test_a_matching_record_passes_even_with_no_trip(self):
        e = self.live2()
        self.assertIsNone(e.ex.gates.twin_refusal(e.clock()))

    def test_no_record_is_no_buy_but_no_latch(self):
        e = self.live2(pinned_twin_record=False)
        self.assertEqual(e.ex.gates.twin_refusal(e.clock()), "twin_gate:run_record_unreadable")
        e.fire()
        self.assertEqual(e.refusals()[-1], "twin_gate:run_record_unreadable")
        self.assertNotIn(ax.H_TWIN_PIN, e.ex.counters.halts)

    def mismatch(self, want, **kw):
        e = self.live2()
        gt_write_twin_run(e, rows=[], **kw)
        self.assertEqual(e.ex.gates.twin_refusal(e.clock()), f"twin_gate:pin_mismatch:{want}")
        sent = len(e.rpc.sent)
        e.fire()
        self.assertIn(f"twin_gate:pin_mismatch:{want}", e.refusals())
        self.assertIn(ax.H_TWIN_PIN, e.ex.counters.halts)
        self.assertEqual(len(e.rpc.sent), sent)
        e.fire()  # latched: still no buy
        self.assertEqual(len(e.rpc.sent), sent)

    def test_a_wrong_config_hash_is_no_buy_and_a_latch(self):
        self.mismatch("config_hash", config_hash="0" * 64)

    def test_a_wrong_code_sha_is_no_buy_and_a_latch(self):
        self.mismatch("code_sha256", code_sha256="f" * 64)

    def test_a_record_with_a_missing_pin_field_is_a_mismatch(self):
        e = self.live2()
        f = Path(e.conf["parity_twins_file"])
        rec = json.loads(gt.twin_run_path(str(f)).read_text())
        del rec["code_sha256"]
        gt.twin_run_path(str(f)).write_text(json.dumps(rec))
        self.assertEqual(e.ex.gates.twin_refusal(e.clock()), "twin_gate:pin_mismatch:code_sha256")

    def test_the_latch_is_a_hard_halt(self):
        self.assertIn(ax.H_TWIN_PIN, ax.ARENA_HALTS)
        self.assertIn(ax.H_TWIN_PIN, ax.HARD_ARENA_HALTS)


class RealWriterTests(LiveCase):
    """Fixtures are the REAL output of tools/arena_twin_writer (QP 10-23 ruling 4): a float pnl, and an H5-shaped ledger row."""

    DEC = 1_790_000_000_000
    STAKE = 300_000_000

    def h5_row(self, mint, stake_sol=0.3, state="filled", nofail=-1_234_567.5):
        return {"trade_id": f"b:pool:{stake_sol}", "bot_id": "h5v1-boostfloor-a2", "frozen_sha": "x", "decision_ms": self.DEC, "exit_ms": self.DEC + 330_000, "pool": "pool",
                "mint": mint, "prog": "pumpswap", "stake_sol": stake_sol, "meta": {"schema": "arena_h5_trade_v1", "stack": "DEC-031-rev2"},
                "legs": {"flat_1p9": {"net_lamports": nofail * 0.85, "nofail_lamports": nofail, "state": state, "p_fail": 0.15}}}

    def run_writer(self, e, rows):
        import hashlib
        from tools import arena_twin_writer as tw
        d = Path(e.conf["parity_twins_file"]).parent
        led = d / "ledger"
        led.mkdir(exist_ok=True)
        from datetime import datetime, timezone
        day = datetime.fromtimestamp(self.DEC / 1000, timezone.utc).strftime("%Y-%m-%d")
        (led / f"trades-{day}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
        trips = d / "trips-in.jsonl"
        trips.write_text(json.dumps({"trip_id": MINT, "decision_ms": self.DEC, "stake_lamports": self.STAKE}) + "\n")
        ch, _ = ax.twin_pins(ax.BOOKS["h5v1-boostfloor-a2"])
        rc = tw.main(["--trips", str(trips), "--ledger-dir", str(led), "--bot-id", "h5v1-boostfloor-a2", "--out", e.conf["parity_twins_file"], "--config-hash", ch,
                      "--now-ms", str(e.clock())])
        return rc

    def test_the_h5_adapter_does_not_crash_and_a_real_float_row_opens_the_gate(self):
        e = LiveCase.live(self)
        e.ex.gates.runner = lambda argv: 0
        e.open_position()
        self.assertEqual(self.run_writer(e, [self.h5_row(MINT)]), 0)
        row = json.loads(Path(e.conf["parity_twins_file"]).read_text().splitlines()[0])
        self.assertIsInstance(row["pnl_lamports"], float)
        self.assertEqual(row["pnl_lamports"], -1_234_567.5)
        self.assertEqual(row["pnl_rent0_lamports"], -1_234_567.5 + 2_039_280)
        self.assertEqual(row["stack"], "DEC-031-rev2")
        self.assertIsNone(e.ex.gates.twin_refusal(e.clock()))  # the real writer output + the real run record carry the pins

    def price_failed_case(self, rows):
        e = LiveCase.live(self)
        e.ex.gates.runner = lambda argv: 0
        e.open_position()
        self.assertEqual(self.run_writer(e, rows), 0)
        row = json.loads(Path(e.conf["parity_twins_file"]).read_text().splitlines()[0])
        self.assertTrue(row["price_failed"])
        self.assertEqual(e.ex.gates.twin_refusal(e.clock()), "twin_gate:open_untwinned_cap_1")

    def test_an_h5_row_at_another_stake_is_price_failed_and_refuses(self):
        self.price_failed_case([self.h5_row(MINT, stake_sol=0.25)])

    def test_an_unfilled_h5_row_is_price_failed_and_refuses(self):
        self.price_failed_case([self.h5_row(MINT, state="guarded")])

    def test_a_wrong_h5_pin_refuses_the_whole_run(self):
        from tools import arena_twin_writer as tw
        e = LiveCase.live(self)
        self.assertEqual(tw.main(["--trips", "/nonexistent", "--ledger-dir", "/x", "--bot-id", "b", "--out", "/x/o", "--config-hash", "0" * 64]), 2)

    def test_priced_numbers(self):
        for v, ok in ((1, True), (-5.5, True), (0.0, True), (True, False), (None, False), ("1", False), (float("inf"), False), (float("nan"), False)):
            self.assertEqual(gt._finite_number(v), ok, v)
