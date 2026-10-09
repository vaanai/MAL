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


def test_out_writes_only_the_five_keys_to_a_0600_file_and_nothing_about_rows_to_stdout(monkeypatch, capsys, tmp_path):
    ledger = "\n".join([
        ledger_line("non_synthetic_1", synthetic=False),
        ledger_line("synthetic_1", synthetic=False, kind="fill"),  # not a decision row
        ledger_line("synthetic_2", synthetic=False, ts=TS + 86_400_000),  # another UTC day
        "not json at all",
        ledger_line("non_synthetic_2", synthetic=False),
    ])
    dst = tmp_path / "decisions.jsonl"
    code, out, err = run(monkeypatch, capsys, ["--date", DAY, "--ledger", "-", "--out", str(dst)], None, ledger)
    assert code == 0 and out == ""  # stdout never carries a row
    text = dst.read_text()
    rows = [json.loads(x) for x in text.strip().splitlines()]
    assert len(rows) == 2 and all(tuple(r) == au.KEEP_KEYS for r in rows)
    assert {r["pool"] for r in rows} == {CASES["non_synthetic_1"]["pool"], CASES["non_synthetic_2"]["pool"]}
    assert oct(dst.stat().st_mode & 0o777) == "0o600"
    for s in SECRETS + ("PLAN-POOL", "ANCHOR-MINT", "20000000", "kind", "ts_ms", "stake_lamports"):
        assert s not in text + out + err
    assert "n_lines_read=5 n_rows=2 n_unparseable=1" in err
    for c in CASES.values():
        assert c["pool"] not in err  # stderr counts, never ids


def test_out_needs_a_ledger_and_a_path_and_does_not_follow_a_symlink(monkeypatch, capsys, tmp_path):
    assert run(monkeypatch, capsys, ["--date", DAY, "--decisions", "-", "--out", str(tmp_path / "x")], None)[0] == 2
    assert run(monkeypatch, capsys, ["--date", DAY, "--ledger", "-", "--out", "-"], None)[0] == 2
    target = tmp_path / "target"
    link = tmp_path / "link"
    link.symlink_to(target)
    code, out, _ = run(monkeypatch, capsys, ["--date", DAY, "--ledger", "-", "--out", str(link)], None, ledger_line("non_synthetic_1", synthetic=False))
    assert code == 4 and json.loads(out) == {"date": DAY, "error": "projection_write_failed"} and not target.exists()


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
    assert run(monkeypatch, capsys, ["--date", DAY, "--ledger", "-", "--min-interval", "0.1"], None, "x")[0] == 2
    assert run(monkeypatch, capsys, ["--date", DAY, "--ledger", "-", "--max-calls-per-pool", "0"], None, "x")[0] == 2
    with pytest.raises(SystemExit):
        au.parse_args(["--date", DAY])  # one of --decisions / --ledger is required


def test_rows_without_pool_or_mint_count_as_unclassified():
    out = au.run_audit([{"pool": None, "mint": "m", "synthetic": False}, {"pool": "p", "mint": None, "synthetic": False}], DAY, lambda r: {"class": sc.CLASS_NON_SYNTHETIC, "reason": "", "event_seen_any": False})
    assert out == {"date": DAY, "n_pools": 2, "n_disagree": 0, "n_unclassified_now": 2, "halt": False}


# ---- fail closed on the read -----------------------------------------------------------------------------------------


def test_an_empty_ledger_is_exit_4_not_an_all_clear(monkeypatch, capsys):
    for stdin in ("", "\n  \n"):
        code, out, err = run(monkeypatch, capsys, ["--date", DAY, "--ledger", "-"], multi_rpc("non_synthetic_1"), stdin)
        assert code == 4 and json.loads(out) == {"date": DAY, "error": "no_ledger_lines"}
        assert "n_lines_read=0" in err


def test_no_expect_ledger_lets_an_empty_projected_file_through_as_a_day_with_no_buys(monkeypatch, capsys):
    code, out, err = run(monkeypatch, capsys, ["--date", DAY, "--decisions", "-", "--no-expect-ledger"], multi_rpc("non_synthetic_1"), "")
    assert code == 0 and json.loads(out) == {"date": DAY, "n_pools": 0, "n_disagree": 0, "n_unclassified_now": 0, "halt": False}
    assert "n_lines_read=0" in err


def test_a_ledger_with_lines_but_no_decision_rows_is_a_real_zero(monkeypatch, capsys):
    code, out, err = run(monkeypatch, capsys, ["--date", DAY, "--ledger", "-"], multi_rpc("non_synthetic_1"), ledger_line("non_synthetic_1", synthetic=False, kind="skip"))
    assert code == 0 and json.loads(out)["n_pools"] == 0 and "n_lines_read=1 n_rows=0" in err


def test_a_failed_read_is_exit_4_whatever_the_flag_says(monkeypatch, capsys, tmp_path):
    (tmp_path / "real").write_text(ledger_line("non_synthetic_1", synthetic=False))
    (tmp_path / "link").symlink_to(tmp_path / "real")  # O_NOFOLLOW
    (tmp_path / "bad").write_bytes(b"\xff\xfe\n")
    for path in (tmp_path / "missing", tmp_path / "link", tmp_path / "bad"):
        for flag in ("--expect-ledger", "--no-expect-ledger"):
            code, out, err = run(monkeypatch, capsys, ["--date", DAY, "--ledger", str(path), flag], multi_rpc("non_synthetic_1"))
            assert code == 4 and json.loads(out) == {"date": DAY, "error": "ledger_read_failed"}
            assert str(tmp_path) not in out + err  # the type name only, never the path or the message


# ---- partial synthetic (manager decision, DEC-024 Am.2 C1) ---------------------------------------------------------------


