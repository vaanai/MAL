"""r4bt_a3: synthetic A3 records and pins only (no real daily record, no follower file, no registry of a real book)."""
from __future__ import annotations

import json
import os

import pytest

from tools import r4bt_a3 as A
from tools import r4bt_gate as G
from tools.arena_paper import TEST_ENV, load_registry, register_bot as _register_bot

os.environ[TEST_ENV] = "1"
FLAGS = ("pins_changed", "boost_disabled", "boost_share_low", "boost_last_slice_early", "boost_budget_or_slices_changed", "synthetic_share_high")
PINS = {"accounts": {n: {"sha256": "a" * 64} for n in A.HARD_ACCOUNTS}, "programs": {"pump": {"deploy_slot": 100}, "pumpswap": {"deploy_slot": 200}, "fees": {"deploy_slot": 300}}}
RUN = "2026-11-08T07:00:00Z"
RUN_MS = G.utc_ms(RUN)


def rec(accounts=None, programs=None, flags=None, run=RUN):
    acc = {n: {"pinned": True, "sha256": "a" * 64} for n in A.HARD_ACCOUNTS}
    prog = {"pump": {"deploy_slot": 100}, "pumpswap": {"deploy_slot": 200}, "fees": {"deploy_slot": 300}}
    for n, h in (accounts or {}).items():
        acc[n]["sha256"] = h
    for n, s in (programs or {}).items():
        prog[n] = {"deploy_slot": s, "deploy_utc": "2026-11-08T05:00:00Z"}
    fl = {k: {"halt": False, "evaluated": True} for k in FLAGS}
    for k in (flags or ()):
        fl[k] = {"halt": True, "evaluated": True}
    return {"run_utc": run, "accounts": acc, "programs": prog, "halt": {"flags": fl}}


def test_clean_record_is_none():
    v = A.classify(rec(), PINS)
    assert v["verdict"] == "NONE" and v["halt_at_ms"] is None


@pytest.mark.parametrize("name", A.HARD_ACCOUNTS)
def test_account_pin_change_is_a_hard_halt_at_the_run_instant(name):
    v = A.classify(rec(accounts={name: "b" * 64}, flags=["pins_changed"]), PINS)
    assert v["verdict"] == "HALT" and v["halt_at_ms"] == RUN_MS and any(name in r for r in v["reasons"])


def test_fees_program_redeploy_is_hard_but_pump_and_pumpswap_are_record_only():
    assert A.classify(rec(programs={"fees": 301}, flags=["pins_changed"]), PINS)["verdict"] == "HALT"
    v = A.classify(rec(programs={"pump": 101, "pumpswap": 201}, flags=["pins_changed"]), PINS)
    assert v["verdict"] == "NEEDS_CHECK" and v["halt_at_ms"] is None and len(v["needs_check"]) == 2


def test_decode_check_pass_is_record_only_fail_ends_the_window_at_the_check_instant():
    r = rec(programs={"pump": 101}, flags=["pins_changed"])
    ok = {"redeploys": [{"name": "pump", "slot": 101, "verdict": "PASS"}]}
    assert A.classify(r, PINS, decode_checks=ok)["verdict"] == "RECORD_ONLY"
    at = RUN_MS - 3_600_000
    bad = {"redeploys": [{"name": "pump", "slot": 101, "verdict": "FAIL", "at_ms": at}]}
    v = A.classify(r, PINS, decode_checks=bad)
    assert v["verdict"] == "DECODE_FAIL" and v["halt_at_ms"] == at
    err = {"redeploys": [{"name": "pump", "slot": 101, "verdict": "ERROR"}]}
    assert A.classify(r, PINS, decode_checks=err)["halt_at_ms"] == RUN_MS


def test_boost_and_synthetic_flags_never_halt_r4bt_but_are_listed():
    v = A.classify(rec(flags=["boost_disabled", "boost_share_low", "boost_last_slice_early", "boost_budget_or_slices_changed", "synthetic_share_high"]), PINS)
    assert v["verdict"] == "NONE" and v["not_halting"] == sorted(A.NOT_HALTING)


