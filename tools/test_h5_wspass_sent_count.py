"""Tests for tools/h5_wspass_sent_count.py (counts-only sent count of a finished ws-pass RPC check). Fixtures only: the RPC out-dir is built by the real
`plan` / `ask` / `reask` / `report` commands of tools.h5_wspass_print_rpc with a fake transport; the ledger and the shadow are written under tmp_path. No real
file, no network (a guard fails the test on any socket use).

  /data/mal/venv/bin/python -m pytest -q tools/test_h5_wspass_sent_count.py
"""
from __future__ import annotations

import contextlib
import datetime
import hashlib
import io
import json
import os
import socket
import stat
import urllib.request
from pathlib import Path
from unittest import mock

import pytest

from tools import h5_tipauth_census as C
from tools import h5_tipauth_trigger_print as TP
from tools import h5_wspass_print_rpc as W
from tools import h5_wspass_sent_count as S
from tools import test_h5_wspass_print_rpc as RT
from tools.test_h5_tipauth_item4 import H, MID, T0, start, trig, write

SINCE, UNTIL = T0, T0 + 6 * H
SHA = RT.SHA
T_ASK = RT.T_ASK
SENT_MS = 1_700_000_123_457  # a ledger instant that must never be printed
LEDGER_TS = 1_700_000_999_001
SEAL = S.SEAL_MS


class NetworkAttempted(BaseException):
    pass


@pytest.fixture(autouse=True)
def no_network():
    def boom(*a, **k):
        raise NetworkAttempted("a network call was attempted")
    ps = [mock.patch.object(urllib.request.OpenerDirector, "open", boom), mock.patch.object(socket.socket, "connect", boom),
          mock.patch.object(socket, "create_connection", boom), mock.patch.object(socket, "getaddrinfo", boom)]
    for p in ps:
        p.start()
    yield
    for p in ps:
        p.stop()


# ---- shadow records -----------------------------------------------------------------------------------------------------------------------------

def sg(n):
    return RT.sg(n)


def pool_of(n):
    return "Pool" + n


def rec(kind, n, slot, t=MID, pool=None, **kw):
    pool = pool_of(n) if pool is None else pool
    if kind == "ws":
        return trig(t=t, pool=pool, ws=0, uval=0, signature=sg(n), slot=slot, **kw)
    if kind == "ref":
        return trig(t=t, pool=pool, boost_src="none", signature=sg(n), slot=slot, **kw)
    return trig(t=t, pool=pool, signature=sg(n), slot=slot, **kw)


BASE = 987_600_000  # slots are 9 digits so that no printed hash or count can contain one by chance


def prints_default():
    """(kind, name, slot, answer). a3 is not returned."""
    return [("ws", "a1", BASE + 1, "found"), ("ws", "a2", BASE + 2, "found"), ("ws", "a3", BASE + 3, "null"), ("ref", "r1", BASE + 4, "found")]


def prints_each_status():
    return [("ws", "ret", BASE + 11, "found"), ("ws", "not", BASE + 12, "null"), ("ws", "err", BASE + 13, "err"), ("ws", "pen", BASE + 14, "pend"),
            ("ws", "ret2", BASE + 15, "found"), ("ws", "not2", BASE + 16, "null"), ("ref", "r1", BASE + 17, "found")]


class Env:
    """A finished RPC check made by the real RPC tool, plus the shadow dir it was planned from."""

    def __init__(self, tmp: Path, prints, ta=(1, 0, 0), since=SINCE, until=UNTIL, sha=SHA, extra=(), report=True):
        self.tmp, self.since, self.until, self.sha, self.prints = tmp, since, until, sha, prints
        self.sd, self.rpc = tmp / "shadow", tmp / "rpc"
        self.sd.mkdir()
        t = since + 30 * 60_000
        recs = [rec(k, n, slot, t=t) for k, n, slot, _a in prints] + list(extra)
        write(self.sd, [start(since + 1000, sha=sha)] + recs, hour_ms=since)
        self.plan(ta)
        found = [sg(n) for _k, n, _s, a in prints if a == "found"]
        err = [sg(n) for _k, n, _s, a in prints if a == "err"]
        pend = [sg(n) for _k, n, _s, a in prints if a == "pend"]
        self.rpc_cmd("ask", RT.Fake(found=found, err=err), T_ASK)
        for i in (1, 2, 3):
            self.rpc_cmd("reask", RT.Fake(found=found, err=err + pend), T_ASK + i * 600_000)
        if report:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                assert W.main(["report", "--out-dir", str(self.rpc)]) == 0

    def plan(self, ta):
        argv = ["plan", "--shadow-dir", str(self.sd), "--since-ms", str(self.since), "--until-ms", str(self.until), "--expect-code-sha", self.sha,
                "--out-dir", str(self.rpc), "--tape-absent-ws", str(ta[0]), "--tape-absent-refused", str(ta[1]), "--tape-absent-would-pass", str(ta[2])]
        with contextlib.redirect_stdout(io.StringIO()):
            assert W.main(argv, now_ms=self.until + 600_000) == 0

    def rpc_cmd(self, cmd, transport, t):
        with contextlib.redirect_stdout(io.StringIO()), mock.patch.object(W.time, "time", lambda: t / 1000):
            assert W.main([cmd, "--out-dir", str(self.rpc)], transport=transport, sleep=lambda s: None) == 0

    def argv(self, ledgers, out, since=None, until=None, sha=None):
        return ["--rpc-out-dir", str(self.rpc), "--shadow-dir", str(self.sd), "--since-ms", str(self.since if since is None else since),
                "--until-ms", str(self.until if until is None else until), "--expect-code-sha", self.sha if sha is None else sha,
                "--ledger", *[str(x) for x in ledgers], "--out", str(out)]

    def run(self, ledgers, out=None, shadow_hook=None, ledger_hook=None, **kw):
        out = out or self.tmp / "sent.json"
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = S.main(self.argv(ledgers, out, **kw), shadow_hook, ledger_hook)
        return code, buf.getvalue(), Path(out)


