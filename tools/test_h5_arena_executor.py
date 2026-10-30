"""Offline tests for tools/h5_arena_executor.py (QP-SCOPE-1022-h5exec). Fake RPC, fake clock, throwaway Keypairs. No network, no real key, no feed."""

from __future__ import annotations

import base64
import dataclasses
import hashlib
import json
import os
import struct
import time
from pathlib import Path
from unittest import mock

from tools import h5_arena_executor as ax
from tools import h5_executor as h
from tools import test_h5_shadow_boost_slice as slicetest
from tools.test_h5_executor import MINT, POOL, Q, QREAL, S0, SPS, TRIG_SLOT, V, Case, Env, row, trig
from tools.test_h5_executor_shadow import shadow_trigger

A2, A2V2, Q50 = "h5v1-boostfloor-a2", "h5v2-boostclock-a2", "h5v1q50-boostfloor-a1"
PINNED_BLOB = "03995837317f"


def start_rec(book: ax.Book, **kw) -> dict:
    rec = {"type": "start", "rule": book.rule, "code_sha": book.frozen_sha, "fresh_forward": "ARENA", "dirty": False, "tip_union": "off", "start_ms": 1, "t_ms": 1}
    if book.q_star_in_record is not None:
        rec.update(q_star_sol=book.q_star_in_record, rule_sha256=book.rule_sha256)
    return {**rec, **kw}


def mint_value(owner: str = ax.mf.SPL_TOKEN_PROGRAM, freeze: bool = False, ext_types: tuple = ()) -> dict:
    """A getAccountInfo value for a mint: SPL layout (82 bytes) or Token-2022 with TLV extension types (18 metadataPointer, 19 tokenMetadata, 1 transferFeeConfig...)."""
    raw = bytearray(82)
    raw[45] = 1
    if freeze:
        struct.pack_into("<I", raw, 46, 1)
        raw[50:82] = bytes([7]) * 32
    if owner == ax.mf.TOKEN_2022_PROGRAM:
        raw = bytearray(raw) + bytearray(165 - 82) + bytearray([1])
        for t in ext_types:
            raw += struct.pack("<HH", t, 4) + bytes(4)
    return {"data": [base64.b64encode(bytes(raw)).decode(), "base64"], "owner": owner, "lamports": 1, "executable": False}


CANARY_TEST_FILE = "DEC/DEC-033-h5-arena-canary-test.md"


def write_canary_record(root: Path, *bot_ids: str, name: str = "DEC-033-h5-arena-canary-test.md") -> None:
    (root / "DEC").mkdir(parents=True, exist_ok=True)
    ids = bot_ids or (A2, A2V2, Q50, "h5v1-p155-testbook")
    (root / "DEC" / name).write_text("".join(f"OWNER_CANARY_CONFIRMED: bot_id={b}\n" for b in ids))


def a3_rec(run_utc: str = "2026-10-06T15:00:00Z", **halt_true) -> dict:
    names = ("pins_changed", "boost_disabled", "boost_share_low", "boost_budget_shift", "boost_slices_shift", "synthetic_share_high")
    return {"run_utc": run_utc, "halt": {"flags": {n: {"halt": n in halt_true, "evaluated": True, "reason": "x"} for n in names}},
            "warn": {"flags": {"program_changed": {"warn": bool(halt_true.get("program_changed")), "evaluated": True, "reason": "x"}}}}


class AEnv(Env):
    book_id = A2

    def __init__(self, tmp: Path, live: bool = True, **cfg):
        if live:  # a live start needs these (QP 10-23): the exit-gap feed file, the twins file and the tie-out results dir
            cfg.setdefault("exit_gap_file", str(tmp / "state" / "arena-parity" / "exit-gaps.jsonl"))  # the executor's own writer path (live start check)
            for key, name in (("exit_gap_file", "exit-gap.jsonl"), ("parity_twins_file", "twins.jsonl")):
                cfg.setdefault(key, str(tmp / name))
                if cfg[key]:
                    Path(cfg[key]).parent.mkdir(parents=True, exist_ok=True)
                    Path(cfg[key]).touch()
            cfg.setdefault("tieout_dir", str(tmp / "tieout"))
            if cfg.get("parity_twins_file") and cfg.pop("pinned_twin_record", True):  # the writer has run once on the empty trips file (QP 10-23 blocker 3): a pinned, empty run record
                ch, cs = ax.twin_pins(ax.BOOKS[cfg.get("book_id", A2)])
                Path(cfg["parity_twins_file"] + ".run.json").write_text(json.dumps({"schema": "arena_twin_run_v1", "run_ms": 0, "covered_trip_ids": [], "twin_rows": 0,
                    "twins_sha256": hashlib.sha256(b"").hexdigest(), "config_hash": ch, "code_sha256": cs}))
            (tmp / "etc-mal-h5" / "arena-TIER").write_text("T1\n")
            # the live entry (live_entry_refusal): mode live, no replay_only, a declared A3 record, the arena LIVE_OK naming this book, FEED_NOTE_MERGED
            cfg.pop("replay_only", None)
            cfg.setdefault("a3_record_file", str(tmp / "a3-default.jsonl"))
            lok = ax.ARENA_LIVE_OK_PATH
            assert str(lok).startswith(str(tmp)), lok  # a test never writes the host's /etc/mal-h5-arena
            if cfg.pop("arena_live_ok", True):
                lok.write_text(cfg.get("book_id", A2) + "\n")
                os.chmod(lok, 0o644)
            with mock.patch.object(ax, "FEED_NOTE_MERGED", True):
                super().__init__(tmp, live=live, **cfg)
        else:
            super().__init__(tmp, live=live, **cfg)
        if live:  # the start check saw T1 (the file's content at construction); the older fixtures below test base-tier semantics, so they run on at T0
            (tmp / "etc-mal-h5" / "arena-TIER").write_text("T0\n")

    def build(self):
        self.rpc.accounts[MINT] = mint_value()  # a clean classic SPL mint: the pre-send screen passes
        if self.conf.get("funded_lamports"):
            self.rpc.balance = self.conf["funded_lamports"]  # the finalized balance a live start compares with funded_lamports
        ex = ax.ArenaExecutor(self.rpc, self.conf, self.kp, now_ms=self.clock, root=self.root)
        self.seed_clock(ex)
        if ex.gates is not None:
            ex.gates.runner = lambda argv: 0  # the parity and tie-out tools are stubbed here; tools/test_h5_arena_exec_wiring.py runs the real ones
        return ex


class ACase(Case):
    book_id = A2

    def setUp(self):
        super().setUp()
        (self.tmp / "a3-default.jsonl").write_text(json.dumps(a3_rec()) + "\n")  # never the host's real file
        (self.tmp / ax.A3_APPLIED_NAME).write_text(json.dumps({"run_utc": a3_rec()["run_utc"], "verdict": "NONE"}) + "\n")  # DEC-032 Am.3: the pooled guard's line for the same run, clear
        self.arena_tier = self.etc / "arena-TIER"  # stand-in for /etc/mal-h5-arena/TIER (owned by the test's uid, which the patched root uid matches)
        self.arena_tier.write_text("T1\n")
        os.chmod(self.arena_tier, 0o644)
        for patcher in (mock.patch.object(ax, "ARENA_TIER_PATH", self.arena_tier), mock.patch.object(ax, "ARENA_LIVE_OK_PATH", self.etc / "arena-LIVE_OK"),
                        mock.patch.object(ax, "ARENA_OWNER_CANARY_PATH", self.etc / "arena-OWNER_CANARY"),  # never the host's /etc/mal-h5-arena/OWNER_CANARY
                        mock.patch.object(ax, "FEED_NOTE_BOOKS", frozenset(ax.BOOKS) | {"h5v1-p155-testbook"}),  # fixtures run every book; the real set is tested in test_h5_arena_feed_note
                        mock.patch.object(ax, "V2_RULE_SOURCE", self.tmp / "FREEZE.md"),
                        mock.patch.object(ax, "A3_RECORD_DEFAULT", str(self.tmp / "a3-default.jsonl"))):
            patcher.start()
            self.addCleanup(patcher.stop)

    def env(self, verified: bool = True, **kw) -> AEnv:
        kw.setdefault("live", False)
        kw.setdefault("book_id", self.book_id)
        kw.setdefault("replay_only", True)
        (self.tmp / "FREEZE.md").write_text("x")
        with mock.patch.object(ax, "V2_RULE_SHA256", self._sha(self.tmp / "FREEZE.md")):
            e = AEnv(self.tmp, **kw)
        if verified:
            e.ex.feed_verified = True
            e.ex._start_ms = 0
            e.ex.slices_taken = 1
        e.ex.abook = dataclasses.replace(e.ex.abook, clock_start_ms=0)  # the fixtures live in the 1970s
        return e

    @staticmethod
    def _sha(p: Path) -> str:
        import hashlib
        return hashlib.sha256(p.read_bytes()).hexdigest()


class PinTests(Case):
    def test_the_h5_executor_blob_is_the_pinned_one(self):
        self.assertTrue(ax.executor_blob().startswith(PINNED_BLOB), ax.executor_blob())

    def test_book_table_values(self):
        self.assertEqual(set(ax.BOOKS), {A2, A2V2, Q50})
        for b in (A2, A2V2):
            self.assertEqual((ax.BOOKS[b].frozen_sha[:7], ax.BOOKS[b].q_star_lamports, ax.BOOKS[b].clock_start_ms), ("1f7a89c", 40 * 10**9, 1792713600000))
        q = ax.BOOKS[Q50]
        self.assertEqual((q.frozen_sha[:8], q.q_star_lamports, q.clock_start_ms, q.exit_pin), ("a5f41370", 50 * 10**9, 1792886400000, "v1"))
        self.assertEqual((ax.BOOKS[A2].exit_pin, ax.BOOKS[A2V2].exit_pin), ("v1", "v2"))
        self.assertTrue(all(b.end_ms_seal == 1793923200000 for b in ax.BOOKS.values()))

    def test_q50_constants_equal_the_scorer_wrapper(self):
        from tools import arena_h5_score_q50 as q50
        from tools import h5_shadow as sh
        self.assertEqual((ax.Q50_RULE, ax.Q50_RULE_SHA256, ax.BOOKS[Q50].bot_id), (q50.Q50_RULE, q50.Q50_RULE_SHA256, q50.Q50_ID))
        self.assertEqual(ax.Q50_RULE, sh.Q50_RULE_ID)
        self.assertEqual(ax.BOOKS[A2].rule, sh.RULE_ID)

    def test_the_base_executor_module_is_untouched_by_import(self):
        self.assertEqual(h.RULE_Q_MAX_LAMPORTS, 40 * 10**9)
        self.assertEqual(h.EXIT_MODE, h.EXIT_MODE_V1)


