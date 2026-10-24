"""Tests for the book-list path of tools/arena_h5_read.py and tools/arena_h5_legs.py (--books-file, Q50 included; Audit B1) and the Q50 seal on the DEC-031 Amendment 11
write-once instant file. SYNTHETIC fixtures in tmp dirs only (tools/test_arena_h5_read.py's builder); no real H5 path is opened. The books file of the tests is the registered v2 text
(ARTIFACTS/lab/arena-records-2026-10-24-q50.md section 2, verbatim, plus the newline): its sha256 is the pin.

Run: /data/mal/venv/bin/python3 -m pytest -q tools/test_arena_h5_books.py
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

from tools import arena_h5_books as B
from tools import arena_h5_legs as L
from tools import arena_h5_q50_instant as qi
from tools import arena_h5_read as R
from tools import arena_h5_score as h5s
from tools import test_arena_h5_read as TR

Q50 = qi.Q50_ID
A2V1, A2V2 = "h5v1-boostfloor-a2", "h5v2-boostclock-a2"
V2_TEXT = '{"legacy_bot_ids": ["h5v1-boostfloor-a1", "h5v2-boostclock-a1"], "new_rule_bot_ids": ["h5v1-boostfloor-a2", "h5v2-boostclock-a2", "h5v1q50-boostfloor-a1"]}\n'
CLOCK = TR.BT0 * 1000  # 2026-03-02T00:00Z: the fixture books' clock
END = CLOCK + 30 * qi.DAY_MS


def q50_record(ledger: Path, bot: str = Q50, *, now_ms: int = 2**62, end_ms: int = END, override: dict | None = None) -> dict | None:
    """Write the Am.11 record of a fixture book the way the Q50 wrapper does: the rule over the ledger's classes (counted pools' decisions, exclusions, refusals; nothing open)."""
    d = ledger / bot
    lines = lambda pre: [json.loads(x) for f in sorted(d.glob(f"{pre}-*.jsonl")) for x in f.read_text().splitlines() if x.strip()]  # noqa: E731
    counted = {}
    for r in lines("trades"):
        counted[r["pool"]] = r["decision_ms"]
    cls = {"counted": sorted(counted.values()), "excluded": sorted(e["decision_ms"] for e in lines("exclusions")), "open": [],
           "refused": sorted((r["decision_ms"], r["reason"]) for r in lines("refusals"))}
    doc = qi.record_doc(bot, cls, clock_start_ms=CLOCK, end_ms=end_ms, now_ms=now_ms, attempt_definition="fixture")
    if doc is not None:
        doc.update(override or {})
        qi.record(d, doc)
    return doc


class Base(TR.Base):
    def setUp(self):
        super().setUp()
        self.books_path = self.root / "a3-books.json"
        self.books_path.write_text(V2_TEXT)

    def bargs(self, ri: int, *bots: str, extra: tuple = ()) -> list[str]:
        return self.args(ri, *bots, extra=("--books-file", str(self.books_path)) + tuple(extra))

    def books(self):
        return B.load_books(self.books_path)


class BooksFile(Base):
    def test_the_registered_v2_text_has_the_pinned_sha_and_loads(self):
        b = self.books()
        self.assertEqual(b.sha256, B.BOOKS_V2_SHA256)
        self.assertEqual(list(b), ["h5v1-boostfloor-a1", "h5v2-boostclock-a1", A2V1, A2V2, Q50])
        self.assertEqual(b.new_rule, [A2V1, A2V2, Q50])
        self.assertEqual(dict(b), {"h5v1-boostfloor-a1": "v1", "h5v2-boostclock-a1": "v2", A2V1: "v1", A2V2: "v2", Q50: "v1"})

    def test_exit_rules_agree_with_the_wrappers_and_the_scorer_prefix_rule(self):
        from tools import arena_h5_score_a2 as a2
        from tools import arena_h5_score_q50 as w
        self.assertEqual({k: B.EXIT_RULES[k] for k in w.Q50_BOTS}, w.Q50_BOTS)
        self.assertEqual({k: B.EXIT_RULES[k] for k in a2.A2_BOTS}, a2.A2_BOTS)
        for k, v in B.EXIT_RULES.items():
            self.assertEqual(v, "v2" if k.startswith("h5v2") else "v1", k)  # arena_h5_score.price_trade's rule

    def test_refusals(self):
        bad = {"sha": (V2_TEXT, "0" * 64), "keys": ('{"legacy_bot_ids": []}\n', None), "dup": ('{"legacy_bot_ids": ["h5v1-boostfloor-a1"], "new_rule_bot_ids": ["h5v1-boostfloor-a1"]}\n', None),
               "unknown": ('{"legacy_bot_ids": [], "new_rule_bot_ids": ["h5v9-x"]}\n', None), "repeat": ('{"legacy_bot_ids": [], "new_rule_bot_ids": ["h5v1-boostfloor-a2", "h5v1-boostfloor-a2"]}\n', None),
               "notjson": ("{", None)}
        for name, (text, sha) in bad.items():
            p = self.root / f"{name}.json"
            p.write_text(text)
            import hashlib
            with self.assertRaises(R.Refused, msg=name):
                B.load_books(p, sha or hashlib.sha256(text.encode()).hexdigest())
        p = self.root / "other.json"
        p.write_text(V2_TEXT.replace("a2", "a2 "))
        with self.assertRaises(R.Refused):
            B.load_books(p)  # the default pin is the registered v2
        rc, _, err = self.cli(*self.args(CLOCK, extra=("--books-file", str(p))))
        self.assertEqual(rc, 3)
        self.assertIn("not the registered", err)

    def test_the_instant_file_of_each_book(self):
        self.assertEqual(B.instant_file(Q50), "q50-read-instant.json")
        for b in (A2V1, A2V2, TR.V1, TR.V2):
            self.assertEqual(B.instant_file(b), h5s.READ_INSTANT_FILE)


class Q50Seal(Base):
    def test_q50_reads_only_on_the_am11_file_never_on_read_instant_json(self):
        bk = self.make_book(Q50)  # writes the scorer's read-instant.json only
        self.assertTrue((self.ledger / Q50 / h5s.READ_INSTANT_FILE).is_file())
        with mock.patch.object(L, "load_book_rows", side_effect=AssertionError("a ledger row was opened")):
            with self.assertRaisesRegex(R.Refused, "q50-read-instant.json absent"):
                R.decide_book(Q50, self.ledger, read_instant_ms=bk["ri"], i_am_the_read=True, now_ms=bk["ri"] + 1000, books=self.books())
            with self.assertRaisesRegex(R.Refused, "q50-read-instant.json absent"):
                L.gate(Q50, self.ledger, bk["ri"], True, bk["ri"] + 1000, self.books())
        doc = q50_record(self.ledger)
        self.assertEqual(doc["read_instant_ms"], bk["ri"])  # no exclusion here: the two rules agree
        res = R.decide_book(Q50, self.ledger, read_instant_ms=bk["ri"], i_am_the_read=True, now_ms=bk["ri"] + 1000, books=self.books())
        self.assertEqual((res["status"], res["exit_rule"], res["counts"]["n_counted"]), ("PASS", "v1", 120))
        self.assertIn("q50-read-instant.json", res["provenance"])
        self.assertNotIn("read-instant.json", res["provenance"])

    def test_the_motivating_case_reads_later_with_n_at_least_100_not_fail_n_below_100(self):
        """20 pools a day, 2 excluded on day 0: the scorer's attempt boundary is day 5 (100 attempts, 98 counted: FAIL (n < 100) under the old rule). Am.11 reads at day 6 (118)."""
        bk = self.make_book(Q50, per_day=20, excluded={(0, 0), (0, 1)})
        self.assertEqual(bk["ri"], CLOCK + 5 * qi.DAY_MS)
        doc = q50_record(self.ledger)
        self.assertEqual((doc["read_instant_ms"], doc["counted_before"], doc["excluded_before"]), (CLOCK + 6 * qi.DAY_MS, 118, 2))
        ri = doc["read_instant_ms"]
        with self.assertRaisesRegex(R.Refused, "before the book's recorded read instant"):
            R.decide_book(Q50, self.ledger, read_instant_ms=bk["ri"], i_am_the_read=True, now_ms=ri + 1000, books=self.books())
        res = R.decide_book(Q50, self.ledger, read_instant_ms=ri, i_am_the_read=True, now_ms=ri + 1000, books=self.books())
        self.assertEqual((res["status"], res["counts"]["n_counted"], res["counts"]["n_excluded"], res["read_instant_ms"]), ("PASS", 118, 2, ri))
        for v in res["subcells"].values():
            self.assertEqual(v["n"], 118)

    def test_a_record_whose_instant_is_not_the_am11_instant_is_refused(self):
        bk = self.make_book(Q50, per_day=20, excluded={(0, 0), (0, 1)})
        att = json.loads((self.ledger / Q50 / h5s.READ_INSTANT_FILE).read_text())
        q50_record(self.ledger, override={"read_instant_ms": bk["ri"], "boundary_ms": bk["ri"], "attempts_before": att["attempts_before"], "refused_before": att["refused_before"],
                                          "refused_by_reason_before": att["refused_by_reason_before"]})  # the attempt boundary written as if it were the Am.11 instant
        with self.assertRaisesRegex(R.Refused, "Am.11 rule recomputed"):
            R.decide_book(Q50, self.ledger, read_instant_ms=bk["ri"], i_am_the_read=True, now_ms=bk["ri"] + 1000, books=self.books())

    def test_a_record_that_is_not_an_am11_record_or_not_read_is_refused(self):
        bk = self.make_book(Q50)
        q50_record(self.ledger, override={"rule": "something else"})
        with self.assertRaisesRegex(R.Refused, "not an Amendment 11 record"):
            L.gate(Q50, self.ledger, bk["ri"], True, bk["ri"] + 1000, self.books())

    def test_ended_at_the_end_seal_is_not_decidable_counts_only_and_opens_nothing(self):
        self.make_book(Q50, per_day=10, days=10, excluded={(0, 0), (0, 1)})  # 100 attempts, 98 counted trades by the end: no boundary qualifies
        end = CLOCK + 10 * qi.DAY_MS
        doc = q50_record(self.ledger, end_ms=end, now_ms=end)
        self.assertEqual((doc["status"], doc["read_instant_ms"]), (qi.ENDED, None))
        with mock.patch.object(L, "load_book_rows", side_effect=AssertionError("a ledger row was opened")), \
                mock.patch.object(h5s, "ledger_attempts_before", side_effect=AssertionError("a ledger file was opened")):
            res = R.decide_book(Q50, self.ledger, read_instant_ms=end, i_am_the_read=True, now_ms=end + 1000, books=self.books())
        self.assertEqual((res["status"], res["final"], res["counts"]["n_counted"], res["counts"]["n_excluded"], res["read_instant_ms"]), ("NOT_DECIDABLE", True, 98, 2, None))
        self.assertNotIn("subcells", res)
        rc, out, _ = self.cli(*self.bargs(end, Q50))
        self.assertEqual(rc, 0)
        self.assertIn(f"BOT {Q50}  STATUS NOT_DECIDABLE", out)
        self.assertIn("Amendment 11 item 2", out)

    def test_without_the_books_file_q50_is_refused_as_before_and_an_a1_book_reads_the_same(self):
        b1 = self.make_book(TR.V1)
        with self.assertRaises(SystemExit) as cm, mock.patch("sys.stderr"):
            self.cli(*self.args(b1["ri"], Q50))
        self.assertEqual(cm.exception.code, 2)  # argparse: not a choice without the books file (golden test pins the message)
        old = R.decide_book(TR.V1, self.ledger, read_instant_ms=b1["ri"], i_am_the_read=True, now_ms=b1["ri"] + 1000)
        new = R.decide_book(TR.V1, self.ledger, read_instant_ms=b1["ri"], i_am_the_read=True, now_ms=b1["ri"] + 1000, books=self.books())
        self.assertEqual(sorted(set(new) - set(old)), ["_pools", "d3"])  # the book list adds only the D3 extras and the pool set of the side-by-side block
        self.assertEqual(old, {k: v for k, v in new.items() if k not in ("_pools", "d3")})  # an a1 book keeps its rule, file and figures under the book list

    def test_cli_default_is_the_new_rule_list_and_the_taint_line_names_it(self):
        bk = self.make_book(Q50)
        q50_record(self.ledger)
        js = self.root / "q.json"
        rc, out, _ = self.cli(*self.bargs(bk["ri"], extra=("--json", str(js))))
        self.assertEqual(rc, 3)  # the a2 fixtures have no record: REFUSED beside the Q50 read
        self.assertIn(f"BOT {Q50}  STATUS PASS", out)
        self.assertIn(f"REFUSED {A2V1}", out)
        self.assertIn(f"REFUSED {A2V2}", out)
        self.assertEqual(sorted(json.loads(js.read_text())["refused"]), [A2V1, A2V2])
        rc, out, _ = self.cli(*self.bargs(bk["ri"], Q50, extra=("--json", str(js))))
        self.assertEqual(rc, 0)
        rep = json.loads(js.read_text())
        self.assertEqual(rep["books_file"]["sha256"], B.BOOKS_V2_SHA256)
        self.assertIn(Q50, rep["multiplicity"]["taint_line"])

    def test_legs_tool_reads_q50_with_the_books_file(self):
        bk = self.make_book(Q50)
        q50_record(self.ledger)
        rep = L.build_report(Q50, self.ledger, read_instant_ms=bk["ri"], i_am_the_read=True, now_ms=bk["ri"] + 1000, books=self.books())
        self.assertEqual((rep["bot_id"], rep["read_instant_ms"]), (Q50, bk["ri"]))
        with self.assertRaisesRegex(R.Refused, "is not an H5 arena book"):
            L.build_report(Q50, self.ledger, read_instant_ms=bk["ri"], i_am_the_read=True, now_ms=bk["ri"] + 1000)