# ---- ledger rows (the layout h5_executor.py writes: FillLog adds schema and mode, _log adds kind, ts_ms, mint, book) -----------------------------

def decision(pool, slot, mode="live", **kw):
    r = {"schema": "h5_ledger_v1", "mode": mode, "kind": "decision", "ts_ms": LEDGER_TS, "mint": "LedgerMintXYZ", "book": "h5_boostfloor_v1",
         "pool": pool, "s0_slot": slot - 50, "trigger_slot": slot, "q_lamports": 5, "plan": {"exit_slot": 9}, "anchor": {"x": 1},
         "expected_tokens": 111, "min_out": 99, "signature": "LedgerSigXYZ", "stake_lamports": 20_000_000}
    if mode == "live":
        r["sent_ms"] = SENT_MS
    else:
        r["sent"] = False
    r.update(kw)
    return r


def other(kind, pool="PoolOther", slot=1):
    return {"schema": "h5_ledger_v1", "mode": "live", "kind": kind, "ts_ms": LEDGER_TS, "mint": "LedgerMintXYZ", "pool": pool, "trigger_slot": slot,
            "sent_ms": SENT_MS, "signature": "LedgerSigXYZ", "pnl_sol": 9.0, "fill": {"a": 1}}


def write_ledger(path: Path, rows, raw=()):
    with open(path, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, separators=(",", ":")) + "\n")
        for x in raw:
            fh.write(x if isinstance(x, str) else "")
    return path


FIXTURE_STRINGS = ["Sig" + n + "x" * 20 for n in ("a1", "a2", "a3", "r1", "ret", "not", "err", "pen", "ret2", "not2")] + \
    ["Pool" + n for n in ("a1", "a2", "a3", "r1", "ret", "not", "err", "pen", "ret2", "not2", "Other")] + \
    ["LedgerSigXYZ", "LedgerMintXYZ", str(SENT_MS), str(LEDGER_TS)] + [str(BASE + i) for i in range(0, 30)]


def assert_clean(text: str):
    for s in FIXTURE_STRINGS:
        assert s not in text, s
    for banned in ('"pool"', '"mint"', '"signature"', '"slot"', '"trigger_slot"', '"sent_ms"', '"ts_ms"'):
        assert banned not in text, banned


def sha_of(p: Path):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def result(out: Path):
    return json.loads(out.read_text())


# ---- happy path, every RPC status ---------------------------------------------------------------------------------------------------------------

def test_each_rpc_status_sent_and_not_sent(tmp_path):
    env = Env(tmp_path, prints_each_status())
    led = write_ledger(tmp_path / "led.jsonl", [decision(pool_of("ret"), BASE + 11), decision(pool_of("not"), BASE + 12), decision(pool_of("pen"), BASE + 14),
                                               other("fill", pool_of("err"), BASE + 13)])
    code, text, out = env.run([led])
    assert code == 0, text
    assert out.read_text() == text
    r = json.loads(text)
    ws = r["ws_pass"]
    assert (ws["planned"], ws["sent"], ws["not_sent"]) == (6, 3, 3)
    assert ws["sent_by_rpc_status"] == {"returned": 1, "not_returned": 1, "error": 0, "pending": 1}
    assert ws["not_sent_by_rpc_status"] == {"returned": 1, "not_returned": 1, "error": 1, "pending": 0}
    rep = r["report"]["ws_pass"]
    for s in S.STATUSES:  # the rows add up to the report's per-status counts
        assert ws["sent_by_rpc_status"][s] + ws["not_sent_by_rpc_status"][s] == rep[s]
    assert rep["planned"] == 6 and rep["not_returned"] == 2
    assert r["report"]["reading_is_no_conclusion"] is True
    assert r["dec024_am9"] == {"evaluated": False, "fires": False, "owner_at_once": True}
    assert r["sentence"] == "Of the 2 ws-pass prints the public RPC did not return, the bot sent a buy on 1."
    assert r["am9_reading"] == S.AM9_NOT_EVALUATED and "not \"did not fire\"" in r["am9_reading"]
    assert stat.S_IMODE(os.stat(out).st_mode) == 0o600
    assert r["sent_count_may_be_low"] is False
    assert_clean(text)
    assert W.READING_NONE not in r.values() and r["report"] == {"reading_is_no_conclusion": True, "ws_pass": rep}


