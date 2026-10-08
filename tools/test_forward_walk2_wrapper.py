"""scripts/research/forward-walk2.sh (DEC-021 walk 2, EXP-022). Offline: no network, no /data/mal.

The wrapper runs under bash in a temp dir. FW2_TEST_ROOT (the script's test hook) moves its output dir, lock dir
and Helius env under that temp dir. The walker and verify are stubs: a stub `tools` package in the wrapper's cwd
(the script puts $PWD first on PYTHONPATH, as it does in a job), whose exit codes come from a config file.
`date` and `sleep` are PATH shims (a fixed clock, no waiting). Separate tests read the REAL tools only for their
--help text, REFUSAL_EXIT and the verified_hours signature, so the flag names the wrapper uses are checked.
"""

from __future__ import annotations

import fcntl
import inspect
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "research" / "forward-walk2.sh"
START = "2026-10-16T01"
CAP_TOTAL = 430_000 * 22 * 125 // 100  # 11,825,000: the header arithmetic
SENTINEL = "SENTINEL-helius-key-0123456789"
H1, H2, H3 = "2026-10-16T01", "2026-10-16T02", "2026-10-16T03"

WALKER_STUB = r'''
import json, os, sys
from datetime import datetime, timedelta
from pathlib import Path

root = Path(os.environ["FW2_STUB_ROOT"])
cfg = json.loads((root / "cfg.json").read_text())
a = sys.argv[1:]


def arg(name):
    return a[a.index(name) + 1]


hour = (datetime.strptime(arg("--until"), "%Y-%m-%dT%H:%M:%SZ") - timedelta(hours=1)).strftime("%Y-%m-%dT%H")
out = Path(arg("--out"))
log = root / "calls.jsonl"
n = 0
if log.exists():
    n = sum(1 for x in log.read_text().splitlines() if json.loads(x)["tool"] == "walker" and json.loads(x)["hour"] == hour)
codes = cfg.get("walker", {}).get(hour, [0])
rc = codes[min(n, len(codes) - 1)]
with log.open("a") as fh:
    fh.write(json.dumps({"tool": "walker", "hour": hour, "argv": a, "rc": rc, "has_key": bool(os.environ.get("HELIUS_API_KEY"))}) + "\n")
if rc == 0:
    (out / f"stats-{hour}.json").write_text("{}")
    cp = out / "checkpoint.json"
    d = json.loads(cp.read_text()) if cp.exists() else {}
    d["credits_used"] = int(d.get("credits_used") or 0) + 18000
    cp.write_text(json.dumps(d))
elif rc == 3:
    with (out / "refusals.jsonl").open("a") as fh:
        fh.write(json.dumps({"hour": hour, "kind": "sink_resume", "credits_spent_in_hour_before_refusal": 700}) + "\n")
    print("REFUSED (data hole): stub", file=sys.stderr)
sys.exit(rc)
'''

FORWARD_STUB = r'''
import json, os, sys
from pathlib import Path


def verified_hours(walk_dir, strict_bad_lines=False):
    p = Path(walk_dir) / "verify.jsonl"
    last = {}
    if p.is_file():
        for line in p.read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                last[r["hour"]] = r
    return {h: r for h, r in last.items() if r.get("issues") == []}


def main():
    root = Path(os.environ["FW2_STUB_ROOT"])
    cfg = json.loads((root / "cfg.json").read_text())
    a = sys.argv[1:]
    assert a[0] == "verify", a
    walk, hour = Path(a[a.index("--walk-dir") + 1]), a[a.index("--hour") + 1]
    ok = (walk / f"stats-{hour}.json").is_file() and hour not in cfg.get("verify_bad", [])
    with (root / "calls.jsonl").open("a") as fh:
        fh.write(json.dumps({"tool": "verify", "hour": hour, "argv": a, "ok": ok}) + "\n")
    with (walk / "verify.jsonl").open("a") as fh:
        fh.write(json.dumps({"hour": hour, "issues": [] if ok else ["not sealed"]}) + "\n")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
'''


