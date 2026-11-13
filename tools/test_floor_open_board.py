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


def test_four_stake_ledger_gains_stakes_and_keeps_old_numbers():
    two, four = run_pnl(golden_rows()), run_pnl(golden_rows((0.10, 0.25, 0.5, 1.0)))
    assert set(two["entries_by_day"]) == {"0.1:flat", "0.1:press", "0.25:flat", "0.25:press"}
    assert set(four["entries_by_day"]) == {f"{s}:{m}" for s in ("0.1", "0.25", "0.5", "1.0") for m in ("flat", "press")}
    for k in two["entries_by_day"]:
        assert two["entries_by_day"][k] == four["entries_by_day"][k]
    p = four["positions"][0]
    assert set(p["net"]) == {f"{s}:{m}" for s in ("0.1", "0.25", "0.5", "1.0") for m in ("flat", "press")} and p["t1"] is not None
    assert p["exit_reason"]["1.0"] == "tp_1.5x"
    assert [q for q in four["positions"] if q["pid"] == "o"][0]["t1"] is None


def test_stakes_validation():
    import floor_nov_paper as NP
    assert NP.parse_stakes("0.10,0.25") == (0.10, 0.25)
    assert NP.parse_stakes("0.10,0.25,0.5,1.0") == (0.10, 0.25, 0.5, 1.0)
    for bad in ("0.10", "0.10,0.25,0.3,0.4,0.5,0.6,0.7", "0.25,0.10", "0.10,0.25,0.25", "0.10,0.25,2.5", "0.10,0.25,0", "0.5,1.0", "0.10,0.5", "0.10,0.25,x", "0.10,0.25,-1"):
        try:
            NP.parse_stakes(bad)
        except SystemExit:
            continue
        raise AssertionError("accepted " + bad)


def board_of(base_rows, var_rows, to_ms=FAR, fwd="2026-11-12"):
    import types
    with tempfile.TemporaryDirectory() as d:
        for n, rows in (("b", base_rows), ("v", var_rows)):
            o = run_pnl(rows, to_ms)
            o["header"] = {"variant": {} if n == "b" else {"Q_STAR": 1e9}, "frozen_rule": n == "b"}
            json.dump(o, open(f"{d}/{n}.json", "w"))
        a = types.SimpleNamespace(base=d + "/b.json", variant=d + "/v.json", out_json=d + "/o.json", out_md=d + "/o.md", end_hour="2026-11-13T20", forward_from=fwd)
        AB.board(a, now=datetime(2026, 11, 14, 1, tzinfo=timezone.utc))
        return json.load(open(d + "/o.json")), open(d + "/o.md").read()


def walk(x):
    if isinstance(x, dict):
        for k, v in x.items():
            yield k
            yield from walk(v)
    elif isinstance(x, list):
        for v in x:
            yield from walk(v)
    elif isinstance(x, str):
        yield x


def test_board_no_identifying_fields_and_labels():
    rows = golden_rows((0.10, 0.25, 0.5, 1.0))
    b, md = board_of(rows, rows, ms(14, 6))
    for s in walk(b):
        assert s == AB.EXTRAPOLATION_NOTE or not any(w in s.lower() for w in ("pool", "mint", "sig")), s
    assert "POOL_" not in md and "MINT_" not in md
    assert b["schema"] == "open_sim_board.v1" and b["live"] is None and b["recon"]["status"] == "NOT COMPARABLE"
    assert b["banner"] == "open simulation, paper, not a gate read; no book has cleared the promotion gate"
    assert b["n_books_watched"] == 8 and [x["id"] for x in b["books"][:4]] == ["D0-0.10", "D0-0.25", "D0-0.50", "D0-1.00"]
    lab = {x["stake_sol"]: x["impact_label"] for x in b["books"]}
    assert lab == {0.1: "model; live fills exist, not reconciled (recon NOT COMPARABLE)", 0.25: "model; live check pending", 0.5: "model only, extrapolated; not for steering", 1.0: "model only, extrapolated; not for steering"}
    assert b["tape_through_utc"] == "2026-11-14T06:00:00Z" and b["sim_version"].startswith("v0")
    cav = "8 book rows are watched daily (2 rules x 4 stakes); the vault1.0 rule was itself picked from a variant search on 2026-11-03..11-11. The best-looking row overstates; treat it as shrunk. Days before 2026-11-12 are in-sample for the wider rule."
    assert cav in md and b["caveat"] == cav and AB.EXTRAPOLATION_NOTE in md and b["extrapolation_note"] == AB.EXTRAPOLATION_NOTE
    assert all(("extrapolation_note" in x) == (x["stake_sol"] >= 0.5) for x in b["books"])
    assert "Forward counts only trades entered on or after 2026-11-12" in md and "90 min" in md
    assert "model only, extrapolated; not for steering" in md and "EXIT (close) UTC day" in md and md.index("open simulation") < md.index("| book |")
    assert set(b["books"][0]) >= {"summary", "per_day", "trades", "open", "impact_label", "stake_sol"}
    assert set(b["books"][0]["summary"]) == {"today", "d7", "forward_all", "incl_backfill"}


