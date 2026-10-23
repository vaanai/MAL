"""arena_worst_gaps: per-trade END-minus-worst gaps from synthetic tape and trade fixtures (the fixtures of test_arena_report_legs). Nothing real is opened."""

from __future__ import annotations

import json
import tracemalloc

import pytest

from tools import arena_worst_gaps as G
from tools import arena_report_legs as R
from tools.test_arena_report_legs import (SEND, ST, SPOT, T0, _plenty_of_disk, by, hour_name, make_ledger, pr, ref_trip, tape_rows, tms)  # noqa: F401  (fixtures)


def load_worst_gaps(path):
    """The input contract of the scorer's --worst-in-slot (PR #815, not on main): a JSON object {trade_id: gap_lamports >= 0}."""
    g = json.loads(open(path).read())
    assert isinstance(g, dict) and all(isinstance(k, str) and isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0 for k, v in g.items())
    return g


@pytest.fixture()
def world(tmp_path):
    trades = make_ledger(tmp_path)
    return trades, R.load_pools(tape_rows())


def end_pnl(trade, rule="total", lat="1p9"):
    return trade["calc"][lat]["by_rule"][rule]["pnl_nofail"]


def test_gap_is_end_minus_the_worst_in_slot_row_hand_computed(world):
    trades, pools = world
    gaps, d = G.compute([by(trades, "PX", 0.25)], pools)
    x = by(trades, "PX", 0.25)
    # 1.9 s: END S4 -> S2 (the ledger); worst entry S5, worst exit S0 (see test_arena_report_legs)
    w_total, _ = ref_trip(0.25e9, *ST["S5"], SPOT, *ST["S0"], k_tol=1e-3)
    w_sc, _ = ref_trip(0.25e9, *ST["S5"], SPOT, *ST["S0"], k_tol=None)
    want = max(end_pnl(x, "total") - w_total, end_pnl(x, "sc") - w_sc)
    assert gaps[x["trade_id"]] == pytest.approx(want, abs=1e-3) and want > 0 and d["all_measured"]
    assert d["trades"][x["trade_id"]]["rule"] in ("sc", "total")


def test_a_quiet_pool_has_gap_zero_but_is_measured(world):
    trades, pools = world
    gaps, d = G.compute([by(trades, "PN", 0.25)], pools)
    assert gaps == {by(trades, "PN", 0.25)["trade_id"]: 0.0} and d["measured"] == 1


def test_guard_rescue_is_not_credited(world):
    trades, pools = world
    x = by(trades, "PX", 1.0)  # filled at END, guarded at its worst entry state (S5): the worst row would be -send, a rescue
    gaps, _ = G.compute([x], pools)
    _, _, diag = R.worst_trip(x, pools["PX"])
    assert diag["guard_flips"]  # the fixture does flip the guard
    assert gaps[x["trade_id"]] >= 0
    assert end_pnl(x) - (-SEND) < 0  # a naive END minus the guarded worst row would be NEGATIVE: the guard "rescued" a loser
    assert gaps[x["trade_id"]] == pytest.approx(0.0, abs=1e-6)  # the no-guard-rescue row keeps END: nothing is credited


def test_exit_invalid_at_the_worst_exit_state_makes_the_gap_the_strand_loss(world):
    trades, pools = world
    x = by(trades, "PI", 0.25)
    gaps, d = G.compute([x], pools)
    t = d["trades"][x["trade_id"]]
    assert t["rule"] == "total"  # total strands at LOWK, sc sells normally
    strand = -0.25e9 - SEND - R.STRAND_RENT
    assert gaps[x["trade_id"]] == pytest.approx(end_pnl(x, "total") - strand, abs=1e-3)


def test_gaps_file_is_what_the_scorer_reads(world, tmp_path):
    trades, pools = world
    gaps, _ = G.compute(trades, pools)
    p = tmp_path / "gaps.json"
    p.write_text(json.dumps(gaps))
    assert load_worst_gaps(p) == gaps and len(gaps) == 9


def test_a_trade_that_cannot_be_measured_is_left_out_not_zeroed(world):
    trades, pools = world
    thin = R.load_pools([r for r in tape_rows() if not (r["pool"] == "PX" and r["slot"] == 106)])  # the END print is missing
    gaps, d = G.compute([by(trades, "PX", 0.25), by(trades, "PN", 0.25)], thin)
    assert by(trades, "PX", 0.25)["trade_id"] not in gaps and by(trades, "PN", 0.25)["trade_id"] in gaps
    assert "END consistency failed" in d["left_out"][by(trades, "PX", 0.25)["trade_id"]] and not d["all_measured"]
    gaps, d = G.compute([by(trades, "PX", 0.25)], R.load_pools([r for r in tape_rows() if r["pool"] != "PX"]))
    assert gaps == {} and "not on the tape" in next(iter(d["left_out"].values()))
    gaps, d = G.compute([dict(by(trades, "PX", 0.25), prog="pump_bonding")], pools)
    assert gaps == {} and "PumpSwap" in next(iter(d["left_out"].values()))
    with pytest.raises(R.Refused, match="duplicated"):
        G.compute([trades[0], trades[0]], pools)


def test_windows_merge_and_membership(world):
    trades, _ = world
    w = G.windows_by_pool(trades, margin=10)
    assert set(w) == {"PX", "PI", "PN"} and all(len(v) == 1 for v in w.values())  # the three stakes of a pool share one window
    lo, hi = w["PX"][0]
    assert lo == 104 and hi == 203 + 10  # need = exit slot 202 + 1, plus the margin
    assert G.in_window(w["PX"], 105) and not G.in_window(w["PX"], 50) and not G.in_window(w["PX"], hi + 1)


