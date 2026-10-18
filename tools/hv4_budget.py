"""hv4_budget: per-arm daily ledgers, the month guard, and the group degrade and halt of Haiku trader v4.

Serves /data/mal/hunt-1008/push-1016/hunt20/haiku-v4/DESIGN.md section 6 (budget, hard caps, drop order).
PAPER research only. Reads and appends JSONL files under one root; no network, no key. Time is always passed in (now_ms).

Layout under root (DEFAULT_ROOT in production is /data/mal/arena/haiku-v4):
  ledgers/<LEDGER>/<YYYY-MM-DD>.jsonl   one tools.haiku_caller ledger per ledger name per UTC day. LEDGER is an LLM arm (CON-L ...),
                                        RISK (the shared risk agent) or REV-<arm> (the nightly reviewer of a learning arm).
  state/<YYYY-MM-DD>/<GROUP>.jsonl      the group's degrade and halt marks for that day, append-only.
  cache/<LEDGER>/                       the replay cache (haiku_caller save_dir), one directory per ledger: arms never share it.

The hard stop is haiku_caller's: every call is made through a HaikuCaller whose budget_usd is the EFFECTIVE budget from preflight,
min(day cap, day spent + month headroom). So the day cap and the month guard are both enforced inside the caller's own
reserve-then-settle check. Between ledgers the month headroom is read without a global lock: two calls in flight at once could each
see the same headroom, which can overshoot by at most the sum of their worst-case reserves (a fraction of a cent each). The
owner's console limit is the backstop for anything beyond that.

Groups. A persona group is the LLM arms of one persona: CON = CON-L, CON-F, CON-P; BIG = BIG-L, BIG-F; BST = BST-L, BST-F.
The group's day budget is the sum of its members' caps and its spend is the sum of their ledgers. The drop order starts at 60% of
that budget before 12:00Z, or 80% at any time, and has four steps (see GroupPolicy). Steps hit every member together, and they only
ratchet up within a UTC day. A halt is symmetric: when ANY member cannot afford a call, or the caller refuses one on budget, the
whole group halts entries (code-only exits). The reviewer is never dropped by a group step; the RISK ledger is not in a group.
"""
from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from tools import hv4_arms as arms
from tools.haiku_caller import Ledger, LedgerError

DEFAULT_ROOT = Path("/data/mal/arena/haiku-v4")
# A call that cannot possibly fit is refused before the caller is built. The entry call's worst-case reserve is about 0.003 USD
# (see test_hv4_prompts); below this much headroom there is no use trying.
MIN_CALL_USD = 0.002
_EPS = 1e-9


def utc_day(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).strftime("%Y-%m-%d")


def utc_hour(ms: int) -> int:
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).hour


def month_of(day: str) -> str:
    return day[:7]


def degrade_trigger_frac(hour_utc: int) -> float:
    return arms.DEGRADE_FRAC_BEFORE_NOON if hour_utc < arms.DEGRADE_NOON_HOUR_UTC else arms.DEGRADE_FRAC_ANY_TIME


def degrade_level(frac: float, hour_utc: int) -> int:
    """0 below the trigger fraction; then 1 to 4 in four equal bands up to 100% of the group's day budget."""
    p = degrade_trigger_frac(hour_utc)
    if frac + _EPS < p:
        return 0
    band = (1.0 - p) / arms.DEGRADE_STEPS
    return min(arms.DEGRADE_STEPS, 1 + int(math.floor((frac - p) / band + _EPS)))


@dataclass(frozen=True)
class GroupPolicy:
    """What a persona group may do right now. Identical for every member (L, F and P), by construction."""
    group: str
    day: str
    level: int                 # 0 normal; 1 no board, <=2 cards; 2 + risk refresh on material change only; 3 + re-check x3; 4 halt
    spent_usd: float
    budget_usd: float
    halt_reason: str | None    # why the level is 4 or higher than the spend alone says

    @property
    def board_enabled(self) -> bool:
        return self.level < 1

    @property
    def max_cards_per_call(self) -> int:
        return arms.ENTRY_MAX_CARDS if self.level < 1 else arms.ENTRY_MAX_CARDS_DEGRADED

    @property
    def risk_material_change_only(self) -> bool:
        return self.level >= 2

    @property
    def recheck_cadence_multiplier(self) -> int:
        return 1 if self.level < 3 else 3

    @property
    def entries_halted(self) -> bool:
        return self.level >= arms.DEGRADE_STEPS

    @property
    def llm_rechecks_halted(self) -> bool:
        """At the halt step exits are code-only: no LLM re-check either."""
        return self.level >= arms.DEGRADE_STEPS


