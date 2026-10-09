"""H5 executor: LIVE_OK at /etc/mal-h5 (DEC-024 3) and the review's interface and fidelity items (C1-C11).
Fixtures: test_h5_executor (the probe's fake RPC, the consistent fake chain, PathConf), test_h5_executor_shadow (the vendored #477 records)."""

from __future__ import annotations

import ast
import contextlib
import io
import json
import os
import unittest
from pathlib import Path
from unittest import mock

from tools import h5_executor as h
from tools import probe_executor as pe
from tools.test_h5_executor import BASE0, MINT, POOL, Q, S0, SPS, T0, TRIG_SLOT, V, Case, Clock, Env
from tools.test_h5_executor_shadow import VENDORED, pool_row, shadow_trigger


class LiveOkTests(Case):
    def setUp(self):
        super().setUp()
        self.real = self.tmp / "real-live-ok"

    def test_a_valid_live_ok_passes_and_a_missing_one_does_not(self):
        self.assertEqual(h.live_ok_valid(), "live_ok_missing")
        self.make_live_ok()
        self.assertIsNone(h.live_ok_valid())

    def test_wrong_owner_is_refused(self):
        self.make_live_ok()
        with mock.patch.object(h, "LIVE_OK_UID", os.getuid() + 1):  # "root" is someone else: the file is not root-owned
            self.assertEqual(h.live_ok_valid(), "live_ok_unsafe")

    def test_a_symlink_is_refused_even_to_a_good_file(self):
        self.real.write_text("")
        os.chmod(self.real, 0o644)
        h.LIVE_OK_PATH.symlink_to(self.real)
        self.assertEqual(h.live_ok_valid(), "live_ok_unsafe")

    def test_group_or_other_writable_is_refused(self):
        for mode in (0o664, 0o646, 0o666, 0o620):
            self.make_live_ok(mode)
            self.assertEqual(h.live_ok_valid(), "live_ok_unsafe", oct(mode))
        for mode in (0o600, 0o400, 0o640, 0o444, 0o755):  # exactly 0644: mal-live must be able to read it, and nobody else may write it
            h.LIVE_OK_PATH.unlink(missing_ok=True)
            self.make_live_ok(0o644)
            os.chmod(h.LIVE_OK_PATH, mode)
            self.assertEqual(h.live_ok_valid(), "live_ok_unsafe", oct(mode))
        self.make_live_ok(0o644)
        self.assertIsNone(h.live_ok_valid())

    def test_wrong_group_is_refused(self):
        self.make_live_ok()
        with mock.patch.object(h, "LIVE_OK_GID", os.getgid() + 1):
            self.assertEqual(h.live_ok_valid(), "live_ok_unsafe")

    def test_the_sell_priorities_and_the_late_sell_floor_are_not_configurable(self):
        for key, val in (("sell_priority_lamports", 55_000), ("escalated_priority_lamports", 150_000), ("late_sell_min_n", 1)):
            with self.assertRaises(ValueError, msg=key):
                h.H5Limits.from_config({key: val})  # not even at the default: the key does not exist in config

    def test_a_directory_instead_of_a_file_is_refused(self):
        h.LIVE_OK_PATH.mkdir()
        self.assertEqual(h.live_ok_valid(), "live_ok_unsafe")

    def test_the_parent_directory_must_not_be_group_or_other_writable_or_a_symlink(self):
        self.make_live_ok()
        os.chmod(self.etc, 0o775)
        self.assertEqual(h.live_ok_valid(), "live_ok_unsafe")
        os.chmod(self.etc, 0o757)
        self.assertEqual(h.live_ok_valid(), "live_ok_unsafe")
        os.chmod(self.etc, 0o755)
        self.assertIsNone(h.live_ok_valid())
        other = self.tmp / "other-etc"
        other.mkdir()
        os.chmod(other, 0o755)
        (other / "LIVE_OK").write_text("")
        os.chmod(other / "LIVE_OK", 0o644)
        linked = self.tmp / "linked-etc"
        linked.symlink_to(other)
        with mock.patch.object(h, "LIVE_OK_PATH", linked / "LIVE_OK"):
            self.assertEqual(h.live_ok_valid(), "live_ok_unsafe")  # the parent is a symlink

    def test_a_live_buy_is_refused_for_each_unsafe_live_ok_and_sends_nothing(self):
        cases = {"missing": lambda: h.LIVE_OK_PATH.unlink(), "group_writable": lambda: os.chmod(h.LIVE_OK_PATH, 0o664),
                 "symlink": lambda: (h.LIVE_OK_PATH.unlink(), h.LIVE_OK_PATH.symlink_to(self.real))}
        self.real.write_text("")
        os.chmod(self.real, 0o644)
        for name, damage in cases.items():
            sub = self.tmp / name
            sub.mkdir()
            e = Env(sub)
            damage()
            e.fire()
            self.assertEqual(e.refusals(), ["live_ok_missing" if name == "missing" else "live_ok_unsafe"], name)
            self.assertEqual((e.rpc.sent, e.ex.state.attempts), ([], 0), name)
            h.LIVE_OK_PATH.unlink(missing_ok=True)

    def test_live_ok_removed_mid_run_means_no_new_buy_and_no_buy_rebroadcast(self):
        e = self.env()
        e.fire()
        self.assertEqual(len(e.rpc.sent), 1)
        h.LIVE_OK_PATH.unlink()
        e.clock.t += 450
        e.ex.housekeeping(e.clock())
        e.ex.advance_pending()
        self.assertEqual(len(e.rpc.sent), 1)  # no rebroadcast of the buy
        e.fire(mint="Other" + "1" * 38)
        self.assertEqual(e.refusals()[-1], "live_ok_missing")  # and no new buy
        self.assertEqual(len(e.rpc.sent), 1)

    def test_a_dry_run_never_looks_at_live_ok_and_never_sends(self):
        e = self.env(live=False)
        h.LIVE_OK_PATH.unlink(missing_ok=True)
        e.fire()
        self.assertEqual((e.refusals(), e.rpc.sent, len(e.ledger("decision"))), ([], [], 1))

    def test_stop_halt_and_final_marker_are_pinned_to_the_state_dir_in_live(self):
        e = self.env()
        sd = Path(e.conf["state_dir"])
        paths = e.ledger("start")[0]["paths"]
        self.assertEqual((paths["stop"], paths["halt"], paths["final_marker"], paths["live_ok"]),
                         (str(sd / "STOP"), str(sd / "HALT"), str(sd / "FINAL_WRITTEN"), str(h.LIVE_OK_PATH)))
        self.assertEqual(e.ex.final_marker, sd / "FINAL_WRITTEN")

    def test_a_live_config_may_not_override_any_of_the_four(self):
        for key in h.PINNED_PATH_KEYS:
            sub = self.tmp / key
            sub.mkdir()
            with self.assertRaises(SystemExit, msg=key) as cm:
                Env(sub, **{key: str(self.tmp / "elsewhere")})
            self.assertIn(key, str(cm.exception))
            self.assertEqual(h.start_refusal({"state_dir": str(sub), key: "x", "end_ms": 1}), f"config_path_override:{key}")

    def test_a_dry_run_may_still_point_them_elsewhere(self):
        e = Env(self.tmp, live=False, stop_file=str(self.tmp / "ELSEWHERE_STOP"))
        (self.tmp / "ELSEWHERE_STOP").write_text("")
        e.fire()
        self.assertEqual(e.refusals(), ["stop_file"])

    def test_the_banner_prints_the_resolved_paths(self):
        e = self.env(live=False)
        cp = self.tmp / "c.json"
        cp.write_text(json.dumps(e.conf))
        Path(e.conf["intents_file"]).write_text("")
        with mock.patch.object(pe, "ProbeRpc", lambda url: e.rpc), mock.patch.object(h.sim, "load_rpc_url", return_value="http://x"), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            h.main(["--config", str(cp), "--once"])
        self.assertIn("paths=", out.getvalue())
        self.assertIn(str(Path(e.conf["state_dir"]) / "STOP"), out.getvalue())


