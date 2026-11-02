"""Tests for tools/arena_tip_gapfill_proofs.py (P1 to P3). Synthetic rows and blocks only: no explore-0814 data, no network, no sealed data."""

from __future__ import annotations

import collections
import json
from pathlib import Path

import pytest

from tools import arena_paper
from tools import arena_paper_f2 as f2
from tools import arena_tip_gapfill_proofs as pr
from tools.pump_history_backfill import rows_from_block
from tools.test_arena_paper import _row
from tools.test_fast_tip_follower import Rpc, _block

EXPLORE_START_MS = pr.WINDOW_S[0] * 1000 + 3_600_000


@pytest.fixture(autouse=True)
def _plenty_of_disk(monkeypatch):
    monkeypatch.setattr("tools.arena_paper.shutil.disk_usage", lambda path: collections.namedtuple("Usage", "total used free")(1000, 500, 500))


def stream(pools: int = 10, lo: int = 90, hi: int = 420, stagger: int = 30, inside: bool = True) -> list[dict]:
    """`pools` PumpSwap pools, pool k printing every slot from lo + stagger * k to hi, with t_recv_ms inside explore-0814 (inside=False: the October time of the engine tests)."""
    rows = []
    for k in range(pools):
        for i, s in enumerate(range(lo + stagger * k, hi)):
            r = _row(i, s, pool=f"P{k}", sig=f"sigP{k}x{i}")
            if inside:
                r["t_recv_ms"] = EXPLORE_START_MS + (s - lo) * 400
            rows.append(r)
    return sorted(rows, key=lambda r: (r["slot"], r["pool"]))


def current_source() -> str:
    return Path(arena_paper.__file__).read_text()


# ------------------------------------------------------------------------------------------------------------------------------------ R1: explore-0814 first
def test_the_window_is_the_pinned_explore_0814_list():
    assert pr.EXPLORE_0814 == ("2026-08-14T00", "2026-08-28T00") and pr.WINDOW_S == (1786665600, 1787875200)
    try:
        from tools import arena_restart_equiv as ar  # PR #1183: the vendored constant must equal it once it is on main
    except ImportError:
        return
    assert "2026-08-14" in str(ar.EXPLORATION["explore-0814"]) and "2026-08-28" in str(ar.EXPLORATION["explore-0814"])


def test_enforce_hours_refuses_rows_outside_or_with_no_time_and_prefers_block_time():
    ok = {"slot": 1, "t_recv_ms": EXPLORE_START_MS}
    assert pr.enforce_hours([ok]) == 1
    with pytest.raises(pr.Refused, match="1 of 2 rows outside"):
        pr.enforce_hours([ok, {"slot": 2, "t_recv_ms": EXPLORE_START_MS + 14 * 86_400_000}])  # 2026-08-28T01: after the window
    with pytest.raises(pr.Refused, match="1 with no block_time or t_recv_ms"):
        pr.enforce_hours([ok, {"slot": 3}])
    pr.enforce_hours([{"slot": 4, "block_time": pr.WINDOW_S[0] + 5, "t_recv_ms": 1}])  # block_time decides when both are there
    with pytest.raises(pr.Refused):
        pr.enforce_hours([{"slot": 5, "block_time": pr.WINDOW_S[1], "t_recv_ms": EXPLORE_START_MS}])  # the window is half open


def test_p1_and_p3_refuse_rows_outside_the_window_before_any_engine_runs(tmp_path):
    rows = stream(inside=False)
    with pytest.raises(pr.Refused):
        pr.p1(rows, arena_paper, tmp_path / "a")
    with pytest.raises(pr.Refused):
        pr.p3(rows, tmp_path / "b", ["unfetchable"])
    assert not (tmp_path / "a").exists() and not (tmp_path / "b").exists()


