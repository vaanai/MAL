"""Tests for tools/mal_result.py: the standard result.v1 record every
exploration/scoring tool emits (docs/console-plan.md §5, §9 item 1).

No live data, no SSH, no heavy job. Pure unit tests over synthetic trade
fixtures.
"""

from __future__ import annotations

import json
import multiprocessing as mp
import os
import statistics
from pathlib import Path

import pytest

from tools.mal_result import (
    SCHEMA_PATH,
    append_try,
    build_result,
    data_key,
    gate_leg,
    leg_metrics,
    tries_summary,
    validate_result,
    write_result,
)
from tools.paper_attention_promote import BookTrade, book_stats


def _trade(sol: float, pct: float, day: str, filled: bool = True, segment: str | None = None) -> dict:
    row = {"sol": sol, "pct": pct, "day": day, "filled": filled}
    if segment is not None:
        row["segment"] = segment
    return row


def _days(n: int, start: int = 1) -> list[str]:
    return [f"2026-09-{start + i:02d}" for i in range(n)]


# --- gate boundary cases -----------------------------------------------------


def _base_metrics(**overrides) -> dict:
    metrics = {
        "n_trades": 100,
        "days": 5,
        "days_positive": 3,
        "ci90_lo_sol": 0.01,
        "ex_top3_sol": 0.01,
    }
    metrics.update(overrides)
    return metrics


def test_gate_n_99_fails_n_100_passes():
    assert gate_leg(_base_metrics(n_trades=99))["n_ge_100"] is False
    assert gate_leg(_base_metrics(n_trades=99))["pass"] is False
    assert gate_leg(_base_metrics(n_trades=100))["n_ge_100"] is True
    assert gate_leg(_base_metrics(n_trades=100))["pass"] is True


def test_gate_days_4_fails_5_passes():
    g4 = gate_leg(_base_metrics(days=4, days_positive=3))
    assert g4["days_ge_5"] is False
    assert g4["pass"] is False
    g5 = gate_leg(_base_metrics(days=5, days_positive=3))
    assert g5["days_ge_5"] is True
    assert g5["pass"] is True


def test_gate_majority_days_positive_3_of_6_fails_4_of_6_passes():
    g3 = gate_leg(_base_metrics(days=6, days_positive=3))
    assert g3["majority_days_positive"] is False
    assert g3["pass"] is False
    g4 = gate_leg(_base_metrics(days=6, days_positive=4))
    assert g4["majority_days_positive"] is True
    assert g4["pass"] is True


def test_gate_ci_lo_exactly_zero_fails():
    g = gate_leg(_base_metrics(ci90_lo_sol=0.0))
    assert g["ci90_lo_gt_0"] is False
    assert g["pass"] is False
    g_pos = gate_leg(_base_metrics(ci90_lo_sol=1e-9))
    assert g_pos["ci90_lo_gt_0"] is True


def test_gate_ex_top3_exactly_zero_fails():
    g = gate_leg(_base_metrics(ex_top3_sol=0.0))
    assert g["ex_top3_gt_0"] is False
    assert g["pass"] is False
    g_pos = gate_leg(_base_metrics(ex_top3_sol=1e-9))
    assert g_pos["ex_top3_gt_0"] is True


def test_gate_nulls_are_not_a_pass():
    g = gate_leg(_base_metrics(ci90_lo_sol=None, ex_top3_sol=None))
    assert g["ci90_lo_gt_0"] is False
    assert g["ex_top3_gt_0"] is False
    assert g["pass"] is False


# --- equality with the existing repo bootstrap -------------------------------


def test_leg_metrics_ci_matches_book_stats_directly():
    """leg_metrics must not re-derive the bootstrap: its ci90_lo_sol,
    total_sol and ex_top3_sol must equal tools.paper_attention_promote
    .book_stats's own output on the same (synthetic one-trade-per-mint)
    BookTrade list."""
    rng_sols = [0.10, -0.05, 0.20, 0.00, -0.02, 0.15, 0.30, -0.10, 0.05, 0.08]
    days = _days(5) * 2
    trades = [_trade(sol, sol * 100, day) for sol, day in zip(rng_sols, days)]

    from tools.mal_result import _book_trades

    stats = book_stats(_book_trades(trades))
    metrics = leg_metrics(trades)

    assert metrics["ci90_lo_sol"] == stats["mean_ci90_sol"][0]
    assert metrics["total_sol"] == stats["total_sol"]
    assert metrics["ex_top3_sol"] == stats["total_ex_top3_sol"]
    assert metrics["days_positive"] == stats["days_positive"]
    assert metrics["days"] == stats["n_days"]


