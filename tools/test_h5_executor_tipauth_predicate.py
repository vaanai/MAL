"""One predicate (quant-proof R12 and R9 (i), PR #610 comment 6121641942): the executor's `tipauth_gate` and the item-4 tool's `would_pass`
(tools/h5_tipauth_item4.py, whose sha256 is pinned for the acceptance reads, so it is read here and never changed) must give the same pass / refuse
answer on every record. And a record made by the shadow engine's own test harness must pass and fail the gate as expected, so a renamed shadow
field breaks a test.

What the first run of the generator found (before the executor was tightened; one example each, both are records the item-4 tool refuses):
  * sealed flag: {..., "h5_look2_sealed": 1}  or  {..., "sealed": "yes"}  passed the executor, which only refused the literal True. The item-4 tool
    fails closed (`flag`: anything but absent, null or False is sealed). The executor now does the same. A pure tightening: no record the
    shadow writes carries a sealed flag other than True.
  * variant: {..., "variant": "fv"}  passed `tipauth_gate`; the item-4 tool refuses a record whose variant is not "pv". The live path never reached
    this (parse_shadow_trigger returns before the gate for another variant, and `_tipauth_availability` needs trigger_variant == "pv"), so adding
    the check to the one predicate is a pure tightening.
After those two there are no disagreements, in the answer or in the first failing reason."""
from __future__ import annotations

import copy
import random
import unittest

from tools import h5_executor as h
from tools import h5_tipauth_item4 as item4
from tools import test_h5_shadow_union as su  # the module, not the class: pytest must not collect the shadow's tests a second time here
from tools.test_h5_executor_tipauth_gate import good

NAN = float("nan")
SUBST = [None, True, False, 0, 1, -1, 2, 3, 4, 5, 6, 0.0, 1.0, 4.0, "0", "1", "", "ok", "pda", "pv", [], {}, [0], NAN, 2 ** 70, -(2 ** 70), 10 ** 9]
N_RECORDS = 100_000


def valid_record(rng: random.Random) -> dict:
    """A trigger record that meets every condition, with random slots, 1-5 dropped prints and a random mix of null / integer event indices."""
    lo = rng.randint(0, 10 ** rng.randint(2, 9))
    hi = lo + rng.randint(6, 90)
    if rng.random() < 0.5:
        slot = hi + 4
        hw = slot + rng.randint(0, 5)  # the tip is at or ahead of the trigger slot: cover_hi = slot - 4
    else:
        slot = hi + 4 + rng.randint(1, 6)
        hw = hi + 4  # the tip is behind the trigger slot: cover_hi = hw - 4
    n = rng.randint(1, 5)
    slots = sorted(rng.randint(lo + 1, hi) for _ in range(n))
    entries: list[list] = []
    seen: set = set()
    while len(entries) < n:
        e = [f"sig{rng.randint(0, 10 ** 6)}", rng.choice([None, 0, 1, 2, rng.randint(0, 50)]), slots[len(entries)]]
        if (e[0], e[1]) not in seen:
            seen.add((e[0], e[1]))
            entries.append(e)
    ws = rng.randint(1, 9)
    bt = {"status": "ok", "dropped_n": n, "dropped_first_slot": slots[0], "dropped_last_slot": slots[-1], "cover_lo": lo, "cover_hi": hi,
          "kept_ws_only_before_cover": rng.randint(0, 2), "kept_ws_only_above_cover": rng.randint(0, 2), "kept_ws_only_sig_on_tip": 0,
          "dropped_boost_n": 0, "dropped_sells_n": rng.randint(0, 2), "orphaned_by_drop_n": 0, "tipauth_nosettle": 0, "dropped": entries, "margin_slots": 4}
    bu = {"status": "ok", "ws_only": ws, "tip_only": 0, "both": rng.randint(0, 40), "both_diff": 0, "tip_first_slot": lo, "tip_hw_slot_global": hw,
          "tip_lag_slots_global": rng.randint(0, 3)}
    rec = {"type": "trigger", "variant": "pv", "slot": slot, "base_breaks_unresolved_settled": ws, "base_breaks_unresolved_settled_union": ws,
           "base_breaks_unresolved_settled_tipauth": 0, "base_union": bu, "base_tipauth": bt, "gap": False,
           "boost_src": rng.choice(["pda", "event_authority"])}
    if rng.random() < 0.3:
        rec["h5_look2_sealed"] = rng.choice([False, None])
    if rng.random() < 0.3:
        rec["sealed"] = rng.choice([False, None])
    return rec


def _paths(rec: dict) -> list[tuple]:
    out: list[tuple] = []

    def walk(d: dict, pre: tuple) -> None:
        for k, v in d.items():
            out.append(pre + (k,))
            if isinstance(v, dict):
                walk(v, pre + (k,))

    walk(rec, ())
    dropped = rec["base_tipauth"]["dropped"]
    out += [("base_tipauth", "dropped", i, j) for i in range(len(dropped)) for j in range(3)]
    out += [("base_tipauth", "dropped", i) for i in range(len(dropped))]
    out += [("h5_look2_sealed",), ("sealed",)]
    return out


