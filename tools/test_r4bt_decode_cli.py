"""r4bt_decode_cli: injected fake client / http_get, tmp dirs, no network. The RPC URL and key must appear in no output."""
from __future__ import annotations

import argparse
import json

import pytest

from tools import a3_redeploy_check as ARC
from tools import r4bt_a3 as A
from tools import r4bt_decode_cli as C
from tools import r4bt_decode_check as D
from tools.test_r4bt_decode_parity import clean_sample  # synthetic fixtures

KEY = "SUPERSECRETKEYVALUE123"
T0 = 1_794_100_000_000


def ns(**kw):
    base = dict(rpc_url="https://api.mainnet-beta.solana.com", rpc_env_file=None, rpc_var=None)
    return argparse.Namespace(**{**base, **kw})


def test_build_client_public_default_and_key_in_url_refused():
    assert C.build_client(ns()).url == "https://api.mainnet-beta.solana.com"
    for bad in (f"https://mainnet.helius-rpc.com/?api-key={KEY}", f"https://x.example/?api-key={KEY}"):
        with pytest.raises(C.CliRefused) as e:
            C.build_client(ns(rpc_url=bad))
        assert KEY not in str(e.value)


def test_env_file_url_is_used_and_never_printed(tmp_path, capsys):
    env = tmp_path / "helius.env"
    env.write_text(f"HELIUS_RPC_URL=https://mainnet.helius-rpc.com/?api-key={KEY}\n")
    cl = C.build_client(ns(rpc_env_file=str(env)))
    assert KEY in cl.url  # held in memory only
    bad = tmp_path / "bad.env"
    bad.write_text("NOTHING=1\n")
    assert C.main(["fetch", "--write-name", "pump", "--write-slot", "1", "--out", str(tmp_path / "o.json"), "--rpc-env-file", str(bad)]) == 11
    cap = capsys.readouterr()
    assert KEY not in cap.out + cap.err and "REFUSED" in cap.err


def test_an_rpc_failure_prints_the_class_only(tmp_path, capsys):
    class Boom:
        def call(self, *a, **k):
            raise RuntimeError(f"https://x/?api-key={KEY} down")

    assert C.main(["fetch", "--write-name", "pump", "--write-slot", "10", "--out", str(tmp_path / "o.json")], client=Boom()) == 11
    cap = capsys.readouterr()
    assert KEY not in cap.out + cap.err and "RuntimeError" in cap.err and not (tmp_path / "o.json").exists()


class BlockClient:
    """getBlock returns one tx per slot built from the synthetic sample."""

    def __init__(self, sample):
        self.sample, self.slots = sample, []

    def call(self, method, params):
        assert method == "getBlock"
        self.slots.append(params[0])
        logs = self.sample[(params[0] - self.slots[0]) % len(self.sample)]
        return {"transactions": [{"meta": {"err": None, "logMessages": logs}}]}


