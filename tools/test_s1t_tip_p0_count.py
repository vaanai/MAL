"""Tests for tools/s1t_tip_p0_count.py (the s1t slip P0 counter). SYNTHETIC fixture tapes in system-temp dirs only: no derived copy, tip tape, smoke dir, bots.json or ledger is opened.
The positive tests run the REAL S*' engine: they check out afa4971d (tag q50-succ-tip-1) as a temp git worktree of this clone (fail, never skip, if the object is missing).

  /data/mal/venv/bin/python -m pytest -q tools/test_s1t_tip_p0_count.py
"""
from __future__ import annotations

import io
import json
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from tools import arena_tip_adapter as ta
from tools import s1t_tip_p0_count as C
from tools.pump_structure_monitor import boost_vault_authority
from tools.test_h5_shadow import SOL, Tape

REPO = Path(C.__file__).resolve().parent.parent
NOW = int(datetime(2026, 11, 10, tzinfo=timezone.utc).timestamp() * 1000)
DAYS = "2026-11-06,2026-11-07,2026-11-08"
H0 = "2026-11-05T00"
WSOL = "So11111111111111111111111111111111111111112"
POOL_A, MINT_A = WSOL, "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
POOL_B, MINT_B = "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB", "4k3Dyjzvzp8eMZWUXbBCjEvwSkkk59S5iCNLY3QrkX6R"
POOL_C, MINT_C = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263", "7vfCXTUXx5WJV5JADk17DUJ4ksgau7utNKj4b963voxs"
_WT: dict[str, Path] = {}


def sstar() -> Path:
    """A clean worktree at afa4971d, made once per test run."""
    if "p" not in _WT:
        base = Path(tempfile.mkdtemp(prefix="s1tp0-wt-")).resolve()
        p = base / "sstar"
        r = subprocess.run(["git", "-C", str(REPO), "worktree", "add", "--detach", str(p), C.SSTAR_COMMIT], capture_output=True, text=True)
        if r.returncode != 0:
            raise AssertionError("afa4971d is not in this clone: git fetch it. " + r.stderr[-200:])
        _WT["p"], _WT["base"] = p, base
    return _WT["p"]


def tearDownModule() -> None:
    if "p" in _WT:
        subprocess.run(["git", "-C", str(REPO), "worktree", "remove", "--force", str(_WT["p"])], capture_output=True)
        subprocess.run(["git", "-C", str(REPO), "worktree", "prune"], capture_output=True)
        shutil.rmtree(_WT["base"], ignore_errors=True)


