"""The tipauth gate (DEC-024 phase 2; quant-proof PR #610 comments 6120633640 / 6120949717 / 6121123397). OFF by default.

Covers: the pure C1-C6 function (each condition passing and each single-condition failure, fail closed on missing / null / wrong-type / exception
records), the wiring in parse_shadow_trigger and the executor (switch off = main's decisions, switch on = one refusal becomes an accept), the
0.02 SOL class stake at every tier, the class loss-budget latch (persisted), the three looks, v2 unavailable, ws == 0 untouched, and a
decision-equivalence replay of a synthetic corpus against main's parser (the file at the commit this change was cut from)."""
from __future__ import annotations

import copy
import dataclasses
import importlib.util
import json
import os
import random
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tools import h5_executor as h
from tools.test_h5_executor import MINT, T0, Case, Clock, Env
from tools.test_h5_executor_shadow import shadow_trigger
from tools.test_h5_executor_tiers import TierCase

SOL = 1_000_000_000
BASE_SHA = "5db78a6"  # main when this change was cut: the reference for the decision-equivalence corpus


def tip_fields(**over) -> dict:
    """The report-only fields of tools/h5_shadow.py (#610) for a trigger that meets C1-C6, with websocket value 1."""
    bt = {"status": "ok", "dropped_n": 1, "dropped_first_slot": 101, "dropped_last_slot": 101, "cover_lo": 100, "cover_hi": 150,
          "kept_ws_only_before_cover": 0, "kept_ws_only_above_cover": 0, "kept_ws_only_sig_on_tip": 0, "dropped_boost_n": 0, "dropped_sells_n": 0,
          "orphaned_by_drop_n": 0, "tipauth_nosettle": 0, "dropped": [["sig", 0, 101]], "margin_slots": 4}
    bu = {"status": "ok", "ws_only": 1, "tip_only": 0, "both": 5, "both_diff": 0}
    rec = {"base_breaks_unresolved_settled": 1, "base_breaks_unresolved_settled_tipauth": 0, "base_union": bu, "base_tipauth": bt, "gap": False,
           "boost_src": "pda"}
    rec.update(over)
    return rec


def good() -> dict:
    return tip_fields()


