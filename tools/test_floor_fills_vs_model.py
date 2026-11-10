"""Tests for tools/floor_fills_vs_model.py: synthetic fixtures in the real row formats (lane ledger, host decision log, paper ledger)."""

from __future__ import annotations

import json

import pytest

from tools import floor_fills_vs_model as T

S = 30_000_000  # live stake
BASE = 1_794_268_800_000 + 3_600_000  # 2026-11-10T01:00:00Z in ms
STEP = 1_000_000
LANE = "floor-lane-1"
BUY_OUT = S + 2_100_000 + 160_000


def lrow(ev, mint, ts, *, sol_in=0, sol_out=0, sig=None, **kw):
    return {"schema": "h5_lane_v1", "bot_id": LANE, "row_id": f"r{ev}{mint}{ts}", "ev": ev, "ts_ms": ts, "mint": mint, "signature": sig, "sol_in_lamports": sol_in,
            "sol_out_lamports": sol_out, "estimated": False, **kw}


def intent(mint, oid, ts):
    return lrow("send_intent", mint, ts, sig=f"intent:{oid}", stake_lamports=S)


def buy_fill(mint, ts, out=BUY_OUT, **kw):
    return lrow("fill", mint, ts, ok=True, sol_out=out, sig="sigb", fee_network_lamports=5_000, fee_priority_lamports=155_000, rent_paid_lamports=2_100_000, **kw)


def exit_with_net(mint, ts, total_net, buy_out=BUY_OUT):
    """An exit row such that (sum of in - out over fill + exit) == total_net."""
    sol_out = 160_000
    return lrow("exit", mint, ts, sol_in=total_net + buy_out + sol_out, sol_out=sol_out, sig="sigs", fee_network_lamports=5_000, fee_priority_lamports=155_000,
                rent_refunded_lamports=2_100_000)


def dec_entry(oid, ms, seq=0):
    return {"seq": seq, "arm": T.BOOK_BOT_ID, "ms": ms, "kind": "entry", "order_id": oid, "stake_sol": 0.25}


def dec_exit(oid, ms, reason, seq=1):
    return {"seq": seq, "arm": T.BOOK_BOT_ID, "ms": ms, "kind": "exit", "order_id": oid, "reason": reason, "attempt": 1}


def paper(oid, net_flat, rule="total", kind="trade", send=160_000):
    r = {"kind": kind, "position_id": oid, "stake_sol": 0.25, "bot_id": T.BOOK_BOT_ID, "stack": {"send": send}}
    if kind == "trade":
        r["legs"] = {rule: {"flat_1p9": {"net_lamports": net_flat}, "press_1p9": {"net_lamports": net_flat - 1_000_000}, "flat_1p3": {"net_lamports": net_flat + 1}}}
    else:
        r["exclusion_reason"] = "feed_gap"
        r["legs"] = None
    return r


def make(specs):
    """specs: (live return, model return, exit reason) per trade -> (ledger rows, decision rows, paper rows)."""
    led, dec, pap = [], [], []
    for i, (live_ret, model_ret, reason) in enumerate(specs):
        oid, mint, t0 = f"o{i:02d}", f"M{i:02d}xxxxxxxx", BASE + i * STEP
        led += [intent(mint, oid, t0), buy_fill(mint, t0 + 2_000), exit_with_net(mint, t0 + 3_600_000, int(round(live_ret * S)))]
        dec += [dec_entry(oid, t0 - 50, 2 * i), dec_exit(oid, t0 + 3_599_000, reason, 2 * i + 1)]
        pap.append(paper(oid, int(round(model_ret * 0.25e9))))
    return led, dec, pap


