"""HV8 rating core (chunk C1): pure functions. Design: docs/research/hv8/DESIGN.md sections 2 to 4 and the C1 row of section 7/8.

No clock, no network, no key, no tape. The only file reads are the two frozen static assets (playbook, template).
Nothing here says HV8 makes money. HV8 has 0 trades.

What is here:
  tape_view          the HV6 card with every RPC-derived field removed (fail closed on an unknown key)
  build_rate_request one card -> one Request (system block with one cache marker, user text with the card and a nonce)
  validate_rate      a reply -> ratings 0-3 for flow / dump_risk / trend, or unrated with a reason
  retest_select      the seeded 10% retest set
  placebo_perm       P8's Sattolo derangement, fully seeded
  split_batches      first-rating batches, then retest batches, each at most 64
  call_caps, call_cost_usd   the call-count and cost arithmetic of DESIGN sections 4 and 6
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path

from tools import hv4_cards as C
from tools import hv4_prompts as P
from tools.haiku_caller import DEFAULT_MODEL, PRICES
from tools.hv7_rank import forbidden_keys

PACKAGE_DIR = Path(__file__).resolve().parent
PLAYBOOK_DIR = PACKAGE_DIR / "hv8_playbooks"
TEMPLATE_DIR = PACKAGE_DIR / "hv8_templates"

ARM = "RATE"
KIND = "rate"
MAX_TOKENS = 300
ITEMS = ("flow", "dump_risk", "trend")
MIRROR = {"flow": "f.net_flow_15m", "dump_risk": "f.top3_signer_share_60m", "trend": "f.close_vs_hi_6h"}
ANCHOR = "f.vol_60m"
WHY_MAX_WORDS = 25
REPLY_KEYS = ("why",) + ITEMS

# ---- the tape-only card view (DESIGN section 3): which card keys survive. Every key a HV7 card can carry is in exactly one list.
KEPT_TOP = frozenset({"v", "id", "lane", "as_of_slot", "as_of_ms", "setup", "f", "risk", "tops", "c1", "c5", "untrusted", "x"})
REMOVED_TOP = frozenset({"mint"})
KEPT_F = frozenset(C.FEATURE_FIELDS) | frozenset({"age_known", "age_tier", "max_stake"})
# risk: the three the HV7 runner computes from tape (holder_count from the pool's net map, quote_chg_15m, quote_dd_peak from the quote history)
KEPT_RISK = ("holder_count", "quote_chg_15m", "quote_dd_peak")
REMOVED_RISK = frozenset({
    "top1_frac", "top10_frac", "creator_sold_frac", "same_slot_supply", "sniper_share", "sniper_still_holding", "holders_age_s",
    "as_of_ms", "as_of_slot", "holders_as_of_ms", "extensions", "freeze_authority_null", "mint_authority_null", "holder_count_cover",
    "mayhem", "sell_route_ok", "token_program",
})
KEPT_NESTED = {"tops": frozenset({"buyer", "seller"}), "untrusted": frozenset({"name", "symbol"}), "setup": frozenset({"type", "persona", "z"})}
SETUP_TAGS = tuple(sorted(C.SETUP_PERSONA))

# T8a (all-fields twin): every numeric field of the view, plus setup z and each setup tag as 0/1. Count fixed from code.
T8A_NUMERIC = tuple(f"f.{k}" for k in C.FEATURE_FIELDS) + ("f.max_stake",) + tuple(f"risk.{k}" for k in KEPT_RISK) + ("setup.z",)
T8A_TAGS = tuple(f"setup.tag.{t}" for t in SETUP_TAGS)
T8A_FIELDS = T8A_NUMERIC + T8A_TAGS

_FIELD_NAME = re.compile(r"(?<![\w.])((?:f|risk|setup)\.[a-z][a-z0-9_]*)")
_KNOWN_NAMES = frozenset({f"f.{k}" for k in KEPT_F} | {f"risk.{k}" for k in KEPT_RISK} | {"setup.type", "setup.z"})


class RateError(P.PromptError):
    pass


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- the view and the request

def tape_view(card: dict) -> dict:
    """The card with every RPC-derived key removed. Fail closed: a key that is in neither the kept nor the removed list raises."""
    if not isinstance(card, dict):
        raise RateError("a card must be an object")
    unknown = sorted(set(card) - KEPT_TOP - REMOVED_TOP)
    f, risk = card.get("f"), card.get("risk")
    if isinstance(f, dict):
        unknown += [f"f.{k}" for k in sorted(set(f) - KEPT_F)]
    if isinstance(risk, dict):
        unknown += [f"risk.{k}" for k in sorted(set(risk) - set(KEPT_RISK) - REMOVED_RISK)]
    for k, ok in KEPT_NESTED.items():
        if isinstance(card.get(k), dict):
            unknown += [f"{k}.{x}" for x in sorted(set(card[k]) - ok)]
    if unknown:
        raise RateError(f"card keys in neither the kept nor the removed list: {unknown}")
    view = {k: v for k, v in card.items() if k in KEPT_TOP}
    if isinstance(risk, dict):
        view["risk"] = {k: v for k, v in risk.items() if k in KEPT_RISK}
    return json.loads(json.dumps(view))


def view_sha256(card: dict) -> str:
    """The card sha that keys every HV8 seed: the sha256 of the canonical bytes of the tape-only view."""
    return P.card_sha256(tape_view(card))


def load_rate_playbook(directory: Path | None = None) -> P.Playbook:
    path = (Path(directory) if directory is not None else PLAYBOOK_DIR) / "RATE_v0.txt"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        raise RateError("playbook RATE/v0 cannot be read") from None
    return P.playbook_from_text("RATE", "v0", text)


def load_rate_template() -> P.Template:
    try:
        text = (TEMPLATE_DIR / "rate.txt").read_text(encoding="utf-8")
    except OSError:
        raise RateError("template rate cannot be read") from None
    return P.Template(KIND, text, P.sha256_hex(text))


def build_rate_request(card: dict, *, seq: int = 0, playbook: P.Playbook | None = None) -> P.Request:
    """One card, one request. `seq` 0 is the first rating, 1 the retest, 2 a re-run: the nonce differs, so each is an independent call."""
    view = tape_view(card)
    bad = forbidden_keys(view)
    if bad:
        raise RateError(f"card keys that look like an outcome or decision: {bad}")
    P.check_cards([view], max_cards=1)
    playbook = playbook if playbook is not None else load_rate_playbook()
    template = load_rate_template()
    sha = P.card_sha256(view)
    nonce = P.nonce(ARM, KIND, sha, seq)
    user_text = P.render(template, {"CARD": P._lines([view]), "NONCE": nonce[:P.NONCE_CHARS]})
    req = P.Request(arm=ARM, kind=KIND, persona=None, system=P._system(playbook), user_text=user_text, max_tokens=MAX_TOKENS,
                    ids=(view["id"],), card_shas=(sha,), batch_sha=sha, nonce=nonce, seq=seq, template_sha256=template.sha256,
                    playbook_name=playbook.name, playbook_version=playbook.version, playbook_sha256=playbook.sha256)
    req.body()   # the caller's own validation
    return req


# ---------------------------------------------------------------- the reply

@dataclass(frozen=True)
class RateVerdict:
    rated: bool
    ratings: tuple[int, int, int] | None      # (flow, dump_risk, trend)
    why: str | None
    why_defect: tuple[str, ...]
    reason: str | None                        # why the card is unrated


def _unrated(reason: str) -> RateVerdict:
    return RateVerdict(False, None, None, (), reason)


def _no_dup(pairs):
    d = {}
    for k, v in pairs:
        if k in d:
            raise ValueError("duplicate key")
        d[k] = v
    return d


def validate_rate(reply, stop_reason: str | None = None) -> RateVerdict:
    """A reply is rated only if it is one JSON object with exactly the keys why, flow, dump_risk, trend; each item an int (not bool, not float,
    not string) in 0..3; why a string. Anything else makes the card unrated. A long or field-less why is accepted and logged."""
    if stop_reason == "refusal":
        return _unrated("refusal")
    if not isinstance(reply, str) or not reply.strip():
        return _unrated("empty")
    try:
        obj = json.loads(reply, object_pairs_hook=_no_dup)
    except (ValueError, RecursionError):
        return _unrated("not_json")
    if not isinstance(obj, dict):
        return _unrated("not_object")
    if set(obj) != set(REPLY_KEYS):
        missing = [k for k in ITEMS if k not in obj]
        return _unrated("missing_item" if missing else "bad_keys")
    out = []
    for k in ITEMS:
        v = obj[k]
        if isinstance(v, bool) or not isinstance(v, int):
            return _unrated("item_not_int")
        if not 0 <= v <= 3:
            return _unrated("item_out_of_range")
        out.append(v)
    why = obj["why"]
    if not isinstance(why, str):
        return _unrated("why_not_string")
    defects = []
    if len(why.split()) > WHY_MAX_WORDS:
        defects.append("long")
    if not any(m in _KNOWN_NAMES for m in _FIELD_NAME.findall(why)):
        defects.append("no_field")
    return RateVerdict(True, tuple(out), why, tuple(defects), None)


# ---------------------------------------------------------------- retest, placebo, batches

def _distinct(shas) -> list[str]:
    shas = list(shas)
    if len(set(shas)) != len(shas):
        raise RateError("card shas must be distinct")
    return shas


def retest_select(card_shas, frac_div: int = 10) -> list[str]:
    """Seeded retest set: key = int(sha256("hv8-retest|" + card_sha)); the ceil(n / 10) lowest keys, returned lowest first."""
    shas = _distinct(card_shas)
    n = -(-len(shas) // frac_div)
    keyed = sorted((int(_sha("hv8-retest|" + s), 16), s) for s in shas)
    return [s for _, s in keyed[:n]]


def placebo_perm(t_d_iso: str, card_shas) -> list[int]:
    """P8's permutation for one set (cards in screen order). seed = sha256("hv8-P|" + t_d_iso + "|" + ",".join(shas)).hexdigest();
    Sattolo: pi = [0..N-1]; for i = N-1 down to 1: j = int(sha256(seed + "|" + str(i)).hexdigest(), 16) mod i; swap pi[i], pi[j].
    A uniform random N-cycle, so no fixed point. Card k receives the whole triple of card pi[k]."""
    shas = _distinct(card_shas)
    n = len(shas)
    if n < 2:
        raise RateError("a derangement needs at least 2 cards")
    seed = _sha("hv8-P|" + t_d_iso + "|" + ",".join(shas))
    pi = list(range(n))
    for i in range(n - 1, 0, -1):
        j = int(_sha(seed + "|" + str(i)), 16) % i
        pi[i], pi[j] = pi[j], pi[i]
    return pi


def placebo_ratings(t_d_iso: str, card_shas, triples) -> list:
    pi = placebo_perm(t_d_iso, card_shas)
    triples = list(triples)
    if len(triples) != len(pi):
        raise RateError("one rating triple per card")
    return [triples[pi[k]] for k in range(len(pi))]


BATCH_MAX = 64


def split_batches(first_shas, retest_shas, size: int = BATCH_MAX) -> list[dict]:
    """Seeded split (order = sha256("hv8-batch|" + sha)), first-rating batches numbered from 1, then retest batches, so a first rating
    always precedes its retest. Re-runs (at most 5% of cards) go in one extra batch made later."""
    first, retest = _distinct(first_shas), _distinct(retest_shas)
    if not set(retest) <= set(first):
        raise RateError("every retest card must be one of the first-rated cards")
    out, n = [], 0
    for kind, group in (("first", first), ("retest", retest)):
        ordered = sorted(group, key=lambda s: _sha("hv8-batch|" + s))
        for i in range(0, len(ordered), size):
            n += 1
            out.append({"batch": n, "kind": kind, "shas": ordered[i:i + size]})
    return out


# ---------------------------------------------------------------- counts and cost

def call_caps(n_cards: int) -> dict:
    retest = -(-n_cards // 10)
    rerun = -(-n_cards * 5 // 100)
    return {"first": n_cards, "retest": retest, "rerun": rerun, "cap": n_cards + retest + rerun}


ASSUMED_SYSTEM_TOKENS, ASSUMED_NEW_TOKENS, ASSUMED_OUTPUT_TOKENS = 2874, 1482, 120   # DESIGN section 6, assumptions


def call_cost_usd(cache_write: int = ASSUMED_SYSTEM_TOKENS, new_input: int = ASSUMED_NEW_TOKENS, output: int = ASSUMED_OUTPUT_TOKENS,
                  model: str = DEFAULT_MODEL) -> float:
    p = PRICES[model]
    return (cache_write * p["cache_write"] + new_input * p["input"] + output * p["output"]) / 1e6
