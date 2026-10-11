"""Offline tests for tools/c1nf_item14_reference.py (C1-NF item 14(a): the reference pick list with the canary's pinned model).

A synthetic ml dir (random features under the live engine's 107 column names), a tiny LightGBM model trained here and saved as text (the canary's
file is only checked by sha256), a synthetic tape clock, a synthetic universe. No /data/mal path, no real row. Needs lightgbm, duckdb and pyarrow
(the parity venv)."""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import numpy as np
import pytest

from tools import c1nf_features as cf
from tools import c1nf_item14_reference as ref
from tools import c1nf_model_pin as mp
from tools import c1nf_shadow as cs
from tools import c1nf_walkforward_models as wm

pytest.importorskip("lightgbm")
pytest.importorskip("duckdb")
pytest.importorskip("pyarrow")

D18, D19, D20, D21, D22 = (mp.utc0(f"2026-09-{d}") for d in (18, 19, 20, 21, 22))
START, STOP = D18 + 23 * 3600, D22
BASE_BT = D18 + 20 * 3600
SLOT0 = 400_000_000
FN = list(cf.FEATURE_NAMES)
IX = {n: i for i, n in enumerate(FN)}
# mints: (name, graduation day, first T, last T)
MINTS = [("MA", "2026-09-18", D18 + 22 * 3600, D20), ("MB", "2026-09-18", D18 + 22 * 3600, D20), ("MC", "2026-09-19", D19 + 600, D21),
         ("MD", "2026-09-19", D19 + 600, D21), ("ME", "2026-09-21", D21 + 600, D22 + 3600), ("MF", "2026-09-21", D21 + 600, D22 + 3600)]
MINT_NAME = {i: f"{m[0]}{'x' * 38}" for i, m in enumerate(MINTS)}


def time_h(s: int) -> str:
    return time.strftime("%Y-%m-%dT%H", time.gmtime(s))


