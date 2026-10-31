"""scripts/research/r4b_dry_sidecar.py: the projection / alert logic of the r4-b long DRY run's sidecar (QP #1160 M4 and M6; pc3 P5), on synthetic samples.
No network, no /data/mal, no tape, no systemctl: the cgroup tree, status.json and `systemctl` are fakes in a temp dir.

    /data/mal/venv/bin/python -m pytest tools/test_r4b_dry_sidecar.py
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("r4b_dry_sidecar", REPO / "scripts" / "research" / "r4b_dry_sidecar.py")
sc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sc)

MIB, H = sc.MIB, 3600.0
T0 = 1_793_460_000.0  # an arbitrary instant; only differences matter
CU = T0 + 3000.0      # the catch-up end


def smp(t, anon_mib=1400.0, **kw):
    s = {"ts": t, "utc": "x", "scope_found": True, "mem_anon": anon_mib * MIB, "mem_current": anon_mib * MIB, "ev_oom": 0, "ev_oom_kill": 0, "ev_max": 0,
         "lag_slots": 3, "backlog_jumps": 2, "gaps": 76046, "follower_nrestarts": 0, "follower_active_enter": "Sat 2026-10-24 06:03:16 UTC"}
    s.update(kw)
    return s


def live_monitor(slope_mib_h, **kw):
    """A monitor in the live phase that has seen samples every 60 s from catch-up end + 1 h to `n` hours, at a constant slope."""
    m = sc.Monitor(catchup_end=CU, scope_seen=True, **kw)
    return m


def feed_ramp(m, hours_after_cu, slope_mib_h, base_mib=1371.0, step=60.0, start_h=1.0):
    """Feed samples every `step` s from CU + start_h h to CU + hours_after_cu h; returns (last sample, all alerts)."""
    t, alerts, last = CU + start_h * H, [], None
    while t <= CU + hours_after_cu * H + 1e-6:
        a = base_mib + slope_mib_h * ((t - CU) / H - 2.0)  # 1,371 MiB at catch-up end + 2 h, as in pc3 section 2.6
        last = smp(t, a)
        al = m.feed(last)
        alerts += al
        if al:
            break
        t += step
    return last, alerts


class Parsers(unittest.TestCase):
    def test_memory_stat_and_events(self):
        st = sc.parse_kv_lines("anon 1280561152\nfile 8041750528\nkernel 1\n")
        self.assertEqual((st["anon"], st["file"]), (1280561152, 8041750528))
        ev = sc.parse_kv_lines("low 0\nhigh 0\nmax 3\noom 1\noom_kill 1\noom_group_kill 0\n")
        self.assertEqual((ev["max"], ev["oom"], ev["oom_kill"]), (3, 1, 1))
        self.assertEqual(sc.parse_kv_lines(None), {})
        self.assertEqual(sc.parse_kv_lines("garbage\nanon x\n"), {})

    def test_show_and_psi(self):
        self.assertEqual(sc.parse_show("NRestarts=0\nActiveEnterTimestamp=Sat 2026-10-24 06:03:16 UTC\n"),
                         {"NRestarts": "0", "ActiveEnterTimestamp": "Sat 2026-10-24 06:03:16 UTC"})
        self.assertEqual(sc.parse_psi("some avg10=0.12 avg60=0.5 avg300=0.1 total=1\nfull avg10=0.03 avg60=0 avg300=0 total=0\n"), {"some": 0.12, "full": 0.03})

    def test_fit_projection_exact_line(self):
        pts = [(1000.0 + 60 * i, 5.0 + 0.01 * (60 * i)) for i in range(100)]
        slope, y = sc.fit_projection(pts, 1000.0 + 10_000)
        self.assertAlmostEqual(slope, 0.01, places=9)
        self.assertAlmostEqual(y, 5.0 + 0.01 * 10_000, places=6)
        self.assertIsNone(sc.fit_projection(pts[:1], 5.0))
        self.assertIsNone(sc.fit_projection([(1.0, 1.0), (1.0, 2.0)], 9.0))


class Memory(unittest.TestCase):
    def test_pc3_slopes_do_not_alert(self):
        # pc3 section 2.6: 6.5 and 11.9 MiB/h from 1,371 MiB; projection at +116 h is 2,112 and 2,728 MiB, both under 2,975
        for slope, lo, hi in ((6.5, 2100, 2125), (11.9, 2715, 2740)):
            m = live_monitor(slope, run_end=CU + 200 * H)
            last, alerts = feed_ramp(m, 12.0, slope)
            self.assertEqual(alerts, [], slope)
            self.assertTrue(lo < last["anon_proj_mib"] < hi, (slope, last["anon_proj_mib"]))
            self.assertAlmostEqual(last["anon_slope_mib_h"], slope, places=2)
            self.assertAlmostEqual(last["anon_fit_span_h"], 11.0, places=1)  # exactly M6's [+1 h, +12 h] at +12 h

    def test_steep_slope_alerts_once_the_span_reaches_6_h(self):
        m = live_monitor(0, run_end=CU + 200 * H)
        last, alerts = feed_ramp(m, 12.0, 20.0)  # 1,371 + 20 * 114 = 3,651 MiB at the horizon
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0][0], sc.EXIT_ALERT)
        self.assertIn("m6_projection", alerts[0][1])
        # the first sample whose fit window spans 6 h: CU + 1 h + 6 h
        self.assertAlmostEqual((last["ts"] - CU) / H, 7.0, places=1)
        self.assertGreater(last["anon_proj_mib"], 2975)

    def test_no_projection_and_no_alert_before_6_h_of_samples(self):
        m = live_monitor(0, run_end=CU + 200 * H)
        last, alerts = feed_ramp(m, 6.9, 40.0)  # a very steep ramp, but anon stays under 2,975 and the window is < 6 h
        self.assertEqual(alerts, [])
        self.assertNotIn("anon_proj_mib", last)

    def test_the_first_hour_after_catchup_is_not_fitted(self):
        m = live_monitor(0, run_end=CU + 200 * H)
        alerts = []
        for t in range(int(CU), int(CU + H), 60):  # a steep warm-up inside the first hour (200 to 1,364 MiB): ignored by the fit
            alerts += m.feed(smp(float(t), 200.0 + (1364.5 - 200.0) * (t - CU) / H))
        last, a2 = feed_ramp(m, 12.0, 6.5, start_h=1.0)
        self.assertEqual(alerts + a2, [])
        self.assertAlmostEqual(last["anon_slope_mib_h"], 6.5, places=2)

    def test_horizon_is_the_earlier_of_run_end_and_catchup_plus_116_h(self):
        m = sc.Monitor(catchup_end=CU, scope_seen=True, run_end=CU + 20 * H)
        self.assertEqual(m.horizon(CU + 12 * H), CU + 20 * H)
        m2 = sc.Monitor(catchup_end=CU, scope_seen=True, run_end=CU + 500 * H)
        self.assertEqual(m2.horizon(CU + 12 * H), CU + 116 * H)
        self.assertEqual(sc.Monitor(catchup_end=CU, run_end=CU + 20 * H).horizon(CU + 30 * H), CU + 30 * H)  # past the horizon: the fit at now
        # a steep ramp that would alert at +116 h does not alert when the run ends at +14 h
        m3 = live_monitor(0, run_end=CU + 14 * H)
        last, alerts = feed_ramp(m3, 12.0, 20.0)
        self.assertEqual(alerts, [])
        self.assertLess(last["anon_proj_mib"], 2975)

    def test_anon_at_threshold_alerts_at_once_without_a_fit(self):
        m = live_monitor(0, run_end=CU + 200 * H)
        al = m.feed(smp(CU + 2 * H, 2975.0))
        self.assertEqual([c for c, _ in al], [sc.EXIT_ALERT])
        self.assertIn("anon_at_threshold", al[0][1])
        self.assertEqual(live_monitor(0).feed(smp(CU + 2 * H, 2974.9)), [])

    def test_oom_and_oom_kill(self):
        m = live_monitor(0)
        al = m.feed(smp(CU + 2 * H, 1400.0, ev_oom_kill=1))
        self.assertEqual(al[0][0], sc.EXIT_ALERT)
        self.assertIn("oom", al[0][1])
        al = live_monitor(0).feed(smp(CU + 2 * H, 1400.0, ev_oom=2))
        self.assertEqual(al[0][0], sc.EXIT_ALERT)
        self.assertEqual(live_monitor(0).feed(smp(CU + 2 * H, 1400.0, ev_max=9)), [])  # memory.events max is recorded, not an alert


class Follower(unittest.TestCase):
    def pre(self, m, lags):
        for i, lag in enumerate(lags):
            self.assertEqual(m.feed(smp(T0 + 30 * i, scope_found=False, mem_anon=None, lag_slots=lag)), [])

    def test_new_backlog_jump_alerts_after_catchup_with_code_3(self):
        m = live_monitor(0)
        self.assertEqual(m.feed(smp(CU + 2 * H)), [])
        al = m.feed(smp(CU + 2 * H + 60, backlog_jumps=3))
        self.assertEqual([c for c, _ in al], [sc.EXIT_ALERT])
        self.assertIn("new_backlog_jump", al[0][1])

    def test_new_backlog_jump_before_the_scope_is_code_3_and_during_catchup_code_4(self):
        m = sc.Monitor()
        self.assertEqual(m.feed(smp(T0, scope_found=False, mem_anon=None)), [])
        al = m.feed(smp(T0 + 30, scope_found=False, mem_anon=None, backlog_jumps=3))
        self.assertEqual(al[0][0], sc.EXIT_ALERT)
        m2 = sc.Monitor()
        self.assertEqual(m2.feed(smp(T0, scope_found=False, mem_anon=None)), [])
        self.assertEqual(m2.feed(smp(T0 + 30)), [])  # scope appears: catch-up
        al = m2.feed(smp(T0 + 60, backlog_jumps=3))
        self.assertEqual(al[0][0], sc.EXIT_CATCHUP)
        self.assertIn("catch-up stop rule", al[0][1])

    def test_gaps_increase_is_a_catchup_stop_but_only_recorded_after_catchup(self):
        m = sc.Monitor(scope_seen=True)
        m.feed(smp(T0))
        al = m.feed(smp(T0 + 30, gaps=76047))
        self.assertEqual(al[0][0], sc.EXIT_CATCHUP)
        live = live_monitor(0)
        live.feed(smp(CU + 2 * H))
        self.assertEqual(live.feed(smp(CU + 2 * H + 60, gaps=76047)), [])

    def test_follower_restart_by_nrestarts_or_active_enter(self):
        m = live_monitor(0)
        m.feed(smp(CU + 2 * H))
        al = m.feed(smp(CU + 2 * H + 60, follower_nrestarts=1))
        self.assertEqual((al[0][0], "follower_restart" in al[0][1]), (sc.EXIT_ALERT, True))
        m = live_monitor(0)
        m.feed(smp(CU + 2 * H))
        al = m.feed(smp(CU + 2 * H + 60, follower_active_enter="Sat 2026-10-31 18:00:00 UTC"))  # a manual restart leaves NRestarts at 0
        self.assertEqual((al[0][0], "follower_restart" in al[0][1]), (sc.EXIT_ALERT, True))

    def test_catchup_lag_rule_needs_two_consecutive_samples_outside_the_range(self):
        m = sc.Monitor()
        self.pre(m, [1, 2, 3, 2, 4, 3])  # pre-start max 4: the range edge is the 10-slot floor (pc3 section 2.2: lag 3 to 10 is calm)
        self.assertEqual(m.pre_lag_max, 4)
        self.assertEqual(m.lag_edge(), 10)
        t = T0 + 600
        self.assertEqual(m.feed(smp(t)), [])               # scope appears
        self.assertEqual(m.feed(smp(t + 30, lag_slots=10)), [])  # on the edge: in range
        self.assertEqual(m.feed(smp(t + 60, lag_slots=11)), [])  # one sample out: no stop
        self.assertEqual(m.feed(smp(t + 90, lag_slots=3)), [])   # back in range: the run resets
        self.assertEqual(m.feed(smp(t + 120, lag_slots=11)), [])
        al = m.feed(smp(t + 150, lag_slots=12))                  # two in a row
        self.assertEqual(al[0][0], sc.EXIT_CATCHUP)
        self.assertIn("catchup_lag_out_of_range", al[0][1])

    def test_pre_start_max_above_the_floor_moves_the_edge(self):
        m = sc.Monitor()
        self.pre(m, [2, 14, 3])
        self.assertEqual(m.lag_edge(), 14)
        t = T0 + 600
        m.feed(smp(t))
        self.assertEqual(m.feed(smp(t + 30, lag_slots=14)), [])
        self.assertEqual(m.feed(smp(t + 60, lag_slots=15)), [])
        self.assertEqual(m.feed(smp(t + 90, lag_slots=14)), [])

    def test_hard_line_20_in_two_consecutive_samples_even_if_the_pre_range_was_wide(self):
        m = sc.Monitor()
        self.pre(m, [2, 30, 3])  # an odd pre-start max of 30: the edge would be 30, but the pre-registered line (lag >= 20 twice) still stops
        m.feed(smp(T0 + 600))
        self.assertEqual(m.feed(smp(T0 + 630, lag_slots=20)), [])
        al = m.feed(smp(T0 + 660, lag_slots=21))
        self.assertEqual(al[0][0], sc.EXIT_CATCHUP)

    def test_a_lag_spike_after_catchup_is_not_an_alert(self):
        m = live_monitor(0)
        m.feed(smp(CU + 2 * H))
        for i in range(5):  # the 14:10Z spike of the smoke (lag 76): recorded, not a stop
            self.assertEqual(m.feed(smp(CU + 2 * H + 60 * (i + 1), lag_slots=76)), [])

    def test_missing_status_fields_never_alert(self):
        m = sc.Monitor()
        s = smp(T0, scope_found=False, mem_anon=None, lag_slots=None, backlog_jumps=None, gaps=None, follower_nrestarts=None, follower_active_enter=None)
        self.assertEqual(m.feed(s), [])
        self.assertEqual(s["phase"], "pre")


class Phases(unittest.TestCase):
    def test_phase_follows_the_scope_and_the_catchup_end(self):
        m = sc.Monitor()
        s = smp(T0, scope_found=False, mem_anon=None)
        m.feed(s)
        self.assertEqual(s["phase"], "pre")
        s = smp(T0 + 30)
        m.feed(s)
        self.assertEqual(s["phase"], "catchup")
        m.catchup_end = T0 + 100
        s = smp(T0 + 90)
        m.feed(s)
        self.assertEqual(s["phase"], "catchup")
        s = smp(T0 + 100)
        m.feed(s)
        self.assertEqual(s["phase"], "live")


class Files(unittest.TestCase):
    def test_events_scan_takes_only_the_catch_up_done_ms(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d, "events.jsonl")
            sc_ = sc.EventsScan(str(p))
            self.assertIsNone(sc_.poll())  # no file yet
            p.write_text(json.dumps({"event": "start", "ms": 1}) + "\n" + json.dumps({"event": "other", "note": "catch_up_done is a string here", "ms": 5}) + "\n")
            self.assertIsNone(sc_.poll())
            with open(p, "a") as f:
                f.write('{"event": "catch_up_do')  # an unfinished line waits
            self.assertIsNone(sc_.poll())
            with open(p, "a") as f:
                f.write('ne", "ms": 1793451234567, "released_D": 5}\n')
            self.assertEqual(sc_.poll(), 1793451234.567)
            self.assertEqual(sc_.poll(), 1793451234.567)  # latched

    def test_load_history_rebuilds_from_the_sidecars_own_lines(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d, "sidecar.jsonl")
            rows = [{"ts": 1.0, "scope_found": False, "phase": "pre", "lag_slots": 2}, {"ts": 2.0, "scope_found": False, "phase": "pre", "lag_slots": 5},
                    {"ts": 3.0, "scope_found": True, "phase": "catchup", "mem_anon": 100, "lag_slots": 40},
                    {"ts": 4.0, "scope_found": True, "phase": "live", "mem_anon": 200, "catchup_end_ts": 3.5}]
            p.write_text("\n".join(json.dumps(r) for r in rows) + "\nnot json\n")
            h = sc.load_history(str(p))
            self.assertEqual(h["history"], [(3.0, 100.0), (4.0, 200.0)])
            self.assertEqual((h["pre_lag_max"], h["catchup_end"], h["scope_seen"], h["scope_first"]), (5, 3.5, True, 3.0))
            self.assertEqual(sc.load_history(str(Path(d, "none")))["history"], [])


class Loop(unittest.TestCase):
    """run() on a fake cgroup tree, a fake status.json and a fake systemctl, with a fake clock."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.cg = self.root / "cgroup" / "user.slice" / "r4b-dry-x1.scope"
        self.cg.mkdir(parents=True)
        self.state = self.root / "state"
        self.state.mkdir()
        self.now = 1_793_460_000.0
        self.n_restarts = "0"
        self.control_group = ""
        self.write_scope(anon=1_300_000_000, oom_kill=0)
        self.write_status()

    def tearDown(self):
        self.tmp.cleanup()

    def write_scope(self, anon, oom_kill):
        (self.cg / "memory.current").write_text(f"{anon + 5_000_000}\n")
        (self.cg / "memory.stat").write_text(f"anon {anon}\nfile 8000000000\nkernel 1\n")
        (self.cg / "memory.events").write_text(f"low 0\nhigh 0\nmax 0\noom 0\noom_kill {oom_kill}\n")

    def write_status(self, **kw):
        st = {"ts_ms": int(self.now * 1000) - 2000, "lag_slots": 3, "lag_ms": 450, "backlog_jumps": 2, "gaps": 76046, "skipped": 9, "block_lag_ms_p50": 900, "block_lag_ms_p90": 1800,
              "fetch_ms_p50": 80, "fetch_ms_p90": 200, "decoder_blobs_pinned": True, "trade_event_v": True, "last_done_slot": 463714222}
        st.update(kw)
        (self.state / "status.json").write_text(json.dumps(st))

    def systemctl(self, args):
        if "--user" in args:
            return f"ControlGroup={self.control_group}\n"
        return f"NRestarts={self.n_restarts}\nActiveEnterTimestamp=Sat 2026-10-24 06:03:16 UTC\n"

    def sleep(self, s):
        self.now += s

    def argv(self, **extra):
        a = ["--change-id", "x1", "--out", str(self.root / "sc.jsonl"), "--status-dir", str(self.state), "--cgroup-root", str(self.root / "cgroup"), "--events", "",
             "--hours", "0.02"]
        for k, v in extra.items():
            a += [f"--{k.replace('_', '-')}", str(v)]
        return a

    def go(self, argv=None):
        buf = io.StringIO()
        rc = sc.run(argv or self.argv(), clock=lambda: self.now, sleep=self.sleep, systemctl=self.systemctl, out=buf)
        lines = [json.loads(x) for x in (self.root / "sc.jsonl").read_text().splitlines()]
        return rc, buf.getvalue(), lines

    def test_samples_carry_the_m4_fields_and_the_run_ends_at_hours(self):
        self.control_group = "/user.slice/r4b-dry-x1.scope"
        rc, out, rows = self.go()
        self.assertEqual(rc, 0)
        self.assertIn("END", out)
        self.assertGreaterEqual(len(rows), 2)
        r = rows[0]
        for k in ("mem_current", "mem_anon", "mem_file", "ev_max", "ev_oom", "ev_oom_kill", "lag_slots", "lag_ms", "backlog_jumps", "gaps", "block_lag_ms_p50", "block_lag_ms_p90",
                  "decoder_blobs_pinned", "trade_event_v", "last_done_slot", "load1", "follower_nrestarts", "follower_active_enter", "skipped", "fetch_ms_p50", "fetch_ms_p90",
                  "psi_cpu_some_avg10", "psi_io_some_avg10", "psi_io_full_avg10", "status_age_s", "phase", "scope_found", "ts", "utc"):
            self.assertIn(k, r, k)
        self.assertEqual((r["mem_anon"], r["mem_file"], r["ev_oom_kill"], r["scope_found"]), (1_300_000_000, 8_000_000_000, 0, True))
        self.assertEqual((r["lag_slots"], r["backlog_jumps"], r["follower_nrestarts"], r["phase"]), (3, 2, 0, "catchup"))
        self.assertEqual(r["status_age_s"], 2.0)
        for a, b in zip(rows, rows[1:]):
            self.assertLessEqual(b["ts"] - a["ts"], 60.0)  # every <= 60 s
            self.assertGreater(b["ts"], a["ts"])

    def test_oom_kill_is_an_alert_with_exit_3(self):
        self.control_group = "/user.slice/r4b-dry-x1.scope"
        self.write_scope(anon=1_300_000_000, oom_kill=1)
        rc, out, rows = self.go()
        self.assertEqual(rc, sc.EXIT_ALERT)
        self.assertIn("ALERT", out)
        self.assertIn("oom", out)
        self.assertEqual(len(rows), 1)

    def test_a_follower_restart_midway_is_an_alert_with_exit_3(self):
        self.control_group = "/user.slice/r4b-dry-x1.scope"
        calls = {"n": 0}
        base_sleep = self.sleep

        def sleep(s):
            calls["n"] += 1
            if calls["n"] == 2:
                self.n_restarts = "1"
            base_sleep(s)
        buf = io.StringIO()
        rc = sc.run(self.argv(hours=1), clock=lambda: self.now, sleep=sleep, systemctl=self.systemctl, out=buf)
        self.assertEqual(rc, sc.EXIT_ALERT)
        self.assertIn("follower_restart", buf.getvalue())

    def test_scope_never_started_exits_6(self):
        self.control_group = ""  # systemctl knows no such scope
        rc, out, rows = self.go(self.argv(hours=3))
        self.assertEqual(rc, sc.EXIT_NO_SCOPE)
        self.assertIn("scope_never_started", out)
        self.assertTrue(all(r["scope_found"] is False and r["phase"] == "pre" for r in rows))

    def test_scope_gone_after_it_was_seen_ends_with_exit_0(self):
        self.control_group = "/user.slice/r4b-dry-x1.scope"
        calls = {"n": 0}
        base_sleep = self.sleep

        def sleep(s):
            calls["n"] += 1
            if calls["n"] == 2:
                for f in self.cg.iterdir():
                    f.unlink()
                self.cg.rmdir()
            base_sleep(s)
        buf = io.StringIO()
        rc = sc.run(self.argv(hours=1), clock=lambda: self.now, sleep=sleep, systemctl=self.systemctl, out=buf)
        self.assertEqual(rc, 0)
        self.assertIn("scope_gone", buf.getvalue())

    def test_catchup_end_from_the_events_file_and_cadence(self):
        self.control_group = "/user.slice/r4b-dry-x1.scope"
        ev = self.root / "events.jsonl"
        ev.write_text(json.dumps({"event": "catch_up_done", "ms": int((self.now + 40) * 1000)}) + "\n")
        rc, out, rows = self.go(self.argv(events=ev, hours=0.05))
        self.assertEqual(rc, 0)
        self.assertEqual(rows[-1]["catchup_end_ts"], 1_793_460_040.0)
        self.assertTrue(any(r["phase"] == "live" for r in rows))
        self.assertTrue(any(r["phase"] == "catchup" for r in rows))
        self.assertTrue(all(b["ts"] - a["ts"] <= 31.0 for a, b in zip(rows, rows[1:])))  # 30 s cadence until catch-up end + 2 h

    def test_restart_reads_its_own_history_back(self):
        self.control_group = "/user.slice/r4b-dry-x1.scope"
        self.go(self.argv(hours=0.01))
        n1 = len((self.root / "sc.jsonl").read_text().splitlines())
        rc, out, rows = self.go(self.argv(hours=0.01))
        self.assertIn(f"history_samples={n1}", out)
        self.assertGreater(len(rows), n1)


if __name__ == "__main__":
    unittest.main()
