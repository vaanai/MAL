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
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools import h5_executor as h
from tools.test_h5_executor import MINT, T0, TRIG_SLOT, Case, Clock, Env
from tools.test_h5_executor_shadow import shadow_trigger
from tools.test_h5_executor_tiers import TierCase

SOL = 1_000_000_000
BASE_SHA = "13e3f28"  # main at the second merge into this branch: the reference for the decision-equivalence corpus and the off-path flow
WS = "bad_intent:base_breaks_unresolved_settled"  # the websocket refusal: the reason of every skip row this path touches, byte for byte (R3)
MISSING = object()


def tip_fields(**over) -> dict:
    """The report-only fields of tools/h5_shadow.py (#610) for a trigger that meets C1-C6, with websocket value 1."""
    S = TRIG_SLOT  # the slot of shadow_trigger(): cover_hi = min(tip high-water, slot) - 4 = S - 4 (C7)
    bt = {"status": "ok", "dropped_n": 1, "dropped_first_slot": S - 20, "dropped_last_slot": S - 20, "cover_lo": S - 50, "cover_hi": S - 4,
          "kept_ws_only_before_cover": 0, "kept_ws_only_above_cover": 0, "kept_ws_only_sig_on_tip": 0, "dropped_boost_n": 0, "dropped_sells_n": 0,
          "orphaned_by_drop_n": 0, "tipauth_nosettle": 0, "dropped": [["sig", 0, S - 20]], "margin_slots": 4}
    bu = {"status": "ok", "ws_only": 1, "tip_only": 0, "both": 5, "both_diff": 0, "tip_first_slot": S - 50, "tip_hw_slot_global": S + 3,
          "tip_lag_slots_global": 1}
    rec = {"variant": "pv", "slot": S, "base_breaks_unresolved_settled": 1, "base_breaks_unresolved_settled_union": 1, "base_breaks_unresolved_settled_tipauth": 0,
           "base_union": bu, "base_tipauth": bt, "gap": False, "boost_src": "pda"}
    rec.update(over)
    return rec


def good() -> dict:
    return tip_fields()


def with_n(n: int) -> dict:
    """A passing record whose D has n members (distinct signatures, increasing slots), first / last slot and the list agreeing."""
    S = TRIG_SLOT
    r = good()
    bt = r["base_tipauth"]
    bt["dropped_n"] = n
    bt["dropped"] = [[f"sig{i}", i, S - 20 + i] for i in range(n)]
    bt["dropped_first_slot"], bt["dropped_last_slot"] = S - 20, S - 20 + n - 1
    return r


def reason(r: dict, **kw) -> str:
    return h.tipauth_gate(r, **kw)[1]


def bt_set(**kw) -> dict:
    r = good()
    r["base_tipauth"].update(kw)
    return r


def bu_set(**kw) -> dict:
    r = good()
    r["base_union"].update(kw)
    return r


