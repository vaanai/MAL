import json

from tools.slowbot import report as R


def ex(i, arm, net, day="2026-11-05", reason="stop"):
    return {"kind": "exit", "id": f"{arm}-{i}", "arm": arm, "net_sol": net, "ts_utc": f"{day}T10:00:00Z", "reason": reason}


def write(p, rows, extra=""):
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n" + extra)


def test_arm_table(tmp_path):
    rows = [ex(1, "T", 0.01), ex(2, "T", -0.003), ex(3, "T", 0.5, "2026-11-06"), ex(4, "T", 0.02), ex(5, "T", -0.004, "2026-11-06"),
            ex(1, "H", -0.003), ex(1, "R", 0.002), {"kind": "price", "id": "x", "arm": "T"}]
    rows.append(rows[0])  # duplicate exit after a crash: counted once
    p = tmp_path / "l.jsonl"
    write(p, rows, extra='{"kind": "exit", "id"')  # torn tail ignored
    rep = R.build_report(R.load_exits(p))
    t = rep["arms"]["T"]
    assert t["trades"] == 5 and abs(t["net_sol"] - 0.523) < 1e-12
    assert abs(t["ex_top3_sol"] - (-0.003 - 0.004)) < 1e-12
    assert t["win_rate"] == 3 / 5 and [d["day"] for d in t["days"]] == ["2026-11-05", "2026-11-06"]
    assert t["days_positive"] == 2 and t["exit_reasons"] == {"stop": 5}
    assert rep["arms"]["H"]["trades"] == 1 and rep["arms"]["R"]["net_sol"] == 0.002
    assert "T" in R.format_table(rep)


def test_empty_and_cli(tmp_path, capsys):
    p = tmp_path / "l.jsonl"
    p.write_text("")
    out = tmp_path / "arms.json"
    assert R.main(["--log", str(p), "--out", str(out)]) == 0
    j = json.loads(out.read_text())
    assert j["arms"]["T"]["trades"] == 0 and j["arms"]["T"]["win_rate"] is None and j["last_exit_utc"] is None
