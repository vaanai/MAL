"""Tests for tools.mal_job -- the shared MAL Console job entry point
(docs/console-plan.md §9 item 2).

No live data, no SSH, no replay or exploration job. Schema validation runs
against the real templates/*.schema.json. The data-catalog check runs
against a tiny synthetic ledger fragment (never the real
docs/HOLDOUT_LEDGER.md) so this file never depends on -- or accidentally
exercises -- the real exploration-pool fence. The happy path monkeypatches
the runner with a synthetic trades fixture; it never calls
tools.mal_templates.entry_filter.run / exit.run, which are intentionally
NotImplementedError.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools import mal_job
from tools.mal_templates import entry_filter as entry_filter_tpl

REPO_ROOT = Path(__file__).resolve().parent.parent


# --- fixtures -----------------------------------------------------------------


@pytest.fixture()
def synthetic_ledger(tmp_path) -> Path:
    """A tiny ledger with an exploration-pool day and an EXP-011 (confirmation
    only) day, so catalog-refusal tests don't need the real ledger."""
    text = """# fixture ledger

## Table

| Block | Hours | Host | Owner | Status |
| --- | --- | --- | --- | --- |
| Fixture exploration day | [2026-09-19T00, 2026-09-20T00) | fast | exploration pool | fixture |
| Fixture EXP-011 day | [2026-01-01T00, 2026-01-02T00) | fast | EXP-011 | fixture |
"""
    path = tmp_path / "ledger.md"
    path.write_text(text, encoding="utf-8")
    return path


def _entry_filter_params(**overrides) -> dict:
    params = {
        "trigger": "migrate",
        "model": "s2_clf",
        "select_top_pct": 10,
        "exit": "tpsl_tp50_sl30",
        "size_sol": 0.5,
        "days": ["2026-09-19"],
    }
    params.update(overrides)
    return params


def _trade(sol: float, pct: float, day: str) -> dict:
    return {"sol": sol, "pct": pct, "day": day, "filled": True}


def _synthetic_trades(n: int = 120) -> list[dict]:
    days = ["2026-09-19", "2026-09-20", "2026-09-21", "2026-09-22", "2026-09-23"]
    return [_trade(0.02 + 0.001 * i, 4.0, days[i % len(days)]) for i in range(n)]


# --- (a) schema validation -----------------------------------------------------


def test_unknown_param_refused():
    schema = mal_job.load_schema("explore_entry_filter")
    params = _entry_filter_params(bogus_param="nope")
    errors = mal_job.validate_params(schema, params)
    assert errors, "an unknown param must be refused"


def test_out_of_range_value_refused():
    schema = mal_job.load_schema("explore_entry_filter")
    params = _entry_filter_params(select_top_pct=999)
    errors = mal_job.validate_params(schema, params)
    assert errors, "select_top_pct=999 is outside 1..50 and must be refused"


def test_both_select_top_pct_and_threshold_refused():
    schema = mal_job.load_schema("explore_entry_filter")
    params = _entry_filter_params(threshold=0.5)  # already has select_top_pct=10
    errors = mal_job.validate_params(schema, params)
    assert errors, "setting both select_top_pct and threshold must be refused"


def test_neither_select_top_pct_nor_threshold_refused():
    schema = mal_job.load_schema("explore_entry_filter")
    params = _entry_filter_params()
    del params["select_top_pct"]
    errors = mal_job.validate_params(schema, params)
    assert errors, "setting neither select_top_pct nor threshold must be refused"


def test_valid_entry_filter_params_pass_schema():
    schema = mal_job.load_schema("explore_entry_filter")
    errors = mal_job.validate_params(schema, _entry_filter_params())
    assert errors == []


def test_explore_exit_schema_requires_family_specific_params():
    schema = mal_job.load_schema("explore_exit")
    base = {"trigger": "migrate", "family": "tp_sl_grid", "size_sol": 0.5, "days": "all"}
    # tp_sl_grid without tp_pct/sl_pct is refused
    assert mal_job.validate_params(schema, base)
    ok = dict(base, tp_pct=50, sl_pct=30)
    assert mal_job.validate_params(schema, ok) == []


# --- (b) role refusal -----------------------------------------------------------


@pytest.mark.parametrize("role", ["confirmation-oneshot", "ops", ""])
def test_disallowed_role_refused(monkeypatch, role):
    monkeypatch.setenv("MISCUSI_JOB_ROLE", role)
    with pytest.raises(mal_job.JobRefused) as exc_info:
        mal_job.require_exploration_role(role)
    assert exc_info.value.exit_code == 3


