#!/usr/bin/env python3
"""hv6_review_smoke: the REAL-MODEL REVIEWER SMOKE for HV6 (QP #940 comment 6421493435, change 6; DESIGN section 3: "stub, then a few real calls at the smoke").

PAPER only. Runs AFTER the runner smoke (tools/hv6_smoke.py) on the SAME smoke dir `<HV6_ROOT>/smoke/<tag>/` and writes only under `<smk>/root/` :
  * one real nightly-review call each for L, L-nr and P (claude-haiku-5-5 through tools.haiku_caller; the key is read by the caller from ANTHROPIC_API_KEY,
    which the wrapper takes from /var/lib/mal/anthropic/anthropic.env; this file never reads, prints or stores it);
  * one real regime-review call each for L and P, on a SYNTHETIC regime block written into `<smk>/root/regime/` (a real directory: the runner smoke's link to
    the producer's directory is removed first, so nothing is ever written to the production regime directory).
The code under test is the final-sha `tools/hv6_review.py` imported from a clean checkout (`--repo`): genesis, `review_night` and `regime_review`, the real
bundle builder, the real validators and manifest chain. Only the inputs are synthetic: the trade evidence is 12 records built from CARDS of the smoke's own
entry rows (so the card format is the real hv4-card-1), with fixed fake outcomes; the genesis clock is the previous 00:00Z so those records are inside the
bundle window. The smoke ledger and the smoke's prices are never opened for outcomes, and nothing here is evidence about HV6.

REPORT (counts, timings, flags only; never reply text, never a change, never a pool): per call: arm, kind, call error code (or None), reply parsed (valid) or
not, validator accepted / rejected COUNTS and rejection CODES, tokens, USD and median latency from that call's budget ledger. Verdict PASS iff all five calls
returned without an API / refusal / truncation / empty-reply error and every reply parsed (no `reply_error`); a validator REJECT of a model change is a count,
not a failure. FAIL otherwise (an invalid reply or parse failure is a code or prompt fix at a new sha before registration). INCOMPLETE if the smoke root has no
entry card to build evidence from. Extra spend cap: the day's spend so far + 0.10 USD (BudgetGuard cap, lowered only). Exit 0 PASS, 1 FAIL or INCOMPLETE,
2 refused before any call.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

HV6_ROOT = "/data/mal/arena/haiku-v6"
SMOKE_PARENT = HV6_ROOT + "/smoke"
EXTRA_USD = 0.10
N_RECORDS = 12
DAY_MS = 86_400_000
HOUR_MS = 3_600_000
LLM_NIGHTLY = ("L", "L-nr", "P")
LLM_REGIME = ("L", "P")


class Refused(Exception):
    """Refusal before any call; carries a fixed code only."""


def load_rules(repo: str) -> SimpleNamespace:
    sys.dont_write_bytecode = True
    repo = str(Path(repo).resolve())
    here = str(Path(__file__).resolve().parent)
    sys.path[:] = [p for p in sys.path if p and str(Path(p).resolve()) != here]
    for m in [m for m in sys.modules if m == "tools" or m.startswith("tools.")]:
        del sys.modules[m]
    sys.path.insert(0, repo)
    from tools import hv6_agents as A  # noqa: PLC0415
    from tools import hv6_cards as H  # noqa: PLC0415
    from tools import hv6_review as HRV  # noqa: PLC0415
    from tools.haiku_caller import ledger_summary  # noqa: PLC0415

    for mod in (A, H, HRV):
        if not str(Path(mod.__file__).resolve()).startswith(repo + os.sep):
            raise Refused("module_not_from_repo")
    return SimpleNamespace(A=A, H=H, HRV=HRV, ledger_summary=ledger_summary, repo=repo)


def check_smk(smk: str, parent: str | None = None) -> Path:
    """An EXISTING smoke dir that is a direct child of <HV6_ROOT>/smoke/ with a runner-smoke root inside."""
    p = Path(os.path.normpath(smk))
    if not p.is_absolute() or "probe" in str(p).lower():
        raise Refused("smk_not_absolute_or_probe")
    if os.path.realpath(p.parent) != os.path.realpath(parent or SMOKE_PARENT) or p.name in ("", ".", ".."):
        raise Refused("smk_not_a_child_of_smoke_dir")
    if not (p / "root" / "decisions").is_dir():
        raise Refused("no_runner_smoke_root_here")
    if (p / "root" / "manifests").exists():
        raise Refused("reviewer_smoke_already_ran")
    return p


def smoke_cards(root: Path, limit: int = 8) -> list[dict]:
    """Up to `limit` distinct cards from the smoke's own entry rows (any arm). Whitelisted read: only the `card` dict is kept; nothing is printed."""
    out: dict[str, dict] = {}
    base = root / "decisions"
    for d in sorted(base.iterdir()) if base.is_dir() else []:
        for f in sorted(d.glob("*.jsonl")):
            for line in f.read_bytes().splitlines():
                if not line.strip():
                    continue
                r = json.loads(line)
                c = r.get("card") if r.get("kind") == "entry" else None
                if isinstance(c, dict) and isinstance(c.get("id"), str) and c["id"] not in out:
                    out[c["id"]] = c
                    if len(out) >= limit:
                        return list(out.values())
    return list(out.values())


