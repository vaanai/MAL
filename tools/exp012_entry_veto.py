#!/usr/bin/env python3
"""EXP-012 simple entry-veto rules at the frozen point, paired against the frozen point. **EXPLORATION, BEST-OF-N,
NOT A PROMOTE, NOT GATE EVIDENCE.**

Motivation (live trading, fixed build): take-profits land at +0.51..+0.67 ret, stop-losses at -0.30..-0.43; the stops eat
the edge. Question: does a SIMPLE veto, using only information the executor has when it decides to send, improve SOL
per trade against the frozen point, paired on the same mints?

Frozen point: threshold 0.8030766588450794 (stored OOF score), k = 6 slots after the first PumpSwap print, 0.05 SOL,
per-side fee 505,000 lamports, frozen tp50_sl30 exit, V pricing. Only the 9-day exploration pool is read, through the
same guarded roots as tools.exp012_operating_point. Never the EXP-012 holdout, the backup block, the EXP-011 block or
anything from 2026-10-02 onwards.

What is knowable at send time (the whole point of this module)
  mig = the migration slot (first PumpSwap print). The entry lands at mig + 6. The tx is sent before it lands, and the
  feed reaches the executor with some lag. The veto therefore reads ONLY PumpSwap prints with
      mig + 1 <= slot <= mig + 2          (OBS_FIRST_OFFSET .. OBS_LAST_OFFSET)
  mig+1 is the decision state; mig+2 is the most the brief allows. Nothing at slot mig+3 or later is read: that is
  the landing state (mig+5, bound "start") and the veto must not see it. The feed lag is NOT measured here: if the live
  listener lags by more than about 2 slots then even the mig+2 prints are not knowable at send and only the mig+1 prints
  are safe. Prints at the migration slot itself are not used (the migrate tx's own print). The creator-sold flag uses the
  trader field of PumpSwap sell rows in the same slots, matched to the creator on the create row.
  A vetoed entry sends no tx and costs nothing (scored 0 in the pairing, so a veto is charged what the trade would have
  earned). A window with no print is "no information": no rule fires on it.

Pre-specified veto family (written before any data was read), N = 7 rules, frozen point always a candidate:
  drift_gt_25     price at the last window print / price at the migration-slot print - 1 > 0.25
  drift_gt_50     the same, > 0.50
  netflow_neg     window buy SOL - window sell SOL < 0 (more selling than buying)
  maxsell_ge_2sol largest single window sell >= 2 SOL
  maxsell_ge_5sol largest single window sell >= 5 SOL
  creator_sold    the creator wallet sold in the window
  sells_ge_buys   at least one sell and count of sells >= count of buys in the window

Selection: nested leave-one-day-out over the family (pick on 8 days by pooled pressure paired mean vs frozen, among
candidates with >= 100 entered training trades; the frozen point is always a candidate, its paired difference is 0;
score the 9th day). UPPER BOUND: the stored OOF entry scores were produced by 9-fold models that saw the other days, and
the rule family was written after the live probe's stop-loss pattern was known, on a tape this exploration has already
used 54 times.
CIs: the gate's cluster bootstrap (1,000 draws, seed 1) under flat 15% and pressure.

Full run (see the PR body):
  nice -n 19 /data/mal/venv/bin/python -m tools.exp012_entry_veto --verify-view \
    --out-dir /data/mal/exp012-veto --tries-log /data/mal/ops/tries-exp012-veto.jsonl \
    --fast-dir /data/mal/clean-view/fast-pool-2026-09-18T23_2026-09-22T00 \
    --oracle-insample-dir /data/mal/clean-view/oracle-insample-2026-09-22_25 \
    --oracle-live-dir /data/mal/clean-view/oracle-live-2026-09-25_27
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Sequence

import tools.exploration_entry_model as eem
import tools.exp012_operating_point as op
from tools.exp011_freeze import TARGET_SPEC_ID, add_root_args
from tools.exp012_exit_sensitivity import paired_side
from tools.exp012_latency_sensitivity import DEFAULT_ARTIFACT_DIR, guarded_roots, load_oof, write_rows
from tools.exp012_latency_virtual import DEFAULT_VMAP, set_env
from tools.exploration_entry_model import iter_rows_jsonl

LAMPORTS = 1_000_000_000
FROZEN_THRESHOLD = op.FROZEN_THRESHOLD
K = op.FROZEN_K
SIZE_SOL = op.PRIMARY_SIZE_SOL
FEE = op.PRIMARY_FEE
FROZEN_CELL = op.FROZEN_CELL
OBS_FIRST_OFFSET = 1
OBS_LAST_OFFSET = 2
NESTED_MIN_TRAIN_TRADES = 100
EXISTING_TRIES_ON_POOL = 54  # 18 + the 36 operating-point cells
SCRATCH_ROWS = "veto_rows.jsonl"
TRIES_MARKER = "tries_logged.marker"
TOOL = "tools.exp012_entry_veto"
BANNER = "EXPLORATION, BEST-OF-N, NOT A PROMOTE, NOT GATE EVIDENCE"
ENV_ARTIFACT = "MAL_VETO_ARTIFACT"
ENV_TMIN = "MAL_VETO_TMIN"
MAX_SELL_RECORDS = 300  # per mint, side channel for the creator-sold flag
FROZEN_ID = "frozen"


# --- the rule family (declared before any data is read) ------------------------------------------------------

RULES: dict[str, Callable[[Mapping[str, Any]], bool]] = {
    "drift_gt_25": lambda f: f["drift"] > 0.25,
    "drift_gt_50": lambda f: f["drift"] > 0.50,
    "netflow_neg": lambda f: f["buy_sol"] - f["sell_sol"] < 0,
    "maxsell_ge_2sol": lambda f: f["max_sell"] >= 2 * LAMPORTS,
    "maxsell_ge_5sol": lambda f: f["max_sell"] >= 5 * LAMPORTS,
    "creator_sold": lambda f: bool(f["creator_sold"]),
    "sells_ge_buys": lambda f: f["n_sells"] >= 1 and f["n_sells"] >= f["n_buys"],
}
RULE_IDS = tuple(RULES)


def vetoed(rule: str, feats: Mapping[str, Any] | None) -> bool:
    """A mint with no feature record is never vetoed (no information)."""
    return bool(feats) and RULES[rule](feats)


# --- send-time features (pure) ---------------------------------------------------------------------------------


def veto_features(
    fills: Sequence[Any],
    mig_slot: int,
    ref_price: float | None,
    creator_sell_slots: Sequence[int] = (),
    first_off: int = OBS_FIRST_OFFSET,
    last_off: int = OBS_LAST_OFFSET,
) -> dict[str, Any]:
    """Features from PumpSwap prints with mig_slot+first_off <= slot <= mig_slot+last_off ONLY. A print or a creator
    sell at any later slot is never read (it is the landing state, not the send-time state)."""
    lo, hi = mig_slot + first_off, mig_slot + last_off
    win = [p for p in fills if p.venue == "pumpswap" and lo <= p.slot <= hi]
    buys = [p.sol_lamports for p in win if p.side == "buy"]
    sells = [p.sol_lamports for p in win if p.side == "sell"]
    drift = 0.0
    if win and ref_price and ref_price > 0:
        drift = win[-1].price_sol / ref_price - 1.0
    return {
        "n_window": len(win),
        "n_buys": len(buys),
        "n_sells": len(sells),
        "buy_sol": sum(buys),
        "sell_sol": sum(sells),
        "max_sell": max(sells) if sells else 0,
        "drift": drift,
        "creator_sold": any(lo <= s <= hi for s in creator_sell_slots),
        "window": [lo, hi],
    }


# --- tape pass -----------------------------------------------------------------------------------------------------

_SELLS: dict[str, list[tuple[int, str]]] = {}


def _sell_recorder(orig: Callable[[dict[str, Any]], Any]) -> Callable[[dict[str, Any]], Any]:
    """Wrap print_from_trade_row: remember (slot, trader) of PumpSwap sell rows per mint (capped), for the
    creator-sold flag. Does not alter the row or the result."""

    def wrapped(row: dict[str, Any]) -> Any:
        if row.get("venue") == "pumpswap" and row.get("side") == "sell":
            m, tr = row.get("mint"), row.get("trader")
            if isinstance(m, str) and isinstance(tr, str):
                lst = _SELLS.setdefault(m, [])
                if len(lst) < MAX_SELL_RECORDS:
                    try:
                        lst.append((int(row.get("slot") or 0), tr))
                    except (TypeError, ValueError):
                        pass
        return orig(row)

    return wrapped


@contextlib.contextmanager
def veto_patch(scores: Mapping[str, float], t_min: float) -> Iterator[None]:
    """The operating-point multi-cell patch at ONE cell (k=6, 0.05 SOL), plus the send-time features attached to every
    cached row, plus the sell recorder. Restores everything on exit."""
    orig_print = eem.print_from_trade_row
    _SELLS.clear()
    with op.multi_cell_patch(scores, t_min, ks=(K,), sizes_sol=(SIZE_SOL,)):
        inner = eem.score_one

        def with_veto(mint_id: str, mint: Any, feat: Any, curve: Any, through_ms: int, creator_hist: Any, **kw: Any) -> list[dict[str, Any]]:
            sells = _SELLS.pop(mint_id, [])
            rows = inner(mint_id, mint, feat, curve, through_ms, creator_hist, **kw)
            if not rows:
                return rows
            fills, trig_slot, _ms, ref = eem._fills_for(mint, migrate=True)
            creator = getattr(feat, "creator", "") or ""
            csl = sorted({s for s, tr in sells if creator and tr == creator})
            v = veto_features(fills, trig_slot, ref, csl)
            for r in rows:
                r["veto"] = v
            return rows

        eem.score_one = with_veto
        eem.print_from_trade_row = _sell_recorder(orig_print)
        try:
            yield
        finally:
            eem.print_from_trade_row = orig_print
            eem.score_one = inner


def _worker(which: str, *args: Any, **kw: Any) -> Any:
    from tools import pumpswap_virtual_adapter as ad

    scores, _thr, _doc, _days = load_oof(Path(os.environ[ENV_ARTIFACT]))
    with veto_patch(scores, float(os.environ[ENV_TMIN])):
        return getattr(ad, f"worker_{which}")(*args, **kw)


def veto_worker_a(*args: Any, **kw: Any) -> Any:
    return _worker("a", *args, **kw)


def veto_worker_b(*args: Any, **kw: Any) -> Any:
    return _worker("b", *args, **kw)


def veto_worker_c(*args: Any, **kw: Any) -> Any:
    return _worker("c", *args, **kw)


@contextlib.contextmanager
def patched_veto_workers() -> Iterator[None]:
    import tools.exploration_entry_model_b2 as b2
    import tools.exploration_entry_model_b3 as b3

    saved = (eem.run_worker_a, b2.run_worker_b, b3.run_worker_c)
    eem.run_worker_a, b2.run_worker_b, b3.run_worker_c = veto_worker_a, veto_worker_b, veto_worker_c
    try:
        yield
    finally:
        eem.run_worker_a, b2.run_worker_b, b3.run_worker_c = saved


def collect_rows(fast: Path, insample: Path, live: Path, scratch: Path, artifact_dir: Path, vmap: str, max_workers: int = 2, buffer_hours: int = 24, max_home_hours: int | None = 12) -> list[dict[str, Any]]:
    from tools.exploration_entry_model_b2 import run_all_features_b
    from tools.exploration_entry_model_b3 import run_all_features_c

    if max_workers > 2:
        raise SystemExit("keep max-workers <= 2")
    set_env(vmap, scratch / "counts_virtual")
    os.environ[ENV_ARTIFACT], os.environ[ENV_TMIN] = str(artifact_dir), repr(FROZEN_THRESHOLD)
    common = dict(max_workers=max_workers, buffer_hours=buffer_hours, max_home_hours=max_home_hours)
    passes = (
        ("A", lambda: eem.run_all_features(out_dir=scratch / "poolA", backfill=fast, **common)),
        ("C", lambda: run_all_features_c(out_dir=scratch / "poolC", root=insample, **common)),
        ("B", lambda: run_all_features_b(out_dir=scratch / "poolB", root=live, **common)),
    )
    out: list[dict[str, Any]] = []
    with patched_veto_workers():
        for pool, fn in passes:
            print(f"entry-veto pass: pool {pool}...", file=sys.stderr, flush=True)
            for r in fn():
                if r.get("spec") == TARGET_SPEC_ID:
                    r["pool"] = pool
                    out.append(r)
    out.sort(key=lambda r: (r["day"] or "", r["mint"]))
    return out


# --- analysis (pure; tested on fixtures) --------------------------------------------------------------------------


def frozen_trades(rows: Sequence[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Mapping[str, Any] | None], int]:
    """(frozen-point trades, veto features by mint, n_censored). Only the frozen cell's rows are used."""
    idx = op.index_rows(rows)
    trades, cen = op.cell_trades(idx, FROZEN_CELL)
    size = op.size_lamports(SIZE_SOL)
    feats = {r["mint"]: r.get("veto") for r in rows if int(r["k"]) == K and int(r["size"]) == size}
    return trades, feats, cen


