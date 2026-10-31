"""Tests for tools/q50_strip_dup_audit.py (A-Q50, QP-RULING-1031-tape-dupes-reads section 4.2). SYNTHETIC fixtures in tmp dirs only: no Q50 or s1 shadow dir, strip, ledger,
instant record or read output is opened. The ledger rows are written by the code that writes them (arena_h5_score.price_trade / ledger_rows / write_ledger), the Amendment 11 records by
tools/arena_h5_q50_instant.record_doc / record (s1: tools/arena_h5_q50s_instant), and the recorded read output by tools/arena_h5_read.main itself, made a-w as the read job makes it.

Pools (tools/test_arena_h5_legs.pool_records geometry): trigger slot 1200, sps 0.4, so the 1p3 landing slot is 1204, the 1p9 landing slot 1205, the exit landing slot 1802, and the
pressure window is round(2 / 0.4) = 5 slots. Injected kinds (each pool knows its GENUINE buys, so the corrected leg is restated here without the tool's repeat detection):
  late_buy      the landing-slot buy (1205) and its print are re-delivered after higher slots: a repeat print and a repeat buy (1p9 ssb +1, 1p9 window +1)
  late_window   the same at slot 1202 (in both legs' 2 s windows, in no landing slot)
  pre_from      a late exact repeat of a buy at 1199, before from_slot (no rows there): counted on its own (1p3 window only)
  post_last     the print of slot 1206 re-delivered after slot 1700 is the strip's last row, and the exit (1802) reads post_last: entry/exit-from-a-repeated-last-print on both legs
  twin          a late same-slot, same-size buy whose print has ANOTHER pre-state: a genuine twin, never counted

Run: /data/mal/venv/bin/python -m pytest -q tools/test_q50_strip_dup_audit.py   (or the read's venv /home/claude/venvs/c1nf-ledger/bin/python3)
"""

from __future__ import annotations

import collections
import contextlib
import io
import json
import os
import shutil
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools import arena_h5_cost as cost
from tools import arena_h5_legs as L
from tools import arena_h5_q50_instant as qi
from tools import arena_h5_q50s_instant as s1i
from tools import arena_h5_read as R
from tools import arena_h5_score as h5s
from tools import arena_score
from tools import q50_strip_dup_audit as A
from tools.test_arena_h5_books_v3 import V2_TEXT, V3_TEXT
from tools.test_arena_h5_legs import pool_records, r

Q50, S1 = qi.Q50_ID, s1i.S1_ID
SHA = "a" * 40
QX = {"win": 46e9, "win2": 47e9, "win3": 45e9, "loss": 41e9}
LAND13, LAND19, W = 1204, 1205, 5
TEST_ENV = {A.TEST_ENV: "1"}
TOTAL_LOSS = 300_000_000 + 505_000


def default_kind(d: int, i: int) -> str:
    return "loss" if i % 12 == 11 else ("win", "win2", "win3")[i % 3]


def pool(d: int, i: int, bt0: int, inject: str | None, qpost: float | None = None) -> tuple[dict, dict, dict, list]:
    """(trigger, outcome, strip, genuine buys) of one pool. `buys` in the strip = the genuine buys plus the injected repeats, in arrival order."""
    kind = default_kind(d, i)
    g = [[1150, 100_000_000], [1201, 200_000_000 + i * 1_000_000]] + [[1205, 300_000_000 + k] for k in range(i % 3)]
    rows = [r(1200, 40e9, 2.0e14), r(1206, 40e9, 2.0e14), r(1803, QX[kind], 2.0e14)]
    post = [1900, QX[kind], QX[kind], 2.0e14]
    late_rows: list = []
    late_buys: list = []
    if inject == "late_buy":
        rows.insert(1, r(1205, 40.1e9, 1.995e14))
        g.append([1205, 777_000_000])
        late_rows, late_buys = [r(1205, 40.1e9, 1.995e14)], [[1205, 777_000_000]]
        post = [1205, 40.5e9, 40.5e9, 1.985e14]  # the state after the re-delivered print: stale, but the trade never reads it (1803 is after the exit landing)
    elif inject == "late_window":
        rows.insert(1, r(1202, 40.05e9, 1.998e14))
        g.insert(2, [1202, 600_000_000])
        late_rows, late_buys = [r(1202, 40.05e9, 1.998e14)], [[1202, 600_000_000]]
        post = [1202, 40.3e9, 40.3e9, 1.99e14]
    elif inject == "pre_from":
        g.insert(1, [1199, 400_000_000])
        late_buys = [[1199, 400_000_000]]
    elif inject == "post_last":
        q = QX[kind] if qpost is None else qpost
        rows = [r(1200, 40e9, 2.0e14), r(1206, 40e9, 2.0e14), r(1700, 44e9, 2.0e14)]
        late_rows = [r(1206, 40e9, 2.0e14)]
        post = [1206, q, q, 2.0e14]  # the exit (1802) has no later row: it reads post_last, which follows a re-delivered print
    elif inject == "twin":
        rows.insert(1, r(1205, 40.1e9, 1.995e14))
        g.append([1205, 500_000_000])
        late_rows = [r(1205, 40.3e9, 1.985e14)]  # a late print in 1205 with ANOTHER pre-state: not a repeat print
        g_late = [[1205, 500_000_000]]  # so the late equal buy is a genuine twin
    buys = g + [[1300, 50_000_000]] + late_buys
    genuine = g + [[1300, 50_000_000]]
    if inject == "twin":
        buys, genuine = buys + g_late, genuine + g_late
    trig, out, strip = pool_records(f"P{d}x{i:02d}", rows + late_rows, post, bt=bt0 + d * 86400 + i * 60, buys=buys)
    return trig, out, strip, genuine


