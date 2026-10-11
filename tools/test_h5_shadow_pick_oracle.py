"""The H5 shadow wired to the CAP-PICK pick oracle (EXP-022 section 9, DEC-024 section 6). Synthetic tape and synthetic picks files only:
nothing here reads /data/mal, /var/lib/mal, a gate log or any real row."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from tools import cap_pick_oracle as co
from tools import h5_shadow as h5
from tools.test_h5_shadow import MINT, POOL, SEALED_POOL_KEYS, SOL, Tape, announce, boost_buys, drain, make_engine, types

TS0 = 1_800_000_000  # the Tape's default first block time; the seal window starts exactly there in these tests
SEAL_AT = TS0 * 1000
DRAIN_IDX = 5  # rows: 0 first print (s0), 1-4 BOOST buys, 5 the drain sell (the trigger), 6 a buy, 7 a buy past the exit (resolves outcomes)


class Answer:
    """A three-state pick oracle whose answer the test sets between rows."""

    def __init__(self, ans: bool | None = None) -> None:
        self.ans, self.calls = ans, 0

    def __call__(self, mint: str) -> bool | None:
        self.calls += 1
        return self.ans


def tape() -> Tape:
    t = Tape()
    t.row(1000, "buy", "A", SOL // 10)
    boost_buys(t, 1000)
    drain(t, 1200, 35.0)
    t.row(1210, "buy", "X", SOL)
    t.row(1900, "buy", "W", SOL)
    return t


def engine(oracle, **kw):
    kw.setdefault("seal_start_ms", SEAL_AT)
    eng, out = make_engine(pick_oracle=oracle, suppress_outcome=h5.cap_pick_seal_oracle_stub, **kw)
    announce(eng)
    return eng, out


def feed(eng, rows) -> None:
    for r in rows:
        eng.on_trade(r)


def decision_records(out) -> list[dict]:
    return [x for x in out if x["type"] in ("trigger", "outcome", "strip", "skipped_no_sps", "excluded")]


class PickOracleWireTests(unittest.TestCase):
    def test_no_answer_at_s0_then_not_a_pick_writes_everything(self):
        o = Answer(None)
        eng, out = engine(o)
        rows = tape().rows
        feed(eng, rows[:DRAIN_IDX])  # the gate has not decided yet: the usual state at the pool's first print
        p = eng.pools[POOL]
        self.assertTrue(eng._sealed(p))
        self.assertIsNone(p.sealed_cache)  # not frozen
        o.ans = False  # the exporter wrote the gate's row
        feed(eng, rows[DRAIN_IDX:])
        eng.close_all("t")
        self.assertEqual(sorted(r["variant"] for r in types(out, "trigger")), ["fv", "pv"])
        self.assertEqual(len(types(out, "outcome")), 2)
        self.assertEqual(len(types(out, "strip")), 1)
        (pool,) = types(out, "pool")
        self.assertFalse(pool["sealed"])
        self.assertNotIn("trigger_withheld_pick_pending", pool)
        self.assertGreater(o.calls, 2)  # asked again at each decision point

    def test_no_answer_at_the_trigger_withholds_it_for_good(self):
        o = Answer(None)
        eng, out = engine(o)
        rows = tape().rows
        feed(eng, rows[: DRAIN_IDX + 1])  # the drain fires while the oracle has no answer
        o.ans = False
        feed(eng, rows[DRAIN_IDX + 1:])
        eng.close_all("t")
        self.assertEqual(decision_records(out), [])  # never written late: the executor would buy late
        (pool,) = types(out, "pool")
        self.assertFalse(pool["sealed"])
        self.assertEqual(pool["triggered"], ["fv", "pv"])
        self.assertEqual(pool["trigger_withheld_pick_pending"], ["fv", "pv"])
        self.assertEqual(eng.counters["triggers_pv"], 0)

    def test_a_pick_seals_for_good_and_is_counted_once_at_close(self):
        o = Answer(None)
        eng, out = engine(o)
        rows = tape().rows
        feed(eng, rows[:DRAIN_IDX])
        o.ans = True
        feed(eng, rows[DRAIN_IDX:DRAIN_IDX + 1])
        o.ans = False  # PickOracle never does this (a pick is sticky); the engine must not unseal either
        feed(eng, rows[DRAIN_IDX + 1:])
        self.assertIs(eng.pools[POOL].sealed_cache, True)
        eng.close_all("t")
        self.assertEqual(decision_records(out), [])
        (pool,) = types(out, "pool")
        self.assertEqual(set(pool), SEALED_POOL_KEYS)
        (hour,) = [r for r in types(out, "sealed_hour")]
        self.assertEqual((hour["pools_opened"], hour["decisions"]), (1, "withheld"))

    def test_a_pick_known_at_s0_is_counted_at_s0_and_not_again(self):
        eng, out = engine(Answer(True))
        feed(eng, tape().rows)
        self.assertTrue(eng.pools[POOL].sealed_noted)
        eng.close_all("t")
        (hour,) = types(out, "sealed_hour")
        self.assertEqual(hour["pools_opened"], 1)
        self.assertEqual(decision_records(out), [])

    def test_a_pick_after_the_trigger_withholds_the_outcome_and_the_strip(self):
        o = Answer(False)
        eng, out = engine(o)
        rows = tape().rows
        feed(eng, rows[: DRAIN_IDX + 1])
        self.assertEqual(len(types(out, "trigger")), 2)  # the trigger went out while the mint was a non-pick
        o.ans = True  # a later row (e.g. the walk-2 replay) says pick
        feed(eng, rows[DRAIN_IDX + 1:])
        eng.close_all("t")
        self.assertEqual([x["type"] for x in out if x["type"] in ("outcome", "strip")], [])
        (pool,) = types(out, "pool")
        self.assertEqual(set(pool), SEALED_POOL_KEYS)

    def test_a_stale_answer_after_the_trigger_keeps_the_outcome_pending_until_the_feed_is_back(self):
        o = Answer(False)
        eng, out = engine(o)
        t = tape()
        feed(eng, t.rows[:-1])
        o.ans = None  # the feed went stale when the outcome came due
        feed(eng, t.rows[-1:])
        self.assertEqual(types(out, "outcome"), [])
        self.assertEqual(len(eng.pools[POOL].pending), 2)  # not dropped
        o.ans = False
        feed(eng, [t.row(1950, "buy", "Z", SOL)])
        self.assertEqual(len(types(out, "outcome")), 2)
        eng.close_all("t")
        self.assertFalse(types(out, "pool")[0]["sealed"])

    def test_still_no_answer_at_close_seals_the_pool_and_drops_the_pending_outcome(self):
        o = Answer(False)
        eng, out = engine(o)
        t = tape()
        feed(eng, t.rows[:-1])
        o.ans = None
        feed(eng, t.rows[-1:])
        eng.close_all("t")
        self.assertEqual(types(out, "outcome"), [])
        self.assertEqual(set(types(out, "pool")[0]), SEALED_POOL_KEYS)
        self.assertEqual(eng.pools, {})

    def test_before_the_window_the_oracle_is_not_asked_and_nothing_changes(self):
        o = Answer(True)
        eng, out = engine(o, seal_start_ms=SEAL_AT + 1)
        feed(eng, tape().rows)
        eng.close_all("t")
        self.assertEqual(o.calls, 0)
        self.assertEqual(len(types(out, "outcome")), 2)
        self.assertFalse(types(out, "pool")[0]["sealed"])

    def test_same_records_as_the_legacy_false_hook_for_a_non_pick(self):
        # a pool the oracle calls a non-pick from the start is written exactly as the legacy hook wrote a non-pick (no trading change)
        eng_a, out_a = engine(Answer(False))
        eng_b, out_b = make_engine(seal_start_ms=SEAL_AT, suppress_outcome=lambda m: False)
        announce(eng_b)
        for eng in (eng_a, eng_b):
            feed(eng, tape().rows)
            eng.close_all("t")
        strip = lambda out: [{k: v for k, v in r.items() if k != "t_ms"} for r in out if r["type"] != "sealed_hour"]  # noqa: E731
        self.assertEqual(strip(out_a), strip(out_b))

    def test_the_window_is_judged_once_so_a_late_pre_window_print_cannot_unseal(self):
        o = Answer(None)
        eng, out = make_engine(seal_start_ms=h5.SEAL_START_MS, pick_oracle=o)
        announce(eng, slot=985)
        t = Tape(ts0=h5.SEAL_START_MS // 1000, q=100 * SOL)
        t.row(990, "buy", "A", SOL // 10)  # the true first print, block time before the seal start, arrives LAST
        for i in range(1, 5):
            t.row(1000 + 10 * i, "buy", f"T{i}", SOL // 20)
        feed(eng, t.rows[1:])
        p = eng.pools[POOL]
        self.assertTrue(p.seal_window)
        eng.on_trade(t.rows[0])
        self.assertEqual(p.s0, 990)  # re-anchored ...
        self.assertTrue(p.seal_window)  # ... but still inside the window
        self.assertTrue(eng._sealed(p))
        o.ans = False
        self.assertFalse(eng._sealed(p))  # only the oracle's answer can let it through

    def test_a_real_trigger_is_not_re_anchored_when_the_answer_goes_missing(self):
        o = Answer(False)
        eng, out = engine(o)
        t = Tape()
        t.row(1000, "buy", "A", SOL // 10)
        boost_buys(t, 1000)
        drain(t, 1200, 35.0)
        feed(eng, t.rows)
        self.assertEqual(len(types(out, "trigger")), 2)
        o.ans = None
        p = eng.pools[POOL]
        eng.on_trade(t.row(990, "buy", "LATE", SOL // 100))  # a print below s0 after the trigger record went out
        self.assertEqual(p.s0, 1000)  # flagged, not moved: the executor already holds a plan anchored on 1000
        self.assertEqual(p.gaps[-1]["kind"], "slot_below_s0")

    def test_the_class_is_requested_for_a_pool_whose_verdict_is_open_not_for_a_known_pick(self):
        for ans, want in ((None, [MINT]), (False, [MINT]), (True, [])):
            eng, out = engine(Answer(ans), classifier=h5.StaticClassifier(None))
            asked: list[str] = []
            eng.syn_request = lambda mint, sig=None, asked=asked: asked.append(mint)
            feed(eng, tape().rows[:1])
            self.assertEqual(asked, want, ans)
        eng, out = make_engine(seal_start_ms=SEAL_AT, suppress_outcome=h5.cap_pick_seal_oracle_stub, classifier=h5.StaticClassifier(None))
        announce(eng)
        asked = []
        eng.syn_request = lambda mint, sig=None: asked.append(mint)
        feed(eng, tape().rows[:1])
        self.assertEqual(asked, [])  # the legacy stub: unchanged

    def skip_run(self, oracle, later: bool | None):
        state = {"sps": None}
        eng, out = engine(oracle, sps_fn=lambda p: state["sps"])
        t = tape()
        rows = t.rows[: DRAIN_IDX + 1]
        feed(eng, rows)  # the drain qualifies but the slot rate is unknown: a skipped_no_sps candidate
        oracle.ans = later
        state["sps"] = 0.4
        feed(eng, [t.row(1250, "sell", "S2", SOL // 100)])  # a later sell, now with a slot rate: a trigger candidate
        eng.close_all("t")
        return eng, out

    def test_a_skip_held_while_pending_is_written_before_any_trigger_once_the_pool_is_a_non_pick(self):
        eng, out = self.skip_run(Answer(None), later=False)
        kinds = [x["type"] for x in out if x["type"] in ("skipped_no_sps", "trigger")]
        self.assertEqual(kinds[0], "skipped_no_sps")  # first, so the executor refuses the later trigger (sps_skipped_pool) exactly as before
        self.assertIn("trigger", kinds)
        self.assertTrue(types(out, "skipped_no_sps")[0]["withheld_pick_pending"])
        self.assertEqual(eng.counters["skipped_no_sps_would_trigger"], 1)
        # the same pool when the oracle says "not a pick" from the start: the same records in the same order
        legacy = Answer(False)
        _, out_l = self.skip_run(legacy, later=False)
        self.assertEqual([x["type"] for x in out_l if x["type"] in ("skipped_no_sps", "trigger")], kinds)

    def test_a_skip_held_while_pending_is_dropped_for_a_pick(self):
        eng, out = self.skip_run(Answer(None), later=True)
        self.assertEqual(decision_records(out), [])
        self.assertEqual(set(types(out, "pool")[0]), SEALED_POOL_KEYS)

    def test_an_oracle_that_raises_seals_for_now_and_counts(self):
        state = {"boom": True}

        def flaky(mint):
            if state["boom"]:
                raise RuntimeError("x")
            return False

        eng, out = engine(flaky)
        rows = tape().rows
        feed(eng, rows[:DRAIN_IDX])
        self.assertGreater(eng.counters["seal_oracle_errors"], 0)
        state["boom"] = False
        feed(eng, rows[DRAIN_IDX:])
        eng.close_all("t")
        self.assertEqual(len(types(out, "trigger")), 2)


class RealOracleTests(unittest.TestCase):
    """tools.cap_pick_oracle.PickOracle on a synthetic picks file, in the engine."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.d = Path(self._td.name)
        self.live = self.d / "picks.jsonl"
        self.marker = self.d / "FINAL_WRITTEN"
        self.now = [SEAL_AT + 10_000]

    def write(self, *lines: str) -> None:
        with self.live.open("a") as fh:
            for x in lines:
                fh.write(x + "\n")

    def oracle(self) -> co.PickOracle:
        return co.PickOracle([self.live], [], now_ms=lambda: self.now[0], final_marker=self.marker)

    def test_a_mint_the_reader_cannot_parse_is_never_decided(self):
        self.marker.write_text("")
        self.write(co.heartbeat_row(self.now[0]), json.dumps({"mint": MINT, "pick": False}))  # MINT is a short test id, not base58 of 32-44
        o = self.oracle()
        self.assertIsNone(o(MINT))
        eng, out = engine(o)
        feed(eng, tape().rows)
        eng.close_all("t")
        self.assertEqual(decision_records(out), [])  # sealed, fail closed
        self.assertTrue(types(out, "pool")[0]["sealed"])

    def test_end_to_end_with_a_base58_mint(self):
        m = "M" * 43 + "1"
        self.marker.write_text("")
        self.write(co.heartbeat_row(self.now[0]))
        o = self.oracle()
        eng, out = make_engine(seal_start_ms=SEAL_AT, pick_oracle=o)
        eng.on_create_pool(POOL, m, h5.WSOL_MINT, 999, 0)
        rows = tape().rows
        for r in rows:
            r["mint"] = m
        feed(eng, rows[:DRAIN_IDX])
        self.assertTrue(eng._sealed(eng.pools[POOL]))
        self.write(co.decision_row(m, False, self.now[0]))
        feed(eng, rows[DRAIN_IDX:])
        eng.close_all("t")
        self.assertEqual(sorted(r["variant"] for r in types(out, "trigger")), ["fv", "pv"])
        self.assertEqual(len(types(out, "outcome")), 2)

    def test_stale_feed_and_missing_marker_seal(self):
        m = "M" * 43 + "1"
        self.write(co.decision_row(m, False, self.now[0]), co.heartbeat_row(self.now[0]))
        for marker, age_ms in ((False, 0), (True, 61_000)):
            if marker:
                self.marker.write_text("")
            self.now[0] = SEAL_AT + 10_000 + age_ms
            o = self.oracle()
            eng, out = make_engine(seal_start_ms=SEAL_AT, pick_oracle=o)
            eng.on_create_pool(POOL, m, h5.WSOL_MINT, 999, 0)
            rows = tape().rows
            for r in rows:
                r["mint"] = m
            feed(eng, rows)
            eng.close_all("t")
            self.assertEqual(decision_records(out), [], (marker, age_ms))
            self.assertTrue(types(out, "pool")[0]["sealed"])