def write_tape(d, rows):
    d.mkdir(exist_ok=True)
    (d / hour_name(tms(90))).write_text("".join(json.dumps(r) + "\n" for r in rows))
    return d


def noise(n):
    """Many prints of other pools and of the wanted pools far outside every window."""
    rows = [pr(f"Z{i % 50}", 100_000 + i, "S1", off=i % 7, ev=i) for i in range(n)]
    rows += [pr("PX", 900_000 + i, "S1", off=i % 7, ev=i) for i in range(n // 10)]
    return rows


def test_streaming_run_equals_the_full_load_and_drops_what_cannot_matter(world, tmp_path):
    trades, pools = world
    want, _ = G.compute(trades, pools)
    tape = write_tape(tmp_path / "tp", sorted(tape_rows() + noise(3000), key=lambda r: (r["slot"], r["t_recv_ms"], r["signature"])))
    files = R.tape_files(tape)
    gaps, d = G.run(trades, files, margin=50)
    assert gaps == want and d["all_measured"]
    s = d["stream"]
    assert s["lines"] == len(tape_rows()) + 3300 and s["kept"] == len(tape_rows()) - 6 and s["dropped_outside_window"] == 306 and s["decoded"] == s["kept"] + 306  # 6 = the slot-99/100 prints before every window
    # lines of other pools are never decoded
    assert s["decoded"] < s["lines"] / 5


def test_a_window_too_short_to_reach_the_end_print_is_an_end_mismatch_not_a_wrong_gap(world, tmp_path):
    trades, _ = world
    x = by(trades, "PX", 0.25)
    full = write_tape(tmp_path / "full", tape_rows())
    ok, _ = G.run([x], R.tape_files(full), margin=0)  # the window ends exactly at the END print's slot (203): enough
    assert x["trade_id"] in ok
    far = [r for r in tape_rows() if not (r["pool"] == "PX" and r["slot"] == 203)] + [pr("PX", 5000, "S2")]  # the END print comes 4,800 slots later
    tape = write_tape(tmp_path / "far", sorted(far, key=lambda r: (r["slot"], r["t_recv_ms"], r["signature"])))
    gaps, d = G.run([x], R.tape_files(tape), margin=50)
    assert gaps == {} and "END consistency failed" in d["left_out"][x["trade_id"]]  # left out, never a wrong number
    gaps, d = G.run([x], R.tape_files(tape), margin=6000)
    assert gaps == ok  # a wide enough margin reaches it


def test_memory_is_bounded_by_the_trades_not_the_tape(world, tmp_path):
    trades, _ = world
    small = write_tape(tmp_path / "small", sorted(tape_rows() + noise(500), key=lambda r: (r["slot"], r["t_recv_ms"], r["signature"])))
    big = write_tape(tmp_path / "big", sorted(tape_rows() + noise(20_000), key=lambda r: (r["slot"], r["t_recv_ms"], r["signature"])))
    peaks = []
    for t in (small, big):
        tracemalloc.start()
        G.run(trades, R.tape_files(t), margin=50)
        peaks.append(tracemalloc.get_traced_memory()[1])
        tracemalloc.stop()
    assert peaks[1] < peaks[0] * 1.5  # 40x the tape, about the same peak


def test_cli_end_to_end_and_exit_codes(world, tmp_path, capsys):
    trades, _ = world
    tape = write_tape(tmp_path / "tp", tape_rows())
    tj = tmp_path / "trades.jsonl"
    tj.write_text("".join(json.dumps(r) + "\n" for r in trades))
    out, det = tmp_path / "gaps.json", tmp_path / "detail.json"
    args = ["--trades", str(tj), "--tape-dir", str(tape), "--out", str(out), "--detail", str(det), "--pad-hours", "24"]
    assert G.main(args) == 0
    gaps = json.loads(out.read_text())
    assert len(gaps) == 9 and load_worst_gaps(out) == gaps and json.loads(det.read_text())["all_measured"]
    assert "measured 9" in capsys.readouterr().out
    # a tape without PX: its trades are left out and the exit code says so
    tj2 = tmp_path / "two.jsonl"
    tj2.write_text("".join(json.dumps(r) + "\n" for r in trades if r["pool"] == "PX"))
    write_tape(tmp_path / "tp2", [r for r in tape_rows() if r["pool"] != "PX"])
    assert G.main(["--trades", str(tj2), "--tape-dir", str(tmp_path / "tp2"), "--out", str(tmp_path / "g2.json"), "--pad-hours", "24"]) == 4
    assert json.loads((tmp_path / "g2.json").read_text()) == {}


@pytest.mark.parametrize("flag", ["--trades", "--tape-dir", "--out", "--detail", "--vmap"])
def test_every_path_is_checked_for_closed_material(flag, tmp_path, capsys):
    base = {"--trades": str(tmp_path / "t.jsonl"), "--tape-dir": str(tmp_path), "--out": str(tmp_path / "o.json")}
    base[flag] = "/x/canary/f"
    argv = [x for k, v in base.items() for x in (k, v)] + ([] if flag in base else [flag, "/x/canary/f"])
    assert G.main(argv) == 3 and "REFUSED" in capsys.readouterr().err


def test_non_trade_rows_and_bad_json_are_refused(tmp_path):
    p = tmp_path / "t.jsonl"
    p.write_text('{"kind": "open"}\n')
    with pytest.raises(R.Refused, match="expected a ledger row of kind 'trade'"):
        G.load_trades(p)
    p.write_text("{torn\n")
    with pytest.raises(R.Refused, match="not JSON"):
        G.load_trades(p)
