"""Tests for tools/h5_tipauth_watch.py (the offline watch behind the executor's tipauth_gate). Synthetic files in a temp directory and a fake RPC: no network,
no real shadow or tip data.

  python3 -m unittest tools.test_h5_tipauth_watch
"""
from __future__ import annotations

import fcntl
import inspect
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from tools import h5_tipauth_item4 as I4
from tools import h5_tipauth_watch as W

H = 3_600_000
MIN = 60_000
BASE = 1_792_000_000_000
T0 = BASE - BASE % H  # an hour boundary
NOW = T0 + 5 * MIN  # five minutes into the hour, so +61 minutes crosses into the next hour file
SHA, OTHER = "a" * 40, "b" * 40
URL = "https://api.mainnet-beta.solana.com"


def bt(**kw):
    d = {"status": "ok", "dropped_n": 1, "dropped_first_slot": 105, "dropped_last_slot": 105, "cover_lo": 100, "cover_hi": 150,
         "kept_ws_only_before_cover": 0, "kept_ws_only_above_cover": 0, "kept_ws_only_sig_on_tip": 0, "dropped_boost_n": 0, "dropped_sells_n": 1,
         "orphaned_by_drop_n": 0, "tipauth_nosettle": 0, "dropped": [["DROPA", 0, 105]], "margin_slots": 4}
    d.update(kw)
    return d


def bu(**kw):
    d = {"status": "ok", "ws_only": 1, "tip_only": 0, "both": 5, "both_diff": 0, "dup": 0, "tip_first_slot": 100, "tip_hw_slot_global": 154}
    d.update(kw)
    return d


def trig(sig="SIGT1", pool="P1", ei=3, slot=200, t=NOW - 60_000, dropped=None, **extra):
    dropped = dropped if dropped is not None else [["DROPA", 0, 105]]
    r = {"type": "trigger", "variant": "pv", "pool": pool, "mint": "M1", "t_ms": t, "t_detect_ms": t - 50, "gap": False, "slot": slot, "signature": sig, "event_index": ei,
         "real_quote_pre": 5000, "base_pre": 7000, "boost_src": "pda", I4.WS_FIELD: 1, I4.UNION_FIELD: 1, I4.TIPAUTH_FIELD: 0, "base_union": bu(),
         "base_tipauth": bt(dropped_n=len(dropped), dropped=dropped, dropped_first_slot=dropped[0][2], dropped_last_slot=dropped[-1][2]),
         "pnl_sol": 777.0, "exit_ladder_trigger_slots": {"60": 1}}  # outcome-ish decoys the watch must never touch or copy
    r.update(extra)
    return r


def close(pool="P1", t=NOW - 30_000, status="ok", cover_hi=210, dropped=None, dropped_n=None, **extra):
    dropped = [["DROPA", 0, 105]] if dropped is None else dropped
    r = {"type": "pool", "reason": "horizon", "pool": pool, "t_ms": t, "sealed": False,
         "base_tipauth": {"status": status, "cover_hi": cover_hi, "dropped_n": len(dropped) if dropped_n is None else dropped_n, "dropped": dropped}}
    r.update(extra)
    return r


def start(t=NOW - 3 * MIN, sha=SHA):
    return {"type": "start", "t_ms": t, "code_sha": sha, "rule": "h5-v1"}


def tape_row(sig="SIGT1", pool="P1", ei=3, slot=200, q=5000, b=7000, **kw):
    r = {"type": "trade", "pool": pool, "slot": slot, "signature": sig, "event_index": ei, "quote_reserve": q, "base_reserve": b, "pnl_decoy": 99}
    r.update(kw)
    return r


