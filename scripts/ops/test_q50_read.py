"""Fixture tests for scripts/ops/q50-read.sh. SYNTHETIC trees only (scripts/ops/q50_read_fixture_build.py); no real Q50 ledger, shadow dir, applied log or out dir is opened.
Every run is under dash in test mode (Q50R_TEST=1) except the production-refusal case, which must stop at the host check before any variable is used.

Run (mal-research-0):  /data/mal/venv/bin/python -m pytest -q scripts/ops/test_q50_read.py
It makes a `git clone --shared` of the main checkout in a temp dir (the job checkout stand-in; the script adds its a31d9864 worktree there), never touching the main checkout.
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "q50-read.sh"
BUILDER = HERE / "q50_read_fixture_build.py"
MAIN_REPO = os.environ.get("Q50_TEST_MAIN_REPO", "/home/claude/.miscusi/repos/vaanai/MAL")
PIN = "a31d9864829d1f0645fef8be095eb8df575c2d00"
PY = os.environ.get("Q50_TEST_PY", "/data/mal/venv/bin/python")
SCRATCH = os.environ.get("Q50_TEST_SCRATCH", "/data2/mal/scratch")
Q50 = "h5v1q50-boostfloor-a1"
B = 1_793_318_400_000  # 2026-10-30T00:00Z, the fixture's Am.11 instant
END = 1_793_923_200_000
H = 3_600_000
ALLOWED_PREFIXES = ("TEST MODE", "JOB ", "PINS ", "BOOKS ", "INSTANT ", "A3 ", "READ ", "VERDICT ", "FLAG ", "SIDE ", "MULTIPLICITY ", "CHECK ", "DISCLOSURE ", "LEGS ", "INBAND ",
                    "POSTREAD ", "output ", "REFUSED", "NO READ RECORDED", "WOULD ", "READY ", "SETUP ", "books copy ", "params file ", "shadow dirs ")
FIGURE_WORDS = ("mean", "ci90", "CI90lo", "total ", "ex-top3", "ex-best-day", "n_counted", "attempts ", "counted ")
CLONE: Path | None = None


def setUpModule():
    global CLONE
    d = Path(tempfile.mkdtemp(prefix="q50-read-test-clone-", dir=SCRATCH))
    subprocess.run(["git", "clone", "-q", "--shared", MAIN_REPO, str(d / "c")], check=True)
    subprocess.run(["git", "-C", str(d / "c"), "checkout", "-q", "--detach", "origin/main"], check=True)
    subprocess.run(["git", "-C", str(d / "c"), "worktree", "add", "-q", "--detach", str(d / "pin"), PIN], check=True)  # the builder imports the a31d9864 tests
    CLONE = d / "c"


def tearDownModule():
    if CLONE is not None:
        shutil.rmtree(CLONE.parent, ignore_errors=True)


def iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def applied_lines(until: datetime, *, skip=(), verdicts=None) -> str:
    """The applied log as tools/a3_halt.py writes it: the first official run 10-24T00:57:40Z, then 07:10:05Z and 19:10:05Z daily up to `until`."""
    t = [datetime(2026, 10, 24, 0, 57, 40, tzinfo=timezone.utc)]
    d = datetime(2026, 10, 24, 7, 10, 5, tzinfo=timezone.utc)
    while d <= until:
        t.append(d)
        d += timedelta(hours=12)
    out = []
    for x in t:
        s = x.strftime("%Y-%m-%dT%H:%M:%SZ")
        if s in skip:
            continue
        out.append(json.dumps({"applied_ms": int(x.timestamp() * 1000) + 60_000, "ended": {}, "run_utc": s, "verdict": (verdicts or {}).get(s, "NONE")}, sort_keys=True))
    return "".join(x + "\n" for x in out)


class Base(unittest.TestCase):
    kind = "pass"

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="q50-read-fx-", dir=SCRATCH)).resolve()
        self.out = self.root / "out"
        self.addCleanup(self._cleanup)
        pin = CLONE.parent / "pin"
        r = subprocess.run([PY, str(BUILDER), str(self.root), self.kind], cwd=pin, env=dict(os.environ, PYTHONPATH=str(pin)), capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        self.arena, self.out, self.sm = self.root / "arena", self.root / "out", self.root / "sm"
        self.applied = self.sm / "a3-applied.jsonl"
        self.applied.write_text(applied_lines(datetime(2026, 10, 30, 7, 10, 5, tzinfo=timezone.utc)))
        self.instant = self.arena / "ledger" / Q50 / "q50-read-instant.json"

    def _cleanup(self):
        for p in self.root.rglob("*"):
            try:
                p.chmod(p.stat().st_mode | stat.S_IWUSR)
            except OSError:
                pass
        for wt in self.out.glob("wt-a31d9864.*"):
            subprocess.run(["git", "-C", str(CLONE), "worktree", "remove", "--force", str(wt)], capture_output=True)
        shutil.rmtree(self.root, ignore_errors=True)

    def env(self, now_ms: int = B + 7 * H + 30 * 60_000, **kw) -> dict:
        e = {k: v for k, v in os.environ.items() if not k.startswith(("Q50T_", "Q50R_"))}
        e.update(Q50R_TEST="1", Q50T_NOW_MS=str(now_ms), Q50T_ARENA=str(self.arena), Q50T_APPLIED=str(self.applied), Q50T_OUT=str(self.out), Q50T_PY=PY)
        e.update({k: str(v) for k, v in kw.items()})
        return e

    def run_script(self, *args: str, env: dict | None = None, shell: str = "dash") -> tuple[int, str]:
        r = subprocess.run([shell, str(SCRIPT), *args], cwd=CLONE, env=env if env is not None else self.env(), capture_output=True, text=True, timeout=600)
        return r.returncode, r.stdout + r.stderr

    def outputs(self) -> list[str]:
        return sorted(p.name for p in self.out.glob("q50-*") if p.name != "q50-read-params.txt")

    def assert_status_only(self, log: str):
        for ln in log.splitlines():
            self.assertTrue(ln.startswith(ALLOWED_PREFIXES), f"unexpected log line: {ln}")
            if not ln.startswith(("DISCLOSURE ", "REFUSED", "BOOKS ")):
                for w in FIGURE_WORDS:
                    self.assertNotIn(w, ln, f"figure word {w!r} in the job log: {ln}")

    def set_params(self, text: str):
        p = self.out / "q50-read-params.txt"
        p.write_text(text)


class Syntax(unittest.TestCase):
    def test_dash_and_sh_parse(self):
        for sh in ("dash", "sh"):
            self.assertEqual(subprocess.run([sh, "-n", str(SCRIPT)]).returncode, 0, sh)

    def test_test_variables_are_read_only_inside_the_test_block(self):
        lines = SCRIPT.read_text().splitlines()
        a = next(i for i, x in enumerate(lines) if x.startswith("# >>> TEST MODE"))
        b = next(i for i, x in enumerate(lines) if x.startswith("# <<< TEST MODE"))
        for i, x in enumerate(lines):
            if ("Q50T_" in x or "Q50R_TEST" in x) and not (a < i < b):
                self.assertTrue(x.lstrip().startswith("#") or "grep -q -E '^(Q50T_|Q50R_TEST$)'" in x, f"line {i + 1} reads a test variable outside the test block: {x}")

    def test_usage(self):
        r = subprocess.run(["dash", str(SCRIPT), "--bogus"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)
        self.assertIn("REFUSED: usage", r.stdout)


class Pass(Base):
    def test_end_to_end_then_second_run_refused(self):
        rc, log = self.run_script()
        self.assertEqual(rc, 0, log)
        self.assertIn(f"VERDICT bot={Q50} STATUS PASS", log)
        self.assertIn("INSTANT status=READ boundary=2026-10-30T00:00:00Z", log)
        self.assertIn("A3 guard (DEC-031 Am.4 item 1.6 (a)): run_utc=2026-10-30T07:10:05Z verdict=NONE", log)
        self.assertIn("SIDE h5v1-boostfloor-a2 REFUSED_NO_RECORDED_INSTANT", log)
        self.assertIn("LEGS tool_exit=0 worst_in_slot_measured=True end_consistency_ok=True dec032_measured=True", log)
        self.assertIn("INBAND VERDICT PASS", log)
        self.assertIn("CHECK the stdout STATUS line equals the JSON status", log)
        self.assertIn("The date-cluster figure is not computed by the registered read and decides nothing.", log)
        self.assert_status_only(log)
        names = self.outputs()
        self.assertEqual(names, sorted(["q50-read.out", "q50-read.err", "q50-read.json", "q50-legs.out", "q50-legs.err", "q50-legs.json", "q50-inband.out", "q50-inband.err",
                                        "q50-inband.json"]))
        for n in names:
            self.assertFalse((self.out / n).stat().st_mode & 0o222, f"{n} is writable")
        rep = json.loads((self.out / "q50-read.json").read_text())
        self.assertEqual(rep["books_file"]["sha256"], "819c699d74a2fab8b8c67916a5732e54950d158c514d8fa24dabc9b65b7665c0")
        self.assertEqual(rep["books"][Q50]["read_instant_ms"], B)
        self.assertEqual(rep["multiplicity"]["N"], 40)
        self.assertTrue(json.loads((self.out / "q50-inband.json").read_text().splitlines()[0])["band"]["quote_source"] == "shadow-wsol")
        self.assertEqual(list(self.out.glob("wt-a31d9864*")), [], "the run's worktree is removed at exit")
        rc, log2 = self.run_script()
        self.assertEqual(rc, 2)
        self.assertIn("exists: the one read of", log2)
        self.assertEqual(self.outputs(), names)

    def test_dash_and_bash_as_sh_give_the_same_verdict(self):
        rc, log = self.run_script(shell="sh")
        self.assertEqual(rc, 0, log)
        self.assertIn("STATUS PASS", log)

    def test_wait_only_then_read(self):
        rc, log = self.run_script("--wait-only")
        self.assertEqual(rc, 0, log)
        self.assertIn("READY ", log)
        self.assertEqual(self.outputs(), [])
        rc, log = self.run_script()
        self.assertEqual(rc, 0, log)

    def test_check_mode_opens_nothing_and_writes_no_output(self):
        rc, log = self.run_script("--check")
        self.assertEqual(rc, 0, log)
        self.assertIn("INSTANT READ B=1793318400000", log)
        self.assertIn("A3 newest applied run_utc=2026-10-30T07:10:05Z verdict=NONE; since the Q50 registration: 14 lines, 0 not NONE/PASS", log)
        self.assertIn("CHECK OK", log)
        self.assertEqual(self.outputs(), [])

    def test_instant_absent_would_wait_and_deadline_refuses(self):
        self.instant.unlink()
        rc, log = self.run_script()
        self.assertEqual(rc, 4, log)
        self.assertIn("WOULD WAIT for the Amendment 11 record", log)
        rc, log = self.run_script(env=self.env(now_ms=END + 3 * H))
        self.assertEqual(rc, 2, log)
        self.assertIn("no Amendment 11 record by 2026-11-06T03:00:00Z", log)
        self.assertEqual(self.outputs(), [])

    def test_instant_written_during_the_wait(self):
        body = self.instant.read_bytes()
        self.instant.unlink()

        def later():
            time.sleep(3)
            self.instant.write_bytes(body)
        th = threading.Thread(target=later)
        th.start()
        rc, log = self.run_script(env=self.env(Q50T_POLLS=20))
        th.join()
        self.assertEqual(rc, 0, log)
        self.assertIn("STATUS PASS", log)

    def test_unparseable_instant_refuses_after_three_polls(self):
        self.instant.unlink()
        self.instant.write_text("{")
        rc, log = self.run_script(env=self.env(Q50T_POLLS=5))
        self.assertEqual(rc, 2, log)
        self.assertIn("stays unparseable", log)

    def test_instant_record_not_am11_refuses(self):
        doc = json.loads(self.instant.read_text())
        self.instant.unlink()
        self.instant.write_text(json.dumps(dict(doc, rule="something else")))
        rc, log = self.run_script()
        self.assertEqual(rc, 2, log)
        self.assertIn("not an Amendment 11 record", log)
        self.instant.unlink()
        self.instant.write_text(json.dumps(dict(doc, read_instant_ms=B + 1, boundary_ms=B + 1)))
        rc, log = self.run_script()
        self.assertEqual(rc, 2, log)
        self.assertIn("not a 00:00Z boundary", log)

    def test_a3_guard_missing_waits_then_refuses(self):
        self.applied.write_text(applied_lines(datetime(2026, 10, 29, 19, 10, 5, tzinfo=timezone.utc)))
        rc, log = self.run_script()
        self.assertEqual(rc, 4, log)
        self.assertIn("WOULD WAIT for an applied A3 decision with run_utc >= 2026-10-30T00:00:00Z", log)
        rc, log = self.run_script(env=self.env(now_ms=B + 48 * H))
        self.assertEqual(rc, 2, log)
        self.assertIn("no applied A3 decision with run_utc >= 2026-10-30T00:00:00Z", log)
        self.assertEqual(self.outputs(), [])

    def test_a3_hard_verdict_since_registration_refuses(self):
        for v in ("HALT", "FAIL", "ERROR"):
            self.applied.write_text(applied_lines(datetime(2026, 10, 30, 7, 10, 5, tzinfo=timezone.utc), verdicts={"2026-10-27T19:10:05Z": v}))
            rc, log = self.run_script()
            self.assertEqual(rc, 2, log)
            self.assertIn(f"an applied verdict {v} since the Q50 registration", log)
        self.assertEqual(self.outputs(), [])

    def test_a3_verdict_before_registration_is_ignored_and_pass_lines_counted(self):
        pre = json.dumps({"run_utc": "2026-10-22T19:10:05Z", "verdict": "HALT", "applied_ms": 1, "ended": {}}) + "\n"
        self.applied.write_text(pre + applied_lines(datetime(2026, 10, 30, 7, 10, 5, tzinfo=timezone.utc), verdicts={"2026-10-28T07:10:05Z": "PASS"}))
        rc, log = self.run_script("--wait-only")
        self.assertEqual(rc, 0, log)
        self.assertIn("pass_lines=1", log)

    def test_a3_gap_over_48h_refuses(self):
        skip = {f"2026-10-{d}T{h}:10:05Z" for d in (26, 27) for h in ("07", "19")} | {"2026-10-28T07:10:05Z"}
        self.applied.write_text(applied_lines(datetime(2026, 10, 30, 7, 10, 5, tzinfo=timezone.utc), skip=skip))
        rc, log = self.run_script()
        self.assertEqual(rc, 2, log)
        self.assertIn("the dead-man may have ended the book", log)

    def test_a3_torn_last_line_waits_and_malformed_middle_line_refuses(self):
        good = applied_lines(datetime(2026, 10, 30, 7, 10, 5, tzinfo=timezone.utc))
        self.applied.write_text(good + '{"run_utc": "2026-10-30T19:1')
        rc, log = self.run_script()
        self.assertEqual(rc, 4, log)
        self.assertIn("WOULD WAIT", log)
        ls = good.splitlines(keepends=True)
        self.applied.write_text("".join(ls[:3]) + "not json\n" + "".join(ls[3:]))
        rc, log = self.run_script()
        self.assertEqual(rc, 2, log)
        self.assertIn("is not a {run_utc, verdict} line", log)

    def test_score_window_is_avoided(self):
        self.applied.write_text(applied_lines(datetime(2026, 10, 30, 19, 10, 5, tzinfo=timezone.utc)))
        rc, log = self.run_script(env=self.env(now_ms=B + 24 * H + 30 * 60_000))
        self.assertEqual(rc, 4, log)
        self.assertIn("WOULD SLEEP until 01:00Z", log)
        self.assertEqual(self.outputs(), [])

    def test_production_branch_refuses_off_host_and_ignores_overrides(self):
        e = self.env()
        e.pop("Q50R_TEST")
        rc, log = self.run_script(env=e)
        self.assertEqual(rc, 2, log)
        self.assertIn("production runs only on mal-fast-0", log)
        self.assertNotIn("TEST MODE", log)
        self.assertEqual(self.outputs(), [])
        self.assertEqual(list(self.out.glob("wt-a31d9864*")), [])

    def test_fence_refuses_real_paths(self):
        for kw in ({"Q50T_LEDGER": "/data/mal/arena/ledger"}, {"Q50T_ARENA": "/data/mal/arena"}):
            rc, log = self.run_script(env=self.env(**kw))
            self.assertEqual(rc, 2, log)
            self.assertTrue("must not touch real paths" in log or "test path missing" in log, log)
        self.assertEqual(self.outputs(), [])

    def test_worktree_edited_during_the_wait_refuses_and_worktrees_are_removed(self):
        body = self.instant.read_bytes()
        self.instant.unlink()

        def later():  # while the run waits for the instant: edit a tool in its worktree, then let the record appear
            for _ in range(100):
                wts = list(self.out.glob("wt-a31d9864.*"))
                if wts:
                    break
                time.sleep(0.2)
            time.sleep(1)
            f = wts[0] / "tools" / "arena_stack.py"
            f.write_text(f.read_text() + "\n# edited\n")
            self.instant.write_bytes(body)
        th = threading.Thread(target=later)
        th.start()
        rc, log = self.run_script(env=self.env(Q50T_POLLS=30))
        th.join()
        self.assertEqual(rc, 2, log)
        self.assertIn("has local changes", log)
        self.assertEqual(self.outputs(), [])
        self.assertEqual(list(self.out.glob("wt-a31d9864.*")), [])

    def test_bad_params_refuse_before_the_read(self):
        sd = f"SHADOW_DIR={self.arena / 'h5-q50-2026-10-24'}\n"
        cases = {"N_BOOKS=0\nC=1\n" + sd: "N_BOOKS", "N_BOOKS=5\nC=1\nFOO=1\n" + sd: "unknown key", "N_BOOKS=5\nC=1\nPRIOR_READ=h5v1q50-boostfloor-a1=FAIL\n" + sd: "cannot name",
                 "N_BOOKS=5\nC=1\nOTHER_P=x=1.5\n" + sd: "OTHER_P", "N_BOOKS=2\nC=1\nPRIOR_READ=a=FAIL\nPRIOR_READ=b=FAIL\n" + sd: "smaller than the books read",
                 "N_BOOKS=5\nC=1\nSHADOW_DIR=/tmp\n": "SHADOW_DIR must be", "N_BOOKS=5\nC=1\n": "at least one SHADOW_DIR",
                 "N_BOOKS=5\nC=1\nPRIOR_READ=a=FAIL (n < 100)\n" + sd: "PRIOR_READ must be"}
        for text, want in cases.items():
            self.set_params(text)
            rc, log = self.run_script()
            self.assertEqual(rc, 2, (text, log))
            self.assertIn(want, log, text)
        self.assertEqual(self.outputs(), [])

    def test_books_copy_wrong_or_writable_refuses(self):
        bc = self.out / "a3-books-v2-copy.json"
        bc.chmod(0o644)
        rc, log = self.run_script()
        self.assertEqual(rc, 2, log)
        self.assertIn("is writable", log)
        bc.write_text('{"legacy_bot_ids": [], "new_rule_bot_ids": []}\n')
        bc.chmod(0o444)
        rc, log = self.run_script()
        self.assertEqual(rc, 2, log)
        self.assertIn("missing or not sha256", log)

    def test_a2_read_instant_present_refuses(self):
        d = self.arena / "ledger" / "h5v1-boostfloor-a2"
        d.mkdir(parents=True)
        (d / "read-instant.json").write_text("{}")
        rc, log = self.run_script()
        self.assertEqual(rc, 2, log)
        self.assertIn("has a read-instant.json", log)
        self.assertEqual(self.outputs(), [])

    def test_extra_shadow_dir_skips_the_postread_then_postread_runs(self):
        succ = self.arena / "h5-q50-2026-10-31-succ"
        succ.mkdir()
        rc, log = self.run_script()
        self.assertEqual(rc, 5, log)
        self.assertIn("STATUS PASS", log)
        self.assertIn("POSTREAD SKIPPED", log)
        self.assertEqual(self.outputs(), ["q50-read.err", "q50-read.json", "q50-read.out"])
        p = self.out / "q50-read-params.txt"
        p.write_text(p.read_text() + f"SHADOW_DIR={succ}\n")
        rc, log = self.run_script("--postread")
        self.assertEqual(rc, 0, log)
        self.assertIn("INBAND VERDICT PASS", log)
        self.assert_status_only(log)
        rc, log = self.run_script("--postread")
        self.assertEqual(rc, 2, log)
        self.assertIn("the post-read already ran", log)

    def test_setup_copies_v2_only(self):
        self.out = self.root / "out2"
        self.out.mkdir()
        rc, log = self.run_script("--setup")
        self.assertEqual(rc, 0, log)
        bc = self.out / "a3-books-v2-copy.json"
        self.assertEqual(oct(bc.stat().st_mode & 0o777), "0o444")
        self.assertIn("books copy written", log)
        rc, log = self.run_script("--setup")
        self.assertIn("books copy exists", log)
        bc.chmod(0o644)
        bc.unlink()
        (self.arena / "a3-books.json").write_text('{"legacy_bot_ids": [], "new_rule_bot_ids": []}\n')
        rc, log = self.run_script("--setup")
        self.assertEqual(rc, 2, log)
        self.assertIn("not v2 any more", log)


class Fail(Base):
    kind = "fail"

    def test_fail_records_no_postread(self):
        rc, log = self.run_script()
        self.assertEqual(rc, 0, log)
        self.assertRegex(log, rf"VERDICT bot={Q50} STATUS FAIL")
        self.assertIn("LEGS and INBAND not run", log)
        self.assertEqual(self.outputs(), ["q50-read.err", "q50-read.json", "q50-read.out"])
        self.assert_status_only(log)


class Ended(Base):
    kind = "ended"

    def test_ended_is_not_decidable_after_the_end_seal_guard(self):
        rc, log = self.run_script(env=self.env(now_ms=END + 2 * H))
        self.assertEqual(rc, 4, log)
        self.assertIn("WOULD WAIT for an applied A3 decision with run_utc >= 2026-11-06T00:00:00Z", log)
        self.applied.write_text(applied_lines(datetime(2026, 11, 6, 7, 10, 5, tzinfo=timezone.utc)))
        rc, log = self.run_script(env=self.env(now_ms=END + 8 * H))
        self.assertEqual(rc, 0, log)
        self.assertIn("INSTANT status=ENDED boundary=2026-11-06T00:00:00Z", log)
        self.assertIn(f"VERDICT bot={Q50} STATUS NOT_DECIDABLE", log)
        self.assertIn("FLAG ENDED NOT_DECIDABLE at the end-seal", log)
        self.assertIn("LEGS and INBAND not run", log)
        self.assert_status_only(log)


class Mismatch(Base):
    kind = "mismatch"

    def test_tool_refusal_records_nothing_and_masks_digits(self):
        rc, log = self.run_script()
        self.assertEqual(rc, 2, log)
        self.assertIn("REFUSED by the tool", log)
        self.assertIn("NO READ RECORDED", log)
        ref = [x for x in log.splitlines() if x.startswith("REFUSED by the tool")][0]
        self.assertFalse(any(ch.isdigit() for ch in ref.split(":", 1)[1].replace("q50", "").replace("h5v1", "")), ref)
        self.assertNotIn("VERDICT", log)


if __name__ == "__main__":
    unittest.main()
