"""Tests for tools/floor_fills_vs_model.py: synthetic fixtures in the real row formats (lane ledger, host decision log, executor fills log)."""

from __future__ import annotations

import json

import pytest

from tools import floor_fills_vs_model as T

S = 30_000_000  # live stake
BASE = 1_794_268_800_000 + 3_600_000  # 2026-11-10T01:00:00Z in ms
STEP = 1_000_000
LANE = "floor-lane-1"
BUY_OUT = S + 2_100_000 + 160_000
QE = 20e9  # effective quote reserve, lamports (V included)
BE = 1e15  # base reserve, raw
SPOT_LAM = QE / BE  # lamports per raw token
MARK = SPOT_LAM / 1000.0  # SOL per UI token: mark0 (the pool spot at the decision print)
FEE_LAM = 75_000  # pool fee estimate on S
NET = S - FEE_LAM
TOK_Q = BE * NET / (QE + NET)  # tokens the quote promises
MARK_SEND = (QE + NET) / (BE - TOK_Q) / 1000.0  # mark_send is the POST-buy spot of the quoted buy (probe_executor.entry_quote)


def lrow(ev, mint, ts, *, sol_in=0, sol_out=0, sig=None, **kw):
    return {"schema": "h5_lane_v1", "bot_id": LANE, "row_id": f"r{ev}{mint}{ts}", "ev": ev, "ts_ms": ts, "mint": mint, "signature": sig, "sol_in_lamports": sol_in,
            "sol_out_lamports": sol_out, "estimated": False, **kw}


def intent(mint, oid, ts):
    return lrow("send_intent", mint, ts, sig=f"intent:{oid}", stake_lamports=S)


def buy_fill(mint, ts, sig="sigb", out=BUY_OUT, **kw):
    return lrow("fill", mint, ts, ok=True, sol_out=out, sig=sig, fee_network_lamports=5_000, fee_priority_lamports=155_000, rent_paid_lamports=2_100_000, **kw)


def exit_with_net(mint, ts, total_net, buy_out=BUY_OUT):
    """An exit row such that (sum of in - out over fill + exit) == total_net."""
    sol_out = 160_000
    return lrow("exit", mint, ts, sol_in=total_net + buy_out + sol_out, sol_out=sol_out, sig="sigs", fee_network_lamports=5_000, fee_priority_lamports=155_000,
                rent_refunded_lamports=2_100_000)


def dec_entry(oid, ms, mint, seq=0, mark0=MARK):
    meta = {"age_s": 100000, "cell": "x", "hold_s": 86400, "mark0": mark0, "mint": mint, "r6": 1.0, "rate": 2.5, "rule": "l1-floor-v2", "signal_sig": "s", "signal_slot": 1,
            "signal_t_recv_ms": ms, "tp": 0.5, "v": 17.5e9, "vol24_sol": 12.0}
    return {"seq": seq, "arm": T.BOOK_BOT_ID, "ms": ms, "kind": "entry", "order_id": oid, "pool": "P" + oid, "meta": meta, "stake_sol": 0.25, "decision_slot": 5, "attempt_key": None,
            "prev": "0", "sha": "0"}


def dec_exit(oid, ms, reason, seq=1):
    return {"seq": seq, "arm": T.BOOK_BOT_ID, "ms": ms, "kind": "exit", "order_id": oid, "pool": "P" + oid, "reason": reason, "attempt": 1, "decision_slot": 9, "prev": "0", "sha": "0"}


def fills_buy(mint, ts, *, sig="sigb", slip=0.0, mark_send=MARK_SEND, tokens=None, **kw):
    """A probe `buy` row. tokens = quote tokens / (1 + slip): the fill price is `slip` above the quote price."""
    tok = TOK_Q / (1.0 + slip) if tokens is None else tokens
    return {"kind": "buy", "ts_ms": ts, "mint": mint, "book": "b", "landed": True, "signature": sig, "spend_lamports": S, "tokens_received": int(tok), "expected_tokens": int(TOK_Q),
            "mark_send": mark_send, "pool_fee_est_lamports": FEE_LAM, "price_vs_quote_bps": slip * 10_000, "sol_spent_lamports": BUY_OUT, "rent_charged_lamports": 2_100_000, **kw}


