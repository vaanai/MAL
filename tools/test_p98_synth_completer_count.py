"""Fixture tests for tools/p98_synth_completer_count.py. No real tape, no network."""
import base64
import json
import subprocess
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from observe import trade_decode as td
from tools import p98_synth_completer_count as p98

PUMP = p98.M.PUMP_PROGRAM
DAY = date(2026, 10, 28)
NEXT = date(2026, 10, 29)
WSOL = p98.WSOL


def mk_tx(logs, inner=None, keys=None):
    """A mini tx in the B1 reader's shape. inner=None gives an empty (readable) innerInstructions list."""
    return {"meta": {"err": None, "logMessages": logs, "innerInstructions": [] if inner is None else inner, "loadedAddresses": None},
            "transaction": {"message": {"accountKeys": keys or ["Signer", PUMP, "Other"]}}}


def cpi_ix(raw, pid_idx=1):
    return {"programIdIndex": pid_idx, "data": td.b58encode(p98.M.EVENT_CPI_TAG + raw)}


def raw_event(**kw):
    ev = next(x for x in event_logs(**kw) if x.startswith("Program data: ")).split("Program data: ")[1]
    return base64.b64decode(ev)


def ms(day, h=0, m=0):
    return int(datetime(day.year, day.month, day.day, h, m, tzinfo=timezone.utc).timestamp() * 1000)


def event_logs(quote_in=300_000_000, base_out=5_000_000, signer_byte=7, length=232, token="BuyExactQuoteInV3", depth=1):
    raw = bytearray(length)
    raw[:8] = p98.POST_COMPLETE_BUY_DISC
    raw[8:40] = bytes([signer_byte]) * 32
    raw[144:152] = base_out.to_bytes(8, "little")
    raw[152:160] = quote_in.to_bytes(8, "little")
    return [f"Program {PUMP} invoke [{depth}]", f"Program log: Instruction: {token}",
            "Program data: " + base64.b64encode(bytes(raw)).decode(), f"Program {PUMP} success"]


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
    c = p98.classify_tx(mk_tx(event_logs(quote_in=123, base_out=456, signer_byte=9)))
    assert c["cls"] == "synthetic" and c["quote_in"] == 123 and c["base_out"] == 456
    assert c["signer"] == td.b58encode(bytes([9]) * 32)
    assert set(c) == {"cls", "signer", "base_out", "quote_in", "token", "direct", "payer_differs"}  # no reserve / fee / ts key exists


def test_classify_normal_short_truncated_two_events_nologs():
    assert p98.classify_tx(mk_tx(["Program log: a"]))["cls"] == "normal"
    assert p98.classify_tx(mk_tx(event_logs(length=100)))["why"] == "short_event"
    assert p98.classify_tx(mk_tx(["Log truncated"]))["why"] == "log_truncated"
    assert p98.classify_tx(mk_tx(event_logs() + event_logs(quote_in=1)))["why"] == "two_events"
    assert p98.classify_tx(None)["cls"] == "unclassified"
    assert p98.classify_tx({"meta": {"logMessages": None}})["why"] == "no_logs"


def test_cpi_emitted_event_is_synthetic_not_normal():
    tx = mk_tx(["Program log: a"], inner=[{"index": 0, "instructions": [cpi_ix(raw_event(quote_in=77, base_out=88, signer_byte=4))]}])
    c = p98.classify_tx(tx)
    assert c["cls"] == "synthetic" and c["quote_in"] == 77 and c["base_out"] == 88 and c["signer"] == td.b58encode(bytes([4]) * 32)


def test_event_in_both_log_and_cpi_is_deduplicated():
    raw = raw_event(quote_in=5)
    tx = mk_tx(event_logs(quote_in=5), inner=[{"index": 0, "instructions": [cpi_ix(raw)]}])
    assert p98.classify_tx(tx)["cls"] == "synthetic"


def test_event_source_undeterminable_is_unclassified():
    t = mk_tx(["x"])
    t["meta"]["innerInstructions"] = None
    assert p98.classify_tx(t)["why"] == "inner_instructions_missing"
    bad_pid = mk_tx(["x"], inner=[{"index": 0, "instructions": [{"programIdIndex": 99, "data": "1"}]}])
    assert p98.classify_tx(bad_pid)["why"] == "inner_program_unresolved"
    bad_data = mk_tx(["x"], inner=[{"index": 0, "instructions": [{"programIdIndex": 1, "data": "0OIl"}]}])  # chars outside base58
    assert p98.classify_tx(bad_data)["why"] == "inner_data_undecodable"
    other = mk_tx(["x"], inner=[{"index": 0, "instructions": [{"programIdIndex": 2, "data": "0OIl"}]}])  # other program: irrelevant
    assert p98.classify_tx(other)["cls"] == "normal"
    short = mk_tx(["x"], inner=[{"index": 0, "instructions": [cpi_ix(raw_event(length=100))]}])
    assert p98.classify_tx(short)["why"] == "short_event"


