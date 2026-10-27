"""Fixture tests for tools/p98_synth_completer_count.py. No real tape, no network."""
import base64
import json
import subprocess
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from observe import trade_decode as td
from tools import p98_synth_completer_count as p98

DAY = date(2026, 10, 28)
WSOL = p98.WSOL


def ms(day, h=0, m=0):
    return int(datetime(day.year, day.month, day.day, h, m, tzinfo=timezone.utc).timestamp() * 1000)


def event_logs(quote_in=300_000_000, base_out=5_000_000, signer_byte=7, length=232):
    raw = bytearray(length)
    raw[:8] = p98.POST_COMPLETE_BUY_DISC
    raw[8:40] = bytes([signer_byte]) * 32
    raw[144:152] = base_out.to_bytes(8, "little")
    raw[152:160] = quote_in.to_bytes(8, "little")
    return ["Program log: x", "Program data: " + base64.b64encode(bytes(raw)).decode()]


def write_day(tape, day, rows_by_hour=None, drop_hours=(), hb=60, extra=()):
    rows_by_hour = rows_by_hour or {}
    for h in range(24):
        if h in drop_hours:
            continue
        rows = [{"v": 1, "type": "hb", "prog": "pumpfun", "slot": 1, "t_recv_ms": ms(day, h), "rows_last_min": 3} for _ in range(hb)]
        rows += [{"v": 1, "type": "hb", "prog": "pumpswap", "slot": 1, "t_recv_ms": ms(day, h)}]
        rows += rows_by_hour.get(h, [])
        if h == 0:
            rows += list(extra)
        (tape / f"tape-{day.isoformat()}T{h:02d}.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")


def create(mint, mayhem=False, day=DAY):
    return {"v": 1, "type": "create", "prog": "pumpfun", "slot": 5, "t_recv_ms": ms(day), "signature": "c" + mint, "mint": mint,
            "is_mayhem_mode": mayhem, "name": "n", "symbol": "s", "creator": "x", "trader": "x"}


def complete(sig, mint, day=DAY, q=WSOL, h=3):
    return {"v": 1, "type": "complete", "prog": "pumpfun", "slot": 9, "t_recv_ms": ms(day, h), "signature": sig, "event_index": 0,
            "trader": "t", "mint": mint, "bonding_curve": "b", "event_ts": 1, "quote_mint": q}


def test_disc_matches_decoder():
    assert p98.POST_COMPLETE_BUY_DISC == td._POST_COMPLETE_BUY_DISC


def test_classify_synthetic_reads_only_three_fields():
    c = p98.classify_logs(event_logs(quote_in=123, base_out=456, signer_byte=9))
    assert c["cls"] == "synthetic" and c["quote_in"] == 123 and c["base_out"] == 456
    assert c["signer"] == td.b58encode(bytes([9]) * 32)
    assert set(c) == {"cls", "signer", "base_out", "quote_in"}  # no reserve / fee / ts key exists


def test_classify_normal_short_truncated_two_events_nologs():
    assert p98.classify_logs(["Program log: a"])["cls"] == "normal"
    assert p98.classify_logs(event_logs(length=100))["why"] == "short_event"
    assert p98.classify_logs(["Log truncated"])["why"] == "log_truncated"
    assert p98.classify_logs(event_logs() + event_logs())["why"] == "two_events"
    assert p98.classify_logs(None)["cls"] == "unclassified"


def test_logs_from_response_only_meta_and_failed_tx_is_none():
    ok = {"result": {"meta": {"err": None, "logMessages": ["a"], "preBalances": [1]}, "blockTime": 5}}
    assert p98.logs_from_response(ok) == ["a"]
    assert p98.logs_from_response({"result": {"meta": {"err": {"x": 1}, "logMessages": ["a"]}}}) is None
    assert p98.logs_from_response({"result": None}) is None
    assert p98.logs_from_response({"error": {}}) is None


