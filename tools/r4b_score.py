"""r4-b scorer (FROZEN-r4-b.md section 1): score = mean over seeds 1..5 of Booster.predict(X float64).

The five booster files are md5-checked against tools/r4b_frozen.py before they are loaded (a mismatch raises, nothing is scored).
X columns follow r4b_frozen.FEATURES (features.txt order). LightGBM 4.7.0 is imported lazily, so the module imports in a venv without it;
use /data/mal/venv for scoring.

E-b (builder task 1): tools/test_r4b_score.py scores models/r4b/equiv_fixture.npz X with these boosters and requires seed1..5 and bag_score
to be reproduced EXACTLY (np.array_equal; 52 of 617 rows have a bag score > 0).

CLI:  /data/mal/venv/bin/python -I tools/r4b_score.py --fixture      prints the E-b check
"""
from __future__ import annotations

import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools import r4b_frozen as F  # noqa: E402


class PinMismatch(RuntimeError):
    pass


def check_artifact(name: str, models_dir: str | None = None) -> str:
    """md5 AND sha256 of models_dir/name against the pins; returns the path. Raises PinMismatch."""
    d = F.MODELS_DIR if models_dir is None else models_dir
    p = os.path.join(d, name)
    if not os.path.isfile(p):
        raise PinMismatch(f"missing artifact {p}")
    m, s = F.file_md5(p), F.file_sha256(p)
    if m != F.ARTIFACT_MD5[name] or s != F.ARTIFACT_SHA256[name]:
        raise PinMismatch(f"{name}: md5 {m} / sha256 {s} do not match the pins")
    return p


def check_features(models_dir: str | None = None) -> None:
    p = check_artifact("features.txt", models_dir)
    with open(p) as f:
        got = tuple(ln.strip() for ln in f if ln.strip())
    if got != F.FEATURES:
        raise PinMismatch("features.txt does not equal r4b_frozen.FEATURES")


def load_boosters(models_dir: str | None = None) -> list:
    """The 5 boosters in seed order, after the md5/sha256 check of every file (all checked before any is loaded)."""
    paths = [check_artifact(n, models_dir) for n in F.BOOSTER_FILES]
    import lightgbm as lgb

    if lgb.__version__ != F.LIGHTGBM_VERSION:
        raise PinMismatch(f"lightgbm {lgb.__version__} != pinned {F.LIGHTGBM_VERSION}")
    return [lgb.Booster(model_file=p) for p in paths]


def seed_scores(boosters, X) -> np.ndarray:
    """(5, n) float64 raw predictions, one row per seed."""
    X = np.ascontiguousarray(np.asarray(X, dtype=np.float64))
    if X.ndim != 2 or X.shape[1] != F.N_FEATURES:
        raise ValueError(f"X must be (n, {F.N_FEATURES}), got {X.shape}")
    return np.vstack([np.asarray(b.predict(X), dtype=np.float64) for b in boosters])


def bag_score(boosters, X) -> np.ndarray:
    """Mean over the seeds of Booster.predict(X float64); np.mean(np.vstack(...), axis=0) exactly as the harness's r4-b hook."""
    return np.mean(seed_scores(boosters, X), axis=0)


def check_fixture(models_dir: str | None = None) -> dict:
    """E-b. Returns the comparison; raises PinMismatch on a pin failure. ok is True only if every column is bit-equal."""
    z = np.load(check_artifact("equiv_fixture.npz", models_dir), allow_pickle=False)
    bs = load_boosters(models_dir)
    S = seed_scores(bs, z["X"])
    bag = np.mean(S, axis=0)
    out = {"rows": int(len(z["X"])), "bag_pos": int((bag > 0).sum())}
    for i, s in enumerate(F.SEEDS):
        out[f"seed{s}_equal"] = bool(np.array_equal(S[i], z[f"seed{s}"]))
        out[f"seed{s}_maxabs"] = float(np.max(np.abs(S[i] - z[f"seed{s}"])))
    out["bag_equal"] = bool(np.array_equal(bag, z["bag_score"]))
    out["bag_maxabs"] = float(np.max(np.abs(bag - z["bag_score"])))
    out["ok"] = bool(all(out[f"seed{s}_equal"] for s in F.SEEDS) and out["bag_equal"] and out["bag_pos"] == int((z["bag_score"] > 0).sum()))
    return out


if __name__ == "__main__":
    if "--fixture" in sys.argv:
        import json

        r = check_fixture()
        print(json.dumps(r, indent=1))
        sys.exit(0 if r["ok"] else 1)
    print(__doc__)