def kept_trades(trades: Sequence[Mapping[str, Any]], feats: Mapping[str, Any], rule: str | None) -> list[Mapping[str, Any]]:
    if rule is None:
        return list(trades)
    return [t for t in trades if not vetoed(rule, feats.get(t["mint"]))]


def _mean(xs: Sequence[float]) -> float | None:
    return sum(xs) / len(xs) / LAMPORTS if xs else None


def rule_stats(trades: Sequence[Mapping[str, Any]], feats: Mapping[str, Any], rule: str, n_days: int) -> dict[str, Any]:
    kept = kept_trades(trades, feats, rule)
    kept_m = {t["mint"] for t in kept}
    gone = [t for t in trades if t["mint"] not in kept_m]
    pr = op.paired_vs_reference(trades, kept)
    cs = op.cell_stats(list(kept), FROZEN_CELL, n_days)
    return {
        "rule": rule,
        "n_vetoed": len(gone),
        "n_kept": len(kept),
        "vetoed_press_mean_sol": _mean([t["press"] for t in gone]),
        "vetoed_flat_mean_sol": _mean([t["flat"] for t in gone]),
        "kept_press_mean_sol": cs["press"]["mean_sol"] if cs.get("press") else None,
        "kept_flat_mean_sol": cs["flat"]["mean_sol"] if cs.get("flat") else None,
        "paired_vs_frozen": paired_side(pr),
    }