class Result:
    def __init__(self, root: Path, proc: subprocess.CompletedProcess):
        self.root, self.proc = root, proc
        self.rc, self.out, self.err = proc.returncode, proc.stdout, proc.stderr
        self.walk = root / "walk2"

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


def prepare(root: Path, cfg: dict) -> None:
    (root / "cfg.json").write_text(json.dumps(cfg))
    cwd = root / "cwd" / "tools"
    cwd.mkdir(parents=True, exist_ok=True)
    (cwd / "__init__.py").write_text("")
    (cwd / "pump_history_backfill.py").write_text(WALKER_STUB)
    (cwd / "exp012_forward.py").write_text(FORWARD_STUB)
    (root / "locks").mkdir(exist_ok=True)
    (root / "helius.env").write_text(f"HELIUS_API_KEY={SENTINEL}\n")
    bin_ = root / "bin"
    bin_.mkdir(exist_ok=True)
    real_date = shutil.which("date")
    (bin_ / "date").write_text(f'#!/bin/sh\nexec {real_date} -d "$FW2_FAKE_NOW" "$@"\n')
    (bin_ / "sleep").write_text("#!/bin/sh\nexit 0\n")
    for name in ("date", "sleep"):
        (bin_ / name).chmod(0o755)


def run(root: Path, *, cfg: dict | None = None, now: str = "2026-10-16T04:10:00Z", start: str | None = START,
        passes: int = 1, env_extra: dict | None = None, helius_env: str | None = "default",
        helius_mode: int | None = None) -> Result:
    """helius_env: the env file's text, None for no file, "default" for a file holding SENTINEL as the key."""
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
    if start is not None:
        env["MISCUSI_PARAM_START"] = start
    env.update(env_extra or {})
    proc = subprocess.run(["bash", str(SCRIPT)], cwd=root / "cwd", env=env, capture_output=True, text=True,
                          timeout=120, stdin=subprocess.DEVNULL)
    return Result(root, proc)


class WrapperCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def run_w(self, **kw) -> Result:
        return run(self.root, **kw)


class StartTests(WrapperCase):
    def test_other_starts_are_refused_before_anything_is_created(self) -> None:
        for bad in ("2026-10-16T02", "2026-10-15T01", "2026-10-16", "2026-10-16T01:00", "2026-10-16T01 ", ""):
            with self.subTest(start=bad):
                r = self.run_w(start=bad)
                self.assertEqual(r.rc, 2, r.err)
                self.assertIn("2026-10-16T01", r.err)
                self.assertFalse(r.walk.exists(), "refused start must not create the output dir")
                self.assertEqual(r.calls(), [])

    def test_missing_start_is_refused(self) -> None:
        r = self.run_w(start=None)
        self.assertEqual(r.rc, 2, r.err)
        self.assertFalse(r.walk.exists())
        self.assertEqual(r.calls(), [])

    def test_exact_start_is_accepted(self) -> None:
        r = self.run_w(now="2026-10-16T01:30:00Z")  # no complete hour yet
        self.assertEqual(r.rc, 0, r.err)
        self.assertEqual(r.calls(), [])


