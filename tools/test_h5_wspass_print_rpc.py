"""Tests for tools/h5_wspass_print_rpc.py (ws-pass trigger-print RPC check). Fake transport only: no network, no real directory.

  python3 -m unittest tools.test_h5_wspass_print_rpc
"""
from __future__ import annotations

import builtins
import contextlib
import io
import json
import os
import stat
import socket
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

from tools import h5_tipauth_census as C
from tools import h5_tipauth_item4 as I4
from tools import h5_tipauth_trigger_print as TP
from tools import h5_wspass_print_rpc as W
from tools.test_h5_tipauth_item4 import H, MID, T0, start, trig, write

SINCE = T0
UNTIL = T0 + 6 * H  # a scheduled instant (+6 h)
NOW = UNTIL + 10 * 60_000
SHA = "5db78a663a0617527daacb22f5ce6c1a7e92dfa3"
SLOT_FOUND = 987654321
T_ASK = 10**12


def sg(name):
    return "Sig" + name + "x" * 20


def ws(name, pool=None, **kw):
    return trig(t=MID, pool=pool or "Pool" + name, ws=0, uval=0, signature=sg(name), **kw)


def refused(name, pool=None, **kw):
    return trig(t=MID, pool=pool or "Pool" + name, boost_src="none", signature=sg(name), **kw)


def would(name, pool=None, **kw):
    return trig(t=MID, pool=pool or "Pool" + name, signature=sg(name), **kw)


class Fake:
    def __init__(self, found=(), nulls=(), err=()):
        self.found, self.err, self.calls = set(found), set(err), []

    def __call__(self, url, payload, timeout):
        assert url == C.RPC_URL
        assert payload["method"] == "getTransaction"
        o = payload["params"][1]
        assert o["commitment"] == "finalized" and o["maxSupportedTransactionVersion"] == 0
        sig = payload["params"][0]
        self.calls.append(sig)
        if sig in self.err:
            raise TimeoutError("t")
        return {"result": {"slot": SLOT_FOUND} if sig in self.found else None}


class Clock:
    def __init__(self, t=T_ASK):
        self.t = t

    def __call__(self):
        return self.t


def recording_hook(seen: set):
    class R(dict):
        def get(self, k, d=None):
            seen.add(k)
            return dict.get(self, k, d)

        def __getitem__(self, k):
            seen.add(k)
            return dict.__getitem__(self, k)

        def __contains__(self, k):
            seen.add(k)
            return dict.__contains__(self, k)

    return lambda pairs: R(pairs)


class NetworkAttempted(BaseException):
    """A BaseException, so that the census `except Exception` cannot swallow it: a real network call fails the test."""


def _no_network(*a, **k):
    raise NetworkAttempted("a real network call was attempted")


class Base(unittest.TestCase):
    def setUp(self):
        for target, name in ((urllib.request.OpenerDirector, "open"), (socket.socket, "connect"), (socket, "create_connection"), (socket, "getaddrinfo")):
            p = mock.patch.object(target, name, _no_network)
            p.start()
            self.addCleanup(p.stop)
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.tmp = Path(self._td.name)
        self.sd = self.tmp / "shadow"
        self.sd.mkdir()
        self.out = self.tmp / "out"

    def shadow(self, recs, sha=SHA):
        write(self.sd, [start(T0 + 1000, sha=sha)] + list(recs))

    def plan(self, since=SINCE, until=UNTIL, sha=SHA, now=NOW, out=None, hook=None, ta=(1, 0, 0)):
        argv = ["plan", "--shadow-dir", str(self.sd), "--since-ms", str(since), "--until-ms", str(until), "--expect-code-sha", sha,
                "--out-dir", str(out or self.out), "--tape-absent-ws", str(ta[0]), "--tape-absent-refused", str(ta[1]), "--tape-absent-would-pass", str(ta[2])]
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = W.main(argv, now_ms=now, object_hook=hook)
        return code, json.loads(buf.getvalue())

    def run_cmd(self, cmd, transport=None, clock=None):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            clk = clock or Clock()
            with mock.patch.object(W.time, "time", lambda: clk.t / 1000):
                code = W.main([cmd, "--out-dir", str(self.out)], transport=transport or Fake(), sleep=lambda s: None)
        return code, json.loads(buf.getvalue())

    def report(self, fresh=True):
        """Run `report`. The report file is write-once; a test that reports twice on one out-dir removes the file first (fresh=True)."""
        if fresh and (self.out / W.REPORT_NAME).exists():
            (self.out / W.REPORT_NAME).unlink()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = W.main(["report", "--out-dir", str(self.out)])
        self.assertEqual(code, 0)
        return json.loads(buf.getvalue()), buf.getvalue()

    def state_rows(self):
        return C.read_jsonl(self.out / C.STATE_NAME)

    def asks(self, found=(), err=(), reask_err=(), rounds=3, transport=None):
        """ask, then `rounds` re-ask rounds 600,000 ms apart (a round with nothing to re-ask writes no row). Signatures in `err` answer an error on every
        call; those in `reask_err` answer an error on the re-asks only. `report` refuses before the 3 rounds while anything is pending or in error."""
        self.run_cmd("ask", transport or Fake(found=found, err=err), Clock(T_ASK))
        for i in range(1, rounds + 1):
            self.run_cmd("reask", transport or Fake(found=found, err=list(err) + list(reask_err)), Clock(T_ASK + i * 600_000))

    def full_plan(self, ws_names=("a1", "a2"), ref_names=("r1",), wp_names=(), ta=(1, 0, 0)):
        recs = [ws(n) for n in ws_names] + [refused(n) for n in ref_names] + [would(n) for n in wp_names]
        self.shadow(recs)
        code, _ = self.plan(ta=ta)
        self.assertEqual(code, 0)


class TestSelection(Base):
    def test_all_ws_pass_and_refused_pv_prints_selected_would_pass_own_class(self):
        self.shadow([ws("a1"), ws("a2"), refused("r1"), refused("r2"), would("w1"),
                     ws("fv", variant="fv"), ws("sealed", sealed=True), ws("sealed2", h5_look2_sealed=True),
                     {**ws("late"), "t_ms": UNTIL + 1}, {**ws("early"), "t_ms": T0 + 500}])
        code, res = self.plan()
        self.assertEqual(code, 0)
        self.assertEqual(res["counts"], {"ws_pass": 2, "would_pass": 1, "refused": 2})
        plan = json.loads((self.out / C.PLAN_NAME).read_text())
        m = plan["wspass"]
        self.assertEqual(m["class_sigs"], {"ws_pass": sorted([sg("a1"), sg("a2")]), "would_pass": [sg("w1")], "refused": sorted([sg("r1"), sg("r2")])})
        self.assertEqual(plan["call_cap"], 5)
        self.assertEqual(m["tool_sha256"], W.tool_sha())
        self.assertEqual((m["since_ms"], m["until_ms"], m["expect_code_sha"]), (SINCE, UNTIL, SHA))
        self.assertEqual(len(m["plan_selection_sha256"]), 64)

    def test_window_bounds_inclusive(self):
        self.shadow([{**ws("edge"), "t_ms": UNTIL}, {**ws("over"), "t_ms": UNTIL + 1}, {**ws("pre"), "t_ms": T0 + 999}])
        code, res = self.plan()
        self.assertEqual((code, res["counts"]["ws_pass"]), (0, 1))
        self.assertEqual(json.loads((self.out / C.PLAN_NAME).read_text())["wspass"]["class_sigs"]["ws_pass"], [sg("edge")])

    def test_sealed_skipped_before_any_key_is_read_and_keys_stay_allowed(self):
        seen: set = set()
        self.shadow([ws("a1"), ws("sealed", sealed=True)])
        code, _ = self.plan(hook=recording_hook(seen))
        self.assertEqual(code, 0)
        self.assertTrue(seen <= TP.ALLOWED_KEYS, sorted(seen - TP.ALLOWED_KEYS))
        for banned in ("pnl_sol", "q_trigger_sol", "exit_ladder_trigger_slots"):
            self.assertNotIn(banned, seen)
        # a run holding only a sealed trigger never reads its signature or classification keys
        seen2: set = set()
        d2 = self.tmp / "shadow2"
        d2.mkdir()
        write(d2, [start(T0 + 1000, sha=SHA), ws("sealed", sealed=True)])
        sel = W.select(d2, SINCE, UNTIL, SHA, recording_hook(seen2))
        self.assertEqual(sel["skipped_sealed_trigger"], 1)
        self.assertNotIn("signature", seen2)
        self.assertNotIn("variant", seen2)
        self.assertNotIn(I4.WS_FIELD, seen2)

    def test_expect_code_sha_mismatch_refused(self):
        self.shadow([ws("a1")], sha="a" * 40)
        code, res = self.plan()
        self.assertEqual(code, 6)
        self.assertFalse((self.out / C.PLAN_NAME).exists())

    def test_no_start_record(self):
        write(self.sd, [ws("a1")])
        code, _ = self.plan()
        self.assertEqual(code, 2)

    def test_off_schedule_until_refused(self):
        self.shadow([ws("a1")])
        for until in (T0 + 7 * H, T0 + 6 * H + 1, T0 + 12 * H):
            code, res = self.plan(until=until, now=until + 10**7)
            self.assertEqual(code, 4, until)
        self.assertFalse(self.out.exists())

    def test_until_not_yet_settled_refused(self):
        self.shadow([ws("a1")])
        code, _ = self.plan(now=UNTIL + 119_999)
        self.assertEqual(code, 4)
        code, _ = self.plan(now=UNTIL + 120_000)
        self.assertEqual(code, 0)

    def test_second_plan_into_same_out_dir_refused(self):
        self.full_plan()
        before = (self.out / C.PLAN_NAME).read_bytes()
        self.shadow([ws("zz")])
        code, res = self.plan()
        self.assertEqual(code, 5)
        self.assertEqual((self.out / C.PLAN_NAME).read_bytes(), before)

    def test_signature_in_two_classes_asked_once(self):
        self.shadow([{**refused("dup"), "signature": sg("dup")}, {**ws("dup"), "pool": "PoolOther"}])
        code, res = self.plan()
        self.assertEqual(res["counts"], {"ws_pass": 1, "would_pass": 0, "refused": 0})
        m = json.loads((self.out / C.PLAN_NAME).read_text())["wspass"]
        self.assertEqual(m["cross_class_duplicates"], 1)

    def test_missing_signature_is_unaskable_and_blocks_reading(self):
        self.shadow([ws("a1"), {**ws("a2"), "signature": None}])
        self.plan()
        self.run_cmd("ask", Fake(found=[sg("a1")]))
        rep, _ = self.report()
        self.assertEqual(rep["classes"]["ws_pass"]["unaskable"], 1)
        self.assertEqual(rep["reading"], W.READING_NONE)

    def test_plan_files_private(self):
        self.full_plan()
        for name in (C.PLAN_NAME, C.STATE_NAME, C.LOG_NAME, W.SIDE_NAME):
            self.assertEqual(stat.S_IMODE(os.stat(self.out / name).st_mode), 0o600, name)