def nested_lodo(trades: Sequence[Mapping[str, Any]], feats: Mapping[str, Any], rules: Sequence[str], days: Sequence[str], min_train_trades: int = NESTED_MIN_TRAIN_TRADES) -> dict[str, Any]:
    """Nested LODO over the rule family. Candidates: the frozen point (paired difference 0, always first) then
    `rules`. For each held-out day pick the candidate with the best pooled PRESSURE paired mean on the other days,
    among candidates with >= min_train_trades entered training trades; ties go to the earlier candidate (frozen
    first). No candidate qualifying makes the fold UNAVAILABLE (not defaulted). Held-out paired rows are pooled and
    scored with the gate bootstrap under both fail models."""
    ids = [FROZEN_ID, *rules]
    diffs = {FROZEN_ID: op.paired_vs_reference(trades, trades)}
    for r in rules:
        diffs[r] = op.paired_vs_reference(trades, kept_trades(trades, feats, r))
    days = sorted(days)
    held: list[dict[str, Any]] = []
    folds: list[dict[str, Any]] = []
    for d in days:
        best, best_v = None, float("-inf")
        for i in ids:
            train = [r for r in diffs[i] if r["day"] != d]
            if not train or sum(1 for r in train if r["entered"]) < min_train_trades:
                continue
            v = sum(r["dpress"] for r in train) / len(train)
            if v > best_v:
                best, best_v = i, v
        if best is None:
            folds.append({"day": d, "chosen": None, "available": False, "n_held_out": 0})
            continue
        day_rows = [dict(r, chosen=best) for r in diffs[best] if r["day"] == d]
        held.extend(day_rows)
        folds.append({"day": d, "chosen": best, "available": True, "n_held_out": len(day_rows)})
    n_unavail = sum(1 for f in folds if not f["available"])
    insample = {i: (sum(r["dpress"] for r in diffs[i]) / len(diffs[i]) if diffs[i] else float("-inf")) for i in ids}
    best_in = max(ids, key=lambda i: (insample[i], -ids.index(i)))
    pooled = paired_side(held)
    return {
        "available": n_unavail < len(days),
        "candidates": ids,
        "min_train_trades": min_train_trades,
        "n_days": len(days),
        "n_unavailable_folds": n_unavail,
        "folds": folds,
        "times_chosen": {i: sum(1 for f in folds if f["chosen"] == i) for i in ids},
        "heldout_paired_vs_frozen": pooled,
        "insample_best": best_in,
        "insample_best_paired_press_mean_sol": None if insample[best_in] == float("-inf") else insample[best_in] / LAMPORTS,
        "optimism_gap_press_sol": op._gap(insample[best_in], pooled),
    }