def expected_ssb_nb(genuine: list, land: int) -> tuple[int, int]:
    return sum(1 for s, _ in genuine if s == land), int(sum(x for s, x in genuine if land - W <= s <= land))


def build(root: Path, *, bot: str = Q50, inject=lambda d, i: None, qpost: float | None = None, per_day: int = 24) -> dict:
    """A recorded read of a synthetic book: ledger, Amendment 11 record, shadow dir, books file and the read's own --json output (a-w). Returns paths, the read instant and the pools."""
    s1 = bot == S1
    clock, end = (s1i.CLOCK_MS, s1i.END_MS) if s1 else (1_792_886_400_000, qi.END_SEAL_MS)
    bt0 = clock // 1000 + 3600
    start = {"type": "start", "t_ms": clock - 5_000, "start_ms": clock - 5_000, "code_sha": SHA, "fresh_forward": "ARENA", "dirty": False}
    arena, out = root / "arena", root / "out"
    ledger, shadow = arena / "ledger", arena / ("h5-q50s1-2026-11-05" if s1 else "h5-q50-2026-10-24")
    for p in (ledger, shadow, out):
        p.mkdir(parents=True, exist_ok=True)
    rows, recs, pools = [], [], {}
    for d in range(5):
        for i in range(per_day):
            kind = inject(d, i)
            trig, o, strip, genuine = pool(d, i, bt0, kind, qpost)
            priced = h5s.price_trade(bot, o, trig, strip, None, None, start)
            rows += h5s.ledger_rows(bot, o, trig, priced, start, None, None)
            recs += [trig, strip]
            pools[o["pool"]] = {"inject": kind, "genuine": genuine, "strip": strip}
    with mock.patch.object(h5s.os, "fsync"):
        h5s.write_ledger(bot, rows, [], ledger, collections.Counter())
    counted = sorted({x["pool"]: x["decision_ms"] for x in rows}.values())
    cls = {"counted": counted, "excluded": [], "open": [], "refused": []}
    mod = s1i if s1 else qi
    doc = mod.record_doc(bot, cls, clock_start_ms=clock, end_ms=end, now_ms=2**62, attempt_definition="fixture")
    assert doc and doc["status"] == qi.READ, doc
    mod.record(ledger / bot, doc)
    (shadow / "h5-shadow-2026-10-25T00.jsonl").write_text("".join(json.dumps(x) + "\n" for x in recs))
    books = out / "books.json"
    books.write_text(V3_TEXT if s1 else V2_TEXT)
    read = out / "q50-read.json"
    ri = int(doc["read_instant_ms"])
    argv = ["--books-file", str(books), "--ledger-root", str(ledger), "--read-instant", str(ri), "--i-am-the-read", "--now-ms", str(ri + 1000), "--n-books", "14", "--c", "1",
            "--bot", bot, "--json", str(read)]
    with mock.patch.object(R, "wall_ms", return_value=2**62), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        rc = R.main(argv)
    assert rc == 0, rc
    os.chmod(read, 0o444)
    return {"root": root, "ledger": ledger, "shadow": shadow, "books": books, "read": read, "read_sha": A.sha256_file(read), "ri": ri, "pools": pools, "bot": bot,
            "status": json.loads(read.read_text())["books"][bot]["status"]}