def hname(ms):
    return time.strftime("%Y-%m-%dT%H", time.gmtime(ms // 1000))


class Env(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        root = Path(td.name)
        self.sd, self.tp, self.out = root / "shadow", root / "tip", root / "out"
        self.sd.mkdir()
        self.tp.mkdir()
        self.t = [NOW]
        self.calls: list[tuple[int, str]] = []
        self.rpc_fn = lambda sig, now: {"result": None}  # not found

    def now(self):
        return self.t[0]

    def rpc(self, url, payload, timeout):
        sig = payload["params"][0]
        self.calls.append((self.now(), sig))
        return self.rpc_fn(sig, self.now())

    def mk(self, from_ms=T0, sha=SHA):
        return W.Watch(self.sd, self.tp, self.out, sha, from_ms, URL, rpc=self.rpc, now_ms=self.now, sleep=lambda s: None)

    def shadow(self, recs, hour_ms=NOW, compact=False, raw_tail=""):
        with open(self.sd / f"h5-shadow-{hname(hour_ms)}.jsonl", "a", encoding="utf-8") as fh:
            for r in recs:
                fh.write((json.dumps(r, separators=(",", ":")) if compact else json.dumps(r)) + "\n")
            fh.write(raw_tail)

    def tape(self, rows, hour_ms=NOW):
        with open(self.tp / f"trades-{hname(hour_ms)}.jsonl", "a", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r, separators=(",", ":")) + "\n")

    def baseline(self, hw=210, trigger=None, extra_tape=()):
        """A start record, one watched trigger, its tape row, a high-water row. Everything in order: verdict ok."""
        self.shadow([start(), trigger or trig()])
        self.tape([tape_row(), *extra_tape, tape_row(sig="FILLER", pool="PX", slot=hw)])

    def state(self):
        return json.loads((self.out / "state.json").read_text())

    def events(self):
        return W.read_jsonl(self.out / "events.jsonl")

    def advance(self, ms):
        self.t[0] += ms


def r4_reader(path, now_ms, cfg_sha):
    """What R4 says the executor does with the file: schema, int ts_ms with 0 <= now - ts <= 180 s, the pinned sha, verdict ok. Returns (ok, why)."""
    try:
        d = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return False, "missing"
    if not isinstance(d, dict) or d.get("schema") != "h5_tipauth_watch_v1":
        return False, "bad"
    ts = d.get("ts_ms")
    if not isinstance(ts, int) or isinstance(ts, bool):
        return False, "bad"
    if not 0 <= now_ms - ts <= 180_000:
        return False, "stale"
    if d.get("shadow_sha") != cfg_sha:
        return False, "sha"
    if d.get("verdict") == "off":
        return False, "off"
    return (d.get("verdict") == "ok"), ("ok" if d.get("verdict") == "ok" else "hold")


class TestFixtureAndSchema(Env):
    def test_fixture_passes_the_shared_predicate(self):
        self.assertEqual(I4.would_pass(trig()), (True, "ok"))
        self.assertIs(W.would_pass, I4.would_pass)  # imported, not copied

    def test_ok_baseline_and_exact_schema(self):
        self.baseline()
        w = self.mk()
        st = w.cycle()
        self.assertEqual(tuple(sorted(st)), tuple(sorted(W.STATE_KEYS)))
        self.assertEqual(self.state(), st)
        self.assertEqual((st["schema"], st["verdict"], st["reason"], st["shadow_sha"]), ("h5_tipauth_watch_v1", "ok", "ok", SHA))
        self.assertEqual((st["watched_n"], st["clear_n"], st["pending_n"]), (1, 0, 1))
        self.assertTrue(isinstance(st["ts_ms"], int) and not isinstance(st["ts_ms"], bool) and st["ts_ms"] == NOW)
        self.assertFalse((self.out / "OFF").exists())
        self.assertEqual(self.events(), [])

    def test_r4_reader_accepts_only_fresh_ok_with_pinned_sha(self):
        self.baseline()
        self.mk().cycle()
        p = self.out / "state.json"
        self.assertEqual(r4_reader(p, NOW, SHA), (True, "ok"))
        self.assertEqual(r4_reader(p, NOW + 180_000, SHA), (True, "ok"))
        self.assertEqual(r4_reader(p, NOW + 180_001, SHA), (False, "stale"))
        self.assertEqual(r4_reader(p, NOW - 1, SHA), (False, "stale"))  # a ts in the future
        self.assertEqual(r4_reader(p, NOW, OTHER), (False, "sha"))

    def test_clear_when_every_check_is_definitive(self):
        self.baseline()
        self.shadow([close()])
        w = self.mk()
        w.cycle()
        self.assertEqual((self.state()["clear_n"], self.state()["pending_n"]), (0, 1))  # event 3 pending: one not-found only
        self.advance(RPC_REASK := W.RPC_REASK_MS)
        st = w.cycle()
        self.assertEqual((st["verdict"], st["clear_n"], st["pending_n"]), ("ok", 1, 0))
        self.assertEqual(len(self.calls), 2)


class TestWatchedSet(Env):
    def test_selection(self):
        good = [trig(sig="G1", t=NOW - MIN), trig(sig="G2", t=NOW - 2 * MIN, pool="P2")]
        bad = [trig(sig="B1", pool="B1", sealed=True), trig(sig="B2", pool="B2", h5_look2_sealed=True), trig(sig="B3", pool="B3", sealed="yes"),
               trig(sig="B4", pool="B4", variant="fv"), trig(sig="B5", pool="B5", **{I4.WS_FIELD: 0}),
               trig(sig="B6", pool="B6", t=T0 + 10 * MIN, t_detect_ms=T0 - 1)]  # detected before --from-ms
        self.shadow([start(), *good, *bad])
        w = self.mk(from_ms=T0)
        w.cycle()
        self.assertEqual(sorted(t["signature"] for t in w.trig.values()), ["G1", "G2"])
        self.assertEqual(self.state()["watched_n"], 2)

    def test_from_ms_boundary_is_inclusive_on_t_detect_ms(self):
        a, b = trig(sig="E1", pool="E1"), trig(sig="E2", pool="E2")
        a["t_detect_ms"], b["t_detect_ms"] = NOW - 10 * MIN, NOW - 10 * MIN - 1
        self.shadow([start(), a, b])
        w = self.mk(from_ms=NOW - 10 * MIN)
        w.cycle()
        self.assertEqual(sorted(t["signature"] for t in w.trig.values()), ["E1"])

    def test_junk_lines_and_both_json_styles(self):
        self.shadow([start(), trig(sig="C1", pool="C1")], compact=True)
        self.shadow([trig(sig="C2", pool="C2")], compact=False, raw_tail="not json at all\n[1,2]\n")
        w = self.mk()
        w.cycle()
        self.assertEqual(sorted(t["signature"] for t in w.trig.values()), ["C1", "C2"])
        self.assertNotIn("bad_trigger_line", self.state()["reason"])

    def test_bad_line_mentioning_a_trigger_holds(self):
        self.baseline()
        self.shadow([], raw_tail='{"type":"trigger","pool":"P9","slot":\n')
        self.assertIn("bad_trigger_line", self.mk().cycle()["reason"])

    def test_partial_last_line_waits_for_its_newline(self):
        self.shadow([start()])
        line = json.dumps(trig(sig="PL1", pool="PL1"))
        f = self.sd / f"h5-shadow-{hname(NOW)}.jsonl"
        with open(f, "a") as fh:
            fh.write(line[:40])
        w = self.mk()
        st = w.cycle()
        self.assertEqual(st["watched_n"], 0)
        self.assertEqual(st["verdict"], "ok")  # the partial line is a record being written, not a backlog
        with open(f, "a") as fh:
            fh.write(line[40:] + "\n")
        self.assertEqual(w.cycle()["watched_n"], 1)

    def test_unusable_signature_holds_for_good(self):
        self.shadow([start(), trig(sig="bad-sig!")])
        st = self.mk().cycle()
        self.assertEqual((st["verdict"], st["watched_n"]), ("hold", 1))
        self.assertIn("trigger_unverifiable", st["reason"])

    def test_backlog_is_a_hold(self):
        self.baseline()
        with mock.patch.object(W, "SHADOW_BUDGET_MS", -1):
            st = self.mk().cycle()
        self.assertEqual(st["verdict"], "hold")
        self.assertIn("shadow_read_incomplete", st["reason"])

    def test_missing_directories_hold_and_still_write(self):
        self.baseline()
        w = W.Watch(self.sd / "nope", self.tp / "nope", self.out, SHA, T0, URL, rpc=self.rpc, now_ms=self.now, sleep=lambda s: None)
        st = w.cycle()
        self.assertEqual(st["verdict"], "hold")
        for why in ("shadow_dir_missing", "tip_dir_missing", "shadow_read_incomplete"):
            self.assertIn(why, st["reason"])
        self.assertEqual(self.state(), st)


class TestOffEvent1(Env):
    def assert_off(self, event, reason_kind=None):
        st = self.state()
        self.assertEqual((st["verdict"], st["reason"]), ("off", f"off:{event}"))
        self.assertTrue((self.out / "OFF").exists())
        ev = [e for e in self.events() if e.get("off")]
        self.assertEqual(len(ev), 1)
        self.assertEqual(ev[0]["event"], event)
        if reason_kind:
            self.assertEqual(ev[0]["evidence"]["kind"], reason_kind)
        return ev[0]

    def test_no_row_on_the_tip(self):
        self.shadow([start(), trig()])
        self.tape([tape_row(sig="OTHERSIG", slot=200), tape_row(sig="FILLER", pool="PX", slot=210)])
        self.mk().cycle()
        e = self.assert_off(W.E1, "no_row")
        self.assertEqual((e["evidence"]["signature"], e["evidence"]["tip_hw_slot"], e["evidence"]["slot"]), ("SIGT1", 210, 200))

    def test_row_for_another_pool_or_event_index_is_not_the_print(self):
        for kw in ({"pool": "P2"}, {"ei": 4}):
            with self.subTest(kw=kw):
                self.setUp()
                self.shadow([start(), trig()])
                self.tape([tape_row(**kw), tape_row(sig="FILLER", pool="PX", slot=210)])
                self.mk().cycle()
                self.assert_off(W.E1, "no_row")

    def test_pre_trade_base_or_quote_differs(self):
        for kw in ({"b": 7001}, {"q": 4999}, {"b": "7000"}, {"q": None}):
            with self.subTest(kw=kw):
                self.setUp()
                self.shadow([start(), trig()])
                self.tape([tape_row(**kw), tape_row(sig="FILLER", pool="PX", slot=210)])
                self.mk().cycle()
                e = self.assert_off(W.E1, "pre_trade_differs")
                self.assertEqual((e["evidence"]["record_base_pre"], e["evidence"]["record_real_quote_pre"]), (7000, 5000))

    def test_every_row_with_the_key_must_equal(self):
        self.baseline(extra_tape=[tape_row(b=7001)])
        self.mk().cycle()
        self.assert_off(W.E1, "pre_trade_differs")

    def test_equal_row_clears_event_1_with_no_event(self):
        self.baseline()
        w = self.mk()
        w.cycle()
        self.assertEqual(self.events(), [])
        self.assertEqual(W.read_jsonl(self.out / "resolved.jsonl")[0]["kind"], "e1")

    def test_not_judged_until_the_tip_is_4_slots_past_the_trigger(self):
        self.shadow([start(), trig()])
        self.tape([tape_row(sig="FILLER", pool="PX", slot=203)])  # 203 - 4 < 200
        w = self.mk()
        self.assertEqual(w.cycle()["verdict"], "ok")
        self.assertFalse((self.out / "OFF").exists())
        self.tape([tape_row(sig="FILLER2", pool="PX", slot=204)])  # 204 - 4 >= 200 and no row for the trigger
        w.cycle()
        self.assert_off(W.E1, "no_row")

    def test_tip_not_past_the_trigger_holds_after_10_minutes(self):
        self.shadow([start(), trig(t=NOW)])
        self.tape([tape_row(sig="FILLER", pool="PX", slot=190)])
        w = self.mk()
        self.assertEqual(w.cycle()["verdict"], "ok")
        self.advance(10 * MIN - 100)
        self.assertEqual(w.cycle()["verdict"], "ok")  # 9:59.950 after the trigger's t_detect_ms (50 ms before its t_ms): not yet "after 10 minutes"
        self.advance(200)
        st = w.cycle()
        self.assertEqual(st["verdict"], "hold")
        self.assertIn("tip_not_past_trigger", st["reason"])
        self.assertFalse((self.out / "OFF").exists())

    def test_no_tape_at_all_holds_after_10_minutes(self):
        self.shadow([start(), trig(t=NOW - 11 * MIN)])
        st = self.mk(from_ms=T0 - H).cycle()
        self.assertEqual(st["verdict"], "hold")
        self.assertIn("tip_not_past_trigger", st["reason"])

    def test_own_hour_file_missing_is_not_an_absence_but_holds(self):
        old = NOW - 2 * H
        self.shadow([start(), trig(t=old)], hour_ms=old)
        self.shadow([start()])
        self.tape([tape_row(sig="FILLER", pool="PX", slot=210)])  # only the current hour exists
        st = self.mk(from_ms=old - H).cycle()
        self.assertFalse((self.out / "OFF").exists())
        self.assertEqual(st["verdict"], "hold")
        self.assertIn("trigger_print_unverified", st["reason"])

    def test_compressed_hour_blocks_a_conclusion(self):
        old = NOW - H  # the previous hour's plain file is gone, a .zst sibling exists
        self.shadow([start(), trig(t=NOW - MIN)])
        self.tape([tape_row(sig="FILLER", pool="PX", slot=210)])
        (self.tp / f"trades-{hname(old)}.jsonl.zst").write_bytes(b"x")
        self.mk().cycle()
        self.assertFalse((self.out / "OFF").exists())
        self.assertEqual(self.state()["pending_n"], 1)

    def test_partial_last_tape_line_for_the_signature_is_not_an_absence(self):
        self.shadow([start(), trig()])
        self.tape([tape_row(sig="FILLER", pool="PX", slot=210)])
        with open(self.tp / f"trades-{hname(NOW)}.jsonl", "a") as fh:
            fh.write(json.dumps(tape_row(), separators=(",", ":"))[:60])  # the trigger's own row, half written
        # the last complete row is still the filler: the tip is past the trigger, the row is mid-write
        self.mk().cycle()
        self.assertFalse((self.out / "OFF").exists())


class TestOffEvent2(Env):
    def test_dropped_signature_on_the_tape_in_the_same_pool(self):
        self.baseline(extra_tape=[tape_row(sig="DROPA", pool="P1", ei=0, slot=105)])
        self.mk().cycle()
        st = self.state()
        self.assertEqual((st["verdict"], st["reason"]), ("off", f"off:{W.E2}"))
        e = self.events()[0]
        self.assertEqual((e["event"], e["evidence"]["via"], e["evidence"]["signature"], e["evidence"]["pool"]), (W.E2, "tip_tape", "DROPA", "P1"))

    def test_same_signature_in_another_pool_is_not_a_hit(self):
        self.baseline(extra_tape=[tape_row(sig="DROPA", pool="P2", ei=0, slot=105)])
        self.assertEqual(self.mk().cycle()["verdict"], "ok")

    def test_any_event_index_counts(self):
        self.baseline(extra_tape=[tape_row(sig="DROPA", pool="P1", ei=9, slot=105)])
        self.assertEqual(self.mk().cycle()["verdict"], "off")

    def test_close_record_left_d(self):
        self.baseline()
        self.shadow([close(dropped=[], dropped_n=0)])
        w = self.mk()
        w.cycle()
        st = self.state()
        self.assertEqual((st["verdict"], st["reason"]), ("off", f"off:{W.E2}"))
        e = self.events()[0]
        self.assertEqual(e["evidence"]["via"], "close_record_left_D")
        self.assertEqual(e["evidence"]["left_D_entries"], [["DROPA", 0, 105]])

    def test_close_record_still_dropped_clears_event_2(self):
        self.baseline()
        self.shadow([close()])
        w = self.mk()
        self.assertEqual(w.cycle()["verdict"], "ok")
        self.assertIn("e2", [r["kind"] for r in W.read_jsonl(self.out / "resolved.jsonl")])

    def test_close_before_the_trigger_is_not_its_close(self):
        self.baseline()
        self.shadow([close(t=NOW - 2 * MIN, dropped=[], dropped_n=0)])  # t_ms < the trigger's: item 10 takes the first close at or after it
        self.assertEqual(self.mk().cycle()["verdict"], "ok")

    def test_unusable_close_is_undetermined_then_holds_after_60_minutes(self):
        self.baseline()
        self.shadow([close(status="tip_pending")])
        w = self.mk()
        self.assertEqual(w.cycle()["verdict"], "ok")
        self.advance(W.RPC_REASK_MS)
        self.assertEqual(w.cycle()["verdict"], "ok")
        self.advance(55 * MIN)
        st = w.cycle()  # 65 minutes after the trigger
        self.assertEqual(st["verdict"], "hold")
        self.assertIn("event2_unresolved_60m", st["reason"])
        self.assertFalse((self.out / "OFF").exists())

    def test_cover_hi_below_the_trigger_slot_is_unusable(self):
        self.baseline()
        self.shadow([close(cover_hi=199, dropped=[], dropped_n=0)])  # would read as left D if it were usable
        self.assertEqual(self.mk().cycle()["verdict"], "ok")


class TestOffEvent3AndRpc(Env):
    def test_found_is_off_with_evidence(self):
        self.baseline()
        self.rpc_fn = lambda sig, now: {"result": {"slot": 12345, "transaction": {}}}
        self.mk().cycle()
        st = self.state()
        self.assertEqual((st["verdict"], st["reason"]), ("off", f"off:{W.E3}"))
        e = self.events()[0]
        self.assertEqual((e["event"], e["evidence"]["signature"], e["evidence"]["found_slot"]), (W.E3, "DROPA", 12345))
        self.assertTrue(e["evidence"]["tids"])

    def test_a_found_answer_from_before_a_restart_is_off(self):
        self.baseline()
        self.out.mkdir(parents=True, exist_ok=True)
        (self.out / "rpc_log.jsonl").write_text(json.dumps({"sig": "DROPA", "asked_ms": NOW - 5 * MIN, "result": "found", "slot": 77}) + "\n")
        st = self.mk().cycle()
        self.assertEqual((st["verdict"], st["reason"]), ("off", f"off:{W.E3}"))
        self.assertEqual(self.calls, [])  # nothing asked: the stored answer decides
        self.assertEqual(self.events()[0]["evidence"]["found_slot"], 77)

    def test_payload_is_get_transaction_finalized(self):
        seen = []
        self.rpc_fn = lambda sig, now: seen.append(sig) or {"result": None}
        self.baseline()
        with mock.patch.object(W, "request_payload", wraps=W.request_payload) as rp:
            self.mk().cycle()
        self.assertEqual(rp.call_count, 1)
        p = W.request_payload("X1")
        self.assertEqual((p["method"], p["params"][1]["commitment"]), ("getTransaction", "finalized"))

    def test_not_found_is_asked_again_no_earlier_than_10_minutes_and_two_clear(self):
        self.baseline()
        self.shadow([close()])
        w = self.mk()
        w.cycle()
        self.advance(5 * MIN)
        w.cycle()
        self.advance(5 * MIN - 1)
        w.cycle()
        self.assertEqual(len(self.calls), 1)  # nothing asked again before 10 minutes
        self.advance(1)
        st = w.cycle()
        self.assertEqual(len(self.calls), 2)
        self.assertEqual((st["verdict"], st["clear_n"]), ("ok", 1))
        self.advance(5 * MIN)
        w.cycle()
        self.assertEqual(len(self.calls), 2)  # cleared: never asked again

    def test_an_error_is_never_a_not_found(self):
        self.baseline()
        self.shadow([close()])
        w = self.mk()
        w.cycle()  # a null at t0
        modes = iter(["raise", "http_error", "malformed", "rpc_error", "bad_result"])

        def fn(sig, now):
            m = next(modes)
            if m == "raise":
                raise TimeoutError("boom")
            return {"http_error": {"error": {"code": 429}}, "malformed": "html", "rpc_error": {"error": {"code": -32005}}, "bad_result": {"result": {"nope": 1}}}[m]
        self.rpc_fn = fn
        for _ in range(5):
            self.advance(RPC_ERRS := 11 * MIN)
            st = w.cycle()
        self.assertEqual(W.rpc_class(w.rpc_rows["DROPA"]), "pending")  # one null; five errors changed nothing
        self.assertEqual(st["clear_n"], 0)
        self.assertEqual(len([a for a in w.rpc_rows["DROPA"] if a["result"] == "error"]), 5)

    def test_error_between_nulls_does_not_count_but_a_later_null_does(self):
        self.baseline()
        self.shadow([close()])
        w = self.mk()
        w.cycle()
        answers = iter([RuntimeError("down"), {"result": None}])

        def fn(sig, now):
            a = next(answers)
            if isinstance(a, Exception):
                raise a
            return a
        self.rpc_fn = fn
        self.advance(10 * MIN)
        w.cycle()
        self.assertEqual(w.cycle()["clear_n"], 0)  # same instant: an error is not retried at once
        self.advance(W.RPC_ERROR_RETRY_MS)
        self.assertEqual(w.cycle()["clear_n"], 1)

    def test_rpc_raising_still_rewrites_state_every_cycle(self):
        self.baseline()
        self.rpc_fn = lambda sig, now: (_ for _ in ()).throw(ConnectionError("down"))
        w = self.mk()
        stamps = []
        for _ in range(3):
            st = w.cycle()
            stamps.append(st["ts_ms"])
            self.assertEqual(self.state()["ts_ms"], self.now())
            self.advance(30_000)
        self.assertEqual(stamps, [NOW, NOW + 30_000, NOW + 60_000])
        self.assertEqual(self.state()["verdict"], "ok")  # an unreachable RPC is a hold only after 60 minutes

    def test_no_definitive_answer_holds_after_60_minutes(self):
        self.baseline()
        self.shadow([close()])
        self.rpc_fn = lambda sig, now: (_ for _ in ()).throw(ConnectionError("down"))
        w = self.mk()
        t_detect = NOW - 60_000 - 50
        self.t[0] = t_detect + 60 * MIN
        self.assertEqual(w.cycle()["verdict"], "ok")
        self.t[0] = t_detect + 60 * MIN + 1
        st = w.cycle()
        self.assertEqual(st["verdict"], "hold")
        self.assertIn("rpc_no_answer_60m", st["reason"])
        self.assertFalse((self.out / "OFF").exists())

    def test_rpc_asks_are_bounded_per_cycle(self):
        drops = [[f"DROP{i}", 0, 101 + i] for i in range(5)]
        recs = [trig(sig=f"SIG{i}", pool=f"Q{i}", dropped=[[f"D{i}X{j}", 0, 101 + j] for j in range(5)]) for i in range(5)]
        self.shadow([start(), *recs])
        self.tape([tape_row(sig="FILLER", pool="PX", slot=210)] + [tape_row(sig=f"SIG{i}", pool=f"Q{i}") for i in range(5)])
        self.mk().cycle()
        self.assertEqual(len(self.calls), W.RPC_MAX_ASKS)


class TestHolds(Env):
    def test_shadow_sha_mismatch(self):
        self.shadow([start(sha=OTHER), trig()])
        self.tape([tape_row(), tape_row(sig="FILLER", pool="PX", slot=210)])
        st = self.mk().cycle()
        self.assertEqual((st["verdict"], st["shadow_sha"]), ("hold", OTHER))
        self.assertIn("shadow_sha_mismatch", st["reason"])

    def test_no_start_record_and_non_string_sha(self):
        self.shadow([trig()])
        st = self.mk().cycle()
        self.assertEqual((st["verdict"], st["shadow_sha"]), ("hold", None))
        self.assertIn("no_start_record", st["reason"])
        self.shadow([{"type": "start", "t_ms": NOW, "code_sha": None}])
        st = self.mk().cycle()
        self.assertEqual((st["verdict"], st["shadow_sha"]), ("hold", None))

    def test_newest_file_first_and_last_start_in_a_file_wins(self):
        self.shadow([start(sha=SHA)], hour_ms=NOW - H)
        self.shadow([start(sha=OTHER)])
        self.assertEqual(self.mk().cycle()["shadow_sha"], OTHER)
        self.setUp()
        self.shadow([start(sha=OTHER)], hour_ms=NOW - H)
        self.shadow([start(sha=OTHER, t=NOW - 2 * MIN), start(sha=SHA)])
        self.assertEqual(self.mk().cycle()["shadow_sha"], SHA)

    def test_start_search_stops_at_the_cap(self):
        self.shadow([start()], hour_ms=NOW - 3 * H)
        for k in (2, 1, 0):
            self.shadow([trig(sig=f"N{k}", pool=f"N{k}", dropped=[["D" + str(k), 0, 105]])], hour_ms=NOW - k * H)
        with mock.patch.object(W, "START_SCAN_FILES", 3):
            st = self.mk(from_ms=NOW - 4 * H).cycle()
        self.assertEqual((st["verdict"], st["shadow_sha"]), ("hold", None))
        self.assertIn("no_start_record", st["reason"])
        with mock.patch.object(W, "START_SCAN_FILES", 4):
            self.assertEqual(self.mk(from_ms=NOW - 4 * H).cycle()["shadow_sha"], SHA)

    def test_start_marker_inside_another_record_is_a_hold_not_a_skip(self):
        """Round 2 (R-A): the old test pinned `ok` with the pinned sha here. A marker after the newest valid start that is not a complete start line holds."""
        # json.dumps escapes quotes, so the marker can only be in a line by gluing: a complete record whose newline was lost, then a start record
        self.shadow([start(sha=SHA)], raw_tail='{"type":"gap","kind":"x"}' + json.dumps(start(sha=OTHER)) + "\n")
        st = self.mk().cycle()
        self.assertEqual(st["verdict"], "hold")
        self.assertIn("start_record_unreadable", st["reason"])
        self.assertIsNone(st["shadow_sha"])

    def test_start_record_appended_later_is_seen_through_the_cache(self):
        self.shadow([start(sha=SHA)])
        w = self.mk()
        self.assertEqual(w.cycle()["shadow_sha"], SHA)
        self.shadow([{"type": "hb", "t_ms": NOW}])
        self.assertEqual(w.cycle()["shadow_sha"], SHA)
        self.shadow([start(sha=OTHER)])
        st = w.cycle()
        self.assertEqual((st["shadow_sha"], st["verdict"]), (OTHER, "hold"))

    def test_manual_hold_and_release(self):
        self.baseline()
        w = self.mk()
        self.assertEqual(w.cycle()["verdict"], "ok")
        self.assertEqual(W.main(["--out-dir", str(self.out), "--hold"], now_ms=self.now), 0)
        self.assertTrue((self.out / "HOLD").exists())
        st = w.cycle()
        self.assertEqual((st["verdict"], st["reason"]), ("hold", "manual_hold"))
        self.assertEqual(W.main(["--out-dir", str(self.out), "--release"], now_ms=self.now), 0)
        self.assertFalse((self.out / "HOLD").exists())
        self.assertEqual(w.cycle()["verdict"], "ok")

    def test_release_never_lifts_off(self):
        self.baseline()
        self.mk().cycle()
        W.main(["--out-dir", str(self.out), "--force-off", "test"], now_ms=self.now)
        W.main(["--out-dir", str(self.out), "--release"], now_ms=self.now)
        self.assertTrue((self.out / "OFF").exists())
        self.assertEqual(self.mk().cycle()["verdict"], "off")

    def test_internal_error_is_a_hold_and_state_is_still_written(self):
        self.baseline()
        w = self.mk()
        with mock.patch.object(w, "tape_phase", side_effect=RuntimeError("x")):
            st = w.cycle()
        self.assertEqual(st["verdict"], "hold")
        self.assertIn("internal_error:tape:RuntimeError", st["reason"])
        self.assertEqual(self.state()["ts_ms"], NOW)
        with mock.patch.object(w, "verdict", side_effect=RuntimeError("y")):
            st = w.cycle()
        self.assertEqual((st["verdict"], st["reason"]), ("hold", "internal_error:verdict:RuntimeError"))
        self.assertEqual(self.state(), st)


class TestOffMarker(Env):
    def trip(self):
        self.shadow([start(), trig()])
        self.tape([tape_row(sig="FILLER", pool="PX", slot=210)])  # no row for the trigger: event 1
        w = self.mk()
        w.cycle()
        return w

    def test_off_survives_a_restart_and_a_cleared_cause(self):
        w = self.trip()
        self.assertEqual(self.state()["verdict"], "off")
        self.tape([tape_row()])  # the cause "goes away": the row is now on the tape
        for _ in range(3):
            self.advance(30_000)
            self.assertEqual(w.cycle()["verdict"], "off")
        w2 = self.mk()  # restart
        self.advance(30_000)
        st = w2.cycle()
        self.assertEqual((st["verdict"], st["reason"]), ("off", f"off:{W.E1}"))
        self.assertTrue((self.out / "OFF").exists())
        self.assertEqual(r4_reader(self.out / "state.json", self.now(), SHA), (False, "off"))

    def test_the_tool_never_removes_off(self):
        src = inspect.getsource(W)
        for needle in ("unlink", "os.remove", "rmtree", "os.rename"):
            self.assertEqual(src.count(needle), 1 if needle == "unlink" else 0, needle)
        self.assertIn("(out_dir / FILES[\"hold\"]).unlink()", src)  # the only removal in the module is the manual HOLD marker

    def test_off_marker_is_recreated_from_the_log_if_lost(self):
        self.trip()
        (self.out / "OFF").unlink()
        w2 = self.mk()
        self.assertTrue((self.out / "OFF").exists())
        self.assertEqual(w2.cycle()["verdict"], "off")

    def test_off_is_written_even_if_the_log_cannot_be_appended(self):
        self.shadow([start(), trig()])
        self.tape([tape_row(sig="FILLER", pool="PX", slot=210)])
        w = self.mk()
        with mock.patch.object(W, "append_jsonl", return_value=False):
            st = w.cycle()
        self.assertEqual(st["verdict"], "off")
        self.assertTrue((self.out / "OFF").exists())

    def test_events_are_not_duplicated_across_cycles_or_restarts(self):
        w = self.trip()
        w.cycle()
        self.mk().cycle()
        self.assertEqual(len([e for e in self.events() if e.get("off")]), 1)

    def test_first_off_event_names_the_marker_and_later_events_are_appended(self):
        self.baseline(extra_tape=[tape_row(sig="DROPA", pool="P1", ei=0, slot=105)])
        self.rpc_fn = lambda sig, now: {"result": {"slot": 1}}
        self.mk().cycle()
        names = sorted(e["event"] for e in self.events() if e.get("off"))
        self.assertEqual(names, sorted([W.E2, W.E3]))
        self.assertIn(json.loads((self.out / "OFF").read_text())["event"], names)

    def test_force_off_writes_off_and_state_at_once(self):
        self.baseline()
        w = self.mk()
        w.cycle()
        self.advance(1000)
        self.assertEqual(W.main(["--out-dir", str(self.out), "--force-off", "manager says no"], now_ms=self.now), 0)
        st = self.state()
        self.assertEqual((st["verdict"], st["reason"], st["ts_ms"], st["shadow_sha"]), ("off", "off:manager_force_off", self.now(), SHA))
        self.assertEqual((st["watched_n"], st["pending_n"]), (1, 1))
        first = (self.out / "OFF").read_text()
        self.assertIn("manager says no", first)
        self.assertEqual(w.cycle()["verdict"], "off")
        self.advance(1000)
        W.main(["--out-dir", str(self.out), "--force-off", "again"], now_ms=self.now)
        self.assertEqual((self.out / "OFF").read_text(), first)  # an existing OFF is never rewritten

    def test_force_off_needs_a_reason_and_no_other_input(self):
        with self.assertRaises(SystemExit):
            W.main(["--out-dir", str(self.out), "--force-off", "  "], now_ms=self.now)
        self.assertEqual(W.main(["--out-dir", str(self.out), "--force-off", "no inputs needed"], now_ms=self.now), 0)
        self.assertEqual(self.state()["shadow_sha"], None)


class TestFreshness(Env):
    def test_every_cycle_rewrites_state_by_atomic_rename(self):
        self.baseline()
        w = self.mk()
        real = os.replace
        seen = []

        def spy(a, b):
            seen.append((Path(a).name, Path(b).name))
            return real(a, b)
        with mock.patch.object(W.os, "replace", side_effect=spy):
            for _ in range(3):
                w.cycle()
                self.advance(30_000)
        self.assertEqual(seen, [("state.json.tmp", "state.json")] * 3)
        self.assertEqual(sorted(p.name for p in self.out.iterdir() if p.name.endswith(".tmp")), [])

    def test_state_is_rewritten_when_everything_fails(self):
        w = W.Watch(self.sd / "x", self.tp / "x", self.out, SHA, T0, URL, rpc=self.rpc, now_ms=self.now, sleep=lambda s: None)
        for k in range(3):
            st = w.cycle()
            self.assertEqual(self.state()["ts_ms"], NOW + k * 30_000)
            self.assertEqual(st["verdict"], "hold")
            self.advance(30_000)

    def test_run_loop_never_leaves_more_than_60_seconds_between_writes(self):
        self.baseline()
        stamps = []

        def sleep(s):
            self.advance(int(s * 1000))
        w = W.Watch(self.sd, self.tp, self.out, SHA, T0, URL, rpc=self.rpc, now_ms=self.now, sleep=sleep, log=lambda m: stamps.append(self.state()["ts_ms"]))
        w.run(max_cycles=6)
        gaps = [b - a for a, b in zip(stamps, stamps[1:])]
        self.assertEqual(len(stamps), 6)
        self.assertTrue(all(g == 30_000 for g in gaps), gaps)
        self.assertTrue(all(g <= 60_000 for g in gaps))

    def test_slow_phases_cannot_starve_the_state_write(self):
        """Every phase has a deadline: with the clock jumping 40 s per RPC call the cycle still ends and writes."""
        self.baseline()
        drops = [[f"D{j}", 0, 101 + j] for j in range(5)]
        self.shadow([trig(sig="SIGZ", pool="PZ", dropped=drops)])
        self.tape([tape_row(sig="SIGZ", pool="PZ")])

        def slow(sig, now):
            self.advance(40_000)
            return {"result": None}
        self.rpc_fn = slow
        st = self.mk().cycle()
        self.assertLessEqual(len([c for c in self.calls]), 2)  # the 8 s budget stops the asking after the first slow call
        self.assertEqual(self.state()["ts_ms"], st["ts_ms"])

    def test_stale_file_is_what_a_dead_watch_leaves(self):
        self.baseline()
        self.mk().cycle()
        self.assertEqual(r4_reader(self.out / "state.json", NOW + 100_000, SHA), (True, "ok"))
        self.assertEqual(r4_reader(self.out / "state.json", NOW + 200_000, SHA), (False, "stale"))  # no rewrite: the executor refuses


class TestResume(Env):
    def test_rpc_answers_survive_a_restart(self):
        self.baseline()
        self.shadow([close()])
        self.mk().cycle()
        self.advance(W.RPC_REASK_MS)
        w2 = self.mk()
        st = w2.cycle()
        self.assertEqual(len(self.calls), 2)
        self.assertEqual((st["clear_n"], st["verdict"]), (1, "ok"))
        w3 = self.mk()
        self.advance(MIN)
        self.assertEqual(w3.cycle()["clear_n"], 1)
        self.assertEqual(len(self.calls), 2)

    def test_cleared_checks_are_not_redone_and_w_is_rebuilt_from_the_files(self):
        self.baseline()
        self.mk().cycle()
        for p in self.tp.iterdir():
            p.unlink()  # the tape is gone; event 1 was already cleared
        w2 = self.mk()
        self.advance(30_000)
        st = w2.cycle()
        self.assertEqual((st["watched_n"], st["verdict"]), (1, "ok"))
        self.assertFalse((self.out / "OFF").exists())

    def test_second_watch_on_the_same_out_dir_is_refused(self):
        self.baseline()
        self.out.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.out / "lock", os.O_RDWR | os.O_CREAT, 0o644)
        self.addCleanup(os.close, fd)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        rc = W.main(["--shadow-dir", str(self.sd), "--tip-dir", str(self.tp), "--out-dir", str(self.out), "--shadow-sha", SHA, "--from-ms", str(T0), "--rpc-url", URL, "--once"],
                    rpc=self.rpc, now_ms=self.now, sleep=lambda s: None)
        self.assertEqual(rc, 3)
        self.assertFalse((self.out / "state.json").exists())


class TestCli(Env):
    def args(self, **kw):
        a = {"--shadow-dir": str(self.sd), "--tip-dir": str(self.tp), "--out-dir": str(self.out), "--shadow-sha": SHA, "--from-ms": str(T0), "--rpc-url": URL}
        a.update(kw)
        return [x for k, v in a.items() if v is not None for x in (k, v)] + ["--once"]

    def test_once_runs_one_cycle_and_writes_state(self):
        self.baseline()
        self.assertEqual(W.main(self.args(), rpc=self.rpc, now_ms=self.now, sleep=lambda s: None), 0)
        self.assertEqual(self.state()["verdict"], "ok")

    def test_required_inputs_and_sha_format(self):
        for drop in ("--shadow-dir", "--tip-dir", "--shadow-sha", "--from-ms", "--rpc-url"):
            with self.subTest(drop=drop), self.assertRaises(SystemExit):
                W.main(self.args(**{drop: None}), rpc=self.rpc, now_ms=self.now)
        for bad in ("A" * 40, "a" * 39, "g" * 40):
            with self.subTest(sha=bad), self.assertRaises(SystemExit):
                W.main(self.args(**{"--shadow-sha": bad}), rpc=self.rpc, now_ms=self.now)

    def test_keyed_or_non_public_rpc_urls_are_refused(self):
        self.assertIsNone(W.validate_rpc_url("https://api.mainnet-beta.solana.com"))
        self.assertIsNone(W.validate_rpc_url("https://api.mainnet-beta.solana.com/"))
        for bad in ("http://api.mainnet-beta.solana.com", "https://mainnet.helius-rpc.com/?api-key=abc", "https://x.example/abc123", "https://u:p@x.example",
                    "https://x.example/?k=1", "https://mainnet.helius-rpc.com", "ftp://x.example", "not a url"):
            with self.subTest(url=bad):
                self.assertIsNotNone(W.validate_rpc_url(bad))
                with self.assertRaises(SystemExit):
                    W.main(self.args(**{"--rpc-url": bad}), rpc=self.rpc, now_ms=self.now)

    def test_manager_commands_are_exclusive(self):
        with self.assertRaises(SystemExit):
            W.main(["--out-dir", str(self.out), "--hold", "--release"], now_ms=self.now)


class TestNoOutcomeData(Env):
    def test_nothing_outcome_like_reaches_any_output(self):
        self.shadow([start(), trig()])
        self.tape([tape_row(b=7001), tape_row(sig="FILLER", pool="PX", slot=210)])
        self.mk().cycle()
        blob = "".join(p.read_text() for p in self.out.iterdir() if p.is_file())
        for needle in ("pnl", "777", "exit_ladder", "decoy"):
            self.assertNotIn(needle, blob)

    def test_module_names_no_ledger_and_no_keyed_endpoint(self):
        src = inspect.getsource(W).lower()
        code = src.split('"""', 2)[2]  # below the docstring
        for needle in ("ledger", "api-key", "api_key", "apikey", "helius.env", "/var/lib", "/srv/", "sealed/"):
            self.assertNotIn(needle, code.replace("flag(rec.get(\"sealed\"))", ""))


class TestReadingDetails(Env):
    def test_missing_t_detect_ms_falls_back_to_t_ms_and_unknown_times_are_kept(self):
        a, b, c = trig(sig="F1", pool="F1", t=NOW - MIN), trig(sig="F2", pool="F2", t=T0 - MIN), trig(sig="F3", pool="F3")
        for r in (a, b, c):
            del r["t_detect_ms"]
        del c["t_ms"]
        self.shadow([start(), a, b, c])
        w = self.mk(from_ms=T0)
        w.cycle()
        self.assertEqual(sorted(t["signature"] for t in w.trig.values()), ["F1", "F3"])  # the record with no time at all is watched (the stricter set)
        self.assertIn("trigger_unverifiable", self.state()["reason"])  # and it cannot be matched, so it holds

    def test_explicit_false_seal_flags_are_unsealed(self):
        self.shadow([start(), trig(sealed=False, h5_look2_sealed=False), trig(sig="N2", pool="N2", sealed=None)])
        w = self.mk()
        w.cycle()
        self.assertEqual(len(w.trig), 2)

    def test_hb_and_gap_lines_are_not_read_even_if_garbled(self):
        self.baseline()
        self.shadow([], raw_tail='{"type":"hb","note":"trigger" garbled\n{"type": "gap", "x": "trigger" nope\n')
        self.assertEqual(self.mk().cycle()["verdict"], "ok")

    def test_close_of_an_unwatched_pool_is_not_parsed_but_a_watched_one_is(self):
        self.baseline()
        self.shadow([close(pool="OTHERPOOL", dropped=[], dropped_n=0), close()])
        w = self.mk()
        with mock.patch.object(w, "_on_pool", wraps=w._on_pool) as on_pool:
            w.cycle()
        self.assertEqual(on_pool.call_count, 1)
        self.assertEqual(self.state()["verdict"], "ok")

    def test_a_sealed_close_is_ignored(self):
        self.baseline()
        self.shadow([close(dropped=[], dropped_n=0, sealed=True)])
        self.assertEqual(self.mk().cycle()["verdict"], "ok")  # not read as a left-D close

    def test_reading_in_small_chunks_and_an_oversize_line(self):
        self.baseline()
        with mock.patch.object(W, "READ_CHUNK", 700):
            w = self.mk()
            st = w.cycle()
        self.assertEqual(st["verdict"], "hold")  # a line longer than a whole chunk is skipped unread, and the watch holds
        self.assertIn("bad_trigger_line", st["reason"])
        self.setUp()
        self.baseline()
        with mock.patch.object(W, "READ_CHUNK", 4096):
            w = self.mk()
            for _ in range(3):
                st = w.cycle()
        self.assertEqual((st["watched_n"], st["verdict"]), (1, "ok"))

    def test_truncated_file_is_read_again_without_counting_twice(self):
        self.baseline()
        w = self.mk()
        w.cycle()
        f = self.sd / f"h5-shadow-{hname(NOW)}.jsonl"
        f.write_text(json.dumps(start()) + "\n" + json.dumps(trig()) + "\n")
        st = w.cycle()
        self.assertEqual(st["watched_n"], 1)

    def test_tape_scan_finds_rows_across_chunk_boundaries_and_stops_at_the_deadline(self):
        rows = [tape_row(sig=f"FILL{i:03d}", pool="PX", slot=100 + i) for i in range(60)] + [tape_row()]
        self.tape(rows)
        f = self.tp / f"trades-{hname(NOW)}.jsonl"
        with mock.patch.object(W, "SCAN_CHUNK", 150):
            got, status, partial = W.scan_tape_file(f, ["SIGT1", "FILL030"], NOW + 10**9, self.now)
        self.assertEqual((status, sorted(got)), ("ok", ["FILL030", "SIGT1"]))
        self.assertEqual(got["SIGT1"][0]["base_reserve"], 7000)
        with mock.patch.object(W, "SCAN_CHUNK", 150):
            got, status, partial = W.scan_tape_file(f, ["SIGT1"], NOW - 1, self.now)
        self.assertEqual((status, got), ("incomplete", {}))
        self.assertEqual(W.scan_tape_file(self.tp / "absent.jsonl", ["SIGT1"], NOW + 10**9, self.now)[1], "unreadable")

    def test_a_signature_inside_another_field_is_not_a_row(self):
        self.shadow([start(), trig()])
        self.tape([tape_row(sig="SIGT1X", pool="P1"), tape_row(sig="FILLER", pool="PX", slot=210, note='"signature":"SIGT1"')])
        self.mk().cycle()
        self.assertEqual(self.state()["verdict"], "off")  # no row whose own signature is SIGT1: a longer signature, and the text inside another field, are not it
        self.assertEqual(self.events()[0]["evidence"]["kind"], "no_row")

    def test_high_water_is_the_last_complete_row_of_the_newest_hour(self):
        f = self.tp / f"trades-{hname(NOW)}.jsonl"
        self.assertIsNone(W.last_complete_row_slot(f))
        self.tape([tape_row(sig=f"R{i}", slot=100 + i, pad="x" * 400) for i in range(1000)])
        self.assertEqual(W.last_complete_row_slot(f), 1099)  # a file longer than the tail window: the first line of the tail is partial and dropped
        with open(f, "a") as fh:
            fh.write('{"slot":5000,"signature":"HALF')
        self.assertEqual(W.last_complete_row_slot(f), 1099)  # the half-written last line is not a row
        w = self.mk()
        self.assertEqual(w.tip_hw(), 1099)
        self.t[0] = NOW + H  # next hour, no file yet: the previous hour's
        self.assertEqual(w.tip_hw(), 1099)
        self.t[0] = NOW + 3 * H
        self.assertIsNone(w.tip_hw())

    def test_start_record_read_error_never_falls_back_to_an_older_file(self):
        self.shadow([start(sha=SHA)], hour_ms=NOW - H)
        self.shadow([start(sha=OTHER)])
        w = self.mk()
        newest = self.sd / f"h5-shadow-{hname(NOW)}.jsonl"
        newest.unlink()
        os.mkfifo(newest)  # not a regular file
        st = w.cycle()
        self.assertEqual((st["verdict"], st["shadow_sha"]), ("hold", None))
        self.assertIn("start_search_incomplete", st["reason"])


class _Trickle:
    """A real loopback HTTP server that sends a valid JSON-RPC answer one byte at a time (the reviewer's B2 probe): per-socket-operation timeouts never fire."""

    def __init__(self, body: bytes, gap_s: float) -> None:
        import socketserver
        import threading
        outer = self
        self.accepted = 0
        self.stop = threading.Event()

        class Handler(socketserver.BaseRequestHandler):
            def handle(self):
                outer.accepted += 1
                try:
                    data = b""
                    while b"\r\n\r\n" not in data:
                        chunk = self.request.recv(4096)
                        if not chunk:
                            return
                        data += chunk
                    self.request.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: %d\r\n\r\n" % len(body))
                    for i in range(len(body)):
                        if outer.stop.wait(gap_s):
                            return
                        self.request.sendall(body[i:i + 1])
                except OSError:
                    return

        class Server(socketserver.ThreadingTCPServer):
            daemon_threads = True
            allow_reuse_address = True

        self.srv = Server(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.stop.set()
        self.srv.shutdown()
        self.srv.server_close()


class TestRpcTotalDeadline(Env):
    """R-B (the reviewer's B2): one RPC call has a total deadline, so a server that trickles bytes cannot hold a cycle; a call past its deadline is an error, never a null."""
    BODY = b'{"jsonrpc":"2.0","result":null,"id":1}'

    def trickle(self, gap_s=0.15):
        srv = _Trickle(self.BODY, gap_s)
        self.addCleanup(srv.close)
        return srv

    def test_http_transport_has_a_total_deadline(self):
        srv = self.trickle()
        t0 = time.monotonic()
        with self.assertRaises(Exception):
            W.http_transport(srv.url, W.request_payload("X1"), 0.5)  # the answer needs about 5.7 s
        self.assertLess(time.monotonic() - t0, 2.0)

    def test_a_trickling_server_cannot_hold_a_cycle_and_its_answer_is_never_a_null(self):
        srv = self.trickle()
        self.baseline()
        self.shadow([close()])
        w = W.Watch(self.sd, self.tp, self.out, SHA, T0, srv.url, rpc=W.http_transport, now_ms=self.now, sleep=lambda s: None)
        t0 = time.monotonic()
        with mock.patch.object(W, "RPC_TIMEOUT_S", 0.3):
            st = w.cycle()
        self.assertLess(time.monotonic() - t0, 3.0)
        self.assertEqual(self.state()["ts_ms"], st["ts_ms"])  # state.json was written
        self.assertEqual(srv.accepted, 1)
        rows = W.read_jsonl(self.out / "rpc_log.jsonl")
        self.assertEqual([r["result"] for r in rows], ["error"])  # not a null, although the late answer would have been one
        self.assertEqual(W.rpc_class(w.rpc_rows["DROPA"]), "error")

    def test_a_blocked_transport_is_an_error_and_at_most_one_call_is_outstanding(self):
        import threading
        release = threading.Event()
        self.addCleanup(release.set)
        started = []

        def blocked(url, payload, timeout):
            started.append(payload["params"][0])
            release.wait(5)
            return {"result": None}  # the late answer, a null
        self.baseline()
        w = W.Watch(self.sd, self.tp, self.out, SHA, T0, URL, rpc=blocked, now_ms=self.now, sleep=lambda s: None)
        t0 = time.monotonic()
        with mock.patch.object(W, "RPC_TIMEOUT_S", 0.2):
            w.cycle()
            self.advance(W.RPC_ERROR_RETRY_MS)
            w.cycle()  # the first call is still stuck: no second call is started
        self.assertLess(time.monotonic() - t0, 3.0)
        self.assertEqual(len(started), 1)
        self.assertEqual([r["result"] for r in W.read_jsonl(self.out / "rpc_log.jsonl")], ["error"])
        release.set()
        time.sleep(0.3)  # the late answer arrives after its deadline and is dropped
        self.assertEqual([r["result"] for r in w.rpc_rows["DROPA"]], ["error"])
        self.assertEqual([r["result"] for r in W.read_jsonl(self.out / "rpc_log.jsonl")], ["error"])

    def test_state_is_rewritten_never_more_than_60_seconds_apart_with_a_real_slow_socket(self):
        """The spec bullet with the real clock and the constants scaled down by 100: a cycle every CYCLE_S, the worst gap is bounded by CYCLE_S + the cycle budgets."""
        srv = self.trickle(gap_s=0.1)
        self.baseline()
        stamps: list[float] = [time.monotonic()]  # the instant the watch starts counts as the previous write
        w = W.Watch(self.sd, self.tp, self.out, SHA, T0, srv.url, rpc=W.http_transport, now_ms=lambda: int(time.time() * 1000), sleep=time.sleep,
                    log=lambda m: stamps.append(time.monotonic()))
        with mock.patch.object(W, "CYCLE_S", 0.4), mock.patch.object(W, "RPC_TIMEOUT_S", 0.3), mock.patch.object(W, "RPC_ERROR_RETRY_MS", 1), \
                mock.patch.object(W, "SHADOW_BUDGET_MS", 60), mock.patch.object(W, "START_BUDGET_MS", 30), mock.patch.object(W, "TAPE_BUDGET_MS", 50), mock.patch.object(W, "RPC_BUDGET_MS", 50):
            w.run(max_cycles=4)
        gaps = [b - a for a, b in zip(stamps, stamps[1:])]
        self.assertEqual(len(stamps), 5)
        self.assertLess(max(gaps), 0.4 + 0.3 + 1.0 + 0.5, gaps)  # 100 x smaller than the real 30 s + 24 s: no call took 20 s

    def test_the_gap_arithmetic_counts_the_call_deadline_and_the_pacing_sleep(self):
        worst_cycle_ms = W.SHADOW_BUDGET_MS + W.START_BUDGET_MS + W.TAPE_BUDGET_MS + W.RPC_BUDGET_MS + int((W.RPC_TIMEOUT_S + W.RPC_DEADLINE_GRACE_S) * 1000) + int(W.RPC_PACE_S * 1000)
        self.assertLessEqual(int(W.CYCLE_S * 1000) + worst_cycle_ms + 4_000, 60_000)


class TestStartRecordUnreadable(Env):
    """R-A (the reviewer's B1, extended by quant-proof): a `start` marker after the newest valid start record that is not itself a complete, parseable start line at a
    line start is a hold `start_record_unreadable`, in the same file and in any newer file, and the search never falls back to an older record's sha."""

    def assert_unreadable(self, st):
        self.assertEqual(st["verdict"], "hold", st)
        self.assertIn("start_record_unreadable", st["reason"])
        self.assertIsNone(st["shadow_sha"])  # never the pinned sha of an older record

    def test_torn_hb_line_followed_by_a_start_on_another_sha_is_a_hold(self):
        """The reviewer's probe: start (pinned sha), a watched trigger, a torn hb line with no newline, then a restart's start record (another sha) on the same line."""
        self.shadow([start(), trig()], raw_tail='{"type":"hb","t_ms":17920000')
        self.tape([tape_row(), tape_row(sig="FILLER", pool="PX", slot=210)])
        self.shadow([start(sha=OTHER)])  # lands on the torn line, as JsonlSink.write appends with no newline guard
        st = self.mk().cycle()
        self.assert_unreadable(st)
        self.assertEqual(r4_reader(self.out / "state.json", NOW, SHA), (False, "sha"))

    def test_torn_gap_and_pool_fragments_followed_by_a_start_are_holds(self):
        for frag in ('{"type":"gap","kind":"backlog_jump","t_ms":1792', '{"type": "gap", "kind": "x"', '{"type":"pool","pool":"P1","sl', '{"type":"trigger","pool":"P1","sl'):
            with self.subTest(frag=frag):
                self.setUp()
                self.shadow([start(), trig()], raw_tail=frag)
                self.shadow([start(sha=OTHER)])
                self.assert_unreadable(self.mk().cycle())

    def test_compact_and_spaced_markers_both_count(self):
        for compact in (True, False):
            with self.subTest(compact=compact):
                self.setUp()
                self.shadow([start()], compact=compact, raw_tail="{torn")
                self.shadow([start(sha=OTHER)], compact=compact)
                self.assert_unreadable(self.mk().cycle())

    def test_a_hidden_marker_in_a_newer_file_does_not_fall_back_to_a_clean_older_file(self):
        self.shadow([start(sha=SHA)], hour_ms=NOW - H)  # clean, pinned sha
        self.shadow([{"type": "hb", "t_ms": NOW}], raw_tail='{"type":"hb","t_ms":1')  # newest file: no start record of its own
        self.shadow([start(sha=OTHER)])
        self.assert_unreadable(self.mk().cycle())

    def test_marker_in_a_newer_file_inside_another_record(self):
        self.shadow([start(sha=SHA)], hour_ms=NOW - H)
        self.shadow([], raw_tail='{"type":"gap","kind":"x"}' + json.dumps(start(sha=OTHER)) + "\n")
        self.assert_unreadable(self.mk().cycle())

    def test_unterminated_start_line_holds_until_its_newline_arrives(self):
        self.shadow([start(sha=SHA)])
        f = self.sd / f"h5-shadow-{hname(NOW)}.jsonl"
        line = json.dumps(start(sha=SHA, t=NOW + 1))
        with open(f, "a") as fh:
            fh.write(line[:30])
        w = self.mk()
        self.assert_unreadable(w.cycle())  # a start line is being written: not the older record
        with open(f, "a") as fh:
            fh.write(line[30:] + "\n")
        st = w.cycle()
        self.assertEqual((st["verdict"], st["shadow_sha"]), ("ok", SHA))  # the completed record is the newest valid one

    def test_unparseable_oversize_and_foreign_type_start_lines_hold(self):
        for raw in ('{"type":"start","code_sha":\n', '{"type":"start","type":"trigger"}\n', '{"type":"start","code_sha":"' + OTHER + '"} trailing\n'):
            with self.subTest(raw=raw):
                self.setUp()
                self.shadow([start(sha=SHA)], raw_tail=raw)
                self.assert_unreadable(self.mk().cycle())
        self.setUp()
        self.shadow([start(sha=SHA)])
        self.shadow([], raw_tail=json.dumps(dict(start(sha=OTHER), pad="x" * 400)) + "\n")
        with mock.patch.object(W, "START_LINE_MAX", 300):
            self.assert_unreadable(self.mk().cycle())

    def test_the_hold_survives_the_cache_and_a_restart_and_clears_only_with_a_later_valid_start(self):
        self.shadow([start(sha=SHA), trig()], raw_tail='{"type":"hb","t_ms":17')
        self.tape([tape_row(), tape_row(sig="FILLER", pool="PX", slot=210)])
        self.shadow([start(sha=OTHER)])
        w = self.mk()
        for _ in range(3):  # unchanged file: the cached answer is still the hold
            self.assert_unreadable(w.cycle())
            self.advance(30_000)
        self.shadow([{"type": "hb", "t_ms": NOW}])  # more lines after the hidden marker change nothing
        self.assert_unreadable(w.cycle())
        self.assert_unreadable(self.mk().cycle())  # a watch restart cannot clear it either
        self.shadow([start(sha=SHA, t=NOW + 5)])  # the shadow restarted: a clean start record is the newest valid one
        st = w.cycle()
        self.assertEqual((st["verdict"], st["reason"], st["shadow_sha"]), ("ok", "ok", SHA))
        self.assertEqual(self.mk().cycle()["verdict"], "ok")

    def test_a_clean_newer_file_clears_a_hidden_marker_in_an_older_file(self):
        self.shadow([start(sha=SHA)], hour_ms=NOW - H, raw_tail='{"type":"hb"')
        self.shadow([start(sha=OTHER)], hour_ms=NOW - H)  # hidden in the older file
        self.shadow([start(sha=SHA)])  # newer file, clean: it is the newest valid record and wins
        self.assertEqual(self.mk(from_ms=NOW - 2 * H).cycle()["shadow_sha"], SHA)

    def test_clean_files_and_unrelated_text_are_not_holds(self):
        self.shadow([start(sha=SHA), {"type": "hb", "t_ms": NOW, "note": "restart"}, trig()], compact=True)
        st = self.mk().cycle()
        self.assertNotIn("start_record_unreadable", st["reason"])
        self.assertEqual(st["shadow_sha"], SHA)


if __name__ == "__main__":
    unittest.main()
