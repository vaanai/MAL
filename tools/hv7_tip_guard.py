#!/usr/bin/env python3
"""hv7_tip_guard: rule U2 for HV7 on the fast-0 tip source (QP-RULING-1101-arena-tip-reader section 2; DEC-031 Amendment 23 items 3, 7 and 8 "Age").

The rule. On the tip source `pool_create` rows exist only for migrations inside the derived copy's history, so most pools have `created_known` False. HV4's `_age`
(`tools/hv4_cards.py`, imported here and never edited) then returns `age_since_first_seen_s`, a LOWER BOUND, and a pool older than HV7's 7 d maximum passes the age
band until 7 d after its first print in the history: a short history WIDENS the universe. U2 closes that: HV7 admits a pool to the age band only when its creation is
known, that is when `_age(snap)[1]` is true (the pool's `pool_create` row is in the engine host's history and its `age_s` is a number). Any other pool is refused with
reason `age_unknown` BEFORE `filters` and the cards, and is counted. A pool whose migration row was lost in a gap is refused too (fail closed).

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
    for snap, prints in pairs:
        if admit(snap):
            yield snap, prints
        else:
            if refused is not None:
                refused[0] += 1
            if on_refuse is not None:
                on_refuse(snap)
