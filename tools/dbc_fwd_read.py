#!/usr/bin/env python3
"""dbc_fwd_read: the ONE-LOOK registered confirmation read of cell D1 m10 on [2026-11-01 window] = hours [2026-10-25T00, 2026-11-01T00) (docs/research/dbc-m10-forward/REGISTRATION.md,
QP-RULING-1024-dbc-m10-swap3 R1-R14). PAPER ONLY.

    python -m tools.dbc_fwd_read --confirm-one-look [--out DIR]

Enforced in code: (1) REFUSES before 2026-11-01T00:10Z (MIN_READ_UTC); (2) REFUSES if any output exists in --out, if the preflight fails (all 168 hour NAMES under FWD_ROOT, newest mtime
>= 10 min old) or if the marker HOLDOUT_READ_DONE_v3 exists (claimed atomically before the first window file is opened); (3) the data guard (tools.dbc_price.enable_forward_window) accepts only
hours in [2026-10-25T00, 2026-11-01T00) under FWD_ROOT and refuses every other hour; (4) the cell is frozen to m10 (no flag changes it); Swap3 pricing; (5) R4: configs referenced by window rows and
missing from the committed map are fetched (getMultipleAccounts, deterministic, sorted) BEFORE the load; undecodable ones are excluded and counted; > 5% of window SOL-quoted rows in them = NOT_DECIDABLE;
(6) > 6 incomplete hours = NOT_DECIDABLE; (7) the verdict is computed by verdict() (R11) on the PRIMARY line under BOTH fail models. A PASS is CONFIRMATION only: it licenses a forward paper
book, not a promotion and not a live licence.
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
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import numpy as np

from tools import dbc_config as C
from tools import dbc_price as L
from tools import dbc_screen as S

MAX_BAD_HOURS = 6
MAX_UNMAPPED_ROW_SHARE = 0.05
MIN_READ_UTC = datetime(2026, 11, 1, 0, 10, tzinfo=timezone.utc)
MARKER = "/data/mal/hunt-1008/push-1016/hunt29/dbc/HOLDOUT_READ_DONE_v3"
DEFAULT_OUT = "/data/mal/hunt-1008/push-1016/hunt29/dbc/fwd-read-v1"
BASE_MAP = str(Path(__file__).resolve().parent.parent / "docs/research/dbc-p17/data/cfg_summary.json")
MIN_AGE_S = 600
OUT_JSON, OUT_MD, CELL = "fwd-m10.json", "fwd-m10.md", "m10"
FILES = ("dbc_price.py", "dbc_screen.py", "dbc_config.py", "arena_stack.py", "dbc_fwd_read.py", "venue_backfill_dbc.py")
_CFG_RE = re.compile(rb'"config": ?"([1-9A-HJ-NP-Za-km-z]{32,44})"')
_WSOL = b"So11111111111111111111111111111111111111112"


def expected_names() -> list[str]:
    n = int((L.FWD_END - L.FWD_START).total_seconds() // 3600)
    return [f"tape-{(L.FWD_START + timedelta(hours=i)).strftime('%Y-%m-%dT%H')}.jsonl.zst" for i in range(n)]


def time_gate(now: datetime | None = None) -> tuple[bool, str]:
    now = now or datetime.now(timezone.utc)
    if now < MIN_READ_UTC:
        return False, f"too_early: now {now:%Y-%m-%dT%H:%M:%SZ} < {MIN_READ_UTC:%Y-%m-%dT%H:%M:%SZ}"
    return True, "ok"


def preflight(root: str, now: float | None = None, min_age_s: int = MIN_AGE_S) -> tuple[bool, str]:
    import time

    now = time.time() if now is None else now
    try:
        names = set(os.listdir(root))
    except OSError as e:
        return False, f"cannot_list_root: {e}"
    want = expected_names()
    missing = [n for n in want if n not in names]
    if missing:
        return False, f"missing_{len(missing)}_of_{len(want)}_hour_files (first: {missing[0]})"
    newest = os.lstat(root).st_mtime
    for n in names:
        newest = max(newest, os.lstat(os.path.join(root, n)).st_mtime)
    if now - newest < min_age_s:
        return False, f"newest_mtime_only_{int(now - newest)}s_old (< {min_age_s}s): a writer may still be active"
    return True, "ok"


def claim_marker(path: str) -> bool:
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o640)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w") as f:
        f.write(datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") + " forward one-look claimed\n")
    return True


def scan_configs(root: str) -> dict[str, int]:
    """config -> number of SOL-quoted trade lines, from the 168 window files (regex only; no JSON, no price, no signal). Guard-checked paths."""
    cnt: dict[str, int] = {}
    for h in L.exploration_hours():
        f = L.hour_file(h, root)
        if not os.path.exists(f):
            continue
        for line in L._zstd_lines(f, root):
            if b'"trade"' in line and _WSOL in line:
                m = _CFG_RE.search(line)
                if m:
                    k = m.group(1).decode()
                    cnt[k] = cnt.get(k, 0) + 1
    return cnt


def resolve_configs(counts: dict[str, int], base_map: dict[str, Any], fetch: Callable[[list[str]], dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
    """R4: fetch every window config missing from the map (sorted, 100 per call, deterministic). Returns (final map, report). Undecodable/absent = unmapped."""
    missing = sorted(k for k in counts if k not in base_map)
    raw: dict[str, Any] = {}
    for i in range(0, len(missing), 100):
        raw.update(fetch(missing[i:i + 100]))
    new = C.summary_map(raw)
    final = {**base_map, **new}
    unmapped = sorted(k for k in missing if k not in new)
    tot = sum(counts.values())
    um_rows = sum(counts[k] for k in unmapped)
    rep = {"window_configs": len(counts), "in_base_map": len(counts) - len(missing), "fetched_new": len(new), "unmapped_configs": len(unmapped), "unmapped_rows": um_rows,
           "sol_rows": tot, "unmapped_row_share": um_rows / max(tot, 1), "unmapped": unmapped}
    return final, rep


def helius_fetch(env_file: str | None = None, lockdir: str = "/data/mal/locks") -> Callable[[list[str]], dict[str, Any]]:
    import fcntl
    import time

    from tools import venue_backfill as vb

    url = vb.make_url(vb.read_key(env_file or vb.HELIUS_ENV))
    held = None
    while held is None:
        for i in (4, 3, 2, 1):
            fh = open(Path(lockdir) / f"helius-{i}.lock", "a")
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                held = fh
                break
            except OSError:
                fh.close()
        if held is None:
            time.sleep(15)
    call = vb.http_rpc(url, vb.Pacer(4.0))

    def fetch(keys: list[str]) -> dict[str, Any]:
        res = call("getMultipleAccounts", [keys, {"encoding": "base64", "commitment": "finalized"}])
        return {k: {"data_b64": v["data"][0]} for k, v in zip(keys, res["value"]) if v}

    return fetch


def verdict(primary: dict[str, Any], at_2p5: dict[str, Any] | None) -> dict[str, Any]:
    """R11 (+R9) on the primary line. `primary` = price_cell output; `at_2p5` = price_cell with a 2.5 s ENTRY leg (or None). PASS only if ALL hold under BOTH fail models; 0 fails."""
    checks: dict[str, Any] = {"filled_ge_100": primary["filled"] >= 100}
    allok = checks["filled_ge_100"]
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
            "entry_2p5s_mean_gt_0": bool(at_2p5 and at_2p5.get(nm, {}).get("n") and at_2p5[nm]["mean"] > 0),
        } if s.get("n", 0) else {"no_attempts": False}
        checks[nm] = c
        allok = allok and all(c.values())
    return {"verdict": "PASS" if allok else "FAIL", "checks": checks, "filled": primary["filled"]}


def sha(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def day_shares(D: dict[str, Any]) -> dict[str, Any]:
    from datetime import datetime as dt

    bt = D["a"]["bt"]
    days = (bt // 86400).astype(np.int64)
    pid = D["pid"]
    out = {}
    for u in np.unique(days):
        m = days == u
        n = max(int(m.sum()), 1)
        row = {"rows": int(m.sum()), "swap3_share": float((m & (D["a"]["sw3"] == 1)).sum() / n)}
        for k, ps in D["excl"].items():
            row[f"{k}_excluded_share"] = float((m & np.isin(pid, np.array(sorted(ps), np.int64))).sum() / n) if ps else 0.0
        out[dt.fromtimestamp(int(u) * 86400, tz=timezone.utc).strftime("%Y-%m-%d")] = row
    return out


def run(out_dir: Path, root: str | None = None, now: datetime | None = None, fetch: Callable[[list[str]], dict[str, Any]] | None = None, marker: str | None = None,
        base_map: str = BASE_MAP, make_fetch: Callable[[], Callable[[list[str]], dict[str, Any]]] | None = None) -> int:
    root = root or L.FWD_ROOT
    marker = marker or MARKER
    ok, why = time_gate(now)
    if not ok:
        print(f"REFUSED (time gate, marker NOT claimed): {why}", file=sys.stderr)
        return 6
    if (out_dir / OUT_JSON).exists() or (out_dir.exists() and any(out_dir.iterdir())):
        print(f"REFUSED: output already exists in {out_dir} (one look only)", file=sys.stderr)
        return 3
    out_dir = Path(S.check_out_dir(str(out_dir)))
    ok, why = preflight(root)
    if not ok:
        print(f"REFUSED (preflight, marker NOT claimed): {why}", file=sys.stderr)
        return 5
    try:  # everything that can fail for operational reasons is built/validated BEFORE the marker is claimed (reviewer note): fetcher (key file, lock), out dir
        fetcher = fetch or (make_fetch or helius_fetch)()
        out_dir.mkdir(parents=True, exist_ok=True)
        if not os.access(out_dir, os.W_OK):
            raise OSError(f"out dir not writable: {out_dir}")
    except Exception as e:  # noqa: BLE001
        print(f"REFUSED (setup, marker NOT claimed): {type(e).__name__}: {str(e)[:120]}", file=sys.stderr)
        return 7
    if not claim_marker(marker):
        print(f"REFUSED: one-look marker {marker} exists", file=sys.stderr)
        return 4
    L.enable_swap3_pricing()
    L.enable_forward_window(root)
    counts = scan_configs(root)
    final, rep = resolve_configs(counts, json.loads(Path(base_map).read_text()), fetcher)
    (out_dir / "cfg_map_final.json").write_text(json.dumps(final, sort_keys=True))
    L.load_config_thresholds(str(out_dir / "cfg_map_final.json"))
    L.CFG_UNMAPPED.clear()
    L.CFG_UNMAPPED.update(rep["unmapped"])
    print("loaded: window config map ready; the look is consumed", flush=True)  # R14: consumed here
    meta: dict[str, Any] = {
        "schema": "dbc_fwd_m10_v1", "taken_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "commit": subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=Path(__file__).resolve().parent.parent).stdout.strip(),
        "file_sha256": {f: sha(Path(__file__).resolve().parent / f) for f in FILES}, "config_map": {k: v for k, v in rep.items() if k != "unmapped"}, "unmapped_configs": rep["unmapped"],
    }
    D = None
    if rep["unmapped_row_share"] > MAX_UNMAPPED_ROW_SHARE:
        res = {**meta, "verdict": "NOT_DECIDABLE", "reason": f"unmapped config rows {rep['unmapped_row_share']:.3f} > {MAX_UNMAPPED_ROW_SHARE}"}
    else:
        try:
            D = S.load_exploration(root)
        except L.Refused as e:
            res = {**meta, "verdict": "NOT_DECIDABLE", "reason": f"no usable window hours: {e}"}
    if D is not None:
        needed = len(expected_names())
        meta.update({"hours_needed": needed, "hours_present": len(D["good_hours"]), "bad_hours": D["bad_hours"], "order_fix": D["order_stats"],
                     "slot_s_by_hour": {str(k): v for k, v in sorted(D["slot_s"].items())}, "shares_by_day": day_shares(D)})
        if len(D["bad_hours"]) > MAX_BAD_HOURS:
            res = {**meta, "verdict": "NOT_DECIDABLE", "reason": f"{len(D['bad_hours'])} hours missing/incomplete (> {MAX_BAD_HOURS})"}
        else:
            cell = next(c for c in S.CELLS if c.name == CELL)
            att, refused, st = S.cell_attempts(cell, D)
            primary = S.price_cell(att, L.STAKE_DECIDING) if att else None
            res = {**meta, "cell": CELL, "attempts": len(att), "attempts_by_day": {u: sum(1 for a in att if a["date"] == u) for u in sorted({a["date"] for a in att})},
                   "refused": refused, "trigger_prints": st["trigger_prints"]}
            if primary is None:
                res["verdict"] = "FAIL" if len(att) else "NOT_DECIDABLE"
                res["reason"] = "no attempts"
            else:
                a25, _, _ = S.cell_attempts(cell, D, 2.5, L.LAT_S)
                p25 = S.price_cell(a25, L.STAKE_DECIDING) if a25 else None
                v = verdict(primary, p25)
                res.update({"verdict": v["verdict"], "checks": v["checks"], "primary": primary, "entry_2p5s_required": p25,
                            "printed": {"attempts": len(att), "fills": primary["filled"], "guard_unfilled": primary["guard_unfilled"], "strands": primary["stranded"],
                                        "refusals_by_reason": refused, "fee_paid_bps": primary["fee_paid_bps"]}})
                if primary["filled"] < 100:
                    res["verdict"], res["reason"] = "NOT_DECIDABLE", "fewer than 100 fills (R1)"
                sens: dict[str, Any] = {"strand_at_last_price": S.price_cell(att, L.STAKE_DECIDING, strand_at_last=True), "stake_1sol": S.price_cell(att, L.STAKE_BIG),
                                        "rent_charged": S.price_cell(att, L.STAKE_DECIDING, rent=L.RENT_REPORT)}
                for nm, (li, lo) in {"entry_lat_1p0": (1.0, L.LAT_S), "entry_lat_1p3": (1.3, L.LAT_S), "entry_lat_3p4": (3.4, L.LAT_S), "exit_lag_3p4": (L.LAT_S, 3.4)}.items():
                    a2, _, _ = S.cell_attempts(cell, D, li, lo)
                    sens[nm] = S.price_cell(a2, L.STAKE_DECIDING) if a2 else None
                Dn = {**D, "bad_row": np.zeros_like(D["bad_row"])}
                a3, _, _ = S.cell_attempts(cell, Dn)
                sens["no_bad_row_exclusion"] = S.price_cell(a3, L.STAKE_DECIDING) if a3 else None
                res["sensitivities_report_only"] = sens
    res["note"] = "CONFIRMATION only (QP swap3 ruling): a PASS licenses a forward paper DBC book, not a promotion and not a live licence. No sentence may say m10 makes money."
    txt = json.dumps(res, indent=1, default=float)
    (out_dir / OUT_JSON).write_text(txt)
    md = [f"# DBC m10 registered forward read ({meta['taken_utc']})", "", f"verdict: **{res['verdict']}**", "", f"commit {meta['commit']}", ""]
    md += [f"- {k}: {v}" for k, v in meta["file_sha256"].items()]
    if "primary" in res:
        for nm in ("flat", "press"):
            p = res["primary"][nm]
            md.append(f"- {nm}: n {p['n']} mean {p['mean']:.5f} total {p['total']:.3f} ex-top-3 {p['ex_top3']} ex-best-day {p['ex_best_day']:.3f} CI90 lo trade {p['ci90_lo_trade']} days {p['days_pos']}/{p['days']}")
        md.append(f"- checks: {json.dumps(res['checks'])}")
    md.append(f"- sha256 of {OUT_JSON}: {hashlib.sha256(txt.encode()).hexdigest()}")
    (out_dir / OUT_MD).write_text("\n".join(md) + "\n")
    print("\n".join(md))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--confirm-one-look", action="store_true")
    ns = ap.parse_args(argv)
    if not ns.confirm_one_look:
        print("REFUSED: pass --confirm-one-look", file=sys.stderr)
        return 2
    return run(Path(ns.out))


if __name__ == "__main__":
    sys.exit(main())
