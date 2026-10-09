"""Tests for tools/h5_synthetic_audit.py. No network: the RPC is the recorded-fixture fake of test_synthetic_class."""
from __future__ import annotations

import io
import json
from datetime import datetime, timezone

import pytest

from tools import h5_synthetic_audit as au
from tools import synthetic_class as sc
from tools.test_synthetic_class import CASES, FX, FakeRpc, _entry

DAY = "2026-10-12"
TS = int(datetime(2026, 10, 12, 13, 0, tzinfo=timezone.utc).timestamp() * 1000)
SECRETS = ("FILLPRICE-9911", "PNL-7733", "SIZE-5522", "EXITSIG-4410")


def multi_rpc(*names: str) -> FakeRpc:
    lists, txs = {}, {}
    for n in names:
        c = CASES[n]
        lists[c["pool"]] = [_entry(c["boundary_sig"])] + c["pool_sigs"]
        lists[c["curve"]] = [_entry(c["migrate_sig"])] + c["curve_sigs"]
    return FakeRpc(FX["txs"], lists)


def ledger_line(name: str, *, synthetic, kind="decision", ts=TS, **extra) -> str:
    c = CASES[name]
    row = {"schema": "h5_ledger_v1", "mode": "live", "kind": kind, "ts_ms": ts, "mint": c["mint"], "book": "h5", "pool": c["pool"],
           "synthetic": synthetic, "synthetic_src": "ws", "signature": c["boundary_sig"],
           "stake_lamports": 20_000_000, "expected_tokens": 123, "min_out": 99, "plan": {"pool": "PLAN-POOL", "exit_price": SECRETS[0], "nested": {"signature": SECRETS[3]}},
           "fill_price": SECRETS[0], "pnl_lamports": SECRETS[1], "size": SECRETS[2], "anchor": {"mint": "ANCHOR-MINT", "slot": 5}, **extra}
    return json.dumps(row)


def run(monkeypatch, capsys, argv, rpc=None, stdin=""):
    monkeypatch.setattr("sys.stdin", io.StringIO(stdin))
    code = au.main(argv, rpc=rpc)
    cap = capsys.readouterr()
    return code, cap.out, cap.err


def test_output_keys_are_exactly_the_allowed_set_and_nothing_else_is_printed(monkeypatch, capsys):
    ledger = "\n".join([ledger_line("non_synthetic_1", synthetic=False), ledger_line("synthetic_2", synthetic=False)])
    code, out, err = run(monkeypatch, capsys, ["--date", DAY, "--ledger", "-"], multi_rpc("non_synthetic_1", "synthetic_2"), ledger)
    lines = out.strip().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert tuple(rec) == au.OUTPUT_KEYS == ("date", "n_pools", "n_disagree", "n_unclassified_now", "halt")
    assert rec == {"date": DAY, "n_pools": 2, "n_disagree": 1, "n_unclassified_now": 0, "halt": True}
    assert code == 3
    for c in CASES.values():  # no pool, mint, signature or class per pool, on either stream
        for v in (c["pool"], c["mint"], c["boundary_sig"], c["migrate_sig"], c["complete_sig"]):
            assert v not in out and v not in err
    assert "synthetic_2" not in out + err


def test_no_disagreement_exits_0_and_does_not_halt(monkeypatch, capsys):
    ledger = "\n".join([ledger_line("non_synthetic_1", synthetic=False), ledger_line("non_synthetic_3", synthetic=False)])
    code, out, _ = run(monkeypatch, capsys, ["--date", DAY, "--ledger", "-"], multi_rpc("non_synthetic_1", "non_synthetic_3"), ledger)
    assert code == 0 and json.loads(out) == {"date": DAY, "n_pools": 2, "n_disagree": 0, "n_unclassified_now": 0, "halt": False}


def test_unclassified_now_is_counted_and_is_not_a_disagreement(monkeypatch, capsys):
    rpc = multi_rpc("synthetic_1")
    rpc.txs.pop(CASES["synthetic_1"]["complete_sig"])
    code, out, _ = run(monkeypatch, capsys, ["--date", DAY, "--ledger", "-"], rpc, ledger_line("synthetic_1", synthetic=False))
    assert code == 0 and json.loads(out) == {"date": DAY, "n_pools": 1, "n_disagree": 0, "n_unclassified_now": 1, "halt": False}


def test_a_bought_pool_with_no_recorded_class_disagrees_with_any_b4_class(monkeypatch, capsys):
    lines = []
    for syn in (None, "false", 0):  # absent-like and non-bool values are no class
        row = json.loads(ledger_line("non_synthetic_1", synthetic=False))
        row["synthetic"] = syn
        lines.append(json.dumps(row))
    for ln in lines:
        code, out, _ = run(monkeypatch, capsys, ["--date", DAY, "--ledger", "-"], multi_rpc("non_synthetic_1"), ln)
        assert code == 3 and json.loads(out)["n_disagree"] == 1


