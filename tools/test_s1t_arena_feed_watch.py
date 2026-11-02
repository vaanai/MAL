"""Tests of tools/s1t_arena_feed_watch.py (QP-NOTE-1102-tip-readers Note 2 section N2.3, W1 to W7). Synthetic status series and files in tmp_path only;
no arena path is opened.

Series used: G (gaps) at 100 per minute and R (reconnects) at 50 per minute, so every full hour is b = 6000 and 3000 per hour and T = 12000 and 6000.
The increment of a trailing 60-min window at check minute j after S is the sum of the per-minute rates over minutes j-59..j."""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

import pytest

from tools import s1t_arena_feed_watch as aw

S = 1_800_000_000_000  # the smoke start (epoch ms), an arbitrary synthetic instant
H = 3_600_000
MIN = 60_000
BASELINE = "S1T_SIDE_FEED_BASELINE G b=6000.00 m=6000.00 T=12000.00 R b=3000.00 m=3000.00 T=6000.00"


def status(g, r, pid, upd):
    return {"updated_ms": upd, "started_ms": upd - 5 * H, "pid": pid, "gaps": g,
            "sockets": {"pumpswap": [{"reconnects": r - 1}, {"reconnects": 1}], "pumpfun": {"a": {"reconnects": 0}}}}


class Sim:
    """Minute-by-minute samples from S - 24 h on. `m` is the minute relative to S (the first live sample is m = 0)."""

    def __init__(self, start_min=0):
        self.w = aw.Watcher(S)
        self.t, self.g, self.r, self.pid, self.events = start_min, 1000, 500, 1, []

    @property
    def m(self):
        return self.t - 1440

    def step(self, g_rate=100, r_rate=50, *, stale=False, none=False, pid=None, reset=False):
        if pid is not None and pid != self.pid:
            self.pid = pid
            if reset:
                self.g = self.r = 0  # a new process starts its counters again
        wall = S - 24 * H + self.t * MIN
        self.g += g_rate
        self.r += r_rate
        st = None if none else status(self.g, self.r, self.pid, wall - (301_000 if stale else 0))
        lines = self.w.step(st, wall)
        self.events += [(self.m, ln) for ln in lines]
        self.t += 1
        return lines

    def skip(self, g_rate=100, r_rate=50):  # a minute in which the watcher took no sample
        self.g += g_rate
        self.r += r_rate
        self.t += 1

    def lines(self, prefix):
        return [(m, ln) for m, ln in self.events if ln.startswith(prefix)]


def pre(plan=None):
    """24 hours before S. plan: hour index -> mode (stale, none, pid, n49, n50, dec, hot)."""
    sim = Sim()
    for k in range(24):
        mode = (plan or {}).get(k)
        for i in range(60):
            if mode in ("n49", "n50") and i >= int(mode[1:]):
                sim.skip()
            elif mode == "dec" and i == 30:
                sim.g -= 5000
                sim.step()
            elif i == 30 and mode in ("stale", "none", "pid"):
                sim.step(stale=mode == "stale", none=mode == "none", pid=2 if mode == "pid" else None)
            else:
                sim.step(g_rate=400 if mode == "hot" else 100)
    return sim


def warm():
    sim = pre()
    assert sim.events == [] and sim.m == 0  # nothing is printed before S
    return sim


# ---- W2, W3: the baseline ---------------------------------------------------------------------------------------------------------------------
def test_baseline_line_at_S_then_checks():
    sim = warm()
    assert sim.step() == [BASELINE]
    assert sim.step() == []  # a completed check at or below T prints nothing


def test_threshold_is_the_max_of_2b_1_25m_and_b_plus_20():
    sim = pre({10: "hot"})  # one hour at 400/min: b = 6750, m = 24000, so 1.25 m binds (30000)
    assert sim.step()[0] == "S1T_SIDE_FEED_BASELINE G b=6750.00 m=24000.00 T=30000.00 R b=3000.00 m=3000.00 T=6000.00"
    sim = Sim()
    for _ in range(1440):
        sim.step(g_rate=0, r_rate=0)
    assert sim.step(0, 0)[0].startswith("S1T_SIDE_FEED_BASELINE G b=0.00 m=0.00 T=20.00 R b=0.00 m=0.00 T=20.00")  # b + 20 binds


