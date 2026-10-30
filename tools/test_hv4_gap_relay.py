"""hv4_gap_relay tests. Synthetic tape lines in temp dirs only; nothing under /data/mal is read ("/data/mal/arena" appears only as a string in refusal tests)."""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from tools import arena_zst
from tools import hv4_gap_relay as R
from tools import hv4_live_c0 as c0
from tools.test_hv4_live_c0 import ARM, Hv4Env

REPO = Path(__file__).resolve().parent.parent
HAVE_ZSTD = shutil.which("zstd") is not None
STEM = "tape-2026-10-26T10"


def line(**kw):
    return json.dumps(kw) + "\n"


def hour_ms(h, day=26):
    return int(datetime(2026, 10, day, h, tzinfo=timezone.utc).timestamp() * 1000)


def tline(t, slot=None, **kw):
    """A trade-typed tape line with only the instant and slot that the relay may read (plus whatever extra fields a test adds)."""
    d = {"type": "trade", "t_recv_ms": t}
    if slot is not None:
        d["slot"] = slot
    d.update(kw)
    return line(**d)


class Base(unittest.TestCase):
    def setUp(self):
        self._d = tempfile.TemporaryDirectory()
        self.addCleanup(self._d.cleanup)
        self.tmp = Path(self._d.name)
        self.tape = self.tmp / "tape"
        self.tape.mkdir()

    def write(self, name, *lines):
        (self.tape / name).write_text("".join(lines))

    def append(self, name, *lines):
        with open(self.tape / name, "a") as fh:
            fh.write("".join(lines))


