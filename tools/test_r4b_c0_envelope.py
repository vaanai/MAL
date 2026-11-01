"""scripts/research/r4b_c0_envelope.py: the r4-b C0 decision host's memory envelope and 14-day lifetime, on SYNTHETIC sidecar lines in a temp dir.
No network, no /data/mal, no tape, no decision log, no host output.

    /data/mal/venv/bin/python -m pytest tools/test_r4b_c0_envelope.py
"""
from __future__ import annotations

import ast
import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("r4b_c0_envelope", REPO / "scripts" / "research" / "r4b_c0_envelope.py")
ev = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ev)
_sc_spec = importlib.util.spec_from_file_location("r4b_dry_sidecar", REPO / "scripts" / "research" / "r4b_dry_sidecar.py")
sc = importlib.util.module_from_spec(_sc_spec)
_sc_spec.loader.exec_module(sc)

MIB, H, D = ev.MIB, 3600.0, 86400.0
CE = 1_793_950_671.0  # an arbitrary catch-up end; only differences matter


def ramp(hours, slope_mib_h, base_mib=500.0, step_s=600.0, plateau_h=None, start_h=0.0):
    """(ts, anon bytes) every step_s from CE + start_h to CE + hours; linear at slope_mib_h, flat after plateau_h when given."""
    out, t = [], CE + start_h * H
    while t <= CE + hours * H + 1e-6:
        h = (t - CE) / H
        g = h if plateau_h is None else min(h, plateau_h)
        out.append((t, (base_mib + slope_mib_h * g) * MIB))
        t += step_s
    return out


def lines(samples, ce=CE, extra=None):
    """Sidecar-shaped JSON lines: the keys the sidecar writes, plus fields the tool must ignore."""
    rows = [{"ts": t, "utc": "x", "scope_found": True, "mem_anon": a, "mem_current": a, "mem_file": 7, "lag_slots": 2, "gaps": 76046, "phase": "live",
             "anon_proj_mib": 99999.0} for t, a in samples]
    rows.insert(0, {"ts": ce - 10, "scope_found": True, "mem_anon": 1300 * MIB, "catchup_end_ts": ce, "phase": "catchup"})
    for r in extra or []:
        rows.append(r)
    return [json.dumps(r) for r in rows]


class Constants(unittest.TestCase):
    def test_constants_match_the_sidecar(self):
        self.assertEqual(ev.PLATEAU_H * H, sc.HORIZON_AFTER_CATCHUP_S)
        self.assertEqual(ev.FIT_MIN_POINTS, sc.FIT_MIN_POINTS)
        self.assertEqual(ev.FIT_MIN_SPAN_H * H, sc.FIT_MIN_SPAN_S)
        self.assertEqual(ev.DEFAULT_CAP_MIB, sc.SCOPE_CAP_MIB)
        self.assertEqual(ev.ALERT_FRAC * ev.DEFAULT_CAP_MIB, sc.ALERT_MIB)
        self.assertEqual(ev.MISCUSI_MAX_S, 1_209_600)

    def test_wrapper_cap_is_the_default(self):
        txt = (REPO / "scripts" / "research" / "r4b-dry-long.sh").read_text()
        self.assertIn(f"SCOPE_MAX_MIB={int(ev.DEFAULT_CAP_MIB)}", txt)


class Parse(unittest.TestCase):
    def test_keeps_only_memory_keys_sorted_dedup(self):
        s = ramp(10, 10.0)
        ls = lines(list(reversed(s)) + [s[3]])
        ls.append("not json")
        ls.append(json.dumps([1, 2]))
        ls.append(json.dumps({"ts": CE + 1, "scope_found": False, "mem_anon": 5}))   # scope not found: no sample
        ls.append(json.dumps({"ts": CE + 2, "scope_found": True, "mem_anon": True}))  # bool is not a number
        p = ev.parse_lines(ls)
        self.assertEqual(p["catchup_end"], CE)
        self.assertEqual(p["bad_lines"], 2)
        self.assertEqual([t for t, _ in p["samples"]], sorted({t for t, _ in s} | {CE - 10}))

    def test_first_catchup_end_wins(self):
        ls = lines(ramp(2, 1.0), extra=[{"ts": CE + 99, "catchup_end_ts": CE + 500}])
        self.assertEqual(ev.parse_lines(ls)["catchup_end"], CE)


