"""haiku_v5_export: turn the frozen H5 BOOST-floor binding trades (exploration tape) into the v5 harness inputs. PAPER ONLY.

    <venv with pandas+pyarrow>/bin/python -m tools.haiku_v5_export --out-dir DIR [--with-outcomes --basis el2 --el2-rows P55_ROWS.parquet]

Inputs (h5-lossfilter MAP.md, 2026-10-09; read-only):
  /data/mal/hunt-1008/h5-lossfilter/features_decision_time.parquet   2,826 rows, decision-time fields (no outcome column)
  /data/mal/hunt-1008/h5-lossfilter/frozen_binding_trades.parquet    2,826 rows, meta + outcomes (flat, press in lamports at 0.1 SOL)
Outputs (write-once dir):
  triggers.jsonl   {"type": "v5_trigger", "tid", "blk", "decision_ms", "outcome_known_ms", "card"}   no outcome field
  outcomes.jsonl   {"type": "v5_outcome", "tid", "basis", "flat_sol", "press_sol"}                  only with --with-outcomes
  export.json      counts, exclusions, input/output sha256, outcome basis, parity verdict (no outcome value)

Without --with-outcomes the trades file is opened for the meta columns only (day, mint, blk, mbt, mbt_utc): no outcome column is read.
With it, the totals of the full 2,826-row book are checked against published figures before anything is written (parity; prints OK or
FAIL only). --basis has no default: "el2" = P55's pnl_base_el2 (exit lag 2, the realistic primary) from docs/research/p55/p55_price.py
output, with flat = 0.85 pnl - 8,250 and press = (1 - p_fail) pnl - p_fail 55,000 (lamports), the only basis a Haiku run or a score
accepts; "frozen" = the stored frozen-exit flat/press, for stub runs only. Every outcome row carries its basis.

Exclusions (counted, never repaired): QA-truncated exits; a decision or estimated exit outside the trade's ledger block window (the tape
ends at the block end; nothing past it is read). tids are opaque (sha256 of day|mint, 16 hex): no date, block or mint reaches the model.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

from tools.haiku_v5_harness import ALLOWED_BLOCKS, CARD_NAMES, OUTCOME_KNOWN_AFTER_MS, Refused, check_path, sha256_file

FEATURES = "/data/mal/hunt-1008/h5-lossfilter/features_decision_time.parquet"
TRADES = "/data/mal/hunt-1008/h5-lossfilter/frozen_binding_trades.parquet"
META_COLS = ("day", "mint", "blk", "mbt", "mbt_utc")
SLOT_MS = 400  # nominal slot time, only to place the decision instant (f_s0_lag_slots) inside its UTC day
EXIT_AFTER_S0_MS = 333_000  # 330 s exit + landing margin, for the block-end censoring check only
FEE_FLAT_LAMPORTS = 8_250  # 0.15 x 55,000 (MAP section 3)
SEND_LAMPORTS = 55_000
DISC_BLOCK = "explore-0814"
# Published totals of the full book (SOL, 4 dp): MAP.md section 3 (frozen); docs/research/p84/REPORT.md tables (el2, P55 baseline).
PARITY = {
    "frozen": {("disc", "flat_sol"): 19.7313, ("conf", "flat_sol"): 13.9990},
    "el2": {("disc", "flat_sol"): 19.7313, ("disc", "press_sol"): 18.0336, ("conf", "flat_sol"): 14.0929, ("conf", "press_sol"): 12.3489},
}
PARITY_TOL = 1e-4


def tid_of(day: str, mint: str) -> str:
    return hashlib.sha256(f"v5|{day}|{mint}".encode()).hexdigest()[:16]


def _num(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(f):
        return None
    return int(f) if float(f).is_integer() and abs(f) < 1e15 else f


def build(features: Sequence[Mapping[str, Any]], trades: Sequence[Mapping[str, Any]],
          outcome_rows: Mapping[tuple[str, str], Mapping[str, float]] | None = None, basis: str | None = None) -> dict[str, Any]:
    """Pure transform on dict rows (tests use it without pandas). features: f_* columns + day, mint, qa_exit_truncated, f_s0_lag_slots,
    f_t_trig_wall_s. trades: META_COLS with mbt_utc in ms. outcome_rows: {(day, mint): {"flat_sol", "press_sol"}} or None."""
    fkey = {}
    for r in features:
        k = (r["day"], r["mint"])
        if k in fkey:
            raise Refused("features: duplicate (day, mint)")
        fkey[k] = r
    if outcome_rows is not None and basis not in ("frozen", "el2"):
        raise Refused("outcome rows need a basis (frozen or el2)")
    triggers, outcomes = [], []
    excl = {"qa_exit_truncated": 0, "outside_block_window": 0}
    seen = set()
    for t in trades:
        k = (t["day"], t["mint"])
        if k in seen or k not in fkey:
            raise Refused("trades and features do not join 1:1 on (day, mint)")
        seen.add(k)
        f = fkey[k]
        blk = t["blk"]
        if blk not in ALLOWED_BLOCKS:
            raise Refused(f"block {blk!r} is not an allowed exploration block")
        mbt_ms = int(t["mbt_utc"])
        if mbt_ms != int(t["mbt"]) * 1000:
            raise Refused("mbt and mbt_utc disagree")
        if f.get("qa_exit_truncated"):
            excl["qa_exit_truncated"] += 1
            continue
        s0_ms = mbt_ms + int(f["f_s0_lag_slots"]) * SLOT_MS
        decision_ms = s0_ms + int(round(float(f["f_t_trig_wall_s"]) * 1000))
        lo, hi = ALLOWED_BLOCKS[blk]
        if not (lo <= decision_ms and s0_ms + EXIT_AFTER_S0_MS < hi):
            excl["outside_block_window"] += 1
            continue
        tid = tid_of(*k)
        card = {n: _num(f.get("f_" + n)) for n in CARD_NAMES}
        triggers.append({"type": "v5_trigger", "tid": tid, "blk": blk, "decision_ms": decision_ms,
                         "outcome_known_ms": decision_ms + OUTCOME_KNOWN_AFTER_MS, "card": card})
        if outcome_rows is not None:
            o = outcome_rows[k]
            outcomes.append({"type": "v5_outcome", "tid": tid, "basis": basis, "flat_sol": float(o["flat_sol"]),
                             "press_sol": float(o["press_sol"])})
    if seen != set(fkey):
        raise Refused("features rows without a trade row")
    triggers.sort(key=lambda r: (r["decision_ms"], r["tid"]))
    return {"triggers": triggers, "outcomes": outcomes if outcome_rows is not None else None, "excluded": excl, "n_input": len(trades)}


def outcomes_from(trades_out: Sequence[Mapping[str, Any]], basis: str, el2: Sequence[Mapping[str, Any]] | None) -> dict:
    """{(day, mint): {flat_sol, press_sol}} for the full book, before any exclusion. frozen: flat/press columns (lamports). el2: P55 rows."""
    out = {}
    if basis == "frozen":
        for r in trades_out:
            out[(r["day"], r["mint"])] = {"flat_sol": float(r["flat"]) / 1e9, "press_sol": float(r["press"]) / 1e9}
    elif basis == "el2":
        if el2 is None:
            raise Refused("basis el2 needs --el2-rows")
        for r in el2:
            pnl, p = float(r["pnl_base_el2"]), float(r["p_fail"])
            out[(r["day"], r["mint"])] = {"flat_sol": (0.85 * pnl - FEE_FLAT_LAMPORTS) / 1e9,
                                          "press_sol": ((1 - p) * pnl - p * SEND_LAMPORTS) / 1e9}
    else:
        raise Refused(f"unknown outcome basis {basis!r}")
    return out


def parity(outc: Mapping[tuple[str, str], Mapping[str, float]], blk_of: Mapping[tuple[str, str], str], basis: str) -> bool:
    tot: dict[tuple[str, str], float] = {}
    for k, o in outc.items():
        part = "disc" if blk_of[k] == DISC_BLOCK else "conf"
        for m in ("flat_sol", "press_sol"):
            tot[(part, m)] = tot.get((part, m), 0.0) + o[m]
    return all(abs(tot.get(k, float("nan")) - v) <= PARITY_TOL for k, v in PARITY[basis].items())


def write_jsonl_once(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, sort_keys=True, allow_nan=False) + "\n")


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="tools.haiku_v5_export")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--features", default=FEATURES)
    ap.add_argument("--trades", default=TRADES)
    ap.add_argument("--with-outcomes", action="store_true")
    ap.add_argument("--basis", choices=("frozen", "el2"))  # no default (QP on #1087 item 2)
    ap.add_argument("--el2-rows")
    a = ap.parse_args(argv)
    try:
        if a.with_outcomes and a.basis is None:
            raise Refused("--with-outcomes needs --basis (el2 for any Haiku run or score)")
        import pandas as pd  # only here: the harness and tests run without pandas

        for p in (a.features, a.trades, a.el2_rows):
            if p:
                check_path(p)
        out = Path(a.out_dir)
        if out.exists():
            raise Refused("--out-dir exists (write-once)")
        fcols = ["day", "mint", "qa_exit_truncated", "f_s0_lag_slots", "f_t_trig_wall_s"] + ["f_" + n for n in CARD_NAMES]
        feats = pd.read_parquet(a.features, columns=list(dict.fromkeys(fcols)))
        tcols = list(META_COLS) + (["flat", "press"] if a.with_outcomes and a.basis == "frozen" else [])
        trades = pd.read_parquet(a.trades, columns=tcols)
        trades["mbt_utc"] = trades["mbt_utc"].astype("int64") // (10 ** 6 if str(trades["mbt_utc"].dtype).startswith("datetime64[ns") else 1)
        frows, trows = feats.to_dict("records"), trades.to_dict("records")
        outc = None
        basis_note = "none (no outcome column read)"
        if a.with_outcomes:
            el2 = pd.read_parquet(a.el2_rows, columns=["day", "mint", "pnl_base_el2", "p_fail"]).to_dict("records") if a.basis == "el2" else None
            outc = outcomes_from(trows, a.basis, el2)
            blk_of = {(r["day"], r["mint"]): r["blk"] for r in trows}
            if set(outc) != set(blk_of):
                raise Refused("outcome rows do not cover the book 1:1")
            if not parity(outc, blk_of, a.basis):
                raise Refused(f"parity FAIL against the published {a.basis} totals; nothing written")
            basis_note = a.basis
            print(f"parity OK ({a.basis})")
        res = build(frows, trows, outc, a.basis if a.with_outcomes else None)
        out.mkdir(parents=True, mode=0o700)
        write_jsonl_once(out / "triggers.jsonl", res["triggers"])
        if res["outcomes"] is not None:
            write_jsonl_once(out / "outcomes.jsonl", res["outcomes"])
        doc = {"n_input": res["n_input"], "n_triggers": len(res["triggers"]), "excluded": res["excluded"], "outcome_basis": basis_note,
               "per_block": {b: sum(1 for t in res["triggers"] if t["blk"] == b) for b in ALLOWED_BLOCKS},
               "inputs_sha256": {"features": sha256_file(a.features), "trades": sha256_file(a.trades),
                                 **({"el2_rows": sha256_file(a.el2_rows)} if a.el2_rows else {})},
               "outputs_sha256": {"triggers": sha256_file(out / "triggers.jsonl"),
                                  **({"outcomes": sha256_file(out / "outcomes.jsonl")} if res["outcomes"] is not None else {})},
               "export_sha256": sha256_file(__file__)}
        fd = os.open(out / "export.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
        with os.fdopen(fd, "w") as f:
            f.write(json.dumps(doc, sort_keys=True, indent=1) + "\n")
        print(f"export: {doc['n_triggers']} triggers of {doc['n_input']}; excluded {doc['excluded']}; per block {doc['per_block']}")
        return 0
    except Refused as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
