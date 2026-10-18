#!/usr/bin/env python3
"""hv4_review: the nightly self-improvement reviewer of Haiku trader v4 (HV4): bundle builder, skip pricing hook, shuffled-outcome placebo, change
validator, hash-chained manifests, and the 00:05Z job entry (DESIGN sections 4 and 5, build item 5; DEC-031 section 1.3).

PAPER only. The ONLY model path is tools.hv4_model (CallerModel -> tools.haiku_caller; StubModel in tests). This module never reads a key, has no HTTP
client and imports nothing from tools.r4b_*, l3 or laya. tools.arena_paper (which still imports r4b until #708 vendors the stack) is imported lazily
and only by the skip-pricing adapter, never at import time.

WHAT A BUNDLE HOLDS (frozen by code at 00:01Z, review day D, cutoff = D 00:00:00.000Z):
  * trades   the arm's OWN closed trades with close_ms STRICTLY BEFORE the cutoff and not shown in an earlier bundle. close_ms is the exit decision
             instant plus the slower exit fill leg (EXIT_FILL_MS = 1,900 ms), so a trade whose exit fill lands at or after the cutoff is not an input.
             Net percent is the 0.25 SOL stake on the flat 15% leg at 1.9 s under the strand rule "total"; the pressure leg is never read.
  * skips    up to 60 skipped cards of day D-1 (chosen in sha256(id) order, outcome-blind), each with the v0 default plan's replayed outcome, only if
             that replay's close_ms is before the cutoff.
  * the current rules text, knobs and local vetoes; the open-position COUNT; a 7 day change log with each change's measured effect.
  * nothing from any other arm. Records after the cutoff are filtered out BEFORE anything is built or shuffled (perturbation invariance).

SIZE LIMIT AND TRUNCATION RULE. haiku_caller refuses a request whose canonical body bytes + 256 exceed 100,000. The whole request body (system +
user text + JSON wrapper) must be at most REQUEST_MAX_BYTES = 99,700 bytes, so the bundle is always below it. If the full bundle does not fit, code
cuts in this fixed order, measuring the real request each time (outcome-blind; nothing is chosen by P&L):
  1. strip the candle arrays from every card (they repeat the ret_* fields);
  2. drop skipped cards from the END of their sha256(id) order, as few as needed (down to none);
  3. drop trades from the END of their (close_ms, id) order, as few as needed (down to none). A dropped trade is NOT marked shown, so it is the
     first item of the next night's bundle.
  If even a bundle with no trades and no skips is over the limit, the night is refused (BundleTooLarge) and no call is made. The manifest records
  how many items were cut (dropped_skips, dropped_trades, candles_stripped) and the request byte size.

PLACEBO (CON-P). The arm's evidence set (all of its records closed before the cutoff) has its (net_pct, exit_reason, hold_s) tuples permuted among
records of the same kind (trades with trades, skips with skips) by a Fisher-Yates driven by sha256(arm|date): the marginals are kept, the link to the
card is cut. The SAME shuffled set feeds the bundle AND the change validator, so the placebo goes through the identical procedure on decoupled data.

CHANGE VALIDATOR. At most 3 changes a night (more: the whole reply is refused). Each needs a predicate (the WATCH grammar) matching at least 10
past trades or skips, a claim ("worse" or "better") whose sign agrees with mean net percent of the matches minus the rest, 1 to 20 known evidence ids,
knobs inside the persona's bounds, at most 8 local vetoes that only block, rules text at most 1,200 words, no change to the frozen header, the
NEUTRAL FIELDS paragraph or the MEASURED DIRECTIONS paragraph, and no mention of creator, top1/top10, sniper, same-slot or holder fields in a predicate,
target or new text. The resulting playbook must also pass the paragraph rules of tools/test_hv4_prompts.py (check_playbook_paragraph_rules).

MANIFEST CHAIN. One line per arm per night in <root>/manifests/<arm>.jsonl: seq, version, review_day, cutoff_ms, max_input_close_ms, the sha256 of
playbook, knobs, vetoes, bundle, request and response, commit, model, reviewer instructions, prev_manifest_sha256 and its own sha256. The runner
refuses (ManifestRefused) a manifest whose max_input_close_ms is at or after its cutoff, whose chain is broken, whose files do not hash to the
manifest, or that is written before its cutoff. effective_from_ms = max(cutoff + 30 min, written_ms), so a version is hashed and logged before its
first use.
"""
from __future__ import annotations

if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import argparse
import fcntl
import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence

from tools import hv4_arms as arms
from tools import hv4_prompts as pr
from tools.haiku_caller import DEFAULT_MODEL, MAX_INPUT_TOKENS, canonical_bytes, worst_case_input_tokens
from tools.hv4_budget import utc_day
from tools.hv4_validate import PredicateError, numeric_fields, parse_watch_predicate

HV4_ROOT = "/data/mal/arena/haiku-v4"
GENESIS = "0" * 64
DAY_MS = 86_400_000
MIN_MS = 60_000
EFFECTIVE_OFFSET_MS = 30 * MIN_MS      # v(n+1) is in force from 00:30Z of the review day
EXIT_FILL_MS = 1_900                   # the slower exit fill leg: close_ms = exit decision + this
REQUEST_MAX_BYTES = 99_700             # whole canonical request body; + haiku_caller's 256-token margin stays under 100,000
STAKE_SOL = 0.25
FLAT_RULE, FLAT_LEG = "total", "flat_1p9"
MAX_CHANGES = 3
MIN_MATCHES = 10
MAX_VETOES = 8
MAX_EVIDENCE_IDS = 20
MAX_SKIP_CARDS = 60
CHANGE_LOG_DAYS = 7
MAX_PARAGRAPH_WORDS = 250
MAX_WHY_WORDS = 40
MAX_REPLY_CHARS = 12_000
FROZEN_TITLES = ("NEUTRAL FIELDS", "MEASURED DIRECTIONS")
REVIEW_PERSONAS = ("CON", "BIG", "BST")
CHANGE_TYPES = ("playbook_edit", "knob", "local_veto")
CHANGE_KEYS = frozenset({"type", "target", "new", "predicate", "claim", "evidence_ids", "why"})
CLAIMS = ("worse", "better")
TEMPLATE_DIR = pr.TEMPLATE_DIR

# The paragraph rules of tools/test_hv4_prompts.py (TRIGGER, DIRECTIVE, UNMEASURED, paragraphs). test_hv4_review.py asserts these equal the test file's
# patterns, so the rules the playbooks are tested by and the rules a nightly edit is held to cannot drift apart.
TRIGGER = re.compile(r"creator|top10|concentrat|sniper|same[-_ ]slot", re.I)
DIRECTIVE = re.compile(r"\b(skip|buy|downgrade|grade (?:\w+ )?down|reject|danger|warning|veto|vetoes|bad|good|worse|better|avoid|sells?|selling|helps?)\b", re.I)
UNMEASURED = re.compile(r"net_flow|net_sell_25slots|unique|inflow|uptrend|\bdip\b|ret_\d|dd_ath|vol_\d|momentum|fading", re.I)
# Fields and words a change may not touch (a superset of TRIGGER: top1_frac and holder_count are neutral too).
NEUTRAL_NAME = re.compile(r"creator|top\d|concentrat|sniper|same[-_ ]?slot|holder", re.I)
PARA_OK = re.compile(r"[A-Za-z0-9 .,;:'()%/+_=<>\-]{1,1500}")
TITLE_OK = re.compile(r"[A-Z][A-Z ]{1,40}")
WHY_OK = re.compile(r"[A-Za-z0-9 .,;:'()%/+_=<>\-]{0,400}")
ID_OK = re.compile(r"[A-Za-z0-9_\-]{1,64}")
_FENCE = re.compile(r"^```(?:json|JSON)?[ \t]*\r?\n(.*)\r?\n```$", re.DOTALL)


