"""Tests for the T2 time-shift stress replay (--boost-shift, pre-declaration amendment 7) in tools/cap_pick_score.py. Toy paths only; nothing reads /data/mal."""

from __future__ import annotations

import dataclasses
import hashlib
import json

import numpy as np
import pytest

from tools import cap_pick_score as cps
from tools.test_cap_pick_score import V, needs_zstd, read_rows_csv, run_fix, write_fixture, write_out
from tools.test_cap_pick_score_t2 import BASE, OTHERS, _boost_rows, _cp_tape, run_keeper, write_keeper_fixture

PHASE1_ROWS_MD5 = "7266ed2956c475c0e493396c44ae6075"  # rows.csv of the phase-1 fixture, base scorer 2bab07b


def _shift(s, events):
    t = _cp_tape(events)
    return t, cps.shift_boost_path(s, trader=t["trader"], slot=t["slot"], isbuy=t["isbuy"], sol=t["sol"], tok=t["tok"], qpre=t["qpre"], bpre=t["bpre"], bt=t["bt"], s0=100)


def _events():
    return sorted(OTHERS + _boost_rows(), key=lambda e: e[0])


def test_the_new_slot_is_s0_plus_floor_of_the_offset_times_one_minus_s_in_exact_arithmetic():
    ev = _events()
    for s, frac in ((0.2, (4, 5)), (0.4, (3, 5))):
        t, c = _shift(s, ev)
        old = {e[0] for e in ev if e[1] == 7}
        want = sorted(100 + ((o - 100) * frac[0]) // frac[1] for o in old)
        assert sorted(int(x) for x, w in zip(c["slot"], c["trader"]) if w == 7) == want
    # 0.4 -> 3/5 exactly: an offset of 5 must give 3, not the 2 a float 5 x 0.6000000000000001 or 4.999... could give
    n = 29  # a path that is the keeper alone: the print order cannot change, only the slot labels do, and the tape's states stand
    t = dict(trader=np.full(n, 7, np.int64), slot=np.arange(105, 105 + n, dtype=np.int64), isbuy=np.ones(n, bool), sol=np.full(n, 6e8), tok=np.full(n, 1e9),
             qpre=np.full(n + 1, 8.5e10), bpre=np.full(n + 1, 2e14), bt=np.full(n, -1, np.int64))
    c = cps.shift_boost_path(0.4, s0=100, **t)
    assert [int(x) for x in c["slot"][:4]] == [103, 103, 104, 104] and c["qpre"] is t["qpre"] and c["moved"] == n and c["first_change_slot"] == 103
    assert all(int(c["slot"][i]) == 100 + ((5 + i) * 3) // 5 for i in range(n))


def test_moved_keeper_prints_follow_the_non_keeper_prints_of_their_slot_and_keep_their_own_order():
    # s = 0.2 moves the keeper print at 108 to 106 and the one at 128 to 122; non-keeper prints at 106 and 122 must come first
    ev = sorted([(101, 1, "buy", 10**9), (106, 2, "buy", 10**9), (122, 3, "sell", 5 * 10**12)] + _boost_rows(), key=lambda e: e[0])
    t, c = _shift(0.2, ev)
    assert (np.diff(c["slot"]) >= 0).all()
    pos = {int(s_): [int(w) for w, x in zip(c["trader"], c["slot"]) if x == s_] for s_ in (106, 122)}
    assert pos[106] == [2, 7] and pos[122] == [3, 7]  # non-keeper first, keeper after
    keeper_sol = [x for x, w in zip(c["sol"], c["trader"]) if w == 7]
    assert keeper_sol == [6e8] * 29 and c["moved"] > 0  # same amounts, same order
    assert [int(x) for x, w in zip(c["slot"], c["trader"]) if w not in (7,)] == [101, 106, 122]  # other prints keep their slots and order


def test_the_shift_resimulation_equals_the_tape_a_pool_would_have_written_in_the_new_order():
    ev = _events()
    for s in (0.2, 0.4):
        t, c = _shift(s, ev)
        frac = {0.2: (4, 5), 0.4: (3, 5)}[s]
        new = [(100 + ((e[0] - 100) * frac[0]) // frac[1] if e[1] == 7 else e[0], e[1], e[2], e[3], 1 if e[1] == 7 else 0, i) for i, e in enumerate(ev)]
        new.sort(key=lambda x: (x[0], x[4], x[5]))
        want = _cp_tape([(x[0], x[1], x[2], x[3]) for x in new])
        assert np.array_equal(c["slot"], want["slot"]) and np.array_equal(c["trader"], want["trader"])
        for k in ("sol", "tok", "qpre", "bpre"):
            np.testing.assert_allclose(c[k], want[k], rtol=1e-12, atol=0)
        i0 = int(np.flatnonzero(c["slot"] != t["slot"])[0])
        np.testing.assert_array_equal(c["qpre"][:i0], t["qpre"][:i0])  # the tape stands before the first change
        inv = t["qpre"][i0] * t["bpre"][i0]
        np.testing.assert_allclose(c["qpre"][i0:] * c["bpre"][i0:], inv, rtol=1e-12)  # constant product
        assert c["first_change_slot"] == int(min(t["slot"][i0], c["slot"][i0]))


def test_no_keeper_or_a_zero_shift_leaves_the_path_alone():
    t, c = _shift(0.2, sorted(OTHERS, key=lambda e: e[0]))
    assert c["keeper"] == 0 and c["moved"] == 0 and c["slot"] is t["slot"] and c["first_change_slot"] is None
    # a keeper that is already inside slot 100 stays put: nothing changes
    one = [(100, 7, "buy", 6 * 10**8)] * 29
    t2, c2 = _shift(0.4, one)
    assert c2["moved"] == 0 and c2["qpre"] is t2["qpre"]


def test_b90_fires_on_a_shifted_path_but_not_on_the_tape_and_the_cap_is_unchanged_in_kind():
    ev = _events()
    t, c0 = _shift(0.2, ev)
    for s in (0.2, 0.4):
        _, c = _shift(s, ev)
        bi = cps.boost_scan(c["trader"], c["isbuy"], c["sol"], c["slot"], 100)
        assert bi is not None
        assert c["slot"][bi[0]] < t["slot"][cps.boost_scan(t["trader"], t["isbuy"], t["sol"], t["slot"], 100)[0]]  # the 27th slice comes earlier than on the tape


def test_config_validation_and_refusals():
    ok = dataclasses.replace(cps.Config(), boost_shift=0.2)
    ok.validate()
    assert ok.shift_active and ok.needs_trader and not dataclasses.replace(cps.Config(), boost_shift=0.0).shift_active and not dataclasses.replace(cps.Config(), boost_shift=0.0).needs_trader
    for bad in (dict(boost_shift=-0.1), dict(boost_shift=1.0), dict(boost_shift=True), dict(boost_shift=0.2, boost_cut=0.8), dict(boost_shift=0.0, boost_cut=0.8)):
        with pytest.raises(cps.Refused):
            dataclasses.replace(cps.Config(), **bad).validate()


@needs_zstd
def test_a_zero_shift_is_the_default_run_byte_for_byte(tmp_path):
    view, vpath = write_fixture(tmp_path)
    s, rows = run_fix(view, vpath, cps.Config(boost_shift=0.0))
    out, summary, _ = write_out(tmp_path, s, rows)
    assert hashlib.md5((out / "rows.csv").read_bytes()).hexdigest() == PHASE1_ROWS_MD5
    s0, rows0 = run_fix(view, vpath)
    out0, summary0, _ = write_out(tmp_path, s0, rows0, name="out0")
    assert hashlib.md5((out0 / "rows.csv").read_bytes()).hexdigest() == PHASE1_ROWS_MD5
    assert {k: v for k, v in summary["config"].items() if k != "boost_shift"} == {k: v for k, v in summary0["config"].items() if k != "boost_shift"}
    assert summary["books"] == summary0["books"] and "t2" not in summary


@needs_zstd
def test_end_to_end_a_keeper_ending_near_340_s_fires_before_300_s_only_when_shifted(tmp_path):
    view, vpath = write_keeper_fixture(tmp_path, cadence=29)  # 29 slices, 29 slots apart: 0.416 s/slot -> the last slice about 341 s after s0
    cfg = dataclasses.replace(cps.Config(), exit_mode="boost90", paired=True)
    s0_, r0 = run_keeper(view, vpath, cfg)
    assert r0[0]["exit_type"] == "deadline" and r0[0]["boost_found"] == 1  # today: the 27th slice (about 317 s) is after the 300 s cap: B90 is the cap
    seen = {}
    for sh in (0.2, 0.4):
        s, (r,) = run_keeper(view, vpath, dataclasses.replace(cfg, boost_shift=sh))
        assert r["exit_type"] == "boost" and r["alt_exit_type"] == "deadline" and r["boost_shift_moved"] == 29 and r["boost_shift_keeper"] == 1
        trig = r["boost_trigger_slot"] - (BASE + 1)
        seen[sh] = trig
        assert trig * 0.4158 < 300 and r["boost_shift_entry_overlap"] == 0
        assert s["t2"]["boost_shift"] == sh and s["t2"]["counts"]["shift_prints_moved"] == 29 and s["t2"]["counts"]["shift_entry_overlap"] == 0
        assert s["paired"]["books"]["all"]["P2-P4"]["flat"]["b90_fires"] == 1
    assert seen[0.4] < seen[0.2] < (BASE + 762 - (BASE + 1))  # the earlier the schedule, the earlier the trigger; both before today's 27th slice


@needs_zstd
def test_end_to_end_entry_overlap_is_counted_and_the_entry_is_resimulated(tmp_path):
    view, vpath = write_keeper_fixture(tmp_path, cadence=29, first=6)  # first keeper print at base+6, after the landing slot base+4 (k = 3)
    base_s, (r0,) = run_keeper(view, vpath, cps.Config())
    s, (r,) = run_keeper(view, vpath, cps.Config(boost_shift=0.4))
    assert r["landing_slot"] == r0["landing_slot"] == BASE + 4
    assert r["boost_shift_entry_overlap"] == 1 and s["t2"]["counts"]["shift_entry_overlap"] == 1  # the keeper's first print moved from base+7 to base+4, onto the landing slot
    assert r["exec_ratio"] != r0["exec_ratio"]  # the entry state is the shifted path's, not the tape's
    # and with the keeper starting after the landing slot nothing overlaps, and the entry is the tape's
    view2, vpath2 = write_keeper_fixture(tmp_path / "late", cadence=29, first=8)
    _, (r2,) = run_keeper(view2, vpath2, cps.Config(boost_shift=0.4))
    _, (r20,) = run_keeper(view2, vpath2, cps.Config())
    assert r2["boost_shift_entry_overlap"] == 0 and r2["exec_ratio"] == r20["exec_ratio"]


@needs_zstd
def test_cli_boost_shift_flag_and_the_refusal(tmp_path, capsys):
    view, vpath = write_keeper_fixture(tmp_path, cadence=29)
    out = tmp_path / "cli"
    assert cps.main(["--p2-view-dir", str(view), "--vmap", str(vpath), "--out-dir", str(out), "--exit-mode", "boost90", "--paired", "--boost-shift", "0.2"]) == 0
    c = json.loads((out / "summary.json").read_text())
    assert c["config"]["boost_shift"] == 0.2 and c["t2"]["counts"]["shift_keepers"] == 1
    row = read_rows_csv(out / "rows.csv")[0]
    assert row["exit_type"] == "boost" and row["boost_shift_moved"] == "29" and row["boost_shift_entry_overlap"] == "0"
    bad = tmp_path / "bad"
    capsys.readouterr()
    assert cps.main(["--p2-view-dir", str(view), "--vmap", str(vpath), "--out-dir", str(bad), "--boost-cut", "0.8", "--boost-shift", "0.2"]) == 2
    assert "mutually exclusive" in capsys.readouterr().err and not bad.exists()
    assert cps.main(["--p2-view-dir", str(view), "--vmap", str(vpath), "--out-dir", str(bad), "--boost-shift", "1.0"]) == 2
