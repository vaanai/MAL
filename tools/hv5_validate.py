"""hv5_validate: response validators for Haiku trader v5. A NEW module; tools/hv4_validate.py is not imported-and-patched, only read.

PAPER research only. Pure functions: no I/O, no clock, no network, no key.

What changed against hv4_validate (each item is a mechanism fix, none is tuned on outcomes):

1. A defective reply is no longer called a SKIP. HV4 returned action "SKIP" with invalid=True for bad JSON, a bad enum, a missing
   key, and so on, so "skip" mixed the model's choice with parser failures, transport errors, stale replies and the arm's minimum
   conviction. HV5 keeps the names apart:
     SKIP          the model said SKIP and the card was valid (reason model_skip)
     PARSE_ERROR   the reply or the card item was defective (invalid=True; reasons carry the code)
     NO_OUTPUT     a transport or budget error, no reply at all (invalid=False)
     STALE         the model said BUY/WATCH but the reply came too late (invalid=False; fields["wanted"] = BUY or WATCH)
     BELOW_MIN     a BUY under the arm's minimum conviction (invalid=False; fields["wanted"] = "BUY")
   `wanted_rate()` computes the share of valid replies in which the model asked to act, before code gates. That is the number
   that tells over-skipping apart from a late reply or a gate. A caller treats PARSE_ERROR / NO_OUTPUT / STALE / BELOW_MIN as "no
   entry", exactly as it treated SKIP; only the label differs.
2. `why` is free text that code never executes. A why over the word limit is no longer a reason to void the whole card (HV4:
   bounds:why_words). Words are counted as tokens that hold a letter or digit (a lone "-", "|", "+", an em dash is not a word),
   and a why up to HARD_WORDS_FACTOR x the limit is trimmed to the limit and kept (reason `why_trimmed`, invalid=False). Beyond
   that, or over MAX_WHY_CHARS, it is still invalid. The two-card-fields rule for BUY/WATCH stays. A field written as
   `impact_0.25` matches `impact_025`.
3. Risk reply: for grade reject, max_size is ignored by code (effective 0.0), so a missing, null or 0 max_size on a reject is
   accepted and stored as 0.0 (meaning "no size given"; fields["max_size"] is a model-written size only when it is > 0) (HV4: enum:max_size / schema:missing_max_size, which held the pool). Repeated flags are de-duplicated instead of
   voiding the grade. Everything else is as in HV4: a bad grade, an unknown flag, an extra key, a size that is not 0.25/0.5/1.0 on
   a pass or caution is invalid and holds the pool.
4. A SKIP may carry a `why`; if it does, it gets the same soft word limit.

Persona BST has no HV5 playbook (out of scope: load_playbook("BST") raises); BST keeps HV4's playbook and validator unchanged.

What did NOT change: ids that were not sent void the reply, extra keys are invalid, plan bounds are never clipped, the WATCH
grammar, the stale limit, conviction bounds. A stale or below-minimum card is never promoted to an entry.
"""
from __future__ import annotations

import json
from typing import Mapping, Sequence

from tools import hv4_arms as arms
from tools import hv4_validate as v4
from tools.hv4_model import ModelReply
from tools.hv4_validate import Knobs, PredicateError, Verdict, final_size, invalid_fraction, numeric_fields, parse_watch_predicate  # noqa: F401

MAX_WHY_CHARS = v4.MAX_WHY_CHARS
HARD_WORDS_FACTOR = 2            # a why up to 2 x the word limit is trimmed; beyond it the card is invalid
ACTIONS_NOT_MODEL_SKIP = ("PARSE_ERROR", "NO_OUTPUT", "STALE", "BELOW_MIN")


def word_count(text: str) -> int:
    """Tokens that hold at least one letter or digit. A bare dash, bar, plus or em dash is not a word."""
    return sum(1 for t in text.split() if any(ch.isalnum() for ch in t))


