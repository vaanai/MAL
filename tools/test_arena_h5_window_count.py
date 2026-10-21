"""tools/arena_h5_window_count.py: fixtures only (no network, no real data). The executor's parser is replaced by a stub (it needs solders)."""
from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools import arena_h5_score as sc
from tools import arena_h5_window_count as wc
from tools import h5_shadow as h5

SHA = "1f7a89cdf02ff46c3e33b9dcc88531c75d2b6cde"
REG = 1_000
START = 1_792_424_776_000  # 2026-10-19T15:46:16Z
END = 1_792_480_287_000  # 2026-10-20T07:11:27Z
B1, B2 = "h5v1-boostfloor-a1", "h5v2-boostclock-a1"
SECRET_WORDS = ("exit_price", "pnl_sol", "fill_price", "outcome_net", "SECRETVAL")


def start_rec(start_ms=START - 10_000, sha=SHA, **kw):
    return {"type": "start", "start_ms": start_ms, "code_sha": sha, "fresh_forward": h5.FRESH_FORWARD_REF, "dirty": 0, **kw}


def trig(pool, t, variant="pv", **kw):
    return {"type": "trigger", "variant": variant, "pool": pool, "t_detect_ms": t, **kw}


def write_dir(root: Path, name: str, recs, hour="2026-10-19T15", raw_lines=()):
    d = root / name
    d.mkdir()
    with open(d / f"h5-shadow-{hour}.jsonl", "w") as fh:
        for r in recs:
            fh.write((r if isinstance(r, str) else json.dumps(r)) + "\n")
        for r in raw_lines:
            fh.write(r + "\n")
    return d


def run(root, dirs, books=B1, start="2026-10-19T15:46:16Z", end_ms=END, extra=()):
    argv = ["--books", books, "--start-utc", start, "--end-ms", str(end_ms), "--arena-root", str(root), "--allow-root", "--frozen-sha", SHA, "--registered-at-ms", str(REG)]
    for d in dirs:
        argv += ["--shadow-dir", d]
    argv += list(extra)
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = wc.main(argv)
    return rc, out.getvalue(), err.getvalue()