def test_projection_keeps_only_the_five_keys_and_never_a_fill_size_exit_or_pnl_field(monkeypatch, capsys):
    ledger = "\n".join([
        ledger_line("non_synthetic_1", synthetic=False),
        ledger_line("synthetic_1", synthetic=False, kind="fill"),  # not a decision row
        ledger_line("synthetic_2", synthetic=False, ts=TS + 86_400_000),  # another UTC day
        "not json at all",
        ledger_line("non_synthetic_2", synthetic=False),
    ])
    code, out, err = run(monkeypatch, capsys, ["--date", DAY, "--ledger", "-", "--project-only"], None, ledger)
    rows = [json.loads(x) for x in out.strip().splitlines()]
    assert code == 0 and len(rows) == 2
    assert all(tuple(r) == au.KEEP_KEYS for r in rows)
    assert {r["pool"] for r in rows} == {CASES["non_synthetic_1"]["pool"], CASES["non_synthetic_2"]["pool"]}
    blob = out + err
    for s in SECRETS + ("PLAN-POOL", "ANCHOR-MINT", "20000000", "kind", "ts_ms", "stake_lamports"):
        assert s not in blob
    assert "1 unparseable" in err


def test_the_parser_hook_drops_other_keys_at_every_depth():
    row = json.loads('{"kind":"decision","fill_price":1,"plan":{"signature":"inner","x":[{"pool":"p"}]},"pool":"P"}', object_pairs_hook=au._hook)
    assert row == {"kind": "decision", "pool": "P"}  # `plan` is not an allowed key: it is gone, with the `signature` and `pool` inside it
    inner = json.loads('{"signature":"inner","x":[{"pool":"p"}],"fill_price":1}', object_pairs_hook=au._hook)
    assert inner == {"signature": "inner"}  # a nested object is filtered the same way when it is parsed on its own
    assert au._project(row) == {"pool": "P", "mint": None, "synthetic": None, "synthetic_src": None, "signature": None}


def test_decisions_mode_takes_a_projected_file_and_drops_any_extra_key(monkeypatch, capsys, tmp_path):
    c = CASES["synthetic_3"]
    p = tmp_path / "d.jsonl"
    p.write_text(json.dumps({"pool": c["pool"], "mint": c["mint"], "synthetic": False, "synthetic_src": "rpc", "signature": c["boundary_sig"], "pnl": SECRETS[1]}) + "\n")
    code, out, err = run(monkeypatch, capsys, ["--date", DAY, "--decisions", str(p)], multi_rpc("synthetic_3"))
    assert code == 3 and json.loads(out)["n_disagree"] == 1 and SECRETS[1] not in out + err


def test_the_same_pool_twice_is_one_pool(monkeypatch, capsys):
    ledger = "\n".join([ledger_line("non_synthetic_1", synthetic=False)] * 2)
    _, out, _ = run(monkeypatch, capsys, ["--date", DAY, "--ledger", "-"], multi_rpc("non_synthetic_1"), ledger)
    assert json.loads(out)["n_pools"] == 1


def test_helius_and_keyed_urls_are_refused_before_any_call(monkeypatch, capsys):
    for url in ("https://mainnet.helius-rpc.com/?api-key=x", "https://example.org/rpc?key=1", "https://user:pw@example.org/"):
        code, out, err = run(monkeypatch, capsys, ["--date", DAY, "--ledger", "-", "--rpc-url", url], None, ledger_line("non_synthetic_1", synthetic=False))
        assert code == 2 and out == "" and "refused" in err


def test_usage_errors(monkeypatch, capsys):
    assert run(monkeypatch, capsys, ["--date", "2026-13-40", "--ledger", "-"], None)[0] == 2
    assert run(monkeypatch, capsys, ["--date", DAY, "--decisions", "-", "--project-only"], None)[0] == 2
    with pytest.raises(SystemExit):
        au.parse_args(["--date", DAY])  # one of --decisions / --ledger is required


def test_rows_without_pool_or_mint_count_as_unclassified():
    out = au.run_audit([{"pool": None, "mint": "m", "synthetic": False}, {"pool": "p", "mint": None, "synthetic": False}], DAY, lambda r: sc.CLASS_NON_SYNTHETIC)
    assert out == {"date": DAY, "n_pools": 2, "n_disagree": 0, "n_unclassified_now": 2, "halt": False}
