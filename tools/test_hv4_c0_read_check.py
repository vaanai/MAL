"""Tests for tools/hv4_c0_read_check.py: parity is shown only when match is true AND every unresolved_hard_halts list is empty (an exit code of 0 is never enough), and every
backstop-closed trade is flagged. Synthetic fixtures only: parity files built from the parity tests' fixtures, a bridge run on a synthetic root. No HV4 row, no arena path
(/data/mal/arena appears only as a string in a refusal test), no network, no key."""

from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tools import arena_live_bridge as alb
from tools import arena_live_parity as AP
from tools import hv4_c0_read_check as RC
from tools import hv4_live_c0 as c0
from tools.test_arena_live_parity import NOW, attempts, book, fresh_state
from tools.test_hv4_live_c0 import Hv4Env, write_a3, write_feed

REPO = Path(__file__).resolve().parent.parent


def jl(path: Path, rows) -> None:
    path.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows))


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self._d = tempfile.TemporaryDirectory()
        self.addCleanup(self._d.cleanup)
        self.d = Path(self._d.name)
        self.par = self.d / "par"
        self.par.mkdir()

    def make_parity(self, n=100, unresolved=(), edit_trips=None, **book_kw):
        """A parity dir as the bridge leaves it, with the REAL parity tool's output. Returns the tool's exit code."""
        ts, ws = book(n, **book_kw)
        if edit_trips:
            edit_trips(ts)
        jl(self.par / "trips.jsonl", ts)
        jl(self.par / "twins.jsonl", ws)
        jl(self.par / "attempts.jsonl", attempts(40))
        (self.par / "state.json").write_text(json.dumps(fresh_state(unresolved_hard_halts=list(unresolved)), sort_keys=True))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
            rc = AP.main(["--trips", str(self.par / "trips.jsonl"), "--twins", str(self.par / "twins.jsonl"), "--attempts", str(self.par / "attempts.jsonl"),
                          "--state", str(self.par / "state.json"), "--now-ms", str(NOW), "--stop-file", str(self.d / "STOP"), "--json", str(self.par / "parity-result.json")])
        return rc

    def run_check(self, *args):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = RC.main(list(args))
        return rc, buf.getvalue()