def test_tx_from_response_keeps_only_needed_fields():
    resp = {"result": {"slot": 5, "blockTime": 6, "transaction": {"signatures": ["s"], "message": {"accountKeys": ["a"], "recentBlockhash": "h"}},
                       "meta": {"err": None, "logMessages": ["l"], "innerInstructions": [], "preBalances": [1], "postBalances": [0], "fee": 5,
                                "preTokenBalances": [], "postTokenBalances": [], "loadedAddresses": None, "computeUnitsConsumed": 7}}}
    t = p98.tx_from_response(resp)
    assert set(t["meta"]) == {"err", "logMessages", "innerInstructions", "loadedAddresses"} and set(t) == {"meta", "transaction"}
    assert "slot" not in json.dumps(t) and "blockTime" not in json.dumps(t)
    assert "fee" in p98.tx_from_response(resp, balances=True)["meta"]
    assert p98.tx_from_response({"result": {"meta": {"err": {"x": 1}}}}) is None
    assert p98.tx_from_response({"result": None}) is None
    assert p98.tx_from_response({"error": {}}) is None


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
    assert p98.check_rpc_url("https://rpc.example.org")
    for u in ("http://a.com", "https://mainnet.helius-rpc.com", "https://a.com/?api-key=1", "https://u:p@a.com", "https://a.com/x"):
        with pytest.raises(SystemExit):
            p98.check_rpc_url(u)


def fake_fetch(table):
    calls = []

    def f(sig):
        calls.append(sig)
        v = table.get(sig)
        return mk_tx(v) if isinstance(v, list) else v

    f.calls = calls
    return f


def test_day_counts_and_kill_lines(tmp_path):
    rows = [create("m1"), create("m2"), create("m3"), create("mm", mayhem=True), create("m4")]
    comps = [complete("s1", "m1"), complete("s2", "m2"), complete("s3", "m3"), complete("sm", "mm"), complete("s4", "m4"), complete("s1", "m1")]
    write_day(tmp_path, DAY, {2: rows, 3: comps})
    write_day(tmp_path, date(2026, 10, 27))
    write_day(tmp_path, date(2026, 10, 26))
    write_day(tmp_path, NEXT)
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
    write_day(tmp_path, NEXT)
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
    write_day(tmp_path, NEXT)
    f = fake_fetch({"s1": ["Program log: n"]})
    write_day(tmp_path, DAY, base, drop_hours=(5,))
    r = p98.count_day(DAY, tmp_path, f)
    assert r["status"] == "NOT_DECIDABLE" and r["missing_hours"] == ["05"]
    gap = {"v": 1, "type": "gap", "prog": "pumpfun", "kind": "socket_reconnect", "data_loss": True, "slot": 1, "t_recv_ms": ms(DAY)}
    write_day(tmp_path, DAY, base, extra=[gap])
    r = p98.count_day(DAY, tmp_path, f)
    assert r["bad_hours"] == ["00"] and "bad_hour" in r["not_decidable_reasons"]
    write_day(tmp_path, DAY, base, hb=54)
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
        quotes = quotes * 20 if len(quotes) < 20 else quotes
        return {"status": "DECIDABLE", "synthetic_share": share, "_signers": p98.collections.Counter(sig_counts), "_quotes": quotes, "_unverified": p98.collections.Counter(), "event_user_not_fee_payer": 0}
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


def test_pacing_floor_enforced_in_code_and_in_calls():
    with pytest.raises(SystemExit):
        p98.make_rpc_fetch("https://rpc.example.org", min_interval_s=0.99)
    now = [100.0]
    sleeps = []
    calls = []

    class R:
        def __init__(self, body):
            self.body = body

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return self.body

    ok = json.dumps({"result": {"meta": {"err": None, "logMessages": ["x"], "innerInstructions": []}, "transaction": {"message": {"accountKeys": []}}}}).encode()
    seq = [OSError("boom"), OSError("boom"), R(ok), R(ok)]

    def opener(req, timeout):
        calls.append(now[0])
        v = seq.pop(0)
        if isinstance(v, Exception):
            raise v
        return v

    def sleep(x):
        sleeps.append(x)
        now[0] += x

    f = p98.make_rpc_fetch("https://rpc.example.org", clock=lambda: now[0], sleep=sleep, opener=opener)
    assert f("a") is not None and f("b") is not None
    assert len(calls) == 4 and all(b - a >= 1.0 - 1e-9 for a, b in zip(calls, calls[1:]))  # retries and next signatures all spaced