def test_board_close_day_booking_in_sample_open_and_forward_completeness():
    rows = golden_rows()
    # b: entry 11-05T03 time_stop exits 11-06T03; d: entry 11-12T02 time_stop exits 11-13T02; e: entry 11-12T04 tp exits 11-12T06; o open from 11-13T02
    b, _ = board_of(rows, rows, ms(14, 6))
    d25 = [x for x in b["books"] if x["id"] == "D0-0.25"][0]
    pd = {r["day"]: r for r in d25["per_day"]}
    assert pd["2026-11-06"]["n"] == 1 and abs(pd["2026-11-06"]["net_sol_flat"] - (-4e-3)) < 1e-12  # booked on the exit day, not the 11-05 entry day
    assert pd["2026-11-05"]["n"] == 1 and abs(pd["2026-11-05"]["net_sol_flat"] - 5e-3) < 1e-12 and pd["2026-11-05"]["in_sample"] is True
    assert pd["2026-11-06"]["in_sample"] is True and pd["2026-11-12"]["in_sample"] is False
    assert pd["2026-11-13"]["n"] == 1 and abs(pd["2026-11-13"]["net_sol_flat"] - (-3e-3)) < 1e-12
    tr_ = {t["tid"]: t for t in d25["trades"]}
    assert [t["tid"] for t in d25["trades"]] == ["P00001", "P00002", "P00003", "P00004", "P00005"]
    assert tr_["P00001"]["in_sample"] is True and tr_["P00004"]["in_sample"] is False and tr_["P00004"]["exit_reason"] == "time_stop"
    assert abs(tr_["P00001"]["ret_pct"] - 2.0) < 1e-9
    f = d25["summary"]["forward_all"]
    assert f["n_closed"] == 2 and abs(f["net_sol_flat"] - (7e-3 - 3e-3 + 0)) < 1e-12 and f["days"] == 2 and f["days_pos"] == 1 and f["tp_count"] == 1 and f["time_count"] == 1
    assert d25["summary"]["today"]["day"] == "2026-11-13" and d25["summary"]["today"]["n_closed"] == 1
    assert d25["summary"]["incl_backfill"]["n_closed"] == 5 and d25["summary"]["incl_backfill"]["n_open_at_tape_end"] == 1
    assert [o["tid"] for o in d25["open"]] == ["P00007"] and abs(d25["open"][0]["age_h"] - 28.0) < 1e-9 and d25["open"][0]["entry_utc"] == "2026-11-13T02:00:00Z"
    # tape ends mid 11-13: 11-13 is an incomplete day and its close is in no window; 11-12 is the newest complete day
    b2, _ = board_of(rows, rows, ms(13, 9))
    s2 = [x for x in b2["books"] if x["id"] == "D0-0.25"][0]
    assert s2["summary"]["today"]["day"] == "2026-11-12"
    assert s2["summary"]["forward_all"]["days"] == 1 and s2["summary"]["forward_all"]["n_closed"] == 1 and "2026-11-13" not in {r["day"] for r in s2["per_day"]}
    assert s2["summary"]["incl_backfill"]["n_closed"] == 4


def book(b, bid="D0-0.25"):
    return [x for x in b["books"] if x["id"] == bid][0]


def test_ids_stable_across_reruns():
    m = [op("m", ms(11, 1))]
    run1 = golden_rows() + m
    run2 = golden_rows() + m + [tr("m", s, 1_000_000, ms(11, 1), ms(11, 5), "tp_1.5x") for s in (0.10, 0.25)]
    x1, x2 = book(board_of(run1, run1, ms(14, 6))[0]), book(board_of(run2, run2, ms(14, 6))[0])
    ids1 = {t["entry_utc"]: t["tid"] for t in x1["trades"] + x1["open"]}
    ids2 = {t["entry_utc"]: t["tid"] for t in x2["trades"] + x2["open"]}
    assert "2026-11-11T01:00:00Z" in ids1 and ids1["2026-11-11T01:00:00Z"] == ids2["2026-11-11T01:00:00Z"]
    assert all(ids2[k] == v for k, v in ids1.items())


def test_partial_day_not_complete_without_book_lag():
    rows = golden_rows()
    assert book(board_of(rows, rows, ms(14, 0))[0])["summary"]["today"]["day"] == "2026-11-12"
    assert book(board_of(rows, rows, ms(14, 2))[0])["summary"]["today"]["day"] == "2026-11-13"


def test_forward_all_only_entries_on_or_after_forward_from():
    rows = golden_rows() + [op("x", ms(11, 3))] + [tr("x", s, 3_000_000, ms(11, 3), ms(12, 3), "time_stop") for s in (0.10, 0.25)]
    b, _ = board_of(rows, rows, ms(14, 6))
    s = book(b)["summary"]
    assert s["incl_backfill"]["n_closed"] == 6 and s["incl_backfill"]["n_in_sample"] == 4
    assert s["forward_all"]["n_closed"] == 2 and s["forward_all"]["n_in_sample"] == 0 and s["forward_all"]["n_in_sample_entries_excluded"] == 1
    assert {r["day"]: r for r in book(b)["per_day"]}["2026-11-12"]["n_in_sample"] == 1


def test_board_refuses_unbounded_pnl_and_table_ignores_extra_stakes():
    import types
    rows = golden_rows((0.10, 0.25, 0.5, 1.0))
    with tempfile.TemporaryDirectory() as d:
        o = run_pnl(rows)
        o["header"] = {}
        json.dump(o, open(d + "/p.json", "w"))
        AB.table(types.SimpleNamespace(base=d + "/p.json", variant=d + "/p.json", out_json=d + "/t.json", out_md=d + "/t.md", end_hour="2026-11-19T23", forward_from="2026-11-12"))
        assert set(json.load(open(d + "/t.json"))["legs"]) == {"0.1:flat", "0.1:press", "0.25:flat", "0.25:press"}
        o["bound_applied"] = False
        json.dump(o, open(d + "/p.json", "w"))
        try:
            AB.board(types.SimpleNamespace(base=d + "/p.json", variant=d + "/p.json", out_json=d + "/b.json", out_md=d + "/b.md", end_hour="x", forward_from="2026-11-12"))
        except SystemExit:
            return
        raise AssertionError("board accepted an unbounded pnl file")
