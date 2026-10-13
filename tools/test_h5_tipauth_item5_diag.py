"""Tests for tools/h5_tipauth_item5_diag.py (the counts-only item-5 table and gap-record table). Synthetic records in temp directories only.

  /data/mal/venv/bin/python -m pytest -q tools/test_h5_tipauth_item5_diag.py
  python3 -m unittest tools.test_h5_tipauth_item5_diag
"""
from __future__ import annotations

import ast
import collections
import contextlib
import hashlib
import io
import json
import os
import random
import re
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from tools import h5_tipauth_item4 as I4
from tools import h5_tipauth_item5_diag as D
from tools.test_h5_tipauth_item4 import H, MID, T0, bt, hb, pool as pool_rec, start, trig, write

SHA = "5db78a663a0617527daacb22f5ce6c1a7e92dfa3"
UNTIL = T0 + 6 * H  # item 4's +6 h instant for since = T0
NOW = UNTIL + I4.SETTLE_MS
ORIGIN = T0 + 1000  # start() default
BTFIELDS = ("kept_ws_only_before_cover", "kept_ws_only_above_cover", "kept_ws_only_sig_on_tip", "dropped_boost_n", "dropped_sells_n")
# keys the tables must never read, planted on records of every type
FORBID = {"from_slot": 111111111, "to_slot": 222222222, "silent_ms": 20001, "open_pools": 7, "at_ms": 5, "sockets_down_recent": [0, 1], "net_sol": 3.0,
          "pnl_sol": 9.0, "mint": "MintSecretBBB", "rule_x": "x"}
POISON = set(FORBID) | {"reason", "q_trigger_sol", "min_q_pv_sol", "triggered", "exit_ladder_trigger_slots", "argv", "rule", "pnl", "slot", "boost_src", "counters"}


