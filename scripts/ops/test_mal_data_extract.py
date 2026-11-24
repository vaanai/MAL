"""Tests for scripts/ops/mal_data_extract.py. Synthetic fixtures only; no real ledger, board, tape or book dir is opened."""
import calendar
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mal_data_extract as X  # noqa: E402

BOT = "floor-lane-1"
H = 3600 * 1000


def ms(y, mo, d, h=0, mi=0, s=0):
    return calendar.timegm((y, mo, d, h, mi, s)) * 1000


NOW = ms(2026, 11, 30, 12)


def trade_rows(entry, exit_, stake=250_000_000, buy=250_000_000, back=260_000_000, mint="MINTX", ok=True, extra=None):
    """send_intent, fill, exit rows carrying forbidden-to-release fields on purpose."""
    base = {"bot_id": BOT, "mint": mint, "signature": "SIGSECRET", "schema": "x", "pool": "POOLSECRET", "order_id": "ORDER1"}
    rows = [
        {**base, "ev": "send_intent", "ts_ms": entry, "stake_lamports": stake, "sol_in_lamports": 0, "sol_out_lamports": 0, "row_id": "i" + mint},
        {**base, "ev": "fill", "ts_ms": entry + 1000, "ok": ok, "sol_in_lamports": 0, "sol_out_lamports": buy, "row_id": "f" + mint},
    ]
    if ok:
        rows.append({**base, "ev": "exit", "ts_ms": exit_, "sol_in_lamports": back, "sol_out_lamports": 0, "exit_reason": "tp", "row_id": "e" + mint, **(extra or {})})
    return rows


class TradeTests(unittest.TestCase):
    def rows(self, ledger, release=False, now=NOW):
        return X.trade_rows(X.closed_trades(ledger, BOT), now, release)

    def test_whitelist_and_no_exit_reason(self):
        out = self.rows(trade_rows(ms(2026, 11, 27, 1), ms(2026, 11, 27, 2)))
        self.assertEqual(len(out), 1)
        self.assertEqual(tuple(out[0]), ("entry_time", "exit_time", "stake_sol", "realized_sol", "failed"))
        blob = json.dumps(out)
        for bad in ("exit_reason", "tp", "SIGSECRET", "POOLSECRET", "ORDER1", "MINTX", "signature", "mint", "order_id", "pool"):
            self.assertNotIn(bad, blob if bad != "tp" else blob.replace("stake_sol", ""))
        self.assertEqual(out[0]["stake_sol"], 0.25)
        self.assertAlmostEqual(out[0]["realized_sol"], 0.01)

    def test_failed_landing_included_at_cost_open_and_other_bot_excluded(self):
        led = trade_rows(ms(2026, 11, 27, 1), 0, mint="FAIL", ok=False)
        led += trade_rows(ms(2026, 11, 27, 3), 0, mint="OPEN")[:2]  # filled, never exited
        other = trade_rows(ms(2026, 11, 27, 1), ms(2026, 11, 27, 2), mint="OTH")
        for r in other:
            r["bot_id"] = "other-bot"
        out = self.rows(led + other)
        self.assertEqual(len(out), 1)
        self.assertAlmostEqual(out[0]["realized_sol"], -0.25)
        self.assertIs(out[0]["failed"], True)

    def test_failed_flag(self):
        ok = trade_rows(ms(2026, 11, 27, 1), ms(2026, 11, 27, 2), mint="OKT")
        bad = trade_rows(ms(2026, 11, 27, 3), 0, mint="BADT", ok=False)
        unsent = trade_rows(ms(2026, 11, 27, 5), 0, mint="UNS")[:1] + [{"bot_id": BOT, "mint": "UNS", "ev": "writeoff", "ts_ms": ms(2026, 11, 27, 6), "sol_in_lamports": 0, "sol_out_lamports": 5000}]
        by = {r["entry_time"]: r["failed"] for r in self.rows(ok + bad + unsent)}
        self.assertEqual(list(by.values()), [False, True, True])

    def test_24h_lag(self):
        exit_ = ms(2026, 11, 29, 10)
        led = trade_rows(ms(2026, 11, 29, 9), exit_)
        self.assertEqual(len(self.rows(led, now=exit_ + 24 * H)), 1)  # exactly 24 h old: released
        self.assertEqual(len(self.rows(led, now=exit_ + 24 * H - 1)), 0)

    def test_e2_boundaries(self):
        lo, hi = X.E2_START_MS, X.E2_END_MS
        led = []
        for i, (e, name) in enumerate([(lo - 1, "BEFORE"), (lo, "AT_START"), (hi - 1, "LAST"), (hi, "AT_END")]):
            led += trade_rows(e, e + H, mint=name)
        held = self.rows(led)
        self.assertEqual([r["entry_time"] for r in held], [X.iso_ms(lo - 1), X.iso_ms(hi)])
        self.assertEqual(len(self.rows(led, release=True)), 4)

    def test_duplicate_row_id_counted_once(self):
        led = trade_rows(ms(2026, 11, 27, 1), ms(2026, 11, 27, 2))
        self.assertAlmostEqual(self.rows(led + [dict(led[2])])[0]["realized_sol"], 0.01)


