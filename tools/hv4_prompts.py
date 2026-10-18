"""hv4_prompts: playbook and template loading, and the request builder (cache marker, nonce) of Haiku trader v4.

Serves /data/mal/hunt-1008/push-1016/hunt20/haiku-v4/DESIGN.md sections 3 (trader agent), 5 (matching, nonce) and 6 (caching).
PAPER research only. No I/O beyond reading the playbook and template text files next to this module. No clock, no network, no key.

A request has two parts, in this order:
  system   one text block: the playbook (frozen header + rules), carrying ONE cache_control marker (5-minute ephemeral). The
           playbook is the arm's prefix. L, F and P of a persona differ here once their playbooks differ.
  messages one user message: the cards (canonical JSON, one per line, byte-identical for every arm of a persona at one instant),
           then the nonce line. The nonce is sha256(arm|kind|card_sha|seq), so two arms never produce the same request bytes, and
           the caller's replay cache (keyed by request hash) can never hand one arm another arm's reply (v2's defect).
A restart rebuilds the same bytes from the same (arm, kind, cards, seq), so a replay finds the saved reply.

Cards are plain dicts. The builder refuses a card that is not plain JSON, has no string id or integer as_of_ms, repeats an id,
carries a name or symbol outside [A-Za-z0-9 ]{0,24}, or carries a non-null x (X features are null in this window). Those are
guards on the way out; the scanner (hv4_cards) is where the sanitising is done.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path

from tools import hv4_arms as arms
from tools.haiku_caller import DEFAULT_MODEL, DEFAULT_THINKING, BadArguments, build_body, canonical_bytes

PACKAGE_DIR = Path(__file__).resolve().parent
PLAYBOOK_DIR = PACKAGE_DIR / "hv4_playbooks"
TEMPLATE_DIR = PACKAGE_DIR / "hv4_templates"

HEADER_MARK = "=== FROZEN HEADER ==="
RULES_MARK = "=== RULES ==="
RULES_WORDS_V0_MAX = 600       # DESIGN section 5: playbook v0 is at most 600 words per persona (rules text)
RULES_WORDS_MAX = 1200         # DESIGN section 4: 2x v0; for the reviewer's change validator (PR hv4-review)

CACHE_MARKER = {"type": "ephemeral"}  # the default lifetime is 5 minutes; haiku_caller prices a missing ttl as 5m
NAME_OK = re.compile(r"[A-Za-z0-9 ]{0,24}")
NONCE_CHARS = 24               # hex characters of the nonce shown to the model (96 bits); the full sha256 is logged
BYTES_PER_TOKEN = 3.6          # ASSUMPTION for the estimate only; not measured. The shakedown measures real counts.

TEMPLATE_FILES = {"entry": "entry_user.txt", "recheck": "recheck_user.txt", "risk": "risk_user.txt"}
OUTPUT_TOKENS_ASSUMED = {"entry": 300, "recheck": 60, "risk": 150}  # DESIGN section 6 table


class PromptError(BadArguments):
    """A request this module refuses to build. No call is made."""


def sha256_hex(data: bytes | str) -> str:
    return hashlib.sha256(data.encode("utf-8") if isinstance(data, str) else data).hexdigest()


# ---------------------------------------------------------------- playbooks and templates

@dataclass(frozen=True)
class Playbook:
    name: str            # CON, BIG, BST or RISK
    version: str
    text: str            # exactly what is sent as the system block
    header: str          # the frozen header: the reviewer may not change it
    rules: str           # the editable rules text
    sha256: str
    header_sha256: str
    rules_words: int
    total_words: int


def playbook_from_text(name: str, version: str, text: str) -> Playbook:
    if not isinstance(text, str) or text.count(HEADER_MARK) != 1 or text.count(RULES_MARK) != 1:
        raise PromptError(f"playbook {name}/{version}: needs exactly one {HEADER_MARK} line and one {RULES_MARK} line")
    head_at, rules_at = text.index(HEADER_MARK), text.index(RULES_MARK)
    if head_at != 0 or rules_at < head_at:
        raise PromptError(f"playbook {name}/{version}: the frozen header must come first, then the rules")
    header = text[len(HEADER_MARK):rules_at].strip("\n")
    rules = text[rules_at + len(RULES_MARK):].strip("\n")
    return Playbook(name=name, version=version, text=text, header=header, rules=rules, sha256=sha256_hex(text),
                    header_sha256=sha256_hex(header), rules_words=len(rules.split()), total_words=len(text.split()))


def load_playbook(name: str, version: str = "v0", directory: Path | None = None) -> Playbook:
    if name not in (*arms.PERSONAS, arms.RISK_LEDGER) or not re.fullmatch(r"v[0-9]{1,3}", version):
        raise PromptError("unknown playbook name or version")
    path = (Path(directory) if directory is not None else PLAYBOOK_DIR) / f"{name}_{version}.txt"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        raise PromptError(f"playbook {name}/{version} cannot be read") from None
    return playbook_from_text(name, version, text)


@dataclass(frozen=True)
class Template:
    kind: str
    text: str
    sha256: str


def load_template(kind: str) -> Template:
    if kind not in TEMPLATE_FILES:
        raise PromptError(f"no user template for call kind {kind!r}")
    try:
        text = (TEMPLATE_DIR / TEMPLATE_FILES[kind]).read_text(encoding="utf-8")
    except OSError:
        raise PromptError(f"template {kind} cannot be read") from None
    return Template(kind, text, sha256_hex(text))


def render(template: Template, values: dict) -> str:
    """Fill {{KEY}} placeholders in ONE pass: text that arrives inside a value (a card) is never scanned for placeholders."""
    used = set()

    def sub(m):
        key = m.group(1)
        if key not in values:
            raise PromptError(f"template {template.kind}: no value for {{{{{key}}}}}")
        used.add(key)
        return values[key]

    out = re.sub(r"\{\{([A-Z]+)\}\}", sub, template.text)
    if used != set(values):
        raise PromptError(f"template {template.kind}: placeholders {sorted(used)} do not match values {sorted(values)}")
    return out.rstrip("\n")


# ---------------------------------------------------------------- hashes and the nonce

def card_sha256(card: dict) -> str:
    return sha256_hex(canonical_bytes(card))


def batch_sha256(card_shas) -> str:
    """The card hash that goes into the nonce: the card's own hash for one card, else the hash of the ordered hashes."""
    shas = list(card_shas)
    if not shas:
        raise PromptError("no cards")
    return shas[0] if len(shas) == 1 else sha256_hex("|".join(shas))