@dataclass(frozen=True)
class Preflight:
    allowed: bool
    reason: str | None         # None when allowed
    arm: str
    kind: str
    ledger: str
    day: str
    ledger_path: Path
    cache_dir: Path
    budget_usd: float          # the effective budget to hand HaikuCaller
    cap_usd: float
    month_limited: bool        # the month guard, not the day cap, is what limits budget_usd
    group: str | None


class BudgetGuard:
    def __init__(self, root, *, month_limit_usd: float = arms.MONTH_GUARD_USD, min_call_usd: float = MIN_CALL_USD):
        self.root = Path(root)
        self.month_limit_usd = float(month_limit_usd)
        self.min_call_usd = float(min_call_usd)

    # -- paths and spend

    def ledger_path(self, ledger: str, day: str) -> Path:
        arms.day_cap_usd(ledger)  # raises on an unknown ledger name
        return self.root / "ledgers" / ledger / f"{day}.jsonl"

    def cache_dir(self, ledger: str) -> Path:
        arms.day_cap_usd(ledger)
        return self.root / "cache" / ledger

    def spent(self, ledger: str, day: str) -> float:
        """Total cost_usd on the ledger for the day, reserves included. Raises LedgerError on a row it cannot trust."""
        led = Ledger(self.ledger_path(ledger, day))
        with led.locked(exclusive=False):
            return led.spent()

    def month_spent(self, month: str) -> float:
        base = self.root / "ledgers"
        total = []
        if base.is_dir():
            for d in sorted(base.iterdir()):
                if d.is_dir():
                    for f in sorted(d.glob(f"{month}-??.jsonl")):
                        led = Ledger(f)
                        with led.locked(exclusive=False):
                            total.append(led.spent())
        return math.fsum(total)

    def group_spent(self, group: str, day: str) -> float:
        return math.fsum(self.spent(m, day) for m in arms.group_members(group))

    @staticmethod
    def group_budget(group: str) -> float:
        return arms.LLM_ARM_DAY_CAP_USD * len(arms.group_members(group))

    # -- group state (the ratchet)

    def _state_path(self, group: str, day: str) -> Path:
        arms.group_members(group)
        return self.root / "state" / day / f"{group}.jsonl"

    def persisted_level(self, group: str, day: str) -> tuple[int, str | None]:
        """The highest level marked for the group on the day, and the reason for it. An unreadable file reads as halted."""
        path = self._state_path(group, day)
        try:
            raw = path.read_bytes()
        except FileNotFoundError:
            return 0, None
        except OSError:
            return arms.DEGRADE_STEPS, "state_unreadable"
        level, reason = 0, None
        for line in raw.split(b"\n"):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                lv = row["level"]
                if isinstance(lv, bool) or not isinstance(lv, int) or not 0 <= lv <= arms.DEGRADE_STEPS:
                    raise ValueError
            except (ValueError, KeyError, TypeError):
                return arms.DEGRADE_STEPS, "state_unreadable"
            if lv > level:
                level, reason = lv, row.get("reason")
        return level, reason

    def _mark(self, group: str, day: str, level: int, reason: str, spent: float, now_ms: int) -> None:
        path = self._state_path(group, day)
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps({"ts_ms": now_ms, "group": group, "level": level, "reason": reason, "spent_usd": round(spent, 8)},
                          separators=(",", ":")).encode("utf-8") + b"\n"
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(fd, line)
            os.fsync(fd)
        finally:
            os.close(fd)

    def policy(self, group: str, now_ms: int) -> GroupPolicy:
        """The group's policy at now_ms: the level from spend, ratcheted up by anything already marked today. Marks a rise."""
        day = utc_day(now_ms)
        members = {m: self.spent(m, day) for m in arms.group_members(group)}
        spent, budget = math.fsum(members.values()), self.group_budget(group)
        computed, why = degrade_level(spent / budget, utc_hour(now_ms)), f"spend_{spent / budget:.3f}_of_budget"
        for m, s in members.items():  # symmetric: a member that cannot afford another call halts all of them, whoever is asked
            if arms.LLM_ARM_DAY_CAP_USD - s < self.min_call_usd:
                computed, why = arms.DEGRADE_STEPS, f"cap:{m}"
                break
        level, reason = self.persisted_level(group, day)
        if computed > level:
            level, reason = computed, why
            self._mark(group, day, level, reason, spent, now_ms)
        return GroupPolicy(group, day, level, spent, budget, reason if level >= arms.DEGRADE_STEPS else None)

    def halt_group(self, group: str, now_ms: int, reason: str) -> None:
        """Halt entries for every member of the group until 00:00Z. Idempotent."""
        day = utc_day(now_ms)
        if self.persisted_level(group, day)[0] < arms.DEGRADE_STEPS:
            self._mark(group, day, arms.DEGRADE_STEPS, reason, self.group_spent(group, day), now_ms)

    def halt_all(self, now_ms: int, reason: str) -> None:
        for g in arms.PERSONAS:
            self.halt_group(g, now_ms, reason)

    # -- the gate in front of every call

    def preflight(self, arm: str, kind: str, now_ms: int) -> Preflight:
        ledger = arms.ledger_name(arm, kind)
        day = utc_day(now_ms)
        cap = arms.day_cap_usd(ledger)
        group = arms.ARMS[arm].group if ledger in arms.ARMS else None
        path, cache = self.ledger_path(ledger, day), self.cache_dir(ledger)

        def deny(reason: str, budget: float = 0.0, month_limited: bool = False) -> Preflight:
            return Preflight(False, reason, arm, kind, ledger, day, path, cache, budget, cap, month_limited, group)

        try:
            day_spent = self.spent(ledger, day)
            month_spent = self.month_spent(month_of(day))
            if group is not None:
                pol = self.policy(group, now_ms)
                if pol.entries_halted:
                    return deny("group_halted")
        except LedgerError:
            return deny("ledger_unreadable")  # fail closed; the runner decides what to do about a damaged ledger
        headroom = self.month_limit_usd - month_spent
        if headroom < self.min_call_usd:
            if group is not None:
                self.halt_all(now_ms, "month_guard")
            return deny("month_guard", month_limited=True)
        remaining = cap - day_spent
        if remaining < self.min_call_usd:
            if group is not None:
                self.halt_group(group, now_ms, f"cap:{arm}")  # symmetric: one member out of budget halts every member
            return deny("day_cap")
        budget = min(cap, day_spent + headroom)
        return Preflight(True, None, arm, kind, ledger, day, path, cache, budget, cap, budget < cap, group)

    def record_refusal(self, pf: Preflight, now_ms: int) -> None:
        """The caller refused a call on budget after preflight allowed it (its worst-case reserve did not fit)."""
        if pf.group is None:
            return
        if pf.month_limited:
            self.halt_all(now_ms, "month_guard")
        else:
            self.halt_group(pf.group, now_ms, f"cap:{pf.arm}")

    def day_report(self, day: str) -> dict:
        """Spend and caps per ledger and the group levels for the day, for the daily note. Reads only."""
        names = [*arms.llm_arms(), arms.RISK_LEDGER, *(f"REV-{a}" for a in arms.reviewer_arms())]
        rows = {n: {"spent_usd": round(self.spent(n, day), 8), "cap_usd": arms.day_cap_usd(n)} for n in names}
        groups = {g: {"marked_level": self.persisted_level(g, day)[0], "spent_usd": round(self.group_spent(g, day), 8),
                      "budget_usd": self.group_budget(g)} for g in arms.PERSONAS}
        return {"day": day, "ledgers": rows, "groups": groups, "month_spent_usd": round(self.month_spent(month_of(day)), 8),
                "month_limit_usd": self.month_limit_usd}