def make(specs):
    """specs: (live return, exit reason, entry slip fraction) per trade -> (ledger rows, decision rows, fills rows)."""
    led, dec, fil = [], [], []
    for i, (live_ret, reason, slip) in enumerate(specs):
        oid, mint, t0 = f"o{i:02d}", f"M{i:02d}xxxxxxxx", BASE + i * STEP
        led += [intent(mint, oid, t0), buy_fill(mint, t0 + 2_000, sig=f"sb{i}"), exit_with_net(mint, t0 + 3_600_000, int(round(live_ret * S)))]
        dec += [dec_entry(oid, t0 - 50, mint, 2 * i), dec_exit(oid, t0 + 3_599_000, reason, 2 * i + 1)]
        fil.append(fills_buy(mint, t0 + 1_900, sig=f"sb{i}", slip=slip))
    return led, dec, fil


def test_entry_slippage_matches_injected_slip_and_fees():
    led, dec, fil = make([(0.5, "tp_1.5x", 0.0), (-0.02, "time_24h", 0.02)])
    rep = T.build_report(led, dec, fil, min_trades=2)
    t0, t1 = rep["trades"]
    assert t0["matched"] and t0["match_key"] == "order_id" and t0["host_order_id"] == "o00" and t0["buy_match_key"] == "sig"
    assert t0["entry_slip_pp"] == pytest.approx(0.0, abs=1e-3) and t0["impact_adjusted"] is True
    assert t1["entry_slip_pp"] == pytest.approx(2.0, abs=1e-3)
    assert t1["fill_vs_quote_pp"] == pytest.approx(2.0)
    assert t1["live_return"] == pytest.approx(-0.02, abs=1e-6) and t1["exit_reason"] == "time_24h"
    assert t1["fees_live_pp"] == pytest.approx(320_000 / S * 100) and t1["fees_model_pp"] == pytest.approx(320_000 / S * 100)
    assert t1["entry_lag_ms"] == 50 and t1["entry_ts_ms"] == BASE + STEP and t1["exit_ts_ms"] == BASE + STEP + 3_600_000
    s = rep["summary"]
    assert s["median_entry_slip_pp"] == pytest.approx(1.0, abs=1e-3) and s["verdict"] == "no" and not any(s["flags"].values())  # only 2 measured: below the 10


def test_impact_term_uses_live_stake():
    # mark0 is the pre-impact spot; a fill exactly at the quote price carries the stake's impact, which the model price must include (no spurious slip)
    led, dec, fil = make([(0.0, "tp_1.5x", 0.0)])
    r = T.build_report(led, dec, fil, min_trades=1)["trades"][0]
    assert r["model_price"] > MARK / 1.0 * (1 + 0.9 * NET / QE)
    assert r["entry_slip_pp"] == pytest.approx(0.0, abs=1e-3)
    # without a derivable Qe (no quote fields) the impact term is dropped and flagged
    fil2 = [dict(fil[0], expected_tokens=None)]
    r2 = T.build_report(led, dec, fil2, min_trades=1)["trades"][0]
    assert r2["impact_adjusted"] is False and r2["entry_slip_pp"] == pytest.approx(NET / QE * 100, abs=0.01)


def test_verdict_clauses():
    led, dec, fil = make([(0.01, "tp_1.5x", 0.05)] * 10)  # median entry slip 5 pp
    s = T.build_report(led, dec, fil)["summary"]
    assert s["verdict"] == "no" and any("median entry slippage" in c for c in s["verdict_failed_clauses"])
    led, dec, fil = make([(0.45, "tp_1.5x", 0.01)] * 10)
    s = T.build_report(led, dec, fil)["summary"]
    assert s["verdict"] == "yes" and s["n_matched"] == 10
    led, dec, fil = make([(0.45, "tp_1.5x", 0.01)] * 9)
    s = T.build_report(led, dec, fil)["summary"]
    assert s["verdict"] == "no" and any("only 9" in c for c in s["verdict_failed_clauses"])
    led, dec, fil = make([(0.45, "tp_1.5x", 0.0299)] * 10)  # just inside 3 pp passes
    assert T.build_report(led, dec, fil)["summary"]["verdict"] == "yes"