def test_feed_host_and_helius_refused():
    for u in ("https://api.mainnet-beta.solana.com", "https://x.solanavibestation.com", "https://mainnet.helius-rpc.com"):
        with pytest.raises(SystemExit):
            p98.check_rpc_url(u)


def test_hb_threshold_and_lookback_constants():
    assert p98.HB_MIN_PER_HOUR == 55 and 3600 // 65 == 55  # arena_feed.py:94 HB_S = 60, checked on a >= 5 s tick
    assert p98.LOOKBACK_DAYS == 2


def test_hb_55_rows_is_a_good_hour(tmp_path):
    write_day(tmp_path, DAY, {2: [create("m1")], 3: [complete("s1", "m1")]}, hb=55)
    write_day(tmp_path, NEXT, {})
    assert p98.count_day(DAY, tmp_path, fake_fetch({"s1": ["x"]}))["bad_hours"] == []


def test_late_row_in_next_day_hour00_counted(tmp_path):
    write_day(tmp_path, DAY, {2: [create("m1")]})
    write_day(tmp_path, NEXT, {}, extra=[complete("sl", "m1", day=DAY)])
    r = p98.count_day(DAY, tmp_path, fake_fetch({"sl": ["x"]}))
    assert r["completions"] == 1 and r["normal"] == 1 and r["bad_hours"] == []


def test_next_day_hour00_missing_not_decidable(tmp_path):
    write_day(tmp_path, DAY, {2: [create("m1")]})
    r = p98.count_day(DAY, tmp_path, fake_fetch({}))
    assert "next_day_00" in r["missing_hours"] and r["status"] == "NOT_DECIDABLE"


def test_wrapped_dict_not_touched_outside_class():
    import ast
    tree = ast.parse(Path(p98.__file__).read_text())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "GuardedRow")
    inside = {id(x) for x in ast.walk(cls)}
    names = ("_d", "__d", "_GuardedRow__d", "_allow", "__allow", "_GuardedRow__allow")
    for n in ast.walk(tree):
        if isinstance(n, ast.Attribute) and n.attr in names and id(n) not in inside:
            raise AssertionError(f"line {n.lineno}: wrapped dict touched outside GuardedRow")
    r = p98.GuardedRow({"type": "complete", "slot": 1})
    with pytest.raises(AttributeError):
        r._d


# ---- V1 verify identity ---------------------------------------------------------------------------------------
def trade_blob(user, mint_byte=3, sol=1_000_000_000, tok=40_000_000, is_buy=1, pfee=9_000_000, cfee=3_000_000):
    raw = bytearray(225)
    raw[:8] = p98.TRADE_DISC
    raw[8:40] = bytes([mint_byte]) * 32
    raw[40:48] = sol.to_bytes(8, "little")
    raw[48:56] = tok.to_bytes(8, "little")
    raw[56] = is_buy
    raw[57:89] = user
    raw[169:177] = pfee.to_bytes(8, "little")
    raw[217:225] = cfee.to_bytes(8, "little")
    return bytes(raw)


def verify_tx(hyp, signer_byte=7, mint_byte=3):
    user = bytes([signer_byte]) * 32
    me = td.b58encode(user)
    mint = td.b58encode(bytes([mint_byte]) * 32)
    q_in, b_out, fee_ev = 500_000_000, 20_000_000, 12_345
    ev = bytearray(raw_event(quote_in=q_in, base_out=b_out, signer_byte=signer_byte, length=232))
    ev[168:176] = (10_000).to_bytes(8, "little")
    ev[184:192] = (2_000).to_bytes(8, "little")
    ev[192:200] = (345).to_bytes(8, "little")
    tr = trade_blob(user, mint_byte)
    curve_total = 1_000_000_000 + 9_000_000 + 3_000_000
    fee, rent = 5_000, 2_039_280
    if hyp == "pool_only":
        debit_net, tok_delta = curve_total + q_in + fee_ev, 40_000_000 + b_out
    else:
        debit_net, tok_delta = q_in + fee_ev, b_out
    inner = [{"index": 0, "instructions": [cpi_ix(b) for b in (bytes(ev), tr)]}]
    tx = mk_tx(["x"], inner=inner, keys=[me, PUMP, "Acct"])
    tx["meta"].update({"fee": fee, "preBalances": [10**10, 0, 0], "postBalances": [10**10 - debit_net - fee - rent, 0, rent],
                       "preTokenBalances": [], "postTokenBalances": [{"owner": me, "mint": mint, "uiTokenAmount": {"amount": str(tok_delta)}}]})
    return tx