def analyze(rows: Sequence[Mapping[str, Any]], scores: Mapping[str, float], thr_doc: Mapping[str, Any] | None = None, oof_days: Mapping[str, str] | None = None, min_train_trades: int = NESTED_MIN_TRAIN_TRADES) -> dict[str, Any]:
    op.check_integrity(rows, scores, thr_doc, oof_days)
    days = sorted(set(oof_days.values()) if oof_days else {r["day"] for r in rows if r.get("day")})
    trades, feats, cen = frozen_trades(rows)
    missing = sum(1 for t in trades if not feats.get(t["mint"]))
    ref_stats = op.cell_stats(trades, FROZEN_CELL, len(days), cen)
    per_rule = [rule_stats(trades, feats, r, len(days)) for r in RULE_IDS]
    return {
        "schema": "exp012_entry_veto_v1",
        "status": BANNER,
        "n_rules_tried": len(RULE_IDS),
        "cumulative_tries_on_pool": EXISTING_TRIES_ON_POOL + len(RULE_IDS),
        "existing_tries_on_pool": EXISTING_TRIES_ON_POOL,
        "n_days": len(days),
        "days": days,
        "frozen_point": {"threshold": FROZEN_THRESHOLD, "k": K, "size_sol": SIZE_SOL, "fee": FEE},
        "observation_window_slots": [f"mig+{OBS_FIRST_OFFSET}", f"mig+{OBS_LAST_OFFSET}"],
        "n_frozen_trades_without_features": missing,
        "ci": "gate cluster bootstrap, 1000 draws, seed 1 (tools.paper_attention_promote.book_stats)",
        "caveats": [
            "exploration pool, the same 9 days the model was frozen on: best-of-N, winner's curse applies; not a promote, not gate evidence",
            f"features use PumpSwap prints in slots mig+{OBS_FIRST_OFFSET}..mig+{OBS_LAST_OFFSET} only; whether the live feed delivers mig+{OBS_LAST_OFFSET} before the send is NOT measured here",
            "a vetoed entry is assumed to cost nothing (no tx sent); a window with no print gives no veto",
            "the nested number is an UPPER BOUND: stored OOF scores saw the other days, and the rule family was written after the live stop-loss pattern was known",
            "only the ENTRY slot is delayed (k=6); the exit delay stays at the frozen k=1 'start'; the live sell shortfall and entry noise are not added",
            "V-priced execution; exit sells quoted by the frozen exec model",
        ],
        "frozen_stats": ref_stats,
        "rules": per_rule,
        "nested_lodo": nested_lodo(trades, feats, RULE_IDS, days, min_train_trades=min_train_trades),
    }