def _slot(rec: dict, path: tuple):
    d = rec
    for p in path[:-1]:
        try:
            d = d[p]
        except (KeyError, IndexError, TypeError):
            return None
    return d


def mutate(rng: random.Random, rec: dict) -> dict:
    """1 to 3 edits at random places: delete a key, nudge an integer by a small step (the boundary cases), or substitute a value of another type."""
    paths = _paths(rec)
    for _ in range(rng.choice([1, 1, 1, 2, 2, 3])):
        path = rng.choice(paths)
        parent = _slot(rec, path)
        if parent is None:
            continue
        k, kind = path[-1], rng.random()
        try:
            cur = parent[k] if (isinstance(parent, dict) and k in parent) or (isinstance(parent, list) and k < len(parent)) else 0
            if kind < 0.12:
                if isinstance(parent, dict):
                    parent.pop(k, None)
                elif isinstance(parent, list) and k < len(parent):
                    del parent[k]
            elif kind < 0.45 and isinstance(cur, int) and not isinstance(cur, bool):
                parent[k] = cur + rng.choice([-1, 1, -1, 1, 2, -2, 10])
            else:
                parent[k] = copy.deepcopy(rng.choice(SUBST))
        except (KeyError, IndexError, TypeError):
            pass
    bt, shape = rec.get("base_tipauth"), rng.random()
    if isinstance(bt, dict) and isinstance(bt.get("dropped"), list) and bt["dropped"]:
        if shape < 0.04:
            bt["dropped"].append(copy.deepcopy(bt["dropped"][-1]))  # a repeated (signature, event_index)
        elif shape < 0.07:
            bt["dropped"].reverse()
        elif shape < 0.09:
            bt["dropped"].pop()
    return rec


def random_record(rng: random.Random):
    rec = valid_record(rng)
    x = rng.random()
    if x < 0.40:
        return rec
    if x < 0.42:
        return rng.choice([None, [], "x", 5, {}, 1.5, True, [rec]])
    return mutate(rng, rec)


def item4_answer(rec) -> tuple[bool, str]:
    """The item-4 tool's answer in the executor's spelling: its reasons carry no `tipauth:` prefix, "ok" has none in either."""
    ok, why = item4.would_pass(copy.deepcopy(rec))
    return ok, ("ok" if ok else "tipauth:" + why)


class OnePredicateTests(unittest.TestCase):
    def test_r12_tipauth_gate_and_would_pass_agree_on_100000_random_records(self):
        rng = random.Random(1)
        n_pass = 0
        disagreements: list = []
        reasons: dict[str, int] = {}
        for _ in range(N_RECORDS):
            rec = random_record(rng)
            try:
                mine = h.tipauth_gate(copy.deepcopy(rec))
            except Exception as exc:  # the gate never raises
                mine = (None, f"raised {type(exc).__name__}")
            theirs = item4.would_pass(copy.deepcopy(rec))
            if mine != item4_answer(rec):  # the answer, and the first failing reason, are the same
                disagreements.append((mine, theirs, rec))
            n_pass += bool(theirs[0])
            reasons[theirs[1]] = reasons.get(theirs[1], 0) + 1
        self.assertEqual(disagreements[:3], [])
        self.assertEqual(len(disagreements), 0)
        share = n_pass / N_RECORDS
        self.assertGreater(share, 0.30, share)  # a meaningful share passes
        self.assertLess(share, 0.70, share)  # and a meaningful share is refused
        for r in item4.GATE_REASONS:  # every reason of the one predicate is hit (variant_not_pv and the like need the mutation's deletes and substitutions)
            self.assertGreater(reasons.get(r, 0), 0, r)
        self.assertEqual(set(reasons) - set(item4.GATE_REASONS) - {"ok"}, set())

    def test_r12_the_generator_is_deterministic_and_seeded_at_1(self):
        a = [random_record(random.Random(1)) for _ in range(2)]
        self.assertEqual(repr(a[0]), repr(a[1]))
        self.assertNotEqual(repr(random_record(random.Random(1))), repr(random_record(random.Random(2))))

    def test_r12_the_two_classes_the_first_run_found_are_closed(self):
        for extra in ({"h5_look2_sealed": 1}, {"sealed": "yes"}, {"sealed": 0}, {"variant": "fv"}):
            rec = {**good(), **extra}
            self.assertFalse(h.tipauth_gate(rec)[0], extra)
            self.assertFalse(item4.would_pass(rec)[0], extra)

    def test_r12_a_null_event_index_inside_a_dropped_entry_is_accepted_by_both_and_other_types_by_neither(self):
        """Part A accepted a null event_index; the item-4 tool's _dropped_list_ok does too (e[1] is None or an integer)."""
        for ei, ok in ((None, True), (0, True), (7, True), ("0", False), (0.0, False), (True, False), ([], False), ({}, False)):
            rec = good()
            rec["base_tipauth"]["dropped"][0][1] = ei
            self.assertEqual((h.tipauth_gate(copy.deepcopy(rec))[0], item4.would_pass(copy.deepcopy(rec))[0]), (ok, ok), repr(ei))

    def test_r12_good_passes_in_both_and_the_item_4_tool_is_not_touched(self):
        self.assertEqual(h.tipauth_gate(good()), (True, "ok"))
        self.assertEqual(item4.would_pass(good()), (True, "ok"))
        self.assertEqual((h.TIPAUTH_MARGIN_SLOTS, h.TIPAUTH_DROPPED_MAX, h.TIPAUTH_BOOST_SRC_OK), (item4.TIPAUTH_MARGIN_SLOTS, item4.TIPAUTH_DROPPED_MAX, item4.BOOST_SRC_OK))


