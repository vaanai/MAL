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
        {"kind": "price", "id": "T-1", "arm": "T", "sell_out_lamports": 30_000_000, "proceeds_lamports": 0,
         "fees_lamports": 2_339_280},
        {"kind": "open", "id": "T-2", "arm": "T", "mint": "b", "cost_lamports": 25_000_000},
        {"kind": "price_miss", "id": "T-2", "arm": "T"},
        {"kind": "refused", "arm": "H", "mint": "c", "reason": "arm_full"},
        {"kind": "refused", "arm": "H", "mint": "d", "reason": "arm_full"},
    ]
    rep = R.build_report(rows)
    t, h = rep["arms"]["T"], rep["arms"]["H"]
    # T-1 untaken: 30M - 25M - 2.33928M - 0.3M ; T-2 stuck, never polled with fees: 0 - 25M - 0 - 0.3M
    assert t["open_now"] == 2 and t["stuck_now"] == 1
    assert abs(t["open_marked_sol"] - ((30_000_000 - 25_000_000 - 2_339_280 - 300_000) + (-25_000_000 - 300_000)) / 1e9) < 1e-12
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


def test_open_marking_taken_half_and_stuck_after_take():
    cost, fees_open = 25_000_000, 300_000 + 2_039_280
    rows = [
        {"kind": "open", "id": "H-1", "arm": "H", "mint": "a", "cost_lamports": cost, "fee_lamports": 300_000,
         "ata_rent_lamports": 2_039_280},
        # take-half booked 18M proceeds and a second tx fee; remainder now quotes 17M
        {"kind": "price", "id": "H-1", "arm": "H", "sell_out_lamports": 17_000_000, "proceeds_lamports": 18_000_000,
         "fees_lamports": fees_open + 300_000},
        {"kind": "open", "id": "H-2", "arm": "H", "mint": "b", "cost_lamports": cost, "fee_lamports": 300_000,
         "ata_rent_lamports": 2_039_280},
        {"kind": "price", "id": "H-2", "arm": "H", "sell_out_lamports": 17_000_000, "proceeds_lamports": 18_000_000,
         "fees_lamports": fees_open + 300_000},
        {"kind": "price_miss", "id": "H-2", "arm": "H", "proceeds_lamports": 18_000_000, "fees_lamports": fees_open + 300_000},
    ]
    h = R.build_report(rows)["arms"]["H"]
    taken = (18_000_000 + 17_000_000 - cost - fees_open - 300_000 - 300_000) / 1e9
    stuck = (18_000_000 - cost - fees_open - 300_000 - 300_000) / 1e9
    assert h["stuck_now"] == 1 and abs(h["open_marked_sol"] - (taken + stuck)) < 1e-12
    assert taken > 0 > stuck