class HappyPathTests(WrapperCase):
    def test_walks_each_complete_hour_with_event_v_and_strict_verify(self) -> None:
        r = self.run_w()
        self.assertEqual(r.rc, 0, r.err)
        self.assertEqual(r.hours("walker"), [H1, H2, H3])
        self.assertEqual(r.hours("verify"), [H1, H2, H3])
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
        self.assertEqual(r.calls("walker")[0]["argv"][r.calls("walker")[0]["argv"].index("--until") + 1], "2026-10-16T02:00:00Z")
        for c in r.calls("verify"):
            self.assertEqual(c["argv"][:1], ["verify"])
            self.assertEqual(c["argv"][c["argv"].index("--walk-dir") + 1], walk)
            self.assertIn("--strict-lines", c["argv"])
            self.assertTrue(c["ok"])
        self.assertEqual(json.loads((r.walk / "credit-probe.json").read_text()), {"confirmed_credits_per_getblock": 1})
        self.assertEqual(r.alerts(), [])
        self.assertIn("TEST MODE", r.out)
        self.assertIn("2026-10-16T03", r.progress()["note"])
        self.assertIn(str(3 * 18000), r.progress()["note"])
        # every path the stubs saw is under the temp root: nothing under /data/mal
        self.assertNotIn("/data/mal", "".join(json.dumps(c["argv"]) for c in r.calls()))

    def test_second_run_walks_nothing_already_verified(self) -> None:
        self.run_w()
        (self.root / "calls.jsonl").unlink()
        r = self.run_w()
        self.assertEqual(r.rc, 0, r.err)
        self.assertEqual(r.calls(), [])

    def test_nothing_before_five_minutes_past(self) -> None:
        r = self.run_w(now="2026-10-16T03:02:00Z")
        self.assertEqual(r.rc, 0, r.err)
        self.assertEqual(r.calls(), [])

    def test_helius_env_is_never_printed_or_written(self) -> None:
        r = self.run_w(cfg={"walker": {H2: [1, 3]}}, passes=2)
        self.assertEqual(r.rc, 3)
        self.assertNotIn(SENTINEL, r.every_text())
        self.assertNotIn("HELIUS_API_KEY", r.out + r.err)

    def test_lock_slots_are_taken_in_order_4_3_2_1(self) -> None:
        r = self.run_w(now="2026-10-16T01:30:00Z")
        self.assertIn("holding helius slot 4", r.out)
        locks = self.root / "locks"
        held = open(locks / "helius-4.lock", "w")
        self.addCleanup(held.close)
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        r = self.run_w(now="2026-10-16T01:30:00Z")
        self.assertIn("holding helius slot 3", r.out)


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
        # the slot is released: the lock file can be taken again
        with open(r.root / "locks" / "helius-4.lock", "w") as fh:
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
        # the file sets the key empty: sourcing overrides whatever the job environment carried
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
        # bash prints the offending line of a file it cannot parse, and that line is the key
        r = self.run_w(helius_env=f"HELIUS_API_KEY={SENTINEL}(\n")
        self.assert_refused_for_env(r)

    def test_a_good_key_still_walks(self) -> None:
        r = self.run_w(helius_env=f"HELIUS_API_KEY={SENTINEL}\n")
        self.assertEqual(r.rc, 0, r.err)
        self.assertEqual(r.hours("walker"), [H1, H2, H3])
        self.assertEqual(r.alerts(), [])

    def test_a_key_with_surrounding_quotes_and_export_walks(self) -> None:
        r = self.run_w(helius_env=f'export HELIUS_API_KEY="{SENTINEL}"\n')
        self.assertEqual(r.rc, 0, r.err)
        self.assertTrue(all(c["has_key"] for c in r.calls("walker")))

    def test_the_guard_sits_before_the_walk_loop_and_releases_the_lock(self) -> None:
        text = SCRIPT.read_text()
        guard = text.index("helius_env_refuse()")
        self.assertLess(text.index('. "$HELIUS_ENV"'), text.index("while :; do"))
        self.assertLess(guard, text.index("while :; do"))
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
        r = self.run_w(now="2026-10-16T01:30:00Z")
        self.assertEqual(r.rc, 0, r.err)
        self.assertIn("TEST MODE", r.out)

    def test_the_check_is_the_first_thing_after_the_bash_check(self) -> None:
        text = SCRIPT.read_text()
        self.assertLess(text.index("MISCUSI_JOB_ID"), text.index("COUNT_START=2026"))
        self.assertLess(text.index("MISCUSI_JOB_ID"), text.index('mkdir -p "$D"'))


