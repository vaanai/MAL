"""r4bt_c0_a3_carry: synthetic daily records, verdicts, follower samples only (tools.test_r4bt_a3 builders). Round trips through the executor's own carry reader (a3_problem)."""
from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tools import r4bt_c0_a3_carry as C
from tools import r4bt_c0_executor as T
from tools import test_r4bt_a3 as TA
from tools.test_r4bt_c0_executor import CID, World

NOW = TA.RUN_MS + 60_000
REF = ["blob_a", "blob_b", "blob_c"]
REPO = Path(__file__).resolve().parent.parent
SH = str(REPO / "scripts/ops/r4bt-c0-a3-carry.sh")


def R(**kw):
    """The test_r4bt_a3 record plus the monitor's warn block (program_changed)."""
    r = TA.rec(**kw)
    r["warn"] = {"flags": {"program_changed": {"warn": False}}}
    return r


def sample(ts_ms):
    return {"ts_ms": ts_ms, "decoder_blobs_pinned": True, "trade_event_v": True, "decoder_blobs": REF}


def v_of(kind, run=TA.RUN):
    return {"schema": "r4bt_a3_v1", "run_utc": run, "verdict": kind, "hard": []}


class Case:
    """A tmp dir holding daily, record snapshot, verdict, samples, decoder ref, out dir."""

    def __init__(self, tmp, recs=None, verdict=None, samples=None, snapshot=None):
        recs = recs if recs is not None else [R()]
        self.tmp = tmp
        self.out = str(tmp / "carry.json")
        self.daily = tmp / "daily.jsonl"
        self.daily.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
        self.record = tmp / "record.json"
        self.record.write_text(snapshot if snapshot is not None else json.dumps(recs[-1]) + "\n")
        self.verdict = tmp / "verdict.json"
        self.verdict.write_text(json.dumps(verdict if verdict is not None else TA.cl(recs[-1])))
        self.samples = tmp / "samples.jsonl"
        self.samples.write_text("\n".join(json.dumps(s) for s in (samples if samples is not None else [sample(TA.RUN_MS - 1000)])) + "\n")
        self.ref = tmp / "ref.json"
        self.ref.write_text(json.dumps(REF))

    def run(self, now=NOW, cid=CID, rc=0, final=False):
        return C.run(daily=str(self.daily), record=str(self.record), verdict=str(self.verdict), samples=str(self.samples), decoder_ref=str(self.ref), change_id=cid, out=self.out,
                     now_ms=now, classify_rc=rc, final=final)

    def args(self, *extra):
        return ["--daily", str(self.daily), "--record", str(self.record), "--verdict", str(self.verdict), "--follower-samples", str(self.samples), "--decoder-ref", str(self.ref),
                "--change-id", CID, "--out", self.out, *extra]


def reader(tmp_path, out, now=NOW):
    import tempfile

    w = World(Path(tempfile.mkdtemp(dir=tmp_path)))
    return T.a3_problem(T.make_config({**w.d, "a3_carry_path": out}), now, False)


@pytest.fixture
def tp(tmp_path):
    (tmp_path / "world").mkdir()
    return tmp_path


def test_clean_round_trip_and_fields(tp):
    c = Case(tp, [R(run="2026-11-07T07:00:00Z"), R()])
    carry, why = c.run()
    assert why is None and carry["hard_halt"] is False and carry["hard_halt_reasons"] == [] and carry["verdict"] == "NONE" and carry["skipped_flags"] == []
    assert carry["run_utc"] == TA.RUN and "pins_changed" in carry["flags_evaluated"]
    assert reader(tp, c.out) == (None, [])
    assert stat.S_IMODE(os.stat(c.out).st_mode) == 0o644 and not [p for p in os.listdir(tp) if p.endswith(".tmp")]
    s = carry["source"]
    assert s["daily_sha256"] == hashlib.sha256(c.daily.read_bytes()).hexdigest()
    assert s["record_sha256"] == hashlib.sha256(json.dumps(R()).encode()).hexdigest()
    assert s["verdict_sha256"] == hashlib.sha256(c.verdict.read_bytes()).hexdigest()
    assert s["follower_samples_sha256"] == hashlib.sha256(c.samples.read_bytes()).hexdigest() and s["newest_sample_ts_ms"] == TA.RUN_MS - 1000


def test_boost_and_synthetic_flags_are_skipped_and_counted_not_halts(tp):
    c = Case(tp, [R(flags=T.NOT_HALTING_FLAGS)])
    carry, why = c.run()
    assert why is None and carry["hard_halt"] is False and carry["skipped_flags"] == sorted(T.NOT_HALTING_FLAGS) and set(carry["skipped_counts"].values()) == {1}
    assert reader(tp, c.out) == (None, sorted(T.NOT_HALTING_FLAGS))


