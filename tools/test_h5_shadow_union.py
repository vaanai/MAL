"""Report-only tip union in tools/h5_shadow.py (trade-rate phase 0): UnionChain arithmetic, the engine fields, decision equivalence, TipTail IO.

Synthetic data only (the Tape helper of test_h5_shadow). No tape, no network."""
from __future__ import annotations

import asyncio
import collections
import json
import os
import random
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from tools import h5_shadow as h5
from tools.test_h5_shadow import MINT, POOL, SOL, Tape, announce, boost_buys, drain, make_engine, types

NEW_KEYS = {h5.UNION_FIELD, "base_union", "base_unmatched_ws_n", "base_unmatched_ws", "base_unmatched_union_n", "base_unmatched_union"}


class FakeTip:
    def __init__(self, status="ok", hw_slot=None):
        self.st, self.hw_slot = status, hw_slot

    def status(self, feed_hw):
        if self.st == "raise":
            raise RuntimeError("boom")
        return self.st

    def stats(self):
        return {"hw_slot": self.hw_slot}


def brute_settled(members: dict, hw: int | None) -> int:
    posts = collections.Counter(v[1] for v in members.values())
    pres = collections.Counter(v[0] for v in members.values() if hw is None or v[2] <= hw - h5.SETTLE_SLOTS)
    return max(0, sum(max(0, n - posts[b]) for b, n in pres.items()) - 1)


def chain(n, seed=0, b0=10**12):
    """n prints of a complete base chain: (key, pre, post, slot)."""
    rng = random.Random(seed)
    out, b, slot = [], b0, 100
    for i in range(n):
        d = rng.randint(1, 10**6) * (1 if rng.random() < 0.5 else -1)
        slot += rng.randint(0, 2)
        out.append(((f"s{i}", 0), b, b + d, slot))
        b += d
    return out


class UnionChainTests(unittest.TestCase):
    def test_complete_chain_any_order_is_zero(self):
        c = chain(50)
        u = h5.UnionChain()
        for it in random.Random(3).sample(c, len(c)):
            u.add(*it, h5.UNION_WS)
        self.assertEqual(u.settled(None), 0)
        self.assertEqual(u.settled(c[-1][3] + 5), 0)

    def test_miss_on_feed_is_filled_by_tip(self):
        c = chain(30)
        u = h5.UnionChain()
        for i, it in enumerate(c):
            if i != 12:
                u.add(*it, h5.UNION_WS)
        hw = c[-1][3] + 5
        self.assertEqual(u.settled(hw), 1)  # the feed view alone: one break
        for it in c:
            u.add(*it, h5.UNION_TIP)
        self.assertEqual(u.settled(hw), 0)
        self.assertEqual(u.n, [0, 0, 1, 29])

    def test_phantom_on_feed_still_breaks_the_union(self):
        c = chain(20)
        u = h5.UnionChain()
        for it in c:
            u.add(*it, h5.UNION_WS)
            u.add(*it, h5.UNION_TIP)
        u.add(("phantom", 0), c[5][1], c[5][1] + 7, c[5][3], h5.UNION_WS)  # a second print off the same pre base
        self.assertEqual(u.settled(None), 1)
        self.assertEqual(u.n[h5.UNION_WS], 1)
        n, rows = u.unmatched()
        self.assertEqual(n, 3)  # the head, the phantom and the real print sharing its pre base
        self.assertIn(["phantom", 0, c[5][3], "ws"], rows)

    def test_tip_version_wins_on_disagreement(self):
        u = h5.UnionChain()
        u.add(("a", 0), 100, 90, 10, h5.UNION_WS)
        u.add(("a", 0), 100, 80, 11, h5.UNION_TIP)
        self.assertEqual(u.both_diff, 1)
        self.assertEqual(u.m[("a", 0)][:3], [100, 80, 11])
        self.assertEqual((u.pre[100], u.post[90], u.post[80]), (1, 0, 1))
        u.add(("a", 0), 100, 80, 11, h5.UNION_TIP)
        self.assertEqual(u.dup, 1)

    def test_incremental_settled_matches_brute_force(self):
        rng = random.Random(7)
        for trial in range(200):
            c = chain(rng.randint(1, 60), seed=trial)
            u, ref = h5.UnionChain(), {}
            for it in c:
                r = rng.random()
                srcs = [h5.UNION_WS, h5.UNION_TIP] if r < 0.7 else ([h5.UNION_WS] if r < 0.85 else [h5.UNION_TIP] if r < 0.95 else [])
                for s in rng.sample(srcs, len(srcs)):
                    key, bpre, bpost, slot = it
                    if s == h5.UNION_WS and rng.random() < 0.05:  # a fork version on the feed
                        bpost, slot = bpost + 1, slot + 1
                    u.add(key, bpre, bpost, slot, s)
                    if key not in ref or s == h5.UNION_TIP:
                        ref[key] = (bpre, bpost, slot)
            if rng.random() < 0.2:
                u.add(("x", 1), c[0][1], c[0][2] + 3, c[-1][3], h5.UNION_WS)
                ref[("x", 1)] = (c[0][1], c[0][2] + 3, c[-1][3])
            for hw in (None, c[-1][3], c[-1][3] + 1, c[-1][3] + 2, c[-1][3] + 3, c[0][3] + 1):
                self.assertEqual(u.settled(hw), brute_settled(ref, hw), (trial, hw))
            self.assertEqual(u.excess, sum(max(0, n - u.post[b]) for b, n in u.pre.items()))

    def test_far_slot_range_falls_back_to_full_count(self):
        u = h5.UnionChain()
        u.add(("a", 0), 100, 90, 10, h5.UNION_WS)
        u.add(("b", 0), 90, 80, 10 + h5.UNION_SETTLE_SCAN_MAX + 50, h5.UNION_TIP)
        ref = {("a", 0): (100, 90, 10), ("b", 0): (90, 80, 10 + h5.UNION_SETTLE_SCAN_MAX + 50)}
        self.assertEqual(u.settled(12), brute_settled(ref, 12))


