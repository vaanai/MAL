"""r4bt_a3: synthetic A3 records, pins, samples and registries only (no real daily record, follower file or registry of a real book)."""
from __future__ import annotations

import json
import os

import pytest

from tools import r4bt_a3 as A
from tools import r4bt_gate as G
from tools.arena_paper import TEST_ENV, load_registry, register_bot as _register_bot

os.environ[TEST_ENV] = "1"
H = 3_600_000
D = 86_400_000
FLAGS = ("pins_changed", "boost_disabled", "boost_share_low", "boost_last_slice_early", "boost_budget_or_slices_changed", "synthetic_share_high")
PINS = {"pinned_utc": "2026-11-01T00:00:00Z", "accounts": {n: {"sha256": "a" * 64} for n in A.HARD_ACCOUNTS},
        "programs": {"pump": {"deploy_slot": 100}, "pumpswap": {"deploy_slot": 200}, "fees": {"deploy_slot": 300}}}
RUN = "2026-11-08T07:00:00Z"
RUN_MS = G.utc_ms(RUN)
CLEAN = "2026-11-07T07:00:00Z"
CLEAN_MS = G.utc_ms(CLEAN)
WRITE_UTC = "2026-11-08T05:00:00Z"
WRITE_MS = G.utc_ms(WRITE_UTC)


def rec(accounts=None, programs=None, flags=None, run=RUN, unevaluated=False):
    acc = {n: {"pinned": True, "sha256": "a" * 64} for n in A.HARD_ACCOUNTS}
    prog = {"pump": {"deploy_slot": 100}, "pumpswap": {"deploy_slot": 200}, "fees": {"deploy_slot": 300}}
    for n, h in (accounts or {}).items():
        acc[n].update(h if isinstance(h, dict) else {"sha256": h})
    for n, s in (programs or {}).items():
        prog[n] = {"deploy_slot": s, "deploy_utc": WRITE_UTC}
    fl = {k: {"halt": False, "evaluated": True} for k in FLAGS}
    for k in (flags or ()):
        fl[k] = {"halt": True, "evaluated": True}
    if unevaluated:
        fl["pins_changed"]["evaluated"] = False
    return {"run_utc": run, "accounts": acc, "programs": prog, "halt": {"flags": fl}}


def cl(r, now=RUN_MS + 60_000, **kw):
    return A.classify(r, PINS, now_ms=now, **kw)


def checks(*rs):
    return {"profile": A.PROFILE, "redeploys": list(rs)}


def test_clean_record_is_none():
    v = cl(rec())
    assert v["verdict"] == "NONE" and v["halt_at_ms"] is None


@pytest.mark.parametrize("name", A.HARD_ACCOUNTS)
def test_account_pin_halts_at_the_write_instant_else_the_last_clean_run_else_the_pin_date(name):
    r = rec(accounts={name: {"sha256": "b" * 64, "write_utc": WRITE_UTC}}, flags=["pins_changed"])
    assert cl(r)["halt_at_ms"] == WRITE_MS
    r = rec(accounts={name: "b" * 64}, flags=["pins_changed"])
    v = cl(r, last_clean_run_utc=CLEAN)
    assert v["verdict"] == "HALT" and v["halt_at_ms"] == CLEAN_MS and v["halt_at_ms"] != RUN_MS
    assert cl(r)["halt_at_ms"] == G.utc_ms(PINS["pinned_utc"])


def test_fees_program_halts_at_its_deploy_instant_pump_and_pumpswap_do_not_halt_by_themselves():
    v = cl(rec(programs={"fees": 301}, flags=["pins_changed"]))
    assert v["verdict"] == "HALT" and v["halt_at_ms"] == WRITE_MS
    v = cl(rec(programs={"pump": 101}, flags=["pins_changed"]), now=WRITE_MS + H)
    assert v["verdict"] == "NEEDS_CHECK" and v["halt_at_ms"] is None and v["needs_check"][0]["deadline_ms"] == WRITE_MS + A.CHECK_WINDOW_MS


def test_program_redeploy_24h_rule():
    r = rec(programs={"pump": 101}, flags=["pins_changed"])
    ok = checks({"name": "pump", "slot": 101, "verdict": "PASS", "at_ms": WRITE_MS + 10 * H})
    assert cl(r, now=WRITE_MS + 11 * H, decode_checks=ok)["verdict"] == "RECORD_ONLY"
    # no result once 24 h passed: a hard halt AT THE WRITE, never open-ended
    v = cl(r, now=WRITE_MS + 25 * H)
    assert v["verdict"] == "HALT" and v["halt_at_ms"] == WRITE_MS
    # FAIL / ERROR: the halt is the redeploy instant, not the check's
    for verdict in ("FAIL", "ERROR"):
        bad = checks({"name": "pump", "slot": 101, "verdict": verdict, "at_ms": WRITE_MS + 3 * H})
        v = cl(r, now=WRITE_MS + 4 * H, decode_checks=bad)
        assert v["verdict"] == "HALT" and v["halt_at_ms"] == WRITE_MS
    # a PASS later than 24 h, or one with no time, does not count
    for at in (WRITE_MS + 30 * H, None):
        late = checks({"name": "pump", "slot": 101, "verdict": "PASS", "at_ms": at})
        assert cl(r, now=WRITE_MS + 31 * H, decode_checks=late)["halt_at_ms"] == WRITE_MS
    # "a decode change found later": a later FAIL beside an earlier PASS for the same write halts at the write
    both = checks({"name": "pump", "slot": 101, "verdict": "PASS", "at_ms": WRITE_MS + H}, {"name": "pump", "slot": 101, "verdict": "FAIL", "at_ms": WRITE_MS + 40 * H})
    assert cl(r, now=WRITE_MS + 41 * H, decode_checks=both)["halt_at_ms"] == WRITE_MS


