#!/usr/bin/env python3
"""arena_h5_signal: the H5 v1 DECISION half, on the arena tape, emitting TRIGGER FACTS ONLY (Haiku trader v4, BST persona; DESIGN section 5).

PAPER only. No key, no RPC, no order. It reads arena tape rows (`tools/arena_feed.py` shape) and runs `tools.h5_shadow.Engine`'s decision path in
memory. Every record the engine emits is looked at once and dropped unless its type is exactly "trigger" (variant "pv", the primary); the
trigger is cut down to a whitelist of decision-time fields (TRIGGER_FIELDS). An outcome, a ladder, a pool-close record, a strip, a minimum-Q
or any field named like one is never kept, never written and never returned. BST never reads stream A's out-dir.

Seals (DEC-031 section 3.5). The CAP-PICK seal is moot (EXP-022 withdrawn), so the engine is built with seal_start_ms=None; otherwise the stub
oracle would withhold the trigger records themselves. The H5 Look-2 outcome seal stays ON (h5_look2_observed=False): for a pool with s0 in
the Look-2 window the engine never computes a ladder or an outcome at all. A classifier is REQUIRED for a trigger to be emitted (EXP-024 Am.4:
no buy on a synthetic or unclassified pool); with `classifier=None` every trigger is excluded by the engine, so this module emits none (fail closed).

A second entry point, `read_trigger_records(path)`, serves a caller that wants an arena H5 book's records from a FILE: it refuses sealed
paths (`safe_path`), keeps only the lines whose type is "trigger" (a byte-level test before any JSON parse, so an outcome line is never parsed)
and whitelists the fields.
"""

from __future__ import annotations

if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import json
import re
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Mapping

SCHEMA = "arena_h5_trigger_v1"
H5_SECONDS_EXIT = 330  # the rule exits at s0 + 330 s only (DESIGN section 3, BST)

# Decision-time fields of a trigger record. Nothing here is an outcome: they are the state of the pool at the trigger print, the BOOST
# budget spent so far, the pool clock and the planned slots (a plan, not a fill).
TRIGGER_FIELDS = (
    "variant", "pool", "mint", "s0", "s0_t_recv_ms", "slot", "signature", "event_index", "t_since_s0_s", "sps", "block_time", "t_recv_ms",
    "q_trigger_sol", "q_pv_post_sol", "q_fv_post_sol", "q_pv_pre_sol", "q_fv_pre_sol", "pv_fv_disagree_at_trigger", "real_quote_pre", "v_print", "v0",
    "base_pre", "sell_token_raw", "sell_user_out", "boost_spent_sol", "boost_remaining_sol", "boost_id", "boost_src", "boost_vault_remaining_sol",
    "landing_slot_primary", "landing_slot_binding", "entry_slots", "exit_trigger_slot", "exit_landing_slot", "exit_lag_slots", "prints_seen",
    "chain_breaks", "base_breaks", "slot_regress", "announced", "announced_slot", "s0_minus_announced_slots", "gap", "v_missing", "synthetic",
    "h5_look2_sealed",
)
_OUTCOME_WORDS = ("outcome", "pnl", "ladder", "strip", "min_q", "fill", "leg", "exit_q", "result")
_TRIGGER_LINE = re.compile(rb'"type"\s*:\s*"trigger"')
_SEALED_PARTS = ("forward-", "truth-", "fresh-08", "exp011-", "tip-tape-archive", "meta-capture", "shadow", "canary", "probe")
_SEALED_ABS = ("/var/lib/mal/sealed", "/data/mal/blocks/forward-1016")


class SealedPath(Exception):
    """A path the arena must not open (DESIGN section 7, Isolation)."""


def safe_path(path: str | Path) -> Path:
    """The path as a Path, or SealedPath if any component names a sealed store."""
    p = Path(path)
    s = str(p)
    if any(s == a or s.startswith(a + "/") for a in _SEALED_ABS):
        raise SealedPath("sealed path")
    for part in p.parts:
        low = part.lower()
        if any(low.startswith(w) for w in _SEALED_PARTS):
            raise SealedPath("sealed path component")
    return p


def trigger_facts(rec: Mapping[str, Any]) -> dict[str, Any]:
    """The whitelisted fields of one trigger record, plus a schema stamp. Raises ValueError for a record that is not a plain pv trigger."""
    if rec.get("type") != "trigger":
        raise ValueError("not a trigger record")
    out = {k: rec[k] for k in TRIGGER_FIELDS if k in rec}
    bad = [k for k in out if any(w in k for w in _OUTCOME_WORDS)]
    if bad:  # defence in depth: a whitelist entry that reads like an outcome is a code bug
        raise ValueError("outcome-like field in the whitelist")
    out["schema"] = SCHEMA
    return out