def test_matched_trades_gap_and_split():
    led, dec, pap = make([(0.5, 0.5, "tp_1.5x"), (-0.02, 0.0, "time_24h")])
    rep = T.build_report(led, dec, pap, min_trades=2)
    t0, t1 = rep["trades"]
    assert t0["matched"] and t0["order_id"] == "o00" and t0["exit_reason"] == "tp_1.5x"
    assert t0["gap_pp"] == pytest.approx(0.0, abs=1e-4)
    assert t1["exit_reason"] == "time_24h" and t1["gap_pp"] == pytest.approx(-2.0, abs=1e-4)
    # live fee fields: buy 160k + sell 160k = 320k over 30M; model 2 x 160k over 0.25 SOL
    assert t1["live_fees_pp"] == pytest.approx(320_000 / S * 100)
    assert t1["model_fees_pp"] == pytest.approx(0.128)
    assert t1["fee_gap_pp"] == pytest.approx(-(320_000 / S * 100 - 0.128))
    assert t1["price_gap_pp"] == pytest.approx(t1["gap_pp"] - t1["fee_gap_pp"])
    assert t1["entry_exit_split"].startswith("unavailable")
    assert t1["entry_lag_ms"] == 50 and t1["entry_ts_ms"] == BASE + STEP and t1["exit_ts_ms"] == BASE + STEP + 3_600_000
    s = rep["summary"]
    assert s["n_matched"] == 2 and s["n_unmatched"] == 0
    assert s["mean_gap_pp"] == pytest.approx(-1.0, abs=1e-4) and s["median_gap_pp"] == pytest.approx(-1.0, abs=1e-4) and s["worst_gap_pp"] == pytest.approx(-2.0, abs=1e-4)
    assert s["verdict"] == "yes"


def test_verdict_clauses():
    led, dec, pap = make([(-0.05, 0.0, "tp_1.5x")] * 10)  # median beyond 3 pp
    s = T.build_report(led, dec, pap)["summary"]
    assert s["verdict"] == "no" and any("median" in c for c in s["verdict_failed_clauses"])
    led, dec, pap = make([(0.0, 0.0, "tp_1.5x")] * 9 + [(-0.16, 0.0, "time_24h")])  # one gap below -15 pp, median fine
    s = T.build_report(led, dec, pap)["summary"]
    assert s["verdict"] == "no" and any("worst gap" in c for c in s["verdict_failed_clauses"])
    led, dec, pap = make([(0.01, 0.0, "tp_1.5x")] * 10)
    assert T.build_report(led, dec, pap)["summary"]["verdict"] == "yes"
    led, dec, pap = make([(0.0, 0.0, "tp_1.5x")] * 9)  # fewer than 10 matched
    s = T.build_report(led, dec, pap)["summary"]
    assert s["verdict"] == "no" and any("only 9" in c for c in s["verdict_failed_clauses"])
    led, dec, pap = make([(-0.03, 0.0, "tp_1.5x")] * 9 + [(-0.15, 0.0, "tp_1.5x")])  # exactly on the edges: passes
    assert T.build_report(led, dec, pap)["summary"]["verdict"] == "yes"


def test_unmatched_trade_listed_and_fails_verdict():
    led, dec, pap = make([(0.0, 0.0, "tp_1.5x")] * 10)
    led += [intent("MUNMATCH0000", "zz", BASE + 99_000_000), buy_fill("MUNMATCH0000", BASE + 99_002_000), exit_with_net("MUNMATCH0000", BASE + 99_500_000, 1)]
    s = T.build_report(led, dec, pap)["summary"]
    assert s["n_closed"] == 11 and s["n_matched"] == 10 and s["n_unmatched"] == 1
    assert s["unmatched"][0]["order_id"] == "zz" and s["unmatched"][0]["reason"] == "no_decision_entry"
    assert s["verdict"] == "no"
    led2, dec2, pap2 = make([(0.0, 0.0, "tp_1.5x")] * 2)  # a decision with an excluded or absent paper trade is unmatched too
    pap2[1] = paper("o01", 0, kind="excluded")
    s2 = T.build_report(led2, dec2, pap2, min_trades=1)["summary"]
    assert s2["n_unmatched"] == 1 and s2["unmatched"][0]["reason"].startswith("paper_excluded")
    assert T.build_report(led2, dec2, pap2[:1], min_trades=1)["summary"]["unmatched"][0]["reason"] == "no_paper_row"


