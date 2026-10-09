"""The executor and the synthetic-migration class (owner-approved, quant-proof ruled): "the executor and the shadow apply the same classifier at
decision time; synthetic or unclassifiable pools get no buy".

The shadow classifies. A trigger it lets through says `synthetic: false` (and `synthetic_src`); a pool it keeps out gets an `excluded` record. This
executor does not re-derive the class, so the one thing it must guarantee is that it never acts on a trigger that does not say `false`: absent
(a shadow from before the classifier), null, true or a non-bool are all refused as `synthetic_unconfirmed`. Offline: fake RPC, fake clock."""

from __future__ import annotations

import ast
import json
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from tools import h5_executor as h
from tools.test_h5_executor import BASE0, MINT, POOL, Q, S0, TRIG_SLOT, Case, Clock, Env, buy_args, trig
from tools.test_h5_executor_shadow import pool_row, shadow_trigger

OTHER_POOL, OTHER_MINT = "O" * 43 + "1", "P" * 43 + "2"


def excluded(reason: str = "synthetic", **kw) -> dict:
    """An `excluded` record as the classifying shadow writes it: no price, no outcome, only who and where."""
    return {"v": 1, "schema": "h5_shadow_v1", "type": "excluded", "reason": reason, "pool": POOL, "mint": MINT, "s0": S0, "slot": TRIG_SLOT, **kw}


def without(row: dict, *keys: str) -> dict:
    return {k: v for k, v in row.items() if k not in keys}


class Feed(Case):
    def append(self, e: Env, *rows: dict) -> None:
        with Path(e.conf["intents_file"]).open("a") as fh:
            for r in rows:
                fh.write(json.dumps(r) + "\n")

    def start(self, **kw) -> Env:
        e = self.env(**kw)
        Path(e.conf["intents_file"]).write_text("")
        e.ex.intent_tick()
        return e

    def counters(self, e: Env) -> h.H5Counters:
        return h.H5Counters.load(Path(e.ex.counters_path), e.ex.run_mode)


class ConfirmedTriggerTests(Feed):
    def test_a_trigger_with_synthetic_false_is_accepted_exactly_as_before(self):
        e = self.start()
        self.append(e, shadow_trigger(e.clock))  # synthetic false, synthetic_src "ws"
        self.assertEqual(e.ex.intent_tick(), 1)
        spend, min_out = buy_args(e.sent()[0])
        terms = h.entry_terms(Q, BASE0, e.ex.h5.stake_lamports, 1500)
        self.assertEqual((spend, min_out), (e.ex.h5.stake_lamports, terms["min_out"]))  # the same buy the unclassified trigger made
        self.assertEqual(e.refusals(), [])
        dec = e.ledger("decision")[0]
        self.assertEqual((dec["q_lamports"], dec["trigger_slot"]), (Q, TRIG_SLOT))
        self.assertEqual((dec["synthetic"], dec["synthetic_src"]), (False, "ws"))  # the class and its source are on the decision row
        self.assertEqual(self.counters(e).synthetic_unconfirmed, 0)

    def test_every_documented_source_is_carried_and_the_source_is_never_a_gate(self):
        for src in ("ws", "rpc", "pre_event_binary"):
            t, bad = h.parse_shadow_trigger(shadow_trigger(Clock(), synthetic_src=src))
            self.assertEqual((bad, t.synthetic, t.synthetic_src), (None, False, src))
        for src in (None, "", "x" * 33, "ws rpc", "a;b", 7, ["ws"]):  # an odd or missing source is dropped from the ledger row, the trigger stays tradable
            t, bad = h.parse_shadow_trigger(shadow_trigger(Clock(), synthetic_src=src))
            self.assertEqual((bad, t.synthetic, t.synthetic_src), (None, False, None), src)
        r = without(shadow_trigger(Clock()), "synthetic_src")
        t, bad = h.parse_shadow_trigger(r)
        self.assertEqual((bad, t.synthetic_src), (None, None))

    def test_a_confirmed_flat_intent_row_is_accepted_and_carries_the_class(self):
        e = self.env()
        e.fire()  # tools.test_h5_executor.row() sets synthetic=False
        self.assertEqual(len(e.rpc.sent), 1)
        self.assertIs(e.ledger("decision")[0]["synthetic"], False)


