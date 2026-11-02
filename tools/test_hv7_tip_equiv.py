"""Tests for tools/hv7_tip_equiv.py (the HV7 F1 proof legs on the tip source). SYNTHETIC walker rows only (built here from the runner tests' tape), a fake tip dir, the pinned Writer in process,
StubModel, no API, no network, no sealed path, no exploration file. Nothing here says HV7 makes money."""
from __future__ import annotations

import json
import shutil
import socket
from pathlib import Path

import pytest

import tools.haiku_caller as hcall
from tools import hv4_model as m4
from tools import hv7_runner as R
from tools import hv7_tip_equiv as E
from tools import test_arena_tip_adapter as TA
from tools.test_hv4_runner import MIN, T0
from tools.test_hv7_runner import HOUR, POOLS, T_E1, tape_rows

pytestmark = pytest.mark.skipif(shutil.which("zstd") is None, reason="the zstd CLI is required (the writer's closing)")

FIRST_HOUR = "2026-09-25T04"                       # the hour of the first trade of the synthetic tape (T0 - 70 min)
LAST_HOUR = "2026-09-25T09"
START, END = T0 + HOUR, T_E1 + 5 * MIN             # one screen at 07:02, entries at 07:02:45
SHA = "abcdef1234567"


@pytest.fixture(autouse=True)
def _env(tmp_path_factory, monkeypatch):
    base = str(tmp_path_factory.getbasetemp())
    monkeypatch.setattr(R, "READ_ROOTS", (base,))
    monkeypatch.setattr(R, "WRITE_ROOTS", (base,))
    monkeypatch.setenv("MAL_ARENA_TEST", "1")

    def no_net(*a, **k):
        raise AssertionError("network used in a test")

    monkeypatch.setattr(socket.socket, "connect", no_net)

    def no_api(*a, **k):
        raise AssertionError("HaikuCaller used in a test")

    monkeypatch.setattr(hcall.HaikuCaller, "__init__", no_api)
    monkeypatch.setattr(m4, "HaikuCaller", hcall.HaikuCaller)


