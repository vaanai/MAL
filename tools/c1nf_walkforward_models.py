"""C1-NF item 13, P0 inputs (GO-PLAN 2.1 (a) and (b)): the 21 walk-forward models, and the Rdet / R419 reference exports.

Three jobs, one recipe (the pinned one, through `tools/c1nf_model_pin.py`: the sha-checked `ARTIFACTS/exp025/scripts/mlcommon.py` and `rule.json`):

  train       One LightGBM model per confirmation day D (2026-09-03..15 and 2026-09-18..25: 21 days), trained from scratch on the stage-1 rows of
              an ml dir (P2's `disc.npz` + `conf.npz`) with t < D 00:00Z - purge_s (3,600 s), the frozen params, seed 1, num_threads 3. Each model
              is saved as LightGBM text (`model-D.txt`) and `manifest.json` lists them in the harness's format,
              `{"models":[{"from_day","file","sha256"}]}`, for `tools/c1nf_parity_exact.py --model-manifest`. The harness picks the model for a
              decision day as the latest entry with from_day <= day (`c1nf_shadow.ModelSet.for_day`); file paths are relative to the manifest.
              The manifest also records the input sha256s (P2's arrays are NOT checked against VERIFY's constants: they differ), the recipe and
              per-model row counts. It holds no timestamp or path, so two runs give byte-equal files; `compare` checks that.
  compare     Two model dirs, byte for byte (manifest and every model file). Exit 1 on any difference.
  export-rdet Rdet: P2's spec-order C1-NF book (cap before the one-position-per-mint book, threshold 0.02, re-entry at exit + 60 s), scored with
              the 21 manifest files through the harness's own `ModelSet` (sha256-checked), as sorted lines `mint,decision_T_ms,SD_slot,pred,h_top1`.
  export-r419 R419: VERIFY's `v/v3_report.py` main book (independent simulator, 1.3 s, END bound, cap h1 <= 0.5 before the book) from `v/cands.npz`
              and `v/sim.parquet`, the same five fields.

What is read, and what is not (the rule of the C1-NF parity work: no P&L, return or label statistic is opened, printed or written):

  * Every npz and parquet read goes through `read_npz` / `read_parquet_columns`, which refuse any key outside an allow-list (`ColumnRefused`).
  * `train` reads the features, `t`, `mid`, `day`, `days`, `fnames` and the ONE training-label column the recipe names (`pnl_E3_p`). It is consumed
    inside the model fit (`np.clip(pnl / SIZE, ...)`, as 16_confirm.py does) and is never printed, summarised or written. A model cannot be trained
    from scratch without its target. This is the only place a label column is opened.
  * `export-rdet` and `export-r419` never open `pnl_*`, the legs (`nofail`, `flat`, `press`), the fail inputs (`ssb_*`, `nearby_*`, `guard_*`) or
    any return. They open the ONE timestamp column the one-position-per-mint book rule needs, the simulated exit time (`xt_E3_p` for Rdet,
    `xt_p_END_l055` for R419): the book re-enters a mint only at exit + 60 s, so the 422/419 rows cannot be chosen without it. It is used
    inside the book function only; it is not exported. (p2_counts.py and v3_report.py read the same column for the same reason.)
  * The exported file has exactly the five columns `mint,decision_T_ms,SD_slot,pred,h_top1`; `export_records` refuses any other set.
  * SD_slot is not stored in P2's arrays. It is recomputed from the exploration tape's clock as `11_passA.dec_slot` does: the min slot of the first
    block_time >= T, over the hour files of the mint's graduation-day window (graduation day D00 .. D+2 T01), clipped to the last clock entry.
    The tape guard (`c1nf_shadow.refuse_replay`: exploration hours only, forbidden path tokens) runs before any hour file is read.

  /data/mal/c1nf-parity-venv/bin/python tools/c1nf_walkforward_models.py train --ml-dir /data/mal/hunt-1008/c1nf-p2/ml --out-dir OUT
  /data/mal/c1nf-parity-venv/bin/python tools/c1nf_walkforward_models.py compare OUT OUT2
  /data/mal/c1nf-parity-venv/bin/python tools/c1nf_walkforward_models.py export-rdet --ml-dir /data/mal/hunt-1008/c1nf-p2/ml --manifest OUT/manifest.json --out-dir REF
  /data/mal/c1nf-parity-venv/bin/python tools/c1nf_walkforward_models.py export-r419 --verify-dir /data/mal/hunt-1008/c1nf-verify --out-dir REF
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import sys
import time
import warnings
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools import c1nf_model_pin as mp  # noqa: E402

CONFIRMATION_DAYS = tuple([f"2026-09-{d:02d}" for d in list(range(3, 16)) + list(range(18, 26))])
assert len(CONFIRMATION_DAYS) == 21
MODEL_FMT = "model-{day}.txt"
MANIFEST, MANIFEST_SHA = "manifest.json", "manifest.sha256"
EXPORT_COLUMNS = ("mint", "decision_T_ms", "SD_slot", "pred", "h_top1")
BASE_KEYS = ("F", "fnames", "mid", "t", "day", "days")
PRIMARY_KEYS = ("idx", "pred")                      # confirm_primary_trades.npz: the primary book's row index and prediction, nothing else
R419_CANDS_KEYS = ("idx", "isconf", "t", "mid", "pred", "f_h_top1")
R419_SIM_COLUMNS = ("idx", "mint", "sd", "h1", "xt_p_END_l055")
R419_XT, R419_CAP, R419_EXPECT_N = "xt_p_END_l055", 0.5, 419
DEFAULT_TAPE = "/data/mal/audit-1008/tape"
TAPE_WINDOW_HOURS = 50                              # 11_passA: hours D T00 .. D+2 T01 of a graduation day
RDET_CSV, R419_CSV = "rdet.csv", "r419.csv"
HOUR_S = 3600


class ColumnRefused(ValueError):
    """A read or an export asked for a column outside the allow-list."""


# ---- guarded reads ----------------------------------------------------------------------------------------------------------------------
def allowed_keys(rule: Mapping[str, Any], purpose: str) -> frozenset:
    """train: BASE + the one training label; book: BASE + the one book-rule exit time. Nothing else."""
    leg = rule["train_leg"]
    if purpose == "train":
        return frozenset(BASE_KEYS) | {f"pnl_{rule['exit']}_{leg}"}
    if purpose == "book":
        return frozenset(BASE_KEYS) | {f"xt_{rule['exit']}_{leg}"}
    raise ValueError(purpose)


def read_npz(path: str | Path, keys: Iterable[str], allowed: Iterable[str]) -> dict:
    """Open an npz and read exactly `keys`, each of which must be in `allowed`; any other key is refused before the file is opened."""
    keys, allowed = tuple(keys), frozenset(allowed)
    bad = [k for k in keys if k not in allowed]
    if bad:
        raise ColumnRefused(f"{path}: columns outside the allow-list: {bad}")
    with np.load(path, allow_pickle=False) as z:
        missing = [k for k in keys if k not in z.files]
        if missing:
            raise ColumnRefused(f"{path}: missing keys {missing}")
        return {k: z[k] for k in keys}


def read_parquet_columns(path: str | Path, columns: Iterable[str], allowed: Iterable[str]) -> dict:
    columns, allowed = tuple(columns), frozenset(allowed)
    bad = [c for c in columns if c not in allowed]
    if bad:
        raise ColumnRefused(f"{path}: columns outside the allow-list: {bad}")
    import pyarrow.parquet as pq
    tbl = pq.read_table(str(path), columns=list(columns))
    return {c: tbl.column(c).to_numpy(zero_copy_only=False) for c in columns}


def sha256_file(p: str | Path) -> str:
    return mp.sha256_file(p)


def prog(pct: int, note: str) -> None:
    f = os.environ.get("MISCUSI_PROGRESS")
    if f:
        try:
            Path(f).write_text(json.dumps({"pct": pct, "note": note}))
        except OSError:
            pass
    print(f"[{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}] {note}", flush=True)


# ---- rows -------------------------------------------------------------------------------------------------------------------------------
def load_rows(ml_dir: str | Path, rule: Mapping[str, Any], purpose: str):
    """disc + conf stacked as 16_confirm.py does. Returns (d, dn, isconf): d has F, fnames, t, mid and the one purpose column."""
    ml_dir = Path(ml_dir)
    allowed = allowed_keys(rule, purpose)
    extra = f"pnl_{rule['exit']}_{rule['train_leg']}" if purpose == "train" else f"xt_{rule['exit']}_{rule['train_leg']}"
    keys = BASE_KEYS + (extra,)
    d1, d2 = read_npz(ml_dir / "disc.npz", keys, allowed), read_npz(ml_dir / "conf.npz", keys, allowed)
    if list(d1["fnames"]) != list(d2["fnames"]):
        raise SystemExit("refused: disc.npz and conf.npz have different feature columns")
    d = {"F": np.vstack([d1["F"], d2["F"]]), "fnames": d1["fnames"]}
    for k in ("t", "mid", extra):
        d[k] = np.concatenate([d1[k], d2[k]])
    dn = np.concatenate([d1["days"][d1["day"]], d2["days"][d2["day"]]])
    isconf = np.concatenate([np.zeros(len(d1["t"]), bool), np.ones(len(d2["t"]), bool)])
    return d, dn, isconf


def check_columns(d: Mapping[str, Any], check: bool = True) -> list[str]:
    fnames = [str(x) for x in d["fnames"]]
    if check:
        from tools.c1nf_features import FEATURE_NAMES  # the live engine's column order (#506)
        if tuple(fnames) != tuple(FEATURE_NAMES):
            raise SystemExit("refused: npz fnames differ from tools.c1nf_features.FEATURE_NAMES (column order)")
    return fnames


def feature_names_sha256(fnames: Sequence[str]) -> str:
    return hashlib.sha256(json.dumps(list(fnames)).encode()).hexdigest()


# ---- train ------------------------------------------------------------------------------------------------------------------------------
def train_walkforward(ml_dir, out_dir, *, pinned: bool = True, expect_days: Optional[Sequence[str]] = CONFIRMATION_DAYS,
                      check_fnames: bool = True, log=print) -> dict:
    """The 21 (or `expect_days`) models and the manifest. Refuses a non-empty out dir. Returns the manifest document."""
    import lightgbm as lgb
    ml_dir, out = Path(ml_dir), Path(out_dir)
    if out.exists() and any(out.iterdir()):
        raise SystemExit(f"refused: {out} is not empty")
    ml, pinned_rule, sums = mp.load_pinned_mlcommon()
    rule_path = ml_dir / "rule.json"
    rule = json.loads(rule_path.read_text())
    rule_sha = sha256_file(rule_path)
    if pinned and rule_sha != sums["rule.json"]:
        raise SystemExit(f"refused: {rule_path} is not the pinned rule.json (sha256 {rule_sha})")
    if rule.get("model") != "lgb" or rule.get("retrain") != "daily-expanding":
        raise SystemExit(f"refused: unexpected rule {rule}")
    in_sha = {"disc.npz": sha256_file(ml_dir / "disc.npz"), "conf.npz": sha256_file(ml_dir / "conf.npz")}
    d, dn, isconf = load_rows(ml_dir, rule, "train")
    fnames = check_columns(d, check_fnames)
    t = d["t"]
    t_max = int(t.max())
    if t_max >= mp.utc0("2026-09-26") and pinned:
        raise SystemExit("refused: a row at or after 2026-09-26T00Z (no October row may be read)")
    cdays = sorted(set(str(x) for x in dn[isconf]))
    if expect_days is not None and tuple(cdays) != tuple(expect_days):
        raise SystemExit(f"refused: the confirmation days are not the expected {len(expect_days)}: {cdays}")
    F = d["F"]
    s1 = ml.stage1_mask(d, tuple(rule["stage1"]))
    label = f"pnl_{rule['exit']}_{rule['train_leg']}"
    y = np.clip(d.pop(label) / ml.SIZE, rule["clip"][0], rule["clip"][1])   # the pinned training target; never printed or written
    purge, nbr = int(rule["purge_s"]), int(rule["rounds"])
    out.mkdir(parents=True, exist_ok=True)
    entries, per_model = [], []
    n = len(cdays)
    for i, D in enumerate(cdays):
        t0 = time.time()
        cut = mp.utc0(D) - purge
        tr = s1 & (t < cut)
        te = s1 & isconf & (dn == D)
        if not te.any() or not tr.any():
            raise SystemExit(f"refused: {D} has no scored rows or no training rows")
        m = mp.train(ml, rule, F, y, tr)
        path = out / MODEL_FMT.format(day=D)
        m.save_model(str(path))
        sha = sha256_file(path)
        b = lgb.Booster(model_file=str(path))
        if b.num_feature() != len(fnames) or b.num_trees() != nbr:
            raise SystemExit(f"refused: saved model shape for {D}")
        diff = float(np.max(np.abs(m.predict(F[te]) - b.predict(F[te]))))       # in-memory vs reloaded file, on the day's stage-1 rows
        entries.append({"from_day": D, "file": path.name, "sha256": sha})
        per_model.append({"day": D, "cut_t": cut, "train_rows": int(tr.sum()), "last_train_t": int(t[tr].max()), "scored_rows": int(te.sum()),
                          "num_trees": int(b.num_trees()), "max_abs_diff_mem_vs_file": diff})
        log(f"{D} train_rows {int(tr.sum())} scored_rows {int(te.sum())} sha256 {sha} mem_vs_file {diff} wall_s {time.time() - t0:.0f}")
        prog(int(100 * (i + 1) / n), f"{D} done ({i + 1}/{n})")
        if diff != 0.0:
            raise SystemExit(f"refused: reloaded model for {D} scores differently from the in-memory one")
    doc = {
        "models": entries,
        "what": "C1-NF item 13 walk-forward models: one per confirmation day D, trained from scratch on P2's stage-1 rows with t < D 00:00Z - purge",
        "built_by": "tools/c1nf_walkforward_models.py",
        "built_by_sha256": sha256_file(Path(__file__)),
        "inputs": {**in_sha, "rule.json": rule_sha, "pinned_rule.json": sums["rule.json"], "mlcommon.py": sums["scripts/mlcommon.py"],
                   "note": "P2's arrays (not VERIFY's): their sha256 is recorded here, not checked against EXP-025 section 2.3"},
        "recipe": {"objective": rule["objective"], "params": ml.lgb_params(rule["objective"]), "rounds": nbr, "stage1": rule["stage1"],
                   "label": f"clip({label} / {ml.SIZE}, {rule['clip'][0]}, {rule['clip'][1]})", "threshold": rule["threshold"], "purge_s": purge},
        "training": per_model,
        "n_rows": int(len(t)), "last_row_t": t_max,
        "feature_names_sha256": feature_names_sha256(fnames),
        "input_dtype": str(F.dtype),
        "env": {"lightgbm": lgb.__version__, "numpy": np.__version__, "python": platform.python_version(), "machine": platform.machine()},
    }
    mpath = out / MANIFEST
    mpath.write_text(json.dumps(doc, indent=1) + "\n")
    (out / MANIFEST_SHA).write_text(f"{sha256_file(mpath)}  {MANIFEST}\n")
    return doc


def compare_dirs(a: str | Path, b: str | Path) -> dict:
    """Byte-for-byte: the manifest and every file it lists (and nothing else is compared). ok iff the file sets and bytes are equal."""
    a, b = Path(a), Path(b)
    rows, ok = [], True
    names = sorted({MANIFEST} | {e["file"] for e in json.loads((a / MANIFEST).read_text())["models"]}
                   | {e["file"] for e in json.loads((b / MANIFEST).read_text())["models"]})
    for nm in names:
        pa, pb = a / nm, b / nm
        sa = sha256_file(pa) if pa.is_file() else None
        sb = sha256_file(pb) if pb.is_file() else None
        eq = sa is not None and sa == sb
        ok = ok and eq
        rows.append({"file": nm, "sha256_a": sa, "sha256_b": sb, "equal": eq})
    return {"ok": ok, "n_files": len(rows), "files": rows}


# ---- SD_slot from the tape clock ---------------------------------------------------------------------------------------------------------
def _hour_list(first_hour_s: int, last_hour_s: int) -> list[str]:
    return [time.strftime("%Y-%m-%dT%H", time.gmtime(s)) for s in range(first_hour_s, last_hour_s + HOUR_S, HOUR_S)]


def decision_slots(tape_dir: str | Path, rows: Sequence[tuple[str, int]]) -> tuple[np.ndarray, dict]:
    """SD_slot for each (graduation_day, T_seconds): 11_passA.dec_slot. The clock is `block_time -> min(slot)` over the tape's hour files of the
    graduation-day window (D T00 .. D+2 T01, files that exist), narrowed to one hour before the first and one after the last T of the day (the
    first block_time >= T and its min slot are inside that); dec_slot = clock slot at searchsorted(bt, T, 'left'), clipped to the last entry.
    Returns (slots int64, info) with info counting the files read and the rows clipped."""
    import duckdb
    from tools import c1nf_shadow as cs
    tape = Path(tape_dir)
    by_day: dict[str, list[int]] = {}
    for k, (gday, T) in enumerate(rows):
        by_day.setdefault(gday, []).append(k)
    out = np.zeros(len(rows), dtype=np.int64)
    n_files, n_clipped = 0, 0
    con = duckdb.connect()
    con.execute("SET memory_limit='4GB'; SET threads=3")
    for gday, ks in sorted(by_day.items()):
        g0 = mp.utc0(gday)
        Ts = np.array([rows[k][1] for k in ks], dtype=np.int64)
        lo = max(g0, int(Ts.min()) // HOUR_S * HOUR_S - HOUR_S)
        hi = min(g0 + (TAPE_WINDOW_HOURS - 1) * HOUR_S, int(Ts.max()) // HOUR_S * HOUR_S + HOUR_S)
        hours = [h for h in _hour_list(lo, hi) if (tape / "trades" / f"{h}.parquet").is_file()]
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
        out[ks] = sl[np.clip(i, 0, len(sl) - 1)]
    return out, {"graduation_days": len(by_day), "tape_files_read": n_files, "rows": len(rows), "clipped_to_last_clock_entry": n_clipped}


# ---- export -----------------------------------------------------------------------------------------------------------------------------
def line_key(r: Mapping[str, Any]) -> str:
    return f"{r['mint']},{int(r['decision_T_ms'])},{int(r['SD_slot'])}"


def format_line(r: Mapping[str, Any]) -> str:
    return f"{line_key(r)},{float(r['pred'])!r},{float(r['h_top1'])!r}"


def export_records(path: str | Path, records: Sequence[Mapping[str, Any]]) -> dict:
    """Write the sorted lines `mint,decision_T_ms,SD_slot,pred,h_top1` (no header) and `<path>.sha256`. Every record must hold EXACTLY the five
    columns; any other key (a P&L, a leg, a label) is refused before anything is written. Sorted by the first three fields as a string, i.e. by
    c1nf_parity_exact.p3_line, so cutting the first three fields of the file gives P3's sorted list. pred and h_top1 are repr() of the float64
    value (h_top1 of a float32 feature is its exact float64 widening)."""
    for r in records:
        if tuple(sorted(r)) != tuple(sorted(EXPORT_COLUMNS)):
            raise ColumnRefused(f"export columns {sorted(r)} are not exactly {sorted(EXPORT_COLUMNS)}")
    lines = sorted((format_line(r) for r in records), key=lambda ln: (",".join(ln.split(",")[:3]), ln))
    keys = [",".join(ln.split(",")[:2]) for ln in lines]
    if len(set(keys)) != len(keys):
        raise SystemExit("refused: a (mint, decision_T_ms) appears twice in the book")
    p = Path(path)
    if p.exists():
        raise SystemExit(f"refused: {p} exists")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("".join(ln + "\n" for ln in lines))
    sha = sha256_file(p)
    Path(str(p) + ".sha256").write_text(f"{sha}  {p.name}\n")
    return {"file": p.name, "sha256": sha, "n": len(lines)}




def _p3(records: Sequence[Mapping[str, Any]]) -> dict:
    """P3's list md5 (c1nf_parity_exact.p3_md5) of the export, per UTC day and overall, BEFORE the segment-end cut and tie removal."""
    from tools import c1nf_parity_exact as px
    return px.p3_md5(records)


