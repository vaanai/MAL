"""score_one_spec / explore_exit wiring: decision-equivalence of the default
grid path, data-root parameter, and the hours-actually-opened fence.

Fixture only: a tmp root of plain .jsonl hours, no network, no real data.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from tools import exploration_exits as ex


def _epoch(hour: str) -> int:
    return int(datetime.strptime(hour, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc).timestamp())


def _bond(mint: str, slot: int, t_ms: int, quote: int, base: int) -> dict[str, Any]:
    return {
        "type": "trade", "mint": mint, "venue": "pump_bonding", "slot": slot, "t_recv_ms": t_ms,
        "block_time": t_ms // 1000, "quote_reserve": quote, "base_reserve": base,
        "sol_lamports": 1_000_000, "side": "buy", "event_index": 1, "signature": f"b-{mint}-{slot}",
    }


def _swap(mint: str, slot: int, t_ms: int, quote: int, base: int) -> dict[str, Any]:
    row = _bond(mint, slot, t_ms, quote, base)
    row.update(venue="pumpswap", quote_is_wsol=True, signature=f"s-{mint}-{slot}")
    return row


def _write(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def _mint_rows(mint: str, hour: str, slot0: int, up: bool) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    t0 = (_epoch(hour) + 60) * 1000
    base = 400_000_000_000_000
    create = {"type": "create", "mint": mint, "slot": slot0, "block_time": t0 // 1000,
              "quote_reserve": 30_000_000_000, "base_reserve": base}
    trades = [_bond(mint, slot0 + 1, t0 + 1_000, 30_000_000_000, base),
              _swap(mint, slot0 + 100, t0 + 40_000, 80_000_000_000, base),
              _swap(mint, slot0 + 101, t0 + 40_400, 80_000_000_000, base),
              _swap(mint, slot0 + 102, t0 + 40_800, 80_000_000_000, base)]
    q = 128_000_000_000 if up else 50_000_000_000
    for i in range(1, 6):
        trades.append(_swap(mint, slot0 + 110 + i, t0 + 60_000 + i * 20_000, q, base))
    return create, trades


# (mint, hour, slot0, up)
FIXTURE_MINTS = [
    ("MA", "2026-09-19T05", 1000, True),
    ("MB", "2026-09-20T10", 5000, False),
    ("MC", "2026-09-21T12", 9000, True),
]


def build_fixture(root: Path, hours: Iterable[str] | None = None, mints=FIXTURE_MINTS) -> list[str]:
    """Every requested pool hour gets a (mostly empty) trades + creates file."""
    hours = list(ex.POOL_HOURS if hours is None else hours)
    by_hour_t: dict[str, list[dict[str, Any]]] = {h: [] for h in hours}
    by_hour_c: dict[str, list[dict[str, Any]]] = {h: [] for h in hours}
    for mint, hour, slot0, up in mints:
        create, trades = _mint_rows(mint, hour, slot0, up)
        if hour in by_hour_c:
            by_hour_c[hour].append(create)
            by_hour_t[hour].extend(trades)
    for h in hours:
        _write(root / "trades" / f"trades-{h}.jsonl", by_hour_t[h])
        _write(root / "creates" / f"creates-{h}.jsonl", by_hour_c[h])
    return hours


def rows_md5(rows: Any) -> str:
    return hashlib.md5(json.dumps(rows, sort_keys=True).encode()).hexdigest()


def default_grid_rows(root: Path) -> list[dict[str, Any]]:
    old = ex.BACKFILL
    ex.BACKFILL = root
    try:
        return ex.run_all(max_workers=1, buffer_hours=2)
    finally:
        ex.BACKFILL = old


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

from datetime import timedelta

import pytest

from tools.mal_templates import exit as exit_tpl

# Recorded from the ORIGINAL (pre-change) run_all/summarize on this fixture.
GOLDEN_ROWS_MD5 = "4e42b4648a3d2597dfc3e63fb3f98c2f"
GOLDEN_SUMMARY_MD5 = "120383db5e75040c991772ae43a6379a"
GOLDEN_N_ROWS = 99


@pytest.fixture(scope="module")
def pool_root(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("fast-pool")
    build_fixture(root)
    return root


def test_default_grid_path_is_decision_equivalent_to_pre_change(pool_root):
    rows = default_grid_rows(pool_root)
    assert len(rows) == GOLDEN_N_ROWS
    assert rows_md5(rows) == GOLDEN_ROWS_MD5
    assert rows_md5(ex.summarize(rows)) == GOLDEN_SUMMARY_MD5


def test_explicit_defaults_equal_implicit_defaults(pool_root):
    old = ex.BACKFILL
    ex.BACKFILL = pool_root
    try:
        implicit = ex.run_worker(0, ex.POOL_HOURS, [])
        explicit = ex.run_worker(0, ex.POOL_HOURS, [], pool_root, ex.SPECS, ex.ENTRY_SIZE, ex.ENTRY_PRIORITY_LAMPORTS)
    finally:
        ex.BACKFILL = old
    assert rows_md5(implicit) == rows_md5(explicit) == GOLDEN_ROWS_MD5


def test_chunk_plan_and_grid_constants_unchanged():
    assert ex.ENTRY_SIZE == 500_000_000 and ex.ENTRY_PRIORITY_LAMPORTS == 500_000
    assert len(ex.SPECS) == 41
    plan = ex.chunk_plan(ex.POOL_HOURS, 4, 2)
    assert [len(h) for _i, h, _b in plan] == [18, 18, 18, 17]
    assert plan[0][2] == ["2026-09-19T19", "2026-09-19T20"]
    assert plan[-1][2] == []


def test_score_one_spec_all_days_matches_grid_rows_for_that_spec(pool_root):
    spec = next(s for s in ex.SPECS if s["id"] == "tpsl_tp20_sl15")
    out = ex.score_one_spec(spec, days=list(exit_tpl.DAYS), root=pool_root, max_workers=1)
    grid = [r for r in default_grid_rows(pool_root) if r["spec"] == "tpsl_tp20_sl15"]
    assert out["rows"] == grid and len(grid) == 3
    assert out["n_days"] == 3


def test_score_one_spec_plan_for_all_days_equals_chunk_plan():
    assert ex._plan_days(list(exit_tpl.DAYS), 4, 2) == ex.chunk_plan(ex.POOL_HOURS, 4, 2)


def test_score_one_spec_rejects_days_outside_pool(pool_root):
    with pytest.raises(ValueError):
        ex.score_one_spec(ex.SPECS[0], days=["2026-09-22"], root=pool_root, max_workers=1)


def test_priority_is_a_parameter(pool_root):
    spec = next(s for s in ex.SPECS if s["id"] == "tpsl_tp20_sl15")
    base = ex.score_one_spec(spec, days=["2026-09-19"], root=pool_root, max_workers=1)["rows"]
    cheap = ex.score_one_spec(spec, days=["2026-09-19"], root=pool_root, max_workers=1, priority_lamports=58_000)["rows"]
    assert len(base) == len(cheap) == 1
    assert cheap[0]["flat"] > base[0]["flat"]  # lower priority fee, same fill
    assert cheap[0]["gross"] == base[0]["gross"]


def test_root_resolution_param_then_env_then_default(monkeypatch, tmp_path):
    monkeypatch.delenv("MAL_FAST_POOL_ROOT", raising=False)
    assert ex._resolve_pool_root() == ex.BACKFILL
    monkeypatch.setenv("MAL_FAST_POOL_ROOT", str(tmp_path / "env"))
    assert ex._resolve_pool_root() == tmp_path / "env"
    assert ex._resolve_pool_root(tmp_path / "arg") == tmp_path / "arg"


def test_blocks_include_trailing_buffer_hours():
    blocks = exit_tpl.resolve_data_blocks({"days": ["2026-09-19"]})
    assert blocks == [
        {"start_hour": "2026-09-19T01", "end_hour_exclusive": "2026-09-20T02", "host": "fast",
         "ledger_owner": "exploration-pool"}
    ]
    # Last day: buffer is clipped at the pool end, never past 2026-09-21T23.
    last = exit_tpl.resolve_data_blocks({"days": ["2026-09-21"]})
    assert last[-1]["end_hour_exclusive"] == "2026-09-22T00"
    # Non-adjacent days -> two blocks, the first carrying its own buffer.
    split = exit_tpl.resolve_data_blocks({"days": ["2026-09-19", "2026-09-21"]})
    assert [(b["start_hour"], b["end_hour_exclusive"]) for b in split] == [
        ("2026-09-19T01", "2026-09-20T02"),
        ("2026-09-21T00", "2026-09-22T00"),
    ]
    allb = exit_tpl.resolve_data_blocks({"days": "all"})
    assert [(b["start_hour"], b["end_hour_exclusive"]) for b in allb] == [("2026-09-19T01", "2026-09-22T00")]


def _hours_in_blocks(blocks) -> set[str]:
    out: set[str] = set()
    for b in blocks:
        cur = datetime.strptime(b["start_hour"], "%Y-%m-%dT%H")
        end = datetime.strptime(b["end_hour_exclusive"], "%Y-%m-%dT%H")
        while cur < end:
            out.add(cur.strftime("%Y-%m-%dT%H"))
            cur += timedelta(hours=1)
    return out


@pytest.mark.parametrize("days", [["2026-09-19"], ["2026-09-20"], ["2026-09-21"], ["2026-09-19", "2026-09-21"], "all"])
def test_day_subset_opens_no_hour_outside_its_blocks(pool_root, monkeypatch, days):
    opened: list[str] = []
    real = ex._iter_trades

    def spy(path):
        opened.append(Path(path).name.split("-", 1)[1].split(".")[0])
        return real(path)

    monkeypatch.setattr(ex, "_iter_trades", spy)
    allowed = _hours_in_blocks(exit_tpl.resolve_data_blocks({"days": days}))
    spec = exit_tpl.build_spec({"family": "tp_sl_grid", "tp_pct": 20, "sl_pct": 15})
    ex.score_one_spec(spec, days=exit_tpl._resolve_days(days), root=pool_root, max_workers=1)
    assert opened, "nothing was read"
    assert set(opened) <= allowed
    # the blocks are not looser than the read set either
    assert set(opened) == allowed
    # the buffer hours are really opened, not just declared
    if days == ["2026-09-19"]:
        assert "2026-09-20T01" in opened and "2026-09-20T02" not in opened


def test_check_read_sees_buffer_hours_on_real_ledger():
    from tools import mal_catalog

    repo = Path(__file__).resolve().parent.parent
    ledger = mal_catalog.parse_ledger((repo / "docs" / "HOLDOUT_LEDGER.md").read_text(encoding="utf-8"))
    for days in (["2026-09-19"], ["2026-09-21"], "all"):
        for b in exit_tpl.resolve_data_blocks({"days": days}):
            ok, reasons = mal_catalog.check_read(ledger, "exploration", b["host"], b["start_hour"], b["end_hour_exclusive"])
            assert ok, reasons


def test_build_spec_matches_the_grid_cells():
    by_id = {s["id"]: s for s in ex.SPECS}
    cases = [
        {"family": "tp_sl_grid", "tp_pct": 50, "sl_pct": 30},
        {"family": "time_cap", "tp_pct": 50, "sl_pct": 30, "cap_minutes": 5},
        {"family": "trailing_stop", "trail_pct": 15, "activation_pct": 20},
        {"family": "trailing_stop", "trail_pct": 10, "activation_pct": None},
        {"family": "partial_ladder", "take_pct": 100, "trail_rem_pct": 20},
    ]
    for case in cases:
        spec = exit_tpl.build_spec(case)
        want = dict(by_id[spec["id"]])
        want.pop("desc")
        spec.pop("desc")
        assert spec == want


def test_run_end_to_end_on_fixture(pool_root):
    params = {"trigger": "migrate", "family": "tp_sl_grid", "tp_pct": 20, "sl_pct": 15, "size_sol": 0.5,
              "priority_fee_tier": "p75", "days": "all", "data_root": str(pool_root)}
    res = exit_tpl.run(params)
    assert set(res) == {"trades_flat", "trades_pressure_s1", "n_candidates", "n_days", "peak_rss_mb", "notes"}
    assert res["n_days"] == 3 and res["n_candidates"] == len(res["trades_flat"]) == 3
    grid = [r for r in default_grid_rows(pool_root) if r["spec"] == "tpsl_tp20_sl15"]
    assert sorted(t["day"] for t in res["trades_flat"]) == sorted(r["day"] for r in grid)
    assert set(res["trades_flat"][0]) == {"sol", "pct", "day", "filled"}
    from tools import mal_result

    leg = mal_result.leg_metrics(res["trades_flat"], n_candidates=res["n_candidates"], n_days=res["n_days"])
    assert leg["n_trades"] == 3


def test_run_uses_env_root_and_refuses_unsupported(pool_root, monkeypatch):
    monkeypatch.setenv("MAL_FAST_POOL_ROOT", str(pool_root))
    base = {"trigger": "migrate", "family": "tp_sl_grid", "tp_pct": 20, "sl_pct": 15, "size_sol": 0.5, "days": ["2026-09-19"]}
    assert exit_tpl.run(base)["n_candidates"] == 1
    with pytest.raises(ValueError):
        exit_tpl.run(dict(base, mcap_band={"min": 1}))
    with pytest.raises(ValueError):
        exit_tpl.run(dict(base, priority_fee_tier="p90"))