class ParityNotEnoughRc0(Base):
    def test_a_matching_book_is_shown_and_clear(self):
        self.assertEqual(self.make_parity(), 0)
        rc, out = self.run_check("--parity-dir", str(self.par), "--parity-rc", "0")
        self.assertEqual(rc, 0, out)
        self.assertIn("PARITY SHOWN", out)
        self.assertNotIn("parity not shown", out)
        self.assertIn("RESULT CLEAR", out)

    def test_qp_ruling_1_the_tool_exits_0_with_a_stake_mismatch_and_the_check_says_not_shown(self):
        """QP #970 ruling 1: a bridge latch (stake_mismatch) rides in the state's unresolved_hard_halts; the parity tool's exit code stays 0."""
        self.assertEqual(self.make_parity(unresolved=["stake_mismatch"]), 0)  # the tool's rc is 0
        res = json.loads((self.par / "parity-result.json").read_text())
        self.assertFalse(res["match"])
        self.assertEqual(res["unresolved_hard_halts"], ["stake_mismatch"])
        rc, out = self.run_check("--parity-dir", str(self.par), "--parity-rc", "0")
        self.assertEqual(rc, 2, out)
        self.assertIn("parity not shown", out)
        self.assertIn("stake_mismatch", out)
        self.assertNotIn("PARITY SHOWN", out)
        for latch in ("tieout_mismatch", "parity_hard_halt", "landed_buy_unreadable", "twin_pin_mismatch"):
            self.make_parity(unresolved=[latch])
            self.assertEqual(self.run_check("--parity-dir", str(self.par), "--parity-rc", "0")[0], 2, latch)

    def test_a_latch_raised_after_the_last_parity_run_blocks_through_the_state_file(self):
        self.make_parity()
        (self.par / "state.json").write_text(json.dumps(fresh_state(unresolved_hard_halts=["tieout_mismatch"])))
        rc, out = self.run_check("--parity-dir", str(self.par))
        self.assertEqual(rc, 2, out)
        self.assertIn("state unresolved_hard_halts is ['tieout_mismatch']", out)

    def test_rc_zero_with_no_result_file_is_not_shown(self):
        self.make_parity()
        (self.par / "parity-result.json").unlink()
        rc, out = self.run_check("--parity-dir", str(self.par), "--parity-rc", "0")
        self.assertEqual(rc, 2, out)
        self.assertIn("parity not shown", out)
        self.assertIn("missing", out)

    def test_a_non_zero_rc_blocks_a_clean_looking_result(self):
        self.make_parity()
        for code in ("10", "11", "99"):
            rc, out = self.run_check("--parity-dir", str(self.par), "--parity-rc", code)
            self.assertEqual(rc, 2, code)
            self.assertIn(f"parity tool rc {code}", out)

    def test_fewer_than_100_eligible_trips_is_not_shown(self):
        self.assertEqual(self.make_parity(n=99), 0)
        rc, out = self.run_check("--parity-dir", str(self.par), "--parity-rc", "0")
        self.assertEqual(rc, 2)
        self.assertIn("99 eligible", out)

    def test_match_must_be_the_boolean_true(self):
        self.make_parity()
        p = self.par / "parity-result.json"
        for bad in ("true", 1, "yes", None):
            res = json.loads(p.read_text())
            res["match"] = bad
            q = self.d / "r.json"
            q.write_text(json.dumps(res))
            rc, out = self.run_check("--result", str(q), "--state", str(self.par / "state.json"), "--trips", str(self.par / "trips.jsonl"))
            self.assertEqual(rc, 2, bad)
            self.assertIn("not true", out)

    def test_missing_or_malformed_unresolved_lists_are_not_shown(self):
        self.make_parity()
        res = json.loads((self.par / "parity-result.json").read_text())
        for key in ("unresolved_hard_halts", "hard_halts"):
            r2 = {k: v for k, v in res.items() if k != key}
            q = self.d / "r.json"
            q.write_text(json.dumps(r2))
            rc, out = self.run_check("--result", str(q), "--state", str(self.par / "state.json"), "--trips", str(self.par / "trips.jsonl"))
            self.assertEqual(rc, 2, key)
            self.assertIn(f"result has no {key}", out)
            r3 = {**res, key: "none"}
            q.write_text(json.dumps(r3))
            self.assertEqual(self.run_check("--result", str(q), "--state", str(self.par / "state.json"), "--trips", str(self.par / "trips.jsonl"))[0], 2)
        for bad in (None, "x", {}, ["a"]):
            st = fresh_state()
            if bad is None:
                st.pop("unresolved_hard_halts")
            else:
                st["unresolved_hard_halts"] = bad
            (self.par / "state.json").write_text(json.dumps(st))
            self.assertEqual(self.run_check("--parity-dir", str(self.par))[0], 2, bad)

    def test_a_result_older_than_the_trips_file_is_not_accepted(self):
        self.make_parity()
        with (self.par / "trips.jsonl").open("a") as fh:
            fh.write(json.dumps({"trip_id": "late"}) + "\n")
        rc, out = self.run_check("--parity-dir", str(self.par))
        self.assertEqual(rc, 2)
        self.assertIn("covers 100 trips, the trips file has 101", out)

    def test_unusable_inputs_fail_closed(self):
        self.make_parity()
        (self.par / "parity-result.json").write_text("{not json")
        self.assertEqual(self.run_check("--parity-dir", str(self.par))[0], 2)
        self.make_parity()
        real = self.par / "parity-result.json"
        moved = self.d / "real.json"
        real.rename(moved)
        os.symlink(moved, real)  # a link is not the tool's own output
        rc, out = self.run_check("--parity-dir", str(self.par))
        self.assertEqual(rc, 2)
        self.assertIn("not_a_regular_file", out)
        rc, out = self.run_check("--parity-dir", str(self.d / "nowhere"))
        self.assertEqual(rc, 2)
        self.assertIn("parity not shown", out)

    def test_a_path_under_the_sealed_prefix_is_refused_as_a_string(self):
        rc, out = self.run_check("--parity-dir", "/data/mal/arena/x")
        self.assertEqual(rc, 2)
        self.assertIn("path_under_sealed_arena", out)
        self.assertIn("parity not shown", out)

    def test_bridge_state_latches(self):
        self.make_parity()
        bs = self.d / "bridge-state-live.json"
        bs.write_text(json.dumps({"latches": {"stake_mismatch": "spent 1 order 2"}, "orders": {}}))
        rc, out = self.run_check("--parity-dir", str(self.par), "--bridge-state", str(bs))
        self.assertEqual(rc, 2)
        self.assertIn("hard latch(es) ['stake_mismatch']", out)
        bs.write_text(json.dumps({"latches": {"total_loss_cap": "x"}, "orders": {}}))
        rc, out = self.run_check("--parity-dir", str(self.par), "--bridge-state", str(bs))
        self.assertEqual(rc, 0, out)
        self.assertIn("non-parity latch(es) ['total_loss_cap']", out)
        bs.write_text("[]")
        self.assertEqual(self.run_check("--parity-dir", str(self.par), "--bridge-state", str(bs))[0], 2)

    def test_soft_halts_are_printed_as_notes(self):
        self.make_parity()
        p = self.par / "parity-result.json"
        res = json.loads(p.read_text())
        res["soft_halts"] = ["P2"]
        p.write_text(json.dumps(res))
        rc, out = self.run_check("--parity-dir", str(self.par))
        self.assertEqual(rc, 0, out)
        self.assertIn("NOTE soft halts in the result: ['P2']", out)

    def test_cli_exit_codes_and_json(self):
        self.make_parity()
        out_json = self.d / "rec.json"
        env = {"PATH": "/usr/bin:/bin"}
        p = subprocess.run([sys.executable, "-m", "tools.hv4_c0_read_check", "--parity-dir", str(self.par), "--json", str(out_json)], capture_output=True, text=True, cwd=REPO, env=env)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(json.loads(out_json.read_text())["exit_code"], 0)
        p = subprocess.run([sys.executable, "-m", "tools.hv4_c0_read_check"], capture_output=True, text=True, cwd=REPO, env=env)
        self.assertNotEqual(p.returncode, 0)  # usage error: never a clean exit
        (self.par / "state.json").unlink()
        p = subprocess.run([sys.executable, "-m", "tools.hv4_c0_read_check", "--parity-dir", str(self.par)], capture_output=True, text=True, cwd=REPO, env=env)
        self.assertEqual(p.returncode, 2)
        self.assertIn("parity not shown", p.stdout)

    def test_stdlib_only_when_no_bridge_state_is_given(self):
        code = "import sys; import tools.hv4_c0_read_check; print('numpy' in sys.modules, 'tools.hv4_c0_parity' in sys.modules)"
        p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=REPO)
        self.assertEqual(p.stdout.strip(), "False False", p.stdout + p.stderr)


