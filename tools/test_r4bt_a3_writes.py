"""r4bt_a3_writes (QP Addendum 1 A): the classifier with per-write decode results. Synthetic record, no RPC."""
from __future__ import annotations

import json

import pytest

from tools import a3_redeploy_check as ARC
from tools import r4bt_a3 as A
from tools import r4bt_a3_writes as W

RUN = "2026-11-08T07:11:00Z"
NOW = ARC.utc_ms(RUN) + 1000
AT1, AT2 = ARC.utc_ms("2026-11-08T03:46:00Z"), ARC.utc_ms("2026-11-08T05:47:00Z")
DEADLINE = AT1 + 24 * 3_600_000
PINS = {"programs": {"pump": {"deploy_slot": 100}, "pumpswap": {"deploy_slot": 200}, "fees": {"deploy_slot": 300}}, "accounts": {}, "pinned_utc": "2026-11-01T00:00:00Z"}
WRITES = [{"name": "pump", "slot": 150, "at_ms": AT1}, {"name": "pump", "slot": 250, "at_ms": AT2}]


def rec(pump=250, pumpswap=200, fees=300):
    mk = lambda n, s: {"deploy_slot": s, "deploy_utc": "2026-11-08T05:47:00Z", "programdata": f"PD{n}"}  # noqa: E731
    return {"run_utc": RUN, "programs": {"pump": mk("pump", pump), "pumpswap": mk("pumpswap", pumpswap), "fees": mk("fees", fees)}, "accounts": {},
            "halt": {"flags": {"pins_changed": {"halt": True}}}}


def res(slot, verdict="PASS", at=NOW, mode="sample", name="pump"):
    return {"name": name, "slot": slot, "verdict": verdict, "at_ms": at, "mode": mode, "slot_range": [slot + 150, None], "reasons": []}


def checks(*rs):
    return {"profile": A.PROFILE, "redeploys": list(rs)}


def classify(rs, now=NOW, writes=WRITES, r=None, **kw):
    return W.classify_all(r or rec(), PINS, now_ms=now, writes=writes, decode_checks=checks(*rs) if rs is not None else None, **kw)


def test_both_writes_pass_is_record_only_with_the_per_write_records_and_repin_advice():
    v = classify([res(150), res(250, mode="vacuous")])
    assert v["verdict"] == "RECORD_ONLY" and v["halt_at_ms"] is None and v["repin_advice"] == W.REPIN
    assert [(p["slot"], p["status"], p["mode"]) for p in v["per_write"]] == [(150, "PASS", "sample"), (250, "PASS", "vacuous")]
    assert [(p["slot"], p["mode"]) for p in v["record_only"]] == [(150, "sample"), (250, "vacuous")]
    A.validate(v, NOW)  # `r4bt_a3 apply` accepts it unchanged


def test_the_older_write_failing_halts_at_the_older_write_even_when_the_newer_passes():
    v = classify([res(150, "FAIL"), res(250)])
    assert v["verdict"] == "HALT" and v["halt_at_ms"] == AT1 and "decode check FAIL for the pump write at slot 150" in v["reasons"][0]
    assert [p["status"] for p in v["per_write"]] == ["FAIL", "PASS"]
    A.validate(v, NOW)


def test_the_second_write_failing_halts_at_the_second_write_not_at_the_first():
    v = classify([res(150), res(250, "ERROR")])
    assert v["verdict"] == "HALT" and v["halt_at_ms"] == AT2 and v["record_only"] == []
    assert [p["status"] for p in v["per_write"]] == ["PASS", "ERROR"]


def test_both_failing_halts_at_the_earliest_failing_write():
    assert classify([res(150, "FAIL"), res(250, "FAIL")])["halt_at_ms"] == AT1


