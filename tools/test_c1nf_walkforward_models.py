"""Offline tests for tools/c1nf_walkforward_models.py (DEC-026 item 13 P0 inputs: 21 walk-forward models, Rdet and R419 exports).

A tiny synthetic ml dir (random features under the live engine's 107 column names, a few hundred stage-1 rows per day, 12 rounds instead of 400),
a synthetic tape clock and a synthetic VERIFY dir. No /data/mal path, no real row. Needs lightgbm, duckdb and pyarrow (the parity venv); the
pure-numpy tests (column refusal, book rule) run anywhere."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from tools import c1nf_features as cf
from tools import c1nf_model_pin as mp
from tools import c1nf_walkforward_models as wm

DISC_DAYS = ("2026-08-20", "2026-08-21")
CONF_DAYS = ("2026-09-04", "2026-09-05")
GDAY = "2026-09-04"
PURGE = 3600
N_PER_DAY = 900                                   # one row per minute from 00:00, 15 hours
N_MID = 40
BASE_BT = 1_790_000_000
SLOT0 = 400_000_000


def _day0(day: str) -> int:
    return mp.utc0(day)


def _mints() -> list[str]:
    return [f"MINT{i:03d}xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx" for i in range(N_MID)]


def _frame(days, rng, extra_t=()):
    """Stage-1 rows (v5, surge, qreal set; ~20% fail qreal), h_top1 uniform (about 40% above the 0.5 cap), a label that depends on one feature."""
    names = list(cf.FEATURE_NAMES)
    ix = {n: i for i, n in enumerate(names)}
    t, day = [], []
    for di, d in enumerate(days):
        ts = [_day0(d) + 60 * k for k in range(N_PER_DAY)]
        t += ts
        day += [di] * len(ts)
    for tt in extra_t:                                         # boundary rows: put them in the last day
        t.append(tt)
        day.append(len(days) - 1)
    t = np.asarray(t, np.int64)
    n = len(t)
    F = rng.normal(size=(n, len(names))).astype(np.float32)
    F[:, ix["v5"]] = 2.0
    F[:, ix["surge"]] = 4.0
    F[:, ix["new5"]] = 0.0
    F[:, ix["qreal"]] = np.where(rng.random(n) < 0.2, 5.0, 30.0)
    F[:, ix["h_top1"]] = rng.random(n).astype(np.float32)
    mid = rng.integers(0, N_MID, size=n).astype(np.int64)
    sig = F[:, 0] > 0
    pnl = (250_000_000 * (0.12 * sig - 0.05 + 0.02 * rng.normal(size=n))).astype(np.float64)
    xt = (t + rng.integers(120, 1500, size=n)).astype(np.int64)
    return dict(F=F, fnames=np.array(names), mid=mid, t=t, day=np.asarray(day, np.int64), days=np.array(list(days), dtype="U10"),
                seg=np.zeros(n, np.int8), blk=np.array(["b"] * n, dtype="U32"), fmax60=np.ones(n, np.float32),
                pnl_E3_p=pnl, xt_E3_p=xt,
                # decoys the code must never open
                pnl_E1_p=pnl * 2, pnl_E3_b=pnl * 3, ssb_p=np.zeros(n, np.int64), nearby_p=np.zeros(n), guard_p=np.zeros(n, np.int8),
                exr_p=np.ones(n, np.float32), xk_E3_p=np.zeros(n, np.int64))


def make_ml(tmp: Path, rounds: int = 12, boundary: bool = True):
    ml = tmp / "ml"
    ml.mkdir()
    rng = np.random.default_rng(3)
    disc = _frame(DISC_DAYS, rng)
    cut_second_day = _day0(CONF_DAYS[1]) - PURGE
    conf = _frame(CONF_DAYS, rng, extra_t=(cut_second_day - 1, cut_second_day) if boundary else ())
    np.savez_compressed(ml / "disc.npz", **disc)
    np.savez_compressed(ml / "conf.npz", **conf)
    rule = json.loads((mp.ART / "rule.json").read_text())
    rule["rounds"] = rounds
    (ml / "rule.json").write_text(json.dumps(rule))
    return ml, disc, conf, rule


def _s1(d):
    ix = {n: i for i, n in enumerate(cf.FEATURE_NAMES)}
    F = d["F"]
    return (((F[:, ix["v5"]] >= 1.0) & (F[:, ix["surge"]] >= 3.0)) | (F[:, ix["new5"]] >= 20.0)) & (F[:, ix["qreal"]] >= 20.0)


def _spy_np_load(monkeypatch):
    """Record every key read from an npz (np.load is swapped for a lazy-key wrapper for the length of the test)."""
    log = []
    real = np.load

    class Spy:
        def __init__(self, z):
            self.z = z
            self.files = z.files

        def __enter__(self):
            return self

        def __exit__(self, *a):
            self.z.close()

        def __getitem__(self, k):
            log.append(k)
            return self.z[k]

    monkeypatch.setattr(np, "load", lambda p, *a, **k: Spy(real(p, *a, **k)))
    return log


@pytest.fixture(scope="module")
def lgb():
    return pytest.importorskip("lightgbm")


@pytest.fixture(scope="module")
def built(tmp_path_factory, lgb):
    """ml dir + one trained manifest dir (shared: training is the slow part)."""
    tmp = tmp_path_factory.mktemp("wf")
    ml, disc, conf, rule = make_ml(tmp)
    out = tmp / "models"
    doc = wm.train_walkforward(ml, out, pinned=False, expect_days=CONF_DAYS, log=lambda *_: None)
    return dict(tmp=tmp, ml=ml, disc=disc, conf=conf, rule=rule, out=out, doc=doc)


# ---- train ------------------------------------------------------------------------------------------------------------------------------
def test_one_model_per_day_in_the_harness_manifest_format(built):
    doc, out = built["doc"], built["out"]
    assert [e["from_day"] for e in doc["models"]] == list(CONF_DAYS)
    for e in doc["models"]:
        assert set(e) == {"from_day", "file", "sha256"}                       # exactly the harness's entry format
        p = out / e["file"]
        assert e["file"] == f"model-{e['from_day']}.txt"
        assert hashlib.sha256(p.read_bytes()).hexdigest() == e["sha256"]
    on_disk = json.loads((out / wm.MANIFEST).read_text())
    assert on_disk == doc
    sha_line = (out / wm.MANIFEST_SHA).read_text().split()
    assert sha_line == [hashlib.sha256((out / wm.MANIFEST).read_bytes()).hexdigest(), wm.MANIFEST]


def test_day_cut_is_strictly_before_d_midnight_minus_purge(built):
    d = {k: np.concatenate([built["disc"][k], built["conf"][k]]) for k in ("F", "t")}
    s1 = _s1(d)
    for per, D in zip(built["doc"]["training"], CONF_DAYS):
        cut = _day0(D) - PURGE
        assert per["cut_t"] == cut
        want = int((s1 & (d["t"] < cut)).sum())
        assert per["train_rows"] == want
        assert per["last_train_t"] < cut
    # the boundary rows: t = cut - 1 is trained on, t = cut is not (D = second day)
    cut2 = _day0(CONF_DAYS[1]) - PURGE
    le = int((s1 & (d["t"] <= cut2)).sum())
    assert built["doc"]["training"][1]["train_rows"] == le - int((s1 & (d["t"] == cut2)).sum())
    assert built["doc"]["training"][1]["train_rows"] != le
    # a later day trains on at least the earlier day's rows
    assert built["doc"]["training"][1]["train_rows"] > built["doc"]["training"][0]["train_rows"]


def test_manifest_loads_in_the_harness_and_picks_the_day_model(built):
    from tools import c1nf_shadow as cs
    ms = cs.ModelSet.from_manifest(built["out"] / wm.MANIFEST)              # sha256-checked, relative paths, lightgbm loader
    want = {e["from_day"]: e["sha256"] for e in built["doc"]["models"]}
    for D in CONF_DAYS:
        assert ms.for_day(D)[1] == want[D]
    assert ms.for_day("2026-09-10")[1] == want[CONF_DAYS[-1]]                 # a gap day uses the latest earlier entry
    with pytest.raises(cs.Refused):
        ms.for_day("2026-09-03")                                              # before the first entry: no pick without a model


def test_models_are_trained_from_scratch_per_day(built, lgb):
    m1 = lgb.Booster(model_file=str(built["out"] / f"model-{CONF_DAYS[0]}.txt"))
    m2 = lgb.Booster(model_file=str(built["out"] / f"model-{CONF_DAYS[1]}.txt"))
    assert m1.num_trees() == m2.num_trees() == built["rule"]["rounds"]
    assert m1.num_feature() == len(cf.FEATURE_NAMES)
    assert (built["out"] / f"model-{CONF_DAYS[0]}.txt").read_bytes() != (built["out"] / f"model-{CONF_DAYS[1]}.txt").read_bytes()
    for per in built["doc"]["training"]:
        assert per["max_abs_diff_mem_vs_file"] == 0.0
    p = built["doc"]["recipe"]["params"]
    assert (p["seed"], p["num_threads"], p["deterministic"]) == (1, 3, True)


def test_inputs_are_recorded_not_checked_against_verify(built):
    inp = built["doc"]["inputs"]
    ml = built["ml"]
    assert inp["disc.npz"] == hashlib.sha256((ml / "disc.npz").read_bytes()).hexdigest()
    assert inp["conf.npz"] == hashlib.sha256((ml / "conf.npz").read_bytes()).hexdigest()
    assert inp["disc.npz"] != mp.DISC_SHA256 and inp["conf.npz"] != mp.CONF_SHA256
    assert inp["pinned_rule.json"] == mp.pinned_sums()["rule.json"]
    assert inp["mlcommon.py"] == mp.pinned_sums()["scripts/mlcommon.py"]


def test_two_runs_are_byte_equal_and_compare_sees_a_difference(built, tmp_path):
    out2 = tmp_path / "models2"
    wm.train_walkforward(built["ml"], out2, pinned=False, expect_days=CONF_DAYS, log=lambda *_: None)
    for e in built["doc"]["models"]:
        assert (built["out"] / e["file"]).read_bytes() == (out2 / e["file"]).read_bytes()
    assert (built["out"] / wm.MANIFEST).read_bytes() == (out2 / wm.MANIFEST).read_bytes()
    res = wm.compare_dirs(built["out"], out2)
    assert res["ok"] and res["n_files"] == 1 + len(CONF_DAYS) and all(r["equal"] for r in res["files"])
    f = out2 / built["doc"]["models"][0]["file"]
    f.write_bytes(f.read_bytes() + b" ")
    bad = wm.compare_dirs(built["out"], out2)
    assert not bad["ok"] and [r["file"] for r in bad["files"] if not r["equal"]] == [f.name]
    assert wm.main(["compare", str(built["out"]), str(out2)]) == 1


def test_train_refuses_nonempty_out_wrong_days_and_unpinned_rule(built, tmp_path):
    with pytest.raises(SystemExit, match="not empty"):
        wm.train_walkforward(built["ml"], built["out"], pinned=False, expect_days=CONF_DAYS, log=lambda *_: None)
    with pytest.raises(SystemExit, match="confirmation days"):
        wm.train_walkforward(built["ml"], tmp_path / "a", pinned=False, expect_days=wm.CONFIRMATION_DAYS, log=lambda *_: None)
    with pytest.raises(SystemExit, match="pinned rule.json"):                 # the fixture's rule has 12 rounds: not the pinned file
        wm.train_walkforward(built["ml"], tmp_path / "b", pinned=True, expect_days=CONF_DAYS, log=lambda *_: None)
    assert not (tmp_path / "b").exists() or not any((tmp_path / "b").iterdir())


def test_confirmation_days_are_the_21():
    assert len(wm.CONFIRMATION_DAYS) == 21
    assert wm.CONFIRMATION_DAYS[0] == "2026-09-03" and wm.CONFIRMATION_DAYS[-1] == "2026-09-25"
    assert "2026-09-16" not in wm.CONFIRMATION_DAYS and "2026-09-17" not in wm.CONFIRMATION_DAYS and "2026-09-15" in wm.CONFIRMATION_DAYS


def test_training_reads_the_label_only_and_never_a_leg_or_fail_input(built, monkeypatch, tmp_path):
    log = _spy_np_load(monkeypatch)
    wm.load_rows(built["ml"], built["rule"], "train")
    assert set(log) == set(wm.BASE_KEYS) | {"pnl_E3_p"}
    del log[:]
    wm.load_rows(built["ml"], built["rule"], "book")
    assert set(log) == set(wm.BASE_KEYS) | {"xt_E3_p"}
    assert not {"pnl_E3_p", "pnl_E1_p", "pnl_E3_b", "ssb_p", "nearby_p", "guard_p", "exr_p", "xk_E3_p"} & set(log)


# ---- guarded reads / export columns -----------------------------------------------------------------------------------------------------
def test_reader_refuses_any_column_outside_the_allow_list(built):
    book = wm.allowed_keys(built["rule"], "book")
    train = wm.allowed_keys(built["rule"], "train")
    assert "pnl_E3_p" not in book and "xt_E3_p" in book and "xt_E3_p" not in train and "pnl_E3_p" in train
    for k in ("pnl_E3_p", "pnl_E1_p", "pnl_E3_b", "ssb_p", "nearby_p", "guard_p", "exr_p", "xk_E3_p", "nofail", "flat", "press"):
        with pytest.raises(wm.ColumnRefused):
            wm.read_npz(built["ml"] / "conf.npz", ["t", k], book)
    ok = wm.read_npz(built["ml"] / "conf.npz", ["t", "xt_E3_p"], book)
    assert set(ok) == {"t", "xt_E3_p"}
    with pytest.raises(wm.ColumnRefused):                                      # the primary-book file: idx and pred only
        wm.read_npz(built["ml"] / "conf.npz", ["t"], wm.PRIMARY_KEYS)


def test_exporter_refuses_any_column_outside_the_five(tmp_path):
    ok = {"mint": "M1", "decision_T_ms": 1_000, "SD_slot": 5, "pred": 0.03, "h_top1": 0.25}
    for extra in ("pnl", "flat", "nofail", "press", "xt", "ret", "label", "idx"):
        with pytest.raises(wm.ColumnRefused):
            wm.export_records(tmp_path / "x.csv", [{**ok, extra: 1.0}])
    with pytest.raises(wm.ColumnRefused):                                      # a missing column is refused too
        wm.export_records(tmp_path / "x.csv", [{k: v for k, v in ok.items() if k != "h_top1"}])
    with pytest.raises(wm.ColumnRefused):
        wm.export_records(tmp_path / "x.csv", [ok, {**ok, "mint": "M2", "flat": 2.0}])
    assert not (tmp_path / "x.csv").exists() and not (tmp_path / "x.csv.sha256").exists()
    res = wm.export_records(tmp_path / "x.csv", [ok])
    assert res["n"] == 1 and (tmp_path / "x.csv").read_text() == "M1,1000,5,0.03,0.25\n"
    assert (tmp_path / "x.csv.sha256").read_text() == f"{res['sha256']}  x.csv\n"
    with pytest.raises(SystemExit, match="exists"):
        wm.export_records(tmp_path / "x.csv", [ok])


def test_export_sort_is_p3s_and_h_top1_is_the_exact_float32_value(tmp_path):
    from tools import c1nf_parity_exact as px
    recs = [{"mint": "B", "decision_T_ms": 60_000, "SD_slot": 7, "pred": 0.1, "h_top1": float(np.float32(0.13))},
            {"mint": "A", "decision_T_ms": 120_000, "SD_slot": 23, "pred": 0.2, "h_top1": 0.5},
            {"mint": "A", "decision_T_ms": 60_000, "SD_slot": 2, "pred": 0.3, "h_top1": 0.4}]
    wm.export_records(tmp_path / "x.csv", recs)
    lines = (tmp_path / "x.csv").read_text().splitlines()
    # P3 sorts the line STRINGS: "A,120000,23" < "A,60000,2" ('1' < '6'), not numerically
    assert [ln.split(",")[:3] for ln in lines] == [["A", "120000", "23"], ["A", "60000", "2"], ["B", "60000", "7"]]
    cut = "".join(",".join(ln.split(",")[:3]) + "\n" for ln in lines)
    p3 = px.p3_md5([{"mint": r["mint"], "decision_T_ms": r["decision_T_ms"], "SD_slot": r["SD_slot"]} for r in recs])
    assert hashlib.md5(cut.encode()).hexdigest() == p3["md5"]                  # cutting 3 fields gives P3's list byte for byte
    b = next(ln for ln in lines if ln.startswith("B,"))
    assert float(b.split(",")[4]) == float(np.float32(0.13))                   # exact widening, not the shortest float32 repr
    assert float(b.split(",")[4]) != 0.13
    with pytest.raises(SystemExit, match="twice"):
        wm.export_records(tmp_path / "y.csv", [recs[0], dict(recs[0])])


# ---- the book rule ----------------------------------------------------------------------------------------------------------------------
def test_local_book_equals_the_pinned_mlcommon_book():
    ml, _r, _s = mp.load_pinned_mlcommon()
    rng = np.random.default_rng(1)
    n = 3000
    t = rng.integers(0, 40_000, n).astype(np.int64)
    mid = rng.integers(0, 60, n).astype(np.int64)
    xt = t + rng.integers(0, 900, n)
    sel = rng.random(n) < 0.4
    a = wm._book(t, mid, xt, sel)
    b = ml.book({"t": t, "mid": mid, "xt_E3_p": xt}, sel, "E3", "p")
    assert len(a) > 100 and np.array_equal(a, b)


# ---- SD from the tape -------------------------------------------------------------------------------------------------------------------
def _write_tape(tape: Path, skip_minutes=()):
    """Hour files 2026-09-04T00 .. 2026-09-05T23: one row per second (two slots), slot = SLOT0 + 2 * (bt - BASE_BT); `skip_minutes` drop the
    block_times of whole seconds [m, m + 3) so the first block_time >= T is later than T."""
    import pyarrow as pa
    import pyarrow.parquet as pq
    (tape / "trades").mkdir(parents=True)
    t0 = _day0(CONF_DAYS[0])
    skip = set()
    for m in skip_minutes:
        skip |= {m, m + 1, m + 2}
    all_bt, all_slot = [], []
    for h in range(48):
        bt = np.arange(t0 + h * 3600, t0 + (h + 1) * 3600, dtype=np.int64)
        bt = np.array([b for b in bt if int(b) not in skip], dtype=np.int64)
        slot = SLOT0 + 2 * (bt - t0)
        slot2 = slot + 1                                                        # a second print in the same block_time with a later slot
        bts = np.concatenate([bt, bt])
        sl = np.concatenate([slot2, slot])                                      # min(slot) per block_time must still win
        pq.write_table(pa.table({"block_time": pa.array(bts), "slot": pa.array(sl)}),
                       str(tape / "trades" / (time_h(t0 + h * 3600) + ".parquet")))
        all_bt.append(bts)
        all_slot.append(sl)
    return np.concatenate(all_bt), np.concatenate(all_slot)


def time_h(s: int) -> str:
    import time
    return time.strftime("%Y-%m-%dT%H", time.gmtime(s))


def test_decision_slots_is_the_min_slot_of_the_first_block_time_at_or_after_T(tmp_path):
    pytest.importorskip("duckdb")
    pytest.importorskip("pyarrow")
    t0 = _day0(CONF_DAYS[0])
    skipped = t0 + 5 * 3600 + 17 * 60
    bt, sl = _write_tape(tmp_path / "tape", skip_minutes=(skipped, t0 + 20 * 3600))
    Ts = [t0 + 3600 * 3, t0 + 3600 * 3 + 60, skipped, t0 + 20 * 3600, t0 + 23 * 3600 + 59 * 60]
    got, info = wm.decision_slots(tmp_path / "tape", [(GDAY, T) for T in Ts])
    want = [int(sl[bt == bt[bt >= T].min()].min()) for T in Ts]
    assert list(got) == want
    assert want[2] == SLOT0 + 2 * (skipped + 3 - t0)                            # the first block_time >= T is 3 s later
    from tools.c1nf_features import SlotClock
    c = SlotClock()
    for b, s in zip(bt.tolist(), sl.tolist()):
        c.add(s, b)
    assert [c.decision_slot(T) for T in Ts] == want                             # the engine's clock agrees
    assert info["graduation_days"] == 1 and info["clipped_to_last_clock_entry"] == 0 and info["tape_files_read"] > 0


def test_decision_slots_refuses_a_non_exploration_hour(tmp_path):
    pytest.importorskip("duckdb")
    pytest.importorskip("pyarrow")
    import pyarrow as pa
    import pyarrow.parquet as pq
    from tools import c1nf_shadow as cs
    (tmp_path / "tape" / "trades").mkdir(parents=True)
    pq.write_table(pa.table({"block_time": pa.array([1], pa.int64()), "slot": pa.array([1], pa.int64())}),
                   str(tmp_path / "tape" / "trades" / "2026-10-12T00.parquet"))
    with pytest.raises(cs.Refused):
        wm.decision_slots(tmp_path / "tape", [("2026-10-12", _day0("2026-10-12") + 3600)])


# ---- Rdet -------------------------------------------------------------------------------------------------------------------------------
def _universe(tmp: Path):
    import pyarrow as pa
    import pyarrow.parquet as pq
    p = tmp / "universe.parquet"
    pq.write_table(pa.table({"mid": pa.array(np.arange(N_MID, dtype=np.int64)), "mint": pa.array(_mints()),
                             "gday": pa.array([GDAY] * N_MID)}), str(p))
    return p


def _brute_book(conf, pred, th, cap=0.5, cap_first=True):
    """An independent book: cap before the book (the spec order; cap_first=False is C1's order, book then cap); a mint re-enters only at
    t >= max(exit, t) + 60."""
    ix = {n: i for i, n in enumerate(cf.FEATURE_NAMES)}
    F = conf["F"]
    s1 = _s1(conf)
    keep = np.isfinite(F[:, ix["h_top1"]]) & (F[:, ix["h_top1"]] <= cap)
    ok = s1 & (pred > th) & (keep if cap_first else True)
    free, taken = {}, []
    for i in sorted(np.where(ok)[0], key=lambda i: (conf["t"][i], conf["mid"][i])):
        m = int(conf["mid"][i])
        if conf["t"][i] < free.get(m, -1):
            continue
        taken.append(int(i))
        free[m] = max(int(conf["xt_E3_p"][i]), int(conf["t"][i])) + 60
    return taken if cap_first else [i for i in taken if keep[i]]


@pytest.fixture(scope="module")
def rdet(built, tmp_path_factory, lgb):
    pytest.importorskip("duckdb")
    pytest.importorskip("pyarrow")
    tmp = tmp_path_factory.mktemp("rdet")
    _write_tape(tmp / "tape")
    uni = _universe(tmp)
    recs, meta = wm.build_rdet(built["ml"], built["out"] / wm.MANIFEST, uni, tmp / "tape", pinned=False)
    return dict(tmp=tmp, recs=recs, meta=meta, uni=uni)


def test_rdet_book_is_the_spec_order_cap_then_book_scored_by_the_manifest_files(built, rdet, lgb):
    conf, rule = built["conf"], built["rule"]
    s1 = _s1(conf)
    pred = np.full(len(conf["t"]), np.nan)
    dn = conf["days"][conf["day"]]
    for D in CONF_DAYS:
        te = s1 & (dn == D)
        pred[te] = lgb.Booster(model_file=str(built["out"] / f"model-{D}.txt")).predict(conf["F"][te])
    taken = _brute_book(conf, pred, rule["threshold"])
    assert len(taken) > 30                                                      # the fixture makes a real book
    ix = {n: i for i, n in enumerate(cf.FEATURE_NAMES)}
    mint_of = _mints()
    want = {(mint_of[int(conf["mid"][i])], int(conf["t"][i]) * 1000): (float(pred[i]), float(conf["F"][i, ix["h_top1"]])) for i in taken}
    got = {(r["mint"], r["decision_T_ms"]): (r["pred"], r["h_top1"]) for r in rdet["recs"]}
    assert got == want                                                          # same rows, bit-equal pred and h_top1
    assert rdet["meta"]["n"] == len(taken) == rdet["meta"]["checks"]["n_book"]
    assert all(r["pred"] > rule["threshold"] and r["h_top1"] <= 0.5 for r in rdet["recs"])
    # the cap came BEFORE the book: on this fixture the other order (book, then cap) gives a different set, so the test tells them apart
    assert rdet["meta"]["checks"]["n_selected_capped"] < rdet["meta"]["checks"]["n_selected"]
    assert set(_brute_book(conf, pred, rule["threshold"], cap_first=False)) != set(taken)


def test_rdet_sd_comes_from_the_tape_clock_and_the_export_is_five_sorted_columns(built, rdet):
    for r in rdet["recs"]:
        assert set(r) == set(wm.EXPORT_COLUMNS)
        T = r["decision_T_ms"] // 1000
        assert r["SD_slot"] == SLOT0 + 2 * (T - _day0(CONF_DAYS[0]))
    out = rdet["tmp"] / "ref"
    res = wm._write_export(out, wm.RDET_CSV, rdet["recs"], rdet["meta"])
    lines = (out / wm.RDET_CSV).read_text().splitlines()
    assert len(lines) == res["n"] == len(rdet["recs"])
    assert all(len(ln.split(",")) == 5 for ln in lines)
    keys = [",".join(ln.split(",")[:3]) for ln in lines]
    assert keys == sorted(keys)
    meta = json.loads((out / "rdet.meta.json").read_text())
    assert meta["sha256"] == hashlib.sha256((out / wm.RDET_CSV).read_bytes()).hexdigest()
    assert meta["p3_before_segment_end_cut_and_tie_removal"]["n"] == len(lines)
    assert meta["model_sha256"] == {e["from_day"]: e["sha256"] for e in built["doc"]["models"]}
    assert meta["inputs_sha256"]["manifest.json"] == hashlib.sha256((built["out"] / wm.MANIFEST).read_bytes()).hexdigest()
    with pytest.raises(SystemExit, match="exists"):
        wm._write_export(out, wm.RDET_CSV, rdet["recs"], rdet["meta"])


def test_rdet_opens_only_allowed_keys(built, rdet, monkeypatch):
    log = _spy_np_load(monkeypatch)
    wm.build_rdet(built["ml"], built["out"] / wm.MANIFEST, rdet["uni"], rdet["tmp"] / "tape", pinned=False)
    assert set(log) == set(wm.BASE_KEYS) | {"xt_E3_p"}
    assert not {k for k in log if k.startswith(("pnl_", "ssb_", "nearby_", "guard_", "exr_", "xk_"))}


def test_rdet_refuses_when_the_p2_count_or_the_primary_book_does_not_reproduce(built, rdet, tmp_path):
    counts = tmp_path / "p2_counts.json"
    counts.write_text(json.dumps({"n_c1nf_spec": rdet["meta"]["n"] + 1}))
    # pinned=True also needs the pinned rule.json, which the fixture does not have: the count check is exercised with pinned off + manual ok flag
    _recs, meta = wm.build_rdet(built["ml"], built["out"] / wm.MANIFEST, rdet["uni"], rdet["tmp"] / "tape", pinned=False, p2_counts=counts)
    assert meta["checks"]["n_equal_p2_counts"] is False and meta["checks"]["ok"] is False
    counts.write_text(json.dumps({"n_c1nf_spec": rdet["meta"]["n"]}))
    _recs, meta = wm.build_rdet(built["ml"], built["out"] / wm.MANIFEST, rdet["uni"], rdet["tmp"] / "tape", pinned=False, p2_counts=counts)
    assert meta["checks"]["n_equal_p2_counts"] is True and meta["checks"]["ok"] is True


def test_rdet_primary_book_check_uses_idx_and_pred_only(built, rdet, tmp_path, lgb, monkeypatch):
    disc, conf, rule = built["disc"], built["conf"], built["rule"]
    F = np.vstack([disc["F"], conf["F"]])
    t = np.concatenate([disc["t"], conf["t"]])
    mid = np.concatenate([disc["mid"], conf["mid"]])
    xt = np.concatenate([disc["xt_E3_p"], conf["xt_E3_p"]])
    isconf = np.concatenate([np.zeros(len(disc["t"]), bool), np.ones(len(conf["t"]), bool)])
    dn = np.concatenate([disc["days"][disc["day"]], conf["days"][conf["day"]]])
    s1 = _s1({"F": F})
    pred = np.full(len(F), np.nan)
    for D in CONF_DAYS:
        te = s1 & isconf & (dn == D)
        pred[te] = lgb.Booster(model_file=str(built["out"] / f"model-{D}.txt")).predict(F[te])
    ml, _r, _s = mp.load_pinned_mlcommon()
    sel = s1 & isconf & (pred > rule["threshold"])
    idx = ml.book({"t": t, "mid": mid, "xt_E3_p": xt}, sel, "E3", "p")
    assert len(idx) > 50
    prim = tmp_path / "confirm_primary_trades.npz"
    np.savez(prim, idx=idx, pred=pred[idx], flat=np.ones(len(idx)), nofail=np.ones(len(idx)), press=np.ones(len(idx)))   # decoys stay unopened
    log = _spy_np_load(monkeypatch)
    _recs, meta = wm.build_rdet(built["ml"], built["out"] / wm.MANIFEST, rdet["uni"], rdet["tmp"] / "tape", pinned=False, primary_trades=prim)
    pb = meta["checks"]["primary_book"]
    assert pb["index_equal"] is True and pb["max_abs_diff_pred"] == 0.0 and pb["n_pred_equal"] == pb["n"] == len(idx)
    assert meta["checks"]["ok"] is True
    assert set(log) == set(wm.BASE_KEYS) | {"xt_E3_p", "idx", "pred"}
    assert not {"flat", "nofail", "press"} & set(log)
    monkeypatch.undo()
    np.savez(prim.with_name("bad.npz"), idx=idx, pred=pred[idx] + 1e-9)
    _recs, meta = wm.build_rdet(built["ml"], built["out"] / wm.MANIFEST, rdet["uni"], rdet["tmp"] / "tape", pinned=False,
                                primary_trades=prim.with_name("bad.npz"))
    assert meta["checks"]["ok"] is False and meta["checks"]["primary_book"]["max_abs_diff_pred"] > 0


# ---- R419 -------------------------------------------------------------------------------------------------------------------------------
def make_verify(tmp: Path, n=600, seed=5):
    import pyarrow as pa
    import pyarrow.parquet as pq
    rng = np.random.default_rng(seed)
    v = tmp / "verify" / "v"
    v.mkdir(parents=True)
    t = (_day0(CONF_DAYS[0]) + 60 * rng.integers(0, 600, n)).astype(np.int64)
    mid = rng.integers(0, 25, n).astype(np.int64)
    idx = np.arange(1000, 1000 + n, dtype=np.int64)
    isconf = rng.random(n) < 0.8
    pred = rng.random(n) * 0.2
    h1 = np.where(rng.random(n) < 0.05, np.nan, rng.random(n))
    xt = (t + rng.integers(60, 1200, n)).astype(float)
    sd = (SLOT0 + 2 * (t - _day0(CONF_DAYS[0]))).astype(float)
    mints = np.array([_mints()[int(m)] for m in mid])
    perm = rng.permutation(n)                                                   # sim rows in another order than cands
    np.savez(v / "cands.npz", idx=idx, isconf=isconf, t=t, mid=mid, pred=pred, f_h_top1=np.where(np.isnan(h1), np.nan, h1).astype(np.float32),
             pnl_E3_p=rng.normal(size=n), xt_E3_p=xt.astype(np.int64), ssb_p=np.zeros(n), guard_p=np.zeros(n))
    full = np.full(5000, np.nan)
    full[idx] = pred
    np.savez(v / "preds.npz", pred=full, pdisc=np.zeros(3))
    pq.write_table(pa.table({"idx": idx[perm], "mint": mints[perm], "t": t[perm], "sd": sd[perm], "h1": h1[perm], "xt_p_END_l055": xt[perm],
                             "pnl_p_END_l055": rng.normal(size=n)[perm], "g_p_END_l055": np.zeros(n)[perm]}), str(v / "sim.parquet"))
    (v / "v3_report.py").write_text("# stand-in\n")
    return tmp / "verify", dict(t=t, mid=mid, idx=idx, isconf=isconf, pred=pred, h1=h1, xt=xt, sd=sd, mints=mints)


def test_r419_book_is_v3_reports_main_book_with_the_five_fields(tmp_path):
    pytest.importorskip("pyarrow")
    vd, a = make_verify(tmp_path)
    recs, meta = wm.build_r419(vd, expect_n=None)
    conf = a["isconf"]
    sel = np.isfinite(a["h1"]) & (a["h1"] <= 0.5) & conf
    free, taken = {}, []
    for i in sorted(np.where(sel)[0], key=lambda i: (a["t"][i], a["mid"][i])):
        m = int(a["mid"][i])
        if a["t"][i] < free.get(m, -1):
            continue
        taken.append(int(i))
        free[m] = max(int(a["xt"][i]), int(a["t"][i])) + 60
    assert len(taken) > 20
    want = {(a["mints"][i], int(a["t"][i]) * 1000): (int(a["sd"][i]), float(a["pred"][i]), float(a["h1"][i])) for i in taken}
    got = {(r["mint"], r["decision_T_ms"]): (r["SD_slot"], r["pred"], r["h_top1"]) for r in recs}
    assert got == want
    assert all(set(r) == set(wm.EXPORT_COLUMNS) for r in recs)
    assert meta["all_five_fields_available"] is True and meta["n"] == len(taken)
    assert meta["checks"]["max_abs_diff_pred_vs_preds_npz"] == 0.0
    assert meta["checks"]["max_abs_diff_h1_vs_f_h_top1"] < 1e-6                  # float32 feature vs float64 simulator value
    assert set(meta["reads"]["sim_parquet_columns"]) == {"idx", "mint", "sd", "h1", "xt_p_END_l055"}
    with pytest.raises(SystemExit, match="expected 419"):
        wm.build_r419(vd)


def test_r419_sd_is_checked_against_the_tape_and_never_opens_a_pnl_column(tmp_path):
    pytest.importorskip("duckdb")
    pytest.importorskip("pyarrow")
    vd, a = make_verify(tmp_path)
    _write_tape(tmp_path / "tape")
    uni = _universe(tmp_path)
    recs, meta = wm.build_r419(vd, tape_dir=tmp_path / "tape", universe_path=uni, expect_n=None)
    chk = meta["checks"]["sd_vs_tape"]
    assert chk["rows"] == len(recs) and chk["equal_to_simulator_sd"] == len(recs)
    for col in ("pnl_p_END_l055", "g_p_END_l055", "t"):
        with pytest.raises(wm.ColumnRefused):
            wm.read_parquet_columns(vd / "v" / "sim.parquet", [col], wm.R419_SIM_COLUMNS)
    for k in ("pnl_E3_p", "xt_E3_p", "ssb_p", "guard_p"):
        with pytest.raises(wm.ColumnRefused):
            wm.read_npz(vd / "v" / "cands.npz", [k], wm.R419_CANDS_KEYS)
    with pytest.raises(wm.ColumnRefused):
        wm.read_npz(vd / "v" / "preds.npz", ["pdisc"], ("pred",))


def test_export_writes_csv_sha256_and_meta(tmp_path):
    pytest.importorskip("pyarrow")
    vd, _a = make_verify(tmp_path)
    out = tmp_path / "ref"
    recs, meta = wm.build_r419(vd, expect_n=None)
    res = wm._write_export(out, wm.R419_CSV, recs, meta)
    assert sorted(p.name for p in out.iterdir()) == ["r419.csv", "r419.csv.sha256", "r419.meta.json"]
    assert (out / "r419.csv.sha256").read_text() == f"{res['sha256']}  r419.csv\n"
