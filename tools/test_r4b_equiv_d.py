"""Tests for scripts/research/r4b_equiv_d.py (E-d). The unit tests are synthetic; the data test skips without the exploration tapes, duckdb or solders.

  PYTHONPATH=/data/mal/audit-1008/venv/lib/python3.12/site-packages /data/mal/venv/bin/python -s -m pytest -q tools/test_r4b_equiv_d.py
Full E-d (about 80 min): R4B_EQUIV_D_FULL=1 runs the whole replay and requires "E-d PASS".
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import numpy as np
import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
_spec = importlib.util.spec_from_file_location("r4b_equiv_d", REPO / "scripts" / "research" / "r4b_equiv_d.py")
E = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(E)


def test_md5_is_over_c_contiguous_bytes():
    a = np.arange(12, dtype=np.float32).reshape(3, 4)
    assert E.md5(a) == E.md5(np.asfortranarray(a))
    assert E.md5(a) != E.md5(a + 1)


def test_guard_refuses_sealed_paths(tmp_path):
    for bad in ("/data/mal/blocks/forward-1016/x", "/data/mal/exp012-forward/x", "/data/mal/blocks/truth-1001", "/data/mal/blocks/fresh-0802/a"):
        with pytest.raises(SystemExit):
            E.guard(bad)
    assert E.guard(tmp_path) == Path(os.path.realpath(tmp_path))


def test_first_diff_nan_equal_and_row():
    a = np.array([[1.0, np.nan], [2.0, 3.0], [4.0, 5.0]])
    b = a.copy()
    assert E._first_diff(a, b) is None
    b[1, 1] = 3.5
    assert E._first_diff(a, b) == 1


def _rep_ok():
    meta = {k: 0 for k in E.META_KEYS}
    meta["pool_ne_pda_mints"] = []
    nt = {"rows_kept": 10, "rows_excluded": 2}
    for k in ("I", "X", "dec", "marks"):
        nt[f"{k}_md5_port"] = nt[f"{k}_md5_l3"] = "aa"
    feat = {"days": [{"day": "2026-08-14", "I_equal": True, "X_equal": False, "dec_rows_diff": 3, "marks_rows_diff": 1, "nontie": nt,
                      "differing_mints_not_in_tie_set": 0, "marks_keys_equal": True}]}
    return meta, feat


def test_raw_fails_while_scoped_passes():
    meta, feat = _rep_ok()
    assert not E.raw_status(meta, feat)[0]
    ok, probs = E.scoped_status(meta, feat, True)
    assert ok and not probs


def test_scoped_fails_each_condition():
    for mutate in (
        lambda m, f: m.update(pool_ne_pda_mints=["m1"]),
        lambda m, f: m.update(mismatch_v=1),
        lambda m, f: f["days"][0].update(I_equal=False),
        lambda m, f: f["days"][0]["nontie"].update(X_md5_port="bb"),
        lambda m, f: f["days"][0]["nontie"].update(marks_md5_l3="bb"),
        lambda m, f: f["days"][0]["nontie"].update(rows_kept=0),
        lambda m, f: f["days"][0].update(differing_mints_not_in_tie_set=2),
        lambda m, f: f["days"][0].update(marks_keys_equal=False),
    ):
        meta, feat = _rep_ok()
        mutate(meta, feat)
        assert not E.scoped_status(meta, feat, True)[0]
    meta, feat = _rep_ok()
    assert not E.scoped_status(meta, feat, False)[0]
    assert not E.scoped_status(meta, feat, None)[0]


def test_tie_info_uses_content_not_mismatch():
    n = 6
    U = {"mid": np.array([0, 0, 0, 1, 1, 2]), "slot": np.array([5, 5, 6, 5, 5, 5]), "txi": np.array([-1, -1, 3, 2, 2, 1]), "evi": np.array([0, 0, 0, 0, 0, 0]),
         "bt": np.full(n, 1786708985 + 100), "isbuy": np.array([1, 0, 1, 1, 1, 1]), "sol": np.array([1, 1, 1, 7, 7, 1]), "tok": np.ones(n), "q": np.ones(n),
         "b": np.ones(n), "th": np.arange(n)}
    U["th"][4] = U["th"][3]  # mint 1: identical duplicate rows -> not a content tie
    t = E.tie_info(U, 1786708985 - 5000)
    assert t["mints"] == [0] and t["groups"] == 1 and t["txi_values"] == [-1]


def test_memory_cap_refused_without_scope(monkeypatch):
    monkeypatch.setattr(E, "memory_cap_problem", lambda: "no MemoryMax found")
    assert E.main(["--out", "/nonexistent/never-created"]) == 4


def test_memory_cap_seen_inside_a_scope():
    """Inside `systemd-run --scope -p MemoryMax=...` the cap is found; outside any limit it is None-or-reason, never an exception."""
    why = E.memory_cap_problem()
    assert why is None or isinstance(why, str)


def test_link_merged_picks_the_window_and_refuses_a_gap(tmp_path, monkeypatch):
    clean = tmp_path / "clean"
    for w, h in (("w7", "2026-08-14T12"), ("w6", "2026-08-16T12")):
        for sub in ("trades", "creates", "migrations"):
            d = clean / w / sub
            d.mkdir(parents=True)
            (d / f"{sub}-{h}.jsonl.zst").write_bytes(b"x")
    monkeypatch.setattr(E, "CLEAN", clean)
    dst = tmp_path / "merged"
    E.link_merged(dst, ["2026-08-14T12", "2026-08-16T12"])
    assert (dst / "trades" / "trades-2026-08-16T12.jsonl.zst").is_file()
    assert not (dst / "trades" / "trades-2026-08-16T12.jsonl.zst").is_symlink()  # zstd refuses symlinks
    with pytest.raises(SystemExit):
        E.link_merged(dst, ["2026-08-14T13"])


def _have_data_deps():
    pytest.importorskip("duckdb")
    pytest.importorskip("solders")
    pytest.importorskip("pandas")
    if not E.data_available():
        pytest.skip("exploration tape / l3 files not on this host")


def test_adapter_two_hours_match_l3_meta(tmp_path):
    """Two exploration hours through the adapter (static_map): every member equals l3's meta row (mint, pool = PDA, mslot, mbt, s0, v)."""
    _have_data_deps()
    import pandas as pd

    h0, h1 = "2026-08-14T12", "2026-08-14T14"
    E.run_adapter(tmp_path, E.hours_of(h0, h1), h0, h1, memory_limit="4GB", threads=2)
    ours = pd.read_parquet(tmp_path / "adapter" / "meta.parquet")
    assert len(ours) > 0
    g = pd.read_parquet(E.L3 / "meta.parquet")
    m = ours.merge(g, on="mint", suffixes=("", "_g"))
    assert len(m) == len(ours)
    for c in ("mid", "pool", "mslot", "mbt", "s0", "v"):
        assert (m[c] == m[c + "_g"]).all(), c
    st = json.loads((tmp_path / "adapter" / "stats.json").read_text())
    assert st["v_source"] == "static_map"


@pytest.mark.skipif(os.environ.get("R4B_EQUIV_D_FULL") != "1", reason="full E-d replay (~80 min) only with R4B_EQUIV_D_FULL=1")
def test_full_replay_passes(tmp_path):
    _have_data_deps()
    out = Path(os.environ.get("R4B_EQUIV_D_OUT", str(tmp_path)))
    assert E.main(["--out", str(out)]) == 0  # scoped PASS
    rep = json.loads((out / "report.json").read_text())
    assert rep["scoped"]["status"] == "PASS" and rep["injection_equal"] is True
    assert rep["raw"]["diff_counts"] and all(v is not None for v in rep["raw"]["diff_counts"].values())  # raw diff counts are reported (raw FAIL expected)
    assert all(d["ties"]["mints_sha256"] and d["nontie"]["rows_excluded"] >= 0 for d in rep["features"]["days"])