def args_for(fx: dict, *, out: Path | None = None, read: Path | None = None, read_sha: str | None = None, now_ms: int | None = None, shadow: list | None = None,
             ledger: Path | None = None) -> object:
    argv = ["--bot", fx["bot"], "--ledger-root", str(ledger or fx["ledger"]), "--books-file", str(fx["books"]), "--read-json", str(read or fx["read"]),
            "--read-json-sha256", read_sha or fx["read_sha"], "--json", str(out or (fx["root"] / "out" / f"a-q50-{os.urandom(4).hex()}.json"))]
    for d in (shadow or [fx["shadow"]]):
        argv += ["--shadow-dir", str(d)]
    if now_ms is not None:
        argv += ["--now-ms", str(now_ms)]
    return A.parser().parse_args(argv)


def run(fx: dict, **kw) -> tuple[int, dict]:
    env = kw.pop("env", TEST_ENV)
    host = kw.pop("host", "research-test")
    return A.audit(args_for(fx, **kw), env=env, host=host)


def dec_rows(fx: dict) -> list[dict]:
    led = h5s.ledger_attempts_before(fx["ledger"] / fx["bot"], fx["ri"])
    return [x for x in led["rows"] if f"{x['stake_sol']:.2f}" == "0.30"]


# ------------------------------------------------------------------------------------------------------------------------------ item 3, pure functions
class RepeatDefinition(unittest.TestCase):
    def test_a_late_exact_repeat_print_is_counted(self):
        rows = [r(1200, 40e9, 2e14), r(1205, 41e9, 2e14), r(1300, 42e9, 2e14), r(1205, 41e9, 2e14)]
        self.assertEqual(A.repeat_print_index(rows), [3])

    def test_a_genuine_same_slot_twin_with_another_pre_state_is_not_a_repeat(self):
        rows = [r(1200, 40e9, 2e14), r(1205, 41e9, 2e14), r(1300, 42e9, 2e14), r(1205, 41.2e9, 1.99e14)]
        self.assertEqual(A.repeat_print_index(rows), [])
        buys = [[1205, 5e8], [1300, 1e8], [1205, 5e8]]  # late and equal, but no repeat print exists in 1205
        self.assertEqual(A.repeat_buy_index(buys, rows, 1200), [])

    def test_an_equal_row_that_is_not_late_is_not_a_repeat(self):
        rows = [r(1200, 40e9, 2e14), r(1205, 41e9, 2e14), r(1205, 41e9, 2e14), r(1300, 42e9, 2e14)]
        self.assertEqual(A.repeat_print_index(rows), [])
        self.assertEqual(A.repeat_buy_index([[1205, 5e8], [1205, 5e8], [1300, 1e8]], rows, 1200), [])

    def test_a_late_equal_buy_counts_only_with_a_repeat_print_at_its_slot(self):
        rows = [r(1200, 40e9, 2e14), r(1205, 41e9, 2e14), r(1300, 42e9, 2e14), r(1205, 41e9, 2e14)]
        self.assertEqual(A.repeat_buy_index([[1205, 5e8], [1300, 1e8], [1205, 5e8]], rows, 1200), [2])
        self.assertEqual(A.repeat_buy_index([[1206, 5e8], [1300, 1e8], [1206, 5e8]], rows, 1200), [])  # no repeat print in 1206
        self.assertEqual(A.repeat_buy_index([[1205, 5e8], [1300, 1e8], [1205, 6e8]], rows, 1200), [])  # not equal to an earlier entry

    def test_before_from_slot_the_late_exact_repeat_alone_suffices(self):
        rows = [r(1200, 40e9, 2e14), r(1300, 42e9, 2e14)]
        self.assertEqual(A.repeat_buy_index([[1199, 4e8], [1250, 1e8], [1199, 4e8]], rows, 1200), [2])
        self.assertEqual(A.repeat_buy_index([[1199, 4e8], [1199, 4e8], [1250, 1e8]], rows, 1200), [])  # equal but in order: not late

    def test_window_is_the_scorers(self):
        self.assertEqual(A.pressure_window_slots(0.4), 5)
        self.assertEqual(A.pressure_window_slots(0.45), int(round(2 / 0.45)))
        self.assertEqual(A.TOTAL_LOSS_LAMPORTS, TOTAL_LOSS)