def test_writeoff_not_sent_is_not_a_trade():
    led, dec, pap = make([(0.0, 0.0, "tp_1.5x")])
    led += [intent("MREFUSED000", "rf", BASE + 5), lrow("writeoff", "MREFUSED000", BASE + 12, reason="not_sent", sig="intent:rf")]
    s = T.build_report(led, dec, pap, min_trades=1)["summary"]
    assert s["n_closed"] == 1 and s["n_not_sent_excluded"] == 1 and s["n_unmatched"] == 0 and s["n_open"] == 0


def test_buy_landed_with_error_and_expired_buy_are_trades():
    led, dec, pap = [], [], []
    for oid in ("e1", "e2"):
        dec += [dec_entry(oid, BASE - 10)]
        pap += [paper(oid, 50_000_000)]  # model +20%
    led += [intent("MERR0000000", "e1", BASE), lrow("fill", "MERR0000000", BASE + 900, ok=False, sol_out=165_000, sig="x", fee_network_lamports=5_000, fee_priority_lamports=155_000)]
    led += [intent("MEXP0000000", "e2", BASE + STEP), lrow("writeoff", "MEXP0000000", BASE + 70_000 + STEP, reason="buy_expired", sig="intent:e2")]
    rep = T.build_report(led, dec, pap, min_trades=1)
    by = {r["order_id"]: r for r in rep["trades"]}
    assert by["e1"]["close"] == "buy_error" and by["e1"]["exit_reason"] == "buy_error" and by["e1"]["net_lamports"] == -165_000
    assert by["e1"]["live_return"] == pytest.approx(-165_000 / S)
    assert by["e2"]["exit_reason"] == "writeoff:buy_expired" and by["e2"]["net_lamports"] == 0
    assert rep["summary"]["n_zero_cash"] == 1 and rep["summary"]["n_closed"] == 2
    assert by["e2"]["gap_pp"] == pytest.approx(-20.0, abs=1e-6) and rep["summary"]["verdict"] == "no"


def test_open_trade_listed_not_scored_then_failed_sell_fee_in_sum():
    led = [intent("MOPEN000000", "op", BASE), buy_fill("MOPEN000000", BASE + 1_000), lrow("fee", "MOPEN000000", BASE + 5_000, sol_out=160_000, reason="sell_failed")]
    rep = T.build_report(led, [], [], min_trades=1)
    assert rep["summary"]["n_open"] == 1 and rep["summary"]["n_closed"] == 0 and rep["summary"]["open"][0]["order_id"] == "op"
    led += [exit_with_net("MOPEN000000", BASE + 9_000, 0)]  # buy fill and exit net to 0; the failed sell's fee row is also in the sum
    dec = [dec_entry("op", BASE - 10), dec_exit("op", BASE + 8_000, "time_24h")]
    t = T.build_report(led, dec, [paper("op", 0)], min_trades=1)["trades"][0]
    assert t["net_lamports"] == -160_000 and t["exit_reason"] == "time_24h"


def test_remint_starts_new_trade_and_other_bot_ignored():
    led, dec, pap = make([(0.0, 0.0, "tp_1.5x")])
    led = [dict(intent("M00xxxxxxxx", "zz9", BASE + 1), bot_id="h5-lane-1", row_id="other")] + led
    mint = "M00xxxxxxxx"
    led += [intent(mint, "o00b", BASE + 50_000_000), buy_fill(mint, BASE + 50_001_000), exit_with_net(mint, BASE + 50_500_000, 0)]
    dec += [dec_entry("o00b", BASE + 49_999_950, 90), dec_exit("o00b", BASE + 50_400_000, "tp_1.5x", 91)]
    pap += [paper("o00b", 0)]
    assert [r["order_id"] for r in T.build_report(led, dec, pap, min_trades=1)["trades"]] == ["o00", "o00b"]


def test_duplicate_row_ids_counted_once():
    led, dec, pap = make([(0.01, 0.0, "tp_1.5x")])
    led = led + [dict(led[1])]
    assert T.build_report(led, dec, pap, min_trades=1)["trades"][0]["net_lamports"] == int(0.01 * S)