def test_role_env_absent_defaults_to_exploration_with_warning(monkeypatch):
    monkeypatch.delenv("MISCUSI_JOB_ROLE", raising=False)
    role, warnings = mal_job.resolve_role()
    assert role == "exploration"
    assert warnings and "MISCUSI_JOB_ROLE" in warnings[0]


def test_role_env_exploration_no_warning(monkeypatch):
    monkeypatch.setenv("MISCUSI_JOB_ROLE", "exploration")
    role, warnings = mal_job.resolve_role()
    assert role == "exploration"
    assert warnings == []


def test_run_job_refuses_disallowed_role_before_runner_called(monkeypatch, tmp_path, synthetic_ledger):
    monkeypatch.setenv("MISCUSI_JOB_ROLE", "ops")
    called = {"run": False}
    monkeypatch.setitem(mal_job.TEMPLATES["explore_entry_filter"], "run", lambda params: called.update(run=True) or {})
    code = mal_job.run_job(
        "explore_entry_filter",
        _entry_filter_params(),
        out_arg=str(tmp_path / "result.json"),
        ledger_path=synthetic_ledger,
    )
    assert code == 3
    assert called["run"] is False
    assert not (tmp_path / "result.json").exists()


# --- (c) catalog refusal ---------------------------------------------------------


def test_run_job_refuses_exp011_hour_before_runner_called(monkeypatch, tmp_path, synthetic_ledger):
    monkeypatch.setenv("MISCUSI_JOB_ROLE", "exploration")
    called = {"run": False}

    def _resolve_exp011_hour(params):
        return [{"start_hour": "2026-01-01T00", "end_hour_exclusive": "2026-01-01T01", "host": "fast", "ledger_owner": "EXP-011"}]

    monkeypatch.setitem(mal_job.TEMPLATES["explore_entry_filter"], "resolve_data_blocks", _resolve_exp011_hour)
    monkeypatch.setitem(mal_job.TEMPLATES["explore_entry_filter"], "run", lambda params: called.update(run=True) or {})

    code = mal_job.run_job(
        "explore_entry_filter",
        _entry_filter_params(),
        out_arg=str(tmp_path / "result.json"),
        ledger_path=synthetic_ledger,
    )
    assert code == 3
    assert called["run"] is False
    assert not (tmp_path / "result.json").exists()


def test_check_all_blocks_allows_exploration_pool_hour(synthetic_ledger):
    blocks = [{"start_hour": "2026-09-19T00", "end_hour_exclusive": "2026-09-19T01", "host": "fast", "ledger_owner": "exploration-pool"}]
    mal_job.check_all_blocks(blocks, "exploration", ledger_path=synthetic_ledger)  # must not raise


def test_check_all_blocks_denies_exp011_hour(synthetic_ledger):
    blocks = [{"start_hour": "2026-01-01T00", "end_hour_exclusive": "2026-01-01T01", "host": "fast", "ledger_owner": "EXP-011"}]
    with pytest.raises(mal_job.JobRefused) as exc_info:
        mal_job.check_all_blocks(blocks, "exploration", ledger_path=synthetic_ledger)
    assert exc_info.value.exit_code == 3
    assert any("EXP-011" in r for r in exc_info.value.reasons)


# --- template resolve_data_blocks against the REAL ledger ------------------------


def test_entry_filter_resolve_data_blocks_is_allowed_on_real_ledger():
    """The real exploration-pool days this template declares must actually be
    ALLOW under the real ledger -- a live sanity check that the hardcoded
    DAY_HOST map hasn't drifted from docs/HOLDOUT_LEDGER.md."""
    from tools import mal_catalog

    blocks = mal_catalog.parse_ledger((REPO_ROOT / "docs" / "HOLDOUT_LEDGER.md").read_text(encoding="utf-8"))
    for day in entry_filter_tpl.ALL_DAYS:
        data_blocks = entry_filter_tpl.resolve_data_blocks({"days": [day]})
        for b in data_blocks:
            ok, reasons = mal_catalog.check_read(blocks, "exploration", b["host"], b["start_hour"], b["end_hour_exclusive"])
            assert ok, f"{day}: {reasons}"


# --- (d)-(e) happy path with a monkeypatched runner -------------------------------