class WindowCount(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        p = mock.patch.object(sc, "executor_refusal", lambda rec: "bad_intent:ids" if rec.get("pool", "").startswith("REF") else None)
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)

    def test_half_open_boundaries(self):
        recs = [start_rec(), trig("a", START - 1), trig("b", START), trig("c", END - 1), trig("d", END), trig("e", END + 1)]
        write_dir(self.root, "h5-x", recs)
        rc, out, _ = run(self.root, ["h5-x"])
        doc = json.loads(out)
        self.assertEqual(rc, 0)
        b = doc["books"][B1]
        self.assertEqual(b["attempts"], 2)  # START (inclusive) and END-1; not START-1, not END
        self.assertEqual(b["per_utc_date"], {"2026-10-19": 1, "2026-10-20": 1})
        self.assertEqual(b["total"], 2)

    def test_empty_window_is_zero(self):
        write_dir(self.root, "h5-x", [start_rec(), trig("a", START + 5)])
        rc, out, _ = run(self.root, ["h5-x"], end_ms=START)
        self.assertEqual(rc, 0)
        b = json.loads(out)["books"][B1]
        self.assertEqual((b["attempts"], b["per_utc_date"], b["triggers_seen"]), (0, {}, 0))

    def test_unknown_bot_refused(self):
        write_dir(self.root, "h5-x", [start_rec()])
        rc, out, err = run(self.root, ["h5-x"], books=f"{B1},h5v9-nope")
        self.assertEqual(rc, 2)
        self.assertEqual(out, "")
        self.assertIn("unknown bot id", err)

    def test_two_books_and_duplicates(self):
        write_dir(self.root, "h5-x", [start_rec(), trig("a", START + 1), trig("a", START + 2), trig("b", START + 3)])
        _, out, _ = run(self.root, ["h5-x"], books=f"{B1},{B2}")
        doc = json.loads(out)
        self.assertEqual(sorted(doc["books"]), [B1, B2])
        self.assertEqual(doc["books"][B2]["attempts"], 2)  # the pool 'a' counts once

    def test_refused_triggers_are_not_attempts(self):
        write_dir(self.root, "h5-x", [start_rec(), trig("REF1", START + 1), trig("ok", START + 2), trig("x", START + 3, variant="other")])
        b = json.loads(run(self.root, ["h5-x"])[1])["books"][B1]
        self.assertEqual((b["attempts"], b["refused"], b["triggers_seen"], b["refused_by_reason"]), (1, 1, 2, {"bad_intent:ids": 1}))

    def test_malformed_rows_counted_and_reported(self):
        recs = [start_rec(), trig("a", START + 1), trig("nodate", None), trig("", START + 2), '{"type": "trigger", "variant": "pv", BROKEN', '{"type": "trigger", "variant": "pv", "pool": "nan", "t_detect_ms": NaN}',
                trig("ok", START + 3)]
        write_dir(self.root, "h5-x", recs)
        rc, out, _ = run(self.root, ["h5-x"])
        doc = json.loads(out)
        self.assertEqual(rc, 3)
        self.assertEqual(doc["books"][B1]["attempts"], 2)
        self.assertEqual(doc["malformed"], {"bad_json_lines": 1, "pv_trigger_without_pool": 1, "trigger_without_usable_t_detect_ms": 1})
        self.assertEqual(doc["lines"]["unclassified_lines"], 1)  # the truncated line (does not end in }) is classified from raw text, never parsed
        self.assertTrue(doc["lower_bound"])
        self.assertIn("LOWER BOUND", doc["label"])

    def test_parser_fault_is_reported(self):
        write_dir(self.root, "h5-x", [start_rec(), trig("a", START + 1)])
        with mock.patch.object(sc, "executor_refusal", side_effect=sc.ParserFault("x")):
            rc, out, _ = run(self.root, ["h5-x"])
        self.assertEqual(rc, 3)
        self.assertEqual(json.loads(out)["malformed"], {"parser_fault": 1})

    def test_trigger_before_start_reported(self):
        write_dir(self.root, "h5-x", [trig("a", START + 1), start_rec()])
        rc, out, _ = run(self.root, ["h5-x"])
        self.assertEqual(rc, 3)
        self.assertEqual(json.loads(out)["malformed"], {"trigger_before_any_start": 1})

    def test_identity_failures_feed_nothing(self):
        write_dir(self.root, "h5-bad", [start_rec(sha="0" * 40), trig("a", START + 1)])
        write_dir(self.root, "h5-early", [start_rec(start_ms=REG - 1), trig("b", START + 1)])
        write_dir(self.root, "h5-good", [start_rec(), trig("c", START + 1)])
        doc = json.loads(run(self.root, ["h5-bad", "h5-early", "h5-good"])[1])
        self.assertEqual(doc["refused_dirs"], 2)
        self.assertEqual(doc["books"][B1]["attempts"], 1)

    def test_decision_before_run_start_not_counted(self):
        write_dir(self.root, "h5-x", [start_rec(start_ms=START + 100), trig("a", START + 50), trig("b", START + 100)])
        self.assertEqual(json.loads(run(self.root, ["h5-x"])[1])["books"][B1]["attempts"], 1)

    def test_end_utc_equals_end_ms(self):
        write_dir(self.root, "h5-x", [start_rec(), trig("c", END - 1), trig("d", END)])
        argv_rc, out, _ = run(self.root, ["h5-x"], end_ms=END)
        out2 = io.StringIO()
        argv = ["--books", B1, "--start-utc", "2026-10-19T15:46:16Z", "--end-utc", "2026-10-20T07:11:27Z", "--arena-root", str(self.root), "--allow-root", "--frozen-sha", SHA,
                "--registered-at-ms", str(REG), "--shadow-dir", "h5-x"]
        with contextlib.redirect_stdout(out2):
            wc.main(argv)
        self.assertEqual(out, out2.getvalue())

    def test_outcome_fields_never_printed_or_parsed(self):
        outcome = {"type": "outcome", "variant": "pv", "pool": "a", "exit_price": 1.5, "pnl_sol": "SECRETVAL"}
        trig_with = trig("a", START + 1, exit_price=2.0, fill_price=3.0, pnl_sol=4.0, outcome_net="SECRETVAL")
        write_dir(self.root, "h5-x", [start_rec(), trig_with, outcome, '{"type": "outcome", "pnl_sol": "SECRETVAL" BROKEN JSON'])
        rc, out, err = run(self.root, ["h5-x"])
        self.assertEqual(rc, 3)  # the broken outcome line has no closing brace: UNCLASSIFIED (counted from raw text, never parsed), so a lower bound
        self.assertEqual(json.loads(out)["lines"], {"total_lines": 4, "parsed_lines": 2, "other_type_lines": 1, "unclassified_lines": 1})
        for w in SECRET_WORDS:
            self.assertNotIn(w, out + err)
        self.assertEqual(set(json.loads(out)), {"window", "books", "refused_dirs", "lines", "malformed", "feed_gaps_by_date", "hard_gap", "lower_bound", "label", "counts_only"})
        self.assertEqual(set(json.loads(out)["books"][B1]), {"attempts", "refused", "refused_by_reason", "triggers_seen", "per_utc_date", "total"})
        # the reader hands on whitelisted fields only (the raw trigger goes to the executor's parser, never to output)
        import collections
        recs = list(wc.read_decision_records(self.root / "h5-x", collections.Counter()))
        self.assertEqual([r["type"] for r in recs], ["start", "trigger"])
        self.assertTrue(set(recs[0]) <= set(wc.START_FIELDS))
        self.assertTrue(set(recs[1]) - {"_raw"} <= set(wc.TRIGGER_FIELDS))

    def test_root_guard(self):
        write_dir(self.root, "h5-x", [start_rec()])
        argv = ["--books", B1, "--start-utc", "2026-10-19T15:46:16Z", "--end-ms", str(END), "--arena-root", str(self.root), "--frozen-sha", SHA, "--registered-at-ms", "1",
                "--shadow-dir", "h5-x"]
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(wc.main(argv), 2)
        self.assertIn("--arena-root", err.getvalue())

    def test_shadow_dir_outside_root_refused(self):
        other = tempfile.TemporaryDirectory()
        self.addCleanup(other.cleanup)
        rc, _, err = run(self.root, [other.name])
        self.assertEqual(rc, 2)
        self.assertIn("under the arena root", err)

    def test_bad_instant_and_reversed_window_refused(self):
        write_dir(self.root, "h5-x", [start_rec()])
        self.assertEqual(run(self.root, ["h5-x"], start="2026-10-19 15:46:16")[0], 2)
        self.assertEqual(run(self.root, ["h5-x"], end_ms=START - 1)[0], 2)

    def test_line_classification_from_raw_text(self):
        recs = [start_rec(), trig("a", START + 1), '{"type": "hb", "x": 1}', '{"x": 1}', '{"type": "pool", "pool": "a"', '', 'garbage']
        write_dir(self.root, "h5-x", recs)
        rc, out, _ = run(self.root, ["h5-x"])
        doc = json.loads(out)
        self.assertEqual(rc, 3)
        self.assertEqual(doc["lines"], {"total_lines": 6, "parsed_lines": 2, "other_type_lines": 1, "unclassified_lines": 3})
        self.assertTrue(doc["lower_bound"])

    def test_clean_run_is_not_lower_bound(self):
        write_dir(self.root, "h5-x", [start_rec(), trig("a", START + 1), '{"type": "hb"}'])
        rc, out, _ = run(self.root, ["h5-x"])
        self.assertEqual(rc, 0)
        self.assertFalse(json.loads(out)["lower_bound"])

    def test_sealed_look2_triggers_skipped(self):
        write_dir(self.root, "h5-x", [start_rec(), trig("a", START + 1, h5_look2_sealed=True), trig("b", START + 2)])
        b = json.loads(run(self.root, ["h5-x"])[1])["books"][B1]
        self.assertEqual((b["attempts"], b["triggers_seen"]), (1, 1))

    def test_equality_with_score_book(self):
        recs = [start_rec(), trig("a", START - 1), trig("b", START), trig("REF1", START + 5), trig("b", START + 6), trig("seal", START + 7, h5_look2_sealed=True),
                trig("c", END - 1), trig("d", END), trig("o", START + 9, variant="other"), trig("e", START + 20_000_000)]
        # same fixture in two dirs: the second repeats pool 'e' (cross-dir duplicate) and adds one
        d1 = write_dir(self.root, "h5-a", [dict(r, t_ms=r.get("t_detect_ms", START)) if r["type"] == "trigger" else r for r in recs])
        d2 = write_dir(self.root, "h5-b", [start_rec(), trig("e", START + 20_000_001), trig("f", START + 30_000_000)], hour="2026-10-19T16")
        counts = __import__("collections").Counter()
        dirs = [sc.ShadowDir(d1, counts), sc.ShadowDir(d2, counts)]
        res = sc.score_book(B1, {"clock_start_ms": START, "window_end_ms": END, "frozen_sha": SHA, "registered_at_ms": REG}, dirs)
        rc, out, _ = run(self.root, ["h5-a", "h5-b"])
        b = json.loads(out)["books"][B1]
        self.assertEqual(rc, 0)
        self.assertEqual((b["attempts"], b["refused"], b["triggers_seen"]), (len(res.decisions), len(res.refusals), res.triggers_seen))
        self.assertGreater(b["attempts"], 3)
        self.assertEqual(b["refused"], 1)

    def test_gaps_restarts_and_missing_hours_by_date(self):
        h0 = "2026-10-19T15"
        recs = [start_rec(t_ms=START - 10_000), trig("a", START + 1_000),
                {"type": "gap", "kind": "silence", "t_ms": START + 60_000, "silent_ms": 45_000, "flags_pools": True},
                {"type": "gap", "kind": "slot_jump", "t_ms": START + 61_000, "missed_slots": 7, "flags_pools": True, "pool_secret": "SECRETVAL"},
                {"type": "gap", "kind": "socket_reconnect", "t_ms": START + 62_000, "flags_pools": False},
                {"type": "gap", "kind": "silence", "t_ms": START - 5_000, "silent_ms": 999},  # before the window
                start_rec(start_ms=START + 70_000, t_ms=START + 70_000)]
        write_dir(self.root, "h5-x", recs, hour=h0)
        end = START + 2 * 3_600_000  # window touches hours 15, 16, 17
        rc, out, _ = run(self.root, ["h5-x"], end_ms=end)
        doc = json.loads(out)
        self.assertEqual(rc, 0)
        self.assertNotIn("SECRETVAL", out)
        row = doc["feed_gaps_by_date"]["2026-10-19"]
        self.assertEqual(row["gap_records"], {"silence": 1, "slot_jump": 1, "socket_reconnect": 1})
        self.assertEqual((row["silent_ms_total"], row["missed_slots_total"], row["flagged_gap_records"]), (45_000, 7, 2))
        self.assertEqual(row["restarts"], 1)
        self.assertEqual(row["restart_ms_upper_bound"], 8_000)  # start 70 s minus the last gap record (62 s)
        self.assertGreaterEqual(row["missing_hour_files"], 2)  # hours 16 and 17 have no file
        self.assertEqual(row["missing_hour_ms"], row["missing_hour_files"] * 3_600_000)
        self.assertTrue(row["hard_gap"] and doc["hard_gap"])

    def test_no_gap_records_no_restart_no_missing_hour(self):
        write_dir(self.root, "h5-x", [start_rec(), trig("a", START + 1)], hour="2026-10-19T15")
        doc = json.loads(run(self.root, ["h5-x"], end_ms=START + 1_000)[1])
        self.assertEqual(doc["feed_gaps_by_date"], {})
        self.assertFalse(doc["hard_gap"])

    def test_p0_mode(self):
        day = 86_400_000
        d0 = 1_792_368_000_000  # 2026-10-19T00:00:00Z
        recs = [start_rec(start_ms=d0 - 10_000), trig("a", d0 + 5), trig("b", d0 + day + 5), trig("c", d0 + day + 6), trig("d", d0 + 3 * day), trig("z", d0 - 1)]
        for h in range(72):
            hour = __import__("datetime").datetime.fromtimestamp((d0 + h * 3_600_000) / 1000, tz=__import__("datetime").timezone.utc).strftime("%Y-%m-%dT%H")
            (self.root / "h5-x").mkdir(exist_ok=True)
            (self.root / "h5-x" / f"h5-shadow-{hour}.jsonl").write_text("")
        with open(self.root / "h5-x" / "h5-shadow-2026-10-19T00.jsonl", "w") as fh:
            for r in recs:
                fh.write(json.dumps(r) + "\n")
        argv = ["--books", B1, "--p0-dates", "2026-10-19,2026-10-20,2026-10-21", "--arena-root", str(self.root), "--allow-root", "--frozen-sha", SHA, "--registered-at-ms", str(REG),
                "--shadow-dir", "h5-x"]
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = wc.main(argv)
        doc = json.loads(out.getvalue())
        self.assertEqual(rc, 0)
        p0 = doc["p0"]
        self.assertEqual(p0["attempts_by_date"], {"2026-10-19": 1, "2026-10-20": 2, "2026-10-21": 0})
        self.assertFalse(p0["floor_no_zero_day"])
        self.assertAlmostEqual(p0["average_per_day"], 1.0)
        self.assertFalse(p0["floor_average_ge_2_0"])
        self.assertFalse(p0["hard_gap"])
        self.assertEqual(doc["window"]["end_ms"], d0 + 3 * day)

    def test_p0_dates_refusals(self):
        write_dir(self.root, "h5-x", [start_rec()])
        for bad in ("2026-10-19,2026-10-21", "2026-10-19T00", ""):
            argv = ["--books", B1, "--p0-dates", bad, "--arena-root", str(self.root), "--allow-root", "--frozen-sha", SHA, "--registered-at-ms", "1", "--shadow-dir", "h5-x"]
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(wc.main(argv), 2)


if __name__ == "__main__":
    unittest.main()