class ScannerTests(Base):
    def test_kinds_come_from_the_engine_definition(self):
        from tools import arena_paper
        self.assertEqual(R.HARD_GAP_KINDS, arena_paper.HARD_GAP_KINDS)
        cfg = arena_paper.StackConfig()
        self.assertEqual(R.GAP_SILENCE_MS, cfg.gap_silence_ms)
        self.assertEqual(R.GAP_SLOTS, cfg.gap_slots)
        self.assertTrue(set(R.RELAY_KINDS) <= set(c0.GAP_BREAK_KINDS))
        self.assertIn("gap_slots", c0.GAP_BREAK_KINDS)

    def test_compaction_grace_equals_the_feed(self):
        # read from the source text: tools.arena_feed needs network libraries that the test venv does not have
        m = re.search(r"^COMPACT_GRACE_MS = ([\d_]+)", (REPO / "tools" / "arena_feed.py").read_text(), re.M)
        self.assertIsNotNone(m)
        self.assertEqual(R.COMPACT_GRACE_MS, int(m.group(1).replace("_", "")))

    def test_only_kind_and_instant_are_emitted(self):
        self.write("a.jsonl",
                   line(type="gap", kind="process_restart", t_recv_ms=1000, prog="CREDENTIAL_LIKE", slot=9, price=123.0, mint="DECOY"),
                   line(type="gap", kind="soft_thing", t_recv_ms=1500),
                   line(type="gap", kind="x", data_loss=True, t_recv_ms=2000, mint="DECOY"),
                   line(type="start", t_recv_ms=3000, extra="DECOY"),
                   line(type="gap", kind="takeover", t_recv_ms=4000))
        rows = R.scan([self.tape / "a.jsonl"])
        self.assertEqual(rows, [{"kind": "process_restart", "ms": 1000}, {"kind": "data_loss", "ms": 2000}, {"kind": "start", "ms": 3000}, {"kind": "takeover", "ms": 4000}])
        self.assertTrue(all(set(r) == {"kind", "ms"} for r in rows))

    def test_trade_lines_are_never_parsed_only_instant_and_slot_are_used(self):
        # the trade line is not valid JSON after the slot: a parser would choke, the relay only matches type, t_recv_ms and slot
        bad = b'{"type": "trade", "t_recv_ms": 10, "slot": 5, "price": ###, "mint": "DECOY", "sol_lamports": \xff\xfe\n'
        later = b'{"type": "trade", "t_recv_ms": %d, "slot": 6, "price": ###\n' % (10 + R.GAP_SILENCE_MS + 1)
        (self.tape / "a.jsonl").write_bytes(bad + later)
        self.assertEqual(R.scan([self.tape / "a.jsonl"]), [{"kind": "gap_silence", "ms": 10 + R.GAP_SILENCE_MS + 1}])
        (self.tape / "b.jsonl").write_bytes(bad + b'{"type": "trade", "t_recv_ms": %d}\n' % (10 + R.GAP_SILENCE_MS))
        self.assertEqual(R.scan([self.tape / "b.jsonl"]), [])  # exactly the bound is not a breach (> only)

    def test_json_loads_sees_only_gap_and_start_lines(self):
        real = json.loads
        seen = []

        def spy(s, *a, **k):
            seen.append(s)
            return real(s, *a, **k)

        self.write("a.jsonl",
                   tline(1, 1, price=1.5, mint="DECOY", pool="DECOY", sol_lamports=7, quote_reserve=8, base_reserve=9, signature="DECOY"),
                   line(type="gap", kind="process_restart", t_recv_ms=2),
                   tline(3, 2, price=2.5), line(type="start", t_recv_ms=4), line(type="hb", t_recv_ms=5, slot=3),
                   line(type="create", t_recv_ms=6, name='x", "type": "gap'), tline(7, 3))
        with mock.patch.object(R.json, "loads", spy):
            R.scan([self.tape / "a.jsonl"])
        self.assertEqual(len(seen), 2)
        for s in seen:
            self.assertIn(real(s)["type"], ("gap", "start"))

    def test_the_only_tape_line_regexes_are_type_instant_and_slot(self):
        pats = {v.pattern for v in vars(R).values() if isinstance(v, re.Pattern) and isinstance(v.pattern, bytes)}
        self.assertEqual(pats, {rb'"type"\s*:\s*"([a-z_]+)"', rb'"t_recv_ms"\s*:\s*(\d+)', rb'"slot"\s*:\s*(\d+)'})

    def test_no_field_other_than_the_three_reaches_a_row_or_the_state(self):
        # every other trade field carries a recognisable value; none may appear anywhere in the stream or the state file
        out = self.tmp / "out"
        t0 = hour_ms(10)
        self.write(f"{STEM}.jsonl",
                   tline(t0, 100, mint="DECOYMINT", pool="DECOYPOOL", signature="DECOYSIG", price=0.123456, sol_lamports=987654321, quote_reserve=111222333, prog="DECOYPROG"),
                   tline(t0 + R.GAP_SILENCE_MS + 1, 101, mint="DECOYMINT", signature="DECOYSIG2", price=0.7, sol_lamports=987654322))
        self.assertEqual(R.main(["--tape-dir", str(self.tape), "--bot-id", ARM, "--out-dir", str(out)]), 0)
        blob = b"".join(p.read_bytes() for p in out.rglob("*") if p.is_file())
        for decoy in (b"DECOY", b"0.123456", b"987654321", b"111222333"):
            self.assertNotIn(decoy, blob)

    def test_torn_tail_is_ignored_and_output_is_sorted_and_deduplicated(self):
        self.write("a.jsonl", line(type="gap", kind="feed_restart", t_recv_ms=500), line(type="gap", kind="feed_restart", t_recv_ms=500))
        with open(self.tape / "a.jsonl", "a") as fh:
            fh.write('{"type": "gap", "kind": "feed_restart", "t_recv_ms": 9')
        self.assertEqual(R.scan([self.tape / "a.jsonl"]), [{"kind": "feed_restart", "ms": 500}])

    # --- P7-a
    def test_gap_slots_threshold_and_the_engines_elif_order(self):
        t = 1_000_000
        # depth 1 = the engine's own high-water rule on a tape with no rejected rows
        self.write("a.jsonl", tline(t, 1000), tline(t + 1000, 1150),  # exactly 150 above: not a breach
                   tline(t + 2000, 1301),  # 151 above the high-water mark 1150: a breach
                   tline(t + 3000, 1500))  # 199 above 1301: a breach
        rows = R.scan([self.tape / "a.jsonl"], depth=1)
        self.assertEqual(rows, [{"kind": "gap_slots", "ms": t + 2000}, {"kind": "gap_slots", "ms": t + 3000}])
        # silence first: a row that is both more than 60 s and more than 150 slots later is ONE gap_silence (the engine's elif)
        self.write("b.jsonl", tline(t, 10), tline(t + R.GAP_SILENCE_MS + 1, 10 + 500))
        self.assertEqual(R.scan([self.tape / "b.jsonl"], depth=1), [{"kind": "gap_silence", "ms": t + R.GAP_SILENCE_MS + 1}])

    def test_gap_slots_ignores_rows_without_a_slot_and_lookalike_keys(self):
        t = 5_000_000
        self.write("a.jsonl", tline(t, 100), tline(t + 10, to_slot=10**9, last_slot=10**9, slot_x=10**9, ix_name='a"slot": 10000000'), tline(t + 20, 120), tline(t + 30, 121))
        self.assertEqual(R.scan([self.tape / "a.jsonl"], depth=1), [])

    def test_echo_after_a_real_gap_is_bounded_by_the_depth(self):
        # a real silence: the next depth-1 rows still compare against an older reference (stricter, never looser), then it stops
        t = 7_000_000
        lines = [tline(t + i * 100, 1000 + i) for i in range(10)]
        g = t + 900 + R.GAP_SILENCE_MS + 1
        lines += [tline(g + i * 100, 1010 + i) for i in range(10)]
        self.write("a.jsonl", *lines)
        rows = R.scan([self.tape / "a.jsonl"])
        self.assertEqual([r["ms"] for r in rows], [g + i * 100 for i in range(R.REF_DEPTH)])
        self.assertTrue(all(r["kind"] == "gap_silence" for r in rows))
        self.assertEqual(R.scan([self.tape / "a.jsonl"], depth=1), [{"kind": "gap_silence", "ms": g}])

    def test_rows_without_an_instant_and_other_row_types_do_not_move_the_references(self):
        t = 9_000_000
        self.write("a.jsonl", tline(t, 1), line(type="hb", t_recv_ms=t + 10**9, slot=10**9), line(type="create", t_recv_ms=t + 10**9, slot=10**9),
                   line(type="trade", slot=10**9), tline(t + 1000, 2))
        self.assertEqual(R.scan([self.tape / "a.jsonl"]), [])


# --- the engine cross-test (P7-d)
V = 17_584_505_288
S0 = 400_000_000


def engine_row(sig, t, slot, prog="pumpswap", pool="P1", side="buy"):
    return {"type": "trade", "prog": prog, "venue": "pumpswap" if prog == "pumpswap" else "pump_bonding", "pool": pool, "mint": "M" + pool, "side": side,
            "sol_lamports": 1_000_000_000, "token_raw": 10**11, "quote_reserve": 60 * 10**9, "base_reserve": 10**15, "slot": slot, "event_index": 0,
            "signature": sig, "t_recv_ms": t, "virtual_quote_reserves": V}