class GateTests(unittest.TestCase):
    def test_all_conditions_met_passes(self):
        self.assertEqual(h.tipauth_gate(good()), (True, "ok"))
        for n in (1, 2, 3, 4, 5):
            r = good()
            r["base_tipauth"]["dropped_n"] = n
            self.assertTrue(h.tipauth_gate(r)[0], n)
        r = good()
        r["boost_src"] = "event_authority"
        self.assertTrue(h.tipauth_gate(r)[0])
        r = good()
        r["base_breaks_unresolved_settled"] = 7  # any websocket value >= 1
        self.assertTrue(h.tipauth_gate(r)[0])

    def test_each_single_condition_fails_with_its_own_reason(self):
        def mut(f):
            r = good()
            f(r)
            return r

        cases = [
            ("ws0", lambda r: r.update(base_breaks_unresolved_settled=0), "tipauth:ws_not_ge1"),
            ("gap_true", lambda r: r.update(gap=True), "tipauth:gap"),
            ("gap_null", lambda r: r.update(gap=None), "tipauth:gap"),
            ("union_status", lambda r: r["base_union"].update(status="tip_pending"), "tipauth:union_status"),
            ("tipauth_status", lambda r: r["base_tipauth"].update(status="big"), "tipauth:tipauth_status"),
            ("value_1", lambda r: r.update(base_breaks_unresolved_settled_tipauth=1), "tipauth:value_not_zero"),
            ("value_null", lambda r: r.update(base_breaks_unresolved_settled_tipauth=None), "tipauth:value_not_zero"),
            ("tip_only", lambda r: r["base_union"].update(tip_only=1), "tipauth:tip_only"),
            ("both_diff", lambda r: r["base_union"].update(both_diff=1), "tipauth:both_diff"),
            ("dropped_boost", lambda r: r["base_tipauth"].update(dropped_boost_n=1), "tipauth:dropped_boost"),
            ("behavioural", lambda r: r.update(boost_src="behavioural"), "tipauth:boost_src"),
            ("boost_src_null", lambda r: r.update(boost_src=None), "tipauth:boost_src"),
            ("dropped_0", lambda r: r["base_tipauth"].update(dropped_n=0), "tipauth:dropped_n"),
            ("dropped_6", lambda r: r["base_tipauth"].update(dropped_n=6), "tipauth:dropped_n"),
            ("sig_on_tip", lambda r: r["base_tipauth"].update(kept_ws_only_sig_on_tip=1), "tipauth:kept_ws_only_sig_on_tip"),
            ("orphaned", lambda r: r["base_tipauth"].update(orphaned_by_drop_n=1), "tipauth:orphaned_by_drop"),
            ("nosettle", lambda r: r["base_tipauth"].update(tipauth_nosettle=1), "tipauth:nosettle"),
            ("first_eq_lo", lambda r: r["base_tipauth"].update(dropped_first_slot=100), "tipauth:dropped_first_slot"),
            ("first_lt_lo", lambda r: r["base_tipauth"].update(dropped_first_slot=99), "tipauth:dropped_first_slot"),
        ]
        reasons = set()
        for name, f, why in cases:
            ok, got = h.tipauth_gate(mut(f))
            self.assertEqual((ok, got), (False, why), name)
            reasons.add(got)
        self.assertGreaterEqual(len(reasons), 14)  # distinct per failing condition
        ok, got = h.tipauth_gate(good(), rule="v2")
        self.assertEqual((ok, got), (False, "tipauth:rule_not_v1"))

    def test_missing_null_wrong_type_and_exception_records_fail_closed(self):
        used = {"": ["base_breaks_unresolved_settled", "gap", "base_union", "base_tipauth", "base_breaks_unresolved_settled_tipauth", "boost_src"],
                "base_union": ["status", "tip_only", "both_diff"],
                "base_tipauth": ["status", "dropped_boost_n", "dropped_n", "kept_ws_only_sig_on_tip", "orphaned_by_drop_n", "tipauth_nosettle",
                                 "dropped_first_slot", "cover_lo"]}
        for sub, keys in used.items():
            for k in keys:
                for how in ("missing", "null", "string", "bool_true", "float"):
                    r = good()
                    d = r[sub] if sub else r
                    if how == "missing":
                        del d[k]
                    else:
                        d[k] = {"null": None, "string": "ok", "bool_true": True, "float": 0.0}[how]
                    ok, why = h.tipauth_gate(r)
                    # any non-behavioural string is a valid boost_src; everything else here must fail
                    if (sub, k, how) in (("", "boost_src", "string"), ("base_union", "status", "string"), ("base_tipauth", "status", "string")):
                        self.assertTrue(ok)
                        continue
                    self.assertFalse(ok, (sub, k, how))
                    self.assertTrue(why.startswith("tipauth:"), (sub, k, how, why))
        for bad in (None, [], "x", 5, {}):
            self.assertFalse(h.tipauth_gate(bad)[0])
        exc = good()
        exc["base_tipauth"] = {"status": "error", "margin_slots": 4}  # the except path of h5_shadow: no other keys
        exc["base_breaks_unresolved_settled_tipauth"] = None
        self.assertEqual(h.tipauth_gate(exc), (False, "tipauth:tipauth_status"))
        exc2 = good()
        exc2["base_union"] = {"status": "error"}
        self.assertEqual(h.tipauth_gate(exc2), (False, "tipauth:union_status"))
        bare = {"base_breaks_unresolved_settled": 2}
        self.assertEqual(h.tipauth_gate(bare)[0], False)
        self.assertEqual(h.tipauth_gate({"base_breaks_unresolved_settled": 1, "gap": False})[1], "tipauth:union_missing")

    def test_constants_are_the_ruled_ones(self):
        self.assertEqual((h.TIPAUTH_DROPPED_MAX, h.TIPAUTH_STAKE_LAMPORTS, h.TIPAUTH_LOSS_BUDGET_LAMPORTS, h.TIPAUTH_LOOKS),
                         (5, 20_000_000, 40_000_000, (25, 50, 100)))
        self.assertEqual(h.TIPAUTH_ENABLE_REF, "DEC-024-Am8")
        self.assertEqual(h.TIPAUTH_STAKE_LAMPORTS, h.TIERS["T0"]["stake_lamports"])