def write_tape(tape: Path, skip: set[int]):
    """Hours 09-18T23 .. 09-22T03: one block_time per second (not in `skip`), two prints per block_time (min slot must win)."""
    import pyarrow as pa
    import pyarrow.parquet as pq
    (tape / "trades").mkdir(parents=True)
    all_bt, all_sl = [], []
    first = D18 + 23 * 3600
    for h in range((D22 + 3 * 3600 - first) // 3600):
        bt = np.arange(first + h * 3600, first + (h + 1) * 3600, dtype=np.int64)
        bt = bt[~np.isin(bt, list(skip))]
        slot = SLOT0 + 2 * (bt - first)
        bts, sl = np.concatenate([bt, bt]), np.concatenate([slot + 1, slot])
        pq.write_table(pa.table({"block_time": pa.array(bts), "slot": pa.array(sl)}), str(tape / "trades" / f"{time_h(first + h * 3600)}.parquet"))
        all_bt.append(bts)
        all_sl.append(sl)
    return np.concatenate(all_bt), np.concatenate(all_sl)


def make_world(tmp: Path, skip_minutes=()):
    """ml dir (conf.npz + rule.json), model file, universe, tape. Returns a dict of everything the tests compare against."""
    import lightgbm as lgb
    import pyarrow as pa
    import pyarrow.parquet as pq
    rng = np.random.default_rng(5)
    ts, mids = [], []
    for i, (_, _, a, b) in enumerate(MINTS):
        tt = np.arange(a, b, 60, dtype=np.int64)
        tt = tt[rng.random(len(tt)) < 0.25]
        ts.append(tt)
        mids.append(np.full(len(tt), i, np.int64))
    t, mid = np.concatenate(ts), np.concatenate(mids)
    # generation order is mint-major, not time order: the tool must sort
    n = len(t)
    F = rng.normal(size=(n, len(FN))).astype(np.float32)
    F[:, IX["v5"]], F[:, IX["surge"]], F[:, IX["new5"]], F[:, IX["qreal"]] = 2.0, 4.0, 0.0, 30.0
    drop = rng.random(n) < 0.15
    F[drop, IX["qreal"]] = 5.0                                          # fails stage 1
    F[:, IX["h_top1"]] = rng.random(n).astype(np.float32)
    F[rng.random(n) < 0.03, IX["h_top1"]] = np.nan
    y = 0.06 + 0.05 * rng.normal(size=n)
    model = lgb.train({"objective": "huber", "seed": 1, "num_threads": 1, "deterministic": True, "force_row_wise": True, "verbose": -1},
                      lgb.Dataset(F, y), num_boost_round=8)
    mdir = tmp / "model"
    mdir.mkdir()
    mpath = mdir / "m.txt"
    model.save_model(str(mpath))
    msha = hashlib.sha256(mpath.read_bytes()).hexdigest()
    day_names = np.array(["2026-09-18", "2026-09-19", "2026-09-20", "2026-09-21"], dtype="U10")
    ml = tmp / "ml"
    ml.mkdir()
    np.savez_compressed(ml / "conf.npz", F=F, fnames=np.array(FN), mid=mid, t=t, day=np.zeros(n, np.int64), days=day_names[:1],
                        # decoys the code must never open
                        pnl_E3_p=rng.normal(size=n), xt_E3_p=t + 600, pnl_E1_p=rng.normal(size=n), ssb_p=np.zeros(n, np.int64))
    (ml / "rule.json").write_text(json.dumps(json.loads((mp.ART / "rule.json").read_text())))
    uni = tmp / "universe.parquet"
    pq.write_table(pa.table({"mid": pa.array(np.arange(len(MINTS), dtype=np.int64)), "mint": pa.array([MINT_NAME[i] for i in range(len(MINTS))]),
                             "gday": pa.array([m[1] for m in MINTS])}), str(uni))
    skip = set()
    for m in skip_minutes:
        skip |= {m, m + 1, m + 2}
    bt, sl = write_tape(tmp / "tape", skip)
    return dict(ml=ml, mpath=mpath, msha=msha, uni=uni, tape=tmp / "tape", F=F, t=t, mid=mid, bt=bt, sl=sl, model=model)


def build(w, **kw):
    return ref.build_reference(w["ml"], w["uni"], w["tape"], model_path=w["mpath"], model_sha256=w["msha"], pinned=False, **kw)


def brute(w):
    """An independent implementation: per row in (t, mid) order, window, stage 1, pred > 0.02, cap, then the book with bt from the tape."""
    F, t, mid = w["F"], w["t"], w["mid"]
    s1 = (((F[:, IX["v5"]] >= 1.0) & (F[:, IX["surge"]] >= 3.0)) | (F[:, IX["new5"]] >= 20.0)) & (F[:, IX["qreal"]] >= 20.0)
    pred = w["model"].predict(F)
    book, out = {}, []
    for i in np.lexsort((mid, t)):
        if not (START <= t[i] < STOP) or not s1[i] or not pred[i] > 0.02:
            continue
        h = float(F[i, IX["h_top1"]])
        if not (np.isfinite(h) and h <= 0.5):
            continue
        name = MINT_NAME[int(mid[i])]
        if t[i] < book.get(name, -1.0) + 60:
            continue
        j = w["bt"] >= t[i]
        bt = int(w["bt"][j].min())
        sd = int(w["sl"][w["bt"] == bt].min())
        book[name] = bt + 1.3 + 300 + 0.55
        out.append(f"{name},{int(t[i]) * 1000},{sd}")
    return sorted(out)


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("w14a")
    skip_min = (D19 + 5 * 3600 + 17 * 60, D20 + 3600, START + 600)
    w = make_world(tmp, skip_minutes=skip_min)
    w["skip_min"] = skip_min
    return w


def test_pins_match_the_executor_and_the_manifest():
    import re
    src = (ref.ROOT / "tools" / "c1nf_executor.py").read_text()                 # read as text: the parity venv has no solders
    pins = re.search(r"^C1NF_MODEL_SHA256: frozenset\[str\] = frozenset\(\{([^}]*)\}\)", src, re.M).group(1)
    assert re.findall(r'"([0-9a-f]{64})"', pins) == [ref.CANARY_SHA256]
    assert wm.sha256_file(ref.CANARY_MODEL) == ref.CANARY_SHA256
    assert json.loads((ref.ROOT / "ARTIFACTS" / "c1nf_model" / "manifest.json").read_text())["models"][0]["sha256"] == ref.CANARY_SHA256
    assert (ref.S3_START, ref.DECIDE_TO) == ("2026-09-18T23", "2026-09-22T00")
    assert ref.hour_epoch(ref.S3_START) == START and ref.hour_epoch(ref.DECIDE_TO) == D22


def test_reference_equals_an_independent_book(world):
    recs, meta = build(world)
    want = brute(world)
    from tools import c1nf_parity_exact as px
    got = sorted(px.p3_line(r) for r in recs)
    assert got == want and len(want) > 20
    assert meta["counts"]["picks"] == len(want) and meta["counts"]["blocked_by_book"] > 0     # the book actually blocked rows
    assert meta["counts"]["selected_capped"] == len(want) + meta["counts"]["blocked_by_book"]
    assert any(r["decision_T_ms"] // 1000 < D20 for r in recs) and any(D20 <= r["decision_T_ms"] // 1000 < D21 for r in recs)


def test_book_starts_at_s3_start_and_decisions_stop_at_decide_to(world):
    recs, _ = build(world)
    Ts = [r["decision_T_ms"] // 1000 for r in recs]
    assert min(Ts) >= START and max(Ts) < STOP
    # rows before the start exist in the input and are never picks; rows of ME/MF at or after 09-22T00 exist and are never picks
    assert (world["t"] < START).any() and (world["t"] >= STOP).any()
    # a different start moves the first picks: the book is not carried in from earlier rows
    recs2, _ = build(world, start="2026-09-19T00")
    assert min(r["decision_T_ms"] for r in recs2) // 1000 >= D19
    recs3, _ = build(world, decide_to="2026-09-21T00")
    assert max(r["decision_T_ms"] for r in recs3) // 1000 < D21


def test_reentry_blocks_until_bt_plus_hold_and_lag_plus_cooldown():
    cands = [{"mint": "A", "T": 1000, "bt": 1003}, {"mint": "A", "T": 1000 + 60 * 6, "bt": 1360}, {"mint": "A", "T": 1000 + 60 * 7, "bt": 1420},
             {"mint": "B", "T": 1060, "bt": 1060}]
    # book[A] = 1003 + 1.3 + 300 + 0.55 = 1304.85; blocked while T < 1364.85; T = 1360 blocked, T = 1420 free
    taken, blocked = ref.shadow_book(cands)
    assert taken == [0, 3, 2]
    assert blocked == 1
    # exact float edge: T = bt + 1.3 + 300 + 0.55 + 60 is free (T < x is the drop condition), one second less is dropped
    edge = 1003 + cs.PRIMARY_LAT + cs.EXIT_S + cs.EXIT_LAG_S + cs.REENTRY_S
    assert edge == pytest.approx(1364.85)
    t_ok = [{"mint": "A", "T": 1000, "bt": 1003}, {"mint": "A", "T": 1365, "bt": 1365}]
    assert ref.shadow_book(t_ok) == ([0, 1], 0)
    t_no = [{"mint": "A", "T": 1000, "bt": 1003}, {"mint": "A", "T": 1364, "bt": 1364}]
    assert ref.shadow_book(t_no) == ([0], 1)


def test_sd_and_bt_come_from_the_tape_clock_and_slots_equal_the_p0_tool(world):
    t0 = world["skip_min"][0]
    rows = [("2026-09-19", t0), ("2026-09-19", D19 + 3 * 3600 + 60), ("2026-09-19", D20 + 3600 + 5), ("2026-09-21", D21 + 7200)]
    sd, bt, info = ref.decision_clock(world["tape"], rows)
    assert bt[0] == t0 + 3 and bt[1] == D19 + 3 * 3600 + 60 and bt[2] == D20 + 3600 + 5          # the skipped minute's first block_time is 3 s later
    sd0, _ = wm.decision_slots(world["tape"], rows)
    assert np.array_equal(sd, sd0)
    want = [int(world["sl"][world["bt"] == b].min()) for b in bt]
    assert list(sd) == want and info["clipped_to_last_clock_entry"] == 0


def test_only_the_base_columns_are_opened(world, monkeypatch):
    log = []
    real = np.load

    class Spy:
        def __init__(self, z):
            self.z, self.files = z, z.files

        def __enter__(self):
            return self

        def __exit__(self, *a):
            self.z.close()

        def __getitem__(self, k):
            log.append(k)
            return self.z[k]

    monkeypatch.setattr(np, "load", lambda p, *a, **k: Spy(real(p, *a, **k)))
    build(world)
    assert set(log) == set(wm.BASE_KEYS)
    assert not [k for k in log if k.startswith(("pnl_", "xt_", "ssb_", "nearby_", "guard_", "exr_", "xk_"))]
    with pytest.raises(wm.ColumnRefused):
        wm.read_npz(world["ml"] / "conf.npz", ("pnl_E3_p",), wm.BASE_KEYS)


def test_outputs_format_md5_sidecars_refusal_and_determinism(world, tmp_path):
    from tools import c1nf_parity_exact as px
    recs, meta = build(world)
    res = ref.write_outputs(tmp_path / "o1", recs, meta)
    out = tmp_path / "o1"
    p3 = px.p3_md5(recs)
    f20 = (out / ref.OUT_0920).read_bytes()
    assert hashlib.md5(f20).hexdigest() == p3["days"][ref.COMPARED_DAY]["md5"] == res["0920"]["md5"]
    lines = f20.decode().split("\n")
    assert lines[-1] == "" and lines[:-1] == sorted(lines[:-1]) and len(lines) - 1 == p3["days"][ref.COMPARED_DAY]["n"] > 0
    for ln in lines[:-1]:
        mint, tms, sd = ln.split(",")
        assert mint in MINT_NAME.values() and len(ln.split(",")) == 3
        assert px.utc_day(int(tms) // 1000) == ref.COMPARED_DAY and int(sd) > 0
    allb = (out / ref.OUT_ALL).read_bytes()
    assert hashlib.md5(allb).hexdigest() == p3["md5"] and len(allb.decode().split("\n")) - 1 == len(recs)
    pred = (out / ref.OUT_ALL_PRED).read_text().splitlines()
    assert len(pred) == len(recs) and all(len(ln.split(",")) == 5 for ln in pred)
    assert [",".join(ln.split(",")[:3]) for ln in pred] == sorted(all_ln for all_ln in allb.decode().splitlines())
    for nm in (ref.OUT_0920, ref.OUT_ALL, ref.OUT_ALL_PRED):
        sha, name = (out / (nm + ".sha256")).read_text().split()
        assert name == nm and sha == hashlib.sha256((out / nm).read_bytes()).hexdigest()
    doc = json.loads((out / ref.OUT_META).read_text())
    assert doc["p3_md5_compared_day"] == res["0920"]["md5"] and doc["model"]["sha256"] == world["msha"]
    assert doc["reads"]["conf_npz_keys"] == list(wm.BASE_KEYS)
    assert not any(k.startswith("pnl") for k in json.dumps(doc).split('"'))
    with pytest.raises(SystemExit):
        ref.write_outputs(out, recs, meta)                                                  # refuse-overwrite
    recs2, meta2 = build(world)
    ref.write_outputs(tmp_path / "o2", recs2, meta2)
    for nm in (ref.OUT_0920, ref.OUT_ALL, ref.OUT_ALL_PRED, ref.OUT_META, ref.OUT_0920 + ".sha256"):
        assert (tmp_path / "o1" / nm).read_bytes() == (tmp_path / "o2" / nm).read_bytes()


def test_pinned_mode_refuses_another_model_or_window(world):
    with pytest.raises(SystemExit, match="canary"):
        ref.build_reference(world["ml"], world["uni"], world["tape"], model_path=world["mpath"], model_sha256=world["msha"])
    with pytest.raises(SystemExit, match="GO-PLAN window"):
        ref.build_reference(world["ml"], world["uni"], world["tape"], start="2026-09-19T00")
    with pytest.raises(Exception):                                                          # a wrong sha for the file is refused by the ModelSet
        ref.build_reference(world["ml"], world["uni"], world["tape"], model_path=world["mpath"], model_sha256="0" * 64, pinned=False)


def test_near_tie_counts_are_reported(world):
    _, meta = build(world)
    nt = meta["near_ties"]
    assert set(nt) == {"stage1_pred_within_eps_of_threshold", "selected_h_top1_within_eps_of_cap", "eps"} and nt["eps"] == 1e-6
