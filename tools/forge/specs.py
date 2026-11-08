"""FORGE spec handling for the team loop: robust JSON extraction from model text, validation/repair against the DSL, canonical hash.

The DSL is in FORGE-SPEC.md. Repair is deliberately small and only clamps what the evaluator would reject by range
(tp, sl, trail, max_hold_s, cooldown_s), truncates to 6 conditions, fills defaults and fixes stake_sol to 0.1.
Anything else (unknown feature, bad op, non-numeric value, no entry condition) is a rejection with a reason string.
"""
from __future__ import annotations

import hashlib
import json
import math
import re

OPS = ("<", "<=", ">", ">=")
MAX_CONDITIONS = 6
MAX_HOLD = 86400
STAKE = 0.1


class SpecRejected(ValueError):
    pass


def _num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def _frac(x, lo, hi, name, repairs):
    """None stays None. A number is clamped into [lo, hi]. Anything else is a rejection."""
    if x is None:
        return None
    if not _num(x):
        raise SpecRejected(f"{name} not a number")
    y = min(max(float(x), lo), hi)
    if y != x:
        repairs.append(f"clamp {name}")
    return y


def canonical_spec(spec: dict) -> dict:
    """Same strategy -> same dict: conditions sorted, numbers normalised."""
    out = json.loads(json.dumps(spec))
    out["entry"] = sorted(out["entry"], key=lambda c: (c["f"], c["op"], c["v"]))
    return out


def spec_hash(spec: dict) -> str:
    c = canonical_spec(spec)
    return hashlib.sha256(json.dumps(c, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:16]


def validate_spec(raw, features) -> tuple[dict, list[str]]:
    """Return (clean spec, repairs). Raise SpecRejected(reason) when the spec cannot be used."""
    feats = set(features)
    repairs: list[str] = []
    if not isinstance(raw, dict):
        raise SpecRejected("spec not an object")
    ent = raw.get("entry")
    if not isinstance(ent, list) or not ent:
        raise SpecRejected("no entry conditions")
    if len(ent) > MAX_CONDITIONS:
        ent = ent[:MAX_CONDITIONS]
        repairs.append("truncate conditions")
    entry = []
    for c in ent:
        if not isinstance(c, dict):
            raise SpecRejected("condition not an object")
        f, op, v = c.get("f"), c.get("op"), c.get("v")
        if f not in feats:
            raise SpecRejected(f"unknown feature {str(f)[:40]!r}")
        if not _num(v):
            raise SpecRejected("condition value not a finite number")
        if op in ("==", "=") and v in (0, 1):  # binary feature (venue, ev_is_buy): x == 1 -> x >= 1, x == 0 -> x <= 0
            op = ">=" if v == 1 else "<="
            repairs.append("eq_to_ineq")
        if op not in OPS:
            raise SpecRejected(f"bad op {str(op)[:10]!r}")
        entry.append({"f": f, "op": op, "v": float(v) if isinstance(v, float) else v})
    venue = raw.get("venue", "any")
    if venue not in (0, 1, "any") or isinstance(venue, bool):
        if venue in ("0", "1"):
            venue = int(venue)
            repairs.append("venue str->int")
        else:
            venue = "any"
            repairs.append("venue default")
    if "venue" not in raw:
        repairs.append("venue default")
    cd = raw.get("cooldown_s", 0)
    if not _num(cd):
        cd = 0
        repairs.append("cooldown default")
    cd = int(min(max(cd, 0), MAX_HOLD))
    opp = raw.get("one_per_pool", True)
    if not isinstance(opp, bool):
        opp = True
        repairs.append("one_per_pool default")
    ex = raw.get("exit")
    if not isinstance(ex, dict):
        raise SpecRejected("no exit object")
    tp = _frac(ex.get("tp"), 0.0, 10.0, "tp", repairs)
    sl = _frac(ex.get("sl"), 0.0, 0.99, "sl", repairs)
    tr = _frac(ex.get("trail"), 0.0, 0.99, "trail", repairs)
    mh = ex.get("max_hold_s", 3600)
    if not _num(mh):
        raise SpecRejected("max_hold_s not a number")
    mh2 = int(min(max(mh, 1), MAX_HOLD))
    if mh2 != mh:
        repairs.append("clamp max_hold_s")
    # a zero tp/sl/trail would close at once; treat as absent
    tp, sl, tr = (None if x == 0 else x for x in (tp, sl, tr))
    if raw.get("stake_sol") != STAKE:
        repairs.append("stake fixed")
    return {"entry": entry, "venue": venue, "cooldown_s": cd, "one_per_pool": opp,
            "exit": {"tp": tp, "sl": sl, "trail": tr, "max_hold_s": mh2}, "stake_sol": STAKE}, repairs


def _balanced(text: str, start: int):
    """Substring of the balanced {...} or [...] starting at text[start], honouring strings; None if unbalanced."""
    open_c = text[start]
    close_c = "}" if open_c == "{" else "]"
    depth, in_s, esc = 0, False, False
    for i in range(start, len(text)):
        ch = text[i]
        if in_s:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_s = False
            continue
        if ch == '"':
            in_s = True
        elif ch == open_c:
            depth += 1
        elif ch == close_c:
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None


def extract_json(text: str):
    """Best-effort: whole text, fenced block, then the first balanced object/array that parses. Returns None on failure."""
    if not isinstance(text, str):
        return None
    t = text.strip()
    cands = [t]
    m = re.search(r"```(?:json)?\s*(.*?)```", t, re.S)
    if m:
        cands.append(m.group(1).strip())
    for c in cands:
        try:
            return json.loads(c)
        except Exception:
            pass
    for i, ch in enumerate(t):
        if ch in "{[":
            sub = _balanced(t, i)
            if sub:
                try:
                    return json.loads(sub)
                except Exception:
                    # trailing commas are the usual model slip
                    try:
                        return json.loads(re.sub(r",\s*([}\]])", r"\1", sub))
                    except Exception:
                        pass
    return None


def parse_specs(text: str, features, key: str = "specs"):
    """-> (items, stats). items: list of {spec, rationale, repairs}. stats: counts incl. rejected reasons. Never raises."""
    stats = {"parsed": 0, "repaired": 0, "rejected": 0, "json_failed": 0, "reasons": []}
    obj = extract_json(text)
    if obj is None:
        stats["json_failed"] = 1
        return [], stats
    if isinstance(obj, dict) and key in obj:
        arr = obj[key]
    elif isinstance(obj, dict) and "entry" in obj:
        arr = [obj]
    else:
        arr = obj
    if not isinstance(arr, list):
        stats["json_failed"] = 1
        return [], stats
    items = []
    for it in arr:
        rat = ""
        raw = it
        if isinstance(it, dict) and "spec" in it:
            raw = it["spec"]
            rat = str(it.get("rationale", ""))[:300]
        try:
            clean, rep = validate_spec(raw, features)
        except SpecRejected as e:
            stats["rejected"] += 1
            stats["reasons"].append(str(e))
            continue
        stats["parsed"] += 1
        if rep:
            stats["repaired"] += 1
        items.append({"spec": clean, "rationale": rat, "repairs": rep})
    return items, stats
