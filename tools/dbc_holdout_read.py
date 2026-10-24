#!/usr/bin/env python3
"""dbc_holdout_read: the ONE-LOOK confirmation read of cell D1 m10 on the reserved DBC holdout [2026-10-17T00, 2026-10-24T00) (QP ruling 2026-10-24, protocol P0-P8). PAPER ONLY.

    python -m tools.dbc_holdout_read --out DIR --confirm-one-look

Rules enforced in code: (1) refuses to start if ANY output file already exists in DIR, or if the fixed marker file MARKER exists (created atomically before the first holdout file is opened, regardless of --out); (2) the holdout switch tools.dbc_price.HOLDOUT_READ_ENABLED is False by
default and is turned on here, explicitly, after the output check, and the run prints that it did; (3) while it is on the exploration hours are refused; (4) the cell is frozen (D1 m10 as in
tools.dbc_screen.CELLS), no flag changes it; (5) more than 6 missing/incomplete hours = NOT_DECIDABLE (nothing priced); (6) the verdict is computed by `verdict()` below (P5) on the PRIMARY line
(strand = full loss, deciding leg 1.9 s / 0.25 SOL / rent 0 / 160k sends) under BOTH fail models; sensitivities are report-only and never enter it. A PASS is exploration-to-forward evidence only, not a
promotion-gate pass (P8).
"""

from __future__ import annotations

if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from tools import arena_stack as F
from tools import dbc_price as L
from tools import dbc_screen as S

MAX_BAD_HOURS = 6
MARKER = "/data/mal/hunt-1008/push-1016/hunt29/dbc/HOLDOUT_READ_DONE"  # fixed path OUTSIDE --out: one look per holdout, whatever --out is
OUT_JSON = "holdout-m10.json"
OUT_MD = "holdout-m10.md"
CELL = "m10"


def verdict(primary: dict[str, Any]) -> dict[str, Any]:
    """P5 on the primary line. `primary` = price_cell(...) output (keys flat, press). PASS only if ALL hold under BOTH fail models; a value of 0 or below fails. No rounding."""
    out: dict[str, Any] = {"filled": primary["filled"], "checks": {}}
    ok = primary["filled"] >= 100
    out["checks"]["filled_ge_100"] = ok
    allok = ok
    for nm in ("flat", "press"):
        s = primary[nm]
        n_days = s.get("days", 0)
        c = {
            "days_ge_5": n_days >= 5,
            "majority_days_positive": n_days > 0 and s["days_pos"] * 2 > n_days,
            "ci90_lo_trade_gt_0": s.get("ci90_lo_trade") is not None and s["ci90_lo_trade"] > 0,
            "total_ex_top3_gt_0": s.get("ex_top3") is not None and s["ex_top3"] > 0,
            "total_ex_best_day_gt_0": s.get("ex_best_day") is not None and s["ex_best_day"] > 0,
            "mean_gt_0": s.get("mean") is not None and s["mean"] > 0,
        } if s.get("n", 0) else {"no_attempts": False}
        out["checks"][nm] = c
        allok = allok and all(c.values())
    out["verdict"] = "PASS" if allok else "FAIL"
    return out


