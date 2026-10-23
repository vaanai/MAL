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

    def test_a_twin_row_for_every_trip_opens_the_gate_and_a_price_failed_row_counts(self):
        e = self.live2()
        e.open_position()
        gt_write_twin_run(e, rows=[{"trip_id": MINT, "price_failed": True, "reason": "paper_excluded:gap"}])
        self.assertIsNone(e.ex.gates.twin_refusal(e.clock()))

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