def test_account_pin_halt_latches_in_the_executor(tp):
    c = Case(tp, [R(accounts={"bonding_fee_config": "b" * 64}, flags=("pins_changed",))])
    carry, why = c.run()
    assert why is None and carry["verdict"] == "HALT" and carry["hard_halt"] is True
    assert "verdict:HALT" in carry["hard_halt_reasons"] and "flag:pins_changed" in carry["hard_halt_reasons"]
    assert reader(tp, c.out)[0] == "a3_carry:HALT"


def test_unevaluated_flag_and_program_changed_warn_latch(tp):
    c = Case(tp, [R(unevaluated=True)], verdict=v_of("NEEDS_CHECK"))
    carry, _ = c.run()
    assert carry["hard_halt"] is True and "flag:pins_changed" in carry["hard_halt_reasons"]
    assert reader(tp, c.out)[0] == "a3_carry:NEEDS_CHECK"
    r2 = R()
    r2["warn"] = {"flags": {"program_changed": {"warn": True}}}
    b = tp / "b"
    b.mkdir()
    (b / "world").mkdir()
    c2 = Case(b, [r2], verdict=v_of("RECORD_ONLY"))
    carry2, _ = c2.run()
    assert carry2["hard_halt"] is True and carry2["hard_halt_reasons"] == ["warn:program_changed"]
    assert reader(b, c2.out)[0] == "a3_flags:program_changed"


def _bad_flag():
    r = R()
    r["halt"]["flags"]["pins_changed"]["evaluated"] = "yes"
    return r


def _no_halt():
    r = R()
    del r["halt"]
    return r


def _no_warn():
    r = TA.rec()
    return r


GOOD = R()
# (name, Case kwargs, run kwargs): EVERY one must write a carry that the executor latches (a3_carry:ERROR), never leave the old carry.
REFUSALS = [
    ("classifier_error_rc11", {}, {"rc": 11}),
    ("stale_record", {}, {"now": TA.RUN_MS + 37 * 3_600_000}),
    ("future_record", {}, {"now": TA.RUN_MS - 301_000}),
    ("verdict_run_utc_differs", {"verdict": dict(TA.cl(GOOD), run_utc="2026-11-08T06:00:00Z")}, {}),
    ("verdict_schema", {"verdict": dict(TA.cl(GOOD), schema="other")}, {}),
    ("unknown_verdict", {"verdict": dict(TA.cl(GOOD), verdict="MAYBE")}, {}),
    ("verdict_lists_hard", {"verdict": dict(TA.cl(GOOD), hard=[{"x": 1}])}, {}),
    ("clear_verdict_beside_hard_flag", {"recs": [R(flags=("pins_changed",))], "verdict": v_of("NONE")}, {}),
    ("duplicate_run_utc", {"recs": [dict(GOOD, rpc_host="x"), GOOD]}, {}),
    ("no_halt_flags", {"recs": [_no_halt()], "verdict": v_of("NONE")}, {}),
    ("non_bool_flag", {"recs": [_bad_flag()], "verdict": v_of("NONE")}, {}),
    ("no_program_changed_warn", {"recs": [_no_warn()], "verdict": v_of("NONE")}, {}),
    ("unparseable_newest_line", {"recs": [GOOD], "snapshot": "{not json\n"}, {"newest_bad": True}),
    ("no_follower_sample", {"samples": []}, {}),
    ("follower_sample_without_ts", {"samples": [{"decoder_blobs_pinned": True}]}, {}),
    ("stale_follower_sample", {"samples": [sample(TA.RUN_MS - 37 * 3_600_000)]}, {}),
]


@pytest.mark.parametrize("name,ckw,rkw", REFUSALS, ids=[r[0] for r in REFUSALS])
def test_every_refusal_writes_a_latching_carry_and_never_leaves_the_old_one(tp, name, ckw, rkw):
    c = Case(tp, **{k: v for k, v in ckw.items() if k != "recs"}, recs=ckw.get("recs"))
    if rkw.get("newest_bad"):
        c.daily.write_text(json.dumps(GOOD) + "\n{not json\n")
    Path(c.out).write_text("OLD CLEAN CARRY")
    now, rc = rkw.get("now", NOW), rkw.get("rc", 0)
    carry, why = c.run(now=now, rc=rc)
    assert why and carry["verdict"] == "ERROR" and carry["hard_halt"] is True and carry["hard_halt_reasons"] == ["refused:" + why]
    assert json.loads(Path(c.out).read_text()) == carry and (carry["schema"], carry["bot_id"], carry["change_id"]) == (T.CARRY_SCHEMA, T.BOT_ID, CID)
    assert reader(tp, c.out, now=NOW if now == NOW else now)[0] in ("a3_carry:ERROR", "a3_carry_stale", "a3_carry_future")
    if name not in ("stale_record", "future_record"):
        assert reader(tp, c.out)[0] == "a3_carry:ERROR"