def claim_marker(path: str) -> bool:
    """Atomically create the one-look marker (O_CREAT|O_EXCL). False if it already exists. Called BEFORE the first holdout file is opened; never removed by this tool."""
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o640)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w") as f:
        f.write(datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") + " holdout one-look claimed\n")
    return True


def sha(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def hook_share_by_day(D: dict[str, Any]) -> dict[str, Any]:
    bt = D["a"]["bt"]
    days = (bt // 86400).astype(np.int64)
    out = {}
    for u in np.unique(days):
        m = days == u
        out[datetime.fromtimestamp(int(u) * 86400, tz=timezone.utc).strftime("%Y-%m-%d")] = {"rows": int(m.sum()), "hook_pool_rows": int((m & D["hook_row"]).sum()),
                                                                                           "hook_share": float((m & D["hook_row"]).sum() / max(m.sum(), 1))}
    return out


def zero_legacy_sell_fee(att: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sensitivity 'sell fee as in 5ea8bf17': mode-1 sells were priced with rate ~ 0 (fee lamports / base atoms)."""
    return [{**a, "fee_out": 0.0 if a["mode"] == 1 else a["fee_out"]} for a in att]


def run(out_dir: Path, root: str | None = None) -> int:
    cmd_out = [out_dir / OUT_JSON, out_dir / OUT_MD]
    if any(p.exists() for p in cmd_out) or (out_dir.exists() and any(out_dir.iterdir())):
        print(f"REFUSED: output already exists in {out_dir} (one look only)", file=sys.stderr)
        return 3
    out_dir = Path(S.check_out_dir(str(out_dir)))
    if not claim_marker(MARKER):
        print(f"REFUSED: one-look marker {MARKER} exists (the holdout was already read or claimed)", file=sys.stderr)
        return 4
    out_dir.mkdir(parents=True, exist_ok=True)
    L.enable_holdout_read()
    cnt = L.count_hour_files(root)
    print(json.dumps({"file_counts": cnt}), flush=True)
    D = S.load_exploration(root)
    needed = int((L.HOLDOUT_END - L.EXPLORE_END).total_seconds() // 3600)
    meta: dict[str, Any] = {
        "schema": "dbc_holdout_m10_v1", "taken_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "holdout_read_enabled": L.HOLDOUT_READ_ENABLED,
        "commit": subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=Path(__file__).resolve().parent.parent).stdout.strip(),
        "file_sha256": {f: sha(Path(__file__).resolve().parent / f) for f in ("dbc_price.py", "dbc_screen.py", "arena_stack.py", "dbc_holdout_read.py")},
        "hours_needed": needed, "hours_present": len(D["good_hours"]), "bad_hours": D["bad_hours"], "order_fix": D["order_stats"],
    }
    if len(D["bad_hours"]) > MAX_BAD_HOURS:
        res = {**meta, "verdict": "NOT_DECIDABLE", "reason": f"{len(D['bad_hours'])} hours missing/incomplete (> {MAX_BAD_HOURS})"}
    else:
        cell = next(c for c in S.CELLS if c.name == CELL)
        att, refused, st = S.cell_attempts(cell, D)
        primary = S.price_cell(att, L.STAKE_DECIDING) if att else None
        res = {**meta, "cell": CELL, "attempts": len(att), "refused": refused, "trigger_prints": st["trigger_prints"], "hook_pool_share_by_day": hook_share_by_day(D)}
        if primary is None:
            res["verdict"] = "FAIL"
            res["reason"] = "no attempts"
        else:
            v = verdict(primary)
            res.update({"verdict": v["verdict"], "checks": v["checks"], "primary": primary,
                        "printed": {"attempts": len(att), "fills": primary["filled"], "guard_unfilled": primary["guard_unfilled"], "strands": primary["stranded"], "refusals_by_reason": refused,
                                    "fee_paid_bps": primary["fee_paid_bps"]}})
            sens: dict[str, Any] = {"strand_at_last_price": S.price_cell(att, L.STAKE_DECIDING, strand_at_last=True), "stake_1sol": S.price_cell(att, L.STAKE_BIG),
                                    "rent_charged": S.price_cell(att, L.STAKE_DECIDING, rent=L.RENT_REPORT), "sell_fee_as_in_5ea8bf17": S.price_cell(zero_legacy_sell_fee(att), L.STAKE_DECIDING)}
            for nm, (li, lo) in {"entry_lat_1p0": (1.0, L.LAT_S), "entry_lat_2p5": (2.5, L.LAT_S), "entry_lat_3p4": (3.4, L.LAT_S), "exit_lag_3p4": (L.LAT_S, 3.4)}.items():
                a2, _, _ = S.cell_attempts(cell, D, li, lo)
                sens[nm] = S.price_cell(a2, L.STAKE_DECIDING) if a2 else None
            res["sensitivities_report_only"] = sens
    res["note"] = "A PASS is exploration-to-forward evidence only, NOT a promotion-gate pass (QP P8). No sentence may say m10 makes money."
    txt = json.dumps(res, indent=1, default=float)
    (out_dir / OUT_JSON).write_text(txt)
    md = [f"# DBC m10 holdout one-look read ({meta['taken_utc']})", "", f"verdict: **{res['verdict']}**", "", f"commit {meta['commit']}", ""]
    md += [f"- {k}: {v}" for k, v in meta["file_sha256"].items()]
    if "primary" in res:
        for nm in ("flat", "press"):
            p = res["primary"][nm]
            md.append(f"- {nm}: n {p['n']} mean {p['mean']:.5f} total {p['total']:.3f} ex-top-3 {p['ex_top3']} ex-best-day {p['ex_best_day']:.3f} CI90 lo trade {p['ci90_lo_trade']} date {p['ci90_lo_date']} days {p['days_pos']}/{p['days']}")
        md.append(f"- checks: {json.dumps(res['checks'])}")
    md.append(f"- sha256 of {OUT_JSON}: {hashlib.sha256(txt.encode()).hexdigest()}")
    (out_dir / OUT_MD).write_text("\n".join(md) + "\n")
    print("\n".join(md))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--confirm-one-look", action="store_true", help="required: acknowledges that this is the single holdout read")
    ns = ap.parse_args(argv)
    if not ns.confirm_one_look:
        print("REFUSED: pass --confirm-one-look", file=sys.stderr)
        return 2
    return run(Path(ns.out))


if __name__ == "__main__":
    sys.exit(main())
