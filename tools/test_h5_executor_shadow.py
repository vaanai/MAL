"""The executor against the shadow detector's records (tools/h5_shadow.py, PR #477, claude/h5-shadow at 3dbe1de).

SHADOW_TRIGGER mirrors the dict built in h5_shadow.Engine._fire at that commit (same keys, same units). If #477 renames a key the
parser tests here are the ones that fail."""

from __future__ import annotations

import json
import os
import unittest
from pathlib import Path

from tools import h5_executor as h
from tools.test_h5_executor import BASE0, MINT, POOL, Q, S0, SPS, T0, TRIG_SLOT, V, Case, Env, buy_args
from tools.test_h5_executor import Clock

SOLD = 5_000_000_000  # tokens the triggering sell put into the pool (raw)


VENDORED = json.loads((Path(__file__).parent / "fixtures" / "h5_shadow" / "records_3dbe1de.json").read_text())


def shadow_trigger(clock: Clock, **kw) -> dict:
    """The vendored record (keys as Engine._fire writes them at 3dbe1de) with the fixture pool's values and the fake clock's times, plus the two
    class fields a detector with the synthetic-migration classifier adds to a trigger it lets through (synthetic false, synthetic_src). The vendored
    record itself predates them, so a record taken straight from the fixture is an old-schema record and the executor refuses it."""
    row = {**VENDORED["trigger"], "synthetic": False, "synthetic_src": "rpc", "pool": POOL, "mint": MINT, "s0": S0, "slot": TRIG_SLOT, "sps": SPS, "s0_t_recv_ms": clock() - 101_600,
           "block_time": clock() // 1000, "t_recv_ms": clock() - 50, "t_detect_ms": clock(), "q_trigger_sol": Q / 1e9, "q_pv_post_sol": Q / 1e9,
           "q_fv_post_sol": Q / 1e9 + 0.001, "q_pv_pre_sol": Q / 1e9 + 0.2, "q_fv_pre_sol": Q / 1e9 + 0.2, "v_print": V, "v0": V,
           "base_pre": BASE0 - SOLD, "sell_token_raw": SOLD, "landing_slot_primary": TRIG_SLOT + 7, "landing_slot_binding": TRIG_SLOT + 10,
           "exit_trigger_slot": S0 + 1650, "exit_landing_slot": S0 + 1653}
    return {**row, **kw}


def pool_row(mint: str, **kw) -> dict:
    """A pool close record as #477 writes it for a pool that ran its horizon with the vault PDA as BOOST."""
    row = {**VENDORED["pool"], "pool": POOL, "mint": mint, "s0": S0, "boost_last_slice_slot": S0 + 1707}
    return {**row, **kw}


class ShadowParseTests(unittest.TestCase):
    def test_trigger_maps_onto_the_executors_fields(self):
        c = Clock()
        t, bad = h.parse_shadow_trigger(shadow_trigger(c))
        self.assertIsNone(bad)
        self.assertEqual((t.mint, t.pool, t.s0_slot, t.trigger_slot, t.sps), (MINT, POOL, S0, TRIG_SLOT, SPS))
        self.assertEqual(t.q_lamports, Q)  # q_trigger_sol x 1e9, post-trade Q of the variant
        self.assertEqual(t.base_reserve, BASE0)  # base_pre + sell_token_raw: a sell adds its tokens to the pool
        self.assertEqual((t.v_lamports, t.decision_ms, t.gap), (V, T0, False))

    def test_only_the_primary_variant_is_acted_on(self):
        c = Clock()
        self.assertEqual(h.parse_shadow_trigger(shadow_trigger(c, variant="fv")), (None, None))
        t, _ = h.parse_shadow_trigger(shadow_trigger(c, variant="fv"), variant="fv")
        self.assertIsNotNone(t)
        self.assertEqual(h.parse_shadow_trigger({"type": "pool"}), (None, None))
        self.assertEqual(h.parse_shadow_trigger(shadow_trigger(c, variant="pv", type="outcome")), (None, None))

    def test_unusable_records_are_refused_with_a_reason(self):
        c = Clock()
        for k, v, why in (("v_print", None, "bad_intent:v_print"), ("q_trigger_sol", 0, "bad_intent:q_trigger_sol"), ("sell_token_raw", 0, "bad_intent:base"),
                          ("base_pre", None, "bad_intent:base"), ("sps", 0.7, "bad_intent:sps"), ("slot", S0 - 1, "bad_intent:slot_order")):
            t, bad = h.parse_shadow_trigger(shadow_trigger(c, **{k: v}))
            self.assertEqual((t, bad), (None, why), k)
        r = shadow_trigger(c)
        del r["t_detect_ms"]
        self.assertEqual(h.parse_shadow_trigger(r), (None, "bad_intent:missing_t_detect_ms"))

    def test_the_gap_flag_is_carried(self):
        t, _ = h.parse_shadow_trigger(shadow_trigger(Clock(), gap=True))
        self.assertTrue(t.gap)


