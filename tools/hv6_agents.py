"""hv6_agents: the agent layer of HV6, a general Haiku 5.5 trader with a regime channel (build item 2 of docs/research/hv6/DESIGN.md).

PAPER research only. No network, no key, no clock: time is always passed in (now_ms). Nothing here calls the API; the real caller path
is tools.hv4_model.CallerModel over tools.haiku_caller.HaikuCaller (key read from the environment at call time by the caller, never here).
Tests use StubModel only.

What this module is
  persona  one persona, "GEN" (HV4's CON and BIG merged, wider knobs; DESIGN section 1-2). Playbook v0 is tools/hv6_playbooks/GEN_v0.txt,
           written from #814's wording (tools/hv5_playbooks) and the merged bounds. HV4 frozen files are imported, never edited.
  arms     L, L-nr, F, P call the model; X is a rule book with NO model call (its ledger name does not exist; asking for one raises).
  requests built with tools.hv5_prompts (one cache_control marker on the playbook block, nonce sha256(arm|kind|card_sha|seq)), model id
           claude-haiku-5-5, thinking off, no temperature. Cards must be at least 2 hours old (hard check, fail closed).
  replies  tools.hv5_validate wording (#814): SKIP only when the model said SKIP; PARSE_ERROR / NO_OUTPUT / STALE / BELOW_MIN are kept
           apart. A refusal, a truncation or any parse failure is an abstain (never an entry).
  budget   per-ledger USD day caps, a global 3.00 USD/day cap, a month guard, and a group degrade/halt (HV4 section 6, adapted to
           the one HV6 group of four LLM arms). Constants below are code, not config.
  regime   `validate_regime_rule`: the `regime_rule` change type (DESIGN section 3, "Validator for regime_rule").

Not here: the runner, the regime block builder (the block is read here as a plain mapping, see validate_regime_rule), the reviewer
bundle and reply parser, the 10-match rule on playbook/knob changes, the release code. Those are build items 3-5.
"""
from __future__ import annotations

import json
import math
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

from tools import hv4_budget as b4
from tools import hv4_model as m4
from tools import hv4_prompts as p4
from tools import hv5_prompts as p5
from tools import hv5_validate as v5
from tools import hv4_validate as v4
from tools.haiku_caller import DEFAULT_MODEL, Ledger, LedgerError
from tools.hv4_arms import (DEGRADE_STEPS, ENTRY_MAX_CARDS, ENTRY_MAX_CARDS_DEGRADED, MAX_TOKENS, SIZES, PersonaSpec)  # noqa: F401
from tools.hv4_model import ModelReply, refusal_reply  # noqa: F401
from tools.hv4_prompts import PromptError, Request  # noqa: F401
from tools.hv4_validate import Verdict  # noqa: F401
from tools.hv4_review import MAX_VETOES, WHY_OK, ReviewError, parse_predicate

MODEL_ID = "claude-haiku-5-5"
assert DEFAULT_MODEL == MODEL_ID, "haiku_caller's default model must be the HV6 model"

DEFAULT_ROOT = Path("/data/mal/arena/haiku-v6")
PERSONA = "GEN"
GROUP = "GEN"
PACKAGE_DIR = Path(__file__).resolve().parent
PLAYBOOK_DIR = PACKAGE_DIR / "hv6_playbooks"
# Seed playbook per release (docs/research/hv6/V61-PROMPT.md). v6.0 = tools/hv6_playbooks (untouched). v6.1 = tools/hv6_playbooks_v61: the v6.0 seed with
# one MEASURED DIRECTIONS clause replaced (QP-RULING-1026-entry-rate (c)); bot ids hv6.1-<arm> from hv6_review.bot_id. Closed map: any other release is refused.
SEED_DIRS = {"v6.0": PLAYBOOK_DIR, "v6.1": PACKAGE_DIR / "hv6_playbooks_v61"}
MIN_AGE_H = 2.0                      # DESIGN section 8 item 5: a hard code check refuses any card under 2 h

# ---------------------------------------------------------------------------------------------------------------- persona spec
# DESIGN section 2 table: knob bounds, code limits, min_conviction 3. Bounds: stop -4..-30, target +6..+150, time 10..360 min, trail 20..40
# (arms after +50%). READING (not stated by the DESIGN, for QP/registration to confirm): the v0 default plan, which X and skip pricing
# use, is HV4 BIG's default (-25 / +100 / trail 30 / 360 min): the only default inside the merged bounds that arms the trail. The
# re-check cadence is BIG's 15 min.
GEN_SPEC = PersonaSpec(
    "GEN", ("BUY", "WATCH", "SKIP"),
    plan_bounds={"stop_pct": (-30.0, -4.0), "target_pct": (6.0, 150.0), "trail_pct": (20.0, 40.0), "time_min": (10, 360)},
    plan_defaults={"stop_pct": -25.0, "target_pct": 100.0, "trail_pct": 30.0, "time_min": 360},
    min_conviction_bounds=(3, 5), min_conviction_v0=3, recheck_minutes=15,
    code_limits={"hard_stop_pct": -40.0, "max_hold_min": 720, "max_open": 6, "entries_per_h": 3, "day_loss_stop_sol": 1.5,
                 "trail_arms_after_pct": 50.0})