def test_hashes_are_the_files_hashes(tmp_path):
    env = Env(tmp_path, prints_default())
    led1 = write_ledger(tmp_path / "l1.jsonl", [decision(pool_of("a1"), BASE + 1)])
    led2 = write_ledger(tmp_path / "l2.jsonl", [other("fill")])
    code, text, out = env.run([led1, led2])
    assert code == 0
    h = result(out)["hashes"]
    assert h["tool_sha256"] == sha_of(Path(S.__file__)) == S.tool_sha()
    assert h["report_sha256"] == sha_of(env.rpc / W.REPORT_NAME)
    assert h["plan_sha256"] == sha_of(env.rpc / C.PLAN_NAME)
    assert h["state_sha256"] == sha_of(env.rpc / C.STATE_NAME) == json.loads((env.rpc / W.REPORT_NAME).read_text())["state_sha256"]
    assert h["plan_selection_sha256"] == json.loads((env.rpc / C.PLAN_NAME).read_text())["wspass"]["plan_selection_sha256"]
    assert [f["sha256"] for f in h["ledger_files"]] == [sha_of(led1), sha_of(led2)] and [f["index"] for f in h["ledger_files"]] == [0, 1]
    assert all(set(f) == {"index", "sha256", "records_parsed", "bad_lines"} for f in h["ledger_files"])


# ---- what counts as sent -------------------------------------------------------------------------------------------------------------------------

def sent_n(tmp_path, rows, raw=(), prints=None):
    env = Env(tmp_path, prints or prints_default())
    led = write_ledger(tmp_path / "led.jsonl", rows, raw)
    code, text, out = env.run([led])
    assert code == 0, text
    return result(out)


def test_dry_run_decision_row_is_not_sent(tmp_path):
    r = sent_n(tmp_path, [decision(pool_of("a1"), BASE + 1, mode="dryrun"), decision(pool_of("a2"), BASE + 2, mode="dryrun", sent_ms=SENT_MS),
                          decision(pool_of("a3"), BASE + 3)])
    assert r["ws_pass"]["sent"] == 1 and r["ws_pass"]["sent_by_rpc_status"]["not_returned"] == 1
    assert r["ledger"]["decision_rows_dryrun"] == 2 and r["ledger"]["live_decision_rows"] == 1


@pytest.mark.parametrize("mut", [None, "missing", True, False, "123", 1.5, [1], {"a": 1}])
def test_sent_ms_must_be_an_integer(tmp_path, mut):
    row = decision(pool_of("a3"), BASE + 3)
    if mut == "missing":
        del row["sent_ms"]
    else:
        row["sent_ms"] = mut
    r = sent_n(tmp_path, [row, decision(pool_of("a1"), BASE + 1)])
    assert r["ws_pass"]["sent"] == 1 and r["ws_pass"]["sent_by_rpc_status"]["returned"] == 1
    assert r["ledger"]["live_decision_rows"] == 2 and r["ledger"]["live_decision_rows_sent_ms_not_int"] == 1
    assert r["ledger"]["live_decision_rows_sent_ms_present_not_int"] == (0 if mut in (None, "missing") else 1)
    assert r["dec024_am9"]["owner_at_once"] is False


def test_integer_zero_and_negative_sent_ms_are_integers(tmp_path):
    r = sent_n(tmp_path, [decision(pool_of("a3"), BASE + 3, sent_ms=0)])
    assert r["ws_pass"]["sent"] == 1


def test_same_pool_other_trigger_slot_is_not_sent(tmp_path):
    r = sent_n(tmp_path, [decision(pool_of("a3"), BASE + 99), decision(pool_of("a3"), BASE + 3, trigger_slot=str(BASE + 3))])
    assert r["ws_pass"]["sent"] == 0 and r["dec024_am9"]["owner_at_once"] is False
    assert r["ledger"]["live_sent_rows"] == 2 and r["ledger"]["live_sent_join_keys_n"] == 1  # the string slot is unusable, the other is a key that matches no print


def test_two_live_sent_rows_for_one_pool_and_slot_count_once(tmp_path):
    env = Env(tmp_path, prints_default())
    l1 = write_ledger(tmp_path / "l1.jsonl", [decision(pool_of("a3"), BASE + 3), decision(pool_of("a3"), BASE + 3, ts_ms=LEDGER_TS + 5)])
    l2 = write_ledger(tmp_path / "l2.jsonl", [decision(pool_of("a3"), BASE + 3)])
    code, text, out = env.run([l1, l2])
    r = json.loads(text)
    assert code == 0 and r["ws_pass"]["sent"] == 1
    assert r["ledger"]["live_sent_rows"] == 3 and r["ledger"]["live_sent_join_keys_n"] == 1
    assert r["ledger"]["live_sent_keys_with_more_than_one_row_n"] == 1 and r["ledger"]["live_sent_extra_rows_n"] == 2


