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
    meta = {k: 0 for k in ("only_ours", "only_g", "mismatch_mid", "mismatch_pool", "mismatch_mslot", "mismatch_mbt", "mismatch_s0", "mismatch_v",
                           "mismatch_ch", "mismatch_cbt")}
    meta["pool_ne_pda_mints"] = []
    feat = {"days": [{"day": "2026-08-14", "I_equal": True, "X_equal": True, "dec_rows_diff": 0, "marks_rows_diff": 0}]}
    return meta, feat


def test_verdict_pass_and_each_failure():
    meta, feat = _rep_ok()
    ok, probs = E.verdict(meta, feat)
    assert ok and not probs
    meta["pool_ne_pda_mints"] = ["m1"]
    assert not E.verdict(meta, feat)[0]
    meta, feat = _rep_ok()
    feat["days"][0]["X_equal"] = False
    assert not E.verdict(meta, feat)[0]
    meta, feat = _rep_ok()
    feat["days"][0]["dec_rows_diff"] = 3
    ok, probs = E.verdict(meta, feat)
    assert not ok and any("dec_rows_diff" in p for p in probs)
    meta, feat = _rep_ok()
    meta["mismatch_v"] = 1
    assert not E.verdict(meta, feat)[0]


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
    assert E.main(["--out", str(out)]) == 0