def test_return_flags():
    led, dec, fil = make([(0.45, "tp_1.5x", 0.0)] * 9 + [(-0.16, "time_24h", 0.0)])
    s = T.build_report(led, dec, fil)["summary"]
    assert s["flags"]["flag_a"] == ["o09"] and s["flags"]["flag_c"] == ["o09"] and s["verdict"] == "no"
    led, dec, fil = make([(0.45, "tp_1.5x", 0.0)] * 9 + [(0.39, "tp_1.5x", 0.0)])  # tp fill far below its trigger
    s = T.build_report(led, dec, fil)["summary"]
    assert s["flags"] == {"flag_a": [], "flag_b": ["o09"], "flag_c": []} and s["verdict"] == "no"
    led, dec, fil = make([(0.45, "tp_1.5x", 0.0)] * 9 + [(-0.13, "time_24h", 0.0)])  # time exit below -12% but above -15%
    s = T.build_report(led, dec, fil)["summary"]
    assert s["flags"] == {"flag_a": [], "flag_b": [], "flag_c": ["o09"]} and s["verdict"] == "no"
    led, dec, fil = make([(0.45, "tp_1.5x", 0.0)] * 9 + [(-0.11, "time_24h", 0.0)])  # -11% time exit is fine
    assert T.build_report(led, dec, fil)["summary"]["verdict"] == "yes"
    led, dec, fil = make([(0.45, "tp_1.5x", 0.0)] * 9 + [(0.40, "tp_1.5x", 0.0)])  # +40% on the edge is fine
    assert T.build_report(led, dec, fil)["summary"]["verdict"] == "yes"


def test_unmatched_trade_listed_and_fails_verdict():
    led, dec, fil = make([(0.45, "tp_1.5x", 0.0)] * 10)
    led += [intent("MUNMATCH0000", "zz", BASE + 99_000_000), buy_fill("MUNMATCH0000", BASE + 99_002_000, sig="sz"), exit_with_net("MUNMATCH0000", BASE + 99_500_000, 1)]
    fil.append(fills_buy("MUNMATCH0000", BASE + 99_001_000, sig="sz"))
    s = T.build_report(led, dec, fil)["summary"]
    assert s["n_closed"] == 11 and s["n_matched"] == 10 and s["n_unmatched"] == 1
    assert s["unmatched"][0]["order_id"] == "zz" and s["unmatched"][0]["reason"] == "no_host_entry_row" and s["verdict"] == "no"


def test_mint_time_fallback_match():
    led, dec, fil = make([(0.45, "tp_1.5x", 0.0)])
    led[0] = intent("M00xxxxxxxx", "differs", BASE)  # the executor's order_id is not in the host log: same mint, entry 50 ms earlier
    r = T.build_report(led, dec, fil, min_trades=1)["trades"][0]
    assert r["matched"] and r["match_key"] == "mint_time" and r["host_order_id"] == "o00"
    dec2 = dec + [dec_entry("o99", BASE - 20, "M00xxxxxxxx", 7)]  # two candidates: ambiguous, not matched
    assert not T.build_report(led, dec2, fil, min_trades=1)["trades"][0]["matched"]


def test_writeoff_not_sent_is_not_a_trade():
    led, dec, fil = make([(0.45, "tp_1.5x", 0.0)])
    led += [intent("MREFUSED000", "rf", BASE + 5), lrow("writeoff", "MREFUSED000", BASE + 12, reason="not_sent", sig="intent:rf")]
    s = T.build_report(led, dec, fil, min_trades=1)["summary"]
    assert s["n_closed"] == 1 and s["n_not_sent_excluded"] == 1 and s["n_unmatched"] == 0 and s["n_open"] == 0