class ParseTests(unittest.TestCase):
    def row(self, **kw) -> dict:
        r = shadow_trigger(Clock())
        r.update(tip_fields())
        r.update(kw)
        return r

    def test_switch_off_refuses_ws_ge_1_with_main_reason_whatever_the_tip_fields(self):
        for ws in (1, 2, 40):
            t, bad = h.parse_shadow_trigger(self.row(base_breaks_unresolved_settled=ws))
            self.assertEqual((t, bad), (None, "bad_intent:base_breaks_unresolved_settled"))
            self.assertEqual(h.parse_shadow_trigger(self.row(base_breaks_unresolved_settled=ws), "pv", None), (None, bad))

    def test_switch_on_available_accepts_a_passing_trigger_and_tags_it(self):
        t, bad = h.parse_shadow_trigger(self.row(), "pv", "")
        self.assertIsNone(bad)
        self.assertEqual(t.gate_path, "tipauth")
        self.assertEqual(t.base_breaks_unresolved_settled, 1)
        self.assertEqual(t.public()["gate_path"], "tipauth")
        self.assertEqual(t.log_fields()["gate_path"], "tipauth")

    def test_switch_on_failing_condition_gives_the_specific_reason(self):
        r = self.row()
        r["base_tipauth"]["orphaned_by_drop_n"] = 2
        self.assertEqual(h.parse_shadow_trigger(r, "pv", ""), (None, "tipauth:orphaned_by_drop"))

    def test_unavailable_path_returns_its_reason_only_for_ws_ge_1(self):
        self.assertEqual(h.parse_shadow_trigger(self.row(), "pv", "tipauth:latched_off"), (None, "tipauth:latched_off"))
        t, bad = h.parse_shadow_trigger(self.row(base_breaks_unresolved_settled=0), "pv", "tipauth:latched_off")  # ws 0: untouched
        self.assertIsNone(bad)
        self.assertIsNone(t.gate_path)

    def test_non_int_and_negative_ws_keep_main_reason_even_when_on(self):
        for ws in (None, "0", True, 1.0, -1, "1"):
            self.assertEqual(h.parse_shadow_trigger(self.row(base_breaks_unresolved_settled=ws), "pv", ""),
                             (None, "bad_intent:base_breaks_unresolved_settled"), repr(ws))

    def test_ws0_trigger_is_identical_with_and_without_the_path(self):
        r = self.row(base_breaks_unresolved_settled=0)
        a, b = h.parse_shadow_trigger(r), h.parse_shadow_trigger(r, "pv", "")
        self.assertEqual(a, b)
        self.assertNotIn("gate_path", a[0].public())
        self.assertNotIn("gate_path", a[0].log_fields())

    def test_old_path_trigger_public_has_no_gate_path_key(self):
        t, _ = h.parse_shadow_trigger(shadow_trigger(Clock()))
        self.assertNotIn("gate_path", t.public())


def _load_main():
    """main's executor (BASE_SHA), written to a temp module. None when the object is not available in this clone."""
    try:
        src = subprocess.run(["git", "show", f"{BASE_SHA}:tools/h5_executor.py"], capture_output=True, check=True, cwd=Path(__file__).resolve().parents[1]).stdout
    except (OSError, subprocess.CalledProcessError):
        return None
    d = tempfile.mkdtemp()
    p = Path(d) / "h5_executor_main.py"
    p.write_bytes(src)
    spec = importlib.util.spec_from_file_location("h5_executor_main", p)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["h5_executor_main"] = mod
    spec.loader.exec_module(mod)
    return mod