def test_fetch_writes_the_parity_input_from_the_pinned_start_slot_and_never_overwrites(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(ARC, "SAMPLE_MIN_TXS", 6)
    cl, out = BlockClient(clean_sample()), tmp_path / "in.json"
    argv = ["fetch", "--write-name", "pump", "--write-slot", "1000", "--out", str(out)]
    assert C.main(argv, client=cl) == 0
    doc = json.loads(out.read_text())
    assert doc["schema"] == C.INPUT_SCHEMA and doc["start_slot"] == 1000 + ARC.SAMPLE_START_OFFSET_SLOTS == cl.slots[0] and doc["n_txs"] == 6 and len(doc["sample"]) == 6
    assert json.loads(capsys.readouterr().out) == {"fetched": True, "n_txs": 6, "slots_scanned": 6, "start_slot": 1150}
    assert C.main(argv, client=BlockClient(clean_sample())) == 11  # exists


def test_parity_subcommand_pass_fail_and_bad_input(tmp_path, capsys):
    ok, bad, junk = tmp_path / "ok.json", tmp_path / "bad.json", tmp_path / "junk.json"
    ok.write_text(json.dumps({"schema": C.INPUT_SCHEMA, "sample": clean_sample() * (ARC.SAMPLE_MIN_TXS // 6 + 1)}))
    bad.write_text(json.dumps({"schema": C.INPUT_SCHEMA, "sample": clean_sample()}))  # 6 txs: below the bar, no evidence
    junk.write_text("{}")
    assert C.main(["parity", "--input", str(ok), "--out", str(tmp_path / "r.json")]) == 0
    assert json.loads((tmp_path / "r.json").read_text())["verdict"] == "PASS"
    assert C.main(["parity", "--input", str(bad)]) == 10
    assert C.main(["parity", "--input", str(junk)]) == 11
    assert "no evidence" in capsys.readouterr().out


def test_newest_write_per_program_with_the_earliest_deploy_instant():
    ws = [{"name": "pump", "slot": 150, "at_ms": 5}, {"name": "pump", "slot": 180, "at_ms": 9}, {"name": "pumpswap", "slot": 7, "at_ms": 3}]
    assert C.newest_writes(ws) == [{"name": "pump", "slot": 180, "deploy_ms": 5}, {"name": "pumpswap", "slot": 7, "deploy_ms": 3}]
    with pytest.raises(C.CliRefused):
        C.newest_writes([{"name": "pump", "slot": "x", "at_ms": 1}])


@pytest.fixture
def stubs(monkeypatch):
    big = clean_sample() * (ARC.SAMPLE_MIN_TXS // 6 + 1)
    ev = {"seen_events": list(ARC.REQUIRED_EVENTS) + ["Complete"], "undecodable": 0, "unknown": {}, "idl_mismatch": 0, "layout_mismatch": 0, "disc_len": {}, "capped": 0,
          "n_swap_events": 0}
    monkeypatch.setattr(ARC, "frozen_guard", lambda p: dict(p["frozen_blobs"]))
    monkeypatch.setattr(ARC, "fetch_idls", lambda http_get, pins: ({}, []))
    monkeypatch.setattr(ARC, "idl_event_table", lambda idls: {})
    monkeypatch.setattr(ARC, "validate_trailing_pins", lambda t: {})
    monkeypatch.setattr(ARC, "fetch_sample", lambda client, slot: (big, 100))
    monkeypatch.setattr(ARC, "evaluate_sample", lambda *a, **k: dict(ev))
    monkeypatch.setattr(ARC, "migrate_evidence", lambda *a, **k: dict(ev))
    monkeypatch.setattr(ARC, "sample_failures", lambda *a, **k: None)


def run_args(tmp_path, writes):
    (tmp_path / "w.json").write_text(json.dumps(writes))
    (tmp_path / "pins.json").write_text(json.dumps({"migrate": {"required_events": ["Complete"]}, "trailing_bytes": {}, "ignored_discs": {}}))
    (tmp_path / "r4.json").write_text(json.dumps({"frozen_sha": "a" * 40, "frozen_blobs": {"observe/trade_decode.py": "x"}}))
    return ["run", "--writes", str(tmp_path / "w.json"), "--pins", str(tmp_path / "pins.json"), "--r4bt-pins", str(tmp_path / "r4.json"), "--out", str(tmp_path / "res.json")]


def test_run_writes_the_file_the_a3_classifier_reads(tmp_path, stubs, capsys):
    ws = [{"name": "pump", "slot": 1000, "at_ms": T0 - 7_200_000}, {"name": "pump", "slot": 1100, "at_ms": T0 - 3_600_000}]
    argv = run_args(tmp_path, ws) + ["--save-input", str(tmp_path / "in.json")]
    assert C.main(argv, client=object(), http_get=None, now_ms=lambda: T0) == 0
    res = json.loads((tmp_path / "res.json").read_text())
    assert res["profile"] == A.PROFILE == D.PROFILE and [(r["name"], r["slot"], r["verdict"]) for r in res["redeploys"]] == [("pump", 1100, "PASS")]
    assert json.loads((tmp_path / "pump-in.json").read_text())["schema"] == C.INPUT_SCHEMA
    assert C.main(argv, client=object(), http_get=None, now_ms=lambda: T0) == 11  # outputs exist: never overwritten
    capsys.readouterr()


def test_run_after_24h_is_a_fail_in_the_file_and_empty_writes_are_refused(tmp_path, stubs):
    ws = [{"name": "pump", "slot": 1000, "at_ms": T0 - 25 * 3_600_000}]
    assert C.main(run_args(tmp_path, ws), client=object(), http_get=None, now_ms=lambda: T0) == 0
    assert json.loads((tmp_path / "res.json").read_text())["redeploys"][0]["verdict"] == "FAIL"


def test_run_refuses_an_empty_writes_file(tmp_path, stubs):
    assert C.main(run_args(tmp_path, []), client=object(), http_get=None, now_ms=lambda: T0) == 11
    assert not (tmp_path / "res.json").exists()