# DESIGN section 2 (G5 table), row "X cap, X exit": HV6 v0 = 2/h, -8 / +20 / 60 min. Frozen before P0. No trail: the trail arms only after
# +50%, so under a +20 target it is inert; pinned here as None so the runner cannot invent one. X takes ITS exit from this constant.
X_EXIT = {"stop_pct": -8.0, "target_pct": 20.0, "trail_pct": None, "time_min": 60}
X_ENTRIES_PER_H = 2
# READING: GEN's own plan_defaults (BIG-style -25 / +100 / trail 30 / 360) are used for SKIP PRICING ONLY (what a skipped card would have
# done). They are not X's exit and not a plan the model inherits (a model BUY writes all four plan keys).
SKIP_PRICING_PLAN = dict(GEN_SPEC.plan_defaults)
# max_tokens per call kind: HV4's table has no `regime` key. 1,200 is the DESIGN section 6 assumed output size of a regime review.
MAX_TOKENS = {**MAX_TOKENS, "regime": 1200}


def knob_names() -> tuple[str, ...]:
    return ("min_conviction", *GEN_SPEC.plan_keys)


def x_exit() -> dict:
    return dict(X_EXIT)


def v0_knobs() -> dict:
    return {"min_conviction": GEN_SPEC.min_conviction_v0, **GEN_SPEC.plan_defaults}


@dataclass(frozen=True)
class Knobs:
    """The arm's knobs the validator needs (v4.Knobs looks the persona up in HV4's table, which has no GEN)."""
    min_conviction: int
    persona: str = PERSONA

    def __post_init__(self):
        lo, hi = GEN_SPEC.min_conviction_bounds
        if self.persona != PERSONA or isinstance(self.min_conviction, bool) or not isinstance(self.min_conviction, int) \
                or not lo <= self.min_conviction <= hi:
            raise ValueError(f"GEN min_conviction must be an integer in [{lo}, {hi}]")

    @classmethod
    def v0(cls) -> "Knobs":
        return cls(GEN_SPEC.min_conviction_v0)


# ---------------------------------------------------------------------------------------------------------------- arms
@dataclass(frozen=True)
class ArmSpec:
    name: str
    role: str
    llm: bool
    promotable: bool
    reviewer: bool        # nightly reviewer (DESIGN section 4)
    regime_review: bool   # intraday regime review (only arms that see the real block)


ARMS = {a.name: a for a in (
    ArmSpec("L", "learning + regime", True, True, True, True),
    ArmSpec("L-nr", "learning, no regime block", True, True, True, False),
    ArmSpec("F", "frozen playbook v0", True, True, False, False),
    ArmSpec("P", "placebo (shuffled outcomes)", True, False, True, True),
    ArmSpec("X", "rule book, no model", False, True, False, False),
)}
KINDS = ("entry", "recheck", "review", "regime")
RECHECK_MINUTES = GEN_SPEC.recheck_minutes


def llm_arms() -> tuple[str, ...]:
    return tuple(n for n, a in ARMS.items() if a.llm)


def reviewer_arms() -> tuple[str, ...]:
    return tuple(n for n, a in ARMS.items() if a.reviewer)


def regime_arms() -> tuple[str, ...]:
    return tuple(n for n, a in ARMS.items() if a.regime_review)


def ledger_name(arm: str, kind: str) -> str:
    """entry/recheck -> the arm; review -> REV-<arm>; regime -> RGM-<arm>. X has no ledger: it makes no model call."""
    if kind not in KINDS:
        raise ValueError(f"unknown call kind {kind!r}")
    if arm not in ARMS:
        raise ValueError(f"unknown arm {arm!r}")
    if not ARMS[arm].llm:
        raise ValueError(f"{arm!r} makes no model calls")
    if kind == "review":
        if not ARMS[arm].reviewer:
            raise ValueError(f"{arm!r} has no reviewer")
        return f"REV-{arm}"
    if kind == "regime":
        if not ARMS[arm].regime_review:
            raise ValueError(f"{arm!r} has no regime review")
        return f"RGM-{arm}"
    return arm


def all_ledgers() -> tuple[str, ...]:
    return (*llm_arms(), *(f"REV-{a}" for a in reviewer_arms()), *(f"RGM-{a}" for a in regime_arms()))


# ---------------------------------------------------------------------------------------------------------------- budget constants
# DESIGN section 6: "Hard caps in HV6's own ledger: 0.50 per LLM arm, 0.15 reviewers, 0.05 regime review (2.20) and a global 3.00 USD/day."
# READING: 0.15 and 0.05 are the TOTALS over the reviewer ledgers (3 x 0.05) and the regime-review ledgers (2 x 0.025), because
# 4 x 0.50 + 0.15 + 0.05 = 2.20 is the stated sum. A worst-case reserve of a reviewer call is about 0.0145 USD and of a regime review
# under 0.006, so each ledger fits its 1-2 calls a day with room.
LLM_ARM_DAY_CAP_USD = 0.50
REVIEWER_DAY_CAP_USD = 0.05
REGIME_REVIEW_DAY_CAP_USD = 0.025
GLOBAL_DAY_CAP_USD = 3.00
# READING: the DESIGN names a "month guard" without a value. HV6's own month guard is 40.0 USD: 12 days x 3.00 = 36 plus margin. The
# 200 USD pot shared with HV4 is summed by the manager; this module does not read HV4's ledger.
MONTH_GUARD_USD = 40.0
MIN_CALL_USD = b4.MIN_CALL_USD
# DESIGN section 8.2 deny tokens. A guard root containing one is refused: a mis-passed HV4 root would read HV4's sealed per-arm ledgers.
DENY_ROOT_TOKENS = ("hv4", "haiku-v4", "q50", "bots.json", "shadow", "canary", "forward-", "truth-", "fresh-08", "exp011-",
                    "tip-tape-archive", "/var/lib/mal/sealed", "meta-capture", "blocks/forward-1016")