def mark_derived(pools: set[str]):
    """A make_book `mutate` that sets the scorer's derived_last flag (meta.state_src of the 1.9 s leg: exit state after the pool's last print) on the rows of `pools`. Meta only."""
    def mutate(rows):
        for r in rows:
            if r["pool"] in pools:
                r["meta"]["state_src"]["1p9"] = [r["meta"]["state_src"]["1p9"][0], "post"]
    return mutate


class D3(Base):
    def read(self, bk, bot=Q50):
        return R.decide_book(bot, self.ledger, read_instant_ms=bk["ri"], i_am_the_read=True, now_ms=bk["ri"] + 1000, books=self.books())

    def test_above_5pct_the_ex_derived_total_is_the_restatement_without_those_trades(self):
        marked = {f"P{d}x{i:02d}" for d in range(5) for i in (0, 1)}  # 10 of 120 attempts = 8.33%
        bk = self.make_book(Q50, mutate=mark_derived(marked))
        q50_record(self.ledger)
        res = self.read(bk)
        d = res["d3"]
        self.assertEqual((d["derived_last_deciding_leg"], d["attempts"], d["over_5pct"]), (10, 120, True))
        self.assertAlmostEqual(d["share_of_attempts"], 10 / 120)
        sel = [(k, TR.h5s.utc_day((TR.BT0 + dd * 86400 + i * 60) * 1000)) for dd, i, k, counted in bk["pools"] if counted and (TR.BT0 + dd * 86400 + i * 60) * 1000 < bk["ri"]
               and f"P{dd}x{i:02d}" not in marked]
        want = TR.expected_cells([k for k, _ in sel], [x for _, x in sel])
        self.assertEqual(sorted(d["ex_derived"]), sorted(R.SUBCELLS))
        for k, v in d["ex_derived"].items():
            self.assertEqual(v["n"], 110)
            self.assertAlmostEqual(v["total_sol"], want[k]["total_sol"], places=12)
            self.assertAlmostEqual(v["mean_sol"], want[k]["mean_sol"], places=12)
        self.assertEqual(res["status"], "PASS")  # the deciding cells are unchanged: D3 never decides
        for v in res["subcells"].values():
            self.assertEqual(v["n"], 120)

    def test_at_or_below_5pct_only_the_count_is_printed(self):
        marked = {f"P{d}x00" for d in range(5)} | {"P0x01"}  # 6 of 120 = exactly 5%: not above
        bk = self.make_book(Q50, mutate=mark_derived(marked))
        q50_record(self.ledger)
        d = self.read(bk)["d3"]
        self.assertEqual((d["derived_last_deciding_leg"], d["over_5pct"]), (6, False))
        self.assertIn("not required", d["ex_derived"])

    def test_not_decidable_prints_the_count_only(self):
        bk = self.make_book(Q50, excluded={(0, i) for i in range(7)}, mutate=mark_derived({"P1x00"}))
        q50_record(self.ledger)
        res = self.read(bk)
        self.assertEqual(res["status"], "NOT_DECIDABLE")
        self.assertEqual(res["d3"]["derived_last_deciding_leg"], 1)
        self.assertIn("counts only", res["d3"]["ex_derived"])
        self.assertNotIn("subcells", res)

    def test_books_side_by_side_with_the_shared_pool_count(self):
        bq = self.make_book(Q50, mutate=mark_derived({"P0x00"}))
        q50_record(self.ledger)
        ba = self.make_book(A2V1, per_day=20, kind=lambda d, i: "flat")
        ri = max(bq["ri"], ba["ri"])
        js = self.root / "sbs.json"
        rc, out, _ = self.cli(*self.bargs(ri, A2V1, A2V2, Q50, extra=("--json", str(js))))
        self.assertEqual(rc, 3)  # v2-a2 has no record
        sb = json.loads(js.read_text())["side_by_side"]
        self.assertEqual(sorted(sb["books"]), sorted([A2V1, A2V2, Q50]))
        self.assertEqual((sb["books"][A2V1]["status"], sb["books"][A2V1]["counted"], sb["books"][A2V1]["d3"]["derived_last_deciding_leg"]), ("FAIL", 100, 0))
        self.assertEqual((sb["books"][Q50]["status"], sb["books"][Q50]["counted"], sb["books"][Q50]["d3"]["derived_last_deciding_leg"]), ("PASS", 120, 1))
        self.assertEqual(sb["books"][A2V2]["status"], "REFUSED")
        self.assertEqual(sb["shared_pools"], {f"{A2V1}|{Q50}": 100})
        self.assertIn("BOOKS SIDE BY SIDE", out)
        self.assertIn(f"shared pools {A2V1}|{Q50}: 100", out)
        self.assertNotIn("_pools", json.loads(js.read_text())["books"][Q50])


if __name__ == "__main__":
    import unittest
    unittest.main()