class ReviewError(Exception):
    """A night this module refuses to run. The message is a fixed code."""


class BundleTooLarge(ReviewError):
    """Even a bundle with no trades and no skips is over REQUEST_MAX_BYTES."""


class ReviewReplyError(ReviewError):
    """The reviewer's reply is unusable as a whole: no change tonight."""


class ManifestRefused(ReviewError):
    """A manifest the runner will not use: a cutoff breach, a broken chain, files that do not hash, or a field outside the rules."""


def sha256_hex(data: bytes | str) -> str:
    return pr.sha256_hex(data)


def canon(obj: Any) -> str:
    return canonical_bytes(obj).decode("utf-8")


def day_start_ms(day: str) -> int:
    if not isinstance(day, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
        raise ReviewError("bad_day")
    return int(datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()) * 1000


def _round(x: float | None, nd: int = 2) -> float | None:
    return None if x is None else round(float(x), nd)


def _mean(xs: Sequence[float]) -> float | None:
    return sum(xs) / len(xs) if xs else None


# ---------------------------------------------------------------------------------------------------------------------------------------- arm state
@dataclass(frozen=True)
class ArmState:
    """What a version is: the playbook text, the knobs (min_conviction and the default plan) and the local vetoes."""
    persona: str
    version: int
    playbook: pr.Playbook
    knobs: Mapping[str, float]
    vetoes: tuple[str, ...] = ()

    def knobs_sha256(self) -> str:
        return sha256_hex(canon(dict(self.knobs)))

    def vetoes_sha256(self) -> str:
        return sha256_hex(canon(list(self.vetoes)))


def knob_names(persona: str) -> tuple[str, ...]:
    spec = arms.PERSONA_SPECS[persona]
    return ("min_conviction", *spec.plan_keys)


def v0_state(persona: str, directory: Path | None = None) -> ArmState:
    spec = arms.PERSONA_SPECS[persona]
    knobs = {"min_conviction": spec.min_conviction_v0, **spec.plan_defaults}
    return ArmState(persona, 0, pr.load_playbook(persona, "v0", directory), knobs, ())


def check_knob(persona: str, name: str, value: Any) -> str | None:
    """None when value is a legal setting of knob `name` for the persona (inside the declared bounds), else a fixed code."""
    spec = arms.PERSONA_SPECS[persona]
    if name not in knob_names(persona):
        return "knob_unknown"
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value or value in (float("inf"), float("-inf")):
        return "knob_not_number"
    lo, hi = spec.min_conviction_bounds if name == "min_conviction" else spec.plan_bounds[name]
    if name in ("min_conviction", "time_min") and (isinstance(value, float) and not value.is_integer()):
        return "knob_not_integer"
    if not lo <= value <= hi:
        return "knob_out_of_bounds"
    return None


# ---------------------------------------------------------------------------------------------------------------------------------------- paragraph rules
def paragraphs(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]


def paragraph_title(para: str) -> str | None:
    m = re.match(r"([A-Z][A-Z ]{1,40})\.", para)
    return m.group(1) if m else None


def check_playbook_paragraph_rules(pb: pr.Playbook, base: pr.Playbook | None = None) -> list[str]:
    """The checks of tools/test_hv4_prompts.py, as a function: [] when the playbook obeys them, else violation codes. With `base` (the v0 playbook of
    the persona) the frozen header and the two frozen paragraphs must also be unchanged. Applies to CON, BIG and BST playbooks."""
    bad: list[str] = []
    text = pb.text
    for flag in arms.RISK_FLAGS:
        text = text.replace(flag, "")
    if "concentrat" in pb.text.lower():
        bad.append("concentration_word")
    hits = [p for p in paragraphs(text) if TRIGGER.search(p)]
    if len(hits) != 1:
        bad.append("neutral_fields_not_exactly_one_trigger_paragraph")
    for p in hits:
        if not p.startswith("NEUTRAL FIELDS."):
            bad.append("trigger_word_outside_neutral_fields")
        elif DIRECTIVE.search(p) or not all(s in p for s in ("no direction", "0.237", "0.349")):
            bad.append("neutral_fields_paragraph_changed")
    for para in paragraphs(pb.text):
        if "holder_count" in para:
            if not (para.startswith("CARDS") or para.startswith("NEUTRAL FIELDS.")):
                bad.append("holder_count_outside_neutral_fields")
            elif para.startswith("NEUTRAL FIELDS.") and not all(s in para for s in ("no fixed direction", "fresh pools", "2 hours to 7 days")):
                bad.append("holder_count_neutral_wording_changed")
    if re.search(r"0\.815|fewer drains|more drains|wide base|concentrated holder|higher holder_count|lower holder_count|low holder_count", pb.text, re.I):
        bad.append("holder_count_direction")
    md = [x for x in paragraphs(pb.rules) if x.startswith("MEASURED DIRECTIONS.")]
    if len(md) != 1:
        bad.append("measured_directions_missing")
    elif UNMEASURED.search(md[0]) or "holder_count" in md[0] or "0.815" in md[0] or "drain" in md[0]:
        bad.append("measured_directions_changed")
    if pb.rules_words > pr.RULES_WORDS_MAX:
        bad.append("rules_over_1200_words")
    if base is not None:
        if pb.header_sha256 != base.header_sha256:
            bad.append("frozen_header_changed")
        for t in FROZEN_TITLES:
            a = [x for x in paragraphs(base.rules) if x.startswith(t + ".")]
            b = [x for x in paragraphs(pb.rules) if x.startswith(t + ".")]
            if a != b:
                bad.append(f"frozen_paragraph_changed:{t}")
    return sorted(set(bad))


# ---------------------------------------------------------------------------------------------------------------------------------------- records
# A record is a plain dict: {"id", "kind": "trade"|"skip", "entry_ms", "close_ms", "card", "decision", "exit_reason", "hold_s", "net_pct"}.
OUTCOME_KEYS = ("net_pct", "exit_reason", "hold_s")


def _jsonl(path: Path) -> list[dict]:
    out = []
    try:
        data = path.read_bytes()
    except OSError:
        return out
    for line in data.splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except ValueError:
                raise ReviewError("unreadable_jsonl") from None
    return out


def _decision_of(entry: Mapping) -> dict:
    return {k: entry.get(k) for k in ("size", "conviction", "plan", "why") if entry.get(k) is not None}


def load_trade_records(arm: str, root: str | os.PathLike, ledger_root: str | os.PathLike, cutoff_ms: int) -> tuple[list[dict], int]:
    """(closed trades of THIS arm with close_ms < cutoff_ms, count of positions open at the cutoff). Joins the arm's decision log (entry card,
    decision) with the arena ledger's priced trade rows of bot hv4-<arm>. Only flat_1p9 under the rule "total" at 0.25 SOL is read; no other leg."""
    dec = Path(root) / "decisions" / arm
    entries: dict[str, dict] = {}
    sells: dict[str, dict] = {}
    for f in sorted(dec.glob("*.jsonl")) if dec.exists() else []:
        for row in _jsonl(f):
            if row.get("kind") == "entry" and row.get("pos"):
                entries[row.get("order_id")] = row
            elif row.get("kind") == "sell":
                sells[row.get("pid")] = row
    out: list[dict] = []
    done: set[str] = set()
    led = Path(ledger_root) / f"hv4-{arm}"
    for f in sorted(led.glob("trades-*.jsonl")) if led.exists() else []:
        for row in _jsonl(f):
            if row.get("kind") != "trade" or abs(float(row.get("stake_sol", 0)) - STAKE_SOL) > 1e-9:
                continue
            pid = row.get("position_id")
            ent = entries.get(pid)
            ms = row.get("decision_ms") or {}
            if ent is None or not isinstance(ms.get("entry"), int) or not isinstance(ms.get("exit"), int):
                continue
            net = row["legs"][FLAT_RULE][FLAT_LEG]["net_lamports"]
            close = ms["exit"] + EXIT_FILL_MS
            done.add(pid)
            if close >= cutoff_ms:
                continue
            out.append({"id": pid, "kind": "trade", "entry_ms": ms["entry"], "close_ms": close, "card": ent.get("card") or {}, "decision": _decision_of(ent),
                        "exit_reason": str(row.get("exit_reason") or (sells.get(pid) or {}).get("reason") or ""),
                        "hold_s": int(round((ms["exit"] - ms["entry"]) / 1000.0)), "net_pct": _round(100.0 * float(net) / (STAKE_SOL * 1e9), 4)})
    open_n = sum(1 for pid, e in entries.items() if e["ms"] < cutoff_ms and not (pid in sells and sells[pid]["ms"] < cutoff_ms))
    return sorted(out, key=lambda r: (r["close_ms"], r["id"])), open_n


class CardStore:
    """Cards by sha256, one JSONL file per UTC day under <root>/cards/. The runner appends every card it builds (dedup by sha); the reviewer reads the
    cards of skipped decisions here, because a skip row in the decision log keeps only the card's hash."""

    def __init__(self, root: str | os.PathLike):
        self.dir = Path(root) / "cards"
        self._seen: set[str] = set()

    def put(self, card: Mapping, ms: int | None = None) -> str:
        sha = pr.card_sha256(dict(card))
        if sha in self._seen:
            return sha
        self._seen.add(sha)
        day = utc_day(int(ms if ms is not None else card.get("as_of_ms", 0)))
        self.dir.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(self.dir / f"{day}.jsonl"), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            os.write(fd, canon({"sha": sha, "card": card}).encode() + b"\n")
        finally:
            os.close(fd)
        return sha

    def get(self, sha: str, day: str) -> dict | None:
        for r in _jsonl(self.dir / f"{day}.jsonl"):
            if r.get("sha") == sha:
                return r.get("card")
        return None


class SkipPricer(Protocol):
    def price(self, card: Mapping, plan: Mapping, persona: str, cutoff_ms: int) -> Mapping | None:
        """The v0 default plan's outcome for this card: {"close_ms", "exit_reason", "hold_s", "net_pct"} (net_pct at 0.25 SOL, flat leg), or None when
        the replayed exit did not finish before cutoff_ms or could not be priced. Replayed in arena_paper; never reads a pressure leg."""


def load_skip_records(arm: str, root: str | os.PathLike, day: str, persona: str, pricer: SkipPricer | None, cutoff_ms: int) -> list[dict]:
    """Skipped decisions of `day` (the day before the review day), with the v0 default plan's replayed outcome. Without a pricer: none."""
    if pricer is None:
        return []
    store = CardStore(root)
    plan = dict(arms.PERSONA_SPECS[persona].plan_defaults)
    out = []
    for row in _jsonl(Path(root) / "decisions" / arm / f"{day}.jsonl"):
        if row.get("kind") != "skip" or not row.get("card_sha256") or row.get("lane") == "H" or not row.get("pool"):
            continue
        card = store.get(row["card_sha256"], day)
        if card is None:
            continue
        res = pricer.price(card, plan, persona, cutoff_ms)
        if res is None or not isinstance(res.get("close_ms"), int) or res["close_ms"] >= cutoff_ms:
            continue
        out.append({"id": "s" + row["card_sha256"][:15], "kind": "skip", "entry_ms": row["ms"], "close_ms": res["close_ms"], "card": card,
                    "decision": {"action": "SKIP", "reasons": [str(x)[:40] for x in row.get("reasons", [])][:6]}, "exit_reason": str(res["exit_reason"]),
                    "hold_s": int(res["hold_s"]), "net_pct": _round(res["net_pct"], 4)})
    seen, uniq = set(), []
    for r in out:
        if r["id"] not in seen:
            seen.add(r["id"])
            uniq.append(r)
    return sorted(uniq, key=lambda r: (r["close_ms"], r["id"]))


def before_cutoff(records: Sequence[Mapping], cutoff_ms: int) -> list[dict]:
    """The only records anything downstream may see: close_ms STRICTLY before the cutoff (a close at exactly 00:00:00.000 is out)."""
    return sorted((dict(r) for r in records if isinstance(r.get("close_ms"), int) and r["close_ms"] < cutoff_ms), key=lambda r: (r["close_ms"], r["id"]))


# ---------------------------------------------------------------------------------------------------------------------------------------- placebo
def _draw(seed: str, kind: str, i: int) -> int:
    return int(hashlib.sha256(f"{seed}|{kind}|{i}".encode()).hexdigest()[:16], 16)


def shuffle_outcomes(records: Sequence[Mapping], arm: str, date: str) -> list[dict]:
    """The placebo. Permutes the (net_pct, exit_reason, hold_s) tuples among records of the same kind with a Fisher-Yates driven by
    sha256(arm|date). Every marginal (net, exit reason, hold, and their joint multiset) is kept; the link to the card and the decision is cut.
    A permutation that happens to be the identity on a set with at least two distinct tuples is rotated by one."""
    seed = sha256_hex(f"{arm}|{date}")
    out = {r["id"]: dict(r) for r in records}
    for kind in ("trade", "skip"):
        grp = sorted((r for r in records if r["kind"] == kind), key=lambda r: (r["close_ms"], r["id"]))
        tuples = [tuple(r[k] for k in OUTCOME_KEYS) for r in grp]
        perm = list(range(len(grp)))
        for i in range(len(perm) - 1, 0, -1):
            j = _draw(seed, kind, i) % (i + 1)
            perm[i], perm[j] = perm[j], perm[i]
        if len(grp) > 1 and perm == list(range(len(grp))) and len(set(tuples)) > 1:
            perm = perm[1:] + perm[:1]
        for r, src in zip(grp, perm):
            out[r["id"]].update(dict(zip(OUTCOME_KEYS, tuples[src])))
    return sorted(out.values(), key=lambda r: (r["close_ms"], r["id"]))


# ---------------------------------------------------------------------------------------------------------------------------------------- predicates
def numeric_universe(records: Sequence[Mapping]) -> dict[str, float]:
    u: dict[str, float] = {}
    for r in records:
        for k in numeric_fields(r["card"]):
            u[k] = 0.0
    return u


def normalize_predicate(terms: Sequence[tuple[str, str, float]]) -> str:
    return " AND ".join(f"{f} {op} {format(n, '.12g')}" for f, op, n in terms)


def parse_predicate(text: Any, universe: Mapping[str, float]) -> tuple[list[tuple[str, str, float]], str]:
    """(terms, normalised text) or ReviewError('predicate_...'). A neutral field (creator, top1/top10, sniper, same-slot, holder) is refused."""
    if isinstance(text, str) and NEUTRAL_NAME.search(text):
        raise ReviewError("neutral_field")
    try:
        terms = parse_watch_predicate(text, universe)
    except PredicateError as e:
        raise ReviewError(str(e)) from None
    norm = normalize_predicate(terms)
    if NEUTRAL_NAME.search(norm):
        raise ReviewError("neutral_field")
    return terms, norm


_OPS: dict[str, Callable[[float, float], bool]] = {">": lambda a, b: a > b, ">=": lambda a, b: a >= b, "<": lambda a, b: a < b, "<=": lambda a, b: a <= b}


def holds(terms: Sequence[tuple[str, str, float]], nums: Mapping[str, float]) -> bool:
    return all(f in nums and _OPS[op](nums[f], n) for f, op, n in terms)


def vetoed(vetoes: Sequence[str], card: Mapping) -> str | None:
    """The first local veto whose predicate holds on the card (for the runner's entry gate), else None. A veto that cannot be parsed never fires."""
    nums = numeric_fields(card)
    for v in vetoes:
        try:
            terms = parse_watch_predicate(v, nums)
        except PredicateError:
            continue
        if holds(terms, nums):
            return v
    return None


def match_stats(terms: Sequence[tuple[str, str, float]], records: Sequence[Mapping]) -> dict:
    hit = [r for r in records if holds(terms, numeric_fields(r["card"]))]
    ids = {r["id"] for r in hit}
    rest = [r for r in records if r["id"] not in ids]
    return {"n_match": len(hit), "n_rest": len(rest), "mean_match": _mean([r["net_pct"] for r in hit]), "mean_rest": _mean([r["net_pct"] for r in rest])}


# ---------------------------------------------------------------------------------------------------------------------------------------- change validator
@dataclass(frozen=True)
class ChangeResult:
    accepted: tuple[dict, ...]
    rejected: tuple[tuple[int, str], ...]       # (index in the reply, code)
    state: ArmState                              # the next version's state (same as the input state when nothing was accepted)
    reply_error: str | None = None


def _strip_fence(text: str) -> str:
    t = text.strip()
    m = _FENCE.match(t)
    return m.group(1).strip() if m else t


def _no_dupes(pairs):
    d = {}
    for k, v in pairs:
        if k in d:
            raise ValueError("dup")
        d[k] = v
    return d


def parse_changes(text: Any) -> list[dict]:
    """The reply as a list of change objects, or ReviewReplyError(code): no change tonight. More than 3 proposals refuse the whole reply."""
    if not isinstance(text, str) or not text.strip():
        raise ReviewReplyError("empty_reply")
    if len(text) > MAX_REPLY_CHARS:
        raise ReviewReplyError("reply_too_long")
    try:
        data = json.loads(_strip_fence(text), object_pairs_hook=_no_dupes, parse_constant=lambda c: (_ for _ in ()).throw(ValueError("const")))
    except (ValueError, RecursionError):
        raise ReviewReplyError("not_json") from None
    if not isinstance(data, list):
        raise ReviewReplyError("not_an_array")
    if len(data) > MAX_CHANGES:
        raise ReviewReplyError("too_many_changes")
    if not all(isinstance(c, dict) for c in data):
        raise ReviewReplyError("item_not_object")
    return data


def _words(s: str) -> int:
    return len(s.split())


def _validate_one(c: Mapping, state: ArmState, evidence: Sequence[Mapping], universe: Mapping[str, float], ids: set[str]) -> tuple[dict | None, str | None]:
    """(normalised change, None) or (None, code). Pure: does not apply the change."""
    if set(c) != CHANGE_KEYS:
        return None, "bad_keys"
    typ, target, new = c["type"], c["target"], c["new"]
    if typ not in CHANGE_TYPES:
        return None, "bad_type"
    if c["claim"] not in CLAIMS:
        return None, "bad_claim"
    if not isinstance(c["why"], str) or not WHY_OK.fullmatch(c["why"]) or _words(c["why"]) > MAX_WHY_WORDS:
        return None, "bad_why"
    ev = c["evidence_ids"]
    if not isinstance(ev, list) or not 1 <= len(ev) <= MAX_EVIDENCE_IDS or not all(isinstance(i, str) and ID_OK.fullmatch(i) for i in ev):
        return None, "bad_evidence_ids"
    if len(set(ev)) != len(ev) or not set(ev) <= ids:
        return None, "unknown_evidence_id"
    try:
        terms, norm = parse_predicate(c["predicate"], universe)
    except ReviewError as e:
        return None, str(e)
    if not isinstance(target, str):
        return None, "bad_target"
    out = {"type": typ, "target": target, "predicate": norm, "claim": c["claim"], "evidence_ids": list(ev), "why": c["why"]}
    if typ == "knob":
        if NEUTRAL_NAME.search(target):
            return None, "neutral_field"
        err = check_knob(state.persona, target, new)
        if err:
            return None, err
        out["new"] = int(new) if target in ("min_conviction", "time_min") else new
    elif typ == "local_veto":
        if target != "veto":
            return None, "bad_target"
        if c["claim"] != "worse":
            return None, "veto_claim_must_be_worse"
        if not isinstance(new, str):
            return None, "bad_new"
        try:
            _t, vnorm = parse_predicate(new, universe)
        except ReviewError as e:
            return None, str(e)
        if vnorm != norm:
            return None, "veto_differs_from_predicate"
        out["new"] = vnorm
    else:
        if not isinstance(new, str) or not isinstance(target, str):
            return None, "bad_new"
        if NEUTRAL_NAME.search(target) or NEUTRAL_NAME.search(new):
            return None, "neutral_field"
        if not TITLE_OK.fullmatch(target) or target in FROZEN_TITLES:
            return None, "bad_or_frozen_target"
        if not PARA_OK.fullmatch(new) or not new.startswith(target + ". ") or _words(new) > MAX_PARAGRAPH_WORDS:
            return None, "bad_paragraph"
        out["new"] = new
    st = match_stats(terms, evidence)
    if st["n_match"] < MIN_MATCHES:
        return None, "fewer_than_10_matches"
    if st["n_rest"] < 1 or st["mean_match"] is None or st["mean_rest"] is None or st["mean_match"] == st["mean_rest"]:
        return None, "no_contrast"
    if (st["mean_match"] < st["mean_rest"]) != (c["claim"] == "worse"):
        return None, "effect_sign_mismatch"
    out["evidence"] = {"n_match": st["n_match"], "mean_match": _round(st["mean_match"]), "mean_rest": _round(st["mean_rest"])}
    return out, None


def apply_change(state: ArmState, ch: Mapping, base: pr.Playbook) -> tuple[ArmState | None, str | None]:
    """(next state, None) or (None, code). Applies one validated change; the result must pass the paragraph rules."""
    if ch["type"] == "knob":
        knobs = dict(state.knobs)
        knobs[ch["target"]] = ch["new"]
        return ArmState(state.persona, state.version, state.playbook, knobs, state.vetoes), None
    if ch["type"] == "local_veto":
        if ch["new"] in state.vetoes:
            return None, "duplicate_veto"
        if len(state.vetoes) + 1 > MAX_VETOES:
            return None, "veto_cap"
        return ArmState(state.persona, state.version, state.playbook, state.knobs, (*state.vetoes, ch["new"])), None
    paras = paragraphs(state.playbook.rules)
    titles = [paragraph_title(p) for p in paras]
    if ch["target"] in titles:
        paras[titles.index(ch["target"])] = ch["new"]
    else:
        paras.append(ch["new"])
    text = f"{pr.HEADER_MARK}\n{state.playbook.header}\n{pr.RULES_MARK}\n" + "\n\n".join(paras) + "\n"
    try:
        nb = pr.playbook_from_text(state.playbook.name, state.playbook.version, text)
    except pr.PromptError:
        return None, "playbook_unparseable"
    bad = check_playbook_paragraph_rules(nb, base)
    if bad:
        return None, bad[0]
    return ArmState(state.persona, state.version, nb, state.knobs, state.vetoes), None


def validate_changes(reply_text: Any, state: ArmState, evidence: Sequence[Mapping], base: pr.Playbook | None = None) -> ChangeResult:
    """Parse, test and apply the reviewer's reply. `evidence` is every record of the arm closed before the cutoff (shuffled already for the placebo).
    The returned state keeps the input version number; the caller makes the next version."""
    base = base or pr.load_playbook(state.persona, "v0")
    try:
        changes = parse_changes(reply_text)
    except ReviewReplyError as e:
        return ChangeResult((), (), state, str(e))
    universe = numeric_universe(evidence)
    ids = {r["id"] for r in evidence}
    accepted: list[dict] = []
    rejected: list[tuple[int, str]] = []
    cur = state
    used: set[tuple[str, str]] = set()
    for i, c in enumerate(changes):
        ch, err = _validate_one(c, cur, evidence, universe, ids)
        if err is None:
            key = (ch["type"], ch["target"] if ch["type"] != "local_veto" else ch["new"])
            if key in used:
                err = "duplicate_target"
            else:
                nxt, err = apply_change(cur, ch, base)
        if err is not None:
            rejected.append((i, err))
            continue
        used.add(key)
        accepted.append(ch)
        cur = nxt
    return ChangeResult(tuple(accepted), tuple(rejected), cur)


# ---------------------------------------------------------------------------------------------------------------------------------------- the bundle
@dataclass(frozen=True)
class Bundle:
    obj: dict
    text: str
    sha256: str
    request: pr.Request
    request_bytes: int
    shown_ids: tuple[str, ...]
    max_input_close_ms: int
    dropped: dict
    evidence: tuple[dict, ...]


def _strip_candles(card: Mapping) -> dict:
    return {k: v for k, v in card.items() if not str(k).startswith("candles")}


def _clean(s: Any, n: int = 160) -> str:
    return re.sub(r"[^A-Za-z0-9 _.:/+\-]", " ", str(s))[:n]


def _trade_item(r: Mapping, strip: bool) -> dict:
    d = r["decision"]
    return {"id": r["id"], "entry_ms": r["entry_ms"], "close_ms": r["close_ms"], "card": _strip_candles(r["card"]) if strip else r["card"],
            "decision": {"conviction": d.get("conviction"), "size": d.get("size"), "plan": d.get("plan"), "why": _clean(d.get("why", ""))},
            "exit_reason": _clean(r["exit_reason"], 40), "hold_s": r["hold_s"], "net_pct": r["net_pct"]}


def _skip_item(r: Mapping, strip: bool) -> dict:
    return {"id": r["id"], "entry_ms": r["entry_ms"], "card": _strip_candles(r["card"]) if strip else r["card"], "reasons": r["decision"].get("reasons", []),
            "replay": {"close_ms": r["close_ms"], "exit_reason": _clean(r["exit_reason"], 40), "hold_s": r["hold_s"], "net_pct": r["net_pct"]}}


def review_nonce(arm: str, day: str, bundle_sha: str) -> str:
    return sha256_hex(f"{arm}|review|{bundle_sha}|{day}")


def review_system_text() -> tuple[str, str]:
    """(text, sha256 of system + user templates): the reviewer's frozen instructions."""
    s = (TEMPLATE_DIR / "review_system.txt").read_text(encoding="utf-8")
    u = (TEMPLATE_DIR / "review_user.txt").read_text(encoding="utf-8")
    return s, sha256_hex(s + "\x00" + u)


def build_review_request(arm: str, day: str, state: ArmState, bundle_text: str, bundle_sha: str) -> pr.Request:
    """The reviewer's request: the frozen instructions as the system block (no cache marker: one call a night), the bundle in the user message.
    kind "review": ledger REV-<arm>, max_tokens 4000 (hv4_arms)."""
    system, tsha = review_system_text()
    n = review_nonce(arm, day, bundle_sha)
    user = pr.render(pr.Template("review", (TEMPLATE_DIR / "review_user.txt").read_text(encoding="utf-8"), ""), {"BUNDLE": bundle_text, "NONCE": n[:pr.NONCE_CHARS]})
    req = pr.Request(arm=arm, kind="review", persona=state.persona, system=({"type": "text", "text": system},), user_text=user,
                     max_tokens=arms.MAX_TOKENS["review"], ids=(), card_shas=(), batch_sha=bundle_sha, nonce=n, seq=0, template_sha256=tsha,
                     playbook_name=state.persona, playbook_version=f"v{state.version}", playbook_sha256=state.playbook.sha256)
    req.body()
    return req


def request_size(req: pr.Request) -> int:
    return len(canonical_bytes(req.body()))


def _largest(lo: int, hi: int, ok: Callable[[int], bool]) -> int | None:
    """The largest n in [lo, hi] with ok(n), for ok monotone (true up to some n); None when ok(lo) is false."""
    if not ok(lo):
        return None
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if ok(mid):
            lo = mid
        else:
            hi = mid - 1
    return lo


def skip_order(skips: Sequence[Mapping]) -> list[dict]:
    """sha256(id) order: outcome-blind and deterministic; the first MAX_SKIP_CARDS are the ones considered, truncation cuts from the end."""
    return sorted((dict(s) for s in skips), key=lambda s: (sha256_hex(s["id"]), s["id"]))[:MAX_SKIP_CARDS]


def change_log(manifests: Sequence[Mapping], records: Sequence[Mapping], cutoff_ms: int) -> list[dict]:
    """The last CHANGE_LOG_DAYS versions with accepted changes, each change's measured effect on trades entered while the version was in force:
    n and mean net percent of the trades its predicate matched and of the rest (the same records the reviewer sees, shuffled for the placebo)."""
    rows = [m for m in manifests if m.get("seq", 0) >= 1]
    out = []
    trades = [r for r in records if r["kind"] == "trade"]
    for i, m in enumerate(rows):
        if m["cutoff_ms"] < cutoff_ms - CHANGE_LOG_DAYS * DAY_MS:
            continue
        end = rows[i + 1]["effective_from_ms"] if i + 1 < len(rows) else cutoff_ms
        inforce = [r for r in trades if m["effective_from_ms"] <= r["entry_ms"] < end]
        chs = []
        for ch in m.get("changes", []):
            eff: dict = {"n_in_force": len(inforce)}
            try:
                terms = parse_watch_predicate(ch["predicate"], numeric_universe(inforce) or {})
                st = match_stats(terms, inforce)
                eff.update(n_match=st["n_match"], mean_match=_round(st["mean_match"]), mean_rest=_round(st["mean_rest"]))
            except (PredicateError, KeyError):
                pass
            chs.append({"type": ch["type"], "target": _clean(ch["target"], 40), "new": _clean(ch["new"], 200), "predicate": ch["predicate"], "claim": ch["claim"],
                        "effect": eff})
        out.append({"day": m["review_day"], "version": m["version"], "effective_from_ms": m["effective_from_ms"], "changes": chs,
                    "version_effect": {"n": len(inforce), "mean_net_pct": _round(_mean([r["net_pct"] for r in inforce]))}})
    return out


def build_bundle(arm: str, day: str, state: ArmState, records: Sequence[Mapping], *, shown_before: Sequence[str] = (), open_positions: int = 0,
                 manifests: Sequence[Mapping] = (), placebo: bool = False, limit: int = REQUEST_MAX_BYTES) -> Bundle:
    """Build the arm's bundle for review day `day`. `records` is anything the loaders returned; only close_ms < cutoff survive, before any shuffle.
    Raises BundleTooLarge when not even an empty bundle fits."""
    cutoff = day_start_ms(day)
    ev = before_cutoff(records, cutoff)
    if placebo:
        ev = shuffle_outcomes(ev, arm, day)
    shown = set(shown_before)
    trades = [r for r in ev if r["kind"] == "trade" and r["id"] not in shown]
    skips = skip_order([r for r in ev if r["kind"] == "skip" and r["id"] not in shown and cutoff - DAY_MS <= r["entry_ms"] < cutoff])
    log = change_log(manifests, ev, cutoff)
    for r in (*trades, *skips):
        pr.check_strings(r["card"])  # the card guard of the request builder: the alphabet and key rules hold for every card that reaches the prompt
    playbook = {"version": state.version, "sha256": state.playbook.sha256, "rules": state.playbook.rules}

    def make(nt: int, ns: int, strip: bool) -> tuple[dict, pr.Request, int, str, str]:
        obj = {"schema": "hv4-review-bundle-1", "arm": arm, "review_day": day, "cutoff_ms": cutoff, "playbook": playbook, "knobs": dict(state.knobs),
               "vetoes": list(state.vetoes), "open_positions": int(open_positions), "change_log": log,
               "trades": [_trade_item(r, strip) for r in trades[:nt]], "skips": [_skip_item(r, strip) for r in skips[:ns]],
               "dropped": {"trades": len(trades) - nt, "skips": len(skips) - ns, "candles_stripped": strip}}
        text = canon(obj)
        sha = sha256_hex(text)
        req = build_review_request(arm, day, state, text, sha)
        return obj, req, request_size(req), text, sha

    def fits(nt: int, ns: int, strip: bool) -> bool:
        return make(nt, ns, strip)[2] <= limit

    nt, ns, strip = len(trades), len(skips), False
    if not fits(nt, ns, strip):
        strip = True
        if not fits(nt, ns, strip):
            k = _largest(0, ns, lambda k: fits(nt, k, True))
            if k is not None:
                ns = k
            else:
                ns = 0
                m = _largest(0, nt, lambda m: fits(m, 0, True))
                if m is None:
                    raise BundleTooLarge("bundle_too_large")
                nt = m
    obj, req, size, text, sha = make(nt, ns, strip)
    if size > limit or (limit <= REQUEST_MAX_BYTES and worst_case_input_tokens(req.body()) > MAX_INPUT_TOKENS):
        raise BundleTooLarge("bundle_too_large")  # not reachable by the fit loop above; kept as a fail-closed guard in front of haiku_caller's own refusal
    close = max([r["close_ms"] for r in ev], default=0)
    return Bundle(obj, text, sha, req, size, tuple(r["id"] for r in trades[:nt]), close, dict(obj["dropped"]), tuple(ev))


# ---------------------------------------------------------------------------------------------------------------------------------------- manifests
MANIFEST_FIELDS = ("schema", "seq", "arm", "version", "review_day", "cutoff_ms", "written_ms", "effective_from_ms", "max_input_close_ms", "commit", "model",
                   "reviewer_sha256", "playbook_sha256", "playbook_header_sha256", "knobs", "knobs_sha256", "vetoes", "vetoes_sha256", "bundle_sha256",
                   "request_sha256", "response_sha256", "changes", "rejected", "reply_error", "review_error", "shown_ids", "truncation",
                   "prev_manifest_sha256", "sha256")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")


def manifest_hash(row: Mapping) -> str:
    return sha256_hex(canon({k: v for k, v in row.items() if k != "sha256"}))


def _paths(root: str | os.PathLike, arm: str) -> tuple[Path, Path, Path]:
    r = Path(root)
    return r / "manifests" / f"{arm}.jsonl", r / "playbooks" / arm, r / "bundles" / arm


def read_manifests(root: str | os.PathLike, arm: str) -> list[dict]:
    return _jsonl(_paths(root, arm)[0])


def _write_once(path: Path, data: bytes) -> None:
    if path.exists():
        if path.read_bytes() != data:
            raise ManifestRefused("file_exists_with_other_content")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def genesis_manifest(arm: str, clock_start_ms: int, commit: str) -> tuple[dict, ArmState]:
    st = v0_state(arms.ARMS[arm].persona)
    _, tsha = review_system_text()
    row = {"schema": "hv4-manifest-1", "seq": 0, "arm": arm, "version": 0, "review_day": None, "cutoff_ms": 0, "written_ms": clock_start_ms,
           "effective_from_ms": clock_start_ms, "max_input_close_ms": 0, "commit": commit, "model": DEFAULT_MODEL, "reviewer_sha256": tsha,
           "playbook_sha256": st.playbook.sha256, "playbook_header_sha256": st.playbook.header_sha256, "knobs": dict(st.knobs), "knobs_sha256": st.knobs_sha256(),
           "vetoes": [], "vetoes_sha256": st.vetoes_sha256(), "bundle_sha256": None, "request_sha256": None, "response_sha256": None, "changes": [], "rejected": [],
           "reply_error": None, "review_error": None, "shown_ids": [], "truncation": None, "prev_manifest_sha256": GENESIS}
    row["sha256"] = manifest_hash(row)
    return row, st


def check_manifest(row: Mapping, prev: Mapping | None, arm: str, *, reviewer_sha256: str | None = None, frozen_sha: str | None = None,
                   v0_playbook_sha256: str | None = None) -> None:
    """Raises ManifestRefused(code) unless the line obeys every rule. `prev` is the previous line (None for the genesis line)."""
    def no(code: str):
        raise ManifestRefused(code)

    if not isinstance(row, Mapping) or set(row) != set(MANIFEST_FIELDS):
        no("manifest_fields")
    if row["sha256"] != manifest_hash(row):
        no("manifest_hash")
    if row["arm"] != arm or row["model"] != DEFAULT_MODEL:
        no("arm_or_model")
    if reviewer_sha256 is not None and row["reviewer_sha256"] != reviewer_sha256:
        no("reviewer_instructions_changed")
    if frozen_sha is not None and not str(row["commit"]).startswith(frozen_sha[:12]):
        no("commit_not_frozen_sha")
    want_prev = GENESIS if prev is None else prev["sha256"]
    if row["prev_manifest_sha256"] != want_prev:
        no("chain_broken")
    if row["seq"] != (0 if prev is None else prev["seq"] + 1):
        no("seq_gap")
    for k in ("playbook_sha256", "playbook_header_sha256", "knobs_sha256", "vetoes_sha256"):
        if not isinstance(row[k], str) or not SHA_RE.match(row[k]):
            no("bad_hash_field")
    persona = arms.ARMS[arm].persona
    if row["knobs_sha256"] != sha256_hex(canon(dict(row["knobs"]))) or row["vetoes_sha256"] != sha256_hex(canon(list(row["vetoes"]))):
        no("knobs_or_vetoes_hash")
    if set(row["knobs"]) != set(knob_names(persona)) or any(check_knob(persona, k, v) for k, v in row["knobs"].items()):
        no("knob_out_of_bounds")
    if len(row["vetoes"]) > MAX_VETOES or (prev is not None and list(row["vetoes"][:len(prev["vetoes"])]) != list(prev["vetoes"])):
        no("vetoes_not_tightening")
    if prev is None:
        if row["version"] != 0 or row["max_input_close_ms"] != 0 or (v0_playbook_sha256 and row["playbook_sha256"] != v0_playbook_sha256):
            no("genesis_not_v0")
        return
    day = row["review_day"]
    cutoff = day_start_ms(day)
    if row["cutoff_ms"] != cutoff:
        no("cutoff_not_review_day")
    if prev["review_day"] is not None and day <= prev["review_day"]:
        no("review_day_not_increasing")
    if row["version"] != prev["version"] + 1:
        no("version_gap")
    if not isinstance(row["max_input_close_ms"], int) or row["max_input_close_ms"] >= cutoff:
        no("input_after_cutoff")
    if not isinstance(row["written_ms"], int) or row["written_ms"] < cutoff:
        no("written_before_cutoff")
    if row["effective_from_ms"] != max(cutoff + EFFECTIVE_OFFSET_MS, row["written_ms"]):
        no("effective_from")
    if len(row["changes"]) > MAX_CHANGES:
        no("too_many_changes")
    if row["playbook_header_sha256"] != prev["playbook_header_sha256"]:
        no("frozen_header_changed")


def load_version(root: str | os.PathLike, arm: str, row: Mapping, *, verify_bundle: bool = True) -> ArmState:
    """The ArmState a manifest line names; its files must hash to the line (ManifestRefused otherwise)."""
    persona = arms.ARMS[arm].persona
    _, pdir, bdir = _paths(root, arm)
    if row["version"] == 0:
        st = v0_state(persona)
        pb = st.playbook
    else:
        try:
            text = (pdir / f"v{row['version']}.txt").read_text(encoding="utf-8")
        except OSError:
            raise ManifestRefused("playbook_file_missing") from None
        pb = pr.playbook_from_text(persona, f"v{row['version']}", text)
    if pb.sha256 != row["playbook_sha256"] or pb.header_sha256 != row["playbook_header_sha256"]:
        raise ManifestRefused("playbook_hash")
    base = pr.load_playbook(persona, "v0")
    if check_playbook_paragraph_rules(pb, base):
        raise ManifestRefused("playbook_paragraph_rules")
    if verify_bundle and row["version"] > 0 and row["bundle_sha256"]:
        try:
            data = (bdir / f"{row['review_day']}.json").read_bytes()
        except OSError:
            raise ManifestRefused("bundle_file_missing") from None
        if sha256_hex(data) != row["bundle_sha256"]:
            raise ManifestRefused("bundle_hash")
        b = json.loads(data)
        closes = [x["close_ms"] for x in b.get("trades", [])] + [x["replay"]["close_ms"] for x in b.get("skips", [])]
        if any(c >= row["cutoff_ms"] for c in closes) or (closes and max(closes) > row["max_input_close_ms"]):
            raise ManifestRefused("input_after_cutoff")
    return ArmState(persona, row["version"], pb, dict(row["knobs"]), tuple(row["vetoes"]))


def verify_chain(root: str | os.PathLike, arm: str, **kw) -> list[dict]:
    """Every line of the arm's manifest file, checked in order. ManifestRefused on the first bad line."""
    rows = read_manifests(root, arm)
    v0 = pr.load_playbook(arms.ARMS[arm].persona, "v0").sha256
    prev = None
    for row in rows:
        check_manifest(row, prev, arm, v0_playbook_sha256=v0, **kw)
        load_version(root, arm, row)
        prev = row
    return rows


@dataclass(frozen=True)
class ActiveVersion:
    arm: str
    manifest: Mapping
    state: ArmState


def active_version(root: str | os.PathLike, arm: str, now_ms: int, **kw) -> ActiveVersion | None:
    """The version in force for `arm` at now_ms: the last line with effective_from_ms <= now_ms, after the WHOLE chain verifies. None when the arm
    has no manifest file (no reviewer for it, or genesis not written). ManifestRefused when anything is wrong: the runner does not run on it."""
    rows = verify_chain(root, arm, **kw)
    live = [r for r in rows if r["effective_from_ms"] <= now_ms]
    if not rows:
        return None
    if not live:
        raise ManifestRefused("no_version_in_force")
    row = live[-1]
    return ActiveVersion(arm, row, load_version(root, arm, row, verify_bundle=False))


def append_manifest(root: str | os.PathLike, arm: str, row: Mapping, playbook_text: str | None, bundle_bytes: bytes | None, **kw) -> None:
    """Write the version's files, then append the line under flock, only if it extends the verified chain. Idempotent for the same line."""
    mpath, pdir, bdir = _paths(root, arm)
    rows = read_manifests(root, arm)
    if any(r["sha256"] == row["sha256"] for r in rows):
        return
    check_manifest(row, rows[-1] if rows else None, arm, **kw)
    if playbook_text is not None and row["version"] > 0:
        _write_once(pdir / f"v{row['version']}.txt", playbook_text.encode("utf-8"))
    if bundle_bytes is not None:
        _write_once(bdir / f"{row['review_day']}.json", bundle_bytes)
    mpath.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(mpath), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        if len(_jsonl(mpath)) != len(rows):
            raise ManifestRefused("chain_moved")
        os.write(fd, canon(row).encode() + b"\n")
        os.fsync(fd)
    finally:
        os.close(fd)


def make_manifest(arm: str, day: str, prev: Mapping, state: ArmState, bundle: Bundle, result: ChangeResult, *, written_ms: int, commit: str,
                  response_sha256: str | None, review_error: str | None) -> dict:
    cutoff = day_start_ms(day)
    _, tsha = review_system_text()
    row = {"schema": "hv4-manifest-1", "seq": prev["seq"] + 1, "arm": arm, "version": state.version, "review_day": day, "cutoff_ms": cutoff,
           "written_ms": written_ms, "effective_from_ms": max(cutoff + EFFECTIVE_OFFSET_MS, written_ms), "max_input_close_ms": bundle.max_input_close_ms,
           "commit": commit, "model": DEFAULT_MODEL, "reviewer_sha256": tsha, "playbook_sha256": state.playbook.sha256,
           "playbook_header_sha256": state.playbook.header_sha256, "knobs": dict(state.knobs), "knobs_sha256": state.knobs_sha256(), "vetoes": list(state.vetoes),
           "vetoes_sha256": state.vetoes_sha256(), "bundle_sha256": bundle.sha256, "request_sha256": bundle.request.request_sha256(),
           "response_sha256": response_sha256, "changes": [dict(c) for c in result.accepted], "rejected": [[i, c] for i, c in result.rejected],
           "reply_error": result.reply_error, "review_error": review_error, "shown_ids": list(bundle.shown_ids),
           "truncation": {**bundle.dropped, "request_bytes": bundle.request_bytes}, "prev_manifest_sha256": prev["sha256"]}
    row["sha256"] = manifest_hash(row)
    return row


# ---------------------------------------------------------------------------------------------------------------------------------------- the night
def review_night(arm: str, day: str, *, root: str | os.PathLike, ledger_root: str | os.PathLike, model: Any, now_ms: int, commit: str,
                 pricer: SkipPricer | None = None, records: Sequence[Mapping] | None = None, open_positions: int | None = None) -> dict:
    """One arm's review for review day `day`. Returns the manifest line it appended (or the existing one for that day). Every failure of the model
    or of the reply leaves the playbook unchanged and is recorded in the line (review_error / reply_error); only a broken chain or an oversize bundle
    stops the night."""
    spec = arms.ARMS[arm]
    if not spec.reviewer:
        raise ReviewError("no_reviewer_for_arm")
    rows = verify_chain(root, arm, reviewer_sha256=review_system_text()[1])
    if not rows:
        raise ManifestRefused("no_genesis_manifest")
    if any(r["review_day"] == day for r in rows):
        return next(r for r in rows if r["review_day"] == day)
    head = rows[-1]
    cutoff = day_start_ms(day)
    state = load_version(root, arm, head, verify_bundle=False)
    if records is None:
        trades, open_n = load_trade_records(arm, root, ledger_root, cutoff)
        prev_day = utc_day(cutoff - DAY_MS)
        records = trades + load_skip_records(arm, root, prev_day, spec.persona, pricer, cutoff)
        open_positions = open_n
    shown = {i for m in rows for i in m["shown_ids"]}
    bundle = build_bundle(arm, day, state, records, shown_before=shown, open_positions=open_positions or 0, manifests=rows, placebo=spec.role == "P")
    reply = model.complete(bundle.request, now_ms)
    err = reply.error or ("refusal" if getattr(reply, "refused", False) else None)
    if err is None and not (reply.text or "").strip():
        err = "empty_reply"
    result = validate_changes("" if err else reply.text, state, bundle.evidence) if not err else ChangeResult((), (), state, None)
    nv = head["version"] + 1
    nxt = ArmState(state.persona, nv, pr.playbook_from_text(state.persona, f"v{nv}", result.state.playbook.text), result.state.knobs, result.state.vetoes)
    row = make_manifest(arm, day, head, nxt, bundle, result, written_ms=now_ms, commit=commit, response_sha256=reply.response_sha256, review_error=err)
    append_manifest(root, arm, row, nxt.playbook.text, bundle.text.encode("utf-8"))
    return row


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="HV4 nightly reviewer: paper only. The API key is read by tools.haiku_caller from the environment; never printed.")
    ap.add_argument("cmd", choices=("genesis", "run", "verify"))
    ap.add_argument("--root", default=HV4_ROOT)
    ap.add_argument("--ledger-root", default="/data/mal/arena/ledger")
    ap.add_argument("--tape-dir", default="/data/mal/arena/tape")
    ap.add_argument("--arm", required=True, choices=arms.reviewer_arms())
    ap.add_argument("--day", default=None, help="review day YYYY-MM-DD (default: today UTC)")
    ap.add_argument("--commit", default="")
    ap.add_argument("--clock-start-ms", type=int, default=0)
    ap.add_argument("--now-ms", type=int, default=None)
    ap.add_argument("--stub", action="store_true", help="StubModel: no API call, no key read, every reply is []")
    a = ap.parse_args(argv)
    now = a.now_ms if a.now_ms is not None else time.time_ns() // 1_000_000
    if a.cmd == "genesis":
        row, _st = genesis_manifest(a.arm, a.clock_start_ms, a.commit)
        append_manifest(a.root, a.arm, row, None, None)
        print(canon({"arm": a.arm, "genesis": row["sha256"]}))
        return 0
    if a.cmd == "verify":
        rows = verify_chain(a.root, a.arm, reviewer_sha256=review_system_text()[1])
        print(canon({"arm": a.arm, "lines": len(rows), "head": rows[-1]["sha256"] if rows else None}))
        return 0
    from tools.hv4_budget import BudgetGuard
    from tools.hv4_model import CallerModel, StubModel

    guard = BudgetGuard(a.root)
    model = StubModel(guard=guard) if a.stub else CallerModel(guard)
    pricer = None  # the arena_paper replay adapter is not wired in this PR: skipped cards are left out and the manifest says so (truncation.skips = 0)
    row = review_night(a.arm, a.day or utc_day(now), root=a.root, ledger_root=a.ledger_root, model=model, now_ms=now, commit=a.commit, pricer=pricer)
    print(canon({"arm": a.arm, "day": row["review_day"], "version": row["version"], "accepted": len(row["changes"]), "rejected": row["rejected"],
                 "review_error": row["review_error"], "reply_error": row["reply_error"], "manifest": row["sha256"], "playbook": row["playbook_sha256"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