def _half_readable(name="synthetic_1"):
    """The pool's completing tx is gone, the migrate tx shows the PostCompleteBuyEvent (spliced): B3 says unclassified; the event WAS seen."""
    rpc = multi_rpc(name)
    c = CASES[name]
    line = [ln for ln in FX["txs"][c["complete_sig"]]["meta"]["logMessages"] if "Program data: " in ln]
    from tools.test_synthetic_class import _line_with_disc
    from tools import pump_structure_monitor as M

    rpc.txs[c["migrate_sig"]]["meta"]["logMessages"].append(_line_with_disc(FX["txs"][c["complete_sig"]], M.DISC_POST_COMPLETE_BUY))
    rpc.txs.pop(c["complete_sig"])
    assert line
    return rpc


def test_shadow_plain_and_event_seen_in_one_readable_tx_is_a_disagreement_even_if_the_other_is_unreadable(monkeypatch, capsys):
    code, out, err = run(monkeypatch, capsys, ["--date", DAY, "--ledger", "-"], _half_readable(), ledger_line("synthetic_1", synthetic=False))
    assert code == 3
    assert json.loads(out) == {"date": DAY, "n_pools": 1, "n_disagree": 1, "n_unclassified_now": 1, "halt": True}
    assert 'unclassified_by_reason={"tx_missing:complete":1}' in err


def test_the_same_half_readable_pool_is_no_disagreement_if_the_shadow_called_it_synthetic(monkeypatch, capsys):
    code, out, _ = run(monkeypatch, capsys, ["--date", DAY, "--ledger", "-"], _half_readable(), ledger_line("synthetic_1", synthetic=True))
    assert code == 0 and json.loads(out) == {"date": DAY, "n_pools": 1, "n_disagree": 0, "n_unclassified_now": 1, "halt": False}


# ---- a buy that never landed, budgets and the RPC guard -----------------------------------------------------------------


class NodeFake(FakeRpc):
    """The public node's answer to an unknown `before` (measured 2026-10-09): -32020, with the signature in the message."""

    def call(self, method, params):
        if method == "getSignaturesForAddress" and params[1].get("before") and not any(s["signature"] == params[1]["before"] for s in self.lists.get(params[0], [])):
            raise au.M.RpcError(f"rpc error -32020 on getSignaturesForAddress: Transaction {params[1]['before']} not found")
        return super().call(method, params)


def test_a_buy_that_never_landed_has_an_unknown_before_signature_and_is_unclassified_not_a_disagreement(monkeypatch, capsys):
    c = CASES["synthetic_1"]
    rpc = NodeFake(FX["txs"], {c["pool"]: [_entry(c["boundary_sig"])] + c["pool_sigs"], c["curve"]: [_entry(c["migrate_sig"])] + c["curve_sigs"]})
    never = "N" * 20 + "EVERLANDED"
    row = json.loads(ledger_line("synthetic_1", synthetic=False))
    row["signature"] = never
    code, out, err = run(monkeypatch, capsys, ["--date", DAY, "--ledger", "-"], rpc, json.dumps(row))
    assert code == 0 and json.loads(out) == {"date": DAY, "n_pools": 1, "n_disagree": 0, "n_unclassified_now": 1, "halt": False}
    assert 'unclassified_by_reason={"fetch_failed:signatures:pool:RpcError":1}' in err
    assert never not in out + err and c["pool"] not in out + err  # the node's message carries the signature; it never reaches a stream


def test_a_pool_is_cut_off_at_its_per_pool_call_cap(monkeypatch, capsys):
    rpc = multi_rpc("synthetic_1", "non_synthetic_1")
    ledger = "\n".join([ledger_line("synthetic_1", synthetic=False), ledger_line("non_synthetic_1", synthetic=False)])
    code, out, err = run(monkeypatch, capsys, ["--date", DAY, "--ledger", "-", "--max-calls-per-pool", "2"], rpc, ledger)
    assert json.loads(out)["n_unclassified_now"] == 2 and code == 0
    assert "PoolCallBudgetExceeded" in err
    assert len(rpc.tx_calls) + len(rpc.sig_calls) == 4  # 2 calls for each of the two pools, not one more
    # and the cap resets per pool: with room for both, both are classified
    rpc2 = multi_rpc("synthetic_1", "non_synthetic_1")
    code, out, _ = run(monkeypatch, capsys, ["--date", DAY, "--ledger", "-", "--max-calls-per-pool", "300"], rpc2, ledger)
    assert json.loads(out)["n_unclassified_now"] == 0 and code == 3  # synthetic_1 was bought as plain: a real disagreement


def test_rpc_allowlist_and_pacing_floor():
    ok = au.check_public_rpc
    assert ok("https://api.mainnet-beta.solana.com") is None
    assert ok("https://api.mainnet-beta.solana.com/") is None
    assert ok("https://rpc.example.org") is not None  # not on the allowlist
    assert ok("https://rpc.example.org", ["rpc.example.org"]) is None  # named explicitly
    assert ok("https://rpc.example.org/?api-key=1", ["rpc.example.org"]) is not None  # a keyed URL is never allowed
    assert ok("https://mainnet.helius-rpc.com", ["mainnet.helius-rpc.com"]) is not None
    assert ok("http://api.mainnet-beta.solana.com") is not None  # https only
    assert ok("https://user:pw@api.mainnet-beta.solana.com") is not None


def test_a_host_off_the_allowlist_is_refused_before_any_call(monkeypatch, capsys):
    code, out, err = run(monkeypatch, capsys, ["--date", DAY, "--ledger", "-", "--rpc-url", "https://rpc.example.org"], None, ledger_line("non_synthetic_1", synthetic=False))
    assert code == 2 and out == "" and "allowlist" in err
