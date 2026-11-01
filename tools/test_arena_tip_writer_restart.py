"""F3 (a): a restart of tools/arena_tip_tape_writer.py with a downtime below the live-dir margin leaves NO hole. The follower keeps writing while the writer is down; the new writer resumes
from .writer/state.json and its output is byte-identical to a writer that never stopped, with no `tip_gap_writer_hole` (or any writer-sourced gap) row. Exits 8/9 and the latch are untouched
(this change does not edit the writer; the drill also runs their tests). SYNTHETIC tip dirs in tmp only: no real tip file, tape, state or network.

  /data/mal/venv/bin/python -m pytest tools/test_arena_tip_writer_restart.py -q
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from tools import arena_tip_reader as tr
from tools import arena_tip_tape_writer as aw
from tools import test_arena_tip_adapter as TA
from tools import test_arena_tip_tape_writer as T

pytestmark = pytest.mark.skipif(shutil.which("zstd") is None, reason="the zstd CLI is required")
LIVE_KEEP_H = 72  # the follower's retain(): hours below now - 72 h are removed from the live dir
H = 3_600_000


def loop(sim: T.TipSim, clock: T.Clock, out: Path, *, n_slots: int, every: int, down: tuple[int, int] | None, retain_h: int | None = None, **kw) -> aw.Writer:
    """T.scenario's loop with a downtime: batches [down[0], down[1]) are not stepped (the writer is closed at the start and a NEW one started at the end); the follower keeps writing, the clock
    advances. retain_h: at every batch the follower's retain() removes the live-dir hours below now - retain_h (both runs, so the reference is the same world)."""
    w = T.make_writer(sim, out, clock, **kw)
    w.start()
    step = 0
    for s in range(1, n_slots + 1):
        if 211 <= s <= 219:
            if s == 211:
                sim.gap(211, 219, sim.t(211) - 500)
            continue
        if s == 300:
            sim.skip(300)
            continue
        sim.add(s, checkpoint=False)
        if s % every == 0 or s == n_slots:
            sim.checkpoint()
            clock.ms = sim.t_done + 1500
            sim.healthy(clock.ms)
            if retain_h is not None:
                cut = tr.hour_key((clock.ms - retain_h * H) // 1000)
                for p in sim.tip.iterdir():
                    if p.name.endswith(".jsonl") and p.name[-19:-6] < cut:
                        p.unlink()
            if down and step == down[0]:
                w.close()
                w = None
            if down and step == down[1]:
                w = T.make_writer(sim, out, clock, **kw)
                w.start()
            if w is not None:
                w.step()
            step += 1
    assert w is not None
    return w


def no_hole(out: Path) -> None:
    rows = T.written_rows(out)
    assert not [r for r in rows if r.get("type") == "gap" and (r.get("src") == "writer" or r.get("kind") == aw.KIND_WRITER_HOLE)]
    assert aw.KIND_WRITER_HOLE not in {r.get("kind") for r in rows}


@pytest.mark.parametrize("down", [(3, 4), (5, 12), (10, 28)])
def test_a_a_restart_with_a_downtime_leaves_no_hole_and_the_bytes_of_a_writer_that_never_stopped(tmp_path, down):
    sim0, clock0, out0 = T.fresh(tmp_path, "ref")
    ref_w = loop(sim0, clock0, out0, n_slots=600, every=20, down=None)
    ref, ref_st = T.hours_bytes(out0), ref_w.status()
    ref_w.close()
    sim, clock, out = T.fresh(tmp_path, "down")
    w = loop(sim, clock, out, n_slots=600, every=20, down=down)
    st = w.status()
    assert st["resumed"] is True
    assert T.hours_bytes(out) == ref
    assert st["gaps"] == ref_st["gaps"]
    assert {k: st["gaps"][k] for k in ("written",)} == {k: ref_st["gaps"][k] for k in ("written",)}
    no_hole(out)
    w.close()


class Coarse(T.TipSim):
    """10 minutes per slot: 59 h of downtime is 354 slots instead of 17 700."""

    @classmethod
    def t(cls, slot: int) -> int:
        return cls.T_BASE + slot * 600_000


def coarse_fresh(tmp_path: Path, name: str):
    root = tmp_path / name
    root.mkdir()
    sim = Coarse(root)
    (sim.tip / f"trades-{tr.hour_key(Coarse.T_BASE // 1000)}.jsonl").touch()
    clock = T.Clock(Coarse.T_BASE)
    sim.healthy(clock.ms)
    return sim, clock, root / "out"


def test_a_a_downtime_just_below_the_resume_limit_with_the_follower_pruning_its_live_dir_leaves_no_hole(tmp_path):
    """The margin: the follower keeps 72 h, the writer resumes from a state up to 60 h old (RESUME_MAX_AGE_H) and starts fresh at most 70 h back (LOOKBACK_MAX_H): a resumed cursor is never
    older than 60 h, so the file it reads is always still there (12 h to spare); a fresh start has 2 h to spare. Here: ~59 h of downtime while the follower prunes at 72 h."""
    assert aw.RESUME_MAX_AGE_H < LIVE_KEEP_H and aw.LOOKBACK_MAX_H < LIVE_KEEP_H and LIVE_KEEP_H - aw.RESUME_MAX_AGE_H >= 12 and LIVE_KEEP_H - aw.LOOKBACK_MAX_H == 2
    n_slots, every = 1200, 6  # 200 hours of follower history; a step every hour
    ref_sim, ref_clock, ref_out = coarse_fresh(tmp_path, "ref")
    ref_w = loop(ref_sim, ref_clock, ref_out, n_slots=n_slots, every=every, down=None, retain_h=LIVE_KEEP_H)
    ref = T.hours_bytes(ref_out)
    ref_w.close()
    sim, clock, out = coarse_fresh(tmp_path, "down")
    first = 60  # the writer runs for 60 hourly steps, is down for 59 of them, resumes with a state 59 h old
    w = loop(sim, clock, out, n_slots=n_slots, every=every, down=(first, first + 59), retain_h=LIVE_KEEP_H)
    assert w.status()["resumed"] is True
    assert T.hours_bytes(out) == ref
    no_hole(out)
    w.close()


def test_a_a_downtime_past_the_resume_limit_is_refused_loudly_never_a_silent_hole(tmp_path):
    sim, clock, out = coarse_fresh(tmp_path, "late")
    w = T.make_writer(sim, out, clock)
    w.start()
    for s in range(1, 13):
        sim.add(s, checkpoint=False)
    sim.checkpoint()
    clock.ms = sim.t_done + 1500
    sim.healthy(clock.ms)
    w.step()
    w.close()
    clock.ms += (aw.RESUME_MAX_AGE_H + 1) * H
    sim.healthy(clock.ms)
    w2 = T.make_writer(sim, out, clock)
    with pytest.raises(aw.Refused, match="state_older_than_60h"):
        w2.start()
    w2.close()
    clock.ms -= 2 * H  # 59 h: resumes
    sim.healthy(clock.ms)
    w3 = T.make_writer(sim, out, clock)
    w3.start()
    assert w3.status()["resumed"] is True
    w3.close()


def test_a_the_latch_still_refuses_a_restart_after_exit_8_or_9(tmp_path):
    """Unchanged semantics: a stopped marker (exits 6-9) refuses a plain restart whatever the downtime."""
    sim, clock, out = T.fresh(tmp_path)
    w = T.make_writer(sim, out, clock)
    w.start()
    w.close()
    for rc in aw.LATCH_RCS:
        (out / aw.META_DIR / aw.STOP_MARKER).write_text('{"rc": %d, "why": "drill"}' % rc)
        w2 = T.make_writer(sim, out, clock)
        with pytest.raises(aw.Refused, match=f"stopped_marker_rc{rc}_drill"):
            w2.start()
        w2.close()
    assert aw.EXIT_CATCHUP == 8 and aw.EXIT_BACKLOG == 9 and aw.LATCH_RCS == (6, 7, 8, 9)
