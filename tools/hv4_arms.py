"""hv4_arms: the arm registry, persona specs (knob bounds, v0 defaults, code limits) and the budget constants of Haiku trader v4.

Serves /data/mal/hunt-1008/push-1016/hunt20/haiku-v4/DESIGN.md sections 3 (trader agent), 5 (personas and controls) and 6 (budget).
PAPER research only. Pure data and small helpers: no I/O, no clock, no network, no key.

Frozen for the window: the numbers here are the DESIGN's v0 numbers. A change after registration is a counted trial.
"""
from __future__ import annotations

from dataclasses import dataclass, field

PERSONAS = ("CON", "BIG", "BST")
KINDS = ("entry", "recheck", "risk", "review")

# DESIGN section 6: max_tokens per call kind. Thinking is disabled and no temperature is sent (the caller's default).
MAX_TOKENS = {"entry": 400, "risk": 400, "recheck": 120, "review": 4000}

# DESIGN section 6: hard caps in USD per ledger per UTC day.
# DESIGN says "$0.50 per LLM arm, $1.00 for risk, $0.10 for the reviewer: $4.70/day". 7 x 0.50 + 1.00 + 0.10 is 4.60, and with one
# reviewer ledger per learning arm (4) at 0.10 it is 4.90. This module reads it as 0.10 per reviewer ledger (a reviewer call that
# times out keeps its worst case charged, so a 0.025 split would starve it): 4.90/day at cap, 147/month, still under MONTH_GUARD_USD.
LLM_ARM_DAY_CAP_USD = 0.50
RISK_DAY_CAP_USD = 1.00
REVIEWER_DAY_CAP_USD = 0.10
MONTH_GUARD_USD = 160.0

# DESIGN section 6 drop order: starts at 60% of the group's day budget before 12:00Z, or 80% at any time.
DEGRADE_FRAC_BEFORE_NOON = 0.60
DEGRADE_FRAC_ANY_TIME = 0.80
DEGRADE_NOON_HOUR_UTC = 12
# The DESIGN does not say how fast the four steps follow each other. This module uses four equal bands between the trigger fraction
# and 100% of the group budget: step 1 at the trigger, step 4 (halt) at trigger + 3/4 of the remaining span.
DEGRADE_STEPS = 4

ENTRY_MAX_CARDS = 4
ENTRY_MAX_CARDS_DEGRADED = 2
STALE_MS = 10_000  # a reply more than 10 s after as_of_ms is discarded (DESIGN section 3)

SIZES = (0.25, 0.5, 1.0)
WHY_MAX_WORDS = 25
RISK_WHY_MAX_WORDS = 30
WATCH_MAX_TERMS = 4
WATCH_MAX_TTL_MIN = 60
WATCH_MAX_PER_ARM = 10

RISK_FLAGS = ("CREATOR_SELLING", "SNIPER_OVERHANG", "WASH_SUSPECT", "THIN_EXIT", "DRAIN_RISK", "FLOW_FADING", "COPYCAT",
              "UNKNOWN_COVERAGE", "OTHER")
RISK_GRADES = ("pass", "caution", "reject")
# A caution caps the stake at 0.25; a reject hides the pool from traders (stake 0).
CAUTION_MAX_SIZE = 0.25


@dataclass(frozen=True)
class PersonaSpec:
    persona: str
    actions: tuple[str, ...]                       # what the trader may say per card
    plan_bounds: dict = field(default_factory=dict)  # plan key -> (lo, hi), inclusive; an out-of-bounds value is a SKIP, never clipped
    plan_defaults: dict = field(default_factory=dict)  # the v0 default plan (X arms and skip pricing use it)
    min_conviction_bounds: tuple[int, int] = (3, 5)
    min_conviction_v0: int = 4
    fixed_size: float | None = None                # BST: the stake is fixed by code and not a model output
    recheck_minutes: int | None = None             # LLM re-check cadence (None: no re-check, code exit only)
    code_limits: dict = field(default_factory=dict)  # fixed, for reference: the runner enforces them

    @property
    def plan_keys(self) -> tuple[str, ...]:
        return tuple(self.plan_bounds)


