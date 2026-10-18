"""hv4_validate: response validators for Haiku trader v4 (schema, bounds, hallucinated ids), and the WATCH predicate grammar check.

Serves /data/mal/hunt-1008/push-1016/hunt20/haiku-v4/DESIGN.md sections 3 (trader output, code gates), 6 (refusal) and 7
(hallucinated fields). PAPER research only. Pure functions: no I/O, no clock (the receive instant is passed in), no network.

The rule behind every function here: the model proposes, code supplies every executed number, and the default is to abstain.
  - A refusal, a truncated reply, an error, unreadable JSON, a wrong shape, a wrong enum, an out-of-bounds number, an id that was
    not sent, a missing key, an extra key: the card is SKIPped (HOLD for a re-check, HOLD_POOL for a risk grade). Never repaired,
    never clipped. Anything that is the model's fault is flagged invalid; the arm is NOT EVALUABLE above 10% invalid over the
    window (DESIGN section 7). Not the model's fault (a transport or budget error, a stale reply, a conviction under the arm's
    minimum) is an abstain that is not flagged invalid.
  - An id that was not sent, or one sent twice, voids the whole reply: after that the model's other answers cannot be trusted to
    belong to the right cards. Field-level failures void only their own card.
  - Free text (why) is never executed. It is bounded in words and characters; for a BUY or WATCH it must name at least two fields
    that exist in the card it is about.
  - The WATCH predicate is checked here against the grammar only: `field op number [AND ...]`, at most 4 terms, ops > >= < <=,
    fields numeric and present in that card. hv4_cards.Predicate is what evaluates it later; nothing is ever passed to eval.
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from typing import Mapping, Sequence

from tools import hv4_arms as arms
from tools.hv4_model import ModelReply

MAX_REPLY_CHARS = 6000
MAX_WHY_CHARS = 400
MAX_PREDICATE_CHARS = 200
INVALID_RATE_LIMIT = 0.10       # DESIGN section 7: above this over the window, the arm is NOT EVALUABLE
_FENCE = re.compile(r"^```(?:json|JSON)?[ \t]*\r?\n(.*)\r?\n```$", re.DOTALL)
_TERM = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)*)\s*(>=|<=|>|<)\s*(-?(?:\d+(?:\.\d+)?|\.\d+))$")
_AND = re.compile(r"\s+AND\s+")
_TOKEN = re.compile(r"[A-Za-z0-9_.]+")
_CTRL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_NOT_NUMERIC_FIELDS = frozenset({"as_of_slot", "as_of_ms"})


class PredicateError(ValueError):
    """The WATCH predicate is outside the grammar. The message is a fixed reason code, never model text."""


@dataclass(frozen=True)
class Knobs:
    """The arm's knobs the validator needs. min_conviction must lie inside the persona's bounds."""
    persona: str
    min_conviction: int

    def __post_init__(self):
        lo, hi = arms.PERSONA_SPECS[self.persona].min_conviction_bounds
        if isinstance(self.min_conviction, bool) or not isinstance(self.min_conviction, int) or not lo <= self.min_conviction <= hi:
            raise ValueError(f"min_conviction for {self.persona} must be an integer in [{lo}, {hi}]")

    @classmethod
    def v0(cls, persona: str) -> "Knobs":
        return cls(persona, arms.PERSONA_SPECS[persona].min_conviction_v0)


@dataclass(frozen=True)
class Verdict:
    """The outcome for one card (or position, or pool) sent in a request. `action` is what code may act on; `fields` holds only
    validated, normalised values. `invalid` marks defective model output (counts toward the 10% limit); `reasons` are codes."""
    id: str
    action: str
    fields: Mapping
    reasons: tuple[str, ...] = ()
    invalid: bool = False


# ---------------------------------------------------------------- helpers

def _no_dupes(pairs):
    out = {}
    for k, v in pairs:
        if k in out:
            raise ValueError("duplicate key")
        out[k] = v
    return out


def _no_constants(name):
    raise ValueError("non-finite number")


def _is_num(v) -> bool:
    if not isinstance(v, (int, float)) or isinstance(v, bool):
        return False
    try:
        return math.isfinite(v)
    except OverflowError:  # an int too large for a float
        return False


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def numeric_fields(card: Mapping) -> dict[str, float]:
    """Dotted path -> value for every finite number reachable through nested objects (not through arrays), minus as_of_*."""
    out: dict[str, float] = {}

    def walk(node, prefix):
        for k, v in node.items():
            path = f"{prefix}{k}"
            if isinstance(v, dict):
                walk(v, path + ".")
            elif _is_num(v) and path not in _NOT_NUMERIC_FIELDS:
                out[path] = float(v)

    walk(card, "")
    return out


def card_field_names(card: Mapping) -> set[str]:
    """Lower-cased key names (and dotted paths) anywhere in nested objects, for checking that a why names real fields."""
    names: set[str] = set()

    def walk(node, prefix):
        for k, v in node.items():
            if not isinstance(k, str):
                continue
            names.add(k.lower())
            names.add((prefix + k).lower())
            if isinstance(v, dict):
                walk(v, prefix + k + ".")

    walk(card, "")
    return {n for n in names if len(n) >= 3}


def parse_watch_predicate(text, numeric: Mapping[str, float]) -> list[tuple[str, str, float]]:
    """[(field, op, number), ...] or PredicateError. Grammar: term (AND term){0,3}; term = field op number; op one of > >= < <=.
    Every field must be in `numeric` (numeric fields of the card the WATCH is about)."""
    if not isinstance(text, str) or not text.strip() or len(text) > MAX_PREDICATE_CHARS or _CTRL.search(text):
        raise PredicateError("predicate_shape")
    parts = _AND.split(text.strip())
    if len(parts) > arms.WATCH_MAX_TERMS:
        raise PredicateError("predicate_too_many_terms")
    terms = []
    for part in parts:
        m = _TERM.match(part.strip())
        if not m:
            raise PredicateError("predicate_grammar")
        field, op, num = m.group(1), m.group(2), float(m.group(3))
        if field not in numeric:
            # a bare leaf name is accepted when it names exactly one numeric field (risk.top10_frac <- top10_frac)
            hits = [p for p in numeric if p.endswith("." + field)]
            if len(hits) != 1:
                raise PredicateError("predicate_unknown_field")
            field = hits[0]
        if not math.isfinite(num) or abs(num) > 1e12:
            raise PredicateError("predicate_number")
        terms.append((field, op, num))
    return terms


def _fmt(n: float) -> str:
    """A number the predicate grammar can read back (no exponent)."""
    return ("%.12f" % n).rstrip("0").rstrip(".") or "0"


def final_size(trader_size: float, *caps: float | None) -> float:
    """DESIGN section 3: size = min(trader, risk max_size, impact cap, age cap). Caps that are None do not apply."""
    vals = [trader_size, *(c for c in caps if c is not None)]
    if not all(_is_num(v) and v > 0 for v in vals):
        raise ValueError("sizes and caps must be positive numbers")
    return float(min(vals))


def invalid_fraction(verdicts: Sequence[Verdict]) -> float:
    return sum(v.invalid for v in verdicts) / len(verdicts) if verdicts else 0.0


# ---------------------------------------------------------------- reply parsing (shared)

def _abstain_all(ids: Sequence[str], action: str, reason: str, invalid: bool) -> list[Verdict]:
    return [Verdict(i, action, {}, (reason,), invalid) for i in ids]


def _parse_items(reply: ModelReply, ids: Sequence[str]):
    """(by_id, None) on a well-formed reply, else (None, (reason, invalid))."""
    if reply.error:
        return None, (f"no_output:{reply.error}", False)
    if reply.refused:
        return None, ("refusal", True)
    if reply.stop_reason == "max_tokens":
        return None, ("truncated", True)
    text = reply.text if isinstance(reply.text, str) else ""
    if not text.strip():
        return None, ("empty_text", True)
    if len(text) > MAX_REPLY_CHARS:
        return None, ("reply_too_long", True)
    text = text.strip()
    m = _FENCE.match(text)
    if m:
        text = m.group(1).strip()
    try:
        data = json.loads(text, object_pairs_hook=_no_dupes, parse_constant=_no_constants)
    except (ValueError, RecursionError):
        return None, ("bad_json", True)
    if isinstance(data, dict) and len(ids) == 1:
        data = [data]
    if not isinstance(data, list):
        return None, ("not_array", True)
    if len(data) > len(ids):
        return None, ("too_many_items", True)
    by_id: dict[str, dict] = {}
    for item in data:
        if not isinstance(item, dict):
            return None, ("bad_item", True)
        iid = item.get("id")
        if not isinstance(iid, str) or iid not in ids:
            return None, ("unknown_id", True)     # hallucinated id: the whole reply is void
        if iid in by_id:
            return None, ("duplicate_id", True)
        by_id[iid] = item
    return by_id, None


def _check_keys(item: dict, required: set, optional: set) -> str | None:
    """Null optional values are the same as absent. Returns a reason code or None."""
    present = {k for k, v in item.items() if v is not None or k in required}
    if (present - required) - optional:
        return "schema:extra_key"
    for k in required:
        if item.get(k) is None:
            return f"schema:missing_{k}"
    return None


def _why(item: dict, *, max_words: int, card: Mapping | None) -> tuple[str | None, str | None]:
    """(normalised why, None) or (None, reason). With a card, the why must name at least two of its fields."""
    w = item.get("why")
    if not isinstance(w, str):
        return None, "schema:why_type"
    w = " ".join(w.split())
    if not w or len(w) > MAX_WHY_CHARS or _CTRL.search(w):
        return None, "schema:why_text"
    if len(w.split()) > max_words:
        return None, "bounds:why_words"
    if card is not None:
        named = {t.lower().strip(".") for t in _TOKEN.findall(w)} & card_field_names(card)
        if len(named) < 2:
            return None, "why:names_fewer_than_two_card_fields"
    return w, None


def _stale(received_ms: int, as_of_ms) -> bool:
    return _is_int(as_of_ms) and received_ms - as_of_ms > arms.STALE_MS


# ---------------------------------------------------------------- entry (trader) replies

def validate_entry(reply: ModelReply, *, persona: str, cards: Sequence[Mapping], knobs: Knobs, received_ms: int) -> list[Verdict]:
    """One Verdict per card sent, in order. action is BUY, WATCH or SKIP. Only a clean BUY or WATCH carries fields."""
    spec = arms.PERSONA_SPECS[persona]
    if knobs.persona != persona:
        raise ValueError("knobs are for another persona")
    ids = [c["id"] for c in cards]
    by_id, err = _parse_items(reply, ids)
    if err:
        return _abstain_all(ids, "SKIP", *err)
    out = []
    for card in cards:
        item = by_id.get(card["id"])
        if item is None:
            out.append(Verdict(card["id"], "SKIP", {}, ("missing",), True))
        else:
            out.append(_entry_item(item, card, spec, knobs, received_ms))
    return out


def _entry_item(item: dict, card: Mapping, spec: arms.PersonaSpec, knobs: Knobs, received_ms: int) -> Verdict:
    cid = card["id"]

    def bad(reason: str) -> Verdict:
        return Verdict(cid, "SKIP", {}, (reason,), True)

    action = item.get("action")
    if action not in spec.actions:  # exact, case-sensitive; also rejects unhashable junk via the isinstance guard below
        return bad("enum:action")
    if action == "SKIP":
        r = _check_keys(item, {"id", "action"}, {"why"})
        if r:
            return bad(r)
        return Verdict(cid, "SKIP", {}, ("model_skip",), False)

    if action == "BUY" and spec.fixed_size is not None:      # BST: take or skip; code fixes stake and exit
        r = _check_keys(item, {"id", "action", "conviction", "why"}, set())
    elif action == "BUY":
        r = _check_keys(item, {"id", "action", "size", "conviction", "plan", "why"}, set())
    else:                                                    # WATCH
        r = _check_keys(item, {"id", "action", "conviction", "watch", "why"}, set())
    if r:
        return bad(r)
    conv = item["conviction"]
    if not _is_int(conv) or not 1 <= conv <= 5:
        return bad("bounds:conviction")
    why, r = _why(item, max_words=arms.WHY_MAX_WORDS, card=card)
    if r:
        return bad(r)

    fields: dict = {"conviction": conv, "why": why}
    if action == "BUY":
        if spec.fixed_size is not None:
            fields["size"] = spec.fixed_size
        else:
            size = item["size"]
            if not _is_num(size) or float(size) not in arms.SIZES:
                return bad("enum:size")
            fields["size"] = float(size)
            plan, r = _plan(item["plan"], spec)
            if r:
                return bad(r)
            fields["plan"] = plan
    else:
        w = item["watch"]
        if not isinstance(w, dict) or set(w) != {"when", "ttl_min"}:
            return bad("schema:watch")
        ttl = w["ttl_min"]
        if not _is_int(ttl) or not 1 <= ttl <= arms.WATCH_MAX_TTL_MIN:
            return bad("bounds:ttl_min")
        try:
            terms = parse_watch_predicate(w["when"], numeric_fields(card))
        except PredicateError as e:
            return bad(str(e))
        canon = " AND ".join(f"{f} {op} {_fmt(n)}" for f, op, n in terms)
        fields["watch"] = {"when": canon, "terms": [list(t) for t in terms], "ttl_min": ttl}

    if _stale(received_ms, card.get("as_of_ms")):
        return Verdict(cid, "SKIP", {}, ("stale",), False)
    if action == "BUY" and conv < knobs.min_conviction:
        return Verdict(cid, "SKIP", {}, ("below_min_conviction",), False)
    return Verdict(cid, action, fields, (), False)


def _plan(plan, spec: arms.PersonaSpec):
    """(normalised plan, None) or (None, reason). Out of bounds is a failure, never a clip."""
    if not isinstance(plan, dict) or set(plan) != set(spec.plan_keys):
        return None, "schema:plan_keys"
    out = {}
    for key, (lo, hi) in spec.plan_bounds.items():
        v = plan[key]
        if not _is_num(v):
            return None, f"schema:plan_{key}"
        if not lo <= v <= hi:
            return None, f"bounds:{key}"
        if key == "time_min":
            if float(v) != int(v):
                return None, f"schema:plan_{key}"
            out[key] = int(v)
        else:
            out[key] = float(v)
    return out, None


# ---------------------------------------------------------------- re-check replies

def validate_recheck(reply: ModelReply, *, positions: Sequence[Mapping], received_ms: int) -> list[Verdict]:
    """One Verdict per open position sent, in order. action is HOLD, EXIT or TIGHTEN (HOLD on any defect).
    Each position carries code-computed id, stop_pct, target_pct, mark_ret_pct and as_of_ms. Stops only tighten."""
    ids = [p["id"] for p in positions]
    by_id, err = _parse_items(reply, ids)
    if err:
        return _abstain_all(ids, "HOLD", *err)
    out = []
    for pos in positions:
        item = by_id.get(pos["id"])
        out.append(Verdict(pos["id"], "HOLD", {}, ("missing",), True) if item is None else _recheck_item(item, pos, received_ms))
    return out


def _recheck_item(item: dict, pos: Mapping, received_ms: int) -> Verdict:
    pid = pos["id"]

    def bad(reason: str) -> Verdict:
        return Verdict(pid, "HOLD", {}, (reason,), True)

    action = item.get("action")
    if action not in ("HOLD", "EXIT", "TIGHTEN"):
        return bad("enum:action")
    if action == "HOLD":
        r = _check_keys(item, {"id", "action"}, {"why"})
        return bad(r) if r else Verdict(pid, "HOLD", {}, ("model_hold",), False)
    if action == "EXIT":
        r = _check_keys(item, {"id", "action", "why"}, set())
    else:
        r = _check_keys(item, {"id", "action"}, {"new_stop_pct", "new_target_pct", "why"})
        if not r and item.get("new_stop_pct") is None and item.get("new_target_pct") is None:
            r = "schema:tighten_needs_a_change"
        if not r and item.get("why") is None:
            r = "schema:missing_why"
    if r:
        return bad(r)
    why, r = _why(item, max_words=arms.WHY_MAX_WORDS, card=None)
    if r:
        return bad(r)
    fields: dict = {"why": why}
    if action == "TIGHTEN":
        stop, target, mark = pos["stop_pct"], pos["target_pct"], pos["mark_ret_pct"]
        ns, nt = item.get("new_stop_pct"), item.get("new_target_pct")
        if ns is not None:
            if not _is_num(ns):
                return bad("schema:new_stop_pct")
            if not stop < ns < mark:      # tighter than now, and still below the mark (else it would fire at once)
                return bad("bounds:new_stop_pct")
            fields["new_stop_pct"] = float(ns)
        if nt is not None:
            if not _is_num(nt):
                return bad("schema:new_target_pct")
            if not mark < nt < target:    # lower than now, and still above the mark
                return bad("bounds:new_target_pct")
            fields["new_target_pct"] = float(nt)
    if _stale(received_ms, pos.get("as_of_ms")):
        return Verdict(pid, "HOLD", {}, ("stale",), False)
    return Verdict(pid, action, fields, (), False)


# ---------------------------------------------------------------- risk replies

def validate_risk(reply: ModelReply, *, cards: Sequence[Mapping]) -> list[Verdict]:
    """One Verdict per pool graded. action is the grade (pass, caution, reject), or HOLD_POOL when the reply is unusable: an
    invalid reply holds the pool until the next check (DESIGN section 2). effective_max_size is what code uses: a reject hides
    the pool (0.0), a caution caps the stake at 0.25."""
    ids = [c["id"] for c in cards]
    by_id, err = _parse_items(reply, ids)
    if err:
        return _abstain_all(ids, "HOLD_POOL", *err)
    out = []
    for card in cards:
        item = by_id.get(card["id"])
        out.append(Verdict(card["id"], "HOLD_POOL", {}, ("missing",), True) if item is None else _risk_item(item, card["id"]))
    return out


def _risk_item(item: dict, pid: str) -> Verdict:
    def bad(reason: str) -> Verdict:
        return Verdict(pid, "HOLD_POOL", {}, (reason,), True)

    r = _check_keys(item, {"id", "grade", "flags", "max_size", "why"}, set())
    if r:
        return bad(r)
    grade = item["grade"]
    if grade not in arms.RISK_GRADES:
        return bad("enum:grade")
    flags = item["flags"]
    if not isinstance(flags, list) or len(flags) > len(arms.RISK_FLAGS) or not all(isinstance(f, str) for f in flags):
        return bad("schema:flags")
    if len(set(flags)) != len(flags) or any(f not in arms.RISK_FLAGS for f in flags):
        return bad("enum:flags")
    size = item["max_size"]
    if not _is_num(size) or float(size) not in arms.SIZES:
        return bad("enum:max_size")
    why, r = _why(item, max_words=arms.RISK_WHY_MAX_WORDS, card=None)
    if r:
        return bad(r)
    size = float(size)
    effective = 0.0 if grade == "reject" else min(size, arms.CAUTION_MAX_SIZE) if grade == "caution" else size
    return Verdict(pid, grade, {"grade": grade, "flags": [f for f in arms.RISK_FLAGS if f in flags], "max_size": size,
                                "effective_max_size": effective, "why": why}, (), False)
