"""slowbot decision step: one Haiku call per candidate, BUY or SKIP, schema-validated.

PAPER only. The Anthropic call goes through tools.haiku_caller.HaikuCaller (hard USD ledger, replay cache). This module
never reads a key. The daily cap is enforced by one ledger file per UTC day with budget DAILY_USD_CAP.
A parse or schema error, or an unavailable caller, yields action SKIP with decision_error set, and is logged.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from tools.haiku_caller import DEFAULT_MODEL, HaikuCaller, HaikuCallerError

MODEL = "claude-haiku-5-5"
assert MODEL == DEFAULT_MODEL
DAILY_USD_CAP = 2.0
MAX_TOKENS = 300
TEMPERATURE = 0
PROMPT_PATH = Path(__file__).parent / "prompts" / "decide_v0.txt"
ACTIONS = ("BUY", "SKIP")
RISKS = ("rug", "dump", "none")
MAX_CATALYST_WORDS = 20
MAX_X_CONTEXT = 5
MAX_X_CHARS = 280
FEATURE_KEYS = ("symbol", "name", "age_min", "liq_usd", "mcap_usd", "vol_5m", "vol_1h", "vol_6h", "vol_24h", "txns_1h",
                "buys_1h", "sells_1h", "chg_5m", "chg_1h", "chg_6h", "chg_24h", "top10_share", "has_site", "has_x",
                "rule_hit", "venue")


def load_prompt(path: Path = PROMPT_PATH) -> tuple[str, str]:
    data = Path(path).read_bytes()
    return data.decode("utf-8"), hashlib.sha256(data).hexdigest()


def clean_x_context(x_context) -> list[str] | None:
    if not x_context or not isinstance(x_context, (list, tuple)):
        return None
    out = [s.strip()[:MAX_X_CHARS] for s in x_context if isinstance(s, str) and s.strip()]
    return out[:MAX_X_CONTEXT] or None


def build_card(candidate: dict, x_context=None) -> dict:
    """The fixed-field candidate card. Quote, arm and mint are not shown to the model."""
    card = {k: candidate.get(k) for k in FEATURE_KEYS}
    xc = clean_x_context(x_context)
    if xc is not None:
        card["x_context"] = xc
    return card


def validate_decision(text: str) -> dict:
    """Parse and validate the model text. Raises ValueError with a short reason."""
    s = text.strip()
    if s.startswith("```"):
        s = s.strip("`")
        if s[:4].lower() == "json":
            s = s[4:]
        s = s.strip()
    try:
        obj = json.loads(s)
    except ValueError:
        raise ValueError("not_json") from None
    if not isinstance(obj, dict):
        raise ValueError("not_object")
    if set(obj) != {"action", "conviction", "catalyst", "risk"}:
        raise ValueError("bad_keys")
    if obj["action"] not in ACTIONS:
        raise ValueError("bad_action")
    c = obj["conviction"]
    if isinstance(c, bool) or not isinstance(c, int) or not 1 <= c <= 5:
        raise ValueError("bad_conviction")
    cat = obj["catalyst"]
    if not isinstance(cat, str) or len(cat.split()) > MAX_CATALYST_WORDS:
        raise ValueError("bad_catalyst")
    if obj["risk"] not in RISKS:
        raise ValueError("bad_risk")
    return {"action": obj["action"], "conviction": c, "catalyst": cat, "risk": obj["risk"]}


def _utc_day(now=None) -> str:
    return (now or datetime.now(timezone.utc)).strftime("%Y-%m-%d")


def daily_caller(ledger_dir, now=None, *, save_dir=None, replay=False, budget_usd: float = DAILY_USD_CAP) -> HaikuCaller:
    """A HaikuCaller on today's ledger file. A new UTC day means a new file and a fresh daily cap."""
    ledger = Path(ledger_dir) / f"slowbot-haiku-ledger-{_utc_day(now)}.jsonl"
    return HaikuCaller(ledger, budget_usd, save_dir=save_dir, model=MODEL, replay=replay)


def decide(candidate: dict, caller, *, x_context=None, prompt_path: Path = PROMPT_PATH, log_path=None, now=None) -> dict:
    """Return the log row (also appended to log_path as JSONL). row["action"] is BUY or SKIP; never raises on model or budget errors."""
    system, prompt_sha = load_prompt(prompt_path)
    card = build_card(candidate, x_context)
    user = json.dumps(card, sort_keys=True, separators=(",", ":"))
    messages = [{"role": "user", "content": user}]
    ts = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
    row = {"ts_utc": ts, "mint": candidate.get("mint"), "pool": candidate.get("pool"), "arm": candidate.get("arm"),
           "model": MODEL, "prompt_sha256": prompt_sha, "temperature": TEMPERATURE, "has_x_context": "x_context" in card,
           "request": {"system_sha256": prompt_sha, "messages": messages, "max_tokens": MAX_TOKENS},
           "response_text": None, "action": "SKIP", "conviction": None, "catalyst": None, "risk": None,
           "decision_error": None, "usage": None, "cost_usd": 0.0, "request_sha256": None, "replayed": None}
    try:
        res = caller.call(messages=messages, system=system, max_tokens=MAX_TOKENS, temperature=TEMPERATURE)
    except HaikuCallerError as e:
        row["decision_error"] = f"caller_{type(e).__name__}"
    else:
        row.update(response_text=res.text, usage=res.usage, cost_usd=res.cost_usd, request_sha256=res.request_sha256,
                   replayed=res.replayed)
        try:
            row.update(validate_decision(res.text))
        except ValueError as e:
            row["decision_error"] = f"parse_{e}"
    if log_path is not None:
        append_jsonl(log_path, row)
    return row


def append_jsonl(path, row: dict) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(p, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.write(fd, (json.dumps(row, sort_keys=True) + "\n").encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