def synthetic_records(cards: list[dict], cutoff_ms: int, exit_fill_ms: int) -> list[dict]:
    """12 fake trade records on real card bytes. Fixed outcomes (alternating small wins and losses); ids t0000..; all closed before the cutoff."""
    reasons = ("stop", "target", "time")
    out = []
    for i in range(N_RECORDS):
        c = dict(cards[i % len(cards)])
        close = cutoff_ms - HOUR_MS - i * 600_000
        hold = 300 + 60 * i
        net = (2.0 + 0.5 * i) if i % 2 else -(1.5 + 0.5 * i)
        out.append({"id": f"t{i:04d}", "kind": "trade", "entry_ms": close - hold * 1000 - exit_fill_ms, "close_ms": close, "card": c,
                    "decision": {"conviction": 4, "size": 0.25, "plan": {"stop_pct": -25, "target_pct": 100, "trail_pct": 30, "time_min": 360}, "why": "smoke"},
                    "exit_reason": reasons[i % 3], "hold_s": hold, "net_pct": net})
    return out


def synthetic_block(as_of_ms: int) -> dict:
    """A hv6_regime.v1 block with ONE direct HIGH event (so a regime review is allowed) and one LOW adjacent event. No drill/smoke/stub/synthetic key (the real
    loader refuses those); it is marked synthetic only by where it lives (the smoke root) and by its id prefix being a hash of a fixed string."""
    eid = "RG-" + hashlib.sha256(b"hv6-review-smoke-event").hexdigest()[:8]
    return {"schema": "hv6_regime.v1", "as_of_ms": as_of_ms, "as_of_utc": datetime.fromtimestamp(as_of_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "radar_status": "ok", "pins": {}, "pin_mismatch": [], "counts": {}, "summary": {"n_high_core_7d": 1, "hours_since_last_high_direct": 3},
            "events": [{"id": eid, "kind": "program_upgrade", "sev": "HIGH", "tier": "core", "program": "pump", "relevance": "direct", "change_utc": None,
                        "change_basis": "run_utc", "key_class": "other", "first_run_utc": None, "hours_since": 3},
                       {"id": "RG-" + hashlib.sha256(b"hv6-review-smoke-low").hexdigest()[:8], "kind": "new_vocab", "sev": "LOW", "tier": "none", "program": "OTHER",
                        "relevance": "adjacent", "change_utc": None, "change_basis": "run_utc", "key_class": "other", "first_run_utc": None, "hours_since": 9}]}


def _counts(root: Path, rel: str) -> dict[str, Any]:
    """Counts from a validator file: accepted / rejected numbers and rejection CODES only (never a change, never reply text)."""
    p = root / rel
    if not p.is_file():
        return {"accepted": None, "rejected": None, "reject_codes": {}, "reply_error": None}
    v = json.loads(p.read_bytes())
    codes: dict[str, int] = {}
    for item in v.get("rejected") or []:
        code = item[1] if isinstance(item, (list, tuple)) and len(item) > 1 and isinstance(item[1], str) else "unknown"
        codes[code] = codes.get(code, 0) + 1
    return {"accepted": len(v.get("accepted") or []), "rejected": len(v.get("rejected") or []), "reject_codes": dict(sorted(codes.items())),
            "reply_error": v.get("reply_error") if isinstance(v.get("reply_error"), str) else None,
            "review_error": v.get("review_error") if isinstance(v.get("review_error"), str) else None}


def ledger_figures(R: SimpleNamespace, root: Path, ledger: str, day: str) -> dict[str, Any]:
    f = root / "ledgers" / ledger / f"{day}.jsonl"
    if not f.is_file():
        return {"calls": 0, "tokens": None, "usd": None, "median_latency_ms": None}
    s = R.ledger_summary(f)
    return {"calls": s.get("calls"), "tokens": s.get("tokens"), "usd": round(float(s.get("total_cost_usd", 0.0)), 6), "median_latency_ms": s.get("median_latency_ms_200")}


def run_review_smoke(R: SimpleNamespace, a: argparse.Namespace, *, model: Any = None, now_ms: int | None = None) -> dict[str, Any]:
    smk = check_smk(a.smk)
    root = smk / "root"
    if model is None and not os.environ.get("ANTHROPIC_API_KEY", "").strip():
        raise Refused("api_key_not_in_env")
    HRV, A, H = R.HRV, R.A, R.H
    H.check(root, "w")
    cards = smoke_cards(root)
    wall = int(time.time() * 1000) if now_ms is None else now_ms
    day = datetime.fromtimestamp(wall / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    cutoff = HRV.day_start_ms(day)
    t0 = cutoff - DAY_MS  # the previous 00:00Z: a boundary, and before the synthetic evidence
    rep: dict[str, Any] = {"kind": "hv6_review_smoke_report", "day": day, "frozen_sha": a.frozen_sha, "n_cards_available": len(cards), "calls": [], "extra_cap_usd": EXTRA_USD,
                           "disclosure": "synthetic evidence on real card bytes; synthetic regime block in the smoke root; counts only; never counted, not in N, never an input to any read"}
    if not cards:
        rep.update(verdict="INCOMPLETE", why="no_entry_card_in_the_smoke_root")
        return rep
    link = root / "regime"
    if link.is_symlink():
        link.unlink()  # never write through the link into the producer's directory
    link.mkdir(exist_ok=True)
    block = synthetic_block(wall - 3 * HOUR_MS)
    raw = json.dumps(block, sort_keys=True, separators=(",", ":")).encode()
    bpath = link / f"block-{block['as_of_ms']}.json"
    bpath.write_bytes(raw)
    blk_read, raw_read = HRV.read_block_file(bpath, root=root)  # the real loader must accept it
    rep["synthetic_block_accepted_by_loader"] = blk_read["as_of_ms"] == block["as_of_ms"] and raw_read == raw
    guard0 = A.BudgetGuard(root)
    cap = min(A.GLOBAL_DAY_CAP_USD, guard0.day_spent(day) + EXTRA_USD)
    guard = A.BudgetGuard(root, global_cap_usd=cap)
    model = model(guard) if callable(model) else (model if model is not None else A.CallerModel(guard))
    for arm in LLM_NIGHTLY:  # genesis (the chain's first line), before any call
        row, _ = HRV.genesis_manifest(arm, t0, a.frozen_sha)
        HRV.append_manifest(root, arm, row, None, None)
    recs = synthetic_records(cards, cutoff, HRV.EXIT_FILL_MS)
    for arm in LLM_NIGHTLY:
        err = None
        try:
            HRV.review_night(arm, day, root=root, ledger_root=None, model=model, now_ms=wall, commit=a.frozen_sha, records=recs, open_positions=0,
                            block=(block if arm in A.regime_arms() else None))
        except Exception as e:  # noqa: BLE001 - class name only
            err = type(e).__name__
        c = _counts(root, f"validator/{arm}/{day}.nightly.json")
        rep["calls"].append({"arm": arm, "kind": "nightly", "exception": err, **c, "ledger": ledger_figures(R, root, f"REV-{arm}", day)})
    n_after = {arm: 1 for arm in LLM_REGIME}
    for arm in LLM_REGIME:
        err = None
        try:
            HRV.regime_review(arm, day, root=root, ledger_root=None, model=model, now_ms=wall + 1000, commit=a.frozen_sha, block=block, records=recs, block_bytes=raw)
        except Exception as e:  # noqa: BLE001
            err = type(e).__name__
        c = _counts(root, f"validator/{arm}/{day}.intraday{n_after[arm]}.json")
        rep["calls"].append({"arm": arm, "kind": "regime", "exception": err, **c, "ledger": ledger_figures(R, root, f"RGM-{arm}", day)})
    bad = [f"{c['arm']}:{c['kind']}" for c in rep["calls"]
           if c["exception"] or c.get("review_error") or c.get("reply_error") or c["accepted"] is None]
    rep["not_ok"] = bad
    rep["verdict"] = "FAIL" if bad else "PASS"
    rep["spend_usd"] = round(sum((c["ledger"]["usd"] or 0.0) for c in rep["calls"]), 6)
    return rep


def render(rep: dict[str, Any]) -> str:
    body = json.dumps(rep, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n"
    return body + "sha256 " + hashlib.sha256(body.encode()).hexdigest() + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="HV6 real-model reviewer smoke (paper; writes only under <smk>/root/).")
    ap.add_argument("--repo", required=True, help="clean checkout at the final sha")
    ap.add_argument("--smk", required=True, help="the existing runner-smoke dir <HV6_ROOT>/smoke/<tag>")
    ap.add_argument("--expect-sha", required=True, help="the registered final sha (40 hex); the repo HEAD must equal it")
    a = ap.parse_args(argv)
    a.frozen_sha = a.expect_sha.strip().lower()
    try:
        if len(a.frozen_sha) != 40 or any(ch not in "0123456789abcdef" for ch in a.frozen_sha):
            raise Refused("expect_sha_not_40_hex")
        head = subprocess.run(["git", "-C", a.repo, "rev-parse", "HEAD"], capture_output=True, text=True, timeout=60).stdout.strip()
        dirty = subprocess.run(["git", "-C", a.repo, "status", "--porcelain"], capture_output=True, text=True, timeout=60).stdout.strip()
        if head != a.frozen_sha or dirty:
            raise Refused("repo_not_at_final_sha_or_dirty")
        R = load_rules(a.repo)
        rep = run_review_smoke(R, a)
    except Refused as e:
        print(f"hv6_review_smoke: refused: {e}", file=sys.stderr)
        return 2
    sys.stdout.write(render(rep))
    (Path(a.smk) / "root" / "review-smoke-report.json").write_text(render(rep))
    return 0 if rep["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