def test_buy_landed_with_error_and_expired_buy_are_trades():
    dec = [dec_entry("e1", BASE - 10, "MERR0000000"), dec_entry("e2", BASE + STEP - 10, "MEXP0000000", 1)]
    led = [intent("MERR0000000", "e1", BASE), lrow("fill", "MERR0000000", BASE + 900, ok=False, sol_out=165_000, sig="x", fee_network_lamports=5_000, fee_priority_lamports=155_000)]
    led += [intent("MEXP0000000", "e2", BASE + STEP), lrow("writeoff", "MEXP0000000", BASE + 70_000 + STEP, reason="buy_expired", sig="intent:e2")]
    rep = T.build_report(led, dec, [], min_trades=1)
    by = {r["order_id"]: r for r in rep["trades"]}
    assert by["e1"]["close"] == "buy_error" and by["e1"]["net_lamports"] == -165_000 and by["e1"]["live_return"] == pytest.approx(-165_000 / S)
    assert by["e2"]["exit_reason"] == "writeoff:buy_expired" and by["e2"]["net_lamports"] == 0 and by["e1"]["entry_note"] == "not_filled"
    s = rep["summary"]
    assert s["n_zero_cash"] == 1 and s["n_closed"] == 2 and s["n_matched"] == 2
    assert "no entry slippage measured" in s["verdict_failed_clauses"] and s["verdict"] == "no"


def test_open_trade_listed_not_scored_then_failed_sell_fee_in_sum():
    led = [intent("MOPEN000000", "op", BASE), buy_fill("MOPEN000000", BASE + 1_000), lrow("fee", "MOPEN000000", BASE + 5_000, sol_out=160_000, reason="sell_failed")]
    rep = T.build_report(led, [], [], min_trades=1)
    assert rep["summary"]["n_open"] == 1 and rep["summary"]["n_closed"] == 0 and rep["summary"]["open"][0]["order_id"] == "op"
    led += [exit_with_net("MOPEN000000", BASE + 9_000, 0)]
    dec = [dec_entry("op", BASE - 10, "MOPEN000000"), dec_exit("op", BASE + 8_000, "time_24h")]
    t = T.build_report(led, dec, [fills_buy("MOPEN000000", BASE + 900)], min_trades=1)["trades"][0]
    assert t["net_lamports"] == -160_000 and t["exit_reason"] == "time_24h"


def test_no_fills_log_means_not_measurable():
    led, dec, _ = make([(0.45, "tp_1.5x", 0.0)] * 10)
    s = T.build_report(led, dec, None)["summary"]
    assert s["verdict"] == "no" and any("no fills log given" in c for c in s["verdict_failed_clauses"])


def test_buy_row_matched_by_mint_window_without_signature():
    led, dec, fil = make([(0.45, "tp_1.5x", 0.0)])
    led[1] = buy_fill("M00xxxxxxxx", BASE + 2_000, sig=None)
    r = T.build_report(led, dec, fil, min_trades=1)["trades"][0]
    assert r["buy_match_key"] == "mint_window" and r["entry_slip_pp"] is not None


def test_remint_other_bot_and_duplicate_rows():
    led, dec, fil = make([(0.45, "tp_1.5x", 0.0)])
    led = [dict(intent("M00xxxxxxxx", "zz9", BASE + 1), bot_id="h5-lane-1", row_id="other")] + led + [dict(led[1])]  # a duplicate row_id is counted once
    mint = "M00xxxxxxxx"
    led += [intent(mint, "o00b", BASE + 50_000_000), buy_fill(mint, BASE + 50_001_000, sig="sb2"), exit_with_net(mint, BASE + 50_500_000, 0)]
    dec += [dec_entry("o00b", BASE + 49_999_950, mint, 90), dec_exit("o00b", BASE + 50_400_000, "tp_1.5x", 91)]
    fil.append(fills_buy(mint, BASE + 50_000_900, sig="sb2"))
    rep = T.build_report(led, dec, fil, min_trades=1)
    assert [r["order_id"] for r in rep["trades"]] == ["o00", "o00b"] and rep["trades"][0]["net_lamports"] == int(round(0.45 * S))


