"""tools/forward_v_join.py on synthetic fixtures only. No /data/mal, no network, no real forward-1002 row.

Fixtures are tiny walk dirs (checkpoint.json, verify.jsonl, trades/trades-<hour>.jsonl.zst) built with the zstd binary,
with sentinel strings in every field a seal must not leak (signatures, mints, traders, V). The hash-only tests assert
those sentinels never reach stdout, stderr or a file.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import tools.forward_v_join as fj
from observe.trade_decode import EVENT_V_KEYS
from observe.trade_store import stored_trade

REPO = Path(__file__).resolve().parents[1]
HAVE_ZSTD = shutil.which("zstd") is not None
H1, H2, H3 = "2026-10-10T00", "2026-10-10T01", "2026-10-10T02"
SIG_SENTINEL = "SIGSENTINEL"
MINT_SENTINEL = "MINTSENTINEL"
TRADER_SENTINEL = "TRADERSENTINEL"
V_SENTINEL = 17_580_123_456_789  # a V value nothing else in the fixtures can equal


def ps_row(i: int, slot: int = 0, v: bool = False, *, venue: str = "pumpswap") -> dict:
    slot = slot or 1000 + i // 3
    row = {
        "v": 2, "venue": venue, "mint": f"{MINT_SENTINEL}{i % 7}", "trader": f"{TRADER_SENTINEL}{i}", "side": "buy" if i % 2 else "sell",
        "sol_lamports": 1_000 + i, "token_raw": 5_000_000 + i, "quote_reserve": 80_000_000_000 + i, "base_reserve": 9_000_000_000 + i,
        "price_sol": 1e-8, "pool": f"POOL{i % 7}", "slot": slot, "signature": f"{SIG_SENTINEL}{i:05d}", "event_index": i % 3,
        "t_recv_ms": None, "event_ts": 1_790_000_000 + i, "source": "backfill", "block_time": 1_790_000_000, "tx_index": i,
    }
    if venue != "pumpswap":
        row.update({"v": 1, "pool": None})
    if v:
        if venue == "pumpswap":
            row.update({"ix_name": "buy", "buyback_fee": 0, "virtual_quote_reserves": V_SENTINEL + i, "creator_fee_unclaimed": i,
                        "fee_recipient_zero": False})
        else:
            row.update({"ix_name": "buy"})
    return row


def base_rows(n: int, *, bonding_every: int = 5) -> list[dict]:
    return [ps_row(i, venue="pump_bonding" if bonding_every and i % bonding_every == 0 else "pumpswap") for i in range(n)]


def ev_of(rows: list[dict]) -> list[dict]:
    """The ev walk's version of base rows: the same row plus the V fields, keys inserted mid-row like the walker does."""
    out = []
    for r in rows:
        e = dict(r)
        if r["venue"] == "pumpswap":
            e.update({"ix_name": "buy", "buyback_fee": 0, "virtual_quote_reserves": V_SENTINEL + int(r["trader"][len(TRADER_SENTINEL):]),
                      "creator_fee_unclaimed": 7, "fee_recipient_zero": False})
        else:
            e["ix_name"] = "buy"
        out.append(e)
    return out


def write_walk(root: Path, hours: dict[str, list[dict] | bytes], *, seal: bool = True, verify: bool = True, bad_lines_in_verify: int = 0,
               skip_checkpoint: tuple[str, ...] = (), skip_verify: tuple[str, ...] = (), unsealed: tuple[str, ...] = ()) -> None:
    (root / "trades").mkdir(parents=True, exist_ok=True)
    cp: dict = {"hours": {}}
    vlines = []
    for h, rows in hours.items():
        path = root / "trades" / f"trades-{h}.jsonl.zst"
        data = rows if isinstance(rows, bytes) else b"".join(json.dumps(r, separators=(",", ":")).encode() + b"\n" for r in rows)
        proc = subprocess.run(["zstd", "-q", "-3", "-c"], input=data, capture_output=True, check=True)
        path.write_bytes(proc.stdout)
        if h not in skip_checkpoint:
            cp["hours"][h] = {"status": "sealed" if h not in unsealed else "partial"}
        if h not in skip_verify:
            vlines.append({"hour": h, "issues": [], "content": {"trades": {"duplicates": 0, "bad_lines": bad_lines_in_verify}},
                           "sha256": {"trades": hashlib.sha256(path.read_bytes()).hexdigest(), "creates": "0" * 64}})
    (root / "checkpoint.json").write_text(json.dumps(cp))
    (root / "verify.jsonl").write_text("".join(json.dumps(x) + "\n" for x in vlines))


class Cli:
    def __init__(self, rc: int, out: str, err: str):
        self.rc, self.out, self.err = rc, out, err

    @property
    def text(self) -> str:
        return self.out + self.err