def test_model_options_and_paper_stake_filter():
    led, dec, pap = make([(0.0, 0.0, "tp_1.5x")])
    pap.append(dict(paper("o00", 99_000_000), stake_sol=0.5))  # a different stake is ignored
    r = T.build_report(led, dec, pap, fail="press", min_trades=1)["trades"][0]
    assert r["model_return"] == pytest.approx(-1_000_000 / 0.25e9)
    r = T.build_report(led, dec, pap, rule="sc", min_trades=1)["trades"][0]
    assert not r["matched"] and r["unmatched_reason"].startswith("no_leg")


def test_since_until_filters():
    led, dec, pap = make([(0.0, 0.0, "tp_1.5x")] * 3)
    r = T.build_report(led, dec, pap, since_ms=BASE + STEP, until_ms=BASE + 2 * STEP, min_trades=1)
    assert [x["order_id"] for x in r["trades"]] == ["o01"]


def test_cli_text_json_readonly(tmp_path, capsys):
    root = tmp_path / "root"
    led, dec, pap = make([(0.01, 0.0, "tp_1.5x")] * 10)
    (tmp_path / "lane").mkdir()
    lp = tmp_path / "lane" / "ledger.jsonl"
    lp.write_text("\n".join(json.dumps(r) for r in led) + "\n{torn")
    d = root / "decisions" / T.BOOK_BOT_ID
    d.mkdir(parents=True)
    (d / "2026-11-10.jsonl").write_text("\n".join(json.dumps(r) for r in dec) + "\n")
    p = root / "ledger" / T.BOOK_BOT_ID
    p.mkdir(parents=True)
    (p / "trades-2026-11-10.jsonl").write_text("\n".join(json.dumps(r) for r in pap) + "\n")
    cfg = tmp_path / "cfg.json"
    cfg.write_text(json.dumps({"root": str(root), "lane": {"ledger_path": str(lp)}, "wallet": "NOT-PRINTED", "other_field": "NOT-PRINTED"}))
    before = {f: (f.stat().st_mtime_ns, f.stat().st_size) for f in tmp_path.rglob("*") if f.is_file()}
    assert T.main(["--config", str(cfg)]) == 0
    out = capsys.readouterr().out
    assert "FILLS_MATCH_MODEL yes" in out and "unparseable lines: ledger 1" in out and "NOT-PRINTED" not in out
    assert T.main(["--config", str(cfg), "--json", "--day", "2026-11-10"]) == 0
    j = json.loads(capsys.readouterr().out)
    assert j["summary"]["n_closed"] == 10 and j["summary"]["verdict"] == "yes" and j["inputs"]["unparseable_lines"]["ledger"] == 1
    assert T.main(["--config", str(cfg), "--json", "--day", "2026-11-09"]) == 0
    assert json.loads(capsys.readouterr().out)["summary"]["n_closed"] == 0
    assert T.main(["--config", str(cfg), "--since", "2026-11-10T01:00:00Z", "--until", "1794273400000", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["summary"]["n_closed"] == 1  # only the trade at BASE (the next is BASE + STEP = the until instant)
    after = {f: (f.stat().st_mtime_ns, f.stat().st_size) for f in tmp_path.rglob("*") if f.is_file()}
    assert before == after


def test_cli_errors_and_help(tmp_path, capsys):
    assert T.main([]) == 2
    assert T.main(["--root", str(tmp_path), "--ledger", str(tmp_path / "missing.jsonl")]) == 2
    with pytest.raises(SystemExit):
        T.main(["--help"])
    h = capsys.readouterr().out
    assert "VERDICT RULE" in h and "FILLS_MATCH_MODEL yes" in h and "3.0 pp" in h and "-15.0 pp" in h


def test_constants_match_code():
    from tools import arena_rule_l1 as L
    from tools import floor_lane_executor as FL

    assert T.LANE_BOT_ID == FL.LANE_BOT_ID and T.BOOK_BOT_ID == FL.BOOK_BOT_ID == L.BOT_ID
    assert T.PAPER_STAKE_SOL == L.STAKE_SOL
    assert (L.REASON_TP, L.REASON_TIME) == T.FOLLOWED_EXITS