@pytest.mark.parametrize("marker", ["missing", None, "LIVE", "Live", "", 1, True, ["live"]])
def test_marker_absent_or_other_is_not_live(tmp_path, marker):
    row = decision(pool_of("a3"), BASE + 3)
    if marker == "missing":
        del row["mode"]
    else:
        row["mode"] = marker
    r = sent_n(tmp_path, [row, decision(pool_of("a1"), BASE + 1)])
    assert r["ws_pass"]["sent"] == 1 and r["ws_pass"]["sent_by_rpc_status"]["returned"] == 1
    assert r["ledger"]["decision_rows"] == 2 and r["ledger"]["live_decision_rows"] == 1
    assert r["ledger"]["decision_rows_without_marker"] == (1 if marker == "missing" else 0)
    assert r["ledger"]["decision_rows_marker_other"] == (0 if marker == "missing" else 1)


def test_rows_of_other_kinds_are_not_sent(tmp_path):
    r = sent_n(tmp_path, [other("buy_sent", pool_of("a3"), BASE + 3), other("fill", pool_of("a3"), BASE + 3), other("send", pool_of("a2"), BASE + 2),
                          decision(pool_of("a1"), BASE + 1)], raw=["{\"kind\": [1, 2]}\n", "{\"kind\": null}\n", "{}\n"])
    assert r["ws_pass"]["sent"] == 1 and r["ledger"]["other_kind_rows"] == 6 and r["ledger"]["decision_rows"] == 1


def test_unusable_join_key_makes_the_count_a_lower_bound(tmp_path):
    r = sent_n(tmp_path, [decision(None, BASE + 1), decision("", BASE + 2), decision(pool_of("a3"), BASE + 3, trigger_slot=True),
                          decision(pool_of("a3"), BASE + 3, trigger_slot=None)])
    assert r["ws_pass"]["sent"] == 0 and r["ledger"]["live_sent_rows_join_key_unusable"] == 4 and r["sent_count_may_be_low"] is True


def test_bad_lines_are_counted_not_fatal(tmp_path):
    env = Env(tmp_path, prints_default())
    led = tmp_path / "led.jsonl"
    led.write_bytes((json.dumps(decision(pool_of("a3"), BASE + 3)) + "\n").encode() + b"\xff\xfe not utf8\n" + b"{\"kind\": NaN}\n" + b"[1, 2]\n" + b"{\"kind\": \"decis")
    code, text, out = env.run([led])
    r = json.loads(text)
    assert code == 0 and r["ws_pass"]["sent"] == 1
    assert r["ledger"]["bad_lines"] == 4 and r["ledger"]["records_parsed"] == 1 and r["ledger"]["lines"] == 5


def test_two_prints_sharing_one_join_key_are_both_sent_and_reported(tmp_path):
    prints = [("ws", "a1", BASE + 1, "found"), ("ws", "a2", BASE + 1, "found"), ("ws", "a3", BASE + 3, "null"), ("ref", "r1", BASE + 4, "found")]
    env = Env(tmp_path, prints)
    # a2 is a second print of a1's pool and slot: give it a1's pool by rewriting nothing; the plan is made from the shadow, so build that shadow instead
    tmp2 = tmp_path / "second"
    tmp2.mkdir()
    t = SINCE + 30 * 60_000
    env2 = Env.__new__(Env)
    env2.tmp, env2.since, env2.until, env2.sha, env2.prints = tmp2, SINCE, UNTIL, SHA, prints
    env2.sd, env2.rpc = tmp2 / "shadow", tmp2 / "rpc"
    env2.sd.mkdir()
    write(env2.sd, [start(SINCE + 1000, sha=SHA), rec("ws", "a1", BASE + 1, t=t), rec("ws", "a2", BASE + 1, t=t, pool=pool_of("a1")), rec("ws", "a3", BASE + 3, t=t),
                    rec("ref", "r1", BASE + 4, t=t)], hour_ms=SINCE)
    env2.plan((1, 0, 0))
    env2.rpc_cmd("ask", RT.Fake(found=[sg("a1"), sg("a2"), sg("r1")]), T_ASK)
    for i in (1, 2, 3):
        env2.rpc_cmd("reask", RT.Fake(found=[sg("a1"), sg("a2"), sg("r1")]), T_ASK + i * 600_000)
    with contextlib.redirect_stdout(io.StringIO()):
        assert W.main(["report", "--out-dir", str(env2.rpc)]) == 0
    led = write_ledger(tmp2 / "led.jsonl", [decision(pool_of("a1"), BASE + 1)])
    code, text, out = env2.run([led])
    r = json.loads(text)
    assert code == 0 and r["ws_pass"]["sent"] == 2 and r["ws_pass"]["prints_sharing_join_key_n"] == 2


# ---- the keys read ------------------------------------------------------------------------------------------------------------------------------