@pytest.mark.parametrize("bad", [0, 1, 2, 3, 4, 5, 6])
def test_hours_are_valid_or_not(bad):
    """Hour validity: a STALE sample, an UNREADABLE sample, two pids, 49 samples (50 are enough) or a counter going down each invalidate one hour; 18 valid
    hours of 24 are enough, 17 are not."""
    modes = ["stale", "pid", "n49", "none", "dec", "stale", "none"]
    plan = {k: modes[k] for k in range(bad)}
    plan[20] = "n50"  # exactly 50 samples: valid
    sim = pre(plan)
    first = sim.step()
    if bad <= 6:
        assert BASELINE in first or first[0].startswith("S1T_SIDE_FEED_BASELINE")
        assert not sim.lines("S1T_SIDE_FEED_NO_BASELINE")
    else:
        assert sim.lines("S1T_SIDE_FEED_NO_BASELINE")


def test_seven_invalid_hours_end_the_baseline_before_S():
    plan = {k: "stale" for k in range(7)}
    sim = pre(plan)
    hits = sim.lines("S1T_SIDE_FEED_NO_BASELINE")
    assert hits and hits[0][0] == 420 - 1440  # at the first sample after the 7th invalid hour completes, long before S
    sim6 = pre({k: "stale" for k in range(6)})
    assert not sim6.lines("S1T_SIDE_FEED_NO_BASELINE") and sim6.step()[0].startswith("S1T_SIDE_FEED_BASELINE")


def test_no_baseline_when_the_watcher_started_late():
    sim = Sim(start_min=720)  # 12 h before S, not 24 h
    assert sim.step() == ["S1T_SIDE_FEED_NO_BASELINE"]


# ---- W4: the check ----------------------------------------------------------------------------------------------------------------------------
def test_a_doubling_stops_on_the_tenth_check():
    sim = warm()
    sim.step()
    for _ in range(60):
        sim.step(g_rate=300)
    stops = sim.lines("S1T_SIDE_FEED_STOP")
    assert stops[0] == (40, "S1T_SIDE_FEED_STOP series=G inc=14000.00 T=12000.00")  # 6000 + 200 j > 12000 from j = 31; the 10th is j = 40


def test_the_r_series_stops_too():
    sim = warm()
    sim.step()
    for _ in range(60):
        sim.step(r_rate=150)
    assert sim.lines("S1T_SIDE_FEED_STOP")[0] == (40, "S1T_SIDE_FEED_STOP series=R inc=7000.00 T=6000.00")


def test_a_rise_above_1_25m_but_below_2b_does_not_stop():
    sim = warm()
    sim.step()
    for _ in range(300):
        sim.step(g_rate=150)  # 9000 an hour: above 1.25 m = 7500, below T = 2 b = 12000
    assert not sim.lines("S1T_SIDE_FEED_STOP") and not sim.lines("S1T_SIDE_FEED_BLIND")


def burst(rate):
    sim = warm()
    sim.step()
    for _ in range(60):
        sim.step(g_rate=rate)  # minutes 1 to 60
    for _ in range(200):
        sim.step()
    return sim


def test_a_nine_check_excursion_does_not_stop():
    # 208 per minute for 60 minutes: the window sum is 6000 + 108 n, above 12000 for n >= 56, which is 5 checks up and 4 down = 9 checks
    assert not burst(208).lines("S1T_SIDE_FEED_STOP")
    # 210 per minute: 6000 + 110 n is above 12000 for n >= 55: 6 up and 4 down reach the 10th check at j = 64
    assert burst(210).lines("S1T_SIDE_FEED_STOP")[0] == (64, "S1T_SIDE_FEED_STOP series=G inc=12160.00 T=12000.00")


