"""floor_nov_pnl: the --send-lamports restatement must equal pricing directly at the lower send through the same stack function
(tools/arena_paper.price_leg, which applies tools/arena_stack fail_legs / book_fail_legs). That equivalence is the specification."""
import dataclasses, json, sys, tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts" / "research"))
import floor_nov_pnl as P  # noqa: E402
from tools import arena_paper as A  # noqa: E402
from tools.arena_stack_v2 import stack_config_for  # noqa: E402

LAT, STAKE, OLD, NEW = 1.9, 0.1e9, 1_673_840, 160_000
CFG = dataclasses.replace(stack_config_for(2, 7), send=OLD)
CFG_NEW = dataclasses.replace(CFG, send=NEW)


def ms(d, h=12):
    return int(datetime(2026, 11, d, h, tzinfo=timezone.utc).timestamp() * 1000)


def st(qe, be, qx, bx, ref):
    return {"ref": ref, "Qe": {LAT: qe}, "Be": {LAT: be}, "ssb": {LAT: 3.0}, "nearby": {LAT: 1e7}, "Qx": qx, "Bx": bx}


CASES = {  # name -> (pool state, expected ledger state)
    "filled": (st(20e9, 1e15, 24e9, 0.85e15, 20e9 / 1e15), "filled"),
    "guarded": (st(20e9, 1e15, 24e9, 0.85e15, 1e-6), "guarded"),
    "stranded": (st(20e9, 1e15, float("nan"), 1e15, 20e9 / 1e15), "exit_invalid"),
}


def trade_row(pid, stake_sol, res, t0, t1):
    legs = {f"{m}_1p9": {"net_lamports": res[m], "state": res["state"]} for m in ("flat", "press")}
    return {"kind": "trade", "position_id": pid, "stake_sol": stake_sol, "exit_reason": "time_24h", "decision_ms": {"entry": t0, "exit": t1}, "legs": {"total": legs},
            "calc": {"1p9": {"by_rule": {"total": {"p_press_provisional": res["p"]}}}}}


def mk_ledger(d, results):
    rows = []
    for i, (name, res) in enumerate(results.items()):
        pid = "p%d" % i
        rows.append({"kind": "open", "position_id": pid, "pool": name, "decision_ms": {"entry": ms(5, 1 + i)}})
        for sol in (0.10, 0.25):
            rows.append(trade_row(pid, sol, res, ms(5, 1 + i), ms(5, 20)))
    p = Path(d) / "ledger" / "l1-floor-v2"; p.mkdir(parents=True)
    (p / "trades-2026-11-05.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return str(Path(d) / "ledger")


def price(cfg):
    return {n: A.price_leg("pumpswap", STAKE, s, LAT, cfg, "total") for n, (s, _) in CASES.items()}


def test_cases_cover_the_three_states():
    old = price(CFG)
    assert {n: r["state"] for n, r in old.items()} == {n: e for n, (_, e) in CASES.items()}
    assert 0 < old["filled"]["p"] < 1


def test_addback_equals_direct_repricing_to_the_lamport():
    old, new, D = price(CFG), price(CFG_NEW), OLD - NEW
    for n in CASES:
        for model in ("flat", "press"):
            got = old[n][model] + P.send_addback(old[n]["state"], D, model, old[n]["p"])
            assert abs(got - new[n][model]) < 1e-3, (n, model, got, new[n][model])
    # the shape of it: filled moves by (2-f)*D, not 2*D; guarded and stranded by D
    assert abs((new["filled"]["flat"] - old["filled"]["flat"]) - 1.85 * D) < 1e-3
    assert abs((new["filled"]["press"] - old["filled"]["press"]) - (2 - old["filled"]["p"]) * D) < 1e-3
    assert abs((new["guarded"]["flat"] - old["guarded"]["flat"]) - D) < 1e-3
    assert abs((new["stranded"]["flat"] - old["stranded"]["flat"]) - D) < 1e-3


def test_reader_end_to_end_matches_direct_pricing_per_day_totals():
    old, new = price(CFG), price(CFG_NEW)
    with tempfile.TemporaryDirectory() as d:
        P.main([mk_ledger(d, old), d + "/o.json", "--send-lamports", str(NEW)])
        r = json.load(open(d + "/o.json"))
        assert r["send_delta_lamports"] == OLD - NEW
        for model in ("flat", "press"):
            for stake in ("0.1", "0.25"):
                day = r["entries_by_day"][f"{stake}:{model}"]["2026-11-05"]
                assert (day["n_entries"], day["n_closed"], day["n_open_at_tape_end"]) == (3, 3, 0)
                assert abs(day["total_sol"] * 1e9 - sum(x[model] for x in new.values())) < 1e-2


def test_no_flag_leaves_nets_alone_and_target_send_needs_none():
    old = price(CFG)
    with tempfile.TemporaryDirectory() as d:
        P.main([mk_ledger(d, old), d + "/o.json"])
        day = json.load(open(d + "/o.json"))["entries_by_day"]["0.1:flat"]["2026-11-05"]
        assert abs(day["total_sol"] * 1e9 - sum(x["flat"] for x in old.values())) < 1e-2
    with tempfile.TemporaryDirectory() as d:
        P.main([mk_ledger(d, old), d + "/o.json", "--send-lamports", str(OLD)])
        assert json.load(open(d + "/o.json"))["send_delta_lamports"] == 0


def test_unfilled_leg_counts_as_open_at_tape_end_not_feed_gap():
    old = price(CFG)
    with tempfile.TemporaryDirectory() as d:
        led = mk_ledger(d, old)
        f = Path(led) / "l1-floor-v2" / "trades-2026-11-05.jsonl"
        f.write_text(f.read_text() + json.dumps({"kind": "open", "position_id": "x", "pool": "x", "decision_ms": {"entry": ms(5, 22)}}) + "\n")
        P.main([led, d + "/o.json"])
        day = json.load(open(d + "/o.json"))["entries_by_day"]["0.1:flat"]["2026-11-05"]
        assert (day["n_entries"], day["n_closed"], day["n_engine_excluded"], day["n_open_at_tape_end"]) == (4, 3, 0, 1)
        assert day["complete"] is False and day["total_sol"] is None
