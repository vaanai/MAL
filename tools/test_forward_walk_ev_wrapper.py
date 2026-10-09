"""scripts/research/forward-walk-ev.sh (DEC-016 Amendment 9, forward-1002ev). Offline: no network, no /data/mal.

Same harness as tools/test_forward_walk2_wrapper.py (it reuses that file's stubs and `prepare`): the script's test hook
FW2_TEST_ROOT moves its output dir, lock dir and Helius env under a temp dir; the walker and verify are stubs in a stub
`tools` package in the script's cwd; `date` and `sleep` are PATH shims. The script must run under sh, so every
behavior test runs it under `sh` (dash on this host); a few run it under bash as well, and a text test checks that no
bash-ism is in the code. Separate tests read the REAL tools only for their --help text.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tools.test_forward_walk2_wrapper import SENTINEL, prepare

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "research" / "forward-walk-ev.sh"
START = "2026-10-09T00"
STOP = "2026-10-16T01"
FINAL = "2026-10-16T00"
CAP_TOTAL = 3_600_000
H0, H1, H2, H3 = "2026-10-09T00", "2026-10-09T01", "2026-10-09T02", "2026-10-09T03"
SHELLS = [s for s in ("sh", "dash") if shutil.which(s)]


class Result:
    def __init__(self, root: Path, proc: subprocess.CompletedProcess):
        self.root, self.proc = root, proc
        self.rc, self.out, self.err = proc.returncode, proc.stdout, proc.stderr
        self.walk = root / "walkev"

    def calls(self, tool: str | None = None) -> list[dict]:
        p = self.root / "calls.jsonl"
        rows = [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []
        return [r for r in rows if tool is None or r["tool"] == tool]

    def hours(self, tool: str) -> list[str]:
        return [r["hour"] for r in self.calls(tool)]

    def alerts(self) -> list[dict]:
        p = self.walk / "alerts.jsonl"
        return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []

    def progress(self) -> dict:
        return json.loads((self.root / "progress.json").read_text())

    def every_text(self) -> str:
        parts = [self.out, self.err]
        for p in self.root.rglob("*"):
            if p.is_file() and p.name != "helius.env":
                parts.append(p.read_text(errors="replace"))
        return "\n".join(parts)


def run(root: Path, *, cfg: dict | None = None, now: str = "2026-10-09T04:10:00Z", passes: int = 1,
        env_extra: dict | None = None, helius_env: str | None = "default", helius_mode: int | None = None,
        shell: str = "sh", params: dict | None = None) -> Result:
    """params: MISCUSI_PARAM_* to set (name -> value); none by default (the pins apply). helius_env: the env file's
    text, None for no file, "default" for a file holding SENTINEL as the key."""
    prepare(root, cfg or {})
    if helius_env is None:
        (root / "helius.env").unlink()
    elif helius_env != "default":
        (root / "helius.env").write_text(helius_env)
    if helius_mode is not None:
        (root / "helius.env").chmod(helius_mode)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("MISCUSI_", "FW2_", "HELIUS"))}
    env.update(
        PATH=f"{root / 'bin'}:{env.get('PATH', '/usr/bin:/bin')}",
        MISCUSI_PROGRESS=str(root / "progress.json"),
        FW2_TEST_ROOT=str(root),
        FW2_TEST_PY=sys.executable,
        FW2_TEST_PASSES=str(passes),
        FW2_STUB_ROOT=str(root),
        FW2_FAKE_NOW=now,
    )
    for k, v in (params or {}).items():
        env[f"MISCUSI_PARAM_{k.upper()}"] = v
    env.update(env_extra or {})
    proc = subprocess.run([shell, str(SCRIPT)], cwd=root / "cwd", env=env, capture_output=True, text=True,
                          timeout=300, stdin=subprocess.DEVNULL)
    return Result(root, proc)


class WrapperCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def run_w(self, **kw) -> Result:
        return run(self.root, **kw)


class PinTests(WrapperCase):
    def test_other_start_or_stop_is_refused_before_anything_is_created(self) -> None:
        for name, val in (("start", "2026-10-09T01"), ("start", "2026-10-08T00"), ("start", ""), ("start", "2026-10-09"),
                          ("start", "2026-10-09T00 "), ("stop", "2026-10-16T02"), ("stop", "2026-10-16T00"),
                          ("stop", "")):
            with self.subTest(name=name, val=val):
                with tempfile.TemporaryDirectory() as tmp:
                    r = run(Path(tmp), params={name: val})
                    self.assertEqual(r.rc, 2, r.err)
                    self.assertIn(name + "=", r.err)
                    self.assertFalse(r.walk.exists(), "a refused pin must not create the output dir")
                    self.assertEqual(r.calls(), [])

    def test_the_pins_alone_and_the_pins_as_params_are_accepted(self) -> None:
        for params in (None, {"start": START}, {"start": START, "stop": STOP}):
            with self.subTest(params=params), tempfile.TemporaryDirectory() as tmp:
                r = run(Path(tmp), now="2026-10-09T00:30:00Z", params=params)  # no complete hour yet
                self.assertEqual(r.rc, 0, r.err)
                self.assertEqual(r.calls(), [])

    def test_a_missing_progress_file_is_refused(self) -> None:
        r = self.run_w(env_extra={"MISCUSI_PROGRESS": ""})
        self.assertEqual(r.rc, 2, r.err)
        self.assertFalse(r.walk.exists())

    def test_pin_constants_agree(self) -> None:
        text = SCRIPT.read_text()
        self.assertIn(f"START_PIN={START}\n", text)
        self.assertIn(f"STOP_PIN={STOP} ", text)
        self.assertIn(f"FINAL_HOUR={FINAL} ", text)


class HappyPathTests(WrapperCase):
    def test_walks_each_complete_hour_with_event_v_and_strict_verify(self) -> None:
        r = self.run_w()
        self.assertEqual(r.rc, 0, r.err)
        self.assertEqual(r.hours("walker"), [H0, H1, H2, H3])
        self.assertEqual(r.hours("verify"), [H0, H1, H2, H3])
        walk = str(r.walk)
        for c in r.calls("walker"):
            a = c["argv"]
            self.assertIn("--event-v", a)
            self.assertEqual(a[a.index("--out") + 1], walk)
            self.assertEqual(a[a.index("--credits-file") + 1], walk + "/credit-probe.json")
            self.assertEqual(a[a.index("--credit-cap") + 1], str(CAP_TOTAL))
            self.assertEqual(a[a.index("--hours") + 1], "1")
            for flag, val in (("--rps", "8"), ("--lookup-rps", "2"), ("--workers", "8"), ("--max-bytes", "322122547200")):
                self.assertEqual(a[a.index(flag) + 1], val, flag)
            self.assertTrue(c["has_key"], "the Helius env must reach the walker")
        first = r.calls("walker")[0]["argv"]
        self.assertEqual(first[first.index("--until") + 1], "2026-10-09T01:00:00Z")
        for c in r.calls("verify"):
            self.assertEqual(c["argv"][:1], ["verify"])
            self.assertEqual(c["argv"][c["argv"].index("--walk-dir") + 1], walk)
            self.assertIn("--strict-lines", c["argv"])
            self.assertTrue(c["ok"])
        self.assertEqual(json.loads((r.walk / "credit-probe.json").read_text()), {"confirmed_credits_per_getblock": 1})
        self.assertTrue((r.walk / ".nobackup").is_file(), ".nobackup marks the walk dir as re-downloadable bulk data")
        self.assertEqual(r.alerts(), [])
        self.assertIn("TEST MODE", r.out)
        self.assertIn("2026-10-09T03", r.progress()["note"])
        self.assertIn(str(4 * 18000), r.progress()["note"])
        self.assertNotIn("/data/mal", "".join(json.dumps(c["argv"]) for c in r.calls()))

    def test_nobackup_exists_before_the_helius_slot_is_taken(self) -> None:
        # a held-by-others lock set would block forever, so use a bad env file: the guard exits 6 after the slot.
        r = self.run_w(helius_env=None)
        self.assertEqual(r.rc, 6)
        self.assertTrue((r.walk / ".nobackup").is_file())

    def test_second_run_walks_nothing_already_verified(self) -> None:
        self.run_w()
        (self.root / "calls.jsonl").unlink()
        r = self.run_w()
        self.assertEqual(r.rc, 0, r.err)
        self.assertEqual(r.calls(), [])
        self.assertTrue((r.walk / ".nobackup").is_file())

    def test_nothing_before_five_minutes_past(self) -> None:
        r = self.run_w(now="2026-10-09T03:02:00Z")
        self.assertEqual(r.rc, 0, r.err)
        self.assertEqual(r.calls(), [])

    def test_helius_env_is_never_printed_or_written(self) -> None:
        r = self.run_w(cfg={"walker": {H2: [1, 3]}}, passes=2)
        self.assertEqual(r.rc, 3)
        self.assertNotIn(SENTINEL, r.every_text())
        self.assertNotIn("HELIUS_API_KEY", r.out + r.err)

    def test_lock_slots_are_taken_in_order_4_3_2_1(self) -> None:
        r = self.run_w(now="2026-10-09T01:30:00Z")
        self.assertIn("holding helius slot 4", r.out)
        held = open(self.root / "locks" / "helius-4.lock", "w")
        self.addCleanup(held.close)
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        r = self.run_w(now="2026-10-09T01:30:00Z")
        self.assertIn("holding helius slot 3", r.out)

    def test_runs_under_bash_too(self) -> None:
        r = self.run_w(shell="bash")
        self.assertEqual(r.rc, 0, r.err)
        self.assertEqual(r.hours("walker"), [H0, H1, H2, H3])

    def test_runs_under_dash_when_present(self) -> None:
        if shutil.which("dash") is None:
            self.skipTest("no dash")
        r = self.run_w(shell="dash")
        self.assertEqual(r.rc, 0, r.err)
        self.assertEqual(r.hours("walker"), [H0, H1, H2, H3])


class StopTests(WrapperCase):
    def test_the_last_hour_walked_is_2026_10_16T00_then_the_job_ends_itself(self) -> None:
        r = self.run_w(now="2026-10-16T05:10:00Z")  # well past the stop: 169 hours, none after FINAL
        self.assertEqual(r.rc, 0, r.err)
        walked = r.hours("walker")
        self.assertEqual(len(walked), 169)
        self.assertEqual(walked[0], START)
        self.assertEqual(walked[-1], FINAL)
        self.assertNotIn("2026-10-16T01", walked)
        self.assertIn(f"done: every hour {START}..{FINAL} is verified OK", r.out)
        prog = r.progress()
        self.assertEqual(prog["pct"], 100)
        self.assertIn("complete", prog["note"])
        self.assertEqual(r.alerts(), [])
        self.assertLess(169 * 18000, CAP_TOTAL, "the whole walk fits under the cap at the stub's 18k an hour")

    def test_not_done_before_the_final_hour_is_complete(self) -> None:
        r = self.run_w(now="2026-10-16T00:40:00Z")  # last complete hour 10-15T23
        self.assertEqual(r.rc, 0, r.err)
        self.assertEqual(r.hours("walker")[-1], "2026-10-15T23")
        self.assertNotIn("done:", r.out)
        self.assertNotEqual(r.progress().get("pct"), 100)

    def test_final_hour_with_a_bad_verify_is_retried_then_exit_7(self) -> None:
        r = self.run_w(now="2026-10-16T01:10:00Z", cfg={"verify_bad": [FINAL]}, passes=6)
        self.assertEqual(r.rc, 7, r.err)
        self.assertEqual(r.hours("walker").count(FINAL), 3, "one try per pass, three passes")
        (a,) = r.alerts()
        self.assertEqual((a["kind"], a["rc"]), ("unverified_at_stop", 7))
        self.assertTrue(r.progress()["note"].startswith("ALERT unverified_at_stop"))
        self.assertNotIn("done:", r.out)

    def test_a_bad_hour_in_the_middle_is_retried_on_the_next_pass_and_blocks_done(self) -> None:
        r = self.run_w(now="2026-10-16T01:10:00Z", cfg={"verify_bad": ["2026-10-12T07"]}, passes=2)
        self.assertEqual(r.rc, 0, r.err)  # the test hook ends the job after 2 passes
        self.assertEqual(r.hours("walker").count("2026-10-12T07"), 2)
        self.assertEqual(r.hours("walker").count("2026-10-12T08"), 1, "verified hours are not walked again")
        self.assertNotIn("done:", r.out)


class FailureTests(WrapperCase):
    def test_exit_3_is_fatal_alerts_and_stops_the_loop(self) -> None:
        r = self.run_w(cfg={"walker": {H2: [3]}})
        self.assertEqual(r.rc, 3, r.err)
        self.assertEqual(r.hours("walker"), [H0, H1, H2], "the hour after a refused hour must not be walked")
        self.assertEqual(r.hours("verify"), [H0, H1], "a refused hour is not verified")
        self.assertIn("ALERT", r.out)
        (a,) = r.alerts()
        self.assertEqual((a["kind"], a["hour"], a["rc"]), ("walker_refused", H2, 3))
        prog = r.progress()
        self.assertTrue(prog["note"].startswith("ALERT walker_refused " + H2), prog)
        self.assertEqual((prog["kind"], prog["hour"]), ("walker_refused", H2))
        self.assertIn("pct", prog)

    def test_resubmit_meets_the_same_refusal_and_counts_its_credits(self) -> None:
        cfg = {"walker": {H2: [3]}}
        self.run_w(cfg=cfg)
        r = self.run_w(cfg=cfg)
        self.assertEqual(r.rc, 3)
        self.assertEqual(r.hours("walker"), [H0, H1, H2, H2], "H0 and H1 are verified and skipped; H3 is never reached")
        last = r.calls("walker")[-1]["argv"]
        self.assertEqual(last[last.index("--credit-cap") + 1], str(CAP_TOTAL - 700))
        self.assertEqual(len(r.alerts()), 2)

    def test_exit_1_is_a_walk_error_and_the_job_goes_on(self) -> None:
        r = self.run_w(cfg={"walker": {H2: [1]}})
        self.assertEqual(r.rc, 0, r.err)
        self.assertEqual(r.hours("walker"), [H0, H1, H2, H3])
        self.assertIn(f"walk error {H2} rc=1 consecutive=1/3", r.out)
        self.assertIn(f"verify not OK {H2}", r.out)
        self.assertEqual(r.alerts(), [])

    def test_three_consecutive_failures_on_one_hour_stop_the_job(self) -> None:
        r = self.run_w(cfg={"walker": {H2: [1, 1, 1, 1]}}, passes=5)
        self.assertEqual(r.rc, 4, r.err)
        self.assertEqual(r.hours("walker").count(H2), 3)
        self.assertEqual(r.hours("walker").count(H0), 1)
        self.assertEqual(r.hours("walker").count(H3), 1, "H3 is walked after H2 on pass 1, verified, and not walked again")
        self.assertIn(f"walk error {H2} rc=1 consecutive=3/3", r.out)
        (a,) = r.alerts()
        self.assertEqual((a["kind"], a["hour"], a["rc"]), ("walker_failed", H2, 1))
        self.assertTrue(r.progress()["note"].startswith("ALERT walker_failed"))

    def test_two_failures_then_success_does_not_stop(self) -> None:
        r = self.run_w(cfg={"walker": {H2: [1, 1, 0]}}, passes=4)
        self.assertEqual(r.rc, 0, r.err)
        self.assertEqual(r.hours("walker").count(H2), 3)
        self.assertEqual(r.alerts(), [])
        self.assertEqual(r.hours("walker")[-1], H2, "once verified, nothing is walked again")

    def test_a_success_resets_the_count(self) -> None:
        # pass 1 fail, pass 2 walker ok but verify not OK (resets), pass 3 fail, pass 4 fail: 2 in a row, not 3.
        r = self.run_w(cfg={"walker": {H2: [1, 0, 1, 1]}, "verify_bad": [H2]}, passes=4)
        self.assertEqual(r.rc, 0, r.err)
        self.assertEqual(r.hours("walker").count(H2), 4)
        self.assertEqual(r.alerts(), [])
        self.assertIn(f"walk error {H2} rc=1 consecutive=2/3", r.out)

    def test_failure_counts_are_per_hour(self) -> None:
        # H1 and H2 each fail twice (not three in a row for either): no alert, though 4 failures in all.
        r = self.run_w(cfg={"walker": {H1: [1, 1, 0], H2: [1, 1, 0]}}, passes=4)
        self.assertEqual(r.rc, 0, r.err)
        self.assertEqual(r.alerts(), [])
        self.assertIn(f"walk error {H1} rc=1 consecutive=2/3", r.out)
        self.assertIn(f"walk error {H2} rc=1 consecutive=2/3", r.out)

    def test_verify_not_ok_does_not_stop_later_hours(self) -> None:
        r = self.run_w(cfg={"verify_bad": [H2]})
        self.assertEqual(r.rc, 0, r.err)
        self.assertEqual(r.hours("walker"), [H0, H1, H2, H3])
        self.assertIn(f"verify not OK {H2}", r.out)
        self.assertEqual(r.alerts(), [])


class CreditTests(WrapperCase):
    def seed(self, checkpoint: int | None = None, refusals: list[str] | None = None) -> None:
        self.root.joinpath("walkev").mkdir(parents=True, exist_ok=True)
        walk = self.root / "walkev"
        if checkpoint is not None:
            (walk / "checkpoint.json").write_text(json.dumps({"credits_used": checkpoint}))
        if refusals is not None:
            (walk / "refusals.jsonl").write_text("\n".join(refusals) + "\n")

    def cap_args(self, r: Result) -> set[str]:
        return {c["argv"][c["argv"].index("--credit-cap") + 1] for c in r.calls("walker")}

    def test_cap_is_3_6m(self) -> None:
        self.assertIn("CAP_TOTAL=3600000\n", SCRIPT.read_text())
        self.assertIn("3,600,000", SCRIPT.read_text())

    def test_refusal_credits_are_taken_off_the_cap(self) -> None:
        self.seed(refusals=[
            json.dumps({"credits_spent_in_hour_before_refusal": 1500}),
            "",
            "not json",
            json.dumps({"credits_spent_in_hour_before_refusal": 2500}),
            json.dumps({"hour": "no credit field"}),
        ])
        r = self.run_w()
        self.assertEqual(r.rc, 0, r.err)
        self.assertEqual(self.cap_args(r), {str(CAP_TOTAL - 4000)})
        self.assertIn("refused 4000", r.progress()["note"])
        self.assertIn("unreadable", r.err)

    def test_stops_with_an_alert_when_less_than_an_hour_is_left(self) -> None:
        # 3,580,000 + 10,000 refused leaves 10,000 under 3,600,000: below the 20,000 reserve.
        self.seed(checkpoint=3_580_000, refusals=[json.dumps({"credits_spent_in_hour_before_refusal": 10_000})])
        r = self.run_w()
        self.assertEqual(r.rc, 5, r.err)
        self.assertEqual(r.calls(), [])
        (a,) = r.alerts()
        self.assertEqual((a["kind"], a["hour"]), ("credit_cap", H0))
        self.assertIn("ALERT", r.out)
        self.assertTrue(r.progress()["note"].startswith("ALERT credit_cap"))

    def test_walks_while_the_reserve_is_left(self) -> None:
        self.seed(checkpoint=3_400_000, refusals=[json.dumps({"credits_spent_in_hour_before_refusal": 10_000})])
        r = self.run_w()
        self.assertEqual(r.rc, 0, r.err)
        self.assertEqual(r.hours("walker"), [H0, H1, H2, H3])

    def test_unreadable_checkpoint_is_fatal(self) -> None:
        self.seed()
        (self.root / "walkev" / "checkpoint.json").write_text("{not json")
        r = self.run_w()
        self.assertEqual(r.rc, 5)
        self.assertEqual(r.calls("walker"), [])
        self.assertEqual(r.alerts()[0]["kind"], "credit_state")

    def test_header_arithmetic(self) -> None:
        # 15 h at 13.4k + 154 h at 18k, as the header says; quant-proof's estimate is about 3.3M; the cap is above both.
        total = 15 * 13_400 + 154 * 18_000
        self.assertEqual(15 + 154, 169)
        self.assertLess(total, 3_000_000)
        self.assertGreater(CAP_TOTAL, 3_300_000 * 1.08, "the cap leaves margin over quant-proof's 3.3M")
        self.assertIn("3.3M", SCRIPT.read_text())
        self.assertIn("BAD hours", SCRIPT.read_text())


class HeliusEnvGuardTests(WrapperCase):
    """Guard: the Helius env must give a key before the walk loop. Walker reads HELIUS_API_KEY (resolve_rpc_url)."""

    def assert_refused_for_env(self, r: Result) -> None:
        self.assertEqual(r.rc, 6, r.err)
        self.assertEqual(r.calls(), [], "nothing walked or verified: no credit spent")
        self.assertFalse((r.walk / "checkpoint.json").exists())
        (a,) = r.alerts()
        self.assertEqual((a["kind"], a["hour"], a["rc"]), ("helius_env", "-", 6))
        self.assertIn("ALERT helius_env", r.out)
        self.assertTrue(r.progress()["note"].startswith("ALERT helius_env"), r.progress())
        self.assertEqual(r.progress()["kind"], "helius_env")
        with open(r.root / "locks" / "helius-4.lock", "w") as fh:  # the slot is released
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.assertNotIn(SENTINEL, r.every_text())
        self.assertNotIn("HELIUS_API_KEY", r.out + r.err)

    def test_empty_key_refuses_with_an_alert_and_walks_nothing(self) -> None:
        r = self.run_w(helius_env=f"OTHER_VALUE={SENTINEL}\nHELIUS_API_KEY=\n")
        self.assert_refused_for_env(r)
        self.assertIn("no key", r.alerts()[0]["msg"])

    def test_unset_and_blank_keys_refuse(self) -> None:
        for name, text in (("absent", f"OTHER_VALUE={SENTINEL}\n"), ("empty file", ""),
                           ("blank", 'HELIUS_API_KEY="   \t "\n'), ("comment only", "# HELIUS_API_KEY=x\n")):
            with self.subTest(case=name), tempfile.TemporaryDirectory() as tmp:
                self.assert_refused_for_env(run(Path(tmp), helius_env=text))

    def test_inherited_key_does_not_hide_an_empty_file_key(self) -> None:
        r = self.run_w(helius_env="HELIUS_API_KEY=\n", env_extra={"HELIUS_API_KEY": SENTINEL})
        self.assert_refused_for_env(r)

    def test_missing_env_file_refuses_the_same_way(self) -> None:
        r = self.run_w(helius_env=None)
        self.assert_refused_for_env(r)
        self.assertIn("missing or unreadable", r.alerts()[0]["msg"])

    @unittest.skipIf(os.geteuid() == 0, "root reads any file")
    def test_unreadable_env_file_refuses_the_same_way(self) -> None:
        r = self.run_w(helius_mode=0o000)
        self.assert_refused_for_env(r)
        self.assertIn("missing or unreadable", r.alerts()[0]["msg"])

    def test_malformed_env_file_does_not_echo_its_lines(self) -> None:
        # a shell prints the offending line of a file it cannot parse, and that line is the key
        for shell in SHELLS + ["bash"]:
            with self.subTest(shell=shell), tempfile.TemporaryDirectory() as tmp:
                r = run(Path(tmp), helius_env=f"HELIUS_API_KEY={SENTINEL}(\n", shell=shell)
                self.assert_refused_for_env(r)

    def test_env_file_that_references_an_unset_variable_gets_the_normal_refusal(self) -> None:
        r = self.run_w(helius_env=f"OTHER_VALUE={SENTINEL}\nHELIUS_API_KEY=$FW2_NEVER_DEFINED\n")
        self.assert_refused_for_env(r)
        self.assertNotIn("FW2_NEVER_DEFINED", r.every_text())

    def test_a_good_key_still_walks(self) -> None:
        r = self.run_w(helius_env=f"HELIUS_API_KEY={SENTINEL}\n")
        self.assertEqual(r.rc, 0, r.err)
        self.assertEqual(r.hours("walker"), [H0, H1, H2, H3])
        self.assertEqual(r.alerts(), [])

    def test_a_key_with_surrounding_quotes_and_export_walks(self) -> None:
        r = self.run_w(helius_env=f'export HELIUS_API_KEY="{SENTINEL}"\n')
        self.assertEqual(r.rc, 0, r.err)
        self.assertTrue(all(c["has_key"] for c in r.calls("walker")))

    def test_the_guard_sits_before_the_walk_loop_and_releases_the_lock(self) -> None:
        text = SCRIPT.read_text()
        guard = text.index("helius_env_refuse()")
        self.assertLess(text.index('. "$HELIUS_ENV"'), text.index("while :; do\n  NOWH"))
        self.assertLess(guard, text.index("while :; do\n  NOWH"))
        body = text[guard:text.index("}", guard)]
        self.assertIn("alert helius_env - 6", body)
        self.assertLess(body.index("exec 9>&-"), body.index("exit 6"))


class TestHookInJobTests(WrapperCase):
    def test_test_root_with_a_job_id_refuses_before_anything_is_created(self) -> None:
        r = self.run_w(env_extra={"MISCUSI_JOB_ID": "1234"})
        self.assertEqual(r.rc, 2, r.err)
        self.assertEqual(len([x for x in r.err.splitlines() if x.strip()]), 1, r.err)
        self.assertIn("FW2_TEST_ROOT", r.err)
        self.assertIn("MISCUSI_JOB_ID", r.err)
        self.assertNotIn("TEST MODE", r.out)
        self.assertFalse(r.walk.exists(), "D must not be created")
        self.assertEqual(list((self.root / "locks").iterdir()), [], "no lock file may be created")
        self.assertFalse((self.root / "progress.json").exists())
        self.assertEqual(r.calls(), [])

    def test_an_empty_job_id_still_refuses(self) -> None:
        r = self.run_w(env_extra={"MISCUSI_JOB_ID": ""})
        self.assertEqual(r.rc, 2, r.err)
        self.assertFalse(r.walk.exists())

    def test_without_a_job_id_the_hook_still_works(self) -> None:
        r = self.run_w(now="2026-10-09T01:30:00Z")
        self.assertEqual(r.rc, 0, r.err)
        self.assertIn("TEST MODE", r.out)

    def test_the_check_comes_before_the_pins_and_before_anything_is_created(self) -> None:
        text = SCRIPT.read_text()
        self.assertLess(text.index("MISCUSI_JOB_ID"), text.index("START_PIN=2026"))
        self.assertLess(text.index("MISCUSI_JOB_ID"), text.index('mkdir -p "$D"'))


class ScriptTextTests(unittest.TestCase):
    def setUp(self) -> None:
        self.text = SCRIPT.read_text()
        self.code = "\n".join(x for x in self.text.splitlines() if not x.lstrip().startswith("#"))

    def test_posix_sh_syntax(self) -> None:
        for shell in SHELLS + ["bash"]:
            subprocess.run([shell, "-n", str(SCRIPT)], check=True, stdin=subprocess.DEVNULL)

    def test_shebang_is_sh_and_the_file_is_executable(self) -> None:
        self.assertTrue(self.text.startswith("#!/bin/sh\n"))
        self.assertTrue(os.access(SCRIPT, os.X_OK))

    def test_no_bashisms_in_the_code(self) -> None:
        bad = {
            "[[": r"\[\[",
            "declare/typeset": r"\b(declare|typeset)\b",
            "local": r"(^|\s)local\s",
            "PIPESTATUS": r"PIPESTATUS",
            "BASH_ vars": r"BASH_[A-Z]+",
            "${x//y}": r"\$\{[A-Za-z_]+//",
            "${x:o:l}": r"\$\{[A-Za-z_]+:[0-9]+",
            "${!x}": r"\$\{!",
            "<<<": r"<<<",
            "process substitution": r"<\(|>\(",
            "(( )) command": r"(^|[;&|{]\s*|\sthen\s+|\sdo\s+)\(\(",
            "function kw": r"(^|\s)function\s",
            "source": r"(^|\s)source\s",
            "echo -e": r"echo\s+-[en]",
            "array": r"\w+=\(",
            "&>": r"&>",
            "|&": r"\|&",
            "$'..'": r"\$'",
            "==": r"\[[^\]]*==",
        }
        for name, pat in bad.items():
            with self.subTest(bashism=name):
                self.assertIsNone(re.search(pat, self.code, re.M), name)

    def test_job_constants(self) -> None:
        self.assertIn("D=/data/mal/blocks/forward-1002ev;", self.text)
        self.assertIn("/data/mal/locks", self.text)
        self.assertIn("/var/lib/mal/backfill/helius.env", self.text)
        self.assertIn(".nobackup", self.code)
        self.assertIn("--event-v", self.code)
        self.assertIn("--strict-lines", self.code)
        self.assertIn("for i in 4 3 2 1", self.code)

    def test_never_names_another_walk_dir_outside_comments(self) -> None:
        for line in self.code.splitlines():
            self.assertNotRegex(line.replace("forward-1002ev", ""), r"forward-1002|forward-1016|forward-walk2")
        self.assertEqual(sum("blocks/forward-1002ev" in x for x in self.code.splitlines()), 1, "D= is the only line that names the walk dir")

    def test_no_trace_or_env_dump(self) -> None:
        for bad in ("set -x", "set -o xtrace", "printenv", "env |", 'cat "$HELIUS_ENV"'):
            self.assertNotIn(bad, self.code)

    def test_walk_1_and_walk_2_scripts_are_not_this_file(self) -> None:
        walk1 = (REPO / "scripts" / "research" / "forward-walk.sh").read_text()
        walk2 = (REPO / "scripts" / "research" / "forward-walk2.sh").read_text()
        self.assertIn("forward-1002\n", walk1.replace("forward-1002;", "forward-1002\n"))
        self.assertNotIn("--event-v", walk1)
        self.assertIn("forward-1016", walk2)
        self.assertNotIn("forward-1002ev", walk1 + walk2)

    def test_code_lines_are_those_of_the_running_job(self) -> None:
        # job #433 runs this script at 153f1a02; later commits may change comments only
        ref = "153f1a02fc9b9540644e48e3cf4fefacd035fca1"
        if shutil.which("git") is None:
            self.skipTest("no git")
        proc = subprocess.run(["git", "show", f"{ref}:scripts/research/forward-walk-ev.sh"], cwd=REPO, capture_output=True,
                              text=True, timeout=60)
        if proc.returncode != 0:
            self.skipTest("the job's commit is not in this clone")

        def code(text: str) -> list[str]:
            return [x for x in text.splitlines() if not x.lstrip().startswith("#")]

        self.assertEqual(code(self.text), code(proc.stdout))

    def test_credit_governance_is_in_the_header(self) -> None:
        self.assertIn("when the spend reaches 3.0M", self.text)
        self.assertIn("recorded owner OK", self.text)
        self.assertIn("cancels this job", self.text)

    def test_window_hours(self) -> None:
        from datetime import datetime
        a, b = datetime.strptime(START, "%Y-%m-%dT%H"), datetime.strptime(STOP, "%Y-%m-%dT%H")
        self.assertEqual(int((b - a).total_seconds() // 3600), 169)
        self.assertEqual(FINAL, "2026-10-16T00")


class RealToolContractTests(unittest.TestCase):
    """Every walker/verify flag the script passes exists in the real tools."""

    def help_text(self, *args: str) -> str:
        env = {k: v for k, v in os.environ.items() if not k.startswith(("HELIUS", "MISCUSI_"))}
        env["PYTHONPATH"] = str(REPO)
        p = subprocess.run([sys.executable, *args, "--help"], cwd=REPO, env=env, capture_output=True, text=True,
                           timeout=60, stdin=subprocess.DEVNULL)
        self.assertEqual(p.returncode, 0, p.stderr)
        return p.stdout

    def test_walker_flags_exist(self) -> None:
        text = SCRIPT.read_text()
        line = next(x for x in text.splitlines() if "tools.pump_history_backfill" in x and "--until" in x)
        flags = set(re.findall(r"(?<=\s)--[a-z][a-z-]*", line))
        self.assertTrue({"--event-v", "--until", "--hours", "--out", "--credits-file", "--credit-cap", "--max-bytes",
                         "--rps", "--lookup-rps", "--workers"} <= flags, flags)
        h = self.help_text("-m", "tools.pump_history_backfill")
        for f in flags:
            self.assertIn(f, h)

    def test_verify_has_strict_lines(self) -> None:
        h = self.help_text("-m", "tools.exp012_forward", "verify")
        for flag in ("--walk-dir", "--hour", "--strict-lines"):
            self.assertIn(flag, h)


if __name__ == "__main__":
    unittest.main()
