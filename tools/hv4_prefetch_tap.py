"""Harness-only taps on a replay rig's ChainPrefetcher (tools/hv4_memreplay.Rig): counts for the null-holders diagnosis of PR #768. Paper only. Reads the prefetcher's
state and times its refreshes; it changes no argument, no result and no clock, and nothing in run mode imports it. Used by `tools/hv4_scanprof.py run --tap PATH`.

What it records (counts only):
  * every card read (`part_at`) that returned a chain dict WITHOUT a holders block, with the cause: `never_had_a_fact` (the pool has no holders fact in the store),
    `older_than_limit` (it has one, but the newest one before T is older than the card's holders age limit), `fact_not_before_T` (it has facts, none stamped before T),
    `other`; each split by whether the pool was promoted to hot AT this scan (it was not hot before the scan's watch call, so no holders read had been planned for it);
  * every refresh the worker ran, by hour and class: mint reads of new pools, of hot pools, of cold pools, and holders reads, with the calls each used (counter delta);
  * the hot pool count and the store size at every scan (p50 and p90 are in the summary).
"""
from __future__ import annotations

import collections
import json
from pathlib import Path
from typing import Any

from tools import hv4_cards as C

HOUR_MS = 3_600_000


def _pct(vals: list[float], q: float) -> float | None:
    if not vals:
        return None
    s = sorted(vals)
    return s[max(0, int(-(-q * len(s) // 1)) - 1)]


class PrefetchTap:
    def __init__(self, rig: Any) -> None:
        self.pf = rig.prefetch
        self.serves = 0
        self.serves_with_holders = 0
        self.null: collections.Counter = collections.Counter()
        self.null_age_ms: list[int] = []
        self.hours: dict[int, collections.Counter] = collections.defaultdict(collections.Counter)
        self.scans: list[dict[str, int]] = []
        self.promoted: set[str] = set()

    def install(self) -> "PrefetchTap":
        pf = self.pf
        orig_watch, orig_part, orig_refresh = pf.watch, pf.part_at, pf._refresh

        def watch(items, hot, now_ms):
            items = list(items)
            before = {k: p.hot_until_ms for k, p in pf.pools.items()}
            orig_watch(items, hot, now_ms)
            self.promoted = {k for k in set(hot) if before.get(k, 0) <= now_ms}
            self.scans.append({"now_ms": now_ms, "filter_pass": len(items), "store": len(pf.pools), "hot": sum(1 for p in pf.pools.values() if now_ms < p.hot_until_ms)})

        def part_at(pool, mint, T, slot):
            res = orig_part(pool, mint, T, slot)
            if res is None:
                return res
            self.serves += 1
            if res.get("holders_as_of_ms") is not None:
                self.serves_with_holders += 1
                return res
            p = pf.pools.get(pool)
            facts = [h for h in (list(p.holders) if p else []) if h.as_of_ms is not None]
            before_T = [h.as_of_ms for h in facts if h.as_of_ms < T]
            if not facts:
                cause = "never_had_a_fact"
            elif not before_T:
                cause = "fact_not_before_T"
            elif T - max(before_T) > C.CHAIN_HOLDERS_MAX_AGE_MS:
                cause = "older_than_limit"
                self.null_age_ms.append(T - max(before_T))
            else:
                cause = "other"
            self.null[(cause, "promoted_at_this_scan" if pool in self.promoted else "was_hot_before")] += 1
            return res

        def refresh(p, now, need_mint, need_hold):
            cls = "new" if not p.mints else ("hot" if now < p.hot_until_ms else "cold")
            c0 = pf._calls_total()
            orig_refresh(p, now, need_mint, need_hold)
            c1 = pf._calls_total()
            used = max(0, (c1 or 0) - (c0 or 0))
            h = self.hours[pf.clock() // HOUR_MS]
            if need_mint:
                h["mint_reads_" + cls] += 1
                h["mint_calls_" + cls] += used if not need_hold else used  # a refresh item is one kind in v2; v1 can combine, so the split is a v2 reading
            if need_hold:
                h["holders_reads"] += 1
                h["holders_calls"] += used if not need_mint else 0

        pf.watch, pf.part_at, pf._refresh = watch, part_at, refresh  # type: ignore[method-assign]
        return self

    def summary(self) -> dict[str, Any]:
        hot = [s["hot"] for s in self.scans]
        store = [s["store"] for s in self.scans]
        fp = [s["filter_pass"] for s in self.scans]
        n_null = sum(self.null.values())
        by_cause: collections.Counter = collections.Counter()
        for (cause, _), n in self.null.items():
            by_cause[cause] += n
        return {"serves": self.serves, "serves_with_holders": self.serves_with_holders, "serves_null_holders": n_null, "null_by_cause": dict(by_cause),
                "null_by_cause_and_promotion": {f"{a}|{b}": n for (a, b), n in sorted(self.null.items())},
                "older_than_limit_age_s": {"p50": (_pct(self.null_age_ms, 0.5) or 0) / 1000, "p90": (_pct(self.null_age_ms, 0.9) or 0) / 1000},
                "scans": len(self.scans), "hot_pools_per_scan": {"p50": _pct(hot, 0.5), "p90": _pct(hot, 0.9), "max": max(hot, default=None)},
                "store_pools_per_scan": {"p50": _pct(store, 0.5), "p90": _pct(store, 0.9)}, "filter_pass_per_scan": {"p50": _pct(fp, 0.5), "p90": _pct(fp, 0.9)},
                "per_hour": {str(h): dict(c) for h, c in sorted(self.hours.items())}}

    def write(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.summary(), sort_keys=True))
