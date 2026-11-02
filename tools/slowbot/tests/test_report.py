import json

from tools.slowbot import report as R


def ex(i, arm, net, day="2026-11-05", reason="stop", mint=None):
    return {"kind": "exit", "id": f"{arm}-{i}", "arm": arm, "net_sol": net, "ts_utc": f"{day}T10:00:00Z", "reason": reason,
            "mint": mint or f"m{i}"}


def write(p, rows, extra=""):
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n" + extra)


def test_arm_table(tmp_path):
    rows = [ex(1, "T", 0.01), ex(2, "T", -0.003), ex(3, "T", 0.5, "2026-11-06"), ex(4, "T", 0.02),
            ex(5, "T", -0.004, "2026-11-06", reason="no_quote_writeoff"),
            ex(1, "H", -0.003), ex(2, "H", -0.01), ex(1, "R", 0.002), {"kind": "price", "id": "x", "arm": "T"}]
    rows.append(rows[0])
    p = tmp_path / "l.jsonl"
    write(p, rows, extra='{"kind": "exit", "id"')
    rep = R.build_report(R._rows(p))
    t = rep["arms"]["T"]
    assert t["trades"] == 5 and abs(t["net_sol"] - 0.523) < 1e-12
    assert abs(t["ex_top3_sol"] - (-0.003 - 0.004)) < 1e-12
    assert t["win_rate"] == 3 / 5 and t["days_positive"] == 2 and t["writeoffs"] == 1
    assert rep["arms"]["H"]["ex_top3_sol"] is None and rep["arms"]["R"]["ex_top3_sol"] is None  # n <= 3
    assert "n/a" in R.format_table(rep)


def test_open_stuck_and_refused():
    rows = [
        {"kind": "open", "id": "T-1", "arm": "T", "mint": "a", "cost_lamports": 25_000_000},
        {"kind": "price", "id": "T-1", "arm": "T", "sell_out_lamports": 30_000_000},
        {"kind": "open", "id": "T-2", "arm": "T", "mint": "b", "cost_lamports": 25_000_000},
        {"kind": "price_miss", "id": "T-2", "arm": "T"},
        {"kind": "refused", "arm": "H", "mint": "c", "reason": "arm_full"},
        {"kind": "refused", "arm": "H", "mint": "d", "reason": "arm_full"},
    ]
    rep = R.build_report(rows)
    t, h = rep["arms"]["T"], rep["arms"]["H"]
    assert t["open_now"] == 2 and t["stuck_now"] == 1 and abs(t["open_marked_sol"] - 0.005) < 1e-12
    assert h["refused"] == {"arm_full": 2} and h["candidates_offered"] == 2 and h["opened"] == 0


def test_decision_section_and_r_control():
    d = []
    for i in range(10):
        d.append({"ts_utc": "2026-11-05T01:00:00Z", "mint": f"m{i}", "action": "BUY" if i < 3 else "SKIP", "h_buy": i < 3,
                  "decision_error": None})
    d.append({"ts_utc": "2026-11-05T02:00:00Z", "mint": "mE", "action": "SKIP", "h_buy": False,
              "decision_error": "caller_BudgetExceeded"})
    for i in range(7):
        d.append({"kind": "r_draw", "ts_utc": "2026-11-05T01:00:00Z", "mint": f"m{i}", "seed": 1, "skip_rate": 0.7,
                  "skipped": True})
    rows = [ex(1, "T", 0.1, mint="mE"), ex(2, "T", -0.1)]
    rep = R.build_report(rows, d)
    day = rep["decisions"]["days"]["2026-11-05"]
    assert day["h_model_skips"] == 7 and day["h_error_skips"] == 1 and day["r_skips"] == 7 and day["r_seeds"] == [1]
    assert day["R_CONTROL_INVALID"] is False
    assert rep["arms_ex_decision_error"]["T"]["trades"] == 1 and rep["arms"]["T"]["trades"] == 2
    d.extend({"kind": "r_draw", "ts_utc": "2026-11-05T01:00:00Z", "mint": "z", "seed": 1, "skipped": True} for _ in range(5))
    rep2 = R.build_report(rows, d)
    assert rep2["decisions"]["days"]["2026-11-05"]["R_CONTROL_INVALID"] is True
    assert "R_CONTROL_INVALID" in R.format_table(rep2)


def test_empty_and_cli(tmp_path):
    p = tmp_path / "l.jsonl"
    p.write_text("")
    out = tmp_path / "arms.json"
    assert R.main(["--log", str(p), "--out", str(out)]) == 0
    j = json.loads(out.read_text())
    assert j["arms"]["T"]["trades"] == 0 and j["arms"]["T"]["win_rate"] is None and j["last_exit_utc"] is None
