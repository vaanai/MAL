"""C1-NF item 14(a): the reference pick list with the canary's pinned model (GO-PLAN 2.4, QP's terms).

Item 14(a) compares the harness's 09-20 pick list (`tools/c1nf_parity_exact.py --state-mode exact`, the canary model, decisions and the book from S3's
start, 2026-09-18T23:00Z) with the frozen scorer's: P2's stage-1 rows, scored with the SAME model file, then the cap and the book. This tool is the
frozen scorer's side. It differs from `tools/c1nf_walkforward_models.py export-rdet` in three things only (the rest is that tool's code path):

  model     ONE file, `ARTIFACTS/c1nf_model/c1nf_model_exp36.txt` (sha256 faf8a01f...), the canary's pin; not the 21 walk-forward files.
  start     the book starts empty at S3's start, 2026-09-18T23:00Z (rows with t before it are not read into the book), and decisions end where the
            harness's `--decide-to 2026-09-22T00` ends them (T < that). Rdet's book runs from 09-03.
  book      the SHADOW's re-entry rule, not P2's simulated exit time: a pick sets book[mint] = bt + 1.3 + 300 + 0.55 (bt = the block_time of the
            decision's SD row, `c1nf_shadow.PRIMARY_LAT + EXIT_S + EXIT_LAG_S`), and a row of that mint is dropped while T < book[mint] + 60
            (`c1nf_shadow.REENTRY_S`). Order: stage 1, prediction > 0.02, cap h_top1 <= 0.5 (NaN dropped), THEN the book (the spec order; the shadow's
            `_decide` does the same). The book rule needs no exit-time or label column, so none is opened.

What is read: P2's `conf.npz` keys F, fnames, mid, t, day, days only (`read_npz` refuses any other key before the file is opened: no `pnl_*`, `xt_*`,
leg, fail-input or return column), `rule.json` (pinned sha), `universe.parquet` columns mid, mint, gday, and the exploration tape's `block_time`, `slot`
(through `c1nf_shadow.refuse_replay`). No P&L, return or label value is opened, printed or written.

Outputs in --out-dir (each path is refused if it exists):
  14a-0920.csv           the compared list: lines `mint,decision_T_ms,SD_slot` with decision_T on UTC 2026-09-20 (all graduation days), sorted,
                         newline-terminated; its md5 is P3's list md5 (`c1nf_parity_exact.p3_md5`, days["2026-09-20"])
  14a-all.csv            the same lines for the whole window (S3 start .. decide-to), the book running continuously
  14a-all-pred.csv       the window as `mint,decision_T_ms,SD_slot,pred,h_top1` (for tie removal on this side; section 2.3)
  *.sha256, 14a-0920.meta.json   sidecars; the meta holds counts, the P3 md5 per day, input sha256s and the code sha256, and no timestamp, so two runs
                         give byte-equal files

  /data/mal/c1nf-parity-venv/bin/python tools/c1nf_item14_reference.py --ml-dir /data/mal/hunt-1008/c1nf-p2/ml --out-dir OUT
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import sys
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools import c1nf_model_pin as mp  # noqa: E402
from tools import c1nf_walkforward_models as wm  # noqa: E402

CANARY_MODEL = ROOT / "ARTIFACTS" / "c1nf_model" / "c1nf_model_exp36.txt"
CANARY_SHA256 = "faf8a01f5fb5019a3c26affc7de49f47270e7b6717eea34767bfcdc759399478"
S3_START = "2026-09-18T23"                    # the book and the decisions start here (GO-PLAN 2.4, book convention)
DECIDE_TO = "2026-09-22T00"                   # the harness's --decide-to: decisions with T < this
COMPARED_DAY = "2026-09-20"
DEFAULT_ML = "/data/mal/hunt-1008/c1nf-p2/ml"
OUT_0920, OUT_ALL, OUT_ALL_PRED, OUT_META = "14a-0920.csv", "14a-all.csv", "14a-all-pred.csv", "14a-0920.meta.json"
TIE_EPS = 1e-6


def hour_epoch(h: str) -> int:
    """'2026-09-18T23' -> epoch seconds (UTC)."""
    return mp.utc0(h[:10]) + 3600 * int(h[11:13])


# ---- the SD clock, with the block_time ---------------------------------------------------------------------------------------------------
def decision_clock(tape_dir: str | Path, rows: Sequence[tuple[str, int]], threads: int = 1) -> tuple[np.ndarray, np.ndarray, dict]:
    """(SD_slot, bt) for each (graduation_day, T_seconds). Same clock and rule as `c1nf_walkforward_models.decision_slots` (11_passA.dec_slot: the
    min slot of the first block_time >= T over the graduation-day window of the tape's hour files), and `bt` is that block_time, the `bt` of the
    shadow's `_decide(Tc, slot, bt)` that sets the book. T past the last clock entry is clipped to it (counted)."""
    import duckdb
    from tools import c1nf_shadow as cs
    tape = Path(tape_dir)
    by_day: dict[str, list[int]] = {}
    for k, (gday, T) in enumerate(rows):
        by_day.setdefault(gday, []).append(k)
    sd = np.zeros(len(rows), dtype=np.int64)
    bt_out = np.zeros(len(rows), dtype=np.int64)
    n_files, n_clipped = 0, 0
    con = duckdb.connect()
    con.execute(f"SET memory_limit='4GB'; SET threads={int(threads)}")
    for gday, ks in sorted(by_day.items()):
        g0 = mp.utc0(gday)
        Ts = np.array([rows[k][1] for k in ks], dtype=np.int64)
        lo = max(g0, int(Ts.min()) // wm.HOUR_S * wm.HOUR_S - wm.HOUR_S)
        hi = min(g0 + (wm.TAPE_WINDOW_HOURS - 1) * wm.HOUR_S, int(Ts.max()) // wm.HOUR_S * wm.HOUR_S + wm.HOUR_S)
        hours = [h for h in wm._hour_list(lo, hi) if (tape / "trades" / f"{h}.parquet").is_file()]
        files = [str(tape / "trades" / f"{h}.parquet") for h in hours]
        cs.refuse_replay(files, hours)                                       # exploration hours only, no forbidden path token
        if not files:
            raise SystemExit(f"refused: no tape hour file for the window of graduation day {gday}")
        n_files += len(files)
        clock = con.execute("SELECT block_time, min(slot) FROM read_parquet(?) WHERE block_time IS NOT NULL GROUP BY 1 ORDER BY 1", [files]).fetchall()
        bts = np.array([r[0] for r in clock], dtype=np.int64)
        sl = np.array([r[1] for r in clock], dtype=np.int64)
        i = np.searchsorted(bts, Ts, "left")
        n_clipped += int((i >= len(bts)).sum())
        j = np.clip(i, 0, len(sl) - 1)
        sd[ks], bt_out[ks] = sl[j], bts[j]
    con.close()
    return sd, bt_out, {"graduation_days": len(by_day), "tape_files_read": n_files, "rows": len(rows), "clipped_to_last_clock_entry": n_clipped}


# ---- the shadow's book -------------------------------------------------------------------------------------------------------------------
def shadow_book(cands: Sequence[Mapping[str, Any]]) -> tuple[list[int], int]:
    """`Shadow._decide`'s per-mint book on candidates already past stage 1, the 0.02 threshold and the cap. cands: dicts with `mint`, `T` (seconds),
    `bt` (the SD row's block_time). Rows go in (T, input order). A row is dropped while T < book[mint] + REENTRY_S; a taken row sets
    book[mint] = bt + PRIMARY_LAT + EXIT_S + EXIT_LAG_S (the same float expression). Returns (indices taken, rows blocked)."""
    from tools import c1nf_shadow as cs
    order = sorted(range(len(cands)), key=lambda i: (int(cands[i]["T"]), i))
    book: dict[str, float] = {}
    taken, blocked = [], 0
    for i in order:
        c = cands[i]
        if c["T"] < book.get(c["mint"], -1.0) + cs.REENTRY_S:
            blocked += 1
            continue
        book[c["mint"]] = c["bt"] + cs.PRIMARY_LAT + cs.EXIT_S + cs.EXIT_LAG_S
        taken.append(i)
    return taken, blocked


# ---- build ------------------------------------------------------------------------------------------------------------------------------
def build_reference(ml_dir, universe_path, tape_dir, *, model_path=CANARY_MODEL, model_sha256=CANARY_SHA256, start=S3_START, decide_to=DECIDE_TO,
                    pinned: bool = True, check_fnames: bool = True, threads: int = 1) -> tuple[list[dict], dict]:
    """The reference records (mint, decision_T_ms, SD_slot, pred, h_top1) and the meta document. With `pinned`, the model must be the canary's pin,
    rule.json the pinned one, and the window the GO-PLAN's; else SystemExit before anything is read."""
    from tools import c1nf_shadow as cs
    ml_dir = Path(ml_dir)
    ml, _rule, sums = mp.load_pinned_mlcommon()
    cap = wm._load_pinned_cap(sums)
    rule_path = ml_dir / "rule.json"
    rule = json.loads(rule_path.read_text())
    if pinned:
        if model_sha256 != CANARY_SHA256 or Path(model_path).resolve() != CANARY_MODEL.resolve():
            raise SystemExit("refused: pinned mode scores with the canary's model file and sha256 only")
        if (start, decide_to) != (S3_START, DECIDE_TO):
            raise SystemExit("refused: pinned mode uses the GO-PLAN window (S3 start .. 2026-09-22T00)")
        if wm.sha256_file(rule_path) != sums["rule.json"]:
            raise SystemExit(f"refused: {rule_path} is not the pinned rule.json")
    if float(rule["threshold"]) != cs.PRED_MIN or cap.H_TOP1_MAX != cs.H_TOP1_MAX:
        raise SystemExit("refused: rule.json threshold or the cap differs from the shadow's PRED_MIN / H_TOP1_MAX")
    t_lo, t_hi = hour_epoch(start), hour_epoch(decide_to)
    d = wm.read_npz(ml_dir / "conf.npz", wm.BASE_KEYS, wm.BASE_KEYS)       # F fnames mid t day days: no label, exit time, leg or return key
    wm.check_columns(d, check_fnames)
    ms = cs.ModelSet.single(str(model_path), model_sha256)                 # the sha256 is checked here
    model, msha = ms.for_day(COMPARED_DAY)
    t = d["t"].astype(np.int64)
    win = (t >= t_lo) & (t < t_hi)
    s1 = ml.stage1_mask(d, tuple(rule["stage1"]))
    idx = np.nonzero(win & s1)[0]
    F = d["F"]
    pred = np.full(len(t), np.nan)
    pred[idx] = np.asarray(model.predict(F[idx]), dtype=np.float64).reshape(-1)
    h = ml.col(d, "h_top1")
    sel = np.zeros(len(t), bool)
    sel[idx] = pred[idx] > cs.PRED_MIN
    capped = cap.apply_cap_before_book(h, sel)
    ci = np.nonzero(capped)[0]
    U = wm.read_parquet_columns(universe_path, ("mid", "mint", "gday"), ("mid", "mint", "gday"))
    mid_row = {int(m): i for i, m in enumerate(U["mid"])}
    if len(mid_row) != len(U["mid"]):
        raise SystemExit("refused: universe.parquet has a duplicate mid")
    mids = d["mid"][ci].astype(np.int64)
    if any(int(m) not in mid_row for m in mids):
        raise SystemExit("refused: a candidate mid is not in universe.parquet")
    ui = np.array([mid_row[int(m)] for m in mids], dtype=np.int64)
    sd, bt, tinfo = decision_clock(tape_dir, [(str(U["gday"][u])[:10], int(tt)) for u, tt in zip(ui, t[ci])], threads=threads)
    cands = [{"mint": str(U["mint"][u]), "T": int(tt), "bt": int(b)} for u, tt, b in zip(ui, t[ci], bt)]
    taken, blocked = shadow_book(cands)
    records = [{"mint": cands[k]["mint"], "decision_T_ms": cands[k]["T"] * 1000, "SD_slot": int(sd[k]), "pred": float(pred[ci[k]]),
                "h_top1": float(np.float32(h[ci[k]]))} for k in taken]
    near = {"stage1_pred_within_eps_of_threshold": int((np.abs(pred[idx] - cs.PRED_MIN) < TIE_EPS).sum()),
            "selected_h_top1_within_eps_of_cap": int((np.abs(h[sel].astype(np.float64) - cs.H_TOP1_MAX) < TIE_EPS).sum()), "eps": TIE_EPS}
    from tools import c1nf_parity_exact as px
    p3 = px.p3_md5(records)
    meta = {
        "what": "C1-NF item 14(a) reference: P2 stage-1 rows scored with the canary's pinned model, cap before the book, the shadow's re-entry rule, "
                "book continuous from S3's start",
        "model": {"file": "ARTIFACTS/c1nf_model/c1nf_model_exp36.txt", "sha256": msha, "pinned_sha256": CANARY_SHA256},
        "window": {"start": start, "decide_to": decide_to, "t_lo": t_lo, "t_hi": t_hi, "compared_day": COMPARED_DAY},
        "book": {"rule": "T < book[mint] + REENTRY_S drops the row; a pick sets book[mint] = bt + PRIMARY_LAT + EXIT_S + EXIT_LAG_S",
                 "REENTRY_S": cs.REENTRY_S, "PRIMARY_LAT": cs.PRIMARY_LAT, "EXIT_S": cs.EXIT_S, "EXIT_LAG_S": cs.EXIT_LAG_S, "PRED_MIN": cs.PRED_MIN,
                 "H_TOP1_MAX": cs.H_TOP1_MAX, "order": "stage 1, pred > PRED_MIN, cap, then the book"},
        "counts": {"conf_rows": int(len(t)), "window_rows": int(win.sum()), "stage1_rows": int(len(idx)), "selected": int(sel.sum()),
                   "selected_capped": int(len(ci)), "blocked_by_book": int(blocked), "picks": len(records)},
        "near_ties": near,
        "p3_before_segment_end_cut_and_tie_removal": p3,
        "p3_compared_day": p3["days"].get(COMPARED_DAY, {"n": 0, "md5": hashlib.md5(b"").hexdigest()}),
        "inputs_sha256": {"conf.npz": wm.sha256_file(ml_dir / "conf.npz"), "rule.json": wm.sha256_file(rule_path),
                          "universe.parquet": wm.sha256_file(universe_path), "model": wm.sha256_file(model_path)},
        "code_sha256": {"c1nf_item14_reference.py": wm.sha256_file(Path(__file__)), "c1nf_walkforward_models.py": wm.sha256_file(Path(wm.__file__)),
                        "c1nf_shadow.py": wm.sha256_file(Path(cs.__file__))},
        "reads": {"conf_npz_keys": list(wm.BASE_KEYS), "universe_columns": ["mid", "mint", "gday"], "tape_columns": ["block_time", "slot"],
                  "note": "no pnl_*, xt_*, leg, ssb/nearby/guard or return column is opened: the shadow's book rule needs no exit time"},
        "tape": tinfo, "sd_slot": "11_passA.dec_slot from the exploration tape clock; bt is that clock entry's block_time",
        "env": {"numpy": np.__version__, "python": platform.python_version(), "machine": platform.machine()},
    }
    return records, meta


# ---- output -----------------------------------------------------------------------------------------------------------------------------
def write_lines(path: Path, lines: Sequence[str]) -> dict:
    """Sorted (plain string sort, as p3_md5), newline-terminated, plus `<path>.sha256`; refuses an existing path."""
    if path.exists():
        raise SystemExit(f"refused: {path} exists")
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "".join(ln + "\n" for ln in sorted(lines))
    path.write_text(body)
    sha = wm.sha256_file(path)
    Path(str(path) + ".sha256").write_text(f"{sha}  {path.name}\n")
    return {"file": path.name, "sha256": sha, "n": len(lines), "md5": hashlib.md5(body.encode()).hexdigest()}


def write_outputs(out_dir: str | Path, records: Sequence[Mapping[str, Any]], meta: Mapping[str, Any]) -> dict:
    out = Path(out_dir)
    from tools import c1nf_parity_exact as px
    meta_path = out / OUT_META
    if meta_path.exists():
        raise SystemExit(f"refused: {meta_path} exists")
    for nm in (OUT_0920, OUT_ALL, OUT_ALL_PRED):
        if (out / nm).exists():
            raise SystemExit(f"refused: {out / nm} exists")
    lines_all = [px.p3_line(r) for r in records]
    day = [px.p3_line(r) for r in records if px.utc_day(int(r["decision_T_ms"]) // 1000) == COMPARED_DAY]
    res = {"0920": write_lines(out / OUT_0920, day), "all": write_lines(out / OUT_ALL, lines_all)}
    res["all_pred"] = wm.export_records(out / OUT_ALL_PRED, list(records))
    if res["0920"]["md5"] != meta["p3_compared_day"]["md5"] or res["0920"]["n"] != meta["p3_compared_day"]["n"]:
        raise SystemExit("refused: the written 09-20 list does not match P3's md5 of the same records")
    doc = {**meta, "outputs": res, "p3_md5_compared_day": res["0920"]["md5"]}
    meta_path.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n")
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ml-dir", default=DEFAULT_ML)
    ap.add_argument("--universe", default=None, help="default: <ml-dir>/../work/universe.parquet")
    ap.add_argument("--tape", default=wm.DEFAULT_TAPE)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--threads", type=int, default=1)
    a = ap.parse_args(argv)
    try:
        out = Path(a.out_dir)
        if (out / OUT_0920).exists() or (out / OUT_META).exists():
            raise SystemExit(f"refused: {out} already holds a 14(a) export")
        wm.prog(5, "loading P2 conf rows, scoring with the canary model")
        uni = a.universe or str(Path(a.ml_dir).parent / "work" / "universe.parquet")
        records, meta = build_reference(a.ml_dir, uni, a.tape, threads=a.threads)
        wm.prog(90, f"{len(records)} picks; writing")
        res = write_outputs(out, records, meta)
        wm.prog(100, f"done; 09-20 list n={res['0920']['n']} md5={res['0920']['md5']}")
        print(json.dumps(res, sort_keys=True), flush=True)
        return 0
    except wm.ColumnRefused as exc:
        raise SystemExit(f"refused: {exc}")


if __name__ == "__main__":
    raise SystemExit(main())