class GateTests(unittest.TestCase):
    def test_all_conditions_met_passes(self):
        self.assertEqual(h.tipauth_gate(good()), (True, "ok"))
        for n in (1, 2, 3, 4, 5):
            self.assertEqual(h.tipauth_gate(with_n(n)), (True, "ok"), n)
        r = good()
        r["boost_src"] = "event_authority"
        self.assertTrue(h.tipauth_gate(r)[0])
        r = good()
        r["base_breaks_unresolved_settled"] = r["base_breaks_unresolved_settled_union"] = 7  # any websocket value >= 1 (the union equals it, R2 e)
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
            ("first_eq_lo", lambda r: r["base_tipauth"].update(dropped_first_slot=TRIG_SLOT - 50), "tipauth:dropped_first_slot"),
            ("first_lt_lo", lambda r: r["base_tipauth"].update(dropped_first_slot=TRIG_SLOT - 51), "tipauth:dropped_first_slot"),
        ]
        reasons = set()
        for name, f, why in cases:
            ok, got = h.tipauth_gate(mut(f))
            self.assertEqual((ok, got), (False, why), name)
            reasons.add(got)
        self.assertGreaterEqual(len(reasons), 14)  # distinct per failing condition
        ok, got = h.tipauth_gate(good(), rule="v2")
        self.assertEqual((ok, got), (False, "tipauth:rule_not_v1"))

    def test_every_field_the_gate_reads_fails_closed_on_twelve_substitutions(self):
        """The shape of quant-proof's section-1 run: single-field substitutions on a passing record. Every one refuses, each with a tipauth: reason."""
        paths = [("", k) for k in ("slot", "base_breaks_unresolved_settled", "base_breaks_unresolved_settled_union", "base_breaks_unresolved_settled_tipauth",
                                   "gap", "boost_src", "base_union", "base_tipauth")]
        paths += [("base_union", k) for k in ("status", "tip_only", "both_diff", "tip_first_slot", "tip_hw_slot_global")]
        paths += [("base_tipauth", k) for k in ("status", "dropped_boost_n", "dropped_n", "kept_ws_only_sig_on_tip", "orphaned_by_drop_n", "tipauth_nosettle",
                                                 "dropped_first_slot", "dropped_last_slot", "cover_lo", "cover_hi", "margin_slots", "dropped")]
        values = [MISSING, None, True, False, 0.0, 1.0, "0", "1", -1, [], {}, float("nan")]
        n = 0
        for sub, k in paths:
            for v in values:
                r = good()
                d = r[sub] if sub else r
                base = d[k]
                if v is not MISSING and type(v) is type(base) and v == base:
                    continue  # the same value as the passing record's
                if v is MISSING:
                    del d[k]
                else:
                    d[k] = v
                ok, why = h.tipauth_gate(r)
                self.assertFalse(ok, (sub, k, v))
                self.assertTrue(why.startswith("tipauth:"), (sub, k, v, why))
                n += 1
        self.assertGreaterEqual(n, 200)

    def test_non_record_inputs_and_the_error_records_of_the_shadow_fail_closed(self):
        for bad in (None, [], "x", 5, {}):
            self.assertEqual(h.tipauth_gate(bad)[0], False)
        exc = good()
        exc["base_tipauth"] = {"status": "error", "margin_slots": 4}  # the except path of h5_shadow: no other keys
        exc["base_breaks_unresolved_settled_tipauth"] = None
        self.assertEqual(h.tipauth_gate(exc), (False, "tipauth:tipauth_status"))
        exc2 = good()
        exc2["base_union"] = {"status": "error"}
        self.assertEqual(h.tipauth_gate(exc2), (False, "tipauth:union_status"))
        self.assertEqual(h.tipauth_gate({"base_breaks_unresolved_settled": 2})[0], False)
        self.assertEqual(h.tipauth_gate({"base_breaks_unresolved_settled": 1, "gap": False})[1], "tipauth:union_missing")
        for ws in (0, -1, None, 1.0, True, "1"):
            r = good()
            r["base_breaks_unresolved_settled"] = ws
            self.assertEqual(reason(r), "tipauth:ws_not_ge1", ws)

    def test_dropped_n_boundaries_0_1_5_6(self):
        r = good()
        r["base_tipauth"]["dropped_n"] = 0
        self.assertEqual(reason(r), "tipauth:dropped_n")
        self.assertTrue(h.tipauth_gate(with_n(1))[0])
        self.assertTrue(h.tipauth_gate(with_n(5))[0])
        self.assertEqual(reason(with_n(6)), "tipauth:dropped_n")

    # --- R1: the boost_src allowlist ---------------------------------------------------------------------------------------
    def test_r1_boost_src_is_an_allowlist(self):
        for ok_src in ("pda", "event_authority"):
            self.assertEqual(h.tipauth_gate({**good(), "boost_src": ok_src}), (True, "ok"))
        for bad in ("none", "behavioural", "", "PDA", "0", "1", None, 0, 1, True, ["pda"], {"pda": 1}):
            self.assertEqual(reason({**good(), "boost_src": bad}), "tipauth:boost_src", repr(bad))
        r = good()
        del r["boost_src"]
        self.assertEqual(reason(r), "tipauth:boost_src")

    # --- R2 (a) to (f): one test per reason string -------------------------------------------------------------------------
    def test_r2a_margin(self):
        self.assertEqual(h.TIPAUTH_MARGIN_SLOTS, 4)
        for bad in (0, 3, 5, 4.0, True, "4", None, -4):
            self.assertEqual(reason(bt_set(margin_slots=bad)), "tipauth:margin", repr(bad))
        r = good()
        del r["base_tipauth"]["margin_slots"]
        self.assertEqual(reason(r), "tipauth:margin")
        self.assertEqual(h.tipauth_gate(bt_set(margin_slots=4)), (True, "ok"))

    def test_r2b_slots(self):
        S = TRIG_SLOT
        no_slot = good()
        del no_slot["slot"]
        no_last = good()
        del no_last["base_tipauth"]["dropped_last_slot"]
        cases = {
            "slot_missing": no_slot, "slot_null": {**good(), "slot": None}, "slot_float": {**good(), "slot": float(S)}, "slot_bool": {**good(), "slot": True},
            "hi_one_under_margin_top": bt_set(cover_hi=S - 3), "hi_above_slot": bt_set(cover_hi=S + 10), "hi_null": bt_set(cover_hi=None),
            "hi_below_last": bt_set(cover_hi=S - 21), "hi_below_lo": bt_set(cover_hi=S - 60), "lo_negative": bt_set(cover_lo=-1),
            "last_below_first": bt_set(dropped_last_slot=S - 21), "last_missing": no_last,
            "last_null": bt_set(dropped_last_slot=None), "last_above_hi": bt_set(dropped_last_slot=S - 3),
        }
        for name, r in cases.items():
            self.assertEqual(reason(r), "tipauth:slots", name)
        # boundaries that pass: hi at slot-4, last at hi, first one above lo, lo at 0
        r = good()
        r["base_tipauth"].update(dropped_first_slot=S - 4, dropped_last_slot=S - 4, dropped=[["sig", 0, S - 4]])
        self.assertEqual(h.tipauth_gate(r), (True, "ok"))  # dropped_last_slot == cover_hi
        r = good()
        r["base_tipauth"].update(dropped_first_slot=S - 49, dropped_last_slot=S - 49, dropped=[["sig", 0, S - 49]])
        self.assertEqual(h.tipauth_gate(r), (True, "ok"))  # first == lo + 1
        r = good()
        r["base_tipauth"]["cover_lo"] = 0
        r["base_union"]["tip_first_slot"] = 0
        self.assertEqual(h.tipauth_gate(r), (True, "ok"))  # lo == 0

    def test_r2c_cover(self):
        S = TRIG_SLOT
        no_hw = good()
        del no_hw["base_union"]["tip_hw_slot_global"]
        no_first = good()
        del no_first["base_union"]["tip_first_slot"]
        cases = {
            "first_ne_lo": bu_set(tip_first_slot=S - 49), "first_null": bu_set(tip_first_slot=None), "first_float": bu_set(tip_first_slot=float(S - 50)),
            "first_bool": bu_set(tip_first_slot=True), "hw_null": bu_set(tip_hw_slot_global=None), "hw_float": bu_set(tip_hw_slot_global=float(S + 3)),
            "hw_str": bu_set(tip_hw_slot_global="x"), "hw_bool": bu_set(tip_hw_slot_global=True),
            "hw_below_slot_hi_not_min": bu_set(tip_hw_slot_global=S - 1),  # min(hw, slot) - 4 = S - 5, the record says S - 4
            "hi_ne_min": bt_set(cover_hi=S - 5), "hw_missing": no_hw, "first_missing": no_first,
        }
        for name, r in cases.items():
            self.assertEqual(reason(r), "tipauth:cover", name)
        for hw in (S, S + 1, S + 10**6):  # the high-water at or above the slot: the slot binds
            self.assertEqual(h.tipauth_gate(bu_set(tip_hw_slot_global=hw)), (True, "ok"), hw)
        r = good()  # the high-water below the slot binds instead, when the record says so
        r["base_union"]["tip_hw_slot_global"] = S - 2
        r["base_tipauth"]["cover_hi"] = S - 6
        self.assertEqual(h.tipauth_gate(r), (True, "ok"))

    def test_r2d_dropped_list(self):
        S = TRIG_SLOT
        r = good()
        del r["base_tipauth"]["dropped"]
        self.assertEqual(reason(r), "tipauth:dropped_list")
        cases = {"null": None, "empty": [], "dict": {}, "str": "sig", "three_for_one": [["a", 0, S - 20], ["b", 1, S - 20], ["c", 2, S - 20]],
                 "entry_not_list": ["sig"], "entry_short": [["sig", S - 20]], "entry_long": [["sig", 0, S - 20, 1]], "entry_dict": [{"s": 1}],
                 "sig_empty": [["", 0, S - 20]], "sig_not_str": [[5, 0, S - 20]], "ei_str": [["sig", "0", S - 20]], "ei_float": [["sig", 0.0, S - 20]],
                 "ei_bool": [["sig", True, S - 20]], "slot_float": [["sig", 0, float(S - 20)]], "slot_null": [["sig", 0, None]],
                 "slot_below_first": [["sig", 0, S - 21]], "slot_above_last": [["sig", 0, S - 19]]}
        for name, d in cases.items():
            self.assertEqual(reason(bt_set(dropped=d)), "tipauth:dropped_list", name)
        r2 = with_n(2)  # D of 2: first / last entry slots must equal dropped_first_slot / dropped_last_slot, and no key twice
        self.assertEqual(h.tipauth_gate(r2), (True, "ok"))
        r2["base_tipauth"]["dropped"] = [["a", 0, S - 20], ["a", 0, S - 19]]
        self.assertEqual(reason(r2), "tipauth:dropped_list")  # repeated (signature, event_index)
        r2["base_tipauth"]["dropped"] = [["a", 0, S - 20], ["a", 1, S - 19]]
        self.assertEqual(h.tipauth_gate(r2), (True, "ok"))  # same signature, another event_index: two prints
        r2["base_tipauth"]["dropped"] = [["a", None, S - 20], ["b", None, S - 19]]
        self.assertEqual(h.tipauth_gate(r2), (True, "ok"))  # a null event_index is the shadow's own value for a print without one
        r2["base_tipauth"]["dropped"] = [["a", 0, S - 20], ["b", 1, S - 20]]
        self.assertEqual(reason(r2), "tipauth:dropped_list")  # the last entry's slot is not dropped_last_slot
        r2["base_tipauth"]["dropped"] = [["a", 0, S - 19], ["b", 1, S - 19]]
        self.assertEqual(reason(r2), "tipauth:dropped_list")  # the first entry's slot is not dropped_first_slot

    def test_r2e_union_equals_websocket(self):
        for bad in (MISSING, None, 0, 2, 1.0, True, "1", -1, [], {}):
            r = good()
            if bad is MISSING:
                del r["base_breaks_unresolved_settled_union"]
            else:
                r["base_breaks_unresolved_settled_union"] = bad
            self.assertEqual(reason(r), "tipauth:union_ne_ws", repr(bad))
        r = good()
        r["base_breaks_unresolved_settled"] = 3
        self.assertEqual(reason(r), "tipauth:union_ne_ws")  # ws 3, union 1
        r["base_breaks_unresolved_settled_union"] = 3
        self.assertEqual(h.tipauth_gate(r), (True, "ok"))

    def test_r2f_sealed_record(self):
        self.assertEqual(reason({**good(), "h5_look2_sealed": True}), "tipauth:sealed_record")
        self.assertEqual(reason({**good(), "sealed": True}), "tipauth:sealed_record")
        for ok_v in (False, None):
            self.assertEqual(h.tipauth_gate({**good(), "h5_look2_sealed": ok_v, "sealed": ok_v}), (True, "ok"), ok_v)
        for key in ("h5_look2_sealed", "sealed"):  # R12: a sealed flag fails closed like the item-4 tool's `flag`: anything but absent, null or the literal False
            for bad in (True, 1, 0, 0.0, "", "yes", "false", [], {}, [False]):
                self.assertEqual(reason({**good(), key: bad}), "tipauth:sealed_record", (key, bad))

    def test_r12_variant_must_be_pv_in_the_one_predicate_too(self):
        for bad in ("fv", "", None, 1, True, ["pv"], "PV"):
            self.assertEqual(reason({**good(), "variant": bad}), "tipauth:variant_not_pv", repr(bad))
        r = good()
        del r["variant"]
        self.assertEqual(reason(r), "tipauth:variant_not_pv")

    def test_every_reason_string_has_a_test_in_this_file(self):
        src = Path(h.__file__).read_text()
        body = src[src.index("def tipauth_gate"):src.index("def parse_shadow_trigger")]
        in_gate = set(re.findall(r'return False, "tipauth:([a-z_0-9]+)"', body))
        self.assertEqual(in_gate, {"not_a_record", "ws_not_ge1", "gap", "union_missing", "tipauth_missing", "union_status", "tipauth_status", "value_not_zero",
                                   "tip_only", "both_diff", "dropped_boost", "boost_src", "dropped_n", "kept_ws_only_sig_on_tip", "orphaned_by_drop", "nosettle",
                                   "dropped_first_slot", "rule_not_v1", "margin", "slots", "cover", "dropped_list", "union_ne_ws", "sealed_record",
                                   "variant_not_pv"})

    def test_constants_are_the_ruled_ones(self):
        self.assertEqual((h.TIPAUTH_DROPPED_MAX, h.TIPAUTH_STAKE_LAMPORTS, h.TIPAUTH_LOSS_BUDGET_LAMPORTS, h.TIPAUTH_LOOKS),
                         (5, 20_000_000, 40_000_000, (25, 50, 100)))
        self.assertEqual((h.TIPAUTH_MARGIN_SLOTS, h.TIPAUTH_BOOST_SRC_OK, h.BASE_BREAKS_WS_REFUSAL), (4, ("pda", "event_authority"), WS))
        self.assertEqual(h.parse_shadow_trigger({**shadow_trigger(Clock()), "base_breaks_unresolved_settled": 1})[1], WS)  # the constant is the parser's string
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