# ------------------------------------------------------------------------------------------------------------------------------ end to end
def dirty_kind(d: int, i: int) -> str | None:
    return {3: "late_buy", 5: "late_window", 7: "post_last", 9: "twin", 10: "pre_from"}.get(i)


class EndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="aq50-e2e-")).resolve()
        cls.clean = build(cls.tmp / "clean", inject=lambda d, i: "twin" if i == 9 else None)
        cls.dirty = build(cls.tmp / "dirty", inject=dirty_kind)
        cls.block = build(cls.tmp / "block", inject=lambda d, i: "post_last" if i % 2 == 0 else None, qpost=47e9)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_fixtures_are_recorded_pass_reads(self):
        for fx in (self.clean, self.dirty, self.block):
            self.assertEqual(fx["status"], "PASS")
            self.assertFalse(os.stat(fx["read"]).st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))

    def test_the_read_priced_the_repeats(self):
        """The registered scorer cannot tell a repeat buy from a genuine one: the ledger's 1p9 ssb of a late_buy pool includes the repeat."""
        for row in dec_rows(self.dirty):
            p = self.dirty["pools"][row["pool"]]
            if p["inject"] == "late_buy":
                self.assertEqual(row["legs"]["press_1p9"]["ssb"], expected_ssb_nb(p["genuine"], LAND19)[0] + 1)

    def test_clean_book_is_clean(self):
        rc, out = run(self.clean)
        self.assertEqual(rc, A.EXIT_CLEAN)
        self.assertEqual(out["result"], "A-Q50 clean")
        self.assertTrue(out["qp_ok_condition"]["met"])
        self.assertNotIn("corrected_pressure_leg", out)
        self.assertEqual(out["counted_trades"], 120)
        for sfx in A.SFXS:
            t = out["counts_by_leg"][sfx]
            self.assertEqual((t["ssb_repeat_buys"], t["window_repeat_buys"], t["entry_post_last_repeat"], t["exit_post_last_repeat"], t["trades_flagged"]), (0, 0, 0, 0, 0))
            self.assertEqual(out["trade_ids_flagged_by_leg"][sfx], [])
        self.assertEqual((out["strip_totals"]["repeat_prints"], out["strip_totals"]["repeat_buys"]), (0, 0))  # the five twin pools are genuine

    def test_dirty_book_counts(self):
        rc, out = run(self.dirty)
        self.assertEqual(rc, A.EXIT_REPEATS)
        t9, t3 = out["counts_by_leg"]["1p9"], out["counts_by_leg"]["1p3"]
        self.assertEqual((t9["ssb_repeat_buys"], t9["window_repeat_buys"], t9["entry_post_last_repeat"], t9["exit_post_last_repeat"], t9["trades_flagged"]), (5, 10, 0, 5, 15))
        self.assertEqual((t3["ssb_repeat_buys"], t3["window_repeat_buys"], t3["entry_post_last_repeat"], t3["exit_post_last_repeat"], t3["trades_flagged"]), (0, 10, 0, 5, 15))
        self.assertEqual((out["strip_totals"]["repeat_prints"], out["strip_totals"]["repeat_buys"], out["strip_totals"]["strips"]), (15, 15, 120))
        want9 = sorted(f"{Q50}:{p}:0.30" for p, v in self.dirty["pools"].items() if v["inject"] in ("late_buy", "late_window", "post_last"))
        want3 = sorted(f"{Q50}:{p}:0.30" for p, v in self.dirty["pools"].items() if v["inject"] in ("late_window", "pre_from", "post_last"))
        self.assertEqual(out["trade_ids_flagged_by_leg"]["1p9"], want9)
        self.assertEqual(out["trade_ids_flagged_by_leg"]["1p3"], want3)
        self.assertFalse(any(v["inject"] == "twin" for p, v in self.dirty["pools"].items() if f"{Q50}:{p}:0.30" in out["per_trade_flagged"]))

    def test_corrected_leg_matches_an_independent_restatement(self):
        """Item 6 restated here from the fixture's GENUINE buys (not the tool's repeat detection), arena_h5_cost's functions and arena_score.cell_stats."""
        rc, out = run(self.dirty)
        cp = out["corrected_pressure_leg"]
        rows = dec_rows(self.dirty)
        ss = [expected_ssb_nb(self.dirty["pools"][x["pool"]]["genuine"], LAND19) for x in rows]
        filled = [cost.is_filled(x["legs"]["flat_1p9"]["state"]) for x in rows]
        c = cost.press_intercept([a for a, _ in ss], [b for _, b in ss], filled)
        self.assertAlmostEqual(cp["intercept"], c, places=12)
        read = json.loads(self.dirty["read"].read_text())["books"][Q50]
        self.assertNotAlmostEqual(cp["intercept"], read["refit"]["intercept"], places=6)  # the repeats moved the refit
        days = [h5s.utc_day(x["decision_ms"]) for x in rows]
        tl = {x["trade_id"] for x in rows if self.dirty["pools"][x["pool"]]["inject"] == "post_last"}
        self.assertEqual(set(cp["trades_valued_at_total_loss"]), tl)
        self.assertEqual(len(tl), 5)
        for rule in ("total", "sc"):
            vals = []
            for x, (ssb, nb) in zip(rows, ss):
                leg = x["legs"]["flat_1p9"] if rule == "total" else x["legs"]["flat_1p9"]["sc"]
                v = cost.press_leg(leg["nofail_lamports"], leg["state"], cost.press_p(c, ssb, nb))
                vals.append(-TOTAL_LOSS if x["trade_id"] in tl else v)
            want = arena_score.cell_stats(vals, days)
            got = cp["cells"][f"press_1p9_{rule}"]
            for f in ("n", "days", "days_pos"):
                self.assertEqual(got[f], want[f], (rule, f))
            for f in ("mean_sol", "total_sol", "ci90_lo_sol", "ex_top3_sol", "ex_best_day_sol"):
                self.assertAlmostEqual(got[f], want[f], places=12, msg=(rule, f))
            self.assertEqual(got["passes"], arena_score.cell_verdict(want)["pass"])
        self.assertEqual(out["qp_ok_condition"]["met"], cp["passes_both_strand_rules"])
        self.assertEqual(sorted(out["recorded_cells_pass"]), sorted(R.SUBCELLS))

    def test_a_defect_blocks_a_pass_it_never_creates_one(self):
        """The read PASSed on stale post_last exits; valued at total loss the corrected pressure leg fails, so QP's OK condition is not met. The recorded read is untouched."""
        before = A.sha256_file(self.block["read"])
        rc, out = run(self.block)
        self.assertEqual(rc, A.EXIT_REPEATS)
        cp = out["corrected_pressure_leg"]
        self.assertEqual(len(cp["trades_valued_at_total_loss"]), 60)
        self.assertFalse(cp["passes_both_strand_rules"])
        self.assertFalse(out["qp_ok_condition"]["met"])
        self.assertTrue(all(out["recorded_cells_pass"].values()))
        self.assertEqual(A.sha256_file(self.block["read"]), before)

    def test_cli_writes_once_and_prints_no_figure(self):
        outp = self.tmp / "cli-out.json"
        argv = ["--ledger-root", str(self.dirty["ledger"]), "--books-file", str(self.dirty["books"]), "--shadow-dir", str(self.dirty["shadow"]),
                "--read-json", str(self.dirty["read"]), "--read-json-sha256", self.dirty["read_sha"], "--json", str(outp)]
        so, se = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, TEST_ENV), contextlib.redirect_stdout(so), contextlib.redirect_stderr(se):
            rc = A.main(argv)
        self.assertEqual(rc, A.EXIT_REPEATS, se.getvalue())
        self.assertEqual(stat.S_IMODE(os.stat(outp).st_mode), 0o444)
        text = so.getvalue()
        self.assertIn("A-Q50 COUNTS leg=1p9 ssb_repeat_buys=5 window_repeat_buys=10 entry_post_last_repeat=0 exit_post_last_repeat=5 trades_flagged=15", text)
        self.assertIn("A-Q50 RESULT repeats found", text)
        self.assertIn(f"output {outp} sha256 {A.sha256_file(outp)}", text)
        self.assertNotIn("P0x", text)  # no pool or trade id on stdout
        self.assertNotIn("sol", text.lower().replace("solana", ""))  # no SOL figure on stdout
        with mock.patch.dict(os.environ, TEST_ENV), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(se):
            self.assertEqual(A.main(argv), A.EXIT_REFUSED)  # write-once
        self.assertIn("write-once", se.getvalue())


