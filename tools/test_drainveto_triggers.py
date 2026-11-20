"""drainveto_triggers tests: fixtures only. No real tape, no grid.pkl, no network."""

from __future__ import annotations

import importlib.util
import inspect
import json
import os
import pickle
import stat
import subprocess
import textwrap
import zlib

import pytest

from tools import drainveto_triggers as B

REAL_L1 = "/data/mal/hunt-1008/push-1016/hunt24/l4-audit/l1_rule.py"

FAKE_L1 = textwrap.dedent('''
    class L1Rule:
        def __init__(self, aux=None, proxy_first_print=False, aux_dir=None):
            self.seen = []

        def triggers(self, hour, state):
            out = []
            canon = {'MINT': 'P'}
            qpost = hour.q - hour.sol / (1 - 0.0125)  # the rule's own sell-side q_post
            for p in range(len(hour.pools)):
                pool = str(hour.pools[p])
                for r in hour.rows_q[p]:
                    out.append((pool, int(hour.sl[r]), None))
            return out
''')


def load_fake(tmp_path):
    f = tmp_path / "fake_l1.py"
    f.write_text(FAKE_L1)
    spec = importlib.util.spec_from_file_location("fake_l1", f)
    m = importlib.util.module_from_spec(spec)
    # exec_module needs the source for inspect.getsource
    import sys
    sys.modules["fake_l1"] = m
    spec.loader.exec_module(m)
    return m


class Hour:
    def __init__(self, pools, mints, rows_q, sl, tx, ev, block="blk0", T=3600):
        import numpy as np
        self.pools, self.mints, self.rows_q, self.sl, self.tx, self.ev, self.block, self.T = pools, mints, rows_q, sl, tx, ev, block, T
        n = len(sl)
        self.q, self.sol, self.b, self.tok, self.v = (np.arange(n) * 1e6 + 1e8), np.full(n, 8e7), np.full(n, 1000.0), np.full(n, 50.0), np.full(n, 17.6e9)


def test_source_equality_fake(tmp_path):
    m = load_fake(tmp_path)
    parent = textwrap.dedent(inspect.getsource(m.L1Rule.triggers)).split("\n")
    new = B.locrule_source(inspect.getsource(m.L1Rule.triggers)).split("\n")
    assert len(new) == len(parent) + 1
    k = next(i for i, ln in enumerate(new) if ln.strip() == B.ADDED_LINE)
    assert new[:k] + new[k + 1:] == parent
    assert new[k - 1].strip().startswith("out.append(")


@pytest.mark.skipif(not os.path.exists(REAL_L1), reason="pinned l1_rule not on this host")
def test_source_equality_real_l1():
    spec = importlib.util.spec_from_file_location("l1_rule_real", REAL_L1)
    import sys
    sys.path.insert(0, os.path.dirname(REAL_L1))
    try:
        m = importlib.util.module_from_spec(spec)
        sys.modules["l1_rule_real"] = m
        spec.loader.exec_module(m)
    finally:
        sys.path.pop(0)
    parent = textwrap.dedent(inspect.getsource(m.L1Rule.triggers)).split("\n")
    new = B.locrule_source(inspect.getsource(m.L1Rule.triggers)).split("\n")
    assert len(new) == len(parent) + 1
    k = next(i for i, ln in enumerate(new) if ln.strip() == B.ADDED_LINE)
    assert new[:k] + new[k + 1:] == parent
    B.make_locrule(m)


def test_parent_without_exactly_one_append_refused():
    with pytest.raises(B.BuilderRefused):
        B.locrule_source("def triggers(self):\n    return []\n")
    with pytest.raises(B.BuilderRefused):
        B.locrule_source("def triggers(self):\n    out = []\n    out.append(1)\n    out.append(2)\n")