class StartTests(ACase):
    def test_pick_file_refuses_the_start(self):
        for k, v in (("pick_file", "/x"), ("pick_replay_files", ["/y"])):
            with self.assertRaises(SystemExit) as cm:
                self.env(**{k: v})
            self.assertIn("pick_file_set", str(cm.exception))

    def test_without_the_feed_note_only_replay_runs(self):
        with mock.patch.object(ax, "FEED_NOTE_MERGED", False), self.assertRaises(SystemExit) as cm:
            self.env(replay_only=False)
        self.assertIn("feed_note_unmerged_replay_only", str(cm.exception))

    def test_unknown_book_refused(self):
        with self.assertRaises(SystemExit):
            self.env(book_id="h5v1-boostfloor-a1")

    def test_live_not_built_switch_still_refuses_when_off(self):
        """LIVE_BUILT is True since the live-entry PR; the switch itself still refuses every keyed start when it is off (tools/test_h5_arena_live_entry.py covers the entry)."""
        with mock.patch.object(ax, "LIVE_BUILT", False), self.assertRaises(SystemExit) as cm:
            self.env(live=True, funded_lamports=500_000_000)
        self.assertIn("live_not_built", str(cm.exception))

    def test_end_ms_is_clamped_to_the_book_seal_end(self):
        e = self.env(end_ms=ax.SEAL_END_MS + 10**9)
        self.assertEqual(e.ex.h5.end_ms, ax.SEAL_END_MS)
        self.assertEqual(self.env(end_ms=5).ex.h5.end_ms, 5)
        self.assertEqual(self.env().ex.h5.end_ms, ax.SEAL_END_MS)

    def test_the_cap_pick_seal_does_not_block_a_dry_buy_in_the_old_window(self):
        e = self.env()
        e.set_time(h.SEAL_START_MS + 3_600_000)  # inside the old [10-16T01Z, 11-06T02Z) window, no oracle, no FINAL
        e.ex.abook = dataclasses.replace(e.ex.abook, end_ms_seal=h.SEAL_END_DEFAULT_MS + 10**9)
        e.fire()
        self.assertNotIn("seal_skip", e.ex._refusal_counts)
        self.assertEqual(e.ledger("decision")[0]["bot_id"], A2)

    def test_v2_start_checks_run_for_the_v2_book(self):
        self.book_id = A2V2
        (self.tmp / "FREEZE.md").write_text("x")
        with mock.patch.object(ax, "V2_RULE_SHA256", "0" * 64), self.assertRaises(SystemExit) as cm:
            AEnv(self.tmp, live=False, book_id=A2V2, replay_only=True)
        self.assertIn("v2_rule_source_sha_mismatch", str(cm.exception))
        (self.tmp / "FREEZE.md").unlink()
        self.assertEqual(ax.v2_start_refusal(), "v2_rule_source_unreadable")
        e = self.env()
        self.assertEqual(e.ex.exit_mode, h.EXIT_MODE_V2)  # the pin, not the module's v1
        self.assertEqual(self.env().ex.exit_mode, h.EXIT_MODE_V2)

    def test_v1_books_pin_v1(self):
        self.assertEqual(self.env().ex.exit_mode, h.EXIT_MODE_V1)
        self.book_id = Q50
        self.assertEqual(self.env().ex.exit_mode, h.EXIT_MODE_V1)


class StartRecordTests(ACase):
    def test_the_registered_start_records_pass(self):
        for b in ax.BOOKS.values():
            self.assertIsNone(ax.start_record_problem(b, start_rec(b)), b.bot_id)

    def test_each_mismatch_is_named(self):
        a2, q = ax.BOOKS[A2], ax.BOOKS[Q50]
        for book, kw, why in ((a2, {"code_sha": None}, "null_code_sha"), (a2, {"code_sha": "a5f413704eb120c8f529101a6186e3a95de7f83e"}, "frozen_sha_mismatch"),
                              (a2, {"fresh_forward": None}, "not_fresh_forward"), (a2, {"dirty": True}, "dirty_or_unknown"), (a2, {"dirty": None}, "dirty_or_unknown"),
                              (a2, {"tip_union": "on"}, "tip_union_on"), (a2, {"q_star_sol": 50.0}, "q_star_mismatch"), (a2, {"rule": ax.Q50_RULE}, "rule_mismatch"),
                              (q, {"q_star_sol": 40.0}, "q_star_mismatch"), (q, {"rule_sha256": "0" * 64}, "rule_sha256_mismatch"), (q, {"code_sha": ax.A2_SHA}, "frozen_sha_mismatch")):
            self.assertEqual(ax.start_record_problem(book, start_rec(book, **kw)), why, (book.bot_id, kw))
        self.assertEqual(ax.start_record_problem(a2, {k: v for k, v in start_rec(a2).items() if k != "dirty"}), "dirty_or_unknown")
        self.assertEqual(ax.start_record_problem(q, {k: v for k, v in start_rec(q).items() if k != "q_star_sol"}), "q_star_mismatch")

    def test_a_bad_start_record_latches_feed_break_and_buys_stop_even_in_dry(self):
        e = self.env()
        e.ex.decide_row(start_rec(e.ex.abook, code_sha="0" * 40))
        self.assertIn(ax.H_FEED_BREAK, e.ex.counters.halts)
        e.fire()
        self.assertEqual(e.ledger("decision"), [])
        self.assertIn(ax.H_FEED_BREAK, e.refusals())

    def test_no_start_record_no_buy(self):
        e = self.env(verified=False)
        e.fire()
        self.assertEqual(e.refusals(), [ax.REFUSAL_UNVERIFIED])
        e.ex.decide_row(start_rec(e.ex.abook))
        self.assertTrue(e.ex.feed_verified)

    def test_a_later_bad_start_record_unverifies_and_latches(self):
        e = self.env(verified=False)
        e.ex.decide_row(start_rec(e.ex.abook))
        e.ex.decide_row(start_rec(e.ex.abook, dirty=True))
        self.assertFalse(e.ex.feed_verified)
        self.assertIn(ax.H_FEED_BREAK, e.ex.counters.halts)


class QTests(ACase):
    def test_45_sol_refused_for_a2_accepted_for_q50(self):
        q45 = 45 * 10**9
        e = self.env()
        e.fire(q_lamports=q45)
        self.assertEqual(e.refusals(), [ax.REFUSAL_Q])
        self.book_id = Q50
        e2 = self.env(state_dir=str(self.tmp / "s2"), intents_file=str(self.tmp / "i2.jsonl"))
        e2.fire(q_lamports=q45)
        self.assertEqual(e2.refusals(), [])
        self.assertEqual(len(e2.ledger("decision")), 1)

    def test_50_1_refused_for_q50_and_50_accepted(self):
        self.book_id = Q50
        e = self.env()
        e.fire(q_lamports=50_100_000_000)
        self.assertEqual(e.refusals(), [ax.REFUSAL_Q])
        e3 = self.env(state_dir=str(self.tmp / "s3"), intents_file=str(self.tmp / "i3.jsonl"))
        e3.fire(q_lamports=50 * 10**9)
        self.assertEqual(e3.refusals(), [])

    def test_a2_40_sol_still_accepted(self):
        e = self.env()
        e.fire(q_lamports=40 * 10**9)
        self.assertEqual(e.refusals(), [])