def test_fail_closed_cases():
    assert A.classify(rec(flags=["pins_changed"]), PINS)["verdict"] == "HALT"  # flag set, no typed difference
    assert A.classify(rec(accounts={"bonding_fee_config": "c" * 64}), PINS)["verdict"] == "HALT"  # difference, flag not set
    r = rec()
    r["halt"]["flags"]["pins_changed"]["evaluated"] = False
    assert A.classify(r, PINS)["verdict"] == "HALT"
    r = rec()
    r["halt"]["flags"]["mystery_flag"] = {"halt": True, "evaluated": True}
    assert A.classify(r, PINS)["verdict"] == "HALT"
    assert A.classify(rec(), PINS, follower_decoder_pinned=False)["verdict"] == "HALT"
    assert A.classify(rec(), PINS, follower_decoder_pinned=True)["verdict"] == "NONE"
    broken = rec()
    del broken["halt"]
    assert A.classify(broken, PINS)["verdict"] == "ERROR"
    assert A.classify(rec(run="bad"), PINS)["verdict"] == "ERROR"


def registry(tmp):
    reg = tmp / "bots.json"
    _register_bot(G.BOT_ID, "a" * 40, G.CLOCK_MS, reg, deciding_stake=0.25, model_ids=[], verify_sha=False, now_ms=0)
    return reg


def test_apply_stops_once_and_never_moves_an_earlier_stop(tmp_path):
    reg, log = registry(tmp_path), tmp_path / "applied.jsonl"
    now = RUN_MS + 60_000
    none = A.classify(rec(), PINS)
    assert A.apply(none, reg, log, now_ms=now)["action"] == "none" and load_registry(reg)[G.BOT_ID]["stopped_at_ms"] is None
    halt = A.classify(rec(accounts={"pumpswap_fee_config": "b" * 64}, flags=["pins_changed"]), PINS)
    assert A.apply(halt, reg, log, now_ms=now)["action"] == "stopped" and load_registry(reg)[G.BOT_ID]["stopped_at_ms"] == RUN_MS
    later = dict(halt, halt_at_ms=RUN_MS + 86_400_000)
    assert A.apply(later, reg, log, now_ms=RUN_MS + 2 * 86_400_000)["action"] == "already_stopped"
    assert load_registry(reg)[G.BOT_ID]["stopped_at_ms"] == RUN_MS
    assert len(log.read_text().splitlines()) == 3


def test_apply_refuses_error_future_and_malformed_verdicts(tmp_path):
    reg, log = registry(tmp_path), tmp_path / "applied.jsonl"
    with pytest.raises(G.Refused, match="ERROR"):
        A.apply(A.classify(rec(run="bad"), PINS), reg, log, now_ms=RUN_MS)
    halt = A.classify(rec(flags=["pins_changed"]), PINS)
    with pytest.raises(G.Refused, match="halt_at_ms"):
        A.apply(halt, reg, log, now_ms=RUN_MS - 3_600_000)  # an instant in the future
    with pytest.raises(G.Refused, match="carries"):
        A.apply(dict(A.classify(rec(), PINS), halt_at_ms=RUN_MS), reg, log, now_ms=RUN_MS + 1)
    with pytest.raises(G.Refused, match="not an r4bt_a3"):
        A.apply({"verdict": "HALT"}, reg, log, now_ms=RUN_MS)
    with pytest.raises(G.Refused, match="closed|refused"):
        A.apply(A.classify(rec(), PINS), "/data/mal/arena/bots.json", log, now_ms=RUN_MS)
    assert not log.exists()


def test_cli_classify_and_apply(tmp_path):
    (tmp_path / "rec.json").write_text(json.dumps(rec(accounts={"bonding_fee_config": "b" * 64}, flags=["pins_changed"])))
    (tmp_path / "pins.json").write_text(json.dumps(PINS))
    (tmp_path / "fs.json").write_text(json.dumps({"decoder_blobs_pinned": True}))
    argv = ["classify", "--record", str(tmp_path / "rec.json"), "--pins", str(tmp_path / "pins.json"), "--follower-status", str(tmp_path / "fs.json")]
    assert A.main(argv + ["--out", str(tmp_path / "v.json")]) == 0
    reg = registry(tmp_path)
    assert A.main(["apply", "--verdict", str(tmp_path / "v.json"), "--registry", str(reg), "--applied-log", str(tmp_path / "l.jsonl")], now_ms=RUN_MS + 1) == 0
    assert load_registry(reg)[G.BOT_ID]["stopped_at_ms"] == RUN_MS
    assert A.main(argv + ["--out", str(tmp_path / "v.json")]) == 2  # an existing --out is refused
    (tmp_path / "rec.json").write_text(json.dumps(rec()))
    (tmp_path / "fs.json").write_text(json.dumps({"decoder_blobs_pinned": "yes"}))  # present, not a boolean: fail closed (HALT)
    assert A.main(argv + ["--out", str(tmp_path / "v2.json")]) == 0
    assert json.loads((tmp_path / "v2.json").read_text())["verdict"] == "HALT"