def pool_rows(pool: str, mint: str, ts0: int, slot0: int, target_q: float = 35.0) -> list[dict]:
    """One BOOST pool (its per-pool boost vault buys 1 SOL every 25 slots from s0 + 5), drained by a dump to Q = target_q SOL: the Q50 rule fires. Arena-tape rows via the real adapter."""
    pda = boost_vault_authority(pool)
    ev = [(slot0, "buy", "A", SOL // 10)] + [(slot0 + 5 + 25 * i, "buy", pda, SOL) for i in range(18)]
    ev += [(slot0 + 200, "sell", "DUMP", None), (slot0 + 500, "sell", "Z", SOL // 2), (slot0 + 900, "buy", "W", SOL)]
    t = Tape(pool=pool, slot0=slot0, ts0=ts0)
    for slot, side, trader, sol in sorted(ev, key=lambda e: e[0]):
        t.row(slot, side, trader, sol if sol is not None else int(t.q + t.v - target_q * SOL), mint=mint)
    rows = []
    for i, r in enumerate(t.rows):
        w = {k: v for k, v in r.items() if k not in ("lp_fee", "pool_quote_amount")}
        w.update(event_index=0, tx_index=i, venue="pumpswap", block_time=r["event_ts"])
        if i == 0:
            rows.append(ta.pool_create_row({"slot": r["slot"] - 1, "t_recv_ms": r["t_recv_ms"], "pool": pool, "mint": mint, "quote_mint": WSOL,
                                            "signature": "create-" + pool, "event_index": 0, "tx_index": 0, "block_time": r["event_ts"]}))
        rows.append(ta.trade_row(w))
    return rows


def ts(day: str, hour: int) -> int:
    return int(datetime.strptime(f"{day}T{hour:02d}", "%Y-%m-%dT%H").replace(tzinfo=timezone.utc).timestamp())


def write_tape(d: Path, rows: list[dict], *, skip: tuple[str, ...] = (), start="2026-11-05T00", end="2026-11-09T00") -> None:
    d.mkdir(parents=True, exist_ok=True)
    by: dict[str, list[str]] = {}
    t, e = datetime.strptime(start, "%Y-%m-%dT%H"), datetime.strptime(end, "%Y-%m-%dT%H")
    while t < e:
        by[t.strftime("%Y-%m-%dT%H")] = []
        t += timedelta(hours=1)
    for r in rows:
        h = datetime.fromtimestamp(r["t_recv_ms"] // 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H")
        by.setdefault(h, []).append(json.dumps(r, separators=(",", ":")) + "\n")
    for h, lines in by.items():
        if h not in skip and h < end.replace("T", "T"):
            (d / f"tape-{h}.jsonl").write_text("".join(lines))


class Tmp(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="s1tp0-")).resolve()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.tape = self.root / "hv7"

    def count(self, **kw):
        kw.setdefault("tape_dir", self.tape)
        return C.run_p0(kw.pop("days", DAYS), kw.pop("notebook_ref", "nb-1"), kw.pop("h0", H0), kw.pop("src", str(sstar())), now_ms=NOW, **kw)


class Refusals(Tmp):
    def test_tape_dir(self):
        for bad in ("/data/mal/arena/tape", "/var/lib/mal/sealed/fast-trades-tip", "/data2/mal/arena-tip-feed", "relative", "", "/data2/mal/tip-tape-archive/x",
                    str(self.root / "sealed" / "x"), str(self.root / ".writer" / "x")):
            with self.assertRaises(C.Refused, msg=bad):
                C.check_tape_dir(bad)
        self.assertEqual(C.check_tape_dir(self.root), self.root)

    def test_days(self):
        bad = ["2026-11-06,2026-11-07", "2026-11-06,2026-11-07,2026-11-07", "2026-11-06,2026-11-08,2026-11-07", "2026-11-06,2026-11-07,2026-11-09",
               "2026-11-05,2026-11-06,2026-11-07", "2026-11-06,2026-11-07,2026-11-31", "2026-11-8,2026-11-09,2026-11-10", "x,y,z", "2026-11-07,2026-11-08,2026-11-09",
               "2026-11-08,2026-11-07,2026-11-06", "2026-11-06,2026-11-07,2026-11-08,2026-11-09"]
        for b in bad:
            with self.assertRaises(C.Refused, msg=b):
                C.parse_days(b, now_ms=NOW + 10 * 86_400_000)
        with self.assertRaises(C.Refused) as cm:                       # day 3 not closed yet
            C.parse_days(DAYS, now_ms=int(datetime(2026, 11, 8, 23, tzinfo=timezone.utc).timestamp() * 1000))
        self.assertEqual(str(cm.exception), "day_not_closed")
        self.assertEqual(C.parse_days(DAYS, now_ms=NOW), DAYS.split(","))

    def test_h0(self):
        days = DAYS.split(",")
        for bad in ("2026-10-16T01", "2026-11-04T23", "2026-10-16T00", "2026-11-06T00", "2026-11-07T05", "2026-11-05", "x", None, "2026-13-01T00"):
            with self.assertRaises(C.Refused, msg=str(bad)):
                C.parse_h0(bad, days)
        self.assertEqual(C.parse_h0("2026-11-05T00", days)[0], "2026-11-05T00")

    def test_before_the_earliest_start_is_refused(self):
        for now in (int(datetime(2026, 11, 9, 0, 10, tzinfo=timezone.utc).timestamp() * 1000), int(datetime(2026, 11, 9, 0, 29, 59, tzinfo=timezone.utc).timestamp() * 1000)):
            with self.assertRaises(C.Refused) as cm:
                C.run_p0(DAYS, "nb", H0, str(sstar()), tape_dir=self.tape, now_ms=now)
            self.assertEqual(str(cm.exception), "before_2026_11_09T00_30Z")

    def test_notebook_ref_required(self):
        for ref in ("", "  ", None):
            with self.assertRaises(C.Refused) as cm:
                C.run_p0(DAYS, ref, H0, str(sstar()), tape_dir=self.tape, now_ms=NOW)
            self.assertEqual(str(cm.exception), "notebook_ref_required")

    def test_main_refusals_are_exit_2(self):
        err = io.StringIO()
        with redirect_stderr(err):
            self.assertEqual(C.main(["--days", DAYS, "--notebook-ref", "", "--h0", H0, "--sstar-src", "/x"]), 2)
            self.assertEqual(C.main(["--days", "2026-11-06", "--notebook-ref", "nb", "--h0", H0, "--sstar-src", "/x"]), 2)
        self.assertIn('"refused"', err.getvalue())

    def test_no_hours_at_all_is_refused(self):
        self.tape.mkdir()
        with self.assertRaises(C.Refused) as cm:
            self.count()
        self.assertEqual(str(cm.exception), "no_hours_in_days")


class Identity(Tmp):
    def test_this_checkout_is_not_sstar(self):
        with self.assertRaises(C.Refused) as cm:
            C.check_sstar_src(REPO)
        self.assertIn(str(cm.exception), ("sstar_head_mismatch", "sstar_src_not_a_worktree_root"))

    def test_plain_dir_and_relative(self):
        for bad in (str(self.root), "relative/x", "", str(self.root / "nope")):
            with self.assertRaises(C.Refused):
                C.check_sstar_src(bad)

    def test_the_pinned_worktree_passes(self):
        pins = C.check_sstar_src(sstar())
        self.assertEqual(pins["commit"], C.SSTAR_COMMIT)
        self.assertEqual(pins["h5_shadow.py"], "7f859b12deef87573a7314e49e125b5121eaa645")
        self.assertEqual(pins["h5_shadow_tip.py"], "05c5489a786dac4eb0696a74279a0f789ddb1deb")

    def test_main_h5_shadow_is_not_the_pinned_blob(self):
        """Why the engine comes from a worktree and not from this checkout."""
        self.assertNotEqual(C.blob_sha1((REPO / "tools" / "h5_shadow.py").read_bytes()), C.SSTAR_BLOBS["tools/h5_shadow.py"])

    def test_dirty_untracked_and_edited_are_refused(self):
        src = sstar()
        extra = src / "tools" / "stray_untracked.py"
        extra.write_text("x = 1\n")
        try:
            with self.assertRaises(C.Refused) as cm:
                C.check_sstar_src(src)
            self.assertEqual(str(cm.exception), "sstar_tree_dirty")
        finally:
            extra.unlink()
        f = src / "tools" / "h5_shadow_tip.py"
        orig = f.read_bytes()
        try:
            f.write_bytes(orig + b"\n# edit\n")
            with self.assertRaises(C.Refused) as cm:
                C.check_sstar_src(src)
            self.assertEqual(str(cm.exception), "sstar_blob_mismatch")
        finally:
            f.write_bytes(orig)
        self.assertEqual(C.check_sstar_src(src)["commit"], C.SSTAR_COMMIT)

    def test_wrong_head_is_refused(self):
        wt = self.root / "other"
        r = subprocess.run(["git", "-C", str(REPO), "worktree", "add", "--detach", str(wt), "HEAD"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.addCleanup(lambda: subprocess.run(["git", "-C", str(REPO), "worktree", "remove", "--force", str(wt)], capture_output=True))
        with self.assertRaises(C.Refused) as cm:
            C.check_sstar_src(wt)
        self.assertEqual(str(cm.exception), "sstar_head_mismatch")


class Counting(Tmp):
    def build(self, **kw):
        rows = pool_rows(POOL_A, MINT_A, ts("2026-11-07", 12), 400_000_000)                      # fires on 11-07
        rows += pool_rows(POOL_B, MINT_B, ts("2026-11-05", 12), 399_000_000)                     # fires on 11-05: history, never counted
        rows += pool_rows(POOL_C, MINT_C, ts("2026-11-08", 3), 401_000_000, target_q=70.0)       # never reaches Q <= 50: no structure
        write_tape(self.tape, rows, **kw)

    def test_a_triggered_pool_is_counted_once_on_its_day(self):
        self.build()
        res = self.count()
        d = res["per_day"]
        self.assertEqual([d[x]["records_pv"] for x in DAYS.split(",")], [0, 1, 0])
        self.assertGreaterEqual(d["2026-11-07"]["records_fv"], 0)                                 # report-only
        self.assertEqual(res["p0_per_day"], sum(d[x]["records_pv"] for x in DAYS.split(",")) / 3)  # unrounded mean of records_pv (the fixture cannot make an fv-only pool: pv and fv cross together)
        self.assertEqual(res["p0_per_day"], 1 / 3)
        self.assertTrue(res["p0_day_zero"])
        self.assertIn("upper bound", res["disclosure"])
        self.assertIn("REPORT-ONLY", res["unit"])
        self.assertEqual(res["undercount_days"], [])
        self.assertTrue(res["outcome_free"] and res["pricing_off"])
        self.assertEqual(res["sstar"]["commit"], C.SSTAR_COMMIT)
        self.assertEqual(res["history_hours_missing_before_day_1"], 0)

    def test_output_has_a_fixed_key_set_and_no_pool_level_text(self):
        self.build()
        res = self.count()
        self.assertEqual(set(res), {"s1t_tip_p0", "purpose", "sstar", "pricing_off", "outcome_free", "disclosure", "first_day", "last_day", "h0", "notebook_ref", "unit", "per_day", "p0_per_day",
                                    "p0_day_zero", "undercount_days", "history_hours_missing_before_day_1", "hour_files_before_h0_skipped_by_name", "input_counters"})
        self.assertEqual(set(res["per_day"]["2026-11-07"]), {"records_pv", "records_fv", "hours_present", "hours_expected", "hours_missing", "hour_before_missing", "undercount"})
        self.assertTrue(set(res["input_counters"]) <= set(C.KEEP_COUNTERS))
        text = json.dumps(res)
        for token in (POOL_A, MINT_A, POOL_B, MINT_B, "sig", "q_trigger", "exit_", "pnl"):
            self.assertNotIn(token, text)

    def test_no_file_is_written_into_tape_or_sstar_worktree(self):
        self.build()
        names = sorted(p.name for p in self.tape.iterdir())
        self.count()
        self.assertEqual(subprocess.run(["git", "-C", str(sstar()), "status", "--porcelain", "--untracked-files=all"], capture_output=True, text=True).stdout, "")
        self.assertEqual(sorted(p.name for p in self.tape.iterdir()), names)

    def test_a_missing_hour_is_a_declared_undercount_and_still_counted(self):
        self.build(skip=("2026-11-07T20",))
        res = self.count()
        self.assertEqual(res["undercount_days"], ["2026-11-07"])
        self.assertEqual(res["per_day"]["2026-11-07"]["hours_missing"], ["2026-11-07T20"])
        self.assertEqual(res["per_day"]["2026-11-07"]["hours_present"], 23)
        self.assertEqual(res["per_day"]["2026-11-07"]["records_pv"], 1)                                # still counted
        self.assertFalse(res["per_day"]["2026-11-06"]["undercount"])

    def test_the_hour_before_a_day_missing_declares_that_day(self):
        self.build(skip=("2026-11-07T23",))
        res = self.count()
        self.assertEqual(res["undercount_days"], ["2026-11-07", "2026-11-08"])
        self.assertTrue(res["per_day"]["2026-11-08"]["hour_before_missing"])

    def test_history_gap_before_day_1_is_reported_not_refused(self):
        self.build(skip=("2026-11-05T07", "2026-11-05T08"))
        self.assertEqual(self.count()["history_hours_missing_before_day_1"], 2)

    def test_hour_files_before_h0_are_skipped_by_name_and_never_opened(self):
        self.build(start="2026-11-04T00")
        (self.tape / "tape-2026-11-04T10.jsonl").write_text("{not json\n" * 50)           # would count as bad_lines if opened
        res = self.count()
        self.assertEqual(res["hour_files_before_h0_skipped_by_name"], 24)
        self.assertEqual(res["input_counters"].get("reader_loss_bad_lines", 0), 0)
        self.assertEqual(res["per_day"]["2026-11-07"]["records_pv"], 1)

    def test_rows_at_or_after_the_end_are_never_read_or_counted(self):
        self.build()
        late = pool_rows(POOL_B, MINT_B, ts("2026-11-09", 5), 402_000_000)                    # fires on 11-09: outside the days
        write_tape(self.tape, pool_rows(POOL_A, MINT_A, ts("2026-11-07", 12), 400_000_000) + late[:3], end="2026-11-09T00")
        (self.tape / "tape-2026-11-09T05.jsonl").write_text("{not json\n" * 50)              # an 11-09 hour file: must never be opened
        (self.tape / "tape-2026-11-10T05.jsonl").write_text("{not json\n" * 50)
        res = self.count()
        self.assertEqual(res["input_counters"].get("reader_loss_bad_lines", 0), 0)
        self.assertEqual(sum(v["records_pv"] for v in res["per_day"].values()), 1)

    def test_a_row_past_the_end_inside_the_last_hour_file_stops_the_read(self):
        rows = pool_rows(POOL_A, MINT_A, ts("2026-11-07", 12), 400_000_000)
        late = pool_rows(POOL_B, MINT_B, ts("2026-11-08", 23) + 3599, 402_000_000)              # its rows run past 11-09T00:00 yet sit in the 11-08T23 file
        late = [dict(r, t_recv_ms=r["t_recv_ms"]) for r in late]
        d = self.tape
        write_tape(d, rows)
        with open(d / "tape-2026-11-08T23.jsonl", "a") as f:
            for r in late:
                f.write(json.dumps(r, separators=(",", ":")) + "\n")
        res = self.count()
        self.assertGreater(res["input_counters"]["rows_at_or_after_end"], 0)
        self.assertEqual(sum(v["records_pv"] for v in res["per_day"].values()), 1)

    def test_a_short_last_day_is_declared_not_refused(self):
        self.build(end="2026-11-08T12")
        res = self.count()
        self.assertEqual(res["undercount_days"], ["2026-11-08"])
        self.assertEqual(res["per_day"]["2026-11-08"]["hours_present"], 12)


class Boundaries(unittest.TestCase):
    def test_utc_day_boundaries(self):
        s, e = C.day_ms("2026-11-06"), C.day_ms("2026-11-09")
        D = 86_400_000
        self.assertIsNone(C.bucket(s - 1, s, e))
        self.assertEqual(C.bucket(s, s, e), "2026-11-06")
        self.assertEqual(C.bucket(s + D - 1, s, e), "2026-11-06")
        self.assertEqual(C.bucket(s + D, s, e), "2026-11-07")
        self.assertEqual(C.bucket(s + 2 * D, s, e), "2026-11-08")
        self.assertEqual(C.bucket(e - 1, s, e), "2026-11-08")
        self.assertIsNone(C.bucket(e, s, e))
        for bad in (None, "x", True, 1.5):
            self.assertIsNone(C.bucket(bad, s, e))


class Hygiene(Tmp):
    def test_test_switch_only_in_test_runs(self):
        self.assertNotIn("MAL_ARENA_TEST", C.child_env(None))
        self.assertEqual(C.child_env([str(self.root)])["MAL_ARENA_TEST"], "1")

    def test_a_production_run_has_no_test_switch(self):
        seen = {}
        real = subprocess.run

        def spy(cmd, *a, **k):
            if "--child" in cmd:
                seen["env"] = k["env"]
            return real(cmd, *a, **k)
        with mock.patch.object(C.subprocess, "run", spy), self.assertRaises(RuntimeError):
            C.run_engine(str(self.root), self.tape, DAYS.split(","), C.day_ms("2026-11-05"), roots=None)      # fails inside the child; the env is what is checked
        self.assertNotIn("MAL_ARENA_TEST", seen["env"])

    def test_error_texts_are_fixed_codes_only(self):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(C, "run_p0", side_effect=RuntimeError("Pool" + POOL_A[:30])), redirect_stdout(out), redirect_stderr(err):
            rc = C.main(["--days", DAYS, "--notebook-ref", "nb", "--h0", H0, "--sstar-src", "/x"])
        self.assertEqual(rc, 3)
        self.assertNotIn(POOL_A[:10], err.getvalue())
        self.assertEqual(json.loads(err.getvalue())["code"], "")

    def test_timeout_is_configurable_with_a_long_default(self):
        self.assertGreaterEqual(C.TIMEOUT_S_DEFAULT, 10 * 3600)
        with mock.patch.object(C, "run_p0", return_value={"ok": 1}) as m, redirect_stdout(io.StringIO()):
            C.main(["--days", DAYS, "--notebook-ref", "nb", "--h0", H0, "--sstar-src", "/x", "--timeout-s", "99"])
        self.assertEqual(m.call_args.kwargs["timeout_s"], 99)


class OutcomeGuard(Tmp):
    def tampered(self) -> Path:
        dst = self.root / "tampered"
        shutil.copytree(sstar() / "tools", dst / "tools", ignore=shutil.ignore_patterns("__pycache__"))
        f = dst / "tools" / "h5_shadow_tip.py"
        s = f.read_text()
        old = "engine.h5_look2_start_ms, engine.h5_look2_observed = 0, False"
        self.assertIn(old, s)
        f.write_text(s.replace(old, "engine.h5_look2_start_ms, engine.h5_look2_observed = None, False"))     # the Look-2 seal OFF: outcomes would be priced
        return dst

    def test_an_engine_that_prices_outcomes_is_exit_3_with_no_counts(self):
        dst = self.tampered()
        write_tape(self.tape, pool_rows(POOL_A, MINT_A, ts("2026-11-07", 12), 400_000_000))
        real = C.run_p0
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(C, "check_sstar_src", return_value={"commit": "x"}), \
                mock.patch.object(C, "run_p0", side_effect=lambda *a, **k: real(*a, tape_dir=self.tape, now_ms=NOW, **k)), redirect_stdout(out), redirect_stderr(err):
            rc = C.main(["--days", DAYS, "--notebook-ref", "nb-1", "--h0", H0, "--sstar-src", str(dst)])
        self.assertEqual(rc, 3)
        self.assertEqual(out.getvalue(), "")
        self.assertIn('"error"', err.getvalue())
        self.assertNotIn(POOL_A, err.getvalue())

    def test_run_engine_raises_on_the_tampered_engine(self):
        dst = self.tampered()
        write_tape(self.tape, pool_rows(POOL_A, MINT_A, ts("2026-11-07", 12), 400_000_000))
        with self.assertRaises(RuntimeError):
            C.run_engine(str(dst), self.tape, DAYS.split(","), C.day_ms("2026-11-05"), roots=[str(self.root)])

    def test_the_pinned_engine_emits_no_outcome_type(self):
        write_tape(self.tape, pool_rows(POOL_A, MINT_A, ts("2026-11-07", 12), 400_000_000))
        res = C.run_engine(str(sstar()), self.tape, DAYS.split(","), C.day_ms("2026-11-05"), roots=[str(self.root)])
        self.assertEqual(res["blocked"], 0)
        self.assertEqual(res["per_day"]["2026-11-07"]["records_pv"], 1)


if __name__ == "__main__":
    unittest.main()
