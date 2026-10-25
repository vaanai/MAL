import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import p31_census as c  # noqa: E402


def test_burst_rows():
    sells = [(0, "m", "a"), (5, "m", "b"), (9, "m", "c"), (100, "m", "d"), (1, "n", "e"), (2, "n", "f")]
    assert c.burst_rows(sells) == {0, 1, 2}


def test_wilson_bounds():
    lo, hi = c.wilson(0, 100)
    assert lo == 0.0 and 0 < hi < 0.05
    assert c.wilson(0, 0) == (0.0, 1.0)


def test_extract_and_tags():
    tx = {"transaction": {"message": {
        "accountKeys": [{"pubkey": "KEEP", "signer": True}, {"pubkey": "USER", "signer": True}],
        "instructions": [
            {"programId": "Memo1UhkJRfHyvLMcVucJwxXeuD728EqVDDwQDxFMNo", "parsed": "pump-limit:v3:stop_loss:5500:1"},
            {"programId": "11111111111111111111111111111111", "parsed": {"type": "transfer", "info": {"source": "KEEP", "destination": "FEE", "lamports": 5000}}},
            {"programId": "PROG"}]}},
        "meta": {"err": None, "logMessages": ["Program log: Instruction: CollectCreatorFee"], "innerInstructions": []}}
    r = c.extract(tx, "USER")
    r.update(stratum="A", day="2026-09-04", venue="pumpswap")
    assert r["ok"] and r["payer"] == "KEEP" and not r["payer_is_trader"]
    tg = set(c.tags_for(r))
    assert ("K1", "KEEP") in tg and ("K2", "FEE") in tg and ("K3", "pump-limit:v#:stop_loss:#:#") in tg
    assert ("K4", "CollectCreatorFee") in tg and ("K5", "PROG") in tg


def test_extract_error():
    assert c.extract({"_err": "URLError"}, "x")["why"] == "URLError"
    assert c.extract(None, "x")["why"] == "null"