class FeedScreenTests(ACase):
    def go(self, e, **kw):
        r = shadow_trigger(e.clock, **kw)
        return e.ex.decide_row(r)

    def test_attempt_and_parser_refusal(self):
        e = self.env()
        self.assertEqual(self.go(e), ("keep", "attempt"))
        self.assertEqual(self.go(e, pool="P2", v_print=None), ("keep", "bad_intent:v_print"))

    def test_look2_sealed_refused_and_counted_and_alerted(self):
        e = self.env()
        self.assertEqual(self.go(e, h5_look2_sealed=True), ("drop", ax.REFUSAL_LOOK2))
        self.assertEqual(self.go(e, pool="P3", h5_look2_sealed=1), ("drop", ax.REFUSAL_LOOK2))  # fails closed on a non-bool
        self.assertEqual(e.ex.window_counts[ax.REFUSAL_LOOK2], 2)
        self.assertTrue(e.alerts(ax.REFUSAL_LOOK2))

    def test_s0_window(self):
        e = self.env()
        e.ex.abook = dataclasses.replace(e.ex.abook, clock_start_ms=1_000_000)
        e.ex._start_ms = 0
        t0 = e.clock()
        self.assertEqual(self.go(e, t_detect_ms=2_000_000, s0_t_recv_ms=999_999)[1], ax.REFUSAL_S0_WINDOW)
        self.assertEqual(self.go(e, pool="P4", t_detect_ms=2_000_000, s0_t_recv_ms=ax.SEAL_END_MS)[1], ax.REFUSAL_S0_WINDOW)
        self.assertEqual(self.go(e, pool="P5", t_detect_ms=2_000_000, s0_t_recv_ms=1_000_000)[1], "attempt")
        self.assertEqual(self.go(e, pool="P6", t_detect_ms=999_999, s0_t_recv_ms=1_500_000)[1], ax.REFUSAL_DECISION_WINDOW)
        self.assertEqual(self.go(e, pool="P7", t_detect_ms=ax.SEAL_END_MS)[1], ax.REFUSAL_DECISION_WINDOW)
        del t0

    def test_v2_and_q50_books_carry_the_scorers_60_s_s0_margin(self):
        for bid in (A2V2, Q50):
            self.book_id = bid
            e = self.env()
            e.ex.abook = dataclasses.replace(e.ex.abook, clock_start_ms=1_000_000)
            e.ex._start_ms = 0
            self.assertEqual(self.go(e, t_detect_ms=2_000_000, s0_t_recv_ms=1_059_999)[1], ax.REFUSAL_S0_WINDOW, bid)
            self.assertEqual(self.go(e, pool="P2", t_detect_ms=2_000_000, s0_t_recv_ms=1_060_000)[1], "attempt", bid)
            self.assertEqual(self.go(e, pool="P3", t_detect_ms=2_000_000, s0_t_recv_ms=None)[1], ax.REFUSAL_S0_WINDOW, bid)

    def test_suppressed_stub_stays_refused_by_the_parser(self):
        e = self.env()
        self.assertEqual(e.ex.decide_row({"type": "trigger", "variant": "pv", "pool": "P", "slot": 1, "suppressed": True, "t_detect_ms": e.clock()})[1], "bad_intent:suppressed")

    def test_duplicate_pool_is_not_a_second_attempt(self):
        e = self.env()
        self.assertEqual(self.go(e)[1], "attempt")
        self.assertEqual(self.go(e), ("keep_dup", "duplicate_pool"))

    def test_intent_tick_applies_the_screen_to_the_files_lines(self):
        e = self.env(verified=False)
        p = Path(e.conf["intents_file"])
        p.write_text("")
        e.ex.intent_tick()  # the first look at a file only marks its end
        rows = [start_rec(e.ex.abook), shadow_trigger(e.clock, h5_look2_sealed=True), shadow_trigger(e.clock, pool=POOL)]
        with open(p, "a") as f:
            f.write("\n".join(json.dumps(r) for r in rows) + "\n")
        e.ex.abook = dataclasses.replace(e.ex.abook, clock_start_ms=0)
        e.ex.intent_tick()
        self.assertTrue(e.ex.feed_verified)
        self.assertEqual(e.ex.window_counts, {ax.REFUSAL_LOOK2: 1})
        self.assertEqual(len(e.ledger("decision")), 1)
        self.assertIs(h.tail_lines, h.tail_lines)
        self.assertEqual(h.tail_lines.__name__, "tail_lines")  # the swap is undone


class V2FeedTests(ACase):
    book_id = A2V2

    def test_no_slice_taken_means_no_buy(self):
        e = self.env()
        e.ex.slices_taken = 0
        e.fire()
        self.assertEqual(e.refusals(), [ax.REFUSAL_V2_FEED])

    def test_the_shadows_boost_slice_record_is_taken_by_the_executors_v2_reader(self):
        """Format proof (item 6) from the frozen shadow engine's own output: every record it writes passes the executor's v2 reader conditions."""
        _eng, out = slicetest.go(slicetest.tape())
        recs = slicetest.types(out, "boost_slice")
        self.assertTrue(recs)
        e = self.env()
        e.ex.slices_taken = 0
        for r in recs:  # the engine fixture's feed rows carry no event_index (null): the executor's reader would ignore them, so v2 stays refused
            e.ex.decide_row(r)
        self.assertEqual(e.ex.slices_taken, 0)
        recs = [dict(r, event_index=i) for i, r in enumerate(recs)]  # a live feed row with its event_index (arena_feed rows carry an int one)
        for r in recs:
            e.ex.decide_row(r)
        self.assertEqual(e.ex.slices_taken, len(recs))
        r0 = dict(recs[0], mint=MINT, pool=POOL)
        e.ex.on_boost_slice(r0)
        self.assertIn(MINT, e.ex._v2_slices)
        self.assertEqual(e.ledger("v2_slice_ignored"), [])
        # and a record without the live fields is not counted
        self.assertFalse(ax.ArenaExecutor._slice_format_ok({k: v for k, v in r0.items() if k != "event_index"}))
        self.assertFalse(ax.ArenaExecutor._slice_format_ok(dict(r0, boost_src="behavioural")))


class FidelityTests(ACase):
    def test_decision_row_carries_the_join_key_and_no_outcome(self):
        e = self.env()
        e.fire()
        d = e.ledger("decision")[0]
        self.assertEqual((d["bot_id"], d["frozen_sha"], d["trigger_key"]), (A2, ax.A2_SHA, [MINT, POOL, TRIG_SLOT]))
        for k in ("decision_ms", "plan"):
            self.assertIn(k, d)
        self.assertFalse(d["sent"])
        banned = ("pnl", "profit", "proceeds", "realized", "exit_quote", "exit_price", "sell_quote", "outcome")
        flat = json.dumps(d).lower()
        self.assertFalse([b for b in banned if b in flat], flat)

    def test_dry_sell_row_has_slots_and_no_quote_or_pnl(self):
        e = self.env()
        e.fire()
        e.at_slot(e.plan()["send_slot"])
        s = e.ledger("dry_sell")[0]
        self.assertEqual((s["bot_id"], s["frozen_sha"]), (A2, ax.A2_SHA))
        for k in ("est_slot", "send_slot", "land_slot", "late_slots"):
            self.assertIn(k, s)
        flat = json.dumps(s).lower()
        self.assertFalse([b for b in ("pnl", "proceeds", "realized", "quote", "profit") if b in flat], flat)

    def test_max_trigger_age_is_at_most_3_5_s(self):
        self.assertLessEqual(self.env().ex.h5.max_trigger_age_ms, 3500)
        self.assertEqual(self.env().ex.h5.max_trigger_age_ms, h.PRESEND_MAX_TRIGGER_AGE_MS)

    def test_tiers_unchanged_t2_is_the_top(self):
        self.assertEqual(set(h.TIERS), {"T0", "T1", "T2"})
        self.assertEqual(h.TIERS["T2"]["stake_lamports"] if isinstance(h.TIERS["T2"], dict) else h.TIERS["T2"].stake_lamports, 300_000_000)


class MonitorTests(ACase):
    def test_entry_gap_after_10_fills(self):
        m = ax.FidelityMonitor()
        for _ in range(9):
            self.assertIsNone(m.note_entry_gap(500))
        self.assertEqual(m.note_entry_gap(500), ax.H_ENTRY_GAP)
        m2 = ax.FidelityMonitor()
        self.assertIsNone([m2.note_entry_gap(200) for _ in range(10)][-1])  # exactly 200 is not worse than 200

    def test_exit_gap_after_10_sells(self):
        m = ax.FidelityMonitor()
        r = [m.note_exit_gap(201) for _ in range(10)]
        self.assertEqual((r[8], r[9]), (None, ax.H_EXIT_GAP))

    def test_landing_fail_excess_after_30_attempts(self):
        m = ax.FidelityMonitor()
        out = [m.note_attempt(i >= 8) for i in range(30)]  # 8 failed of 30 = 26.7%, excess 11.7 pp
        self.assertEqual(out[28], None)
        self.assertEqual(out[29], ax.H_LANDING)
        m2 = ax.FidelityMonitor()
        self.assertIsNone([m2.note_attempt(i >= 7) for i in range(30)][-1])  # 7 of 30 = 23.3%, excess 8.3 pp

    def test_two_late_send_trips(self):
        m = ax.FidelityMonitor()
        self.assertIsNone(m.note_sell_send(100, 103))  # 3 slots is on time
        self.assertIsNone(m.note_sell_send(100, 104))
        self.assertEqual(m.note_sell_send(100, 105), ax.H_LATE_SEND)
        self.assertEqual(m.late_slots_distribution(), {"3": 1, "4": 1, "5": 1})

    def test_two_stuck_sells(self):
        m = ax.FidelityMonitor()
        self.assertIsNone(m.note_stuck("a"))
        self.assertIsNone(m.note_stuck("a"))
        self.assertEqual(m.note_stuck("b"), ax.H_STUCK2)

    def test_latched_arena_halt_stops_a_dry_buy(self):
        e = self.env()
        e.ex.latch_external(ax.H_A3, why="pins_changed")
        e.fire()
        self.assertEqual(e.refusals(), [ax.H_A3])
        with self.assertRaises(ValueError):
            e.ex.latch_external("nonsense")

    def test_entry_gap_hook_on_a_landed_buy(self):
        e = self.env(live=False)
        e.fire()
        # dry run lands nothing; the hook is exercised through the monitor on the real _finish_buy path in the live-path test below
        self.assertEqual(list(e.ex.monitor.entry_gaps), [])


