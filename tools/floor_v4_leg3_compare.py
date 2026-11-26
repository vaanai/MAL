"""Leg (iii) comparator for the l1-floor-v4 rule (names, md5s and counts only). NOT RUN by the PR that adds it: the manager runs it later as a MiScusi job, at the exact install tree (including 8288ba12).

The normalization below is FIXED here, before any run, and reviewed with this file (T1). No mapping may be chosen after a result is seen; a failed leg (iii) is never re-run under another normalization.

  B = osb_follow's CE1 `decisions.md5`, i.e. `osb_follow.decisions_md5_from_rows` over the CE1 book's engine ledger rows, whole span (read as a value only).
  A = the SAME function over the v4 host's engine ledger rows for the same span, after remapping every position_id to the v2 hashing:
        position_id("l1-floor-v2", pool, entry decision_ms, entry decision_slot)   (tools/arena_rules.py: sha256(bot|pool|ms|slot)[:16]; osb_follow runs bot l1-floor-v2)
      where pool / decision_ms / decision_slot come from the v4 decision log's `entry` row with that order_id.
  PASS iff A == B, same hours, same seed_from_hour. The md5 covers (position_id, entry decision_ms, exit decision_ms) only, so the TP reason label (osb_follow overrides L.TP but not
  L.REASON_TP, so its CE1 take-profit exits are labelled tp_1.5x while v4 at 1.3 says tp_1.3x) and the entry meta (rule id, q_star_lamports) are not compared and need no mapping.
  A ledger row whose position_id has no entry row in the decision log makes the comparison UNEQUAL (it is never dropped or guessed).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Iterable, Mapping

from tools.arena_rules import position_id

OSB_BOT = "l1-floor-v2"


def remap_ids(entry_rows: Iterable[Mapping[str, Any]], bot: str = OSB_BOT) -> dict[str, str]:
    """{order_id in the v4 log: position_id under `bot`'s hashing}, from the decision log's entry rows (row `ms` = the decision instant the engine hashes)."""
    return {r["order_id"]: position_id(bot, r["pool"], r["ms"], r["decision_slot"]) for r in entry_rows if r.get("kind") == "entry"}


def _osb_md5():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "research"))
    import osb_follow  # noqa: PLC0415
    return osb_follow.decisions_md5_from_rows


def normalized_md5(ledger_rows: Iterable[Mapping[str, Any]], entry_rows: Iterable[Mapping[str, Any]], md5_fn=None) -> str | None:
    """A: the osb_follow decisions md5 of the v4 ledger rows with position ids remapped to v2 hashing; None (never equal to B) when a row's id has no entry row."""
    ids = remap_ids(entry_rows)
    rows = []
    for r in ledger_rows:
        r = dict(r)
        pid = r.get("position_id")
        if isinstance(pid, str):
            if pid not in ids:
                return None
            r["position_id"] = ids[pid]
        rows.append(r)
    return (md5_fn or _osb_md5())(rows)


def leg3_equal(a: str | None, b: str | None) -> bool:
    return a is not None and a == b