def day_cap_usd(ledger: str) -> float:
    if ledger.startswith("REV-") and ledger[4:] in reviewer_arms():
        return REVIEWER_DAY_CAP_USD
    if ledger.startswith("RGM-") and ledger[4:] in regime_arms():
        return REGIME_REVIEW_DAY_CAP_USD
    if ledger in llm_arms():
        return LLM_ARM_DAY_CAP_USD
    raise ValueError(f"unknown ledger {ledger!r}")


def caps_total_usd() -> float:
    return math.fsum(day_cap_usd(n) for n in all_ledgers())


assert caps_total_usd() <= GLOBAL_DAY_CAP_USD + 1e-9, "per-ledger caps must fit inside the global day cap"
assert GLOBAL_DAY_CAP_USD <= 3.00 + 1e-9


# ---------------------------------------------------------------------------------------------------------------- group policy
@dataclass(frozen=True)
class GroupPolicy:
    """What the LLM arms may do right now. Identical for L, L-nr, F and P by construction; X halts entries in step (T8).
    Levels as HV4: 1 no board, <= 2 cards; 2 (HV4: risk refresh on change only; HV6 has no risk call, so a no-op); 3 re-check cadence x3; 4 halt."""
    day: str
    level: int
    spent_usd: float
    budget_usd: float
    halt_reason: str | None

    @property
    def board_enabled(self) -> bool:
        return self.level < 1

    @property
    def max_cards_per_call(self) -> int:
        return ENTRY_MAX_CARDS if self.level < 1 else ENTRY_MAX_CARDS_DEGRADED

    @property
    def recheck_cadence_multiplier(self) -> int:
        return 1 if self.level < 3 else 3

    @property
    def entries_halted(self) -> bool:
        return self.level >= DEGRADE_STEPS

    @property
    def llm_rechecks_halted(self) -> bool:
        return self.level >= DEGRADE_STEPS

    @property
    def x_entries_halted(self) -> bool:
        """X has no model call and no spend, but halts entries with the group so L - X and F - X never run on a day only X traded (T8)."""
        return self.entries_halted


@dataclass(frozen=True)
class Preflight:
    allowed: bool
    reason: str | None
    arm: str
    kind: str
    ledger: str
    day: str
    ledger_path: Path
    cache_dir: Path
    budget_usd: float          # the effective budget to hand HaikuCaller
    cap_usd: float
    month_limited: bool
    global_limited: bool
    group: str | None