class ProbeStateAndLockTests(Case):
    def probe_state(self, profile: str, **kw):
        pe.State(mode="live", **kw).save(pe.state_path_for(self.probe_dir, "live", profile))

    def test_live_refuses_while_either_probe_profile_has_a_position_open_or_in_flight(self):
        for i, (prof, kw) in enumerate((("dec019", {"open": {"m": {"x": 1}}}), ("dec020", {"pending": {"m": {"kind": "buy"}}}))):
            self.probe_state(prof, **kw)
            sub = self.tmp / f"p{i}"
            sub.mkdir()
            with self.assertRaises(SystemExit) as cm:
                Env(sub)
            self.assertIn(prof, str(cm.exception))
            pe.state_path_for(self.probe_dir, "live", prof).unlink()

    def test_live_passes_when_the_probe_states_are_empty_or_absent(self):
        self.probe_state("dec019")  # the decommissioned probe: 0 open, 0 pending
        self.env()

    @unittest.skipIf(os.geteuid() == 0, "root ignores directory modes")
    def test_unreadable_probe_state_fails_closed_in_live_but_not_in_a_dry_run(self):
        self.probe_state("dec019")
        os.chmod(self.probe_dir, 0)
        self.addCleanup(os.chmod, self.probe_dir, 0o700)
        with self.assertRaises(SystemExit) as cm:
            self.env()
        self.assertIn("fail closed", str(cm.exception))
        Env(self.tmp / "dry", live=False) if (self.tmp / "dry").mkdir() is None else None  # a dry run as another user skips the cross-check

    def test_the_lock_is_not_opened_through_a_symlink(self):
        target = self.tmp / "precious"
        target.write_text("keep me")
        link = self.tmp / "state" / "h5-executor.lock"
        link.parent.mkdir()
        link.symlink_to(target)
        with self.assertRaises(OSError):
            h.acquire_lock(link)
        self.assertEqual(target.read_text(), "keep me")  # not truncated