def test_guarded_row_allowlist():
    r = p98.GuardedRow(complete("s", "m"))
    assert r["signature"] == "s" and r["t_recv_ms"] == ms(DAY, 3)
    for bad in ("slot", "trader", "bonding_curve", "event_ts", "event_index"):
        with pytest.raises(p98.ForbiddenFieldError):
            r[bad]
        with pytest.raises(p98.ForbiddenFieldError):
            r.get(bad)
    for fn in (lambda: list(r), lambda: r.items(), lambda: r.keys(), lambda: r.values(), lambda: "slot" in r, lambda: len(r)):
        with pytest.raises(p98.ForbiddenFieldError):
            fn()
    t = p98.GuardedRow({"type": "trade", "price": 1})
    assert t["type"] == "trade"
    with pytest.raises(p98.ForbiddenFieldError):
        t["price"]
    cr = p98.GuardedRow(create("m"))
    with pytest.raises(p98.ForbiddenFieldError):
        cr["quote_reserve"]


def test_check_rpc_url():
    assert p98.check_rpc_url("https://api.mainnet-beta.solana.com")
    for u in ("http://a.com", "https://mainnet.helius-rpc.com", "https://a.com/?api-key=1", "https://u:p@a.com", "https://a.com/x"):
        with pytest.raises(SystemExit):
            p98.check_rpc_url(u)


def fake_fetch(table):
    calls = []

    def f(sig):
        calls.append(sig)
        return table.get(sig)

    f.calls = calls
    return f


def test_day_counts_and_kill_lines(tmp_path):
    rows = [create("m1"), create("m2"), create("m3"), create("mm", mayhem=True), create("m4")]
    comps = [complete("s1", "m1"), complete("s2", "m2"), complete("s3", "m3"), complete("sm", "mm"), complete("s4", "m4"), complete("s1", "m1")]
    write_day(tmp_path, DAY, {2: rows, 3: comps})
    write_day(tmp_path, date(2026, 10, 27))
    write_day(tmp_path, date(2026, 10, 26))
    f = fake_fetch({"s1": ["x"] + event_logs(quote_in=1_000_000_000, signer_byte=1), "s2": event_logs(quote_in=3_000_000_000, signer_byte=1),
                    "s3": ["Program log: y"], "s4": event_logs(quote_in=2_000_000_000, signer_byte=2)})
    r = p98.count_day(DAY, tmp_path, f)
    assert r["completions"] == 5 and r["mayhem"] == 1 and r["non_mayhem"] == 4  # dup s1 collapsed
    assert (r["synthetic"], r["normal"], r["unclassified"]) == (3, 1, 0)
    assert "sm" not in f.calls  # a Mayhem completion tx is never fetched
    assert r["synthetic_share"] == 0.75 and r["quote_in_median_sol"] == 2.0
    assert r["distinct_signers"] == 2 and r["top5_share"] == 1.0
    assert r["status"] == "DECIDABLE"
    pub = json.dumps(p98.public(r))
    for leak in ("s1", "m1", "mm", "signer"):
        assert f'"{leak}"' not in pub
    k = p98.kill_review([r])
    assert k["kill_lines_hit"] == ["top5_ge_80pct"] and k["verdict"] == "KILL"


def test_unclassified_over_one_percent_not_decidable(tmp_path):
    rows = [create(f"m{i}") for i in range(100)]
    comps = [complete(f"s{i}", f"m{i}") for i in range(100)]
    write_day(tmp_path, DAY, {2: rows, 3: comps})
    table = {f"s{i}": ["Program log: n"] for i in range(100)}
    r = p98.count_day(DAY, tmp_path, fake_fetch({**table, "s0": None}))  # exactly 1/100 = 1%: not over
    assert r["unclassified"] == 1 and r["status"] == "DECIDABLE"
    r = p98.count_day(DAY, tmp_path, fake_fetch({**table, "s0": None, "s1": None}))
    assert r["status"] == "NOT_DECIDABLE" and "unclassified_over_1pct" in r["not_decidable_reasons"]
    assert p98.kill_review([r])["verdict"] == "NOT_DECIDABLE"


def test_unknown_mayhem_flag_is_unclassified_and_in_denominator(tmp_path):
    write_day(tmp_path, DAY, {3: [complete("s1", "never_created")]})
    r = p98.count_day(DAY, tmp_path, fake_fetch({}))
    assert r["unclassified"] == 1 and r["non_mayhem"] == 1 and r["unclassified_why"] == {"mayhem_flag_unknown": 1}
    assert r["status"] == "NOT_DECIDABLE"