class ShadowEngineRecordTests(unittest.TestCase):
    """R9 (i): records written by the shadow engine itself (TipAuthEngineTests.run_pool, 13 prints, trigger at slot 1200, a phantom print dropped from the
    tip, a tip high-water and skipped tip rows varied), not a dict this file builds. The harness pool has a feed gap and a behavioural BOOST, so, as
    quant-proof's run did, `gap` and `boost_src` are set to what a clean live pool carries; everything else is the shadow's own."""

    @classmethod
    def records(cls) -> list[dict]:
        eng = su.TipAuthEngineTests("run_pool")
        n_rows = len(su.pool_rows().rows)
        out: list[dict] = []
        for hw in (1100, 1200, 2000):
            for after in [None] + list(range(n_rows - 1)):
                for phantom_slot in (None, 1198, 1150, 1000):
                    if after is None and phantom_slot is not None:
                        continue
                    for skip in ((), (3,), (6, 7)):
                        _, rows = eng.run_pool(su.FakeTip("ok", hw), phantom_after=after, phantom_slot=phantom_slot, tip_skip=skip)
                        out += [r for r in su.types(rows, "trigger") if r["variant"] == "pv"]
        return out

    def test_the_engine_s_own_records_pass_and_fail_the_gate_as_expected(self):
        recs = self.records()
        self.assertGreaterEqual(len(recs), 400)
        by_reason: dict[str, int] = {}
        passed = 0
        for raw in recs:
            self.assertEqual(h.tipauth_gate(copy.deepcopy(raw))[1] in ("tipauth:gap", "tipauth:ws_not_ge1"), True)  # untouched, the harness pool has a gap: refused as such
            rec = copy.deepcopy(raw)
            rec["gap"], rec["boost_src"] = False, "pda"
            ok, why = h.tipauth_gate(rec)
            self.assertEqual((ok, why), item4_answer(rec), rec)  # the one predicate
            by_reason[why] = by_reason.get(why, 0) + 1
            if ok:
                passed += 1
                bt, bu = rec["base_tipauth"], rec["base_union"]
                self.assertGreaterEqual(rec["base_breaks_unresolved_settled"], 1)
                self.assertEqual(rec["base_breaks_unresolved_settled_tipauth"], 0)
                self.assertTrue(1 <= bt["dropped_n"] <= h.TIPAUTH_DROPPED_MAX)
                self.assertEqual(bt["cover_hi"], min(bu["tip_hw_slot_global"], rec["slot"]) - h.TIPAUTH_MARGIN_SLOTS)
                self.assertEqual(len(bt["dropped"]), bt["dropped_n"])
        # The engine's records meet the condition fields by the names the gate reads: a renamed shadow field turns every pass into a refusal, and fails here.
        self.assertGreaterEqual(passed, 30, by_reason)
        self.assertGreaterEqual(by_reason.get("tipauth:value_not_zero", 0), 1, by_reason)  # the tip does not repair every case
        self.assertGreaterEqual(by_reason.get("tipauth:dropped_first_slot", 0), 1, by_reason)  # a drop at the start of the tip's cover (C5 c)
        self.assertEqual(set(by_reason) - {"ok", "tipauth:value_not_zero", "tipauth:dropped_first_slot", "tipauth:ws_not_ge1"}, set(), by_reason)

    def test_a_renamed_shadow_field_turns_the_engine_s_passes_into_refusals(self):
        recs = [r for r in self.records()]
        renames = [("base_tipauth", "dropped_first_slot"), ("base_tipauth", "dropped_n"), ("base_tipauth", "cover_lo"), ("base_tipauth", "cover_hi"),
                   ("base_tipauth", "dropped"), ("base_union", "tip_hw_slot_global"), ("base_union", "tip_first_slot"), ("base_tipauth", "margin_slots")]
        for sub, key in renames:
            passes = 0
            for raw in recs:
                rec = copy.deepcopy(raw)
                rec["gap"], rec["boost_src"] = False, "pda"
                rec[sub][key + "_v2"] = rec[sub].pop(key, None)
                passes += h.tipauth_gate(rec)[0]
            self.assertEqual(passes, 0, (sub, key))


if __name__ == "__main__":
    unittest.main()