def recording_hook(seen: set, instances: list | None = None):
    class R(dict):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self.acc = set()

        def _n(self, k):
            self.acc.add(k)
            seen.add(k)

        def get(self, k, d=None):
            self._n(k)
            return dict.get(self, k, d)

        def __getitem__(self, k):
            self._n(k)
            return dict.__getitem__(self, k)

        def __contains__(self, k):
            self._n(k)
            return dict.__contains__(self, k)

        def __iter__(self):
            self._n("<iter>")
            return dict.__iter__(self)

        def keys(self):
            self._n("<keys>")
            return dict.keys(self)

        def items(self):
            self._n("<items>")
            return dict.items(self)

        def values(self):
            self._n("<values>")
            return dict.values(self)

        def pop(self, *a):
            self._n("<pop>")
            return dict.pop(self, *a)

    def hook(pairs):
        r = R(pairs)
        if instances is not None:
            instances.append(r)
        return r
    return hook


def test_allowed_key_set_is_exact(tmp_path):
    assert S.LEDGER_KEYS == {"kind", "mode", "ts_ms", "sent_ms", "pool", "trigger_slot"}
    env = Env(tmp_path, prints_default())
    seen: set = set()
    rows = [decision(pool_of("a3"), BASE + 3), decision(pool_of("a1"), BASE + 1, mode="dryrun"), other("fill"), other("buy_sent"),
            decision(pool_of("a2"), BASE + 2, sent_ms=None)]
    led = write_ledger(tmp_path / "led.jsonl", rows)
    code, text, out = env.run([led], ledger_hook=recording_hook(seen))
    assert code == 0, text
    assert seen == S.LEDGER_KEYS, sorted(seen ^ S.LEDGER_KEYS)
    for never in ("signature", "mint", "expected_tokens", "min_out", "q_out", "pnl_sol", "fill", "plan", "anchor", "sent", "stake_lamports", "<iter>", "<keys>",
                  "<items>", "<values>", "<pop>", "schema", "book", "q_lamports", "s0_slot"):
        assert never not in seen


def test_each_row_is_read_only_as_far_as_its_kind_and_marker_allow(tmp_path):
    env = Env(tmp_path, prints_default())
    seen: set = set()
    inst: list = []
    rows = [other("fill"), other("buy_sent"), other("exit"), decision(pool_of("a1"), BASE + 1, mode="dryrun"), decision(pool_of("a2"), BASE + 2),
            decision(pool_of("a3"), BASE + 3, sent_ms=None), dict(decision(pool_of("a1"), BASE + 1), mode=None), decision(pool_of("a1"), BASE + 1, mode="weird")]
    del rows[-2]["mode"]
    led = write_ledger(tmp_path / "led.jsonl", rows)
    code, text, out = env.run([led], ledger_hook=recording_hook(seen, inst))
    assert code == 0, text
    tops = [i for i in inst if dict.get(i, "schema") == "h5_ledger_v1"]
    assert len(tops) == len(rows)
    for i in tops:
        kind, mode = dict.get(i, "kind"), dict.get(i, "mode", "<absent>")
        if kind != "decision":
            assert i.acc == {"kind"}, (kind, i.acc)
        elif mode == "live" and dict.get(i, "sent_ms") is None:
            assert i.acc == {"kind", "mode", "ts_ms", "sent_ms"}
        elif mode == "live":
            assert i.acc == S.LEDGER_KEYS
        else:  # dryrun, no marker, null marker, other marker
            assert i.acc == {"kind", "mode"}, (mode, i.acc)


def test_shadow_keys_stay_within_the_allow_list_and_pool_slot_only_on_ws_pass_signatures(tmp_path):
    # a refused record with an unusable pool and slot, and a sealed record with a ws-pass-looking signature: neither is read for pool or slot
    extra = [rec("ref", "r2", "not-an-int", t=SINCE + 30 * 60_000, pool=None), rec("ws", "sealed", BASE + 77, t=SINCE + 30 * 60_000, sealed=True)]
    extra[0]["pool"] = None
    env = Env(tmp_path, prints_default(), extra=extra)
    seen: set = set()
    led = write_ledger(tmp_path / "led.jsonl", [decision(pool_of("a3"), BASE + 3)])
    code, text, out = env.run([led], shadow_hook=recording_hook(seen))
    assert code == 0, text
    assert seen <= TP.ALLOWED_KEYS, sorted(seen - TP.ALLOWED_KEYS)
    for banned in ("pnl_sol", "q_trigger_sol", "exit_ladder_trigger_slots"):
        assert banned not in seen
    assert {"pool", "slot", "signature"} <= seen


# ---- no pool, mint, signature, slot or time in any output ---------------------------------------------------------------------------------------

