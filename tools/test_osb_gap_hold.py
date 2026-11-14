"""gap_exclusion "hold" (gap-in-hold twin books): window rules, flags, byte identity of "on"/"off", decisions.md5. Synthetic fixtures in tmp dirs only; no real tape or ledger.
Nothing here says any book makes money."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts" / "research"))
from tools.arena_paper import Gap  # noqa: E402
from tools.test_arena_paper import _row, _tape, tms  # noqa: E402,F401
from tools.test_floor_gap_range import mk  # noqa: E402
import tools.arena_paper as AP  # noqa: E402


@pytest.fixture(autouse=True)
def _arena_test_env(monkeypatch):
    monkeypatch.setenv("MAL_ARENA_TEST", "1")  # the engine's test-only registry clock / sha overrides are gated on it


def gap(slot, silent_ms=800, prog="pumpswap", kind="silence"):
    return {"type": "gap", "kind": kind, "slot": slot, "t_recv_ms": tms(slot), "silent_ms": silent_ms, "data_loss": True, "prog": prog}


def drive(eng, gaps=(), buy=100, sell=150, rows=None):
    """gaps: [(after_slot, gap row)] injected right after the tape row at that slot"""
    pid = None
    g = dict(gaps)
    for r in (rows if rows is not None else _tape()):
        eng.on_row(r)
        if r["slot"] in g:
            eng.on_row(g.pop(r["slot"]))
        if r["slot"] == buy and pid is None:
            pid = eng.buy("botA", "P1", None, tms(buy), buy)
        if r["slot"] == sell and pid and eng.positions[pid].exit_slot is None:
            eng.sell("botA", pid, 1.0, tms(sell), sell)
    return pid


def rws(eng, kind):
    return [r for r in eng.ledger.read_bot("botA") if r["kind"] == kind]


def sub(tmp, name, **kw):
    d = tmp / name
    d.mkdir()
    return mk(d, **kw)


def test_no_gap_hold_rows_equal_strict_rows(tmp_path):
    s, h = sub(tmp_path, "s", gap_exclusion=True), sub(tmp_path, "h", gap_exclusion="hold")
    drive(s)
    drive(h)
    assert [json.dumps(r, sort_keys=True) for r in rws(s, "trade")] == [json.dumps(r, sort_keys=True) for r in rws(h, "trade")] and len(rws(h, "trade")) == 3


def test_gap_mid_hold_is_priced_and_flagged_with_blind_seconds(tmp_path):
    h, s = sub(tmp_path, "h", gap_exclusion="hold"), sub(tmp_path, "s", gap_exclusion=True)
    drive(h, gaps=[(120, gap(120))])
    t = rws(h, "trade")
    assert len(t) == 3 and rws(h, "excluded") == []
    assert all(r["gap_in_hold"] is True and r["gap_count"] == 1 and r["blind_s"] == pytest.approx(0.8) for r in t)
    g = h.gaps[0]
    assert all(r["gap_holes"] == [[max(g.t_lo, r["decision_ms"]["entry"]), g.t_hi]] for r in t)  # times only, clipped to the hold
    drive(s, gaps=[(120, gap(120))])
    assert rws(s, "trade") == [] and {r["exclusion_reason"] for r in rws(s, "excluded")} == {"feed_gap"}  # strict leaves the same trade out


def test_two_overlapping_gaps_union_and_count(tmp_path):
    h = sub(tmp_path, "h", gap_exclusion="hold")
    drive(h, gaps=[(120, gap(120, 800)), (121, gap(121, 1200))])
    t = rws(h, "trade")
    assert t and t[0]["gap_count"] == 2
    lo, hi = min(g.t_lo for g in h.gaps), max(g.t_hi for g in h.gaps)
    assert t[0]["blind_s"] == pytest.approx((hi - lo) / 1000.0)  # the two overlap: the union is one interval, not the sum


def test_gap_in_entry_window_is_excluded_feed_gap_entry(tmp_path):
    h = sub(tmp_path, "h", gap_exclusion="hold")
    drive(h, gaps=[(98, gap(98))])
    assert rws(h, "trade") == [] and {r["exclusion_reason"] for r in rws(h, "excluded")} == {"feed_gap_entry"}
    assert h.counters.get("excluded_feed_gap_entry") == 1


def test_gap_in_exit_window_is_excluded_feed_gap_exit(tmp_path):
    h = sub(tmp_path, "h", gap_exclusion="hold")
    drive(h, gaps=[(150, gap(150, 800))])
    assert rws(h, "trade") == [] and {r["exclusion_reason"] for r in rws(h, "excluded")} == {"feed_gap_exit"}


def test_gaps_between_the_two_windows_are_priced(tmp_path):
    h = sub(tmp_path, "h", gap_exclusion="hold")
    drive(h, gaps=[(125, gap(125, 400)), (135, gap(135, 400))])
    t = rws(h, "trade")
    assert len(t) == 3 and t[0]["gap_count"] == 2 and t[0]["blind_s"] == pytest.approx(0.8)


def test_program_matching_as_gap_hit(tmp_path):
    h = sub(tmp_path, "a", gap_exclusion="hold")  # pool P1 is pumpswap: a pumpfun gap in its entry window does not touch it
    drive(h, gaps=[(98, gap(98, prog="pumpfun"))])
    t = rws(h, "trade")
    assert t and "gap_in_hold" not in t[0]
    h2 = sub(tmp_path, "b", gap_exclusion="hold")  # prog None / "all" match any program
    drive(h2, gaps=[(98, gap(98, prog=None))])
    assert {r["exclusion_reason"] for r in rws(h2, "excluded")} == {"feed_gap_entry"}
    h3 = sub(tmp_path, "c", gap_exclusion="hold")
    drive(h3, gaps=[(98, gap(98, prog="all"))])
    assert {r["exclusion_reason"] for r in rws(h3, "excluded")} == {"feed_gap_entry"}


def test_window_matching_is_time_or_slot(tmp_path):
    h = sub(tmp_path, "h", gap_exclusion="hold")
    drive(h, rows=[r for r in _tape() if r["slot"] <= 105], sell=999)
    pos = next(iter(h.positions.values()))
    tlo, thi, slo, shi = h._entry_window(pos)
    assert tlo == pos.decision_ms - 2000 and shi == pos.sd + 0 + 5 + 1 + 0 and slo == min(pos.call_feed_slot, pos.sd) - 5  # NEARBY_WINDOW_S 2 s at 0.4 s/slot = 5; k_max 4 (1.9 s)
    far_time_in_slots = Gap("pumpswap", 0, 1, pos.sd, pos.sd + 1, "x")  # time far away, slots inside: matches
    far_slots_in_time = Gap("pumpswap", pos.decision_ms, pos.decision_ms + 10, 0, 1, "x")  # slots far away, time inside: matches
    nothing = Gap("pumpswap", 0, 1, 0, 1, "x")
    for g, want in ((far_time_in_slots, True), (far_slots_in_time, True), (nothing, False)):
        h.gaps = [g]
        assert bool(h._gap_matches("pumpswap", h._entry_window(pos))) is want


def test_derived_last_exit_extends_the_exit_window(tmp_path):
    h = sub(tmp_path, "h", gap_exclusion="hold")
    drive(h, rows=[r for r in _tape() if r["slot"] <= 152], sell=150)  # pool P1's tape stops at slot 160: need is slot ~160, so the exit waits for the high-water mark
    far = _row(500, 400, pool="Z")  # another pool's print moves hw_slot far past need + grace
    h.on_row(far)
    pos = next(iter(h.positions.values()))
    need = h._needed_slot(pos)
    tape_win = h._exit_window(pos, h.pools["P1"], "tape", need)
    der_win = h._exit_window(pos, h.pools["P1"], "derived_last", need)
    assert der_win[1] >= tape_win[1] and der_win[1] >= h.hw_ms and der_win[3] >= h.hw_slot and der_win[3] > tape_win[3]
    h.gaps = [Gap("pumpswap", h.hw_ms - 300, h.hw_ms - 200, h.hw_slot, h.hw_slot, "x")]  # a late gap, inside only the derived window
    assert h._gap_matches("pumpswap", tape_win) == [] and h._gap_matches("pumpswap", der_win)


def test_first_print_after_gap_at_or_above_tp_fires_there(tmp_path):
    """the engine prices what the host decided: a sell decided at the first print after the hole is priced there (Q3: observed prints only, nothing added in the engine)"""
    h = sub(tmp_path, "h", gap_exclusion="hold")
    drive(h, gaps=[(120, gap(120, 2000))], sell=125)
    t = rws(h, "trade")
    assert len(t) == 3 and t[0]["effective_slot"]["exit"] == 125 and t[0]["gap_in_hold"] is True


def test_exit_invalid_across_a_gap_stays_priced_and_is_counted(tmp_path, monkeypatch):
    real = AP.price_leg

    def bad(*a, **k):
        r = dict(real(*a, **k))
        r["state"] = "exit_invalid"
        return r
    monkeypatch.setattr(AP, "price_leg", bad)
    h, n = sub(tmp_path, "h", gap_exclusion="hold"), sub(tmp_path, "n", gap_exclusion="hold")
    drive(h, gaps=[(120, gap(120))])
    drive(n)
    assert len(rws(h, "trade")) == 3 and rws(h, "excluded") == [] and h.counters.get("exit_invalid_across_gap") == 1
    assert len(rws(n, "trade")) == 3 and "exit_invalid_across_gap" not in n.counters  # no gap in the hold: not counted


# ------------------------------------------------------------------------------------------------------------------------------ review follow-ups
OLD_ENGINE_SHA256 = "4e20a017d8d4f6b6aa654d11f4dac44af9ae020c6219d19877fef2ae3e29d493"  # sha256 of scripts/research/floor_gap_range_engine.py at origin/main 8c7061e7


def _old_engine():
    import hashlib
    import types
    raw = (Path(__file__).resolve().parent / "fixtures_osb" / "floor_gap_range_engine_8c7061e7.py.txt").read_bytes()
    assert hashlib.sha256(raw).hexdigest() == OLD_ENGINE_SHA256  # the frozen copy cannot drift silently
    mod = types.ModuleType("floor_gap_range_engine_old")
    exec(compile(raw.decode(), "floor_gap_range_engine_8c7061e7.py", "exec"), mod.__dict__)
    return mod.GapRangeEngine


@pytest.mark.parametrize("flag", [True, False])
@pytest.mark.parametrize("gaps", [(), ((120, 800),), ((120, 800), (121, 1200)), ((98, 800),), ((150, 800),)])
def test_on_off_priced_trades_equal_the_old_engine(tmp_path, monkeypatch, flag, gaps):
    """priced and excluded rows with and without gaps: the new engine's 'on' / 'off' ledger projection equals the 8c7061e7 engine's"""
    import sys as _sys
    from tools.test_arena_paper import SC, register_bot
    Old = _old_engine()
    out = {}
    for tag, cls in (("new", sys.modules["floor_gap_range_engine"].GapRangeEngine), ("old", Old)):
        d = tmp_path / tag
        d.mkdir()
        reg = d / "bots.json"
        register_bot("botA", "abc1234", 0, reg, now_ms=0)
        eng = cls(d / "ledger", reg, SC(strand_rule="total"), sps_fn=lambda: 0.4, code_state_fn=lambda: ("abc1234", False), gap_exclusion=flag)
        eng.now_fn = lambda e=eng: e.hw_ms or 0
        drive(eng, gaps=[(sl, gap(sl, ms_)) for sl, ms_ in gaps])
        out[tag] = [json.dumps({k: v for k, v in r.items() if k not in ("written_ms", "engine_id")}, sort_keys=True) for r in eng.ledger.read_bot("botA") if r["kind"] != "start"]
    assert out["new"] and out["new"] == out["old"]


def test_zero_length_and_slot_only_gaps_are_flagged_not_holes(tmp_path):
    h = sub(tmp_path, "h", gap_exclusion="hold")
    drive(h, rows=[r for r in _tape() if r["slot"] <= 121], sell=120)
    pos = next(iter(h.positions.values()))
    h.gaps = [Gap("pumpswap", 0, 1, pos.sd + 2, pos.sd + 3, "x")]  # slots inside the hold, time far away: a slot-only match
    h._hold_note(pos, "tape", h._needed_slot(pos))
    info = h._hold_info[pos.position_id]
    assert info == {"matched": 1, "gap_count": 0, "slot_only_gaps": 1, "blind_s": 0.0, "gap_holes": []}
    h.gaps = [Gap("pumpswap", pos.decision_ms + 5000, pos.decision_ms + 5000, 0, 1, "x")]  # zero-length in time
    h._hold_note(pos, "tape", h._needed_slot(pos))
    assert h._hold_info[pos.position_id]["gap_holes"] == [] and h._hold_info[pos.position_id]["blind_s"] == 0.0 and h._hold_info[pos.position_id]["matched"] == 1