def test_the_decode_check_input_must_be_the_r4bt_profile():
    r = rec(programs={"pump": 101}, flags=["pins_changed"])
    h5 = {"redeploys": [{"name": "pump", "slot": 101, "verdict": "PASS", "at_ms": WRITE_MS}]}  # no profile marker: an H5-style verdict
    assert cl(r, decode_checks=h5)["verdict"] == "ERROR"
    assert cl(r, decode_checks=dict(h5, profile="a3_redeploy_check"))["verdict"] == "ERROR"


def test_follower_decoder_pin_failure_halts_at_the_first_failing_sample():
    good = {"ts_ms": RUN_MS - 3 * H, "decoder_blobs_pinned": True, "trade_event_v": True}
    bad1 = {"ts_ms": RUN_MS - 2 * H, "decoder_blobs_pinned": False, "trade_event_v": True}
    bad2 = {"ts_ms": RUN_MS - H, "decoder_blobs_pinned": True, "trade_event_v": None}
    v = cl(rec(), follower_samples=[good, bad2, bad1])
    assert v["verdict"] == "HALT" and v["halt_at_ms"] == bad1["ts_ms"]
    assert cl(rec(), follower_samples=[good])["verdict"] == "NONE"
    ref = ["b1", "b2", "b3"]
    assert cl(rec(), follower_samples=[dict(good, decoder_blobs=["b1", "b2", "b3"])], decoder_ref=ref)["verdict"] == "NONE"
    assert cl(rec(), follower_samples=[dict(good, decoder_blobs=["b1", "b2", "x"])], decoder_ref=ref)["halt_at_ms"] == good["ts_ms"]


def test_deadman_one_unevaluated_run_is_not_a_halt_two_consecutive_dates_are():
    r = lambda d, ev: {"run_utc": f"2026-11-{d:02d}T07:00:00Z", "evaluated": ev}  # noqa: E731
    now = G.utc_ms("2026-11-12T12:00:00Z")
    assert A.deadman([r(8, True), r(9, False), r(10, True)], now) is None
    assert A.deadman([r(8, True), r(9, False), r(10, False)], now) == G.utc_ms("2026-11-09T07:00:00Z")  # the first unevaluated run's run_utc
    assert A.deadman([r(8, True), r(10, True)], now) is None  # one missing date
    assert A.deadman([r(8, True), r(11, True)], now) == G.utc_ms("2026-11-09T00:00:00Z")  # two missing dates: 00:00Z of the first
    assert A.deadman([r(8, True)], G.utc_ms("2026-11-10T01:00:00Z")) is None  # 11-09 is closed and empty, 11-10 is still open
    assert A.deadman([r(8, True)], G.utc_ms("2026-11-11T01:00:00Z")) == G.utc_ms("2026-11-09T00:00:00Z")
    assert A.deadman([], now) is None


def test_deadman_through_classify_and_the_applied_log_history():
    hist = [{"run_utc": "2026-11-07T07:00:00Z", "evaluated": True}, {"run_utc": "2026-11-08T07:00:00Z", "evaluated": False}]
    one = cl(rec(run="2026-11-08T07:00:00Z", unevaluated=True), history=hist[:1], now=G.utc_ms("2026-11-08T08:00:00Z"))
    assert one["verdict"] == "NONE" and one["pins_changed_evaluated"] is False
    two = cl(rec(run="2026-11-09T07:00:00Z", unevaluated=True), history=hist, now=G.utc_ms("2026-11-09T08:00:00Z"))
    assert two["verdict"] == "HALT" and two["halt_at_ms"] == G.utc_ms("2026-11-08T07:00:00Z")


def test_boost_and_synthetic_flags_never_halt_r4bt_but_are_listed():
    v = cl(rec(flags=["boost_disabled", "boost_share_low", "boost_last_slice_early", "boost_budget_or_slices_changed", "synthetic_share_high"]))
    assert v["verdict"] == "NONE" and v["not_halting"] == sorted(A.NOT_HALTING)


def test_fail_closed_cases():
    assert cl(rec(flags=["pins_changed"]))["verdict"] == "HALT"  # flag set, no typed difference
    assert cl(rec(accounts={"bonding_fee_config": "c" * 64}))["verdict"] == "HALT"  # difference, flag not set
    r = rec()
    r["halt"]["flags"]["mystery_flag"] = {"halt": True, "evaluated": True}
    assert cl(r)["verdict"] == "HALT"
    broken = rec()
    del broken["halt"]
    assert cl(broken)["verdict"] == "ERROR" and cl(rec(run="bad"))["verdict"] == "ERROR"