class EquivalenceTests(unittest.TestCase):
    def test_switch_off_decisions_equal_mains_on_a_synthetic_corpus(self):
        main = _load_main()
        if main is None:
            self.skipTest(f"git object {BASE_SHA} not available")
        rng = random.Random(1)
        c = Clock()
        base = shadow_trigger(c)
        base.update(tip_fields(base_breaks_unresolved_settled=0))
        ws_vals = [0, 1, 2, 5, -1, None, "0", True, False, 1.0, 0.0]
        tweaks = [("gap", [True, False, None, "x"]), ("synthetic", [False, True, None]), ("synthetic_src", ["rpc", "ws", None]),
                  ("v_missing", [False, True, None]), ("base_pre", [None, 0, 5, 10**9]), ("sps", [0.4, 0.7, 0.01]), ("slot", [1, 10**9]),
                  ("s0_minus_announced_slots", [None, -1, 0, 5, 10**6]), ("sps_span_s", [None, 1, 100]), ("boost_spent_sol", [None, 0, 17.5, 20.0]),
                  ("variant", ["pv", "fv"]), ("type", ["trigger", "pool"]), ("suppressed", [True, None]), ("t_detect_ms", [1.5, 5, None])]
        n_same = n_refused_ws = n_accept = 0
        for i in range(1500):
            r = copy.deepcopy(base)
            r["base_breaks_unresolved_settled"] = rng.choice(ws_vals)
            for k, vals in rng.sample(tweaks, rng.randint(0, 3)):
                r[k] = rng.choice(vals)
            if rng.random() < 0.3:
                r["base_tipauth"]["status"] = rng.choice(["ok", "big", "tip_pending"])
            if rng.random() < 0.15:
                del r[rng.choice(list(r))]
            if rng.random() < 0.2:
                r.pop("base_tipauth", None)
            a = main.parse_shadow_trigger(copy.deepcopy(r))
            b = h.parse_shadow_trigger(copy.deepcopy(r))
            c2 = h.parse_shadow_trigger(copy.deepcopy(r), "pv", None)
            for new in (b, c2):
                self.assertEqual(a[1], new[1], r)
                self.assertEqual(None if a[0] is None else dataclasses.asdict(a[0]), None if new[0] is None else {k: v for k, v in dataclasses.asdict(new[0]).items() if k != "gate_path"}, r)
                if new[0] is not None:
                    self.assertIsNone(new[0].gate_path)
            n_same += 1
            n_refused_ws += a[1] == "bad_intent:base_breaks_unresolved_settled"
            n_accept += a[0] is not None
        self.assertEqual(n_same, 1500)
        self.assertGreater(n_refused_ws, 100)  # the corpus exercises the refusal this change touches
        self.assertGreater(n_accept, 30)

    def test_with_the_path_on_the_only_change_is_a_ws_refusal_becoming_an_accept_or_a_tipauth_refusal(self):
        main = _load_main()
        if main is None:
            self.skipTest(f"git object {BASE_SHA} not available")
        rng = random.Random(2)
        c = Clock()
        base = shadow_trigger(c)
        base.update(tip_fields())
        changed = 0
        for i in range(1500):
            r = copy.deepcopy(base)
            r["base_breaks_unresolved_settled"] = rng.choice([0, 0, 1, 2, -1, None])
            if rng.random() < 0.5:
                r["base_tipauth"]["dropped_n"] = rng.choice([0, 1, 3, 6])
            if rng.random() < 0.3:
                r["s0_minus_announced_slots"] = rng.choice([None, 0, 10**6])
            a = main.parse_shadow_trigger(copy.deepcopy(r))
            b = h.parse_shadow_trigger(copy.deepcopy(r), "pv", "")
            if a[0] is not None:  # a main accept is never changed (and never un-accepted)
                self.assertIsNotNone(b[0])
                self.assertIsNone(b[0].gate_path)
            elif a[1] != "bad_intent:base_breaks_unresolved_settled":  # any other refusal reason is the same reason
                self.assertEqual(a, (b[0], b[1]))
            else:
                changed += b != a
                if b[0] is None:
                    self.assertTrue(b[1].startswith("tipauth:") or b[1] == a[1] or b[1].startswith("bad_intent:"), b[1])
        self.assertGreater(changed, 50)


class FlowCase(Case):
    """The executor against shadow records, switch off / on."""

    def start(self, **kw) -> Env:
        e = self.env(**kw)
        Path(e.conf["intents_file"]).write_text("")
        e.ex.intent_tick()
        return e

    def append(self, e: Env, *rows: dict) -> None:
        with Path(e.conf["intents_file"]).open("a") as fh:
            for r in rows:
                fh.write(json.dumps(r) + "\n")

    def tip_row(self, e: Env, **kw) -> dict:
        r = shadow_trigger(e.clock)
        r.update(tip_fields())
        r.update(kw)
        return r

    def counters(self, e: Env) -> dict:
        return json.loads(Path(e.ex.counters_path).read_text())


