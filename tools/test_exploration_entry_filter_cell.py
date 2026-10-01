"""explore_entry_filter wiring: decision-equivalence of the default B3 grid path,
the single-cell entry point (tools.exploration_entry_model_b3.score_one_cell),
the pool-root allowlist, the hours-actually-opened fence, and the strict
block_time-in-hour filter.

Fixture only (tools/entry_filter_fixture.py): tmp roots of plain .jsonl hours,
no network, no real data, no /data/mal/blocks.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from unittest import mock

import pytest

import tools.exploration_entry_model_b3 as b3
from tools.entry_filter_fixture import build_pools


def _md5(b: bytes) -> str:
    return hashlib.md5(b).hexdigest()


@pytest.fixture(scope="module")
def pools(tmp_path_factory) -> tuple[Path, Path, Path]:
    return build_pools(tmp_path_factory.mktemp("pools"))


# ---------------------------------------------------------------------------
# 1. The default B3 grid path is decision-equivalent to main (golden captured
#    from the UNMODIFIED code at a788cc1, before any edit).
# ---------------------------------------------------------------------------

GOLDEN_ROWS_MD5 = "700978d11ccf58606b91fa37de6202dd"  # run_all_features_a/c/b rows, 3 pools
GOLDEN_N_ROWS = [58, 66, 50]
GOLDEN_JSON_MD5 = "0babab63ec0a19e679add0f77ab460ae"  # b3.main() --out-json bytes (wall_s pinned)
GOLDEN_MD_MD5 = "8eb4a0ef6103869991e0d0939f0d28a2"  # b3.main() --out-md bytes


def default_grid_digests(pools: tuple[Path, Path, Path], tmp: Path) -> dict:
    fast, insample, live = pools
    out_md, out_json = tmp / "o.md", tmp / "o.json"
    argv = ["b3", "--out-md", str(out_md), "--out-json", str(out_json), "--max-workers", "1",
            "--fast-dir", str(fast), "--oracle-insample-dir", str(insample), "--oracle-live-dir", str(live)]
    with mock.patch.object(sys, "argv", argv), mock.patch.object(b3.time, "time", lambda: 1000.0):
        b3.main()
    rows = [
        b3.run_all_features_a(max_workers=1, buffer_hours=2, backfill=fast),
        b3.run_all_features_c(max_workers=1, buffer_hours=2, root=insample),
        b3.run_all_features_b(max_workers=1, buffer_hours=2, root=live),
    ]
    return {
        "rows": _md5(json.dumps(rows, sort_keys=True).encode()),
        "n_rows": [len(r) for r in rows],
        "json": _md5(out_json.read_bytes()),
        "md": _md5(out_md.read_bytes()),
        "report": json.loads(out_json.read_text()),
    }


def test_default_grid_path_is_decision_equivalent_to_pre_change(pools, tmp_path):
    d = default_grid_digests(pools, tmp_path)
    assert d["n_rows"] == GOLDEN_N_ROWS
    assert d["rows"] == GOLDEN_ROWS_MD5
    assert d["json"] == GOLDEN_JSON_MD5
    assert d["md"] == GOLDEN_MD_MD5
    # the golden is not vacuous: models trained and selected trades on real folds
    folds = d["report"]["results"]["tpsl_tp50_sl30"]["s2_clf"]
    assert sum(1 for f in folds.values() if f.get("trained")) >= 5
    assert d["report"]["pooled"]["tpsl_tp50_sl30"]["s2_clf"]["top10"]["n"] > 0


# ---------------------------------------------------------------------------
# 2. The single-cell entry point
# ---------------------------------------------------------------------------

from datetime import datetime, timedelta

import tools.oracle_live_adapter as live_adapter
from tools.entry_filter_fixture import DAYS, epoch, mint_rows, write_jsonl
from tools.mal_templates import entry_filter as tpl

_REAL_EVAL = b3.evaluate_fold_cell
CELL = dict(model="s2_clf", exit_id="tpsl_tp50_sl30", size_sol=0.5, priority_lamports=500_000)


def run_cell(pools, monkeypatch, **kw):
    """score_one_cell with the fold evaluator spied: returns (result, every candidate
    row scored in the requested days, each exactly once as a held-out row)."""
    fast, insample, live = pools
    seen: list[dict] = []

    def spy(train, test, *a, **k):
        seen.extend(test)
        return _REAL_EVAL(train, test, *a, **k)

    monkeypatch.setattr(b3, "evaluate_fold_cell", spy)
    args = {**CELL, "days": list(DAYS), "select_top_pct": 30, "max_workers": 1,
            "fast_root": fast, "insample_root": insample, "live_root": live}
    args.update(kw)
    return b3.score_one_cell(**args), seen


@pytest.fixture
def allow(pools, monkeypatch):
    """Allowlist the tmp fixture roots (the real allowlist has no tmp dirs)."""
    fast, insample, live = pools
    monkeypatch.setattr(b3._ex, "POOL_ROOT_ALLOWLIST", (fast,))
    monkeypatch.setattr(b3, "CELL_ROOT_ALLOWLIST", {"C": (insample,), "B": (live,)})


def _grid_rows(pools):
    fast, insample, live = pools
    rows = (
        b3.run_all_features_a(max_workers=1, buffer_hours=2, backfill=fast)
        + b3.run_all_features_c(max_workers=1, buffer_hours=2, root=insample)
        + b3.run_all_features_b(max_workers=1, buffer_hours=2, root=live)
    )
    return {r["mint"]: r for r in rows if r["spec"] == "tpsl_tp50_sl30"}


def test_cell_rows_equal_the_grid_rows_for_that_exit(pools, allow, monkeypatch):
    """Same mints, same days, same features (incl. creator lookback from only the
    24h hours opened), same flat/press: the cell path scores what the grid scores."""
    out, seen = run_cell(pools, monkeypatch)
    grid = _grid_rows(pools)
    cell = {r["mint"]: r for r in seen}
    assert len(cell) == len(seen) == out["n_candidates"] == len(grid) > 40
    for mint, g in grid.items():
        c = cell[mint]
        for k in ("day", "status", "filled", "gross", "flat", "press", "features"):
            assert c[k] == g[k], (mint, k)
    assert out["n_days"] == 9 and out["rows"]
    assert sum(1 for f in out["folds"].values() if f["trained"]) >= 5


def test_cell_day_subset_rows_equal_grid_rows_for_those_days(pools, allow, monkeypatch):
    days = ["2026-09-22", "2026-09-23", "2026-09-25"]
    out, seen = run_cell(pools, monkeypatch, days=days)
    grid = {m: r for m, r in _grid_rows(pools).items() if r["day"] in days}
    cell = {r["mint"]: r for r in seen}
    assert set(cell) == set(grid)
    for mint, g in grid.items():
        for k in ("day", "flat", "press", "features"):
            assert cell[mint][k] == g[k], (mint, k)
    assert set(out["folds"]) == set(days)
    assert {r["day"] for r in out["rows"]} <= set(days)


def test_workers_do_not_change_rows(pools, allow, monkeypatch):
    days = ["2026-09-19", "2026-09-20", "2026-09-26"]
    one, _ = run_cell(pools, monkeypatch, days=days, max_workers=1)
    two, _ = run_cell(pools, monkeypatch, days=days, max_workers=2)
    assert one["rows"] == two["rows"] and one["n_candidates"] == two["n_candidates"]


def test_priority_and_size_are_parameters(pools, allow, monkeypatch):
    days = ["2026-09-19", "2026-09-20"]
    _, base = run_cell(pools, monkeypatch, days=days)
    _, cheap = run_cell(pools, monkeypatch, days=days, priority_lamports=58_000)
    _, small = run_cell(pools, monkeypatch, days=days, size_sol=0.25)
    b, c, s = ({r["mint"]: r for r in x} for x in (base, cheap, small))
    assert set(b) == set(c) == set(s) and b
    for m in b:
        assert c[m]["gross"] == b[m]["gross"] and c[m]["flat"] > b[m]["flat"]
        assert s[m]["gross"] != b[m]["gross"] or not b[m]["filled"]
        assert c[m]["features"] == b[m]["features"] == s[m]["features"]


def test_selection_rules(pools, allow, monkeypatch):
    days = ["2026-09-19", "2026-09-20", "2026-09-21"]
    top, _ = run_cell(pools, monkeypatch, days=days, select_top_pct=10)
    wide, _ = run_cell(pools, monkeypatch, days=days, select_top_pct=100)
    zero, _ = run_cell(pools, monkeypatch, days=days, select_top_pct=None, threshold=0.0)
    none, _ = run_cell(pools, monkeypatch, days=days, select_top_pct=None, threshold=1.0)
    trained_n = sum(f["n_test"] for f in wide["folds"].values() if f["trained"])
    assert trained_n > 0
    assert len(wide["rows"]) == trained_n and 0 < len(top["rows"]) < len(wide["rows"])
    assert len(zero["rows"]) == trained_n  # every probability is >= 0
    assert len(none["rows"]) == 0  # a probability of exactly 1.0 does not occur
    for d, f in top["folds"].items():  # top-k per held-out day is max(1, round(n * pct / 100))
        if f["trained"]:
            assert f["n_selected"] == max(1, round(f["n_test"] * 0.10))


@pytest.mark.parametrize("model", ["s1_reg", "s3_reg_winsor"])
def test_regression_models_run_and_threshold_is_refused_for_them(pools, allow, monkeypatch, model):
    out, _ = run_cell(pools, monkeypatch, model=model, days=["2026-09-19", "2026-09-20", "2026-09-21"])
    assert out["rows"]
    with pytest.raises(ValueError, match="probability"):
        run_cell(pools, monkeypatch, model=model, select_top_pct=None, threshold=0.5)


@pytest.mark.parametrize("kw", [
    {"select_top_pct": None},                      # neither
    {"threshold": 0.5},                            # both
    {"select_top_pct": 0},
    {"select_top_pct": None, "threshold": 1.5},
    {"model": "lgb_medium"},
    {"exit_id": "nope"},
    {"days": []},
    {"days": ["2026-09-28"]},                      # the clean-clock day
    {"days": ["2026-09-18"]},
    {"size_sol": 0},
    {"priority_lamports": -1},
])
def test_bad_params_refused_before_any_open(pools, allow, monkeypatch, kw):
    opened: list = []
    for name in ("_hour_info_a", "_hour_info_c", "_hour_info_b"):
        monkeypatch.setattr(b3, name, lambda *a, **k: opened.append(a))
    with pytest.raises(ValueError):
        run_cell(pools, monkeypatch, **kw)
    assert not opened


# ---------------------------------------------------------------------------
# 3. Roots: allowlist, resolved, refused before any file opens
# ---------------------------------------------------------------------------

def test_real_allowlist_contents():
    assert [str(p) for p in b3._cell_allowlist("A")] == [
        "/var/lib/mal/backfill-fast", "/data/mal/clean-view/fast-pool-2026-09-18T23_2026-09-22T00"]
    assert [str(p) for p in b3.CELL_ROOT_ALLOWLIST["C"]] == [
        "/home/claude/data/oracle-insample-2026-09-22_25", "/data/mal/clean-view/oracle-insample-2026-09-22_25"]
    assert [str(p) for p in b3.CELL_ROOT_ALLOWLIST["B"]] == [
        "/home/claude/data/oracle-live-2026-09-25_27", "/data/mal/clean-view/oracle-live-2026-09-25_27"]
    for pool in "ACB":  # the defaults are allowlisted
        assert b3.check_cell_root(pool, b3.CELL_ROOT_DEFAULT[pool]) == b3.CELL_ROOT_DEFAULT[pool].resolve()


REFUSED = [
    ("A", "/data/mal/blocks/fast-holdout-2026-09-09T12_2026-09-15T12"),
    ("A", "/data/mal/blocks"),
    ("A", "/data/mal/clean-view/fast-pool-2026-09-18T23_2026-09-22T00/../../blocks/x"),
    ("A", "/var/lib/mal/backfill-fast-b"),
    ("A", "/var/lib/mal/backfill-fast/../backfill-fast-c"),
    ("A", "/var/lib/mal/backfill-fast/trades"),
    ("C", "/data/mal/blocks/oracle-insample-holdout"),
    ("C", "/data/mal/clean-view/oracle-live-2026-09-25_27"),   # an exploration root, but the wrong slot
    ("B", "/data/mal/clean-view/oracle-insample-2026-09-22_25"),
    ("B", "/home/claude/data/oracle-live-2026-09-25_27/../oracle-live-2026-09-28"),
    ("B", "/tmp"),
]


@pytest.mark.parametrize("pool,bad", REFUSED)
def test_non_allowlisted_roots_refused(pool, bad):
    with pytest.raises(ValueError, match="not an allowlisted"):
        b3.check_cell_root(pool, bad)


@pytest.mark.parametrize("pool,bad", REFUSED)
def test_refusal_happens_before_any_file_is_opened(pool, bad, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("a file was opened before the root check")

    for name in ("_hour_info_a", "_hour_info_c", "_hour_info_b", "_iter_trades"):
        monkeypatch.setattr(b3, name, boom)
    monkeypatch.setattr(live_adapter, "_create_day_file", boom)
    key = {"A": "fast_root", "C": "insample_root", "B": "live_root"}[pool]
    with pytest.raises(ValueError, match="not an allowlisted"):
        b3.score_one_cell(**CELL, days=list(DAYS), select_top_pct=10, max_workers=1, **{key: bad})


@pytest.mark.parametrize("env,bad", [("MAL_FAST_POOL_ROOT", "/data/mal/blocks/x"), ("MAL_INSAMPLE_POOL_ROOT", "/tmp"),
                                     ("MAL_LIVE_POOL_ROOT", "/var/lib/mal/backfill-fast")])
def test_env_roots_are_checked_too(monkeypatch, env, bad):
    for k in ("MAL_FAST_POOL_ROOT", "MAL_INSAMPLE_POOL_ROOT", "MAL_LIVE_POOL_ROOT"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv(env, bad)
    monkeypatch.setattr(b3, "_hour_info_a", lambda *a, **k: (_ for _ in ()).throw(AssertionError("opened")))
    with pytest.raises(ValueError, match="not an allowlisted"):
        b3.score_one_cell(**CELL, days=list(DAYS), select_top_pct=10, max_workers=1)


def test_root_resolution_param_then_env_then_default(monkeypatch, tmp_path):
    for pool, env in b3.CELL_ROOT_ENV.items():
        monkeypatch.delenv(env, raising=False)
        assert b3.resolve_cell_root(pool) == b3.CELL_ROOT_DEFAULT[pool]
        monkeypatch.setenv(env, str(tmp_path / "env"))
        assert b3.resolve_cell_root(pool) == tmp_path / "env"
        assert b3.resolve_cell_root(pool, tmp_path / "arg") == tmp_path / "arg"


def test_tmp_dir_and_symlink_escape_refused(pools, tmp_path, monkeypatch):
    fast, insample, live = pools
    monkeypatch.setattr(b3._ex, "POOL_ROOT_ALLOWLIST", (tmp_path / "allowed-fast",))
    monkeypatch.setattr(b3, "CELL_ROOT_ALLOWLIST", {"C": (tmp_path / "allowed-c",), "B": (tmp_path / "allowed-b",)})
    with pytest.raises(ValueError, match="not an allowlisted"):  # a real fixture dir that is simply not on the list
        b3.check_cell_root("A", fast)
    # a link AT an allowlisted path that points elsewhere: resolve() follows it -> refused
    (tmp_path / "allowed-fast").symlink_to(fast)
    with pytest.raises(ValueError, match="not an allowlisted"):
        b3.check_cell_root("A", tmp_path / "allowed-fast")
    # a link under another name that points AT the allowlisted (real) dir is that dir: accepted
    real = tmp_path / "allowed-c"
    real.mkdir()
    (tmp_path / "alias-c").symlink_to(real)
    assert b3.check_cell_root("C", tmp_path / "alias-c") == real.resolve()
    with pytest.raises(ValueError, match="not an allowlisted"):  # `..` out of the allowlisted dir
        b3.check_cell_root("C", real / ".." / "elsewhere")
    with pytest.raises(ValueError, match="not an allowlisted"):  # a sub-directory is not the root
        b3.check_cell_root("C", real / "trades")
    # and through the entry point, with real data behind the escaping link
    with pytest.raises(ValueError, match="not an allowlisted"):
        b3.score_one_cell(**CELL, days=["2026-09-19"], select_top_pct=10, max_workers=1, fast_root=tmp_path / "allowed-fast")


# ---------------------------------------------------------------------------
# 4. resolve_data_blocks covers every hour opened (spy on the hour-info fns)
# ---------------------------------------------------------------------------

def _hours_in_blocks(blocks) -> set[str]:
    out: set[str] = set()
    for b in blocks:
        cur = datetime.strptime(b["start_hour"], "%Y-%m-%dT%H")
        end = datetime.strptime(b["end_hour_exclusive"], "%Y-%m-%dT%H")
        while cur < end:
            out.add(cur.strftime("%Y-%m-%dT%H"))
            cur += timedelta(hours=1)
    return out


SPY_DAYS = [["2026-09-19"], ["2026-09-21"], ["2026-09-22"], ["2026-09-25"], ["2026-09-26"], ["2026-09-27"],
            ["2026-09-19", "2026-09-21"], ["2026-09-24", "2026-09-26"], "all"]


@pytest.mark.parametrize("days", SPY_DAYS)
def test_blocks_equal_the_hours_actually_opened(pools, allow, monkeypatch, days):
    opened: dict[str, set[str]] = {"A": set(), "C": set(), "B": set()}
    for pool, name in (("A", "_hour_info_a"), ("C", "_hour_info_c"), ("B", "_hour_info_b")):
        real = getattr(b3, name)

        def spy(key, *a, _real=real, _pool=pool, **k):
            opened[_pool].add(key)
            return _real(key, *a, **k)

        monkeypatch.setattr(b3, name, spy)
    create_days: set[str] = set()
    real_day = live_adapter._create_day_file

    def day_spy(day, root=None):
        create_days.add(day)
        return real_day(day, root)

    monkeypatch.setattr(live_adapter, "_create_day_file", day_spy)
    day_list = tpl._resolve_days(days)
    out, _ = run_cell(pools, monkeypatch, days=day_list)
    read = set().union(*opened.values()) | {f"{d}T{k:02d}" for d in create_days for k in range(24)}
    assert read
    blocks = tpl.resolve_data_blocks({"days": days})
    assert _hours_in_blocks(blocks) == read == set(out["hours_read"])
    assert "2026-09-28" not in create_days and not any(h >= "2026-09-28" for h in read)


def test_buffer_and_lookback_hours_are_in_the_blocks():
    hours = _hours_in_blocks(tpl.resolve_data_blocks({"days": ["2026-09-22"]}))
    assert "2026-09-23T00" in hours and "2026-09-23T01" in hours and "2026-09-23T02" not in hours  # buffer
    assert "2026-09-22T00" in hours and "2026-09-21T23" not in hours  # lookback clipped at the pool C start
    p25 = _hours_in_blocks(tpl.resolve_data_blocks({"days": ["2026-09-25"]}))
    assert "2026-09-24T00" in p25  # pool C lookback of 25T00
    assert "2026-09-25T23" in p25 and "2026-09-26T00" not in p25
    assert all(h < "2026-09-28" for h in _hours_in_blocks(tpl.resolve_data_blocks({"days": "all"})))
    last = tpl.resolve_data_blocks({"days": ["2026-09-27"]})
    assert max(b["end_hour_exclusive"] for b in last) == "2026-09-28T00"  # clipped at the pool end, never past it
    assert {b["host"] for b in tpl.resolve_data_blocks({"days": ["2026-09-19"]})} == {"fast"}
    assert {b["host"] for b in tpl.resolve_data_blocks({"days": ["2026-09-19", "2026-09-23"]})} == {"fast", "oracle"}


def test_check_read_sees_every_block_on_real_ledger():
    from tools import mal_catalog

    repo = Path(__file__).resolve().parent.parent
    ledger = mal_catalog.parse_ledger((repo / "docs" / "HOLDOUT_LEDGER.md").read_text(encoding="utf-8"))
    for days in (["2026-09-19"], ["2026-09-21"], ["2026-09-22"], ["2026-09-25"], ["2026-09-27"], ["2026-09-24", "2026-09-26"], "all"):
        for b in tpl.resolve_data_blocks({"days": days}):
            ok, reasons = mal_catalog.check_read(ledger, "exploration", b["host"], b["start_hour"], b["end_hour_exclusive"])
            assert ok, (days, b, reasons)


# ---------------------------------------------------------------------------
# 5. Strict block_time-in-hour filter, new path only
# ---------------------------------------------------------------------------

def _spill_pools(tmp_path: Path):
    """A copy of the fixture where pool A hour 2026-09-19T08 carries (a) a mint whose
    create block_time is in the PREVIOUS hour and (b) one extra bonding print with
    block_time outside its hour on a real mint."""
    fast, insample, live = build_pools(tmp_path)
    spec = {"mint": "SPILL", "day": "2026-09-19", "hour": "2026-09-19T08", "j": 0, "creator": "crX", "n_bond": 3, "up": True, "slot0": 9_000_000}
    create, trades, _t0 = mint_rows(spec)
    create["block_time"] = epoch("2026-09-19T07") + 100  # in the 07 hour, written into the 08 file
    cpath = fast / "creates" / "creates-2026-09-19T08.jsonl"
    tpath = fast / "trades" / "trades-2026-09-19T08.jsonl"
    old_c = [json.loads(x) for x in cpath.read_text().splitlines() if x]
    old_t = [json.loads(x) for x in tpath.read_text().splitlines() if x]
    victim_row = next(r for r in old_t if r["venue"] == "pump_bonding")
    extra = dict(victim_row)  # block_time in hour 09 while sitting in the 08 file
    extra.update(block_time=epoch("2026-09-19T09") + 5, signature="out-of-hour", side="buy", sol_lamports=9_000_000_000)
    write_jsonl(cpath, old_c + [create])
    write_jsonl(tpath, old_t + trades + [extra])
    return (fast, insample, live), victim_row["mint"]


def test_strict_block_time_filter_is_on_the_cell_path_only(tmp_path, monkeypatch):
    pools_, victim = _spill_pools(tmp_path)
    fast, insample, live = pools_
    monkeypatch.setattr(b3._ex, "POOL_ROOT_ALLOWLIST", (fast,))
    monkeypatch.setattr(b3, "CELL_ROOT_ALLOWLIST", {"C": (insample,), "B": (live,)})
    days = ["2026-09-19", "2026-09-20", "2026-09-21"]
    _, cell = run_cell(pools_, monkeypatch, days=days)
    cell = {r["mint"]: r for r in cell}
    grid = {r["mint"]: r for r in b3.run_all_features_a(max_workers=1, buffer_hours=2, backfill=fast) if r["spec"] == "tpsl_tp50_sl30"}
    assert "SPILL" in grid and "SPILL" not in cell  # out-of-hour create: grid keeps it, cell drops it
    assert victim in grid and victim in cell
    assert cell[victim]["features"]["n_bonding_trades"] == grid[victim]["features"]["n_bonding_trades"] - 1  # out-of-hour print dropped
    assert cell[victim]["features"]["buy_sol"] < grid[victim]["features"]["buy_sol"]


# ---------------------------------------------------------------------------
# 6. The template
# ---------------------------------------------------------------------------

def _params(**kw):
    p = {"trigger": "migrate", "model": "s2_clf", "select_top_pct": 30, "exit": "tpsl_tp50_sl30", "size_sol": 0.5,
         "priority_fee_tier": "p75", "mcap_band": None, "days": ["2026-09-19", "2026-09-20", "2026-09-21"]}
    p.update(kw)
    return p


def test_fee_tiers():
    assert tpl.PRIORITY_LAMPORTS == {"p50": 58_000, "p75": 500_000}
    with pytest.raises(ValueError, match="p90"):
        tpl.run(_params(priority_fee_tier="p90"))
    with pytest.raises(ValueError, match="mcap_band"):
        tpl.run(_params(mcap_band={"min": 1}))


def test_template_run_end_to_end_on_fixture(pools, allow, monkeypatch):
    fast, insample, live = pools
    monkeypatch.setenv("MAL_FAST_POOL_ROOT", str(fast))
    monkeypatch.setenv("MAL_INSAMPLE_POOL_ROOT", str(insample))
    monkeypatch.setenv("MAL_LIVE_POOL_ROOT", str(live))
    out = tpl.run(_params(priority_fee_tier="p50"))
    assert set(out) == {"trades_flat", "trades_pressure_s1", "n_candidates", "n_days", "peak_rss_mb", "notes"}
    assert len(out["trades_flat"]) == len(out["trades_pressure_s1"]) > 0
    assert out["n_days"] == 3 and out["n_candidates"] >= len(out["trades_flat"])
    t = out["trades_flat"][0]
    assert set(t) == {"sol", "pct", "day", "filled"} and abs(t["pct"] - t["sol"] / 0.5 * 100) < 1e-9
    p75 = tpl.run(_params(priority_fee_tier="p75"))
    assert "p50" in out["notes"] and "58000" in out["notes"] and "500000" in p75["notes"]


def test_template_run_refuses_non_allowlisted_param_root(tmp_path):
    with pytest.raises(ValueError, match="not an allowlisted"):
        tpl.run(_params(fast_pool_root=str(tmp_path)))
    with pytest.raises(ValueError, match="not an allowlisted"):
        tpl.run(_params(live_pool_root="/data/mal/blocks/live-holdout", days=["2026-09-26"]))
