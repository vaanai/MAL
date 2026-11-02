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
N_MAX = 8             # card cap of one rank request; any N from N_MIN to N_MAX is valid (hv4_prompts' entry cap of 4 is for HV4/HV6)
MAX_TOKENS = 2000    # was 600 until 2026-10-30: real-model smoke #1779 cut 30 of 31 replies at 600 (stop_reason max_tokens, 6-7 of 8 items written).
                     # Measured structure (cached replies, no content): 78-206 chars per item, 1.412-1.620 chars per output token, so a full N_MAX reply
                     # is about 8 x 206 / 1.412 = 1,167 tokens at worst; 2000 leaves a margin over 1.5x (test_max_tokens_covers_a_full_reply)
MEASURED_MAX_ITEM_CHARS = 206          # smoke #1779, 175 complete items: the longest {"id":..,"why":..} object plus its comma
MEASURED_MIN_CHARS_PER_TOKEN = 1.412   # smoke #1779, 31 replies: the lowest reply chars / output_tokens
KIND = "rank"
ARM_L = "L"
ASSUMED_OUTPUT_TOKENS = 400   # DESIGN section 5, an assumption

# Request keys that would carry an outcome, a fill, a prior trade or another arm's decision. A guard, not a proof: the card
# builder is the real boundary (as_of_ms == t_d is also enforced).
FORBIDDEN_KEY_PARTS = ("outcome", "pnl", "fwd", "future", "realis", "realiz", "fill", "label", "forward", "next_ret",
                       "prior_trade", "exit_")
FORBIDDEN_KEYS_EXACT = frozenset({"arm", "arms", "pick", "picks", "rank", "ranks", "decision", "decisions", "exit", "trade", "trades"})

FALLBACK_REASONS = ("empty", "refusal", "not_json", "not_list", "bad_item", "unknown_id", "duplicate_id", "missing_id")

# One markdown code fence around the whole reply (smoke #1779: 2 of 31 replies opened one despite "no code fence"). Only whitespace may sit outside it;
# the info string may be empty or "json" (any case). Prose before or after, a second fence, or an unclosed fence does not match and stays not_json.
_FENCE = re.compile(r"\A\s*```[ \t]*(?:[Jj][Ss][Oo][Nn])?[ \t]*\r?\n(.*?)\r?\n?[ \t]*```\s*\Z", re.S)
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
    ids: tuple[str, ...]      # screen order: vol_60m descending, ties by pool id
    no_trade: bool            # fewer than n_min candidates: no trade in any arm
    reason: str | None