def test_missing_hour_and_data_loss_and_low_hb(tmp_path):
    base = {2: [create("m1")], 3: [complete("s1", "m1")]}
    f = fake_fetch({"s1": ["Program log: n"]})
    write_day(tmp_path, DAY, base, drop_hours=(5,))
    r = p98.count_day(DAY, tmp_path, f)
    assert r["status"] == "NOT_DECIDABLE" and r["missing_hours"] == ["05"]
    gap = {"v": 1, "type": "gap", "prog": "pumpfun", "kind": "socket_reconnect", "data_loss": True, "slot": 1, "t_recv_ms": ms(DAY)}
    write_day(tmp_path, DAY, base, extra=[gap])
    r = p98.count_day(DAY, tmp_path, f)
    assert r["bad_hours"] == ["00"] and "bad_hour" in r["not_decidable_reasons"]
    write_day(tmp_path, DAY, base, hb=59)
    assert len(p98.count_day(DAY, tmp_path, f)["bad_hours"]) == 24


def test_completion_on_other_utc_day_is_not_counted(tmp_path):
    write_day(tmp_path, DAY, {2: [create("m1")], 3: [complete("s1", "m1", day=date(2026, 10, 27))]})
    assert p98.count_day(DAY, tmp_path, fake_fetch({}))["completions"] == 0


def test_non_wsol_quote_excluded_from_sol_median(tmp_path):
    write_day(tmp_path, DAY, {2: [create("m1"), create("m2")], 3: [complete("s1", "m1"), complete("s2", "m2", q="USDCmint")]})
    f = fake_fetch({"s1": event_logs(quote_in=100_000_000), "s2": event_logs(quote_in=9_000_000_000_000)})
    r = p98.count_day(DAY, tmp_path, f)
    assert r["synthetic"] == 2 and r["synthetic_nonwsol_quote"] == 1 and r["quote_in_median_sol"] == 0.1


def test_kill_lines_each(tmp_path):
    def rec(share, sig_counts, quotes):
        return {"status": "DECIDABLE", "synthetic_share": share, "_signers": p98.collections.Counter(sig_counts), "_quotes": quotes}
    spread = {str(i): 1 for i in range(20)}
    ok = p98.kill_review([rec(0.3, spread, [10**9] * 5)])
    assert ok["kill_lines_hit"] == [] and ok["verdict"].startswith("NO_KILL")
    assert "share_lt_10pct (not blind, disclosed)" in p98.kill_review([rec(0.05, spread, [10**9])])["kill_lines_hit"]
    assert "median_quote_in_lt_0.05_sol" in p98.kill_review([rec(0.3, spread, [10**7])])["kill_lines_hit"]
    assert "top5_ge_80pct" in p98.kill_review([rec(0.3, {"a": 8, "b": 2}, [10**9])])["kill_lines_hit"]


def test_tape_floor_and_open_day_refused(tmp_path):
    with pytest.raises(SystemExit):
        p98.count_day(date(2026, 10, 19), tmp_path, fake_fetch({}))
    with pytest.raises(SystemExit):
        p98.parse_days("2026-10-28", now=datetime(2026, 10, 28, 5, tzinfo=timezone.utc))
    assert p98.parse_days("2026-10-29,2026-10-28", now=datetime(2026, 10, 30, tzinfo=timezone.utc)) == [date(2026, 10, 28), date(2026, 10, 29)]


def test_zst_hour_file_read(tmp_path):
    if subprocess.run(["which", "zstd"], capture_output=True).returncode != 0:
        pytest.skip("no zstd")
    write_day(tmp_path, DAY, {2: [create("m1")], 3: [complete("s1", "m1")]})
    p = tmp_path / f"tape-{DAY.isoformat()}T03.jsonl"
    subprocess.run(["zstd", "-q", "--rm", str(p)], check=True)
    r = p98.count_day(DAY, tmp_path, fake_fetch({"s1": event_logs()}))
    assert r["completions"] == 1 and r["synthetic"] == 1


def test_verify_one_tx_prints_three_fields():
    out = p98.verify_one_tx("s", lambda s: event_logs(quote_in=5, base_out=6))
    assert out == {"cls": "synthetic", "quote_in": 5, "base_out": 6}