def test_stale_samples_do_not_hide_a_rise():
    sim = warm()
    sim.step()
    for i in range(200):
        sim.step(g_rate=300, stale=(i % 7 == 6))  # every 7th sample is STALE: not a completed check, but the run of checks above T is kept
    assert sim.lines("S1T_SIDE_FEED_STOP")


# ---- restart ----------------------------------------------------------------------------------------------------------------------------------
def test_a_restart_is_not_a_stop_and_not_a_new_baseline():
    sim = warm()
    for _ in range(30):
        sim.step()
    sim.step(g_rate=300, pid=2, reset=True)  # m = 30: a new process
    for _ in range(100):
        sim.step(g_rate=300)
    assert sim.lines("S1T_SIDE_FEED_FEED_RESTART") == [(30, "S1T_SIDE_FEED_FEED_RESTART")]
    assert len(sim.lines("S1T_SIDE_FEED_BASELINE")) == 1  # never recomputed
    # no check across the pid change: the first one is at m = 90 (a sample of the new pid is 60 min old), above T on 10 consecutive checks to m = 99
    assert sim.lines("S1T_SIDE_FEED_STOP")[0] == (99, "S1T_SIDE_FEED_STOP series=G inc=18000.00 T=12000.00")
    assert not sim.lines("S1T_SIDE_FEED_BLIND")


# ---- W5: blind --------------------------------------------------------------------------------------------------------------------------------
def test_stale_for_90_minutes_is_blind():
    sim = warm()
    sim.step()  # m = 0: the last completed check
    for i in range(90):  # m = 1..90: STALE and UNREADABLE samples only, no completed check
        sim.step(stale=(i % 2 == 0), none=(i % 2 == 1))
    assert sim.lines("S1T_SIDE_FEED_BLIND") == [(90, "S1T_SIDE_FEED_BLIND reason=no_completed_check_90min")]
    assert not sim.lines("S1T_SIDE_FEED_STOP")


def test_stale_for_89_minutes_is_not_blind():
    sim = warm()
    sim.step()  # m = 0: the last completed check
    for i in range(89):  # m = 1..89: 89 bad samples, more than the old 10-sample rule, but under 90 min
        sim.step(stale=(i % 2 == 0), none=(i % 2 == 1))
    assert not sim.lines("S1T_SIDE_FEED_BLIND")
    sim.step()  # m = 90: a good sample completes a check before the 90-minute test
    assert not sim.lines("S1T_SIDE_FEED_BLIND")


def test_a_good_sample_resets_the_stale_count():
    sim = warm()
    sim.step()
    for _ in range(9):
        sim.step(stale=True)
    sim.step()
    for _ in range(9):
        sim.step(stale=True)
    assert not sim.lines("S1T_SIDE_FEED_BLIND")


def test_no_completed_check_for_90_minutes_is_blind():
    sim = warm()
    sim.step()  # m = 0: the last completed check
    for j in range(1, 100):
        pid = 2 + (j - 1) // 20  # a restart every 20 minutes: never 60 minutes of one pid
        sim.step(pid=pid, reset=True)
        if sim.lines("S1T_SIDE_FEED_BLIND"):
            break
    assert sim.lines("S1T_SIDE_FEED_BLIND") == [(90, "S1T_SIDE_FEED_BLIND reason=no_completed_check_90min")]
    assert not sim.lines("S1T_SIDE_FEED_STOP")


# ---- run_watch exit codes ---------------------------------------------------------------------------------------------------------------------
def drive(rate, start_min=0, none_from=None, seconds=None):
    """run_watch over a synthetic feed; one loop per minute. rate(t) is the per-minute G rate at minute t since S - 24 h."""
    st_ = {"t": start_min}
    out = []

    def counters(t):
        return 1000 + sum(rate(i) for i in range(t + 1)), 500 + 50 * (t + 1)

    def read():
        if none_from is not None and st_["t"] >= none_from:
            return None
        g, r = counters(st_["t"])
        return status(g, r, 1, S - 24 * H + st_["t"] * MIN)

    rc = aw.run_watch(read, seconds, 60.0, S, clock=lambda: st_["t"] * 60.0, sleep=lambda s: st_.update(t=st_["t"] + 1),
                      wall_ms=lambda: S - 24 * H + st_["t"] * MIN, emit=out.append)
    return rc, out