def _vol_ok(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def screen_set(passed, vol_60m: dict, n: int = N_DEFAULT, n_min: int = N_MIN, vetoed=frozenset()) -> ScreenSet:
    """passed: ids of pools that passed the scan. vol_60m: id -> 60-minute SOL volume (the card field f.vol_60m). Pools in `vetoed` or with a
    missing or non-finite vol_60m are dropped (fail closed) before the first n are taken, so a vetoed pool is replaced by the next."""
    if isinstance(n, bool) or not isinstance(n, int) or not (N_MIN <= n <= N_MAX) or isinstance(n_min, bool) or not isinstance(n_min, int) or not (N_MIN <= n_min <= n):
        raise RankError(f"need n and n_min to be integers with {N_MIN} <= n_min <= n <= {N_MAX} (got n={n!r}, n_min={n_min!r})")
    pool = []
    for pid in set(passed):
        if pid in vetoed or not _vol_ok(vol_60m.get(pid)):
            continue
        pool.append((-float(vol_60m[pid]), pid))
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


def check_rank_cards(cards) -> list[dict]:
    """HV7-local card check. C2 NOTE: cards must be built only through hv6_cards.build_card (so no outcome field can exist) or checked
    against an allowlist of keys; the forbidden-key guard is a backstop, not the boundary.

    N_MIN <= N <= N_MAX cards, then hv4_prompts' per-card checks (ids, as_of_ms, x null, strings, size).
    Cards must be in screen order: f.vol_60m descending, ties by id (R's index and the S arm point into this order)."""
    if not isinstance(cards, (list, tuple)) or not (N_MIN <= len(cards) <= N_MAX):
        raise RankError(f"a rank request needs {N_MIN} to {N_MAX} cards")
    cards = P.check_cards(cards, max_cards=N_MAX)
    keys = []
    for c in cards:
        v = (c.get("f") or {}).get("vol_60m") if isinstance(c.get("f"), dict) else None
        if not _vol_ok(v):
            raise RankError("every card needs a finite f.vol_60m (the screen key)")
        keys.append((-float(v), c["id"]))
    if keys != sorted(keys):
        raise RankError("cards must be in screen order (f.vol_60m descending, ties by id)")
    return list(cards)


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
    cards = check_rank_cards(cards)
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
    why_defect: int = 0               # items with any why defect (over 20 words, no card field named, missing or not a string)
    why_invalid: int = 0              # items whose why is missing or not a string


def _fallback(reason: str) -> RankVerdict:
    return RankVerdict(ok=False, order=(), reason=reason)


def validate_rank(reply, shown_ids) -> RankVerdict:
    """reply: the model's text (or None for a refusal, timeout or budget stop). Valid only if it is a JSON array holding exactly one
    {"id": ..., "why": ...} object per shown id (no other keys), each id once. Anything else (SKIP text, prose, a short, long or
    duplicated list, a reply cut off by max_tokens) is a FALLBACK with a reason code.

    FENCE RULE (2026-10-30, after real-model smoke #1779, before any HV7 clock): ONE markdown code fence around the whole reply (```
    or ```json, only whitespace outside it) is removed before parsing; the array inside must still pass every check below. Prose
    before or after the array, a second fence and an unclosed fence (a cut-off reply) stay not_json. The fence carries no ranking
    information, so removing it cannot change an order, only turn a wrapped full permutation from FALLBACK into valid.

    WHY RULE (decided, QP ruling on #996 part d): a full permutation with a bad `why` (over 20 words, naming no card field, missing,
    or not a string) is ACCEPTED and logged in the verdict counts (report-only). It is never a fallback."""
    shown = list(shown_ids)
    if reply is None or not isinstance(reply, str):
        return _fallback("refusal")
    if not reply.strip():
        return _fallback("empty")
    dup_key = False

    def _pairs(pairs):      # json.loads keeps the LAST of two equal keys; a reply object with a repeated key is malformed, not "the last one wins"
        nonlocal dup_key
        d = dict(pairs)
        dup_key = dup_key or len(d) != len(pairs)
        return d

    m = _FENCE.match(reply)
    body = m.group(1) if m else reply
    try:
        data = json.loads(body, object_pairs_hook=_pairs)
    except (ValueError, RecursionError):
        return _fallback("not_json")
    if not isinstance(data, list):
        return _fallback("not_list")
    if dup_key:
        return _fallback("bad_item")
    ids: list[str] = []
    over = nofield = bad = defect = 0
    for item in data:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not set(item) <= {"id", "why"}:
            return _fallback("bad_item")
        why = item.get("why")
        if isinstance(why, str):
            o, nf = len(why.split()) > 20, not _WORD_FIELD.search(why)
            over += o
            nofield += nf
            defect += o or nf
        else:
            bad += 1      # missing or non-string why: accepted, counted
            defect += 1
        ids.append(item["id"])
    known = set(shown)
    if any(i not in known for i in ids):
        return _fallback("unknown_id")
    if len(set(ids)) != len(ids):
        return _fallback("duplicate_id")
    if len(ids) < len(shown):      # known ids, no duplicates: the list cannot be longer than `shown`, so this is the only length fault left
        return _fallback("missing_id")
    return RankVerdict(ok=True, order=tuple(ids), reason=None, why_words_over_20=over, why_without_field=nofield, why_defect=defect, why_invalid=bad)


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