class BudgetGuard:
    """Per-ledger day caps + global day cap + month guard + group halt. Same duck-typed surface hv4_model's gate and models use:
    preflight(arm, kind, now_ms) and record_refusal(pf, now_ms).

    Layout under root (production DEFAULT_ROOT): ledgers/<LEDGER>/<day>.jsonl, state/<day>/GEN.jsonl, cache/<LEDGER>/. The effective
    budget handed to the caller is min(ledger cap, day spent + month headroom, day spent + global headroom), so the caller's own
    reserve-then-settle check enforces all three. Across ledgers there is no global lock: two calls in flight at once may each see
    the same headroom and overshoot by at most the sum of their worst-case reserves (a fraction of a cent each)."""

    def __init__(self, root, *, global_cap_usd: float = GLOBAL_DAY_CAP_USD, month_limit_usd: float = MONTH_GUARD_USD,
                 min_call_usd: float = MIN_CALL_USD):
        if global_cap_usd > GLOBAL_DAY_CAP_USD + 1e-12:
            raise ValueError("global_cap_usd may only be lowered")
        low = str(root).lower()
        bad = next((tok for tok in DENY_ROOT_TOKENS if tok in low), None)
        if bad is not None:
            raise ValueError(f"guard root contains the deny token {bad!r}")
        self.root = Path(root)
        self.global_cap_usd = float(global_cap_usd)
        self.month_limit_usd = float(month_limit_usd)
        self.min_call_usd = float(min_call_usd)

    # paths and spend
    def ledger_path(self, ledger: str, day: str) -> Path:
        day_cap_usd(ledger)
        return self.root / "ledgers" / ledger / f"{day}.jsonl"

    def cache_dir(self, ledger: str) -> Path:
        day_cap_usd(ledger)
        return self.root / "cache" / ledger

    def spent(self, ledger: str, day: str) -> float:
        led = Ledger(self.ledger_path(ledger, day))
        with led.locked(exclusive=False):
            return led.spent()

    def day_spent(self, day: str) -> float:
        return math.fsum(self.spent(n, day) for n in all_ledgers())

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

    def group_spent(self, day: str) -> float:
        return math.fsum(self.spent(m, day) for m in llm_arms())

    @staticmethod
    def group_budget() -> float:
        return LLM_ARM_DAY_CAP_USD * len(llm_arms())

    # group state (the ratchet)
    def _state_path(self, day: str) -> Path:
        return self.root / "state" / day / f"{GROUP}.jsonl"

    def persisted_level(self, day: str) -> tuple[int, str | None]:
        """Highest level marked for the day and its reason. An unreadable or malformed file reads as halted."""
        try:
            raw = self._state_path(day).read_bytes()
        except FileNotFoundError:
            return 0, None
        except OSError:
            return DEGRADE_STEPS, "state_unreadable"
        level, reason = 0, None
        for line in raw.split(b"\n"):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                lv = row["level"]
                if isinstance(lv, bool) or not isinstance(lv, int) or not 0 <= lv <= DEGRADE_STEPS:
                    raise ValueError
            except (ValueError, KeyError, TypeError):
                return DEGRADE_STEPS, "state_unreadable"
            if lv > level:
                level, reason = lv, row.get("reason")
        return level, reason

    def _mark(self, day: str, level: int, reason: str, spent: float, now_ms: int) -> None:
        path = self._state_path(day)
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps({"ts_ms": now_ms, "group": GROUP, "level": level, "reason": reason, "spent_usd": round(spent, 8)},
                          separators=(",", ":")).encode("utf-8") + b"\n"
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(fd, line)
            os.fsync(fd)
        finally:
            os.close(fd)

    def policy(self, now_ms: int) -> GroupPolicy:
        """The group's policy at now_ms: level from spend, ratcheted up by anything already marked today. Marks a rise."""
        day = b4.utc_day(now_ms)
        members = {m: self.spent(m, day) for m in llm_arms()}
        spent, budget = math.fsum(members.values()), self.group_budget()
        computed, why = b4.degrade_level(spent / budget, b4.utc_hour(now_ms)), f"spend_{spent / budget:.3f}_of_budget"
        for m, s in members.items():       # symmetric: a member that cannot afford another call halts all of them
            if LLM_ARM_DAY_CAP_USD - s < self.min_call_usd:
                computed, why = DEGRADE_STEPS, f"cap:{m}"
                break
        level, reason = self.persisted_level(day)
        if computed > level:
            level, reason = computed, why
            self._mark(day, level, reason, spent, now_ms)
        return GroupPolicy(day, level, spent, budget, reason if level >= DEGRADE_STEPS else None)

    def halt_group(self, now_ms: int, reason: str) -> None:
        """Halt entries for every LLM arm (and X, in step) until 00:00Z. Idempotent."""
        day = b4.utc_day(now_ms)
        if self.persisted_level(day)[0] < DEGRADE_STEPS:
            try:
                spent = self.group_spent(day)
            except (LedgerError, OSError):
                spent = -1.0                    # unknown: the mark must still be written
            self._mark(day, DEGRADE_STEPS, reason, spent, now_ms)

    def x_entries_halted(self, now_ms: int) -> bool:
        """Fail closed, never raises. X halts with the group (T8), and also whenever any ledger this guard reads (the four arms, the
        reviewers, the regime reviews, the month's earlier days) cannot be read: that marks the group halted with `ledger_unreadable`."""
        try:
            day = b4.utc_day(now_ms)
            halted = self.policy(now_ms).x_entries_halted
            self.day_spent(day)
            self.month_spent(b4.month_of(day))
            return halted
        except Exception:                       # noqa: BLE001 - LedgerError, OSError, anything: X must not trade on a day we cannot account
            try:
                self.halt_group(now_ms, "ledger_unreadable")
            except Exception:                   # noqa: BLE001
                pass
            return True

    # the gate in front of every call
    def preflight(self, arm: str, kind: str, now_ms: int) -> Preflight:
        ledger = ledger_name(arm, kind)           # raises for X, an unknown arm/kind, or a reviewer the arm does not have
        day = b4.utc_day(now_ms)
        cap = day_cap_usd(ledger)
        group = GROUP if kind in ("entry", "recheck") else None
        path, cache = self.ledger_path(ledger, day), self.cache_dir(ledger)

        def deny(reason: str, month_limited: bool = False, global_limited: bool = False) -> Preflight:
            return Preflight(False, reason, arm, kind, ledger, day, path, cache, 0.0, cap, month_limited, global_limited, group)

        try:
            ledger_spent = self.spent(ledger, day)
            total_spent = self.day_spent(day)
            month_spent = self.month_spent(b4.month_of(day))
            if group is not None and self.policy(now_ms).entries_halted:
                return deny("group_halted")
        except (LedgerError, OSError):
            if group is not None:
                self.halt_group(now_ms, "ledger_unreadable")      # the four LLM arms and X stop together (T8)
            return deny("ledger_unreadable")      # fail closed
        month_room = self.month_limit_usd - month_spent
        if month_room < self.min_call_usd:
            if group is not None:
                self.halt_group(now_ms, "month_guard")
            return deny("month_guard", month_limited=True)
        global_room = self.global_cap_usd - total_spent
        if global_room < self.min_call_usd:
            if group is not None:
                self.halt_group(now_ms, "global_day_cap")
            return deny("global_day_cap", global_limited=True)
        if cap - ledger_spent < self.min_call_usd:
            if group is not None:
                self.halt_group(now_ms, f"cap:{arm}")
            return deny("day_cap")
        budget = min(cap, ledger_spent + month_room, ledger_spent + global_room)
        return Preflight(True, None, arm, kind, ledger, day, path, cache, budget, cap,
                         ledger_spent + month_room < cap and month_room <= global_room,
                         ledger_spent + global_room < cap and global_room < month_room, group)

    def record_refusal(self, pf: Preflight, now_ms: int) -> None:
        """The caller refused a call on budget after preflight allowed it (its worst-case reserve did not fit)."""
        if pf.group is None:
            return
        reason = "month_guard" if pf.month_limited else "global_day_cap" if pf.global_limited else f"cap:{pf.arm}"
        self.halt_group(now_ms, reason)

    def day_report(self, day: str, *, include_per_arm: bool = False) -> dict:
        """Default: the summed spend and cap-state flags only, the shape the manager may see. Per-ledger spend is sealed until each
        read (DESIGN section 8 item 4); it is returned only with include_per_arm=True, a read-time flag no manager-facing tool passes."""
        total = round(self.day_spent(day), 8)
        level, reason = self.persisted_level(day)
        out = {"day": day, "summary": {"day_spent_usd": total, "global_cap_usd": self.global_cap_usd,
                                       "global_cap_hit": total + self.min_call_usd > self.global_cap_usd,
                                       "group_halted": level >= DEGRADE_STEPS, "group_level": level, "halt_reason": reason},
               "month_spent_usd": round(self.month_spent(b4.month_of(day)), 8), "month_limit_usd": self.month_limit_usd}
        if include_per_arm:
            out["ledgers"] = {n: {"spent_usd": round(self.spent(n, day), 8), "cap_usd": day_cap_usd(n)} for n in all_ledgers()}
            out["group"] = {"spent_usd": round(self.group_spent(day), 8), "budget_usd": self.group_budget()}
        return out