class FailureTests(WrapperCase):
    def test_exit_3_is_fatal_alerts_and_stops_the_loop(self) -> None:
        r = self.run_w(cfg={"walker": {H2: [3]}})
        self.assertEqual(r.rc, 3, r.err)
        self.assertEqual(r.hours("walker"), [H1, H2], "the hour after a refused hour must not be walked")
        self.assertEqual(r.hours("verify"), [H1], "a refused hour is not verified")
        self.assertIn("ALERT", r.out)
        alerts = r.alerts()
        self.assertEqual(len(alerts), 1)
        self.assertEqual((alerts[0]["kind"], alerts[0]["hour"], alerts[0]["rc"]), ("walker_refused", H2, 3))
        prog = r.progress()
        self.assertTrue(prog["note"].startswith("ALERT walker_refused " + H2), prog)
        self.assertEqual(prog["kind"], "walker_refused")
        self.assertEqual(prog["hour"], H2)
        self.assertIn("pct", prog)

    def test_resubmit_meets_the_same_refusal_and_counts_its_credits(self) -> None:
        cfg = {"walker": {H2: [3]}}
        self.run_w(cfg=cfg)
        r = self.run_w(cfg=cfg)
        self.assertEqual(r.rc, 3)
        self.assertEqual(r.hours("walker"), [H1, H2, H2], "H1 is verified and skipped; H3 is never reached")
        self.assertEqual(r.calls("walker")[-1]["argv"][r.calls("walker")[-1]["argv"].index("--credit-cap") + 1], str(CAP_TOTAL - 700))
        self.assertEqual(len(r.alerts()), 2)

    def test_exit_1_is_a_walk_error_and_the_job_goes_on(self) -> None:
        r = self.run_w(cfg={"walker": {H2: [1]}})
        self.assertEqual(r.rc, 0, r.err)
        self.assertEqual(r.hours("walker"), [H1, H2, H3])
        self.assertIn(f"walk error {H2} rc=1 consecutive=1/3", r.out)
        self.assertIn(f"verify not OK {H2}", r.out)
        self.assertEqual(r.hours("verify"), [H1, H2, H3])
        self.assertEqual(r.alerts(), [])

    def test_three_consecutive_failures_on_one_hour_stop_the_job(self) -> None:
        r = self.run_w(cfg={"walker": {H2: [1, 1, 1, 1]}}, passes=5)
        self.assertEqual(r.rc, 4, r.err)
        self.assertEqual(r.hours("walker").count(H2), 3)
        self.assertEqual(r.hours("walker").count(H1), 1)
        self.assertEqual(r.hours("walker").count(H3), 1)
        self.assertIn(f"walk error {H2} rc=1 consecutive=3/3", r.out)
        self.assertIn("ALERT", r.out)
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

    def test_verify_not_ok_does_not_stop_later_hours(self) -> None:
        r = self.run_w(cfg={"verify_bad": [H2]})
        self.assertEqual(r.rc, 0, r.err)
        self.assertEqual(r.hours("walker"), [H1, H2, H3])
        self.assertIn(f"verify not OK {H2}", r.out)
        self.assertEqual(r.alerts(), [])


class CreditTests(WrapperCase):
    def seed(self, checkpoint: int | None = None, refusals: list[str] | None = None) -> None:
        walk = self.root / "walk2"
        walk.mkdir(parents=True, exist_ok=True)
        if checkpoint is not None:
            (walk / "checkpoint.json").write_text(json.dumps({"credits_used": checkpoint}))
        if refusals is not None:
            (walk / "refusals.jsonl").write_text("\n".join(refusals) + "\n")

    def cap_args(self, r: Result) -> set[str]:
        return {c["argv"][c["argv"].index("--credit-cap") + 1] for c in r.calls("walker")}

    def test_cap_arithmetic(self) -> None:
        self.assertEqual(CAP_TOTAL, 11_825_000)
        self.assertIn("11,825,000", SCRIPT.read_text())

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
        # checkpoint 11,800,000 + refused 10,000 leaves 15,000 under 11,825,000: below the 20,000 reserve.
        self.seed(checkpoint=11_800_000, refusals=[json.dumps({"credits_spent_in_hour_before_refusal": 10_000})])
        r = self.run_w()
        self.assertEqual(r.rc, 5, r.err)
        self.assertEqual(r.calls(), [])
        (a,) = r.alerts()
        self.assertEqual((a["kind"], a["hour"]), ("credit_cap", H1))
        self.assertIn("ALERT", r.out)
        self.assertTrue(r.progress()["note"].startswith("ALERT credit_cap"))

    def test_walks_while_the_reserve_is_left(self) -> None:
        self.seed(checkpoint=11_000_000, refusals=[json.dumps({"credits_spent_in_hour_before_refusal": 10_000})])
        r = self.run_w()
        self.assertEqual(r.rc, 0, r.err)
        self.assertEqual(r.hours("walker"), [H1, H2, H3])

    def test_unreadable_checkpoint_is_fatal(self) -> None:
        walk = self.root / "walk2"
        walk.mkdir(parents=True)
        (walk / "checkpoint.json").write_text("{not json")
        r = self.run_w()
        self.assertEqual(r.rc, 5)
        self.assertEqual(r.calls("walker"), [])
        self.assertEqual(r.alerts()[0]["kind"], "credit_state")