def test_run_watch_exit_codes():
    rc, out = drive(lambda t: 300 if t > 1440 else 100)
    assert rc == aw.EXIT_STOP == 4 and out[0] == BASELINE and out[-1].startswith("S1T_SIDE_FEED_STOP series=G")
    rc, out = drive(lambda t: 100, none_from=1442)
    assert rc == aw.EXIT_BLIND == 5 and out[-1].startswith("S1T_SIDE_FEED_BLIND")
    rc, out = drive(lambda t: 100, start_min=720)
    assert rc == aw.EXIT_NO_BASELINE == 6 and out == ["S1T_SIDE_FEED_NO_BASELINE"]
    rc, out = drive(lambda t: 100, seconds=300)
    assert rc == 0 and out == ["S1T_SIDE_FEED_OK"]


# ---- inputs and the path ----------------------------------------------------------------------------------------------------------------------
def test_extract_shapes():
    base = {"updated_ms": 1, "started_ms": 0, "pid": 7}
    ex = aw.extract({**base, "gaps": [1, 2, 3], "sockets": {"a": [{"reconnects": 2}, {"reconnects": 3}], "b": {"x": {"reconnects": 4}}, "c": []}})
    assert ex["G"] == 3 and ex["R"] == 9
    assert aw.extract({**base, "gaps": {"k": 1}, "sockets": {}})["G"] == 1
    assert aw.extract({**base, "gaps": 12, "sockets": {"a": [{"reconnects": "x"}, {}, 5]}})["R"] == 0
    assert aw.extract({**base, "gaps": "n", "sockets": {}}) is None and aw.extract([1]) is None
    w = aw.Watcher(S)
    assert w.step(None, S - 24 * H) == ["UNREADABLE"] and w.step({"updated_ms": 1}, S - 24 * H + MIN) == ["UNREADABLE"]


@pytest.mark.parametrize("name", ["other.json", "tape-2026-11-01T00.jsonl", "arena-feed-status.json.bak", "x-arena-feed-status.json", "status.json"])
def test_status_path_refusal(tmp_path, capsys, name):
    p = tmp_path / name
    p.write_text("{}")
    assert aw.main(["--status", str(p), "--smoke-start-ms", str(S), "--seconds", "0"]) == 2
    cap = capsys.readouterr()
    assert cap.out == "" and "refused" in cap.err


def test_smoke_start_is_required():
    with pytest.raises(SystemExit) as e:
        aw.main(["--seconds", "0"])
    assert e.value.code == 2


def test_main_reads_only_the_named_file(tmp_path, capsys):
    now = int(time.time() * 1000)
    p = tmp_path / "arena-feed-status.json"
    p.write_text(json.dumps(status(100, 40, 1, now)))
    (tmp_path / "tape-2026-11-01T00.jsonl").write_text("DO NOT OPEN\n")
    assert aw.main(["--status", str(p), "--smoke-start-ms", str(now + 30 * H), "--seconds", "0", "--interval-s", "0.01"]) == 0
    assert capsys.readouterr().out.strip() == "S1T_SIDE_FEED_OK"
    p.unlink()
    assert aw.main(["--status", str(p), "--smoke-start-ms", str(now + 30 * H), "--seconds", "0", "--interval-s", "0.01"]) == 0
    assert capsys.readouterr().out.splitlines()[0] == "UNREADABLE"


def test_source_never_lists_a_directory_or_names_a_tape_file():
    src = Path(aw.__file__).read_text()
    code = src.split('"""', 2)[2]
    assert not re.search(r"\b(listdir|scandir|glob|iterdir|walk|rglob)\b", code) and "tape-" not in code