def nonce(arm: str, kind: str, card_sha: str, seq: int) -> str:
    """sha256(arm|kind|card_sha|seq) as hex. seq is the arm's own call counter for the UTC day, supplied by the runner, so a
    restart that replays the day rebuilds the same nonces."""
    if isinstance(seq, bool) or not isinstance(seq, int) or seq < 0:
        raise PromptError("seq must be an integer >= 0")
    return sha256_hex(f"{arm}|{kind}|{card_sha}|{seq}")


# ---------------------------------------------------------------- the request

@dataclass(frozen=True)
class Request:
    arm: str                       # the arm (or RISK) the call is for
    kind: str                      # entry, recheck or risk
    persona: str | None
    system: tuple                  # one text block with the cache marker
    user_text: str
    max_tokens: int
    ids: tuple[str, ...]           # the ids sent: a reply may only name these
    card_shas: tuple[str, ...]
    batch_sha: str
    nonce: str
    seq: int
    template_sha256: str
    playbook_name: str
    playbook_version: str
    playbook_sha256: str

    @property
    def ledger(self) -> str:
        return arms.ledger_name(self.arm, self.kind)

    def caller_kwargs(self) -> dict:
        """The keyword arguments for HaikuCaller.call. Thinking is left at the caller's default (disabled); no temperature."""
        return {"system": json.loads(json.dumps(self.system)), "messages": [{"role": "user", "content": self.user_text}],
                "max_tokens": self.max_tokens}

    def body(self, model: str = DEFAULT_MODEL) -> dict:
        kw = self.caller_kwargs()
        return build_body(model, kw["system"], kw["messages"], kw["max_tokens"], None, DEFAULT_THINKING)

    def request_sha256(self, model: str = DEFAULT_MODEL) -> str:
        return sha256_hex(canonical_bytes(self.body(model)))

    def token_estimate(self) -> dict:
        return estimate_tokens(self)


def estimate_tokens(req: Request) -> dict:
    """ASSUMED token counts from text size (BYTES_PER_TOKEN), for budgeting. cached = the system block, new = the user message,
    out = the DESIGN's assumed output size for the kind. Not measured: the shakedown replaces it with usage fields."""
    cached = math.ceil(len(req.system[0]["text"].encode("utf-8")) / BYTES_PER_TOKEN)
    new = math.ceil(len(req.user_text.encode("utf-8")) / BYTES_PER_TOKEN)
    return {"cached": cached, "new": new, "out": OUTPUT_TOKENS_ASSUMED[req.kind], "max_tokens": req.max_tokens}