_MAIN_CACHE: list = []


def _load_main():
    """main's executor (BASE_SHA), written to a temp module. None when the object is not available in this clone."""
    if _MAIN_CACHE:
        return _MAIN_CACHE[0]
    _MAIN_CACHE.append(_load_main_uncached())
    return _MAIN_CACHE[0]


def _load_main_uncached():
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

    def patch_main(self, main) -> None:
        """The same host stand-ins Case.setUp gives the head's module (LIVE_OK, tier file, owner ids), for main's separately loaded module."""
        for attr in ("LIVE_OK_PATH", "TIER_FILE_PATH", "LIVE_OK_UID", "LIVE_OK_GID"):
            patcher = mock.patch.object(main, attr, getattr(h, attr))
            patcher.start()
            self.addCleanup(patcher.stop)

    def skips(self, e: Env) -> list[tuple[str, str | None]]:
        """(reason, tipauth_reason) of every skip row: the reason is main's string; the tipauth cause rides beside it (R3)."""
        return [(r["reason"], r.get("tipauth_reason")) for r in e.ledger("skip")]


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

    def test_off_the_status_line_is_main_s_byte_for_byte_r10(self):
        e = self.start()
        self.append(e, self.tip_row(e, base_breaks_unresolved_settled=0))
        e.ex.intent_tick()
        out = h.status_report(e.conf)
        self.assertNotIn("tipauth", out)
        self.assertIn("[live] attempts=1 (lifetime; 1/150 in T0)", out)
        main = _load_main()
        if main is not None:
            self.patch_main(main)
            keep = lambda t: [ln for ln in t.splitlines() if ln.startswith(("[live]", "[dryrun]"))]  # (the header's part1= reads a repo-relative file: main is loaded from a temp path)
            self.assertEqual(keep(out), keep(main.status_report(e.conf)))
            self.assertTrue(keep(out)[-1].startswith("[live]"))

    def test_status_shows_the_path_only_when_the_switch_is_on_or_the_counters_hold_class_state_r10(self):
        e = self.start()
        e.ex.counters.tipauth.update({"n_landed": 3, "mints": ["M1"]})
        e.ex.counters.save(e.ex.counters_path)
        self.assertIn(" tipauth_gate=off tipauth_n=3", h.status_report(e.conf))  # the flag is off but the class has state: say so
        e2 = Env(self.tmp / "on", tipauth_gate="DEC-024-Am8")
        self.assertIn(" tipauth_gate=on tipauth_n=0", h.status_report(e2.conf))  # on, no state yet
        e2.ex.counters.tipauth["latched"] = {"reason": "class_loss_budget", "ts_ms": 1}
        e2.ex.counters.save(e2.ex.counters_path)
        self.assertIn(" tipauth_gate=latched_off tipauth_n=0", h.status_report(e2.conf))
        for bad in ("DEC-024-am8", True, "yes", ""):
            e3 = Env(self.tmp / f"x{bad!r}".replace("'", ""), tipauth_gate=bad)
            self.assertNotIn("tipauth", h.status_report(e3.conf), bad)


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
        self.assertEqual(self.skips(e), [(WS, "tipauth:nosettle")])  # R3: main's reason byte for byte, the cause in tipauth_reason
        self.assertEqual(e.rpc.sent, [])
        self.assertEqual(e.ex._refusal_counts, {WS: 1})  # the hourly count (and so the daily check's h5_feed_schema) sees main's string only

    def test_on_v2_rule_makes_the_path_unavailable(self):
        e = self.start(**self.ON)
        e.ex.exit_mode = h.EXIT_MODE_V2
        self.append(e, self.tip_row(e))
        e.ex.intent_tick()
        self.assertEqual(self.skips(e), [(WS, "tipauth:rule_not_v1")])
        self.assertEqual(e.rpc.sent, [])

    def test_r2f_the_path_needs_the_pv_variant(self):
        e = self.start(**self.ON)
        self.assertEqual(e.ex._tipauth_availability(), "")
        e.ex.trigger_variant = "fv"
        self.assertEqual(e.ex._tipauth_availability(), "tipauth:variant_not_pv")
        self.append(e, self.tip_row(e, variant="fv"))
        e.ex.intent_tick()
        self.assertEqual(self.skips(e), [(WS, "tipauth:variant_not_pv")])
        self.assertEqual(e.rpc.sent, [])
        e.ex.exit_mode = h.EXIT_MODE_V2  # the rule is asked first
        self.assertEqual(e.ex._tipauth_availability(), "tipauth:rule_not_v1")

    def test_r3_every_tipauth_refusal_keeps_main_s_reason_and_only_the_skip_row_carries_the_cause(self):
        e = self.start(**self.ON)
        bad = self.tip_row(e)
        bad["base_tipauth"]["dropped_n"] = 9
        old_style = self.tip_row(e, base_breaks_unresolved_settled=None)
        self.append(e, bad, old_style)
        e.ex.intent_tick()
        self.assertEqual(self.skips(e), [(WS, "tipauth:dropped_n"), (WS, None)])  # a non-int ws is main's refusal with no tipauth key
        for row in e.ledger():
            if row["kind"] != "skip":
                self.assertNotIn("tipauth_reason", row)
        self.assertEqual(e.alerts(), [])
        self.assertTrue(all(k == WS for k in e.ex._refusal_counts))

    def test_a_handed_in_tagged_trigger_is_rechecked(self):
        e = self.start()  # switch off
        trg, _ = h.parse_shadow_trigger(self.tip_row(e), "pv", "")
        e.ex.handle_trigger(trg)
        self.assertEqual(self.skips(e), [(WS, "tipauth:unavailable")])  # R3 holds for the recheck too
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

    def _tagged_row(self, e: Env) -> None:
        row = shadow_trigger(e.clock)
        row.update(tip_fields())
        with Path(e.conf["intents_file"]).open("a") as fh:
            fh.write(json.dumps(row) + "\n")

    def test_r5_a_tier_change_on_a_tagged_trigger_describes_the_tier_not_the_class_cap(self):
        """T0 to T1 arriving on a tagged trigger: the tier_change row carries T1's own limits (stake 0.10 SOL), no config_clamps_tier is raised, and the
        buy is the class stake (0.02 SOL)."""
        from tools.test_h5_executor import buy_args
        e = self.tier_env("T0", tipauth_gate="DEC-024-Am8")
        e.ex._refresh_tier(e.clock())  # the T0 baseline (a non-tagged read: no cap in force)
        Path(e.conf["intents_file"]).write_text("")
        e.ex.intent_tick()
        self.assertEqual(self.changes(e), [(None, "T0", None)])
        self.write_tier("T1\n")
        self._tagged_row(e)
        e.ex.intent_tick()
        self.assertEqual(self.changes(e), [(None, "T0", None), ("T0", "T1", None)])
        row = e.ledger("tier_change")[-1]
        self.assertEqual(row["limits"]["stake_lamports"], 100_000_000)
        self.assertEqual(row["limits"], dataclasses.asdict(h.H5Limits.from_config(e.conf, "T1")))  # every field the tier's own
        self.assertEqual(row["config_clamps"], {})
        self.assertEqual(e.alerts("config_clamps_tier"), [])
        dec = e.ledger("decision")
        self.assertEqual((len(dec), dec[0]["gate_path"], dec[0]["stake_lamports"]), (1, "tipauth", 20_000_000))
        self.assertEqual(buy_args(e.sent()[0])[0], 20_000_000)
        self.assertEqual((e.ex.tier, e.ex.h5.stake_lamports, e.ex._stake_cap), ("T1", 100_000_000, None))

    def test_r5_a_real_config_clamp_is_still_reported_on_a_tagged_trigger_and_the_cap_is_not_one(self):
        e = self.tier_env("T0", tipauth_gate="DEC-024-Am8", stake_lamports=50_000_000, max_open=1)
        e.ex._refresh_tier(e.clock())
        Path(e.conf["intents_file"]).write_text("")
        e.ex.intent_tick()
        self.write_tier("T1\n")
        self._tagged_row(e)
        e.ex.intent_tick()
        want = {"stake_lamports": {"table": 100_000_000, "effective": 50_000_000}, "max_open": {"table": 3, "effective": 1}}
        self.assertEqual(e.ledger("tier_change")[-1]["config_clamps"], want)
        self.assertEqual(e.ledger("tier_change")[-1]["limits"]["stake_lamports"], 50_000_000)
        self.assertEqual([a["clamps"] for a in e.alerts("config_clamps_tier")][-1], want)
        self.assertEqual(e.ledger("decision")[0]["stake_lamports"], 20_000_000)  # the class stake is the smaller of the tier's and 0.02 SOL

    def test_r5_the_description_of_the_tier_is_the_same_with_and_without_a_cap_in_force(self):
        e = self.tier_env("T2", tipauth_gate="DEC-024-Am8", stake_lamports=250_000_000)
        e.ex.tier = "T2"
        plain = (dataclasses.asdict(e.ex._tier_h5()), e.ex._config_clamps())
        e.ex._stake_cap = h.TIPAUTH_STAKE_LAMPORTS
        self.assertEqual(e.ex.h5.stake_lamports, 20_000_000)  # sizing, budget and balance read the cap
        self.assertEqual((dataclasses.asdict(e.ex._tier_h5()), e.ex._config_clamps()), plain)  # the description does not
        self.assertEqual(plain[0]["stake_lamports"], 250_000_000)
        self.assertEqual(e.ex._stake_cap, 20_000_000)  # and the cap is restored

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
        self.assertEqual(self.skips(e)[-1], (WS, "tipauth:latched_off"))
        self.assertEqual(e.rpc.sent, [])
        self.assertEqual(self.counters(e).get("halts") or {}, {})  # the class latch is a switch-off of the path, not one of the executor's HALTs

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
        self.assertEqual(self.skips(e)[-1], (WS, "tipauth:look_pending"))
        # an ack that is not a look already written is "ahead"; the pending look's own number releases it (config, so a reviewed reinstall)
        e.conf["tipauth_look_ack"] = 24
        self.assertEqual(e.build()._tipauth_availability(), "tipauth:look_ack_ahead")
        e.conf["tipauth_look_ack"] = 25
        ex3 = e.build()
        self.assertEqual(ex3._tipauth_availability(), "")
        for i in range(26, 51):
            ex3._note_tipauth_landed(f"M{i}")
        self.assertEqual([r["look"] for r in e.ledger("tipauth_look")], [25, 50])
        self.assertEqual(ex3._tipauth_availability(), "tipauth:look_pending")  # the stale ack 25 does not satisfy look 50
        e.conf["tipauth_look_ack"] = 50
        ex4 = e.build()
        for i in range(51, 101):
            ex4._note_tipauth_landed(f"M{i}")
        self.assertEqual([r["look"] for r in e.ledger("tipauth_look")], [25, 50, 100])
        self.assertEqual(ex4._tipauth_availability(), "tipauth:look_pending")
        e.conf["tipauth_look_ack"] = 100
        self.assertEqual(e.build()._tipauth_availability(), "")
        self.assertEqual(self.counters(e)["tipauth"]["n_landed"], 100)

    def test_r8_an_ack_cannot_be_set_ahead_of_the_looks(self):
        e = self.start(**self.ON)
        self.assertEqual(e.ex._tipauth_availability(), "")  # no ack at all: fine, and no look is pending
        for ack in (100, 25, 0, -1, 25.0, True, "25", None, [25]):  # nothing written yet: every present value is ahead (null included)
            e.conf["tipauth_look_ack"] = ack
            self.assertEqual(e.build()._tipauth_availability(), "tipauth:look_ack_ahead", repr(ack))
        e.conf["tipauth_look_ack"] = 100  # a config that ships 100 at install does not skip looks 25, 50 and 100
        self.append(e, self.tip_row(e))
        e.build().intent_tick()
        self.assertEqual(self.skips(e), [(WS, "tipauth:look_ack_ahead")])
        self.assertEqual(e.rpc.sent, [])
        for i in range(1, 26):
            e.ex._note_tipauth_landed(f"M{i}")
        self.assertEqual(e.ex.counters.tipauth["looks"], [25])
        for ack, want in ((100, "tipauth:look_ack_ahead"), (50, "tipauth:look_ack_ahead"), (24, "tipauth:look_ack_ahead"), (25, "")):
            e.conf["tipauth_look_ack"] = ack
            self.assertEqual(e.build()._tipauth_availability(), want, ack)

    def test_r8_if_look_50_arrives_before_25_is_acked_both_rows_exist_and_only_50_releases(self):
        e = self.start(**self.ON)
        for i in range(1, 51):
            e.ex._note_tipauth_landed(f"M{i}")
        self.assertEqual([r["look"] for r in e.ledger("tipauth_look")], [25, 50])
        self.assertEqual(e.ex.counters.tipauth["pause_look"], 50)
        e.conf["tipauth_look_ack"] = 25
        self.assertEqual(e.build()._tipauth_availability(), "tipauth:look_pending")  # a written look, but not the pending one
        e.conf["tipauth_look_ack"] = 50
        self.assertEqual(e.build()._tipauth_availability(), "")

    def test_the_class_loss_switch_off_is_not_a_halt_name(self):
        src = Path(h.__file__).read_text()
        self.assertNotIn('_latch("class_loss_budget"', src)
        self.assertIn('self._tipauth_off("class_loss_budget")', src)
        self.assertFalse(any(m == "class_loss_budget" for m in re.findall(r'_latch\("([a-z0-9_]+)"', src)))

    def test_a_non_dict_tipauth_in_the_counters_file_refuses_to_start(self):
        e = self.start(**self.ON)
        path = Path(e.ex.counters_path)
        d = json.loads(path.read_text())
        for bad in ([], "x", 5, None, ["mints"]):
            path.write_text(json.dumps({**d, "tipauth": bad}))
            with self.assertRaises(SystemExit):
                h.H5Counters.load(path, "live")
            with self.assertRaises(SystemExit):
                e.build()
        path.write_text(json.dumps({**d, "tipauth": {}}))
        self.assertEqual(h.H5Counters.load(path, "live").tipauth, {})

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