def test_read_rows_refuses_a_sealed_directory_and_rows_outside_the_window(tmp_path):
    with pytest.raises(pr.Refused):
        pr.read_rows("/var/lib/mal/sealed/fast-trades-tip")
    (tmp_path / "a.jsonl").write_text(json.dumps({"slot": 1, "t_recv_ms": 5}) + "\n")
    with pytest.raises(pr.Refused):
        pr.read_rows(tmp_path)
    good = tmp_path / "good"
    good.mkdir()
    (good / "b.jsonl").write_text(json.dumps({"slot": 2, "t_recv_ms": EXPLORE_START_MS}) + "\n")
    (good / "a.jsonl").write_text(json.dumps({"slot": 1, "t_recv_ms": EXPLORE_START_MS}) + "\n\n")
    assert [r["slot"] for r in pr.read_rows(good)] == [1, 2]


def test_the_cli_exits_3_on_a_refusal_before_loading_any_engine(tmp_path, capsys):
    (tmp_path / "a.jsonl").write_text(json.dumps({"slot": 1, "t_recv_ms": 5}) + "\n")
    assert pr.main(["p1", "--hours-dir", str(tmp_path), "--old-sha", "nosuchsha", "--min-rows", "1", "--out", str(tmp_path / "o.json")]) == pr.EXIT_REFUSED
    assert pr.main(["p3", "--hours-dir", "/var/lib/mal/sealed/x", "--reasons", "unfetchable", "--min-rows", "1", "--out", str(tmp_path / "o.json")]) == pr.EXIT_REFUSED
    assert pr.main(["p3", "--hours-dir", str(tmp_path), "--reasons", "unfetchable", "--min-rows", "1", "--out", "/var/lib/mal/sealed/o.json"]) == pr.EXIT_REFUSED
    assert "REFUSED" in capsys.readouterr().err


# ------------------------------------------------------------------------------------------------------------------------------------ P1 (R2)
def test_p1_the_same_ledger_in_order_and_as_a_set_with_detection_off_and_on_and_the_shas_printed(tmp_path):
    old = pr.load_engine_source(current_source(), "tools.arena_paper_old_t1")
    res = pr.p1(stream(), old, tmp_path, every=1)
    assert res["pass"] is True and set(res["runs"]) == {"detection_off", "detection_on"}
    for cell in res["runs"].values():
        assert cell["old"] == cell["new"] and cell["old"]["rows"] > 0 and cell["book_old"]["buys"] >= 5 and set(cell["old"]) == {"rows", "md5_in_order", "md5_as_set"}
    assert res["old_engine_sha256"] == pr.sha256_file(arena_paper.__file__)
    assert set(res["new_engine_sha256"]) == {"arena_paper.py", "arena_paper_f2.py"} and res["new_engine_sha256"]["arena_paper.py"] == res["old_engine_sha256"]


def test_p1_detects_an_engine_that_prices_differently_in_both_cells(tmp_path):
    src = current_source()
    patched = src.replace("return int(F.k_slots(lat, sps, self.cfg.k_rounding))", "return int(F.k_slots(lat, sps, self.cfg.k_rounding)) + 1")
    assert patched != src
    res = pr.p1(stream(), pr.load_engine_source(patched, "tools.arena_paper_old_t2"), tmp_path, every=1)
    assert res["pass"] is False and not any(c["pass"] for c in res["runs"].values()) and res["old_engine_sha256"] != pr.sha256_file(arena_paper.__file__)


def test_p1_refuses_below_the_min_rows_floor(tmp_path):
    res = pr.p1(stream(pools=1), arena_paper, tmp_path, min_rows=10**9)
    assert res["pass"] is False and "min-rows" in res["refused"]


