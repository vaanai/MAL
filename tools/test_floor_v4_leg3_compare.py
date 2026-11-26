"""Synthetic fixtures for the leg (iii) comparator: identical decisions under different bot ids and labels compare equal; a changed decision compares unequal. No real data."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "research"))

from tools import floor_v4_leg3_compare as C  # noqa: E402
from tools.arena_rules import position_id  # noqa: E402
import osb_follow as O  # noqa: E402

DEC = [("P1", 1000, 11, 5000), ("P2", 2000, 12, None), ("P3", 3000, 13, 9000)]  # pool, entry ms, slot, exit ms (None: still open)


def book(bot, label):
    """(ledger rows, decision-log entry rows) of one book deciding DEC under `bot`, exits labelled `label`."""
    led, ent = [], []
    for pool, ms, slot, ex in DEC:
        pid = position_id(bot, pool, ms, slot)
        ent.append({"kind": "entry", "order_id": pid, "pool": pool, "ms": ms, "decision_slot": slot})
        led.append({"kind": "open", "position_id": pid, "decision_ms": {"entry": ms}})
        if ex is not None:
            led.append({"kind": "trade", "position_id": pid, "decision_ms": {"entry": ms, "exit": ex}, "reason": label})
    return led, ent


def osb_side():
    led, _ = book("l1-floor-v2", "tp_1.5x")
    return O.decisions_md5_from_rows(led)


def test_remap_uses_the_v2_hashing():
    _, ent = book("l1-floor-v4", "tp_1.3x")
    assert set(C.remap_ids(ent).values()) == {position_id("l1-floor-v2", p, m, s) for p, m, s, _ in DEC}


def test_identical_decisions_under_other_bot_id_and_label_compare_equal():
    led, ent = book("l1-floor-v4", "tp_1.3x")
    raw = O.decisions_md5_from_rows(led)
    assert raw != osb_side()  # without the remap the ids differ: this is the failure the normalization fixes
    a = C.normalized_md5(led, ent)
    assert C.leg3_equal(a, osb_side())


def test_a_changed_exit_or_entry_compares_unequal():
    led, ent = book("l1-floor-v4", "tp_1.3x")
    moved = [dict(r) for r in led]
    for r in moved:
        if r["kind"] == "trade" and r["position_id"] == ent[0]["order_id"]:
            r["decision_ms"] = {"entry": 1000, "exit": 5001}
    assert not C.leg3_equal(C.normalized_md5(moved, ent), osb_side())
    assert not C.leg3_equal(C.normalized_md5(led[:-1], ent), osb_side())  # a missing close
    ent2 = [dict(e) for e in ent]
    ent2[1]["ms"] = 2001  # another entry instant hashes to another id
    assert not C.leg3_equal(C.normalized_md5(led, ent2), osb_side())


def test_a_ledger_row_without_an_entry_row_is_unequal_never_dropped():
    led, ent = book("l1-floor-v4", "x")
    assert C.normalized_md5(led, ent[:-1]) is None and not C.leg3_equal(None, None)