class EnvTests(unittest.TestCase):
    def test_not_configured_keeps_the_stub(self):
        o, info = h5.cap_pick_oracle_from_env({})
        self.assertIsNone(o)
        self.assertEqual(info["oracle"], "stub_always_true")
        o, info = h5.cap_pick_oracle_from_env({"CAP_PICK_LIVE": "/x/picks.jsonl"})  # no FINAL marker: not configured
        self.assertIsNone(o)
        o, info = h5.cap_pick_oracle_from_env({"CAP_PICK_LIVE": "/x", "CAP_PICK_FINAL_MARKER": "/m", "CAP_PICK_STALE_S": "soon"})
        self.assertIsNone(o)
        self.assertEqual(info["why"], "cap_pick_env_error:ValueError")

    def test_configured_and_the_staleness_limit_cannot_be_loosened(self):
        env = {"CAP_PICK_LIVE": "/x/picks.jsonl", "CAP_PICK_REPLAY": os.pathsep.join(["/x/r1.jsonl", "/x/r2.jsonl"]), "CAP_PICK_FINAL_MARKER": "/x/F",
               "CAP_PICK_STALE_S": "600"}
        o, info = h5.cap_pick_oracle_from_env(env)
        self.assertIsInstance(o, co.PickOracle)
        self.assertEqual(o.stale_s, 60.0)
        self.assertEqual(info, {"oracle": "cap_pick_oracle", "live_files": 1, "replay_files": 2, "final_marker": True, "stale_s": 60.0})
        o, _ = h5.cap_pick_oracle_from_env({**env, "CAP_PICK_STALE_S": "30"})
        self.assertEqual(o.stale_s, 30.0)  # tighter is allowed

    def test_the_engine_uses_the_pick_oracle_over_the_legacy_hook(self):
        eng = h5.Engine(lambda r: None, pda_fn=lambda p: "x", suppress_outcome=h5.cap_pick_seal_oracle_stub, pick_oracle=lambda m: False,
                        classifier=h5.StaticClassifier(False))
        after = h5.Pool("p", "m", 1, h5.Pr(recv_ms=h5.SEAL_START_MS, ts=h5.SEAL_START_MS // 1000), 17_584_505_288, None, None)
        self.assertFalse(eng._sealed(after))


if __name__ == "__main__":
    unittest.main()