class LiveShapePathTests(ACase):
    """The keyed code path with a throwaway Keypair and the fake RPC (LIVE_BUILT patched on for the test only; nothing is sent anywhere)."""

    def live_env(self):
        write_canary_record(self.tmp / "repo")
        (self.tmp / "FREEZE.md").write_text("x")
        e = AEnv(self.tmp, live=True, book_id=self.book_id, replay_only=True, end_ms=ax.SEAL_END_MS, funded_lamports=500_000_000)
        e.ex.feed_verified, e.ex._start_ms, e.ex.slices_taken = True, 0, 1
        e.ex.abook = dataclasses.replace(e.ex.abook, clock_start_ms=0)
        return e

    def test_landed_buy_feeds_the_entry_gap_and_attempt_counters(self):
        with mock.patch.object(ax, "LIVE_BUILT", True):
            e = self.live_env()
            e.open_position()
        self.assertEqual(e.ex.monitor.attempts, 1)
        self.assertEqual(len(e.ex.monitor.entry_gaps), 1)

    def test_sell_goes_out_within_one_slot_of_plan_send_slot_at_200_ms_and_does_not_wait_on_poll_s(self):
        with mock.patch.object(ax, "LIVE_BUILT", True):
            e = self.live_env()
            self.assertEqual(e.conf["poll_s"], 5.0)  # the probe's poll interval is in the config; the exit must not use it
            e.open_position()
            plan = e.plan()
            n0 = len(e.rpc.sent)
            sent_at = None
            while e.rpc.slot < plan["send_slot"] + 10 and sent_at is None:
                e.clock.t += int(e.rpc.slot_ms)  # one tick per slot: finer than poll_s
                e.ex.exit_tick(e.clock())
                if len(e.rpc.sent) > n0:
                    sent_at = e.rpc.slot
        self.assertIsNotNone(sent_at)
        self.assertLessEqual(sent_at - plan["send_slot"], 1)
        self.assertGreaterEqual(sent_at - plan["send_slot"], 0)
        self.assertEqual(e.ex.monitor.late_slots[-1], sent_at - plan["send_slot"])

    def test_sell_send_slot_at_400_ms_slots(self):
        """exit_plan arithmetic at 400 ms: the send slot is the 330 s exit minus the 0.5 s lead (ceil(0.5 / 0.4) = 2 slots), within a slot of wall time."""
        lim = h.H5Limits.from_config({"state_dir": "/x"})
        for sps in (0.2, 0.4):
            p = h.exit_plan(S0, sps, lim, 1_000_000)
            self.assertEqual(p.send_slot, S0 + int(round(330 / sps)) - h.ceil_slots(lim.send_lead_ms / 1000.0, sps))
            send_s = (p.send_slot - S0) * sps
            self.assertLessEqual(abs(send_s - (330 - lim.send_lead_ms / 1000.0)), sps)
            self.assertEqual(p.send_wall_ms, 1_000_000 + 330_000 - lim.send_lead_ms)
            self.assertLess(lim.slot_poll_ms, 400)  # the slot poll is finer than a slot at either rate; poll_s is not read by the exit path


class PriorityPinTests(ACase):
    """Item 9: the per-book priority pin. Registered books keep 55,000 / 55,000 / 150,000; a NEW p155 book sends exactly 155,000 on both legs (DEC-031 Am.8 item 2)."""

    NEW = "h5v1-p155-testbook"

    def p155_book(self) -> ax.Book:
        return dataclasses.replace(ax.BOOKS[A2], bot_id=self.NEW, priority=ax.PRIORITY_P155)

    def live_env(self, **kw):
        write_canary_record(self.tmp / "repo")
        (self.tmp / "FREEZE.md").write_text("x")
        kw.setdefault("book_id", self.book_id)
        e = AEnv(self.tmp, live=True, replay_only=True, end_ms=ax.SEAL_END_MS, **{"funded_lamports": 500_000_000, **kw})
        e.ex.feed_verified, e.ex._start_ms, e.ex.slices_taken = True, 0, 1
        e.ex.abook = dataclasses.replace(e.ex.abook, clock_start_ms=0)
        return e

    def test_registered_books_keep_their_current_priority(self):
        for bot_id, book in ax.BOOKS.items():
            self.assertEqual(book.priority, ax.PRIORITY_DEFAULT, bot_id)
            self.assertEqual((book.priority.buy, book.priority.sell, book.priority.escalated), (55_000, 55_000, 150_000), bot_id)
            self.assertEqual((book.priority.buy, book.priority.sell, book.priority.escalated),
                             (h.H5_DEFAULT["buy_priority_lamports"], h.H5_DEFAULT["sell_priority_lamports"], h.H5_DEFAULT["escalated_priority_lamports"]))

    def test_default_books_h5_limits_equal_the_base_executor_limits(self):
        for bot_id in (A2, A2V2, Q50):
            self.book_id = bot_id
            e = self.env()
            base = dataclasses.replace(h.H5Limits.from_config(e.conf), end_ms=ax.SEAL_END_MS, gap_hold_ms=ax.GAP_HOLD_MS)  # end_ms: the seal clamp; gap hold: DEC-032 s5 (10 min). Neither is a fee
            self.assertEqual(e.ex.h5, base, bot_id)
            self.assertEqual((e.ex.h5.buy_priority_lamports, e.ex.h5.sell_priority_lamports, e.ex.h5.escalated_priority_lamports), (55_000, 55_000, 150_000))

    def test_default_book_sends_55k_buy_55k_sell_150k_escalated_and_emergency(self):
        with mock.patch.object(ax, "LIVE_BUILT", True):
            e = self.live_env()
            e.open_position()
            plan = e.plan()
            e.at_slot(plan["send_slot"])
            e.at_slot(plan["escalate_slot"])
            e.at_slot(plan["deadline_slot"])
        self.assertEqual([d["priority"] for d in e.sent()], [55_000, 55_000, 150_000, 150_000])

    def test_p155_book_sends_exactly_155k_on_buy_sell_escalation_and_emergency(self):
        nb = self.p155_book()
        with mock.patch.object(ax, "LIVE_BUILT", True), mock.patch.dict(ax.BOOKS, {self.NEW: nb}):
            e = self.live_env(book_id=self.NEW, priority_pin=ax.PRIORITY_P155.as_dict())
            self.assertEqual((e.ex.h5.buy_priority_lamports, e.ex.h5.sell_priority_lamports, e.ex.h5.escalated_priority_lamports), (155_000,) * 3)
            e.open_position()
            plan = e.plan()
            e.at_slot(plan["send_slot"])
            e.at_slot(plan["escalate_slot"])
            e.at_slot(plan["deadline_slot"])
            sent = e.sent()
        self.assertEqual([d["priority"] for d in sent], [155_000] * 4)  # buy, timed sell, escalated sell, emergency sell: nothing above 155,000
        self.assertEqual(sent[0]["swap"][:8], h.tx.DISC_BUY_EXACT_QUOTE_IN)
        self.assertEqual([d["closes"] for d in sent[1:]], [2, 2, 2])  # every sell closes the token account and the WSOL account in the same tx
        start = [r for r in e.ledger("start")][-1] if e.ledger("start") else None
        if start is not None:
            self.assertEqual(start["limits"]["buy_priority_lamports"], 155_000)

    def test_p155_book_requires_the_declaration_and_it_must_match(self):
        nb = self.p155_book()
        with mock.patch.dict(ax.BOOKS, {self.NEW: nb}):
            self.assertEqual(ax.priority_refusal(nb, {}), "priority_pin_missing")
            self.assertEqual(ax.priority_refusal(nb, {"priority_pin": ax.PRIORITY_DEFAULT.as_dict()}), "priority_pin_mismatch")
            self.assertEqual(ax.priority_refusal(nb, {"priority_pin": {**ax.PRIORITY_P155.as_dict(), "sell": 150_000}}), "priority_pin_mismatch")
            self.assertIsNone(ax.priority_refusal(nb, {"priority_pin": ax.PRIORITY_P155.as_dict(), "buy_priority_lamports": 155_000}))
            self.assertEqual(ax.priority_refusal(nb, {"priority_pin": ax.PRIORITY_P155.as_dict(), "buy_priority_lamports": 55_000}), "buy_priority_lamports_not_pin")
            with self.assertRaises(SystemExit) as cm:
                ax.ArenaExecutor(None, {"book_id": self.NEW, "replay_only": True}, None, root=self.tmp)
            self.assertIn("priority_pin_missing", str(cm.exception))

    def test_default_book_refuses_a_155k_declaration_and_config_priorities(self):
        a2 = ax.BOOKS[A2]
        self.assertEqual(ax.priority_refusal(a2, {"priority_pin": ax.PRIORITY_P155.as_dict()}), "priority_pin_mismatch")
        self.assertEqual(ax.priority_refusal(a2, {"buy_priority_lamports": 155_000}), "buy_priority_lamports_not_pin")
        self.assertEqual(ax.priority_refusal(a2, {"sell_priority_lamports": 155_000}), "sell_priority_lamports_not_configurable")
        self.assertEqual(ax.priority_refusal(a2, {"escalated_priority_lamports": 155_000}), "escalated_priority_lamports_not_configurable")
        self.assertIsNone(ax.priority_refusal(a2, {"priority_pin": ax.PRIORITY_DEFAULT.as_dict()}))
        self.assertIsNone(ax.priority_refusal(a2, {}))

    def test_p155_profile_cannot_carry_an_escalation_above_155k(self):
        with self.assertRaises(ValueError):
            ax.Priority("p155", 155_000, 155_000, 150_000 + 10_000)
        with self.assertRaises(ValueError):
            ax.Priority("p155", 155_000, 150_000, 155_000)


class SellBuilderPinTests(PriorityPinTests):
    """DEC-032 section 4: the sell + close of the base ATA in one tx under the mint's token program, pinned by sha256."""

    def test_pins_match_the_source(self):
        import hashlib
        import inspect
        from tools import probe_live as pl
        from tools import pumpswap_tx as ptx
        sha = lambda b: hashlib.sha256(b).hexdigest()  # noqa: E731
        pin = ax.SELL_BUILDER_PIN
        self.assertEqual(sha(Path(h.__file__).read_bytes()), pin["file_sha256"])
        self.assertEqual(sha(inspect.getsource(h.H5Executor._arm_sell).encode()), pin["symbol_sha256"])
        self.assertEqual(sha(inspect.getsource(pl.close_token_account).encode()), pin["close_token_account_sha256"])
        self.assertEqual(sha(inspect.getsource(ptx.sell_instructions).encode()), pin["sell_instructions_sha256"])
        self.assertIs(ax.ArenaExecutor._arm_sell, h.H5Executor._arm_sell)  # the subclass does not override the builder

    def test_built_sell_is_one_tx_that_closes_the_base_ata_under_the_mints_token_program(self):
        import base64
        from solders.transaction import VersionedTransaction
        from tools import pumpswap_tx as ptx
        with mock.patch.object(ax, "LIVE_BUILT", True):
            e = self.live_env()
            pos = e.open_position()
            plan = e.plan()
            e.at_slot(plan["send_slot"])
            sells = [t for t in e.rpc.sent][1:]
            self.assertEqual(len(sells), 1)
            msg = VersionedTransaction.from_bytes(base64.b64decode(sells[0][1])).message
        keys = list(msg.account_keys)
        ixs = [(keys[i.program_id_index], bytes(i.data), [keys[a] for a in i.accounts]) for i in msg.instructions]
        closes = [x for x in ixs if x[1] == b"\x09"]
        self.assertEqual(len(closes), 2)  # WSOL account and base token account, in the same transaction
        base_close = closes[-1]
        self.assertEqual(str(base_close[0]), pos["base_tp"])  # the mint's own token program (Token or Token-2022)
        self.assertIn(pos["base_tp"], (str(ptx.TOKEN_PROGRAM), str(ptx.TOKEN_2022_PROGRAM)))
        self.assertEqual(str(base_close[2][0]), pos["base_ata"])
        self.assertEqual(ixs.index(base_close), len(ixs) - 1)  # after the swap
        self.assertTrue(any(x[1][:8] == ptx.DISC_SELL for x in ixs))