def scenario():
    """(rows in tape order, named instants). Seconds are since 10:00:00Z of 2026-10-26; the slot clock runs 2.5 slots per second."""
    H10, H11 = hour_ms(10), hour_ms(11)
    rows: list[dict] = []
    names: dict[str, int] = {}
    shift = [0]

    def sl(s):
        return S0 + int(2.5 * s) + shift[0]

    def tr(sig, s, slot=None, **kw):
        return engine_row(sig, H10 + int(s * 1000), sl(s) if slot is None else slot, **kw)

    rows.append({"type": "start", "prog": "all", "slot": 0, "t_recv_ms": H10 + 500, "signature": None, "event_index": None})  # the engine ignores the FIRST start
    for s in range(1, 61, 2):
        rows.append(tr(f"n{s}", s))
    # silence 1 (100 s -> 171 s) with a duplicate of A and an unknown-venue row (with a far-ahead slot) INSIDE it
    a_slot = sl(100)
    rows.append(tr("A", 100))
    rows.append(tr("A", 130, slot=a_slot))  # duplicate of A: same slot, signature, event_index
    rows.append(tr("U1", 145, slot=a_slot + 200, prog="mystery"))  # unknown venue
    rows.append(tr("B", 171))
    names["silence1"] = H10 + 171_000
    for s in range(172, 199, 2):
        rows.append(tr(f"m{s}", s))
    # a slot jump (no silence) with a duplicate and an unknown-venue row (far-ahead slot) just before the first row that shows it
    rows.append(tr("m198", 199.2, slot=sl(198)))  # duplicate of the m198 row above (same slot, signature, event_index)
    rows.append(tr("U2", 199.5, slot=sl(198) + 400, prog="mystery"))
    shift[0] = 400
    rows.append(tr("J", 200))
    names["jump"] = H10 + 200_000
    for s in range(202, 341, 2):
        rows.append(tr(f"p{s}", s))
        if s == 300:
            rows.append({"type": "gap", "kind": "process_restart", "data_loss": True, "prog": None, "t_recv_ms": H10 + 300_500, "slot": sl(300)})
            names["restart"] = H10 + 300_500
        if s == 310:
            rows.append({"type": "gap", "kind": "decode_error", "data_loss": True, "prog": "pumpswap", "t_recv_ms": H10 + 310_500, "slot": sl(310)})
            names["decode"] = H10 + 310_500
        if s == 320:
            rows.append({"type": "gap", "kind": "socket_reconnect", "data_loss": False, "prog": "pumpswap", "t_recv_ms": H10 + 320_500, "slot": sl(320)})  # soft
        if s == 330:
            rows.append({"type": "gap", "kind": "takeover", "prog": "all", "t_recv_ms": H10 + 330_500, "slot": sl(330)})  # soft for the engine; the relay is stricter
        if s == 340:
            rows.append({"type": "start", "prog": "all", "slot": 0, "t_recv_ms": H10 + 340_500, "signature": None, "event_index": None})  # engine: a feed_start gap
            names["start"] = H10 + 340_500
    for s in range(370, 3591, 15):  # at least REF_DEPTH rows per 60 s: no breach on a feed this dense
        rows.append(tr(f"q{s}", s))
    rows.append(tr("L", 3590))
    rows.append({"type": "gap", "kind": "write_error", "data_loss": True, "prog": "all", "t_recv_ms": H10 + 3_595_000, "slot": sl(3590)})  # the last minutes of the hour
    names["late"] = H10 + 3_595_000
    for s in range(3675, 3700, 2):  # the first row of the next hour is 85 s after L: a silence spanning the hour boundary
        rows.append(tr(f"r{s}", s))
    names["boundary"] = H10 + 3_675_000
    assert rows[-1]["t_recv_ms"] >= H11
    return rows, names


def tape_name(row):
    return time.strftime("tape-%Y-%m-%dT%H", time.gmtime(row["t_recv_ms"] / 1000))


ENGINE_TO_RELAY = {"engine_silence": {"gap_silence"}, "engine_slot_jump": {"gap_slots"}, "feed_start": {"start", "feed_start"}, "process_restart": {"process_restart"},
                   "feed_restart": {"feed_restart"}}