def test_locrule_records_first_in_kit_order_and_earlier_nonqualifying(tmp_path):
    m = load_fake(tmp_path)
    Loc = B.make_locrule(m)
    rule = Loc()
    import numpy as np
    # rows 0..3 of pool P: row0 non-qualifying in slot 100 (not in rows_q), rows 1,2 qualify in slot 100 (tx 1 ev 3, then tx 2 ev 0), row3 other slot
    h = Hour(np.array(["P"]), np.array(["MINT"]), {0: [1, 2, 3]}, np.array([100, 100, 100, 101]), np.array([0, 1, 2, 0]), np.array([0, 3, 0, 0]))
    out = rule.triggers(h, None)
    assert [o[1] for o in out] == [100, 100, 101]
    assert rule.loc[0][3:6] == (100, 1, 3)  # first qualifying print in kit order
    assert rule.loc[1][3:6] == (100, 2, 0)
    assert rule.loc[0][6] == "MINT" and rule.loc[0][7] is True
    # decision state of the trigger print itself: Q = the rule's qpost at that row (V excluded), B = b + tok, V = hour.v
    assert rule.loc[0][8] == pytest.approx(h.q[1] - h.sol[1] / (1 - 0.0125)) and rule.loc[0][9] == 1050.0 and rule.loc[0][10] == 17.6e9
    assert rule.loc[1][8] == pytest.approx(h.q[2] - h.sol[2] / (1 - 0.0125))


def mk_rec(pool="P", slot=100, T=3600, tx=1, ev=3, mint="MINT", canon=True, block="blk0", Q=1e8, B=1050.0, V=17.6e9):
    return (block, T, pool, slot, tx, ev, mint, canon, Q, B, V)


def crcp(p):
    return zlib.crc32(p.encode())


def test_join_first_print_and_count():
    recs = [mk_rec(tx=1, ev=3), mk_rec(tx=2, ev=0)]
    out = B.join(recs, [(0, crcp("P"), 100, 3600)], ["blk0"], expect_n=1)
    assert out[0]["tx_index"] == 1 and out[0]["event_index"] == 3 and out[0]["n_qual_in_slot"] == 2
    out = B.join([mk_rec(tx=1, Q=5.0), mk_rec(tx=2, Q=9.0)], [(0, crcp("P"), 100, 3600)], ["blk0"], expect_n=1)
    assert out[0]["Q"] == 5.0  # the first print's state


def test_join_unmatched_refused():
    with pytest.raises(B.BuilderRefused, match="unmatched 1"):
        B.join([mk_rec()], [(0, crcp("OTHER"), 100, 3600)], ["blk0"], expect_n=1)


def test_join_crc_collision_refused(monkeypatch):
    monkeypatch.setattr(B, "crc", lambda p: 7)
    with pytest.raises(B.BuilderRefused, match="ambiguous 1"):
        B.join([mk_rec(pool="A"), mk_rec(pool="B")], [(0, 7, 100, 3600)], ["blk0"], expect_n=1)


def test_join_hour_mismatch_and_count_refused():
    with pytest.raises(B.BuilderRefused, match="hour_mismatch 1"):
        B.join([mk_rec(T=7200)], [(0, crcp("P"), 100, 3600)], ["blk0"], expect_n=1)
    with pytest.raises(B.BuilderRefused, match="grid_count"):
        B.join([mk_rec()], [(0, crcp("P"), 100, 3600)], ["blk0"], expect_n=332)


def test_grid_view_refuses_outcome_keys():
    v = B.GridView({"blk": [0], "pool": [1], "sd": [2], "T": [3], "xs": [9], "why": [1], "Qx": [1]})
    for k in ("xs", "why", "Qx", "dur_s", "tdec", "Qe", "Be", "ref", "ssb"):
        with pytest.raises(B.BuilderRefused):
            v[k]
    assert v["sd"] == [2]


def test_project_grid_reads_only_allowed(tmp_path):
    class Spy(dict):
        def __init__(self, d):
            super().__init__(d)
            self.touched = []

        def __getitem__(self, k):
            self.touched.append(k)
            return super().__getitem__(k)

    cell = Spy({"blk": [0, 1], "pool": [5, 6], "sd": [10, 11], "T": [3600, 7200], "xs": [1, 2], "why": [0, 0]})
    keys = B.project_grid({"cells": {B.CELL: cell, "other": {"xs": 1}}, "counters": {"x": 1}})
    assert keys == [(0, 5, 10, 3600), (1, 6, 11, 7200)] and sorted(cell.touched) == ["T", "blk", "pool", "sd"]
    p = tmp_path / "g.pkl"
    p.write_bytes(pickle.dumps({"cells": {B.CELL: dict(cell), "x": 1}}))
    assert B.read_grid_keys(str(p)) == keys


