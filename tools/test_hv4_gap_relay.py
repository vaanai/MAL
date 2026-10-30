"""hv4_gap_relay tests. Synthetic tape lines only; nothing under /data/mal is read."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from tools import arena_live_bridge as alb
from tools import hv4_gap_relay as R
from tools import hv4_live_c0 as c0
from tools.test_hv4_live_c0 import ARM, Hv4Env


def line(**kw):
    return json.dumps(kw) + "\n"


class RelayTests(unittest.TestCase):
    def setUp(self):
        self._d = tempfile.TemporaryDirectory()
        self.addCleanup(self._d.cleanup)
        self.tmp = Path(self._d.name)
        self.tape = self.tmp / "tape"
        self.tape.mkdir()

    def write(self, name, *lines):
        (self.tape / name).write_text("".join(lines))

    def test_kinds_come_from_the_engine_definition(self):
        from tools import arena_paper
        self.assertEqual(R.HARD_GAP_KINDS, arena_paper.HARD_GAP_KINDS)
        self.assertEqual(R.GAP_SILENCE_MS, arena_paper.ArenaConfig().gap_silence_ms if hasattr(arena_paper, "ArenaConfig") else R.GAP_SILENCE_MS)
        self.assertTrue(set(R.RELAY_KINDS) <= set(c0.GAP_BREAK_KINDS))

    def test_only_kind_and_instant_are_emitted(self):
        self.write("2026-10-26.jsonl",
                   line(type="gap", kind="process_restart", t_recv_ms=1000, prog="SECRET_PROG", slot=9, price=123.0, mint="DECOY"),
                   line(type="gap", kind="soft_thing", t_recv_ms=1500),
                   line(type="gap", kind="x", data_loss=True, t_recv_ms=2000, mint="DECOY"),
                   line(type="start", t_recv_ms=3000, extra="DECOY"),
                   line(type="gap", kind="takeover", t_recv_ms=4000))
        rows = R.scan([self.tape / "2026-10-26.jsonl"])
        self.assertEqual(rows, [{"kind": "process_restart", "ms": 1000}, {"kind": "data_loss", "ms": 2000}, {"kind": "start", "ms": 3000}, {"kind": "takeover", "ms": 4000}])
        self.assertTrue(all(set(r) == {"kind", "ms"} for r in rows))

    def test_trade_lines_are_never_parsed_only_their_instant_is_used(self):
        # the trade line is not valid JSON after the instant: a parser would choke, the relay only matches type and t_recv_ms
        bad = b'{"type": "trade", "t_recv_ms": 10, "price": ###\n'
        later = b'{"type": "trade", "t_recv_ms": %d, "price": ###\n' % (10 + R.GAP_SILENCE_MS + 1)
        (self.tape / "a.jsonl").write_bytes(bad + later)
        self.assertEqual(R.scan([self.tape / "a.jsonl"]), [{"kind": "gap_silence", "ms": 10 + R.GAP_SILENCE_MS + 1}])
        (self.tape / "b.jsonl").write_bytes(bad + b'{"type": "trade", "t_recv_ms": %d}\n' % (10 + R.GAP_SILENCE_MS))
        self.assertEqual(R.scan([self.tape / "b.jsonl"]), [])  # exactly the bound is not a breach (> only)

    def test_torn_tail_is_ignored_and_output_is_sorted_and_deduplicated(self):
        self.write("a.jsonl", line(type="gap", kind="feed_restart", t_recv_ms=500), line(type="gap", kind="feed_restart", t_recv_ms=500))
        with open(self.tape / "a.jsonl", "a") as fh:
            fh.write('{"type": "gap", "kind": "feed_restart", "t_recv_ms": 9')
        self.assertEqual(R.scan([self.tape / "a.jsonl"]), [{"kind": "feed_restart", "ms": 500}])

    def test_main_writes_the_stream_atomically_and_refuses_bad_ids(self):
        self.write("a.jsonl", line(type="gap", kind="process_restart", t_recv_ms=7))
        out = self.tmp / "out"
        self.assertEqual(R.main(["--tape-dir", str(self.tape), "--bot-id", ARM, "--out-dir", str(out)]), 0)
        f = out / "transport" / f"{ARM}.tapegaps.jsonl"
        self.assertEqual([json.loads(x) for x in f.read_text().splitlines()], [{"kind": "process_restart", "ms": 7}])
        self.assertEqual([p.name for p in (out / "transport").iterdir()], [f.name])  # no tmp left
        for bad in ("hv4-../x-F", "hv4-CON-L", "rule1"):
            self.assertEqual(R.main(["--tape-dir", str(self.tape), "--bot-id", bad, "--out-dir", str(out)]), 2, bad)

    def test_bridge_latches_feed_break_on_the_relay_stream(self):
        e = Hv4Env(str(self.tmp / "env"))
        b = e.bridge()
        e.start()
        d = Path(e.raw["root"]) / "transport"
        d.mkdir(exist_ok=True)
        (d / f"{e.bot}.tapegaps.jsonl").write_text(json.dumps({"kind": "data_loss", "ms": e.clock.t}) + "\n")
        b.tick()
        self.assertIn(c0.FEED_BREAK_LATCH, b.state.latches)
        self.assertEqual(b.state.gap_noted_ms, e.clock.t)


if __name__ == "__main__":
    unittest.main()