class H5Signal:
    """Feeds arena tape rows to the H5 engine and collects trigger facts. `feed(row)` returns the new triggers (a list, usually empty)."""

    def __init__(self, classifier: Any = None, *, engine_factory: Callable[..., Any] | None = None, sps_fn: Callable[[Any], float | None] | None = None,
                 variant: str = "pv", **engine_kw: Any) -> None:
        from tools import h5_shadow as H

        self.variant = variant
        self.triggers: list[dict[str, Any]] = []
        self.dropped: dict[str, int] = {}
        self._new: list[dict[str, Any]] = []
        kw = dict(classifier=classifier, seal_start_ms=None, h5_look2_observed=False, require_announce=True, sps_fn=sps_fn)
        kw.update(engine_kw)
        if kw.get("h5_look2_observed"):
            raise ValueError("the signal never opens the Look-2 observation: outcomes stay uncomputed")
        self.engine = (engine_factory or H.Engine)(self._emit, **kw)

    def _emit(self, rec: dict) -> None:
        typ = str(rec.get("type"))
        if typ != "trigger" or rec.get("variant") != self.variant:
            self.dropped[typ] = self.dropped.get(typ, 0) + 1  # counted by type only; the record is not kept
            return
        facts = trigger_facts(rec)
        self.triggers.append(facts)
        self._new.append(facts)

    def feed(self, row: Mapping[str, Any]) -> list[dict[str, Any]]:
        """One arena tape row. Only PumpSwap rows matter: pool_create announces a pool, a trade is a print, a data-loss gap restarts the feed view."""
        self._new = []
        typ, prog = row.get("type"), row.get("prog")
        if typ == "pool_create" and prog == "pumpswap":
            self.engine.on_create_pool(row["pool"], row.get("mint"), row.get("quote_mint"), int(row["slot"]), int(row["t_recv_ms"]))
        elif typ == "trade" and prog == "pumpswap":
            r = dict(row)
            r.setdefault("venue", "pumpswap")
            self.engine.on_trade(r)
        elif typ == "gap" and row.get("data_loss") and prog in ("pumpswap", "all"):
            self.engine.feed_restart(int(row["t_recv_ms"]), "arena_gap")
        return list(self._new)


def read_trigger_records(path: str | Path) -> Iterator[dict[str, Any]]:
    """Trigger facts from a JSONL file of an arena H5 book. Lines of any other type are skipped before parsing. Sealed paths are refused."""
    p = safe_path(path)
    with p.open("rb") as fh:
        for line in fh:
            if not _TRIGGER_LINE.search(line):
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if rec.get("type") == "trigger" and rec.get("variant", "pv") == "pv":
                yield trigger_facts(rec)


def bst_card(facts: Mapping[str, Any], *, as_of_ms: int, as_of_slot: int, launch: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """The BST card for one trigger: BOOST spent so far, slices (when known), Q, and launch rug facts. Every number is from the trigger record or
    the caller's launch facts, all at or before the trigger print. Strings are the pool id only."""
    def num(k: str) -> float | None:
        v = facts.get(k)
        return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and v == v else None

    t_ms = facts.get("t_recv_ms")
    if isinstance(t_ms, int) and t_ms >= as_of_ms:
        raise ValueError("trigger is not before the card instant")
    f = {"boost_spent_sol": num("boost_spent_sol"), "boost_remaining_sol": num("boost_remaining_sol"), "q_trigger_sol": num("q_trigger_sol"),
         "q_pre_sol": num("q_pv_pre_sol"), "real_quote_pre_sol": (num("real_quote_pre") or 0) / 1e9 if num("real_quote_pre") is not None else None,
         "t_since_s0_s": num("t_since_s0_s"), "sell_sol": (num("sell_user_out") or 0) / 1e9 if num("sell_user_out") is not None else None,
         "prints_seen": num("prints_seen")}
    risk = {k: (None if v is None else (float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None)) for k, v in dict(launch or {}).items()}
    return {"v": "hv4-bst-1", "id": facts["pool"], "lane": "H", "as_of_slot": int(as_of_slot), "as_of_ms": int(as_of_ms), "setup": None,
            "f": f, "risk": risk, "x": None}
