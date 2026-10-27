"""Tests for tools/hv8_calib.py (HV8 C2). SYNTHETIC fixtures only: no tape, no cards, no HV4/Q50/HV6 output, no network, no model.
Run: /data/mal/venv/bin/python -B -m pytest tools/test_hv8_calib.py -q"""
from __future__ import annotations

import builtins
import hashlib
import json
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from tools import hv8_calib as K
from tools import hv8_rate as R

T0 = datetime(2026, 8, 21, 12, 2, tzinfo=timezone.utc)


def synth(seed=1, sets_per_day=4, n_card=6, signal=0.0, days=7):
    rng = np.random.default_rng(seed)
    rows, rets = [], {}
    for d in range(days):
        for h in range(sets_per_day):
            t = (T0 + timedelta(days=d, hours=h)).strftime("%Y-%m-%dT%H:%M:%SZ")
            for c in range(n_card):
                f = {k: float(rng.normal()) for k in R.C.FEATURE_FIELDS}
                f["vol_60m"] = float(abs(rng.normal()) * 50)
                f["max_stake"] = 0.25
                rt = [int(v) for v in rng.integers(0, 4, 3)]
                sha = hashlib.sha256(f"{seed}|{t}|{c}".encode()).hexdigest()
                rows.append({"card_sha": sha, "t_d": t, "f": f, "risk": {k: float(rng.normal()) for k in R.KEPT_RISK},
                             "setup_type": R.SETUP_TAGS[c % len(R.SETUP_TAGS)], "setup_z": float(rng.normal()), "ratings": rt})
                rets[sha] = float(signal * rt[0] + rng.normal())
    return rows, rets


def labelled(seed=1, **kw):
    rows, rets = synth(seed, **kw)
    return K.label_rows(rows, rets)


def test_feature_sets_registered():
    assert len(K.L8_NAMES) == 4 and len(K.T8_NAMES) == 4 and K.T8_NAMES[-1] == "f.vol_60m"
    assert len(K.T8A_NAMES) == len(set(K.T8A_NAMES)) > 40


def test_irls_recovers_known_weights():
    rng = np.random.default_rng(3)
    X = rng.normal(size=(20000, 3))
    wt = np.array([1.5, -1.0, 0.0])
    y = (rng.random(20000) < 1 / (1 + np.exp(-(0.3 + X @ wt)))).astype(float)
    w = K.irls(X, y)
    assert np.allclose(w, [0.3, 1.5, -1.0, 0.0], atol=0.1)


def test_determinism_model_bytes_and_lodo():
    rows, rets, ys, _ = labelled(2, signal=0.5)
    m1, m2 = K.fit_model(rows, ys, K.L8_NAMES), K.fit_model(rows, ys, K.L8_NAMES)
    assert m1 == m2 and m1["sha256"] == m2["sha256"] and len(m1["sha256"]) == 64
    assert K.lodo(rows, rets, ys) == K.lodo(rows, rets, ys)


def test_refuses_unregistered_feature_retest_and_window():
    rows, rets, ys, _ = labelled(2)
    with pytest.raises(K.CalibError):
        K.fit_model(rows, ys, ("f.net_flow_15m", "f.not_a_field"))
    with pytest.raises(K.CalibError):
        K.fit_model([{**rows[0], "retest": True}] + rows[1:], ys, K.L8_NAMES)
    with pytest.raises(K.CalibError):
        K.fit_model([{**rows[0], "t_d": "2026-08-28T11:02:00Z"}] + rows[1:], ys, K.L8_NAMES)


def test_ties_go_to_earliest():
    assert K.pick_index([1.0, 3.0, 3.0, 2.0]) == 1