class ScriptTextTests(unittest.TestCase):
    def setUp(self) -> None:
        self.text = SCRIPT.read_text()

    def test_bash_syntax(self) -> None:
        subprocess.run(["bash", "-n", str(SCRIPT)], check=True, stdin=subprocess.DEVNULL)

    def test_job_constants(self) -> None:
        self.assertIn("D=/data/mal/blocks/forward-1016;", self.text)
        self.assertIn("COUNT_START=2026-10-16T01\n", self.text)
        self.assertIn("/data/mal/locks", self.text)
        self.assertIn("/var/lib/mal/backfill/helius.env", self.text)

    def test_never_names_another_walk_dir_outside_comments(self) -> None:
        code = [x for x in self.text.splitlines() if not x.lstrip().startswith("#")]
        for line in code:
            self.assertNotIn("forward-1002", line)
            self.assertNotIn("blocks/forward-walk2", line)
        self.assertEqual(sum("forward-1016" in x for x in code), 1, "D= is the only line that names the walk dir")

    def test_no_trace_or_env_dump(self) -> None:
        code = "\n".join(x for x in self.text.splitlines() if not x.lstrip().startswith("#"))
        for bad in ("set -x", "set -o xtrace", "printenv", "env |", "cat \"$HELIUS_ENV\""):
            self.assertNotIn(bad, code)

    def test_walk_1_script_is_untouched_by_this_change(self) -> None:
        walk1 = (REPO / "scripts" / "research" / "forward-walk.sh").read_text()
        self.assertIn("forward-1002", walk1)
        self.assertNotIn("--event-v", walk1)
        self.assertNotIn("--strict-lines", walk1)


class RealToolContractTests(unittest.TestCase):
    """The flag names and exit code the wrapper depends on exist in the real tools."""

    def help_text(self, *args: str) -> str:
        env = {k: v for k, v in os.environ.items() if not k.startswith(("HELIUS", "MISCUSI_"))}
        env["PYTHONPATH"] = str(REPO)
        p = subprocess.run([sys.executable, *args, "--help"], cwd=REPO, env=env, capture_output=True, text=True,
                           timeout=60, stdin=subprocess.DEVNULL)
        self.assertEqual(p.returncode, 0, p.stderr)
        return p.stdout

    def test_walker_has_event_v_and_the_flags_the_wrapper_passes(self) -> None:
        h = self.help_text("-m", "tools.pump_history_backfill")
        for flag in ("--event-v", "--until", "--hours", "--out", "--credits-file", "--credit-cap", "--max-bytes",
                     "--rps", "--lookup-rps", "--workers"):
            self.assertIn(flag, h)

    def test_verify_has_strict_lines(self) -> None:
        h = self.help_text("-m", "tools.exp012_forward", "verify")
        for flag in ("--walk-dir", "--hour", "--strict-lines"):
            self.assertIn(flag, h)

    def test_refusal_exit_is_3_and_verified_hours_takes_strict(self) -> None:
        sys.path.insert(0, str(REPO))
        try:
            import tools.exp012_forward as ef
            import tools.pump_history_backfill as bf
        finally:
            sys.path.pop(0)
        self.assertEqual(bf.REFUSAL_EXIT, 3)
        self.assertEqual(bf.REFUSALS_NAME, "refusals.jsonl")
        params = list(inspect.signature(ef.verified_hours).parameters)
        self.assertEqual(params[:2], ["walk_dir", "strict_bad_lines"])


if __name__ == "__main__":
    unittest.main()
