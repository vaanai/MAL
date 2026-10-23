"""hv5_prompts: HV5 playbooks and user templates, and request builders. A NEW module; tools/hv4_prompts.py is imported, never changed.

The HV5 texts live in tools/hv5_playbooks/ and tools/hv5_templates/. They differ from HV4's in framing only (see the PR body):
SKIP is no longer "the default answer" or the first, shortest example; "most cards should be SKIP / more than 4 in 10 is too eager"
is replaced by a symmetric statement; why is asked at <=20 words (the validator's soft limit is 25 / 30). The base-rate facts, the
measured directions, the vetoes and every key the validator checks are unchanged. No text here is tuned on outcomes.
PAPER research only. No clock, no network, no key.
"""
from __future__ import annotations

from pathlib import Path

from tools import hv4_arms as arms
from tools import hv4_prompts as p4
from tools.hv4_prompts import PromptError, Request, Template, Playbook, render, sha256_hex  # noqa: F401

PACKAGE_DIR = Path(__file__).resolve().parent
PLAYBOOK_DIR = PACKAGE_DIR / "hv5_playbooks"
TEMPLATE_DIR = PACKAGE_DIR / "hv5_templates"


def load_playbook(name: str, version: str = "v0") -> Playbook:
    return p4.load_playbook(name, version, directory=PLAYBOOK_DIR)


def load_template(kind: str) -> Template:
    if kind not in p4.TEMPLATE_FILES:
        raise PromptError(f"no user template for call kind {kind!r}")
    try:
        text = (TEMPLATE_DIR / p4.TEMPLATE_FILES[kind]).read_text(encoding="utf-8")
    except OSError:
        raise PromptError(f"template {kind} cannot be read") from None
    return Template(kind, text, sha256_hex(text))


def _build(arm, kind, persona, playbook, cards, seq, extra, items=None) -> Request:
    template = load_template(kind)
    card_shas = tuple(p4.card_sha256(c) for c in cards)
    batch = p4.batch_sha256(card_shas + tuple(p4.card_sha256(p) for p in (items or ())))
    n = p4.nonce(arm, kind, batch, seq)
    values = {"N": str(len(items) if items is not None else len(cards)), "CARDS": p4._lines(cards), "NONCE": n[:p4.NONCE_CHARS], **extra}
    user_text = render(template, values)
    ids = tuple(c["id"] for c in (items if items is not None else cards))
    req = Request(arm=arm, kind=kind, persona=persona, system=p4._system(playbook), user_text=user_text,
                  max_tokens=arms.MAX_TOKENS[kind], ids=ids, card_shas=card_shas, batch_sha=batch, nonce=n, seq=seq,
                  template_sha256=template.sha256, playbook_name=playbook.name, playbook_version=playbook.version,
                  playbook_sha256=playbook.sha256)
    req.body()
    return req


def build_entry_request(arm: str, playbook: Playbook, cards, seq: int, *, max_cards: int = arms.ENTRY_MAX_CARDS) -> Request:
    spec = arms.ARMS.get(arm)
    if spec is None or not spec.llm:
        raise PromptError(f"{arm!r} is not an LLM arm")
    if playbook.name != spec.persona:
        raise PromptError(f"arm {arm} needs a {spec.persona} playbook, got {playbook.name}")
    cards = p4.check_cards(cards, max_cards=min(max_cards, arms.ENTRY_MAX_CARDS))
    return _build(arm, "entry", spec.persona, playbook, cards, seq, {})


def build_risk_request(playbook: Playbook, cards, seq: int, *, arm: str = arms.RISK_LEDGER) -> Request:
    if playbook.name != arms.RISK_LEDGER or arm != arms.RISK_LEDGER:
        raise PromptError("the risk call uses the RISK playbook and the RISK ledger")
    cards = p4.check_cards(cards, max_cards=1)
    return _build(arm, "risk", None, playbook, cards, seq, {})