def test_leg_metrics_bootstrap_is_seed_1_1000_draws():
    """Cross-check against a hand-rolled bootstrap using the same
    algorithm (seed 1, 1000 draws, resample-with-replacement, 5th
    percentile of means) to make sure book_stats (and therefore
    leg_metrics) hasn't silently changed shape."""
    import random

    sols = [0.1, 0.2, -0.1, 0.05, 0.3, -0.2, 0.15, 0.0, 0.25, -0.05]
    days = _days(5) * 2
    trades = [_trade(sol, sol * 100, day) for sol, day in zip(sols, days)]
    metrics = leg_metrics(trades)

    lamports = [round(s * 1_000_000_000) for s in sols]
    rng = random.Random(1)
    means = []
    n = len(lamports)
    for _ in range(1000):
        draw = [lamports[rng.randrange(n)] for _ in range(n)]
        means.append(sum(draw) / n)
    means.sort()
    k = (len(means) - 1) * 0.05
    lo = int(k)
    hi = min(lo + 1, len(means) - 1)
    w = k - lo
    expected_lo_lamports = means[lo] * (1.0 - w) + means[hi] * w
    expected_lo_sol = expected_lo_lamports / 1_000_000_000

    assert metrics["ci90_lo_sol"] == pytest.approx(expected_lo_sol, rel=1e-9)


# --- leg_metrics field behavior ----------------------------------------------


def test_leg_metrics_empty_trades_are_all_null_or_zero():
    metrics = leg_metrics([])
    assert metrics["n_trades"] == 0
    assert metrics["days"] == 0
    assert metrics["ci90_lo_sol"] is None
    assert metrics["total_sol"] == 0.0
    assert metrics["ex_top3_sol"] is None
    assert metrics["mean_pct"] is None
    assert metrics["fill_rate"] is None
    assert metrics["top5_profit_share"] is None
    assert metrics["by_segment"] == {}


def test_leg_metrics_selected_fraction_and_n_days_override():
    trades = [_trade(0.1, 10.0, d) for d in _days(3)]
    metrics = leg_metrics(trades, n_candidates=30, n_days=10)
    assert metrics["selected_fraction"] == pytest.approx(3 / 30)
    assert metrics["trades_per_day"] == pytest.approx(3 / 10)
    assert metrics["sol_per_day"] == pytest.approx(0.3 / 10)


def test_leg_metrics_fill_rate_and_fill_conditional():
    trades = [
        _trade(0.1, 10.0, "2026-09-01", filled=True),
        _trade(0.0, 0.0, "2026-09-01", filled=False),
        _trade(0.2, 20.0, "2026-09-02", filled=True),
        _trade(0.0, 0.0, "2026-09-02", filled=False),
    ]
    metrics = leg_metrics(trades)
    assert metrics["fill_rate"] == pytest.approx(0.5)
    assert metrics["fill_cond_mean_pct"] == pytest.approx(15.0)


def test_leg_metrics_top5_profit_share():
    trades = [
        _trade(1.0, 100.0, "2026-09-01"),
        _trade(1.0, 100.0, "2026-09-01"),
        _trade(1.0, 100.0, "2026-09-02"),
        _trade(1.0, 100.0, "2026-09-02"),
        _trade(1.0, 100.0, "2026-09-03"),
        _trade(1.0, 100.0, "2026-09-03"),  # 6th winner, excluded from top 5
        _trade(-0.5, -50.0, "2026-09-04"),
    ]
    metrics = leg_metrics(trades)
    # gross profit = 6.0, top 5 winners sum = 5.0
    assert metrics["top5_profit_share"] == pytest.approx(5.0 / 6.0)


def test_leg_metrics_top5_profit_share_none_with_no_winners():
    trades = [_trade(-0.1, -10.0, "2026-09-01"), _trade(-0.2, -20.0, "2026-09-02")]
    metrics = leg_metrics(trades)
    assert metrics["top5_profit_share"] is None


def test_leg_metrics_by_segment():
    trades = [
        _trade(0.1, 10.0, "2026-09-01", segment="raydium"),
        _trade(0.2, 20.0, "2026-09-01", segment="raydium"),
        _trade(-0.1, -10.0, "2026-09-02", segment="pumpswap"),
        _trade(0.5, 50.0, "2026-09-03"),  # no segment -> excluded
    ]
    metrics = leg_metrics(trades)
    assert set(metrics["by_segment"]) == {"raydium", "pumpswap"}
    assert metrics["by_segment"]["raydium"]["n_trades"] == 2
    assert metrics["by_segment"]["raydium"]["mean_pct"] == pytest.approx(15.0)
    assert metrics["by_segment"]["raydium"]["total_sol"] == pytest.approx(0.3)
    assert metrics["by_segment"]["pumpswap"]["n_trades"] == 1


# --- data_key / tries log -----------------------------------------------------


def test_data_key_is_order_independent():
    blocks_a = [
        {"start_hour": "2026-09-01T00", "end_hour_exclusive": "2026-09-01T01", "host": "fast", "ledger_owner": "w1"},
        {"start_hour": "2026-09-01T01", "end_hour_exclusive": "2026-09-01T02", "host": "fast", "ledger_owner": "w1"},
    ]
    blocks_b = list(reversed(blocks_a))
    assert data_key(blocks_a) == data_key(blocks_b)


def test_data_key_differs_for_different_blocks():
    blocks_a = [{"start_hour": "2026-09-01T00", "end_hour_exclusive": "2026-09-01T01", "host": "fast", "ledger_owner": "w1"}]
    blocks_b = [{"start_hour": "2026-09-02T00", "end_hour_exclusive": "2026-09-02T01", "host": "fast", "ledger_owner": "w1"}]
    assert data_key(blocks_a) != data_key(blocks_b)