# --- rendering ---------------------------------------------------------------------------------------------------


def _f(v: float | None, d: int = 5) -> str:
    return "n/a" if v is None else f"{v:.{d}f}"


def _ci(ci: Sequence[float] | None) -> str:
    return "n/a" if not ci else f"[{ci[0]:.5f}, {ci[1]:.5f}]"


def _leg(g: Mapping[str, Any] | None) -> str:
    return "n/a | n/a | n/a" if not g else f"{_f(g['mean_sol'])} | {_ci(g['ci90_sol'])} | {g['days_positive']}/{g['n_days']}"


def render_md(rep: Mapping[str, Any]) -> str:
    fs = rep["frozen_stats"]
    frozen_line = (
        f"Frozen: n {fs['n']}, flat mean {_f(fs['flat']['mean_sol'])} {_ci(fs['flat']['ci90_sol'])}, pressure mean {_f(fs['press']['mean_sol'])} {_ci(fs['press']['ci90_sol'])}"
        if fs.get("press") and fs.get("flat")
        else "Frozen: no trades"
    )
    lines = [
        "# EXP-012 entry-veto rules at the frozen point, paired vs frozen",
        "",
        f"**{rep['status']}.** N = {rep['n_rules_tried']} rules; cumulative tries on these {rep['n_days']} days: {rep['existing_tries_on_pool']} existing + {rep['n_rules_tried']} new = {rep['cumulative_tries_on_pool']}. CI: {rep['ci']}.",
        "",
        f"Frozen point: {rep['frozen_point']}. Observation window: PumpSwap prints in slots {rep['observation_window_slots'][0]}..{rep['observation_window_slots'][1]} only.",
        "",
        "Caveats:",
        *[f"- {c}" for c in rep["caveats"]],
        "",
        frozen_line,
        "",
        "## Per rule, paired against frozen (a vetoed trade scores 0; difference = rule - frozen, SOL per frozen mint)",
        "",
        "| rule | n vetoed | n kept | vetoed press mean | kept press mean | paired press | press CI90 | press days+ | paired flat | flat CI90 | flat days+ |",
        "| " + " | ".join(["---"] * 11) + " |",
    ]
    for r in rep["rules"]:
        p = r["paired_vs_frozen"]
        lines.append(
            f"| {r['rule']} | {r['n_vetoed']} | {r['n_kept']} | {_f(r['vetoed_press_mean_sol'])} | {_f(r['kept_press_mean_sol'])} | {_leg(p.get('press'))} | {_leg(p.get('flat'))} |"
        )
    n = rep["nested_lodo"]
    lines += ["", "## Nested leave-one-day-out over the rule family (frozen always a candidate), paired vs frozen", ""]
    if not n["available"]:
        lines.append("- unavailable: no fold met the training floor")
    else:
        p = n["heldout_paired_vs_frozen"]
        lines.append(f"- held-out n {p['n']}, unavailable folds {n['n_unavailable_folds']}/{n['n_days']}, training floor {n['min_train_trades']}, times chosen {n['times_chosen']}")
        for name in ("press", "flat"):
            g = p.get(name)
            lines.append(f"  - {name}: " + ("no held-out rows" if not g else f"paired mean {_f(g['mean_sol'])} SOL/mint, CI90 {_ci(g['ci90_sol'])}, held-out days positive {g['days_positive']}/{g['n_days']}"))
        lines.append(f"- optimism gap (in-sample best `{n['insample_best']}` paired pressure mean {_f(n['insample_best_paired_press_mean_sol'])} minus nested held-out): {_f(n['optimism_gap_press_sol'])} SOL/mint")
    lines.append("")
    return "\n".join(lines)