def test_no_fixture_identifier_in_stdout_out_file_or_any_refusal(tmp_path):
    env = Env(tmp_path, prints_each_status())
    led = write_ledger(tmp_path / "led.jsonl", [decision(pool_of("ret"), BASE + 11), decision(pool_of("not"), BASE + 12)])
    code, text, out = env.run([led])
    assert code == 0
    assert_clean(text)
    assert_clean(out.read_text())
    texts = []
    for kw in ({"since": SINCE + 1}, {"until": UNTIL + H}, {"sha": "b" * 40}, {"until": SEAL}):
        c, t, _o = env.run([led], out=tmp_path / "x.json", **kw)
        assert c != 0
        texts.append(t)
    c, t, _o = env.run([tmp_path / "nope.jsonl"], out=tmp_path / "y.json")
    assert c == 10
    texts.append(t)
    c, t, _o = env.run([led], out=out)  # out exists
    assert c == 6
    texts.append(t)
    for t in texts:
        assert_clean(t)
        assert set(json.loads(t)) == {"tool", "error", "exit_code"}


# ---- the four combinations of dec024_am9 -------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("ta,sent_a3,evaluated,fires,owner", [
    ((0, 0, 0), False, False, False, False),  # "no conclusion" (ws-pass tape-absent is 0), K = 0
    ((0, 0, 0), True, False, False, True),    # "no conclusion", K = 1: not evaluated, does not fire, the owner is told at once
    ((1, 0, 0), False, True, False, False),   # a conclusive reading, K = 0
    ((1, 0, 0), True, True, True, True),      # a conclusive reading, K = 1: fires
])
def test_dec024_am9_four_combinations(tmp_path, ta, sent_a3, evaluated, fires, owner):
    env = Env(tmp_path, prints_default(), ta=ta)
    rep = json.loads((env.rpc / W.REPORT_NAME).read_text())
    assert (rep["reading"] == W.READING_NONE) is (not evaluated)
    rows = [decision(pool_of("a1"), BASE + 1)] + ([decision(pool_of("a3"), BASE + 3)] if sent_a3 else [])
    code, text, out = env.run([write_ledger(tmp_path / "led.jsonl", rows)])
    r = json.loads(text)
    assert code == 0
    assert r["dec024_am9"] == {"evaluated": evaluated, "fires": fires, "owner_at_once": owner}
    k = 1 if sent_a3 else 0
    assert r["sentence"] == f"Of the 1 ws-pass prints the public RPC did not return, the bot sent a buy on {k}."
    assert r["report"]["reading_is_no_conclusion"] is (not evaluated)
    assert r["am9_reading"] == (S.AM9_EVALUATED if evaluated else S.AM9_NOT_EVALUATED)
    if evaluated:
        assert rep["reading"] not in text  # the report's reading sentence is copied only as a boolean
    assert_clean(text)


def test_sentence_form_is_fixed():
    assert S.SENTENCE.format(n=2, k=1) == "Of the 2 ws-pass prints the public RPC did not return, the bot sent a buy on 1."
    block, sentence, _t = S.am9(False, 0, 0)
    assert block == {"evaluated": True, "fires": False, "owner_at_once": False} and sentence.endswith("sent a buy on 0.")


# ---- refusals and exit codes -------------------------------------------------------------------------------------------------------------------

def new_env(d: Path, **kw):
    d.mkdir()
    return Env(d, prints_default(), **kw)


def good_ledger(tmp_path):
    return write_ledger(tmp_path / "good.jsonl", [decision(pool_of("a1"), BASE + 1)])


def refused(env, ledgers, code, **kw):
    out = env.tmp / "refused.json"
    c, text, o = env.run(ledgers, out=out, **kw)
    assert c == code, text
    assert not out.exists(), "a refused run leaves no output file"
    body = json.loads(text)
    assert set(body) == {"tool", "error", "exit_code"} and body["exit_code"] == code
    assert_clean(text)
    return body


def test_exit_missing_or_unfinished_out_dir(tmp_path):
    env = Env(tmp_path, prints_default())
    led = good_ledger(tmp_path)
    (env.rpc / W.REPORT_NAME).rename(tmp_path / "moved.json")
    assert "not finished" in refused(env, [led], 3)["error"]
    (tmp_path / "moved.json").rename(env.rpc / W.REPORT_NAME)
    (env.rpc / W.REPORT_NAME).write_text("not json")
    refused(env, [led], 3)
    (env.rpc / W.REPORT_NAME).write_text("{}")
    refused(env, [led], 3)
    env2 = new_env(tmp_path / "e2")
    env2.rpc = tmp_path / "no-such-dir"
    refused(env2, [led], 3)
    env3 = new_env(tmp_path / "e3")
    env3.sd = tmp_path / "no-shadow"
    refused(env3, [led], 3)


def test_unfinished_check_without_report_is_refused(tmp_path):
    env = Env(tmp_path, prints_default(), report=False)
    refused(env, [good_ledger(tmp_path)], 3)


def test_old_layout_plan_is_refused(tmp_path):
    env = Env(tmp_path, prints_default())
    plan = json.loads((env.rpc / C.PLAN_NAME).read_text())
    del plan["wspass"]["tape_absent"]
    (env.rpc / C.PLAN_NAME).write_text(json.dumps(plan))
    refused(env, [good_ledger(tmp_path)], 3)


@pytest.mark.parametrize("kw", [{"since": SINCE + 1}, {"until": UNTIL + H}, {"sha": "c" * 40}])
def test_exit_flag_mismatch_with_the_plan(tmp_path, kw):
    env = Env(tmp_path, prints_default())
    refused(env, [good_ledger(tmp_path)], 4, **kw)