def hour(t_ms: int) -> str:
    return time.strftime("%Y-%m-%dT%H", time.gmtime((t_ms // H) * 3600))


H0, H1, H2 = hour(T0), hour(T0 + H), hour(T0 + 2 * H)


def close(t=MID, p="P1", *, tval=0, orph=0, dn=0, gap=False, ws=0, status="ok", top=None, **fields):
    """A plain pool close. Defaults: item 5's n_ok, not incomplete."""
    f = {"kept_ws_only_before_cover": 0, "kept_ws_only_above_cover": 0, "kept_ws_only_sig_on_tip": 0, "dropped_boost_n": 0, "dropped_sells_n": 0,
         "dropped": [], "dropped_first_slot": None, "dropped_last_slot": None}
    f.update(fields)
    r = pool_rec(t=t, pool=p, ws=ws, tval=tval, gap=gap, btd=bt(status=status, dropped_n=dn, orphaned_by_drop_n=orph, **f))
    r.update(top or {})
    return r


def gaprec(kind, t=MID, **kw):
    r = {"type": "gap", "t_ms": t, "kind": kind, "open_pools": 3, "flags_pools": True}
    if kind == "slot_jump":
        r.update(from_slot=100, to_slot=200, missed_slots=99)
    elif kind == "silence":
        r.update(silent_ms=20001)
    r.update(kw)
    return r


def ent(kind, **kw):
    return {"kind": kind, "from_slot": 5, "to_slot": 9, "missed_slots": 77, **kw}


def decorate(r):
    for k, v in FORBID.items():
        r.setdefault(k, v)
    return r


def fx_a():
    """Table A. Hand-computed: incomplete = a2, a3, a4, a9; other n_ok = a1, a5, a6, a7, a8, a10, a11, a12."""
    r = [
        close(MID, "A1"),
        close(MID + 1, "A2", tval=2, dn=3, dropped_boost_n=1),
        close(MID + 2, "A3", orph=1, dn=7, kept_ws_only_above_cover=2, dropped_sells_n=1),
        close(T0 + H + 5, "A4", tval=1, orph=3, dn=21, kept_ws_only_sig_on_tip=1),
        close(T0 + H + 6, "A5", dn=1, kept_ws_only_before_cover=4),
        close(T0 + 2 * H + 7, "A6", dn=None, kept_ws_only_before_cover=-1, kept_ws_only_above_cover=None, dropped_boost_n="x", dropped_sells_n=True),
        close(T0 + 2 * H + 8, "A7", tval=-1, dn=0),
        close(T0 + 2 * H + 9, "A8", orph=True, dn=5),
        close(T0 + 2 * H + 10, "A9", tval=3, orph=1, dn=6),
        close(T0 + 2 * H + 11, "A10", dn=20),
        close(T0 + 2 * H + 12, "A11", dn=2),
        close(T0 + 2 * H + 13, "A12", dn=-1),
        # in the window and unsealed, but not in n_ok
        close(MID + 3, "X1", gap=True, top={"gaps": [ent("slot_jump")]}),
        close(MID + 4, "X2", ws=1),
        close(MID + 5, "X3", ws=None),
        close(MID + 6, "X4", status="tip_pending", tval=None),
        close(MID + 7, "X10", tval=None),
        close(MID + 8, "X11", tval=True),
        close(MID + 9, "X14", gap=None),
        # sealed or not a string pool: skipped and counted
        close(MID + 10, "X5", top={"sealed": True}),
        close(MID + 11, "X6", top={"h5_look2_sealed": True}),
        close(MID + 12, "X7", top={"sealed": "yes"}),
        close(MID + 13, "X8", top={"sealed": True, "h5_look2_sealed": True}),
        close(MID + 14, 5),
        # outside the window
        close(UNTIL + 1, "X12", tval=4),
        close(T0 + 500, "X13", tval=4),
        # other types are not read
        trig(MID + 15, "T1", gap=False),
        gaprec("slot_jump", MID + 16),
        {"type": "outcome", "variant": "pv", "pool": "A2", "t_ms": MID + 17, "gap": False, "base_tipauth": {"status": "ok"}},
    ]
    return r


def fx_b():
    """Table B. Hand-computed in the tests."""
    r = []
    for i, m in enumerate([29, 30, 59, 60, 149, 150, 599, 600]):
        r.append(gaprec("slot_jump", MID + 20 + i, missed_slots=m))
    r.append(gaprec("slot_jump", MID + 30))  # missed_slots = 99 default
    j = gaprec("slot_jump", MID + 31)
    del j["missed_slots"]
    r += [j, gaprec("slot_jump", MID + 32, missed_slots="x"), gaprec("slot_jump", MID + 33, missed_slots=True)]
    r += [gaprec("silence", MID + 40), gaprec("silence", MID + 41), gaprec("feed_restart", MID + 42, reason="r")]
    sr = [gaprec("socket_reconnect", MID + 50, flags_pools=True), gaprec("socket_reconnect", MID + 51, flags_pools=False), gaprec("socket_reconnect", MID + 52, flags_pools="no"),
          gaprec("socket_reconnect", MID + 53, flags_pools=None), gaprec("socket_reconnect", MID + 54)]
    del sr[-1]["flags_pools"]
    r += sr
    r += [gaprec("ambiguous_mint", MID + 60), gaprec("announced_before_gap", MID + 61), gaprec("slot_below_s0", MID + 62)]
    r += [gaprec("weird", MID + 63), gaprec(None, MID + 64), gaprec(5, MID + 65), gaprec(["x"], MID + 66)]
    r += [gaprec("silence", UNTIL + 1), gaprec("silence", T0 + 500), gaprec("silence", "1")]  # outside the window; non-integer t_ms
    # plain pool closes
    G = lambda *ks: [ent(k) for k in ks]  # noqa: E731
    pools = [
        close(MID + 100, "Q1"),
        close(MID + 101, "Q2", gap=True, top={"gaps": G("slot_jump")}),
        close(MID + 102, "Q3", gap=True, top={"gaps": G("slot_jump", "silence")}),
        close(MID + 103, "Q4", gap=True, top={"gaps": G("silence", "slot_jump", "silence")}),
        close(MID + 104, "Q5", gap=True, top={"gaps": G(*["slot_jump"] * 5)}),
        close(MID + 105, "Q6", gap=True, top={"gaps": []}),
        close(MID + 106, "Q7", gap=True),
        close(MID + 107, "Q8", gap=True, top={"gaps": [{"kind": "zzz"}, "notadict", {"kind": 5}, {}]}),
        close(MID + 108, "Q9", gap=None, top={"gaps": G("silence")}),
        close(MID + 109, "Q10", gap="yes", top={"gaps": G("silence")}),
        close(MID + 110, "Q11", gap=True, top={"gaps": G("socket_reconnect", "feed_restart", "silence", "slot_jump")}),
        close(MID + 111, "Q12", gap=True, top={"gaps": G(*["silence"] * 6)}),
        close(MID + 112, "Q13", gap=True, top={"gaps": G("ambiguous_mint", "announced_before_gap", "slot_below_s0")}),
        close(MID + 113, "Q14", gap=True, top={"gaps": G("slot_jump", "silence", "slot_jump", "silence", "slot_jump")}),
        close(MID + 114, "Q15", gap=True, top={"sealed": True, "gaps": G("slot_jump")}),
        close(MID + 115, 5, gap=True, top={"gaps": G("slot_jump")}),
        close(MID + 116, "Q17", gap=False, top={"gaps": G("slot_jump")}),
    ]
    trigs = [
        trig(MID + 120, "R1", gap=False),
        trig(MID + 121, "R2", gap=True, gaps=G("socket_reconnect")),
        trig(MID + 122, "R3", gap=True, gaps=G(*["slot_jump"] * 5)),
        trig(MID + 123, "R4", variant="fv", gap=True, gaps=G("slot_jump")),
        trig(MID + 124, "R5", gap=True, gaps=G("slot_jump"), h5_look2_sealed=True),
        trig(MID + 125, 5, gap=True, gaps=G("slot_jump")),
        trig(MID + 126, "R7", variant="xx", gap=True, gaps=G("slot_jump")),
        trig(MID + 127, "R8", gap=None, gaps=G("slot_jump")),
    ]
    outs = [{"type": "outcome", "variant": "pv", "pool": "Q2", "t_ms": MID + 130 + i, "gap": True, "gaps": G("silence"), "sealed": False, "net_sol": 3.0} for i in range(2)]
    return r + pools + trigs + outs


def fx_leak():
    """Identifiers and a distinctive slot that must not appear in any output."""
    return [close(MID + 777, "PoolSecretAAA", tval=1, dn=1, dropped=[["SigSecretCCC", 0, 987654321]], top={"mint": "MintSecretBBB", "slot": 987654321}),
            close(MID + 778, "PoolSecretDDD", gap=True, top={"gaps": [ent("slot_jump", from_slot=987654321)], "slot": 987654321}),
            trig(MID + 779, "PoolSecretEEE", gap=True, gaps=[ent("silence")], slot=987654321)]


class Rec(dict):
    """A dict that records every key looked at, by the `type` of the record it belongs to ("<nested>" for an object without a type)."""
    log: dict = collections.defaultdict(set)

    def _n(self, k):
        Rec.log[dict.get(self, "type", "<nested>")].add(k)

    def __getitem__(self, k):
        self._n(k)
        return super().__getitem__(k)

    def get(self, k, default=None):
        self._n(k)
        return super().get(k, default)

    def __contains__(self, k):
        self._n(k)
        return super().__contains__(k)

    def __iter__(self):
        self._n("<ITER>")
        return super().__iter__()

    def keys(self):
        self._n("<KEYS>")
        return super().keys()

    def values(self):
        self._n("<VALUES>")
        return super().values()

    def items(self):
        self._n("<ITEMS>")
        return super().items()

    def copy(self):
        self._n("<COPY>")
        return super().copy()

    def __len__(self):
        self._n("<LEN>")
        return super().__len__()

    def pop(self, *a):
        self._n("<POP>")
        return super().pop(*a)

    def popitem(self):
        self._n("<POPITEM>")
        return super().popitem()

    def setdefault(self, *a):
        self._n("<SETDEFAULT>")
        return super().setdefault(*a)

    def update(self, *a, **k):
        self._n("<UPDATE>")
        return super().update(*a, **k)


def rec_hook(d):
    return Rec(d)


def walk_keys(o, out=None):
    out = [] if out is None else out
    if isinstance(o, dict):
        for k, v in o.items():
            out.append(k)
            walk_keys(v, out)
    elif isinstance(o, list):
        for v in o:
            walk_keys(v, out)
    return out


def walk_leaves(o, out=None):
    out = [] if out is None else out
    if isinstance(o, dict):
        for v in o.values():
            walk_leaves(v, out)
    elif isinstance(o, list):
        for v in o:
            walk_leaves(v, out)
    else:
        out.append(o)
    return out


class Base(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self._od = tempfile.TemporaryDirectory()
        self.d = Path(self._td.name)
        self.outdir = Path(self._od.name)
        self.addCleanup(self._td.cleanup)
        self.addCleanup(self._od.cleanup)

    def put(self, recs, hour_ms=T0):
        write(self.d, [decorate(r) for r in recs], hour_ms)

    def i4(self, since=T0, until=UNTIL):
        return I4.analyze(self.d, since, until)

    def expect_from_i4(self, r=None):
        r = r or self.i4()
        i5 = r["item5_tip_incompleteness"]["plain_pool_closes"]
        return {"n_ok": i5["n_ok"], "incomplete_n": i5["incomplete_n"], "plain_pool_closes": r["records"]["plain_pool_closes"],
                "triggers_pv": r["records"]["triggers_pv"], "gap_records_by_kind": dict(r["records"]["gap_records_by_kind"])}

    def run_diag(self, expect=None, hook=None, since=T0, until=UNTIL, sha=SHA):
        return D.analyze(self.d, since, until, sha, expect or self.expect_from_i4(), object_hook=hook)

    def snap(self, until=UNTIL):
        return I4.snapshot(I4.hour_files(self.d, T0, until))

    def argv(self, expect=None, out="out.json", **over):
        e = expect or self.expect_from_i4()
        a = {"--shadow-dir": str(self.d), "--since-ms": str(T0), "--until-ms": str(UNTIL), "--expect-code-sha": SHA, "--expect-n-ok": str(e["n_ok"]),
             "--expect-incomplete-n": str(e["incomplete_n"]), "--expect-plain-pool-closes": str(e["plain_pool_closes"]), "--expect-triggers-pv": str(e["triggers_pv"]),
             "--expect-gap-records-by-kind": json.dumps(e["gap_records_by_kind"]), "--out": str(self.outdir / out)}
        a.update(over)
        return [x for kv in a.items() if kv[1] is not None for x in kv]

    def cli(self, argv, now_ms=NOW):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = D.main(argv, now_ms=now_ms)
        return code, buf.getvalue()


# ---- table A ------------------------------------------------------------------------------------------------------------------------------------

class TestItem5Table(Base):
    def setUp(self):
        super().setUp()
        self.put([start(ORIGIN, SHA)] + fx_a())

    def test_totals_and_cells(self):
        r = self.run_diag()
        t = r["item5_table"]
        self.assertTrue(t["totals_equal_item4"])
        self.assertEqual(t["totals"], {"expected": {"n_ok": 12, "incomplete_n": 4}, "found": {"n_ok": 12, "incomplete_n": 4}})
        inc, oth = t["result"]["incomplete"], t["result"]["other_n_ok"]
        self.assertEqual((inc["n"], oth["n"]), (4, 8))
        hours = {H0, H1, H2}
        self.assertEqual(set(inc["by_utc_hour"]), hours)  # both groups list the same hours
        self.assertEqual(set(oth["by_utc_hour"]), hours)
        self.assertEqual(inc["by_utc_hour"], {H0: 2, H1: 1, H2: 1})
        self.assertEqual(oth["by_utc_hour"], {H0: 1, H1: 1, H2: 6})
        self.assertEqual(inc["by_arm"], {"tipauth_only": 1, "orphaned_only": 1, "both": 2, "neither": 0})
        self.assertEqual(oth["by_arm"], {"tipauth_only": 0, "orphaned_only": 0, "both": 0, "neither": 8})
        self.assertEqual(inc["by_dropped_n_class"], {"0": 0, "1": 0, "2_to_5": 1, "6_to_20": 2, "above_20": 1, "not_int_or_negative": 0})
        self.assertEqual(oth["by_dropped_n_class"], {"0": 2, "1": 1, "2_to_5": 2, "6_to_20": 1, "above_20": 0, "not_int_or_negative": 2})
        al = lambda g: {f: v["at_least_1"] for f, v in g["by_field_at_least_1"].items()}  # noqa: E731
        self.assertEqual(al(inc), {"kept_ws_only_before_cover": 0, "kept_ws_only_above_cover": 1, "kept_ws_only_sig_on_tip": 1, "dropped_boost_n": 1, "dropped_sells_n": 1})
        self.assertEqual(al(oth), {"kept_ws_only_before_cover": 1, "kept_ws_only_above_cover": 0, "kept_ws_only_sig_on_tip": 0, "dropped_boost_n": 0, "dropped_sells_n": 0})
        self.assertEqual(inc["by_field_at_least_1"]["dropped_boost_n"], {"at_least_1": 1, "not_at_least_1": 3})
        self.assertEqual(oth["by_field_at_least_1"]["kept_ws_only_before_cover"], {"at_least_1": 1, "not_at_least_1": 7})

    def test_item5_totals_equal_item4(self):
        i5 = self.i4()["item5_tip_incompleteness"]["plain_pool_closes"]
        self.assertEqual((i5["n_ok"], i5["incomplete_n"]), (12, 4))

    def test_every_dimension_sums_to_the_group_size(self):
        for g in self.run_diag()["item5_table"]["result"].values():
            self.assertEqual(sum(g["by_utc_hour"].values()), g["n"])
            self.assertEqual(sum(g["by_arm"].values()), g["n"])
            self.assertEqual(sum(g["by_dropped_n_class"].values()), g["n"])
            for v in g["by_field_at_least_1"].values():
                self.assertEqual(v["at_least_1"] + v["not_at_least_1"], g["n"])

    def test_dropped_n_class_boundaries(self):
        for x, c in [(0, "0"), (1, "1"), (2, "2_to_5"), (5, "2_to_5"), (6, "6_to_20"), (20, "6_to_20"), (21, "above_20"), (10 ** 6, "above_20"), (-1, "not_int_or_negative"),
                     (None, "not_int_or_negative"), (True, "not_int_or_negative"), ("3", "not_int_or_negative"), (1.0, "not_int_or_negative"), (10 ** 30, "not_int_or_negative")]:
            self.assertEqual(D.dropped_class(x), c, x)

    def test_hour_buckets_split_at_the_hour(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            write(d, [start(ORIGIN, SHA), close(T0 + H - 1, "B1"), close(T0 + H, "B2"), close(T0 + 2 * H - 1, "B3", tval=1)])
            snap = I4.snapshot(I4.hour_files(d, T0, UNTIL))
            t = D.item5_table(snap, ORIGIN, UNTIL, 3, 1)
        self.assertEqual(t["result"]["other_n_ok"]["by_utc_hour"], {H0: 1, H1: 1})
        self.assertEqual(t["result"]["incomplete"]["by_utc_hour"], {H0: 0, H1: 1})

    def test_sealed_skipped_and_counted_like_item4(self):
        t = self.run_diag()["item5_table"]
        self.assertEqual(t["skipped"], {"sealed_pool": 3, "h5_look2_sealed_pool": 1, "both_flags": 1})
        sk = self.i4()["skipped"]
        self.assertEqual((sk["sealed_pool"], sk["h5_look2_sealed_pool"], sk["both_flags"]), (3, 1, 1))
        self.assertEqual(t["scan"]["record_pool_not_str"], 1)
        # 12 in n_ok + X1, X2, X3, X4, X10, X11, X14 read and unsealed
        self.assertEqual(t["unsealed_plain_pool_closes_read"], 19)

    def test_window_and_other_types(self):
        t = self.run_diag()["item5_table"]
        self.assertEqual(t["scan"]["records_outside_window"], 2)  # X12, X13
        self.assertEqual(t["scan"]["bad_line"], 0)
        # the sealed X5..X8 with a huge tipauth value would change the totals if read; the late X12 and early X13 likewise
        self.assertEqual(t["totals"]["found"], {"n_ok": 12, "incomplete_n": 4})

    def test_totals_unequal_n_ok(self):
        e = self.expect_from_i4()
        e["n_ok"] += 1
        t = self.run_diag(e)["item5_table"]
        self.assertFalse(t["totals_equal_item4"])
        self.assertIsNone(t["result"])
        self.assertEqual(t["totals"], {"expected": {"n_ok": 13, "incomplete_n": 4}, "found": {"n_ok": 12, "incomplete_n": 4}})

    def test_totals_unequal_incomplete_n(self):
        e = self.expect_from_i4()
        e["incomplete_n"] -= 1
        t = self.run_diag(e)["item5_table"]
        self.assertFalse(t["totals_equal_item4"])
        self.assertIsNone(t["result"])
        self.assertEqual(t["totals"]["found"], {"n_ok": 12, "incomplete_n": 4})

    def test_each_table_is_checked_on_its_own(self):
        e = self.expect_from_i4()
        e["n_ok"] += 1
        r = self.run_diag(e)
        self.assertIsNone(r["item5_table"]["result"])
        self.assertTrue(r["gap_table"]["totals_equal_item4"])
        self.assertIsNotNone(r["gap_table"]["result"])
        e = self.expect_from_i4()
        e["triggers_pv"] += 1
        r = self.run_diag(e)
        self.assertTrue(r["item5_table"]["totals_equal_item4"])
        self.assertIsNotNone(r["item5_table"]["result"])
        self.assertFalse(r["gap_table"]["totals_equal_item4"])
        self.assertIsNone(r["gap_table"]["result"])

    def test_no_gap_kind_in_the_item5_table(self):
        keys = set(walk_keys(self.run_diag()["item5_table"]["result"]))
        for k in D.SEVEN_KINDS:
            self.assertNotIn(k, keys)


# ---- table B ------------------------------------------------------------------------------------------------------------------------------------

class TestGapTable(Base):
    def setUp(self):
        super().setUp()
        self.put([start(ORIGIN, SHA)] + fx_b())

    def result(self, e=None):
        return self.run_diag(e)["gap_table"]

    def test_item4_records_block_for_the_fixture(self):
        rec = self.i4()["records"]
        self.assertEqual(rec["plain_pool_closes"], 15)
        self.assertEqual(rec["triggers_pv"], 4)
        self.assertEqual(rec["gap_records_by_kind"], {"ambiguous_mint": 1, "announced_before_gap": 1, "feed_restart": 1, "null": 3, "silence": 2, "slot_below_s0": 1,
                                                      "slot_jump": 12, "socket_reconnect": 5, "weird": 1})

    def test_seven_kinds_and_other(self):
        g = self.result()
        self.assertTrue(g["totals_equal_item4"])
        self.assertEqual(g["result"]["gap_records_by_kind"], {"slot_jump": 12, "silence": 2, "feed_restart": 1, "socket_reconnect": 5, "ambiguous_mint": 1,
                                                              "announced_before_gap": 1, "slot_below_s0": 1, "other": 4})
        self.assertEqual(list(g["result"]["gap_records_by_kind"]), list(D.KIND_LABELS))
        self.assertEqual(len(D.SEVEN_KINDS), 7)

    def test_missed_slots_classes(self):
        # 29 | 30 59 | 60 149 | 150 599 | 600 | missing, "x", True (and the default 99 is in 60_to_149)
        self.assertEqual(self.result()["result"]["slot_jump_by_missed_slots"],
                         {"below_30": 1, "30_to_59": 2, "60_to_149": 3, "150_to_599": 2, "600_and_more": 1, "not_int_or_missing": 3})

    def test_missed_slots_class_function_boundaries(self):
        for m, c in [(29, "below_30"), (30, "30_to_59"), (59, "30_to_59"), (60, "60_to_149"), (149, "60_to_149"), (150, "150_to_599"), (599, "150_to_599"), (600, "600_and_more"),
                     (10 ** 6, "600_and_more"), (0, "below_30"), (-5, "below_30"), (None, "not_int_or_missing"), (True, "not_int_or_missing"), ("60", "not_int_or_missing"),
                     (60.0, "not_int_or_missing"), (10 ** 30, "not_int_or_missing")]:
            self.assertEqual(D.missed_class(m), c, m)

    def test_jump_below_30_is_counted_in_its_own_cell(self):
        r = self.result()["result"]["slot_jump_by_missed_slots"]
        self.assertEqual(r["below_30"], 1)
        self.assertEqual(sum(r.values()), self.result()["result"]["gap_records_by_kind"]["slot_jump"])

    def test_socket_reconnect_by_flags_pools(self):
        self.assertEqual(self.result()["result"]["socket_reconnect_by_flags_pools"], {"true": 1, "false": 1, "missing_or_not_bool": 3})

    def test_flags_pools_only_read_for_socket_reconnect(self):
        Rec.log = collections.defaultdict(set)
        self.run_diag(hook=rec_hook)
        self.assertIn("flags_pools", Rec.log["gap"])
        # a silence record with flags_pools False must not be in any flags_pools cell
        r = D.gap_table(self.snap(), ORIGIN, UNTIL, self.expect_from_i4()["gap_records_by_kind"], 15, 4)
        self.assertEqual(sum(r["result"]["socket_reconnect_by_flags_pools"].values()), 5)

    def test_pool_close_split_by_kind_set(self):
        p = self.result()["result"]["unsealed_plain_pool_closes"]
        self.assertEqual((p["n"], p["gap_true"], p["gap_false"], p["gap_not_bool"]), (15, 11, 2, 2))
        self.assertEqual(p["gap_true_by_kind_set"], {"slot_jump": 1, "silence+slot_jump": 2, "(no_entries)": 2, "other": 1, "feed_restart+silence+slot_jump+socket_reconnect": 1,
                                                     "silence": 1, "ambiguous_mint+announced_before_gap+slot_below_s0": 1})
        self.assertEqual(p["gaps_len_5_n"], 2)
        self.assertEqual(p["gap_true_gaps_len_5_by_kind_set_lower_bound"], {"slot_jump": 1, "silence+slot_jump": 1})
        self.assertEqual(sum(p["gap_true_by_kind_set"].values()) + sum(p["gap_true_gaps_len_5_by_kind_set_lower_bound"].values()), p["gap_true"])

    def test_trigger_split_is_pv_only(self):
        t = self.result()["result"]["unsealed_pv_triggers"]
        self.assertEqual((t["n"], t["gap_true"], t["gap_false"], t["gap_not_bool"]), (4, 2, 1, 1))
        self.assertEqual(t["gap_true_by_kind_set"], {"socket_reconnect": 1})
        self.assertEqual(t["gaps_len_5_n"], 1)
        self.assertEqual(t["gap_true_gaps_len_5_by_kind_set_lower_bound"], {"slot_jump": 1})

    def test_exactly_5_count_is_separate_from_four_and_six(self):
        # Q5, Q14 have 5 entries; Q11 has 4 and Q12 has 6 and stay in the ordinary table
        p = self.result()["result"]["unsealed_plain_pool_closes"]
        self.assertEqual(p["gaps_len_5_n"], 2)
        self.assertIn("silence", p["gap_true_by_kind_set"])  # Q12, 6 entries
        self.assertEqual(D.kind_set([ent("silence")] * 5), ("silence", True))
        self.assertEqual(D.kind_set([ent("silence")] * 4), ("silence", False))
        self.assertEqual(D.kind_set([]), (D.NO_ENTRIES, False))
        self.assertEqual(D.kind_set(None), (D.NO_ENTRIES, False))
        self.assertEqual(D.kind_set("slot_jump"), (D.NO_ENTRIES, False))

    def test_sealed_skip_and_counts(self):
        g = self.result()
        self.assertEqual(g["skipped"], {"sealed_pool": 1, "h5_look2_sealed_pool": 0, "sealed_trigger": 0, "h5_look2_sealed_trigger": 1, "both_flags": 0})
        self.assertEqual(g["scan"]["record_pool_not_str"], 2)
        sk = self.i4()["skipped"]
        self.assertEqual((sk["sealed_pool"], sk["h5_look2_sealed_pool"], sk["sealed_trigger"], sk["h5_look2_sealed_trigger"]), (1, 0, 0, 1))
        self.assertEqual(g["scan"]["records_without_int_t_ms"], 1)

    def test_outcome_records_are_not_counted(self):
        p = self.result()["result"]["unsealed_plain_pool_closes"]
        self.assertEqual(p["n"], 15)  # the two outcome records (pool Q2, gap True) are not closes

    def test_totals_equal_and_unequal_for_each_total(self):
        base = self.expect_from_i4()
        self.assertTrue(self.result(base)["totals_equal_item4"])
        for key, bad in [("plain_pool_closes", 16), ("triggers_pv", 5), ("gap_records_by_kind", {**base["gap_records_by_kind"], "slot_jump": 11}),
                         ("gap_records_by_kind", {k: v for k, v in base["gap_records_by_kind"].items() if k != "weird"})]:
            e = dict(base)
            e[key] = bad
            g = self.result(e)
            self.assertFalse(g["totals_equal_item4"], key)
            self.assertIsNone(g["result"], key)
            self.assertFalse(g["totals_equal_by_total"][key])
            self.assertEqual(sum(1 for v in g["totals_equal_by_total"].values() if not v), 1)
            self.assertEqual(g["totals"]["found"]["plain_pool_closes"], 15)
            self.assertEqual(g["totals"]["found"]["triggers_pv"], 4)

    def test_printed_found_kinds_are_labels_only(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            write(d, [start(ORIGIN, SHA), gaprec("Weird Kind! with a/slash", MID), gaprec("silence", MID + 1)])
            snap = I4.snapshot(I4.hour_files(d, T0, UNTIL))
            g = D.gap_table(snap, ORIGIN, UNTIL, {"silence": 1}, 0, 0)
        self.assertFalse(g["totals_equal_item4"])
        self.assertEqual(g["totals"]["found"]["gap_records_by_kind"], {"other": 1, "silence": 1})
        self.assertNotIn("Weird", json.dumps(g))


# ---- the recording-dict tests ---------------------------------------------------------------------------------------------------------------------

class TestKeysRead(Base):
    def setUp(self):
        super().setUp()
        self.put([start(ORIGIN, SHA)] + fx_a() + fx_b() + [hb(MID + 200), {"type": "boost_slice", "pool": "P1", "t_ms": MID, "sol": 5.0},
                                                          {"type": "excluded", "pool": "E", "t_ms": MID, "slot": 1}, {"type": "tip_union_start", "t_ms": MID}])

    def test_item5_table_reads_no_key_outside_item4_allowed_keys(self):
        Rec.log = collections.defaultdict(set)
        e = self.expect_from_i4()
        t = D.item5_table(self.snap(), ORIGIN, UNTIL, e["n_ok"], e["incomplete_n"], object_hook=rec_hook)
        self.assertTrue(t["totals_equal_item4"])
        log = Rec.log
        union = set().union(*log.values())
        self.assertGreater(len(union), 20)
        self.assertEqual(union - I4.ALLOWED_KEYS, set(), f"keys read outside item 4's list: {sorted(union - I4.ALLOWED_KEYS, key=str)}")
        self.assertEqual(union & D.WIDENED_KEYS, set())  # missed_slots, flags_pools, gaps are the gap table's only
        self.assertEqual(union & POISON, {"slot", "boost_src"} & union)  # item 4's own view of a pool record reads these two, both in its list
        self.assertEqual(union & (POISON - {"slot", "boost_src"}), set())
        for typ in ("outcome", "trigger", "gap", "hb", "start", "boost_slice", "excluded", "tip_union_start"):
            self.assertEqual(log[typ], {"type"}, typ)  # not read past `type`
        self.assertIn("pool", log)

    def test_gap_table_reads_only_the_listed_keys(self):
        Rec.log = collections.defaultdict(set)
        e = self.expect_from_i4()
        g = D.gap_table(self.snap(), ORIGIN, UNTIL, e["gap_records_by_kind"], e["plain_pool_closes"], e["triggers_pv"], object_hook=rec_hook)
        self.assertTrue(g["totals_equal_item4"])
        log = Rec.log
        union = set().union(*log.values())
        self.assertEqual(union - D.GAP_TABLE_KEYS, set(), f"keys read outside the gap table's list: {sorted(union - D.GAP_TABLE_KEYS, key=str)}")
        self.assertEqual(union & POISON, set())
        self.assertEqual(log["gap"], {"type", "t_ms", "kind", "missed_slots", "flags_pools"})
        self.assertEqual(log["pool"], {"type", "t_ms", "sealed", "h5_look2_sealed", "pool", "gap", "gaps"})
        self.assertEqual(log["trigger"], {"type", "t_ms", "sealed", "h5_look2_sealed", "pool", "variant", "gap", "gaps"})
        self.assertEqual(log["<nested>"], {"kind"})  # inside a `gaps` entry: kind only, never its own missed_slots, from_slot, to_slot
        for typ in ("outcome", "hb", "start", "boost_slice", "excluded", "tip_union_start"):
            self.assertEqual(log[typ], {"type"}, typ)

    def test_whole_run_reads_only_item4_list_plus_the_three_names(self):
        Rec.log = collections.defaultdict(set)
        r = self.run_diag(hook=rec_hook)
        self.assertTrue(r["item5_table"]["totals_equal_item4"] and r["gap_table"]["totals_equal_item4"])
        union = set().union(*Rec.log.values())
        self.assertEqual(union - (I4.ALLOWED_KEYS | D.WIDENED_KEYS), set())
        self.assertIn("code_sha", Rec.log["start"])
        self.assertEqual(Rec.log["start"], {"type", "t_ms", "code_sha"})
        self.assertEqual(Rec.log["outcome"], {"type"})

    def test_hook_does_not_change_the_result(self):
        plain = self.run_diag()
        hooked = self.run_diag(hook=rec_hook)
        for t in ("item5_table", "gap_table"):
            self.assertEqual(plain[t]["result"], hooked[t]["result"])

    def test_key_lists(self):
        self.assertEqual(D.GAP_TABLE_KEYS - I4.ALLOWED_KEYS, {"missed_slots", "flags_pools", "gaps"})
        self.assertEqual(D.WIDENED_KEYS, {"missed_slots", "flags_pools", "gaps"})
        self.assertLessEqual(D.GAP_TABLE_KEYS, I4.ALLOWED_KEYS | D.WIDENED_KEYS)
        self.assertEqual(D.ITEM5_KEYS, I4.ALLOWED_KEYS)
        for k in ("from_slot", "to_slot", "silent_ms", "open_pools", "reason", "at_ms", "sockets_down_recent"):
            self.assertNotIn(k, D.GAP_TABLE_KEYS)
            self.assertNotIn(k, D.ITEM5_KEYS)
        self.assertFalse({k for k in D.GAP_TABLE_KEYS if any(w in k for w in ("pnl", "net_", "fill", "outcome", "min_q", "q_trigger", "_sol", "ladder", "return", "exit_"))})


# ---- output shape ---------------------------------------------------------------------------------------------------------------------------------

class TestOutputShape(Base):
    def setUp(self):
        super().setUp()
        self.put([start(ORIGIN, SHA)] + fx_a() + fx_b() + fx_leak())

    def test_no_what_if_cells(self):
        r = self.run_diag()["gap_table"]["result"]
        self.assertEqual(set(r), {"gap_records_by_kind", "slot_jump_by_missed_slots", "socket_reconnect_by_flags_pools", "unsealed_plain_pool_closes", "unsealed_pv_triggers"})
        for pop in ("unsealed_plain_pool_closes", "unsealed_pv_triggers"):
            self.assertEqual(set(r[pop]), {"n", "gap_true", "gap_false", "gap_not_bool", "gaps_len_5_n", "gap_true_by_kind_set", "gap_true_gaps_len_5_by_kind_set_lower_bound"})
        for name in ("gap_records_by_kind", "slot_jump_by_missed_slots", "socket_reconnect_by_flags_pools"):
            self.assertTrue(all(type(v) is int for v in r[name].values()), name)  # flat: no nested cell
        for pop in ("unsealed_plain_pool_closes", "unsealed_pv_triggers"):
            for name in ("gap_true_by_kind_set", "gap_true_gaps_len_5_by_kind_set_lower_bound"):
                self.assertTrue(all(type(v) is int for v in r[pop][name].values()), name)
        self.assertEqual(list(r["slot_jump_by_missed_slots"]), list(D.MISSED_CLASSES))
        banned = {"ws", "tipauth", "would", "pass", "incomplete", "incompleteness", "orphaned", "orphan", "union", "share", "pct", "percent", "ratio", "rate", "trigger_slot"}
        for k in walk_keys(self.run_diag()["gap_table"]["result"]):
            self.assertFalse(set(re.split(r"[_+]", str(k))) & banned, k)
        for pop in ("unsealed_plain_pool_closes", "unsealed_pv_triggers"):
            for lab in list(r[pop]["gap_true_by_kind_set"]) + list(r[pop]["gap_true_gaps_len_5_by_kind_set_lower_bound"]):
                for part in lab.split("+"):
                    self.assertIn(part, D.KIND_LABELS + (D.NO_ENTRIES,))

    def test_counts_only_output(self):
        res = self.run_diag()
        text = D.dumps(res)
        leaves = walk_leaves(res)
        self.assertFalse([x for x in leaves if isinstance(x, float)])
        self.assertNotIn("%", text)
        for k in walk_keys(res):
            self.assertIsNone(re.search(r"share|pct|percent|ratio", str(k)), k)
        for s in ("PoolSecret", "MintSecret", "SigSecret", "987654321", "111111111", "222222222", "weird"):  # `weird` is an unknown kind in fx_b
            self.assertNotIn(s, text)
        for t in (MID + 777, MID + 778, MID + 779):
            self.assertNotIn(str(t), text)
        # the only record-derived times are the UTC hour labels; the operator's own instants and the origin are printed
        printed_ints = {x for x in leaves if isinstance(x, int) and not isinstance(x, bool) and x > 10 ** 9}
        self.assertLessEqual(printed_ints, {T0, UNTIL, ORIGIN} | {n for n in walk_leaves(res["files"]["sizes"])})

    def test_output_is_deterministic_json(self):
        self.assertEqual(D.dumps(self.run_diag()), D.dumps(self.run_diag()))
        json.loads(D.dumps(self.run_diag()))

    def test_inputs_origin_and_tool_hash(self):
        r = self.run_diag()
        self.assertEqual(r["tool_sha256"], hashlib.sha256(Path(D.__file__).read_bytes()).hexdigest())
        self.assertEqual(r["window"], {"n_starts_in_range": 1, "origin_t_ms": ORIGIN, "origin_code_sha": SHA, "origin_code_sha_matches": True})
        self.assertEqual(r["inputs"]["since_ms"], T0)
        self.assertEqual(r["inputs"]["until_ms"], UNTIL)
        self.assertEqual(r["files"]["names"], [f"h5-shadow-{H0}.jsonl"])
        self.assertIn("EXP-024 ended NOT_DECIDABLE on 2026-10-13 with no look read; not a book", r["label"])
        self.assertIn("H5 has not cleared the promotion gate", r["label"])

    def test_origin_is_the_last_start_in_range(self):
        self.put([start(T0 + 5000, "b" * 40), start(UNTIL + 10, "c" * 40)])
        e = self.expect_from_i4()
        r = self.run_diag(e, sha="b" * 40)
        self.assertEqual(r["window"]["origin_t_ms"], T0 + 5000)
        self.assertEqual(r["window"]["n_starts_in_range"], 2)
        self.assertEqual(r["window"]["origin_t_ms"], self.i4()["starts"]["origin_t_ms"])
        self.assertEqual(r["window"]["origin_code_sha"], self.i4()["starts"]["origin_code_sha"])

    def test_source_has_no_forbidden_wording_and_no_network_import(self):
        src = Path(D.__file__).read_text()
        for pat in (r"profitable", r"\bedges?\b", r"likely", r"probably", r"too strict", r"trades lost", r"recoverable", r"unreliable", r"tape outage"):
            self.assertIsNone(re.search(pat, src, re.I), pat)
        self.assertNotIn("%", re.sub(r"%[ds]", "", src.replace("%Y-%m-%dT%H", "")))
        text = D.dumps(self.run_diag())
        for pat in (r"profitable", r"\bedges?\b", r"likely", r"probably", r"too strict", r"trades lost", r"recoverable", r"unreliable", r"tape outage"):
            self.assertIsNone(re.search(pat, text, re.I), pat)
        mods = set()
        for n in ast.walk(ast.parse(src)):
            if isinstance(n, ast.Import):
                mods |= {a.name.split(".")[0] for a in n.names}
            elif isinstance(n, ast.ImportFrom):
                mods.add((n.module or "").split(".")[0])
        self.assertLessEqual(mods, {"__future__", "argparse", "collections", "hashlib", "json", "os", "re", "sys", "time", "pathlib", "typing", "tools"})
        self.assertEqual(len(re.findall(r"\bopen\(", re.sub(r'""".*?"""', "", src, flags=re.S))), 1)  # only the exclusive --out file


# ---- agreement with item 4 ------------------------------------------------------------------------------------------------------------------------

def rand_fixture(seed: int, n_pool=800, n_trig=300, n_gap=250):
    """Mostly valid values with a tail of malformed ones, so n_ok is large and every branch is reached."""
    rng = random.Random(seed)
    pick = rng.choice
    nums = [0, 0, 1, 1, 2, 5, 6, 20, 21, -1, None, True, "x", 10 ** 30, 3, 7]
    flags = [None] * 20 + [False] * 20 + [True, "yes", 0, 1]
    gaps_vals = [False] * 8 + [True] * 3 + [None, "x"]
    kinds = list(D.SEVEN_KINDS) + ["weird", None, 5]
    r = [start(ORIGIN, SHA)]
    for i in range(n_pool):
        t = T0 + 2000 + rng.randrange(0, 7 * H)
        c = close(t, pick(["P%d" % rng.randrange(40)] * 10 + [5]), tval=pick([0] * 6 + [1, 2, -1, None, True, "x"]), orph=pick(nums), dn=pick(nums), gap=pick(gaps_vals),
                  ws=pick([0] * 10 + [1, None, "x", True]), status=pick(["ok"] * 10 + ["tip_pending", None]),
                  top={"sealed": pick(flags), "h5_look2_sealed": pick(flags), "gaps": [ent(pick(kinds)) for _ in range(pick([0, 1, 2, 4, 5, 6]))]},
                  **{f: pick(nums) for f in BTFIELDS})
        r.append(c)
    for i in range(n_trig):
        t = T0 + 2000 + rng.randrange(0, 7 * H)
        r.append(trig(t, pick(["P%d" % rng.randrange(40)] * 10 + [5]), variant=pick(["pv", "pv", "fv", "xx"]), gap=pick(gaps_vals), gaps=[ent(pick(kinds)) for _ in range(pick([0, 1, 5]))],
                      sealed=pick(flags), h5_look2_sealed=pick(flags)))
    for i in range(n_gap):
        t = pick([T0 + 2000 + rng.randrange(0, 7 * H)] * 8 + ["1", None])
        r.append(gaprec(pick(kinds), t, missed_slots=pick(nums + [29, 30, 59, 60, 149, 150, 599, 600]), flags_pools=pick(flags)))
    rng.shuffle(r[1:])
    return r


class TestAgreement(Base):
    def check(self, recs):
        self.put(recs)
        i4 = self.i4()
        r = self.run_diag()
        a, b = r["item5_table"], r["gap_table"]
        i5 = i4["item5_tip_incompleteness"]["plain_pool_closes"]
        self.assertEqual(a["totals"]["found"], {"n_ok": i5["n_ok"], "incomplete_n": i5["incomplete_n"]})
        self.assertTrue(a["totals_equal_item4"])
        rec = i4["records"]
        self.assertEqual(b["totals"]["found"]["plain_pool_closes"], rec["plain_pool_closes"])
        self.assertEqual(b["totals"]["found"]["triggers_pv"], rec["triggers_pv"])
        printed: collections.Counter = collections.Counter()
        for k, n in rec["gap_records_by_kind"].items():
            printed[k if k in D.SEVEN_KINDS else "other"] += n
        self.assertEqual(b["totals"]["found"]["gap_records_by_kind"], dict(sorted(printed.items())))
        self.assertTrue(b["totals_equal_item4"])
        sk = i4["skipped"]
        self.assertEqual(a["skipped"]["sealed_pool"], sk["sealed_pool"])
        self.assertEqual(a["skipped"]["h5_look2_sealed_pool"], sk["h5_look2_sealed_pool"])
        self.assertEqual(b["skipped"]["sealed_pool"], sk["sealed_pool"])
        self.assertEqual(b["skipped"]["sealed_trigger"], sk["sealed_trigger"])
        self.assertEqual(b["skipped"]["h5_look2_sealed_trigger"], sk["h5_look2_sealed_trigger"])
        self.assertEqual(b["skipped"]["both_flags"], sk["both_flags"])
        self.assertEqual(r["window"]["origin_t_ms"], i4["starts"]["origin_t_ms"])
        # identities
        for g in a["result"].values():
            self.assertEqual(sum(g["by_utc_hour"].values()), g["n"])
            self.assertEqual(sum(g["by_arm"].values()), g["n"])
            self.assertEqual(sum(g["by_dropped_n_class"].values()), g["n"])
        self.assertEqual(a["result"]["incomplete"]["by_arm"]["neither"], 0)
        self.assertEqual(sum(a["result"]["other_n_ok"]["by_arm"][k] for k in ("tipauth_only", "orphaned_only", "both")), 0)
        res = b["result"]
        self.assertEqual(sum(res["gap_records_by_kind"].values()), sum(rec["gap_records_by_kind"].values()))
        self.assertEqual(sum(res["slot_jump_by_missed_slots"].values()), res["gap_records_by_kind"]["slot_jump"])
        self.assertEqual(sum(res["socket_reconnect_by_flags_pools"].values()), res["gap_records_by_kind"]["socket_reconnect"])
        for pop in ("unsealed_plain_pool_closes", "unsealed_pv_triggers"):
            p = res[pop]
            self.assertEqual(p["gap_true"] + p["gap_false"] + p["gap_not_bool"], p["n"])
            self.assertEqual(sum(p["gap_true_by_kind_set"].values()) + sum(p["gap_true_gaps_len_5_by_kind_set_lower_bound"].values()), p["gap_true"])
            self.assertEqual(sum(p["gap_true_gaps_len_5_by_kind_set_lower_bound"].values()), p["gaps_len_5_n"])
        return i4, r

    def test_deterministic_fixture(self):
        i4, r = self.check([start(ORIGIN, SHA)] + fx_a() + fx_b())
        self.assertEqual(r["item5_table"]["totals"]["found"], {"n_ok": 12 + 2, "incomplete_n": 4})  # + Q1, Q17 (gap False, tipauth ok, ws 0)

    def test_seeded_random_fixtures(self):
        for seed in (1, 2, 3):
            with self.subTest(seed=seed):
                self.clear_dir()
                i4, r = self.check(rand_fixture(seed))
                self.assertGreater(r["item5_table"]["totals"]["found"]["n_ok"], 50)
                self.assertGreater(r["item5_table"]["totals"]["found"]["incomplete_n"], 10)
                self.assertGreater(r["gap_table"]["totals"]["found"]["triggers_pv"], 50)
                self.assertGreater(r["gap_table"]["result"]["unsealed_plain_pool_closes"]["gaps_len_5_n"], 5)

    def clear_dir(self):
        for p in self.d.iterdir():
            p.unlink()

    def test_flipping_one_item4_total_breaks_only_its_table(self):
        self.put([start(ORIGIN, SHA)] + fx_a() + fx_b())
        for key, tbl in [("n_ok", "item5_table"), ("incomplete_n", "item5_table"), ("plain_pool_closes", "gap_table"), ("triggers_pv", "gap_table")]:
            e = self.expect_from_i4()
            e[key] += 1
            r = self.run_diag(e)
            other = "gap_table" if tbl == "item5_table" else "item5_table"
            self.assertFalse(r[tbl]["totals_equal_item4"], key)
            self.assertTrue(r[other]["totals_equal_item4"], key)


def label_counts(d):
    return D.label_counts(d)


# ---- the command line -----------------------------------------------------------------------------------------------------------------------------

class TestCli(Base):
    def setUp(self):
        super().setUp()
        self.put([start(ORIGIN, SHA)] + fx_a() + fx_b())

    def test_success_writes_stdout_and_exclusive_file(self):
        code, out = self.cli(self.argv())
        self.assertEqual(code, 0)
        f = self.outdir / "out.json"
        self.assertEqual(f.read_text(), out)
        r = json.loads(out)
        self.assertIsNotNone(r["item5_table"]["result"])
        self.assertIsNotNone(r["gap_table"]["result"])
        self.assertTrue(r["item5_table"]["totals_equal_item4"] and r["gap_table"]["totals_equal_item4"])

    def test_out_exists_exit_6_and_not_overwritten(self):
        f = self.outdir / "out.json"
        f.write_text("keep")
        code, out = self.cli(self.argv())
        self.assertEqual(code, 6)
        self.assertEqual(f.read_text(), "keep")
        self.assertEqual(json.loads(out), {"tool": D.TOOL, "error": D.OUT_EXISTS_MSG, "exit_code": 6})  # only the error JSON, one document
        self.assertNotIn("item5_table", out)

    def test_out_directory_missing_exit_6(self):
        code, out = self.cli(self.argv(out="nodir/out.json"))
        self.assertEqual(code, 6)
        self.assertEqual(json.loads(out), {"tool": D.TOOL, "error": D.OUT_FAIL_MSG, "exit_code": 6})
        self.assertNotIn("item5_table", out)
        self.assertNotIn(str(self.outdir), out)  # a fixed message: no path, no exception text

    def test_out_directory_not_writable_exit_6_no_traceback_no_counts(self):
        self.outdir.chmod(0o555)
        self.addCleanup(self.outdir.chmod, 0o700)
        if os.access(self.outdir, os.W_OK):
            self.skipTest("the directory stays writable for this user (root)")
        called = []
        with mock.patch.object(D, "analyze", side_effect=lambda *a, **k: called.append(1)):
            code, out = self.cli(self.argv())
        self.assertEqual(code, 6)
        self.assertEqual(called, [])  # the files were not read: the result could not have been kept
        self.assertEqual(json.loads(out), {"tool": D.TOOL, "error": D.OUT_FAIL_MSG, "exit_code": 6})
        self.assertEqual(list(self.outdir.iterdir()), [])

    def test_out_is_created_before_the_analysis(self):
        f = self.outdir / "out.json"
        seen = []
        real = D.analyze

        def spy(*a, **k):
            seen.append(f.exists())
            return real(*a, **k)
        with mock.patch.object(D, "analyze", side_effect=spy):
            code, out = self.cli(self.argv())
        self.assertEqual((code, seen), (0, [True]))
        self.assertEqual(f.read_text(), out)

    def test_write_failure_is_exit_6_with_no_counts_and_no_file_left(self):
        class Failing:
            def __init__(self, fh):
                self.fh = fh

            def write(self, text):
                raise OSError(28, "No space left on device")

            def close(self):
                self.fh.close()
        real = D.create_out
        with mock.patch.object(D, "create_out", side_effect=lambda p: Failing(real(p))):
            code, out = self.cli(self.argv())
        self.assertEqual(code, 6)
        self.assertEqual(json.loads(out), {"tool": D.TOOL, "error": D.OUT_FAIL_MSG, "exit_code": 6})
        self.assertNotIn("No space", out)
        self.assertFalse((self.outdir / "out.json").exists())

    def test_unexpected_error_in_the_analysis_leaves_no_empty_file(self):
        with mock.patch.object(D, "analyze", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                self.cli(self.argv())
        self.assertFalse((self.outdir / "out.json").exists())

    def test_unknown_kinds_are_never_printed(self):
        """B1: a lowercase kind outside the seven (in a record and in the operator's flag) is `other` everywhere; the totals comparison stays raw."""
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            write(d, [start(ORIGIN, SHA), gaprec("weird", MID), gaprec("sentinelkind", MID + 1), gaprec("sentinelkind", MID + 2), gaprec("null", MID + 3),
                      gaprec(None, MID + 4), gaprec("silence", MID + 5)])
            raw = {"weird": 1, "sentinelkind": 2, "null": 2, "silence": 1}
            e = {"n_ok": 0, "incomplete_n": 0, "plain_pool_closes": 0, "triggers_pv": 0, "gap_records_by_kind": raw}
            code, out = self.cli(self.argv(expect=e, out="ok.json", **{"--shadow-dir": str(d)}))
            self.assertEqual(code, 0)
            r = json.loads(out)
            self.assertEqual(r["gap_table"]["totals"]["found"]["gap_records_by_kind"], {"other": 5, "silence": 1})
            self.assertEqual(r["gap_table"]["totals"]["expected"]["gap_records_by_kind"], {"other": 5, "silence": 1})
            self.assertEqual(r["inputs"]["expect"]["gap_records_by_kind"], {"other": 5, "silence": 1})
            self.assertEqual(r["gap_table"]["result"]["gap_records_by_kind"]["other"], 5)
            # a flag that differs only in an unknown kind's count is a mismatch (raw comparison), and still prints nothing of the names
            e2 = dict(e, gap_records_by_kind={"weird": 2, "sentinelkind": 1, "null": 2, "silence": 1})
            code2, out2 = self.cli(self.argv(expect=e2, out="bad.json", **{"--shadow-dir": str(d)}))
            self.assertEqual(code2, 9)
            r2 = json.loads(out2)
            self.assertFalse(r2["gap_table"]["totals_equal_item4"])
            self.assertFalse(r2["gap_table"]["totals_equal_by_total"]["gap_records_by_kind"])
            self.assertIsNone(r2["gap_table"]["result"])
            # a flag naming a kind the data does not hold, and the string `null`
            e3 = dict(e, gap_records_by_kind={**raw, "othersentinel": 1})
            code3, out3 = self.cli(self.argv(expect=e3, out="bad2.json", **{"--shadow-dir": str(d)}))
            self.assertEqual(code3, 9)
            for text in (out, out2, out3, (self.outdir / "ok.json").read_text(), (self.outdir / "bad.json").read_text()):
                for s in ("weird", "sentinelkind", "othersentinel", '"null"'):
                    self.assertNotIn(s, text)

    def test_label_prints_only_the_seven_kinds(self):
        for k in D.SEVEN_KINDS:
            self.assertEqual(D.label(k), k)
        for k in ("weird", "sentinelkind", "null", "other", "", "Silence", "slot_jump ", None, 5, ["x"], {"a": 1}, True):
            self.assertEqual(D.label(k), "other", k)
        self.assertEqual(D.label_counts({"weird": 2, "null": 1, "silence": 3, "x" * 40: 1}), {"other": 4, "silence": 3})

    def test_out_is_required(self):
        with self.assertRaises(SystemExit) as cm, contextlib.redirect_stderr(io.StringIO()):
            D.main(self.argv(**{"--out": None}), now_ms=NOW)
        self.assertEqual(cm.exception.code, 2)

    def test_off_schedule_exit_8_no_counts_no_file(self):
        for until in (T0 + 7 * H, T0 + 6 * H + 1, T0 + 12 * H, T0 + 30 * H, T0 + 24 * H + 1):
            code, out = self.cli(self.argv(**{"--until-ms": str(until)}), now_ms=until + 10 * H)
            self.assertEqual(code, 8, until)
            self.assertNotIn("item5_table", out)
            self.assertFalse((self.outdir / "out.json").exists())
        # scheduled instants pass the schedule check
        for h in (6, 24, 36, 48, 60, 72):
            self.assertTrue(I4.on_schedule(T0, T0 + h * H))
            code, out = self.cli(self.argv(**{"--until-ms": str(T0 + h * H), "--out": str(self.outdir / f"o{h}.json")}), now_ms=T0 + h * H + I4.SETTLE_MS)
            self.assertIn(code, (0, 9), h)

    def test_beyond_horizon_exit_8(self):
        until = T0 + (24 * 14 + 12) * H
        self.assertTrue(I4.on_schedule(T0, until))
        code, _ = self.cli(self.argv(**{"--until-ms": str(until)}), now_ms=until + H)
        self.assertEqual(code, 8)

    def test_too_early_exit_8(self):
        code, out = self.cli(self.argv(), now_ms=UNTIL + I4.SETTLE_MS - 1)
        self.assertEqual(code, 8)
        self.assertNotIn("item5_table", out)
        self.assertFalse((self.outdir / "out.json").exists())
        code, _ = self.cli(self.argv(), now_ms=UNTIL + I4.SETTLE_MS)
        self.assertEqual(code, 0)

    def test_code_sha_mismatch_exit_5(self):
        code, out = self.cli(self.argv(**{"--expect-code-sha": "a" * 40}))
        self.assertEqual(code, 5)
        self.assertNotIn("item5_table", out)
        self.assertFalse((self.outdir / "out.json").exists())

    def test_bad_code_sha_is_a_usage_error(self):
        for bad in ("abc", "A" * 40, "g" * 40):
            with self.assertRaises(SystemExit) as cm, contextlib.redirect_stderr(io.StringIO()):
                D.main(self.argv(**{"--expect-code-sha": bad}), now_ms=NOW)
            self.assertEqual(cm.exception.code, 2)

    def test_missing_directory_exit_3(self):
        code, out = self.cli(self.argv(**{"--shadow-dir": str(self.d / "nope")}))
        self.assertEqual(code, 3)
        self.assertNotIn("item5_table", out)
        self.assertFalse((self.outdir / "out.json").exists())  # the file this run created is removed again

    def test_no_start_record_exit_4(self):
        for p in self.d.iterdir():
            p.unlink()
        self.put(fx_a())
        code, out = self.cli(self.argv(expect={"n_ok": 0, "incomplete_n": 0, "plain_pool_closes": 0, "triggers_pv": 0, "gap_records_by_kind": {}}))
        self.assertEqual(code, 4)
        self.assertNotIn("item5_table", out)
        self.assertFalse((self.outdir / "out.json").exists())

    def test_totals_mismatch_exit_9_file_written_other_table_kept(self):
        e = self.expect_from_i4()
        e["n_ok"] += 1
        code, out = self.cli(self.argv(expect=e))
        self.assertEqual(code, 9)
        r = json.loads(out)
        self.assertEqual((self.outdir / "out.json").read_text(), out)
        self.assertIsNone(r["item5_table"]["result"])
        self.assertFalse(r["item5_table"]["totals_equal_item4"])
        self.assertIsNotNone(r["gap_table"]["result"])
        self.assertEqual(r["item5_table"]["totals"]["expected"]["n_ok"], e["n_ok"])
        self.assertEqual(r["item5_table"]["totals"]["found"]["n_ok"], e["n_ok"] - 1)
        e = self.expect_from_i4()
        e["gap_records_by_kind"]["silence"] += 1
        code, out = self.cli(self.argv(expect=e, out="o2.json"))
        self.assertEqual(code, 9)
        r = json.loads(out)
        self.assertIsNone(r["gap_table"]["result"])
        self.assertIsNotNone(r["item5_table"]["result"])
        e = self.expect_from_i4()
        e["incomplete_n"] += 1
        e["triggers_pv"] += 1
        code, out = self.cli(self.argv(expect=e, out="o3.json"))
        self.assertEqual(code, 9)
        r = json.loads(out)
        self.assertIsNone(r["gap_table"]["result"])
        self.assertIsNone(r["item5_table"]["result"])

    def test_every_expect_flag_is_required_and_checked(self):
        for flag in ("--expect-n-ok", "--expect-incomplete-n", "--expect-plain-pool-closes", "--expect-triggers-pv", "--expect-gap-records-by-kind", "--expect-code-sha",
                     "--shadow-dir", "--since-ms", "--until-ms"):
            with self.assertRaises(SystemExit) as cm, contextlib.redirect_stderr(io.StringIO()):
                D.main(self.argv(**{flag: None}), now_ms=NOW)
            self.assertEqual(cm.exception.code, 2, flag)
        for flag, bad in (("--expect-n-ok", "-1"), ("--expect-n-ok", "x"), ("--expect-triggers-pv", "1.5"), ("--expect-gap-records-by-kind", "[1]"),
                          ("--expect-gap-records-by-kind", '{"silence": -1}'), ("--expect-gap-records-by-kind", '{"silence": "1"}'),
                          ("--expect-gap-records-by-kind", '{"silence": true}'), ("--expect-gap-records-by-kind", "{bad")):
            with self.assertRaises(SystemExit) as cm, contextlib.redirect_stderr(io.StringIO()):
                D.main(self.argv(**{flag: bad}), now_ms=NOW)
            self.assertEqual(cm.exception.code, 2, (flag, bad))
        with self.assertRaises(SystemExit) as cm, contextlib.redirect_stderr(io.StringIO()):
            D.main(self.argv(**{"--until-ms": str(T0 - H)}), now_ms=NOW)
        self.assertEqual(cm.exception.code, 2)

    def test_empty_kinds_object_is_accepted(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            write(d, [start(ORIGIN, SHA)])
            a = self.argv(expect={"n_ok": 0, "incomplete_n": 0, "plain_pool_closes": 0, "triggers_pv": 0, "gap_records_by_kind": {}}, **{"--shadow-dir": str(d)})
            code, out = self.cli(a)
        self.assertEqual(code, 0)
        r = json.loads(out)
        self.assertEqual(r["gap_table"]["result"]["gap_records_by_kind"], {k: 0 for k in D.KIND_LABELS})
        self.assertEqual(r["item5_table"]["result"]["incomplete"]["n"], 0)

    def test_script_entry_point_runs_from_another_directory(self):
        script = Path(D.__file__)
        out = self.outdir / "script.json"
        with tempfile.TemporaryDirectory() as cwd:
            p = subprocess.run([sys.executable, str(script)] + self.argv(out="script.json"), cwd=cwd, capture_output=True, text=True, timeout=120)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(out.read_text(), p.stdout)
        self.assertEqual(json.loads(p.stdout)["tool"], "h5_tipauth_item5_diag")

    def test_shadow_dir_other_files_are_not_opened(self):
        (self.d / "ledger-other.jsonl").write_text('{"type":"pool","t_ms":%d,"pool":"Z","gap":false}\n' % MID)
        (self.d / "notes.txt").write_text("x")
        code, out = self.cli(self.argv())
        self.assertEqual(code, 0)
        self.assertNotIn("ledger-other", out)


if __name__ == "__main__":
    unittest.main()
