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


@pytest.fixture(autouse=True)
def _clean_latch():
    oc._reset_latch_for_tests()
    yield
    oc._reset_latch_for_tests()


def _grant(role):
    """Stand in for a granted check_read (the latch is what mask_row/guarded_rows consult)."""
    oc._reset_latch_for_tests()
    oc._LATCH.append(role)


@pytest.fixture(scope="module")
def blocks():
    return mc.parse_ledger((REPO_ROOT / "docs" / "HOLDOUT_LEDGER.md").read_text(encoding="utf-8"))


def _marker(tmp_path: Path, doc: dict | None = None, raw: str | None = None) -> Path:
    p = tmp_path / "FINAL_READS.jsonl"
    p.write_text(raw if raw is not None else json.dumps(doc if doc is not None else {"final": True}) + "\n")
    return p


def _read(blocks, tmp_path, role=D0, *, dataset="forward-1002", start="2026-10-09T00", end="2026-10-16T01",
          columns=("mint", "slot"), ledger=None, now=NOW, paths=None):
    if paths is None:
        paths = [tmp_path / "blocks" / dataset / "trades" / "trades-2026-10-10T00.jsonl.zst"]
    return oc._check_read_impl(blocks, role, dataset, start, end, columns, paths=paths,
                               migrate_pos={} if role == D1 else None,
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


def _f(tmp_path, dataset, kind, hour, name=None):
    return tmp_path / "blocks" / dataset / kind / (name or f"{kind}-{hour}.jsonl.zst")


@pytest.mark.parametrize("name", ["fresh-0802", "fresh-0808", "fresh-0828", "forward-1016", "truth-1002", "exp011-0909"])
def test_sealed_block_paths_refused(blocks, tmp_path, name):
    p = tmp_path / "blocks" / name / "trades" / "trades-2026-10-09T00.jsonl.zst"
    _reasons_have(_read(blocks, tmp_path, paths=[p]), "sealed or unreleased")


@pytest.mark.parametrize("kind", ["trades", "creates"])
@pytest.mark.parametrize("hour", ["2026-10-16T01", "2026-10-16T02"])
def test_walk2_files_inside_forward_1002_refused(blocks, tmp_path, kind, hour):
    # the requested window is the full released one; the walk-2 files still must not pass
    _reasons_have(_read(blocks, tmp_path, paths=[_f(tmp_path, "forward-1002", kind, hour)]), "outside the validated window")


def test_file_outside_requested_window_refused(blocks, tmp_path):
    p = _f(tmp_path, "forward-1002", "trades", "2026-10-12T00")
    _reasons_have(_read(blocks, tmp_path, start="2026-10-13T00", end="2026-10-14T00", paths=[p]), "outside the validated window")
    assert _read(blocks, tmp_path, start="2026-10-12T00", end="2026-10-13T00", paths=[p])[0] is True


@pytest.mark.parametrize("rel", ["trades/notes.txt", "trades/trades-2026-10-10T00.csv", "stats-2026-10-10T00.json",
                                 "verify.jsonl", "checkpoint.json", "pools/pool-mints.jsonl.zst",
                                 "creates/trades-2026-10-10T00.jsonl.zst", "trades/sub/trades-2026-10-10T00.jsonl"])
def test_unknown_filenames_and_dirs_refused(blocks, tmp_path, rel):
    p = tmp_path / "blocks" / "forward-1002" / rel
    ok, reasons = _read(blocks, tmp_path, paths=[p])
    assert ok is False and any(("unknown name" in r) or ("may not be opened" in r) for r in reasons), reasons


def test_per_role_subdirs(blocks, tmp_path):
    mig = _f(tmp_path, "forward-1002", "migrations", "2026-10-10T00")
    assert _read(blocks, tmp_path, D0, paths=[mig])[0] is True
    _reasons_have(_read(blocks, tmp_path, D1, paths=[mig]), "may not be opened by role=oct-pregrad-d1")
    for kind in ("trades", "creates"):
        assert _read(blocks, tmp_path, D1, columns=("mint",), paths=[_f(tmp_path, "forward-1002", kind, "2026-10-10T00")])[0] is True


def test_path_traversal_and_symlink_out_refused(blocks, tmp_path):
    root = tmp_path / "blocks"
    (root / "forward-1002" / "trades").mkdir(parents=True)
    (root / "fresh-0802" / "trades").mkdir(parents=True)
    (root / "forward-1002" / "trades" / "link").symlink_to(root / "fresh-0802" / "trades")
    for p in (root / "forward-1002" / ".." / "fresh-0802" / "trades" / "trades-2026-10-10T00.jsonl.zst",
              root / "forward-1002" / "trades" / "link" / "trades-2026-10-10T00.jsonl.zst"):
        assert _read(blocks, tmp_path, paths=[p])[0] is False
    ok = [_f(tmp_path, "forward-1002", "trades", "2026-10-10T00"), _f(tmp_path, "forward-1002ev", "creates", "2026-10-10T00")]
    assert _read(blocks, tmp_path, paths=ok[:1])[0] is True


def test_wrong_dataset_dir_refused(blocks, tmp_path):
    p = _f(tmp_path, "forward-1002ev", "trades", "2026-10-10T00")
    _reasons_have(_read(blocks, tmp_path, dataset="forward-1002", paths=[p]), "not under forward-1002")


# --- B2: the public API has no caller-controlled gates ---------------------


def test_public_api_takes_no_clock_ledger_or_root(blocks, tmp_path):
    import inspect

    params = set(inspect.signature(oc.check_read).parameters)
    assert not params & {"now", "final_ledger", "blocks_root"}
    forged = _marker(tmp_path, {"final": True})
    for kw in ({"now": datetime(2030, 1, 1, tzinfo=timezone.utc)}, {"final_ledger": forged}, {"blocks_root": tmp_path}):
        with pytest.raises(TypeError):
            oc.check_read(blocks, D0, "forward-1002", "2026-10-09T00", "2026-10-10T00", ("mint",), paths=["x"], **kw)


def test_public_api_uses_real_clock_and_fixed_ledger(blocks, tmp_path, monkeypatch):
    monkeypatch.setattr(oc, "DEFAULT_FINAL_LEDGER", tmp_path / "absent.jsonl")
    monkeypatch.setattr(oc, "_now", lambda: datetime(2026, 10, 16, 1, 0, tzinfo=timezone.utc))
    ok, reasons = oc.check_read(blocks, D0, "forward-1002", "2026-10-09T00", "2026-10-10T00", ("mint",), paths=["x"])
    assert ok is False and any("FINAL marker" in r for r in reasons) and any("release is not in effect" in r for r in reasons)
    monkeypatch.setattr(oc, "DEFAULT_FINAL_LEDGER", _marker(tmp_path))
    monkeypatch.setattr(oc, "_now", lambda: NOW)
    monkeypatch.setattr(oc, "BLOCKS_ROOT", tmp_path / "blocks")
    ok = [tmp_path / "blocks" / "forward-1002" / "trades" / "trades-2026-10-09T05.jsonl.zst"]
    oc._reset_latch_for_tests()
    assert oc.check_read(blocks, D0, "forward-1002", "2026-10-09T00", "2026-10-10T00", ("mint",), paths=ok) == (True, [])
    oc._reset_latch_for_tests()
    assert set(oc.resolved_gates()) == {"now", "final_ledger", "blocks_root"}


# --- N1: D1 needs the row-level guard -----------------------------------------


def test_d1_public_read_requires_migrate_map(blocks, tmp_path, monkeypatch):
    monkeypatch.setattr(oc, "DEFAULT_FINAL_LEDGER", _marker(tmp_path))
    monkeypatch.setattr(oc, "_now", lambda: NOW)
    monkeypatch.setattr(oc, "BLOCKS_ROOT", tmp_path / "blocks")
    oc._reset_latch_for_tests()
    args = (blocks, D1, "forward-1002", "2026-10-09T00", "2026-10-10T00", ("mint", "side"))
    kw = {"paths": [tmp_path / "blocks" / "forward-1002" / "trades" / "trades-2026-10-09T05.jsonl.zst"]}
    _reasons_have(oc.check_read(*args, **kw), "needs a migrate_pos map")
    assert oc.check_read(*args, migrate_pos={"M": None}, **kw) == (True, [])
    oc._reset_latch_for_tests()


def test_guarded_rows_yields_early_rows_and_raises_on_post_migrate():
    _grant(D1)
    it = oc.guarded_rows(D1, [_row(slot=99), _row(slot=100, ei=1)], {"M": (100, 1)}, {"M": None})
    assert next(it)["slot"] == 99
    assert list(it) == []  # the post-migrate row is skipped, not yielded


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


def _row(mint="M", slot=100, ei=0, venue="pump_bonding", type="trade"):
    return {"type": type, "mint": mint, "venue": venue, "slot": slot, "event_index": ei}


NC = {"M": None}  # complete_pos: never completed


def test_d1_rows_before_migrate_allowed_and_never_migrated_allowed():
    assert oc.check_rows(D1, [_row(slot=99), _row(slot=100, ei=0)], {"M": (100, 1)}, NC) == []
    assert oc.check_rows(D1, [_row(slot=10**9)], NC, NC) == []


def test_d1_post_migrate_row_refused():
    for r in (_row(slot=100, ei=1), _row(slot=100, ei=2), _row(slot=101)):
        reasons = oc.check_rows(D1, [r], {"M": (100, 1)}, NC)
        assert reasons and "at or after the migrate tx" in reasons[0]


def test_d1_pool_trade_unknown_mint_and_bad_position_refused():
    assert "pool trade" in oc.check_rows(D1, [_row(venue="pumpswap")], NC, NC)[0]
    assert "fail closed" in oc.check_rows(D1, [_row(mint="X")], NC, NC)[0]
    bad = {"type": "trade", "mint": "M", "venue": "pump_bonding"}
    assert "usable" in oc.check_rows(D1, [bad], NC, NC)[0]


@pytest.mark.parametrize("typ", ["post_complete_buy", "sweep_curve_fee", "sweep_pool_fee", "boost_buy_and_burn", "init_boost"])
def test_d1_non_trade_rows_refused_even_on_the_bonding_venue(typ):
    reasons = oc.check_rows(D1, [_row(slot=1, type=typ)], NC, NC)
    assert reasons and "is not a trade" in reasons[0]


def test_d1_complete_pos_required_and_cutoff_enforced():
    mig = {"M": (200, 0)}
    with pytest.raises(TypeError):
        oc.check_rows(D1, [_row(slot=1)], mig)  # complete_pos is a required argument
    assert oc.check_rows(D1, [_row(slot=150)], mig, {"M": (150, 0)}) == []
    assert "after the complete" in oc.check_rows(D1, [_row(slot=151)], mig, {"M": (150, 0)})[0]
    assert "no complete position" in oc.check_rows(D1, [_row(slot=1)], mig, {})[0]
    _grant(D1)
    with pytest.raises(oc.OctoberRefused):
        list(oc.guarded_rows(D1, [_row(slot=1)], mig))  # guarded_rows without complete_pos


def test_other_roles_refused_by_check_rows():
    assert oc.check_rows("exploration", [_row()], NC, NC)


# --- item 5/6: mask_row by row type ---------------------------------------------

FORBIDDEN_TRADE_KEYS = ("side", "sol_lamports", "token_raw", "sol", "token", "price_sol", "market_cap_sol",
                        "quote_reserve", "base_reserve", "pool_quote_amount", "lp_fee", "protocol_fee", "creator_fee",
                        "zero_sol", "virtual_quote_reserves", "ix_name", "creator_fee_unclaimed", "buyback_fee",
                        "fee_recipient_zero", "fm_flow_sol")


def _full_trade():
    r = {k: 1 for k in FORBIDDEN_TRADE_KEYS}
    r.update(type="trade", venue="pumpswap", mint="M", trader="T", pool="P", slot=5, signature="S", event_index=0,
             event_ts=1, quote_mint="Q", quote_is_wsol=True, v=1, feed="f", t_recv="x", t_recv_ms=1, commitment="c")
    return r


def test_d0_post_s0_trade_row_keeps_identities_only():
    _grant(D0)
    m = oc.mask_row(D0, _full_trade(), {"M": "S"})  # even in the migrate tx a trade row keeps no amount
    assert not set(m) & set(FORBIDDEN_TRADE_KEYS)
    assert {"mint", "trader", "pool", "slot", "signature", "event_ts"} <= set(m)
    assert "trader" in oc.D0_RAW_COLUMNS and "zero_sol" not in oc.D0_ALLOWED_COLUMNS


def test_d1_mask_drops_pool_fees_and_event_v_keys_keeps_trade_fields():
    _grant(D1)
    m = oc.mask_row(D1, _full_trade())
    for k in ("pool", "pool_quote_amount", "lp_fee", "protocol_fee", "creator_fee", "virtual_quote_reserves",
              "ix_name", "creator_fee_unclaimed", "buyback_fee", "fm_flow_sol"):
        assert k not in m
    assert {"side", "sol_lamports", "quote_reserve", "base_reserve", "price_sol"} <= set(m)


def test_init_boost_reserve_and_v_only_on_the_migrate_tx():
    row = {"type": "init_boost", "mint": "M", "signature": "S", "slot": 1, "event_index": 0, "event_ts": 1,
           "virtual_quote_reserves": 17, "quote_reserve": 9, "base_reserve": 8}
    _grant(D0)
    assert oc.mask_row(D0, row, {"M": "S"})["virtual_quote_reserves"] == 17
    second = dict(row, signature="S2")
    assert "virtual_quote_reserves" not in oc.mask_row(D0, second, {"M": "S"})
    assert "quote_reserve" not in oc.mask_row(D0, row, None)  # no migrate map: nothing kept
    _grant(D1)
    assert "virtual_quote_reserves" not in oc.mask_row(D1, row, {"M": "S"})  # D1 never keeps s0 fields


@pytest.mark.parametrize("typ", ["boost_buy_and_burn", "sweep_pool_fee", "sweep_curve_fee", "unknown_new_type"])
def test_fee_and_boost_rows_keep_no_amounts(typ):
    row = {"type": typ, "mint": "M", "signature": "S", "slot": 1, "event_ts": 1, "amount": 5, "fee": 2,
           "quote_in": 3, "virtual_quote_reserves": 4}
    _grant(D0)
    m = oc.mask_row(D0, row, {"M": "S"})
    assert set(m) == {"type", "mint", "signature", "slot", "event_ts"}


def test_d1_guarded_rows_skips_out_of_scope_rows_and_counts_them():
    _grant(D1)
    mig, comp = {"M": (200, 0)}, {"M": (150, 0)}
    rows = [_row(slot=10), _row(slot=11, type="post_complete_buy"), _row(slot=12, venue="pumpswap"),
            _row(slot=160), _row(slot=250), _row(slot=13, type="sweep_curve_fee")]
    counts: dict = {}
    out = list(oc.guarded_rows(D1, rows, mig, comp, skipped=counts))
    assert [r["slot"] for r in out] == [10]
    assert counts == {"not_trade": 2, "pool_venue": 1, "after_complete": 1, "after_migrate": 1}


def test_d1_guarded_rows_still_raises_on_missing_map_entry_or_bad_position():
    _grant(D1)
    with pytest.raises(oc.OctoberRefused):
        list(oc.guarded_rows(D1, [_row(mint="X")], {"M": None}, {"M": None}))
    with pytest.raises(oc.OctoberRefused):
        list(oc.guarded_rows(D1, [_row(), _row(mint="M")], {"M": None}, {"X": None}))
    bad = {"type": "trade", "mint": "M", "venue": "pump_bonding"}
    with pytest.raises(oc.OctoberRefused):
        list(oc.guarded_rows(D1, [bad], {"M": None}, {"M": None}))


def test_mask_and_guarded_rows_need_a_grant_for_that_role():
    with pytest.raises(oc.OctoberRefused):
        oc.mask_row(D0, _full_trade())
    with pytest.raises(oc.OctoberRefused):
        oc.guarded_rows(D0, [_full_trade()])  # raises at call time, not on first next()
    _grant(D1)
    with pytest.raises(oc.OctoberRefused):
        oc.mask_row(D0, _full_trade())
    with pytest.raises(oc.OctoberRefused):
        oc.guarded_rows(D0, [_full_trade()])
    assert oc.mask_row(D1, _full_trade())


def test_migrate_sigs_from_rows():
    rows = [{"type": "migration", "mint": "M", "signature": "S"}, {"type": "pool_create", "mint": "P", "signature": "T"},
            {"type": "create_pool", "mint": "Q", "signature": "U"}, {"type": "migration", "mint": "N"},
            {"type": "migration", "signature": "Z"}]
    assert oc.migrate_sigs_from(rows) == {"M": "S", "P": "T", "Q": "U"}


def test_migrate_sigs_ignores_non_migration_rows():
    rows = [{"type": "trade", "mint": "M", "signature": "X"}, {"type": "init_boost", "mint": "M", "signature": "Y"},
            {"type": "complete", "mint": "M", "signature": "C"},
            {"mint": "M", "signature": "W"}, {"type": "migration", "mint": "M", "signature": "S"}]
    assert oc.migrate_sigs_from(rows) == {"M": "S"}


def test_migrate_sigs_conflicting_signatures_refused():
    rows = [{"type": "migration", "mint": "M", "signature": "S"}, {"type": "pool_create", "mint": "M", "signature": "S2"}]
    with pytest.raises(oc.OctoberRefused):
        oc.migrate_sigs_from(rows)


def test_migrate_sigs_identical_duplicates_fine():
    rows = [{"type": "migration", "mint": "M", "signature": "S"}, {"type": "create_pool", "mint": "M", "signature": "S"}]
    assert oc.migrate_sigs_from(rows) == {"M": "S"}


def test_guarded_rows_d0_masks_every_row():
    _grant(D0)
    out = list(oc.guarded_rows(D0, [_full_trade()]))
    assert not set(out[0]) & set(FORBIDDEN_TRADE_KEYS)


# --- items 2, 3, 7 -------------------------------------------------------------


def test_empty_paths_and_columns_refused(blocks, tmp_path):
    _reasons_have(_read(blocks, tmp_path, paths=[]), "no paths named")
    _reasons_have(_read(blocks, tmp_path, columns=()), "no columns named")


def test_migrate_positions_keeps_three_fields_and_is_gated(blocks, tmp_path, monkeypatch):
    monkeypatch.setattr(oc, "DEFAULT_FINAL_LEDGER", _marker(tmp_path))
    monkeypatch.setattr(oc, "BLOCKS_ROOT", tmp_path / "blocks")
    monkeypatch.setattr(oc, "_now", lambda: NOW)
    f = _f(tmp_path, "forward-1002", "migrations", "2026-10-10T00", "migrations-2026-10-10T00.jsonl")
    f.parent.mkdir(parents=True)
    f.write_text(json.dumps({"type": "migration", "mint": "M", "slot": 7, "event_index": 2, "virtual_quote_reserves": 99}) + "\n")
    args = (blocks, "forward-1002", "2026-10-09T00", "2026-10-16T01")
    assert oc.migrate_positions(*args, [f]) == {"M": (7, 2)}
    walk2 = _f(tmp_path, "forward-1002", "migrations", "2026-10-16T01", "migrations-2026-10-16T01.jsonl")
    with pytest.raises(oc.OctoberRefused):
        oc.migrate_positions(*args, [walk2])
    with pytest.raises(oc.OctoberRefused):
        oc.migrate_positions(*args, [_f(tmp_path, "forward-1002", "trades", "2026-10-10T00")])
    monkeypatch.setattr(oc, "_now", lambda: datetime(2026, 10, 16, 1, 0, tzinfo=timezone.utc))
    with pytest.raises(oc.OctoberRefused):
        oc.migrate_positions(*args, [f])


def _mig_file(tmp_path, monkeypatch, rows):
    monkeypatch.setattr(oc, "DEFAULT_FINAL_LEDGER", _marker(tmp_path))
    monkeypatch.setattr(oc, "BLOCKS_ROOT", tmp_path / "blocks")
    monkeypatch.setattr(oc, "_now", lambda: NOW)
    f = _f(tmp_path, "forward-1002", "migrations", "2026-10-10T00", "migrations-2026-10-10T00.jsonl")
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return f


@pytest.mark.parametrize("comp_ei", [0, 1, 5])  # below, equal to and above the completing trade's event_index (1)
@pytest.mark.parametrize("order", ["comp_last", "comp_first"])
def test_migrate_positions_ignores_complete_rows(blocks, tmp_path, monkeypatch, comp_ei, order):
    comp = {"type": "complete", "mint": "M", "slot": 10, "event_index": comp_ei}
    mig = {"type": "migration", "mint": "M", "slot": 12, "event_index": 3}
    f = _mig_file(tmp_path, monkeypatch, [comp, mig] if order == "comp_first" else [mig, comp])
    pos = oc.migrate_positions(blocks, "forward-1002", "2026-10-09T00", "2026-10-16T01", [f])
    assert pos == {"M": (12, 3)}
    trade = _row(slot=10, ei=1)  # the completing trade stays before the migrate cutoff whatever the complete row says
    assert oc.check_rows(D1, [trade], pos, {"M": (10, 1)}) == []


def test_migrate_positions_complete_only_means_never_migrated(blocks, tmp_path, monkeypatch):
    f = _mig_file(tmp_path, monkeypatch, [{"type": "complete", "mint": "M", "slot": 10, "event_index": 0}])
    assert oc.migrate_positions(blocks, "forward-1002", "2026-10-09T00", "2026-10-16T01", [f]) == {}


def test_complete_positions_reads_complete_rows_only_with_event_ts(blocks, tmp_path, monkeypatch):
    f = _mig_file(tmp_path, monkeypatch, [
        {"type": "complete", "mint": "M", "slot": 10, "event_index": 1, "event_ts": 55, "virtual_quote_reserves": 9},
        {"type": "migration", "mint": "M", "slot": 10, "event_index": 1, "event_ts": 55},
        {"type": "migration", "mint": "N", "slot": 12, "event_index": 3}])
    ts: dict[str, int] = {}
    args = (blocks, "forward-1002", "2026-10-09T00", "2026-10-16T01", [f])
    assert oc.complete_positions(*args, ts) == {"M": (10, 1)} and ts == {"M": 55}
    assert oc.migrate_positions(*args) == {"M": (10, 1), "N": (12, 3)}


@pytest.mark.parametrize("bad", [{}, {"event_ts": None}, {"event_ts": "5"}, {"event_ts": True}])
def test_complete_positions_row_without_integer_event_ts_refused(blocks, tmp_path, monkeypatch, bad):
    f = _mig_file(tmp_path, monkeypatch, [{"type": "complete", "mint": "M", "slot": 10, "event_index": 1, **bad}])
    with pytest.raises(oc.OctoberRefused):
        oc.complete_positions(blocks, "forward-1002", "2026-10-09T00", "2026-10-16T01", [f], {})


def test_migrate_positions_conflicting_migration_rows_refused(blocks, tmp_path, monkeypatch):
    f = _mig_file(tmp_path, monkeypatch, [{"type": "migration", "mint": "M", "slot": 12, "event_index": 3},
                                          {"type": "pool_create", "mint": "M", "slot": 13, "event_index": 0}])
    with pytest.raises(oc.OctoberRefused):
        oc.migrate_positions(blocks, "forward-1002", "2026-10-09T00", "2026-10-16T01", [f])
    f = _mig_file(tmp_path, monkeypatch, [{"type": "migration", "mint": "M", "slot": 12, "event_index": 3},
                                          {"type": "pool_create", "mint": "M", "slot": 12, "event_index": 1}])
    assert oc.migrate_positions(blocks, "forward-1002", "2026-10-09T00", "2026-10-16T01", [f]) == {"M": (12, 1)}


def test_latch_one_october_role_per_process(blocks, tmp_path, monkeypatch):
    monkeypatch.setattr(oc, "DEFAULT_FINAL_LEDGER", _marker(tmp_path))
    monkeypatch.setattr(oc, "BLOCKS_ROOT", tmp_path / "blocks")
    monkeypatch.setattr(oc, "_now", lambda: NOW)
    oc._reset_latch_for_tests()
    kw = {"paths": [_f(tmp_path, "forward-1002", "trades", "2026-10-10T00")]}
    a = (blocks, "forward-1002", "2026-10-09T00", "2026-10-16T01")
    assert oc.check_read(blocks, D0, *a[1:], ("mint",), **kw) == (True, [])
    ok, reasons = oc.check_read(blocks, D1, *a[1:], ("mint",), migrate_pos={}, **kw)
    assert ok is False and any("never in one process" in r for r in reasons)
    assert oc.check_read(blocks, D0, *a[1:], ("mint",), **kw)[0] is True  # same role again is fine
    oc._reset_latch_for_tests()
    assert oc.check_read(blocks, D1, *a[1:], ("mint",), migrate_pos={}, **kw)[0] is True
    assert oc.check_read(blocks, D0, *a[1:], ("mint",), **kw)[0] is False
    oc._reset_latch_for_tests()


# --- the #545 fm_* columns, both roles ---------------------------------------


@pytest.mark.parametrize("role", [D0, D1])
@pytest.mark.parametrize("col", ["fm_buy_sol", "fm_sell_sol", "fm_max_buy_sol", "fm_max_sell_sol", "fm_flow_sol", "fm_anything"])
def test_fm_columns_refused(blocks, tmp_path, role, col):
    ok, reasons = _read(blocks, tmp_path, role, columns=("mint", col))
    assert ok is False and any(f"{col!r} is forbidden" in r for r in reasons), reasons


# --- events/ (forward-1002ev's fourth stream, --event-v): D0 may open it through mask_row; D1 never ----------------


def test_d0_may_open_events_stream_d1_never(blocks, tmp_path):
    ev = _f(tmp_path, "forward-1002ev", "events", "2026-10-10T00")
    assert _read(blocks, tmp_path, D0, dataset="forward-1002ev", paths=[ev]) == (True, [])
    _reasons_have(_read(blocks, tmp_path, D1, dataset="forward-1002ev", columns=("mint",), paths=[ev]),
                  "may not be opened by role=oct-pregrad-d1")
    assert "events" not in oc.ROLE_KINDS[D1]


@pytest.mark.parametrize("hour", ["2026-10-08T23", "2026-10-16T01", "2026-10-16T02"])
def test_events_stream_keeps_the_released_window(blocks, tmp_path, hour):
    ev = _f(tmp_path, "forward-1002ev", "events", hour)
    _reasons_have(_read(blocks, tmp_path, D0, dataset="forward-1002ev", paths=[ev]), "outside the validated window")


@pytest.mark.parametrize("rel", ["events/notes.txt", "events/trades-2026-10-10T00.jsonl.zst", "events/sub/events-2026-10-10T00.jsonl"])
def test_events_dir_unknown_names_refused(blocks, tmp_path, rel):
    p = tmp_path / "blocks" / "forward-1002ev" / rel
    ok, reasons = _read(blocks, tmp_path, D0, dataset="forward-1002ev", paths=[p])
    assert ok is False and any("unknown name" in r for r in reasons), reasons


def test_post_complete_buy_row_keeps_identities_only_off_the_migrate_tx():
    row = {"type": "post_complete_buy", "mint": "M", "signature": "C", "slot": 1, "event_index": 2, "event_ts": 1,
           "trader": "T", "quote_in": 3, "base_out": 4, "pool_quote_reserves_before": 5, "pool_base_reserves_after": 6,
           "virtual_quote_reserves": 7, "fee": 8}
    _grant(D0)
    m = oc.mask_row(D0, row, {"M": "MIG"})  # the curve-completing tx is not the migrate tx: identities only
    assert set(m) == {"type", "mint", "signature", "slot", "event_index", "event_ts"}
    assert "trader" not in m  # a non-trade row keeps no trader; the completer comes from the completing trade row
    assert set(oc.mask_row(D0, row, None)) == set(m)
