#!/usr/bin/env python3
"""dbc_fwd_read_am1: the ONE-LOOK registered forward read of DBC m10 under AMENDMENT 1 (docs/research/dbc-m10-forward/AMENDMENT-1.md = QP-RULING-1024-m10-lookahead.md section 2,
AM1-AM6). PAPER ONLY. Window [2026-10-25T00, 2026-11-01T00), read at or after 2026-11-01T00:10Z.

    python3 -m tools.dbc_fwd_read_am1 --confirm-one-look [--out DIR]

Identical to tools/dbc_fwd_read.py (tag dbc-m10-forward-v1, sha256 ccffd905...) in everything AM1-AM5 do not change; its functions are IMPORTED, not copied:
time gate (MIN_READ_UTC), output-exists refusal, names-plus-lstat preflight on FWD_ROOT, setup (fetcher, out dir) validated before the claim, the SAME marker v3
(tools.dbc_fwd_read.MARKER, O_CREAT|O_EXCL: only ONE of the two tools can ever read), "LOOK CONSUMED" printed right after the claim and before the window guard opens, Swap3 pricing,
dbc_price.enable_forward_window, R4 config map (scan, fetch missing, cfg_map_final.json, unmapped share > 5% = NOT_DECIDABLE), > 6 bad hours = NOT_DECIDABLE, k = 1.

What Amendment 1 changes:
* AM1 deciding set = the live-policy decisions of tools/dbc_fwd.Engine, CAUSAL (no oracle), fed the window tape in tape (slot) order, first_ok_bt = 2026-10-25T01:00Z,
  ss_live exactly as dbc_fwd.replay (previous hour's measured slot length, dbc_screen.slot_s_for). Each sealed decision is priced by dbc_screen.resolve(m10, P, [i], D, comp_slot)
  on the full-window dbc_screen.load_exploration D, i = the row with the same key (pool|slot|post sqrt price), then the dbc_screen.price_cell legs
  (tools/dbc_fwd_sec3.Pricer / price: the code that produced the section-3 result).
* AM2 refusals of a sealed decision, every count printed: bad_state -> priced on D with bad_row zeroed; completed_before_entry and no_state -> -160,000 lamports; block_edge and bad_hour
  -> excluded; key not found -> full loss (stake + send + 2,039,280 rent); > 1% not found -> NOT_DECIDABLE. Edge rules (SEC3-DECLARE.md): bad_state still refused on the zeroed D and
  any other refusal -> full loss.
* AM3 R11 and the R9 2.5 s entry condition (tools.dbc_fwd_read.verdict) on this set under BOTH fail models; fewer than 100 fills = NOT_DECIDABLE (R1).
* AM4 the as-registered screen set (dbc_screen.cell_attempts + price_cell) is printed REPORT-ONLY with the overlap counts and per-model means of the screen-only and causal-only
  entries. It never enters the verdict.
A PASS here is the CONFIRMATION of R1-R14 as amended: it licenses a forward paper book (own registration, day-1 dry score, clean clock), not a promotion and not a live licence.
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
from typing import Any, Callable

from tools import dbc_fwd as W
from tools import dbc_fwd_read as R
from tools import dbc_fwd_sec3 as X
from tools import dbc_price as L
from tools import dbc_screen as S

AMENDMENT = "AMENDMENT-1 (QP-RULING-1024-m10-lookahead.md section 2, AM1-AM6)"
FIRST_OK = datetime(2026, 10, 25, 1, 0, tzinfo=timezone.utc)
MARKER = R.MARKER  # the SAME one-look marker as the unamended tool
DEFAULT_OUT = "/data/mal/hunt-1008/push-1016/hunt29/dbc/fwd-read-v1-am1"
OUT_JSON, OUT_MD = "fwd-m10-am1.json", "fwd-m10-am1.md"
FILES = ("dbc_price.py", "dbc_screen.py", "dbc_config.py", "arena_stack.py", "dbc_fwd_read.py", "dbc_fwd_read_am1.py", "dbc_fwd.py", "dbc_fwd_sec3.py", "venue_backfill_dbc.py",
         "dbc_decode.py", "arena_declog.py")


def deciding_set(root: str) -> tuple[list[tuple[str, float]], dict[str, Any]]:
    """AM1: the causal engine's live-policy decisions on the window tape (guarded reads, tape order). Returns ([(key, block time)], engine stats)."""
    live_ss: dict[int, float] = {}
    eng = W.Engine(FIRST_OK.timestamp(), ss_live=lambda hk, m=live_ss: S.slot_s_for(m, hk), log=None, meta={"bt_src": "tape_block_time"})
    hours = W.stream_hours(eng, L.FWD_START, L.FWD_END, root, live_ss, None)
    return [(x["key"], float(x["bt"])) for x in eng.decisions_live], {**eng.stats, "hours_streamed": hours, "live_slot_s": {str(k): v for k, v in sorted(live_ss.items())}}