class TestAskAndReport(Base):
    def ask(self, found=(), err=(), t=T_ASK):
        return self.run_cmd("ask", Fake(found=found, err=err), Clock(t))

    def test_ask_uses_census_transport_contract_and_stores_minimal_fields(self):
        self.full_plan(wp_names=("w1",))
        fake = Fake(found=[sg("a1")])
        code, res = self.run_cmd("ask", fake)
        self.assertEqual((code, res["calls"]), (0, 4))
        self.assertEqual(sorted(fake.calls), sorted([sg("a1"), sg("a2"), sg("r1"), sg("w1")]))
        for r in self.state_rows():
            self.assertTrue(set(r) <= {"asked_ms", "sig", "round", "result", "slot", "err"}, r)
        for name in (C.PLAN_NAME, C.STATE_NAME, C.LOG_NAME):
            self.assertEqual(stat.S_IMODE(os.stat(self.out / name).st_mode), 0o600, name)

    def test_ask_twice_refused_by_census(self):
        self.full_plan()
        self.ask()
        with self.assertRaises(SystemExit):
            self.ask()

    def test_reask_needs_600000_ms(self):
        self.full_plan(ws_names=("a1",), ref_names=())
        self.ask()
        with self.assertRaises(SystemExit):
            self.run_cmd("reask", Fake(), Clock(T_ASK + 599_999))
        code, _ = self.run_cmd("reask", Fake(), Clock(T_ASK + 600_000))
        self.assertEqual(code, 0)
        rep, _ = self.report()
        self.assertEqual(rep["classes"]["ws_pass"]["not_returned"], 1)

    def test_not_returned_needs_two_nulls_600000_apart(self):
        self.full_plan(ws_names=("a1",), ref_names=())
        for gap, want in ((599_999, "pending"), (600_000, "not_returned")):
            # two nulls `gap` apart, then errors in the 2nd and 3rd re-ask round (so that the 3 rounds ran and `report` is allowed)
            rows = [{"asked_ms": T_ASK, "sig": sg("a1"), "round": "ask", "result": "null"},
                    {"asked_ms": T_ASK + gap, "sig": sg("a1"), "round": "reask1", "result": "null"},
                    {"asked_ms": T_ASK + gap + 1, "sig": sg("a1"), "round": "reask2", "result": "error", "err": "TimeoutError"},
                    {"asked_ms": T_ASK + gap + 2, "sig": sg("a1"), "round": "reask3", "result": "error", "err": "TimeoutError"}]
            (self.out / C.STATE_NAME).write_text("".join(json.dumps(r) + "\n" for r in rows))
            rep, _ = self.report()
            self.assertEqual(rep["classes"]["ws_pass"][want], 1, gap)
            self.assertEqual(rep["classes"]["ws_pass"]["returned"], 0)

    def test_error_never_becomes_not_returned(self):
        self.full_plan(ws_names=("a1",), ref_names=())
        self.asks(err=[sg("a1")])
        rep, _ = self.report()
        d = rep["classes"]["ws_pass"]
        self.assertEqual((d["error"], d["not_returned"], d["returned"]), (1, 0, 0))
        self.assertEqual(rep["reading"], W.READING_NONE)
        # one null plus errors in the 3 re-ask rounds is pending, still not not_returned
        rows = [{"asked_ms": T_ASK, "sig": sg("a1"), "round": "ask", "result": "null"}] + [
            {"asked_ms": T_ASK + i * 10**7, "sig": sg("a1"), "round": f"reask{i}", "result": "error", "err": "TimeoutError"} for i in (1, 2, 3)]
        (self.out / C.STATE_NAME).write_text("".join(json.dumps(r) + "\n" for r in rows))
        rep, _ = self.report()
        self.assertEqual((rep["classes"]["ws_pass"]["not_returned"], rep["classes"]["ws_pass"]["pending"]), (0, 1))

    def test_report_has_no_signature_pool_or_slot(self):
        self.full_plan(wp_names=("w1",))
        self.asks(found=[sg("a1"), sg("r1")])
        rep, text = self.report()
        for n in ("a1", "a2", "r1", "w1"):
            self.assertNotIn(sg(n), text)
            self.assertNotIn("Pool" + n, text)
        self.assertNotIn(str(SLOT_FOUND), text)
        self.assertNotIn('"slot"', text)
        self.assertNotIn('"pool"', text)
        self.assertEqual(rep["tool_sha256"], W.tool_sha())
        self.assertEqual(len(rep["plan_sha256"]), 64)
        self.assertTrue(rep["interim"])
        # stdout of plan and ask carries none either
        buf = io.StringIO()
        out2 = self.tmp / "out2"
        self.shadow([ws("a1")])
        with contextlib.redirect_stdout(buf):
            W.main(["plan", "--shadow-dir", str(self.sd), "--since-ms", str(SINCE), "--until-ms", str(UNTIL), "--expect-code-sha", SHA,
                    "--out-dir", str(out2), "--tape-absent-ws", "1", "--tape-absent-refused", "0", "--tape-absent-would-pass", "0"], now_ms=NOW)
        self.assertNotIn(sg("a1"), buf.getvalue())

    def test_counts_per_class(self):
        self.full_plan(ws_names=("a1", "a2", "a3"), ref_names=("r1", "r2"), wp_names=("w1",))
        self.asks(found=[sg("a1"), sg("r1"), sg("w1")])
        rep, _ = self.report()
        self.assertEqual({k: rep["classes"][k]["asked"] for k in ("ws_pass", "refused", "would_pass")}, {"ws_pass": 3, "refused": 2, "would_pass": 1})
        self.assertEqual((rep["classes"]["ws_pass"]["returned"], rep["classes"]["ws_pass"]["not_returned"], rep["classes"]["ws_pass"]["pending"]), (1, 2, 0))

    def test_unasked_signatures_are_pending(self):
        self.full_plan()
        # r1 answered; its rows also name the 3 re-ask rounds, so `report` is allowed; the two ws-pass signatures have no row at all
        rows = [{"asked_ms": T_ASK, "sig": sg("r1"), "round": r, "result": "found", "slot": SLOT_FOUND} for r in ("ask", "reask1", "reask2", "reask3")]
        (self.out / C.STATE_NAME).write_text("".join(json.dumps(x) + "\n" for x in rows))
        rep, _ = self.report()
        self.assertEqual(rep["classes"]["ws_pass"], {"asked": 0, "returned": 0, "not_returned": 0, "error": 0, "pending": 2, "planned": 2, "unaskable": 0})
        self.assertEqual(rep["reading"], W.READING_NONE)

    def two_rounds(self, found, ws_names=("a1", "a2"), ref_names=("r1",), wp_names=(), ta=(1, 0, 0)):
        self.full_plan(ws_names, ref_names, wp_names, ta)
        self.ask(found=found)
        self.run_cmd("reask", Fake(found=found), Clock(T_ASK + 600_000))

    def test_reading_all_returned(self):
        self.two_rounds([sg("a1"), sg("a2"), sg("r1")])
        rep, _ = self.report()
        self.assertEqual(rep["reading"], "all 2 were returned at finalized commitment; the missing row is on the tape's side; it is not shown that the print's reserves were right")

    def test_reading_one_ws_not_returned_others_returned(self):
        self.two_rounds([sg("a1"), sg("r1")])
        rep, _ = self.report()
        self.assertEqual(rep["classes"]["ws_pass"]["not_returned"], 1)
        self.assertEqual(rep["reading"], "consistent with a live-feed print for a transaction not in a finalized block; not proof; the count does not name which print")

    def test_reading_no_conclusion_cases(self):
        # every ws_pass returned and a refused print not returned: the rule's first row only asks for no error or pending
        self.two_rounds([sg("a1")], ws_names=("a1",))
        self.assertEqual(self.report()[0]["reading"], W.READING_ALL.format(n=1))
        # a ws_pass not returned AND a refused print not returned: neither row holds
        self.tearDown()
        self.setUp()
        self.two_rounds([sg("a2")])
        self.assertEqual(self.report()[0]["reading"], "no conclusion")
        # 0 returned / 2 not_returned, refused returned: (b) needs exactly 1 not_returned and at least 1 returned (quant-proof, PR #619)
        self.tearDown()
        self.setUp()
        self.two_rounds([sg("r1")])
        self.assertEqual(self.report()[0]["reading"], "no conclusion")
        # a would-pass print pending (never asked twice) blocks both readings
        self.tearDown()
        self.setUp()
        self.full_plan(wp_names=("w1",))
        self.asks(found=[sg("a1"), sg("a2"), sg("r1")], reask_err=[sg("w1")])  # w1: one null, then errors in the 3 re-ask rounds
        rep = self.report()[0]
        self.assertEqual((rep["classes"]["would_pass"]["pending"], rep["reading"]), (1, "no conclusion"))
        # no ws_pass at all
        self.tearDown()
        self.setUp()
        self.two_rounds([sg("r1")], ws_names=(), ref_names=("r1",), ta=(0, 0, 0))
        rep = self.report()[0]
        self.assertEqual(rep["reading"], "no conclusion")
        self.assertIn("ws_pass_tape_absent_is_0", rep["failed_checks"])

    def test_reading_b_only_on_the_declared_split(self):
        # quant-proof's table on PR #619: every row below is "no conclusion"
        five = ("a1", "a2", "a3", "a4", "a5")
        cases = [
            (dict(found=[sg("a1"), sg("a2"), sg("a3"), sg("r1")], ws_names=five), "3 returned / 2 not_returned, refused returned"),
            (dict(found=[sg("r1")], ws_names=five), "0 / 5, refused returned"),
            (dict(found=[], ws_names=("a1",), ref_names=()), "0 / 1, no refused planned, nothing returned at all"),
            (dict(found=[sg("a1"), sg("a2"), sg("a3"), sg("a4"), sg("r1")], ws_names=five, wp_names=("w1",)), "4 / 1, refused returned, a would_pass not_returned"),
            (dict(found=[sg("a1"), sg("a2"), sg("a3"), sg("a4")], ws_names=five, ref_names=()), "4 / 1 with no refused planned"),
        ]
        for kw, label in cases:
            with self.subTest(label):
                self.tearDown()
                self.setUp()
                self.two_rounds(**kw)
                self.assertEqual(self.report()[0]["reading"], "no conclusion")
        # the declared row itself: 4 returned and 1 not_returned, refused 1 returned
        self.tearDown()
        self.setUp()
        self.two_rounds([sg("a1"), sg("a2"), sg("a3"), sg("a4"), sg("r1")], ws_names=five)
        rep = self.report()[0]
        self.assertEqual((rep["classes"]["ws_pass"]["returned"], rep["classes"]["ws_pass"]["not_returned"]), (4, 1))
        self.assertEqual(rep["reading"], W.READING_ABSENT)
        self.assertEqual(rep["state_torn_lines_n"], 0)

    def test_torn_state_line_gives_no_conclusion(self):
        # every answer returned, one torn line in the state file
        self.two_rounds([sg("a1"), sg("a2"), sg("r1")])
        with open(self.out / C.STATE_NAME, "a", encoding="utf-8") as fh:
            fh.write('{"sig": "' + sg("a1") + '", "result": "fou\n')
        rep = self.report()[0]
        self.assertEqual(rep["state_torn_lines_n"], 1)
        self.assertEqual(rep["reading"], "no conclusion")

    def test_null_torn_null_is_not_a_conclusion(self):
        # a null, a torn line that may have been a `found`, then a null 1,200,000 ms after the first
        self.full_plan()
        self.ask(found=[sg("a1"), sg("r1")])
        with open(self.out / C.STATE_NAME, "a", encoding="utf-8") as fh:
            fh.write('{"sig": "' + sg("a2") + '", "result": "fou\n')
        self.run_cmd("reask", Fake(found=[sg("a1"), sg("r1")]), Clock(T_ASK + 1_200_000))
        rep = self.report()[0]
        self.assertEqual(rep["state_torn_lines_n"], 1)
        self.assertEqual(rep["classes"]["ws_pass"]["not_returned"], 1)
        self.assertEqual(rep["reading"], "no conclusion")

    def test_reading_sentences_are_in_the_docstring_word_for_word(self):
        for s in (W.READING_ALL, W.READING_ABSENT):
            self.assertIn(s, W.__doc__)
        self.assertIn('"no conclusion"', W.__doc__)
        self.assertIn("/usr/bin/python3 -m tools.h5_wspass_print_rpc", W.__doc__)
        self.assertIn("WITHOUT `-I`", W.__doc__)