class TriggerFidelityTests(Case):
    def parse(self, **kw):
        return h.parse_shadow_trigger(shadow_trigger(Clock(), **kw))

    def test_the_sps_window_must_span_120_seconds(self):
        for span in (None, 0, 119.9, True, "300", float("nan")):
            self.assertEqual(self.parse(sps_span_s=span), (None, "bad_intent:sps_span"), span)
        self.assertIsNotNone(self.parse(sps_span_s=120.0)[0])

    def test_a_pool_the_detector_skipped_for_want_of_an_sps_is_refused(self):
        e = self.env()
        path = Path(e.conf["intents_file"])
        path.write_text("")
        e.ex.intent_tick()
        with path.open("a") as fh:
            fh.write(json.dumps({"type": "skipped_no_sps", "pool": POOL, "mint": MINT, "s0": S0, "slot": S0 + 5}) + "\n")
            fh.write(json.dumps(shadow_trigger(e.clock)) + "\n")
        e.ex.intent_tick()
        self.assertEqual((e.refusals(), e.rpc.sent), (["sps_skipped_pool"], []))

    def test_a_missed_print_refuses_the_trigger_but_a_pure_reorder_does_not(self):
        # F1: base_breaks_unresolved_settled decides. It counts only prints at least 2 slots behind the high-water mark, so a predecessor still
        # in flight is not a break. 0 for a reorder or an in-flight print, >= 1 for a missed print; missing, null or non-integer refuses.
        for bad in (1, 2, None, "0", True, 0.0):
            self.assertEqual(self.parse(base_breaks_unresolved_settled=bad), (None, "bad_intent:base_breaks_unresolved_settled"), bad)
        r = shadow_trigger(Clock())
        del r["base_breaks_unresolved_settled"]  # an absent key is a schema mismatch (see test_a_shadow_that_lacks_a_required_key...)
        self.assertEqual(h.parse_shadow_trigger(r), (None, "bad_intent:missing_base_breaks_unresolved_settled"))
        # the live pattern: many raw breaks, the unsettled field reading 1 for a predecessor in flight, nothing missed once settled
        t, bad = self.parse(base_breaks=7, slot_regress=5, base_breaks_unresolved=1, base_breaks_unresolved_settled=0, s0_reanchored_slots=3)
        self.assertIsNone(bad)
        self.assertEqual((t.base_breaks, t.slot_regress, t.base_breaks_unresolved, t.base_breaks_unresolved_settled, t.s0_reanchored_slots),
                         (7, 5, 1, 0, 3))
        r = shadow_trigger(Clock())
        del r["base_breaks_unresolved"]  # the unsettled field is needed too (it marks a trigger whose predecessor is unlinked): absent is a mismatch
        self.assertEqual(h.parse_shadow_trigger(r), (None, "bad_intent:missing_base_breaks_unresolved"))
        self.assertEqual(self.parse(base_breaks_unresolved=None)[1], None)  # present as null (a value, not a missing key) is still ledgered as None

    def test_the_break_counts_and_the_reanchor_are_on_the_decision_row(self):
        e = self.env()
        e.ex.handle_trigger(h.parse_shadow_trigger(shadow_trigger(e.clock, base_breaks=7, slot_regress=5, base_breaks_unresolved=1,
                                                                  s0_reanchored_slots=2))[0])
        dec = e.ledger("decision")[0]
        self.assertEqual((dec["base_breaks"], dec["slot_regress"], dec["base_breaks_unresolved"], dec["base_breaks_unresolved_settled"],
                          dec["s0_reanchored_slots"], dec["s0_minus_announced_slots"]), (7, 5, 1, 0, 2, 0))

    def test_a_trigger_whose_s0_was_reanchored_below_it_is_refused_through_the_gap_flag(self):
        # the detector puts slot_below_s0 in `gaps` and sets gap true when a print below s0 arrives after a trigger: already a refusal
        self.assertTrue(self.parse(gap=True, gaps=[{"kind": "slot_below_s0", "s0": S0, "slot": S0 - 1}])[0].gap)
        e = self.env()
        e.ex.handle_trigger(self.parse(gap=True, gaps=[{"kind": "slot_below_s0"}])[0])
        self.assertEqual((e.refusals(), e.rpc.sent), (["feed_gap"], []))

    def test_the_first_print_must_follow_the_announcement_by_at_most_two_slots(self):
        for ok in (0, 1, 2):
            self.assertIsNone(self.parse(s0_minus_announced_slots=ok)[1], ok)
        for bad in (3, 4440, -1, None, "0", True):
            self.assertEqual(self.parse(s0_minus_announced_slots=bad), (None, "bad_intent:s0_minus_announced_slots"), bad)
        r = shadow_trigger(Clock())
        del r["s0_minus_announced_slots"]
        self.assertEqual(h.parse_shadow_trigger(r), (None, "bad_intent:missing_s0_minus_announced_slots"))

    def test_the_sealed_stub_from_the_seal_window_is_refused(self):
        stub = {"type": "trigger", "variant": "pv", "pool": POOL, "slot": TRIG_SLOT, "suppressed": "cap_pick_seal", "v": 1, "schema": "h5_shadow_v1",
                "t_ms": T0}  # exactly the keys #477 writes: nothing to trade on
        self.assertEqual(h.parse_shadow_trigger(stub), (None, "bad_intent:suppressed"))
        e = self.env()
        path = Path(e.conf["intents_file"])
        path.write_text("")
        e.ex.intent_tick()
        with path.open("a") as fh:
            fh.write(json.dumps(stub) + "\n")
        e.ex.intent_tick()
        self.assertEqual((e.refusals(), e.rpc.sent), (["bad_intent:suppressed"], []))

    def test_more_than_five_bad_records_in_an_hour_raise_an_alert_once(self):
        e = self.env()
        path = Path(e.conf["intents_file"])
        path.write_text("")
        e.ex.intent_tick()
        with path.open("a") as fh:
            for _ in range(6):
                fh.write(json.dumps(shadow_trigger(e.clock, base_breaks_unresolved_settled=1)) + "\n")
        e.ex.intent_tick()
        alerts = [r for r in e.ledger("alert") if r.get("alert") == "bad_intent_rate"]
        self.assertEqual((len(alerts), alerts[0]["count"]), (1, 6))
        with path.open("a") as fh:
            fh.write(json.dumps(shadow_trigger(e.clock, base_breaks_unresolved_settled=1)) + "\n")
        e.ex.intent_tick()
        self.assertEqual(len([r for r in e.ledger("alert") if r.get("alert") == "bad_intent_rate"]), 1)  # not repeated within the hour
        e.jump(3_700_000)
        with path.open("a") as fh:
            for _ in range(6):
                fh.write(json.dumps(shadow_trigger(e.clock, base_breaks_unresolved_settled=1)) + "\n")
        e.ex.intent_tick()
        self.assertEqual(len([r for r in e.ledger("alert") if r.get("alert") == "bad_intent_rate"]), 2)  # an hour on, it can alert again

    def test_five_bad_records_are_not_an_alert(self):
        e = self.env()
        path = Path(e.conf["intents_file"])
        path.write_text("")
        e.ex.intent_tick()
        with path.open("a") as fh:
            for _ in range(5):
                fh.write(json.dumps(shadow_trigger(e.clock, base_breaks_unresolved_settled=1)) + "\n")
        e.ex.intent_tick()
        self.assertEqual([r for r in e.ledger("alert") if r.get("alert") == "bad_intent_rate"], [])

    def old_shadow_trigger(self, e: Env, **kw) -> dict:
        r = shadow_trigger(e.clock, schema="h5_shadow_v1", **kw)  # (the engine's emit adds the schema string; the vendored record is the body)
        del r["base_breaks_unresolved_settled"]  # a detector from before 3dbe1de: same schema string, no such key
        return r

    def feed(self, e: Env, *rows: dict) -> None:
        path = Path(e.conf["intents_file"])
        if not path.exists():
            path.write_text("")
            e.ex.intent_tick()
        with path.open("a") as fh:
            for r in rows:
                fh.write(json.dumps(r) + "\n")
        e.ex.intent_tick()

    def test_a_shadow_that_lacks_a_required_key_alerts_at_the_first_record_and_refuses(self):
        e = self.env()
        self.feed(e, self.old_shadow_trigger(e))  # ONE record, at the measured 1.5 to 3 triggers an hour
        alerts = e.alerts("shadow_schema_mismatch")
        self.assertEqual(len(alerts), 1)
        self.assertEqual((alerts[0]["missing"], alerts[0]["shadow_schema"]), (["base_breaks_unresolved_settled"], "h5_shadow_v1"))
        self.assertEqual((e.refusals(), e.rpc.sent), (["bad_intent:missing_base_breaks_unresolved_settled"], []))
        self.assertEqual(e.alerts("bad_intent_rate"), [])

    def test_the_schema_alert_is_rate_limited_to_one_per_ten_minutes_and_is_not_the_hourly_count(self):
        e = self.env()
        self.feed(e, *[self.old_shadow_trigger(e) for _ in range(8)])  # eight in a row: one alert, and no bad_intent_rate (that is for values)
        self.assertEqual((len(e.alerts("shadow_schema_mismatch")), e.alerts("bad_intent_rate")), (1, []))
        self.assertEqual(len(e.refusals()), 8)  # every one is refused and ledgered
        e.jump(599_000)
        self.feed(e, self.old_shadow_trigger(e))
        self.assertEqual(len(e.alerts("shadow_schema_mismatch")), 1)  # still inside the ten minutes
        e.jump(2_000)
        self.feed(e, self.old_shadow_trigger(e))
        self.assertEqual(len(e.alerts("shadow_schema_mismatch")), 2)  # ten minutes on, it alerts again

    def test_every_required_key_is_a_schema_mismatch_when_absent(self):
        for k in h.SHADOW_REQUIRED_KEYS:
            r = shadow_trigger(Clock())
            del r[k]
            self.assertEqual(h.parse_shadow_trigger(r), (None, f"bad_intent:missing_{k}"), k)
        self.assertEqual(set(h.SHADOW_REQUIRED_KEYS) - set(VENDORED["trigger"]), set())  # and the vendored #477 record has all of them

    def test_a_bad_value_is_not_a_schema_mismatch_and_keeps_the_hourly_threshold(self):
        e = self.env()
        self.feed(e, *[shadow_trigger(e.clock, base_breaks_unresolved_settled=1) for _ in range(5)])
        self.assertEqual((e.alerts("shadow_schema_mismatch"), e.alerts("bad_intent_rate")), ([], []))
        self.feed(e, shadow_trigger(e.clock, base_breaks_unresolved_settled=1))
        self.assertEqual((e.alerts("shadow_schema_mismatch"), len(e.alerts("bad_intent_rate"))), ([], 1))

    def test_a_flat_intent_row_missing_a_key_is_not_a_shadow_mismatch(self):
        e = self.env()
        r = {"schema": "h5_intent_v1", "synthetic": False, "synthetic_src": "rpc", "mint": MINT, "pool": POOL, "s0_slot": S0, "sps": SPS, "trigger_slot": TRIG_SLOT, "q_lamports": Q,
             "base_reserve": BASE0, "v_lamports": V}  # no decision_ms
        self.feed(e, r)
        self.assertEqual((e.refusals(), e.alerts("shadow_schema_mismatch")), (["bad_intent:missing_decision_ms"], []))

    def test_gap_must_be_a_bool_and_v_missing_literally_false(self):
        for bad in (None, "false", 0, 1, [], "False"):
            self.assertEqual(self.parse(gap=bad), (None, "bad_intent:gap"), bad)
            self.assertEqual(self.parse(v_missing=bad), (None, "bad_intent:v_missing"), bad)
        self.assertEqual(self.parse(v_missing=True), (None, "bad_intent:v_missing"))
        r = shadow_trigger(Clock())
        del r["gap"]
        self.assertEqual(h.parse_shadow_trigger(r), (None, "bad_intent:missing_gap"))
        r = shadow_trigger(Clock())
        del r["v_missing"]
        self.assertEqual(h.parse_shadow_trigger(r), (None, "bad_intent:missing_v_missing"))
        flat = {"schema": "h5_intent_v1", "synthetic": False, "synthetic_src": "rpc", "mint": MINT, "pool": POOL, "s0_slot": S0, "sps": SPS, "trigger_slot": TRIG_SLOT, "q_lamports": Q,
                "base_reserve": BASE0, "v_lamports": V, "decision_ms": T0, "gap": "no"}
        self.assertEqual(h.parse_trigger(flat), (None, "bad_intent:gap"))

    def test_boost_spent_is_rechecked_against_the_rule(self):
        for spent in (None, "4", -1.0, 17.5675, 17.585, 20.0, float("nan")):
            self.assertEqual(self.parse(boost_spent_sol=spent), (None, "bad_intent:boost_spent"), spent)
        self.assertIsNotNone(self.parse(boost_spent_sol=17.5674)[0])  # 0.999 x 17.585 = 17.567415
        self.assertIsNotNone(self.parse(boost_spent_sol=0.0)[0])

    def test_the_window_is_300_seconds_with_no_slack(self):
        e = self.env()
        e.seed_clock(back_s=320)  # a history that reaches an s0 300 s back
        e.fire(trigger_slot=e.rpc.slot - 5, s0_slot=e.rpc.slot - 5 - 1501)  # 300.2 s after s0
        self.assertEqual(e.refusals(), ["outside_rule_window"])
        e.fire(trigger_slot=e.rpc.slot - 5, s0_slot=e.rpc.slot - 5 - 1500)  # exactly 300.0 s
        self.assertEqual(len(e.rpc.sent), 1)

    def test_a_trigger_print_older_than_the_limit_by_block_time_is_stale(self):
        e = self.env()
        e.fire(block_time=T0 // 1000 - 13)
        self.assertEqual(e.refusals(), ["stale_trigger"])
        e.fire(block_time=T0 // 1000 - 12)  # block_time is whole seconds: 2 s of slack on top of the 10 s limit
        self.assertEqual(len(e.rpc.sent), 1)

    def test_the_vendored_records_carry_every_key_the_executor_reads(self):
        trig, pool = VENDORED["trigger"], VENDORED["pool"]
        read = {"type", "variant", "pool", "mint", "s0", "slot", "sps", "t_detect_ms", "q_trigger_sol", "base_pre", "sell_token_raw", "v_print",
                "v_missing", "gap", "base_breaks", "slot_regress", "sps_span_s", "boost_spent_sol", "s0_t_recv_ms", "block_time",
                "base_breaks_unresolved", "base_breaks_unresolved_settled", "s0_reanchored_slots", "s0_minus_announced_slots", "announced_slot"}
        self.assertTrue(read <= set(trig), read - set(trig))
        self.assertTrue({"type", "reason", "mint", "gap", "boost_src", "boost_last_slice_s", "boost_last_slice_s_blocktime",
                         "boost_last_slice_s_recv"} <= set(pool))
        stamp = {"pool": POOL, "mint": MINT, "t_detect_ms": T0, "block_time": T0 // 1000, "s0_t_recv_ms": T0 - 100_000}
        self.assertEqual(h.parse_shadow_trigger({**trig, **stamp}), (None, "synthetic_unconfirmed"))  # the 3dbe1de record has no class: refused
        t, bad = h.parse_shadow_trigger({**trig, **stamp, "synthetic": False, "synthetic_src": "rpc"})
        self.assertIsNone(bad)
        self.assertEqual(t.s0_wall_ms, T0 - 100_000)

    @unittest.skipUnless((Path(h.__file__).parent / "h5_shadow.py").exists(), "tools/h5_shadow.py (PR #477) is not on this branch yet")
    def test_the_vendored_records_match_the_detector_source_once_it_is_here(self):
        tree = ast.parse((Path(h.__file__).parent / "h5_shadow.py").read_text())
        dicts = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Dict):
                keys = {k.value: v for k, v in zip(node.keys, node.values) if isinstance(k, ast.Constant) and isinstance(k.value, str)}
                if "type" in keys and isinstance(keys["type"], ast.Constant):
                    dicts.append((keys["type"].value, set(keys)))
        for kind in ("trigger", "pool"):
            written = set().union(*[k for t, k in dicts if t == kind])
            need = set(VENDORED[kind]) - {"exit_ladder_trigger_slots"}  # (added after the literal in _fire)
            self.assertTrue(need <= written, f"{kind}: the detector no longer writes {sorted(need - written)}")


class OracleAndPoolRecordTests(Case):
    def append(self, e: Env, *rows: dict, path: Path | None = None) -> None:
        with (path or Path(e.conf["intents_file"])).open("a") as fh:
            for r in rows:
                fh.write(json.dumps(r) + "\n")

    def start(self, **kw) -> Env:
        e = self.env(**kw)
        Path(e.conf["intents_file"]).write_text("")
        e.ex.intent_tick()
        return e

    def test_only_clean_horizon_pda_pool_records_feed_the_boost_halts(self):
        e = self.start()
        ignored = [pool_row("S" * 43 + "1", reason="shutdown"), pool_row("G" * 43 + "2", gap=True), pool_row("B" * 43 + "3", boost_src="behavioural"),
                   pool_row("N" * 43 + "4", boost_src=None), pool_row("U" * 43 + "5", gap=None)]
        counted = [pool_row("H" * 43 + "6"), pool_row("E" * 43 + "7", boost_src="event_authority")]
        self.append(e, *ignored, *counted)
        e.ex.intent_tick()
        self.assertEqual(len(e.ledger("boost_row_ignored")), 5)
        self.assertEqual(sorted(e.ex.counters.day(h.day_key(T0))["boost_s"]), sorted(r["mint"] for r in counted))
        self.assertEqual(len(e.ledger("boost_last_slice")), 2)

    def test_a_pick_is_sticky_in_the_oracle(self):
        a, b = "A" * 43 + "1", "B" * 43 + "2"
        f = self.tmp / "picks.jsonl"
        f.write_text(json.dumps({"mint": a, "pick": True}) + "\n" + json.dumps({"mint": a, "pick": False}) + "\n"
                     + json.dumps({"mint": b, "pick": False}) + "\n" + json.dumps({"mint": b, "pick": True}) + "\n")
        o = h.JsonlPickOracle(f)
        self.assertIs(o(a), True)  # a later false never undoes a true
        self.assertIs(o(b), True)
        with f.open("a") as fh:
            fh.write(json.dumps({"mint": a, "pick": False}) + "\n")
        self.assertIs(o(a), True)

    def test_a_restart_drains_the_file_it_was_reading_before_it_moves_to_the_newest_hour(self):
        d = self.tmp / "shadow"
        d.mkdir()
        old, new = d / "h5-shadow-2026-10-06T14.jsonl", d / "h5-shadow-2026-10-06T15.jsonl"
        old.write_text("")
        e = self.env(intents_file=str(d))
        e.ex.intent_tick()
        self.append(e, pool_row("A" * 43 + "1"), path=old)
        e.ex.intent_tick()
        self.assertEqual(e.ex.counters.tail_path, str(old))  # persisted
        self.append(e, pool_row("B" * 43 + "2"), path=old)  # written while the executor was down ...
        new.write_text(json.dumps(pool_row("C" * 43 + "3")) + "\n")  # ... and the hour rolled
        e.ex = e.build()  # restart
        self.assertEqual(e.ex._tail_path, old)
        e.ex.intent_tick()
        self.assertEqual([r["mint"] for r in e.ledger("boost_last_slice")], ["A" * 43 + "1", "B" * 43 + "2", "C" * 43 + "3"])  # old tail first
        self.assertEqual(e.ex.counters.tail_path, str(new))


if __name__ == "__main__":
    unittest.main()