class C0PinTests(ACase):
    """Gap (a): the C0 canary table is pinned in code; the shared TIER file may only lower it."""

    def tier_limits(self, tier):
        e = self.env()
        (self.tmp / "state").mkdir(parents=True, exist_ok=True)
        (self.tmp / "state" / "TIER").write_text(tier)
        e.ex._refresh_tier(e.clock())
        return e.ex.h5

    def test_c0_table_is_the_dec032_one(self):
        self.assertEqual(ax.C0_TIER, {"stake_lamports": 50_000_000, "max_open": 2, "max_trades_per_day": 30,
                                      "daily_loss_lamports": 100_000_000, "total_loss_lamports": 200_000_000})

    def test_t1_and_t2_files_cannot_raise_stake_or_open_count_or_stops(self):
        for tier in ("T1", "T2"):
            lim = self.tier_limits(tier)
            self.assertEqual(self.sub(lim), ax.C0_TIER, tier)
            self.assertEqual(self.ex_tier(tier), tier)  # the file was read as that tier: the clamp, not the file, held the line

    def test_t0_file_lowers_below_c0(self):
        lim = self.tier_limits("T0")
        self.assertEqual(lim.stake_lamports, h.TIERS["T0"]["stake_lamports"])
        self.assertLess(lim.stake_lamports, ax.C0_TIER["stake_lamports"])

    def test_config_can_lower_but_not_raise(self):
        e = self.env(stake_lamports=10_000_000)
        self.assertEqual(e.ex.h5.stake_lamports, 10_000_000)
        e = self.env(stake_lamports=900_000_000, max_open=9)
        (self.tmp / "state").mkdir(parents=True, exist_ok=True)
        (self.tmp / "state" / "TIER").write_text("T2")
        e.ex._refresh_tier(e.clock())
        self.assertLessEqual(e.ex.h5.stake_lamports, ax.C0_TIER["stake_lamports"])
        self.assertLessEqual(e.ex.h5.max_open, 2)

    def test_t2_file_does_not_let_a_third_buy_through(self):
        e = self.env()
        (self.tmp / "state").mkdir(parents=True, exist_ok=True)
        (self.tmp / "state" / "TIER").write_text("T2")
        self.assertEqual(e.ex.h5.max_open, 2)

    @staticmethod
    def sub(lim):
        return {k: getattr(lim, k) for k in ax.C0_TIER}

    def ex_tier(self, tier):
        e = self.env()
        (self.tmp / "state").mkdir(parents=True, exist_ok=True)
        (self.tmp / "state" / "TIER").write_text(tier)
        e.ex._refresh_tier(e.clock())
        return e.ex.tier


class GapHoldTests(ACase):
    """Gap (c): the feed-gap hold is 10 minutes (DEC-032 s5 item 2)."""

    def test_hold_is_ten_minutes_and_config_cannot_shorten_it(self):
        self.assertEqual(ax.GAP_HOLD_MS, 600_000)
        self.assertEqual(self.env().ex.h5.gap_hold_ms, 600_000)
        self.assertEqual(self.env(gap_hold_ms=1).ex.h5.gap_hold_ms, 600_000)

    def check(self, flags):
        e = self.env()
        n0, d0 = len(e.refusals()), len(e.ledger("decision"))
        self.assertEqual(e.ex.decide_row({"type": "gap", "kind": "reconnect", "flags_pools": flags}), ("keep", None))
        e.jump(599_000)
        e.fire()
        self.assertEqual(e.refusals()[n0:], ["feed_gap"], flags)  # 599 s after the gap record: held
        e.jump(1_000)
        e.fire()
        self.assertEqual(e.refusals()[n0:], ["feed_gap"], flags)  # 600 s after: the hold is over, no second gap refusal
        self.assertEqual(len(e.ledger("decision")) - d0, 1, flags)

    def test_any_gap_record_holds_buys_for_ten_minutes(self):
        self.check(True)

    def test_a_redundant_reconnect_gap_record_holds_too(self):
        self.check(False)  # the base class would skip the hold for flags_pools false


class SellReserveTests(ACase):
    """Gap (d): the sell reserve covers 10 sends at the p155 worst case (1.6M)."""

    def test_reserve_is_at_least_1_6m_for_every_book_and_follows_the_pin(self):
        for b in ax.BOOKS.values():
            self.assertGreaterEqual(ax.sell_reserve_lamports(b), 1_600_000)
        p155 = dataclasses.replace(ax.BOOKS[A2], priority=ax.PRIORITY_P155)
        self.assertEqual(ax.sell_reserve_lamports(p155), 10 * (155_000 + 5_000))
        high = dataclasses.replace(ax.BOOKS[A2], priority=ax.Priority("default", 55_000, 55_000, 400_000))
        self.assertEqual(ax.sell_reserve_lamports(high), 10 * 405_000)


class CommittedLossTests(ACase):
    """Gap (b): DEC-032 s3 committed loss and the wallet floor, in code (QP 10-23)."""

    def live_env(self, **kw):
        write_canary_record(self.tmp / "repo")
        (self.tmp / "FREEZE.md").write_text("x")
        kw.setdefault("book_id", self.book_id)
        kw.setdefault("funded_lamports", 500_000_000)
        e = AEnv(self.tmp, live=True, replay_only=True, end_ms=ax.SEAL_END_MS, **kw)
        e.ex.feed_verified, e.ex._start_ms, e.ex.slices_taken = True, 0, 1
        e.ex.abook = dataclasses.replace(e.ex.abook, clock_start_ms=0)
        e.rpc.balance = 10 * 10**9
        return e

    def worst(self, e, stake):
        return stake + 55_000 + 5_000 + h.RENT_RESERVE_LAMPORTS + ax.sell_reserve_lamports(e.ex.abook)

    def test_open_position_counts_stake_buy_fee_rent_and_sell_reserve(self):
        with mock.patch.object(ax, "LIVE_BUILT", True):
            e = self.live_env()
            self.assertEqual(e.ex.committed_loss_lamports(), 0)
            pos = e.open_position()
            extra = int(pos.get("extra_cost") or 0)
            self.assertEqual(e.ex.committed_loss_lamports(), self.worst(e, int(pos["spend"])) + extra)
            self.assertGreater(e.ex.committed_loss_lamports(), int(pos["spend"]) + 2_100_000 + 1_600_000)

    def test_in_flight_buy_counts_before_it_lands(self):
        with mock.patch.object(ax, "LIVE_BUILT", True):
            e = self.live_env()
            e.fire()
            self.assertIn(MINT, e.ex.state.pending)
            self.assertEqual(e.ex.committed_loss_lamports(), self.worst(e, e.ex.h5.stake_lamports))

    def test_realized_loss_is_counted_and_a_profit_is_floored_at_zero(self):
        with mock.patch.object(ax, "LIVE_BUILT", True):
            e = self.live_env()
            e.ex.state.realized_lamports = -7_000_000
            self.assertEqual(e.ex.committed_loss_lamports(), 7_000_000)
            e.ex.state.realized_lamports = 9_000_000
            self.assertEqual(e.ex.committed_loss_lamports(), 0)

    def test_pre_send_check_refuses_when_committed_plus_this_buy_passes_the_total_stop(self):
        with mock.patch.object(ax, "LIVE_BUILT", True):
            e = self.live_env(total_loss_lamports=22_000_000)  # the stake alone (20M) would pass the base check; the stake plus fees, rent and reserve does not
            e.fire()
            self.assertEqual(e.refusals(), ["committed_loss_stop"])
            self.assertEqual(e.rpc.sent, [])

    def test_realized_loss_since_first_send_enters_the_pre_send_check(self):
        with mock.patch.object(ax, "LIVE_BUILT", True):
            e = self.live_env(total_loss_lamports=35_000_000)
            e.ex.state.realized_lamports = -12_000_000
            e.fire()
            self.assertEqual(e.refusals(), ["committed_loss_stop"])
            e.ex.state.realized_lamports = -10_000_000
            e.fire()
            self.assertEqual(len(e.ledger("decision")), 1)

    def test_live_buy_rechecks_right_before_building_even_if_the_budget_stop_was_bypassed(self):
        with mock.patch.object(ax, "LIVE_BUILT", True):
            e = self.live_env(total_loss_lamports=22_000_000)
            with mock.patch.object(ax.ArenaExecutor, "_budget_stop", return_value=None):
                e.fire()
            self.assertEqual(e.refusals(), ["committed_loss_stop"])
            self.assertEqual(e.rpc.sent, [])

    def test_floor_is_funded_minus_a_quarter_sol_never_below_0_05(self):
        for funded, floor in ((350_000_000, 100_000_000), (500_000_000, 250_000_000), (1_000_000_000, 750_000_000)):
            with mock.patch.object(ax, "LIVE_BUILT", True):
                self.assertEqual(self.live_env(funded_lamports=funded).ex.h5.wallet_floor_lamports, floor)
        self.assertEqual(ax.wallet_floor_lamports(300_000_000, 50_000_000), 50_000_000)  # 0.30 funded: 0.05, the minimum
        self.assertEqual(ax.wallet_floor_lamports(None, 50_000_000), 50_000_000)

    def test_config_cannot_lower_the_floor_but_can_raise_it(self):
        with mock.patch.object(ax, "LIVE_BUILT", True):
            self.assertEqual(self.live_env(wallet_floor_lamports=60_000_000).ex.h5.wallet_floor_lamports, 250_000_000)
            self.assertEqual(self.live_env(wallet_floor_lamports=300_000_000).ex.h5.wallet_floor_lamports, 300_000_000)

    def test_balance_check_counts_open_stakes_again_and_the_floor(self):
        with mock.patch.object(ax, "LIVE_BUILT", True):
            e = self.live_env(funded_lamports=500_000_000)
            floor, stake = e.ex.h5.wallet_floor_lamports, e.ex.h5.stake_lamports
            need0 = stake + 60_000 + 2_100_000 + ax.sell_reserve_lamports(e.ex.abook) + floor
            e.rpc.balance = need0 - 1
            e.fire()
            self.assertEqual(e.refusals(), ["balance_floor"])
            e.rpc.balance = need0
            e.ex._bal = None
            e.fire()
            self.assertEqual(len(e.ledger("decision")), 1)
            # one pending/open position: the second buy needs that stake once more, plus a second sell reserve
            need1 = need0 + int(e.ex.state.pending[MINT]["spend"]) + ax.sell_reserve_lamports(e.ex.abook)
            e.rpc.balance = need1 - 1
            e.ex._bal = None
            self.assertEqual(e.ex._balance_refusal(e.clock()), "balance_floor")
            e.rpc.balance = need1
            e.ex._bal = None
            self.assertIsNone(e.ex._balance_refusal(e.clock()))

    def test_live_start_needs_funded_lamports_in_range(self):
        with mock.patch.object(ax, "LIVE_BUILT", True):
            for bad, why in ((None, "funded_lamports_missing"), (349_999_999, "funded_lamports_out_of_range"), (1_000_000_001, "funded_lamports_out_of_range"),
                             (True, "funded_lamports_out_of_range"), (5e8, "funded_lamports_out_of_range")):
                kw = {"funded_lamports": bad}
                with self.assertRaises(SystemExit) as cm:
                    write_canary_record(self.tmp / "repo")
                    AEnv(self.tmp, live=True, book_id=self.book_id, replay_only=True, end_ms=ax.SEAL_END_MS, **kw)
                self.assertIn(why, str(cm.exception))