# ------------------------------------------------------------------------------------------------------------------------------------ P2
def test_p2_compare_passes_on_equal_rows_and_names_a_differing_field_by_name_only():
    rows = [{"slot": s, "signature": f"s{s}", "event_index": 0, "tx_index": 1, "sol_lamports": 5, "t_recv_ms": s * 7, "source": "tip"} for s in (10, 11)]
    walker = [dict(r, t_recv_ms=None, source="backfill") for r in rows]
    ok = pr.p2_compare(rows, rows, walker, 10, 11)
    assert ok["pass"] is True and ok["mismatched_slots"] == 0 and ok["slots_with_rows"] == 2 and "diagnostics" not in ok
    bad = pr.p2_compare(rows, rows, [walker[0], dict(walker[1], sol_lamports=6, extra_key=1)], 10, 11)
    assert bad["pass"] is False and bad["mismatched_slots"] == 1
    assert bad["diagnostics"]["differing_fields"] == ["extra_key", "sol_lamports"] and "extra_key" in bad["diagnostics"]["field_names"]["walker"] and "extra_key" not in bad["diagnostics"]["field_names"]["fill"]
    assert not any(isinstance(v, list) and v and isinstance(v[0], int) for v in bad.values())  # no slot list


def test_p2_compare_fails_when_tx_index_presence_differs():
    a = [{"slot": 10, "signature": "s", "event_index": 0, "tx_index": 3}]
    b = [{"slot": 10, "signature": "s", "event_index": 0}]
    res = pr.p2_compare(a, a, b, 10, 10)
    assert res["pass"] is False and res["rows_no_tx_index"] == {"fill": 0, "warm": 0, "walker": 1} and "tx_index" in res["diagnostics"]["differing_fields"]


def test_the_dropped_p2_fields_are_pinned():
    assert pr.DROP_P2 == frozenset({"t_recv_ms", "source", "virtual_quote_reserve"})


def explore_fetch(rpc: Rpc):
    def fetch(slot):
        blk, code = rpc.fetch(slot)
        return (dict(blk, blockTime=pr.WINDOW_S[0] + 3600 + slot) if blk else blk), code

    return fetch


def walker_for(slots) -> list[dict]:
    out = []
    for s in slots:
        out.extend(rows_from_block(dict(_block(s), blockTime=pr.WINDOW_S[0] + 3600 + s), {}, "helius_getblock_tip", event_v=True)["trades"])
    return out


def test_p2_refuses_before_the_first_fetch_unless_the_window_is_inside_the_walker_span_and_explore_0814(tmp_path):
    rpc = Rpc(1000)
    outside = [dict(r, block_time=5) for r in walker_for(range(94, 111))]
    with pytest.raises(pr.Refused):
        pr.p2_run(100, 103, 3, explore_fetch(rpc).__call__, outside, tmp_path / "a")
    with pytest.raises(pr.Refused, match="slot span"):
        pr.p2_run(100, 103, 3, explore_fetch(rpc), walker_for(range(100, 104)), tmp_path / "b")  # the margin leaves the span
    with pytest.raises(pr.Refused):
        pr.p2_run(100, 103, 3, explore_fetch(rpc), [], tmp_path / "c")
    assert rpc.calls == []  # nothing was fetched


def test_p2_run_cold_fill_equals_the_warm_follower_and_the_walker_rows(tmp_path):
    rpc = Rpc(1000)
    res = pr.p2_run(100, 103, 3, explore_fetch(rpc), walker_for(range(94, 111)), tmp_path, min_slots=4)
    assert res["fill_complete"] is True and res["slots_with_rows"] == 4 and res["planned_getblock_calls"] == 2 * 3 + 2 * 4 + 1
    assert res["pass"] is True and res["mismatched_slots"] == 0, res