def check_cards(cards, *, max_cards: int) -> list[dict]:
    if not isinstance(cards, (list, tuple)) or not cards:
        raise PromptError("cards must be a non-empty list")
    if len(cards) > max_cards:
        raise PromptError(f"{len(cards)} cards in one call; the limit is {max_cards}")
    seen = set()
    for card in cards:
        if not isinstance(card, dict):
            raise PromptError("every card must be an object")
        cid = card.get("id")
        if not isinstance(cid, str) or not cid or len(cid) > 64 or not re.fullmatch(r"[A-Za-z0-9_\-]+", cid):
            raise PromptError("every card needs a string id of 1 to 64 characters [A-Za-z0-9_-]")
        if cid in seen:
            raise PromptError("two cards share an id")
        seen.add(cid)
        am = card.get("as_of_ms")
        if isinstance(am, bool) or not isinstance(am, int) or am < 0:
            raise PromptError("every card needs an integer as_of_ms")
        if card.get("x") is not None:
            raise PromptError("x must be null in this window")
        _check_names(card)
        canonical_bytes(card)  # plain JSON, finite numbers
    return list(cards)


def _check_names(node) -> None:
    stack = [node]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            for k, v in cur.items():
                if k in ("name", "symbol") and isinstance(v, str) and not NAME_OK.fullmatch(v):
                    raise PromptError("name and symbol must match [A-Za-z0-9 ]{0,24}")
                stack.append(v)
        elif isinstance(cur, list):
            stack.extend(cur)


def _lines(items) -> str:
    return "\n".join(canonical_bytes(i).decode("utf-8") for i in items)


def _system(playbook: Playbook) -> tuple:
    return ({"type": "text", "text": playbook.text, "cache_control": dict(CACHE_MARKER)},)


def _build(arm: str, kind: str, persona, playbook: Playbook, cards: list[dict], seq: int, extra: dict, items=None) -> Request:
    template = load_template(kind)
    card_shas = tuple(card_sha256(c) for c in cards)
    batch = batch_sha256(card_shas + tuple(card_sha256(p) for p in (items or ())))
    n = nonce(arm, kind, batch, seq)
    values = {"N": str(len(items) if items is not None else len(cards)), "CARDS": _lines(cards), "NONCE": n[:NONCE_CHARS], **extra}
    user_text = render(template, values)
    ids = tuple(c["id"] for c in (items if items is not None else cards))
    req = Request(arm=arm, kind=kind, persona=persona, system=_system(playbook), user_text=user_text,
                  max_tokens=arms.MAX_TOKENS[kind], ids=ids, card_shas=card_shas, batch_sha=batch, nonce=n, seq=seq,
                  template_sha256=template.sha256, playbook_name=playbook.name, playbook_version=playbook.version,
                  playbook_sha256=playbook.sha256)
    req.body()  # the caller's own validation (plain JSON, text blocks, cache_control shape) before anything is reserved
    return req


def build_entry_request(arm: str, playbook: Playbook, cards, seq: int, *, max_cards: int = arms.ENTRY_MAX_CARDS) -> Request:
    spec = arms.ARMS.get(arm)
    if spec is None or not spec.llm:
        raise PromptError(f"{arm!r} is not an LLM arm")
    if playbook.name != spec.persona:
        raise PromptError(f"arm {arm} needs a {spec.persona} playbook, got {playbook.name}")
    cards = check_cards(cards, max_cards=min(max_cards, arms.ENTRY_MAX_CARDS))
    return _build(arm, "entry", spec.persona, playbook, cards, seq, {})


def build_recheck_request(arm: str, playbook: Playbook, positions, cards, seq: int) -> Request:
    """positions: one dict per open position, id first, with the code-computed numbers (stop_pct, target_pct, mark_ret_pct,
    as_of_ms). cards: the fresh card for each position, same ids, same order."""
    spec = arms.ARMS.get(arm)
    if spec is None or not spec.llm or arms.PERSONA_SPECS[spec.persona].recheck_minutes is None:
        raise PromptError(f"{arm!r} has no LLM re-check")
    if playbook.name != spec.persona:
        raise PromptError(f"arm {arm} needs a {spec.persona} playbook, got {playbook.name}")
    cards = check_cards(cards, max_cards=arms.ENTRY_MAX_CARDS)
    if not isinstance(positions, (list, tuple)) or [p.get("id") if isinstance(p, dict) else None for p in positions] != [c["id"] for c in cards]:
        raise PromptError("positions and cards must have the same ids in the same order")
    for p in positions:
        canonical_bytes(p)
    return _build(arm, "recheck", spec.persona, playbook, cards, seq, {"POSITIONS": _lines(positions)}, items=list(positions))


def build_risk_request(playbook: Playbook, cards, seq: int, *, arm: str = arms.RISK_LEDGER) -> Request:
    if playbook.name != arms.RISK_LEDGER or arm != arms.RISK_LEDGER:
        raise PromptError("the risk call uses the RISK playbook and the RISK ledger")
    cards = check_cards(cards, max_cards=1)  # DESIGN section 6: one pool per risk call
    return _build(arm, "risk", None, playbook, cards, seq, {})
