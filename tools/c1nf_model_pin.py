"""C1-NF model pin (DEC-026 section 11 item 12): train the canary's stage-2 LightGBM on the 36 exploration days only, by the frozen recipe.

No frozen model file exists: the pinned scorer `ARTIFACTS/exp025/scripts/16_confirm.py` (and VERIFY's `v/v1_wf.py`) retrain in memory per
decision day and never save a model. So this tool rebuilds one with the pinned code and nothing else:

- data: VERIFY's `ml/disc.npz` + `ml/conf.npz` (15 August + 21 September exploration days), sha256 checked against EXP-025 section 2.3;
- recipe: the pinned `mlcommon.py` (`load`, `stage1_mask`, `lgb_params`, `SIZE`) and `rule.json` (stage 1, exit E3, train leg p, label clip,
  huber, 400 rounds, purge 3,600 s), both sha256-checked against `ARTIFACTS/exp025/SHA256SUMS`; the arrays are assembled as 16_confirm does;
- training set: the recipe's set for any October decision day D (every stage-1 row with t < D 00:00Z - purge) with no October row, i.e. every
  stage-1 row of the 36 days. EXP-025 section 4 seals October labels while the read runs, so the canary's model never retrains.

Proof of recipe parity (`--repro-day`, default 2026-09-25): the same code trains VERIFY's walk-forward model for that day (rows with t < D0 - purge),
saves it as text, loads it back with `lightgbm.Booster(model_file=...)` (the shadow's loader, #503) and scores that day's stage-1 rows. The scores
must equal VERIFY's `v/preds.npz` exactly (max |diff| == 0) and the pinned 16_confirm's `ml/confirm_primary_trades.npz` pred on that day's book rows.

Prints and writes scores, counts and hashes only: no P&L, no label statistic, no October row (none exists in the inputs; the tool asserts it).

  /data/mal/venv/bin/python tools/c1nf_model_pin.py --verify-dir /data/mal/hunt-1008/c1nf-verify --out-dir ARTIFACTS/c1nf_model
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import platform
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ART = ROOT / "ARTIFACTS" / "exp025"
# EXP-025 section 2.3: VERIFY's training arrays (non-deterministic ledger; reference only for the read, the canary model's input here)
DISC_SHA256 = "4b68d55b93158c3967926fb9b41192ba2a5aee27075669b95af374bd0a582409"
CONF_SHA256 = "0dc37940dee7768086fe6f1f618c80fdc97b0a93fec80af01e19d902f4649e1d"
EXPLORATION_DAYS = tuple([f"2026-08-{d:02d}" for d in range(14, 29)] + [f"2026-09-{d:02d}" for d in list(range(3, 16)) + list(range(18, 26))])
assert len(EXPLORATION_DAYS) == 36
FROM_DAY = "2026-09-26"  # first UTC day after the last training row: an exploration-day replay gets no model (no in-sample scoring)
OCT_D0 = "2026-10-01"  # any October decision day's recipe cutoff (D0 - purge) lies after every training row
MODEL_FILE = "c1nf_model_exp36.txt"
MANIFEST = "manifest.json"
SMOKE = "smoke.json"
SMOKE_ROWS = 32


def sha256_file(p: str | Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for b in iter(lambda: fh.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def pinned_sums() -> dict[str, str]:
    return {name.strip(): sha for sha, name in (ln.split(None, 1) for ln in (ART / "SHA256SUMS").read_text().splitlines() if ln.strip())}


def load_pinned_mlcommon():
    sums = pinned_sums()
    p = ART / "scripts" / "mlcommon.py"
    if sha256_file(p) != sums["scripts/mlcommon.py"]:
        raise SystemExit("refused: ARTIFACTS/exp025/scripts/mlcommon.py does not match SHA256SUMS")
    spec = importlib.util.spec_from_file_location("exp025_mlcommon", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    rp = ART / "rule.json"
    if sha256_file(rp) != sums["rule.json"]:
        raise SystemExit("refused: ARTIFACTS/exp025/rule.json does not match SHA256SUMS")
    return mod, json.loads(rp.read_text()), sums


def utc0(day: str) -> int:
    import numpy as np
    return int(np.datetime64(day + "T00:00:00").astype("datetime64[s]").astype(np.int64))


def assemble(ml, disc: str, conf: str):
    """16_confirm.py's assembly, line for line (stacked F, concatenated columns, per-row day names, isconf)."""
    import numpy as np
    d1, d2 = ml.load(disc), ml.load(conf)
    assert list(d1["fnames"]) == list(d2["fnames"])
    F = np.vstack([d1["F"], d2["F"]])
    d = {k: np.concatenate([d1[k], d2[k]]) for k in d1 if k not in ("F", "fnames", "days", "day")}
    d["F"] = F
    d["fnames"] = d1["fnames"]
    dn = np.concatenate([d1["days"][d1["day"]], d2["days"][d2["day"]]])
    isconf = np.concatenate([np.zeros(len(d1["t"]), bool), np.ones(len(d2["t"]), bool)])
    return d, dn, isconf, [str(x) for x in d1["days"]] + [str(x) for x in d2["days"]]


