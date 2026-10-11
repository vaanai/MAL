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
from unittest import mock
from datetime import datetime, timezone
from pathlib import Path

from tools import h5_shadow as h5
from tools.test_h5_shadow import MINT, POOL, SOL, Tape, announce, boost_buys, drain, make_engine, types  # noqa: F401

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

    def test_wrapper_union_off_by_default_explicit_dir_with_readable_precheck(self):
        import shutil
        import subprocess

        d = tempfile.mkdtemp(dir="/tmp", prefix="h5-tip-")
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        fake = Path(d) / "fakepy"
        fake.write_text('#!/bin/sh\nif [ "$1" = "-c" ]; then exit 0; fi\nprintf "ARGV:%s\\n" "$*"\nprintf "SHA:%s\\n" "$H5_CODE_SHA"\n')
        fake.chmod(0o755)
        tip = Path(d) / "tip"
        tip.mkdir()
        empty = Path(d) / "tip-empty"
        empty.mkdir()
        (tip / "trades-2027-01-01T11.jsonl").write_text("")
        newest = tip / "trades-2027-01-01T12.jsonl"
        newest.write_text("")
        script = Path(__file__).resolve().parent.parent / "scripts" / "research" / "h5-shadow.sh"
        head = subprocess.run(["git", "-C", str(script.parent), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()

        def argv(r):
            return r.stdout.split("ARGV:")[1].split("\n")[0]

        for shell in ("sh", "bash"):
            base = {"PATH": os.environ["PATH"], "HOME": d, "H5_OUT_DIR": d + "/out", "H5_PYTHON": str(fake)}
            for env in ({}, {"H5_TIP_DIR": "off"}, {"H5_TIP_DIR": ""}):  # unset, "off" and empty: OFF
                r = subprocess.run([shell, str(script)], env={**base, **env}, capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, (shell, env, r.stderr))
                self.assertNotIn("--tip-dir", argv(r), (shell, env))
                self.assertIn("tip_union=off", r.stdout)
            r = subprocess.run([shell, str(script)], env={**base, "H5_TIP_DIR": str(tip)}, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, (shell, r.stderr))
            self.assertIn(f"--tip-dir {tip}", argv(r))
            self.assertIn(f"tip_union=on:{tip}", r.stdout)
            self.assertEqual(r.stdout.split("SHA:")[1].split("\n")[0], head)  # the start record's code_sha comes from here
            self.assertRegex(head, "^[0-9a-f]{40}$")
            r = subprocess.run([shell, str(script)], env={**base, "H5_TIP_DIR": str(empty)}, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, (shell, r.stderr))  # fail-soft: no trades file -> the union stays OFF, logged
            self.assertNotIn("--tip-dir", argv(r))
            self.assertIn("starting with the union OFF", r.stderr)
            if os.geteuid() != 0:  # root passes test -r on a 000 file
                newest.chmod(0)
                try:
                    r = subprocess.run([shell, str(script)], env={**base, "H5_TIP_DIR": str(tip)}, capture_output=True, text=True)
                finally:
                    newest.chmod(0o644)
                self.assertEqual(r.returncode, 0, (shell, r.stderr))
                self.assertNotIn("--tip-dir", argv(r))
                self.assertIn(f"newest trades-*.jsonl there ({newest}) is not readable", r.stderr)
            for bad in ("rel/dir", "/var/lib/mal/../x"):
                r = subprocess.run([shell, str(script)], env={**base, "H5_TIP_DIR": bad}, capture_output=True, text=True)
                self.assertEqual(r.returncode, 2, (shell, bad))


class H5SealedUnionTests(unittest.TestCase):
    """EXP-024 Look-2-sealed pools (h5_look2_sealed): the pool record stays exactly as without the union; the trigger keeps the decision-time fields."""

    def run_pool(self, tip, skip=(3,)):
        eng, out = make_engine(h5_look2_start_ms=0)  # synthetic 2027 times are past H5_LOOK2_END_MS: sealed whatever the flag says
        if tip is not None:
            eng.tip = tip
        announce(eng)
        t = pool_rows()
        for i, r in enumerate(t.rows):
            if i not in skip:
                eng.on_trade(r)
            if tip is not None and i + 1 < len(t.rows):
                eng.on_tip_row(t.rows[i])
        eng.close_all("test")
        return out

    def test_sealed_pool_record_identical_to_union_off(self):
        off = self.run_pool(None)
        (pool_off,) = types(off, "pool")
        self.assertTrue(pool_off["h5_look2_sealed"])
        for tip in (FakeTip("ok", 2000), FakeTip("stale"), FakeTip("raise")):
            on = self.run_pool(tip)
            (pool_on,) = types(on, "pool")
            self.assertEqual(json.dumps(pool_on, sort_keys=True), json.dumps(pool_off, sort_keys=True))  # exact: not one key added
            self.assertFalse(NEW_KEYS & set(pool_on))
            self.assertEqual(strip_new(on), strip_new(off))

    def test_sealed_trigger_keeps_decision_time_union_only(self):
        on = self.run_pool(FakeTip("ok", 2000))
        (trig,) = [r for r in types(on, "trigger") if r["variant"] == "pv"]
        self.assertTrue(trig["h5_look2_sealed"])
        self.assertEqual(trig[h5.UNION_FIELD], 0)
        self.assertNotIn("base_unmatched_ws", trig)
        self.assertNotIn("base_unmatched_union", trig)

    def test_unsealed_pool_still_carries_the_union(self):
        eng, out = make_engine()
        eng.tip = FakeTip("ok", 2000)
        announce(eng)
        for r in pool_rows().rows:
            eng.on_trade(r)
        eng.close_all("test")
        (pool,) = types(out, "pool")
        self.assertNotIn("h5_look2_sealed", pool)
        self.assertIn("base_unmatched_ws", pool)


class UnionMemoryTests(unittest.TestCase):
    def test_hb_mem_self_report(self):
        eng, out = make_engine()
        self.assertNotIn("tip_union", h5.status_snapshot(eng, None, None))  # off: hb / status unchanged
        eng.tip = FakeTip("ok", 2000)
        announce(eng)
        t = pool_rows()
        for r in t.rows[:10]:
            eng.on_trade(r)
            eng.on_tip_row(r)
        mem = h5.status_snapshot(eng, None, None)["tip_union"]["mem"]
        u = eng.pools[POOL].union
        self.assertEqual((mem["union_pools"], mem["union_members"]), (1, len(u.m)))
        self.assertEqual(mem["union_slots"], len(u.by_slot))
        self.assertEqual(mem["union_counter_keys"], len(u.pre) + len(u.post))
        self.assertEqual(mem["est_mb"], round(len(u.m) * h5.UNION_MEMBER_BYTES_EST / 1e6, 1))
        if Path("/proc/self/status").exists():
            self.assertGreater(mem["rss_kb"], 0)
            self.assertGreaterEqual(mem["hwm_kb"], mem["rss_kb"])
        u.broken = True
        self.assertEqual(h5.status_snapshot(eng, None, None)["tip_union"]["mem"]["union_broken_pools"], 1)

    def test_proc_mem_missing_file_is_null(self):
        self.assertEqual(h5._proc_mem("/nonexistent/status"), {"rss_kb": None, "rss_anon_kb": None, "hwm_kb": None})

    def test_union_key_only_built_when_on(self):
        eng, _ = make_engine()
        announce(eng)
        t = pool_rows()
        eng.on_trade(t.rows[0])
        self.assertIsNone(eng.pools[POOL].prints[0].ukey)
        eng2, _ = make_engine()
        eng2.tip = FakeTip("ok")
        announce(eng2)
        eng2.on_trade(t.rows[0])
        self.assertEqual(eng2.pools[POOL].prints[0].ukey, (t.rows[0]["signature"], 0))


class TipTailResilienceTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.dir = Path(self.td.name)
        self.now = [int(datetime(2027, 1, 1, 12, 30, tzinfo=timezone.utc).timestamp() * 1000)]
        self.rows = []
        self.tail = h5.TipTail(self.dir, self.rows.append, lambda p: p == "WANT", wall=lambda: self.now[0], chunk=4096)
        self.path = self.dir / "trades-2027-01-01T12.jsonl"

    def tearDown(self):
        self.tail.close()
        self.td.cleanup()

    def test_permission_error_on_dir_is_unreadable_and_logged_once_per_window(self):
        clock = [1000.0]
        logs = []
        with mock.patch.object(Path, "is_dir", side_effect=PermissionError(13, "Permission denied")):
            self.assertFalse(self.tail.poll())  # no raise
            self.assertEqual(self.tail.status(1), "unreadable")
            self.assertEqual(self.tail.err, "PermissionError")

            seen = {}

            async def go():
                stop = asyncio.Event()
                task = asyncio.create_task(h5.tip_follow(self.tail, stop, poll_s=0.005, on_error=lambda e, ctx: logs.append((e, ctx)),
                                                         log_every_s=600.0, clock=lambda: clock[0]))
                await asyncio.sleep(0.15)
                seen["first"] = (len(logs), self.tail.c["errors_log_suppressed"])
                clock[0] += 601.0
                await asyncio.sleep(0.05)
                stop.set()
                await task

            asyncio.run(go())
            self.assertEqual(seen["first"][0], 1)  # dozens of polls, one error log line
            self.assertGreater(seen["first"][1], 3)
            self.assertIsInstance(logs[0][0], PermissionError)
            self.assertEqual(logs[0][1], {"where": "tip_follow", "suppressed_since_last": 0})
            self.assertGreater(self.tail.c["dir_errors"], 3)
            self.assertEqual(len(logs), 2)  # one more after the window, carrying the count suppressed since
            self.assertEqual(logs[1][1]["suppressed_since_last"], seen["first"][1])
        eng, out = make_engine()
        eng.tip = self.tail
        announce(eng)
        with mock.patch.object(Path, "is_dir", side_effect=PermissionError(13, "Permission denied")):
            self.tail.poll()
            for r in pool_rows().rows:
                eng.on_trade(r)
        (trig,) = [r for r in types(out, "trigger") if r["variant"] == "pv"]
        self.assertIsNone(trig[h5.UNION_FIELD])
        self.assertEqual(trig["base_union"]["status"], "unreadable")

    @unittest.skipIf(os.geteuid() == 0, "root traverses a 000 dir")
    def test_real_untraversable_parent(self):
        parent = self.dir / "locked"
        (parent / "tip").mkdir(parents=True)
        tail = h5.TipTail(parent / "tip", lambda r: None, lambda p: True, wall=lambda: self.now[0])
        logs = []

        async def go():
            stop = asyncio.Event()
            task = asyncio.create_task(h5.tip_follow(tail, stop, poll_s=0.005, on_error=lambda e, ctx: logs.append(e)))
            await asyncio.sleep(0.1)
            stop.set()
            await task

        parent.chmod(0)
        try:
            asyncio.run(go())
        finally:
            parent.chmod(0o755)
        self.assertEqual((tail.status(1), tail.err), ("unreadable", "PermissionError"))
        self.assertEqual(len(logs), 1)
        self.assertEqual(tail.c["poll_errors"], 0)  # absorbed in poll: no traceback per poll

    def test_failing_error_log_does_not_stop_the_tail(self):
        def bad_log(e, ctx):
            raise OSError("disk full")

        async def go():
            stop = asyncio.Event()
            with mock.patch.object(Path, "is_dir", side_effect=PermissionError(13, "x")):
                task = asyncio.create_task(h5.tip_follow(self.tail, stop, poll_s=0.005, on_error=bad_log))
                await asyncio.sleep(0.03)
                stop.set()
                await task

        asyncio.run(go())
        self.assertEqual(self.tail.c["errors_logged"], 1)

    def test_truncation_reopens_at_the_cut_and_drops_the_partial(self):
        self.tail.poll()  # start: the file does not exist yet, so it is read from its start once it appears
        a, b = line("WANT", 10, "a"), line("WANT", 11, "b")
        self.path.write_text(a + b + line("WANT", 12, "c")[:15])  # a crashed slot write: "c" is partial
        self.tail.poll()
        self.assertEqual([r["signature"] for r in self.rows], ["a", "b"])
        with open(self.path, "r+b") as fh:  # fast_tip_follower's recovery: cut back to the pre-write size of slot 11
            fh.truncate(len(a))
        self.tail.poll()
        self.assertEqual((self.tail.c["resets"], self.tail.c["resets_truncated"]), (1, 1))
        self.assertEqual(self.tail.stats()["partial_bytes"], 0)
        with open(self.path, "a") as f:  # slot 11 rewritten, then slot 12
            f.write(b + line("WANT", 12, "c"))
        self.tail.poll()
        self.assertEqual([r["signature"] for r in self.rows], ["a", "b", "b", "c"])  # "b" twice: the union counts it as dup
        self.assertEqual(self.tail.c["parse_errors"], 0)
        self.assertIsNone(self.tail.err)
        eng, _ = make_engine()
        eng.tip = FakeTip("ok")
        announce(eng)
        t = pool_rows()
        eng.on_trade(t.rows[0])
        eng.on_tip_row(t.rows[1])
        eng.on_tip_row(t.rows[1])
        u = eng.pools[POOL].union
        self.assertEqual((u.dup, len(u.ahead)), (0, 2))  # above the feed's line (decision time): held, not counted yet
        eng.on_trade(t.rows[1])
        self.assertEqual((u.dup, len(u.ahead), u.n[h5.UNION_BOTH]), (1, 0, 1))

    def test_rotation_reopens_from_the_start(self):
        self.path.write_text(line("WANT", 1, "old"))
        self.tail.poll()  # present at start: read from its end
        self.assertEqual(self.rows, [])
        tmp = self.dir / "next.tmp"
        tmp.write_text(line("WANT", 20, "n1") + line("WANT", 21, "n2"))
        os.replace(tmp, self.path)
        self.tail.poll()  # EOF on the old inode: the path has a new one
        self.assertEqual((self.tail.c["resets"], self.tail.c["resets_rotated"]), (1, 1))
        self.tail.poll()
        self.assertEqual([r["signature"] for r in self.rows], ["n1", "n2"])

    def test_removed_file_kept_until_its_hour_leaves(self):
        self.tail.poll()
        self.path.write_text(line("WANT", 5, "x"))
        self.tail.poll()
        self.path.unlink()
        self.tail.poll()
        self.assertEqual(self.tail.c["resets"], 0)
        self.assertIsNone(self.tail.err)


def lead_rows(pool=POOL, slots=(1205, 1206, 1207), pre0=None, post0=None):
    """Tip-only rows (keys the feed never has) at the given slots, a little chain of their own; the first one's post base is post0 if given."""
    out, b = [], (pre0 if pre0 is not None else 7 * 10**13)
    for i, sl in enumerate(slots):
        nb = post0 if (i == 0 and post0 is not None) else b - 1000
        out.append({"venue": "pumpswap", "pool": pool, "slot": sl, "side": "buy", "token_raw": b - nb, "base_reserve": b, "signature": f"lead{sl}",
                    "event_index": 0})
        b = nb
    return out


UNION_COUNT_KEYS = ("ws_only", "tip_only", "both", "both_diff", "dup", "tip_first_slot")


class DecisionTimeUnionTests(unittest.TestCase):
    """Follow-up of #596 (reviewer fix 1, QP #600, DEC-024 phase-2 note item 5): a trigger record's union counts only tip rows at slot <= the
    trigger print's slot; the pool close record is the eager union of everything read."""

    def feed(self, lead=(), lead_before_trigger=True, skip=(3,), tip_skip=(3,), rows=None, close=True, tip=None):
        eng, out = make_engine()
        eng.tip = tip or FakeTip("ok", 2000)
        announce(eng)
        t = rows or pool_rows(drain_slot=1200)
        n = len(t.rows)
        log = []  # arrival order for the eager reference: (key, pre, post, slot, src)
        for i, r in enumerate(t.rows):
            if i == n - 1 and lead_before_trigger:
                for x in lead:
                    eng.on_tip_row(x)
                    log.append(x)
            if i not in skip:
                eng.on_trade(r)
                log.append(("ws", r))
            if i + 1 < n and i not in tip_skip:
                eng.on_tip_row(r)
                log.append(r)
        if not lead_before_trigger:
            for x in lead:
                eng.on_tip_row(x)
                log.append(x)
        if close:
            eng.close_all("test")
        return eng, out, t, log

    @staticmethod
    def eager(log):
        """The phase-0 (eager) union of the same arrivals, built with UnionChain.add directly."""
        u = h5.UnionChain()
        for x in log:
            if isinstance(x, tuple):
                r = x[1]
                tok = r["token_raw"]
                bpre = r["base_reserve"]
                u.add((r["signature"], r.get("event_index")), bpre, bpre + (-tok if r["side"] == "buy" else tok), r["slot"], h5.UNION_WS)
            else:
                bpre = x["base_reserve"]
                u.add((x["signature"], x.get("event_index")), bpre, bpre + (-x["token_raw"] if x["side"] == "buy" else x["token_raw"]), x["slot"],
                      h5.UNION_TIP)
        return u

    def test_tip_leading_rows_never_reach_the_trigger_record(self):
        _, base, t, _ = self.feed()
        (trig0,) = [r for r in types(base, "trigger") if r["variant"] == "pv"]
        self.assertEqual(trig0["slot"], 1200)
        self.assertEqual(trig0[h5.UNION_FIELD], 1)  # the print at index 3 is on neither source: a real break
        fill = t.rows[4]["base_reserve"]  # a tip-only row whose post base is the unmatched pre: would close the break if it were counted
        lead = lead_rows(post0=fill)
        eng, out, _, log = self.feed(lead=lead)
        (trig,) = [r for r in types(out, "trigger") if r["variant"] == "pv"]
        self.assertEqual(trig[h5.UNION_FIELD], 1)
        self.assertEqual(trig["base_union"], trig0["base_union"])
        self.assertEqual(trig["base_union"]["tip_only"], 0)
        self.assertEqual(trig["base_union"]["tip_first_slot"], 1000)
        self.assertEqual((trig["base_union"]["tip_hw_slot_global"], trig["base_union"]["tip_lag_slots_global"]), (2000, 1200 - 2000))
        self.assertNotIn("tip_hw_slot", trig["base_union"])
        for k in UNION_COUNT_KEYS + ("status",):
            self.assertIn(k, trig["base_union"])
        # the eager (phase-0) union at the trigger would have read the lead rows: what this fix removes
        pre = self.eager(log[:-1])
        self.assertEqual((pre.n[h5.UNION_TIP], pre.tip_first_slot), (3, 1000))
        self.assertEqual(pre.settled(1200), 0)
        # pool close: unchanged, the eager union of every row read
        (pool,) = types(out, "pool")
        ref = self.eager(log)
        bu = pool["base_union"]
        self.assertEqual([bu[k] for k in UNION_COUNT_KEYS], [ref.n[h5.UNION_WS], ref.n[h5.UNION_TIP], ref.n[h5.UNION_BOTH], ref.both_diff, ref.dup,
                                                              ref.tip_first_slot])
        self.assertEqual(bu["tip_only"], 3)
        self.assertEqual(pool[h5.UNION_FIELD], ref.settled(eng.hw_slot))
        self.assertEqual((bu["tip_hw_slot"], bu["tip_lag_slots"]), (2000, 1200 - 2000))
        self.assertEqual(pool["base_unmatched_union_n"], ref.unmatched()[0])
        # every decision field and every non-union key is the same as without the lead rows
        self.assertEqual(strip_new(out), strip_new(base))

    def test_equal_slot_tip_rows_count_and_later_ones_do_not(self):
        lead = lead_rows(slots=(1200, 1201))
        _, out, _, _ = self.feed(lead=lead)
        (trig,) = [r for r in types(out, "trigger") if r["variant"] == "pv"]
        self.assertEqual(trig["base_union"]["tip_only"], 1)  # the row at the trigger slot is admitted by the trigger print itself
        self.assertEqual(trig["base_union"]["status"], "ok")
        (pool,) = types(out, "pool")
        self.assertEqual(pool["base_union"]["tip_only"], 2)

    def test_lead_rows_arriving_after_the_trigger_change_nothing_in_it(self):
        _, a, _, _ = self.feed(lead=lead_rows(), lead_before_trigger=True)
        _, b, _, _ = self.feed(lead=lead_rows(), lead_before_trigger=False)
        ta = [r for r in a if r["type"] == "trigger"]
        tb = [r for r in b if r["type"] == "trigger"]
        self.assertEqual(ta, tb)
        self.assertEqual(types(a, "pool"), types(b, "pool"))

    def test_slot_regress_trigger_is_null_not_look_ahead(self):
        t = Tape()
        t.row(1000, "buy", "SNIPER", SOL // 5)
        for i in range(1, 8):
            t.row(1000 + 10 * i, "buy", f"T{i}", SOL // 20)
        boost_buys(t, 1000)
        t.rows.sort(key=lambda r: r["slot"])
        _fix_chain(t)
        t.row(1203, "buy", "LATE", SOL // 100)  # arrives before the drain print but at a later slot: the drain is a slot regress
        drain(t, 1200, 35.0)
        for lead_slot, want in ((1202, "regress"), (1200, "ok")):
            _, out, _, _ = self.feed(lead=lead_rows(slots=(lead_slot,)), rows=t, skip=(), tip_skip=(len(t.rows) - 2,))
            (trig,) = [r for r in types(out, "trigger") if r["variant"] == "pv"]
            self.assertEqual(trig["slot"], 1200)
            bu = trig["base_union"]
            self.assertEqual(bu["status"], want, lead_slot)
            if want == "regress":
                self.assertIsNone(trig[h5.UNION_FIELD])
                self.assertEqual([bu[k] for k in UNION_COUNT_KEYS], [None] * len(UNION_COUNT_KEYS))
            else:
                self.assertEqual(bu["tip_only"], 1)

    def test_far_lead_takes_the_incremental_path_at_the_trigger(self):
        far = 1200 + h5.UNION_SETTLE_SCAN_MAX + 100
        eng, out, t, log = self.feed(lead=lead_rows(slots=(far,)), close=False)
        self.assertEqual(eng.union_mem()["union_ahead_rows"], 1)
        (trig,) = [r for r in types(out, "trigger") if r["variant"] == "pv"]
        self.assertEqual((trig["base_union"]["status"], trig[h5.UNION_FIELD]), ("ok", 1))
        calls = []
        real = h5.UnionChain.settled

        def spy(self_, hw, full=True):
            calls.append(full)
            return real(self_, hw, full)

        with mock.patch.object(h5.UnionChain, "settled", spy):
            eng.close_all("test")
        (pool,) = types(out, "pool")
        self.assertEqual(calls, [True])  # the close may count in full, once per pool
        self.assertEqual(pool[h5.UNION_FIELD], self.eager(log).settled(eng.hw_slot))
        self.assertEqual(pool["base_union"]["tip_only"], 1)
        u = h5.UnionChain()
        u.add(("a", 0), 100, 90, 10, h5.UNION_WS)
        u.add(("b", 0), 90, 80, 10 + h5.UNION_SETTLE_SCAN_MAX + 50, h5.UNION_TIP)
        self.assertIsNone(u.settled(12, full=False))  # never a full O(members) count on the trigger path

    def test_admission_order_gives_the_eager_union_and_keeps_tip_members_below_the_line(self):
        rng = random.Random(11)
        for trial in range(300):
            c = chain(rng.randint(1, 50), seed=trial)
            ev = []
            for it in c:
                for src in rng.sample([h5.UNION_WS, h5.UNION_TIP], 2):
                    if rng.random() < 0.8:
                        ev.append((src, it))
                    if rng.random() < 0.05:
                        ev.append((src, it))  # a repeat (dup)
            rng.shuffle(ev)
            ev.sort(key=lambda e: e[1][3] + (rng.randint(-3, 3) if e[0] == h5.UNION_TIP else 0))  # the tip sometimes leads, sometimes lags
            u, ref = h5.UnionChain(), h5.UnionChain()
            for src, (key, bpre, bpost, slot) in ev:
                (u.add_ws if src == h5.UNION_WS else u.add_tip)(key, bpre, bpost, slot)
                ref.add(key, bpre, bpost, slot, src)
                if u.line is not None:
                    self.assertTrue(all(v[2] <= u.line for v in u.m.values() if v[3] & h5.UNION_TIP), trial)
                    self.assertTrue(u.tip_max_slot is None or u.tip_max_slot <= u.line)
            u.flush()
            self.assertEqual((u.m, u.n, u.dup, u.both_diff, u.tip_first_slot, u.excess),
                             (ref.m, ref.n, ref.dup, ref.both_diff, ref.tip_first_slot, ref.excess), trial)
            if c:
                for hw in (c[-1][3], c[-1][3] + 2, c[0][3]):
                    self.assertEqual(u.settled(hw), ref.settled(hw))

    def test_event_index_is_the_trigger_prints_own(self):
        t = pool_rows(drain_slot=1200)
        t.rows[-1]["event_index"] = 3
        for tip in (None, FakeTip("ok", 2000)):
            eng, out = make_engine()
            eng.tip = tip
            announce(eng)
            for r in t.rows:
                eng.on_trade(r)
            trig = [r for r in types(out, "trigger")]
            self.assertTrue(trig)
            for r in trig:
                self.assertEqual((r["signature"], r["event_index"]), (t.rows[-1]["signature"], 3))
                keys = list(r)
                self.assertEqual(keys[keys.index("signature") + 1], "event_index")
        t.rows[-1]["tape_event_index"] = 9  # a replay row: its own position rides in tape_event_index (the union key's event_index is 0)
        t.rows[-1]["event_index"] = 0
        eng, out = make_engine()
        announce(eng)
        for r in t.rows:
            eng.on_trade(r)
        self.assertEqual({r["event_index"] for r in types(out, "trigger")}, {9})
        t.rows[-1].pop("tape_event_index")
        t.rows[-1]["event_index"] = None
        eng, out = make_engine()
        announce(eng)
        for r in t.rows:
            eng.on_trade(r)
        self.assertEqual({r["event_index"] for r in types(out, "trigger")}, {None})


class TipTailOffsetTests(unittest.TestCase):
    """Follow-up of #596 (reviewer fix 2 and 3): a transient error reopens at the saved offset; a reopen inside a line drops the fragment."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.dir = Path(self.td.name)
        self.now = [int(datetime(2027, 1, 1, 12, 30, tzinfo=timezone.utc).timestamp() * 1000)]
        self.rows = []
        self.tail = h5.TipTail(self.dir, self.rows.append, lambda p: p == "WANT", wall=lambda: self.now[0], chunk=4096)
        self.path = self.dir / "trades-2027-01-01T12.jsonl"

    def tearDown(self):
        self.tail.close()
        self.td.cleanup()

    def drain_polls(self, n=50):
        for _ in range(n):
            self.tail.poll()

    def test_read_error_mid_hour_delivers_each_row_exactly_once(self):
        self.tail.poll()  # the file appears after the start: read from byte 0
        sigs = [f"r{i:02d}" for i in range(51)]
        self.path.write_text("".join(line("WANT", 100 + i, s) for i, s in enumerate(sigs)))
        real, calls = os.read, [0]

        def flaky(fd, n):
            calls[0] += 1
            if calls[0] == 2:  # the second chunk: the first left a partial line behind
                raise OSError(5, "Input/output error")
            return real(fd, n)

        with mock.patch.object(h5.os, "read", flaky):
            self.tail.poll()
            self.assertGreater(self.tail.stats()["partial_bytes"], 0)
            self.tail.poll()  # the read error: fd closed
            self.assertEqual(self.tail.c["read_errors"], 1)
            self.assertEqual(self.tail.err, "OSError")
            self.drain_polls()
        self.assertEqual([r["signature"] for r in self.rows], sigs)  # 51 rows, each once (before the fix: 72 on_row calls)
        self.assertEqual(self.tail.c["files_opened"], 2)
        self.assertEqual(self.tail.c["parse_errors"], 0)
        self.assertEqual(self.tail.c["fragments_dropped"], 0)
        self.assertIsNone(self.tail.err)

    def test_open_error_at_the_first_poll_starts_at_that_size_not_byte_0(self):
        self.path.write_text(line("WANT", 1, "old1") + line("WANT", 2, "old2"))
        real = os.open
        with mock.patch.object(h5.os, "open", side_effect=PermissionError(13, "Permission denied")):
            self.tail.poll()
        self.assertEqual(self.tail.err, "PermissionError")
        with open(self.path, "a") as f:
            f.write(line("WANT", 3, "new"))
        self.assertIs(h5.os.open, real)
        self.drain_polls(3)
        self.assertEqual([r["signature"] for r in self.rows], ["new"])  # the backlog present at the start is not re-read
        self.assertIsNone(self.tail.err)

    def test_truncation_inside_a_line_drops_the_fragment(self):
        self.tail.poll()
        a, b = line("WANT", 10, "a"), line("WANT", 11, "b")
        self.path.write_text(a + b)
        self.tail.poll()
        self.assertEqual([r["signature"] for r in self.rows], ["a", "b"])
        with open(self.path, "r+b") as fh:
            fh.truncate(len(a) + 7)  # a cut that is not a line boundary
        self.tail.poll()
        self.assertEqual(self.tail.c["resets_truncated"], 1)
        with open(self.path, "a") as f:
            f.write(line("WANT", 12, "x")[30:] + line("WANT", 13, "d"))  # the rest of a line, then a whole one
        self.drain_polls(3)
        self.assertEqual([r["signature"] for r in self.rows], ["a", "b", "d"])
        self.assertEqual((self.tail.c["fragments_dropped"], self.tail.c["parse_errors"]), (1, 0))

    def test_truncation_at_a_line_boundary_keeps_the_next_line(self):
        self.tail.poll()
        a, b = line("WANT", 10, "a"), line("WANT", 11, "b")
        self.path.write_text(a + b)
        self.tail.poll()
        with open(self.path, "r+b") as fh:
            fh.truncate(len(a))
        self.tail.poll()
        with open(self.path, "a") as f:
            f.write(line("WANT", 12, "c"))
        self.drain_polls(3)
        self.assertEqual([r["signature"] for r in self.rows], ["a", "b", "c"])
        self.assertEqual(self.tail.c["fragments_dropped"], 0)

    def test_tip_follow_closes_its_fds_when_cancelled(self):
        self.path.write_text(line("WANT", 1, "x"))

        async def go():
            stop = asyncio.Event()
            task = asyncio.create_task(h5.tip_follow(self.tail, stop, poll_s=0.01))
            for _ in range(100):
                await asyncio.sleep(0.005)
                if self.tail.files:
                    break
            self.assertTrue(self.tail.files)
            fds = [e[0] for e in self.tail.files.values()]
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            return fds

        fds = asyncio.run(go())
        self.assertEqual(self.tail.files, {})
        for fd in fds:
            with self.assertRaises(OSError):
                os.fstat(fd)


class CodeShaTests(unittest.TestCase):
    def test_env_sha_then_git_then_null(self):
        self.assertEqual(h5.code_sha({"H5_CODE_SHA": "A" * 40}), "a" * 40)
        here = Path(h5.__file__).resolve().parent
        import subprocess

        head = subprocess.run(["git", "-C", str(here), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
        self.assertEqual(h5.code_sha({"H5_CODE_SHA": "not-a-sha"}), head)
        self.assertEqual(h5.code_sha({}), head)
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(h5.code_sha({}, git_dir=d))  # not a checkout: unknown
        with mock.patch.object(h5.subprocess, "run", side_effect=FileNotFoundError("git")):
            self.assertIsNone(h5.code_sha({}))

    def test_start_record_carries_code_sha(self):
        import shutil

        d = tempfile.mkdtemp(dir="/tmp", prefix="h5-sha-")
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)

        async def no_feed(*a, **k):
            return None

        for env, want in (({"H5_CODE_SHA": "b" * 40}, "b" * 40),):
            args = h5.build_parser().parse_args(["--out-dir", d, "--sockets", "3"])
            with mock.patch.object(h5, "run_feed", no_feed), mock.patch.object(h5, "build_source", lambda *a, **k: None), \
                    mock.patch.dict(os.environ, env):
                self.assertEqual(asyncio.run(h5.run_live(args)), 0)
            rows = [json.loads(x) for f in sorted(Path(d).glob("h5-shadow-*.jsonl")) for x in f.read_text().splitlines()]
            (start,) = [r for r in rows if r["type"] == "start"]
            self.assertEqual(start["code_sha"], want)
            keys = list(start)
            self.assertEqual(keys[keys.index("rule_sha256") + 1], "code_sha")
        with mock.patch.object(h5, "code_sha", lambda: None):
            args = h5.build_parser().parse_args(["--out-dir", d + "/2", "--sockets", "3"])
            with mock.patch.object(h5, "run_feed", no_feed), mock.patch.object(h5, "build_source", lambda *a, **k: None):
                self.assertEqual(asyncio.run(h5.run_live(args)), 0)
            rows = [json.loads(x) for f in sorted(Path(d + "/2").glob("h5-shadow-*.jsonl")) for x in f.read_text().splitlines()]
            (start,) = [r for r in rows if r["type"] == "start"]
            self.assertIn("code_sha", start)
            self.assertIsNone(start["code_sha"])


if __name__ == "__main__":
    unittest.main()
