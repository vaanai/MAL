"""tools/q50s1_health.py: the s1 smoke blocking list, the clean-log check and the dry-score structure filter, on fixtures only. No shadow output is read."""
from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from tools import q50s1_health as h

S_SHA = "5c2ab7c64b21358619e37068c6a53e4d183512ea"
T0 = 1793700000  # 2026-11-03T08:40:00Z, a smoke-day instant (fixtures only)


def row(d: dict, t_ms: int = T0 * 1000) -> str:
    """As h5_shadow's Engine.emit: v, schema and t_ms are set by setdefault AFTER the record's own keys (so t_ms is last unless the record has one)."""
    d = dict(d)
    d.setdefault("v", 1)
    d.setdefault("schema", "h5_shadow_v1")
    d.setdefault("t_ms", t_ms)
    return json.dumps(d, separators=(",", ":"), allow_nan=False) + "\n"


def start_row(**over) -> dict:
    r = {"type": "start", "rule": "H5-BOOSTFLOOR-Q50 v1", "rule_sha256": "b3f8ecbf8731dc322a035a0b3996b7ed3f0b8c508cc6947014e3de1aa9524fd4", "code_sha": S_SHA,
         "argv": ["--fresh-forward", "ARENA"], "pid": 7, "bx10": False, "t_ms": T0 * 1000, "start_ms": T0 * 1000, "fresh_forward": "ARENA",
         "h5_look2": {"end_only_ms": 1796083200000}, "tip_union": "off", "dirty": False, "q_star_sol": 50.0}
    r.update(over)
    return r


def hour_name(epoch_s: int) -> str:
    import datetime as dt
    return "h5-shadow-" + dt.datetime.fromtimestamp(epoch_s, tz=dt.timezone.utc).strftime("%Y-%m-%dT%H") + ".jsonl"


def run(argv) -> tuple[int, str]:
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = h.main(argv)
    return rc, buf.getvalue()