def test_verify_pass_pool_only_and_reject_curve_plus_pool():
    r = p98.verify_identity(verify_tx("pool_only"), "buy_v3")
    assert r["verdict"] == "PASS_POOL_ONLY" and r["residual_pool_only"] == {"tok": 0, "sol": 0}
    assert r["residual_curve_plus_pool"]["sol"] == 1_012_000_000 and r["quote_in"] == 500_000_000 and r["new_account_rent"] == 2_039_280


def test_verify_curve_plus_pool_world_is_reported_not_pool_only():
    assert p98.verify_identity(verify_tx("both"), "buy_exact_quote_in_v3")["verdict"] == "PASS_CURVE_PLUS_POOL"


def test_verify_off_by_one_lamport_fails():
    tx = verify_tx("pool_only")
    tx["meta"]["postBalances"][0] -= 1
    assert p98.verify_identity(tx, "buy_v3")["verdict"] == "FAIL"


def test_verify_fails_closed():
    tx = verify_tx("pool_only")
    tx["meta"]["preBalances"] = None
    assert p98.verify_identity(tx, "v")["why"] == "balances_missing"
    assert p98.verify_identity(mk_tx(["x"]), "v")["why"].startswith("not_synthetic")
    tx = verify_tx("pool_only")
    tx["meta"]["postTokenBalances"][0]["owner"] = "someone_else"
    assert p98.verify_identity(tx, "v")["verdict"] == "FAIL"


def test_verify_reads_no_reserve_or_ts_bytes():
    a, b = verify_tx("pool_only"), verify_tx("pool_only")
    for ix in b["meta"]["innerInstructions"][0]["instructions"]:
        raw = bytearray(td.b58decode(ix["data"])[8:])
        if raw[:8] == p98.POST_COMPLETE_BUY_DISC:
            raw[200:232] = b"\xff" * 32
            raw[136:144] = b"\xee" * 8
        elif raw[:8] == p98.TRADE_DISC:
            raw[89:121] = b"\xaa" * 32
        ix["data"] = td.b58encode(p98.M.EVENT_CPI_TAG + bytes(raw))
    assert p98.verify_identity(a, "v") == p98.verify_identity(b, "v")


# ---- fetch fix: User-Agent and tx version 1 / -32015 retry ----------------------------------------------------
class _R:
    def __init__(self, body):
        self.body = json.dumps(body).encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self.body


def _scripted_fetch(responses):
    now = [10.0]
    seen = []

    def opener(req, timeout):
        seen.append((json.loads(req.data)["params"][1]["maxSupportedTransactionVersion"], req.get_header("User-agent"), now[0]))
        return _R(responses.pop(0))

    def sleep(x):
        now[0] += x

    return p98.make_rpc_fetch("https://rpc.example.org", clock=lambda: now[0], sleep=sleep, opener=opener), seen


HINT = ("Transaction version (2) is not supported by the requesting client. Please try the request again with the following "
        "configuration parameter: \"maxSupportedTransactionVersion\": 2")


def test_user_agent_explicit_and_start_version_one():
    ok = {"result": {"meta": {"err": None, "logMessages": ["x"], "innerInstructions": []}, "transaction": {"message": {"accountKeys": []}}}}
    f, seen = _scripted_fetch([ok])
    assert f("a") is not None
    assert seen[0][0] == 1 and seen[0][1] == "MAL-p98/1 (+research)" and "curl" not in seen[0][1].lower()


def test_32015_retries_once_at_named_version_and_stays_paced():
    ok = {"result": {"meta": {"err": None, "logMessages": ["x"], "innerInstructions": []}, "transaction": {"message": {"accountKeys": []}}}}
    f, seen = _scripted_fetch([{"error": {"code": -32015, "message": HINT}}, ok])
    assert f("a") is not None
    assert [v for v, _, _ in seen] == [1, 2] and seen[1][2] - seen[0][2] >= 1.0