class RollingWindowTests(ACase):
    """Gap (e): P1 and P2 are rolling (DEC-032 s5), not running totals."""

    def test_a_bad_stretch_after_a_good_one_still_fires_the_entry_gap(self):
        m = ax.FidelityMonitor()
        got = [m.note_entry_gap(0) for _ in range(10)] + [m.note_entry_gap(210) for _ in range(20)]
        self.assertIn(ax.H_ENTRY_GAP, got)
        cumulative_mean = (20 * 210) / 30
        self.assertLess(cumulative_mean, ax.ENTRY_GAP_BPS)  # a running total would have stayed silent

    def test_first_ten_then_rolling_twenty_boundaries(self):
        m = ax.FidelityMonitor()
        self.assertEqual([m.note_entry_gap(500) for _ in range(9)], [None] * 9)  # fewer than 10 fills: no verdict
        self.assertEqual(m.note_entry_gap(500), ax.H_ENTRY_GAP)

    def test_old_bad_fills_age_out(self):
        m = ax.FidelityMonitor()
        for _ in range(9):
            m.note_entry_gap(1000)
        out = [m.note_entry_gap(0) for _ in range(40)]
        self.assertEqual(out[-1], None)  # the window has moved past the bad stretch
        self.assertEqual(len(m.entry_gaps), 20)

    def test_landing_fail_excess_is_over_the_last_30_attempts(self):
        m = ax.FidelityMonitor()
        got = [m.note_attempt(True) for _ in range(30)] + [m.note_attempt(i >= 9) for i in range(30)]  # then 9 failures in the next 30
        self.assertEqual(got[:30], [None] * 30)
        self.assertIn(ax.H_LANDING, got[30:])
        self.assertEqual((m.failed, m.attempts), (9, 60))
        self.assertLessEqual(100.0 * (m.failed / m.attempts - ax.PAPER_FAIL_RATE), ax.LANDING_EXCESS_PP)  # a running total would not have fired

    def test_landing_old_failures_age_out(self):
        m = ax.FidelityMonitor()
        for _ in range(30):
            m.note_attempt(False)
        out = [m.note_attempt(True) for _ in range(30)]
        self.assertIsNone(out[-1])
        self.assertEqual(len(m.recent_attempts), 30)


class MintScreenTests(ACase):
    """Gap (f, second half): a pre-send refusal of a close-blocked mint (DEC-032 s3 Venue; DEC-031 Am.2 item 6)."""

    live_env = CommittedLossTests.live_env
    worst = CommittedLossTests.worst

    def refused_with(self, value, expect):
        with mock.patch.object(ax, "LIVE_BUILT", True):
            e = self.live_env()
            e.rpc.accounts[MINT] = value
            e.fire()
        self.assertEqual(e.refusals(), [f"close_blocked_mint:{expect}"])
        self.assertEqual(e.rpc.sent, [])
        self.assertEqual(e.ex.close_blocked_refused, 1)

    def test_freeze_authority_is_refused_before_anything_is_sent(self):
        self.refused_with(mint_value(freeze=True), "freeze_authority")

    def test_token2022_freeze_authority_is_refused_too(self):
        self.refused_with(mint_value(ax.mf.TOKEN_2022_PROGRAM, freeze=True, ext_types=(18, 19)), "freeze_authority")

    def risky(self, t):
        self.refused_with(mint_value(ax.mf.TOKEN_2022_PROGRAM, ext_types=(18, t)), "extension_outside_allowlist")

    def test_token2022_transfer_fee_is_refused(self):
        self.risky(1)

    def test_token2022_transfer_hook_is_refused(self):
        self.risky(14)

    def test_token2022_permanent_delegate_is_refused(self):
        self.risky(12)

    def test_token2022_mint_close_authority_is_refused(self):
        self.risky(3)

    def test_unknown_owner_program_is_refused(self):
        self.refused_with(mint_value(owner="11111111111111111111111111111111"), "unknown_owner_program")

    def test_a_failed_or_missing_fetch_is_refused_deny_by_default(self):
        self.refused_with(None, "fetch_failed")

    def test_token2022_with_only_the_allowlisted_extensions_passes(self):
        with mock.patch.object(ax, "LIVE_BUILT", True):
            e = self.live_env()
            e.rpc.accounts[MINT] = mint_value(ax.mf.TOKEN_2022_PROGRAM, ext_types=(18, 19))
            e.fire()
        self.assertEqual(e.refusals(), [])
        self.assertEqual(len(e.ledger("decision")), 1)
        self.assertIsNone(e.ex.mint_screens[MINT])

    def test_classic_spl_mint_without_freeze_passes(self):
        with mock.patch.object(ax, "LIVE_BUILT", True):
            e = self.live_env()
            e.fire()
        self.assertEqual(len(e.ledger("decision")), 1)

    def test_extension_names_used_by_the_allowlist_are_the_real_ones(self):
        self.assertEqual((ax.mf.EXT_NAMES[18], ax.mf.EXT_NAMES[19]), ("metadataPointer", "tokenMetadata"))


class StrandTests(ACase):
    """Gap (f, first half): H_STRAND fires. close_blocked is set when the mint is re-read after the buy lands; strand when the sell is stuck past the deadline."""

    live_env = CommittedLossTests.live_env

    def test_freeze_authority_added_after_the_pre_send_screen_marks_the_position_and_latches(self):
        with mock.patch.object(ax, "LIVE_BUILT", True):
            e = self.live_env()
            e.fire()
            e.rpc.accounts[MINT] = mint_value(freeze=True)  # set between the screen and the landing
            e.land_buy()
            pos = e.ex.state.open[MINT]
            self.assertTrue(pos["close_blocked"])
            self.assertEqual(pos["close_blocked_why"], "freeze_authority")
            self.assertNotIn(ax.H_STRAND, e.ex.counters.halts)
            e.ex.exit_tick(e.clock())
            self.assertIn(ax.H_STRAND, e.ex.counters.halts)
            e.fire(mint="Other")  # the latch stops further buys (any reason; the arena halt is checked first)
            self.assertEqual(e.refusals()[-1], ax.H_STRAND)

    def test_a_clean_mint_leaves_the_position_unmarked(self):
        with mock.patch.object(ax, "LIVE_BUILT", True):
            e = self.live_env()
            pos = e.open_position()
            e.ex.exit_tick(e.clock())
        self.assertNotIn("close_blocked", pos)
        self.assertNotIn("strand", pos)
        self.assertNotIn(ax.H_STRAND, e.ex.counters.halts)

    def test_stuck_sell_past_the_emergency_attempt_is_a_strand(self):
        with mock.patch.object(ax, "LIVE_BUILT", True):
            e = self.live_env()
            pos = e.open_position()
            pos["stuck"] = True
            e.ex.exit_tick(e.clock())
            self.assertNotIn(ax.H_STRAND, e.ex.counters.halts)  # stuck alone is the stuck_position / two_stuck_sells path, not a strand yet
            pos["emergency_attempted"] = True
            e.ex.exit_tick(e.clock())
            self.assertTrue(pos["strand"])
            self.assertIn(ax.H_STRAND, e.ex.counters.halts)

    def test_an_abandoned_position_is_a_strand(self):
        with mock.patch.object(ax, "LIVE_BUILT", True):
            e = self.live_env()
            pos = e.open_position()
            pos["abandoned"] = True
            e.ex.exit_tick(e.clock())
            self.assertIn(ax.H_STRAND, e.ex.counters.halts)