class Envelope(unittest.TestCase):
    def test_insufficient_without_enough_samples(self):
        r = ev.envelope(ramp(5, 26.75), CE, origin=CE, run_end=CE + 14 * D)
        self.assertEqual(r["verdict"], "INSUFFICIENT")
        r = ev.envelope([], CE, origin=CE, run_end=CE + D)
        self.assertEqual(r["verdict"], "INSUFFICIENT")

    def test_manager_note_reproduced_without_plateau(self):
        """26.75 MiB/h with no plateau over 336 h is about 9 GB: the C0 note's arithmetic, so 5120M does not fit and the needed cap is reported."""
        r = ev.envelope(ramp(30, 26.75, base_mib=558.3), CE, origin=CE, run_end=CE + 336 * H)
        self.assertEqual(r["projection_label"], "linear")
        self.assertFalse(r["plateau_measured"])
        self.assertAlmostEqual(r["pre_slope_mib_h"], 26.75, places=2)
        self.assertAlmostEqual(r["projection_mib"], 558.3 + 26.75 * 336, delta=1.0)
        self.assertFalse(r["envelope_fits"])
        self.assertEqual(r["needed_cap_mib"], 11264)  # ceil(9546.3 / 0.85 / 256) x 256
        self.assertEqual(r["verdict"], "NO_FIT")

    def test_measured_plateau_fits_5120(self):
        """Growth stops at the bonding-idle horizon: the tail slope is about 0 and the 14-day projection stays at the plateau."""
        s = ramp(141, 26.75, base_mib=558.3, plateau_h=116)
        r = ev.envelope(s, CE, origin=CE, run_end=CE + 336 * H)
        self.assertTrue(r["plateau_measured"])
        self.assertAlmostEqual(r["tail_slope_mib_h"], 0.0, places=3)
        self.assertEqual(r["projection_label"], "tail")
        self.assertAlmostEqual(r["projection_mib"], 558.3 + 26.75 * 116, delta=1.0)  # 3,661.3: QP's own 3,661.6 projection, now as a plateau
        self.assertTrue(r["envelope_fits"])
        self.assertEqual(r["verdict"], "FIT")
        # the pessimistic figure is still printed beside it
        self.assertGreater(r["proj_no_plateau_mib"], 4352.0)
        self.assertFalse(r["envelope_fits_no_plateau"])

    def test_tail_still_growing_is_extended(self):
        s = ramp(141, 10.0, base_mib=500.0)
        r = ev.envelope(s, CE, origin=CE, run_end=CE + 336 * H)
        self.assertEqual(r["projection_label"], "tail")
        self.assertAlmostEqual(r["tail_slope_mib_h"], 10.0, places=2)
        self.assertAlmostEqual(r["projection_mib"], 500 + 10.0 * 336, delta=1.0)
        self.assertTrue(r["envelope_fits"])  # 3,860 <= 4,352

    def test_negative_tail_never_lowers_the_projection(self):
        s = ramp(116, 20.0, base_mib=500.0) + [(t, a) for t, a in ((CE + h * H, (2820 - 5 * (h - 116)) * MIB) for h in [117 + k / 6 for k in range(150)])]
        r = ev.envelope(sorted(s), CE, origin=CE, run_end=CE + 336 * H)
        self.assertEqual(r["projection_label"], "tail")
        self.assertLess(r["tail_slope_mib_h"], 0)
        self.assertAlmostEqual(r["projection_mib"], r["anon_last_mib"], delta=5.0)

    def test_short_tail_is_not_a_plateau(self):
        s = ramp(125, 26.75, base_mib=558.3, plateau_h=116)  # only 8 h past 117 h: not measured
        r = ev.envelope(s, CE, origin=CE, run_end=CE + 336 * H)
        self.assertFalse(r["plateau_measured"])
        self.assertEqual(r["projection_label"], "linear")

    def test_horizon_inside_the_record_uses_the_observed_max(self):
        s = ramp(100, 5.0, base_mib=500.0)
        r = ev.envelope(s, CE, origin=CE, run_end=CE + 40 * H)
        self.assertEqual(r["projection_label"], "observed")
        self.assertAlmostEqual(r["projection_mib"], 500 + 5.0 * 40, delta=1.0)
        self.assertIsNone(r["proj_no_plateau_mib"])

    def test_origin_shift_for_a_live_warmup_host(self):
        """A live-warm-up host started at S reaches the 52 h state at S + 52 h; its horizon is the run end minus that."""
        s = ramp(30, 20.0, base_mib=500.0)
        S = CE + 100 * D
        r = ev.envelope(s, CE, origin=S + ev.WARMUP_H * H, run_end=S + ev.WARMUP_H * H + 200 * H)
        self.assertEqual(r["horizon_h"], 200.0)
        self.assertAlmostEqual(r["projection_mib"], 500 + 20.0 * 200, delta=1.0)

    def test_lifetime(self):
        s = ramp(30, 1.0, base_mib=500.0)
        start = CE - 3000.0
        ok = ev.envelope(s, CE, origin=CE, run_end=start + 14 * D, host_job_start=start, first_send=start + 3 * D)
        self.assertTrue(ok["lifetime_fits"])
        self.assertEqual(ok["max_canary_days_in_job"], 11.0)
        self.assertEqual(ok["verdict"], "FIT")
        bad = ev.envelope(s, CE, origin=CE, run_end=start + 14 * D + 1, host_job_start=start)
        self.assertFalse(bad["lifetime_fits"])
        self.assertEqual(bad["verdict"], "NO_FIT")

    def test_cap_candidate(self):
        s = ramp(30, 26.75, base_mib=558.3)
        r = ev.envelope(s, CE, origin=CE, run_end=CE + 336 * H, cap_mib=11264)
        self.assertTrue(r["envelope_fits"])
        self.assertEqual(r["alert_line_mib"], 9574.4)