def _trim_words(text: str, limit: int) -> str:
    """The first `limit` words (as word_count counts them); bare separators inside that span are kept."""
    out, n = [], 0
    for t in text.split():
        if any(ch.isalnum() for ch in t):
            if n == limit:
                break
            n += 1
        out.append(t)
    return " ".join(out)


def strip_separators(text: str) -> str:
    """Drop the tokens with no letter or digit (a lone "-", "|", "+", an em dash). HV4 counts words with split(); handing it a why
    without bare separators makes its count equal word_count."""
    return " ".join(t for t in text.split() if any(ch.isalnum() for ch in t))


def _names(w: str) -> set[str]:
    names = set()
    for t in v4._TOKEN.findall(w):
        t = t.lower().strip(".")
        names.add(t)
        if "." in t:
            names.add(t.replace(".", ""))        # impact_0.25 -> impact_025
    return names


def _why5(item: dict, *, max_words: int, card: Mapping | None, required: bool = True):
    """(normalised why or None, reasons tuple, error code or None). A why over the soft limit is trimmed and kept."""
    w = item.get("why")
    if w is None and not required:
        return None, (), None
    if not isinstance(w, str):
        return None, (), "schema:why_type"
    w = " ".join(w.split())
    if not w or len(w) > MAX_WHY_CHARS or v4._CTRL.search(w):
        return None, (), "schema:why_text"
    reasons = ()
    n = word_count(w)
    if n > max_words * HARD_WORDS_FACTOR:
        return None, (), "bounds:why_words"
    if n > max_words:
        w = _trim_words(w, max_words)
        reasons = ("why_trimmed",)
    if card is not None and len(_names(w) & v4.card_field_names(card)) < 2:
        # a trimmed why may have lost the field names that sat past the limit: say so, not "never named them"
        return None, (), ("why:names_fewer_than_two_card_fields_after_trim" if reasons else "why:names_fewer_than_two_card_fields")
    return w, reasons, None


# ---------------------------------------------------------------- shared reply parsing

def _abstain_all(ids: Sequence[str], valid_action: str, reason: str, invalid: bool) -> list[Verdict]:
    """No usable reply: PARSE_ERROR for a defect, NO_OUTPUT for a transport or budget error."""
    action = "NO_OUTPUT" if reason.startswith("no_output:") else "PARSE_ERROR"
    return [Verdict(i, action, {}, (reason,), invalid) for i in ids]


# ---------------------------------------------------------------- entry

def validate_entry(reply: ModelReply, *, persona: str, cards: Sequence[Mapping], knobs: Knobs, received_ms: int) -> list[Verdict]:
    """One Verdict per card, in order. action: BUY, WATCH, SKIP, PARSE_ERROR, NO_OUTPUT, STALE or BELOW_MIN."""
    spec = arms.PERSONA_SPECS[persona]
    if knobs.persona != persona:
        raise ValueError("knobs are for another persona")
    ids = [c["id"] for c in cards]
    by_id, err = v4._parse_items(reply, ids)
    if err:
        return _abstain_all(ids, "SKIP", *err)
    out = []
    for card in cards:
        item = by_id.get(card["id"])
        out.append(Verdict(card["id"], "PARSE_ERROR", {}, ("missing",), True) if item is None
                   else _entry_item(item, card, spec, knobs, received_ms))
    return out