def test_append_try_variant_counting_across_two_data_keys(tmp_path):
    log_path = tmp_path / "tries.jsonl"
    blocks_x = [{"start_hour": "2026-09-01T00", "end_hour_exclusive": "2026-09-01T01", "host": "fast", "ledger_owner": "w1"}]
    blocks_y = [{"start_hour": "2026-09-05T00", "end_hour_exclusive": "2026-09-05T01", "host": "fast", "ledger_owner": "w2"}]

    r1 = append_try(log_path, tool="t", config={}, data_blocks=blocks_x, result_path="r1.json", role="exploration")
    r2 = append_try(log_path, tool="t", config={}, data_blocks=blocks_y, result_path="r2.json", role="exploration")
    r3 = append_try(log_path, tool="t", config={}, data_blocks=blocks_x, result_path="r3.json", role="exploration")

    assert r1["data_key"] == r3["data_key"] != r2["data_key"]
    assert r1["variant_n"] == 1
    assert r2["variant_n"] == 1
    assert r3["variant_n"] == 2

    summary_x = tries_summary(log_path, r1["data_key"])
    summary_y = tries_summary(log_path, r2["data_key"])
    assert summary_x["of_m"] == 2
    assert summary_y["of_m"] == 1


def _append_worker(log_path: str, idx: int, n: int) -> None:
    blocks = [{"start_hour": "2026-09-01T00", "end_hour_exclusive": "2026-09-01T01", "host": "fast", "ledger_owner": "w1"}]
    for i in range(n):
        append_try(log_path, tool=f"worker{idx}", config={"i": i}, data_blocks=blocks, result_path=f"r{idx}-{i}.json", role="exploration")


def test_append_try_concurrent_writers_all_lines_land(tmp_path):
    log_path = str(tmp_path / "tries_concurrent.jsonl")
    ctx = mp.get_context("spawn")
    procs = [ctx.Process(target=_append_worker, args=(log_path, idx, 50)) for idx in range(2)]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=60)
        assert p.exitcode == 0

    lines = Path(log_path).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 100
    variant_ns = set()
    for line in lines:
        rec = json.loads(line)  # must be parseable -- no torn/interleaved writes
        variant_ns.add(rec["variant_n"])
    # 100 total appends against one data_key -> variant_n values 1..100, each once
    assert variant_ns == set(range(1, 101))


# --- schema validation / atomic write ----------------------------------------


def _flat_trades() -> list[dict]:
    return [_trade(0.05 + 0.001 * i, 5.0, _days(5)[i % 5]) for i in range(120)]


def _pressure_trades() -> list[dict]:
    return [_trade(0.03 + 0.001 * i, 3.0, _days(5)[i % 5]) for i in range(120)]


def _sample_result(tmp_path) -> dict:
    blocks = [{"start_hour": "2026-09-01T00", "end_hour_exclusive": "2026-09-02T00", "host": "fast", "ledger_owner": "w1"}]
    key = data_key(blocks)
    return build_result(
        tool="test_tool",
        git_sha="deadbeef",
        command="python3 -m tools.test_tool",
        config={"threshold": 0.5},
        role="exploration",
        data_blocks=blocks,
        stage="exploring",
        trades_flat=_flat_trades(),
        trades_pressure_s1=_pressure_trades(),
        tries={"data_key": key, "variant_n": 1, "of_m": 1},
    )


def test_schema_validates_a_well_formed_result(tmp_path):
    result = _sample_result(tmp_path)
    errors = validate_result(result)
    assert errors == []


def test_schema_rejects_missing_leg():
    result = _sample_result(Path("."))
    del result["metrics"]["pressure_s1"]
    errors = validate_result(result)
    assert errors, "removing metrics.pressure_s1 must fail validation"


def test_schema_rejects_missing_gate_field():
    result = _sample_result(Path("."))
    del result["gate"]["flat"]["ci90_lo_gt_0"]
    errors = validate_result(result)
    assert errors


def test_write_result_atomic_and_readable(tmp_path):
    result = _sample_result(tmp_path)
    out_path = tmp_path / "result.json"
    write_result(out_path, result)
    assert out_path.exists()
    loaded = json.loads(out_path.read_text(encoding="utf-8"))
    assert loaded["schema_version"] == "result.v1"
    # no leftover tmp files
    tmp_files = list(tmp_path.glob("result.json.tmp*"))
    assert tmp_files == []


def test_write_result_refuses_invalid_result(tmp_path):
    result = _sample_result(tmp_path)
    del result["metrics"]["flat"]
    out_path = tmp_path / "bad_result.json"
    with pytest.raises(ValueError):
        write_result(out_path, result)
    assert not out_path.exists()


def test_schema_file_is_valid_json_and_parseable():
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    assert schema["$schema"].endswith("2020-12/schema")
    assert "result.v1" == schema["properties"]["schema_version"]["const"]