def test_exit_selection_mismatch(tmp_path):
    env = Env(tmp_path, prints_default())
    led = good_ledger(tmp_path)
    # a ws-pass trigger record the plan does not have
    write(env.sd, [rec("ws", "late", BASE + 21, t=SINCE + 31 * 60_000)], hour_ms=SINCE)
    refused(env, [led], 5)


def test_exit_selection_mismatch_when_a_planned_record_is_gone(tmp_path):
    env = Env(tmp_path, prints_default())
    for f in env.sd.glob("h5-shadow-*.jsonl"):
        lines = [x for x in f.read_text().splitlines() if sg("a2") not in x]
        f.write_text("\n".join(lines) + "\n")
    refused(env, [good_ledger(tmp_path)], 5)


def test_exit_ws_pass_record_without_a_usable_join_key(tmp_path):
    env = Env(tmp_path, [("ws", "a1", "not-an-int", "found"), ("ws", "a2", BASE + 2, "null"), ("ref", "r1", BASE + 4, "found")])
    body = refused(env, [good_ledger(tmp_path)], 5)
    assert "join key" in body["error"]
    tmp2 = tmp_path / "t2"
    tmp2.mkdir()
    prints = [("ws", "a1", BASE + 1, "found"), ("ws", "a2", BASE + 2, "null"), ("ref", "r1", BASE + 4, "found")]
    env2 = Env(tmp2, prints)
    for f in env2.sd.glob("h5-shadow-*.jsonl"):
        f.write_text(f.read_text().replace(pool_of("a2"), "").replace('"pool":"",', '"pool":null,'))
    for f in env2.sd.glob("h5-shadow-*.jsonl"):
        assert pool_of("a2") not in f.read_text()
    refused(env2, [good_ledger(tmp2)], 5)


def test_exit_seal_boundary(tmp_path):
    assert SEAL == int(datetime.datetime(2026, 10, 16, 1, 0, 0, tzinfo=datetime.timezone.utc).timestamp() * 1000) == 1792112400000
    # real runs on both sides: a 24 h read ending one hour before the seal, and one ending exactly at it
    before = new_env(tmp_path / "b", since=SEAL - 25 * H, until=SEAL - H)
    code, text, out = before.run([good_ledger(tmp_path)])
    assert code == 0, text
    after = new_env(tmp_path / "a", since=SEAL - 24 * H, until=SEAL)
    refused(after, [good_ledger(tmp_path)], 7)
    # the seal check comes before every other check, so an out-dir that does not exist still gives 7 at the boundary and not before it
    ghost = Env.__new__(Env)
    ghost.tmp, ghost.since, ghost.sha = tmp_path, SINCE, SHA
    ghost.sd, ghost.rpc = tmp_path / "ghost-shadow", tmp_path / "ghost-rpc"
    for until, want in ((SEAL - 1, 3), (SEAL, 7), (SEAL + 1, 7), (SEAL + 365 * 24 * H, 7)):
        ghost.until = until
        c, text, _o = ghost.run([tmp_path / "x.jsonl"], out=tmp_path / "ghost.json")
        assert c == want, (until, text)


def test_exit_out_file_exists_and_is_never_overwritten(tmp_path):
    env = Env(tmp_path, prints_default())
    led = good_ledger(tmp_path)
    out = tmp_path / "keep.json"
    out.write_text("old")
    c, text, _o = env.run([led], out=out)
    assert c == 6 and out.read_text() == "old"
    c, text, _o = env.run([led], out=tmp_path / "no-such-dir" / "x.json")
    assert c == 6
    # write once: a second run into the first run's file is refused and the file is unchanged
    first = tmp_path / "once.json"
    assert env.run([led], out=first)[0] == 0
    before = first.read_bytes()
    assert env.run([led], out=first)[0] == 6 and first.read_bytes() == before


def test_exit_status_totals_differ_from_the_report(tmp_path):
    env = Env(tmp_path, prints_default())
    p = env.rpc / W.REPORT_NAME
    rep = json.loads(p.read_text())
    rep["classes"]["ws_pass"]["returned"] += 1
    rep["classes"]["ws_pass"]["not_returned"] -= 1
    p.write_text(json.dumps(rep))
    refused(env, [good_ledger(tmp_path)], 8)


def test_exit_state_or_plan_differs_from_the_hash_the_report_names(tmp_path):
    env = Env(tmp_path, prints_default())
    st = env.rpc / C.STATE_NAME
    old = st.read_bytes()
    st.write_bytes(old + b"\n")
    refused(env, [good_ledger(tmp_path)], 9)
    st.write_bytes(old)
    plan = env.rpc / C.PLAN_NAME
    plan.write_bytes(plan.read_bytes() + b"\n")
    refused(env, [good_ledger(tmp_path)], 9)