def board_fixture(gen, ids=("osb-a", "osb-b", "r4bt-a2-x")):
    books = []
    for i in ids:
        trades = [{"tid": i + "-00001", "entry_utc": "2026-11-17T01:00:00Z", "exit_utc": "2026-11-17T02:00:00Z"},
                  {"tid": i + "-00002", "entry_utc": "2026-11-19T01:00:00Z", "exit_utc": "2026-11-19T02:00:00Z"}]
        books.append({"id": i, "role": "control" if i == "osb-a" else "variant", "rows": [{"stake_sol": 0.25, "trades": trades, "open": [{"tid": i + "-00003", "entry_utc": "2026-11-20T00:00:00Z"}],
                                                                                         "per_day": [{"day": "2026-11-17"}, {"day": "2026-11-19"}, {"day": "2026-11-26"}],
                                                                                         "disclosure": {"sentence": "DISCLOSE-" + i}}]})
    md = ["# Open sim board", "", "- Generated %s;" % gen, "", "| book | role |"]
    md += ["| %s | x |" % i for i in ids] + ["", "## Books"] + ["- %s: title." % i for i in ids]
    for i in ids:
        md += ["", "## %s, stake 0.25: forward disclosure (QP floor)" % i, "", "- DISCLOSE-" + i, "", "| day | n |", "|---|---|", "| 2026-11-17 | 1 |", "| 2026-11-19 | 1 |", "| 2026-11-26 | 1 |",
               "", "## %s, stake 0.25: last 2 of 2 closed trades" % i, "", "| id | entry UTC |", "| %s-00001 | a |" % i, "| %s-00002 | b |" % i,
               "", "## %s, stake 0.25: open (1)" % i, "", "- %s-00003 entered 2026-11-20T00:00:00Z, age 1.0 h" % i]
    return {"schema": "open_sim_board.v1", "generated_utc": gen, "books": books, "pairs": []}, "\n".join(md) + "\n"


def write_snap(root, name, gen, **kw):
    d = Path(root) / name
    d.mkdir()
    b, md = board_fixture(gen, **kw)
    (d / "board.json").write_text(json.dumps(b))
    (d / "board.md").write_text(md)