def _load_pinned_cap(sums: Mapping[str, str]):
    import importlib.util
    p = ROOT / "ARTIFACTS" / "exp025" / "c1nf_cap.py"
    if sha256_file(p) != sums["c1nf_cap.py"]:
        raise SystemExit("refused: ARTIFACTS/exp025/c1nf_cap.py does not match SHA256SUMS")
    spec = importlib.util.spec_from_file_location("exp025_c1nf_cap", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _universe(path: str | Path, cols: Sequence[str]) -> dict:
    return read_parquet_columns(path, cols, ("mid", "mint", "gday"))


def build_rdet(ml_dir, manifest_path, universe_path, tape_dir, *, p2_counts=None, primary_trades=None, pinned: bool = True,
               check_fnames: bool = True) -> tuple[list[dict], dict]:
    """Rdet: records (the five columns) and the meta document. With `pinned`, the P2 acceptance count (n_c1nf_spec of p2_counts.json) and the
    primary-book reproduction (index list and prediction equal to confirm_primary_trades.npz) must hold, else SystemExit before anything is
    returned."""
    from tools import c1nf_shadow as cs
    ml_dir, manifest_path = Path(ml_dir), Path(manifest_path)
    ml, _pinned_rule, sums = mp.load_pinned_mlcommon()
    cap = _load_pinned_cap(sums)
    rule_path = ml_dir / "rule.json"
    rule = json.loads(rule_path.read_text())
    if pinned and sha256_file(rule_path) != sums["rule.json"]:
        raise SystemExit(f"refused: {rule_path} is not the pinned rule.json")
    d, dn, isconf = load_rows(ml_dir, rule, "book")
    check_columns(d, check_fnames)
    F = d["F"]
    ex, leg, th = rule["exit"], rule["train_leg"], float(rule["threshold"])
    s1 = ml.stage1_mask(d, tuple(rule["stage1"]))
    models = cs.ModelSet.from_manifest(manifest_path)                      # every file's sha256 is checked here
    man = json.loads(manifest_path.read_text())
    want = {e["from_day"]: e["sha256"] for e in man["models"]}
    cdays = sorted(set(str(x) for x in dn[isconf]))
    if set(cdays) != set(want):
        raise SystemExit(f"refused: the manifest's days {sorted(want)} are not the ml dir's confirmation days {cdays}")
    pred = np.full(len(F), np.nan)
    for D in cdays:
        te = s1 & isconf & (dn == D)
        if not te.any():
            continue
        model, sha = models.for_day(D)
        if sha != want[D]:
            raise SystemExit(f"refused: the model for {D} is not the manifest's entry")
        pred[te] = model.predict(F[te])
    sel = s1 & isconf & (pred > th)
    capped = cap.apply_cap_before_book(ml.col(d, "h_top1"), sel)             # cap BEFORE the book: the spec order
    bi = ml.book(d, capped, ex, leg)
    checks: dict[str, Any] = {"n_book": int(len(bi)), "n_selected": int(sel.sum()), "n_selected_capped": int(capped.sum())}
    # P2's acceptance count and P2's own primary book, from the same preds (counts, an index list and the pred field only)
    ok = True
    if p2_counts is not None:
        n_spec = int(json.loads(Path(p2_counts).read_text())["n_c1nf_spec"])
        checks["n_c1nf_spec_p2_counts"] = n_spec
        checks["n_equal_p2_counts"] = bool(n_spec == len(bi))
        ok = ok and checks["n_equal_p2_counts"]
    if primary_trades is not None:
        z = read_npz(primary_trades, PRIMARY_KEYS, PRIMARY_KEYS)
        zi = z["idx"].astype(np.int64)
        bp = ml.book(d, sel, ex, leg)
        diff = np.abs(pred[zi] - z["pred"]) if len(zi) else np.zeros(0)
        checks["primary_book"] = {"n": int(len(zi)), "n_recount": int(len(bp)), "index_equal": bool(np.array_equal(bp, zi)),
                                  "n_pred_equal": int((diff == 0).sum()), "max_abs_diff_pred": float(diff.max()) if len(diff) else 0.0}
        ok = ok and checks["primary_book"]["index_equal"] and checks["primary_book"]["max_abs_diff_pred"] == 0.0
    checks["ok"] = bool(ok)
    if pinned and not ok:
        raise SystemExit(f"refused: Rdet does not reproduce P2: {json.dumps(checks)}")
    U = _universe(universe_path, ("mid", "mint", "gday"))
    mid_row = {int(m): i for i, m in enumerate(U["mid"])}
    if len(mid_row) != len(U["mid"]):
        raise SystemExit("refused: universe.parquet has a duplicate mid")
    mids = d["mid"][bi].astype(np.int64)
    if any(int(m) not in mid_row for m in mids):
        raise SystemExit("refused: a book mid is not in universe.parquet")
    ui = np.array([mid_row[int(m)] for m in mids], dtype=np.int64)
    T = d["t"][bi].astype(np.int64)
    sd, tinfo = decision_slots(tape_dir, [(str(U["gday"][u])[:10], int(t)) for u, t in zip(ui, T)])
    h = ml.col(d, "h_top1")[bi].astype(np.float32)
    records = [{"mint": str(U["mint"][u]), "decision_T_ms": int(t) * 1000, "SD_slot": int(s), "pred": float(pred[i]), "h_top1": float(hh)}
               for u, t, s, i, hh in zip(ui, T, sd, bi, h)]
    meta = {
        "what": "Rdet: EXP-025 P2 deterministic rebuild, spec-order C1-NF book (cap before the book, threshold 0.02, re-entry at exit + 60 s), "
                "scored with the manifest's 21 walk-forward files",
        "columns": list(EXPORT_COLUMNS), "n": len(records), "p3_before_segment_end_cut_and_tie_removal": _p3(records),
        "inputs_sha256": {"disc.npz": sha256_file(ml_dir / "disc.npz"), "conf.npz": sha256_file(ml_dir / "conf.npz"), "rule.json": sha256_file(rule_path),
                          "universe.parquet": sha256_file(universe_path), "manifest.json": sha256_file(manifest_path),
                          "p2_counts.json": sha256_file(p2_counts) if p2_counts else None,
                          "confirm_primary_trades.npz": sha256_file(primary_trades) if primary_trades else None},
        "model_sha256": {e["from_day"]: e["sha256"] for e in man["models"]},
        "reads": {"book_npz_keys": sorted(allowed_keys(rule, "book")), "primary_npz_keys": list(PRIMARY_KEYS),
                  "note": "no pnl_*, leg, ssb/nearby/guard or return column is opened; the one timestamp read for the book rule is "
                          f"xt_{ex}_{leg} (re-entry at exit + 60 s), used inside the book function and not exported"},
        "checks": checks, "tape": tinfo,
        "sd_slot": "11_passA.dec_slot from the exploration tape clock (min slot of the first block_time >= T), graduation-day window",
        "env": {"numpy": np.__version__, "python": platform.python_version()},
    }
    return records, meta


def _book(t, mid, xt, sel, cool: int = 60):
    """v3_report.book, line for line (a mint holds one position; the next entry only at t >= max(exit, t) + cool)."""
    order = np.lexsort((mid, t))
    free: dict[int, int] = {}
    taken = []
    for i in order:
        if not sel[i]:
            continue
        m = int(mid[i])
        if t[i] < free.get(m, -1):
            continue
        taken.append(i)
        free[m] = max(int(xt[i]), int(t[i])) + cool
    return np.array(taken, dtype=np.int64)


def build_r419(verify_dir, *, tape_dir=None, universe_path=None, expect_n: Optional[int] = R419_EXPECT_N) -> tuple[list[dict], dict]:
    """R419: v3_report.py's main book (sim_run(conf, cap(0.5)): book on the independent simulator's exit time xt_p_END_l055, selection = the
    confirmation candidates with finite h1 <= 0.5), fields from cands.npz (t, pred) and sim.parquet (mint, sd, h1). With `tape_dir` and
    `universe_path` it also recomputes SD from the tape and counts agreement with the simulator's `sd` (a check of `decision_slots`)."""
    v = Path(verify_dir)
    cands = read_npz(v / "v" / "cands.npz", R419_CANDS_KEYS, R419_CANDS_KEYS)
    sim = read_parquet_columns(v / "v" / "sim.parquet", R419_SIM_COLUMNS, R419_SIM_COLUMNS)
    pz = read_npz(v / "v" / "preds.npz", ("pred",), ("pred",))
    order = np.argsort(sim["idx"], kind="stable")
    sidx = sim["idx"][order]
    pos = np.searchsorted(sidx, cands["idx"])
    if (pos >= len(sidx)).any() or not np.array_equal(sidx[np.minimum(pos, len(sidx) - 1)], cands["idx"]) or len(np.unique(sidx)) != len(sidx):
        raise SystemExit("refused: sim.parquet idx does not cover cands.npz idx exactly once")
    row = order[pos]
    conf = cands["isconf"].astype(bool)
    t, mid, pred = cands["t"][conf], cands["mid"][conf], cands["pred"][conf]
    h1 = np.asarray(sim["h1"][row][conf], dtype=float)
    xt_f = np.asarray(sim[R419_XT][row][conf], dtype=float)
    sel = np.isfinite(h1) & (h1 <= R419_CAP)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")                       # v3_report casts a NaN exit time with .astype(np.int64); same cast, same result
        xt = xt_f.astype(np.int64)
    bi = _book(t, mid, xt, sel)
    sd_f = np.asarray(sim["sd"][row][conf], dtype=float)[bi]
    if not np.isfinite(sd_f).all() or not (sd_f == np.floor(sd_f)).all():
        raise SystemExit("refused: a booked R419 row has no integral sd")
    if not np.isfinite(xt_f[bi]).all():
        raise SystemExit("refused: a booked R419 row has no simulated exit time")
    mints = sim["mint"][row][conf][bi]
    if expect_n is not None and len(bi) != expect_n:
        raise SystemExit(f"refused: the R419 book has {len(bi)} rows, expected {expect_n}")
    pz_diff = np.abs(pz["pred"][cands["idx"][conf][bi]] - pred[bi])
    h_diff = np.abs(h1[bi] - cands["f_h_top1"][conf][bi].astype(float))
    records = [{"mint": str(m), "decision_T_ms": int(tt) * 1000, "SD_slot": int(s), "pred": float(p), "h_top1": float(h)}
               for m, tt, s, p, h in zip(mints, t[bi], sd_f, pred[bi], h1[bi])]
    sd_check = None
    if tape_dir is not None and universe_path is not None:
        U = _universe(universe_path, ("mint", "gday"))
        gd = {str(m): str(g)[:10] for m, g in zip(U["mint"], U["gday"])}
        if all(str(m) in gd for m in mints):
            mine, info = decision_slots(tape_dir, [(gd[str(m)], int(tt)) for m, tt in zip(mints, t[bi])])
            sd_check = {"rows": int(len(bi)), "equal_to_simulator_sd": int((mine == sd_f.astype(np.int64)).sum()), "tape": info}
        else:
            sd_check = {"rows": int(len(bi)), "error": "a mint is not in the given universe.parquet"}
    meta = {
        "what": "R419: VERIFY v3_report.py main book (independent simulator, lat p, END bound, lag l055, cap h1 <= 0.5 before the book), conf rows",
        "columns": list(EXPORT_COLUMNS), "n": len(records), "n_expected": expect_n, "p3_before_segment_end_cut_and_tie_removal": _p3(records),
        "sources": {"mint": "sim.parquet mint", "decision_T_ms": "cands.npz t * 1000", "SD_slot": "sim.parquet sd (the simulator's decision slot)",
                    "pred": "cands.npz pred (equal to preds.npz pred at idx: see checks)",
                    "h_top1": "sim.parquet h1 (float64, the value the cap used); cands.npz f_h_top1 is the float32 batch feature"},
        "all_five_fields_available": True,
        "checks": {"max_abs_diff_pred_vs_preds_npz": float(pz_diff.max()) if len(pz_diff) else 0.0,
                   "max_abs_diff_h1_vs_f_h_top1": float(h_diff.max()) if len(h_diff) else 0.0, "sd_vs_tape": sd_check},
        "inputs_sha256": {"cands.npz": sha256_file(v / "v" / "cands.npz"), "sim.parquet": sha256_file(v / "v" / "sim.parquet"),
                          "preds.npz": sha256_file(v / "v" / "preds.npz"), "v3_report.py": sha256_file(v / "v" / "v3_report.py")},
        "reads": {"cands_npz_keys": list(R419_CANDS_KEYS), "sim_parquet_columns": list(R419_SIM_COLUMNS), "preds_npz_keys": ["pred"],
                  "note": f"no pnl_*, leg, ssb/nearby/g_ or return column is opened; the one timestamp read for the book rule is {R419_XT}, "
                          "used inside the book function and not exported"},
    }
    return records, meta


# ---- command line -----------------------------------------------------------------------------------------------------------------------
def _write_export(out_dir: Path, name: str, records, meta) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    mpath = out_dir / f"{name[: -len('.csv')]}.meta.json"
    if mpath.exists():
        raise SystemExit(f"refused: {mpath} exists")
    res = export_records(out_dir / name, records)
    meta = {**meta, "file": res["file"], "sha256": res["sha256"]}
    mpath.write_text(json.dumps(meta, indent=1, sort_keys=True) + "\n")
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("train")
    a.add_argument("--ml-dir", required=True)
    a.add_argument("--out-dir", required=True)
    b = sub.add_parser("compare")
    b.add_argument("dir_a")
    b.add_argument("dir_b")
    c = sub.add_parser("export-rdet")
    c.add_argument("--ml-dir", required=True)
    c.add_argument("--manifest", required=True)
    c.add_argument("--out-dir", required=True)
    c.add_argument("--universe", default=None, help="default: <ml-dir>/../work/universe.parquet")
    c.add_argument("--tape", default=DEFAULT_TAPE)
    e = sub.add_parser("export-r419")
    e.add_argument("--verify-dir", required=True)
    e.add_argument("--out-dir", required=True)
    e.add_argument("--tape", default=None, help="with --universe: also check SD against the tape clock")
    e.add_argument("--universe", default=None, help="VERIFY's work/universe.parquet (mint -> graduation day)")
    args = ap.parse_args(argv)
    try:
        if args.cmd == "train":
            doc = train_walkforward(args.ml_dir, args.out_dir)
            mpath = Path(args.out_dir) / MANIFEST
            print("manifest", mpath, "sha256", sha256_file(mpath), "models", len(doc["models"]), flush=True)
            return 0
        if args.cmd == "compare":
            res = compare_dirs(args.dir_a, args.dir_b)
            print(json.dumps(res, indent=1))
            return 0 if res["ok"] else 1
        if args.cmd == "export-rdet":
            ml_dir = Path(args.ml_dir)
            uni = args.universe or str(ml_dir.parent / "work" / "universe.parquet")
            records, meta = build_rdet(ml_dir, args.manifest, uni, args.tape, p2_counts=ml_dir / "p2_counts.json",
                                       primary_trades=ml_dir / "confirm_primary_trades.npz")
            res = _write_export(Path(args.out_dir), RDET_CSV, records, meta)
        else:
            records, meta = build_r419(args.verify_dir, tape_dir=args.tape, universe_path=args.universe)
            res = _write_export(Path(args.out_dir), R419_CSV, records, meta)
        print(json.dumps(res), flush=True)
        return 0
    except ColumnRefused as exc:
        raise SystemExit(f"refused: {exc}")


if __name__ == "__main__":
    raise SystemExit(main())