def test_sha_mismatch_refused(tmp_path):
    f = tmp_path / "a"
    f.write_bytes(b"x")
    with pytest.raises(B.BuilderRefused, match="pin_sha_mismatch"):
        B.check_pins({str(f): "0" * 64})
    B.check_pins({str(f): B.sha256_file(str(f))})
    with pytest.raises(B.BuilderRefused, match="pin_missing"):
        B.check_pins({str(tmp_path / "nope"): "0" * 64})


def zst(path, rows):
    raw = "".join(json.dumps(r) + "\n" for r in rows).encode()
    subprocess.run(["zstd", "-q", "-f", "-o", str(path)], input=raw, check=True)


def test_scan_keys_txev_missing(tmp_path):
    f = tmp_path / "h.jsonl.zst"
    zst(f, [{"pool": "P", "slot": 100, "tx_index": 1, "event_index": 3, "sol_lamports": 5},
            {"pool": "P", "slot": 100, "tx_index": 2},  # event_index missing
            {"pool": "Q", "slot": 100, "tx_index": 1, "event_index": 0},
            {"pool": "P", "slot": 101},  # other slot: ignored
            {"pool": "Z", "slot": 100}])  # other pool: ignored
    r = B.scan_keys(str(f), {"P": {100}, "Q": {100}, "N": {100}})
    assert r[("P", 100)] == [2, False] and r[("Q", 100)] == [1, True] and r[("N", 100)] == [0, False]


def test_finish_rows_problems_order_and_allowlist():
    loc = [{"blk": 0, "block": "b0", "pool": "B", "pool_crc32": 2, "slot": 5, "hourT": 3600, "tx_index": 1, "event_index": 2, "mint": "M", "canon_ok": True, "n_qual_in_slot": 1, "Q": 1.0, "B": 2.0, "V": 3.0},
           {"blk": 0, "block": "b0", "pool": "A", "pool_crc32": 1, "slot": 5, "hourT": 3600, "tx_index": 1, "event_index": 2, "mint": "", "canon_ok": True, "n_qual_in_slot": 2, "Q": 1.0, "B": 2.0, "V": 3.0},
           {"blk": 0, "block": "b0", "pool": "C", "pool_crc32": 3, "slot": 4, "hourT": 3600, "tx_index": 1, "event_index": 2, "mint": "M", "canon_ok": False, "n_qual_in_slot": 1, "Q": 1.0, "B": 2.0, "V": 3.0},
           {"blk": 0, "block": "b0", "pool": "D", "pool_crc32": 4, "slot": 9, "hourT": 3600, "tx_index": 1, "event_index": 2, "mint": "M", "canon_ok": True, "n_qual_in_slot": 1, "Q": 1.0, "B": 2.0, "V": 3.0}]
    kc = {("B", 5): [1, True], ("A", 5): [1, True], ("C", 4): [1, True], ("D", 9): [1, False]}
    rows, st = B.finish_rows(loc, kc)
    assert [r["ordinal"] for r in st] == [1, 2, 3, 4] and all(tuple(r) == B.DSTATE_KEYS for r in st)
    assert all(set(r) .isdisjoint({"Q", "B", "V"}) for r in rows)  # the locator file carries no reserve
    assert [r["pool"] for r in rows] == ["C", "A", "B", "D"] and [r["ordinal"] for r in rows] == [1, 2, 3, 4]  # sorted by (blk, slot, pool), not input order
    assert [r["locator_problem"] for r in rows] == ["mint_not_canon", "mint_missing", "none", "txev_missing"]
    assert [r["locator_ok"] for r in rows] == [False, False, True, False]
    for r in rows:
        assert tuple(r) == B.OUT_KEYS and r["hour"] == "1970-01-01T01"
    c = B.counts_only(rows)
    assert c["by_block"]["b0"]["matched"] == 4 and c["multi_qual_in_slot"] == 1 and c["by_block"]["b0"]["txev_missing"] == 1