class EngineCrossTests(Base):
    def engine_gaps(self, rows):
        from tools.arena_paper import ArenaEngine, StackConfig
        eng = ArenaEngine(self.tmp / "ledger", self.tmp / "bots.json", StackConfig(feed_delay_slots=0), code_state_fn=lambda: ("abc1234", False), now_fn=lambda: 0)
        for r in rows:
            eng.on_row(r)
        return eng

    def check_superset(self, eng, relay_rows, names=None):
        by_ms: dict[int, set[str]] = {}
        for r in relay_rows:
            by_ms.setdefault(r["ms"], set()).add(r["kind"])
        self.assertTrue(eng.gaps)
        for g in eng.gaps:
            self.assertIn(g.t_hi, by_ms, f"engine gap {g.kind} ending {g.t_hi} is missing from the relay output")
            allowed = ENGINE_TO_RELAY.get(g.kind, {"data_loss"})
            if g.kind in ("engine_silence", "engine_slot_jump"):  # the relay may be STRICTER, never looser: the same instant must carry a relay breach row
                self.assertTrue(by_ms[g.t_hi] <= {"gap_silence", "gap_slots"} and by_ms[g.t_hi], (g.kind, by_ms[g.t_hi]))
            else:
                self.assertTrue(by_ms[g.t_hi] & allowed, (g.kind, by_ms[g.t_hi]))

    def test_engine_gaps_are_a_subset_of_the_relay_output_over_a_full_scan(self):
        rows, names = scenario()
        eng = self.engine_gaps(rows)
        # the scenario really exercises the rejection paths and every rule
        self.assertEqual(eng.counters.get("rows_dup"), 2)
        self.assertEqual(sorted(g.kind for g in eng.gaps), sorted(["engine_silence", "engine_silence", "engine_slot_jump", "process_restart", "decode_error", "feed_start", "write_error"]))
        self.assertEqual({g.t_hi for g in eng.gaps}, {names[k] for k in ("silence1", "jump", "restart", "decode", "start", "late", "boundary")})
        # write the tape: one file per UTC hour of the row's instant
        for r in rows:
            with open(self.tape / f"{tape_name(r)}.jsonl", "a") as fh:
                fh.write(json.dumps(r) + "\n")
        files = sorted(self.tape.glob("*.jsonl"))
        self.assertEqual([f.name for f in files], ["tape-2026-10-26T10.jsonl", "tape-2026-10-26T11.jsonl"])
        got = R.scan(files)
        self.check_superset(eng, got)
        by = {(r["kind"], r["ms"]) for r in got}
        self.assertIn(("gap_silence", names["silence1"]), by)
        self.assertIn(("gap_slots", names["jump"]), by)
        self.assertNotIn(("gap_slots", names["silence1"]), by)  # elif order: the silence wins
        self.assertIn(("gap_silence", names["boundary"]), by)
        # the control: with depth 1 (the pre-v2 rule: every trade-typed line moves the reference) the relay MISSES the two gaps hidden by the rejected rows
        v1 = {(r["kind"], r["ms"]) for r in R.scan(files, depth=1)}
        self.assertNotIn(("gap_silence", names["silence1"]), v1)
        self.assertFalse(any(ms == names["jump"] for _k, ms in v1))

    def test_staged_passes_with_a_torn_line_compaction_and_an_hour_roll(self):
        if not HAVE_ZSTD:
            self.skipTest("zstd CLI not installed")
        rows, names = scenario()
        eng = self.engine_gaps(rows)
        out = self.tmp / "out"
        stream = out / "transport" / f"{ARM}.tapegaps.jsonl"
        relay = R.Relay(self.tape, ARM, out / "state" / "s.json")
        (out / "transport").mkdir(parents=True)
        (out / "state").mkdir()

        def publish(rs):
            R.write_stream(stream, rs)

        h10 = [r for r in rows if tape_name(r) == STEM]
        h11 = [r for r in rows if tape_name(r) != STEM]
        i_u1 = next(i for i, r in enumerate(h10) if r.get("signature") == "U1")
        i_last = next(i for i, r in enumerate(h10) if r.get("signature") == "L")
        # pass 1 (cold): everything up to and including the unknown-venue row inside silence 1
        self.write(f"{STEM}.jsonl", *[json.dumps(r) + "\n" for r in h10[: i_u1 + 1]])
        info1 = relay.run_pass(publish)
        self.assertEqual(info1["hours"], 1)
        # pass 2: the rest of the hour up to L, the last line of which is torn (a write in progress)
        self.append(f"{STEM}.jsonl", *[json.dumps(r) + "\n" for r in h10[i_u1 + 1: i_last]])
        full_l = json.dumps(h10[i_last]) + "\n"
        self.append(f"{STEM}.jsonl", full_l[:25])
        off_before = relay.offsets[STEM]
        relay.run_pass(publish)
        size = (self.tape / f"{STEM}.jsonl").stat().st_size
        self.assertEqual(relay.offsets[STEM], size - 25)  # the torn tail is not consumed
        self.assertGreater(relay.offsets[STEM], off_before)
        # between passes: L completes, the late gap row lands in the last minutes of the hour, the feed compacts the hour (source deleted) and the next hour starts
        self.append(f"{STEM}.jsonl", full_l[25:], *[json.dumps(r) + "\n" for r in h10[i_last + 1:]])
        arena_zst.compact_file(self.tape / f"{STEM}.jsonl")
        self.assertFalse((self.tape / f"{STEM}.jsonl").exists())
        self.write("tape-2026-10-26T11.jsonl", *[json.dumps(r) + "\n" for r in h11])
        info3 = relay.run_pass(publish)
        self.assertEqual(info3["sealed_through"], STEM)  # the closed .zst hour is sealed after its remainder is read
        self.assertNotIn(STEM, relay.offsets)
        got = [json.loads(x) for x in stream.read_text().splitlines()]
        self.check_superset(eng, got)
        by = {(r["kind"], r["ms"]) for r in got}
        self.assertIn(("data_loss", names["late"]), by)  # only reachable through the .zst remainder
        self.assertIn(("gap_silence", names["boundary"]), by)  # the silence spanning the hour roll
        # the staged result equals the one-shot full scan of the same tape
        self.assertEqual(got, R.scan([self.tape / f"{STEM}.jsonl.zst", self.tape / "tape-2026-10-26T11.jsonl"]))
        # a restarted process resumes from the state file and changes nothing
        again = R.Relay(self.tape, ARM, out / "state" / "s.json")
        again.run_pass(publish)
        self.assertEqual([json.loads(x) for x in stream.read_text().splitlines()], got)

    def test_cold_start_seeds_the_boundary_from_the_newest_closed_hour(self):
        if not HAVE_ZSTD:
            self.skipTest("zstd CLI not installed")
        rows, names = scenario()
        eng = self.engine_gaps(rows)
        for r in rows:
            with open(self.tape / f"{tape_name(r)}.jsonl", "a") as fh:
                fh.write(json.dumps(r) + "\n")
        arena_zst.compact_file(self.tape / f"{STEM}.jsonl")
        out: list = []
        cold = R.Relay(self.tape, ARM, self.tmp / "s2.json")
        cold.run_pass(out.append)
        self.check_superset(eng, out[-1])
        only_open = R.Relay(self.tape, ARM, self.tmp / "s1.json", cold_hours=1)
        only_open.run_pass(out.append)
        self.assertNotIn(names["boundary"], {r["ms"] for r in out[-1]})  # what the .zst seed is for: one hour alone cannot see the roll