# --- tries log -----------------------------------------------------------------------------------------------------


def _already_in_log(log: Path, rule: str, result_path: Path) -> bool:
    if not log.is_file():
        return False
    for line in log.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if rec.get("tool") == TOOL and rec.get("config", {}).get("rule") == rule and rec.get("result_path") == str(result_path):
            return True
    return False


def log_tries(rep: Mapping[str, Any], out_dir: Path, tries_log: str | Path) -> int:
    """One result.v1 tries line per rule (role exploration). Idempotent per out dir (marker plus a log scan)."""
    from tools import mal_result
    from tools.exp012_exit_sensitivity import _read_marker, _write_marker
    from tools.exp012_support import exploration_pool_blocks

    marker = out_dir / TRIES_MARKER
    done = _read_marker(marker)
    if "*" in done:
        return 0
    result_path = out_dir / "entry_veto.json"
    blocks = exploration_pool_blocks()
    n = 0
    for r in rep["rules"]:
        if r["rule"] in done:
            continue
        if not _already_in_log(Path(tries_log), r["rule"], result_path):
            mal_result.append_try(
                tries_log,
                tool=TOOL,
                config={"experiment": "EXP-012 entry veto", "rule": r["rule"], "threshold": FROZEN_THRESHOLD, "k": K, "size_sol": SIZE_SOL, "fee_lamports": FEE,
                        "window_slots": rep["observation_window_slots"], "selection": "OOF", "pricing": "V", "n_rules": rep["n_rules_tried"]},
                data_blocks=blocks,
                result_path=result_path,
                role="exploration",
            )
            n += 1
        done.add(r["rule"])
    _write_marker(marker, done)
    return n