# ---------------------------------------------------------------------------------------------------------------- models
class StubModel(m4.StubModel):
    """A scripted model for tests: no network, no key. With a hv6 BudgetGuard it takes the same preflight as the real path."""

    def __init__(self, script=None, *, guard: BudgetGuard | None = None, cost_usd: float = 0.0, latency_ms: int = 50):
        super().__init__(script, guard=guard, cost_usd=cost_usd, latency_ms=latency_ms)
        self.model = MODEL_ID


class CallerModel(m4.CallerModel):
    """The one real path (tools.haiku_caller.HaikuCaller, budget-stopped, replay cache). Tests inject caller_cls; none calls the API."""

    def __init__(self, guard: BudgetGuard, **kw):
        super().__init__(guard, **kw)
        self.model = MODEL_ID


# ---------------------------------------------------------------------------------------------------------------- playbook and requests
def load_playbook(version: str = "v0", release: str = "v6.0") -> p4.Playbook:
    """The GEN playbook file of `version` in `release`'s seed dir. The default release is v6.0, so every existing caller reads v6.0's bytes."""
    if not re.fullmatch(r"v[0-9]{1,3}", version):
        raise PromptError("unknown playbook version")
    directory = SEED_DIRS.get(release) if isinstance(release, str) else None
    if directory is None:
        raise PromptError("unknown release")
    try:
        text = (directory / f"{PERSONA}_{version}.txt").read_text(encoding="utf-8")
    except OSError:
        raise PromptError(f"playbook {PERSONA}/{version} cannot be read") from None
    return p4.playbook_from_text(PERSONA, version, text)


def _check_llm_arm(arm: str, playbook: p4.Playbook) -> None:
    spec = ARMS.get(arm)
    if spec is None or not spec.llm:
        raise PromptError(f"{arm!r} is not an LLM arm")
    if playbook.name != PERSONA:
        raise PromptError(f"arm {arm} needs a {PERSONA} playbook, got {playbook.name}")


def check_min_age(cards: Sequence[Mapping]) -> None:
    """A card with no numeric f.age_s (the legacy top-level age_h is only a fallback that can lower the age), or one under 2 hours, is refused before anything is built (fail closed)."""
    for c in cards:
        age = c.get("age_h")
        if isinstance(c.get("f"), Mapping):  # a real hv4-card-1 card carries f.age_s (seconds); when both exist the SMALLER age decides (fail closed)
            s = c["f"].get("age_s")
            from_s = s / 3600.0 if isinstance(s, (int, float)) and not isinstance(s, bool) and math.isfinite(s) else None
            if "age_s" in c["f"] and from_s is None:
                age = None
            elif from_s is not None:
                age = from_s if (age is None or isinstance(age, bool) or not isinstance(age, (int, float)) or not math.isfinite(age)) else min(age, from_s)
        if isinstance(age, bool) or not isinstance(age, (int, float)) or not math.isfinite(age) or age < MIN_AGE_H:
            raise PromptError(f"every card needs f.age_s >= {MIN_AGE_H * 3600:g} ({MIN_AGE_H:g} h)")