# ------------------------------------------------------------------------------------------------------------------------------ refusals
class Refusals(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="aq50-ref-")).resolve()
        cls.fx = build(cls.tmp / "fx", inject=dirty_kind)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def copy(self) -> dict:
        dst = Path(tempfile.mkdtemp(prefix="aq50-c-", dir=self.tmp))
        shutil.copytree(self.fx["root"], dst / "fx")
        root = dst / "fx"
        return dict(self.fx, root=root, ledger=root / "arena" / "ledger", shadow=root / "arena" / self.fx["shadow"].name, books=root / "out" / "books.json",
                    read=root / "out" / "q50-read.json")

    def refused(self, fx: dict, needle: str, **kw) -> None:
        with self.assertRaises(A.Refused) as cm:
            run(fx, **kw)
        self.assertIn(needle, str(cm.exception))

    def rewrite_read(self, fx: dict, edit) -> str:
        rep = json.loads(fx["read"].read_text())
        edit(rep)
        os.chmod(fx["read"], 0o644)
        fx["read"].write_text(json.dumps(rep))
        os.chmod(fx["read"], 0o444)
        return A.sha256_file(fx["read"])

    def test_a_read_that_is_not_pass_is_never_reopened(self):
        """Item 1: FAIL, FAIL (n < 100) and NOT_DECIDABLE stop the tool before the seal, the ledger or any strip is touched."""
        cases = ((lambda rep: rep["books"][Q50].update(status="FAIL"), "is not PASS: a FAIL or NOT_DECIDABLE is final"),
                 (lambda rep: rep["books"][Q50].update(status="FAIL", fail_label="FAIL (n < 100)"), "is not PASS: a FAIL or NOT_DECIDABLE is final"),
                 (lambda rep: rep["books"][Q50].update(status="NOT_DECIDABLE"), "is not PASS: a FAIL or NOT_DECIDABLE is final"),
                 (lambda rep: rep["books"].pop(Q50), "holds no recorded read of"))
        for edit, needle in cases:
            fx = self.copy()
            sha = self.rewrite_read(fx, edit)
            boom = mock.Mock(side_effect=AssertionError("opened"))
            with mock.patch.object(L, "gate", boom), mock.patch.object(L, "load_strips", boom), mock.patch.object(R, "counts_and_checks", boom), \
                    mock.patch.object(h5s, "read_records", boom), mock.patch.object(h5s, "ledger_attempts_before", boom):
                self.refused(fx, needle, read_sha=sha)
            boom.assert_not_called()

    def test_the_read_record_must_be_recorded_and_pinned(self):
        fx = self.copy()
        self.refused(fx, "is not --read-json-sha256", read_sha="0" * 64)
        self.refused(fx, "no recorded read output", read=fx["root"] / "out" / "absent.json")
        os.chmod(fx["read"], 0o644)
        self.refused(fx, "is writable")

    def test_existing_output_is_refused_first(self):
        fx = self.copy()
        outp = fx["root"] / "out" / "exists.json"
        outp.write_text("{}")
        self.refused(fx, "write-once", out=outp)

    def test_an_altered_strip_is_not_the_strip_the_scorer_priced(self):
        fx = self.copy()
        f = fx["shadow"] / "h5-shadow-2026-10-25T00.jsonl"
        lines = [json.loads(x) for x in f.read_text().splitlines()]
        for rec in lines:
            if rec["type"] == "strip" and rec["pool"] == "P0x00":
                rec["buys"].append([1205, 1_000_000])  # one more buy in the landing slot: ssb differs from the ledger row
        f.write_text("".join(json.dumps(x) + "\n" for x in lines))
        self.refused(fx, "not the strip the scorer priced")

    def test_a_missing_strip_is_refused(self):
        fx = self.copy()
        f = fx["shadow"] / "h5-shadow-2026-10-25T00.jsonl"
        f.write_text("".join(x + "\n" for x in f.read_text().splitlines() if '"P1x04"' not in x or '"strip"' not in x))
        self.refused(fx, "no strip record")

    def test_the_trade_set_must_be_the_reads(self):
        fx = self.copy()
        sha = self.rewrite_read(fx, lambda rep: rep["books"][Q50]["subcells"]["press_1p9_sc"].update(mean_sol=rep["books"][Q50]["subcells"]["press_1p9_sc"]["mean_sol"] + 1e-9))
        self.refused(fx, "press_1p9_sc", read_sha=sha)
        fx = self.copy()
        sha = self.rewrite_read(fx, lambda rep: rep["books"][Q50]["refit"].update(intercept=rep["books"][Q50]["refit"]["intercept"] + 1e-9))
        self.refused(fx, "refit intercept", read_sha=sha)
        fx = self.copy()
        sha = self.rewrite_read(fx, lambda rep: rep["books"][Q50]["counts"].update(n_counted=119))
        self.refused(fx, "n_counted", read_sha=sha)
        fx = self.copy()
        sha = self.rewrite_read(fx, lambda rep: rep["books"][Q50]["provenance"].update({"tools/arena_h5_cost.py": "0" * 64}))
        self.refused(fx, "registered cost functions", read_sha=sha)

    def test_production_refuses_every_test_override(self):
        fx = self.copy()
        for env in ({"MAL_AQ50_TEST": "0"}, {"MAL_AQ50_TEST": "yes"}, {"MAL_AQ50_T_NOW": "1"}):
            self.refused(fx, "test override", env=env, host="mal-fast-0")
        self.refused(fx, "MAL_ARENA_TEST", env={"MAL_ARENA_TEST": "1"}, host="mal-fast-0")
        self.refused(fx, "--now-ms is a test override", env={}, host="mal-fast-0", now_ms=fx["ri"] + 1)
        self.refused(fx, "production runs only on mal-fast-0", env={}, host="mal-research-0")
        self.refused(fx, "--ledger-root must be", env={}, host="mal-fast-0")

    def test_production_paths_are_the_books(self):
        fx = self.copy()
        arena = fx["root"] / "arena"
        with mock.patch.dict(A.PROD_ROOTS, {Q50: str(arena)}):
            a = args_for(fx)
            self.assertFalse(A.check_mode(a, {}, "mal-fast-0"))  # production, with the fixture standing in for the real root
            bad = arena / "h5-q50s1-2026-11-05"
            bad.mkdir()
            with self.assertRaises(A.Refused) as cm:
                A.check_mode(args_for(fx, shadow=[bad]), {}, "mal-fast-0")
            self.assertIn("is not a shadow dir of", str(cm.exception))

    def test_test_mode_never_touches_a_real_path(self):
        fx = self.copy()
        for kw in ({"ledger": Path("/home/claude/data/arena/ledger")}, {"shadow": [Path("/home/claude/s1home/data/arena/h5-q50s1-2026-11-05")]},
                   {"read": Path("/home/claude/data/q50-read/q50-read.json")}, {"out": Path("/var/lib/mal/x.json")}):
            with mock.patch.object(A, "recorded_pass", side_effect=AssertionError("opened")):
                self.refused(fx, "must not touch a real path", **kw)

    def test_closed_names_are_refused(self):
        fx = self.copy()
        probe = fx["root"] / "arena" / "probe-dir"
        self.refused(fx, "probe", shadow=[probe])


# ------------------------------------------------------------------------------------------------------------------------------ s1
class S1Book(unittest.TestCase):
    def test_s1_screen_read(self):
        tmp = Path(tempfile.mkdtemp(prefix="aq50-s1-")).resolve()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        fx = build(tmp / "s1", bot=S1, inject=lambda d, i: "late_buy" if i == 3 else None, per_day=41)
        self.assertEqual((fx["status"], fx["ri"]), ("PASS", s1i.EARLIEST_BOUNDARY_MS))
        self.assertEqual(fx["shadow"].name, "h5-q50s1-2026-11-05")
        rc, out = run(fx, now_ms=fx["ri"] + 1000)
        self.assertEqual(rc, A.EXIT_REPEATS)
        self.assertEqual(out["counted_trades"], 205)
        self.assertEqual((out["counts_by_leg"]["1p9"]["ssb_repeat_buys"], out["counts_by_leg"]["1p9"]["trades_flagged"]), (5, 5))
        self.assertEqual(out["bot_id"], S1)
        with mock.patch.object(A.time, "time", return_value=(fx["ri"] - 1000) / 1000), self.assertRaises(A.Refused) as cm:  # a wall clock before the instant: the seal refuses
            run(fx)
        self.assertIn("before the book's recorded read instant", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