def means(p: dict[str, Any]) -> dict[str, Any]:
    return {m: ({k: p[m].get(k) for k in ("n", "mean", "total")} if p[m].get("n") else {"n": 0}) for m in ("flat", "press")}


def run(out_dir: Path, root: str | None = None, now: datetime | None = None, fetch: Callable[[list[str]], dict[str, Any]] | None = None, marker: str | None = None,
        base_map: str = R.BASE_MAP, make_fetch: Callable[[], Callable[[list[str]], dict[str, Any]]] | None = None) -> int:
    root = root or L.FWD_ROOT
    marker = marker or MARKER
    ok, why = R.time_gate(now)
    if not ok:
        print(f"REFUSED (time gate, marker NOT claimed): {why}", file=sys.stderr)
        return 6
    if (out_dir / OUT_JSON).exists() or (out_dir.exists() and any(out_dir.iterdir())):
        print(f"REFUSED: output already exists in {out_dir} (one look only)", file=sys.stderr)
        return 3
    out_dir = Path(S.check_out_dir(str(out_dir)))
    ok, why = R.preflight(root)
    if not ok:
        print(f"REFUSED (preflight, marker NOT claimed): {why}", file=sys.stderr)
        return 5
    try:
        fetcher = fetch or (make_fetch or R.helius_fetch)()
        out_dir.mkdir(parents=True, exist_ok=True)
        if not os.access(out_dir, os.W_OK):
            raise OSError(f"out dir not writable: {out_dir}")
    except Exception as e:  # noqa: BLE001
        print(f"REFUSED (setup, marker NOT claimed): {type(e).__name__}: {str(e)[:120]}", file=sys.stderr)
        return 7
    if not R.claim_marker(marker):
        print(f"REFUSED: one-look marker {marker} exists", file=sys.stderr)
        return 4
    print("LOOK CONSUMED: marker claimed; any later failure is NOT_DECIDABLE and ends m10 (R14)", flush=True)
    L.enable_swap3_pricing()
    L.enable_forward_window(root)
    counts = R.scan_configs(root)
    final, rep = R.resolve_configs(counts, json.loads(Path(base_map).read_text()), fetcher)
    (out_dir / "cfg_map_final.json").write_text(json.dumps(final, sort_keys=True))
    L.load_config_thresholds(str(out_dir / "cfg_map_final.json"))
    L.CFG_UNMAPPED.clear()
    L.CFG_UNMAPPED.update(rep["unmapped"])
    here = Path(__file__).resolve().parent
    meta: dict[str, Any] = {
        "schema": "dbc_fwd_m10_am1_v1", "amendment": AMENDMENT, "taken_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "commit": subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=here.parent).stdout.strip(),
        "file_sha256": {f: R.sha(here / f) for f in FILES}, "config_map": {k: v for k, v in rep.items() if k != "unmapped"}, "unmapped_configs": rep["unmapped"],
        "first_ok_bt_utc": FIRST_OK.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    D = None
    res: dict[str, Any]
    if rep["unmapped_row_share"] > R.MAX_UNMAPPED_ROW_SHARE:
        res = {**meta, "verdict": "NOT_DECIDABLE", "reason": f"unmapped config rows {rep['unmapped_row_share']:.3f} > {R.MAX_UNMAPPED_ROW_SHARE}"}
    else:
        try:
            D = S.load_exploration(root)
        except L.Refused as e:
            res = {**meta, "verdict": "NOT_DECIDABLE", "reason": f"no usable window hours: {e}"}
    if D is not None:
        meta.update({"hours_needed": len(R.expected_names()), "hours_present": len(D["good_hours"]), "bad_hours": D["bad_hours"], "order_fix": D["order_stats"],
                     "slot_s_by_hour": {str(k): v for k, v in sorted(D["slot_s"].items())}, "shares_by_day": R.day_shares(D)})
        if len(D["bad_hours"]) > R.MAX_BAD_HOURS:
            res = {**meta, "verdict": "NOT_DECIDABLE", "reason": f"{len(D['bad_hours'])} hours missing/incomplete (> {R.MAX_BAD_HOURS})"}
        else:
            res = decide(meta, D, root)
    res["note"] = ("CONFIRMATION only (QP lookahead ruling, Amendment 1): a PASS licenses a forward paper DBC book, not a promotion and not a live licence. "
                   "The as-registered screen set is REPORT-ONLY. No sentence may say m10 makes money.")
    txt = json.dumps(res, indent=1, default=float)
    (out_dir / OUT_JSON).write_text(txt)
    md = [f"# DBC m10 registered forward read, Amendment 1 ({res['taken_utc']})", "", f"verdict (AM1 causal deciding set): **{res['verdict']}**", "", f"commit {res['commit']}", ""]
    md += [f"- {k}: {v}" for k, v in res["file_sha256"].items()]
    if "deciding" in res:
        p = res["deciding"]["primary_1p9s"]
        md.append(f"- deciding set: {res['deciding']['n_decisions']} sealed decisions, priced {p['n_priced']}, fills {p['fills']}, guard-unfilled {p['guard_unfilled']}, strands {p['strands']}")
        md.append(f"- AM2 counts 1.9 s: {json.dumps(p['counts'])}; 2.5 s: {json.dumps(res['deciding']['entry_2p5s_counts'])}")
        for nm in ("flat", "press"):
            s = p[nm]
            if s.get("n"):
                md.append(f"- {nm}: n {s['n']} mean {s['mean']:.6f} total {s['total']:.4f} ex-top-3 {s['ex_top3']} ex-best-day {s['ex_best_day']:.4f} CI90 lo trade {s['ci90_lo_trade']} "
                          f"days {s['days_pos']}/{s['days']} per-day n {s['per_day_n']} per-day SOL {s['per_day']}; 2.5 s entry mean {res['deciding']['entry_2p5s'][nm].get('mean')}")
        md.append(f"- checks: {json.dumps(res.get('checks'))}")
        md.append(f"- AM4 REPORT-ONLY overlap: {json.dumps(res['screen_report_only']['overlap'])}")
    md.append(f"- sha256 of {OUT_JSON}: {hashlib.sha256(txt.encode()).hexdigest()}")
    (out_dir / OUT_MD).write_text("\n".join(md) + "\n")
    print("\n".join(md))
    return 0


def decide(meta: dict[str, Any], D: dict[str, Any], root: str) -> dict[str, Any]:
    """AM1-AM4 on a loaded window D (called after the claim)."""
    c1, est = deciding_set(root)
    pr = X.Pricer(D)
    r19, r25 = pr.resolve_set(c1, L.LAT_S), pr.resolve_set(c1, 2.5)
    p19, p25 = X.price(r19), X.price(r25)
    nf = r19["counts"]["key_not_found_full_loss"] / max(len(c1), 1)
    res: dict[str, Any] = {**meta, "cell": "m10", "deciding_set": "AM1 causal live-policy decisions (tools/dbc_fwd.Engine, no oracle)",
                           "deciding": {"n_decisions": len(c1), "md5_keys": W.md5_keys(k for k, _ in c1), "primary_1p9s": p19, "entry_2p5s": {m: p25[m] for m in ("flat", "press")},
                                        "entry_2p5s_counts": p25["counts"], "not_found_share": nf, "key_index": {"rows": len(pr.index), "duplicate_keys": len(pr.dup)},
                                        "attempts_by_day": {u: sum(1 for _, b in c1 if S.day_of(b) == u) for u in sorted({S.day_of(b) for _, b in c1})}},
                           "engine_stats": est}
    # AM4: the as-registered screen set, REPORT-ONLY (never enters the verdict)
    att, refused, st = S.cell_attempts(W.CELL, D)
    A, pid = D["a"], D["pid"]
    s_of: dict[int, int] = {}
    for j in range(len(pid)):
        s_of.setdefault(int(pid[j]), j)
    s0 = [(W.key_of(D["pool_names"][a["pid"]], int(A["slot"][s_of[a["pid"]] + a["i"]]), float(A["S1"][s_of[a["pid"]] + a["i"]])), float(a["bt"])) for a in att]
    k1, k0 = {k for k, _ in c1}, {k for k, _ in s0}
    so, co = [x for x in s0 if x[0] not in k1], [x for x in c1 if x[0] not in k0]
    res["screen_report_only"] = {
        "REPORT_ONLY": True, "attempts": len(s0), "md5_keys": W.md5_keys(k0), "refused": refused, "trigger_prints": st["trigger_prints"],
        "as_registered_price_cell": S.price_cell(att, L.STAKE_DECIDING) if att else None,
        "overlap": {"deciding": len(k1), "screen": len(k0), "both": len(k1 & k0), "screen_only": len(so), "causal_only": len(co)},
        "screen_only_means": means(X.price(pr.resolve_set(so, L.LAT_S))) if so else None,
        "causal_only_means": means(X.price(pr.resolve_set(co, L.LAT_S))) if co else None,
    }
    if nf > X.MAX_NOT_FOUND:
        res.update({"verdict": "NOT_DECIDABLE", "reason": f"decisions not found in D {nf:.4f} > {X.MAX_NOT_FOUND} (AM2)"})
        return res
    if not p19.get("fills") and not p19["flat"].get("n"):
        res.update({"verdict": "NOT_DECIDABLE", "reason": "no priced decisions"})
        return res
    v = R.verdict({"filled": p19["fills"], "flat": p19["flat"], "press": p19["press"]}, {"flat": p25["flat"], "press": p25["press"]})
    res.update({"verdict": v["verdict"], "checks": v["checks"]})
    if p19["fills"] < 100:
        res["verdict"], res["reason"] = "NOT_DECIDABLE", "fewer than 100 fills (R1)"
    return res


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