def build_entry_request(arm: str, playbook: p4.Playbook, cards, seq: int, *, max_cards: int = ENTRY_MAX_CARDS) -> Request:
    _check_llm_arm(arm, playbook)
    cards = p4.check_cards(cards, max_cards=min(max_cards, ENTRY_MAX_CARDS))
    check_min_age(cards)
    return p5._build(arm, "entry", PERSONA, playbook, cards, seq, {})


def build_recheck_request(arm: str, playbook: p4.Playbook, positions, cards, seq: int) -> Request:
    """positions: one dict per open position, id first, code-computed numbers; cards: the fresh card per position, same ids and order."""
    _check_llm_arm(arm, playbook)
    cards = p4.check_cards(cards, max_cards=ENTRY_MAX_CARDS)
    check_min_age(cards)
    if not isinstance(positions, (list, tuple)) or [p.get("id") if isinstance(p, dict) else None for p in positions] != [c["id"] for c in cards]:
        raise PromptError("positions and cards must have the same ids in the same order")
    for p in positions:
        p4.check_strings(p)
    return p5._build(arm, "recheck", PERSONA, playbook, cards, seq, {"POSITIONS": p4._lines(positions)}, items=list(positions))


# ---------------------------------------------------------------------------------------------------------------- validators
def validate_entry(reply: ModelReply, *, cards: Sequence[Mapping], knobs: Knobs, received_ms: int) -> list[Verdict]:
    """One Verdict per card, in order. action: BUY, WATCH, SKIP, PARSE_ERROR, NO_OUTPUT, STALE or BELOW_MIN (#814 names).
    A refusal, a truncated reply, bad JSON or any defect is PARSE_ERROR (invalid, counts toward the 10% limit), a transport or budget
    error is NO_OUTPUT: both are an abstain and never a BUY."""
    if knobs.persona != PERSONA:
        raise ValueError("knobs are for another persona")
    ids = [c["id"] for c in cards]
    by_id, err = v4._parse_items(reply, ids)
    if err:
        return v5._abstain_all(ids, "SKIP", *err)
    out = []
    for card in cards:
        item = by_id.get(card["id"])
        out.append(Verdict(card["id"], "PARSE_ERROR", {}, ("missing",), True) if item is None
                   else v5._entry_item(item, card, GEN_SPEC, knobs, received_ms))
    return out


validate_recheck = v5.validate_recheck           # persona-free; defect -> HOLD with invalid=True
wanted_rate = v5.wanted_rate
invalid_fraction = v5.invalid_fraction
final_size = v5.final_size


def is_entry(v: Verdict) -> bool:
    """Only a clean BUY is an entry decision. SKIP, PARSE_ERROR, NO_OUTPUT, STALE and BELOW_MIN are all 'no entry', with different labels."""
    return v.action == "BUY" and not v.invalid


def model_written_size(v: Verdict) -> float | None:
    """The stake the model wrote, or None. A risk-style reject carries max_size 0.0 meaning 'no size given' (#814 reviewer note), so
    0.0 is never a model-written stake; only a value > 0 is. An entry BUY's `size` is read the same way."""
    size = v.fields.get("max_size", v.fields.get("size")) if v.fields else None
    if isinstance(size, bool) or not isinstance(size, (int, float)) or not size > 0:
        return None
    return float(size)


def tally(verdicts: Sequence[Verdict]) -> dict:
    """Counts per action plus wanted_rate, so over-skipping can be told from a late reply, a gate or a parse failure."""
    counts: dict[str, int] = {}
    for x in verdicts:
        counts[x.action] = counts.get(x.action, 0) + 1
    return {"by_action": counts, **v5.wanted_rate(verdicts), "invalid_fraction": v4.invalid_fraction(verdicts)}


# ---------------------------------------------------------------------------------------------------------------- regime_rule
# DESIGN section 3, "Validator for regime_rule". Change shape (all keys required unless noted):
#   {"type":"regime_rule","target":<knob | "local_veto">,"new":<value | predicate>,"ttl_h":1..72,"evidence_ids":["RG-xxxxxxxx",..],
#    "why":<text>}  plus, for a LOOSENING only, "stake":0.25 and "extra_entries_per_h":0|1.
# The direction (tighten or loosen) is computed from target/new against the current knob, never declared by the model.
REGIME_TYPE = "regime_rule"
RG_ID = re.compile(r"RG-[0-9a-f]{8}")
BASE_KEYS = frozenset({"type", "target", "new", "ttl_h", "evidence_ids", "why"})
PROBE_KEYS = frozenset({"stake", "extra_entries_per_h"})
MAX_TTL_H = 72
MAX_REGIME_CHANGES = 3
MAX_EVIDENCE_IDS = 20
MAX_WHY_WORDS = 40
PROBE_STAKE = 0.25
PROBE_MAX_EXTRA_ENTRIES_PER_H = 1
USABLE_STATUS = ("ok", "partial")
# +1: a larger value is tighter (closer to no trade / smaller loss); -1: a smaller value is tighter.
TIGHTER_SIGN = {"min_conviction": 1, "stop_pct": 1, "target_pct": -1, "trail_pct": -1, "time_min": -1}
# READING: "<= 1 knob step" needs a step size the DESIGN does not give. These are code constants for QP to confirm; a change is a new sha.
PROBE_STEP = {"min_conviction": 1, "stop_pct": 5.0, "target_pct": 10.0, "trail_pct": 5.0, "time_min": 30}
TIGHTENING_TARGETS = ("stop_pct", "time_min")        # plus local_veto; DESIGN section 3 "(stop, time, local veto; TTL <= 72 h)"
# READING: "at most 1 extra entry/h" is a TOTAL: across one review's accepted probes, and across probes still active (see
# probe_extra_entries_allowed, which the item-4 runner must call and test).
MAX_EXTRA_ENTRIES_TOTAL = 1
FORBIDDEN_TARGETS = frozenset({"scanner", "veto", "vetoes", "hard_veto", "risk", "risk_facts", "limits", "hard_stop_pct", "max_hold_min",
                               "max_open", "entries_per_h", "day_loss_stop_sol", "fills", "fill", "costs", "fee", "scorer", "gate",
                               "model", "reviewer_prompt", "prompt", "embargo", "controls", "control", "size", "stake"})