class BackstopFlags(Base):
    def dead_runner_log(self, silence_extra_ms=0, keep_field=True, land=True):
        """A REAL bridge run (synthetic root): one position, no exit row ever comes, the time stop backstop sells. Returns (log path, state path, order mint)."""
        e = Hv4Env(str(self.d / "env"))
        b = e.bridge()
        e.start()
        e.entry_sz("o1")
        b.tick()
        e.port.events.append({"order_id": "o1", "event": "buy_landed", "landed_slot": 1002})
        b.tick()
        e.clock.t += b.cfg.max_hold_ms + alb.BACKSTOP_EXTRA_MS + 1 + silence_extra_ms
        write_feed(e.raw["root"], e.bot, e.clock.t)
        write_a3(e.a3_file, e.clock.t)
        b.tick()
        self.assertEqual(e.port.calls[-1], ("sell", "o1", "time_stop_backstop"))
        if land:
            e.port.events.append({"order_id": "o1", "event": "sell_landed", "landed_slot": 1100, "pnl_lamports": 0})
            b.tick()
        log = Path(e.raw["state_dir"]) / "bridge-log-dry.jsonl"
        if not keep_field:
            rows = [json.loads(x) for x in log.read_text().splitlines()]
            for r in rows:
                r.pop("runner_silent_ms", None)
            jl(log, rows)
        return log, Path(e.raw["state_dir"]) / "bridge-state-dry.json", "M-P1"

    def test_bridge_logs_how_long_the_runner_had_been_silent_on_a_backstop_sell(self):
        log, _st, _m = self.dead_runner_log()
        sells = [json.loads(x) for x in log.read_text().splitlines() if '"sell_sent"' in x]
        self.assertEqual([s["reason"] for s in sells], ["time_stop_backstop"])
        ms = sells[0]["runner_silent_ms"]
        self.assertGreater(ms, 600_000)  # at least max_hold + the backstop margin with no row since the entry
        self.assertLess(ms, c0.RUNNER_SILENT_MS)

    def test_a_backstop_close_is_flagged_and_parity_still_shown_gives_exit_3(self):
        self.make_parity()
        log, st, mint = self.dead_runner_log()
        rc, out = self.run_check("--parity-dir", str(self.par), "--bridge-log", str(log), "--bridge-state", str(st))
        self.assertEqual(rc, 3, out)
        self.assertIn("PARITY SHOWN", out)
        flag = [ln for ln in out.splitlines() if ln.startswith("BACKSTOP_FLAG")]
        self.assertEqual(len(flag), 1, out)
        self.assertIn(f"mint={mint}", flag[0])
        self.assertIn("reason=time_stop_backstop", flag[0])
        self.assertIn("closed_by_backstop=yes", flag[0])
        self.assertIn("order_ids=o1", flag[0])
        self.assertIn("bound=under", flag[0])
        self.assertNotIn("runner_silent_ms=unknown", flag[0])

    def test_silence_over_the_bridges_own_bound_is_labelled_over(self):
        self.make_parity()
        log, _st, _m = self.dead_runner_log(silence_extra_ms=c0.RUNNER_SILENT_MS)
        rc, out = self.run_check("--parity-dir", str(self.par), "--bridge-log", str(log))
        self.assertEqual(rc, 3)
        self.assertIn("bound=over", out)

    def test_a_log_without_the_field_or_no_log_is_unknown_and_still_flagged(self):
        self.make_parity()
        log, st, _m = self.dead_runner_log(keep_field=False)
        rc, out = self.run_check("--parity-dir", str(self.par), "--bridge-log", str(log))
        self.assertEqual(rc, 3, out)
        self.assertIn("runner_silent_ms=unknown", out)
        rc, out = self.run_check("--parity-dir", str(self.par), "--bridge-state", str(st))  # state only: no log, silence unknown
        self.assertEqual(rc, 3, out)
        self.assertIn("sources=bridge_state", out)
        self.assertIn("NOTE no bridge log given", out)

    def test_a_backstop_attempt_that_never_landed_is_flagged_as_not_closed(self):
        self.make_parity()
        log, _st, _m = self.dead_runner_log(land=False)
        rc, out = self.run_check("--parity-dir", str(self.par), "--bridge-log", str(log))
        self.assertEqual(rc, 3)
        self.assertIn("closed_by_backstop=no", out)
        self.assertIn("attempts=1", out)

    def test_a_trip_closed_by_a_backstop_in_the_trips_file_is_flagged_with_no_log(self):
        def mark(ts):
            ts[3]["exit_reason"] = "sl_backstop"
            ts[4]["exit_reason"] = "time_stop_backstop"
        self.assertEqual(self.make_parity(edit_trips=mark), 0)
        rc, out = self.run_check("--parity-dir", str(self.par))
        self.assertEqual(rc, 3, out)
        flags = [ln for ln in out.splitlines() if ln.startswith("BACKSTOP_FLAG")]
        self.assertEqual(len(flags), 2)
        self.assertIn("mint=t3 ", flags[0])
        self.assertIn("reason=sl_backstop", flags[0])
        self.assertIn("backstop_flagged_trades=2", out)

    def test_book_exit_trades_are_not_flagged(self):
        self.make_parity()
        e = Hv4Env(str(self.d / "env"))
        b = e.bridge()
        e.start()
        e.entry_sz("o1")
        b.tick()
        e.port.events.append({"order_id": "o1", "event": "buy_landed", "landed_slot": 1002})
        b.tick()
        e.row({"kind": "exit", "order_id": "o1", "pool": "P1", "reason": "tp", "decision_slot": 1010, "attempt": 1})
        b.tick()
        e.port.events.append({"order_id": "o1", "event": "sell_landed", "landed_slot": 1015, "pnl_lamports": 0})
        b.tick()
        log = Path(e.raw["state_dir"]) / "bridge-log-dry.jsonl"
        rows = [json.loads(x) for x in log.read_text().splitlines() if '"sell_sent"' in x]
        self.assertEqual([r["reason"] for r in rows], ["tp"])
        self.assertIsInstance(rows[0]["runner_silent_ms"], int)
        self.assertIsInstance(rows[0]["decision_seq"], int)
        rc, out = self.run_check("--parity-dir", str(self.par), "--bridge-log", str(log))
        self.assertEqual(rc, 0, out)
        self.assertNotIn("BACKSTOP_FLAG", out)

    def test_flags_are_printed_even_when_parity_is_not_shown(self):
        self.make_parity(unresolved=["stake_mismatch"])
        log, _st, _m = self.dead_runner_log()
        rc, out = self.run_check("--parity-dir", str(self.par), "--bridge-log", str(log))
        self.assertEqual(rc, 2)  # parity not shown outranks the flags
        self.assertIn("BACKSTOP_FLAG", out)

    def test_an_unreadable_bridge_log_is_not_a_pass(self):
        self.make_parity()
        bad = self.d / "bad.jsonl"
        bad.write_text('{"kind": "sell_sent"}\nnot json\n')
        rc, out = self.run_check("--parity-dir", str(self.par), "--bridge-log", str(bad))
        self.assertEqual(rc, 2)
        self.assertIn("malformed_line", out)

    def test_the_backstop_reason_strings_are_the_bridges(self):
        src = (REPO / "tools" / "arena_live_bridge.py").read_text()
        body = src[src.index("    def backstops(self)"):src.index("    def warm(self)")]
        for r in RC.BACKSTOP_REASONS:
            self.assertIn(f'reason = "{r}"', body)
        self.assertEqual(len(RC.BACKSTOP_REASONS), body.count('reason = "'))


if __name__ == "__main__":
    unittest.main()
