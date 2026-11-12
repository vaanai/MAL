"""floor_nov_pnl: rent add-back (--send-lamports) and entries_by_day on a tiny synthetic ledger."""
import json, sys, tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts" / "research"))
import floor_nov_pnl as P  # noqa: E402


def ms(d, h=12):
    return int(datetime(2026, 11, d, h, tzinfo=timezone.utc).timestamp() * 1000)


def leg(net, state):
    return {"net_lamports": net, "state": state}


def trade(pid, stake, net, state, t0, t1):
    legs = {f"{m}_1p9": leg(net, state) for m in ("flat", "press")}
    return {"kind": "trade", "position_id": pid, "stake_sol": stake, "exit_reason": "tp_1.5x" if net > 0 else "time_24h",
            "decision_ms": {"entry": t0, "exit": t1}, "legs": {"total": legs}}


def mk_ledger(d):
    rows = [
        {"kind": "open", "position_id": "a", "pool": "A", "decision_ms": {"entry": ms(5)}},
        {"kind": "open", "position_id": "b", "pool": "B", "decision_ms": {"entry": ms(5, 13)}},   # guarded
        {"kind": "open", "position_id": "c", "pool": "C", "decision_ms": {"entry": ms(6)}},       # unpriced (no trade row)
        trade("a", 0.10, 10_000_000, "filled", ms(5), ms(5, 20)), trade("a", 0.25, 20_000_000, "filled", ms(5), ms(5, 20)),
        trade("b", 0.10, -1_700_000, "guarded", ms(5, 13), ms(5, 14)), trade("b", 0.25, -1_700_000, "guarded", ms(5, 13), ms(5, 14)),
    ]
    p = Path(d) / "ledger" / "l1-floor-v2"; p.mkdir(parents=True)
    (p / "trades-2026-11-05.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return str(Path(d) / "ledger")


def run(led, out, *extra):
    P.main([led, out, *extra])
    return json.load(open(out))


def test_no_flag_leaves_nets_alone_and_counts_unpriced():
    with tempfile.TemporaryDirectory() as d:
        r = run(mk_ledger(d), d + "/o.json")
        day5 = r["entries_by_day"]["0.1:flat"]["2026-11-05"]
        assert (day5["n_entries"], day5["n_priced"], day5["n_unpriced"]) == (2, 2, 0)
        assert abs(day5["total_sol"] - (10_000_000 - 1_700_000) / 1e9) < 1e-12
        day6 = r["entries_by_day"]["0.1:flat"]["2026-11-06"]
        assert (day6["n_entries"], day6["n_priced"], day6["n_unpriced"]) == (1, 0, 1)
        assert r["send_delta_lamports"] == 0


def test_send_lamports_adds_back_rent_two_sends_filled_one_guarded():
    with tempfile.TemporaryDirectory() as d:
        r = run(mk_ledger(d), d + "/o.json", "--send-lamports", "160000")
        delta = 1_673_840 - 160_000
        assert r["send_delta_lamports"] == delta
        want = (10_000_000 + 2 * delta) + (-1_700_000 + delta)
        for k in ("0.1:flat", "0.1:press", "0.25:flat"):
            day5 = r["entries_by_day"][k]["2026-11-05"]
            if k.startswith("0.25"):
                want25 = (20_000_000 + 2 * delta) + (-1_700_000 + delta)
                assert abs(day5["total_sol"] - want25 / 1e9) < 1e-12
            else:
                assert abs(day5["total_sol"] - want / 1e9) < 1e-12


def test_ledger_already_at_target_send_needs_no_correction():
    with tempfile.TemporaryDirectory() as d:
        r = run(mk_ledger(d), d + "/o.json", "--send-lamports", "160000", "--ledger-send", "160000")
        assert r["send_delta_lamports"] == 0