class TestNoTapeAccess(Base):
    def test_source_has_no_tip_path_and_no_tip_dir_argument(self):
        src = Path(W.__file__).read_text(encoding="utf-8")
        code_only = "\n".join(line for line in src.split('"""')[2:])  # after the module docstring
        for needle in ("/var/lib/mal/sealed", "fast-trades-tip", "trades-", "--tip-dir", "tip_dir", "mmap", "ledger", "item8", "--out FILE"):
            self.assertNotIn(needle, code_only, needle)
        self.assertNotIn("import mmap", src)

    def test_runtime_opens_only_shadow_dir_out_dir_and_own_source(self):
        opened: list[str] = []
        real_open, real_io_open, real_os_open = builtins.open, io.open, os.open

        def rec(p):
            if not isinstance(p, int):  # an fd comes from os.open, which is recorded by path
                opened.append(str(Path(p).resolve()))

        def fake_open(p, *a, **k):
            rec(p)
            return real_open(p, *a, **k)

        def fake_io_open(p, *a, **k):
            rec(p)
            return real_io_open(p, *a, **k)

        def fake_os_open(p, *a, **k):
            rec(p)
            return real_os_open(p, *a, **k)

        self.shadow([ws("a1"), refused("r1")])
        with mock.patch.object(builtins, "open", fake_open), mock.patch.object(io, "open", fake_io_open), mock.patch.object(os, "open", fake_os_open):
            self.assertEqual(self.plan()[0], 0)
            self.run_cmd("ask", Fake(found=[sg("a1"), sg("r1")]))
            self.report()
        allowed = (str(self.sd.resolve()), str(self.out.resolve()), str(Path(W.__file__).resolve()))
        self.assertTrue(opened)
        for p in opened:
            self.assertTrue(any(p == a or p.startswith(a + os.sep) for a in allowed), p)



# ---- the rule of PR #610 comment 6156017843 section 1: more than one print without a tape row --------------------------------------------------
NONE = "no conclusion"
S0_15 = "all 15 were returned at finalized commitment; the 2 missing rows are on the tape's side; it is not shown that the prints' reserves were right"
S1_15 = ("1 of 15 was not returned at finalized commitment: consistent with a live-feed print for a transaction not in a finalized block; not proof; the count does "
         "not name which print and does not show that it is one of the 2 without a tape row. At least 1 of the 2 without a tape row was returned, so at least 1 "
         "missing row is on the tape's side")
S2_15 = ("2 of 15 were not returned at finalized commitment, as many as have no tape row: consistent with 2 live-feed prints for transactions not in a finalized "
         "block; not proof; the count does not name which prints and does not show that they are the 2 without a tape row")
EXTRA_3 = "3 of the 20 refused prints were not returned (3 of the 20 have no tape row); the cause is not determined."
EXTRA_K = "K of the 20 refused prints were not returned (3 of the 20 have no tape row); the cause is not determined."
OLD_ALL5 = "all 5 were returned at finalized commitment; the missing row is on the tape's side; it is not shown that the print's reserves were right"
OLD_ABSENT = "consistent with a live-feed print for a transaction not in a finalized block; not proof; the count does not name which print"
DISCLOSURE = ("this rule is written after this read's tape counts (2 of 15, 3 of 20) and the +6 h RPC split (2 returned, 3 error) were known, and before any RPC "
              "answer for this read")
CANARY = "H5 is an execution measurement; EXP-024 ended NOT_DECIDABLE on 2026-10-13 with no look read; not a book. H5 has not cleared the promotion gate."
UNTIL36 = T0 + 36 * H
ws_ = ws  # `ws` is also a keyword name of `Rule.scenario`


class Tick:
    """A clock that advances by 1 ms per call, so that every asked_ms is distinct."""

    def __init__(self, t=T_ASK):
        self.t = t

    def __call__(self):
        self.t += 1
        return self.t


class Script:
    """A transport with a script per signature: ('resp', dict) is returned, ('raise', exc) is raised, anything else answers null (or found)."""

    def __init__(self, script=None, found=()):
        self.script, self.found, self.calls = script or {}, set(found), []

    def __call__(self, url, payload, timeout):
        sig = payload["params"][0]
        self.calls.append(sig)
        kind, val = self.script.get(sig, ("null", None))
        if kind == "raise":
            raise val
        if kind == "resp":
            return val
        return {"result": {"slot": SLOT_FOUND} if sig in self.found else None}