class Cli(unittest.TestCase):
    def run_cli(self, ls, *args):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "sidecar.jsonl"
            p.write_text("\n".join(ls) + "\n")
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = ev.main(["--sidecar", str(p), *args])
            return rc, buf.getvalue()

    def test_fit_and_nofit_exit_codes(self):
        plateau = lines(ramp(141, 26.75, base_mib=558.3, plateau_h=116))
        rc, out = self.run_cli(plateau, "--origin-epoch", str(CE), "--run-end-epoch", str(CE + 336 * H))
        self.assertEqual(rc, 0, out)
        self.assertEqual(json.loads(out)["verdict"], "FIT")
        rc, out = self.run_cli(lines(ramp(30, 26.75, base_mib=558.3)), "--origin-epoch", str(CE), "--run-end-epoch", str(CE + 336 * H))
        self.assertEqual(rc, 1)

    def test_insufficient_and_usage(self):
        rc, out = self.run_cli([json.dumps({"ts": CE, "scope_found": True, "mem_anon": 1})], "--origin-epoch", "1", "--run-end-epoch", "2")
        self.assertEqual(rc, 2)
        self.assertEqual(ev.main(["--sidecar", "/nonexistent/sidecar.jsonl", "--origin-epoch", "1", "--run-end-epoch", "2"]), 5)
        self.assertEqual(ev.main([]), 5)

    def test_catchup_override(self):
        ls = [json.dumps({"ts": t, "scope_found": True, "mem_anon": a}) for t, a in ramp(30, 2.0)]
        rc, out = self.run_cli(ls, "--origin-epoch", str(CE), "--run-end-epoch", str(CE + 20 * H))
        self.assertEqual(rc, 2)
        rc, out = self.run_cli(ls, "--origin-epoch", str(CE), "--run-end-epoch", str(CE + 20 * H), "--catchup-end-epoch", str(CE))
        self.assertEqual(rc, 0, out)


class Seal(unittest.TestCase):
    def test_reads_only_the_named_file_and_memory_keys(self):
        """One open() call (the --sidecar path), no subprocess, no os/pathlib walking, and only the four memory/timing keys are read from a line."""
        src = (REPO / "scripts" / "research" / "r4b_c0_envelope.py").read_text()
        tree = ast.parse(src)
        imported = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        imported |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
        self.assertEqual(imported - {"__future__"}, {"argparse", "json", "math", "sys", "typing"})
        opens = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "open"]
        self.assertEqual(len(opens), 1)
        self.assertEqual(ev.KEYS, ("ts", "scope_found", "mem_anon", "catchup_end_ts"))
        for word in ("declog", "sealed", "fast-trades-tip", "/data/mal", "out/host"):
            self.assertNotIn(f'"{word}', src)


if __name__ == "__main__":
    unittest.main()