def test_output_leak_and_mode(tmp_path):
    rows = [{k: 1 for k in B.OUT_KEYS}]
    st = [{k: 1 for k in B.DSTATE_KEYS}]
    w = B.write_files({"a.jsonl": (rows, B.OUT_KEYS), "b.jsonl": (st, B.DSTATE_KEYS)}, str(tmp_path), root=str(tmp_path))
    for name, (p, sha) in w.items():
        assert stat.S_IMODE(os.stat(p).st_mode) == 0o640 and sha == B.sha256_file(p)
    assert w["a.jsonl"][1] != w["b.jsonl"][1]
    assert set(json.loads(open(w["a.jsonl"][0]).readline())) == set(B.OUT_KEYS) and set(json.loads(open(w["b.jsonl"][0]).readline())) == set(B.DSTATE_KEYS)
    for bad in ("Qe", "Be", "block_time", "trader", "sol", "xs", "tdec", "Qx", "why"):
        assert bad not in B.OUT_KEYS and bad not in B.DSTATE_KEYS
    for bad in ("Q", "B", "V", "split"):
        assert bad not in B.OUT_KEYS
    with pytest.raises(B.BuilderRefused, match="output_key_not_allowed"):
        B.write_files({"a": ([{**rows[0], "tdec": 1}], B.OUT_KEYS)}, str(tmp_path), root=str(tmp_path))
    with pytest.raises(B.BuilderRefused, match="output_key_not_allowed"):
        B.write_files({"a": ([{**st[0], "Qe": 1}], B.DSTATE_KEYS)}, str(tmp_path), root=str(tmp_path))
    with pytest.raises(B.BuilderRefused, match="out_dir_not"):
        B.write_files({"a": (rows, B.OUT_KEYS)}, str(tmp_path / "other"), root=str(tmp_path))
    # a bad second file writes NOTHING (the first is not left behind)
    d2 = tmp_path / "x"
    d2.mkdir()
    with pytest.raises(B.BuilderRefused):
        B.write_files({"a.jsonl": (rows, B.OUT_KEYS), "b.jsonl": ([{"bad": 1}], B.DSTATE_KEYS)}, str(d2), root=str(d2))
    assert os.listdir(d2) == []


def test_split_rule():
    from datetime import datetime, timezone
    t = lambda *a: int(datetime(*a, tzinfo=timezone.utc).timestamp())
    assert B.split_of("explore-0814", t(2026, 8, 21, 11)) == "D" and B.split_of("explore-0814", t(2026, 8, 21, 12)) == "C1"
    for blk in ("fast-pool-2026-09-18T23_2026-09-22T00", "oracle-insample-2026-09-22_25", "oracle-live-2026-09-25_27"):
        assert B.split_of(blk, t(2026, 9, 22, 0)) == "C2"


def test_disclosures_include_d7_and_run_note():
    assert any(d.startswith("D7") for d in B.DISCLOSURES)
    assert "-m" in B.__doc__ and "MemoryMax >= 3G" in B.__doc__


def test_refusal_writes_no_file(tmp_path, monkeypatch):
    monkeypatch.setattr(B, "check_pins", lambda pins: (_ for _ in ()).throw(B.BuilderRefused("pin_sha_mismatch: x")))
    assert B.main(["--out-dir", str(tmp_path)]) == 2
    assert os.listdir(tmp_path) == []


def test_reconcile():
    good = {**B.EXPECT_COUNTERS, "triggers_raw": B.EXPECT_TRIGGERS_X12 // 12}
    B.reconcile(good)
    for k in B.EXPECT_COUNTERS:
        with pytest.raises(B.BuilderRefused, match="reconciliation_failed"):
            B.reconcile({**good, k: good[k] + 1})
    with pytest.raises(B.BuilderRefused):
        B.reconcile({**good, "triggers_raw": good["triggers_raw"] + 1})


def test_roots_are_the_four_blocks_only():
    assert [os.path.basename(p) for _, p in B.BLK] == ["explore-0814", "fast-pool-2026-09-18T23_2026-09-22T00", "oracle-insample-2026-09-22_25", "oracle-live-2026-09-25_27"]
    assert not any(w in p for _, p in B.BLK for w in ("fresh", "forward", "truth", "arena", "sealed"))
    assert B.OUT_ROOT.startswith("/data2/mal/scratch/")
