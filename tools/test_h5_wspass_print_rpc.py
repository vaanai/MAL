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
import tempfile
import unittest
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


class Base(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.tmp = Path(self._td.name)
        self.sd = self.tmp / "shadow"
        self.sd.mkdir()
        self.out = self.tmp / "out"

    def shadow(self, recs, sha=SHA):
        write(self.sd, [start(T0 + 1000, sha=sha)] + list(recs))

    def plan(self, since=SINCE, until=UNTIL, sha=SHA, now=NOW, out=None, hook=None):
        argv = ["plan", "--shadow-dir", str(self.sd), "--since-ms", str(since), "--until-ms", str(until), "--expect-code-sha", sha,
                "--out-dir", str(out or self.out)]
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

    def report(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = W.main(["report", "--out-dir", str(self.out)])
        self.assertEqual(code, 0)
        return json.loads(buf.getvalue()), buf.getvalue()

    def state_rows(self):
        return C.read_jsonl(self.out / C.STATE_NAME)

    def full_plan(self, ws_names=("a1", "a2"), ref_names=("r1",), wp_names=()):
        recs = [ws(n) for n in ws_names] + [refused(n) for n in ref_names] + [would(n) for n in wp_names]
        self.shadow(recs)
        code, _ = self.plan()
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
        for name in (C.PLAN_NAME, C.STATE_NAME, C.LOG_NAME):
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
        self.ask()
        rep, _ = self.report()
        self.assertEqual((rep["classes"]["ws_pass"]["not_returned"], rep["classes"]["ws_pass"]["pending"]), (0, 1))
        for gap, want in ((599_999, "pending"), (600_000, "not_returned")):
            rows = [{"asked_ms": T_ASK, "sig": sg("a1"), "round": "ask", "result": "null"},
                    {"asked_ms": T_ASK + gap, "sig": sg("a1"), "round": "reask1", "result": "null"}]
            (self.out / C.STATE_NAME).write_text("".join(json.dumps(r) + "\n" for r in rows))
            rep, _ = self.report()
            self.assertEqual(rep["classes"]["ws_pass"][want], 1, gap)

    def test_error_never_becomes_not_returned(self):
        self.full_plan(ws_names=("a1",), ref_names=())
        self.ask(err=[sg("a1")])
        self.run_cmd("reask", Fake(err=[sg("a1")]), Clock(T_ASK + 10**7))
        rep, _ = self.report()
        d = rep["classes"]["ws_pass"]
        self.assertEqual((d["error"], d["not_returned"], d["returned"]), (1, 0, 0))
        self.assertEqual(rep["reading"], W.READING_NONE)
        # one null plus errors is pending, still not not_returned
        rows = [{"asked_ms": T_ASK, "sig": sg("a1"), "round": "ask", "result": "null"},
                {"asked_ms": T_ASK + 10**7, "sig": sg("a1"), "round": "reask1", "result": "error", "err": "TimeoutError"}]
        (self.out / C.STATE_NAME).write_text("".join(json.dumps(r) + "\n" for r in rows))
        rep, _ = self.report()
        self.assertEqual((rep["classes"]["ws_pass"]["not_returned"], rep["classes"]["ws_pass"]["pending"]), (0, 1))

    def test_report_has_no_signature_pool_or_slot(self):
        self.full_plan(wp_names=("w1",))
        self.ask(found=[sg("a1"), sg("r1")])
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
                    "--out-dir", str(out2)], now_ms=NOW)
        self.assertNotIn(sg("a1"), buf.getvalue())

    def test_counts_per_class(self):
        self.full_plan(ws_names=("a1", "a2", "a3"), ref_names=("r1", "r2"), wp_names=("w1",))
        self.ask(found=[sg("a1"), sg("r1"), sg("w1")])
        rep, _ = self.report()
        self.assertEqual({k: rep["classes"][k]["asked"] for k in ("ws_pass", "refused", "would_pass")}, {"ws_pass": 3, "refused": 2, "would_pass": 1})
        self.assertEqual((rep["classes"]["ws_pass"]["returned"], rep["classes"]["ws_pass"]["pending"]), (1, 2))

    def test_unasked_signatures_are_pending(self):
        self.full_plan()
        rep, _ = self.report()
        self.assertEqual(rep["classes"]["ws_pass"], {"asked": 0, "returned": 0, "not_returned": 0, "error": 0, "pending": 2, "planned": 2, "unaskable": 0})
        self.assertEqual(rep["reading"], W.READING_NONE)

    def two_rounds(self, found, ws_names=("a1", "a2"), ref_names=("r1",), wp_names=()):
        self.full_plan(ws_names, ref_names, wp_names)
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
        # every ws_pass not_returned is still allowed by (b) only if the rest are returned: here refused returned, so (b)
        self.tearDown()
        self.setUp()
        self.two_rounds([sg("r1")])
        self.assertEqual(self.report()[0]["reading"], W.READING_ABSENT)
        # a would-pass print pending (never asked twice) blocks both readings
        self.tearDown()
        self.setUp()
        self.full_plan(wp_names=("w1",))
        self.ask(found=[sg("a1"), sg("a2"), sg("r1")])
        self.assertEqual(self.report()[0]["reading"], "no conclusion")
        # no ws_pass at all
        self.tearDown()
        self.setUp()
        self.two_rounds([sg("r1")], ws_names=(), ref_names=("r1",))
        self.assertEqual(self.report()[0]["reading"], "no conclusion")

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


if __name__ == "__main__":
    unittest.main()