def train(ml, rule, F, y, mask):
    import lightgbm as lgb
    return lgb.train(ml.lgb_params(rule["objective"]), lgb.Dataset(F[mask], y[mask]), num_boost_round=rule["rounds"])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--verify-dir", default="/data/mal/hunt-1008/c1nf-verify")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--repro-day", default="2026-09-25")
    a = ap.parse_args(argv)
    import numpy as np
    import lightgbm as lgb

    V = Path(a.verify_dir)
    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    disc, conf = V / "ml" / "disc.npz", V / "ml" / "conf.npz"
    got = {"disc.npz": sha256_file(disc), "conf.npz": sha256_file(conf)}
    if got != {"disc.npz": DISC_SHA256, "conf.npz": CONF_SHA256}:
        raise SystemExit(f"refused: training arrays differ from EXP-025 section 2.3: {got}")
    ml, rule, sums = load_pinned_mlcommon()
    if rule.get("model") != "lgb" or rule.get("retrain") != "daily-expanding":
        raise SystemExit(f"refused: unexpected rule {rule}")

    d, dn, isconf, days = assemble(ml, str(disc), str(conf))
    if tuple(days) != EXPLORATION_DAYS:
        raise SystemExit(f"refused: day list is not the 36 exploration days: {days}")
    t_max = int(d["t"].max())
    if t_max >= utc0("2026-09-26"):
        raise SystemExit("refused: a row at or after 2026-09-26T00Z (no October row may be read)")
    fnames = [str(x) for x in d["fnames"]]
    sys.path.insert(0, str(ROOT))
    from tools.c1nf_features import FEATURE_NAMES  # the live engine's column order (#506)
    if tuple(fnames) != tuple(FEATURE_NAMES):
        raise SystemExit("refused: npz fnames differ from tools.c1nf_features.FEATURE_NAMES (column order)")
    F = d["F"]
    s1 = ml.stage1_mask(d, tuple(rule["stage1"]))
    y = np.clip(d[f"pnl_{rule['exit']}_{rule['train_leg']}"] / ml.SIZE, rule["clip"][0], rule["clip"][1])
    purge, th = int(rule["purge_s"]), float(rule["threshold"])
    print("rows", len(F), "stage1", int(s1.sum()), "features", F.shape[1], F.dtype, "lightgbm", lgb.__version__, flush=True)

    # ---- recipe parity: VERIFY's walk-forward model for the repro day, through a saved text file
    D = a.repro_day
    if D not in set(dn[isconf]):
        raise SystemExit(f"refused: {D} is not a confirmation day")
    tr = s1 & (d["t"] < utc0(D) - purge)
    te = s1 & isconf & (dn == D)
    m = train(ml, rule, F, y, tr)
    p_mem = m.predict(F[te])
    with tempfile.TemporaryDirectory() as td:
        mp = Path(td) / "repro.txt"
        m.save_model(str(mp))
        p_file = lgb.Booster(model_file=str(mp)).predict(F[te])
        repro_sha = sha256_file(mp)
    vp = np.load(V / "v" / "preds.npz")["pred"]
    if len(vp) != len(F):
        raise SystemExit("refused: v/preds.npz length differs")
    v_te = vp[te]
    z = np.load(V / "ml" / "confirm_primary_trades.npz")
    zi = z["idx"][z["day"] == D]
    zp = z["pred"][z["day"] == D]
    full = np.full(len(F), np.nan)
    full[te] = p_file
    repro = {
        "day": D, "train_rows": int(tr.sum()), "scored_rows": int(te.sum()), "model_text_sha256": repro_sha,
        "selected_ours": int((p_file > th).sum()), "selected_verify": int((v_te > th).sum()),
        "verify_nan": int(np.isnan(v_te).sum()),
        "max_abs_diff_file_vs_verify": float(np.max(np.abs(p_file - v_te))),
        "max_abs_diff_mem_vs_file": float(np.max(np.abs(p_mem - p_file))),
        "selection_equal": bool(((p_file > th) == (v_te > th)).all()),
        "confirm_book_rows": int(len(zi)),
        "max_abs_diff_vs_16_confirm_book": float(np.max(np.abs(full[zi] - zp))) if len(zi) else None,
    }
    print("repro", json.dumps(repro), flush=True)
    ok = (repro["verify_nan"] == 0 and repro["max_abs_diff_file_vs_verify"] == 0.0 and repro["max_abs_diff_mem_vs_file"] == 0.0
          and repro["selection_equal"] and repro["confirm_book_rows"] > 0 and repro["max_abs_diff_vs_16_confirm_book"] == 0.0)
    if not ok:
        raise SystemExit("refused: the recipe does not reproduce VERIFY's scores exactly")

    # ---- the pin: the recipe's set for an October decision day = every stage-1 row of the 36 days; trained twice, both files must match
    tr_all = s1 & (d["t"] < utc0(OCT_D0) - purge)
    assert int(tr_all.sum()) == int(s1.sum())
    shas = []
    for k in (1, 2):
        mk = train(ml, rule, F, y, tr_all)
        path = out / (MODEL_FILE if k == 1 else MODEL_FILE + ".run2")
        mk.save_model(str(path))
        shas.append(sha256_file(path))
    os.remove(out / (MODEL_FILE + ".run2"))
    if shas[0] != shas[1]:
        raise SystemExit(f"refused: two training runs gave different files {shas}")
    sha = shas[0]
    booster = lgb.Booster(model_file=str(out / MODEL_FILE))
    if booster.num_feature() != len(fnames) or booster.num_trees() != rule["rounds"]:
        raise SystemExit("refused: saved model shape")
    # loaded-file scores on the repro day's rows (in-sample now; a smoke check of the file, not evidence)
    p_pin = booster.predict(F[te].astype(np.float32))
    pin_smoke = {"day": D, "rows": int(te.sum()), "selected": int((p_pin > th).sum()),
                 "agree_with_walkforward_selection": float(((p_pin > th) == (p_file > th)).mean())}
    print("pin", sha, json.dumps(pin_smoke), flush=True)
    # a host-independent smoke fixture: SMOKE_ROWS feature vectors of the repro day (no label, no P&L) and the pinned file's scores on them, so
    # any host's lightgbm can show it loads this file and scores it identically (tools/test_c1nf_model_pin.py)
    rows = np.where(te)[0][:: max(1, int(te.sum()) // SMOKE_ROWS)][:SMOKE_ROWS]
    X = F[rows].astype(np.float32)
    smoke = {"day": D, "dtype": "float32", "rows": [[None if np.isnan(v) else float(v) for v in x] for x in X],
             "pred": [float(v) for v in booster.predict(X)], "model_sha256": sha}
    (out / SMOKE).write_text(json.dumps(smoke) + "\n")
    doc = {
        "models": [{"from_day": FROM_DAY, "file": MODEL_FILE, "sha256": sha}],
        "what": "C1-NF canary stage-2 model (DEC-026 section 11 item 12): frozen EXP-025 recipe, 36 exploration days only, no October row",
        "built_by": "tools/c1nf_model_pin.py",
        "inputs": {"disc.npz": DISC_SHA256, "conf.npz": CONF_SHA256, "ledger": "VERIFY's non-deterministic 01_wallet_daily (not EXP-025 P2)",
                   "mlcommon.py": sums["scripts/mlcommon.py"], "rule.json": sums["rule.json"], "16_confirm.py": sums["scripts/16_confirm.py"]},
        "recipe": {"objective": rule["objective"], "params": ml.lgb_params(rule["objective"]), "rounds": rule["rounds"], "stage1": rule["stage1"],
                   "label": f"clip(pnl_{rule['exit']}_{rule['train_leg']} / {ml.SIZE}, {rule['clip'][0]}, {rule['clip'][1]})",
                   "threshold": th, "purge_s": purge},
        "training": {"days": list(EXPLORATION_DAYS), "rows": int(tr_all.sum()), "last_row_t": t_max, "from_day": FROM_DAY,
                     "two_runs_equal_sha256": True},
        "feature_names": fnames,
        "feature_names_sha256": hashlib.sha256(json.dumps(fnames).encode()).hexdigest(),
        "input_dtype": str(F.dtype),
        "env": {"lightgbm": lgb.__version__, "numpy": np.__version__, "python": platform.python_version(), "machine": platform.machine()},
        "repro_verify": repro,
        "pin_smoke_in_sample": pin_smoke,
        "smoke_fixture": SMOKE,
    }
    (out / MANIFEST).write_text(json.dumps(doc, indent=1) + "\n")
    print("wrote", out / MODEL_FILE, out / MANIFEST, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
