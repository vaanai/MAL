"""One Haiku call per (token, variant): validate the JSON, log usage including cache fields. Never raises; bad output = SKIP row.

`output_config.effort` is NOT set: tools.haiku_caller.build_body has a closed key set (REQUEST_KEYS) with no pass-through, and
haiku_caller is not edited here. Thinking stays at the caller's default (disabled); `temperature` is never sent.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from tools.haiku_caller import DEFAULT_MODEL, BudgetExceeded, HaikuCaller
from tools.gradscore import prompt as pr

MODEL = "claude-haiku-5-5"
assert MODEL == DEFAULT_MODEL
KEYS = {"runner_prob_pct", "narrative", "rug_risk", "reason"}
MAX_REASON_WORDS = 20


def validate(text: str) -> dict:
    """Raises ValueError(short_reason) on anything off-schema."""
    s = (text or "").strip()
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
    if set(obj) != KEYS:
        raise ValueError("bad_keys")
    p = obj["runner_prob_pct"]
    if isinstance(p, bool) or not isinstance(p, (int, float)) or p != p or not 0 <= p <= 100:
        raise ValueError("bad_runner_prob_pct")
    for k in ("narrative", "rug_risk"):
        v = obj[k]
        if isinstance(v, bool) or not isinstance(v, int) or not 1 <= v <= 5:
            raise ValueError("bad_" + k)
    r = obj["reason"]
    if not isinstance(r, str) or len(r.split()) > MAX_REASON_WORDS:
        raise ValueError("bad_reason")
    return {"runner_prob_pct": p, "narrative": obj["narrative"], "rug_risk": obj["rug_risk"], "reason": r}


def _utc_day(now):
    return now.strftime("%Y-%m-%d")


class DailyCaller:
    """HaikuCaller per UTC day (one ledger file per day, so the cap is per day)."""

    def __init__(self, ledger_dir, budget_usd, now_fn=lambda: datetime.now(timezone.utc), save_dir=None):
        self.ledger_dir, self.budget_usd, self.now_fn, self.save_dir = ledger_dir, budget_usd, now_fn, save_dir
        self._day = self._caller = None

    def call(self, **kw):
        now = self.now_fn()
        if _utc_day(now) != self._day:
            self._caller = HaikuCaller(Path(self.ledger_dir) / f"gradscore-haiku-ledger-{_utc_day(now)}.jsonl", self.budget_usd,
                                       save_dir=self.save_dir, model=MODEL)
            self._day = _utc_day(now)
        return self._caller.call(**kw)


def score_one(caller, variant: str, card_text: str, max_tokens: int = 200) -> dict:
    """Returns a row fragment: action SCORED|SKIP, skip_reason, scores, usage, cost. cap=True when the daily cap stopped it."""
    row = {"variant": variant, "model": MODEL, "prefix_sha256": pr.prefix_sha256(variant), "action": "SKIP", "skip_reason": None,
           "runner_prob_pct": None, "narrative": None, "rug_risk": None, "reason": None, "response_text": None,
           "usage": None, "cost_usd": 0.0, "request_sha256": None, "cap": False}
    try:
        res = caller.call(system=pr.system_blocks(variant), messages=[{"role": "user", "content": card_text}], max_tokens=max_tokens,
                          temperature=None)
    except BudgetExceeded:
        row.update(skip_reason="budget_cap", cap=True)
        return row
    except Exception as e:  # API error, missing key, transport surprise: class name only
        row["skip_reason"] = f"caller_{type(e).__name__}"
        return row
    u = res.usage or {}
    row.update(response_text=res.text, usage=u, cost_usd=res.cost_usd, request_sha256=res.request_sha256,
               cache_read_input_tokens=u.get("cache_read_input_tokens"), cache_creation_input_tokens=u.get("cache_creation_input_tokens"))
    try:
        row.update(validate(res.text))
        row["action"] = "SCORED"
    except ValueError as e:
        row["skip_reason"] = f"parse_{e}"
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
