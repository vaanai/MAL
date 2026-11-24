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
HOLD = X.hold_range(False, False)
FIRST = X.hold_range(True, False)


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
    def rows(self, ledger, hold=HOLD, now=NOW):
        return X.trade_rows(X.closed_trades(ledger, BOT), now, hold)

    def test_whitelist_and_no_exit_reason(self):
        out = self.rows(trade_rows(ms(2026, 11, 27, 1), ms(2026, 11, 27, 2)), hold=None)
        self.assertEqual(len(out), 1)
        self.assertEqual(tuple(out[0]), ("entry_time", "exit_time", "stake_sol", "realized_sol", "failed"))
        blob = json.dumps(out)
        for bad in ("exit_reason", "SIGSECRET", "POOLSECRET", "ORDER1", "MINTX", "signature", "mint", "order_id", "pool"):
            self.assertNotIn(bad, blob)
        self.assertEqual((out[0]["stake_sol"], out[0]["realized_sol"]), ("0.250000000", "0.010000000"))  # exact 9-decimal strings, not floats

    def test_failed_flag(self):
        ok = trade_rows(ms(2026, 11, 27, 1), ms(2026, 11, 27, 2), mint="OKT")
        bad = trade_rows(ms(2026, 11, 27, 3), 0, mint="BADT", ok=False)
        unsent = trade_rows(ms(2026, 11, 27, 5), 0, mint="UNS")[:1] + [{"bot_id": BOT, "mint": "UNS", "ev": "writeoff", "ts_ms": ms(2026, 11, 27, 6), "sol_in_lamports": 0, "sol_out_lamports": 5000}]
        out = self.rows(ok + bad + unsent, hold=None)
        self.assertEqual([r["failed"] for r in out], [False, True, True])
        self.assertEqual(out[1]["realized_sol"], "-0.250000000")

    def test_open_and_other_bot_excluded(self):
        led = trade_rows(ms(2026, 11, 27, 3), 0, mint="OPEN")[:2]
        other = trade_rows(ms(2026, 11, 27, 1), ms(2026, 11, 27, 2), mint="OTH")
        for r in other:
            r["bot_id"] = "other-bot"
        self.assertEqual(self.rows(led + other, hold=None), [])

    def test_24h_lag(self):
        exit_ = ms(2026, 12, 4, 10)
        led = trade_rows(ms(2026, 12, 4, 9), exit_)
        self.assertEqual(len(self.rows(led, now=exit_ + 24 * H)), 1)
        self.assertEqual(len(self.rows(led, now=exit_ + 24 * H - 1)), 0)

    def test_hold_boundaries_default_extends_to_dec3(self):
        lo, mid, hi = X.E2_START_MS, X.E2_END_MS, X.E2_EXT_END_MS
        led = []
        for e, name in [(lo - 1, "BEFORE"), (lo, "AT_START"), (mid - 1, "LAST1"), (mid, "AT_MID"), (hi - 1, "LAST2"), (hi, "AT_END")]:
            led += trade_rows(e, e + H, mint=name)
        now = hi + 5 * 24 * H
        self.assertEqual([r["entry_time"] for r in self.rows(led, now=now)], [X.iso_ms(lo - 1), X.iso_ms(hi)])
        # after the first read only [11-26, 12-03) is held
        self.assertEqual([r["entry_time"] for r in self.rows(led, FIRST, now)], [X.iso_ms(lo - 1), X.iso_ms(lo), X.iso_ms(mid - 1), X.iso_ms(hi)])
        self.assertEqual(len(self.rows(led, None, now)), 6)

    def test_nov26_entry_not_released_by_default(self):
        led = trade_rows(ms(2026, 11, 26, 1), ms(2026, 11, 26, 2))
        self.assertEqual(self.rows(led, now=ms(2026, 11, 27, 3)), [])

    def test_duplicate_row_id_counted_once(self):
        led = trade_rows(ms(2026, 11, 27, 1), ms(2026, 11, 27, 2))
        self.assertEqual(self.rows(led + [dict(led[2])], hold=None)[0]["realized_sol"], "0.010000000")

    def test_fee_after_close_added_or_listed(self):
        led = trade_rows(ms(2026, 11, 27, 1), ms(2026, 11, 27, 2))  # realized +10_000_000
        led.append({"bot_id": BOT, "mint": "MINTX", "ev": "fee", "ts_ms": ms(2026, 11, 27, 3), "sol_in_lamports": 0, "sol_out_lamports": 4000})
        lone = {"bot_id": BOT, "mint": "NEVER", "ev": "fee", "ts_ms": ms(2026, 11, 27, 4), "sol_in_lamports": 0, "sol_out_lamports": 7000}
        unm = []
        t = X.closed_trades(led + [lone], BOT, unm)
        self.assertEqual(t[0]["realized"], 10_000_000 - 4000)
        self.assertEqual(unm, [(ms(2026, 11, 27, 4), -7000)])

    def test_torn_final_line_dropped_midfile_refused(self):
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / "l.jsonl"
            good = json.dumps({"ev": "start"})
            p.write_text(good + "\n" + '{"ev": "fill", "ts')
            self.assertEqual(len(X.read_ledger(p)), 1)
            p.write_text('{"ev": bad\n' + good + "\n")
            with self.assertRaises(X.ExtractError):
                X.read_ledger(p)