def _entry_item(item: dict, card: Mapping, spec: arms.PersonaSpec, knobs: Knobs, received_ms: int) -> Verdict:
    cid = card["id"]

    def bad(reason: str) -> Verdict:
        return Verdict(cid, "PARSE_ERROR", {}, (reason,), True)

    action = item.get("action")
    if action not in spec.actions:
        return bad("enum:action")
    if action == "SKIP":
        r = v4._check_keys(item, {"id", "action"}, {"why"})
        if r:
            return bad(r)
        why, wr, r = _why5(item, max_words=arms.WHY_MAX_WORDS, card=None, required=False)
        if r:
            return bad(r)
        return Verdict(cid, "SKIP", {"why": why} if why else {}, ("model_skip",) + wr, False)

    if action == "BUY" and spec.fixed_size is not None:
        r = v4._check_keys(item, {"id", "action", "conviction", "why"}, set())
    elif action == "BUY":
        r = v4._check_keys(item, {"id", "action", "size", "conviction", "plan", "why"}, set())
    else:
        r = v4._check_keys(item, {"id", "action", "conviction", "watch", "why"}, set())
    if r:
        return bad(r)
    conv = item["conviction"]
    if not v4._is_int(conv) or not 1 <= conv <= 5:
        return bad("bounds:conviction")
    why, wr, r = _why5(item, max_words=arms.WHY_MAX_WORDS, card=card)
    if r:
        return bad(r)

    fields: dict = {"conviction": conv, "why": why}
    if action == "BUY":
        if spec.fixed_size is not None:
            fields["size"] = spec.fixed_size
        else:
            size = item["size"]
            if not v4._is_num(size) or float(size) not in arms.SIZES:
                return bad("enum:size")
            fields["size"] = float(size)
            plan, r = v4._plan(item["plan"], spec)
            if r:
                return bad(r)
            fields["plan"] = plan
    else:
        w = item["watch"]
        if not isinstance(w, dict) or set(w) != {"when", "ttl_min"}:
            return bad("schema:watch")
        ttl = w["ttl_min"]
        if not v4._is_int(ttl) or not 1 <= ttl <= arms.WATCH_MAX_TTL_MIN:
            return bad("bounds:ttl_min")
        try:
            terms = parse_watch_predicate(w["when"], numeric_fields(card))
        except PredicateError as e:
            return bad(str(e))
        canon = " AND ".join(f"{f} {op} {v4._fmt(n)}" for f, op, n in terms)
        fields["watch"] = {"when": canon, "terms": [list(t) for t in terms], "ttl_min": ttl}

    if v4._stale(received_ms, card.get("as_of_ms")):
        return Verdict(cid, "STALE", {"wanted": action}, ("stale",) + wr, False)
    if action == "BUY" and conv < knobs.min_conviction:
        return Verdict(cid, "BELOW_MIN", {"wanted": "BUY", "conviction": conv}, ("below_min_conviction",) + wr, False)
    return Verdict(cid, action, fields, wr, False)


def wanted_rate(verdicts: Sequence[Verdict]) -> dict:
    """Counts over the valid replies (PARSE_ERROR and NO_OUTPUT are out of the denominator): how many the model answered with SKIP,
    and how many it wanted to act on (BUY/WATCH, including those code then turned into STALE or BELOW_MIN)."""
    valid = [x for x in verdicts if x.action not in ("PARSE_ERROR", "NO_OUTPUT")]
    wanted = sum(1 for x in valid if x.action in ("BUY", "WATCH") or x.fields.get("wanted"))
    return {"valid": len(valid), "model_skip": sum(1 for x in valid if x.action == "SKIP"), "wanted_act": wanted,
            "parse_error": sum(1 for x in verdicts if x.action == "PARSE_ERROR"),
            "no_output": sum(1 for x in verdicts if x.action == "NO_OUTPUT"),
            "wanted_act_share": (wanted / len(valid)) if valid else 0.0}


# ---------------------------------------------------------------- re-check (why handling only)

def validate_recheck(reply: ModelReply, *, positions: Sequence[Mapping], received_ms: int) -> list[Verdict]:
    """As HV4, except: the soft why limit, and a defect is action HOLD with invalid=True (a position is never left without an answer,
    and HOLD is what code does on any defect), with the reason code of the defect as HV4 gives it (no `parse_error:` prefix is added; a re-check defect is told apart from a
    model HOLD by invalid=True)."""
    ids = [p["id"] for p in positions]
    by_id, err = v4._parse_items(reply, ids)
    if err:
        return [Verdict(i, "HOLD", {}, (err[0],), err[1]) for i in ids]
    out = []
    for pos in positions:
        item = by_id.get(pos["id"])
        out.append(Verdict(pos["id"], "HOLD", {}, ("missing",), True) if item is None else _recheck_item(item, pos, received_ms))
    return out