class ExternalLatchTests(ACase):
    """Gap (g): latch_external is wired to the A3 record and the exit-gap input."""

    def a3_env(self, rec_text):
        f = self.tmp / "a3.jsonl"
        f.write_text(rec_text)
        return self.env(a3_record_file=str(f)), f

    def test_clean_record_latches_nothing_and_the_newest_line_decides(self):
        e, f = self.a3_env(json.dumps(a3_rec(pins_changed=True)) + "\n" + json.dumps(a3_rec()) + "\n")
        e.ex.a3_tick(e.clock())
        self.assertNotIn(ax.H_A3, e.ex.counters.halts)

    def test_pins_changed_latches_the_a3_halt_and_refuses_buys(self):
        e, f = self.a3_env(json.dumps(a3_rec(pins_changed=True)) + "\n")
        e.ex.exit_tick(e.clock())
        self.assertIn(ax.H_A3, e.ex.counters.halts)
        self.assertEqual(e.ex.counters.halts[ax.H_A3]["flags"], ["pins_changed"])
        e.fire()
        self.assertEqual(e.refusals(), [ax.H_A3])

    def test_program_changed_warn_latches(self):
        e, f = self.a3_env(json.dumps(a3_rec(program_changed=True)) + "\n")
        e.ex.a3_tick(e.clock())
        self.assertEqual(e.ex.counters.halts[ax.H_A3]["flags"], ["program_changed"])

    def test_any_other_halt_flag_latches_via_the_intent_tick(self):
        e, f = self.a3_env(json.dumps(a3_rec(boost_disabled=True)) + "\n")
        e.ex.intent_tick()
        self.assertIn(ax.H_A3, e.ex.counters.halts)

    def test_unreadable_or_flagless_record_fails_closed(self):
        for text in ("", "not json\n", json.dumps({"run_utc": "x"}) + "\n"):
            e, f = self.a3_env(text)
            e.ex.a3_tick(e.clock())
            self.assertIn(ax.H_A3, e.ex.counters.halts, text)
            (self.tmp / "state" / "h5-counters.json").unlink(missing_ok=True)

    def test_a_malformed_flag_is_not_a_clear(self):
        rec = a3_rec()
        rec["halt"]["flags"]["boost_share_low"] = {"evaluated": True}
        e, f = self.a3_env(json.dumps(rec) + "\n")
        e.ex.a3_tick(e.clock())
        self.assertEqual(e.ex.counters.halts[ax.H_A3]["flags"], ["boost_share_low"])

    def test_the_poll_is_throttled(self):
        e, f = self.a3_env(json.dumps(a3_rec()) + "\n")
        e.ex.a3_tick(e.clock())
        f.write_text(json.dumps(a3_rec(pins_changed=True)) + "\n")
        e.ex.a3_tick(e.clock() + 1_000)
        self.assertNotIn(ax.H_A3, e.ex.counters.halts)
        e.ex.a3_tick(e.clock() + ax.A3_POLL_MS)
        self.assertIn(ax.H_A3, e.ex.counters.halts)

    def test_a_stale_record_fails_closed_and_the_limit_is_36h(self):
        self.assertEqual(ax.A3_MAX_AGE_MS, 36 * 3_600_000)
        e = self.env()
        now = e.clock()
        iso = lambda ms: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ms / 1000))  # noqa: E731
        for age, ok in ((ax.A3_MAX_AGE_MS - 1_000, True), (ax.A3_MAX_AGE_MS + 2_000, False)):
            f = self.tmp / f"a3-{age}.jsonl"
            f.write_text(json.dumps(a3_rec(run_utc=iso(now - age))) + "\n")
            why, problem = ax.a3_record_problem(f, now)
            self.assertEqual((why, problem), ([], None) if ok else ([], "a3_record_stale"), age)

    def test_stale_record_latches_through_the_tick(self):
        e = self.env()
        f = self.tmp / "a3old.jsonl"
        f.write_text(json.dumps(a3_rec(run_utc="2000-01-01T00:00:00Z")) + "\n")
        e.ex.cfg_a3_file = str(f)
        e.ex.a3_tick(e.clock() + 10**12)
        self.assertEqual(e.ex.counters.halts[ax.H_A3]["why"], "a3_record_stale")

    def test_an_unevaluated_flag_is_not_a_clear(self):
        rec = a3_rec()
        rec["halt"]["flags"]["boost_slices_shift"]["evaluated"] = False
        e, f = self.a3_env(json.dumps(rec) + "\n")
        e.ex.a3_tick(e.clock())
        self.assertEqual(e.ex.counters.halts[ax.H_A3]["flags"], ["boost_slices_shift"])

    def test_a_record_without_run_utc_fails_closed(self):
        rec = a3_rec()
        del rec["run_utc"]
        f = self.tmp / "norun.jsonl"
        f.write_text(json.dumps(rec) + "\n")
        self.assertEqual(ax.a3_record_problem(f, 0), ([], "a3_record_no_run_utc"))

    def test_missing_exit_gap_file_latches(self):
        e = self.env(exit_gap_file=str(self.tmp / "nope.jsonl"))
        e.ex.exit_gap_tick()
        self.assertEqual(e.ex.counters.halts[ax.H_EXIT_GAP]["why"], "exit_gap_file_unreadable")

    def test_no_exit_gap_file_configured_does_nothing(self):
        e = self.env()
        e.ex.exit_gap_tick()
        self.assertNotIn(ax.H_EXIT_GAP, e.ex.counters.halts)

    def test_a_partial_last_line_waits_for_its_newline(self):
        g = self.tmp / "gaps2.jsonl"
        full = "".join(json.dumps({"mint": f"m{i}", "exit_gap_bps": 500}) + "\n" for i in range(9))
        g.write_text(full + '{"mint": "m9", "exit_gap_')
        e = self.env(exit_gap_file=str(g))
        e.ex.exit_gap_tick()
        self.assertEqual(len(e.ex.monitor.exit_gaps), 9)
        self.assertEqual(e.ex._exit_gap_off, len(full.encode()))
        self.assertEqual(e.alerts("exit_gap_row_unreadable"), [])
        g.write_text(full + json.dumps({"mint": "m9", "exit_gap_bps": 500}) + "\n")
        e.ex.exit_gap_tick()
        self.assertEqual(len(e.ex.monitor.exit_gaps), 10)
        self.assertIn(ax.H_EXIT_GAP, e.ex.counters.halts)

    def test_a_shrunk_file_resets_the_offset_and_logs_it(self):
        g = self.tmp / "gaps3.jsonl"
        g.write_text("".join(json.dumps({"mint": "a", "exit_gap_bps": 1}) + "\n" for _ in range(5)))
        e = self.env(exit_gap_file=str(g))
        e.ex.exit_gap_tick()
        self.assertEqual(len(e.ex.monitor.exit_gaps), 5)
        g.write_text(json.dumps({"mint": "b", "exit_gap_bps": 2}) + "\n")  # rotated: shorter than the offset
        e.ex.exit_gap_tick()
        self.assertEqual(len(e.ledger("exit_gap_file_reset")), 1)
        self.assertEqual(len(e.ex.monitor.exit_gaps), 6)

    def test_dry_run_without_a_configured_file_never_reads_one(self):
        e = self.env()
        self.assertIsNone(e.ex.cfg_a3_file)

    def test_live_run_defaults_to_the_monitor_file(self):
        with mock.patch.object(ax, "LIVE_BUILT", True):
            e = CommittedLossTests.live_env(self)
        self.assertEqual(e.ex.cfg_a3_file, ax.A3_RECORD_DEFAULT)

    def test_exit_gap_rows_reach_the_halt_after_ten(self):
        g = self.tmp / "gaps.jsonl"
        g.write_text("".join(json.dumps({"mint": f"m{i}", "exit_gap_bps": 500}) + "\n" for i in range(9)))
        e = self.env(exit_gap_file=str(g))
        e.ex.exit_gap_tick()
        self.assertNotIn(ax.H_EXIT_GAP, e.ex.counters.halts)
        with g.open("a") as fh:
            fh.write(json.dumps({"mint": "m9", "exit_gap_bps": 500}) + "\n")
        e.ex.exit_gap_tick()
        self.assertIn(ax.H_EXIT_GAP, e.ex.counters.halts)

    def test_ingest_exit_gap_below_threshold_does_not_latch(self):
        e = self.env()
        for _ in range(12):
            e.ex.ingest_exit_gap(100.0)
        self.assertNotIn(ax.H_EXIT_GAP, e.ex.counters.halts)


