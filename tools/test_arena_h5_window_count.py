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
        self.assertEqual(doc["malformed"], {"bad_json_lines": 2, "pv_trigger_without_pool": 1, "trigger_without_usable_t_detect_ms": 1})

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
        self.assertEqual(rc, 0)  # the broken outcome line is not matched by the type pre-filter, so it is never parsed
        for w in SECRET_WORDS:
            self.assertNotIn(w, out + err)
        self.assertEqual(set(json.loads(out)), {"window", "books", "refused_dirs", "malformed", "counts_only"})
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


if __name__ == "__main__":
    unittest.main()