class Rule(Base):
    def scenario(self, ws=(15, 15), ref=(20, 20), wp=(1, 1), ta=(2, 3, 0), err=(), pend=(), extra=(), until=UNTIL, since=SINCE, now=NOW):
        """(planned, returned) per class; the others answer null twice, 600,000 ms apart. `err` names raise a timeout on every call; `pend` names answer
        null on the ask and an error on the re-asks (one null: pending). The ask is followed by the 3 re-ask rounds, so that `report` is allowed."""
        wsn, rfn, wpn = [f"a{i}" for i in range(ws[0])], [f"r{i}" for i in range(ref[0])], [f"p{i}" for i in range(wp[0])]
        self.shadow([ws_(n) for n in wsn] + [refused(n) for n in rfn] + [would(n) for n in wpn] + list(extra))
        code, _ = self.plan(ta=ta, until=until, since=since, now=now)
        self.assertEqual(code, 0)
        found = [sg(n) for n in wsn[:ws[1]] + rfn[:ref[1]] + wpn[:wp[1]]]
        self.run_cmd("ask", Fake(found=found, err=[sg(n) for n in err]), Clock(T_ASK))
        for i in (1, 2, 3):
            self.run_cmd("reask", Fake(found=found, err=[sg(n) for n in list(err) + list(pend)]), Clock(T_ASK + i * 600_000))
        return self.report()[0]

    def again(self):
        self.tearDown()
        self.setUp()

    def check(self, rep, reading, extra=(), failed=()):
        self.assertEqual(rep["reading"], reading)
        self.assertEqual(rep["reading_extra"], list(extra))
        for f in failed:
            self.assertIn(f, rep["failed_checks"])
        if reading != NONE:
            self.assertEqual(rep["failed_checks"], [])
        else:
            self.assertTrue(rep["failed_checks"])


class TestRuleSentences(Rule):
    def test_s0_every_ws_pass_returned(self):
        self.check(self.scenario(ref=(20, 20)), S0_15)
        self.again()
        self.check(self.scenario(ref=(20, 17)), S0_15, [EXTRA_3])

    def test_s0_holds_whatever_refused_and_would_pass_not_returned(self):
        rep = self.scenario(ref=(20, 12))  # 8 refused not_returned, above the 3 without a tape row
        self.check(rep, S0_15, ["8 of the 20 refused prints were not returned (3 of the 20 have no tape row); the cause is not determined."])
        self.again()
        rep = self.scenario(wp=(1, 0))  # a would-pass print not returned: S0 still, no extra sentence for that class
        self.check(rep, S0_15)
        self.assertEqual(rep["classes"]["would_pass"]["not_returned"], 1)

    def test_s1_14_returned_1_not_returned(self):
        self.check(self.scenario(ws=(15, 14), ref=(20, 20)), S1_15)
        self.again()
        self.check(self.scenario(ws=(15, 14), ref=(20, 17)), S1_15, [EXTRA_3])
        self.again()
        self.check(self.scenario(ws=(15, 14), ref=(20, 18)), S1_15, ["2 of the 20 refused prints were not returned (3 of the 20 have no tape row); the cause is not determined."])

    def test_s2_13_returned_2_not_returned(self):
        self.check(self.scenario(ws=(15, 13), ref=(20, 20)), S2_15)
        self.again()
        self.check(self.scenario(ws=(15, 13), ref=(20, 17)), S2_15, [EXTRA_3])

    def test_n_above_a_in_each_class_is_no_conclusion(self):
        ab = "_not_returned_above_tape_absent"
        cases = [
            (dict(ws=(15, 12)), "ws_pass" + ab),  # 3 not returned, A is 2
            (dict(ws=(15, 0)), "ws_pass" + ab),
            (dict(ws=(15, 14), ref=(20, 16)), "refused" + ab),  # the S1 row, 4 refused not returned, A is 3
            (dict(ws=(15, 13), ref=(20, 16)), "refused" + ab),  # the S2 row
            (dict(ws=(15, 14), wp=(1, 0)), "would_pass" + ab),  # 1 would-pass not returned, A is 0
            (dict(ws=(15, 13), wp=(1, 0)), "would_pass" + ab),
        ]
        for kw, name in cases:
            with self.subTest(kw=kw):
                self.again()
                rep = self.scenario(**kw)
                self.check(rep, NONE, failed=[name])
                self.assertEqual(rep["reading_extra"], [])

    def test_s1_s2_need_a_ws_pass_and_a_refused_print_returned(self):
        self.check(self.scenario(ws=(2, 0), ref=(20, 20), ta=(2, 3, 0)), NONE, failed=["no_ws_pass_print_returned"])
        self.again()
        self.check(self.scenario(ws=(15, 14), ref=(3, 0), ta=(2, 3, 0)), NONE, failed=["no_refused_print_returned"])  # N_ref 3 is not above A_ref 3
        self.again()
        self.check(self.scenario(ws=(15, 14), ref=(0, 0), ta=(2, 0, 0)), NONE, failed=["no_refused_print_returned"])
        self.again()
        # S0 needs no control
        self.check(self.scenario(ws=(15, 15), ref=(3, 0), ta=(2, 3, 0)), S0_15,
                   ["3 of the 3 refused prints were not returned (3 of the 3 have no tape row); the cause is not determined."])

    def test_error_is_no_conclusion_in_each_class_and_never_a_not_returned(self):
        for names, name in ((["a0"], "ws_pass_error_above_0"), (["r0"], "refused_error_above_0"), (["p0"], "would_pass_error_above_0")):
            with self.subTest(name):
                self.again()
                rep = self.scenario(err=names)
                self.check(rep, NONE, failed=[name])
                self.assertEqual(sum(rep["classes"][c]["not_returned"] for c in rep["classes"]), 0)

    def test_pending_is_no_conclusion_even_with_every_ws_pass_returned(self):
        rep = self.scenario(ws=(15, 14), pend=["a14"])  # one null on the ask, errors on the 3 re-asks: pending
        self.check(rep, NONE, failed=["ws_pass_pending_above_0"])
        self.assertEqual(rep["classes"]["ws_pass"]["pending"], 1)
        self.again()
        self.check(self.scenario(ws=(15, 15), ref=(20, 19), pend=["r19"]), NONE, failed=["refused_pending_above_0"])

    def test_unaskable_and_torn_are_no_conclusion(self):
        rep = self.scenario(ws=(15, 15), extra=[{**ws_("u0"), "signature": None}])
        self.check(rep, NONE, failed=["ws_pass_unaskable_above_0", "ws_pass_counts_differ_from_triggers_n"])
        self.again()
        self.scenario(ws=(15, 15))
        with open(self.out / C.STATE_NAME, "a", encoding="utf-8") as fh:
            fh.write('{"sig": "x", "result": "fou\n')
        self.check(self.report()[0], NONE, failed=["state_torn_lines_above_0"])

    def test_other_preconditions(self):
        # a state row for a signature outside the plan
        self.scenario(ws=(15, 15))
        C.append_jsonl(self.out / C.STATE_NAME, {"asked_ms": T_ASK, "sig": sg("zz"), "round": "ask", "result": "null"})
        self.check(self.report()[0], NONE, failed=["state_rows_outside_the_plan"])
        # the plan's tool sha is not this tool's
        self.again()
        self.scenario(ws=(15, 15))
        pl = json.loads((self.out / C.PLAN_NAME).read_text())
        pl["wspass"]["tool_sha256"] = "0" * 64
        (self.out / C.PLAN_NAME).write_text(C.dumps(pl))
        self.check(self.report()[0], NONE, failed=["tool_sha256_differs_from_plan_tool_sha256"])
        # a signature in two classes
        self.again()
        rep = self.scenario(ws=(15, 15), extra=[{**refused("a0"), "signature": sg("a0"), "pool": "PoolOther"}])
        self.check(rep, NONE, failed=["cross_class_duplicates_above_0"])
        # two triggers with one signature: counts differ from triggers_n
        self.again()
        rep = self.scenario(ws=(15, 15), extra=[{**ws_("a0"), "pool": "PoolTwin"}])
        self.check(rep, NONE, failed=["ws_pass_counts_differ_from_triggers_n"])
        # a tape-absent count of 0 for the ws-pass class: nothing to read
        self.again()
        self.check(self.scenario(ta=(0, 3, 0)), NONE, failed=["ws_pass_tape_absent_is_0"])

    def test_generalisation_to_other_counts(self):
        a3 = (3, 0, 0)
        want = {
            5: "all 5 were returned at finalized commitment; the 3 missing rows are on the tape's side; it is not shown that the prints' reserves were right",
            4: ("1 of 5 was not returned at finalized commitment: consistent with a live-feed print for a transaction not in a finalized block; not proof; the count "
                "does not name which print and does not show that it is one of the 3 without a tape row. At least 2 of the 3 without a tape row were returned, so at "
                "least 2 missing rows are on the tape's side"),
            3: ("2 of 5 were not returned at finalized commitment: consistent with 2 live-feed prints for transactions not in a finalized block; not proof; the count "
                "does not name which prints and does not show that they are among the 3 without a tape row. At least 1 of the 3 without a tape row was returned, so "
                "at least 1 missing row is on the tape's side"),
            2: ("3 of 5 were not returned at finalized commitment, as many as have no tape row: consistent with 3 live-feed prints for transactions not in a finalized "
                "block; not proof; the count does not name which prints and does not show that they are the 3 without a tape row"),
        }
        for ws_ret, sentence in want.items():
            with self.subTest(ws_ret=ws_ret):
                self.again()
                self.check(self.scenario(ws=(5, ws_ret), ref=(1, 1), wp=(0, 0), ta=a3), sentence)
        self.again()
        self.check(self.scenario(ws=(5, 1), ref=(1, 1), wp=(0, 0), ta=a3), NONE, failed=["ws_pass_not_returned_above_tape_absent"])  # 4 not returned, A is 3

    def test_pin_at_tape_absent_1_0_0_prints_the_two_6h_sentences_unchanged(self):
        a = (1, 0, 0)
        self.check(self.scenario(ws=(5, 5), ref=(1, 1), wp=(0, 0), ta=a), OLD_ALL5)
        self.again()
        self.check(self.scenario(ws=(5, 4), ref=(1, 1), wp=(0, 0), ta=a), OLD_ABSENT)
        # the +6 h table (PR #619 comment 6127897922): every other split is no conclusion
        for kw, label in (
            (dict(ws=(5, 3), ref=(1, 1)), "3 / 2"), (dict(ws=(5, 0), ref=(1, 1)), "0 / 5"), (dict(ws=(1, 0), ref=(0, 0)), "0 / 1, no refused"),
            (dict(ws=(5, 4), ref=(1, 1), wp=(1, 0)), "4 / 1 with a would-pass not returned"), (dict(ws=(5, 4), ref=(0, 0)), "4 / 1, no refused planned"),
            (dict(ws=(5, 4), ref=(1, 0)), "4 / 1, the refused print not returned"),
        ):
            with self.subTest(label):
                self.again()
                self.check(self.scenario(**{"wp": (0, 0), **kw, "ta": a}), NONE)
        # every ws-pass print returned and a refused print not returned: the sentence, and the fixed extra sentence
        self.again()
        self.check(self.scenario(ws=(5, 5), ref=(1, 0), wp=(0, 0), ta=a), OLD_ALL5,
                   ["1 of the 1 refused prints were not returned (0 of the 1 have no tape row); the cause is not determined."])