# ------------------------------------------------------------------------------------------------------------------------------------ P3 (R2 to R6)
def test_plan_and_lag_helpers_are_deterministic():
    rows = stream()
    cuts = pr.plan_cuts(rows, ["backlog_jump", "unresolved_dropped"], lag=True)
    assert cuts == pr.plan_cuts(rows, ["backlog_jump", "unresolved_dropped"], lag=True)
    assert cuts[0]["reason"] == "backlog_jump" and cuts[0]["hi"] - cuts[0]["lo"] + 1 == 50 and cuts[0]["back"] == 50 and cuts[1]["lo"] == cuts[1]["hi"]
    lagged = pr.apply_rising_lag(rows, cuts)
    lo = cuts[0]["lo"]
    by = {(r["slot"], r["pool"]): r["t_recv_ms"] for r in rows}
    byl = {(r["slot"], r["pool"]): r["t_recv_ms"] for r in lagged}
    assert byl[(lo - 1, "P0")] - by[(lo - 1, "P0")] == 50 * 400 and byl[(lo - 50, "P0")] - by[(lo - 50, "P0")] == 400  # rising to B slots just before lo
    assert byl[(lo + 60, "P0")] == by[(lo + 60, "P0")]  # after the range: the chain's clock again
    ts = [r["t_recv_ms"] for r in lagged if cuts[0]["lo"] - 50 <= r["slot"] <= cuts[0]["hi"]]
    assert ts == sorted(ts)  # non-decreasing through the jump


def test_p3_four_cells_pass_and_the_expected_change_is_computed_not_counted(tmp_path):
    res = pr.p3(stream(), tmp_path, ["backlog_jump", "unresolved_dropped"], every=1)
    assert res["pass"] is True, res
    assert set(res["cells"]) == {"lag_none_detection_off", "lag_none_detection_on", "lag_rising_detection_off", "lag_rising_detection_on"}
    for name, c in res["cells"].items():
        assert c["mismatched_positions"] == 0 and c["expected_classes"] == c["actual_classes"] and c["rows_dup"] == 0 and c["mixed_groups"] == 0, name
        assert c["gaps"]["unbroken"] == c["gaps"]["filled"] and c["positions"] >= 5 and c["fills_seen"] == 2
        assert set(c["late_prints"]["filled"]) == {"fill_margin_flag", "gap_overlap_true", "gap_overlap_false"}
        assert c["spanning_priced_unchanged"] >= 1  # the amendment's claimed benefit: priced positions spanning a range with their legs clear
    assert any(set(c["expected_classes"]) - {"unchanged"} for c in res["cells"].values())


def test_p3_unresolved_dropped_inserts_exactly_the_removed_rows_and_drops_the_twins(tmp_path):
    res = pr.p3(stream(), tmp_path, ["unresolved_dropped"], every=1)
    assert res["pass"] is True and set(res["cells"]) == {"lag_none_detection_off", "lag_none_detection_on"}
    for c in res["cells"].values():
        assert c["expected_fill_rows"] > 0 and c["expected_twin_rows"] > 0
        assert c["splice"]["fill_rows"] == c["expected_fill_rows"] and c["splice"]["fill_rows_dup_dropped"] == c["expected_twin_rows"]


def test_p3_every_fillable_reason_at_once(tmp_path):
    res = pr.p3(stream(pools=14, hi=520), tmp_path, ["backlog_jump", "unfetchable", "skip_contradicted", "skip_contradicted_refetch", "unresolved_dropped"], every=1)
    assert res["pass"] is True, res
    assert all(c["splice"]["filled_ranges"] == 5 and c["rows_dup"] == 0 and c["mixed_groups"] == 0 for c in res["cells"].values())


def test_p3_fails_when_the_engine_does_not_apply_item_6(tmp_path, monkeypatch):
    """R3: a fill that changes a position the amendment says it must not (or fails to change one it must) is a FAIL, whatever the class."""
    monkeypatch.setattr(f2.ArenaEngineF2, "_fill_hit", lambda self, pos: False)  # the margin exclusion silently gone
    res = pr.p3(stream(), tmp_path, ["backlog_jump"], every=1)
    assert res["pass"] is False and any(c["mismatched_positions"] > 0 for c in res["cells"].values())


def test_p3_refuses_below_the_min_rows_floor(tmp_path):
    res = pr.p3(stream(pools=1), tmp_path, ["unfetchable"], min_rows=10**9)
    assert res["pass"] is False and "min-rows" in res["refused"]
