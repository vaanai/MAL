"""hv4_model: the model interface of Haiku trader v4, a StubModel for tests, and CallerModel, the one real path.

Serves /data/mal/hunt-1008/push-1016/hunt20/haiku-v4/DESIGN.md sections 3, 6 and 7.
PAPER research only.

Every real call goes through tools.haiku_caller.HaikuCaller (budget-stopped, reserve then settle, replay cache). This module has no
HTTP client, never reads the API key (the caller reads ANTHROPIC_API_KEY from the environment at the moment of a call), and never
copies server text: a failure becomes an error code that is a class name.

Both models take the same gate first (BudgetGuard.preflight: month guard, group halt, day cap), so a stubbed run exercises the same
budget path as a live one. A model reply with an error, a refusal or unreadable text is an abstain downstream (hv4_validate).
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Protocol

from tools.haiku_caller import DEFAULT_MODEL, BudgetExceeded, HaikuCaller, HaikuCallerError, Ledger, sha256_hex
from tools.hv4_budget import BudgetGuard
from tools.hv4_prompts import Request

REFUSAL_STOP_REASON = "refusal"


@dataclass(frozen=True)
class ModelReply:
    text: str = ""
    stop_reason: str | None = None
    error: str | None = None            # None when the model produced output; else a short code, never server text
    cost_usd: float = 0.0
    latency_ms: int = 0
    request_sha256: str | None = None
    response_sha256: str | None = None
    usage: dict = field(default_factory=dict)
    replayed: bool = False
    model: str = DEFAULT_MODEL

    @property
    def refused(self) -> bool:
        return self.stop_reason == REFUSAL_STOP_REASON


class Model(Protocol):
    model: str

    def complete(self, req: Request, now_ms: int) -> ModelReply: ...


def gate(guard: BudgetGuard | None, req: Request, now_ms: int):
    """(preflight, None) when the call may go ahead, else (preflight or None, an error reply). No guard: no gate."""
    if guard is None:
        return None, None
    pf = guard.preflight(req.arm, req.kind, now_ms)
    if not pf.allowed:
        return pf, ModelReply(error=pf.reason)
    return pf, None


class CallerModel:
    """The real path: one HaikuCaller per call on the arm's day ledger, with the effective budget from preflight."""

    def __init__(self, guard: BudgetGuard, *, caller_cls=HaikuCaller, sleep=time.sleep):
        self.guard = guard
        self.model = DEFAULT_MODEL
        self._caller_cls = caller_cls
        self._sleep = sleep

    def complete(self, req: Request, now_ms: int) -> ModelReply:
        pf, denied = gate(self.guard, req, now_ms)
        if denied is not None:
            return denied
        try:
            caller = self._caller_cls(pf.ledger_path, pf.budget_usd, save_dir=pf.cache_dir, replay=True, sleep=self._sleep)
            res = caller.call(**req.caller_kwargs())
        except BudgetExceeded:
            self.guard.record_refusal(pf, now_ms)  # the worst case did not fit: the whole group halts, not just this arm
            return ModelReply(error="budget_exceeded")
        except HaikuCallerError as e:
            return ModelReply(error=type(e).__name__)
        stop = res.response.get("stop_reason") if isinstance(res.response, dict) else None
        return ModelReply(text=res.text, stop_reason=stop if isinstance(stop, str) and len(stop) <= 64 else None,
                          cost_usd=res.cost_usd, latency_ms=res.latency_ms, request_sha256=res.request_sha256,
                          response_sha256=res.response_sha256, usage=dict(res.usage), replayed=res.replayed)


# ---------------------------------------------------------------- the stub

def default_reply(req: Request) -> str:
    """What the stub says when nothing is scripted: abstain on everything (SKIP / HOLD / a plain pass at 0.25)."""
    if req.kind == "entry":
        return json.dumps([{"id": i, "action": "SKIP"} for i in req.ids])
    if req.kind == "recheck":
        return json.dumps([{"id": i, "action": "HOLD"} for i in req.ids])
    return json.dumps([{"id": i, "grade": "pass", "flags": [], "max_size": 0.25, "why": "stub"} for i in req.ids])


class StubModel:
    """A scripted model for tests. Makes no network call and needs no key.

    script: a list consumed in order, or a callable (Request) -> str | ModelReply. Each item is the reply text (str), a ModelReply,
    or a callable taking the Request. When the list runs out, default_reply(req) is used. With a guard, every call goes through
    the same preflight as CallerModel, and a call that costs money (cost_usd > 0) is appended to the arm's day ledger as a plain
    cost row, so the day cap, the month guard and the group halt behave as they do live. Every Request is kept in .calls."""

    def __init__(self, script=None, *, guard: BudgetGuard | None = None, cost_usd: float = 0.0, latency_ms: int = 50):
        self.model = DEFAULT_MODEL
        self.guard = guard
        self.cost_usd = float(cost_usd)
        self.latency_ms = latency_ms
        self.calls: list[Request] = []
        self._script = script
        self._i = 0

    def _next(self, req: Request):
        s = self._script
        if callable(s):
            return s(req)
        if s is not None and self._i < len(s):
            item = s[self._i]
            self._i += 1
            return item(req) if callable(item) else item
        return default_reply(req)

    def complete(self, req: Request, now_ms: int) -> ModelReply:
        self.calls.append(req)
        pf, denied = gate(self.guard, req, now_ms)
        if denied is not None:
            return denied
        item = self._next(req)
        rsha = req.request_sha256()
        if pf is not None and self.cost_usd > 0:
            led = Ledger(pf.ledger_path)
            with led.locked(exclusive=True):
                fits = led.spent() + self.cost_usd <= pf.budget_usd
                if fits:
                    fd = led.open_append()
                    try:
                        Ledger.append_row(fd, {"cost_usd": self.cost_usd, "ts_ms": now_ms, "source": "stub", "request_sha256": rsha})
                    finally:
                        os.close(fd)
            if not fits:  # outside the lock: the group's spend is read under a shared flock on the same file
                self.guard.record_refusal(pf, now_ms)
                return ModelReply(error="budget_exceeded")
        if isinstance(item, ModelReply):
            return item
        text = item if isinstance(item, str) else ""
        return ModelReply(text=text, stop_reason="end_turn", cost_usd=self.cost_usd, latency_ms=self.latency_ms,
                          request_sha256=rsha, response_sha256=sha256_hex(text.encode("utf-8")),
                          usage={"input_tokens": 0, "output_tokens": 0})


def refusal_reply(text: str = "") -> ModelReply:
    """A reply the way the API reports a refusal: stop_reason 'refusal'. For tests."""
    return ModelReply(text=text, stop_reason=REFUSAL_STOP_REASON)