class TestPlanFlags(Rule):
    def test_flags_are_required_integers_in_range(self):
        self.shadow([ws_("a1"), ws_("a2"), refused("r1")])
        base = ["plan", "--shadow-dir", str(self.sd), "--since-ms", str(SINCE), "--until-ms", str(UNTIL), "--expect-code-sha", SHA, "--out-dir", str(self.out)]
        flags = ["--tape-absent-ws", "1", "--tape-absent-refused", "0", "--tape-absent-would-pass", "0"]
        for i in (0, 2, 4):  # each flag missing
            with self.subTest(missing=flags[i]), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as cm:
                    W.main(base + flags[:i] + flags[i + 2:], now_ms=NOW)
                self.assertEqual(cm.exception.code, 2)
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            W.main(base + ["--tape-absent-ws", "x", "--tape-absent-refused", "0", "--tape-absent-would-pass", "0"], now_ms=NOW)
        self.assertFalse(self.out.exists())
        # outside 0..triggers_n of the class (ws 2, refused 1, would-pass 0 triggers)
        for ta in ((3, 0, 0), (1, 2, 0), (1, 0, 1), (-1, 0, 0)):
            with self.subTest(ta=ta):
                code, res = self.plan(ta=ta)
                self.assertEqual(code, 7, ta)
                self.assertEqual(res["blocking_reasons"], ["tape_absent_out_of_range"])
                self.assertFalse(self.out.exists())
        for ta in ((0, 0, 0), (2, 1, 0)):  # the end points
            self.assertEqual(self.plan(ta=ta, out=self.tmp / f"o{ta[0]}")[0], 0)

    def test_stored_in_the_plan_and_inside_the_selection_hash(self):
        self.shadow([ws_("a1"), ws_("a2"), refused("r1"), refused("r2"), would("w1")])
        c1, r1 = self.plan(ta=(1, 0, 0), out=self.tmp / "o1")
        c2, r2 = self.plan(ta=(2, 0, 0), out=self.tmp / "o2")
        c3, r3 = self.plan(ta=(2, 1, 1), out=self.tmp / "o3")
        self.assertEqual((c1, c2, c3), (0, 0, 0))
        self.assertEqual(r1["tape_absent"], {"ws_pass": 1, "refused": 0, "would_pass": 0})
        self.assertEqual(r3["tape_absent"], {"ws_pass": 2, "refused": 1, "would_pass": 1})
        self.assertEqual(len({r1["plan_selection_sha256"], r2["plan_selection_sha256"], r3["plan_selection_sha256"]}), 3)
        m = json.loads((self.tmp / "o3" / C.PLAN_NAME).read_text())["wspass"]
        self.assertEqual(m["tape_absent"], {"ws_pass": 2, "refused": 1, "would_pass": 1})
        self.assertIn("tape_absent", W.HASH_KEYS)
        # editing a flag in the plan file is refused by `report`
        pl = json.loads((self.tmp / "o3" / C.PLAN_NAME).read_text())
        pl["wspass"]["tape_absent"]["ws_pass"] = 1
        (self.tmp / "o3" / C.PLAN_NAME).write_text(C.dumps(pl))
        with self.assertRaises(SystemExit):
            W.main(["report", "--out-dir", str(self.tmp / "o3")])

    def test_a_plan_whose_count_exceeds_triggers_n_is_refused_even_if_rehashed(self):
        self.full_plan()
        pl = json.loads((self.out / C.PLAN_NAME).read_text())
        pl["wspass"]["tape_absent"]["ws_pass"] = 3  # 2 ws-pass triggers
        pl["wspass"]["plan_selection_sha256"] = W.selection_hash(pl["wspass"])
        (self.out / C.PLAN_NAME).write_text(C.dumps(pl))
        with self.assertRaises(SystemExit) as cm:
            W.main(["report", "--out-dir", str(self.out)])
        self.assertIn("malformed", str(cm.exception))

    def test_flags_belong_to_plan_only(self):
        self.full_plan()
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            W.main(["report", "--out-dir", str(self.out), "--tape-absent-ws", "1"])