class ClassHaltTests(FlowCase):
    """R6: the class does not loosen an existing halt. A tagged landed buy is out of landed_buys / pre_unlinked_landed (the pre_unlinked_share halt) and
    in the landing-median halt, the trade and attempt caps, and the daily and total stops."""

    ON = {"tipauth_gate": "DEC-024-Am8"}
    UNLINKED = {"base_breaks_unresolved": 1, "base_breaks_unresolved_settled": 0}
    TAGGED = {"gate_path": "tipauth", "base_breaks_unresolved": 1, "base_breaks_unresolved_settled": 1}

    def second(self, name: str, **kw) -> Env:
        """Another executor in its own directory (a second world), started the way FlowCase.start starts one."""
        e = Env(self.tmp / name, **kw)
        Path(e.conf["intents_file"]).write_text("")
        e.ex.intent_tick()
        return e

    def test_r6_a_tagged_landed_buy_is_not_in_landed_buys_or_pre_unlinked_landed_but_a_websocket_one_is(self):
        e = self.start(**self.ON)
        self.append(e, self.tip_row(e))
        e.ex.intent_tick()
        e.land_buy()
        c = self.counters(e)
        self.assertEqual((c["landed_buys"], c["pre_unlinked_landed"]), (0, 0))
        self.assertEqual(c["tipauth"]["n_landed"], 1)  # the class counts it
        ctl = self.second("ctl", **self.ON)  # a websocket-path buy (value 0) in the same setup is counted as before
        self.append(ctl, self.tip_row(ctl, base_breaks_unresolved_settled=0))
        ctl.ex.intent_tick()
        ctl.land_buy()
        self.assertEqual((self.counters(ctl)["landed_buys"], self.counters(ctl)["pre_unlinked_landed"]), (1, 0))

    def test_r6_tagged_landings_do_not_dilute_the_pre_unlinked_share(self):
        e = self.start(**self.ON)
        e.ex.counters.landed_buys, e.ex.counters.pre_unlinked_landed = 100, 15  # exactly 15.0%: not above the limit
        for _ in range(6):  # if these were counted, the next pre-unlinked buy would make 16 of 107 = 14.95% and the halt would not trip
            e.ex._note_landed_buy(dict(self.TAGGED))
        self.assertEqual((e.ex.counters.landed_buys, e.ex.counters.pre_unlinked_landed), (100, 15))
        self.assertEqual(e.ex.counters.halts, {})
        e.ex._note_landed_buy(dict(self.UNLINKED))  # 16 of 101 = 15.84% > 15%
        self.assertEqual((e.ex.counters.landed_buys, e.ex.counters.pre_unlinked_landed), (101, 16))
        self.assertIn("pre_unlinked_share", e.ex.counters.halts)

    def test_r6_a_tagged_landing_is_still_in_the_landing_median_halt(self):
        e = self.start(**self.ON)
        e.ex.counters.landing_s = [5.0] * 9  # nine slow landings; the tenth is the tagged one
        self.append(e, self.tip_row(e))
        e.ex.intent_tick()
        e.land_buy(slot=TRIG_SLOT + 20)
        self.assertEqual(len(e.ex.counters.landing_s), 10)
        self.assertIn("landing_median_gt_3s", e.ex.counters.halts)

    def test_r6_a_tagged_trade_is_in_the_trade_and_attempt_caps(self):
        e = self.start(max_trades_per_day=1, **self.ON)
        self.append(e, self.tip_row(e))
        e.ex.intent_tick()
        e.land_buy()
        now = e.clock()
        self.assertEqual((e.ex.counters.day(h.day_key(now))["trades"], e.ex.counters.tier_attempts, e.ex.state.attempts), (1, 1, 1))
        self.assertEqual(e.ex._budget_stop(now), "max_trades_day")
        e2 = self.second("two", max_attempts=1, **self.ON)
        self.append(e2, self.tip_row(e2))
        e2.ex.intent_tick()
        e2.land_buy()
        self.assertEqual(e2.ex._budget_stop(e2.clock()), "max_attempts")

    def test_r6_a_tagged_realized_loss_is_in_the_daily_and_total_stops(self):
        e = self.start(**self.ON)
        e.ex._tipauth_admit("TAGGED")
        e.ex._refresh_tier(e.clock())
        self.assertIsNone(e.ex._budget_stop(e.clock()))
        e.ex._book_fee("TAGGED", 65_000_000, "test")  # -0.065 SOL and 0.02 SOL at risk: the T0 daily stop is 0.08
        self.assertEqual(e.ex._budget_stop(e.clock()), "daily_loss_stop")
        e.ex._book_fee("TAGGED", 45_000_000, "test")  # -0.11 SOL and 0.02 at risk: the T0 total stop is 0.12 (the check that runs first)
        self.assertEqual(e.ex._budget_stop(e.clock()), "total_loss_stop")
        self.assertEqual((e.ex.state.realized_lamports, e.ex.counters.tipauth["realized"]), (-110_000_000, -110_000_000))


