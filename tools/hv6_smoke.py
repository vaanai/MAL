#!/usr/bin/env python3
"""hv6_smoke: the real-model smoke for HV6 (DEC-031 Am.6 style; QP #914 "Item 7", QP #918 comment 6402258796 (a)-(c)).

PAPER only. Runs the FINAL-sha runner (`tools.hv6_runner.Runner`, imported from `--repo`, a clean checkout at the final sha) with the REAL caller
(claude-haiku-5-5 through tools.haiku_caller; the key is read by the caller from ANTHROPIC_API_KEY at call time, which the wrapper takes from
/var/lib/mal/anthropic/anthropic.env; this file never reads, prints or stores it), the real chain source (MAL_HV4_RPC=helius pinned, 1,000 calls/h,
policy-v2 prefetcher) and the live arena tape (read only), for a short window, with a global API cap of at most 0.50 USD (BudgetGuard cap, lowered only).

WRITES ONLY under <HV6_ROOT>/smoke/<tag>/ : root/ (decision log, budget ledgers, chain cache), engine-ledger/, smoke-registry.json (a synthetic registry
made by the unmodified register_bot through tools/hv6_register.register_all, never the real /data/mal/arena/bots.json), report.json. The smoke is never
counted, is not in N, never an input to any read; its clock is a few minutes after the start.

REPORT (counts, timings, flags only): per LLM arm takes / WATCH / STALE / PARSE_ERROR / NO_OUTPUT / BELOW_MIN / other skips; pooled STALE share of LLM entry
verdicts; chunk latency (common decision instant minus the card's as_of_ms) p50/p95/max; model call latency; token counts and USD from the budget ledgers;
chain calls per hour (sampled from the prefetcher, max/mean against the 1,000 cap) and Helius credits (the chain counter snapshot, numbers only); card
refusal reason counts; watches added / fired / expired. It never names a pool, mint, signature, wallet, card, price, fill, exit or P&L. The engine ledger is
never opened and the decision rows are read through a whitelist projection (kind, reasons[0], ms, as_of_ms, ids length, latency, error).

VERDICT (pre-declared; a FAIL is fixed by code at a new sha, never by tuning a limit): FAIL if STALE > 5% of pooled LLM entry verdicts, or the p95 of the chunk
latency > 8,000 ms (QP #918 6402258796 (a)), or as_of_refusal + chain_stale + chain_unavailable + holders_stale card refusals exceed 5% of cards ATTEMPTED (built + refused; QP #940 s3, no carve-out for first-sight chain_unavailable)
(QP #914 item 7 "a small fraction"; 5% is proposed here, QP confirms it on the registration pack head), or the run raised. INCOMPLETE if fewer than 30 LLM
calls returned without error. Otherwise PASS. Exit 0 PASS, 1 FAIL or INCOMPLETE, 2 refused before any call.
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

HV6_ROOT = "/data/mal/arena/haiku-v6"
SMOKE_PARENT = HV6_ROOT + "/smoke"
REGIME_DIR = HV6_ROOT + "/regime"  # the producer job writes block-<as_of_ms>.json here; the smoke root links to it READ-ONLY (blocks hold radar enums, no outcomes)
SHA_RE = "0123456789abcdef"  # the registered final sha is a REQUIRED argument (--expect-sha, 40 hex); the repo HEAD must equal it
CAP_USD_MAX = 0.50
STALE_FAIL_FRAC = 0.05
CHUNK_P95_FAIL_MS = 8_000
REFUSAL_FAIL_FRAC = 0.05
MIN_OK_CALLS = 30
RPC_CALLS_PER_H = 1_000
LLM_ARMS = ("L", "L-nr", "F", "P")
NOT_MODEL_SKIP = ("STALE", "PARSE_ERROR", "NO_OUTPUT", "BELOW_MIN")
CHAIN_REASONS = ("chain_stale", "chain_unavailable", "holders_stale")
MIN_MS = 60_000


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


def _num_only(d: Any) -> dict[str, Any]:
    """A shallow numbers-only copy of a counter snapshot (Helius credits and call counts); anything else is dropped."""
    out: dict[str, Any] = {}
    if isinstance(d, dict):
        for k, v in d.items():
            if isinstance(v, bool):
                continue
            if isinstance(v, (int, float)) and math.isfinite(v):
                out[str(k)] = v
            elif isinstance(v, dict):
                sub = _num_only(v)
                if sub:
                    out[str(k)] = sub
    return out


def report(root: str | os.PathLike, *, samples_calls_h: list[int] | None = None, chain_credits: Any = None, run_error: str | None = None,
           window_ms: tuple[int, int] | None = None, cap_usd: float = CAP_USD_MAX) -> dict[str, Any]:
    """Counts-only report over a smoke root (decision log through a whitelist projection; budget ledgers by their own totals)."""
    root = Path(root)
    arms: dict[str, dict[str, int]] = {a: {"takes": 0, "watch": 0, "stale": 0, "parse_error": 0, "no_output": 0, "below_min": 0, "other_skips": 0} for a in LLM_ARMS}
    chunks: dict[tuple, int] = {}
    asof: dict[tuple, int] = {}
    call_lat: list[float] = []
    n_calls = ok_calls = 0
    for a in LLM_ARMS:
        for r in _rows(root, a):
            k = r.get("kind")
            if k == "entry":
                arms[a]["takes"] += 1
            elif k == "watch_add":
                arms[a]["watch"] += 1
            elif k == "skip":
                reasons = r.get("reasons") or []
                head = reasons[0] if reasons and isinstance(reasons[0], str) else ""
                if head == "STALE":
                    arms[a]["stale"] += 1
                elif head in NOT_MODEL_SKIP:
                    arms[a][head.lower()] += 1
                else:
                    arms[a]["other_skips"] += 1
            elif k == "llm_call":
                n_calls += 1
                ok_calls += int(r.get("error") is None)
                if isinstance(r.get("latency_ms"), (int, float)) and not isinstance(r.get("latency_ms"), bool):
                    call_lat.append(float(r["latency_ms"]))
                if r.get("call") == "entry" and isinstance(r.get("ms"), int) and isinstance(r.get("as_of_ms"), int):
                    key = (r["as_of_ms"], len(r.get("ids") or ()), hashlib.sha256(json.dumps(r.get("ids") or [], separators=(",", ":")).encode()).hexdigest())
                    chunks[key] = max(chunks.get(key, 0), r["ms"])
                    asof[key] = r["as_of_ms"]
    chunk_lat = [float(chunks[k] - asof[k]) for k in chunks]
    scans = cards = asof_ref = chain_ref = refused_rows = 0
    reasons_n: dict[str, int] = {}
    w_add = w_fired = w_exp = 0
    for r in _rows(root, "SCAN"):
        k = r.get("kind")
        if k == "scan":
            scans += 1
            cards += int(r.get("n_cards") or 0)
        elif k == "as_of_refusal":
            asof_ref += 1
        elif k == "card_refused":
            refused_rows += 1
            rs = [x for x in (r.get("reasons") or []) if isinstance(x, str)]
            for x in rs:
                reasons_n[x] = reasons_n.get(x, 0) + 1
            chain_ref += int(any(x in CHAIN_REASONS for x in rs))  # one refused card counts once
    for a in LLM_ARMS:
        for r in _rows(root, a):
            if r.get("kind") == "watch_end":
                if r.get("why") == "fired":
                    w_fired += 1
                elif r.get("why") == "expired":
                    w_exp += 1
    verdicts = sum(v["takes"] + v["watch"] + v["stale"] + v["parse_error"] + v["no_output"] + v["below_min"] + v["other_skips"] for v in arms.values())
    stale = sum(v["stale"] for v in arms.values())
    stale_frac = stale / verdicts if verdicts else 0.0
    tok = {"input": 0, "output": 0, "cache_creation_input": 0, "cache_read_input": 0}
    usd = 0.0
    led_dir = root / "ledgers"
    if led_dir.is_dir():
        from tools.haiku_caller import ledger_summary  # noqa: PLC0415 - the budget ledger (spend), not an engine ledger
        for f in sorted(led_dir.glob("*/*.jsonl")):
            s = ledger_summary(f)
            usd += float(s.get("total_cost_usd", 0.0))
            for k, v in (s.get("tokens") or {}).items():
                if k in tok and isinstance(v, int):
                    tok[k] += v
    cred = _num_only(chain_credits)
    cf = root / "chain-credits.json"
    if cf.is_file():
        try:
            cred_file = _num_only(json.loads(cf.read_bytes()))
        except ValueError:
            cred_file = {}
    else:
        cred_file = {}
    samples = list(samples_calls_h or [])
    p95 = _pct(chunk_lat, 0.95)
    attempted = cards + refused_rows + asof_ref  # cards attempted = built + refused (QP #940 s3)
    asof_frac = asof_ref / attempted if attempted else 0.0
    chain_frac = chain_ref / attempted if attempted else 0.0
    combined_frac = (asof_ref + chain_ref) / attempted if attempted else 0.0
    fails = []
    if stale_frac > STALE_FAIL_FRAC:
        fails.append("stale_share_above_5pct")
    if p95 is not None and p95 > CHUNK_P95_FAIL_MS:
        fails.append("chunk_latency_p95_above_8s")
    if combined_frac > REFUSAL_FAIL_FRAC:  # as_of_refusal + chain_stale + chain_unavailable + holders_stale, first-sight chain_unavailable included (no carve-out)
        fails.append("card_refusals_above_5pct_of_attempted")
    if run_error:
        fails.append("run_error")
    if usd > cap_usd + 1e-9:
        fails.append("spend_above_cap")
    verdict = "FAIL" if fails else ("INCOMPLETE" if ok_calls < MIN_OK_CALLS else "PASS")
    return {"kind": "hv6_smoke_report", "verdict": verdict, "fail_reasons": fails, "incomplete_if_ok_calls_below": MIN_OK_CALLS, "run_error": run_error,
            "window_ms": list(window_ms) if window_ms else None, "scans": scans, "cards": cards,
            "arms": arms, "llm_entry_verdicts": verdicts, "stale": stale, "stale_share": round(stale_frac, 6), "stale_limit": STALE_FAIL_FRAC,
            "takes_total": sum(v["takes"] for v in arms.values()), "watch_total": sum(v["watch"] for v in arms.values()),
            "watches": {"added": sum(v["watch"] for v in arms.values()), "fired": w_fired, "expired": w_exp},
            "chunk_latency_ms": {"n": len(chunk_lat), "p50": _pct(chunk_lat, 0.5), "p95": p95, "max": max(chunk_lat) if chunk_lat else None, "limit_p95": CHUNK_P95_FAIL_MS},
            "llm_calls": {"n": n_calls, "ok": ok_calls, "latency_ms_p50": _pct(call_lat, 0.5), "latency_ms_p95": _pct(call_lat, 0.95)},
            "tokens": tok, "spend_usd": round(usd, 6), "cap_usd": cap_usd,
            "cards_attempted": attempted, "as_of_refusals": asof_ref, "refusal_share_combined": round(combined_frac, 6), "as_of_refusal_share": round(asof_frac, 6), "chain_refused_cards": chain_ref, "chain_refusal_share": round(chain_frac, 6),
            "refusal_reason_counts": dict(sorted(reasons_n.items())),
            "chain_calls_per_h": {"samples": len(samples), "max": max(samples) if samples else None, "mean": round(sum(samples) / len(samples), 2) if samples else None,
                                  "cap": RPC_CALLS_PER_H, "above_cap": bool(samples and max(samples) > RPC_CALLS_PER_H)},
            "helius_credits": {"counter_snapshot": cred, "chain_credits_json": cred_file},
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
    from tools import hv6_agents as A  # noqa: PLC0415
    from tools import hv6_cards as H  # noqa: PLC0415
    from tools import hv6_register as REG  # noqa: PLC0415
    from tools import hv6_runner as RN  # noqa: PLC0415
    from tools.arena_state import ArenaState  # noqa: PLC0415

    for mod in (AP, A, H, REG, RN):
        if not str(Path(mod.__file__).resolve()).startswith(repo + os.sep):
            raise Refused("module_not_from_repo")
    return SimpleNamespace(AP=AP, A=A, H=H, REG=REG, RN=RN, ArenaState=ArenaState, repo=repo)


def check_smk(smk: str, parent: str | None = None) -> Path:
    """The smoke dir must be a fresh child of <HV6_ROOT>/smoke/ (no symlink games), never the production root, never the real registry."""
    p = Path(os.path.normpath(smk))
    if not p.is_absolute() or "probe" in str(p).lower():
        raise Refused("smk_not_absolute_or_probe")
    par = os.path.realpath(parent or SMOKE_PARENT)
    if os.path.realpath(p.parent) != par or p.name in ("", ".", ".."):
        raise Refused("smk_not_a_child_of_smoke_dir")
    if p.exists():
        raise Refused("smk_exists")
    return p


def run_smoke(R: SimpleNamespace, a: argparse.Namespace, *, model: Any = None, chain: Any = None, tape_dir: str | None = None, live: bool = True,
              start_ms: int | None = None, end_ms: int | None = None, state_start_ms: int | None = None, sleep: Callable[[float], None] = time.sleep, log: Any = sys.stderr) -> dict[str, Any]:
    smk = check_smk(a.smk)
    if not (0 < a.cap_usd <= CAP_USD_MAX):
        raise Refused("cap_above_0.50")
    if a.hours <= 0 or a.hours > 4:
        raise Refused("hours_out_of_range")
    if model is None and not os.environ.get("ANTHROPIC_API_KEY", "").strip():
        raise Refused("api_key_not_in_env")
    if chain is None and (os.environ.get("MAL_HV4_RPC", "").strip().lower() != "helius"):
        raise Refused("MAL_HV4_RPC_must_be_helius")
    if os.environ.get("MAL_HV4_CHAIN_STATE"):
        raise Refused("MAL_HV4_CHAIN_STATE_set")
    root, eng, reg = smk / "root", smk / "engine-ledger", smk / "smoke-registry.json"
    H, A, RN = R.H, R.A, R.RN
    for p in (root, eng):
        H.check(p, "w")
    root.mkdir(parents=True)
    eng.mkdir(parents=True)
    rdir = Path(getattr(a, "regime_dir", None) or REGIME_DIR)
    n_blocks = None
    if rdir.is_dir():
        os.symlink(rdir, root / "regime")  # the runner and reviewer read only <root>/regime/block-*.json; nothing is ever written there by the smoke
        n_blocks = len(list(rdir.glob("block-*.json")))
    now = int(time.time() * 1000) if start_ms is None else start_ms
    clock = (now // MIN_MS + 2) * MIN_MS  # the next whole minute after the registration, plus one: register_bot refuses a clock before the registration instant
    # Production registration: the real clock and the sha verified against origin/main (arena_paper.register_bot refuses now_ms unless MAL_ARENA_TEST=1, which the wrapper
    # unsets; smoke job #1769 crashed here with 0 API calls). `now_ms` is passed ONLY when a unit test supplies start_ms.
    R.REG.register_all(a.frozen_sha, clock, str(reg), repo=R.repo, out=open(os.devnull, "w"), **({} if start_ms is None else {"now_ms": now}))
    guard = A.BudgetGuard(root, global_cap_usd=a.cap_usd)
    model = model(guard) if callable(model) else (model if model is not None else A.CallerModel(guard))
    chain = chain if chain is not None else RN.live_chain_source(str(root))
    if getattr(chain, "prefetch", None) is not None:
        chain.prefetch.start()
    cfg = RN.RunnerConfig(root=str(root), frozen_sha=a.frozen_sha, clock_start_ms=clock, live=live)
    tape = tape_dir or H.TAPE_ROOT
    H.check(tape, "r")
    err = None
    samples: list[int] = []
    summ: dict[str, Any] = {}
    t0 = max(clock, now)
    t_start = t0 - t0 % RN.TICK_MS
    t_end = end_ms if end_ms is not None else t_start + int(a.hours * 3_600_000)
    last = [0.0]

    def sleeper(s: float) -> None:
        sleep(s)
        w = time.time()
        if getattr(chain, "prefetch", None) is not None and w - last[0] >= 60:
            last[0] = w
            try:
                samples.append(int(chain.prefetch.stats().get("calls_last_hour", 0)))
            except Exception:  # noqa: BLE001
                pass

    try:
        run = RN.Runner(cfg, R.ArenaState(tape) if state_start_ms is None else R.ArenaState(tape, start_ms=state_start_ms), R.AP.ArenaEngine(str(eng), str(reg), cfg=RN.stack_config(a.feed_delay_slots)), model, chain, guard, review_root=str(root))
        run.resume()
        run.run(t_start, t_end, sleeper)
        summ = run.summary()
    except Exception as e:  # noqa: BLE001 - class name only
        err = type(e).__name__
    finally:
        if getattr(chain, "prefetch", None) is not None:
            chain.prefetch.stop()
    rep = report(root, samples_calls_h=samples, chain_credits=summ.get("chain_credits"), run_error=err, window_ms=(t_start, t_end), cap_usd=a.cap_usd)
    rep["provider"] = os.environ.get("MAL_HV4_RPC") if chain is not None else None
    rep["frozen_sha"] = a.frozen_sha
    rep["regime_blocks_visible"] = n_blocks  # count only; None = no regime dir (no producer running)
    rep["runner_counters"] = _num_only(summ.get("counters"))
    (smk / "report.json").write_text(render(rep))
    return rep


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="HV6 real-model smoke (paper; writes only under <HV6_ROOT>/smoke/<tag>/).")
    ap.add_argument("--repo", required=True, help="clean checkout at the final sha")
    ap.add_argument("--smk", required=True, help="fresh dir <HV6_ROOT>/smoke/<tag>")
    ap.add_argument("--hours", type=float, default=2.0)
    ap.add_argument("--cap-usd", type=float, default=CAP_USD_MAX)
    ap.add_argument("--feed-delay-slots", type=int, required=True)
    ap.add_argument("--regime-dir", default=REGIME_DIR, help="the producer job's block dir, linked read-only as <smk>/root/regime")
    ap.add_argument("--expect-sha", required=True, help="the registered final sha (40 hex)")
    a = ap.parse_args(argv)
    a.frozen_sha = a.expect_sha.strip().lower()
    if len(a.frozen_sha) != 40 or any(ch not in SHA_RE for ch in a.frozen_sha):
        print("hv6_smoke: refused: --expect-sha must be 40 hex characters", file=sys.stderr)
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
        print(f"hv6_smoke: refused: {e}", file=sys.stderr)
        return 2
    sys.stdout.write(render(rep))
    return 0 if rep["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