class TestErrorClassesAndCodes(Rule):
    def rows(self, spec):
        """spec: list of (name, asked_ms, result, err or None[, round])"""
        out = ""
        for name, t, res, err, *rnd in spec:
            r = {"asked_ms": t, "sig": sg(name), "round": rnd[0] if rnd else "ask", "result": res}
            if err is not None:
                r["err"] = err
            if res == "found":
                r["slot"] = SLOT_FOUND
            out += json.dumps(r) + "\n"
        (self.out / C.STATE_NAME).write_text(out)

    def test_error_classes_by_last_answer_and_error_answers_by_all(self):
        self.full_plan(ws_names=("a1", "a2", "a3", "a4", "a5"), ref_names=("r1", "r2"), wp_names=("w1",), ta=(1, 0, 0))
        long_name = "X" * 65
        self.rows([
            ("a1", 1, "error", "TimeoutError"), ("a1", 2, "error", "rpc_error", "reask1"),  # last answer rpc_error
            ("a2", 1, "error", "HTTPError"),                                              # one error
            ("a3", 1, "error", "malformed"), ("a3", 2, "found", None),                    # an error, then found: an error answer, not a signature in error
            ("a4", 1, "error", "bad name; with spaces"),                                  # not a printable name
            ("a5", 1, "error", None),                                                     # no err
            ("r1", 1, "error", long_name, "reask2"), ("r2", 1, "error", "malformed_result", "reask3"),  # (the rounds named here let `report` run)
            ("w1", 1, "null", None), ("w1", 2, "error", "URLError"),                      # one null then an error: pending, one error answer
        ])
        rep = self.report()[0]
        self.assertEqual(rep["error_classes"]["ws_pass"], {"HTTPError": 1, "other": 2, "rpc_error": 1})
        self.assertEqual(rep["error_answers"]["ws_pass"], {"HTTPError": 1, "TimeoutError": 1, "malformed": 1, "other": 2, "rpc_error": 1})
        self.assertEqual(rep["error_classes"]["refused"], {"malformed_result": 1, "other": 1})
        self.assertEqual(rep["error_answers"]["refused"], {"malformed_result": 1, "other": 1})
        self.assertEqual(rep["error_classes"]["would_pass"], {})
        self.assertEqual(rep["error_answers"]["would_pass"], {"URLError": 1})
        text = json.dumps(rep)
        for bad in ("bad name", long_name, "spaces"):
            self.assertNotIn(bad, text)
        self.assertEqual((rep["classes"]["ws_pass"]["error"], rep["classes"]["ws_pass"]["returned"]), (4, 1))  # a1, a2, a4, a5 in error; a3 found

    def test_err_name_rules(self):
        for v in ("rpc_error", "malformed", "malformed_result", "TimeoutError", "_x", "A" * 64):
            self.assertEqual(W.err_name(v), v)
        for v in ("", "1abc", "a b", "a-b", "A" * 65, None, 5, ["x"], "http://x/y", "message: boom"):
            self.assertEqual(W.err_name(v), "other")

    def test_no_exception_message_url_or_body_in_state_side_file_or_report(self):
        self.full_plan(ws_names=("a1", "a2", "a3"), ref_names=("r1",), ta=(1, 0, 0))
        secret = "SECRET-MESSAGE-xyz"
        script = {sg("a1"): ("raise", TimeoutError(secret)),
                  sg("a2"): ("resp", {"error": {"code": -32005, "message": secret, "data": {"x": secret}}}),
                  sg("a3"): ("raise", urllib.error.HTTPError("https://api.example/" + secret, 429, secret, {}, None))}
        self.asks(transport=Script(script, found=[sg("r1")]))
        rep, text = self.report()
        for name in (C.STATE_NAME, C.LOG_NAME, W.SIDE_NAME, W.REPORT_NAME):
            self.assertNotIn(secret, (self.out / name).read_text(), name)
        self.assertNotIn(secret, text)
        self.assertNotIn("api.example", text)
        self.assertEqual(rep["error_classes"]["ws_pass"], {"HTTPError": 1, "TimeoutError": 1, "rpc_error": 1})

    def test_side_file_integer_codes(self):
        self.full_plan(ws_names=("a1", "a2", "a3", "a4"), ref_names=("r1",), ta=(1, 0, 0))
        script = {sg("a1"): ("raise", urllib.error.HTTPError("u", 429, "m", {}, None)),
                  sg("a2"): ("resp", {"error": {"code": -32005, "message": "m", "data": 1}}),
                  sg("a3"): ("raise", TimeoutError("t")),
                  sg("a4"): ("resp", {"error": {"code": "oops"}})}  # a non-integer code is not stored as one
        self.assertEqual(W.cmd_ask(self.out, Script(script, found=[sg("r1")]), lambda s: None, Tick()), 5)
        side = C.read_jsonl(self.out / W.SIDE_NAME)
        self.assertEqual(stat.S_IMODE(os.stat(self.out / W.SIDE_NAME).st_mode), 0o600)
        for r in side:
            self.assertEqual(set(r), {"asked_ms", "sig", "http_status", "rpc_code"})
        by = {r["sig"]: (r["http_status"], r["rpc_code"]) for r in side}
        self.assertEqual(by, {sg("a1"): (429, None), sg("a2"): (None, -32005), sg("a3"): (None, None), sg("a4"): (None, None), sg("r1"): (None, None)})
        # asked_ms is the state row's asked_ms (the census clock reading taken just before the call)
        self.assertEqual({(r["asked_ms"], r["sig"]) for r in side}, {(r["asked_ms"], r["sig"]) for r in self.state_rows()})
        self.assertEqual(len({r["asked_ms"] for r in side}), 5)
        for i in (1, 2, 3):  # the 4 ws-pass signatures stay in error: re-asked in each of 3 rounds (`report` runs after the 3 rounds)
            self.assertEqual(W.cmd_reask(self.out, Script(script, found=[sg("r1")]), lambda s: None, Tick(T_ASK + i * 700_000)), 4)
        self.assertEqual({(r["asked_ms"], r["sig"]) for r in C.read_jsonl(self.out / W.SIDE_NAME)}, {(r["asked_ms"], r["sig"]) for r in self.state_rows()})
        rep = self.report()[0]
        w = rep["codes"]["ws_pass"]
        self.assertEqual(w["http_status"], {"429": 4, "null": 12})
        self.assertEqual(w["rpc_code"], {"-32005": 4, "null": 12})
        self.assertEqual(w["rows_n"], 16)
        self.assertEqual(rep["codes"]["refused"]["http_status"], {"null": 1})
        self.assertEqual(rep["side_file"], {"present": True, "rows_n": 17, "torn_lines_n": 0, "unknown_sig_rows_n": 0, "matches_state": True})

    def test_wrapper_returns_and_reraises_unchanged_and_survives_a_failed_append(self):
        resp = {"result": None}
        side = self.tmp / "side.jsonl"
        wrapped = W.side_transport(lambda u, p, t: resp, side, lambda: 7)
        payload = {"params": [sg("a1"), {}]}
        self.assertIs(wrapped("u", payload, 1), resp)
        exc = ValueError("boom")

        def boom(u, p, t):
            raise exc

        with self.assertRaises(ValueError) as cm:
            W.side_transport(boom, side, lambda: 7)("u", payload, 1)
        self.assertIs(cm.exception, exc)
        # an unwritable side path changes nothing the census sees
        bad = self.tmp / "no-such-dir" / "side.jsonl"
        self.assertIs(W.side_transport(lambda u, p, t: resp, bad, lambda: 7)("u", payload, 1), resp)
        with self.assertRaises(ValueError):
            W.side_transport(boom, bad, lambda: 7)("u", payload, 1)
        # a BaseException (a real network attempt in these tests) is not recorded and not swallowed
        with self.assertRaises(NetworkAttempted):
            W.side_transport(C.http_transport, side, lambda: 7)("https://x.invalid", payload, 1)
        rows = C.read_jsonl(side)
        self.assertEqual([(r["asked_ms"], r["http_status"], r["rpc_code"]) for r in rows], [(7, None, None), (7, None, None)])

    def test_side_file_mismatch_is_reported_and_changes_no_reading(self):
        self.full_plan(ws_names=("a1", "a2"), ref_names=("r1",), ta=(1, 0, 0))
        self.run_cmd("ask", Fake(found=[sg("a1"), sg("a2"), sg("r1")]), Clock())
        (self.out / W.SIDE_NAME).write_text("")
        rep = self.report()[0]
        self.assertFalse(rep["side_file"]["matches_state"])
        self.assertEqual(rep["reading"], W.READING_ALL.format(n=2))
        (self.out / W.SIDE_NAME).unlink()
        rep = self.report()[0]
        self.assertFalse(rep["side_file"]["present"])
        self.assertEqual(rep["reading"], W.READING_ALL.format(n=2))

    def test_the_network_guard_fails_a_test_that_reaches_a_real_transport(self):
        self.full_plan(ws_names=("a1",), ref_names=())
        with self.assertRaises(NetworkAttempted):
            W.main(["ask", "--out-dir", str(self.out)], sleep=lambda s: None)  # the default transport is C.http_transport
        self.assertEqual(self.state_rows(), [])
        with self.assertRaises(NetworkAttempted):
            C.http_transport("https://api.mainnet-beta.solana.com", {}, 1)
        with self.assertRaises(NetworkAttempted):
            socket.create_connection(("127.0.0.1", 9))


class TestWriteOnceReport(Rule):
    def test_report_is_written_once_by_exclusive_create_and_refuses_a_second_run(self):
        self.full_plan()
        self.run_cmd("ask", Fake(found=[sg("a1"), sg("a2"), sg("r1")]), Clock())
        rep, text = self.report()
        path = self.out / W.REPORT_NAME
        self.assertEqual(path.read_text(), text)
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        before = path.read_bytes()
        with self.assertRaises(SystemExit):
            W.main(["report", "--out-dir", str(self.out)])
        self.assertEqual(path.read_bytes(), before)

    def test_report_does_not_overwrite_a_file_created_in_between(self):
        self.full_plan()
        self.run_cmd("ask", Fake(found=[sg("a1"), sg("a2"), sg("r1")]), Clock())
        (self.out / W.REPORT_NAME).write_text("kept")
        with self.assertRaises(SystemExit):
            W.main(["report", "--out-dir", str(self.out)])
        self.assertEqual((self.out / W.REPORT_NAME).read_text(), "kept")

    def test_state_sha256_and_asked_span(self):
        self.full_plan(ws_names=("a1", "a2"), ref_names=("r1",))
        fk = Fake(found=[sg("a1")])
        W.cmd_ask(self.out, fk, lambda s: None, Tick(T_ASK))
        W.cmd_reask(self.out, fk, lambda s: None, Tick(T_ASK + 700_000))
        rep = self.report()[0]
        rows = self.state_rows()
        self.assertEqual(rep["state_sha256"], W.sha256_bytes((self.out / C.STATE_NAME).read_bytes()))
        self.assertEqual((rep["first_asked_ms"], rep["last_asked_ms"]), (min(r["asked_ms"] for r in rows), max(r["asked_ms"] for r in rows)))
        self.assertLess(rep["first_asked_ms"], T_ASK + 10)
        self.assertGreater(rep["last_asked_ms"], T_ASK + 700_000)
        self.assertEqual(rep["plan_sha256"], W.sha256_bytes((self.out / C.PLAN_NAME).read_bytes()))
        self.assertEqual(rep["tape_absent"], {"ws_pass": 1, "refused": 0, "would_pass": 0})
        self.assertEqual(rep["triggers_n"], {"ws_pass": 2, "refused": 1, "would_pass": 0})

    def test_empty_state_has_no_asked_span(self):
        self.shadow([])  # a plan with no trigger at all: nothing to ask, nothing pending
        self.assertEqual(self.plan(ta=(0, 0, 0))[0], 0)
        rep = self.report()[0]
        self.assertEqual((rep["first_asked_ms"], rep["last_asked_ms"]), (None, None))
        self.assertEqual(rep["reading"], NONE)

    def test_interim_label_and_fixed_note_at_36h(self):
        rep = self.scenario(until=UNTIL36, now=UNTIL36 + 10**7)
        self.assertFalse(rep["interim"])
        self.assertEqual(rep["interim_label"], "not the deciding read unless item 4's duration line is met at this until-ms")
        self.assertEqual(rep["interim_note"], "not the deciding read unless item 4's duration line is met at this until-ms")
        self.assertEqual(rep["reading"], S0_15)
        self.again()
        rep = self.scenario()
        self.assertTrue(rep["interim"])
        self.assertEqual((rep["interim_label"], rep["interim_note"]), ("interim, not the deciding read", W.INTERIM_NOTE))

    def test_operator_checks_and_note_are_listed_and_nothing_secret_is_printed(self):
        rep = self.scenario()
        self.assertEqual(rep["operator_checks"], W.OPERATOR_CHECKS)
        self.assertIn("never for either", rep["note"])
        text = json.dumps(rep)
        for n in range(3):
            self.assertNotIn(sg(f"a{n}"), text)
        for key in ("pool", "slot", "signature"):
            self.assertNotIn(f'"{key}"', text)