def test_spearman_arithmetic():
    assert K.spearman([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)
    assert K.spearman([1, 2, 3, 4], [4, 3, 2, 1]) == pytest.approx(-1.0)
    assert K.spearman([1, 2, 2, 3], [1, 2, 3, 4]) == pytest.approx(0.9486832980505138)
    assert K.spearman([1, 1, 1], [1, 2, 3]) == 0.0


def test_label_rows_median_ties_censored():
    rows, _ = synth(4, sets_per_day=1, n_card=5, days=1)
    vals = [0.1, 0.2, 0.2, None, 0.5]  # known median 0.2: one tie each of two cards, one censored
    keep, rets, ys, cnt = K.label_rows(rows, {r["card_sha"]: v for r, v in zip(rows, vals)})
    assert cnt == {"censored": 1, "tied": 2, "kept": 2} and ys == [0, 1]


def test_lodo_planted_signal_beats_t8_and_passes_bar5():
    rows, rets, ys, _ = labelled(5, sets_per_day=10, n_card=8, signal=1.0)
    out = K.lodo(rows, rets, ys)
    b = K.bar5(out["ic"])
    assert b["pass"] and b["g_shrunk"] > 0 and 0 <= out["p8_matching_pick_share"] <= 1


def _ic(l, t, a, p):
    return {"L8": l, "T8": t, "T8a": a, "P8": p}


def test_bar5_cases():
    z = [0.0] * 7
    ok = K.bar5(_ic([0.15] * 7, z, z, z))
    assert ok["pass"] and ok["gap"] == pytest.approx(0.15) and ok["g_shrunk"] == pytest.approx(0.075)  # se 0 here
    assert not K.bar5(_ic([0.09] * 7, z, z, z))["a"]            # gap below 0.10
    five = [0.5, 0.5, 0.5, 0.5, 0.5, -0.1, -0.1]               # mean gap 0.2571 but 5 of 7 positive
    r = K.bar5(_ic(five, z, z, z)); assert r["a"] and not r["b"] and not r["pass"]
    six = [0.2] * 6 + [-0.01]
    assert K.bar5(_ic(six, z, z, z))["b"]
    assert not K.bar5(_ic([0.15] * 7, z, [0.2] * 7, z))["pass"]  # T8a beats L8
    assert not K.bar5(_ic([0.15] * 7, z, z, [0.15] * 7))["pass"]  # P8 ties L8
    noisy = K.bar5(_ic([0.5, 0.0, 0.5, 0.0, 0.5, 0.0, 0.5], z, z, z))
    assert noisy["g_shrunk"] < 0.5 * noisy["gap"]               # shrinkage bites
    assert K.bar5(_ic(z, [0.1] * 7, z, z))["g_shrunk"] == 0.0


def test_bar5_null_simulation_pass_rate_below_5_percent():
    passes, n = 0, 40
    for i in range(n):
        rows, rets, ys, _ = labelled(100 + i, sets_per_day=3, n_card=5, signal=0.0)
        passes += K.bar5(K.lodo(rows, rets, ys)["ic"])["pass"]
    assert passes / n < 0.05


def test_pin_refusal_and_label_guard(tmp_path, monkeypatch):
    man = tmp_path / "manifest.json"; man.write_text("{}")
    lab = tmp_path / "labels.jsonl"; lab.write_text(json.dumps({"card_sha": "a", "ret": 0.1}) + "\n")
    pin = tmp_path / "RATINGS-PIN.md"
    opened = []
    real = builtins.open
    monkeypatch.setattr(builtins, "open", lambda f, *a, **k: (opened.append(str(f)), real(f, *a, **k))[1])
    with pytest.raises(K.CalibError):                       # no pin file
        K.load_labels(lab, man, pin)
    pin.write_text("manifest_sha256: " + "0" * 64 + "\n")
    with pytest.raises(K.CalibError):                       # wrong sha
        K.load_labels(lab, man, pin)
    pin.write_text("nothing here\n")
    with pytest.raises(K.CalibError):                       # malformed
        K.load_labels(lab, man, pin)
    assert str(lab) not in opened                           # no label read before a valid pin
    pin.write_text("manifest_sha256: " + hashlib.sha256(b"{}").hexdigest() + "\n")
    assert K.load_labels(lab, man, pin) == {"a": 0.1} and str(lab) in opened


def test_default_pin_absent_on_this_branch():
    if not K.PIN_PATH.exists():
        with pytest.raises(K.CalibError):
            K.read_pin()