def test_a_missing_result_is_pending_inside_24h_and_a_halt_at_that_write_after():
    v = classify([res(150)])
    assert v["verdict"] == "NEEDS_CHECK" and v["halt_at_ms"] is None and v["needs_check"] == [{"name": "pump", "slot": 250, "at_ms": AT2, "deadline_ms": DEADLINE}]
    v = classify([res(150)], now=DEADLINE + 1)
    assert v["verdict"] == "HALT" and v["halt_at_ms"] == AT2 and "no decode-check result" in v["reasons"][0] and v["per_write"][1]["status"] == "MISSING"
    assert classify(None, now=DEADLINE + 1)["halt_at_ms"] == AT1  # no result at all: the earliest unreviewed write


def test_a_pass_after_24h_from_the_earliest_write_is_a_timeout_halt_at_that_write():
    v = classify([res(150), res(250, at=DEADLINE + 5)], now=DEADLINE + 10)
    assert v["verdict"] == "HALT" and v["halt_at_ms"] == AT2 and v["per_write"][1]["status"] == "LATE"


def test_a_later_failure_after_an_earlier_pass_of_the_same_write_still_halts():
    v = classify([res(150), res(150, "FAIL", at=NOW + 5), res(250)])
    assert v["verdict"] == "HALT" and v["halt_at_ms"] == AT1


def test_other_hard_rules_still_apply_and_the_earliest_instant_wins():
    r = rec()
    r["accounts"] = {"pumpswap_fee_config": {"pinned": True, "sha256": "new"}}
    p = dict(PINS, accounts={"pumpswap_fee_config": {"sha256": "old"}})
    v = W.classify_all(r, p, now_ms=NOW, writes=WRITES, decode_checks=checks(res(150), res(250)), last_clean_run_utc="2026-11-08T00:00:00Z")
    assert v["verdict"] == "HALT" and v["halt_at_ms"] == ARC.utc_ms("2026-11-08T00:00:00Z") and any("account pin" in m for m in v["reasons"])


def test_a_program_with_no_enumerated_writes_keeps_the_single_result_rule_of_r4bt_a3():
    v = classify(None, writes=[], now=NOW)  # PARTIAL writes file: the classifier's floor (pinned_utc) and its 24 h rule
    assert v["verdict"] == "HALT" and v["halt_at_ms"] == ARC.utc_ms("2026-11-01T00:00:00Z")


def test_no_program_difference_is_none_and_the_wrong_profile_is_an_error():
    clean = rec(pump=100)
    clean["halt"]["flags"]["pins_changed"]["halt"] = False
    assert W.classify_all(clean, PINS, now_ms=NOW, writes=[], decode_checks=None)["verdict"] == "NONE"
    v = W.classify_all(rec(), PINS, now_ms=NOW, writes=WRITES, decode_checks={"profile": "a3_redeploy_check.v1", "redeploys": []})
    assert v["verdict"] == "ERROR"


def test_merge_checks_concatenates_and_a_foreign_profile_wins():
    a, b = checks(res(150)), checks(res(250))
    assert [r["slot"] for r in W.merge_checks([a, b])["redeploys"]] == [150, 250] and W.merge_checks([]) is None
    assert W.merge_checks([a, {"profile": "x"}])["profile"] == "x"


def test_cli_classify_reads_the_files_refuses_an_existing_out_and_prints_the_verdict(tmp_path, capsys):
    paths = {}
    for name, obj in (("rec", rec()), ("pins", PINS), ("w", WRITES), ("c1", checks(res(150))), ("c2", checks(res(250, "FAIL")))):
        paths[name] = tmp_path / f"{name}.json"
        paths[name].write_text(json.dumps(obj))
    argv = ["classify", "--record", str(paths["rec"]), "--pins", str(paths["pins"]), "--writes", str(paths["w"]), "--decode-checks", str(paths["c1"]), "--decode-checks", str(paths["c2"]),
            "--out", str(tmp_path / "v.json")]
    assert W.main(argv, now_ms=NOW) == 0
    v = json.loads(capsys.readouterr().out)
    assert v["verdict"] == "HALT" and v["halt_at_ms"] == AT2 and json.loads((tmp_path / "v.json").read_text()) == v
    assert W.main(argv, now_ms=NOW) == 2  # --out exists