@dataclass(frozen=True)
class RegimeRuleVerdict:
    ok: bool
    reason: str | None                 # fixed code, never model text
    change: Mapping | None = None      # normalised change when ok
    mode: str | None = None            # "tighten" | "probe"
    tag: str | None = None             # "regime_probe" for a probe: the runner tags every trade it opens
    expires_ms: int | None = None
    match_rule_waived: bool = False    # True: waived for the first regime_rule on a key; a later regime_rule on the same key needs >= min_matches post-change trades (hv6_review.validate_reply)


def _is_real(v) -> bool:
    return not isinstance(v, bool) and isinstance(v, (int, float)) and math.isfinite(v)


def _bounds(name: str) -> tuple[float, float]:
    return GEN_SPEC.min_conviction_bounds if name == "min_conviction" else GEN_SPEC.plan_bounds[name]


def validate_regime_rule(change, *, knobs: Mapping, block: Mapping | None, now_ms: int, universe: Mapping[str, float] | None = None,
                         vetoes: Sequence[str] = ()) -> RegimeRuleVerdict:
    """Validate ONE regime_rule change. Pure.

    knobs: the arm's current knobs (min_conviction, stop_pct, target_pct, trail_pct, time_min). block: the regime block the arm saw, as
    a mapping with `radar_status`, `as_of_ms` and `events` (a list of mappings with `id` = RG-xxxxxxxx); only those three are read.
    universe: numeric card fields for a local_veto predicate. vetoes: the arm's current local vetoes.

    Rules: must cite a present RG- id of a usable block (status ok or partial, as_of_ms before now_ms, R6). TTL 1-72 h. Tightening is
    always allowed (stop up, target/trail/time down, min_conviction up, a new local_veto) and must change the value. Loosening is
    only a bounded probe: stake 0.25, at most one step (PROBE_STEP) inside the bounds, at most 1 extra entry/h, never a veto.
    Size is not a knob: a 'smaller size' tightening is a no-op in the flat-stake cell (R8) and is refused. Anything that touches the
    scanner, vetoes, risk facts, code limits, fills, costs, scorer, gate, model, reviewer prompt, embargo or controls is refused."""
    def no(reason: str) -> RegimeRuleVerdict:
        return RegimeRuleVerdict(False, reason)

    if not isinstance(change, dict) or not all(isinstance(k, str) for k in change):
        return no("not_object")
    keys = set(change)
    if not BASE_KEYS <= keys or not keys <= BASE_KEYS | PROBE_KEYS:
        return no("bad_keys")
    if change["type"] != REGIME_TYPE:
        return no("bad_type")
    # the block the change cites
    if not isinstance(block, Mapping) or block.get("radar_status") not in USABLE_STATUS:
        return no("block_unusable")
    as_of = block.get("as_of_ms")
    if isinstance(as_of, bool) or not isinstance(as_of, int) or as_of >= now_ms:
        return no("block_not_before_write")
    events = block.get("events")
    events = events if isinstance(events, (list, tuple)) else ()
    present = {e["id"] for e in events if isinstance(e, Mapping) and isinstance(e.get("id"), str)}      # a non-string id never matches
    ev = change["evidence_ids"]
    if not isinstance(ev, list) or not 1 <= len(ev) <= MAX_EVIDENCE_IDS or not all(isinstance(i, str) and RG_ID.fullmatch(i) for i in ev):
        return no("bad_evidence_ids")
    if len(set(ev)) != len(ev) or not set(ev) <= present:
        return no("unknown_evidence_id")
    why = change["why"]
    if not isinstance(why, str) or not WHY_OK.fullmatch(why) or not why.strip() or len(why.split()) > MAX_WHY_WORDS:
        return no("bad_why")
    ttl = change["ttl_h"]
    if isinstance(ttl, bool) or not isinstance(ttl, int) or not 1 <= ttl <= MAX_TTL_H:
        return no("bad_ttl")
    target, new = change["target"], change["new"]
    if not isinstance(target, str):
        return no("bad_target")
    if target in FORBIDDEN_TARGETS:
        return no("size_is_not_a_knob" if target in ("size", "stake") else "forbidden_target")
    out = {"type": REGIME_TYPE, "target": target, "ttl_h": ttl, "evidence_ids": list(ev), "why": why}
    expires = now_ms + ttl * 3_600_000

    if target == "local_veto":                                     # tightening only
        if keys & PROBE_KEYS:
            return no("veto_cannot_be_a_probe")
        if not isinstance(universe, Mapping) or not universe:
            return no("no_universe")
        try:
            _terms, norm = parse_predicate(new, universe)
        except ReviewError as e:
            return no(str(e))
        if norm in vetoes:
            return no("duplicate_veto")
        if len(vetoes) + 1 > MAX_VETOES:
            return no("veto_cap")
        out["new"] = norm
        return RegimeRuleVerdict(True, None, out, "tighten", None, expires, True)

    if target not in TIGHTER_SIGN:
        return no("unknown_target")
    cur = knobs.get(target) if isinstance(knobs, Mapping) else None
    if not _is_real(cur):
        return no("knob_missing")
    if not _is_real(new):
        return no("knob_not_number")
    if target in ("min_conviction", "time_min") and float(new) != int(new):
        return no("knob_not_integer")
    lo, hi = _bounds(target)
    if not lo <= new <= hi:
        return no("knob_out_of_bounds")
    new = int(new) if target in ("min_conviction", "time_min") else float(new)
    delta = (new - cur) * TIGHTER_SIGN[target]
    if delta == 0:
        return no("no_change")
    out["new"] = new
    if delta > 0:                                                  # tightening
        if target not in TIGHTENING_TARGETS:
            return no("tightening_not_allowed_for_knob")           # DESIGN: tightening is stop, time, local veto only
        if keys & PROBE_KEYS:
            return no("tighten_has_probe_keys")
        return RegimeRuleVerdict(True, None, out, "tighten", None, expires, True)
    # loosening: a bounded probe
    if not PROBE_KEYS <= keys:
        return no("loosen_needs_probe_keys")
    stake, extra = change["stake"], change["extra_entries_per_h"]
    if not _is_real(stake) or float(stake) != PROBE_STAKE:
        return no("probe_stake_not_0.25")
    if isinstance(extra, bool) or not isinstance(extra, int) or not 0 <= extra <= PROBE_MAX_EXTRA_ENTRIES_PER_H:
        return no("probe_extra_entries")
    if abs(new - cur) > PROBE_STEP[target] + 1e-9:
        return no("probe_more_than_one_step")
    out.update({"stake": PROBE_STAKE, "extra_entries_per_h": extra})
    return RegimeRuleVerdict(True, None, out, "probe", "regime_probe", expires, True)