def _recheck_item(item: dict, pos: Mapping, received_ms: int) -> Verdict:
    # The why of an EXIT/TIGHTEN is soft-checked here, then HV4's code runs on a copy whose why is trimmed AND has bare separators
    # removed (HV4 counts split() tokens, so " - " would otherwise count as words). The stored why is therefore the model's words
    # without bare separators. A why with no word at all becomes "" so HV4 voids it (schema:why_text).
    w = item.get("why")
    reasons: tuple = ()
    if isinstance(w, str):
        why, reasons, r = _why5({"why": w}, max_words=arms.WHY_MAX_WORDS, card=None)
        if r is None and why is not None:
            item = {**item, "why": strip_separators(why)}
    out = v4._recheck_item(item, pos, received_ms)
    return Verdict(out.id, out.action, out.fields, out.reasons + (reasons if not out.invalid else ()), out.invalid)


# ---------------------------------------------------------------- risk

def validate_risk(reply: ModelReply, *, cards: Sequence[Mapping]) -> list[Verdict]:
    """One Verdict per pool. action is pass, caution, reject, or HOLD_POOL (unchanged name: it is already distinct from SKIP)."""
    ids = [c["id"] for c in cards]
    by_id, err = v4._parse_items(reply, ids)
    if err:
        return [Verdict(i, "HOLD_POOL", {}, (err[0],), err[1]) for i in ids]
    out = []
    for card in cards:
        item = by_id.get(card["id"])
        out.append(Verdict(card["id"], "HOLD_POOL", {}, ("missing",), True) if item is None else _risk_item(item, card["id"]))
    return out


def _risk_item(item: dict, pid: str) -> Verdict:
    def bad(reason: str) -> Verdict:
        return Verdict(pid, "HOLD_POOL", {}, (reason,), True)

    grade = item.get("grade")
    reject = grade == "reject"
    required = {"id", "grade", "flags", "why"} if reject else {"id", "grade", "flags", "max_size", "why"}
    r = v4._check_keys(item, required, {"max_size"} if reject else set())
    if r:
        return bad(r)
    if grade not in arms.RISK_GRADES:
        return bad("enum:grade")
    flags = item["flags"]
    if not isinstance(flags, list) or len(flags) > 4 * len(arms.RISK_FLAGS) or not all(isinstance(f, str) for f in flags):
        return bad("schema:flags")
    if any(f not in arms.RISK_FLAGS for f in flags):
        return bad("enum:flags")
    flags = list(dict.fromkeys(flags))                 # repeated flags: de-duplicated, not a defect
    size = item.get("max_size")
    if reject and (size is None or size == 0) and not isinstance(size, bool):
        size = 0.0         # ignored by code (a reject is stake 0). Stored as 0.0 = "no size given", never a size the model did not write
    elif not v4._is_num(size) or float(size) not in arms.SIZES:
        return bad("enum:max_size")
    why, wr, r = _why5(item, max_words=arms.RISK_WHY_MAX_WORDS, card=None)
    if r:
        return bad(r)
    size = float(size)
    effective = 0.0 if reject else min(size, arms.CAUTION_MAX_SIZE) if grade == "caution" else size
    return Verdict(pid, grade, {"grade": grade, "flags": [f for f in arms.RISK_FLAGS if f in flags], "max_size": size,
                                "effective_max_size": effective, "why": why}, wr, False)


def dumps(verdicts: Sequence[Verdict]) -> str:
    return json.dumps([{"id": x.id, "action": x.action, "reasons": list(x.reasons), "invalid": x.invalid} for x in verdicts], sort_keys=True)
