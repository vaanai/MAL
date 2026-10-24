#!/usr/bin/env python3
"""dbc_m10_engine_equiv: engine-equivalence check on the in-sample Swap3-era hours [2026-10-16T04, 2026-10-17T00) under BACKFILL_ROOT
(QP-RULING-1024-read-hardening.md item 6). PAPER ONLY. Counts and md5s only; nothing is priced.

    python3 tools/dbc_m10_engine_equiv.py --tag-root <clean worktree of tag dbc-m10-forward-v1-am1> --out DIR

It imports the TAGGED tools.dbc_fwd from --tag-root (pins checked first: HEAD 7d5be9e5, clean tree, 12 sha256) and runs dbc_fwd.replay itself, i.e. the same internals
(screen_keys, Engine, stream_hours, the four VARIANTS) with enable_swap3_pricing and the committed docs/research/dbc-p17/data/cfg_summary.json, no decision log.
It bypasses ONLY dbc_fwd.replay_guard (its window ends at 10-16T04): for the duration of the call the guard is replaced by one that accepts exactly
[2026-10-16T04, 2026-10-17T00), the default root (BACKFILL_ROOT) and no non-default dbc_price window. The dbc_price data guard is NOT bypassed (default mode: hours
[10-14T00, 10-17T00) directly under BACKFILL_ROOT only).

Pre-declared verdict (item 6): PASS iff the both-oracle engine md5 triggers and md5 entries equal the screen's (equivalence_pass), no variant (the causal one included) raised,
and the causal live-policy decisions > 0 when the screen has > 0 trigger prints. Screen trigger prints = 0 -> UNINFORMATIVE (disclosed; the read may still run).
Anything else -> FAIL (the read must not run as tagged). Exit 0 PASS, 2 UNINFORMATIVE, 1 FAIL, 3 refused. Peak RSS (self and children) and wall time are recorded
for the read job's MemoryMax / time-limit sizing.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import resource
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

START = datetime(2026, 10, 16, 4, tzinfo=timezone.utc)
END = datetime(2026, 10, 17, 0, tzinfo=timezone.utc)
CFG_REL = "docs/research/dbc-p17/data/cfg_summary.json"
CFG_SHA256 = "913c27256dbfa11762c039957d5977a4a01fcaae5e41ac1ce4b730ef0988ca07"


def load_tagged(tag_root: str) -> tuple[Any, Any, Any]:
    """Import tools.dbc_fwd / dbc_price / dbc_screen from tag_root and nowhere else."""
    if "tools" in sys.modules:
        raise RuntimeError("a 'tools' package is already imported; refusing")
    here = os.path.realpath(os.path.dirname(os.path.abspath(__file__)))
    sys.path[:] = [os.path.realpath(tag_root)] + [p for p in sys.path if p and os.path.realpath(p) != here]
    W = importlib.import_module("tools.dbc_fwd")
    want = os.path.realpath(os.path.join(tag_root, "tools"))
    for m in (W, W.L, W.S):
        if os.path.dirname(os.path.realpath(m.__file__)) != want:
            raise RuntimeError(f"tagged module loaded from the wrong place: {m.__file__}")
    return W, W.L, W.S


def make_guard(W: Any, L: Any, start: datetime, end: datetime) -> Callable[..., None]:
    def guard(s: datetime, e: datetime, root: str | None = None) -> None:
        if (s, e) != (start, end):
            raise W.ReplayRefused(f"sw3_check_window_refused: only [{start:%Y-%m-%dT%H}, {end:%Y-%m-%dT%H})")
        for p in (root or "", os.path.realpath(root) if root else ""):
            if any(x in p for x in W.FORBIDDEN_PATH_PARTS):
                raise W.ReplayRefused(f"replay_root_refused: {root}")
        if root is not None and os.path.realpath(root) != os.path.realpath(L.BACKFILL_ROOT):
            raise W.ReplayRefused(f"sw3_check_root_refused: {root} (only the default BACKFILL_ROOT)")
        if L.SPENT_ROOT is not None or L.HOLDOUT_READ_ENABLED or L.FWD_ACTIVE is not None:
            raise W.ReplayRefused("replay_refused: a non-default dbc_price data window is enabled")

    return guard


def verdict(res: dict[str, Any] | None, error: str | None) -> tuple[str, str]:
    if error is not None or res is None:
        return "FAIL", f"exception: {error}"
    if not res.get("equivalence_pass"):
        return "FAIL", "both-oracle engine md5 triggers/entries differ from the screen's"
    if set(res.get("variants", {})) != {"oracle_rows+oracle_mode", "oracle_mode_only", "oracle_rows_only", "causal"}:
        return "FAIL", "not all four variants ran"
    if res["screen"]["trigger_prints"] == 0:
        return "UNINFORMATIVE", "screen has 0 trigger prints on these hours"
    if res["variants"]["causal"]["decisions_live_policy"] <= 0:
        return "FAIL", "screen has trigger prints but the causal engine sealed 0 live decisions"
    return "PASS", "both-oracle md5 equal; causal ran; causal live decisions > 0"


def check(W: Any, L: Any, cfg_summary: str | None, start: datetime = START, end: datetime = END) -> dict[str, Any]:
    guard = make_guard(W, L, start, end)
    guard(start, end, None)  # refuse before anything is read
    orig = W.replay_guard
    W.replay_guard = guard
    t0 = time.time()
    res, err = None, None
    try:
        res = W.replay(start, end, None, cfg_summary, None)
    except Exception as e:  # noqa: BLE001
        err = f"{type(e).__name__}: {str(e)[:300]}"
    finally:
        W.replay_guard = orig
    v, why = verdict(res, err)
    ru_s, ru_c = resource.getrusage(resource.RUSAGE_SELF), resource.getrusage(resource.RUSAGE_CHILDREN)
    return {"schema": "dbc_m10_engine_equiv_sw3_v1", "window": [f"{start:%Y-%m-%dT%H}", f"{end:%Y-%m-%dT%H}"], "verdict": v, "why": why, "error": err,
            "replay": res, "wall_s": round(time.time() - t0, 1), "peak_rss_self_mib": round(ru_s.ru_maxrss / 1024, 1), "peak_rss_children_mib": round(ru_c.ru_maxrss / 1024, 1),
            "taken_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "note": "Decision-key equivalence on in-sample Swap3-era hours (counts and md5s only, nothing priced). Not evidence that m10 makes money."}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--tag-root", required=True)
    ap.add_argument("--out", required=True)
    ns = ap.parse_args(argv)
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from tools import dbc_m10_read_ops as O  # stdlib-only helper from THIS checkout; dropped before the tagged import

    ok, why = O.check_pins(ns.tag_root)
    own = O.sha256_file(__file__)
    for k in [k for k in sys.modules if k == "tools" or k.startswith("tools.")]:
        del sys.modules[k]
    sys.path.pop(0)
    if not ok:
        print(f"REFUSED (pins): {why}", file=sys.stderr)
        return 3
    cfg = os.path.join(ns.tag_root, CFG_REL)
    if hashlib.sha256(Path(cfg).read_bytes()).hexdigest() != CFG_SHA256:
        print("REFUSED: cfg_summary.json sha256 differs from the tag", file=sys.stderr)
        return 3
    out = Path(ns.out)
    if out.exists() and any(out.iterdir()):
        print(f"REFUSED: out dir not empty: {out}", file=sys.stderr)
        return 3
    W, L, S = load_tagged(ns.tag_root)
    try:
        out = Path(S.check_out_dir(str(out)))
        res = check(W, L, cfg)
    except (W.ReplayRefused, L.Refused) as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 3
    res.update({"script_sha256": own, "tag_commit": subprocess.run(["git", "-C", ns.tag_root, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip(),
                "cfg_summary_sha256": CFG_SHA256})
    out.mkdir(parents=True, exist_ok=True)
    txt = json.dumps(res, indent=1, default=float)
    (out / "equivalence-sw3.json").write_text(txt)
    r = res["replay"] or {}
    print(json.dumps({"verdict": res["verdict"], "why": res["why"], "screen": {k: (r.get("screen") or {}).get(k) for k in ("trigger_prints", "entries", "md5_triggers", "md5_entries")},
                      "wall_s": res["wall_s"], "peak_rss_self_mib": res["peak_rss_self_mib"], "peak_rss_children_mib": res["peak_rss_children_mib"]}))
    for k, v in (r.get("variants") or {}).items():
        print(k, json.dumps({x: v.get(x) for x in ("trigger_prints", "entries_screen_policy", "decisions_live_policy", "md5_triggers", "md5_entries_screen_policy",
                                                   "equal_triggers", "equal_entries", "seconds")}))
    print("sha256 equivalence-sw3.json", hashlib.sha256(txt.encode()).hexdigest())
    return {"PASS": 0, "UNINFORMATIVE": 2}.get(res["verdict"], 1)


if __name__ == "__main__":
    sys.exit(main())