def registry(tmp, name="bots.json"):
    reg = tmp / name
    _register_bot(G.BOT_ID, "a" * 40, G.CLOCK_MS, reg, deciding_stake=0.25, model_ids=[], verify_sha=False, now_ms=0)
    return reg


def test_apply_stops_both_registries_once_and_never_moves_an_earlier_stop(tmp_path):
    own, arena, log = registry(tmp_path, "own.json"), registry(tmp_path, "arena.json"), tmp_path / "applied.jsonl"
    now = RUN_MS + 60_000
    assert A.apply(cl(rec()), own, log, arena_registry=arena, now_ms=now)["actions"] == {}
    halt = cl(rec(accounts={"pumpswap_fee_config": {"sha256": "b" * 64, "write_utc": WRITE_UTC}}, flags=["pins_changed"]))
    out = A.apply(halt, own, log, arena_registry=arena, now_ms=now)
    assert set(out["actions"].values()) == {"stopped"} and len(out["actions"]) == 2
    assert all(load_registry(r)[G.BOT_ID]["stopped_at_ms"] == WRITE_MS for r in (own, arena))
    later = dict(halt, halt_at_ms=WRITE_MS + D)
    assert set(A.apply(later, own, log, arena_registry=arena, now_ms=RUN_MS + 2 * D)["actions"].values()) == {"already_stopped"}
    assert load_registry(own)[G.BOT_ID]["stopped_at_ms"] == WRITE_MS
    assert len(log.read_text().splitlines()) == 3


def test_apply_refuses_error_future_malformed_verdicts_and_the_shared_registry_as_the_own_one(tmp_path):
    reg, log = registry(tmp_path), tmp_path / "applied.jsonl"
    with pytest.raises(G.Refused, match="ERROR"):
        A.apply(cl(rec(run="bad")), reg, log, now_ms=RUN_MS)
    halt = cl(rec(flags=["pins_changed"]))
    with pytest.raises(G.Refused, match="halt_at_ms"):
        A.apply(halt, reg, log, now_ms=CLEAN_MS - 365 * D)  # an instant in the future
    with pytest.raises(G.Refused, match="carries"):
        A.apply(dict(cl(rec()), halt_at_ms=RUN_MS), reg, log, now_ms=RUN_MS + 1)
    with pytest.raises(G.Refused, match="not an r4bt_a3"):
        A.apply({"verdict": "HALT"}, reg, log, now_ms=RUN_MS)
    with pytest.raises(G.Refused, match="refused"):
        A.apply(cl(rec()), "/data/mal/arena/bots.json", log, now_ms=RUN_MS)  # the shared registry is not r4-b-T's own
    with pytest.raises(G.Refused, match="refused"):
        A.apply(cl(rec()), reg, log, arena_registry="/data/mal/arena/other.json", now_ms=RUN_MS)
    assert not log.exists()
    assert str(G.check_registry("/data/mal/arena/bots.json", allow_arena_bots=True)) == "/data/mal/arena/bots.json"  # only that file, only for the stop line


def test_cli_classify_and_apply(tmp_path):
    (tmp_path / "rec.json").write_text(json.dumps(rec(accounts={"bonding_fee_config": {"sha256": "b" * 64, "write_utc": WRITE_UTC}}, flags=["pins_changed"])))
    (tmp_path / "pins.json").write_text(json.dumps(PINS))
    argv = ["classify", "--record", str(tmp_path / "rec.json"), "--pins", str(tmp_path / "pins.json")]
    assert A.main(argv + ["--out", str(tmp_path / "v.json")], now_ms=RUN_MS + 1) == 0
    reg = registry(tmp_path)
    assert A.main(["apply", "--verdict", str(tmp_path / "v.json"), "--registry", str(reg), "--applied-log", str(tmp_path / "l.jsonl")], now_ms=RUN_MS + 1) == 0
    assert load_registry(reg)[G.BOT_ID]["stopped_at_ms"] == WRITE_MS
    assert A.main(argv + ["--out", str(tmp_path / "v.json")], now_ms=RUN_MS + 1) == 2  # an existing --out is refused
    (tmp_path / "samples.jsonl").write_text(json.dumps({"ts_ms": RUN_MS - H, "decoder_blobs_pinned": False, "trade_event_v": True}) + "\n")
    (tmp_path / "rec.json").write_text(json.dumps(rec()))
    assert A.main(["classify", "--record", str(tmp_path / "rec.json"), "--pins", str(tmp_path / "pins.json"), "--follower-samples", str(tmp_path / "samples.jsonl"),
                   "--out", str(tmp_path / "v2.json")], now_ms=RUN_MS + 1) == 0
    v2 = json.loads((tmp_path / "v2.json").read_text())
    assert v2["verdict"] == "HALT" and v2["halt_at_ms"] == RUN_MS - H
