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
    assert cnt == {"censored": 1, "tied": 2, "kept": 2} and ys == [0, None, None, None, 1] and len(keep) == 5


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


# ---------------------------------------------------------------- review fixes (PR #1012)

def test_pin_must_be_exactly_one_anchored_line(tmp_path):
    h = "a" * 64
    pin = tmp_path / "P.md"
    for bad in (f"- manifest_sha256: {h}\n", f"manifest_sha256: {h} extra\n", f"manifest_sha256: {h}\nmanifest_sha256: {h}\n",
                f"# pin\nmanifest_sha256: {h}\n", f"manifest_sha256: `{h}`\n", f"manifest_sha256: {'A' * 64}\n", f"manifest_sha256:  {h}\n", ""):
        pin.write_text(bad)
        with pytest.raises(K.CalibError):
            K.read_pin(pin)
    pin.write_text(f"manifest_sha256: {h}\n")
    assert K.read_pin(pin) == h
    pin.write_text(f"manifest_sha256: {h}")
    assert K.read_pin(pin) == h


def test_fold_of_requires_grid_and_window():
    assert K.fold_of("2026-08-21T12:02:00Z") == 0 and K.fold_of("2026-08-28T10:02:00Z") == 6
    for bad in ("2026-08-21T12:00:00Z", "2026-08-21T12:03:00Z", "2026-08-21T12:02:01Z", "2026-08-21T11:02:00Z",
                "2026-08-28T11:02:00Z", "2026-08-28T12:02:00Z"):
        with pytest.raises(K.CalibError):
            K.fold_of(bad)


def test_kappa_arithmetic():
    assert K.qw_kappa([0, 1, 2, 3], [0, 1, 2, 3]) == pytest.approx(1.0)
    assert K.qw_kappa([0, 0, 3, 3], [3, 3, 0, 0]) == pytest.approx(-1.0)
    assert K.qw_kappa([1, 1, 1], [1, 1, 1]) is None
    # hand value: O01 = .25; E01 = .5*.75, E10 = .5*.25, so expected disagreement .5 (same 1/9 weight): kappa = 1 - .25/.5
    a, b = [0, 0, 1, 1], [0, 1, 1, 1]
    exp = 0.5
    assert K.qw_kappa(a, b) == pytest.approx(exp)


def test_bar2_bar3_bar4():
    f = [(i % 4, (i * 3) % 4, (i * 5 + 1) % 4) for i in range(40)]
    assert K.bar2(f, f)["pass"]
    noise = [(0, 0, 0)] * 40
    assert not K.bar2(f, noise)["pass"]
    one_ok = [(t[0], 0, 0) for t in f]
    assert K.bar2(f, one_ok)["items_ok"] == 1 and not K.bar2(f, one_ok)["pass"]
    assert K.bar3(f)["pass"]
    deg2 = [(0, 0, i % 4) for i in range(40)]
    assert K.bar3(deg2)["degenerate_items"] == 2 and not K.bar3(deg2)["pass"]
    edge = [(0, i % 4, i % 4) for i in range(40)]  # item 0: 100% on one value -> exactly 1 degenerate item
    assert K.bar3(edge)["pass"]
    ninety = [(0 if i < 36 else 1, 0, 0) for i in range(40)]  # 90% exactly is not "more than 90%"
    assert K.bar3(ninety)["top_share"]["flow"] == 0.9 and K.bar3(ninety)["degenerate_items"] == 2
    assert not K.bar4(599)["pass"] and K.bar4(600)["pass"]


def test_offline_verdict_bar4_before_bar5():
    rows, rets, ys, _ = labelled(5, sets_per_day=10, n_card=8, signal=1.0)  # 560 labelled < 600 and bar 5 would pass
    n = sum(y is not None for y in ys)
    assert n < 600 and K.bar5(K.lodo(rows, rets, ys)["ic"])["pass"]
    tri = [tuple(r["ratings"]) for r in rows[:50]]
    v = K.offline_verdict(tri, tri, rows, rets, ys)
    assert v["verdict"] == "NOT_DECIDABLE" and "bar5" not in v and "lodo" not in v
    # raise the bar-4 count by lowering the constant under test
    old = K.MIN_LABELLED
    try:
        K.MIN_LABELLED = 100
        v = K.offline_verdict(tri, tri, rows, rets, ys)
        assert v["verdict"] == "PASS" and "lodo" in v
    finally:
        K.MIN_LABELLED = old


def test_fold_without_a_labelled_triple_set_is_not_decidable():
    rows, rets, ys, _ = labelled(6, sets_per_day=2, n_card=4, signal=0.5)
    for i, r in enumerate(rows):
        if K.fold_of(r["t_d"]) == 3 and i % 4 != 0:   # at most 1 labelled card per set in fold 3
            rets[i], ys[i] = None, None
    out = K.lodo(rows, rets, ys)
    assert out["not_decidable_folds"] == [3] and out["ic"]["L8"][3] is None
    b = K.bar5(out["ic"])
    assert b["verdict"] == "NOT_DECIDABLE" and not b["pass"]


def test_constant_score_sets_counted_and_matching_share_per_fold():
    rows, rets, ys, _ = labelled(7, sets_per_day=3, n_card=5, signal=0.5)
    out = K.lodo(rows, rets, ys)
    for a in ("L8", "T8", "T8a", "P8"):
        assert len(out["constant_score_sets"][a]) == 7 and all(c == 0 for c in out["constant_score_sets"][a])
    assert len(out["p8_matching_pick_share_by_fold"]) == 7
    # a constant-valued design: every score equal -> every set counted, IC 0.0
    flat = [{**r, "f": {**r["f"], "vol_60m": 1.0}, "ratings": [1, 1, 1]} for r in rows]
    f_out = K.lodo(flat, rets, ys)
    assert f_out["constant_score_sets"]["L8"] == [3] * 7 and f_out["ic"]["L8"] == [0.0] * 7


def test_p8_permutes_over_all_cards_then_scores_labelled(monkeypatch):
    rows, rets, ys, _ = labelled(8, sets_per_day=2, n_card=5, signal=0.5)
    ys[1] = rets[1] = None   # a dropped card still takes part in the permutation
    seen = []
    real = R.placebo_ratings
    monkeypatch.setattr(R, "placebo_ratings", lambda t, shas, tri: (seen.append((t, list(shas))), real(t, shas, tri))[1])
    K.lodo(rows, rets, ys)
    full = [s for t, s in seen if rows[1]["card_sha"] in s]
    assert full and all(len(s) == 5 for _, s in seen)


def test_model_stores_pinned_sha_and_age_known_feature():
    rows, rets, ys, _ = labelled(9)
    ids = [i for i, y in enumerate(ys) if y is not None]
    m = K.fit_model([rows[i] for i in ids], [ys[i] for i in ids], K.L8_NAMES, pinned_manifest_sha="b" * 64)
    assert m["pinned_manifest_sha"] == "b" * 64
    assert m["sha256"] != K.fit_model([rows[i] for i in ids], [ys[i] for i in ids], K.L8_NAMES, pinned_manifest_sha="c" * 64)["sha256"]
    assert "f.age_known" in K.T8A_NAMES and K.raw_value({"f": {"age_known": True}}, "f.age_known") == 1.0