class ShadowFlowTests(Case):
    def append(self, e: Env, *rows: dict, path: Path | None = None) -> None:
        p = path or Path(e.conf["intents_file"])
        with p.open("a") as fh:
            for r in rows:
                fh.write(json.dumps(r) + "\n")

    def start(self, **kw) -> Env:
        e = self.env(**kw)
        Path(e.conf["intents_file"]).write_text("")
        e.ex.intent_tick()
        return e

    def start_in(self, sub: Path, **kw) -> Env:
        e = Env(sub, **kw)
        Path(e.conf["intents_file"]).write_text("")
        e.ex.intent_tick()
        return e

    def test_a_shadow_trigger_becomes_the_same_buy_as_an_h5_intent(self):
        e = self.start()
        self.append(e, shadow_trigger(e.clock))
        self.assertEqual(e.ex.intent_tick(), 1)
        spend, min_out = buy_args(e.sent()[0])
        terms = h.entry_terms(Q, BASE0, e.ex.h5.stake_lamports, 1500)
        self.assertEqual((spend, min_out), (e.ex.h5.stake_lamports, terms["min_out"]))
        dec = e.ledger("decision")[0]
        self.assertEqual((dec["trigger_slot"], dec["s0_slot"], dec["q_lamports"], dec["v_lamports"]), (TRIG_SLOT, S0, Q, V))

    def test_the_fixed_v_variant_is_not_traded(self):
        e = self.start()
        self.append(e, shadow_trigger(e.clock, variant="fv"))
        self.assertEqual((e.ex.intent_tick(), e.rpc.sent), (0, []))

    def test_a_trigger_the_detector_flagged_as_gapped_is_refused(self):
        e = self.start()
        self.append(e, shadow_trigger(e.clock, gap=True, gaps=[{"kind": "feed_restart"}]))
        e.ex.intent_tick()
        self.assertEqual((e.refusals(), e.rpc.sent), (["feed_gap"], []))

    def test_a_gap_record_refuses_buys_for_the_hold_then_clears(self):
        e = self.start()
        self.append(e, {"type": "gap", "kind": "feed_restart", "open_pools": 3, "flags_pools": True}, shadow_trigger(e.clock))
        e.ex.intent_tick()
        self.assertEqual(e.refusals(), ["feed_gap"])
        e.jump(e.ex.h5.gap_hold_ms + 1)
        self.append(e, shadow_trigger(e.clock))
        e.ex.intent_tick()
        self.assertEqual(len(e.rpc.sent), 1)

    def test_gap_hold_can_be_lengthened_never_shortened(self):
        self.assertEqual(h.H5Limits.from_config({"gap_hold_ms": 1}).gap_hold_ms, 20_000)
        self.assertEqual(h.H5Limits.from_config({"gap_hold_ms": 90_000}).gap_hold_ms, 90_000)

    def test_a_non_flagging_reconnect_does_not_block_a_trigger_one_second_later(self):
        e = self.start()
        self.append(e, {"type": "gap", "kind": "socket_reconnect", "open_pools": 12, "flags_pools": False})
        e.ex.intent_tick()
        e.clock.t += 1_000
        self.append(e, shadow_trigger(e.clock))
        e.ex.intent_tick()
        self.assertEqual((e.refusals(), len(e.rpc.sent)), ([], 1))
        self.assertEqual([r["kind_"] for r in e.ledger("feed_reconnect_redundant")], ["socket_reconnect"])
        self.assertEqual(e.ledger("feed_gap"), [])  # no hold was started

    def test_a_flagging_gap_blocks_a_trigger_one_second_later(self):
        for kind in ("socket_reconnect", "slot_jump", "silence"):
            sub = self.tmp / kind
            sub.mkdir()
            e = self.start_in(sub)
            self.append(e, {"type": "gap", "kind": kind, "open_pools": 12, "flags_pools": True})
            e.ex.intent_tick()
            e.clock.t += 1_000
            self.append(e, shadow_trigger(e.clock))
            e.ex.intent_tick()
            self.assertEqual((e.refusals(), e.rpc.sent), (["feed_gap"], []), kind)

    def test_a_gap_record_without_flags_pools_or_with_an_unclear_value_blocks(self):
        for i, extra in enumerate(({}, {"flags_pools": None}, {"flags_pools": "false"}, {"flags_pools": 0})):
            sub = self.tmp / f"u{i}"
            sub.mkdir()
            e = self.start_in(sub)
            self.append(e, {"type": "gap", "kind": "socket_reconnect", "open_pools": 12, **extra})
            e.ex.intent_tick()
            e.clock.t += 1_000
            self.append(e, shadow_trigger(e.clock))
            e.ex.intent_tick()
            self.assertEqual((e.refusals(), e.rpc.sent), (["feed_gap"], []), extra)  # only the literal false lifts the hold: unknown fails closed

    def test_a_trigger_the_detector_flagged_is_refused_even_after_a_redundant_reconnect(self):
        e = self.start()
        self.append(e, {"type": "gap", "kind": "socket_reconnect", "open_pools": 12, "flags_pools": False}, shadow_trigger(e.clock, gap=True))
        e.ex.intent_tick()
        self.assertEqual((e.refusals(), e.rpc.sent), (["feed_gap"], []))

    def test_a_pool_close_record_is_a_ledger_row_and_one_early_pool_halts_nothing(self):
        e = self.start()
        self.append(e, pool_row(MINT, boost_last_slice_s=341.5), pool_row("X" * 43 + "1", boost_last_slice_s=None, boost_last_slice_s_blocktime=None,
                                                                         boost_last_slice_s_recv=None),
                    pool_row("Y" * 43 + "2", boost_last_slice_s=334.0), pool_row("W" * 43 + "3", boost_last_slice_s=250.0))
        e.ex.intent_tick()
        self.assertEqual(e.ex.counters.halts, {})  # a pool with no BOOST identified says nothing; single early pools are noise
        self.assertEqual([r["seconds_after_s0"] for r in e.ledger("boost_last_slice")], [341.5, 334.0, 250.0])

    def test_ten_pool_close_records_under_335_s_halt_through_the_file(self):
        e = self.start()
        self.append(e, *[pool_row(f"M{i:02d}" + "1" * 40, boost_last_slice_s=334.0) for i in range(h.BOOST_MEDIAN_MIN_POOLS)])
        e.ex.intent_tick()
        self.assertEqual(list(e.ex.counters.halts), ["boost_median_lt_335"])

    def test_boost_timing_falls_back_to_block_time_then_wall_clock(self):
        e = self.start()
        self.append(e, pool_row(MINT, boost_last_slice_s=None, boost_last_slice_s_blocktime=333.0),
                    pool_row("N" * 43 + "1", boost_last_slice_s=None, boost_last_slice_s_blocktime=None, boost_last_slice_s_recv=342.0))
        e.ex.intent_tick()
        self.assertEqual([r["seconds_after_s0"] for r in e.ledger("boost_last_slice")], [333.0, 342.0])

    def test_heartbeat_records_keep_the_feed_fresh(self):
        e = self.start(feed_heartbeat_max_age_ms=5_000)
        self.append(e, shadow_trigger(e.clock))
        e.ex.intent_tick()
        self.assertEqual(e.refusals(), ["feed_stale"])
        self.append(e, {"type": "hb", "tracked": 12}, shadow_trigger(e.clock))
        e.ex.intent_tick()
        self.assertEqual(len(e.rpc.sent), 1)

    def test_directory_mode_follows_the_hourly_files_and_drains_the_old_hour_first(self):
        d = self.tmp / "shadow"
        d.mkdir()
        old, new = d / "h5-shadow-2026-10-06T14.jsonl", d / "h5-shadow-2026-10-06T15.jsonl"
        old.write_text("")
        e = self.env(intents_file=str(d))
        e.ex.intent_tick()  # first look: the end of the existing file
        self.append(e, pool_row("A" * 43 + "1", boost_last_slice_s=336.0), path=old)
        e.ex.intent_tick()
        self.assertEqual(list(e.ex.counters.day(h.day_key(T0))["boost_s"]), ["A" * 43 + "1"])
        new.write_text("")
        e.ex._glob_ms = 0  # the directory listing is cached for a second
        self.append(e, pool_row("B" * 43 + "2", boost_last_slice_s=336.0), path=old)  # written after the new hour's file appeared
        self.append(e, pool_row("C" * 43 + "3", boost_last_slice_s=336.0), path=new)
        e.ex.intent_tick()
        self.assertEqual(e.ex._tail_path, new)
        self.assertEqual(sorted(e.ex.counters.day(h.day_key(T0))["boost_s"]), ["A" * 43 + "1", "B" * 43 + "2", "C" * 43 + "3"])  # all three, old hour first
        self.assertEqual(e.ex.counters.halts, {})  # three pools: no day median yet

    def test_the_cap_pick_subdirectory_is_not_an_hourly_file(self):
        # The pinned configs set pick_file=/srv/mal-h5-shadow/cap-pick/picks.jsonl: the exporter's subdirectory rides the same bind. It is
        # never the newest hour (even written last), its rows are never read as shadow records, and the oracle reads it as its pick file.
        d = self.tmp / "shadow"
        (d / "cap-pick").mkdir(parents=True)
        hour = d / "h5-shadow-2026-10-06T15.jsonl"
        hour.write_text("")
        picks = d / "cap-pick" / "picks.jsonl"
        e = self.env(intents_file=str(d), pick_file=str(picks))
        e.ex.intent_tick()
        pick = "P" * 43 + "9"
        picks.write_text(json.dumps({"mint": pick, "pick": True}) + "\n" + json.dumps({"hb": True, "t_ms": e.clock()}) + "\n")
        os.utime(picks, (e.clock() / 1000 + 60, e.clock() / 1000 + 60))  # the newest entry by mtime
        e.ex._glob_ms = 0
        self.assertEqual(e.ex._resolve_intents(), hour)
        self.append(e, pool_row("A" * 43 + "1", boost_last_slice_s=336.0), path=hour)
        e.ex.intent_tick()
        self.assertEqual(e.ex._tail_path, hour)
        self.assertEqual(list(e.ex.counters.day(h.day_key(T0))["boost_s"]), ["A" * 43 + "1"])
        self.assertNotIn(pick, "".join(p.read_text() for p in self.tmp.rglob("*.jsonl") if "cap-pick" not in p.parts))  # never copied out
        o = h.build_pick_oracle({"pick_file": str(picks)}, now_ms=e.clock)
        self.assertIs(o(pick), True)
        self.assertIsNone(o("A" * 43 + "1"))  # not in the feed: undecided, refused inside the window

    def test_directory_with_no_shadow_file_yet_is_quiet(self):
        d = self.tmp / "empty"
        d.mkdir()
        e = self.env(intents_file=str(d))
        self.assertEqual(e.ex.intent_tick(), 0)


if __name__ == "__main__":
    unittest.main()