def pool_rows(drain_slot=1200):
    t = Tape()
    t.row(1000, "buy", "SNIPER", SOL // 5)
    for i in range(1, 8):
        t.row(1000 + 10 * i, "buy", f"T{i}", SOL // 20)  # buys only: any sell here would already be under Q* and trigger
    boost_buys(t, 1000)
    t.rows.sort(key=lambda r: r["slot"])
    _fix_chain(t)
    drain(t, drain_slot, 35.0)
    return t


def _fix_chain(t):
    """Re-thread base/quote pre-states after the sort, so the rows form one complete chain."""
    q, b = t.rows[0]["quote_reserve"], t.rows[0]["base_reserve"]
    for i, r in enumerate(t.rows):
        r["quote_reserve"], r["base_reserve"], r["event_index"] = q, b, 0
        buy = r["side"] == "buy"
        q += r["sol_lamports"] if buy else -r["sol_lamports"]
        b += -r["token_raw"] if buy else r["token_raw"]
    t.q, t.b = q, b


def strip_new(recs):
    return [{k: v for k, v in r.items() if k not in NEW_KEYS} for r in recs if r["type"] != "tip_union_start"]


class EngineUnionTests(unittest.TestCase):
    def run_pool(self, tip=None, skip=(), tip_rows="all", close=True, **kw):
        eng, out = make_engine(**kw)
        if tip is not None:
            eng.tip = tip
        announce(eng)
        t = pool_rows()
        rows = t.rows
        for i, r in enumerate(rows):
            if i not in skip:
                eng.on_trade(r)
            if tip is not None and tip_rows == "all" and i + 1 < len(rows):
                eng.on_tip_row(rows[i])  # the tip delivers each print right after the feed (before the trigger print)
        if close:
            eng.close_all("test")
        return eng, out, t

    def test_off_by_default_no_new_keys(self):
        eng, out, _ = self.run_pool()
        self.assertTrue(types(out, "trigger"))
        for r in out:
            self.assertFalse(NEW_KEYS & set(r), r["type"])

    def test_decision_records_identical_with_union_on(self):
        _, base, _ = self.run_pool()
        for tip in (FakeTip("ok", 2000), FakeTip("stale"), FakeTip("raise")):
            _, on, _ = self.run_pool(tip=tip)
            self.assertEqual(strip_new(on), strip_new(base))
            trig = [r for r in on if r["type"] == "trigger"]
            self.assertTrue(trig and all(h5.UNION_FIELD in r for r in trig))
        _, skipped_base, _ = self.run_pool(skip=(3,))
        _, skipped_on, _ = self.run_pool(tip=FakeTip("ok", 2000), skip=(3,))
        self.assertEqual(strip_new(skipped_on), strip_new(skipped_base))

    def test_feed_miss_filled_by_tip_on_trigger_and_close(self):
        eng, out, t = self.run_pool(tip=FakeTip("ok", 2000), skip=(3,))
        (trig,) = [r for r in types(out, "trigger") if r["variant"] == "pv"]
        self.assertEqual(trig["base_breaks_unresolved_settled"], 1)
        self.assertEqual(trig[h5.UNION_FIELD], 0)
        bu = trig["base_union"]
        self.assertEqual((bu["status"], bu["ws_only"], bu["tip_only"]), ("ok", 1, 1))  # the trigger print itself is not on the tip yet
        (pool,) = types(out, "pool")
        self.assertEqual(pool[h5.UNION_FIELD], 0)
        self.assertEqual(pool["base_unmatched_ws_n"], 2)  # the head and the print after the missed one
        missed_next = t.rows[4]
        row = [r for r in pool["base_unmatched_ws"] if r[0] == missed_next["signature"]]
        self.assertEqual(row, [[missed_next["signature"], 0, missed_next["slot"], True, True]])  # on the tip; a tip-only row is its predecessor
        self.assertEqual(pool["base_unmatched_union_n"], 1)  # the head only
        self.assertEqual(pool["base_union"]["ws_only_sig_on_tip"], 0)
        self.assertEqual(pool["base_unmatched_union"][0][0], t.rows[0]["signature"])

    def test_stale_or_broken_tip_is_null_and_trigger_still_written(self):
        for st, want in (("stale", "stale"), ("unreadable", "unreadable"), ("starting", "starting"), ("raise", "error")):
            _, out, _ = self.run_pool(tip=FakeTip(st), skip=(3,))
            (trig,) = [r for r in types(out, "trigger") if r["variant"] == "pv"]
            self.assertIsNone(trig[h5.UNION_FIELD])
            self.assertEqual(trig["base_union"]["status"], want)
            self.assertEqual(trig["base_breaks_unresolved_settled"], 1)

    def test_union_add_error_marks_pool_broken_not_the_feed(self):
        eng, out = make_engine()
        eng.tip = FakeTip("ok")
        announce(eng)
        t = pool_rows()
        eng.on_trade(t.rows[0])
        eng.pools[POOL].union.m = None  # any later add raises inside UnionChain
        for r in t.rows[1:]:
            eng.on_trade(r)
        (trig,) = [r for r in types(out, "trigger") if r["variant"] == "pv"]
        self.assertEqual(trig["base_union"]["status"], "error")
        self.assertIsNone(trig[h5.UNION_FIELD])
        self.assertGreaterEqual(eng.counters["union_errors"], 1)

    def test_bad_tip_row_is_counted(self):
        eng, out = make_engine()
        eng.tip = FakeTip("ok")
        eng.on_tip_row({"venue": "pumpswap", "pool": POOL, "slot": "x"})
        self.assertEqual(eng.counters["tip_bad_rows"], 1)

    def test_tip_rows_before_tracking_are_buffered_then_merged(self):
        eng, out = make_engine()
        eng.tip = FakeTip("ok")
        announce(eng)
        t = pool_rows()
        eng.on_tip_row(t.rows[0])
        self.assertEqual(len(eng.tip_pending[POOL]), 1)
        eng.on_trade(t.rows[0])
        self.assertEqual(eng.tip_pending, {})
        self.assertEqual(eng.pools[POOL].union.n[h5.UNION_BOTH], 1)
        eng.on_tip_row(dict(t.rows[1], pool="Unannounced"))
        self.assertNotIn("Unannounced", eng.tip_pending)

    def test_sealed_pool_record_has_no_union_keys(self):
        eng, out, _ = self.run_pool(tip=FakeTip("ok"), seal_start_ms=0, suppress_outcome=lambda m: True)
        (pool,) = types(out, "pool")
        self.assertTrue(pool["sealed"])
        self.assertFalse(NEW_KEYS & set(pool))
        self.assertEqual(types(out, "trigger"), [])

    def test_replay_tip_equivalence_and_full_overlap(self):
        t = pool_rows()
        meta = {POOL: {"s0": 1000, "v": t.v, "mint": MINT}}
        recs = {}
        for lag in (None, 0, 3):
            eng, out = make_engine()
            if lag is not None:
                eng.tip = h5.ReplayTip(lag)
            h5.replay([dict(r) for r in t.rows], meta, eng)
            recs[lag] = out
        self.assertEqual(strip_new(recs[0]), strip_new(recs[None]))
        self.assertEqual(strip_new(recs[3]), strip_new(recs[None]))
        (pool,) = types(recs[0], "pool")
        self.assertEqual(pool["base_union"]["both"], len(t.rows))
        self.assertEqual(pool[h5.UNION_FIELD], pool["base_breaks_unresolved_settled"])


def line(pool, slot, sig="s", compact=False, **kw):
    row = {"v": 1, "type": "trade", "venue": "pumpswap", "pool": pool, "side": "buy", "token_raw": 5, "base_reserve": 1000, "slot": slot,
           "signature": sig, "event_index": 0, "pool_quote_amount": 7, **kw}
    return (json.dumps(row, separators=(",", ":")) if compact else json.dumps(row)) + "\n"


class TipTailTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.dir = Path(self.td.name)
        self.now = [int(datetime(2027, 1, 1, 12, 30, tzinfo=timezone.utc).timestamp() * 1000)]
        self.rows = []
        self.tail = h5.TipTail(self.dir, self.rows.append, lambda p: p == "WANT", wall=lambda: self.now[0], chunk=4096)

    def tearDown(self):
        self.tail.close()
        self.td.cleanup()

    def path(self, hour="2027-01-01T12"):
        return self.dir / f"trades-{hour}.jsonl"

    def test_byte_helpers(self):
        self.assertEqual(h5._tip_pool(line("WANT", 5).encode()), "WANT")
        self.assertEqual(h5._tip_pool(line("WANT", 5, compact=True).encode()), "WANT")
        self.assertIsNone(h5._tip_pool(line(None, 5).encode()))
        self.assertEqual(h5._tip_slot(line("X", 12345).encode()), 12345)
        self.assertEqual(h5._tip_slot(line("X", 7, compact=True).encode()), 7)

    def test_existing_file_read_from_end_new_lines_filtered_partial_kept(self):
        self.path().write_text(line("WANT", 1, "old"))
        self.assertFalse(self.tail.poll())
        self.assertEqual(self.rows, [])
        self.assertEqual(self.tail.status(10), "starting")
        with open(self.path(), "a") as f:
            f.write(line("WANT", 10, "a") + line("OTHER", 11, "b") + line("WANT", 12, "c")[:20])
        self.tail.poll()
        self.assertEqual([r["signature"] for r in self.rows], ["a"])
        self.assertEqual(self.tail.hw_slot, 11)
        with open(self.path(), "a") as f:
            f.write(line("WANT", 12, "c")[20:])
        self.tail.poll()
        self.assertEqual([r["signature"] for r in self.rows], ["a", "c"])
        self.assertEqual(self.tail.status(12), "ok")
        self.assertEqual(self.tail.status(12 + h5.TIP_STALE_SLOTS + 1), "stale")
        self.now[0] += h5.TIP_STALE_MS + 1
        self.assertEqual(self.tail.status(12), "stale")

    def test_new_hour_file_read_from_start_and_old_hours_closed(self):
        self.tail.poll()
        self.path("2027-01-01T13").write_text(line("WANT", 20, "n"))
        self.now[0] += 3_600_000
        self.tail.poll()
        self.assertEqual([r["signature"] for r in self.rows], ["n"])
        self.now[0] += 2 * 3_600_000
        self.tail.poll()
        self.assertEqual(self.tail.files, {})

    def test_missing_dir_is_unreadable_and_engine_field_null(self):
        tail = h5.TipTail(self.dir / "nope", lambda r: None, lambda p: True, wall=lambda: self.now[0])
        self.assertFalse(tail.poll())
        self.assertEqual(tail.status(1), "unreadable")
        eng, out = make_engine()
        eng.tip = tail
        announce(eng)
        for r in pool_rows().rows:
            eng.on_trade(r)
        (trig,) = [r for r in types(out, "trigger") if r["variant"] == "pv"]
        self.assertIsNone(trig[h5.UNION_FIELD])
        self.assertEqual(trig["base_union"]["status"], "unreadable")
        st = h5.status_snapshot(eng, None, None)["tip_union"]
        self.assertEqual((st["status"], st["err"]), ("unreadable", "no_dir"))

    def test_chunked_backlog_and_follow_task(self):
        self.tail.poll()
        with open(self.path(), "a") as f:
            for i in range(200):
                f.write(line("WANT", 100 + i, f"k{i}"))
        self.assertTrue(self.tail.poll())  # 4 KB chunk: more is waiting

        async def go():
            stop = asyncio.Event()
            task = asyncio.create_task(h5.tip_follow(self.tail, stop, poll_s=0.01))
            for _ in range(200):
                await asyncio.sleep(0.005)
                if len(self.rows) == 200:
                    break
            stop.set()
            await task

        asyncio.run(go())
        self.assertEqual(len(self.rows), 200)
        self.assertEqual(self.tail.hw_slot, 299)
        self.assertEqual(self.tail.files, {})  # closed when the task stopped

    def test_bad_json_counted(self):
        self.tail.poll()
        with open(self.path(), "a") as f:
            f.write('{"pool": "WANT", "slot": 3, broken\n')
        self.tail.poll()
        self.assertEqual(self.tail.c["parse_errors"], 1)
        self.assertEqual(self.rows, [])


class CliTests(unittest.TestCase):
    def test_flags_default_off(self):
        a = h5.build_parser().parse_args([])
        self.assertIsNone(a.tip_dir)
        self.assertIsNone(a.replay_tip_lag_slots)

    def test_wrapper_passes_tip_dir_by_default_off_disables_bad_refused(self):
        import shutil
        import subprocess

        d = tempfile.mkdtemp(dir="/tmp", prefix="h5-tip-")
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        fake = Path(d) / "fakepy"
        fake.write_text('#!/bin/sh\nif [ "$1" = "-c" ]; then exit 0; fi\nprintf "ARGV:%s\\n" "$*"\n')
        fake.chmod(0o755)
        script = Path(__file__).resolve().parent.parent / "scripts" / "research" / "h5-shadow.sh"
        for shell in ("sh", "bash"):
            base = {"PATH": os.environ["PATH"], "HOME": d, "H5_OUT_DIR": d + "/out", "H5_PYTHON": str(fake)}
            r = subprocess.run([shell, str(script)], env=base, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, (shell, r.stderr))
            self.assertIn(f"--tip-dir {h5.TIP_DIR_DEFAULT}", r.stdout.split("ARGV:")[1])
            r = subprocess.run([shell, str(script)], env={**base, "H5_TIP_DIR": "off"}, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, (shell, r.stderr))
            self.assertNotIn("--tip-dir", r.stdout.split("ARGV:")[1])
            r = subprocess.run([shell, str(script)], env={**base, "H5_TIP_DIR": "/tmp/tipx"}, capture_output=True, text=True)
            self.assertIn("--tip-dir /tmp/tipx", r.stdout.split("ARGV:")[1])
            for bad in ("rel/dir", "/var/lib/mal/../x"):
                r = subprocess.run([shell, str(script)], env={**base, "H5_TIP_DIR": bad}, capture_output=True, text=True)
                self.assertEqual(r.returncode, 2, (shell, bad))


if __name__ == "__main__":
    unittest.main()
