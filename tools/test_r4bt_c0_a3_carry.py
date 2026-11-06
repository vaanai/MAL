"""r4bt_c0_a3_carry: synthetic daily records and verdicts only (tools.test_r4bt_a3 builders). Round trip through the executor's own carry reader (a3_problem)."""
from __future__ import annotations

import hashlib
import json
import os
import stat

import pytest

from tools import r4bt_c0_a3_carry as C
from tools import r4bt_c0_executor as T
from tools import test_r4bt_a3 as TA
from tools.test_r4bt_c0_executor import CID, World

NOW = TA.RUN_MS + 60_000


def R(**kw):
    """The test_r4bt_a3 record plus the monitor's warn block (program_changed)."""
    r = TA.rec(**kw)
    r["warn"] = {"flags": {"program_changed": {"warn": False}}}
    return r


def sub(tmp_path, name):
    p = tmp_path / name
    p.mkdir()
    return p


def setup(tmp_path, recs, verdict=None):
    daily = tmp_path / "daily.jsonl"
    daily.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
    vp = tmp_path / "verdict.json"
    vp.write_text(json.dumps(verdict if verdict is not None else TA.cl(recs[-1])))
    out = tmp_path / "out"
    out.mkdir(exist_ok=True)
    return str(daily), str(vp), str(out / "carry.json")


def go(tmp_path, recs, verdict=None, now=NOW, cid=CID):
    d, v, out = setup(tmp_path, recs, verdict)
    return C.run(d, v, cid, out, now), out


def reader(tmp_path, out, now=NOW):
    w = World(sub(tmp_path, "world"))
    return T.a3_problem(T.make_config({**w.d, "a3_carry_path": out}), now, False)


def v_of(kind):
    return {"schema": "r4bt_a3_v1", "run_utc": TA.RUN, "verdict": kind, "hard": []}


def test_clean_round_trip_and_fields(tmp_path):
    c, out = go(tmp_path, [R(run="2026-11-07T07:00:00Z"), R()])
    assert c["hard_halt"] is False and c["hard_halt_reasons"] == [] and c["verdict"] == "NONE" and c["skipped_flags"] == []
    assert c["run_utc"] == TA.RUN and "pins_changed" in c["flags_evaluated"]
    assert reader(tmp_path, out) == (None, [])
    assert stat.S_IMODE(os.stat(out).st_mode) == 0o644
    assert not [p for p in os.listdir(os.path.dirname(out)) if p.endswith(".tmp")]
    s = c["source"]
    assert s["daily_sha256"] == hashlib.sha256((tmp_path / "daily.jsonl").read_bytes()).hexdigest()
    assert s["record_sha256"] == hashlib.sha256(json.dumps(R()).encode()).hexdigest()
    assert s["verdict_sha256"] == hashlib.sha256((tmp_path / "verdict.json").read_bytes()).hexdigest()


def test_boost_and_synthetic_flags_are_skipped_and_counted_not_halts(tmp_path):
    c, out = go(tmp_path, [R(flags=T.NOT_HALTING_FLAGS)])
    assert c["hard_halt"] is False and c["skipped_flags"] == sorted(T.NOT_HALTING_FLAGS) and set(c["skipped_counts"].values()) == {1}
    assert reader(tmp_path, out) == (None, sorted(T.NOT_HALTING_FLAGS))


def test_account_pin_halt_latches_in_the_executor(tmp_path):
    c, out = go(tmp_path, [R(accounts={"bonding_fee_config": "b" * 64}, flags=("pins_changed",))])
    assert c["verdict"] == "HALT" and c["hard_halt"] is True
    assert "verdict:HALT" in c["hard_halt_reasons"] and "flag:pins_changed" in c["hard_halt_reasons"]
    assert reader(tmp_path, out)[0] == "a3_carry:HALT"


def test_unevaluated_flag_and_program_changed_warn_latch(tmp_path):
    c, out = go(tmp_path, [R(unevaluated=True)], verdict=v_of("NEEDS_CHECK"))
    assert c["hard_halt"] is True and "flag:pins_changed" in c["hard_halt_reasons"]
    assert reader(tmp_path, out)[0] == "a3_carry:NEEDS_CHECK"
    r2 = R()
    r2["warn"] = {"flags": {"program_changed": {"warn": True}}}
    b = sub(tmp_path, "b")
    c2, out2 = go(b, [r2], verdict=v_of("RECORD_ONLY"))
    assert c2["hard_halt"] is True and c2["hard_halt_reasons"] == ["warn:program_changed"]
    assert reader(b, out2)[0] == "a3_flags:program_changed"


@pytest.mark.parametrize("now,msg", [(TA.RUN_MS + 37 * 3_600_000, "older than 36 h"), (TA.RUN_MS - 301_000, "future")])
def test_stale_and_future_records_are_refused(tmp_path, now, msg):
    with pytest.raises(C.Refused, match=msg):
        go(tmp_path, [R()], now=now)


def test_ambiguity_refusals(tmp_path):
    good = R()
    cases = [
        ("run_utc differs", dict(TA.cl(good), run_utc="2026-11-08T06:00:00Z")),
        ("schema", dict(TA.cl(good), schema="other")),
        ("unknown verdict", dict(TA.cl(good), verdict="MAYBE")),
        ("disagree", dict(TA.cl(good), hard=[{"x": 1}])),
    ]
    for i, (msg, verdict) in enumerate(cases):
        with pytest.raises(C.Refused, match=msg):
            go(sub(tmp_path, f"c{i}"), [good], verdict=verdict)
    with pytest.raises(C.Refused, match="share the latest run_utc"):
        go(sub(tmp_path, "dup"), [dict(good, rpc_host="x"), good])
    with pytest.raises(C.Refused, match="inputs disagree"):
        go(sub(tmp_path, "clearbut"), [R(flags=("pins_changed",))], verdict=v_of("NONE"))
    with pytest.raises(C.Refused, match="not registered"):
        go(sub(tmp_path, "id"), [good], cid="r4bt-a1-nope")


def test_malformed_inputs_refuse_and_leave_the_old_carry(tmp_path):
    d, v, out = setup(tmp_path, [R()])
    with open(out, "w") as f:
        f.write("OLD")
    with open(d, "a") as f:
        f.write("{not json\n")
    with pytest.raises(C.Refused, match="not JSON"):
        C.run(d, v, CID, out, NOW)
    nf = R()
    del nf["halt"]
    d2, v2, _ = setup(sub(tmp_path, "x"), [nf], verdict=v_of("NONE"))
    with pytest.raises(C.Refused, match="no halt.flags"):
        C.run(d2, v2, CID, out, NOW)
    bad = R()
    bad["halt"]["flags"]["pins_changed"]["evaluated"] = "yes"
    d3, v3, _ = setup(sub(tmp_path, "y"), [bad], verdict=v_of("NONE"))
    with pytest.raises(C.Refused, match="non-bool"):
        C.run(d3, v3, CID, out, NOW)
    assert open(out).read() == "OLD"


def test_cli_exit_codes(tmp_path, capsys):
    d, v, out = setup(tmp_path, [R()])
    assert C.main(["--daily", d, "--verdict", v, "--change-id", CID, "--out", out, "--now-ms", str(NOW)]) == 0
    assert json.loads(capsys.readouterr().out)["hard_halt"] is False
    assert C.main(["--daily", d, "--verdict", v, "--change-id", CID, "--out", out, "--now-ms", str(TA.RUN_MS + 40 * 3_600_000)]) == 2
    assert C.main(["--daily", d + "x", "--verdict", v, "--change-id", CID, "--out", out]) == 2