class UnconfirmedTriggerTests(Feed):
    VALUES = (("absent", None), ("null", None), ("true", True), ("zero", 0), ("one", 1), ("string_false", "false"), ("string_False", "False"),
              ("empty_list", []))

    def record(self, e: Env, name: str, value) -> dict:
        r = shadow_trigger(e.clock)
        return without(r, "synthetic", "synthetic_src") if name == "absent" else {**r, "synthetic": value}

    def test_absent_null_true_and_non_bool_are_each_refused_and_never_sent(self):
        for name, value in self.VALUES:
            sub = self.tmp / name
            sub.mkdir()
            e = Env(sub)
            Path(e.conf["intents_file"]).write_text("")
            e.ex.intent_tick()
            self.append(e, self.record(e, name, value))
            self.assertEqual(e.ex.intent_tick(), 0, name)
            self.assertEqual((e.refusals(), e.rpc.sent, e.ledger("decision"), e.ledger("buy")), (["synthetic_unconfirmed"], [], [], []), name)
            self.assertEqual(e.ex.state.attempts, 0, name)  # not even an attempt
            self.assertEqual(self.counters(e).synthetic_unconfirmed, 1, name)

    def test_the_parsers_return_the_reason_for_each_value(self):
        c = Clock()
        for name, value in self.VALUES:
            r = shadow_trigger(c)
            r = without(r, "synthetic") if name == "absent" else {**r, "synthetic": value}
            self.assertEqual(h.parse_shadow_trigger(r), (None, "synthetic_unconfirmed"), name)

    def test_a_flat_intent_row_is_held_to_the_same_rule(self):
        e = self.env()
        flat = {"schema": "h5_intent_v1", "mint": MINT, "pool": POOL, "s0_slot": S0, "sps": 0.2, "trigger_slot": TRIG_SLOT, "q_lamports": Q,
                "base_reserve": BASE0, "v_lamports": 17_580_000_000, "decision_ms": e.clock()}
        for extra in ({}, {"synthetic": None}, {"synthetic": True}, {"synthetic": "false"}):
            self.assertEqual(h.parse_trigger({**flat, **extra}), (None, "synthetic_unconfirmed"), extra)
        t, bad = h.parse_trigger({**flat, "synthetic": False})
        self.assertEqual((bad, t.synthetic), (None, False))
        path = Path(e.conf["intents_file"])
        path.write_text("")
        e.ex.intent_tick()
        self.append(e, flat)
        e.ex.intent_tick()
        self.assertEqual((e.refusals(), e.rpc.sent), (["synthetic_unconfirmed"], []))

    def test_a_dry_run_refuses_the_same_way_and_records_no_decision(self):
        e = self.start(live=False)
        self.append(e, shadow_trigger(e.clock, synthetic=None), without(shadow_trigger(e.clock, pool=OTHER_POOL, mint=OTHER_MINT), "synthetic"))
        e.ex.intent_tick()
        self.assertEqual((e.refusals(), e.ledger("decision"), e.rpc.sent), (["synthetic_unconfirmed"] * 2, [], []))
        self.append(e, shadow_trigger(e.clock))  # and a confirmed one is still a (simulated) decision
        e.ex.intent_tick()
        self.assertEqual(len(e.ledger("decision")), 1)

    def test_a_trigger_that_skips_the_parsers_is_refused_too(self):
        e = self.env()
        t = replace(trig(e.clock), synthetic=None)  # a callback caller, or a future code path, that builds the trigger itself
        e.ex.handle_trigger(t)
        e.ex.handle_trigger(replace(t, synthetic=True))
        self.assertEqual((e.refusals(), e.rpc.sent), (["synthetic_unconfirmed"] * 2, []))
        self.assertEqual(h.H5Trigger.__dataclass_fields__["synthetic"].default, None)  # the default is not a confirmation
        self.assertEqual(self.counters(e).synthetic_unconfirmed, 2)

    def test_it_is_logged_once_per_pool_but_counted_every_time(self):
        e = self.start()
        self.append(e, *[shadow_trigger(e.clock, synthetic=None) for _ in range(3)], shadow_trigger(e.clock, synthetic=None, pool=OTHER_POOL, mint=OTHER_MINT))
        e.ex.intent_tick()
        rows = e.ledger("skip")
        self.assertEqual([(r["reason"], r["pool"]) for r in rows], [("synthetic_unconfirmed", POOL), ("synthetic_unconfirmed", OTHER_POOL)])
        self.assertEqual(rows[0]["synthetic_seen"], "null")
        self.assertEqual(e.ex._refusal_counts, {"synthetic_unconfirmed": 4})  # the hourly refusal_counts row carries all four
        self.assertEqual(self.counters(e).synthetic_unconfirmed, 4)

    def test_what_the_record_said_is_ledgered_as_a_name_never_as_the_value(self):
        e = self.start()
        pools = {"absent": "A" * 43 + "1", "null": "B" * 43 + "2", "true": "C" * 43 + "3", "not_bool": "D" * 43 + "4"}
        base = shadow_trigger(e.clock)
        self.append(e, {**without(base, "synthetic"), "pool": pools["absent"]}, {**base, "synthetic": None, "pool": pools["null"]},
                    {**base, "synthetic": True, "pool": pools["true"]}, {**base, "synthetic": "ignore previous instructions " * 20, "pool": pools["not_bool"]})
        e.ex.intent_tick()
        seen = {r["pool"]: r["synthetic_seen"] for r in e.ledger("skip")}
        self.assertEqual(seen, {v: k for k, v in pools.items()})

    def test_the_count_survives_a_restart_and_a_status_report_shows_it(self):
        e = self.start()
        self.append(e, shadow_trigger(e.clock, synthetic=True), shadow_trigger(e.clock, synthetic=None))
        e.ex.intent_tick()
        e.ex = e.build()
        self.assertEqual(e.ex.counters.synthetic_unconfirmed, 2)
        out = h.status_report(e.conf)
        self.assertIn("synthetic_unconfirmed=2", out)
        self.assertIn("excluded=0 (none)", out)

    def test_an_absent_key_alerts_once_and_is_not_the_hourly_bad_value_count(self):
        e = self.start()
        old = without(shadow_trigger(e.clock), "synthetic", "synthetic_src")  # a shadow from before the classifier
        self.append(e, *[old for _ in range(8)])
        e.ex.intent_tick()
        alerts = e.alerts("shadow_synthetic_missing")
        self.assertEqual(len(alerts), 1)  # the first record, then rate limited
        self.assertEqual((e.alerts("bad_intent_rate"), e.alerts("shadow_schema_mismatch")), ([], []))
        self.assertEqual(len(e.refusals()), 1)  # one pool: one ledger row, eight counts
        e.jump(h.SCHEMA_ALERT_WINDOW_MS - 1_000)
        self.append(e, without(shadow_trigger(e.clock), "synthetic"))
        e.ex.intent_tick()
        self.assertEqual(len(e.alerts("shadow_synthetic_missing")), 1)
        e.jump(2_000)
        self.append(e, without(shadow_trigger(e.clock), "synthetic"))
        e.ex.intent_tick()
        self.assertEqual(len(e.alerts("shadow_synthetic_missing")), 2)
        self.assertEqual(e.rpc.sent, [])

    def test_a_present_but_wrong_value_counts_like_any_bad_value(self):
        e = self.start()
        self.append(e, *[shadow_trigger(e.clock, synthetic=None) for _ in range(h.BAD_INTENT_ALERT_N)])
        e.ex.intent_tick()
        self.assertEqual((e.alerts("bad_intent_rate"), e.alerts("shadow_synthetic_missing")), ([], []))
        self.append(e, shadow_trigger(e.clock, synthetic=True))
        e.ex.intent_tick()
        alerts = e.alerts("bad_intent_rate")
        self.assertEqual((len(alerts), alerts[0]["last_reason"]), (1, "synthetic_unconfirmed"))
        self.assertEqual(e.alerts("shadow_synthetic_missing"), [])

    def test_the_sealed_stub_keeps_its_own_reason(self):
        stub = {"type": "trigger", "variant": "pv", "pool": POOL, "slot": TRIG_SLOT, "suppressed": "cap_pick_seal", "v": 1, "schema": "h5_shadow_v1"}
        self.assertEqual(h.parse_shadow_trigger(stub), (None, "bad_intent:suppressed"))  # the daily check treats this one as expected

    def test_an_older_shadow_that_also_lacks_other_keys_is_still_a_schema_mismatch_first(self):
        r = without(shadow_trigger(Clock()), "synthetic", "base_breaks_unresolved_settled")
        self.assertEqual(h.parse_shadow_trigger(r), (None, "bad_intent:missing_base_breaks_unresolved_settled"))