def test_since_until_filters():
    led, dec, fil = make([(0.45, "tp_1.5x", 0.0)] * 3)
    r = T.build_report(led, dec, fil, since_ms=BASE + STEP, until_ms=BASE + 2 * STEP, min_trades=1)
    assert [x["order_id"] for x in r["trades"]] == ["o01"]


def test_cli_text_json_readonly(tmp_path, capsys):
    root = tmp_path / "root"
    led, dec, fil = make([(0.45, "tp_1.5x", 0.01)] * 10)
    (tmp_path / "lane").mkdir()
    lp = tmp_path / "lane" / "ledger.jsonl"
    lp.write_text("\n".join(json.dumps(r) for r in led) + "\n{torn")
    d = root / "decisions" / T.BOOK_BOT_ID
    d.mkdir(parents=True)
    (d / "2026-11-10.jsonl").write_text("\n".join(json.dumps(r) for r in dec) + "\n")
    sd = tmp_path / "state"
    (sd / "probe").mkdir(parents=True)
    (sd / "probe" / "fills.jsonl").write_text("\n".join(json.dumps(r) for r in fil) + "\n")
    cfg = tmp_path / "cfg.json"
    cfg.write_text(json.dumps({"root": str(root), "state_dir": str(sd), "lane": {"ledger_path": str(lp)}, "wallet": "NOT-PRINTED", "other_field": "NOT-PRINTED"}))
    before = {f: (f.stat().st_mtime_ns, f.stat().st_size) for f in tmp_path.rglob("*") if f.is_file()}
    assert T.main(["--config", str(cfg)]) == 0
    out = capsys.readouterr().out
    assert "FILLS_MATCH_MODEL yes" in out and "unparseable lines: ledger 1" in out and "NOT-PRINTED" not in out
    assert T.main(["--config", str(cfg), "--json", "--day", "2026-11-10"]) == 0
    j = json.loads(capsys.readouterr().out)
    assert j["summary"]["n_closed"] == 10 and j["summary"]["verdict"] == "yes" and j["inputs"]["fills_rows"] == 10
    assert T.main(["--config", str(cfg), "--json", "--day", "2026-11-09"]) == 0
    assert json.loads(capsys.readouterr().out)["summary"]["n_closed"] == 0
    assert T.main(["--config", str(cfg), "--since", "2026-11-10T01:00:00Z", "--until", "1794273400000", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["summary"]["n_closed"] == 1
    assert {f: (f.stat().st_mtime_ns, f.stat().st_size) for f in tmp_path.rglob("*") if f.is_file()} == before
    # no fills log on disk: still runs, verdict no
    (sd / "probe" / "fills.jsonl").unlink()
    assert T.main(["--config", str(cfg)]) == 0
    assert "fills log not found" in capsys.readouterr().out


def test_cli_errors_and_help(tmp_path, capsys):
    assert T.main([]) == 2
    assert T.main(["--root", str(tmp_path), "--ledger", str(tmp_path / "missing.jsonl")]) == 2
    with pytest.raises(SystemExit):
        T.main(["--help"])
    h = capsys.readouterr().out
    assert "VERDICT RULE" in h and "FILLS_MATCH_MODEL yes" in h and "3.0 pp" in h and "flag_b" in h and "MATCH KEYS" in h


def test_constants_match_code():
    from tools import arena_rule_l1 as L
    from tools import floor_arena_executor as FA
    from tools import floor_lane_executor as FL

    assert T.LANE_BOT_ID == FL.LANE_BOT_ID and T.BOOK_BOT_ID == FL.BOOK_BOT_ID == L.BOT_ID
    assert (L.REASON_TP, L.REASON_TIME) == T.FOLLOWED_EXITS
    assert T.SEND_FEE_LAMPORTS == FA.SEND_FEE_LAMPORTS


def test_derive_qe_recovers_reserve_from_post_buy_spot():
    # the real entry_quote convention: mark = spot after the quoted buy; Q = 60e9, B = 3e14, spend 0.03 SOL
    q, b, spend = 60e9, 3e14, 30_000_000
    net = spend - 75_000
    tok = b * net / (q + net)
    mark_post = (q + net) / (b - tok) / 1000.0
    assert T.derive_qe(spend, 75_000, tok, mark_post) == pytest.approx(q, rel=1e-6)
    assert T.derive_qe(spend, 75_000, tok, (q / b) / 1000.0) is None  # a pre-trade spot is not the convention


def test_min_trades_counts_only_measured_entry_slips():
    led, dec, fil = make([(0.45, "tp_1.5x", 0.0)])
    for i in range(1, 10):  # nine more closed trades that never bought: matched, but no fill to score
        mint, oid, t0 = f"X{i:02d}xxxxxxxx", f"x{i:02d}", BASE + i * STEP
        led += [intent(mint, oid, t0), lrow("writeoff", mint, t0 + 70_000, reason="buy_expired", sig=f"intent:{oid}")]
        dec.append(dec_entry(oid, t0 - 50, mint, 100 + i))
    s = T.build_report(led, dec, fil)["summary"]
    assert s["n_matched"] == 10 and s["n_entry_measured"] == 1 and s["verdict"] == "no"
    assert any("only 1 matched filled trades" in c for c in s["verdict_failed_clauses"])


def test_min_trades_cannot_be_lowered():
    led, dec, fil = make([(0.45, "tp_1.5x", 0.0)] * 3)
    rep = T.build_report(led, dec, fil, min_trades=1)
    assert rep["summary"]["verdict"] == "no" and rep["rule"]["min_trades"] == 10
    led, dec, fil = make([(0.45, "tp_1.5x", 0.0)] * 12)
    assert T.build_report(led, dec, fil, min_trades=13)["summary"]["verdict"] == "no"


def test_fallback_match_skips_claimed_host_entries():
    led, dec, fil = make([(0.45, "tp_1.5x", 0.0), (0.45, "tp_1.5x", 0.0)])
    dec = [d for d in dec if d["order_id"] != "o01"]  # o01's own host entry is missing
    dec[0]["meta"]["mint"] = "M01xxxxxxxx"  # o00's entry (claimed by o00) now looks like a mint/time candidate for o01
    rep = T.build_report(led, dec, fil, min_trades=1)
    by = {r["order_id"]: r for r in rep["trades"]}
    assert by["o00"]["match_key"] == "order_id" and not by["o01"]["matched"]
    assert rep["summary"]["n_unmatched"] == 1


def test_estimated_token_count_is_not_measured():
    led, dec, fil = make([(0.45, "tp_1.5x", 0.0)])
    fil[0]["tokens_source"] = "quote_estimate"
    r = T.build_report(led, dec, fil, min_trades=1)
    t = r["trades"][0]
    assert t["entry_slip_pp"] is None and t["entry_note"] == "estimated_tokens"
    assert r["summary"]["filled_without_entry_slip"] == ["o00"] and r["summary"]["verdict"] == "no"
    fil[0]["tokens_source"], fil[0]["estimated"] = "meta", True
    assert T.build_report(led, dec, fil, min_trades=1)["trades"][0]["entry_note"] == "estimated_tokens"
    fil[0]["estimated"] = False
    assert T.build_report(led, dec, fil, min_trades=1)["trades"][0]["entry_slip_pp"] is not None


def test_intent_on_open_mint_is_counted_and_fails():
    led, dec, fil = make([(0.45, "tp_1.5x", 0.0)] * 10)
    led.insert(0, intent("M00xxxxxxxx", "dropped", BASE - 5_000))  # still open when o00's intent arrives on the same mint
    s = T.build_report(led, dec, fil)["summary"]
    assert s["n_overwritten_open"] == 1 and s["verdict"] == "no" and any("overwrote" in c for c in s["verdict_failed_clauses"])
