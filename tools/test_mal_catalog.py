"""Tests for tools.mal_catalog -- the data catalog and read guard.

Runs against the REAL docs/HOLDOUT_LEDGER.md (the source of truth), plus a
few synthetic ledger fragments for the malformed/overlap error paths.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.mal_catalog import Block, allowed, build_catalog, check_read, parse_ledger

REPO_ROOT = Path(__file__).resolve().parent.parent
LEDGER_PATH = REPO_ROOT / "docs" / "HOLDOUT_LEDGER.md"


@pytest.fixture(scope="module")
def ledger_text() -> str:
    return LEDGER_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def blocks(ledger_text: str) -> list[Block]:
    return parse_ledger(ledger_text)


def _check(blocks: list[Block], role: str, host: str, start: str, end: str, exp_id: str | None = None):
    return check_read(blocks, role, host, start, end, exp_id=exp_id)


# --- Real-ledger parse sanity ---------------------------------------------


def test_parse_real_ledger_has_expected_rows(blocks: list[Block]) -> None:
    by_name = {b.name: b for b in blocks}
    assert len(blocks) == 14
    assert by_name["Fast EXP-009 block"].owner == "EXP-009"
    assert by_name["Fast EXP-009 exclusion"].explicit_hours == ("2026-09-18T23", "2026-09-19T00")
    assert by_name["Forward paper, kill review"].host == "oracle-forward"
    assert by_name["Forward paper, kill review"].owner == "kill-review"
    fast_exp011 = [b for b in blocks if b.owner == "EXP-011"][0]
    assert fast_exp011.host == "fast"
    assert (fast_exp011.start_hour, fast_exp011.end_hour_exclusive) == ("2026-09-09T12", "2026-09-15T12")
    unassigned = by_name["Future fast backfill"]
    assert unassigned.owner == "unassigned"
    assert unassigned.start_hour is None
    assert unassigned.end_hour_exclusive == "2026-08-08T12"
    assert by_name["Fresh confirmation block"].owner == "exploration-pool"  # spent by EXP-012 read, moved by ledger edit
    assert by_name["Fresh confirmation block"].host == "research"
    assert by_name["Backup confirmation block"].owner == "EXP-015"
    assert by_name["Backup confirmation block"].host == "research"
    second = by_name["Second backup confirmation block"]
    assert second.owner == "reserved"
    assert second.host == "research"
    assert (second.start_hour, second.end_hour_exclusive) == ("2026-08-08T12", "2026-08-14T12")
    assert by_name["Exploration expansion"].owner == "exploration-pool"
    assert by_name["Exploration expansion"].host == "research"
    fwd = by_name["Forward walk"]
    assert fwd.host == "research"
    assert fwd.owner == "EXP-012"
    assert fwd.owner != "exploration-pool"
    assert (fwd.start_hour, fwd.end_hour_exclusive) == ("2026-10-02T10", "2026-10-16T01")
    fwd2 = by_name["Forward walk 2"]
    assert fwd2.owner == "reserved"
    assert (fwd2.start_hour, fwd2.end_hour_exclusive) == ("2026-10-16T01", "2026-11-16T01")
    assert fwd2.start_hour == fwd.end_hour_exclusive


# --- check_read against the real ledger -----------------------------------


def test_exploration_fast_precut_allow(blocks: list[Block]) -> None:
    ok, reasons = _check(blocks, "exploration", "fast", "2026-09-20T00", "2026-09-21T00")
    assert ok, reasons


def test_exploration_fast_exclusion_plus_precut_allow(blocks: list[Block]) -> None:
    # 2026-09-18T23 and T19-09-19T00 are the disclosed exploration-pool
    # exclusion hours inside the EXP-009 block; 2026-09-19T01 is the first
    # hour of the "Fast pre-cut" pool. All three are exploration-pool.
    ok, reasons = _check(blocks, "exploration", "fast", "2026-09-18T23", "2026-09-19T02")
    assert ok, reasons


def test_exploration_fast_exp009_deny(blocks: list[Block]) -> None:
    ok, reasons = _check(blocks, "exploration", "fast", "2026-09-17T00", "2026-09-17T01")
    assert not ok
    assert any("EXP-009" in r for r in reasons)


def test_exploration_fast_exp011_deny_and_confirmation_allow(blocks: list[Block]) -> None:
    ok, reasons = _check(blocks, "exploration", "fast", "2026-09-10T00", "2026-09-10T01")
    assert not ok
    assert any("EXP-011" in r for r in reasons)

    ok, reasons = _check(blocks, "confirmation-oneshot", "fast", "2026-09-10T00", "2026-09-10T01", exp_id="EXP-011")
    assert ok, reasons

    ok, reasons = _check(blocks, "confirmation-oneshot", "fast", "2026-09-10T00", "2026-09-10T01", exp_id="EXP-009")
    assert not ok
    assert any("EXP-011" in r for r in reasons)


def test_exploration_fast_unassigned_deny(blocks: list[Block]) -> None:
    ok, reasons = _check(blocks, "exploration", "fast", "2026-08-01T00", "2026-08-01T01")
    assert not ok
    assert any("unassigned" in r for r in reasons)


def test_exploration_oracle_allow_and_oracle_forward_kill_review_deny(blocks: list[Block]) -> None:
    ok, reasons = _check(blocks, "exploration", "oracle", "2026-09-23T00", "2026-09-23T01")
    assert ok, reasons

    ok, reasons = _check(blocks, "exploration", "oracle-forward", "2026-09-29T00", "2026-09-29T01")
    assert not ok
    assert any("kill-review" in r for r in reasons)


def test_ops_role_denies_every_historical_block(blocks: list[Block]) -> None:
    for host, start, end in [
        ("fast", "2026-09-20T00", "2026-09-21T00"),
        ("oracle", "2026-09-23T00", "2026-09-23T01"),
    ]:
        ok, reasons = _check(blocks, "ops", host, start, end)
        assert not ok, (host, start, end, "ops should never read historical data")
        assert reasons


def test_straddling_allow_and_deny_reports_only_denied_hours(blocks: list[Block]) -> None:
    # 2026-09-18T00..T22 is EXP-009-owned (deny), T23/T19-09-19T00 are the
    # exploration-pool exclusion (allow), and 2026-09-19T01 is Fast
    # pre-cut (allow). Overall DENY, listing only the EXP-009 hours.
    ok, reasons = _check(blocks, "exploration", "fast", "2026-09-18T00", "2026-09-20T00")
    assert not ok
    assert any("EXP-009" in r for r in reasons)
    assert not any("2026-09-18T23" in r for r in reasons)
    assert not any("2026-09-19T00" in r for r in reasons)
    assert not any("2026-09-19T01" in r for r in reasons)


# --- allowed() pure function -----------------------------------------------


def test_allowed_matrix() -> None:
    assert allowed("exploration", "exploration-pool") is True
    assert allowed("exploration", "EXP-009") is False
    assert allowed("exploration", "kill-review") is False
    assert allowed("exploration", "unassigned") is False

    assert allowed("confirmation-oneshot", "EXP-011", exp_id="EXP-011") is True
    assert allowed("confirmation-oneshot", "EXP-011", exp_id="EXP-009") is False
    assert allowed("confirmation-oneshot", "EXP-011", exp_id=None) is False
    assert allowed("confirmation-oneshot", "exploration-pool", exp_id="EXP-011") is False
    # A non-EXP owner tag passed as exp_id must never unlock its block.
    assert allowed("confirmation-oneshot", "kill-review", exp_id="kill-review") is False
    assert allowed("confirmation-oneshot", "exploration-pool", exp_id="exploration-pool") is False
    assert allowed("confirmation-oneshot", "unassigned", exp_id="unassigned") is False
    assert allowed("confirmation-oneshot", "EXP-011", exp_id="EXP-011 ") is False

    assert allowed("ops", "exploration-pool") is False
    assert allowed("ops", "EXP-011") is False
    assert allowed("ops", "kill-review") is False
    assert allowed("ops", "unassigned") is False

    assert allowed("some-unknown-role", "exploration-pool") is False


# --- Malformed rows / bad overlaps raise -----------------------------------

_LEDGER_HEADER = "# fixture\n\n## Table\n\n| Block | Hours | Host | Owner | Status |\n| --- | --- | --- | --- | --- |\n"


def test_malformed_hours_cell_raises() -> None:
    bad = _LEDGER_HEADER + "| Some block | sometime last week | fast | exploration pool | test |\n"
    with pytest.raises(ValueError, match="could not parse Hours cell"):
        parse_ledger(bad)


def test_malformed_owner_cell_raises() -> None:
    bad = _LEDGER_HEADER + "| Some block | 2026-01-01T00 → 2026-01-02T00 | fast | nobody in particular | test |\n"
    with pytest.raises(ValueError, match="unrecognized Owner cell"):
        parse_ledger(bad)


def test_wrong_column_count_raises() -> None:
    bad = _LEDGER_HEADER + "| Some block | 2026-01-01T00 → 2026-01-02T00 | fast | exploration pool |\n"
    with pytest.raises(ValueError, match="does not have 5 cells"):
        parse_ledger(bad)


def test_overlapping_ranged_blocks_raise() -> None:
    bad = (
        _LEDGER_HEADER
        + "| Block A | 2026-01-01T00 → 2026-01-03T00 | fast | exploration pool | test |\n"
        + "| Block B | 2026-01-02T00 → 2026-01-04T00 | fast | EXP-100 | test |\n"
    )
    with pytest.raises(ValueError, match="overlapping blocks"):
        parse_ledger(bad)


def test_non_overlapping_adjacent_blocks_do_not_raise() -> None:
    ok_text = (
        _LEDGER_HEADER
        + "| Block A | 2026-01-01T00 → 2026-01-02T00 | fast | exploration pool | test |\n"
        + "| Block B | 2026-01-02T00 → 2026-01-03T00 | fast | EXP-100 | test |\n"
    )
    got = parse_ledger(ok_text)
    assert len(got) == 2


def test_two_explicit_hour_rows_claiming_same_hour_raise() -> None:
    bad = (
        _LEDGER_HEADER
        + "| Block A | 2026-01-01T00 | fast | exploration pool | test |\n"
        + "| Block B | 2026-01-01T00 | fast | EXP-100 | test |\n"
    )
    with pytest.raises(ValueError, match="claimed by two explicit-hour rows"):
        parse_ledger(bad)


def test_missing_table_heading_raises() -> None:
    with pytest.raises(ValueError, match="Table"):
        parse_ledger("# fixture\n\nno table here\n")


# --- build_catalog ----------------------------------------------------------


def test_build_catalog_validates_and_is_deterministic() -> None:
    doc1 = build_catalog(LEDGER_PATH)
    doc2 = build_catalog(LEDGER_PATH)

    assert doc1["schema_version"] == "catalog.v1"
    assert doc1["ledger_sha256"] == doc2["ledger_sha256"]
    assert len(doc1["ledger_sha256"]) == 64
    assert len(doc1["blocks"]) == 14
    assert doc1["walkers"] == []

    def _stable(d: dict) -> dict:
        return {k: v for k, v in d.items() if k != "generated_utc"}

    assert json.dumps(_stable(doc1), sort_keys=True) == json.dumps(_stable(doc2), sort_keys=True)

    exp011_block = [b for b in doc1["blocks"] if b["owner"] == "EXP-011"][0]
    assert exp011_block["access"] == {"exploration": False, "confirmation_oneshot_exp": "EXP-011"}
    pool_block = [b for b in doc1["blocks"] if b["owner"] == "exploration-pool"][0]
    assert pool_block["access"]["exploration"] is True
    assert pool_block["access"]["confirmation_oneshot_exp"] is None


def test_build_catalog_reads_walker_checkpoints(tmp_path: Path) -> None:
    walker_dir = tmp_path / "walker_x"
    walker_dir.mkdir()
    (walker_dir / "checkpoint.json").write_text(
        json.dumps(
            {
                "version": 1,
                "credits_used": 12345,
                "credits_per_getblock": 10,
                "hours": {
                    "2026-01-01T00": {"status": "sealed", "stop_reason": None},
                    "2026-01-01T01": {"status": "partial", "stop_reason": "cap"},
                },
            }
        ),
        encoding="utf-8",
    )
    doc = build_catalog(LEDGER_PATH, checkpoint_dirs={"walker_x": str(walker_dir)})
    assert len(doc["walkers"]) == 1
    w = doc["walkers"][0]
    assert w["name"] == "walker_x"
    assert w["total"] == 2
    assert w["sealed"] == 1
    assert w["sealed_all"] is False
    assert w["credits_used"] == 12345


def test_build_catalog_missing_checkpoint_reports_error_not_crash(tmp_path: Path) -> None:
    doc = build_catalog(LEDGER_PATH, checkpoint_dirs={"walker_missing": str(tmp_path / "does-not-exist")})
    assert len(doc["walkers"]) == 1
    assert "error" in doc["walkers"][0]


def test_reserved_block_denied_for_every_role_even_the_exp_its_text_mentions(blocks: list[Block]) -> None:
    # "reserved: the confirmation test after EXP-012" must not unlock for EXP-012.
    for role, exp in (("exploration", None), ("confirmation-oneshot", "EXP-012"), ("confirmation-oneshot", "EXP-013")):
        ok, reasons = _check(blocks, role, "research", "2026-09-01T00", "2026-09-01T01", exp_id=exp)
        assert not ok, (role, exp, reasons)


def test_research_exploration_expansion_allowed_and_exp012_block_needs_its_id(blocks: list[Block]) -> None:
    ok, reasons = _check(blocks, "exploration", "research", "2026-08-20T00", "2026-08-20T01")
    assert ok, reasons
    # fresh-0903 was spent by EXP-012's one read and moved to the exploration pool
    ok, reasons = _check(blocks, "exploration", "research", "2026-09-05T00", "2026-09-05T01")
    assert ok, reasons
    ok, _ = _check(blocks, "confirmation-oneshot", "research", "2026-09-05T00", "2026-09-05T01", exp_id="EXP-012")
    assert not ok
    # fresh-0828 is the EXP-015 confirmation block: closed to exploration and to other ids
    ok, _ = _check(blocks, "exploration", "research", "2026-08-30T00", "2026-08-30T01")
    assert not ok
    ok, _ = _check(blocks, "confirmation-oneshot", "research", "2026-08-30T00", "2026-08-30T01", exp_id="EXP-013")
    assert not ok


def test_normalize_owner_and_host_synthetic_cells() -> None:
    from tools.mal_catalog import _normalize_host, _normalize_owner

    assert _normalize_owner("**reserved: the confirmation test after EXP-012**", row_name="x") == "reserved"
    assert _normalize_owner("**EXP-013** (reserved until sealed)", row_name="x") == "EXP-013"
    assert _normalize_owner("exploration pool", row_name="x") == "exploration-pool"
    assert _normalize_host("mal-research-0, three walkers", row_name="x") == "research"
    assert _normalize_host("mal-fast-0 (OVH)", row_name="x") == "fast"
    assert _normalize_host("mal-core-0", row_name="x") == "oracle"
    assert _normalize_host("fast", row_name="x") == "fast"
    with pytest.raises(ValueError):
        _normalize_host("mal-research-01 new box", row_name="x")
    with pytest.raises(ValueError):
        _normalize_host("mal-gpu-0", row_name="x")
