#!/usr/bin/env python3
"""hv7_tip_guard: rule U2 for HV7 on the fast-0 tip source (QP-RULING-1101-arena-tip-reader section 2; DEC-031 Amendment 23 items 3, 7 and 8 "Age").

The rule. On the tip source `pool_create` rows exist only for migrations inside the derived copy's history, so most pools have `created_known` False. HV4's `_age`
(`tools/hv4_cards.py`, imported here and never edited) then returns `age_since_first_seen_s`, a LOWER BOUND, and a pool older than HV7's 7 d maximum passes the age
band until 7 d after its first print in the history: a short history WIDENS the universe. U2 closes that: HV7 admits a pool to the age band only when its creation is
known, that is when `_age(snap)[1]` is true (the pool's `pool_create` row is in the engine host's history and its `age_s` is a number). Any other pool is refused with
reason `age_unknown` BEFORE `filters` and the cards, and is counted. A pool whose migration row was lost in a gap is refused too (fail closed).

Eviction (reviewer finding B1 on PR #1170). `ArenaState.sweep` drops a pool idle more than 6 h (`EVICT_IDLE_MS`), and a later trade makes a NEW pool with `created_ms` None, so U2
alone would refuse, for good, a pool whose creation is in the history but whose first trade came more than 6 h after it. `ArenaState` is shared by every arena book and is not edited.
Instead the HV7 runner keeps a `CreationIndex` (below) fed by every `pool_create` row the state applies, and the snapshots the screen, the watch set and the cards read are patched
from it: a pool whose creation the state forgot but the index holds gets the creation the state would have kept (`created_ms`, `created_kind`, `created_known`, `age_s`). Nothing else
of a revived pool is restored (its windows, candles and reserves restart at the revival, as for every arena book). U2 then admits exactly the pools whose `pool_create` row is in the
history, however long the pool was idle.

This module is the one copy of the rule: `Hv7Runner.screen` and `Hv7Runner.watch_tick` call it, and the P0 tip variant (`tools/hv7_tip_p0_count.py`) runs the runner's own
screen, so it imports the same guard (no second copy). It reads one snapshot mapping and nothing else: no tape, no file, no price, no outcome.
History rule that goes with it (Amendment 23 item 7): the clock is at or after H0 + 7 d.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Mapping
from typing import Any

from tools import hv4_cards as C

REASON = "age_unknown"        # the screen-counter name and the refusal reason
COUNTER = "age_unknown"       # key added to the SCAN row's `counts`
RESTORED = "creation_restored"   # report-only: pools admitted because the creation index restored a creation the state's 6 h eviction forgot (QP condition (a))


def created_known(snap: Mapping[str, Any]) -> bool:
    """True exactly when HV4's own `_age` is on its known branch (the same function `filters`, the prescreen and `hv6_cards.age_guard` use)."""
    return bool(C._age(snap)[1])


def admit(snap: Mapping[str, Any]) -> bool:
    """U2: admit a pool to the age band only when its creation is known. Fail closed on anything that is not a mapping."""
    if not isinstance(snap, Mapping):
        return False
    return created_known(snap)


def guard(pairs: Iterable[tuple[Mapping[str, Any], list]], refused: list[int] | None = None,
          on_refuse: Callable[[Mapping[str, Any]], None] | None = None) -> Iterator[tuple[Mapping[str, Any], list]]:
    """Yield the (snapshot, prints) pairs U2 admits. `refused` (a one-item list) is incremented per refused pool; `on_refuse` sees each refused snapshot."""
    for pair in pairs:
        snap, prints = pair[0], pair[1]
        if admit(snap):
            yield snap, prints
        else:
            if refused is not None:
                refused[0] += 1
            if on_refuse is not None:
                on_refuse(snap)


class CreationIndex:
    """pool id -> t_recv_ms of its newest `pool_create` row, from every row the state applied (the same rows, the same clock and the same overwrite rule as `ArenaState._pool_create`).
    Rebuilt identically by any replay of the tape. About 100 bytes a migrated pool in the history, so it is never pruned: a pruned entry would turn a known-but-too-old pool into an
    `age_unknown` refusal instead of an `age_max` filter fail, and change the counters."""

    def __init__(self) -> None:
        self._created: dict[str, int] = {}

    def __len__(self) -> int:
        return len(self._created)

    def note(self, row: Mapping[str, Any]) -> None:
        if not isinstance(row, Mapping) or row.get("type") != "pool_create":
            return
        pool, t = row.get("pool"), row.get("t_recv_ms")
        if isinstance(pool, str) and pool and isinstance(t, int) and not isinstance(t, bool):
            self._created[pool] = t

    def created_ms(self, pool: str) -> int | None:
        return self._created.get(pool)

    def patch(self, snap: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
        """The snapshot with the creation the state forgot put back; the snapshot itself is returned unchanged when the state still knows the creation or the index does not."""
        if not isinstance(snap, Mapping) or snap.get("created_known") or snap.get("created_ms") is not None:
            return snap
        t = self._created.get(snap.get("id"))  # type: ignore[arg-type]
        now = snap.get("now_ms")
        if t is None or not isinstance(now, (int, float)) or isinstance(now, bool):
            return snap
        return {**snap, "created_ms": t, "created_kind": "pool_create", "created_known": True, "age_s": (now - t) / 1000.0}