class TestErrclassAndOldLayout(Rule):
    def old_layout_outdir(self, ws_n=3, rounds=3, err=("a1",), found=("a2", "a3", "r1")):
        """Build an out-dir, then rewrite its plan in the older layout: no tape_absent in the meta block, no side file."""
        self.shadow([ws_(f"a{i}") for i in range(1, ws_n + 1)] + [refused("r1")])
        self.assertEqual(self.plan(ta=(1, 0, 0))[0], 0)
        fk = lambda: Fake(found=[sg(n) for n in found], err=[sg(n) for n in err])  # noqa: E731
        self.run_cmd("ask", fk(), Clock(T_ASK))
        for i in range(1, rounds + 1):
            self.run_cmd("reask", fk(), Clock(T_ASK + i * 600_000))
        pl = json.loads((self.out / C.PLAN_NAME).read_text())
        m = pl["wspass"]
        del m["tape_absent"]
        m["tool_sha256"] = "5a514c40197bcbed8ed71da4c3ceba151940fbea8305bc2bedfd0d73aea1ce42"
        m["plan_selection_sha256"] = W.selection_hash(m)
        (self.out / C.PLAN_NAME).write_text(C.dumps(pl))
        (self.out / W.SIDE_NAME).unlink()

    def snapshot(self):
        return {p.name: (p.read_bytes(), p.stat().st_mtime_ns, stat.S_IMODE(p.stat().st_mode)) for p in sorted(self.out.iterdir())}

    def no_call(self, *a, **k):
        raise AssertionError("errclass made a network call")

    def errclass(self, transport=None):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = W.main(["errclass", "--out-dir", str(self.out)], transport=transport or self.no_call)
        self.assertEqual(code, 0)
        return json.loads(buf.getvalue()), buf.getvalue()

    def test_old_layout_plan_accepted_by_errclass(self):
        self.old_layout_outdir()
        before = self.snapshot()
        res, text = self.errclass()
        self.assertEqual(self.snapshot(), before)  # nothing written in D, no mode change, no new file
        self.assertNotIn("reading", res)
        self.assertEqual(res["error_classes"], {"ws_pass": {"TimeoutError": 1}, "refused": {}, "would_pass": {}})
        self.assertEqual(res["error_answers"], {"ws_pass": {"TimeoutError": 4}, "refused": {}, "would_pass": {}})
        self.assertEqual(res["classes"]["ws_pass"], {"asked": 3, "returned": 2, "not_returned": 0, "error": 1, "pending": 0, "planned": 3, "unaskable": 0})
        self.assertEqual(res["plan_sha256"], W.sha256_bytes((self.out / C.PLAN_NAME).read_bytes()))
        self.assertEqual(res["state_sha256"], W.sha256_bytes((self.out / C.STATE_NAME).read_bytes()))
        rows = self.state_rows()
        self.assertEqual((res["first_asked_ms"], res["last_asked_ms"]), (min(r["asked_ms"] for r in rows), max(r["asked_ms"] for r in rows)))
        self.assertEqual(res["reask_rounds_n"], 3)
        for banned in (sg("a1"), "Pool", '"slot"', '"reading"'):
            self.assertNotIn(banned, text)

    def test_errclass_makes_no_network_call(self):
        self.old_layout_outdir()
        calls = []

        def spy(*a, **k):
            calls.append(a)
            raise AssertionError("network")

        self.errclass(transport=spy)
        self.assertEqual(calls, [])
        self.errclass(transport=C.http_transport)  # the real transport is never reached either (the network guard would raise)

    def test_errclass_refuses_while_unfinished_and_accepts_when_finished(self):
        self.old_layout_outdir(rounds=0, err=())  # ask only: a1 answered null once, so it is pending
        with self.assertRaises(SystemExit) as cm:
            W.main(["errclass", "--out-dir", str(self.out)], transport=self.no_call)
        self.assertIn("pending", str(cm.exception))
        self.again()
        self.old_layout_outdir(rounds=1, err=("a1",))  # fewer than 3 reask rounds with an error left
        with self.assertRaises(SystemExit) as cm:
            W.main(["errclass", "--out-dir", str(self.out)], transport=self.no_call)
        self.assertIn("fewer than 3 reask rounds", str(cm.exception))
        self.again()
        self.old_layout_outdir(rounds=1, err=(), found=("a1", "a2", "a3", "r1"))  # fewer than 3 rounds, no error and nothing pending: finished
        res, _ = self.errclass()
        self.assertEqual(res["error_classes"]["ws_pass"], {})
        self.again()
        self.old_layout_outdir(rounds=3, err=(), found=("a1", "a2", "a3", "r1"))  # a signature with no answer at all is pending
        rows = [r for r in self.state_rows() if r["sig"] != sg("a3")]
        (self.out / C.STATE_NAME).write_text("".join(json.dumps(r) + "\n" for r in rows))
        with self.assertRaises(SystemExit):
            W.main(["errclass", "--out-dir", str(self.out)], transport=self.no_call)

    def test_report_ask_and_reask_refuse_an_old_layout_plan(self):
        self.old_layout_outdir()
        with self.assertRaises(SystemExit) as cm:
            W.main(["report", "--out-dir", str(self.out)])
        self.assertNotEqual(cm.exception.code, 0)
        self.assertIn("older layout", str(cm.exception))
        self.assertFalse((self.out / W.REPORT_NAME).exists())
        for cmd in ("ask", "reask"):
            with self.assertRaises(SystemExit) as cm:
                W.main([cmd, "--out-dir", str(self.out)], transport=self.no_call, sleep=lambda s: None)
            self.assertNotEqual(cm.exception.code, 0)
        self.assertFalse((self.out / W.SIDE_NAME).exists())

    def test_errclass_refuses_a_plan_of_the_current_layout_a_foreign_plan_and_no_plan(self):
        self.full_plan()
        with self.assertRaises(SystemExit) as cm:
            W.main(["errclass", "--out-dir", str(self.out)], transport=self.no_call)
        self.assertIn("use `report`", str(cm.exception))
        self.again()
        self.old_layout_outdir()
        pl = json.loads((self.out / C.PLAN_NAME).read_text())
        pl["wspass"]["since_ms"] += 1  # the old-layout selection hash no longer matches
        (self.out / C.PLAN_NAME).write_text(C.dumps(pl))
        with self.assertRaises(SystemExit):
            W.main(["errclass", "--out-dir", str(self.out)], transport=self.no_call)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(W.main(["errclass", "--out-dir", str(self.tmp / "none")]), 3)

    def test_old_and_new_layout_hash_keys(self):
        self.assertEqual(W.HASH_KEYS_OLD, ("since_ms", "until_ms", "expect_code_sha", "class_sigs", "unaskable", "cross_class_duplicates"))
        body = {k: 1 for k in W.HASH_KEYS_OLD}
        old = W.selection_hash(body)
        self.assertEqual(old, W.sha256_bytes(C.dumps(body).encode("utf-8")))
        self.assertNotEqual(W.selection_hash({**body, "tape_absent": {"ws_pass": 1, "refused": 0, "would_pass": 0}}), old)