# --- CLI -------------------------------------------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    from tools.exp012_exit_sensitivity import resolve_tries_path

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_root_args(ap)
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--artifact-dir", type=Path, default=DEFAULT_ARTIFACT_DIR)
    ap.add_argument("--vmap", default=DEFAULT_VMAP)
    ap.add_argument("--max-workers", type=int, default=2)
    ap.add_argument("--buffer-hours", type=int, default=24)
    ap.add_argument("--max-home-hours", type=int, default=12)
    ap.add_argument("--tries-log", default=None, help="absolute tries-log path (default: MAL_TRIES_LOG, else data/tries.jsonl)")
    ap.add_argument("--reuse-rows", action="store_true", help=f"skip the tape pass and analyze OUT_DIR/{SCRATCH_ROWS} if it exists")
    args = ap.parse_args(argv)
    if args.max_workers > 2:
        raise SystemExit("keep --max-workers <= 2")
    tries_path = resolve_tries_path(args.tries_log)
    scores, threshold, thr_doc, oof_days = load_oof(args.artifact_dir)
    if abs(threshold - FROZEN_THRESHOLD) > 1e-15:
        raise SystemExit(f"threshold.json threshold {threshold!r} != the frozen {FROZEN_THRESHOLD!r}")
    args.out_dir.mkdir(parents=True, exist_ok=True)
    rows_path = args.out_dir / SCRATCH_ROWS
    t0 = time.time()
    if args.reuse_rows and rows_path.is_file():
        rows = list(iter_rows_jsonl(rows_path))
    else:
        roots = guarded_roots(args)
        rows = collect_rows(roots["fast"], roots["insample"], roots["live"], args.out_dir / "scratch", args.artifact_dir, args.vmap,
                            max_workers=args.max_workers, buffer_hours=args.buffer_hours, max_home_hours=(args.max_home_hours or None))
        write_rows(rows_path, rows)
    rep = analyze(rows, scores, thr_doc, oof_days)
    rep["wall_s"] = time.time() - t0
    rep["tries"] = {"logged": log_tries(rep, args.out_dir, tries_path), "log_path": str(tries_path)}
    (args.out_dir / "entry_veto.json").write_text(json.dumps(rep, indent=2) + "\n", encoding="utf-8")
    md = render_md(rep)
    (args.out_dir / "entry_veto.md").write_text(md, encoding="utf-8")
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