def test_32015_hint_not_followed_above_cap_or_without_code():
    f, seen = _scripted_fetch([{"error": {"code": -32015, "message": HINT.replace("2", "9")}}] * 3)
    assert f("a") is None and {v for v, _, _ in seen} == {1}
    f, seen = _scripted_fetch([{"error": {"code": -32000, "message": HINT}}] * 3)
    assert f("a") is None and {v for v, _, _ in seen} == {1}


def test_version1_shaped_tx_with_loaded_addresses_decodes_cpi_event():
    # v1 json response: same accountKeys / meta shape as v0; an inner program index may point into loadedAddresses
    resp = {"result": {"version": 1, "transaction": {"message": {"accountKeys": ["Signer", "Other"], "header": {}, "instructions": [],
                                                                  "recentBlockhash": "h", "transactionConfig": {}}},
                       "meta": {"err": None, "logMessages": ["Program log: x"], "loadedAddresses": {"writable": [], "readonly": [PUMP]},
                                "innerInstructions": [{"index": 0, "instructions": [cpi_ix(raw_event(quote_in=11, base_out=22), pid_idx=2)]}],
                                "fee": 5, "preBalances": [1], "postBalances": [1], "preTokenBalances": [], "postTokenBalances": [],
                                "computeUnitsConsumed": 1, "costUnits": 1, "rewards": [], "status": {"Ok": None}}}}
    tx = p98.tx_from_response(resp)
    c = p98.classify_tx(tx)
    assert c["cls"] == "synthetic" and c["quote_in"] == 11 and c["base_out"] == 22
    assert set(tx["meta"]) == {"err", "logMessages", "innerInstructions", "loadedAddresses"}
    # without loadedAddresses the program index cannot be resolved: unclassified, never normal
    resp["result"]["meta"]["loadedAddresses"] = None
    assert p98.classify_tx(p98.tx_from_response(resp))["why"] == "inner_program_unresolved"


# ---- QP P98-V1: variant reader, median restriction, disclosure, V1a ------------------------------------------
def wrapped(inner_logs, depth=1):
    """pump context nested inside a router at `depth`-1."""
    return ["Program JUP1111111111111111111111111111111111111111 invoke [1]", "Program log: Instruction: MultiHopJupiterBuy"] + inner_logs


def test_pump_variant_reader():
    d = p98.pump_variant(event_logs())
    assert d == {"token": "BuyExactQuoteInV3", "direct": True}
    assert p98.pump_variant(event_logs(token="BuyV3")) == {"token": "BuyV3", "direct": False}
    assert p98.pump_variant(event_logs(depth=2)) == {"token": "BuyExactQuoteInV3", "direct": False}
    routed = wrapped(event_logs(depth=2))
    assert p98.pump_variant(routed) == {"token": "BuyExactQuoteInV3", "direct": False}
    assert p98.pump_variant(["Program JUP1111111111111111111111111111111111111111 invoke [1]", "Program log: Instruction: MultiHopJupiterBuy"]) \
        == {"token": "MultiHopJupiterBuy", "direct": False}
    assert p98.pump_variant(["Program log: x"]) == {"token": "none", "direct": False}
    # two pump buy tokens: not direct
    two = event_logs() + [f"Program {PUMP} invoke [1]", "Program log: Instruction: BuyV3", f"Program {PUMP} success"]
    assert p98.pump_variant(two) == {"token": "multiple", "direct": False}
    # truncated logs: never direct
    assert p98.pump_variant(event_logs() + ["Log truncated"])["direct"] is False
    # an Instruction log of another program is not a pump token
    other = ["Program ABC1111111111111111111111111111111111111111 invoke [1]", "Program log: Instruction: BuyExactQuoteInV3"]
    assert p98.pump_variant(other) == {"token": "BuyExactQuoteInV3", "direct": False}
    assert p98.pump_variant(None) == {"token": "none", "direct": False}