def test_missing_verdict_file_latches(tp):
    c = Case(tp)
    c.verdict.unlink()
    carry, why = c.run(rc=2)
    assert why and reader(tp, c.out)[0] == "a3_carry:ERROR"
    carry, why = c.run(rc=0)
    assert why and "verdict.json" in why and reader(tp, c.out)[0] == "a3_carry:ERROR"


def test_newer_line_after_the_snapshot_writes_nothing_unless_final(tp):
    c = Case(tp)
    c.daily.write_text(c.daily.read_text() + json.dumps(R(run="2026-11-08T08:00:00Z")) + "\n")
    Path(c.out).write_text("OLD")
    with pytest.raises(C.NewerLine):
        c.run()
    assert Path(c.out).read_text() == "OLD"
    assert C.main(c.args("--now-ms", str(NOW))) == 3
    carry, why = c.run(final=True)
    assert why and reader(tp, c.out)[0] == "a3_carry:ERROR"


def test_trailing_blank_lines_do_not_break_the_snapshot_check(tp):
    c = Case(tp)
    c.daily.write_text(c.daily.read_text() + "\n\n")
    assert c.run()[1] is None


def test_unregistered_change_id_writes_nothing(tp):
    c = Case(tp)
    Path(c.out).write_text("OLD")
    with pytest.raises(C.NoIdentity):
        c.run(cid="r4bt-a1-nope")
    assert Path(c.out).read_text() == "OLD"


def test_cli_exit_codes(tp, capsys):
    c = Case(tp)
    assert C.main(c.args("--now-ms", str(NOW))) == 0
    assert json.loads(capsys.readouterr().out)["hard_halt"] is False
    assert C.main(c.args("--now-ms", str(TA.RUN_MS + 40 * 3_600_000))) == 2
    assert json.loads(Path(c.out).read_text())["verdict"] == "ERROR"
    assert C.main(c.args("--now-ms", str(NOW), "--classify-rc", "11")) == 2
    bad = c.args()
    bad[bad.index("--change-id") + 1] = "nope"
    assert C.main(bad + ["--now-ms", str(NOW)]) == 2


def _env(tp, run):
    daily, pins, samples, ref = tp / "daily.jsonl", tp / "pins.json", tp / "samples.jsonl", tp / "ref.json"
    pins.write_text(json.dumps(TA.PINS))
    samples.write_text(json.dumps(sample(int((time.time() - 600) * 1000))) + "\n")
    ref.write_text(json.dumps(REF))
    return daily, {"PATH": os.environ["PATH"], "HOME": str(tp), "R4BT_REPO": str(REPO), "R4BT_PY": sys.executable, "R4BT_DAILY": str(daily), "R4BT_PINS": str(pins),
                   "R4BT_CHANGE_ID": CID, "R4BT_CARRY_OUT": str(tp / "carry.json"), "R4BT_WORK": str(tp / "work"), "R4BT_FOLLOWER_SAMPLES": str(samples),
                   "R4BT_DECODER_REF": str(ref)}


def sh(env):
    return subprocess.run(["sh", SH], env=env, capture_output=True, text=True)


def test_wrapper_end_to_end_clean_then_every_failure_latches(tp):
    run = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 3600))
    daily, env = _env(tp, run)
    out = tp / "carry.json"
    daily.write_text(json.dumps(R(run=run)) + "\n\n")  # a trailing blank line must not produce an empty snapshot
    p = sh(env)
    assert p.returncode == 0, p.stdout + p.stderr
    c = json.loads(out.read_text())
    assert c["run_utc"] == run and c["verdict"] == "NONE" and c["hard_halt"] is False and c["source"]["newest_sample_ts_ms"]
    # classifier ERROR (exit 11): the carry tool still runs and latches
    daily.write_text(json.dumps(R(run="not-a-time")) + "\n")
    p = sh(env)
    assert p.returncode == 2 and "R4BT_CLASSIFY_RC=11" in p.stdout, p.stdout + p.stderr
    assert json.loads(out.read_text())["verdict"] == "ERROR"
    # malformed newest line after a clean one: latches, not the earlier record
    out.write_text("OLD")
    daily.write_text(json.dumps(R(run=run)) + "\n{broken\n")
    assert sh(env).returncode == 2 and json.loads(out.read_text())["verdict"] == "ERROR"
    # missing required follower samples variable: setup error, nothing written
    out.write_text("OLD")
    e2 = {k: v for k, v in env.items() if k != "R4BT_FOLLOWER_SAMPLES"}
    assert sh(e2).returncode == 1 and out.read_text() == "OLD"
    e3 = {k: v for k, v in env.items() if k != "R4BT_DECODER_REF"}
    assert sh(e3).returncode == 1
    # old record/verdict files are cleaned (older than 2 days), fresh ones kept
    work = tp / "work"
    old = work / "verdict-old.json"
    old.write_text("x")
    os.utime(old, (time.time() - 5 * 86400,) * 2)
    daily.write_text(json.dumps(R(run=run)) + "\n")
    assert sh(env).returncode == 0 and not old.exists() and list(work.glob("record-*"))
