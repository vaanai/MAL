"""tools/p35_md5_proof.py on the arena_rules testkit's synthetic tape (test mode). No real arena tape, no network. P35 #957.

The tape is synthetic and flat: nothing here says any rule makes money."""
from __future__ import annotations

import collections
import json
from pathlib import Path

import pytest

from tools import p35_md5_proof as M
from tools.arena_rule_h2ps import make_rule
from tools.arena_rules_testkit import SHA40, T0, scenario_rows, write_tape

CFG = {"slice_from": T0 - 3_600_000, "slice_to": T0 + 3_600_000, "t0": T0 + 120_000, "ho": [T0 + 390_000, T0 + 900_000], "end": T0 + 28 * 60_000,
       "test_rule": "tools.arena_rule_h2ps:make_rule", "stack_version": 1, "feed_delay_slots": 7, "sps_fixed": 0.4, "frozen_sha": SHA40,
       "code_state": [SHA40, False]}


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("MAL_ARENA_TEST", "1")
    monkeypatch.setattr("tools.arena_paper.shutil.disk_usage", lambda path: collections.namedtuple("Usage", "total used free")(1000, 500, 500))


@pytest.fixture
def tape(tmp_path):
    return write_tape(tmp_path / "srctape", scenario_rows(make_rule()))


def run(tmp_path, tape, capsys, mode="ckpt", cfg=CFG, **kw):
    rc = M.main([mode, "--scratch", str(tmp_path / "scratch"), "--tape-src", str(tape), "--test", "--test-config", json.dumps(cfg)])
    out = capsys.readouterr().out
    return rc, json.loads(out.splitlines()[0]), out


def test_ckpt_proof_passes_and_deletes_the_scratch(tmp_path, tape, capsys):
    rc, s, out = run(tmp_path, tape, capsys)
    assert rc == 0 and s["verdict"] == "PASS", s.get("error") or s["result"]["checks"]
    r = s["result"]
    assert r["R0"]["decision_md5"] == r["R1"]["decision_md5"] and r["R0"]["ledger_md5"] == r["R1"]["ledger_md5"]
    assert r["R0"]["decisions"]["entry"] == 2 and r["R0"]["ledger"]["trade"] == 6  # not vacuous
    assert [x["open_positions"] for x in r["restores"]] == [1, 1] and all(x["engine_ok"] and x["state_ok"] for x in r["restores"])
    assert s["slice"]["hours_linked"] >= 1 and s["slice"]["hours_missing"] >= 0
    left = [p for p in (tmp_path / "scratch").rglob("*")]
    assert left == [tmp_path / "scratch" / "summary.json"] and "DELETED" in out
    assert "Pool" not in out and "Mint" not in out and "sig" not in out.lower().replace("signature", "")  # counts and md5s only


def test_only_slice_hours_are_linked(tmp_path, tape):
    (tape / "tape-2026-10-25T00.jsonl").write_text("x\n")
    (tape / "tape-2026-09-25T03.jsonl").write_text("x\n")
    info = M.link_slice(tape, tmp_path / "lnk", CFG["slice_from"], CFG["slice_to"])
    names = sorted(p.name for p in (tmp_path / "lnk").iterdir())
    assert all(CFG["slice_from"] <= M.hour_of(n) < CFG["slice_to"] for n in names) and info["hours_linked"] == len(names)


def test_production_slice_pins():
    assert M.PROD["slice_from"] == M.iso_ms("2026-10-20T00:00:00Z") and M.PROD["slice_to"] == M.iso_ms("2026-10-25T00:00:00Z")
    assert M.PROD["t0"] == M.iso_ms("2026-10-21T04:00:00Z") and M.PROD["end"] == M.iso_ms("2026-10-24T23:00:00Z")
    assert M.PROD["ho"] == [M.iso_ms("2026-10-22T10:00:00Z"), M.iso_ms("2026-10-23T10:00:00Z")]
    assert M.PROD["rule"] == "l1-floor-v2" and M.PROD["stack_version"] == 2


def test_refusals(tmp_path, tape, capsys, monkeypatch):
    assert M.main(["ckpt", "--scratch", str(tmp_path / "s1"), "--tape-src", str(tape)]) == 2  # production mode, not the production tape
    assert M.main(["ckpt", "--scratch", str(tmp_path / "s2"), "--test", "--tape-src", M.PROD_TAPE]) == 2  # test mode, production tape
    with pytest.raises(M.Refused):
        M.make_scratch(str(tmp_path / "s3"), test=False)  # production scratch must be under /data2/mal/p35/bp1/
    (tmp_path / "full").mkdir()
    (tmp_path / "full" / "x").write_text("x")
    with pytest.raises(M.Refused):
        M.make_scratch(str(tmp_path / "full"), test=True)
    monkeypatch.delenv("MAL_ARENA_TEST")
    assert M.main(["ckpt", "--scratch", str(tmp_path / "s4"), "--test", "--tape-src", str(tape)]) == 2
    assert "MAL_ARENA_TEST" in capsys.readouterr().err


def test_host_proof_passes_with_a_d30_probe(tmp_path, tape, capsys):
    from tools.arena_rules_testkit import at

    cfg = dict(CFG, r3_ho=(at("sell_A") // 1000) * 1000 - 2_000)  # put the followed wallet's sell inside the 30 s down interval
    rc, s, out = run(tmp_path, tape, capsys, mode="host", cfg=cfg)
    assert rc == 0 and s["verdict"] == "PASS", s.get("error") or s["result"]["checks"]
    r = s["result"]
    assert r["C"]["decision_md5"] == r["R0"]["decision_md5"] and all(x["equal_R0"] for x in r["segments"])
    assert [x["open_at_ho"] for x in r["segments"]] == [1, 1] and r["ho_steps"] == 0
    v = r["R3"]["vs_R0"]
    assert r["R3"]["seg2"]["handoff"]["path"] == "probe" and v["n_handoff_down"] == 1 and v["n_unexplained"] == 0 and v["handoff_down_rows_ok"]
    assert r["R3"]["seg2"]["excluded_by_reason"] == {"handoff_down": 3}
    assert [p for p in (tmp_path / "scratch").rglob("*")] == [tmp_path / "scratch" / "summary.json"]
    assert "Pool" not in out and "Mint" not in out