def test_exit_ledger_unreadable(tmp_path):
    env = Env(tmp_path, prints_default())
    refused(env, [tmp_path / "nope.jsonl"], 10)
    (tmp_path / "emptydir").mkdir()
    refused(env, [tmp_path / "emptydir"], 10)  # a directory without h5-ledger.jsonl
    refused(env, [good_ledger(tmp_path), tmp_path / "nope.jsonl"], 10)  # one bad file among good ones


def test_exit_empty_ledger_is_never_sent_zero(tmp_path):
    env = Env(tmp_path, prints_default())
    (tmp_path / "e0.jsonl").write_bytes(b"")
    refused(env, [tmp_path / "e0.jsonl"], 11)
    (tmp_path / "blank.jsonl").write_text("\n\n  \n")
    refused(env, [tmp_path / "blank.jsonl"], 11)
    (tmp_path / "junk.jsonl").write_text("not json\n[1]\n")
    refused(env, [tmp_path / "junk.jsonl"], 11)
    refused(env, [good_ledger(tmp_path), tmp_path / "e0.jsonl"], 11)  # one empty file among good ones


def test_exit_decision_rows_but_none_live(tmp_path):
    env = Env(tmp_path, prints_default())
    dry = write_ledger(tmp_path / "dry.jsonl", [decision(pool_of("a3"), BASE + 3, mode="dryrun"), other("fill")])
    refused(env, [dry], 12)
    nomark = decision(pool_of("a3"), BASE + 3)
    del nomark["mode"]
    refused(env, [write_ledger(tmp_path / "nomark.jsonl", [nomark])], 12)
    # a ledger with no decision row at all is a real "sent 0"
    nodec = write_ledger(tmp_path / "nodec.jsonl", [other("start"), other("fill")])
    code, text, out = env.run([nodec])
    assert code == 0 and result(out)["ws_pass"]["sent"] == 0 and result(out)["ledger"]["decision_rows"] == 0


def test_exit_usage(tmp_path):
    env = Env(tmp_path, prints_default())
    led = good_ledger(tmp_path)
    with pytest.raises(SystemExit) as e:
        S.main(["--rpc-out-dir", str(env.rpc)])
    assert e.value.code == 2
    with pytest.raises(SystemExit) as e:
        S.main(env.argv([led], tmp_path / "u.json", sha="XYZ"))
    assert e.value.code == 2
    refused(env, [led, led], 2)  # the same file twice


def test_ledger_directory_means_the_executor_file_name_only(tmp_path):
    env = Env(tmp_path, prints_default())
    live = tmp_path / "state" / "live"
    dry = tmp_path / "state" / "dryrun"
    live.mkdir(parents=True)
    dry.mkdir(parents=True)
    write_ledger(live / S.LEDGER_NAME, [decision(pool_of("a3"), BASE + 3)])
    write_ledger(dry / S.LEDGER_NAME, [decision(pool_of("a1"), BASE + 1, mode="dryrun")])
    write_ledger(live / "other.jsonl", [decision(pool_of("a1"), BASE + 1)])
    code, text, out = env.run([live])
    r = json.loads(text)
    assert code == 0 and r["ws_pass"]["sent"] == 1 and r["ws_pass"]["sent_by_rpc_status"]["not_returned"] == 1
    assert r["ledger"]["decision_rows_dryrun"] == 0 and r["hashes"]["ledger_files"][0]["sha256"] == sha_of(live / S.LEDGER_NAME)
    assert S.LEDGER_NAME == "h5-ledger.jsonl"


def test_ledger_file_is_not_modified_and_out_dir_is_not_written(tmp_path):
    env = Env(tmp_path, prints_default())
    led = good_ledger(tmp_path)
    snap = {p.name: (p.read_bytes(), os.stat(p).st_mtime_ns) for p in list(env.rpc.iterdir()) + [led]}
    assert env.run([led])[0] == 0
    assert {p.name: (p.read_bytes(), os.stat(p).st_mtime_ns) for p in list(env.rpc.iterdir()) + [led]} == snap
    assert sorted(p.name for p in env.rpc.iterdir()) == sorted(n for n in snap if n != led.name)  # no new file in the RPC out-dir


def test_no_network_and_no_other_input_is_opened(tmp_path):
    env = Env(tmp_path, prints_default())
    led = good_ledger(tmp_path)
    opened = []
    real_open = open

    def spy(file, *a, **k):
        opened.append(str(file))
        return real_open(file, *a, **k)
    with mock.patch("builtins.open", spy):
        assert env.run([led])[0] == 0
    # inputs: the shadow hour file(s), the ledger, the --out file; the RPC out-dir files are read with Path.read_bytes (not through builtins.open)
    for p in opened:
        assert p.startswith(str(tmp_path)) or p.endswith(".py") or "/proc/" in p or p.startswith("/dev"), p


def test_tool_does_not_import_or_name_forbidden_inputs():
    src = Path(S.__file__).read_text()
    code = src.split('"""', 2)[2]  # code below the docstring
    for banned in ("subprocess", "requests", "urllib", "socket", "ssh", "http_transport", "pnl", "q_out", "expected_tokens", "min_out"):
        assert banned not in code, banned
    assert "ssh" not in code