class StateAndPassTests(Base):
    def relay(self, **kw):
        self.published: list = []
        return R.Relay(self.tape, ARM, self.tmp / "state.json", **kw)

    def go(self, relay):
        return relay.run_pass(lambda rows: self.published.append(rows))

    def test_state_file_is_atomic_and_carries_the_references_across_passes(self):
        t0 = hour_ms(10)
        self.write(f"{STEM}.jsonl", tline(t0, 100), tline(t0 + 1000, 101), tline(t0 + 2000, 102), tline(t0 + 3000, 103))
        r = self.relay()
        self.go(r)
        st = json.loads((self.tmp / "state.json").read_text())
        self.assertEqual(st["top_t"], [t0, t0 + 1000, t0 + 2000, t0 + 3000])
        self.assertEqual([p.name for p in self.tmp.iterdir() if p.name.startswith(".")], [])  # no tmp left
        self.append(f"{STEM}.jsonl", tline(t0 + 3000 + R.GAP_SILENCE_MS + 1 + 3000, 104))
        r2 = self.relay()  # a new process: the reference comes from the state file, not from re-reading
        info = self.go(r2)
        self.assertEqual(info["new_rows"], 1)
        self.assertEqual(self.published[-1], [{"kind": "gap_silence", "ms": t0 + 3000 + R.GAP_SILENCE_MS + 1 + 3000}])

    def test_incremental_pass_reads_only_new_bytes(self):
        t0 = hour_ms(10)
        self.write(f"{STEM}.jsonl", tline(t0, 1), tline(t0 + 1, 2))
        r = self.relay()
        self.assertGreater(self.go(r)["bytes"], 0)
        self.assertEqual(self.go(r)["bytes"], 0)
        self.append(f"{STEM}.jsonl", tline(t0 + 2, 3))
        self.assertEqual(self.go(r)["bytes"], len(tline(t0 + 2, 3)))

    def test_every_pass_publishes_even_with_no_new_row(self):
        self.write(f"{STEM}.jsonl", tline(hour_ms(10), 1))
        r = self.relay()
        self.go(r)
        self.go(r)
        self.go(r)
        self.assertEqual(len(self.published), 3)

    def test_corrupt_or_foreign_state_is_a_cold_start_and_says_so(self):
        self.write(f"{STEM}.jsonl", tline(hour_ms(10), 1))
        (self.tmp / "state.json").write_text("{not json")
        info = self.go(self.relay())
        self.assertIn("state_invalid", info["notes"])
        (self.tmp / "state.json").write_text(json.dumps({"v": 1, "bot_id": ARM, "tape_dir": "/elsewhere", "sealed_through": None, "offsets": {}, "top_t": [], "top_slot": [], "rows": []}))
        info = self.go(self.relay())
        self.assertIn("tape_dir_changed", info["notes"])
        for bad in ({"v": 2}, {"v": 1, "bot_id": "other"}, {"v": 1, "bot_id": ARM, "tape_dir": str(self.tape), "sealed_through": "x", "offsets": {}, "top_t": [], "top_slot": [], "rows": []},
                    {"v": 1, "bot_id": ARM, "tape_dir": str(self.tape), "sealed_through": None, "offsets": {STEM: -1}, "top_t": [], "top_slot": [], "rows": []},
                    {"v": 1, "bot_id": ARM, "tape_dir": str(self.tape), "sealed_through": None, "offsets": {}, "top_t": [True], "top_slot": [], "rows": []},
                    {"v": 1, "bot_id": ARM, "tape_dir": str(self.tape), "sealed_through": None, "offsets": {}, "top_t": [], "top_slot": [], "rows": [{"kind": "nope", "ms": 1}]}):
            (self.tmp / "state.json").write_text(json.dumps(bad))
            self.assertEqual(self.relay().reset_reason, "state_invalid", bad)

    def test_a_failed_pass_rolls_back_and_does_not_publish(self):
        t0 = hour_ms(10)
        self.write(f"{STEM}.jsonl", tline(t0, 1), tline(t0 + 10, 2))
        r = self.relay()
        self.go(r)
        before = (list(r.sc.top_t), dict(r.offsets), set(r.sc.rows))
        self.append(f"{STEM}.jsonl", tline(t0 + 20, 3), tline(t0 + 30, 4))
        real = r.sc.feed_stream

        def boom(read):
            real(read)
            raise OSError("disk went away")

        with mock.patch.object(r.sc, "feed_stream", boom):
            with self.assertRaises(OSError):
                self.go(r)
        self.assertEqual((r.sc.top_t, r.offsets, r.sc.rows), before)
        self.assertEqual(len(self.published), 1)  # nothing was published by the failed pass
        self.go(r)  # and the retry sees the rows exactly once
        self.assertEqual(r.sc.top_t, sorted([t0, t0 + 10, t0 + 20, t0 + 30]))

    def test_sealing_prunes_offsets_and_a_vanished_stem_is_dropped(self):
        t0 = hour_ms(10)
        self.write(f"{STEM}.jsonl", tline(t0, 1))
        self.write("tape-2026-10-26T11.jsonl", tline(hour_ms(11) + R.SEAL_AFTER_MS + 5, 2))
        r = self.relay()
        self.go(r)
        self.assertEqual(r.sealed_through, STEM)  # an instant past the hour end plus the grace was seen
        self.assertEqual(list(r.offsets), ["tape-2026-10-26T11"])
        (self.tape / "tape-2026-10-26T11.jsonl").unlink()
        self.go(r)
        self.assertEqual(r.offsets, {})

    def test_an_hour_inside_the_compaction_grace_is_not_sealed(self):
        t0 = hour_ms(10)
        self.write(f"{STEM}.jsonl", tline(t0, 1))
        self.write("tape-2026-10-26T11.jsonl", tline(hour_ms(11) + 5_000, 2))  # the writer can still append a late row to hour 10
        r = self.relay()
        self.go(r)
        self.assertIsNone(r.sealed_through)
        self.assertEqual(set(r.offsets), {STEM, "tape-2026-10-26T11"})
        self.append(f"{STEM}.jsonl", line(type="gap", kind="process_restart", t_recv_ms=t0 + 3_599_000))
        self.go(r)  # the old hour is still read: the next pass sees the late row (and the floor is the oldest unsealed hour)
        self.assertIn({"kind": "process_restart", "ms": t0 + 3_599_000}, self.published[-1])

    def test_retention_and_row_cap(self):
        new = hour_ms(10)
        old = new - R.RETAIN_MS - 1
        self.write(f"{STEM}.jsonl", line(type="gap", kind="process_restart", t_recv_ms=old), line(type="gap", kind="feed_restart", t_recv_ms=new))
        self.go(self.relay())
        self.assertEqual(self.published[-1], [{"kind": "feed_restart", "ms": new}])
        with mock.patch.object(R, "MAX_ROWS", 3):
            self.write(f"{STEM}.jsonl", *[line(type="gap", kind="process_restart", t_recv_ms=new + i) for i in range(10)])
            (self.tmp / "state.json").unlink()
            info = self.go(self.relay())
        self.assertEqual([r["ms"] for r in self.published[-1]], [new + 7, new + 8, new + 9])
        self.assertIn("rows_dropped:7", info["notes"])

    def test_discover_takes_only_regular_tape_hour_files(self):
        self.write(f"{STEM}.jsonl", tline(1, 1))
        (self.tape / f"{STEM}.jsonl.zst.tmp").write_bytes(b"x")
        (self.tape / "arena-feed-status.json").write_text("{}")
        (self.tape / "tape-2026-13-40T10.jsonl").write_text("")
        (self.tape / "tape-2026-10-26T12.jsonl").symlink_to(self.tape / f"{STEM}.jsonl")
        (self.tape / "other.jsonl").write_text("")
        self.assertEqual(sorted(R.discover(self.tape)), [STEM])

    @unittest.skipUnless(HAVE_ZSTD, "zstd CLI not installed")
    def test_jsonl_is_preferred_while_both_exist_and_a_vanished_source_falls_back_to_the_zst(self):
        t0 = hour_ms(10)
        self.write(f"{STEM}.jsonl", tline(t0, 1), line(type="gap", kind="process_restart", t_recv_ms=t0 + 5))
        arena_zst.compact_file(self.tape / f"{STEM}.jsonl")
        zst = self.tape / f"{STEM}.jsonl.zst"
        # the compactor verifies before deleting, so for a moment both exist: plant a .jsonl that differs, to see which one is read
        (self.tape / f"{STEM}.jsonl").write_text(line(type="gap", kind="feed_restart", t_recv_ms=t0 + 9))
        r = self.relay()
        self.go(r)
        self.assertEqual(self.published[-1], [{"kind": "feed_restart", "ms": t0 + 9}])
        # a source that disappears between the listing and the open
        r2 = R.Relay(self.tape, ARM, self.tmp / "state2.json")
        (self.tape / f"{STEM}.jsonl").unlink()
        notes: list = []
        off, n, kind = r2._read_stem({"jsonl": self.tape / f"{STEM}.jsonl", "zst": zst}, 0, notes)
        self.assertEqual(kind, "zst")
        self.assertEqual(off, n)
        self.assertIn(("process_restart", t0 + 5), r2.sc.rows)
        # an offset past the end of the stream: the hour is read again from its start
        r3 = R.Relay(self.tape, ARM, self.tmp / "state3.json")
        notes = []
        off, n, kind = r3._read_stem({"zst": zst}, 10**9, notes)
        self.assertEqual((off, notes), (n, ["offset_beyond_end"]))
        self.assertIn(("process_restart", t0 + 5), r3.sc.rows)

    def test_a_shrunk_jsonl_is_read_again_from_the_start(self):
        t0 = hour_ms(10)
        self.write(f"{STEM}.jsonl", tline(t0, 1), tline(t0 + 1, 2), tline(t0 + 2, 3))
        r = self.relay()
        self.go(r)
        self.write(f"{STEM}.jsonl", line(type="gap", kind="process_restart", t_recv_ms=t0 + 9))
        info = self.go(r)
        self.assertIn("file_shrunk", info["notes"])
        self.assertIn({"kind": "process_restart", "ms": t0 + 9}, self.published[-1])

    def test_a_catch_up_over_several_hours_publishes_progress(self):
        for h in (10, 11, 12):
            self.write(f"tape-2026-10-26T{h}.jsonl", tline(hour_ms(h) + 1, 1))
        r = self.relay(cold_hours=3)
        info = self.go(r)
        self.assertEqual(info["hours"], 3)
        self.assertEqual(len(self.published), 4)  # one per hour read plus the final publish

    def test_a_pathological_line_without_a_newline_is_refused_not_buffered_forever(self):
        (self.tape / f"{STEM}.jsonl").write_bytes(b"x" * (R.MAX_LINE_BYTES + R.CHUNK + 1))
        with self.assertRaises(R.RelayError):
            self.go(self.relay())