class MainEnv(Env):
    """The same fake world with main's executor (BASE_SHA) in it, for the off-path flow comparison."""

    MAIN = None

    def build(self):
        ex = self.MAIN.H5Executor(self.rpc, self.conf, self.kp, now_ms=self.clock, pick_oracle=self.oracle, root=self.root)
        self.seed_clock(ex)
        return ex

    def seed_clock(self, ex=None, back_s: int = 150) -> None:
        ex = ex or self.ex
        ex.slots = self.MAIN.SlotClock()
        now = self.clock()
        for k in range(back_s // 2, -1, -1):
            ex.slots.observe(self.rpc.slot_at(now - k * 2_000), now - k * 2_000)


class OffPathFlowTests(FlowCase):
    """Quant-proof checklist item 4 (ii), in the unit-test form: the same scripted stream of trigger rows through main's module and the head's
    module, switch off (no key, and a key that does not match), in dry-run and in the tests' live mode. The ledger rows, every state file and the
    `--status` lines are equal. This is the flow part of the equivalence, not the whole of it (live RPC timing is not shown)."""

    N_ROWS = 240

    def stream(self, E, root: Path, live: bool, **conf):
        from solders.keypair import Keypair
        from tools.test_h5_executor_shadow import pool_row

        e = E(root, live=live, seed=b"\x07" * 32, **conf)
        Path(e.conf["intents_file"]).write_text("")
        e.ex.intent_tick()
        rng = random.Random(11)
        mints = [str(Keypair.from_seed(bytes([i]) * 32).pubkey()) for i in range(1, 6)]

        def row(kind: str):
            if kind == "ws0":
                return {**shadow_trigger(e.clock), **tip_fields(base_breaks_unresolved_settled=0)}
            if kind == "ws1pass":
                return {**shadow_trigger(e.clock), **tip_fields()}
            if kind == "ws2fail":
                r = {**shadow_trigger(e.clock), **tip_fields(base_breaks_unresolved_settled=2)}
                r["base_tipauth"]["dropped_n"] = 9
                return r
            if kind == "wsbad":
                return {**shadow_trigger(e.clock), **tip_fields(base_breaks_unresolved_settled=rng.choice([None, "1", True, -3, 1.0]))}
            if kind == "other_mint":
                return {**shadow_trigger(e.clock, mint=rng.choice(mints)), **tip_fields(base_breaks_unresolved_settled=rng.choice([0, 1, 4]))}
            if kind == "badfield":
                return {**shadow_trigger(e.clock), **tip_fields(base_breaks_unresolved_settled=0), rng.choice(["v_print", "sps", "base_pre", "slot"]): rng.choice([None, 0, "x"])}
            return {"hb": {"type": "hb"}, "gap_t": {"type": "gap", "flags_pools": True, "kind": "ws_reconnect"},
                    "gap_f": {"type": "gap", "flags_pools": False, "kind": "redundant"}, "excl": {"type": "excluded", "reason": "synthetic", "mint": mints[0], "pool": "p"},
                    "nosps": {"type": "skipped_no_sps", "mint": mints[1]}, "pool": pool_row(MINT), "garbage": "{not json", "list": [1, 2]}[kind]

        def write(rows):
            with Path(e.conf["intents_file"]).open("a") as fh:
                for r in rows:
                    fh.write((r if isinstance(r, str) else json.dumps(r)) + "\n")
            e.ex.intent_tick()

        write([row("hb"), row("ws0")])  # the one buy
        if live and MINT in e.ex.state.pending:
            e.land_buy()
        kinds = ["ws1pass"] * 6 + ["ws2fail"] * 3 + ["wsbad"] * 3 + ["other_mint"] * 4 + ["badfield"] * 2 + ["ws0"] * 2 + ["hb", "gap_t", "gap_f", "excl", "nosps", "pool", "garbage", "list"]
        for _ in range(self.N_ROWS // 8):
            write([row(rng.choice(kinds)) for _ in range(8)])
        if MINT in e.ex.state.open:
            e.at_slot(e.plan()["send_slot"])  # the exit: a sent sell (live) or a dry_sell
            if live and MINT in e.ex.state.pending:
                e.land_sell(e.rpc.slot)
        write([row(rng.choice(kinds)) for _ in range(8)])
        e.jump(3_600_000)  # the hour rolls: the refusal counts are written
        write([row("hb")])
        def norm(text: str) -> str:  # the temp path; the sha of the source file (it must differ between two source files); the intents file's inode
            text = re.sub(r'"code_sha256":\s*"[0-9a-f]{64}"', '"code_sha256":"<sha>"', text.replace(str(root), "<T>"))
            return re.sub(r'"inode":\s*\d+', '"inode":0', text)  # (the file system's number for the intents file)

        files = {str(f.relative_to(root)): norm(f.read_text()) for f in sorted((root / "state").rglob("*"))
                 if f.is_file() and "lock" not in f.name and f.name != "H5_EXECUTOR.lock"}
        status = [ln for ln in h.status_report(e.conf).splitlines() if ln.startswith(("[live]", "[dryrun]"))]
        return {"ledger": norm(json.dumps(e.ledger(), sort_keys=True, separators=(",", ":"))), "files": files, "refusals": dict(e.ex.refusals), "status": status,
                "n_ledger": len(e.ledger())}

    def test_switch_off_rows_state_and_status_equal_mains(self):
        main = _load_main()
        if main is None:
            self.skipTest(f"git object {BASE_SHA} not available")
        self.patch_main(main)
        MainEnv.MAIN = main
        for live in (True, False):
            with self.subTest(live=live):
                m = self.stream(MainEnv, self.tmp / f"main{live}", live)
                a = self.stream(Env, self.tmp / f"head{live}", live)
                b = self.stream(Env, self.tmp / f"headk{live}", live, tipauth_gate="DEC-024-am8")  # a key that does not match: still off
                for name, got in (("no key", a), ("non-matching key", b)):
                    self.assertEqual(got["n_ledger"], m["n_ledger"], name)
                    self.assertEqual(got["refusals"], m["refusals"], name)
                    self.assertEqual(got["status"], m["status"], name)
                    self.assertEqual(sorted(got["files"]), sorted(m["files"]), name)
                    for f, text in m["files"].items():
                        self.assertEqual(got["files"][f], text, (name, f))
                    self.assertEqual(got["ledger"], m["ledger"], name)
                self.assertGreater(m["n_ledger"], 100)
                self.assertGreater(m["ledger"].count(f'"reason":"{WS}"'), 20)  # the stream exercises the refusal this change touches
                self.assertIn('"kind":"decision"', m["ledger"])  # and a buy
                self.assertNotIn("tipauth", a["ledger"] + json.dumps(a["files"]))


class RunbookTests(unittest.TestCase):
    def test_the_runbook_says_what_the_code_does_on_these_points(self):
        t = (Path(__file__).resolve().parents[1] / "docs" / "runbooks" / "h5-executor.md").read_text()
        sec = t[t.index("## tipauth gate (OFF by default)"):]
        for needle in ("tipauth_reason", "tipauth:look_ack_ahead", "not one of the executor's HALTs", "up to two stakes", "sell_and_close", "only `tipauth_look_ack: 50` releases it",
                       "drops the key at its first save", "`tipauth_off` row", "only while the switch is on", "not an object"):
            self.assertIn(needle, sec, needle)
        self.assertNotIn("class_loss_budget`", sec.split("## ")[1] if "## " in sec[3:] else sec)  # not added to the halt table


if __name__ == "__main__":
    unittest.main()