def active_extra_entries(active_probes: Sequence[Mapping], now_ms: int) -> int:
    """Extra entries/h held by probes not yet expired. Each probe is a mapping with extra_entries_per_h and expires_ms."""
    total = 0
    for p in active_probes:
        e, x = p.get("extra_entries_per_h"), p.get("expires_ms")
        if isinstance(e, int) and not isinstance(e, bool) and isinstance(x, int) and not isinstance(x, bool) and x > now_ms:
            total += e
    return total


def probe_extra_entries_allowed(active_probes: Sequence[Mapping], new_extra: int, now_ms: int) -> bool:
    """Runner-facing: may a probe adding `new_extra` entries/h start now? The total over active probes plus it must be <= 1."""
    if isinstance(new_extra, bool) or not isinstance(new_extra, int) or new_extra < 0:
        return False
    return active_extra_entries(active_probes, now_ms) + new_extra <= MAX_EXTRA_ENTRIES_TOTAL


def validate_regime_rules(changes, *, knobs: Mapping, block: Mapping | None, now_ms: int, universe: Mapping[str, float] | None = None,
                          vetoes: Sequence[str] = (), active_probes: Sequence[Mapping] = ()) -> list[RegimeRuleVerdict]:
    """A review's regime_rule changes. More than 3 refuses the whole list (as HV4's reviewer does); one change per target."""
    if not isinstance(changes, (list, tuple)):
        return [RegimeRuleVerdict(False, "not_a_list")]
    if len(changes) > MAX_REGIME_CHANGES:
        return [RegimeRuleVerdict(False, "too_many_changes") for _ in changes]
    out, seen = [], set()
    extra_total = active_extra_entries(active_probes, now_ms)
    for c in changes:
        key = c.get("target") if isinstance(c, dict) and isinstance(c.get("target"), str) else None
        if key is not None and key in seen:
            out.append(RegimeRuleVerdict(False, "duplicate_target"))
            continue
        v = validate_regime_rule(c, knobs=knobs, block=block, now_ms=now_ms, universe=universe, vetoes=vetoes)
        if v.ok and v.mode == "probe":
            extra = v.change["extra_entries_per_h"]
            if extra_total + extra > MAX_EXTRA_ENTRIES_TOTAL:
                out.append(RegimeRuleVerdict(False, "extra_entries_over_total"))
                continue
            extra_total += extra
        if v.ok:
            seen.add(key)
        out.append(v)
    return out
