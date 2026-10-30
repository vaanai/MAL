#!/usr/bin/env python3
"""hv7_smoke: the real-model smoke for HV7 (DEC-031 Am.6 item 1.3; pack ARTIFACTS/lab/hv7-registration-1027.md section 3d).

PAPER only. Runs the FINAL-sha runner (`tools.hv7_runner.Hv7Runner`, imported from `--repo`, a clean checkout at the final sha) with the REAL caller
(claude-haiku-5-5 through tools.hv6_agents.CallerModel -> tools.haiku_caller; the key is read by the caller from ANTHROPIC_API_KEY at call time, which
the wrapper takes from /var/lib/mal/anthropic/anthropic.env into the child's environment only; this file never reads, prints or stores its value) and the
live arena tape (read only, through the frozen runner and card code). It edits no frozen file (tools/hv7_*.py at the final sha).

HOURLY DESIGN (no frozen code touched, no compression). `Hv7Runner.tick` fires the screen only when `T % HOUR_MS == T_D_OFFSET_MS` and Hv7Config has no period
parameter, so this tool does not call `tick`; it drives the runner's public methods in the order `tick` uses (`sim_now`, `poll_to`, `exits`, `watch_tick`, then
`screen(T)` at a period instant `t_d`, then `enter(T)` at `t_d + 45 s`). The period instants are the runner's real ones: H:02:00 UTC, one per hour, 31 of them (30 hours
from the first t_d to the last, about 32-33 h wall). The first t_d is the first H:02 that falls at least WARMUP (8 min) + one prefetch window (14 min) + 1 h after the
start: the first window is the cold fill and is NOT screened (a cold store cannot hold the top pools; sim: 67 of 75 cards refused at that screen), the next is. Request bytes are the real RANK_v0 prompt (`build_rank_request`), the call is the
runner's own `_ask`, the verdict its own `_resolve_l`.

The chain source is the runner's own `hv7_runner.live_chain_source(root)` (HV4Chain + policy-v2 prefetcher gated to the window H:48-H:02, 500 calls/h), state files under
the smoke dir; the prefetcher is started as `main` starts it (`chain.prefetch.start()`) and fed by the runner's own `watch_tick` (WATCH_MAX 200). Nothing about the
prefetch regime is overridden: it is the live regime. DISCLOSURE: the smoke's card-refusal share is measured in the live regime at the live instants, but over 31 hourly
screens on whatever the live tape holds; live H:02 fact freshness is also shown by the synthetic tools/test_hv7_prefetch_phase.py (no RPC).

WRITES ONLY under <HV7_ROOT>/smoke/<id>/ : root/ (decision log, RANK-L cost ledger, replay cache, chain state), engine-ledger/, smoke-registry.json,
report.json. The production root, the real registry (/data/mal/arena/bots.json) and every HV4/HV6/Q50 path are never touched. The engine ledger (fills,
prices) is never opened; the decision rows are read through a whitelist projection of the L rank row and the SCAN period row (counts and lengths only);
the cost ledger is read through tools.haiku_caller.ledger_summary. No pool id, mint, price, fill or P&L is read into the report.

SPEND: a hard stop at SPEND_CAP_USD (0.10) over every day file of the RANK-L cost ledger, checked before each call (a wrapper around the model); the
frozen Hv7Guard day cap is lowered to the same figure (a cap can be lowered, never raised).

VERDICT (pre-declared in the pack, section 3d; a FAIL is fixed by code at a new sha, never by tuning a limit):
  FAIL if FALLBACK calls > 5% of ranker calls, or the p95 of the call latency > 8,000 ms, or PINNED-CLASS card refusals > 5% of cards attempted (built + refused), or any
  run error (a raised exception, a hook error, a model exception, a cost mismatch, an unreadable ledger), or spend > 0.10 USD.
  INCOMPLETE if fewer than 30 error-free ranker calls, or the span from the first t_d to the last t_e is under 2 h (7,200 s). Otherwise PASS.
Exit 0 PASS, 1 FAIL or INCOMPLETE, 2 refused before any call.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

HV7_ROOT = "/data/mal/arena/haiku-v7"
SMOKE_PARENT = HV7_ROOT + "/smoke"
SHA_RE = "0123456789abcdef"
SPEND_CAP_USD = 0.10
FALLBACK_FAIL_FRAC = 0.05
CALL_P95_FAIL_MS = 8_000
REFUSAL_FAIL_FRAC = 0.05
MIN_OK_CALLS = 30
MIN_SPAN_MS = 2 * 3_600_000
PERIOD_MS = 3_600_000
PREFETCH_LEAD_MS = 14 * 60_000       # the runner's window length [H:48, H:02); a test pins it to hv7_runner's constants
T_D_OFFSET_MS = 2 * 60_000           # pinned to hv7_runner.T_D_OFFSET_MS by a test
PERIODS = 31
WARMUP_MS = 8 * 60_000
TAIL_MS = 60_000
WATCH_EVERY_MS = 60_000
RPC_CALLS_PER_H = 1_000
ERROR_COUNTERS = ("model_exception", "cost_mismatch", "ledger_unreadable")
REFUSAL_COUNTERS = ("card_refused", "chain_none", "leakage_refused")     # every refusal: the denominator side (cards attempted = built + refused)
# PINNED CLASS (pack section 3d: "card refusals (as_of, chain stale or unavailable, holders stale)"; HV6's as_of_refusal + chain_stale + chain_unavailable +
# holders_stale; QP on PR #1071, comment 6488548141, B1/B2(a)). Counted toward the 5% limit: chain_none (chain unavailable), leakage_refused (as_of), and every
# card_refused card that is NOT shown to be refused by hard vetoes alone. The HV4 hard vetoes (DESIGN section 3: "a vetoed pool is replaced by the next by
# f.vol_60m", a normal screen step and a market property) and the Lane G filters are report-only. FAIL-CLOSED: a reason this list does not know, a reason key the
# runner may have cut (hv7_runner truncates the joined reasons at 60 characters, so a pinned reason can be hidden at the end), and card_refused counts with no
# reason breakdown all count toward the limit. Smoke #1779 under this rule: 3 of 285 (chain_none 3; its 34 card_refused were all hard vetoes).
PINNED_REFUSAL_COUNTERS = ("chain_none", "leakage_refused")
HARD_VETO_REASONS = frozenset({
    # hv4_cards.code_vetoes, DESIGN section 2 hard vetoes 1-7
    "mint_authority_set", "freeze_authority_set", "token_program_unknown", "token_ext_on_classic_program", "token_ext_banned", "transfer_fee_unknown",
    "transfer_fee", "default_account_state_unknown", "default_account_frozen", "mayhem_young", "mayhem_unknown_young", "quote_below_floor",
    "impact_cap_breached", "vstar_unknown", "vstar_wash", "sell_route", "quote_15m_unknown", "quote_15m_stamp_bad", "quote_drop_15m", "quote_not_wsol",
    # hv4_cards.filters, the Lane G filters (a market property of the pool, never a stale or missing fact)
    "venue_pumpswap", "quote_wsol", "v_missing", "v_band", "age_min", "age_max", "quote_floor", "impact_025", "prints_15m", "signers_15m", "vol_60m", "last_print",
    "bad_pool_id",
})
# The pinned-class reasons hv4_cards can give (stamps, staleness, missing chain facts). Only used to flag a reason that is in neither list; any card that is not a
# hard veto alone counts toward the limit whether its reason is listed here or not.
PINNED_REASONS_KNOWN = frozenset({"as_of_slot_invalid", "as_of_slot_missing", "chain_stale", "holders_stale", "holders_stamp_invalid", "state_stale", "state_fresh"})
PINNED_REASON_PREFIXES = ("chain_stamp_invalid:", "chain_missing:", "chain_invalid:")
RUNNER_REASON_KEY_CAP = 60              # hv7_runner: "card_refused:" + ",".join(sorted(reasons))[:60]


def split_card_refusals(cnt: dict[str, Any]) -> dict[str, int]:
    """card_refused split into hard vetoes (report-only) and the pinned class (counted), fail closed. Counts only; no pool is named."""
    total = int(cnt.get("card_refused", 0))
    hard = cut = unknown = 0
    for k, v in cnt.items():
        if not k.startswith("card_refused:") or not _num(v):
            continue
        reasons = k[len("card_refused:"):]
        if len(reasons) >= RUNNER_REASON_KEY_CAP:
            cut += int(v)                                   # may be truncated: counted
            continue
        toks = [t for t in reasons.split(",") if t]
        if toks and all(t in HARD_VETO_REASONS for t in toks):
            hard += int(v)
        elif any(t not in HARD_VETO_REASONS and t not in PINNED_REASONS_KNOWN and not t.startswith(PINNED_REASON_PREFIXES) for t in toks):
            unknown += int(v)                               # counted (fail closed); shown so QP can classify it
    hard = min(hard, total)
    pinned_cards = total - hard                             # everything not shown to be a hard veto alone
    pinned = pinned_cards + sum(int(cnt.get(k, 0)) for k in PINNED_REFUSAL_COUNTERS)
    return {"card_refused": total, "hard_veto_cards": hard, "pinned_cards": pinned_cards, "pinned": pinned, "keys_maybe_cut": cut, "unclassified": unknown}



class Refused(Exception):
    """Argument or environment refusal; carries a fixed code only."""


def _pct(vals: list[float], q: float) -> float | None:
    if not vals:
        return None
    s = sorted(vals)
    return s[min(len(s) - 1, int(q * len(s)))]


def _rows(root: Path, name: str):
    d = root / "decisions" / name
    for f in sorted(d.glob("*.jsonl")) if d.is_dir() else []:
        for line in f.read_bytes().splitlines():
            if line.strip():
                yield json.loads(line)


def _num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _num_only(d: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if isinstance(d, dict):
        for k, v in d.items():
            if _num(v):
                out[str(k)] = v
            elif isinstance(v, dict):
                sub = _num_only(v)
                if sub:
                    out[str(k)] = sub
    return out


def _err_class(e: Any) -> str:
    """A model_error string reduced to a short class label (letters, digits, underscore, colon), never a message."""
    s = str(e)[:48]
    return "".join(ch if (ch.isalnum() or ch in "_:") else "_" for ch in s)


def report(root: str | os.PathLike, *, counters: dict[str, Any] | None = None, run_error: str | None = None, window_ms: tuple[int, int] | None = None,
           cap_usd: float = SPEND_CAP_USD, spend_stop_hits: int = 0, chain_credits: Any = None, calls_h_samples: list[int] | None = None) -> dict[str, Any]:
    """Counts-only report over a smoke root. L rank rows and SCAN period rows through a whitelist projection (never shown, order, pick); the cost ledgers by their own totals."""
    root = Path(root)
    n_calls = ok_valid = fallbacks = refusals = errfree = late = cut = replayed = 0
    lat: list[float] = []
    errs: dict[str, int] = {}
    fb_reasons: dict[str, int] = {}
    for r in _rows(root, "L"):
        if r.get("kind") != "rank":
            continue
        n_calls += 1
        ok_valid += int(r.get("ok") is True)
        fallbacks += int(r.get("fallback") is True)
        refusals += int(r.get("refused") is True)
        late += int(r.get("late") is True)
        cut += int(r.get("cut_off") is True)
        replayed += int(r.get("replayed") is True)
        me = r.get("model_error")
        if me is not None:
            k = _err_class(me)
            errs[k] = errs.get(k, 0) + 1
        fr = r.get("fallback_reason")
        if r.get("fallback") is True and isinstance(fr, str):
            k = _err_class(fr)
            fb_reasons[k] = fb_reasons.get(k, 0) + 1
        if me is None and r.get("refused") is not True and r.get("late") is not True and r.get("cut_off") is not True:
            errfree += 1
        if _num(r.get("latency_ms")):
            lat.append(float(r["latency_ms"]))
    periods = no_trade = built = excluded = 0
    for r in _rows(root, "SCAN"):
        k = r.get("kind")
        if k == "period":
            periods += 1
            no_trade += int(bool(r.get("no_trade")))
            built += len(r.get("card_shas") or ()) + len(r.get("vetoed") or ())
        elif k == "period_excluded":
            excluded += 1
    cnt = _num_only(counters or {})
    refused_cards = sum(int(cnt.get(k, 0)) for k in REFUSAL_COUNTERS)        # every refusal (denominator; report-only share below)
    split = split_card_refusals(cnt)
    pinned_refusals = split["pinned"]
    reason_counts = {k[len("card_refused:"):][:60]: v for k, v in sorted(cnt.items()) if k.startswith("card_refused:")}
    attempted = built + refused_cards
    refusal_frac = pinned_refusals / attempted if attempted else 0.0          # the pinned limit: pinned-class refusals over cards attempted (built + refused)
    refusal_frac_all = refused_cards / attempted if attempted else 0.0      # report-only (hard vetoes included; the figure #1779's report showed)
    att_ex_veto = attempted - split["hard_veto_cards"]
    refusal_frac_ex_veto_den = pinned_refusals / att_ex_veto if att_ex_veto else 0.0   # report-only (QP's alternative: vetoed cards out of the denominator)
    tok = {"input": 0, "output": 0, "cache_creation_input": 0, "cache_read_input": 0}
    usd = 0.0
    led_dir = root / "ledgers"
    if led_dir.is_dir():
        from tools.haiku_caller import ledger_summary  # noqa: PLC0415 - the cost ledger (spend), not an engine ledger

        for f in sorted(led_dir.glob("*/*.jsonl")):
            s = ledger_summary(f)
            usd += float(s.get("total_cost_usd", 0.0))
            for k, v in (s.get("tokens") or {}).items():
                if k in tok and isinstance(v, int):
                    tok[k] += v
    fb_frac = fallbacks / n_calls if n_calls else 0.0
    p95 = _pct(lat, 0.95)
    span_ms = (window_ms[1] - window_ms[0]) if window_ms else None
    err_counts = {k: int(cnt.get(k, 0)) for k in ERROR_COUNTERS}
    fails = []
    if fb_frac > FALLBACK_FAIL_FRAC:
        fails.append("fallback_share_above_5pct")
    if p95 is not None and p95 > CALL_P95_FAIL_MS:
        fails.append("call_latency_p95_above_8s")
    if refusal_frac > REFUSAL_FAIL_FRAC:
        fails.append("card_refusals_above_5pct_of_attempted")
    if run_error or any(err_counts.values()) or int(cnt.get("hook_errors", 0)):
        fails.append("run_error")
    if usd > cap_usd + 1e-9:
        fails.append("spend_above_cap")
    incomplete = []
    if errfree < MIN_OK_CALLS:
        incomplete.append("fewer_than_30_error_free_calls")
    if span_ms is None or span_ms < MIN_SPAN_MS:
        incomplete.append("span_under_2h")
    verdict = "FAIL" if fails else ("INCOMPLETE" if incomplete else "PASS")
    return {"kind": "hv7_smoke_report", "verdict": verdict, "fail_reasons": fails, "incomplete_reasons": incomplete, "run_error": run_error,
            "window_ms": list(window_ms) if window_ms else None, "span_s": None if span_ms is None else span_ms / 1000.0, "min_span_s": MIN_SPAN_MS / 1000.0,
            "periods": periods, "no_trade_periods": no_trade, "excluded_periods": excluded,
            "calls": n_calls, "error_free_calls": errfree, "min_error_free_calls": MIN_OK_CALLS, "valid_permutations": ok_valid,
            "fallback": fallbacks, "fallback_share": round(fb_frac, 6), "fallback_limit": FALLBACK_FAIL_FRAC, "fallback_reasons": dict(sorted(fb_reasons.items())),
            "model_refusals": refusals, "late_replies": late, "cut_off": cut, "replayed": replayed, "model_error_counts": dict(sorted(errs.items())),
            "call_latency_ms": {"n": len(lat), "p50": _pct(lat, 0.5), "p95": p95, "max": max(lat) if lat else None, "limit_p95": CALL_P95_FAIL_MS},
            "tokens": tok, "spend_usd": round(usd, 6), "cap_usd": cap_usd, "spend_stop_hits": spend_stop_hits,
            "cards_built": built, "card_refusals": pinned_refusals, "cards_attempted": attempted, "card_refusal_share": round(refusal_frac, 6),
            "card_refusal_class": "pinned: as_of (leakage_refused, as_of_*), chain stale or unavailable (chain_none, chain_*), holders stale; hard vetoes report-only; fail closed",
            "card_refusals_all": refused_cards, "card_refusal_share_all": round(refusal_frac_all, 6), "card_hard_veto_cards": split["hard_veto_cards"],
            "card_refusal_share_ex_veto_denominator": round(refusal_frac_ex_veto_den, 6), "card_refusal_keys_maybe_cut": split["keys_maybe_cut"],
            "card_refusal_unclassified": split["unclassified"],
            "card_refusal_reason_counts": reason_counts, "error_counters": err_counts, "hook_errors": int(cnt.get("hook_errors", 0)),
            "watch_errors": {k[len("watch_error:"):][:40]: v for k, v in sorted(cnt.items()) if k.startswith("watch_error:")},
            "runner_counters": {k: v for k, v in sorted(cnt.items()) if ":" not in k},
            "chain_calls_per_h": {"samples": len(calls_h_samples or []), "max": max(calls_h_samples) if calls_h_samples else None, "cap": RPC_CALLS_PER_H},
            "helius_credits": _num_only(chain_credits),
            "disclosure": "smoke: never counted, not in N, never an input to any read; engine ledger and prices never opened; no pool is named"}


def render(rep: dict[str, Any]) -> str:
    body = json.dumps(rep, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n"
    return body + "sha256 " + hashlib.sha256(body.encode()).hexdigest() + "\n"


# ------------------------------------------------------------------------------------------------------------------------------ the run
def load_rules(repo: str) -> SimpleNamespace:
    sys.dont_write_bytecode = True
    repo = str(Path(repo).resolve())
    here = str(Path(__file__).resolve().parent)
    sys.path[:] = [p for p in sys.path if p and str(Path(p).resolve()) != here]
    for m in [m for m in sys.modules if m == "tools" or m.startswith("tools.")]:
        del sys.modules[m]
    sys.path.insert(0, repo)
    from tools import arena_paper as AP  # noqa: PLC0415
    from tools import hv4_cards as C  # noqa: PLC0415
    from tools import hv4_model as M4  # noqa: PLC0415
    from tools import hv6_agents as A  # noqa: PLC0415
    from tools import hv6_cards as H  # noqa: PLC0415
    from tools import hv6_runner as H6  # noqa: PLC0415
    from tools import hv7_register as REG  # noqa: PLC0415
    from tools import hv7_runner as RN  # noqa: PLC0415
    from tools.arena_state import ArenaState  # noqa: PLC0415

    for mod in (AP, C, M4, A, H, H6, REG, RN):
        if not str(Path(mod.__file__).resolve()).startswith(repo + os.sep):
            raise Refused("module_not_from_repo")
    return SimpleNamespace(AP=AP, C=C, M4=M4, A=A, H=H, H6=H6, REG=REG, RN=RN, ArenaState=ArenaState, repo=repo)


def check_smk(smk: str, parent: str | None = None) -> Path:
    """The smoke dir must be a fresh child of <HV7_ROOT>/smoke/ (no symlink games), never the production root, never the real registry."""
    p = Path(os.path.normpath(smk))
    if not p.is_absolute() or "probe" in str(p).lower():
        raise Refused("smk_not_absolute_or_probe")
    par = os.path.realpath(parent or SMOKE_PARENT)
    if os.path.realpath(p.parent) != par or p.name in ("", ".", ".."):
        raise Refused("smk_not_a_child_of_smoke_dir")
    if p.exists():
        raise Refused("smk_exists")
    return p


class SpendStopModel:
    """Wraps the model. Before EACH call: total spent over every day file of the RANK-L cost ledger plus the smallest worst-case reserve must fit under the
    cap, else the call is not made and the reply is an error (a counted FALLBACK in the runner). The frozen Hv7Guard day cap is the second, per-day fence."""

    def __init__(self, inner: Any, guard: Any, root: str | os.PathLike, cap_usd: float, min_call_usd: float) -> None:
        self.inner, self.guard, self.root = inner, guard, Path(root)
        self.cap_usd, self.min_call_usd = float(cap_usd), float(min_call_usd)
        self.hits = 0
        self.calls = 0
        self.model = getattr(inner, "model", None)

    def spent(self) -> float:
        total = 0.0
        d = self.root / "ledgers" / "RANK-L"
        for f in sorted(d.glob("*.jsonl")) if d.is_dir() else []:
            total += self.guard.day_spent(f.stem)
        return total

    def complete(self, req: Any, now_ms: int) -> Any:
        if self.spent() + self.min_call_usd > self.cap_usd + 1e-12:
            self.hits += 1
            from tools.hv4_model import ModelReply  # noqa: PLC0415

            return ModelReply(error="smoke_spend_stop")
        self.calls += 1
        return self.inner.complete(req, now_ms)


def build_chain(R: SimpleNamespace, root: Path) -> Any:
    """The runner's own live chain source (hv7_runner.live_chain_source, the function `main` calls): HV4Chain + policy-v2 prefetcher gated to the t_d-locked window
    H:48-H:02, 500 calls/h, state and spend files under `root` (the smoke root is under the HV7 root, so HV7's check_path accepts it). Nothing is built here."""
    return R.RN.live_chain_source(root)


def period_plan(t_start: int, warmup_ms: int, period_ms: int, periods: int, tick_ms: int, te_offset_ms: int, anchor_h02: bool = True) -> tuple[list[int], int, int]:
    """(t_d grid, last t_e, loop end). anchor_h02 (the only mode the CLI reaches): the first t_d is the first H:02:00 UTC at or after t_start + warmup + one full prefetch
    window + one hour (the first window is the cold fill and is not screened: a cold store cannot hold the top pools, hv7_runner LIVE LIMIT c), then every period_ms (one hour). Without it (unit tests only): the first whole tick after the warm-up. t_e is t_d + te_offset_ms."""
    earliest = t_start + warmup_ms + (PREFETCH_LEAD_MS + PERIOD_MS if anchor_h02 else 0)
    if anchor_h02:
        first = (earliest - T_D_OFFSET_MS + PERIOD_MS - 1) // PERIOD_MS * PERIOD_MS + T_D_OFFSET_MS
    else:
        first = (earliest + tick_ms - 1) // tick_ms * tick_ms
    grid = [first + k * period_ms for k in range(periods)]
    last_te = grid[-1] + te_offset_ms
    return grid, last_te, last_te + TAIL_MS


def run_smoke(R: SimpleNamespace, a: argparse.Namespace, *, model: Any = None, chain: Any = None, tape_dir: str | None = None, live: bool = True,
              start_ms: int | None = None, sleep: Callable[[float], None] = time.sleep, now_fn: Callable[[], int] | None = None, parent: str | None = None,
              verify_sha: bool = True, engine_kw: dict | None = None, period_ms: int = PERIOD_MS, anchor_h02: bool = True) -> dict[str, Any]:
    smk = check_smk(a.smk, parent)
    if not (0 < a.cap_usd <= SPEND_CAP_USD):
        raise Refused("cap_above_0.10")
    if not (1 <= a.periods <= 40) or period_ms < 120_000 or a.warmup_ms < 0:
        raise Refused("period_args_out_of_range")
    if chain is None and (period_ms != PERIOD_MS or not anchor_h02):
        raise Refused("real_chain_requires_hourly_h02_grid")
    if model is None and not os.environ.get("ANTHROPIC_API_KEY", "").strip():
        raise Refused("api_key_not_in_env")
    if chain is None and (os.environ.get("MAL_HV4_RPC", "").strip().lower() != "helius"):
        raise Refused("MAL_HV4_RPC_must_be_helius")
    if os.environ.get("MAL_HV4_CHAIN_STATE"):
        raise Refused("MAL_HV4_CHAIN_STATE_set")
    RN, A, H = R.RN, R.A, R.H
    root, eng, reg = smk / "root", smk / "engine-ledger", smk / "smoke-registry.json"
    RN.check_path(root, "w")
    root.mkdir(parents=True)
    eng.mkdir(parents=True)
    clock_fn = now_fn or (lambda: time.time_ns() // 1_000_000)
    now = int(clock_fn()) if start_ms is None else int(start_ms)
    clock = (now // RN.MIN_MS + 2) * RN.MIN_MS
    # Production registration: the real clock and the sha verified against origin/main (arena_paper.register_bot refuses now_ms / verify_sha=False unless MAL_ARENA_TEST=1,
    # which the wrapper unsets; smoke job #1776 crashed here). `start_ms` / `verify_sha=False` are unit-test overrides and are passed on ONLY when asked for.
    reg_kw: dict[str, Any] = {} if (start_ms is None and verify_sha) else {"now_ms": now, "verify_sha": verify_sha}
    R.REG.register_all(a.frozen_sha, clock, str(reg), entries_halt_ms=None, repo=R.repo, out=open(os.devnull, "w"), **reg_kw)
    guard = RN.Hv7Guard(root, day_cap_usd=a.cap_usd)
    inner = model(guard) if callable(model) else (model if model is not None else R.A.CallerModel(guard))
    smodel = SpendStopModel(inner, guard, root, a.cap_usd, guard.min_call_usd)
    chain = chain if chain is not None else build_chain(R, root)
    prefetch = getattr(chain, "prefetch", None)
    if prefetch is not None:
        prefetch.start()
    tape = tape_dir or H.TAPE_ROOT
    RN.check_path(tape, "r")
    cfg = RN.Hv7Config(root=str(root), frozen_sha=a.frozen_sha, clock_start_ms=clock, live=live, entries_halt_ms=None)
    engine = R.AP.ArenaEngine(str(eng), str(reg), cfg=RN.stack_config(a.feed_delay_slots), **(engine_kw or {}))
    t_start = now - now % RN.TICK_MS
    td_grid, last_te, t_end = period_plan(t_start, a.warmup_ms, period_ms, a.periods, RN.TICK_MS, RN.T_E_OFFSET_MS - RN.T_D_OFFSET_MS, anchor_h02)
    first_td = td_grid[0]
    err = None
    samples: list[int] = []
    credits: Any = None
    counters: dict[str, Any] = {}
    run = None
    try:
        run = RN.Hv7Runner(cfg, R.ArenaState(tape, start_ms=0), engine, smodel, chain, guard, now_fn=now_fn)
        run.sim_now = t_start
        run.log_start()
        td_set = set(td_grid)
        last_watch = -WATCH_EVERY_MS
        T = t_start
        while T < t_end and not run._stop:
            if live:
                wait = (T + 1000 - run.now_fn()) / 1000.0
                if wait > 0:
                    sleep(wait)
            run.sim_now = max(run.sim_now, T)
            run.poll_to(T)
            run.exits(T)                                    # the order tick() uses: exits before entries
            if T in td_set:
                run.screen(T)
            elif run.pending is not None and T == run.pending["t_e"]:
                run.enter(T)
            if prefetch is not None and T < td_grid[-1]:
                try:
                    run.watch_tick(T)       # the runner's own method, called as its tick does (self-gated to WATCH_EVERY_MS, WATCH_MAX 200, runner scan and hot set)
                except Exception as e:  # noqa: BLE001 - class name only; a starved watchlist shows as card refusals
                    run._count("watch_error:" + type(e).__name__)
            if prefetch is not None and T < td_grid[-1] and T - last_watch >= WATCH_EVERY_MS:
                last_watch = T
                try:
                    samples.append(int(prefetch.stats().get("calls_last_hour", 0)))
                except Exception:  # noqa: BLE001
                    pass
            T += RN.TICK_MS
        run.sync_engine_counters()
    except Exception as e:  # noqa: BLE001 - class name only
        err = type(e).__name__
    finally:
        if run is not None:
            try:
                run.shutdown()
            except Exception as e:  # noqa: BLE001
                err = err or type(e).__name__
            counters = dict(run.counters)
            if run.hook_errors:
                counters["hook_errors"] = len(run.hook_errors)
        if prefetch is not None:
            prefetch.stop()
        rep_fn = getattr(chain, "credit_report", None)
        if rep_fn is not None:
            try:
                credits = rep_fn()
            except Exception:  # noqa: BLE001
                credits = None
    rep = report(root, counters=counters, run_error=err, window_ms=(first_td, last_te), cap_usd=a.cap_usd, spend_stop_hits=smodel.hits, chain_credits=credits,
                 calls_h_samples=samples)
    rep["frozen_sha"] = a.frozen_sha
    rep["period_ms"] = period_ms
    rep["periods_planned"] = a.periods
    rep["compression"] = "driven_by_smoke_tool_via_public_screen_enter; frozen tick() not used; hourly code unchanged"
    rep["prefetch_window"] = "runner_H48_H02_live_regime" if prefetch is not None else "injected_chain"
    rep["chain_source"] = "hv7_runner.live_chain_source (the runner path)" if prefetch is not None else "injected"
    (smk / "report.json").write_text(render(rep))
    return rep


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="HV7 real-model smoke (paper; writes only under <HV7_ROOT>/smoke/<id>/).")
    ap.add_argument("--repo", required=True, help="clean checkout at the final sha")
    ap.add_argument("--smk", required=True, help="fresh dir <HV7_ROOT>/smoke/<id>")
    ap.add_argument("--cap-usd", type=float, default=SPEND_CAP_USD)
    ap.add_argument("--feed-delay-slots", type=int, required=True)
    ap.add_argument("--periods", type=int, default=PERIODS)
    ap.add_argument("--warmup-min", type=float, default=WARMUP_MS / 60_000)
    ap.add_argument("--expect-sha", required=True, help="the registered final sha (40 hex)")
    a = ap.parse_args(argv)
    a.warmup_ms = int(a.warmup_min * 60_000)
    a.frozen_sha = a.expect_sha.strip().lower()
    if len(a.frozen_sha) != 40 or any(ch not in SHA_RE for ch in a.frozen_sha):
        print("hv7_smoke: refused: --expect-sha must be 40 hex characters", file=sys.stderr)
        return 2
    try:
        import subprocess  # noqa: PLC0415

        head = subprocess.run(["git", "-C", a.repo, "rev-parse", "HEAD"], capture_output=True, text=True, timeout=60).stdout.strip()
        dirty = subprocess.run(["git", "-C", a.repo, "status", "--porcelain"], capture_output=True, text=True, timeout=60).stdout.strip()
        if head != a.frozen_sha or dirty:
            raise Refused("repo_not_at_final_sha_or_dirty")
        R = load_rules(a.repo)
        rep = run_smoke(R, a)
    except Refused as e:
        print(f"hv7_smoke: refused: {e}", file=sys.stderr)
        return 2
    sys.stdout.write(render(rep))
    return 0 if rep["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