class Fixture(unittest.TestCase):
    def setUp(self):
        self._d = tempfile.TemporaryDirectory()
        self.addCleanup(self._d.cleanup)
        self.d = Path(self._d.name)
        self.out = self.d / "h5-smoke-q50s1-x"
        self.out.mkdir()
        self.log = self.d / "process.log"
        self.log.write_text("")
        self.rss = self.d / "rss.tsv"

    def write_rows(self, rows_by_epoch: list[tuple[int, dict]]):
        files: dict[str, list[str]] = {}
        for ep, r in rows_by_epoch:
            files.setdefault(hour_name(ep), []).append(row(r, ep * 1000))
        for name, lines in files.items():
            with open(self.out / name, "a") as fh:
                fh.writelines(lines)

    def healthy(self, dur=25200, rss_bytes=lambda t: 400_000_000, gaps=(), extra=()):
        rows = [(T0, start_row())]
        rows += [(T0 + i * 60, {"type": "hb", "counters": {"triggers": 3}, "open_pools": 2}) for i in range(1, dur // 60 + 1)]
        rows += [(T0 + 100, {"type": "trigger", "variant": "pv", "pool": "POOLX", "mint": "MINTX"}), (T0 + 500, {"type": "outcome", "variant": "pv", "pool": "POOLX", "pnl": 1.0})]
        rows += list(gaps) + list(extra)
        rows += [(T0 + dur + 1, {"type": "stop", "counters": {"x": 1}})]  # the SIGTERM stop row (QP C3)
        self.write_rows(rows)
        self.rss.write_text("".join(f"{T0 + i * 60}\t{rss_bytes(i * 60)}\t7\n" for i in range(dur // 60 + 1)))
        return dur

    def judge(self, dur, alive="yes", extra=(), journal="0"):
        return run(["smoke-judge", "--out-dir", str(self.out), "--proc-log", str(self.log), "--rss", str(self.rss), "--start-epoch", str(T0),
                    "--end-epoch", str(T0 + dur), "--alive-at-end", alive, "--journal-key-count", journal, *extra])


class SmokeJudge(Fixture):
    def test_healthy_smoke_passes_and_prints_no_row_content(self):
        dur = self.healthy()
        rc, out = self.judge(dur)
        self.assertEqual(rc, 0, out)
        self.assertIn("SMOKE_VERDICT PASS failed=[]", out)
        for item in ("duration PASS", "a PASS", "b PASS", "c PASS", "d PASS", "g PASS e_gap=0.000000", "k PASS"):
            self.assertIn(f"SMOKE_ITEM {item}", out)
        for leak in ("POOLX", "MINTX", "pnl", "triggers", "trigger", "outcome", "open_pools"):
            self.assertNotIn(leak, out)

    def test_short_smoke_fails_duration(self):
        dur = self.healthy(dur=21599)
        rc, out = self.judge(dur)
        self.assertEqual(rc, 1)
        self.assertIn("SMOKE_ITEM duration FAIL", out)

    def test_start_row_fields_are_checked(self):
        for bad in (dict(code_sha="a5f413704eb120c8f529101a6186e3a95de7f83e"), dict(q_star_sol=40.0), dict(dirty=True), dict(tip_union="on"),
                    dict(h5_look2={"end_only_ms": 1793923200000}), dict(fresh_forward=None), dict(bx10=True)):
            with self.subTest(bad=bad):
                for p in self.out.iterdir():
                    p.unlink()
                rows = [(T0, start_row(**bad))] + [(T0 + i * 60, {"type": "hb"}) for i in range(1, 421)]
                self.write_rows(rows)
                self.rss.write_text("".join(f"{T0 + i * 60}\t400000000\t7\n" for i in range(421)))
                rc, out = self.judge(25200)
                self.assertEqual(rc, 1)
                self.assertIn("SMOKE_ITEM a FAIL", out)

    def test_two_start_rows_is_a_restart(self):
        dur = self.healthy(extra=[(T0 + 4000, start_row())])
        rc, out = self.judge(dur)
        self.assertIn("SMOKE_ITEM a FAIL start_rows=2", out)

    def test_missing_heartbeats_fail_b(self):
        self.write_rows([(T0, start_row())] + [(T0 + i * 60, {"type": "hb"}) for i in range(1, 60)])
        self.rss.write_text("".join(f"{T0 + i * 60}\t400000000\t7\n" for i in range(421)))
        rc, out = self.judge(25200)
        self.assertIn("SMOKE_ITEM b FAIL hb_rows=59", out)

    def test_c_traceback_dead_process_pid_change_and_error_rows(self):
        cases = {
            "traceback": lambda: self.log.write_text("Traceback (most recent call last):\n  File x\n"),
            "error_line": lambda: self.log.write_text("2026-11-03 09:00:00,000 ERROR mal.h5_shadow boom\n"),
            "dead": None,
            "pid": lambda: self.rss.write_text("".join(f"{T0 + i * 60}\t400000000\t{7 if i < 80 else 8}\n" for i in range(421))),
            "gone": lambda: self.rss.write_text("".join(f"{T0 + i * 60}\t{'-' if i == 90 else 400000000}\t{'-' if i == 90 else 7}\n" for i in range(421))),
            "error_row": lambda: self.write_rows([(T0 + 300, {"type": "error", "where": "decode", "exc": "KeyError"})]),
            "errors_log": lambda: (self.out / "h5-shadow-errors.log").write_text('{"exc":"X"}\nTraceback (most recent call last):\n'),
        }
        for name, act in cases.items():
            with self.subTest(name=name):
                for p in self.out.iterdir():
                    p.unlink()
                self.log.write_text("")
                dur = self.healthy()
                if act:
                    act()
                rc, out = self.judge(dur, alive="no" if name == "dead" else "yes")
                self.assertEqual(rc, 1, out)
                self.assertIn("SMOKE_ITEM c FAIL", out)

    def test_c_requires_exactly_one_stop_row(self):
        self.healthy()
        for p in self.out.iterdir():
            p.write_text("".join(ln for ln in p.read_text().splitlines(keepends=True) if '"type":"stop"' not in ln))
        rc, out = self.judge(25200)
        self.assertIn("SMOKE_ITEM c FAIL", out)
        self.assertIn("stop_rows_not_one", out)
        self.write_rows([(T0 + 25300, {"type": "stop"}), (T0 + 25301, {"type": "stop"})])
        rc, out = self.judge(25200)
        self.assertIn("stop_rows=2", out)
        self.assertIn("SMOKE_ITEM c FAIL", out)

    def test_known_warnings_are_report_only(self):
        dur = self.healthy()
        self.log.write_text("2026-11-03 09:00:00,000 WARNING mal.h5_shadow feed error ConnectionClosed: x\n"
                            "2026-11-03 09:01:00,000 WARNING mal.h5_shadow source ended; restarting\n"
                            "2026-11-03 09:02:00,000 WARNING mal.h5_shadow something new\n")
        rc, out = self.judge(dur)
        self.assertEqual(rc, 0, out)
        self.assertIn("feed_error=1", out)
        self.assertIn("source_restart=1", out)
        self.assertIn("warning_other=1", out)

    def test_memory_projection(self):
        # 10 MB/h growth from 400 MB: 400e6 + 10e6 * 605 = 6.45e9 > cap 1.5728e9 -> FAIL
        dur = self.healthy(rss_bytes=lambda t: int(400_000_000 + 10_000_000 * t / 3600))
        rc, out = self.judge(dur)
        self.assertEqual(rc, 1)
        self.assertIn("SMOKE_ITEM d FAIL", out)
        # 1 MB/h: 400e6 + ~0.5e6 (R0 at +30 min) + 1e6 * 605 = 1.0e9 <= cap -> PASS
        for p in self.out.iterdir():
            p.unlink()
        dur = self.healthy(rss_bytes=lambda t: int(400_000_000 + 1_000_000 * t / 3600))
        rc, out = self.judge(dur)
        self.assertEqual(rc, 0, out)
        self.assertIn("slope_bytes_per_h=1000000.0", out)

    def test_warm_up_ramp_is_outside_the_fit(self):
        dur = self.healthy(rss_bytes=lambda t: 100_000_000 + min(t, 600) * 500_000)  # ramps for 10 min, then flat
        rc, out = self.judge(dur)
        self.assertEqual(rc, 0, out)
        self.assertIn("slope_bytes_per_h=0.0", out)

    def test_too_few_fit_samples_fails_d(self):
        self.healthy()
        self.rss.write_text("".join(f"{T0 + i * 600}\t400000000\t7\n" for i in range(4)))
        rc, out = self.judge(25200)
        self.assertIn("SMOKE_ITEM d FAIL fit_samples=", out)

    def test_malformed_rss_is_unreadable(self):
        self.healthy()
        self.rss.write_text("x\ty\n")
        rc, out = self.judge(25200)
        self.assertEqual(rc, 2)

    def test_gap_counter_by_hour_kind_and_flag(self):
        h0 = T0 - (T0 % 3600) + 3600  # next full hour
        gaps = [(h0 + 10, {"type": "gap", "kind": "socket_reconnect", "open_pools": 4, "flags_pools": False, "sockets": 3}),
                (h0 + 20, {"type": "gap", "kind": "socket_reconnect", "open_pools": 4, "flags_pools": True}),
                (h0 + 30, {"type": "gap", "kind": "socket_reconnect", "open_pools": 4, "flags_pools": True}),
                (h0 + 40, {"type": "gap", "kind": "silence", "open_pools": 4, "flags_pools": True}),
                (h0 + 3700, {"type": "gap", "kind": "feed_restart", "open_pools": 1, "flags_pools": True, "reason": "source_restart"})]
        dur = self.healthy(gaps=gaps)
        rc, out = self.judge(dur)
        # E_gap: [h0+20-340, h0+40] (360 s) and [h0+3700-340, h0+3700] (340 s) = 700 s over 7 full hours = 0.027778 > 0.025 -> g FAILS; the per-hour counts never decide
        self.assertEqual(rc, 1, out)
        self.assertIn("SMOKE_ITEM g FAIL e_gap=0.027778 max=0.025 full_hours=7 covered_s=700 flagged_rows_in_window=4", out)
        self.assertIn("SMOKE_VERDICT FAIL failed=[g]", out)
        hour = hour_name(h0)[10:23]
        self.assertIn(f"SMOKE_GAPS hour={hour} kind=socket_reconnect flags_pools=false n=1", out)
        self.assertIn(f"SMOKE_GAPS hour={hour} kind=socket_reconnect flags_pools=true n=2", out)
        self.assertIn(f"SMOKE_GAPS hour={hour} kind=silence flags_pools=true n=1", out)
        self.assertIn(f"coverage_losing_total=4 coverage_losing_max_per_hour=3 hour_of_max={hour} gap_rows_total=5", out)
        self.assertNotIn("open_pools", out)
        self.assertNotIn("result=", out)  # the old "<= 2 per hour" form is gone (Amendment 22 item 11 replaces it)

    def test_e_gap_one_isolated_flagged_gap_passes_and_redundant_gaps_never_count(self):
        gaps = [(T0 + 7200, {"type": "gap", "kind": "socket_reconnect", "open_pools": 2, "flags_pools": True})]
        gaps += [(T0 + 60 * i, {"type": "gap", "kind": "socket_reconnect", "open_pools": 2, "flags_pools": False}) for i in range(1, 300)]
        dur = self.healthy(gaps=gaps)
        rc, out = self.judge(dur)
        self.assertEqual(rc, 0, out)
        self.assertIn("SMOKE_ITEM g PASS e_gap=0.013492 max=0.025 full_hours=7 covered_s=340 flagged_rows_in_window=1", out)

    def test_e_gap_unit(self):
        self.assertEqual(h.e_gap([], 100, 200), (None, 0, 0, 0))
        eg, full, cov, n = h.e_gap([(T0 + 100) * 1000, (T0 + 200) * 1000, (T0 + 100000) * 1000], T0, T0 + 7200)
        self.assertEqual((full, cov, n), (2, 200, 2))  # [T0-240, T0+100] and [T0-140, T0+200], clipped at the window start, merge to [T0, T0+200]; the third is outside
        self.assertAlmostEqual(eg, 200 / 7200)

    def test_key_string_or_unread_journal_fails_k(self):
        dur = self.healthy()
        rc, out = self.judge(dur, journal="unreadable")
        self.assertIn("SMOKE_ITEM k FAIL", out)
        self.log.write_text("2026-11-03 09:00:00,000 WARNING mal.h5_shadow feed error InvalidURI: wss://mainnet.helius-rpc.com/?api-key=zzz\n")
        rc, out = self.judge(dur)
        self.assertEqual(rc, 1)
        self.assertIn("SMOKE_ITEM k FAIL key_string_lines_process_log_and_errors_file=1", out)
        self.assertNotIn("zzz", out)

    def test_gap_row_without_a_bool_flag_counts_as_coverage_losing(self):
        with open(self.out / hour_name(T0 + 60), "w") as fh:
            fh.write('{"type":"gap","kind":"slot_jump","open_pools":1,"from_slot":1,"to_slot":9,"v":1,"t_ms":1}\n')
        dur = self.healthy()
        rc, out = self.judge(dur)
        self.assertIn("kind=slot_jump flags_pools=missing n=1", out)
        self.assertIn("coverage_losing_total=1", out)

    def test_overriding_a_pin_never_passes(self):
        dur = self.healthy()
        rc, out = self.judge(dur, extra=("--warm-s", "0"))
        self.assertEqual(rc, 1)
        self.assertIn("test_overrides", out)


class CleanLog(Fixture):
    def setUp(self):
        super().setUp()
        self.job = self.d / "log"
        self.now = T0 + 7200

    def book(self, hb_minutes=120, extra_rows=(), log_text=None, status_age=30):
        self.write_rows([(T0, start_row())] + [(T0 + i * 60, {"type": "hb", "counters": {"triggers": 9}}) for i in range(1, hb_minutes + 1)] +
                        [(T0 + 200, {"type": "trigger", "variant": "pv", "pool": "POOLY"})] + list(extra_rows))
        st = self.out / "h5-shadow-status.json"
        st.write_text('{"counters":{"triggers":9}}')
        os.utime(st, (self.now - status_age, self.now - status_age))
        self.job.write_text(log_text if log_text is not None else
                            "q50s1-shadow: memory (P + S + N + M <= L): need=1 L=2\n"
                            "q50s1-shadow: starting rule H5-BOOSTFLOOR-Q50 v1, fresh-forward ARENA, q*=50, out=/x, head=5c2ab7c6\n"
                            "2026-11-03 09:00:00,000 WARNING mal.h5_shadow feed error ConnectionClosed: 1006\n"
                            "some other line with POOLY in it\n")

    def clean(self, active="yes", since=None, cmdline="0", environ="0", journal="0"):
        return run(["clean-log", "--out-dir", str(self.out), "--job-log", str(self.job), "--scope-active", active,
                    "--scope-since-epoch", str(T0 if since is None else since), "--now-epoch", str(self.now),
                    "--cmdline-key-count", cmdline, "--environ-key-count", environ, "--journal-key-count", journal, "--proc-mount", "rw,nosuid,nodev,noexec,relatime"])

    def test_clean(self):
        self.book()
        rc, out = self.clean()
        self.assertEqual(rc, 0, out)
        self.assertIn("CLEAN_VERDICT CLEAN", out)
        self.assertIn("CLEAN_START_SCRIPT q50s1-shadow: starting rule", out)
        for leak in ("POOLY", "triggers", "some other line"):
            self.assertNotIn(leak, out)
        self.assertIn("feed_error=1", out)

    def test_not_clean_cases(self):
        cases = {
            "scope_active": dict(active="no"),
            "scope_up_1h": dict(since=self.now - 1000),
            "one_start_row": dict(extra_rows=[(T0 + 3000, start_row())]),
            "no_stop_row": dict(extra_rows=[(T0 + 3000, {"type": "stop"})]),
            "no_traceback": dict(log_text="q50s1-shadow: starting rule x\nTraceback (most recent call last):\n"),
            "one_starting_line": dict(log_text="q50s1-shadow: starting rule x\nq50s1-shadow: starting rule x\n"),
            "no_error_critical": dict(log_text="q50s1-shadow: starting rule x\n2026-11-03 09:00:00,000 CRITICAL mal.h5_shadow x\n"),
            "hb_last_hour": dict(hb_minutes=50),
            "status_fresh": dict(status_age=600),
            "no_key_string": dict(log_text="q50s1-shadow: starting rule x\nfeed error wss://x/?api-key=zzz\n"),
            "no_key_in_start_rows": dict(extra_rows=[]),
            "no_key_in_cmdline": dict(cmdline="1"),
            "no_key_in_environ": dict(environ="unreadable"),
            "no_key_in_journal": dict(journal="unreadable"),
        }
        for name, kw in cases.items():
            with self.subTest(name=name):
                for p in self.out.iterdir():
                    p.unlink()
                book_kw = {k: v for k, v in kw.items() if k in ("hb_minutes", "extra_rows", "log_text", "status_age")}
                self.book(**book_kw)
                if name == "no_key_in_start_rows":  # a start row whose logged argv still carries a key
                    f = sorted(self.out.glob("h5-shadow-*.jsonl"))[0]
                    f.write_text(f.read_text().replace('"argv":["--fresh-forward","ARENA"]', '"argv":["--ws-url","wss://x/?api-key=zzz"]'))
                rc, out = self.clean(**{k: v for k, v in kw.items() if k in ("active", "since", "cmdline", "environ", "journal")})
                self.assertEqual(rc, 1, out)
                self.assertIn(f"{name}=NO", out)

    def test_missing_inputs_are_unreadable(self):
        self.book()
        self.job.unlink()
        rc, out = self.clean()
        self.assertEqual(rc, 2)


class DryFilter(unittest.TestCase):
    def doc(self, **over):
        b = {"registered": True, "refused_dirs": {}, "attempts": 12, "counted": 7, "excluded": 1, "q50s1_read_instant": {"record": "dry run: not written", "status": "NOT_YET"}}
        b.update(over)
        return {"dry_run": True, "shadow_dirs": ["/home/c/data/arena/h5-q50s1-2026-11-05"], "books": {"h5v1q50-boostfloor-s1": b}, "counts": {"rows": 99}}

    def filt(self, doc, mode="dry"):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            json.dump(doc, fh)
        self.addCleanup(os.unlink, fh.name)
        return run(["dry-filter", "--json", fh.name, "--mode", mode])

    def test_ok_and_no_counts(self):
        rc, out = self.filt(self.doc())
        self.assertEqual(rc, 0, out)
        self.assertIn("DRY_S1 registered=True refused_dirs=0 instant_record=dry run: not written", out)
        self.assertIn("shadow_dirs=h5-q50s1-2026-11-05", out)
        for leak in ("12", "attempts", "counted", "excluded", "NOT_YET", "99"):
            self.assertNotIn(leak, out)

    def test_refused_dir_or_unregistered_is_not_ok(self):
        rc, out = self.filt(self.doc(refused_dirs={"/x/h5-q50s1-2026-11-05": "end_only_mismatch"}))
        self.assertEqual(rc, 1)
        self.assertIn("DRY_REFUSED_DIR dir=h5-q50s1-2026-11-05 reason=end_only_mismatch", out)
        rc, out = self.filt(self.doc(registered=False))
        self.assertEqual(rc, 1)

    def test_mode_must_match_dry_run(self):
        rc, _ = self.filt(self.doc(), mode="score")
        self.assertEqual(rc, 1)
        d = self.doc()
        d["dry_run"] = False
        rc, _ = self.filt(d, mode="score")
        self.assertEqual(rc, 0)

    def test_unreadable(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            fh.write("not json")
        self.addCleanup(os.unlink, fh.name)
        rc, out = run(["dry-filter", "--json", fh.name])
        self.assertEqual(rc, 2)


class Pins(unittest.TestCase):
    def test_pins_match_the_shadow_start_and_the_table_row(self):
        from tools import arena_h5_q50s_instant as si
        repo = Path(__file__).resolve().parent.parent
        start = (repo / "scripts" / "ops" / "q50s1-shadow-start.sh").read_text()
        self.assertIn(f"S_SHA={h.S_SHA}", start)
        self.assertIn("NEW_MAX_MB=1500", start)
        self.assertEqual(h.CAP_BYTES, 1500 * 1024 * 1024)
        self.assertEqual(h.S_SHA, si.S1["frozen_sha"])
        self.assertIn(f'"rule_sha256":"{si.S1["rule_sha256"]}"', h.START_FIELDS)
        self.assertIn(f'"end_only_ms":{si.S1["end_only_ms"]}', h.START_FIELDS)
        # PLAN_H: 2026-11-05T20:00Z (shadow window start) to 2026-12-01T01:00Z (E = S*'s SUCC_END_MS, DEC-031 Amendment 22 item 8.1, + 1 h)
        self.assertEqual(h.PLAN_H * 3600, (si.S1["end_only_ms"] + 3_600_000) // 1000 - 1793908800)
        self.assertEqual(h.MIN_DUR_S, 6 * 3600)
        self.assertEqual((h.EGAP_SPAN_S, h.EGAP_MAX), (340, 0.025))


if __name__ == "__main__":
    unittest.main()