class BoardTests(unittest.TestCase):
    def test_lag_selection(self):
        with tempfile.TemporaryDirectory() as t:
            write_snap(t, "a", "2026-11-28T11:00:00Z")
            write_snap(t, "b", "2026-11-29T12:00:00Z")  # exactly 24 h before NOW: eligible and newest
            write_snap(t, "c", "2026-11-29T12:00:01Z")  # too new
            d, b = X.pick_snapshot(Path(t), NOW)
            self.assertEqual((d.name, b["generated_utc"]), ("b", "2026-11-29T12:00:00Z"))
            with tempfile.TemporaryDirectory() as t2:
                write_snap(t2, "new", "2026-11-30T11:00:00Z")
                with self.assertRaises(X.ExtractError):
                    X.pick_snapshot(Path(t2), NOW)

    def test_sealed_book_removed_disclosures_kept(self):
        b, md = board_fixture("2026-11-28T00:00:00Z")
        bj, mo = X.filter_board(b, md, True, [], [])
        self.assertEqual([x["id"] for x in bj["books"]], ["osb-a", "osb-b"])
        self.assertNotIn("r4bt", mo)
        self.assertNotIn("r4bt", json.dumps(bj))
        self.assertIn("- DISCLOSE-osb-a", mo)
        self.assertIn("- DISCLOSE-osb-b", mo)
        self.assertEqual(bj["books"][0]["rows"][0]["disclosure"]["sentence"], "DISCLOSE-osb-a")
        self.assertTrue(X.is_sealed_book("S1T-foo") and X.is_sealed_book("HV4-c0") and X.is_sealed_book("q50x") and X.is_sealed_book("dbc-1"))
        self.assertTrue(X.is_sealed_book("custom", ["custom"]))
        self.assertFalse(X.is_sealed_book("osb-b"))

    def test_e2_rows_of_book_a_withheld_and_released(self):
        b, md = board_fixture("2026-11-28T00:00:00Z")
        bj, mo = X.filter_board(b, md, False, [], [])
        a = bj["books"][0]["rows"][0]
        self.assertEqual([t["tid"] for t in a["trades"]], ["osb-a-00001"])
        self.assertEqual([d["day"] for d in a["per_day"]], ["2026-11-17", "2026-11-26"])
        self.assertEqual(a["open"], [])
        self.assertNotIn("osb-a-00002", mo)
        self.assertNotIn("osb-a-00003 entered", mo)
        self.assertIn("osb-a-00001", mo)
        self.assertNotIn("| 2026-11-19 | 1 |\n", mo.split("## osb-b")[0])
        # another book (not book A or a twin) is untouched
        self.assertEqual(len(bj["books"][1]["rows"][0]["trades"]), 2)
        self.assertIn("osb-b-00002", mo)
        bj2, mo2 = X.filter_board(b, md, True, [], [])
        self.assertEqual(len(bj2["books"][0]["rows"][0]["trades"]), 2)
        self.assertIn("osb-a-00002", mo2)

    def test_twin_of_book_a_held_via_pairs(self):
        b, md = board_fixture("2026-11-28T00:00:00Z")
        b["pairs"] = [{"twin": "osb-b", "strict": "osb-a"}]
        bj, _ = X.filter_board(b, md, False, [], [])
        self.assertEqual(len(bj["books"][1]["rows"][0]["trades"]), 1)


class TotalsAndRunTests(unittest.TestCase):
    LINE = "2026-11-24 floor-lane-1: sends 8 landed 8 failed 0 (cost 0.000000000 SOL), exits 7, realized 0.012345678 SOL after all fees, open 9 (basis 1.250000000 SOL) missed lane_budget 3, lane_max_open 2"

    def test_totals_parse(self):
        r = X.parse_totals(self.LINE + "\n  gap: landing delay\n2026-11-24 other: sends 1 landed 1 failed 0, exits 0, realized 0 SOL after all fees, open 0 (basis 0 SOL)\n", BOT)
        self.assertEqual(len(r), 1)
        self.assertEqual((r[0]["sends"], r[0]["landed"], r[0]["failed"], r[0]["exits"], r[0]["open"]), (8, 8, 0, 7, 9))
        self.assertEqual((r[0]["realized_sol"], r[0]["open_basis_sol"]), ("0.012345678", "1.250000000"))
        self.assertEqual(r[0]["missed_by_cause"], {"lane_budget": 3, "lane_max_open": 2})

    def test_manifest_hashes(self):
        with tempfile.TemporaryDirectory() as t:
            t = Path(t)
            led = t / "ledger.jsonl"
            led.write_text("".join(json.dumps(r) + "\n" for r in trade_rows(ms(2026, 11, 27, 1), ms(2026, 11, 27, 2))))
            (t / "snaps").mkdir()
            write_snap(t / "snaps", "s1", "2026-11-28T00:00:00Z")
            (t / "totals.txt").write_text(self.LINE + "\n")
            m = X.run(led, t / "snaps", t / "totals.txt", t / "out", NOW, commit="abc123")
            man = json.loads((t / "out" / "MANIFEST.json").read_text())
            self.assertEqual(man, m)
            self.assertEqual(man["extractor_commit"], "abc123")
            self.assertEqual(man["run_utc"], "2026-11-30T12:00:00.000Z")
            self.assertEqual(man["ruling_sha256"][:8], "cfe5d568")
            self.assertEqual(set(man["files"]), {"floor-closed-trades.jsonl", "board.json", "board.md", "daily-totals.json", "NOTES.txt"})
            for n, h in man["files"].items():
                self.assertEqual(hashlib.sha256((t / "out" / n).read_bytes()).hexdigest(), h)
            self.assertNotIn("r4bt", (t / "out" / "board.md").read_text())
            self.assertNotIn("exit_reason", (t / "out" / "floor-closed-trades.jsonl").read_text())
            self.assertEqual(json.loads((t / "out" / "daily-totals.json").read_text())["days"][0]["sends"], 8)


if __name__ == "__main__":
    unittest.main()