class TestReportFinishGuard(Rule):
    """Round 2, quant-proof blocking 1: `report` refuses, writes nothing and prints nothing while a signature is pending, never asked or in error and
    fewer than 3 reask rounds ran; after 3 reask rounds it always runs."""

    def assert_refused(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), self.assertRaises(SystemExit) as cm:
            W.main(["report", "--out-dir", str(self.out)])
        self.assertNotEqual(cm.exception.code, 0)
        self.assertIn("not finished", str(cm.exception))
        self.assertEqual(buf.getvalue(), "")
        self.assertFalse((self.out / W.REPORT_NAME).exists())

    def plan3(self, ta=(2, 0, 0)):
        self.full_plan(ws_names=("a1", "a2", "a3"), ref_names=("r1",), ta=ta)

    def test_no_ask_yet(self):
        self.plan3()
        self.assert_refused()

    def test_ask_with_one_null(self):
        self.plan3()
        self.asks(found=[sg("a1"), sg("a2"), sg("r1")], rounds=0)  # a3 answered null once
        self.assert_refused()

    def test_ask_with_one_error(self):
        self.plan3()
        self.asks(found=[sg("a1"), sg("a2"), sg("r1")], err=[sg("a3")], rounds=0)
        self.assert_refused()

    def test_two_rounds_with_an_error_left(self):
        self.plan3()
        self.asks(found=[sg("a1"), sg("a2"), sg("r1")], err=[sg("a3")], rounds=2)
        self.assertEqual(len({r["round"] for r in self.state_rows() if r["round"].startswith("reask")}), 2)
        self.assert_refused()

    def test_the_sentence_is_not_lost_by_an_early_look(self):
        self.plan3()
        self.asks(found=[sg("a1"), sg("a2"), sg("r1")], rounds=0)
        self.assert_refused()  # a3 is pending
        self.run_cmd("reask", Fake(found=[sg("a1"), sg("a2"), sg("r1")]), Clock(T_ASK + 600_000))  # a3 answers null a second time: not_returned
        rep = self.report()[0]
        self.assertEqual((rep["classes"]["ws_pass"]["returned"], rep["classes"]["ws_pass"]["not_returned"]), (2, 1))
        self.assertEqual(rep["reading"], W.READING_S1_ONE.format(p=3, a=2) + ". " + W.READING_S1_TAIL_ONE.format(r=1, a=2))
        self.assertTrue(rep["reading"].startswith("1 of 3 was not returned at finalized commitment"))
        self.assertEqual(rep["failed_checks"], [])

    def test_three_rounds_with_an_error_left_report_runs_with_no_conclusion(self):
        self.plan3()
        self.asks(found=[sg("a1"), sg("a2"), sg("r1")], err=[sg("a3")])
        rep = self.report()[0]
        self.assertEqual(rep["reask_rounds_n"], 3)
        self.assertEqual(rep["reading"], NONE)
        self.assertIn("ws_pass_error_above_0", rep["failed_checks"])

    def test_a_null_on_the_ask_and_an_error_on_each_of_three_reasks(self):
        self.plan3()
        self.asks(found=[sg("a1"), sg("a2"), sg("r1")], reask_err=[sg("a3")])
        rep = self.report()[0]
        self.assertEqual((rep["reask_rounds_n"], rep["classes"]["ws_pass"]["pending"]), (3, 1))
        self.assertEqual(rep["reading"], NONE)
        self.assertIn("ws_pass_pending_above_0", rep["failed_checks"])

    def test_every_print_returned_on_the_ask_needs_no_reask(self):
        self.plan3(ta=(2, 0, 0))
        self.asks(found=[sg("a1"), sg("a2"), sg("a3"), sg("r1")], rounds=0)
        rep = self.report()[0]
        self.assertEqual(rep["reask_rounds_n"], 0)
        self.assertEqual(rep["reading"], W.READING_ALL_MANY.format(n=3, a=2))

    def test_a_refusal_does_not_use_up_the_one_report(self):
        self.plan3()
        self.asks(found=[sg("a1"), sg("a2"), sg("r1")], rounds=0)
        self.assert_refused()
        self.assert_refused()
        self.assertEqual(sorted(p.name for p in self.out.iterdir()), sorted([C.PLAN_NAME, C.STATE_NAME, C.LOG_NAME, W.SIDE_NAME]))


class TestReadingConditionAndOperatorChecks(Rule):
    COND = 'a sentence other than "no conclusion" holds only if every line of operator_checks holds; one failure gives "no conclusion", with the counts and the failed check'
    CHECKS = [
        "every job exited 0; tool_sha256 equals the merged tool sha recorded at merge",
        "plan_sha256 equals the plan_sha256 printed by the plan job",
        "plan triggers_n equals the class counts of the trigger-print run of the same until-ms as recorded in LAB_STATE",
        "tape_absent equals the absent counts of that trigger-print run as recorded in LAB_STATE",
        "that trigger-print record shows differ 0 and undetermined 0 in every class",
        "wc -l of the state file equals the sum of the calls of the ask and reask jobs",
        "the last reask printed calls 0, or reask_rounds_n is 3",
    ]

    def test_reading_condition_is_always_present_beside_a_sentence_and_beside_no_conclusion(self):
        rep = self.scenario()
        self.assertEqual(rep["reading"], S0_15)
        self.assertEqual(rep["reading_condition"], self.COND)
        self.again()
        rep = self.scenario(ws=(15, 12))
        self.assertEqual(rep["reading"], NONE)
        self.assertEqual(rep["reading_condition"], self.COND)
        self.assertEqual(W.READING_CONDITION, self.COND)

    def test_operator_checks_are_the_seven_lines_in_order(self):
        self.assertEqual(W.OPERATOR_CHECKS, self.CHECKS)
        rep = self.scenario()
        self.assertEqual(rep["operator_checks"], self.CHECKS)
        self.assertEqual(len(rep["operator_checks"]), 7)

    def test_a_wrong_typed_tape_absent_count_is_what_operator_check_4_is_for(self):
        # typed (3, 3, 0) where the record says (2, 3, 0): the tool prints a sentence it cannot call wrong, and the report says it depends on check 4
        rep = self.scenario(ws=(15, 13), ref=(20, 17), ta=(3, 3, 0))
        self.assertEqual(rep["failed_checks"], [])
        self.assertTrue(rep["reading"].startswith("2 of 15 were not returned"))
        self.assertIn("tape_absent equals the absent counts of that trigger-print run as recorded in LAB_STATE", rep["operator_checks"])
        self.assertIn("every line of operator_checks holds", rep["reading_condition"])
        self.assertEqual(rep["tape_absent"], {"ws_pass": 3, "refused": 3, "would_pass": 0})

    def test_the_docstring_names_the_new_checks_and_the_condition(self):
        doc = " ".join(W.__doc__.split())
        for s in ("`tape_absent` equal to the absent counts of that run as recorded in LAB_STATE", "`plan_sha256` equal to the one printed by the plan job",
                  'The report carries `reading_condition`: a sentence other than "no conclusion" holds only if every line of `operator_checks` holds.',
                  "`report` refuses, writes nothing and prints nothing while a signature is pending, never asked or in error and fewer than 3 reask rounds ran; "
                  "after 3 reask rounds it always runs.",
                  '`interim_label` is "interim, not the deciding read" when `interim` is true and "not the deciding read unless item 4\'s duration line is met at '
                  'this until-ms" when it is false; `interim_note` is always the latter.'):
            self.assertIn(s, doc)

    def test_lab_state_carries_the_guard_the_label_rule_and_the_operator_checks(self):
        lab = " ".join(_lab_state_and_archive().split())
        for s in ("`report` refuses, writes nothing and prints nothing while a signature is pending, never asked or in error and fewer than 3 reask rounds ran.",
                  '`interim_label` is "interim, not the deciding read" when until minus since is under 24 h, and otherwise "not the deciding read unless item 4\'s '
                  'duration line is met at this until-ms"; `interim_note` is always the latter.',
                  "The checks it cannot see are printed as `operator_checks` (7 lines: job exits and tool sha, plan_sha256, triggers_n, tape_absent, differ and "
                  "undetermined, state line count, reask end), and the report carries `reading_condition`.",
                  'If one fails, what is quoted is "no conclusion" with the counts and the failed check, and the tool\'s sentence is not quoted.',
                  ", amended 2026-10-13T"):
            self.assertIn(s, lab)


def _lab_state_and_archive():
    """LAB_STATE.md plus its verbatim archive (dated sections moved there by the docs-slimming PR, 2026-10-15)."""
    root = Path(W.__file__).resolve().parent.parent
    return "\n".join((root / n).read_text(encoding="utf-8") for n in ("LAB_STATE.md", "ARTIFACTS/lab/lab-state-archive-2026-10.md"))


class TestDeclaredSentences(Rule):
    CONSTANTS = ("READING_ALL", "READING_ALL_MANY", "READING_ABSENT", "READING_S1_ONE", "READING_S1_MANY", "READING_S1_TAIL_ONE", "READING_S1_TAIL_MANY", "READING_S2",
                 "EXTRA_REFUSED", "INTERIM_LABEL", "INTERIM_NOTE")

    def lab(self):
        return _lab_state_and_archive()

    def test_every_constant_is_in_the_docstring_word_for_word(self):
        for name in self.CONSTANTS:
            self.assertIn(getattr(W, name), W.__doc__, name)
        self.assertEqual(W.READING_NONE, NONE)
        self.assertIn('"no conclusion"', W.__doc__)

    def test_the_constants_at_the_36h_numbers_give_the_declared_sentences(self):
        self.assertEqual(W.READING_ALL_MANY.format(n=15, a=2), S0_15)
        self.assertEqual(W.READING_S1_ONE.format(p=15, a=2) + ". " + W.READING_S1_TAIL_ONE.format(r=1, a=2), S1_15)
        self.assertEqual(W.READING_S2.format(a=2, p=15), S2_15)
        self.assertEqual(W.EXTRA_REFUSED.format(k=3, r=20, a=3), EXTRA_3)
        self.assertEqual(W.READING_ALL.format(n=5), OLD_ALL5)
        self.assertEqual(W.READING_ABSENT, OLD_ABSENT)

    def test_the_concrete_sentences_are_in_the_docstring_and_in_lab_state_character_for_character(self):
        doc, lab = " ".join(W.__doc__.split()), self.lab()
        for s in (S0_15, S1_15, S2_15, EXTRA_K, DISCLOSURE, OLD_ABSENT):
            self.assertIn(s, doc, s[:50])
            self.assertIn(s, lab, s[:50])
        self.assertIn(OLD_ALL5, lab)
        for s in ("can count against phase 2 and against the live rule, never for either", NONE):
            self.assertIn(s, doc)
            self.assertIn(s, lab)
        self.assertIn(CANARY, lab)


if __name__ == "__main__":
    unittest.main()