class ExcludedRecordTests(Feed):
    def test_an_excluded_record_raises_no_alert_no_halt_and_no_ledger_row(self):
        e = self.start()
        before = e.ledger()
        self.append(e, excluded("synthetic"), excluded("unclassified", pool=OTHER_POOL, mint=OTHER_MINT))
        self.assertEqual(e.ex.intent_tick(), 0)
        self.assertEqual((e.alerts(), e.ex.counters.halts, e.ex._crit, e.rpc.sent), ([], {}, False, []))
        self.assertEqual(e.ledger(), before)  # quiet: nothing written, not even a skip row
        self.assertEqual(e.refusals(), [])
        self.assertEqual(e.ex.counters.excluded, {"synthetic": 1, "unclassified": 1})
        self.assertEqual(self.counters(e).excluded, {"synthetic": 1, "unclassified": 1})  # on disk for --status
        self.assertEqual(self.counters(e).synthetic_unconfirmed, 0)

    def test_it_is_not_a_schema_mismatch_even_in_a_flood(self):
        e = self.start()
        self.append(e, *[excluded("synthetic", pool=f"X{i:03d}" + "1" * 39, mint=f"Y{i:03d}" + "2" * 39) for i in range(50)])
        e.ex.intent_tick()
        self.assertEqual((e.alerts("shadow_schema_mismatch"), e.alerts("bad_intent_rate"), e.alerts()), ([], [], []))
        self.assertEqual(e.ex.counters.excluded, {"synthetic": 50})

    def test_an_unknown_reason_or_a_sparse_record_is_counted_as_other_and_never_raises(self):
        e = self.start()
        self.append(e, excluded("novel_reason"), excluded(None), {"type": "excluded"}, {"type": "excluded", "reason": ["x"], "pool": 5, "mint": None})
        e.ex.intent_tick()
        self.assertEqual(e.ex.counters.excluded, {"other": 4})
        self.assertEqual(e.alerts(), [])

    def test_a_good_trigger_on_another_pool_is_not_affected(self):
        e = self.start()
        self.append(e, excluded("synthetic", pool=OTHER_POOL, mint=OTHER_MINT), shadow_trigger(e.clock))
        self.assertEqual(e.ex.intent_tick(), 1)
        self.assertEqual((len(e.rpc.sent), e.refusals()), (1, []))

    def test_a_trigger_on_an_excluded_pool_or_mint_is_refused_whatever_it_says(self):
        for what in ("pool", "mint"):
            sub = self.tmp / what
            sub.mkdir()
            e = Env(sub)
            Path(e.conf["intents_file"]).write_text("")
            e.ex.intent_tick()
            self.append(e, excluded("synthetic", **{("mint" if what == "pool" else "pool"): OTHER_POOL}), shadow_trigger(e.clock))  # only one of the two ids matches
            e.ex.intent_tick()
            self.assertEqual((e.refusals(), e.rpc.sent, e.ledger("decision")), (["excluded_pool"], [], []), what)

    def test_the_remembered_set_is_bounded_and_drops_the_oldest(self):
        with mock.patch.object(h, "EXCLUDED_KEEP", 20):
            e = self.start()
            self.append(e, *[excluded("synthetic", pool=f"P{i:03d}" + "1" * 39, mint=f"M{i:03d}" + "2" * 39) for i in range(30)])
            e.ex.intent_tick()
            self.assertEqual(len(e.ex.excluded_pools), 20)
            self.assertNotIn("P000" + "1" * 39, e.ex.excluded_pools)
            self.assertIn("M029" + "2" * 39, e.ex.excluded_pools)
            self.assertEqual(e.ex.counters.excluded, {"synthetic": 30})  # the count is not bounded

    def test_the_status_report_shows_the_counts_by_reason(self):
        e = self.start()
        self.append(e, excluded("synthetic"), excluded("synthetic", pool=OTHER_POOL), excluded("unclassified", pool="Z" * 43 + "3"))
        e.ex.intent_tick()
        self.append(e, shadow_trigger(e.clock, synthetic=None, pool="Q" * 43 + "4"))
        e.ex.intent_tick()
        out = h.status_report(e.conf)
        self.assertIn("excluded=3 (synthetic:2,unclassified:1)", out)
        self.assertIn("synthetic_unconfirmed=1", out)
        self.assertNotIn("http", out)

    def test_a_counters_file_from_before_the_change_loads_with_zero_counts(self):
        e = self.start()
        p = Path(e.ex.counters_path)
        raw = json.loads(p.read_text())
        raw.pop("synthetic_unconfirmed")
        raw.pop("excluded")
        p.write_text(json.dumps(raw))
        c = h.H5Counters.load(p, e.ex.run_mode)
        self.assertEqual((c.synthetic_unconfirmed, c.excluded), (0, {}))
        self.assertIn("synthetic_unconfirmed=0 excluded=0 (none)", h.status_report(e.conf))