GEN = "2026-11-28T00:00:00Z"


def md_fixture(ids):
    md = ["# Open sim board", "", "- Live: see board.json.", "", "| book | role | stake | label | today | 7-day | forward | all | open | unpriced | QP floor | day in progress (partial) |", "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for i in ids:
        md.append("| %s | %s | 0.25 | lbl | +0.1 / +0.1 (1) | a | b | c | 1 | 0 | FLOORCELL | +0.2 / +0.2 (2) |" % (i, "control" if i == "osb-a" else "variant"))
    md += ["", "## Books"] + ["- %s: title. state running; start x." % i for i in ids]
    for i in ids:
        md += ["", "## %s, stake 0.25: forward disclosure (QP floor: FLOORTXT)" % i, "", "- window: own forward. forward n closed 5",
               "- control, same stake: forward net flat +0.9; paired difference (variant minus control): flat +0.3.", "- DISCLOSE-" + i + ".", "",
               "| day | n | flat |", "|---|---|---|", "| 2026-11-17 | 1 | +0.1 |", "| 2026-11-19 | 1 | +0.2 |", "| 2026-11-20 (forward from 07:17Z) | 1 | +0.3 |",
               "| 2026-11-24 (partial day) | 2 | +0.4 |", "| 2026-11-27 | 3 | +0.5 |",
               "", "## %s, stake 0.25: last 2 of 2 closed trades" % i, "", "| id | entry UTC |", "| %s-00001 | a |" % i, "| %s-00002 | b |" % i,
               "", "## %s, stake 0.25: open (1)" % i, "", "- %s-00003 entered 2026-11-20T00:00:00Z, age 1.0 h" % i]
    md += ["", "## Pair, stake 0.25: osb-a-t (incl. gap-in-hold) beside osb-a (strict)", "", "| window | a | b |", "| today | PAIRNUM | x |", "- osb-a-t-00009: gap-in-hold, blind 3 s"]
    return "\n".join(md) + "\n"


def board_fixture(gen=GEN, ids=("osb-a", "osb-b", "r4bt-a2-x")):
    books = []
    for i in ids:
        win = {"net_sol_flat": 0.1, "n_closed": 1}
        books.append({"id": i, "role": "control" if i == "osb-a" else "variant", "state": "running", "title": "t", "params": {"host": "h"}, "rows": [{
            "stake_sol": 0.25, "windows": {"today": win, "day_in_progress": win}, "vs_control": {"control_book": "osb-a", "x": 1},
            "trades": [{"tid": i + "-00001", "entry_utc": "2026-11-17T01:00:00Z"}, {"tid": i + "-00002", "entry_utc": "2026-11-19T01:00:00Z"}],
            "per_day": [{"day": "2026-11-17"}, {"day": "2026-11-27"}], "open": [{"tid": i + "-00003", "entry_utc": "2026-11-20T00:00:00Z"}],
            "disclosure": {"sentence": "DISCLOSE-" + i, "control_forward": {"n": 1}, "paired_difference": {"d": 1}, "floor_holds": True}}]})
    return {"schema": "open_sim_board.v1", "generated_utc": gen, "live": {"secretlive": "LIVEDATA", "recon": "x"}, "recon": {"status": "PASS"}, "books": books, "pairs": []}, md_fixture(ids)


def write_snap(root, name, gen, **kw):
    d = Path(root) / name
    d.mkdir()
    b, md = board_fixture(gen, **kw)
    (d / "board.json").write_text(json.dumps(b))
    (d / "board.md").write_text(md)


DISC = {"osb-a": ["Am.9 line A"], "osb-b": ["Am.9 line B"]}


class BoardTests(unittest.TestCase):
    def test_lag_selection(self):
        with tempfile.TemporaryDirectory() as t:
            write_snap(t, "a", "2026-11-28T11:00:00Z")
            write_snap(t, "b", "2026-11-29T12:00:00Z")
            write_snap(t, "c", "2026-11-29T12:00:01Z")
            d, b = X.pick_snapshot(Path(t), NOW)
            self.assertEqual((d.name, b["generated_utc"]), ("b", "2026-11-29T12:00:00Z"))
            with tempfile.TemporaryDirectory() as t2:
                write_snap(t2, "new", "2026-11-30T11:00:00Z")
                with self.assertRaises(X.ExtractError):
                    X.pick_snapshot(Path(t2), NOW)

    def test_sealed_book_removed_released_book_intact(self):
        b, md = board_fixture()
        bj, mo = X.filter_board(b, md, False, [], [], DISC)
        self.assertEqual([x["id"] for x in bj["books"]], ["osb-a", "osb-b"])
        self.assertNotIn("r4bt", mo + json.dumps(bj))
        self.assertIn("| 2026-11-24 (partial day) | 2 | +0.4 |", mo)  # no hold: the board's own rows go out as written
        self.assertIn("- DISCLOSE-osb-a.", mo)
        self.assertTrue(all(X.is_sealed_book(x) for x in ("S1T-foo", "HV4-c0", "q50x", "dbc-1", "floor-cpmm-1")) and not X.is_sealed_book("osb-b"))

    def test_hold_withholds_book_a_every_figure(self):
        b, md = board_fixture()
        b["pairs"] = [{"twin": "osb-a-t", "strict": "osb-a", "status": "pair"}]
        bj, mo = X.filter_board(b, md, True, [], [], DISC)
        a, v = bj["books"][0], bj["books"][1]
        self.assertEqual((a["rows"], a["state"]), ([], X.WITHHELD))
        self.assertEqual(bj["pairs"], [])
        r = v["rows"][0]
        self.assertIsNone(r["vs_control"])
        self.assertEqual((r["disclosure"]["control_forward"], r["disclosure"]["paired_difference"], r["disclosure"]["floor_holds"]), (None, None, None))
        self.assertEqual(len(r["trades"]), 2)  # the variant's own rows go out
        # nothing of book A: no tid, no suffix-carrying per-day row, no gap-in-hold line, no pair table, no control-derived cell
        self.assertNotIn("osb-a-0", mo)
        for leak in ("PAIRNUM", "gap-in-hold, blind", "+0.9", "FLOORCELL", "FLOORTXT"):
            self.assertNotIn(leak, mo, leak)
        self.assertNotIn("## Pair", mo)
        self.assertIn("| osb-a | control | %s |" % X.WITHHELD, mo)
        self.assertNotIn("+0.1 / +0.1", mo.split("## Books")[0].split("| osb-a |")[1].split("\n")[0])  # book A's today / day-in-progress cells are gone
        self.assertIn("- control, same stake: %s." % X.WITHHELD, mo)
        self.assertIn("(QP floor: %s)" % X.WITHHELD, mo)
        self.assertEqual(mo.count("(partial day)"), 1)  # only osb-b's own per-day row remains; osb-a's suffixed rows (partial day / forward from) are gone
        self.assertEqual(mo.count("(forward from"), 1)

    def test_twin_held_by_control_role_prefix_and_pairs(self):
        b, md = board_fixture()
        b["books"][1]["role"] = "control (incl. gap-in-hold twin)"
        self.assertEqual(X.e2_books(b, []), {"osb-a", "osb-b"})
        b["books"][1]["role"] = "variant"
        b["pairs"] = [{"twin": "osb-b", "strict": "osb-a"}]
        self.assertEqual(X.e2_books(b, []), {"osb-a", "osb-b"})

    def test_live_never_passed_through_and_disclosures(self):
        b, md = board_fixture()
        bj, mo = X.filter_board(b, md, False, [], [], DISC)
        self.assertEqual(bj["live"], X.LIVE_WITHHELD)
        self.assertNotIn("LIVEDATA", json.dumps(bj))
        self.assertEqual(bj["recon"], {"status": "PASS"})
        self.assertEqual(bj["am9_disclosures"], DISC)
        self.assertEqual(bj["label"], X.BOARD_NOTE)
        head = mo[:400]
        self.assertIn(X.BOARD_NOTE, head)
        self.assertIn("Am.9 disclosure, osb-a: Am.9 line A", head)
        with self.assertRaises(X.ExtractError):
            X.filter_board(b, md, False, [], [], {"osb-a": ["only a"]})


class TotalsAndRunTests(unittest.TestCase):
    LINE = "2026-11-24 floor-lane-1: sends 8 landed 8 failed 0 (cost 0.000000000 SOL), exits 7, realized 0.012345678 SOL after all fees, open 9 (basis 1.250000000 SOL) missed lane_budget 3, lane_max_open 2"

    def test_totals_parse(self):
        r = X.parse_totals(self.LINE + "\n  gap: landing delay\n2026-11-24 other: sends 1 landed 1 failed 0, exits 0, realized 0 SOL after all fees, open 0 (basis 0 SOL)\n", BOT)
        self.assertEqual(len(r), 1)
        self.assertEqual((r[0]["sends"], r[0]["landed"], r[0]["failed"], r[0]["exits"], r[0]["open"]), (8, 8, 0, 7, 9))
        self.assertEqual((r[0]["realized_sol"], r[0]["open_basis_sol"]), ("0.012345678", "1.250000000"))
        self.assertEqual(r[0]["missed_by_cause"], {"lane_budget": 3, "lane_max_open": 2})

    def test_run_notes_manifest_hashes_and_script_sha(self):
        with tempfile.TemporaryDirectory() as t:
            t = Path(t)
            led = t / "ledger.jsonl"
            rows = trade_rows(ms(2026, 11, 10, 1), ms(2026, 11, 10, 2)) + trade_rows(ms(2026, 11, 20, 1), ms(2026, 11, 20, 2), mint="HELD")
            rows.append({"bot_id": BOT, "mint": "NEVER", "ev": "fee", "ts_ms": ms(2026, 11, 10, 5), "sol_in_lamports": 0, "sol_out_lamports": 9000})
            led.write_text("".join(json.dumps(r) + "\n" for r in rows))
            (t / "snaps").mkdir()
            write_snap(t / "snaps", "s1", GEN)
            (t / "totals.txt").write_text(self.LINE + "\n")
            m = X.run(led, t / "snaps", t / "totals.txt", t / "out", NOW, commit="abc123", disclosures=DISC)
            o = t / "out"
            man = json.loads((o / "MANIFEST.json").read_text())
            self.assertEqual(man, m)
            self.assertEqual((man["extractor_commit"], man["run_utc"], man["ruling_sha256"][:8]), ("abc123", "2026-11-30T12:00:00.000Z", "cfe5d568"))
            self.assertEqual(man["extractor_sha256"], hashlib.sha256(Path(X.__file__).read_bytes()).hexdigest())
            self.assertEqual(set(man["files"]), {"floor-closed-trades.jsonl", "board.json", "board.md", "daily-totals.json", "NOTES.txt"})
            for n, h in man["files"].items():
                self.assertEqual(hashlib.sha256((o / n).read_bytes()).hexdigest(), h)
            lines = (o / "floor-closed-trades.jsonl").read_text().splitlines()
            self.assertEqual(json.loads(lines[0]), {"note": X.TRADES_NOTE})
            data = [json.loads(x) for x in lines[1:]]
            self.assertEqual(len(data), 1)  # the E2 entry is held
            self.assertTrue(all(tuple(d) == X.TRADE_FIELDS for d in data))
            self.assertNotIn("exit_reason", (o / "floor-closed-trades.jsonl").read_text())
            dt = json.loads((o / "daily-totals.json").read_text())
            self.assertEqual((dt["days"][0]["sends"], dt["note"], dt["unmatched_fee_sol_by_day"]), (8, X.TRADES_NOTE, {"2026-11-10": "-0.000009000"}))
            self.assertIn(X.TRADES_NOTE, (o / "NOTES.txt").read_text())
            self.assertNotIn("r4bt", (o / "board.md").read_text())

    def test_disclosures_sha_pin(self):
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / "d.json"
            p.write_text(json.dumps(DISC))
            self.assertEqual(X.load_disclosures(p, hashlib.sha256(p.read_bytes()).hexdigest()), DISC)
            with self.assertRaises(X.ExtractError):
                X.load_disclosures(p, "0" * 64)


if __name__ == "__main__":
    unittest.main()
