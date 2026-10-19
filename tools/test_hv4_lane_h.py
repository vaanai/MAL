"""Tests for the lane_h switch of tools/hv4_runner.py: lane H (the BST persona) is OFF in HV4 window 1, and off means off, not silent.

Off: nine arms are registered and run (CON-L/F/P/X/R, BIG-L/F/X/R), no BST bot, book, ledger, log or prompt exists, the `start` row says lane_h=false, and the
lane-G decision logs are byte-identical to those of a run with all thirteen arms (no H5 source is wired, so lane H never decided anything).
"""
from __future__ import annotations

import pytest

from tools import hv4_arms as arms
from tools import hv4_runner as R
from tools.arena_paper import load_registry
from tools.test_hv4_runner import T_END, T_START, make

LANE_G = ("CON-L", "CON-F", "CON-P", "CON-X", "CON-R", "BIG-L", "BIG-F", "BIG-X", "BIG-R")


@pytest.fixture(scope="module", autouse=True)
def _arena_test_env():
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("MAL_ARENA_TEST", "1")
        yield


def test_lane_h_off_registers_exactly_the_nine_lane_g_bots(tmp_path):
    run = make(tmp_path, lane_h=False)
    reg = load_registry(tmp_path / "bots.json")
    assert sorted(reg) == sorted(R.bot_id(a) for a in LANE_G) and len(reg) == 9
    assert not any("BST" in k for k in reg)
    assert sorted(run.books) == sorted(LANE_G) and run.lane_h is False
    assert not any(arms.ARMS[a].persona == "BST" for a in (*run.knobs, *run.arm_state, *run.invalid))
    assert not any(name.startswith("BST") for name in run.playbooks)
    assert len(make(tmp_path / "full").books) == 13  # the default of the module keeps all thirteen (the lane-H tests of this suite)


def test_lane_h_on_registers_thirteen(tmp_path):
    make(tmp_path, lane_h=True)
    assert len(load_registry(tmp_path / "bots.json")) == 13


def test_lane_g_logs_are_byte_identical_with_lane_h_off_and_no_bst_log_exists(tmp_path):
    full = make(tmp_path / "full")
    off = make(tmp_path / "off", lane_h=False)
    full.run(T_START, T_END)
    off.run(T_START, T_END)
    for name in (*LANE_G, "SCAN", "RISK"):
        a = [f.read_bytes() for f in sorted((full.log.dir / name).glob("*.jsonl"))]
        b = [f.read_bytes() for f in sorted((off.log.dir / name).glob("*.jsonl"))]
        assert a == b, name
        assert a or name.startswith("BIG") or name.endswith("-P") or name.endswith("-R"), name  # the arms that trade on this tape have a log
    assert {p.name for p in off.log.dir.iterdir()} <= {*LANE_G, "RISK", "SCAN"}  # a log dir exists only for an arm that wrote a row: never a BST one
    off.log.verify("SCAN")
    assert not any(b.startswith("hv4-BST") for b in load_registry(tmp_path / "off" / "bots.json"))


def test_the_start_row_states_lane_h(tmp_path):
    for lane_h in (False, True):
        run = make(tmp_path / str(lane_h), lane_h=lane_h)
        run.binding = {"git_sha": "abcdef1234567", "dirty": False}
        run.resume()
        start = [r for r in run.log.rows("SCAN") if r["kind"] == "start"]
        assert len(start) == 1 and start[0]["lane_h"] is lane_h


def test_an_h5_source_with_lane_h_off_is_refused(tmp_path):
    with pytest.raises(ValueError):
        make(tmp_path, h5=object(), lane_h=False)


def test_the_cli_refuses_lane_h_until_an_h5_source_is_wired(capsys):
    assert R.main(["--frozen-sha", "abcdef1234567", "--clock-start-ms", "1", "--feed-delay-slots", "7", "--lane-h"]) == 2
    assert "lane H stays off" in capsys.readouterr().err