class SwitchOffFlowTests(FlowCase):
    def test_off_refuses_ws_ge_1_exactly_as_main_with_no_tipauth_vocabulary(self):
        for i, conf in enumerate(({}, {"tipauth_gate": "DEC-024-am8"}, {"tipauth_gate": True}, {"tipauth_gate": "yes"}, {"tipauth_gate": ""})):
            with self.subTest(conf=conf):
                e = Env(self.tmp / f"c{i}", **conf)
                Path(e.conf["intents_file"]).write_text("")
                e.ex.intent_tick()
                self.append(e, self.tip_row(e))
                e.ex.intent_tick()
                self.assertEqual(e.refusals(), ["bad_intent:base_breaks_unresolved_settled"])
                self.assertEqual(e.rpc.sent, [])
                self.assertFalse(e.ex._tipauth_switch)

    def test_off_a_ws0_trade_has_no_tipauth_key_anywhere(self):
        e = self.start()
        self.append(e, self.tip_row(e, base_breaks_unresolved_settled=0))
        e.ex.intent_tick()
        e.land_buy()
        for row in e.ledger():
            self.assertNotIn("gate_path", row)
            self.assertNotIn("tipauth", json.dumps(row))
        self.assertNotIn("tipauth", self.counters(e))
        self.assertNotIn("gate_path", json.dumps(self.counters(e)))
        self.assertEqual(e.ledger("decision")[0]["stake_lamports"], h.TIERS["T0"]["stake_lamports"])

    def test_off_the_status_line_says_off(self):
        e = self.start()
        self.append(e, self.tip_row(e, base_breaks_unresolved_settled=0))
        e.ex.intent_tick()
        out = h.status_report(e.conf)
        self.assertIn("tipauth_gate=off tipauth_n=0", out)
        self.assertIn("[live] attempts=1 (lifetime; 1/150 in T0)", out)
        self.assertNotIn("tipauth_gate", out.split("[dryrun]")[0].split("\n")[0])  # not on the header line


class SwitchOnFlowTests(FlowCase):
    ON = {"tipauth_gate": "DEC-024-Am8"}

    def test_on_accepts_a_passing_ws_refusal_tags_every_row_and_sizes_at_0_02(self):
        e = self.start(**self.ON)
        self.append(e, self.tip_row(e))
        e.ex.intent_tick()
        self.assertEqual(e.refusals(), [])
        dec = e.ledger("decision")[0]
        self.assertEqual((dec["gate_path"], dec["stake_lamports"]), ("tipauth", 20_000_000))
        e.land_buy()
        rows = [r for r in e.ledger() if r.get("mint") == MINT and r["kind"] != "alert"]
        self.assertGreaterEqual(len(rows), 2)
        for r in rows:
            self.assertEqual(r.get("gate_path"), "tipauth", r["kind"])
        c = self.counters(e)["tipauth"]
        self.assertEqual((c["n_landed"], c["mints"]), (1, [MINT]))
        self.assertIn("tipauth_gate=on tipauth_n=1", h.status_report(e.conf))

    def test_on_ws0_is_untouched_no_tag_and_the_tier_stake(self):
        e = self.start(**self.ON)
        self.append(e, self.tip_row(e, base_breaks_unresolved_settled=0))
        e.ex.intent_tick()
        dec = e.ledger("decision")[0]
        self.assertNotIn("gate_path", dec)
        e.land_buy()
        self.assertTrue(all("gate_path" not in r for r in e.ledger()))
        self.assertNotIn("tipauth", self.counters(e))

    def test_on_a_failing_condition_logs_the_specific_reason(self):
        e = self.start(**self.ON)
        r = self.tip_row(e)
        r["base_tipauth"]["tipauth_nosettle"] = 1
        self.append(e, r)
        e.ex.intent_tick()
        self.assertEqual(e.refusals(), ["tipauth:nosettle"])
        self.assertEqual(e.rpc.sent, [])

    def test_on_v2_rule_makes_the_path_unavailable(self):
        e = self.start(**self.ON)
        e.ex.exit_mode = h.EXIT_MODE_V2
        self.append(e, self.tip_row(e))
        e.ex.intent_tick()
        self.assertEqual(e.refusals(), ["tipauth:rule_not_v1"])
        self.assertEqual(e.rpc.sent, [])

    def test_a_handed_in_tagged_trigger_is_rechecked(self):
        e = self.start()  # switch off
        trg, _ = h.parse_shadow_trigger(self.tip_row(e), "pv", "")
        e.ex.handle_trigger(trg)
        self.assertEqual(e.refusals(), ["tipauth:unavailable"])
        self.assertEqual(e.rpc.sent, [])

    def test_global_limits_still_apply_max_open_and_stop(self):
        e = self.start(**self.ON)
        (Path(e.conf["state_dir"]) / "STOP").write_text("")
        self.append(e, self.tip_row(e))
        e.ex.intent_tick()
        self.assertEqual(len(e.refusals()), 1)
        self.assertNotIn("tipauth", e.refusals()[0].split(":")[0] if e.refusals()[0].startswith("tipauth:") else "")
        self.assertEqual(e.rpc.sent, [])