def test_median_only_over_verified_and_unverified_tallied(tmp_path):
    names = [("m1", "s1", event_logs(quote_in=1_000_000_000, signer_byte=1)),
             ("m2", "s2", event_logs(quote_in=9_000_000_000, signer_byte=2, token="BuyV3")),
             ("m3", "s3", wrapped(event_logs(quote_in=8_000_000_000, signer_byte=3, depth=2))),
             ("m4", "s4", event_logs(quote_in=7_000_000_000, signer_byte=4, depth=2)),
             ("m5", "s5", ["Program JUP1111111111111111111111111111111111111111 invoke [1]", "Program log: Instruction: MultiHopJupiterBuy"]
              + event_logs(quote_in=6_000_000_000, signer_byte=5)[2:3]),
             ("m6", "s6", event_logs(quote_in=5_000_000_000, signer_byte=6)),
             ("m7", "s7", ["Program log: nothing"] + event_logs(quote_in=4_000_000_000, signer_byte=8)[2:3])]
    write_day(tmp_path, DAY, {2: [create(m) for m, _, _ in names], 3: [complete(sg, m, q=(WSOL if m != "m6" else "USDCm")) for m, sg, _ in names]})
    write_day(tmp_path, NEXT)
    r = p98.count_day(DAY, tmp_path, fake_fetch({sg: lg for _, sg, lg in names}))
    assert r["synthetic"] == 7 and r["quote_in_n"] == 1 and r["quote_in_median_sol"] == 1.0
    assert r["quote_unverified"] == {"BuyExactQuoteInV3:not_direct": 2, "BuyV3": 1, "MultiHopJupiterBuy": 1, "none": 1, "non_wsol_quote": 1}
    assert r["quote_unverified_n"] == 6
    assert r["distinct_signers"] == 7  # share and top-5 use all synthetic completions
    assert r["event_user_not_fee_payer"] == 7  # mk_tx's fee payer is "Signer", never an event user


def test_payer_equal_is_not_counted():
    me = td.b58encode(bytes([7]) * 32)
    tx = mk_tx(event_logs(signer_byte=7), keys=[me, PUMP])
    assert p98.classify_tx(tx)["payer_differs"] is False
    assert p98.classify_tx(mk_tx(event_logs(signer_byte=7), keys=["Other", PUMP]))["payer_differs"] is True


def _day_rec(quotes, share=0.3):
    return {"status": "DECIDABLE", "synthetic_share": share, "_signers": p98.collections.Counter({str(i): 1 for i in range(30)}),
            "_quotes": quotes, "_unverified": p98.collections.Counter({"BuyV3": 2}), "event_user_not_fee_payer": 3}


def test_median_not_decidable_under_20_verified():
    k = p98.kill_review([_day_rec([10**7] * 19)])
    assert k["median_line"] == "NOT_DECIDABLE" and k["pooled_median_quote_in_sol"] is None
    assert "median_quote_in_lt_0.05_sol" not in k["kill_lines_hit"] and k["verdict"].startswith("NO_KILL")  # lines 1 and 2 still decide
    k = p98.kill_review([_day_rec([10**7] * 10), _day_rec([10**7] * 10)])  # pooled over days
    assert k["median_line"] == "DECIDABLE" and "median_quote_in_lt_0.05_sol" in k["kill_lines_hit"]
    assert k["quote_unverified_pooled"] == {"BuyV3": 4} and k["event_user_not_fee_payer_pooled"] == 6 and "subset" not in k["median_disclosure"] \
        and "only" in k["median_disclosure"]
    k = p98.kill_review([_day_rec([10**7] * 19, share=0.05)])
    assert k["verdict"] == "KILL" and k["kill_lines_hit"] == ["share_lt_10pct (not blind, disclosed)"]


def _v1a_txs(n=3, distinct=True, **kw):
    out = []
    for i in range(n):
        t = verify_tx("pool_only", signer_byte=(7 + i if distinct else 7))
        t["meta"]["logMessages"] = event_logs(**kw)[:2] + [f"Program {PUMP} success"]  # the event itself stays in the CPI blob
        out.append(t)
    return out


def test_v1a_pass_and_failures():
    assert p98.verify_v1a(_v1a_txs())["v1a"] == "PASS"
    assert p98.verify_v1a(_v1a_txs(distinct=False))["v1a"] == "FAIL"  # one signer
    assert p98.verify_v1a(_v1a_txs(2))["v1a"] == "FAIL"  # fewer than 3
    assert p98.verify_v1a(_v1a_txs(token="BuyV3"))["v1a"] == "FAIL"
    assert p98.verify_v1a(_v1a_txs(depth=2))["v1a"] == "FAIL"  # not top level
    txs = _v1a_txs()
    txs[1]["meta"]["postBalances"][0] -= 1  # one lamport off
    r = p98.verify_v1a(txs)
    assert r["v1a"] == "FAIL" and r["rows"][1]["verdict"] == "FAIL"
    txs = _v1a_txs()
    txs[0] = None
    assert p98.verify_v1a(txs)["v1a"] == "FAIL"
