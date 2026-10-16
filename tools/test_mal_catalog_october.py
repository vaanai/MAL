"""Refusal tests for the October F1 roles (tools/mal_catalog_october.py).

Synthetic fixtures only (tmp dirs, tiny dicts). The real FINAL ledger and every
October/forward/walk data file are never opened.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tools import mal_catalog as mc
from tools import mal_catalog_october as oc

REPO_ROOT = Path(__file__).resolve().parent.parent
NOW = datetime(2026, 10, 16, 5, 0, tzinfo=timezone.utc)
D0, D1 = oc.ROLE_OCT_D0, oc.ROLE_OCT_D1


@pytest.fixture(scope="module")
def blocks():
    return mc.parse_ledger((REPO_ROOT / "docs" / "HOLDOUT_LEDGER.md").read_text(encoding="utf-8"))


def _marker(tmp_path: Path, doc: dict | None = None, raw: str | None = None) -> Path:
    p = tmp_path / "FINAL_READS.jsonl"
    p.write_text(raw if raw is not None else json.dumps(doc if doc is not None else {"final": True}) + "\n")
    return p


def _read(blocks, tmp_path, role=D0, *, dataset="forward-1002", start="2026-10-09T00", end="2026-10-16T01",
          columns=("mint", "slot"), ledger=None, now=NOW, paths=()):
    return oc.check_read(blocks, role, dataset, start, end, columns, paths=paths,
                         final_ledger=ledger if ledger is not None else _marker(tmp_path), now=now,
                         blocks_root=tmp_path / "blocks")


def _reasons_have(res, text):
    ok, reasons = res
    assert ok is False
    assert any(text in r for r in reasons), reasons


# --- the happy path and plumbing -------------------------------------------


@pytest.mark.parametrize("role", [D0, D1])
def test_allowed_when_every_condition_holds(blocks, tmp_path, role):
    cols = ("mint", "slot", "n_trades") if role == D0 else ("mint", "slot", "side", "sol_lamports")
    assert _read(blocks, tmp_path, role, columns=cols) == (True, [])


def test_roles_registered_and_hour_only_guard_never_grants(blocks):
    assert D0 in mc.ROLES and D1 in mc.ROLES
    for role in (D0, D1):
        assert mc.allowed(role, "EXP-012", "EXP-012") is False
        ok, _ = mc.check_read(blocks, role, "research", "2026-10-09T00", "2026-10-10T00", exp_id="EXP-012")
        assert ok is False


def test_unknown_role_and_dataset_refused(blocks, tmp_path):
    _reasons_have(_read(blocks, tmp_path, "exploration"), "not an October role")
    _reasons_have(_read(blocks, tmp_path, dataset="truth-1002"), "not released")


# --- FINAL marker and clock (fail closed) ----------------------------------


@pytest.mark.parametrize("role", [D0, D1])
def test_missing_final_ledger_refused(blocks, tmp_path, role):
    _reasons_have(_read(blocks, tmp_path, role, ledger=tmp_path / "nope.jsonl"), "FINAL marker not accepted")


def test_test_window_marker_refused(blocks, tmp_path):
    led = _marker(tmp_path, {"final": True, "test_window": True})
    _reasons_have(_read(blocks, tmp_path, ledger=led), "FINAL marker not accepted")


def test_non_final_wrong_experiment_empty_and_torn_markers_refused(blocks, tmp_path):
    for doc in ({"final": False}, {"final": True, "experiment": "EXP-024"}):
        _reasons_have(_read(blocks, tmp_path, ledger=_marker(tmp_path, doc)), "FINAL marker not accepted")
    _reasons_have(_read(blocks, tmp_path, ledger=_marker(tmp_path, raw="")), "FINAL marker not accepted")
    _reasons_have(_read(blocks, tmp_path, ledger=_marker(tmp_path, raw='{"final": true}')), "FINAL marker not accepted")
    _reasons_have(_read(blocks, tmp_path, ledger=_marker(tmp_path, raw="not json\n")), "FINAL marker not accepted")


def test_experiment_absent_or_exp012_marker_accepted(blocks, tmp_path):
    assert _read(blocks, tmp_path, ledger=_marker(tmp_path, {"final": True, "experiment": "EXP-012"}))[0] is True


@pytest.mark.parametrize("role", [D0, D1])
def test_before_0200z_refused(blocks, tmp_path, role):
    _reasons_have(_read(blocks, tmp_path, role, now=datetime(2026, 10, 16, 1, 59, 59, tzinfo=timezone.utc)),
                  "release is not in effect")
    assert _read(blocks, tmp_path, role, now=datetime(2026, 10, 16, 2, 0, tzinfo=timezone.utc))[0] is True


# --- hours, walk 2, sealed paths --------------------------------------------


def test_walk2_hour_refused(blocks, tmp_path):
    _reasons_have(_read(blocks, tmp_path, start="2026-10-15T00", end="2026-10-16T02"), "walk 2")
    _reasons_have(_read(blocks, tmp_path, start="2026-10-16T01", end="2026-10-16T02"), "walk 2")


def test_hours_before_the_released_window_refused(blocks, tmp_path):
    _reasons_have(_read(blocks, tmp_path, start="2026-10-02T14", end="2026-10-03T00"), "first released hour")
    _reasons_have(_read(blocks, tmp_path, dataset="forward-1002ev", start="2026-10-08T23", end="2026-10-09T05"),
                  "first released hour")


def test_non_forward_hours_refused_by_ledger_owner(blocks, tmp_path):
    # a ledger where the covering row is not the EXP-012 Forward walk row
    other = [b for b in blocks if b.name != "Forward walk"]
    _reasons_have(_read(other, tmp_path), "not the Forward walk row")


@pytest.mark.parametrize("name", ["fresh-0802", "fresh-0808", "fresh-0828", "forward-1016", "truth-1002", "exp011-0909"])
def test_sealed_block_paths_refused(blocks, tmp_path, name):
    p = tmp_path / "blocks" / name / "trades-2026-10-09T00.jsonl.zst"
    _reasons_have(_read(blocks, tmp_path, paths=[p]), "sealed or unreleased")


def test_path_traversal_and_symlink_out_refused(blocks, tmp_path):
    root = tmp_path / "blocks"
    (root / "forward-1002").mkdir(parents=True)
    (root / "fresh-0802").mkdir()
    (root / "forward-1002" / "link").symlink_to(root / "fresh-0802")
    for p in (root / "forward-1002" / ".." / "fresh-0802" / "x", root / "forward-1002" / "link" / "x"):
        _reasons_have(_read(blocks, tmp_path, paths=[p]), "sealed or unreleased")
    assert _read(blocks, tmp_path, paths=[root / "forward-1002" / "trades-2026-10-09T00.jsonl.zst",
                                           root / "forward-1002ev" / "x"])[0] is True


# --- D0 column masks ---------------------------------------------------------


@pytest.mark.parametrize("col", ["side", "sol_lamports", "token_raw", "quote_reserve", "base_reserve",
                                 "virtual_quote_reserves", "price_sol", "market_cap_sol", "sol", "token",
                                 "buy_sol", "net_flow", "signed_volume", "pnl_E1", "trigger_time", "buyback_fee",
                                 "creator_fee_unclaimed", "pool_quote_amount", "unknown_new_column"])
def test_d0_forbidden_and_unknown_columns_refused(blocks, tmp_path, col):
    ok, reasons = _read(blocks, tmp_path, D0, columns=("mint", col))
    assert ok is False and any(repr(col) in r for r in reasons), reasons


def test_d0_allows_structure_columns(blocks, tmp_path):
    cols = sorted(oc.D0_ALLOWED_COLUMNS)
    assert _read(blocks, tmp_path, D0, columns=cols) == (True, [])


# --- D1 column masks and rows ------------------------------------------------


@pytest.mark.parametrize("col", ["pool", "pool_quote_amount", "virtual_quote_reserves", "v0", "seed_quote_reserve",
                                 "synthetic_class", "post_complete_buy", "boost_slice_t", "migrated", "t_migrate",
                                 "s0_price", "buyback_fee", "trigger_flag", "creator_fee_unclaimed", "mystery"])
def test_d1_post_migrate_and_unknown_columns_refused(blocks, tmp_path, col):
    ok, reasons = _read(blocks, tmp_path, D1, columns=("mint", col))
    assert ok is False and any(repr(col) in r for r in reasons), reasons


def test_d1_allows_prefix_columns_and_complete_label(blocks, tmp_path):
    cols = sorted(oc.D1_ALLOWED_COLUMNS)
    assert _read(blocks, tmp_path, D1, columns=cols) == (True, [])


def _row(mint="M", slot=100, ei=0, venue="pump_bonding"):
    return {"mint": mint, "venue": venue, "slot": slot, "event_index": ei}


def test_d1_rows_before_migrate_allowed_and_never_migrated_allowed():
    assert oc.check_rows(D1, [_row(slot=99), _row(slot=100, ei=0)], {"M": (100, 1)}) == []
    assert oc.check_rows(D1, [_row(slot=10**9)], {"M": None}) == []


def test_d1_post_migrate_row_refused():
    for r in (_row(slot=100, ei=1), _row(slot=100, ei=2), _row(slot=101)):
        reasons = oc.check_rows(D1, [r], {"M": (100, 1)})
        assert reasons and "at or after the migrate tx" in reasons[0]


def test_d1_pool_trade_unknown_mint_and_bad_position_refused():
    assert "pool trade" in oc.check_rows(D1, [_row(venue="pumpswap")], {"M": None})[0]
    assert "fail closed" in oc.check_rows(D1, [_row(mint="X")], {"M": None})[0]
    assert "usable" in oc.check_rows(D1, [{"mint": "M", "venue": "pump_bonding"}], {"M": None})[0]


def test_d1_rows_after_complete_refused_when_complete_given():
    mig = {"M": (200, 0)}
    assert oc.check_rows(D1, [_row(slot=150)], mig, {"M": (150, 0)}) == []
    assert "after the complete" in oc.check_rows(D1, [_row(slot=151)], mig, {"M": (150, 0)})[0]
    assert "fail closed" in oc.check_rows(D1, [_row(slot=1)], mig, {})[0]


def test_d0_loads_no_trade_rows_and_other_roles_refused():
    assert oc.check_rows(D0, [_row()], {"M": None})
    assert oc.check_rows("exploration", [_row()], {"M": None})


# --- the #545 fm_* columns, both roles ---------------------------------------


@pytest.mark.parametrize("role", [D0, D1])
@pytest.mark.parametrize("col", ["fm_buy_sol", "fm_sell_sol", "fm_max_buy_sol", "fm_max_sell_sol", "fm_flow_sol", "fm_anything"])
def test_fm_columns_refused(blocks, tmp_path, role, col):
    ok, reasons = _read(blocks, tmp_path, role, columns=("mint", col))
    assert ok is False and any(f"{col!r} is forbidden" in r for r in reasons), reasons