class PoolRecordTests(Feed):
    """The pool close record gains `synthetic: true/false/null`. The BOOST timing halts read the same fields as before."""

    MINTS = [f"S{i:02d}" + "1" * 40 for i in range(h.BOOST_MEDIAN_MIN_POOLS)]
    VARIANTS = (True, False, None, "x", 0)

    def run_pools(self, name: str, secs: float, with_field: bool) -> Env:
        sub = self.tmp / name
        sub.mkdir()
        e = Env(sub)
        Path(e.conf["intents_file"]).write_text("")
        e.ex.intent_tick()
        rows = []
        for i, m in enumerate(self.MINTS):
            r = pool_row(m, boost_last_slice_s=secs)
            rows.append({**r, "synthetic": self.VARIANTS[i % len(self.VARIANTS)]} if with_field else r)
        self.append(e, *rows)
        e.ex.intent_tick()
        return e

    def test_the_boost_median_halt_is_unchanged_by_the_new_field(self):
        a, b = self.run_pools("with", 334.0, True), self.run_pools("without", 334.0, False)
        for e in (a, b):
            self.assertEqual(list(e.ex.counters.halts), ["boost_median_lt_335"])
        self.assertEqual(a.ledger("boost_row_ignored"), [])
        self.assertEqual(sorted(a.ex.counters.day(h.day_key(a.clock()))["boost_s"]), sorted(self.MINTS))  # synthetic pools are in the median, as before

    def test_a_healthy_day_does_not_halt_and_has_the_same_median(self):
        a, b = self.run_pools("with", 341.0, True), self.run_pools("without", 341.0, False)
        self.assertEqual((a.ex.counters.halts, b.ex.counters.halts), ({}, {}))
        def day(e: Env) -> dict:
            return e.ex.counters.day(h.day_key(e.clock()))

        self.assertEqual((day(a)["boost_median"], day(a)["boost_s"]), (day(b)["boost_median"], day(b)["boost_s"]))
        self.assertEqual(len(a.ledger("boost_last_slice")), len(self.MINTS))
        self.assertEqual(a.alerts(), [])

    def test_a_pool_record_is_not_a_trigger_and_not_a_refusal(self):
        e = self.start()
        self.append(e, pool_row(MINT, boost_last_slice_s=341.0, synthetic=True), pool_row(OTHER_MINT, boost_last_slice_s=341.0, synthetic=None))
        self.assertEqual(e.ex.intent_tick(), 0)
        self.assertEqual((e.refusals(), e.alerts(), self.counters(e).synthetic_unconfirmed), ([], [], 0))


class DetectorContractTests(unittest.TestCase):
    SRC = Path(h.__file__).parent / "h5_shadow.py"

    @staticmethod
    def constants(path: Path) -> set[str]:
        return {n.value for n in ast.walk(ast.parse(path.read_text())) if isinstance(n, ast.Constant) and isinstance(n.value, str)}

    @unittest.skipUnless(SRC.exists() and "excluded" in constants.__func__(SRC), "tools/h5_shadow.py has no `excluded` record yet (the classifying shadow PR)")
    def test_the_detector_writes_what_the_executor_gates_on_once_it_classifies(self):
        consts = self.constants(self.SRC)
        self.assertTrue({"synthetic", "synthetic_src", "excluded", "unclassified"} <= consts, {"synthetic", "synthetic_src", "excluded", "unclassified"} - consts)


if __name__ == "__main__":
    unittest.main()
