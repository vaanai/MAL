"""open sim board v0: --stakes validation, N-stake pnl reader, board subcommand. Synthetic rows only (no tape, no real ledger)."""
import hashlib, json, sys, tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts" / "research"))
import floor_forward_ab as AB  # noqa: E402
import floor_nov_pnl as P  # noqa: E402

FAR = int(datetime(2026, 11, 30, tzinfo=timezone.utc).timestamp() * 1000)
H = 3600_000


def ms(d, h=12, m=0):
    return int(datetime(2026, 11, d, h, m, tzinfo=timezone.utc).timestamp() * 1000)


def op(pid, t):
    return {"kind": "open", "position_id": pid, "pool": "POOL_" + pid, "mint": "MINT_" + pid, "decision_ms": {"entry": t}}


def tr(pid, stake, net, t0, t1=None, reason="time_stop", state="filled"):
    legs = {f"{m}_1p9": {"net_lamports": int(net * stake / 0.25 * (1 if m == "flat" else 0.9)), "state": state} for m in ("flat", "press")}
    return {"kind": "trade", "position_id": pid, "stake_sol": stake, "exit_reason": reason, "decision_ms": {"entry": t0, "exit": t1 or t0 + 1000},
            "legs": {"total": legs}, "calc": {"1p9": {"Qe": 18_100_000_000, "Be": 1_000_000_000_000}}, "meta": {"v": 17_600_000_000, "mark0": 1e-8}}


def ex(pid, stake):
    return {"kind": "excluded", "position_id": pid, "stake_sol": stake, "exclusion_reason": "feed_gap"}


def golden_rows(stakes=(0.10, 0.25)):
    r = []
    for pid, t, net, reason in (("a", ms(5, 1), 5_000_000, "tp_1.5x"), ("b", ms(5, 3), -4_000_000, "time_stop"), ("c", ms(10, 2), 2_000_000, "tp_1.5x"),
                                ("d", ms(12, 2), -3_000_000, "time_stop"), ("e", ms(12, 4), 7_000_000, "tp_1.5x")):
        r += [op(pid, t)] + [tr(pid, s, net, t, t + 2 * H if reason.startswith("tp") else t + 24 * H + 1900, reason) for s in stakes]
    r += [op("g", ms(12, 6))] + [ex("g", s) for s in stakes]
    r += [op("o", ms(13, 2))]
    return r


def run_pnl(rows, to_ms=FAR):
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "ledger" / "l1-floor-v2"
        p.mkdir(parents=True)
        (p / "trades-x.jsonl").write_text("\n".join(json.dumps(x) for x in rows) + "\n")
        P.main([d + "/ledger", d + "/o.json", "--to-ms", str(to_ms)])
        return json.load(open(d + "/o.json"))


def test_two_stake_pnl_golden():
    """digest of the pre-change output (origin/main f35a7e40) for golden_rows(); the new per-position `t1` key is the only addition and is removed first"""
    res = run_pnl(golden_rows())
    for p in res["positions"]:
        p.pop("t1", None)
    assert hashlib.sha256(json.dumps(res, sort_keys=True, default=str).encode()).hexdigest() == GOLDEN_PNL


GOLDEN_PNL = "6b758afcd4fb806363fe3353b6ad59a6233de6271269a6778008bae7646de03d"