class RunTests(Base):
    def test_serve_runs_one_pass_per_interval_and_sleeps_the_remainder(self):
        clock = [0.0]
        slept: list = []
        outs: list = []

        def step():
            clock[0] += 2.0  # a pass takes 2 s
            return {"pass_s": 2.0}

        def sleep(s):
            slept.append(s)
            clock[0] += s

        rc = R.serve(step, 30.0, max_passes=3, sleep=sleep, monotonic=lambda: clock[0], log=outs.append)
        self.assertEqual(rc, 0)
        self.assertEqual(len(outs), 3)
        self.assertAlmostEqual(sum(slept), 2 * 28.0)  # between passes only, start to start 30 s
        self.assertTrue(all(s <= 1.0 for s in slept))
        self.assertEqual([json.loads(o)["pass"] for o in outs], [1, 2, 3])

    def test_serve_does_not_sleep_after_a_slow_pass_and_flags_it(self):
        clock = [0.0]
        slept: list = []
        outs: list = []

        def step():
            clock[0] += 130.0
            return {"pass_s": 130.0}

        R.serve(step, 30.0, max_passes=2, sleep=slept.append, monotonic=lambda: clock[0], log=outs.append)
        self.assertEqual(slept, [])
        self.assertTrue(json.loads(outs[0])["slow_pass"])

    def test_serve_survives_failed_passes_and_gives_up_after_ten_in_a_row(self):
        errs: list = []
        calls = [0]

        def step():
            calls[0] += 1
            if calls[0] in (2, 3):
                raise OSError("transient")
            if calls[0] > 5:
                raise OSError("persistent")
            return {"pass_s": 0.0}

        rc = R.serve(step, 1.0, sleep=lambda s: None, monotonic=lambda: 0.0, log=lambda s: None, err=errs.append)
        self.assertEqual(rc, R.FATAL_EXIT)
        self.assertEqual(calls[0], 5 + R.MAX_CONSECUTIVE_ERRORS)  # the two transient failures did not count towards the ten
        self.assertEqual(json.loads(errs[0])["error"], "OSError")

    def test_serve_stops_when_asked(self):
        n = [0]

        def step():
            n[0] += 1
            return {"pass_s": 0.0}

        stop = lambda: n[0] >= 2  # noqa: E731
        self.assertEqual(R.serve(step, 5.0, sleep=lambda s: None, monotonic=lambda: 0.0, log=lambda s: None, stop=stop), 0)
        self.assertEqual(n[0], 2)

    def test_interval_validation(self):
        for ok in (None, 1, 30, 119.9, 0.5):
            self.assertIsNone(R.interval_problem(ok), ok)
        for bad in (0, -1, 120, 120.0, 121, 3600, float("nan"), float("inf")):
            self.assertIsNotNone(R.interval_problem(bad), bad)
        out = self.tmp / "out"
        for bad in ("120", "500", "0", "-3", "nan"):
            self.assertEqual(R.main(["--tape-dir", str(self.tape), "--bot-id", ARM, "--out-dir", str(out), "--loop", "--interval-s", bad]), 2, bad)
        self.assertFalse(out.exists())  # refused before anything was created

    def test_main_loop_rewrites_the_stream_every_pass(self):
        self.write(f"{STEM}.jsonl", line(type="gap", kind="process_restart", t_recv_ms=hour_ms(10)))
        out = self.tmp / "out"
        stream = out / "transport" / f"{ARM}.tapegaps.jsonl"
        sleeps: list = []

        def fake_sleep(s):
            sleeps.append(s)
            os.utime(stream, (1, 1))  # a stale heartbeat, to be refreshed by the next pass

        mt = []
        real_serve = R.serve

        def spy_serve(step, *a, **k):
            def step2():
                info = step()
                mt.append(stream.stat().st_mtime)
                return info

            return real_serve(step2, *a, **k)

        with mock.patch.object(R.time, "sleep", fake_sleep), mock.patch.object(R, "serve", spy_serve):
            rc = R.main(["--tape-dir", str(self.tape), "--bot-id", ARM, "--out-dir", str(out), "--loop", "--interval-s", "5", "--max-passes", "3"])
        self.assertEqual(rc, 0)
        self.assertEqual(len(mt), 3)
        self.assertTrue(all(m > 1_000_000 for m in mt), mt)  # each pass wrote the stream again (the utime above had reset it to 1)
        self.assertLessEqual(sum(sleeps), 2 * 5.0 + 1e-6)

    def test_main_writes_the_stream_atomically_and_refuses_bad_ids(self):
        t0 = hour_ms(10)
        self.write(f"{STEM}.jsonl", line(type="gap", kind="process_restart", t_recv_ms=t0))
        out = self.tmp / "out"
        self.assertEqual(R.main(["--tape-dir", str(self.tape), "--bot-id", ARM, "--out-dir", str(out)]), 0)
        f = out / "transport" / f"{ARM}.tapegaps.jsonl"
        self.assertEqual([json.loads(x) for x in f.read_text().splitlines()], [{"kind": "process_restart", "ms": t0}])
        self.assertEqual([p.name for p in (out / "transport").iterdir()], [f.name])  # no tmp left, and no state file in the carried directory
        self.assertEqual(sorted(p.name for p in (out / "state").iterdir()), [f"{ARM}.relay-state.json", f"{ARM}.relay.lock"])
        for bad in ("hv4-../x-F", "hv4-CON-L", "rule1"):
            self.assertEqual(R.main(["--tape-dir", str(self.tape), "--bot-id", bad, "--out-dir", str(out)]), 2, bad)

    def test_main_refuses_unsafe_directories_before_creating_anything(self):
        self.write(f"{STEM}.jsonl", tline(1, 1))
        for out in ("/data/mal/arena/x", "/data/mal/arena", "/data/mal/arena/../arena/x", "//data/mal/arena/x", "/data/mal//arena/y", "/data/mal/./arena/z",
                    str(self.tape / "o"), str(self.tape), str(self.tmp)):
            before = sorted(os.listdir(self.tape))
            self.assertEqual(R.main(["--tape-dir", str(self.tape), "--bot-id", ARM, "--out-dir", out]), 2, out)
            self.assertEqual(sorted(os.listdir(self.tape)), before)
        self.assertEqual(R.main(["--tape-dir", str(self.tmp / "missing"), "--bot-id", ARM, "--out-dir", str(self.tmp / "o2")]), 2)
        self.assertFalse((self.tmp / "o2").exists())
        link = self.tmp / "lnk"
        link.symlink_to(self.tape)  # a link into the tape dir does not get round the overlap check
        self.assertEqual(R.main(["--tape-dir", str(self.tape), "--bot-id", ARM, "--out-dir", str(link / "sub")]), 2)

    def test_a_second_instance_exits_3_and_sigterm_stops_the_first_cleanly(self):
        self.write(f"{STEM}.jsonl", tline(hour_ms(10), 1))
        out = self.tmp / "out"
        cmd = [sys.executable, "-m", "tools.hv4_gap_relay", "--tape-dir", str(self.tape), "--bot-id", ARM, "--out-dir", str(out)]
        env = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(REPO)}
        p = subprocess.Popen(cmd + ["--loop", "--interval-s", "1"], cwd=str(REPO), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            first = p.stdout.readline()
            self.assertEqual(json.loads(first)["pass"], 1)
            q = subprocess.run(cmd, cwd=str(REPO), env=env, capture_output=True, timeout=30)
            self.assertEqual(q.returncode, R.LOCK_HELD_EXIT, q.stderr)
            p.send_signal(signal.SIGTERM)
            self.assertEqual(p.wait(timeout=15), 0)
        finally:
            if p.poll() is None:
                p.kill()
                p.wait()
            p.stdout.close()
            p.stderr.close()


class DistinctReferenceTests(unittest.TestCase):
    def test_a_reread_does_not_move_the_reference(self):
        sc = R.Scanner()
        for ms in (5000, 6000, 7000, 8000):
            sc.feed_line(line(type="trade", t_recv_ms=ms, slot=ms // 10).encode())
        self.assertEqual(sc.top_t, [5000, 6000, 7000, 8000])
        for ms in (7000, 8000):  # the same rows read again (a shrunk file / offset past the end)
            sc.feed_line(line(type="trade", t_recv_ms=ms, slot=ms // 10).encode())
        self.assertEqual(sc.top_t, [5000, 6000, 7000, 8000])
        self.assertEqual(sc.top_slot, [500, 600, 700, 800])
        sc.feed_line(line(type="trade", t_recv_ms=9000, slot=900).encode())
        self.assertEqual(sc.top_t, [6000, 7000, 8000, 9000])


class BridgeTests(Base):
    def latch_on(self, kind):
        e = Hv4Env(str(self.tmp / "env"))
        b = e.bridge()
        e.start()
        d = Path(e.raw["root"]) / "transport"
        d.mkdir(exist_ok=True)
        (d / f"{e.bot}.tapegaps.jsonl").write_text(json.dumps({"kind": kind, "ms": e.clock.t}) + "\n")
        b.tick()
        self.assertIn(c0.FEED_BREAK_LATCH, b.state.latches, kind)
        self.assertEqual(b.state.gap_noted_ms, e.clock.t)

    @mock.patch.object(c0, "FEED_GAP_MANUAL_REVIEW", True, create=True)  # s5 item 2 as built (#1090 ships True); Am.21 item 3 is tested in tools/test_hv4_c0_gap_hold.py
    def test_bridge_latches_feed_break_on_the_relay_stream(self):
        self.latch_on("data_loss")

    @mock.patch.object(c0, "FEED_GAP_MANUAL_REVIEW", True, create=True)
    def test_bridge_latches_feed_break_on_every_relay_kind(self):
        for i, kind in enumerate(R.RELAY_KINDS):
            with self.subTest(kind=kind):
                self.tmp = Path(tempfile.mkdtemp(dir=self._d.name))
                self.latch_on(kind)


if __name__ == "__main__":
    unittest.main()