def walker_stream(drop_migration: set | None = None) -> list[tuple[str, dict]]:
    """The runner tests' four-pool tape in walker form: a migration per pool in the first history hour, then PumpSwap trades with tx_index (distinct within a slot)."""
    seq, per_slot = [], {}
    first_trade = min(r["t_recv_ms"] for r in tape_rows() if r["type"] == "trade")
    for r in tape_rows():
        if r["type"] == "pool_create":
            if drop_migration and r["pool"] in drop_migration:
                continue
            t = first_trade - 10 * MIN                            # inside the first history hour, before the first trade, 2h22m before the first screen
            seq.append(("migration", {"type": "migration", "mint": r["mint"], "pool": r["pool"], "quote_mint": TA.WSOL, "slot": 399_000_000 + len(seq), "signature": r["signature"],
                                      "event_index": 0, "tx_index": 0, "block_time": t // 1000, "trader": "u"}))
            continue
        tx = per_slot.get(r["slot"], 0)
        per_slot[r["slot"]] = tx + 1
        seq.append(("trade", TA.wt(r["slot"], 0, venue="pumpswap", t=r["t_recv_ms"], tx=tx, pool=r["pool"], mint=r["mint"], trader=r["trader"], side=r["side"],
                                   sol_lamports=r["sol_lamports"], token_raw=r["token_raw"], quote_reserve=r["quote_reserve"], base_reserve=r["base_reserve"], signature=r["signature"],
                                   block_time=r["t_recv_ms"] // 1000, event_ts=r["event_ts"], virtual_quote_reserves=None)))
    seq.sort(key=lambda kw: (int(kw[1]["slot"]), 0 if kw[0] == "migration" else 1))
    return seq


# ------------------------------------------------------------------------------------------------------------------------ refusals
def test_scratch_is_a_new_dir_under_the_scratch_parent_or_the_test_tmp_and_never_a_sealed_or_live_path(tmp_path):
    assert E.check_scratch(tmp_path / "new") == tmp_path / "new"
    (tmp_path / "full").mkdir()
    (tmp_path / "full" / "x").write_text("1")
    for bad, why in ((tmp_path / "full", "scratch_not_empty"), ("relative/x", "scratch_not_absolute"), ("/data2/mal/arena-tip-feed/hv7/x", "arena-tip-feed"),
                     ("/home/claude/mal-data/arena-tipfeed/x", "arena-tipfeed"), ("/var/lib/mal/sealed/fast-trades-tip/x", "sealed"), ("/data/mal/tip-tape-archive/x", "tip-tape"),
                     ("/data/mal/arena/tape/x", "/data/mal/arena"), ("/home/claude/mal-data/arena-mirror/tape/x", "mirror"), ("/data/mal/hunt-1008/elsewhere", "scratch_not_allowed")):
        with pytest.raises(E.Refused, match=why):
            E.check_scratch(bad)
    assert E.check_scratch(E.SCRATCH_PARENT / "run1") == E.SCRATCH_PARENT / "run1"


def test_the_tape_read_by_the_legs_must_be_the_one_built_under_the_scratch_tree(tmp_path):
    assert E.check_tape(tmp_path / "derived", tmp_path) == (tmp_path / "derived").resolve()
    for bad in ("/data2/mal/arena-tip-feed/hv7", "/var/lib/mal/sealed/fast-trades-tip", "/data/mal/arena/tape", str(tmp_path.parent / "other")):
        with pytest.raises(E.Refused):
            E.check_tape(bad, tmp_path)


def test_the_command_line_refuses_without_a_pinned_floor_and_on_a_dirty_tree_or_bad_hours(tmp_path, capsys):
    assert E.main(["--out", str(tmp_path / "o")]) == 2 and json.loads(capsys.readouterr().err)["why"] == "min_periods_required"
    assert E.main(["--out", str(tmp_path / "o"), "--min-periods", "3", "--hours", "2026-10-02T10..2026-10-02T12"]) == 2           # D2/D3 hours are outside the exploration block
    assert "exploration block" in json.loads(capsys.readouterr().err)["why"]
    assert E.main(["--out", "/data2/mal/arena-tip-feed/hv7/x", "--min-periods", "3"]) == 2


def test_v_is_stamped_only_on_pumpswap_trades_that_carry_none():
    seq = [("trade", {"venue": "pumpswap", "virtual_quote_reserves": None}), ("trade", {"venue": "pumpswap", "virtual_quote_reserves": 5}), ("trade", {"venue": "pump_bonding"}),
           ("migration", {"pool": "p"})]
    out = [r for _k, r in E.with_v(seq)]
    assert out[0]["virtual_quote_reserves"] == E.V_LAMPORTS and out[1]["virtual_quote_reserves"] == 5 and "virtual_quote_reserves" not in out[2] and out[3] == {"pool": "p"}


# ------------------------------------------------------------------------------------------------------------------------ the legs
def built(tmp_path, seq=None):
    scratch = tmp_path / "s"
    scratch.mkdir()
    info = E.build_derived(E.with_v(seq if seq is not None else walker_stream()), scratch, FIRST_HOUR)
    return scratch, info


def test_build_derived_runs_the_pinned_writer_and_writes_the_offline_reference(tmp_path):
    scratch, info = built(tmp_path)
    assert info["derived_hour_files"] >= 3 and info["offline_hour_files"] >= 3 and info["offline_rows"] > 1000
    assert (scratch / "derived" / aw_meta()).is_dir()                                      # the writer's own state dir sits beside the tape files, as on the receiver copy
    assert {p.name for p in (scratch / "derived").iterdir() if p.name.startswith("tape-")}
    assert info["walker_rows_no_tx_index"] == 0                                           # the synthetic stream has tx_index everywhere (the shared-slot cases are T1 to T3)


def aw_meta() -> str:
    from tools import arena_tip_tape_writer as aw

    return aw.META_DIR


def test_equiv_1d_writer_hour_files_and_offline_rows_give_the_same_hv7_decisions(tmp_path):
    scratch, _ = built(tmp_path)
    rep = E.leg_equiv_1d(scratch, ["2026-09-25"], START, END, SHA, min_periods=1)
    assert rep["verdict"] == "PASS" and all(rep["equal"].values()), rep
    assert rep["periods_ge3"] >= 1 and rep["log_md5"]["writer"] == rep["log_md5"]["offline"] and rep["u2_age_unknown"] == 0
    flat = json.dumps(rep)
    for pool in POOLS.values():
        assert pool not in flat


def test_equiv_1d_is_not_a_pass_below_the_pinned_floor_or_when_the_runs_differ(tmp_path):
    scratch, _ = built(tmp_path)
    assert E.leg_equiv_1d(scratch, ["2026-09-25"], START, END, SHA, min_periods=10_000)["verdict"] == "FAIL"           # the floor: an empty-ish run is not a PASS
    # mutation: change one row of the offline reference; the md5 equality must break (the leg can see a difference)
    for h in ("06", "07"):                                    # the hours the 07:02 screen reads: drop every other line of the offline reference
        f = scratch / "offline" / f"tape-2026-09-25T{h}.jsonl"
        f.write_bytes(b"".join(f.read_bytes().splitlines(keepends=True)[::2]))
    rep = E.leg_equiv_1d(scratch, ["2026-09-25"], START, END, SHA + "x", min_periods=1)
    assert rep["verdict"] == "FAIL" and not all(rep["equal"].values())


def test_e1_a_runner_killed_between_t_d_and_t_e_and_resumed_gives_the_uninterrupted_md5(tmp_path):
    scratch, _ = built(tmp_path)
    rep = E.leg_e1(scratch, ["2026-09-25"], START, END, SHA, kill_at=T_E1)
    assert rep["verdict"] == "PASS" and rep["log_md5"]["whole"] == rep["log_md5"]["resumed"] and rep["periods_ge3"] >= 1


def test_f_3d_three_processes_with_different_hash_seeds_pass_the_drills_rule(tmp_path):
    scratch, _ = built(tmp_path)
    rep = E.leg_f_3d(scratch, ["2026-09-25"], START, END, SHA)
    assert rep["verdict"] == "PASS", rep["detail"]
    assert len(set(rep["log_md5"])) == 1 and len(set(rep["cards_md5"])) == 1 and rep["periods_ge3"] >= 1
    assert rep["detail"]["md5_equal"] and rep["detail"]["invariants_ok"] and rep["detail"]["non_vacuous"]


def test_u2_is_exercised_a_pool_with_no_migration_is_counted_and_the_legs_still_agree(tmp_path):
    seq = walker_stream(drop_migration={POOLS[3]})
    scratch, _ = built(tmp_path, seq)
    rep = E.leg_equiv_1d(scratch, ["2026-09-25"], START, END, SHA, min_periods=1)
    assert rep["verdict"] == "PASS" and rep["u2_age_unknown"] >= 1                         # the fourth pool is refused as age_unknown in both runs, identically


def test_run_all_reports_md5s_and_counts_only_and_removes_its_scratch(tmp_path):
    rep = E.run_all(walker_stream(), tmp_path / "all", FIRST_HOUR, LAST_HOUR, SHA, start_ms=START, end_ms=END, kill_at=T_E1, min_periods=1)
    assert rep["verdict"] == "PASS" and [r["leg"] for r in rep["legs"]] == ["EQUIV_1D", "F_3D", "E1"] and rep["v_fixture_lamports"] == E.V_LAMPORTS
    flat = json.dumps(rep, default=str)
    for pool in POOLS.values():
        assert pool not in flat
    for word in ("signature", "price", "pnl", "mint"):
        assert f'"{word}"' not in flat
    assert not (tmp_path / "all" / "derived").exists() and not (tmp_path / "all" / "tip").exists()
