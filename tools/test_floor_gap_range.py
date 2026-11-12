"""floor_gap_range_engine: gap exclusion off prices an overlapped position; default excludes it; marks gap_overlap; time-stop shadow. Synthetic tape, no network."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts" / "research"))
import floor_gap_range_engine as G  # noqa: E402
from tools import arena_rule_l1 as L  # noqa: E402
from tools.test_arena_paper import SC, V, _tape, register_bot, tms  # noqa: E402,F401


def mk(tmp, **kw):
    reg = tmp / "bots.json"
    register_bot("botA", "abc1234", 0, reg, now_ms=0)
    eng = G.GapRangeEngine(tmp / "ledger", reg, SC(strand_rule="total"), sps_fn=lambda: 0.4, code_state_fn=lambda: ("abc1234", False), **kw)
    eng.now_fn = lambda: eng.hw_ms or 0
    return eng


def drive(eng, with_gap, gap_slot=110):
    pid = None
    for r in _tape():
        eng.on_row(r)
        if r["slot"] == 100:
            pid = eng.buy("botA", "P1", None, tms(100), 100)
        if with_gap and r["slot"] == gap_slot:
            eng.on_row({"type": "gap", "kind": "silence", "slot": gap_slot, "t_recv_ms": tms(gap_slot), "silent_ms": 800, "data_loss": True, "prog": "pumpswap"})
        if r["slot"] == 150:
            eng.sell("botA", pid, 1.0, tms(150), 150)
    return pid


def rows(eng, kind):
    return [r for r in eng.ledger.read_bot("botA") if r["kind"] == kind]


def test_default_excludes_overlapped_and_marks_it(tmp_path):
    eng = mk(tmp_path, gap_exclusion=True)
    drive(eng, True)
    ex = rows(eng, "excluded")
    assert len(ex) == 3 and {r["exclusion_reason"] for r in ex} == {"feed_gap"} and rows(eng, "trade") == []
    assert all(r["gap_overlap"] is True and len(r["gap_holes"]) == 1 for r in ex)


def test_option_off_prices_the_overlapped_position_same_entry(tmp_path):
    a, b, c = tmp_path / "a", tmp_path / "b", tmp_path / "c"
    for d in (a, b, c):
        d.mkdir()
    on, off, nogap = mk(a, gap_exclusion=True), mk(b, gap_exclusion=False), mk(c, gap_exclusion=False)
    p_on, p_off, p_ng = drive(on, True), drive(off, True), drive(nogap, False)
    assert p_on == p_off == p_ng  # same entry (position id is a function of the entry decision)
    t = rows(off, "trade")
    assert len(t) == 3 and rows(off, "excluded") == [] and all(r["gap_overlap"] is True for r in t)
    # priced through the hole equals the same position on a tape with no hole (the prints are identical)
    clean = rows(nogap, "trade")
    assert [r["legs"] for r in t] == [r["legs"] for r in clean] and all(r["gap_overlap"] is False and r["gap_holes"] == [] for r in clean)


def test_non_overlapped_position_identical_in_both_modes(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(); b.mkdir()
    on, off = mk(a, gap_exclusion=True), mk(b, gap_exclusion=False)
    drive(on, False); drive(off, False)
    assert [r["legs"] for r in rows(on, "trade")] == [r["legs"] for r in rows(off, "trade")] and len(rows(on, "trade")) == 3


def test_ts_shadow_scheduled_only_for_overlapped_non_time_exit(tmp_path):
    eng = mk(tmp_path, gap_exclusion=False, ts_variant=True)
    pid = drive(eng, True)
    assert eng.ts_open() == 1 and len(eng._shadows) == 1 and eng._shadows[0][1] == pid + "-ts"
    assert eng._shadows[0][0] == eng.positions[pid + "-ts"].decision_ms + L.HOLD_MS
    (tmp_path / "x").mkdir()
    clean = mk(tmp_path / "x", gap_exclusion=False, ts_variant=True)
    drive(clean, False)
    assert clean.ts_open() == 0


def test_reader_groups_on_engine_ledger(tmp_path):
    import json
    import floor_gap_range as R
    eng = mk(tmp_path, gap_exclusion=False, ts_variant=True)
    drive(eng, True)
    R.main(str(tmp_path / "ledger"), str(tmp_path / "o.json"))
    r = json.load(open(tmp_path / "o.json"))
    assert r["counts"]["overlap_n"] == 1 and r["counts"]["A_n"] == 0
    assert r["groups"]["0.1:flat"]["B"]["pooled"]["K=16"]["n"] == 1 or r["groups"]["0.1:flat"]["B"]["per_day"]