def test_happy_path_writes_result_and_miscusi_result_and_tries(monkeypatch, tmp_path, synthetic_ledger):
    monkeypatch.setenv("MISCUSI_JOB_ROLE", "exploration")
    monkeypatch.setenv("MAL_TRIES_LOG", str(tmp_path / "tries.jsonl"))
    miscusi_result_path = tmp_path / "miscusi_result.json"
    monkeypatch.setenv("MISCUSI_RESULT", str(miscusi_result_path))
    progress_path = tmp_path / "progress.json"
    monkeypatch.setenv("MISCUSI_PROGRESS", str(progress_path))

    trades = _synthetic_trades(120)
    monkeypatch.setitem(
        mal_job.TEMPLATES["explore_entry_filter"],
        "run",
        lambda params: {"trades_flat": trades, "trades_pressure_s1": trades, "n_candidates": 1200, "n_days": 5},
    )

    out_path = tmp_path / "result.json"
    code = mal_job.run_job(
        "explore_entry_filter",
        _entry_filter_params(days=["2026-09-19"]),
        out_arg=str(out_path),
        ledger_path=synthetic_ledger,
    )
    assert code == 0

    # result.json validates as result.v1
    from tools.mal_result import validate_result

    result = json.loads(out_path.read_text(encoding="utf-8"))
    assert validate_result(result) == []
    assert result["role"] == "exploration"
    assert result["stage"] == "exploring"
    assert result["metrics"]["flat"]["n_trades"] == 120

    # MISCUSI_RESULT written
    miscusi_doc = json.loads(miscusi_result_path.read_text(encoding="utf-8"))
    assert miscusi_doc["ok"] is True
    assert miscusi_doc["metrics"]["n_trades"] == 120
    assert miscusi_doc["outputs"] == ["result.json"]
    assert miscusi_doc["dataVersion"]

    # progress reported
    assert json.loads(progress_path.read_text(encoding="utf-8"))["pct"] == 100

    # tries log incremented
    lines = (tmp_path / "tries.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["variant_n"] == 1
    assert result["tries"]["variant_n"] == 1
    assert result["tries"]["of_m"] == 1


def test_happy_path_second_run_same_data_increments_variant(monkeypatch, tmp_path, synthetic_ledger):
    monkeypatch.setenv("MISCUSI_JOB_ROLE", "exploration")
    tries_log = tmp_path / "tries.jsonl"
    monkeypatch.setenv("MAL_TRIES_LOG", str(tries_log))

    trades = _synthetic_trades(120)
    monkeypatch.setitem(
        mal_job.TEMPLATES["explore_entry_filter"],
        "run",
        lambda params: {"trades_flat": trades, "trades_pressure_s1": trades, "n_candidates": 1200, "n_days": 5},
    )

    params = _entry_filter_params(days=["2026-09-19"])
    for i in range(2):
        code = mal_job.run_job(
            "explore_entry_filter", params, out_arg=str(tmp_path / f"result{i}.json"), ledger_path=synthetic_ledger
        )
        assert code == 0

    lines = tries_log.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    r2 = json.loads((tmp_path / "result1.json").read_text(encoding="utf-8"))
    assert r2["tries"]["variant_n"] == 2
    assert r2["tries"]["of_m"] == 2


# --- real (not-yet-wired) runners stay refused correctly, not silently skipped ----


def test_real_entry_filter_runner_raises_not_implemented():
    with pytest.raises(NotImplementedError):
        entry_filter_tpl.run(_entry_filter_params())


def test_real_exit_runner_raises_not_implemented():
    from tools.mal_templates import exit as exit_tpl

    with pytest.raises(NotImplementedError):
        exit_tpl.run({"trigger": "migrate", "family": "tp_sl_grid", "tp_pct": 50, "sl_pct": 30, "size_sol": 0.5, "days": "all"})


def test_exit_resolve_data_blocks_is_allowed_on_real_ledger():
    from tools import mal_catalog
    from tools.mal_templates import exit as exit_tpl

    blocks = mal_catalog.parse_ledger((REPO_ROOT / "docs" / "HOLDOUT_LEDGER.md").read_text(encoding="utf-8"))
    data_blocks = exit_tpl.resolve_data_blocks({"days": "all"})
    assert len(data_blocks) == 3
    for b in data_blocks:
        ok, reasons = mal_catalog.check_read(blocks, "exploration", b["host"], b["start_hour"], b["end_hour_exclusive"])
        assert ok, reasons