PERSONA_SPECS = {
    "CON": PersonaSpec(
        "CON", ("BUY", "WATCH", "SKIP"),
        plan_bounds={"stop_pct": (-10.0, -4.0), "target_pct": (6.0, 30.0), "time_min": (10, 120)},
        plan_defaults={"stop_pct": -7.0, "target_pct": 12.0, "time_min": 45},
        min_conviction_bounds=(3, 5), min_conviction_v0=4, recheck_minutes=5,
        code_limits={"hard_stop_pct": -15.0, "max_hold_min": 120, "max_open": 4, "entries_per_h": 4, "day_loss_stop_sol": 1.0}),
    "BIG": PersonaSpec(
        "BIG", ("BUY", "WATCH", "SKIP"),
        plan_bounds={"stop_pct": (-30.0, -12.0), "target_pct": (40.0, 400.0), "trail_pct": (20.0, 40.0), "time_min": (30, 720)},
        plan_defaults={"stop_pct": -25.0, "target_pct": 100.0, "trail_pct": 30.0, "time_min": 360},
        min_conviction_bounds=(3, 5), min_conviction_v0=4, recheck_minutes=15,
        code_limits={"hard_stop_pct": -40.0, "max_hold_min": 720, "max_open": 6, "entries_per_h": 3, "day_loss_stop_sol": 1.5,
                     "trail_arms_after_pct": 50.0}),
    "BST": PersonaSpec(
        "BST", ("BUY", "SKIP"),
        min_conviction_bounds=(2, 5), min_conviction_v0=3, fixed_size=0.25, recheck_minutes=None,
        code_limits={"exit_after_s": 330, "max_open": 3, "day_loss_stop_sol": 0.75}),
}


@dataclass(frozen=True)
class ArmSpec:
    name: str
    persona: str
    role: str            # L learning, F frozen twin, P placebo, X rule book, R random
    llm: bool
    promotable: bool
    reviewer: bool       # has a nightly reviewer (one learning arm each)

    @property
    def group(self) -> str:
        """The persona group. L, F and P of a persona degrade and halt together."""
        return self.persona


def _arm(persona: str, role: str, promotable: bool) -> ArmSpec:
    return ArmSpec(f"{persona}-{role}", persona, role, role in ("L", "F", "P"), promotable, role in ("L",) or (persona, role) == ("CON", "P"))


# 13 arms, one stream (DESIGN section 5). Promotable: L, F, X. Not promotable: P, R.
ARMS = {a.name: a for a in (
    _arm("CON", "L", True), _arm("CON", "F", True), _arm("CON", "P", False), _arm("CON", "X", True), _arm("CON", "R", False),
    _arm("BIG", "L", True), _arm("BIG", "F", True), _arm("BIG", "X", True), _arm("BIG", "R", False),
    _arm("BST", "L", True), _arm("BST", "F", True), _arm("BST", "X", True), _arm("BST", "R", False),
)}

RISK_LEDGER = "RISK"  # the shared risk agent's ledger name


def llm_arms() -> tuple[str, ...]:
    return tuple(n for n, a in ARMS.items() if a.llm)


def reviewer_arms() -> tuple[str, ...]:
    return tuple(n for n, a in ARMS.items() if a.reviewer)


def group_members(group: str) -> tuple[str, ...]:
    """The LLM arms of a persona group: CON -> CON-L, CON-F, CON-P; BIG -> BIG-L, BIG-F; BST -> BST-L, BST-F."""
    if group not in PERSONAS:
        raise ValueError(f"unknown group {group!r}")
    return tuple(n for n, a in ARMS.items() if a.llm and a.persona == group)


def ledger_name(arm: str, kind: str) -> str:
    """Which per-day ledger a call is charged to: the arm for entry and re-check, RISK for risk, REV-<arm> for the reviewer."""
    if kind not in KINDS:
        raise ValueError(f"unknown call kind {kind!r}")
    if kind == "risk":
        return RISK_LEDGER
    if kind == "review":
        if arm not in reviewer_arms():
            raise ValueError(f"{arm!r} has no reviewer")
        return f"REV-{arm}"
    if arm not in ARMS or not ARMS[arm].llm:
        raise ValueError(f"{arm!r} is not an LLM arm")
    return arm


def day_cap_usd(ledger: str) -> float:
    if ledger == RISK_LEDGER:
        return RISK_DAY_CAP_USD
    if ledger.startswith("REV-"):
        return REVIEWER_DAY_CAP_USD
    if ledger in ARMS and ARMS[ledger].llm:
        return LLM_ARM_DAY_CAP_USD
    raise ValueError(f"unknown ledger {ledger!r}")