def cli(*argv: str) -> Cli:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            rc = fj.main(list(argv))
        except SystemExit as exc:
            rc = int(exc.code or 0)
    return Cli(rc, out.getvalue(), err.getvalue())


def tree_text(root: Path) -> str:
    parts = []
    for p in root.rglob("*"):
        if p.is_file():
            parts.append(p.read_bytes().decode("utf-8", "replace"))
    return "\n".join(parts)


@unittest.skipUnless(HAVE_ZSTD, "needs the zstd binary")
class Fixture(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.base, self.ev, self.out = self.root / "forward-1002", self.root / "forward-1002ev", self.root / "out"
        self.ledger = self.root / "FINAL_READS.jsonl"
        self.final()  # the default world is after the FINAL; SealTests removes the ledger

    def walks(self, base: dict, ev: dict, **kw) -> None:
        write_walk(self.base, base)
        write_walk(self.ev, ev, **kw)

    def final(self, **over) -> None:
        doc = {"experiment": "EXP-012", "final": True, "utc_time": "2026-10-16T02:10:00Z", "rows_sha256": "a" * 64, "lock_sha256": "b" * 64}
        doc.update(over)
        self.ledger.write_text(json.dumps(doc) + "\n")

    def args(self, cmd: str, hours: tuple[str, str] = (H1, H2), *extra: str) -> list[str]:
        return [cmd, "--base", str(self.base), "--ev", str(self.ev), "--from", hours[0], "--to", hours[1],
                "--final-ledger", str(self.ledger), *extra]


class PerfectHourTests(Fixture):
    def test_identical_hour_is_usable_md5_matches_and_join_carries_v(self) -> None:
        rows = base_rows(60)
        self.walks({H1: rows}, {H1: ev_of(rows)})
        self.final()
        r = cli(*self.args("join", (H1, H2), "--out-dir", str(self.out)))
        self.assertEqual(r.rc, 0, r.text)
        self.assertIn(f"{H1} usable md5=match", r.out)
        res = json.loads((self.out / "join-report.json").read_text())
        (h,) = res["hours"]
        self.assertEqual((h["rows_base"], h["rows_ev"], h["matched_1to1"], h["rate"], h["rate_pumpswap"]), (60, 60, 60, 1.0, 1.0))
        self.assertEqual(h["fallback_rows"], 0)
        self.assertEqual((self.out / "fallback.jsonl").read_text(), "")
        vrows = list(fj.RowReader(self.out / f"v-{H1}.jsonl.zst"))
        self.assertEqual(len(vrows), 60, "every matched row carries ix_name or V")
        by_key = {fj.row_key(v): v for v in vrows}
        for r0 in ev_of(rows):
            k = fj.row_key(r0)
            self.assertEqual({x: by_key[k][x] for x in EVENT_V_KEYS if x in r0}, {x: r0[x] for x in EVENT_V_KEYS if x in r0})
        ps = [v for v in vrows if "virtual_quote_reserves" in v]
        self.assertEqual(len(ps), sum(1 for x in rows if x["venue"] == "pumpswap"))

    def test_iter_joined_rows_merges_v_into_the_forward_1002_rows(self) -> None:
        rows = base_rows(30)
        self.walks({H1: rows}, {H1: ev_of(rows)})
        self.final()
        self.assertEqual(cli(*self.args("join", (H1, H2), "--out-dir", str(self.out))).rc, 0)
        joined = list(fj.iter_joined_rows(self.base, self.out, H1))
        self.assertEqual(len(joined), 30)
        for got, want in zip(joined, ev_of(rows)):
            self.assertEqual(got, want)  # dict equality: base columns + the V fields, key order aside
            self.assertEqual(list(got)[: len(rows[0])], list(rows[joined.index(got)]), "base column order is kept")

    def test_emit_rows_writes_the_merged_rows(self) -> None:
        rows = base_rows(12)
        self.walks({H1: rows}, {H1: ev_of(rows)})
        self.final()
        self.assertEqual(cli(*self.args("join", (H1, H2), "--out-dir", str(self.out), "--emit", "rows")).rc, 0)
        got = list(fj.RowReader(self.out / f"joined-{H1}.jsonl.zst"))
        self.assertEqual(got, ev_of(rows))


class SealTests(Fixture):
    def setUp(self) -> None:
        super().setUp()
        rows = base_rows(60)
        self.walks({H1: rows}, {H1: ev_of(rows)})
        self.ledger.unlink()  # before the FINAL: no ledger

    def test_hash_only_prints_counts_and_no_value_and_writes_nothing(self) -> None:
        self.final()
        before = sorted(p.name for p in self.root.rglob("*"))
        r = cli(*self.args("check", (H1, H2), "--hash-only"))
        self.assertEqual(r.rc, 0, r.text)
        self.assertIn(f"{H1} usable md5=match rows_base=60 rows_ev=60 matched=60 rate=1.000000", r.out)
        for secret in (SIG_SENTINEL, MINT_SENTINEL, TRADER_SENTINEL, str(V_SENTINEL)[:8], "POOL", "virtual_quote"):
            self.assertNotIn(secret, r.text)
        self.assertEqual(sorted(p.name for p in self.root.rglob("*")), before, "hash-only creates no file")

    def test_hash_only_refuses_before_the_final_too_and_opens_no_trade_file(self) -> None:
        # EXP-025's seal lets only the walker, backfill_verify and hour counts touch forward-1002ev before the FINAL
        opened = []
        real_open = fj.RowReader.__iter__

        def spy(self_):
            opened.append(self_.path)
            return real_open(self_)

        fj.RowReader.__iter__ = spy
        try:
            r = cli(*self.args("check", (H1, H2), "--hash-only"))
        finally:
            fj.RowReader.__iter__ = real_open
        self.assertEqual(r.rc, 2, r.text)
        self.assertIn("sealed until the DEC-016 FINAL", r.err)
        self.assertEqual(opened, [], "no trade file is opened before the marker is checked")
        self.assertNotIn("usable", r.out)
        self.assertNotIn("md5=", r.text)

    def test_pins_runs_without_the_final_and_opens_no_trade_file(self) -> None:
        self.assertEqual(cli("pins").rc, 0)

    def test_hash_only_refuses_output_flags(self) -> None:
        for flag in ("--fallback-out", "--report-out"):
            r = cli(*self.args("check", (H1, H2), "--hash-only", flag, str(self.root / "x.out")))
            self.assertEqual(r.rc, 2, r.text)
            self.assertFalse((self.root / "x.out").exists())

    def test_values_modes_refuse_without_the_final(self) -> None:
        for argv in (self.args("check", (H1, H2)), self.args("check", (H1, H2), "--hash-only"),
                     self.args("check", (H1, H2), "--fallback-out", str(self.root / "fb.jsonl")),
                     self.args("join", (H1, H2), "--out-dir", str(self.out))):
            with self.subTest(argv=argv[0]):
                r = cli(*argv)
                self.assertEqual(r.rc, 2, r.text)
                self.assertIn("sealed until the DEC-016 FINAL", r.err)
                self.assertFalse(self.out.exists())
                self.assertFalse((self.root / "fb.jsonl").exists())
                self.assertNotIn(SIG_SENTINEL, r.text)
                self.assertNotIn("usable", r.out, "no per-hour line is printed before the refusal")

    def test_a_ledger_without_the_exp012_final_is_not_enough(self) -> None:
        cases = {
            "other experiment": {"experiment": "EXP-022", "final": True},
            "not final": {"experiment": "EXP-012", "final": False},
            "test window": {"experiment": "EXP-012", "final": True, "test_window": True},
        }
        for name, doc in cases.items():
            with self.subTest(case=name):
                self.ledger.write_text(json.dumps(doc) + "\n")
                r = cli(*self.args("join", (H1, H2), "--out-dir", str(self.out)))
                self.assertEqual(r.rc, 2, r.text)
                self.assertFalse(self.out.exists())

    def test_a_torn_or_invalid_ledger_refuses(self) -> None:
        self.ledger.write_text('{"experiment":"EXP-012","final":true}')  # no newline
        self.assertEqual(cli(*self.args("check", (H1, H2), "--report-out", str(self.root / "r.json"))).rc, 2)
        self.ledger.write_text("not json\n")
        self.assertEqual(cli(*self.args("check", (H1, H2), "--report-out", str(self.root / "r.json"))).rc, 2)
        self.assertFalse((self.root / "r.json").exists())

    def test_with_the_marker_the_values_modes_run_and_the_report_records_it(self) -> None:
        self.final()
        r = cli(*self.args("check", (H1, H2), "--report-out", str(self.root / "r.json"), "--fallback-out", str(self.root / "fb.jsonl")))
        self.assertEqual(r.rc, 0, r.text)
        rep = json.loads((self.root / "r.json").read_text())
        self.assertEqual(rep["final_marker"]["lock_sha256"], "b" * 64)
        self.assertEqual(rep["min_match_rate"], 0.995)
        self.assertEqual(rep["match_fields"], ["sol_lamports", "token_raw", "quote_reserve", "base_reserve"])
        self.assertNotIn(SIG_SENTINEL, (self.root / "r.json").read_text(), "the report is counts only")

    def test_there_is_no_flag_that_skips_the_gate(self) -> None:
        r = subprocess.run([sys.executable, "-m", "tools.forward_v_join", "join", "--help"], cwd=REPO, capture_output=True, text=True,
                           stdin=subprocess.DEVNULL, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        for word in ("skip", "force", "unsafe", "override", "no-seal", "bypass"):
            self.assertNotIn(word, r.stdout.lower())

    def test_a_bad_stream_prints_only_the_reason_never_a_row(self) -> None:
        # a truncated zstd stream: read_error, no message from the decoder
        self.final()
        p = self.ev / "trades" / f"trades-{H1}.jsonl.zst"
        data = p.read_bytes()
        p.write_bytes(data[: len(data) // 2])
        lines = [json.loads(x) for x in (self.ev / "verify.jsonl").read_text().splitlines()]
        lines[0]["sha256"]["trades"] = hashlib.sha256(p.read_bytes()).hexdigest()
        (self.ev / "verify.jsonl").write_text("".join(json.dumps(x) + "\n" for x in lines))
        r = cli(*self.args("check", (H1, H2), "--hash-only"))
        self.assertEqual(r.rc, 1, r.text)
        self.assertIn("refused(read_error)", r.out)
        for secret in (SIG_SENTINEL, MINT_SENTINEL, TRADER_SENTINEL):
            self.assertNotIn(secret, r.text)


class MatchTests(Fixture):
    def test_rate_at_exactly_99_5_percent_is_usable_and_the_misses_go_to_fallback(self) -> None:
        rows = base_rows(1000, bonding_every=0)  # all PumpSwap
        evs = ev_of(rows)[:995]
        self.walks({H1: rows}, {H1: evs})
        self.final()
        r = cli(*self.args("check", (H1, H2), "--report-out", str(self.root / "r.json"), "--fallback-out", str(self.root / "fb.jsonl")))
        self.assertEqual(r.rc, 0, r.text)
        self.assertIn(f"{H1} usable md5=MISMATCH", r.out)
        fb = [json.loads(x) for x in (self.root / "fb.jsonl").read_text().splitlines()]
        self.assertEqual(len(fb), 5)
        self.assertEqual({x["why"] for x in fb}, {"unmatched"})
        self.assertEqual({(x["signature"], x["event_index"]) for x in fb},
                         {(x["signature"], x["event_index"]) for x in rows[995:]})
        self.assertTrue(all({"hour", "slot", "signature", "event_index", "why"} == set(x) for x in fb))

    def test_rate_below_99_5_percent_refuses_the_hour_and_lists_every_pumpswap_row(self) -> None:
        rows = base_rows(1000, bonding_every=4)
        evs = ev_of(rows)[:994]  # 99.4%
        self.walks({H1: rows}, {H1: evs})
        self.final()
        r = cli(*self.args("join", (H1, H2), "--out-dir", str(self.out)))
        self.assertEqual(r.rc, 1, r.text)
        self.assertIn(f"{H1} refused(below_threshold)", r.out)
        self.assertFalse((self.out / f"v-{H1}.jsonl.zst").exists(), "a refused hour's V is not used")
        self.assertEqual(list(self.out.glob("*.tmp")), [])
        fb = [json.loads(x) for x in (self.out / "fallback.jsonl").read_text().splitlines()]
        ps = [x for x in rows if x["venue"] == "pumpswap"]
        self.assertEqual(len(fb), len(ps))
        self.assertEqual({x["why"] for x in fb}, {"hour_below_threshold"})
        self.assertEqual({(x["signature"], x["event_index"]) for x in fb}, {(x["signature"], x["event_index"]) for x in ps})

    def test_the_pumpswap_rate_is_held_to_99_5_too(self) -> None:
        # 1000 rows, 200 PumpSwap: drop 2 PumpSwap rows from ev = 99.8% overall but 99.0% PumpSwap
        rows = base_rows(1000, bonding_every=0)
        for i in range(0, 800):
            rows[i] = ps_row(i, venue="pump_bonding")
        evs = ev_of(rows)
        del evs[900], evs[901]
        self.walks({H1: rows}, {H1: evs})
        self.final()
        r = cli(*self.args("check", (H1, H2), "--hash-only"))
        self.assertIn("refused(below_threshold)", r.out)
        self.assertRegex(r.out, r"rate=0\.99800")
        self.assertRegex(r.out, r"ps_rate=0\.99000")

    def test_each_of_the_four_fields_must_be_equal(self) -> None:
        for fld in ("sol_lamports", "token_raw", "quote_reserve", "base_reserve"):
            with self.subTest(field=fld), tempfile.TemporaryDirectory() as tmp:
                self.root = Path(tmp)
                self.base, self.ev, self.ledger = self.root / "b", self.root / "e", self.root / "L.jsonl"
                self.final()
                rows = base_rows(1000, bonding_every=0)
                evs = ev_of(rows)
                evs[10][fld] += 1
                self.walks({H1: rows}, {H1: evs})
                self.final()
                r = cli(*self.args("check", (H1, H2), "--fallback-out", str(self.root / "fb.jsonl")))
                self.assertEqual(r.rc, 0, r.text)  # 999/1000 = 99.9%
                self.assertIn("field_mismatch=1", r.out)
                self.assertIn(f"{H1} usable md5=MISMATCH", r.out)
                (fb,) = [json.loads(x) for x in (self.root / "fb.jsonl").read_text().splitlines()]
                self.assertEqual((fb["why"], fb["signature"]), ("field_mismatch", rows[10]["signature"]))

    def test_a_mismatched_row_gets_no_v_in_the_join(self) -> None:
        rows = base_rows(1000, bonding_every=0)
        evs = ev_of(rows)
        evs[10]["token_raw"] += 1
        self.walks({H1: rows}, {H1: evs})
        self.final()
        self.assertEqual(cli(*self.args("join", (H1, H2), "--out-dir", str(self.out))).rc, 0)
        keys = {fj.row_key(v) for v in fj.RowReader(self.out / f"v-{H1}.jsonl.zst")}
        self.assertNotIn(fj.row_key(rows[10]), keys)
        self.assertEqual(len(keys), 999)

    def test_a_matched_pumpswap_row_without_v_is_listed_one_by_one(self) -> None:
        rows = base_rows(200, bonding_every=0)
        evs = ev_of(rows)
        del evs[3]["virtual_quote_reserves"]
        self.walks({H1: rows}, {H1: evs})
        self.final()
        r = cli(*self.args("check", (H1, H2), "--fallback-out", str(self.root / "fb.jsonl")))
        self.assertEqual(r.rc, 0, r.text)
        self.assertIn("v_missing=1", r.out)
        (fb,) = [json.loads(x) for x in (self.root / "fb.jsonl").read_text().splitlines()]
        self.assertEqual((fb["why"], fb["signature"]), ("v_missing", rows[3]["signature"]))

    def test_extra_ev_rows_count_against_the_rate(self) -> None:
        rows = base_rows(1000, bonding_every=0)
        evs = ev_of(rows) + ev_of([ps_row(5000 + i) for i in range(10)])  # 1010 ev rows, 1000 matched: 99.01%
        self.walks({H1: rows}, {H1: evs})
        self.final()
        r = cli(*self.args("check", (H1, H2), "--hash-only"))
        self.assertIn("refused(below_threshold)", r.out)

    def test_duplicate_keys_refuse_the_hour(self) -> None:
        rows = base_rows(400, bonding_every=0)
        evs = ev_of(rows) + [ev_of(rows)[0]]
        self.walks({H1: rows}, {H1: evs})
        r = cli(*self.args("check", (H1, H2), "--hash-only"))
        self.assertIn("refused(duplicate_keys)", r.out)
        rows2 = rows + [rows[0]]
        self.walks({H1: rows2}, {H1: ev_of(rows)})
        r = cli(*self.args("check", (H1, H2), "--hash-only"))
        self.assertIn("refused(duplicate_keys)", r.out)

    def test_md5_ignores_only_the_v_fields(self) -> None:
        rows = base_rows(100)
        evs = ev_of(rows)
        evs[7]["mint"] = "someone-else"  # a legacy column differs: the key and the four fields still match
        self.walks({H1: rows}, {H1: evs})
        r = cli(*self.args("check", (H1, H2), "--hash-only"))
        self.assertIn(f"{H1} usable md5=MISMATCH", r.out)
        self.assertRegex(r.out, r"matched=100 rate=1\.000000")

    def test_row_order_does_not_hide_a_match_but_flips_md5(self) -> None:
        rows = base_rows(100)
        evs = ev_of(rows)
        evs[0], evs[1] = evs[1], evs[0]
        self.walks({H1: rows}, {H1: evs})
        r = cli(*self.args("check", (H1, H2), "--hash-only"))
        self.assertIn(f"{H1} usable md5=MISMATCH", r.out)

    def test_empty_hours_are_refused(self) -> None:
        self.walks({H1: []}, {H1: []})
        r = cli(*self.args("check", (H1, H2), "--hash-only"))
        self.assertIn("refused(empty_hour)", r.out)


class BadHourTests(Fixture):
    def test_an_ev_hour_that_was_not_walked_is_a_bad_hour_with_every_pumpswap_row_listed(self) -> None:
        rows = base_rows(100)
        self.walks({H1: rows, H2: rows}, {H1: ev_of(rows)})
        self.final()
        r = cli(*self.args("check", (H1, H3), "--fallback-out", str(self.root / "fb.jsonl")))
        self.assertEqual(r.rc, 1, r.text)
        self.assertIn(f"{H1} usable", r.out)
        self.assertIn(f"{H2} refused(ev_not_walked)", r.out)
        fb = [json.loads(x) for x in (self.root / "fb.jsonl").read_text().splitlines()]
        ps = [x for x in rows if x["venue"] == "pumpswap"]
        self.assertEqual(len(fb), len(ps))
        self.assertEqual({(x["hour"], x["why"]) for x in fb}, {(H2, "hour_ev_not_walked")})

    def test_each_kind_of_unusable_ev_hour(self) -> None:
        rows = base_rows(100)
        cases = {
            "ev_not_sealed": dict(unsealed=(H1,)),
            "ev_not_verified": dict(skip_verify=(H1,)),
            "ev_not_walked": dict(skip_checkpoint=(H1,)),
            "ev_bad_lines": dict(bad_lines_in_verify=2),
        }
        for reason, kw in cases.items():
            with self.subTest(reason=reason), tempfile.TemporaryDirectory() as tmp:
                self.root = Path(tmp)
                self.base, self.ev, self.ledger = self.root / "b", self.root / "e", self.root / "L.jsonl"
                self.final()
                write_walk(self.base, {H1: rows})
                write_walk(self.ev, {H1: ev_of(rows)}, **kw)
                r = cli(*self.args("check", (H1, H2), "--hash-only"))
                self.assertIn(f"refused({reason})", r.out)

    def test_an_ev_file_changed_after_verify_is_a_bad_hour(self) -> None:
        rows = base_rows(100)
        self.walks({H1: rows}, {H1: ev_of(rows)})
        write_walk_file = self.ev / "trades" / f"trades-{H1}.jsonl.zst"
        write_walk_file.write_bytes(write_walk_file.read_bytes() + b"x")
        r = cli(*self.args("check", (H1, H2), "--hash-only"))
        self.assertIn("refused(ev_sha_mismatch)", r.out)

    def test_ev_hour_with_a_non_json_line_is_a_bad_hour_even_if_verify_was_lenient(self) -> None:
        rows = base_rows(100)
        raw = b"".join(json.dumps(r).encode() + b"\n" for r in ev_of(rows)) + b"not json\n"
        self.walks({H1: rows}, {H1: raw})
        r = cli(*self.args("check", (H1, H2), "--hash-only"))
        self.assertIn("refused(ev_bad_lines)", r.out)

    def test_a_forward_1002_hour_with_a_bad_line_lists_its_readable_pumpswap_rows(self) -> None:
        # walk 1's verify is non-strict, so this hour can still look verified; it used to be refused with an empty list
        rows = base_rows(100)
        raw = b"".join(json.dumps(r).encode() + b"\n" for r in rows) + b"not json\n"
        write_walk(self.base, {H1: raw})
        write_walk(self.ev, {H1: ev_of(rows)})
        r = cli(*self.args("join", (H1, H2), "--out-dir", str(self.out)))
        self.assertEqual(r.rc, 1, r.text)
        self.assertIn(f"{H1} refused(bad_lines) md5=match", r.out)
        self.assertIn("bad_lines_base=1 bad_lines_ev=0", r.out)
        fb = [json.loads(x) for x in (self.out / "fallback.jsonl").read_text().splitlines()]
        ps = [x for x in rows if x["venue"] == "pumpswap"]
        self.assertEqual(len(fb), len(ps))
        self.assertEqual({x["why"] for x in fb}, {"hour_bad_lines"})
        self.assertEqual({(x["signature"], x["event_index"]) for x in fb}, {(x["signature"], x["event_index"]) for x in ps})
        self.assertFalse((self.out / f"v-{H1}.jsonl.zst").exists(), "none of a bad_lines hour's V is used")
        (h,) = json.loads((self.out / "join-report.json").read_text())["hours"]
        self.assertEqual((h["bad_lines_base"], h["reason"], h["fallback_rows"]), (1, "bad_lines", len(ps)))

    def test_an_ev_hour_with_bad_lines_lists_every_pumpswap_row_of_the_base_hour(self) -> None:
        rows = base_rows(100)
        write_walk(self.base, {H1: rows})
        write_walk(self.ev, {H1: ev_of(rows)}, bad_lines_in_verify=2)
        r = cli(*self.args("check", (H1, H2), "--fallback-out", str(self.root / "fb.jsonl")))
        self.assertIn("refused(ev_bad_lines)", r.out)
        fb = [json.loads(x) for x in (self.root / "fb.jsonl").read_text().splitlines()]
        self.assertEqual(len(fb), sum(1 for x in rows if x["venue"] == "pumpswap"))
        self.assertEqual({x["why"] for x in fb}, {"hour_ev_bad_lines"})

    def test_a_base_hour_that_is_not_usable_has_nothing_to_join_and_nothing_to_list(self) -> None:
        rows = base_rows(100)
        write_walk(self.base, {H1: rows}, skip_verify=(H1,))
        write_walk(self.ev, {H1: ev_of(rows)})
        self.final()
        r = cli(*self.args("check", (H1, H2), "--fallback-out", str(self.root / "fb.jsonl")))
        self.assertEqual(r.rc, 1, r.text)
        self.assertIn("refused(base_not_verified)", r.out)
        self.assertEqual((self.root / "fb.jsonl").read_text(), "")

    def test_a_hole_in_the_range_is_reported_per_hour(self) -> None:
        rows = base_rows(100)
        self.walks({H1: rows, H3: rows}, {H1: ev_of(rows), H3: ev_of(rows)})  # H2 absent from both
        r = cli(*self.args("check", (H1, "2026-10-10T03"), "--hash-only"))
        self.assertEqual(r.rc, 1)
        self.assertIn(f"{H2} refused(base_not_walked)", r.out)
        self.assertIn("hours=3 usable=2 refused=1", r.out)

    def test_the_first_bad_hour_does_not_stop_the_later_ones(self) -> None:
        rows = base_rows(100)
        self.walks({H1: rows, H2: rows}, {H2: ev_of(rows)})
        r = cli(*self.args("check", (H1, H3), "--hash-only"))
        self.assertIn(f"{H1} refused(ev_not_walked)", r.out)
        self.assertIn(f"{H2} usable", r.out)


class OutputSafetyTests(Fixture):
    def setUp(self) -> None:
        super().setUp()
        rows = base_rows(50)
        self.walks({H1: rows}, {H1: ev_of(rows)})
        self.final()

    def test_never_writes_inside_a_walk_dir(self) -> None:
        for d in (self.base, self.ev):
            r = cli(*self.args("join", (H1, H2), "--out-dir", str(d / "sub")))
            self.assertEqual(r.rc, 2, r.text)
            self.assertFalse((d / "sub").exists())
            r = cli(*self.args("check", (H1, H2), "--fallback-out", str(d / "fb.jsonl")))
            self.assertEqual(r.rc, 2, r.text)
            self.assertFalse((d / "fb.jsonl").exists())

    def test_never_overwrites(self) -> None:
        self.assertEqual(cli(*self.args("join", (H1, H2), "--out-dir", str(self.out))).rc, 0)
        before = tree_text(self.out)
        r = cli(*self.args("join", (H1, H2), "--out-dir", str(self.out)))
        self.assertEqual(r.rc, 2, r.text)
        self.assertEqual(tree_text(self.out), before)

    def test_no_temp_files_remain_after_a_run_with_refused_hours(self) -> None:
        rows = base_rows(50)
        write_walk(self.base, {H1: rows, H2: rows})
        r = cli(*self.args("join", (H1, H3), "--out-dir", str(self.out)))
        self.assertEqual(r.rc, 1, r.text)
        self.assertEqual(sorted(p.name for p in self.out.iterdir()), ["fallback.jsonl", "join-report.json", f"v-{H1}.jsonl.zst"])


class RangeTests(unittest.TestCase):
    def test_hour_list(self) -> None:
        self.assertEqual(fj.hour_list("2026-10-09T23", "2026-10-10T02"), ["2026-10-09T23", "2026-10-10T00", "2026-10-10T01"])
        for a, b in (("2026-10-10", "2026-10-11T00"), ("2026-10-10T01", "2026-10-10T01"), ("2026-10-10T02", "2026-10-10T01")):
            with self.assertRaises(fj.Refused):
                fj.hour_list(a, b)


class DecoderPinTests(unittest.TestCase):
    def test_pins_equal_the_working_tree_and_git(self) -> None:
        for rel, want in fj.PINNED_BLOBS.items():
            self.assertEqual(fj.git_blob_sha(REPO / rel), want, rel)
        if shutil.which("git"):
            proc = subprocess.run(["git", "hash-object", *fj.PINNED_BLOBS], cwd=REPO, capture_output=True, text=True, timeout=60)
            if proc.returncode == 0:
                self.assertEqual(proc.stdout.split(), list(fj.PINNED_BLOBS.values()))

    def test_pins_command_and_a_changed_decoder_is_a_refusal(self) -> None:
        self.assertEqual(cli("pins").rc, 0)
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            for rel in fj.PINNED_BLOBS:
                (repo / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy(REPO / rel, repo / rel)
            fj.require_pins(repo)
            with (repo / "observe/trade_decode.py").open("a") as fh:
                fh.write("\n# edit\n")
            with self.assertRaises(fj.Refused) as ctx:
                fj.require_pins(repo)
            self.assertIn("observe/trade_decode.py", str(ctx.exception))

    def test_pins_at_a_git_ref_checks_the_blobs_a_job_would_run(self) -> None:
        if not shutil.which("git"):
            self.skipTest("no git")
        # this checkout's HEAD runs the pinned decoder
        self.assertEqual(cli("pins", "--ref", "HEAD").rc, 0)

        def has(ref: str) -> bool:
            return subprocess.run(["git", "cat-file", "-e", ref + "^{commit}"], cwd=REPO, capture_output=True).returncode == 0

        # job #433's gitRef (the walk) must carry exactly the pinned blobs
        if has("153f1a02fc9b9540644e48e3cf4fefacd035fca1"):
            r = cli("pins", "--ref", "153f1a02fc9b9540644e48e3cf4fefacd035fca1")
            self.assertEqual(r.rc, 0, r.text)
            self.assertEqual(r.out.count(" ok"), 3)
        # forward-1002 (job #382 at 2bd45f1) was written by older blobs: "same decoder" holds for V only
        if has("2bd45f1"):
            r = cli("pins", "--ref", "2bd45f1")
            self.assertEqual(r.rc, 2, r.text)
            self.assertEqual(r.out.count("DIFFERS"), 3)
            for older in ("a10e0568f3d6", "b5eb3f822e00", "8bcb5ebc4411"):
                self.assertIn(older, r.out)
        for bad in ("bad ref", "--output=x", "", "no-such-ref-zzzz"):
            self.assertEqual(cli("pins", "--ref", bad).rc, 2, bad)

    def test_check_refuses_when_the_pin_differs(self) -> None:
        old = dict(fj.PINNED_BLOBS)
        fj.PINNED_BLOBS["observe/trade_decode.py"] = "0" * 40
        try:
            with tempfile.TemporaryDirectory() as tmp:
                r = cli("check", "--base", tmp, "--ev", tmp, "--from", H1, "--to", H2, "--hash-only")
            self.assertEqual(r.rc, 2)
            self.assertIn("decoder pin", r.err)
        finally:
            fj.PINNED_BLOBS.clear()
            fj.PINNED_BLOBS.update(old)

    def test_the_join_drops_the_decoders_own_v_keys(self) -> None:
        self.assertIs(fj.EVENT_V_KEYS, EVENT_V_KEYS)
        self.assertEqual(set(EVENT_V_KEYS), {"virtual_quote_reserves", "ix_name", "creator_fee_unclaimed", "buyback_fee", "fee_recipient_zero"})
        self.assertIn(fj.V_FIELD, EVENT_V_KEYS)

    def test_dropping_the_v_keys_from_a_stored_row_gives_the_legacy_row(self) -> None:
        # the md5 premise, on the real stored_trade: an event_v row minus EVENT_V_KEYS is the legacy row, for both venues
        base = {"venue": "pumpswap", "mint": "m", "trader": "t", "side": "buy", "sol_lamports": 1, "token_raw": 2, "quote_reserve": 3,
                "base_reserve": 4, "price_sol": 1.0, "pool": "p", "slot": 5, "signature": "s", "event_index": 0, "t_recv_ms": None,
                "event_ts": 6, "quote_mint": "q", "quote_is_wsol": True}
        v = {"virtual_quote_reserves": 17_580_000_000, "ix_name": "buy", "creator_fee_unclaimed": 1, "buyback_fee": 2, "fee_recipient_zero": False}
        legacy = stored_trade(base)
        withv = stored_trade({**base, **v})
        self.assertEqual({k: x for k, x in withv.items() if k not in EVENT_V_KEYS}, legacy)
        self.assertEqual(set(withv) - set(legacy), set(v))
        bond = {**base, "venue": "pump_bonding", "pool": None}
        self.assertEqual({k: x for k, x in stored_trade({**bond, "ix_name": "buy"}).items() if k not in EVENT_V_KEYS}, stored_trade(bond))

    def test_walk_2_and_the_ev_walk_run_the_same_decoder(self) -> None:
        walker = (REPO / "tools" / "pump_history_backfill.py").read_text()
        self.assertRegex(walker, r"from observe\.trade_decode import")
        self.assertRegex(walker, r"from observe\.trade_store import")
        runs = []
        for name in ("forward-walk2.sh", "forward-walk-ev.sh"):
            text = (REPO / "scripts" / "research" / name).read_text()
            code = [x for x in text.splitlines() if not x.lstrip().startswith("#") and "tools.pump_history_backfill" in x]
            self.assertEqual(len(code), 1, name)
            self.assertIn("--event-v", code[0])
            runs.append(re.sub(r"\$\w+|\$\(\(.*?\)\)|\"[^\"]*\"", "X", code[0]).split())
        # same module, same flags, in the same order (the arguments' values are not compared)
        self.assertEqual(runs[0], runs[1])

    def test_walkers_decoder_files_are_exactly_the_pinned_ones(self) -> None:
        # the files the walker imports for decoding: if one is added, the pin list must grow
        walker = (REPO / "tools" / "pump_history_backfill.py").read_text()
        imported = set(re.findall(r"^from (observe\.[a-z_]+) import", walker, re.M))
        for mod in ("observe.trade_decode", "observe.trade_store"):
            self.assertIn(mod, imported)
            self.assertIn(mod.replace(".", "/") + ".py", fj.PINNED_BLOBS)
        self.assertIn("tools/pump_history_backfill.py", fj.PINNED_BLOBS)


if __name__ == "__main__":
    unittest.main()