class TierStakeTests(TierCase):
    def test_the_class_stake_is_0_02_at_every_tier(self):
        for tier in ("T0", "T1", "T2"):
            with self.subTest(tier=tier):
                self.write_tier(tier + "\n")
                e = Env(self.tmp / tier, tipauth_gate="DEC-024-Am8")
                e.rpc.balance = 10 * SOL
                Path(e.conf["intents_file"]).write_text("")
                e.ex.intent_tick()
                row = shadow_trigger(e.clock)
                row.update(tip_fields())
                with Path(e.conf["intents_file"]).open("a") as fh:
                    fh.write(json.dumps(row) + "\n")
                e.ex.intent_tick()
                dec = e.ledger("decision")
                self.assertEqual(len(dec), 1, tier)
                self.assertEqual((dec[0]["gate_path"], dec[0]["stake_lamports"]), ("tipauth", 20_000_000))
                self.assertEqual(e.ex.tier, tier)
                from tools.test_h5_executor import buy_args
                self.assertEqual(buy_args(e.sent()[0])[0], 20_000_000)
                self.assertEqual(e.ex.h5.stake_lamports, h.TIERS[tier]["stake_lamports"])  # the cap is gone after the trigger
                self.assertIsNone(e.ex._stake_cap)

    def test_a_ws0_trigger_keeps_the_tier_stake(self):
        e = self.tier_env("T1", tipauth_gate="DEC-024-Am8")
        Path(e.conf["intents_file"]).write_text("")
        e.ex.intent_tick()
        row = shadow_trigger(e.clock)
        row.update(tip_fields(base_breaks_unresolved_settled=0))
        with Path(e.conf["intents_file"]).open("a") as fh:
            fh.write(json.dumps(row) + "\n")
        e.ex.intent_tick()
        self.assertEqual(e.ledger("decision")[0]["stake_lamports"], h.TIERS["T1"]["stake_lamports"])