class CanaryRecordTests(ACase):
    """Gap (h): the owner line is tied to a bot_id."""

    def start(self, **kw):
        with mock.patch.object(ax, "LIVE_BUILT", True):
            return AEnv(self.tmp, live=True, book_id=A2, replay_only=True, end_ms=ax.SEAL_END_MS, funded_lamports=500_000_000, **kw)

    def refused(self, why):
        with self.assertRaises(SystemExit) as cm:
            self.start()
        self.assertIn(why, str(cm.exception))

    def test_no_record_refuses(self):
        (self.tmp / "FREEZE.md").write_text("x")
        self.refused("canary_record_for_bot_id_missing")

    def test_a_record_for_another_book_does_not_cover_this_one(self):
        write_canary_record(self.tmp / "repo", Q50, A2V2)
        self.refused("canary_record_for_bot_id_missing")

    def test_a_prefix_or_substring_of_the_bot_id_does_not_match(self):
        write_canary_record(self.tmp / "repo", A2 + "-x", "x" + A2)
        self.refused("canary_record_for_bot_id_missing")

    def test_the_old_free_text_owner_line_no_longer_counts(self):
        (self.tmp / "repo" / "DEC").mkdir(parents=True, exist_ok=True)
        (self.tmp / "repo" / "DEC" / "DEC-033-h5-arena-canary-test.md").write_text("OWNER_CANARY_CONFIRMED: test\n")
        self.refused("canary_record_for_bot_id_missing")

    def test_the_template_file_never_counts(self):
        write_canary_record(self.tmp / "repo", A2, name="DEC-032-arena-canary-template.md")
        self.refused("canary_record_for_bot_id_missing")

    # DEC-032 Am.7 (DRAFT; audit rank 4): the owner's line may instead sit in a root-owned file Helm writes; no DEC record needed in the installed tree
    def owner_file(self, text: str, mode: int = 0o644) -> Path:
        p = ax.ARENA_OWNER_CANARY_PATH
        self.assertTrue(str(p).startswith(str(self.tmp)), p)  # a test never writes the host's /etc/mal-h5-arena
        p.write_text(text)
        os.chmod(p, mode)
        return p

    def test_a_root_owned_owner_file_naming_this_book_covers_it_without_a_dec_record(self):
        (self.tmp / "FREEZE.md").write_text("x")
        self.owner_file(f"OWNER_CANARY_CONFIRMED: bot_id={A2}\n")
        self.assertIsNone(ax.canary_record_refusal(A2, self.tmp / "repo"))
        self.assertEqual(self.start().ex.abook.bot_id, A2)

    def test_an_owner_file_that_is_wrong_in_any_way_does_not_cover(self):
        (self.tmp / "FREEZE.md").write_text("x")
        bad = [(f"OWNER_CANARY_CONFIRMED: bot_id={Q50}\n", 0o644, "owner_canary_file_other_book"),
               (f"OWNER_CANARY_CONFIRMED: bot_id={A2}-x\n", 0o644, "owner_canary_file_other_book"),
               (f"OWNER_CANARY_CONFIRMED: bot_id={A2}\nOWNER_CANARY_CONFIRMED: bot_id={Q50}\n", 0o644, "owner_canary_file_other_book"),
               (f"OWNER_CANARY_CONFIRMED: bot_id={A2}\nanything else\n", 0o644, "owner_canary_file_other_book"),
               (f"{A2}\n", 0o644, "owner_canary_file_other_book"), ("", 0o644, "owner_canary_file_other_book"),
               (f"OWNER_CANARY_CONFIRMED: bot_id={A2}\n", 0o664, "owner_canary_file_unsafe"),
               (f"OWNER_CANARY_CONFIRMED: bot_id={A2}\n", 0o600, "owner_canary_file_unsafe")]
        for text, mode, why in bad:
            with self.subTest(text=text, mode=oct(mode)):
                self.owner_file(text, mode)
                self.assertEqual(ax.owner_canary_file_refusal(A2), why)
                self.assertEqual(ax.canary_record_refusal(A2, self.tmp / "repo"), "canary_record_for_bot_id_missing")  # the DEC reason is returned
                self.refused("canary_record_for_bot_id_missing")

    def test_a_symlinked_or_missing_owner_file_does_not_cover(self):
        (self.tmp / "FREEZE.md").write_text("x")
        self.assertEqual(ax.owner_canary_file_refusal(A2), "owner_canary_file_missing")
        real = self.tmp / "real-owner"
        real.write_text(f"OWNER_CANARY_CONFIRMED: bot_id={A2}\n")
        os.chmod(real, 0o644)
        ax.ARENA_OWNER_CANARY_PATH.symlink_to(real)
        self.assertEqual(ax.owner_canary_file_refusal(A2), "owner_canary_file_unsafe")
        self.refused("canary_record_for_bot_id_missing")

    def test_a_dec_record_still_covers_with_no_owner_file(self):
        write_canary_record(self.tmp / "repo")
        self.assertIsNone(ax.canary_record_refusal(A2, self.tmp / "repo"))

    def test_a_symlinked_record_does_not_count(self):
        write_canary_record(self.tmp / "repo", A2, name="real.md")
        (self.tmp / "repo" / "DEC" / "DEC-033-h5-arena-canary-link.md").symlink_to(self.tmp / "repo" / "DEC" / "real.md")
        self.refused("canary_record_for_bot_id_missing")

    def test_a_record_naming_this_bot_id_starts(self):
        write_canary_record(self.tmp / "repo", A2)
        (self.tmp / "FREEZE.md").write_text("x")
        self.assertEqual(self.start().ex.abook.bot_id, A2)


class StartChecksTests(ACase):
    """Funded vs the finalized balance, the sell-builder pin and the P1 definition."""

    def live(self, balance=None, funded=500_000_000):
        write_canary_record(self.tmp / "repo")
        (self.tmp / "FREEZE.md").write_text("x")
        with mock.patch.object(ax, "LIVE_BUILT", True):
            def build(self_env):
                self_env.rpc.accounts[MINT] = mint_value()
                self_env.rpc.balance = funded if balance is None else balance
                ex = ax.ArenaExecutor(self_env.rpc, self_env.conf, self_env.kp, now_ms=self_env.clock, root=self_env.root)
                self_env.seed_clock(ex)
                return ex
            with mock.patch.object(AEnv, "build", build):
                return AEnv(self.tmp, live=True, book_id=A2, replay_only=True, end_ms=ax.SEAL_END_MS, funded_lamports=funded)

    def refused(self, why, **kw):
        with self.assertRaises(SystemExit) as cm:
            self.live(**kw)
        self.assertIn(why, str(cm.exception))

    def test_balance_within_tolerance_starts(self):
        tol = ax.FUNDED_BALANCE_TOLERANCE_LAMPORTS
        self.assertEqual(tol, 10_000_000)
        self.live(balance=500_000_000)
        self.live(balance=500_000_000 + tol)
        self.live(balance=500_000_000 - tol)

    def test_balance_outside_tolerance_refuses(self):
        tol = ax.FUNDED_BALANCE_TOLERANCE_LAMPORTS
        self.refused("funded_balance_mismatch", balance=500_000_000 + tol + 1)
        self.refused("funded_balance_mismatch", balance=500_000_000 - tol - 1)
        self.refused("funded_balance_mismatch", balance=0)

    def test_unreadable_balance_refuses(self):
        with mock.patch.object(ax.ArenaExecutor, "_funded_balance_problem", ax.ArenaExecutor._funded_balance_problem):
            e_rpc_error = lambda m, p: (_ for _ in ()).throw(RuntimeError("down"))  # noqa: E731
            class Dummy:  # a minimal stand-in carrying what the method reads
                rpc = staticmethod(e_rpc_error)
                user = "u"
                funded_lamports = 500_000_000
                state = type("S", (), {"realized_lamports": 0})()
            self.assertEqual(ax.ArenaExecutor._funded_balance_problem(Dummy()), "funded_balance_unreadable")

    def test_a_restart_after_a_realized_loss_may_be_lower_by_that_loss(self):
        class Dummy:
            rpc = staticmethod(lambda m, p: {"value": 500_000_000 - 30_000_000})
            user = "u"
            funded_lamports = 500_000_000
            state = type("S", (), {"realized_lamports": -30_000_000})()
        self.assertIsNone(ax.ArenaExecutor._funded_balance_problem(Dummy()))
        Dummy.state = type("S", (), {"realized_lamports": 0})()
        self.assertEqual(ax.ArenaExecutor._funded_balance_problem(Dummy()), "funded_balance_mismatch")

    def test_the_balance_read_is_finalized(self):
        seen = []
        class Dummy:
            rpc = staticmethod(lambda m, p: (seen.append((m, p)), {"value": 500_000_000})[1])
            user = "u"
            funded_lamports = 500_000_000
            state = type("S", (), {"realized_lamports": 0})()
        ax.ArenaExecutor._funded_balance_problem(Dummy())
        self.assertEqual(seen, [("getBalance", ["u", {"commitment": "finalized"}])])

    def test_sell_pin_matches_on_disk_code(self):
        self.assertIsNone(ax.sell_pin_problem())

    def test_a_wrong_pin_item_refuses_a_live_start(self):
        for k in ax.SELL_BUILDER_PIN:
            if not k.endswith("sha256"):
                continue
            bad = {**ax.SELL_BUILDER_PIN, k: "0" * 64}
            self.assertEqual(ax.sell_pin_problem(bad), f"sell_builder_pin_mismatch:{k}")
        write_canary_record(self.tmp / "repo")
        bad = {**ax.SELL_BUILDER_PIN, "symbol_sha256": "0" * 64}
        ax.ARENA_LIVE_OK_PATH.write_text(A2 + "\n")  # the live entry's own checks pass, so the sell pin is what refuses
        os.chmod(ax.ARENA_LIVE_OK_PATH, 0o644)
        live_cfg = {"book_id": A2, "mode": "live", "a3_record_file": "/x", "intents_file": "/x", "end_ms": 1, "funded_lamports": 500_000_000}
        with mock.patch.object(ax, "FEED_NOTE_MERGED", True), mock.patch.object(ax, "SELL_BUILDER_PIN", bad):
            self.assertEqual(ax.arena_start_refusal(ax.BOOKS[A2], live_cfg, self.tmp / "repo", live=True), "sell_builder_pin_mismatch:symbol_sha256")

    def test_p1_entry_gap_is_the_price_ratio_and_200_bps_reads_200(self):
        self.assertAlmostEqual(ax.entry_gap_bps(10_200, 10_000), 200.0)  # 2% fewer tokens for the same stake is a 2% higher price
        self.assertAlmostEqual(ax.entry_gap_bps(102, 100), 200.0)
        self.assertLess(1e4 * (102 - 100) / 102, 200.0)  # the old (exp - got) / exp read 196
        self.assertEqual(ax.entry_gap_bps(100, 0), float("inf"))
        self.assertLess(ax.entry_gap_bps(100, 150), 0)  # better than quoted is negative
        m = ax.FidelityMonitor()
        self.assertIsNone([m.note_entry_gap(ax.entry_gap_bps(102, 100)) for _ in range(10)][-1])  # exactly 200 is not above 200
        m2 = ax.FidelityMonitor()
        self.assertEqual([m2.note_entry_gap(ax.entry_gap_bps(1021, 1000)) for _ in range(10)][-1], ax.H_ENTRY_GAP)
