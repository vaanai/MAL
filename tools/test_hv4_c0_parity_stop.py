"""The HV4 C0 bridge's parity/tie-out STOP lives in parity_dir (unit-writable), not in the wallet-wide stop_file the hardened unit cannot write, and an rc 3
("STOP required but not writable") latches. Before this fix, a live unit passed /var/lib/mal-live/STOP to the tools, every halt came back as rc 3 and nothing
latched (install pack #1097 finding 1). Synthetic only: no HV4 row, ledger, read record or job log."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

from tools import arena_live_bridge as alb
from tools import hv4_c0_parity as c0p
from tools.test_arena_live_bridge import TmpCase
from tools.test_hv4_live_c0 import Hv4Env
from tools.test_hv4_live_c0_pr3 import LedgerPort, _Runner


class ParityStopTests(TmpCase):
    def _env(self, unwritable_wallet_stop: bool = True):
        e = Hv4Env(self.tmp)
        e.port = LedgerPort()
        e.raw.update(parity_dir=str(Path(self.tmp) / "par"), twins_file=str(Path(self.tmp) / "twins.jsonl"), tieout_dir=str(Path(self.tmp) / "tie"))
        if unwritable_wallet_stop:  # stands in for /var/lib/mal-live/STOP, which the hardened unit cannot write
            ro = Path(self.tmp) / "wallet-ro"
            ro.mkdir()
            e.raw["stop_file"] = str(ro / "STOP")
            os.chmod(ro, stat.S_IRUSR | stat.S_IXUSR)
            self.addCleanup(os.chmod, ro, stat.S_IRWXU)
        return e

    def _bridge(self, e, runner):
        return alb.Bridge(alb.Config.from_dict(e.raw), e.port, now_ms=e.clock, live_ok_path=e.live_ok, tier_path=e.tier, root_checks=False, parity_runner=runner)

    def test_tools_get_the_stop_path_inside_parity_dir(self):
        e = self._env()
        b = self._bridge(e, lambda argv: 0)
        self.assertEqual(b.gates.stop_file, str(Path(e.raw["parity_dir"]) / "STOP"))
        self.assertNotEqual(b.gates.stop_file, e.raw["stop_file"])
        self.assertIn("parity_stop_unwritable", alb.LATCHES)
        self.assertIn(c0p.PARITY_STOP_UNWRITABLE_LATCH, c0p.HARD_LATCHES)

    def test_real_tool_writes_its_stop_in_parity_dir_and_buys_stay_refused_across_a_restart(self):
        e = self._env()
        e.start()
        Path(e.raw["twins_file"]).write_text("{not json\n")  # unusable input: the real tool fails closed (rc 2) and writes STOP
        r = _Runner()
        b = self._bridge(e, r)
        e.entry_sz("o1")
        b.tick()
        self.assertEqual(e.port.calls, [])
        stop = Path(e.raw["parity_dir"]) / "STOP"
        self.assertTrue(stop.exists())  # written where the unit can write: before the fix this was rc 3 with no STOP anywhere
        self.assertFalse(Path(e.raw["stop_file"]).exists())
        self.assertTrue(any(a[0] == "tools.arena_live_parity" and a[a.index("--stop-file") + 1] == str(stop) for a in r.calls))
        b2 = self._bridge(e, lambda argv: 0)  # restart; parity now says rc 0, but the STOP stays until the manager removes it
        self.assertEqual(b2._files(), (False, True))
        e.entry_sz("o2")
        b2.tick()
        self.assertEqual(e.port.calls, [])
        stop.unlink()  # the manager's written review, then the removal
        self.assertEqual(b2._files(), (False, False))

    def test_rc3_latches_and_the_latch_survives_a_restart(self):
        e = self._env()
        e.start()
        e.entry_sz("o1")
        b = self._bridge(e, lambda argv: c0p.RC_STOP_UNWRITABLE)
        b.tick()
        self.assertEqual(e.port.calls, [])
        self.assertIn(c0p.PARITY_STOP_UNWRITABLE_LATCH, b.state.latches)
        b2 = self._bridge(e, lambda argv: 0)
        self.assertIn(c0p.PARITY_STOP_UNWRITABLE_LATCH, b2.state.latches)
        e.entry_sz("o2")
        b2.tick()
        self.assertEqual(e.port.calls, [])
        self.assertTrue(any(r["why"].startswith("latched:") for r in e.log_rows("entry_refused")))

    def test_rc11_still_latches_hard(self):
        e = self._env()
        e.start()
        e.entry_sz("o1")
        b = self._bridge(e, lambda argv: c0p.RC_HARD)
        b.tick()
        self.assertIn("parity_hard_halt", b.state.latches)
        self.assertNotIn(c0p.PARITY_STOP_UNWRITABLE_LATCH, b.state.latches)

    def test_a_link_at_the_stop_path_or_a_missing_parity_dir_is_a_stop(self):
        e = self._env()
        b = self._bridge(e, lambda argv: 0)
        Path(e.raw["parity_dir"]).mkdir(parents=True, exist_ok=True)
        self.assertEqual(b._files(), (False, False))
        os.symlink("/nonexistent-target", Path(e.raw["parity_dir"]) / "STOP")  # a dangling link still counts
        self.assertEqual(b._files(), (False, True))
        os.unlink(Path(e.raw["parity_dir"]) / "STOP")
        moved = Path(self.tmp) / "par-moved"
        os.rename(e.raw["parity_dir"], moved)  # the recorder keeps files in parity_dir; moving the dir away is the "missing" case
        self.assertEqual(b._files(), (False, True))

    def test_without_gates_nothing_changes(self):
        e = Hv4Env(self.tmp)
        b = alb.Bridge(alb.Config.from_dict(e.raw), e.port, now_ms=e.clock, live_ok_path=e.live_ok, tier_path=e.tier, root_checks=False)
        self.assertIsNone(b.gates)
        self.assertEqual(b._files(), (Path(e.raw["halt_file"]).exists(), Path(e.raw["stop_file"]).exists()))

    def test_tieout_gate_gets_the_same_stop_path(self):
        e = self._env()
        calls = []
        b = self._bridge(e, lambda argv: calls.append(argv) or 0)
        b.gates.tieout_refusal(e.clock(), e.clock() - 2 * 86_400_000)
        tie = [a for a in calls if a[0] == "tools.arena_tieout"]
        self.assertTrue(tie)
        self.assertEqual(tie[0][tie[0].index("--stop-file") + 1], str(Path(e.raw["parity_dir"]) / "STOP"))
        self.assertEqual(json.loads(json.dumps(tie[0]))[0], "tools.arena_tieout")


class TwinGraceTests(TmpCase):
    """QP part-A C1 (#1096): with a writable STOP the first parity run after a landing must not STOP the canary before its twin can arrive."""

    def _env(self):
        e = Hv4Env(self.tmp)
        e.port = LedgerPort()
        e.raw.update(parity_dir=str(Path(self.tmp) / "par"), twins_file=str(Path(self.tmp) / "twins.jsonl"), tieout_dir=str(Path(self.tmp) / "tie"))
        return e

    def _land(self, b, e, mint="M1"):
        t = e.clock()
        kw = {"landed": True, "tokens_received": 1_000_000, "spend_lamports": 50_000_000, "confirm_seen_ms": t, "decision_t_ms": t - 1_000, "fee_lamports": 60_000,
              "signature": "sigB-" + mint, "tokens_source": "meta"}
        self.assertIsNone(b.gates.recorder.on_buy(mint, kw, t, t + 3_600_000))
        return t

    def _twin_record(self, e, covered, run_ms):
        import hashlib
        tw = Path(e.raw["twins_file"])
        tw.write_text("")
        from tools import h5_arena_gates as g
        rec = {"schema": g.TWIN_RUN_SCHEMA, "run_ms": run_ms, "covered_trip_ids": list(covered), "twins_sha256": hashlib.sha256(tw.read_bytes()).hexdigest(),
               "config_hash": "0" * 64, "code_sha256": "0" * 64}
        Path(str(tw) + ".run.json").write_text(json.dumps(rec))

    def test_inside_the_grace_no_parity_run_no_stop_and_no_buy(self):
        e = self._env()
        calls = []
        b = alb.Bridge(alb.Config.from_dict(e.raw), e.port, now_ms=e.clock, live_ok_path=e.live_ok, tier_path=e.tier, root_checks=False,
                       parity_runner=lambda argv: calls.append(argv) or 10)
        self.assertIsInstance(b.gates, c0p.Hv4LiveGates)
        t = self._land(b, e)
        b.gates.maybe_run_parity(e.clock())
        self.assertEqual(b.gates.parity_refusal(e.clock()), c0p.TWIN_GRACE_REFUSAL)
        e.clock.t = t + c0p.TWIN_GRACE_MS - 1
        b.gates.maybe_run_parity(e.clock())
        self.assertEqual(b.gates.parity_refusal(e.clock()), c0p.TWIN_GRACE_REFUSAL)
        self.assertEqual([a for a in calls if a[0] == "tools.arena_live_parity"], [])
        self.assertFalse(Path(c0p.parity_stop_path(e.raw["parity_dir"])).exists())

    def test_a_twin_that_covers_the_trip_ends_the_grace_at_once(self):
        e = self._env()
        calls = []
        b = alb.Bridge(alb.Config.from_dict(e.raw), e.port, now_ms=e.clock, live_ok_path=e.live_ok, tier_path=e.tier, root_checks=False,
                       parity_runner=lambda argv: calls.append(argv) or 0)
        t = self._land(b, e)
        self._twin_record(e, [], t + 5)  # a run that does not cover M1: still pending
        self.assertTrue(b.gates.twin_pending(e.clock()))
        self._twin_record(e, ["M1"], t - 1)  # covers M1 but is older than the landing: still pending
        self.assertTrue(b.gates.twin_pending(e.clock()))
        self._twin_record(e, ["M1"], t + 5)
        self.assertFalse(b.gates.twin_pending(e.clock()))
        self.assertIsNone(b.gates.parity_refusal(e.clock()))
        self.assertTrue(any(a[0] == "tools.arena_live_parity" for a in calls))

    def test_after_the_grace_the_real_tool_runs_and_writes_stop_when_the_twin_never_came(self):
        e = self._env()
        r = _Runner()
        b = alb.Bridge(alb.Config.from_dict(e.raw), e.port, now_ms=e.clock, live_ok_path=e.live_ok, tier_path=e.tier, root_checks=False, parity_runner=r)
        t = self._land(b, e)
        self._twin_record(e, [], t - 10)
        e.clock.t = t + c0p.TWIN_GRACE_MS
        why = b.gates.parity_refusal(e.clock())
        self.assertTrue(why and why.startswith("parity_gate:rc_"), why)
        self.assertTrue(any(a[0] == "tools.arena_live_parity" for a in r.calls))
        self.assertTrue(Path(c0p.parity_stop_path(e.raw["parity_dir"])).exists())  # P7 and STOP, as before, once the grace is over
        self.assertEqual(b._files(), (False, True))

    def test_no_landing_no_grace_and_the_bound_is_at_most_stale_ms(self):
        from tools import arena_live_parity as alp
        self.assertLessEqual(c0p.TWIN_GRACE_MS, alp.STALE_MS)
        e = self._env()
        b = alb.Bridge(alb.Config.from_dict(e.raw), e.port, now_ms=e.clock, live_ok_path=e.live_ok, tier_path=e.tier, root_checks=False, parity_runner=lambda argv: 0)
        self.assertFalse(b.gates.twin_pending(e.clock()))
