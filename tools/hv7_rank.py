"""HV7 forced-rank core (chunk C1): pure functions. Design: docs/research/hv7/DESIGN.md sections 2, 3, 5 and 9.

No clock, no network, no key, no tape. The only file reads are the two frozen static assets (playbook, template) in
load_rank_playbook / load_rank_template. Nothing here says HV7 makes money.

The ranker must order ALL shown candidates; there is no SKIP. A reply that is not a full permutation of the shown ids is a
FALLBACK and the L arm then trades R's pick (the seeded uniform pick), so a failing model pulls L toward the null.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from tools import hv4_prompts as P
from tools.haiku_caller import DEFAULT_MODEL, PRICES

PACKAGE_DIR = Path(__file__).resolve().parent
PLAYBOOK_DIR = PACKAGE_DIR / "hv7_playbooks"
TEMPLATE_DIR = PACKAGE_DIR / "hv7_templates"

N_DEFAULT = 8
N_MIN = 3
N_MAX = 12            # card cap of one rank request (hv4_prompts' own cap of 4 is for HV4/HV6)
MAX_TOKENS = 600
KIND = "rank"
ARM_L = "L"
ASSUMED_OUTPUT_TOKENS = 400   # DESIGN section 5, an assumption

# Request keys that would carry an outcome, a fill, a prior trade or another arm's decision. A guard, not a proof: the card
# builder is the real boundary (as_of_ms == t_d is also enforced).
FORBIDDEN_KEY_PARTS = ("outcome", "pnl", "fwd", "future", "realis", "realiz", "fill", "label", "forward", "next_ret",
                       "prior_trade", "exit_")
FORBIDDEN_KEYS_EXACT = frozenset({"arm", "arms", "pick", "picks", "rank", "ranks", "decision", "decisions", "exit", "trade", "trades"})

FALLBACK_REASONS = ("empty", "refusal", "not_json", "not_list", "bad_item", "unknown_id", "duplicate_id", "missing_id",
                    "wrong_length")

_ISO = re.compile(r"(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})\.(\d{3})Z")
_WORD_FIELD = re.compile(r"(?<![\w.])(?:f|risk|setup)\.[a-z][a-z0-9_]*")


class RankError(P.PromptError):
    pass


# ---------------------------------------------------------------- time keys

def iso_from_ms(ms: int) -> str:
    if isinstance(ms, bool) or not isinstance(ms, int) or ms < 0:
        raise RankError("ms must be an integer >= 0")
    return datetime.fromtimestamp(ms // 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S") + f".{ms % 1000:03d}Z"


def ms_from_iso(t_d_iso: str) -> int:
    m = _ISO.fullmatch(t_d_iso) if isinstance(t_d_iso, str) else None
    if not m:
        raise RankError("t_d must look like 2026-11-01T00:02:00.000Z")
    y, mo, d, h, mi, s, milli = (int(x) for x in m.groups())
    try:
        dt = datetime(y, mo, d, h, mi, s, tzinfo=timezone.utc)
    except ValueError:
        raise RankError("t_d is not a valid date") from None
    return int(dt.timestamp()) * 1000 + milli


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- screen and the non-LLM picks

@dataclass(frozen=True)
class ScreenSet:
    ids: tuple[str, ...]      # screen order: vol60 descending, ties by pool id
    no_trade: bool            # fewer than n_min candidates: no trade in any arm
    reason: str | None


def _vol_ok(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def screen_set(passed, vol60: dict, n: int = N_DEFAULT, n_min: int = N_MIN, vetoed=frozenset()) -> ScreenSet:
    """passed: ids of pools that passed the scan. vol60: id -> 60-minute SOL volume. Pools in `vetoed` or with a missing or
    non-finite vol60 are dropped (fail closed) before the first n are taken, so a vetoed pool is replaced by the next."""
    if isinstance(n, bool) or not isinstance(n, int) or not (1 <= n <= N_MAX) or isinstance(n_min, bool) or not isinstance(n_min, int) or not (1 <= n_min <= n):
        raise RankError("need 1 <= n_min <= n <= N_MAX")
    pool = []
    for pid in set(passed):
        if pid in vetoed or not _vol_ok(vol60.get(pid)):
            continue
        pool.append((-float(vol60[pid]), pid))
    pool.sort()
    ids = tuple(pid for _, pid in pool[:n])
    if len(ids) < n_min:
        return ScreenSet(ids=ids, no_trade=True, reason=f"fewer than {n_min} candidates ({len(ids)})")
    return ScreenSet(ids=ids, no_trade=False, reason=None)


def s_pick(screen: ScreenSet) -> str | None:
    """Arm S: candidate 1 by vol60. None on a no-trade period."""
    return None if screen.no_trade else screen.ids[0]


def u_set(screen: ScreenSet) -> tuple[str, ...]:
    """Arm U: all N candidates, one equal position each. Empty on a no-trade period."""
    return () if screen.no_trade else screen.ids


def r_pick_index(t_d_iso: str, card_shas, n: int | None = None) -> int:
    """Arm R: int(sha256("hv7-R|" + t_d_iso + "|" + ",".join(sorted(card_shas))), 16) mod N. The index is into the SCREEN order."""
    ms_from_iso(t_d_iso)
    shas = list(card_shas)
    n = len(shas) if n is None else n
    if n < 1 or n != len(shas):
        raise RankError("r_pick needs one card sha per candidate")
    return int(_sha("hv7-R|" + t_d_iso + "|" + ",".join(sorted(shas))), 16) % n


def r_pick(t_d_iso: str, ids, card_shas) -> str:
    ids = list(ids)
    if len(ids) != len(list(card_shas)):
        raise RankError("ids and card shas differ in length")
    return ids[r_pick_index(t_d_iso, card_shas)]


def fallback_pick(t_d_iso: str, ids, card_shas) -> str:
    """The L arm's fallback is R's pick of the same period."""
    return r_pick(t_d_iso, ids, card_shas)


def display_perm(arm: str, t_d_iso: str, card_shas) -> list[int]:
    """Display order: argsort of sha256("hv7-perm|<arm>|" + t_d_iso + "|" + card_sha) per card, ties by position. Returns indices into
    the screen order; entry j is the screen index shown in display slot j."""
    ms_from_iso(t_d_iso)
    if not isinstance(arm, str) or not arm or "|" in arm:
        raise RankError("arm must be a non-empty string without '|'")
    keys = [(_sha(f"hv7-perm|{arm}|{t_d_iso}|{s}"), i) for i, s in enumerate(card_shas)]
    return [i for _, i in sorted(keys)]


# ---------------------------------------------------------------- the request

def load_rank_playbook(directory: Path | None = None) -> P.Playbook:
    path = (Path(directory) if directory is not None else PLAYBOOK_DIR) / "RANK_v0.txt"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        raise RankError("playbook RANK/v0 cannot be read") from None
    return P.playbook_from_text("RANK", "v0", text)


def load_rank_template() -> P.Template:
    try:
        text = (TEMPLATE_DIR / "rank.txt").read_text(encoding="utf-8")
    except OSError:
        raise RankError("template rank cannot be read") from None
    return P.Template(KIND, text, P.sha256_hex(text))


def forbidden_keys(node) -> list[str]:
    """Keys anywhere in `node` that look like an outcome, fill, prior trade or arm decision. Card keys are code-computed history
    only (ret_60m etc.); none of these parts may appear."""
    bad: list[str] = []
    stack = [node]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            for k, v in cur.items():
                low = str(k).lower()
                if low in FORBIDDEN_KEYS_EXACT or any(part in low for part in FORBIDDEN_KEY_PARTS):
                    bad.append(str(k))
                stack.append(v)
        elif isinstance(cur, list):
            stack.extend(cur)
    return sorted(set(bad))


@dataclass(frozen=True)
class RankRequest:
    request: P.Request
    t_d_iso: str
    shown_ids: tuple[str, ...]        # display order, as sent
    screen_ids: tuple[str, ...]       # screen order
    perm: tuple[int, ...]             # display slot -> screen index
    screen_card_shas: tuple[str, ...]


def build_rank_request(t_d_iso: str, cards, *, seq: int = 0, playbook: P.Playbook | None = None, arm: str = ARM_L,
                       perm_tag: str | None = None) -> RankRequest:
    """cards: the N cards in SCREEN order, all built at t_d. They are shown in the seeded display order. perm_tag overrides the
    permutation seed arm (the offline rehearsal uses 'A' and 'B')."""
    t_ms = ms_from_iso(t_d_iso)
    cards = P.check_cards(cards, max_cards=N_MAX)
    if len(cards) < N_MIN:
        raise RankError(f"a rank request needs at least {N_MIN} cards")
    for c in cards:
        if c["as_of_ms"] != t_ms:
            raise RankError("every card must be built at t_d (as_of_ms == t_d)")
    bad = forbidden_keys(cards)
    if bad:
        raise RankError(f"card keys that look like an outcome or decision: {bad}")
    playbook = playbook if playbook is not None else load_rank_playbook()
    template = load_rank_template()
    screen_shas = tuple(P.card_sha256(c) for c in cards)
    perm = display_perm(perm_tag or arm, t_d_iso, screen_shas)
    shown = [cards[i] for i in perm]
    shown_shas = tuple(screen_shas[i] for i in perm)
    batch = P.batch_sha256(shown_shas)
    nonce = P.nonce(arm, KIND, batch, seq)
    values = {"TD": t_d_iso, "N": str(len(cards)), "CARDS": P._lines(shown), "NONCE": nonce[:P.NONCE_CHARS]}
    user_text = P.render(template, values)
    req = P.Request(arm=arm, kind=KIND, persona=None, system=P._system(playbook), user_text=user_text, max_tokens=MAX_TOKENS,
                    ids=tuple(c["id"] for c in shown), card_shas=shown_shas, batch_sha=batch, nonce=nonce, seq=seq,
                    template_sha256=template.sha256, playbook_name=playbook.name, playbook_version=playbook.version,
                    playbook_sha256=playbook.sha256)
    req.body()   # the caller's own validation before anything is reserved
    return RankRequest(request=req, t_d_iso=t_d_iso, shown_ids=req.ids, screen_ids=tuple(c["id"] for c in cards),
                       perm=tuple(perm), screen_card_shas=screen_shas)


# ---------------------------------------------------------------- the reply

@dataclass(frozen=True)
class RankVerdict:
    ok: bool
    order: tuple[str, ...]            # full permutation of the shown ids, best first; () on FALLBACK
    reason: str | None                # one of FALLBACK_REASONS when not ok
    why_words_over_20: int = 0        # report-only flags, never a fallback cause
    why_without_field: int = 0


def _fallback(reason: str) -> RankVerdict:
    return RankVerdict(ok=False, order=(), reason=reason)


def validate_rank(reply, shown_ids) -> RankVerdict:
    """reply: the model's text (or None for a refusal, timeout or budget stop). Valid only if it is a JSON array holding exactly one
    {"id": ..., "why": ...} object per shown id (no other keys), each id once. Anything else (SKIP text, prose, a code fence, a
    short, long or duplicated list) is a FALLBACK with a reason code."""
    shown = list(shown_ids)
    if reply is None or not isinstance(reply, str):
        return _fallback("refusal")
    if not reply.strip():
        return _fallback("empty")
    try:
        data = json.loads(reply)
    except (ValueError, RecursionError):
        return _fallback("not_json")
    if not isinstance(data, list):
        return _fallback("not_list")
    ids: list[str] = []
    over = nofield = 0
    for item in data:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not set(item) <= {"id", "why"}:
            return _fallback("bad_item")
        why = item.get("why")
        if "why" in item and not isinstance(why, str):
            return _fallback("bad_item")
        if isinstance(why, str):
            over += len(why.split()) > 20
            nofield += not _WORD_FIELD.search(why)
        else:
            nofield += 1
        ids.append(item["id"])
    known = set(shown)
    if any(i not in known for i in ids):
        return _fallback("unknown_id")
    if len(set(ids)) != len(ids):
        return _fallback("duplicate_id")
    if len(ids) < len(shown):
        return _fallback("missing_id")
    if len(ids) != len(shown):
        return _fallback("wrong_length")
    return RankVerdict(ok=True, order=tuple(ids), reason=None, why_words_over_20=over, why_without_field=nofield)


def pick_top_k(verdict: RankVerdict, k: int = 1) -> tuple[str, ...]:
    """The first k ids of a valid order; () for a FALLBACK (the caller then uses fallback_pick)."""
    if isinstance(k, bool) or not isinstance(k, int) or k < 1:
        raise RankError("k must be an integer >= 1")
    return verdict.order[:k] if verdict.ok else ()


@dataclass(frozen=True)
class LChoice:
    pool_id: str
    fallback: bool
    reason: str | None


def l_choice(verdict: RankVerdict, t_d_iso: str, screen_ids, screen_card_shas) -> LChoice:
    top = pick_top_k(verdict, 1)
    if top:
        return LChoice(top[0], False, None)
    return LChoice(fallback_pick(t_d_iso, screen_ids, screen_card_shas), True, verdict.reason)


def display_slot_of(perm, screen_ids, pool_id: str) -> int:
    """0-based display slot a pool was shown in (for the slot histogram)."""
    return list(screen_ids[i] for i in perm).index(pool_id)


# ---------------------------------------------------------------- cost arithmetic (DESIGN section 5)

def cost_usd(cache_write_tokens: int = 0, new_tokens: int = 0, output_tokens: int = 0, cache_read_tokens: int = 0,
             model: str = DEFAULT_MODEL) -> float:
    """USD for one call at the repo price table (per million tokens). Hourly calls never read the previous hour's cache, so a rank
    call is priced as a cache write plus uncached input."""
    p = PRICES[model]
    return (cache_write_tokens * p["cache_write"] + new_tokens * p["input"] + cache_read_tokens * p["cache_read"]
            + output_tokens * p["output"]) / 1e6