class ClassStateTests(FlowCase):
    ON = {"tipauth_gate": "DEC-024-Am8"}

    def admitted(self, e: Env, mints: list[str]) -> None:
        for m in mints:
            e.ex._tipauth_admit(m)

    def test_budget_latch_trips_at_minus_0_04_logs_once_survives_restart_and_never_clears(self):
        e = self.start(**self.ON)
        self.admitted(e, ["M1"])
        e.ex._book_realized(-39_999_999, "M1")
        self.assertEqual(e.ex._tipauth_availability(), "")
        self.assertEqual(e.ledger("tipauth_off"), [])
        e.ex._book_realized(-1, "M1")  # exactly -0.04 SOL: at or below trips
        self.assertEqual(len(e.ledger("tipauth_off")), 1)
        self.assertEqual(e.ledger("tipauth_off")[0]["reason"], "class_loss_budget")
        self.assertEqual(e.ex._tipauth_availability(), "tipauth:latched_off")
        e.ex._book_realized(+30_000_000, "M1")  # a later gain does not clear it
        e.ex._book_realized(-5, "M1")
        e.ex._tipauth_check_budget()
        self.assertEqual(len(e.ledger("tipauth_off")), 1)
        self.assertEqual(e.ex._tipauth_availability(), "tipauth:latched_off")
        ex2 = e.build()  # restart on the same state dir
        self.assertEqual(ex2._tipauth_availability(), "tipauth:latched_off")
        self.assertIn("tipauth_gate=latched_off", h.status_report(e.conf))
        self.assertEqual(len(e.ledger("tipauth_off")), 1)
        # a trigger now is refused with the latch reason
        self.append(e, self.tip_row(e))
        ex2.intent_tick()
        self.assertEqual(e.refusals()[-1], "tipauth:latched_off")
        self.assertEqual(e.rpc.sent, [])

    def test_untagged_mints_do_not_count_toward_the_class(self):
        e = self.start(**self.ON)
        self.admitted(e, ["M1"])
        e.ex._book_realized(-100_000_000, "OTHER")
        e.ex._book_realized(-100_000_000)
        self.assertEqual(e.ex._tipauth_availability(), "")
        self.assertEqual(int(self.counters(e).get("tipauth", {}).get("realized") or 0), 0)

    def test_the_latch_holds_when_the_switch_is_off_then_on_again(self):
        e = self.start(**self.ON)
        self.admitted(e, ["M1"])
        e.ex._book_realized(-50_000_000, "M1")
        e.conf.pop("tipauth_gate")
        off = e.build()
        self.assertFalse(off._tipauth_switch)
        e.conf["tipauth_gate"] = "DEC-024-Am8"
        on = e.build()
        self.assertEqual(on._tipauth_availability(), "tipauth:latched_off")

    def test_three_looks_counts_only_and_a_pause_until_acked(self):
        e = self.start(**self.ON)
        for i in range(1, 25):
            e.ex._note_tipauth_landed(f"M{i}")
        self.assertEqual(e.ledger("tipauth_look"), [])
        self.assertEqual(e.ex._tipauth_availability(), "")
        e.ex._note_tipauth_landed("M25")
        looks = e.ledger("tipauth_look")
        self.assertEqual([r["look"] for r in looks], [25])
        self.assertEqual(sorted(k for k in looks[0] if k not in ("kind", "ts_ms", "mint", "book", "mode", "schema")), ["action", "look", "n_closed", "n_landed", "n_open"])
        self.assertNotIn("realized", json.dumps(looks[0]))
        self.assertEqual(e.ex._tipauth_availability(), "tipauth:look_pending")
        # it persists across a restart, and a trigger is refused with the reason
        ex2 = e.build()
        self.assertEqual(ex2._tipauth_availability(), "tipauth:look_pending")
        self.append(e, self.tip_row(e))
        ex2.intent_tick()
        self.assertEqual(e.refusals()[-1], "tipauth:look_pending")
        # a smaller ack does not release it; the right one does (config, so a reviewed reinstall)
        e.conf["tipauth_look_ack"] = 24
        self.assertEqual(e.build()._tipauth_availability(), "tipauth:look_pending")
        e.conf["tipauth_look_ack"] = 25
        ex3 = e.build()
        self.assertEqual(ex3._tipauth_availability(), "")
        for i in range(26, 51):
            ex3._note_tipauth_landed(f"M{i}")
        self.assertEqual([r["look"] for r in e.ledger("tipauth_look")], [25, 50])
        self.assertEqual(ex3._tipauth_availability(), "tipauth:look_pending")
        e.conf["tipauth_look_ack"] = 50
        ex4 = e.build()
        for i in range(51, 101):
            ex4._note_tipauth_landed(f"M{i}")
        self.assertEqual([r["look"] for r in e.ledger("tipauth_look")], [25, 50, 100])
        self.assertEqual(ex4._tipauth_availability(), "tipauth:look_pending")
        e.conf["tipauth_look_ack"] = 100
        self.assertEqual(e.build()._tipauth_availability(), "")
        self.assertEqual(self.counters(e)["tipauth"]["n_landed"], 100)

    def test_a_look_is_written_once_even_if_the_count_is_replayed(self):
        e = self.start(**self.ON)
        e.ex.counters.tipauth.update({"n_landed": 24})
        e.ex._note_tipauth_landed("A")
        e.ex.counters.tipauth["n_landed"] = 24
        e.ex._note_tipauth_landed("B")
        self.assertEqual(len(e.ledger("tipauth_look")), 1)

    def test_an_end_to_end_loss_trips_the_budget_through_the_real_booking_path(self):
        e = self.start(**self.ON)
        self.append(e, self.tip_row(e))
        e.ex.intent_tick()
        e.land_buy()
        self.assertEqual(self.counters(e)["tipauth"]["n_landed"], 1)
        e.ex._book_fee(MINT, 45_000_000, "test")  # a failed sell's fee booked as realized loss for the tagged mint
        self.assertEqual(len(e.ledger("tipauth_off")), 1)
        self.assertEqual(e.ex._tipauth_availability(), "tipauth:latched_off")


if __name__ == "__main__":
    unittest.main()
