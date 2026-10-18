"""arena_stack is a byte-for-byte vendored copy of the needed logic of tools/r4b_book.py and tools/r4b_frozen.py (DEC-031 section 3.1).

THIS FILE IS A TEST, NOT AN ARENA PROCESS: it may import tools.r4b_* to prove the copy is identical. The arena modules themselves must not.
"""

from __future__ import annotations

import hashlib
import inspect
import json
from pathlib import Path

import numpy as np

from tools import arena_stack as A
from tools import r4b_book as B
from tools import r4b_frozen as FZ

ROOT = Path(__file__).resolve().parent.parent
FIX = Path(__file__).resolve().parent / "fixtures" / "arena_paper"


def blob_sha(path: str) -> str:
    data = (ROOT / path).read_bytes()
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def test_the_header_blob_shas_are_those_of_the_sources_the_copy_was_cut_from():
    assert A.VENDORED_FROM == {"tools/r4b_book.py": blob_sha("tools/r4b_book.py"), "tools/r4b_frozen.py": blob_sha("tools/r4b_frozen.py")}
    head = Path(A.__file__).read_text().split('"""')[1]
    for path, sha in A.VENDORED_FROM.items():
        assert path in head and sha in head  # named in the header comment


def test_every_vendored_function_has_identical_source_text():
    for name in A.VENDORED_FUNCTIONS["tools/r4b_book.py"]:
        assert inspect.getsource(getattr(A, name)) == inspect.getsource(getattr(B, name)), name
    for name in A.VENDORED_FUNCTIONS["tools/r4b_frozen.py"]:
        assert inspect.getsource(getattr(A, name)) == inspect.getsource(getattr(FZ, name)), name


def test_every_vendored_constant_is_equal():
    assert len(A.VENDORED_CONSTANTS) >= 20
    for name in A.VENDORED_CONSTANTS:
        assert getattr(A, name) == getattr(FZ, name), name
    assert np.array_equal(A._TH, B._TH) and np.array_equal(A._TP, B._TP)


def _cases():
    return [json.loads(x) for x in (FIX / "parity_cases.jsonl").read_text().splitlines()]


def test_outputs_are_bit_identical_on_the_parity_set():
    cases = [c for c in _cases() if c["prog"] == "pumpswap"]
    assert len(cases) >= 200
    for tg, lat in (("13", 1.3), ("19", 1.9)):
        stake = np.array([c["stake_sol"] * 1e9 for c in cases])
        qe, be = np.array([c["Qe" + tg] for c in cases]), np.array([c["Be" + tg] for c in cases])
        spot, qx, bx = np.array([c["spot"] for c in cases]), np.array([c["Qx"] for c in cases]), np.array([c["Bx"] for c in cases])
        ssb, near = np.array([c["ssb" + tg] for c in cases]), np.array([c["near" + tg] for c in cases])
        for kw in ({}, {"auto_stranded": False, "k_tol": None}, {"rent": 0.0}, {"h_in": 0.0, "h_out": 0.0}):
            extra = dict(return_stranded=True, return_invalid=True, return_exit_invalid=True, return_exit_cause=True)
            a = A.round_trip(stake, qe, be, spot, qx, bx, **kw, **extra)
            b = B.round_trip(stake, qe, be, spot, qx, bx, **kw, **extra)
            assert len(a) == len(b) and all(np.array_equal(x, y) for x, y in zip(a, b))
        fa, fb = A.fail_legs(a[0], a[1], ssb, near), B.fail_legs(b[0], b[1], ssb, near)
        assert all(np.array_equal(x, y) for x, y in zip(fa, fb))
        assert A.press_intercept(ssb, near, a[1])[0] == B.press_intercept(ssb, near, b[1])[0]
        assert np.array_equal(A.fee_ppm(qe, be), B.fee_ppm(qe, be))
    x = np.random.default_rng(3).normal(size=250)
    assert A.boot_lo(x) == B.boot_lo(x)


def test_mint_states_and_the_slot_helpers_are_bit_identical():
    rng = np.random.default_rng(11)
    n = 400
    sl = np.sort(rng.integers(1000, 1600, n))
    ib = rng.random(n) < 0.6
    so, tk = rng.uniform(1e7, 2e9, n), rng.uniform(1e9, 1e11, n)
    q, b = 60e9 + np.cumsum(rng.normal(0, 1e8, n)), 1e15 - np.cumsum(rng.normal(0, 1e11, n))
    args = (sl, ib, so, tk, q, b, 17_584_505_288.0, np.array([1100, 1200, 1300]), 0.4)
    kw = dict(t_marks=np.array([0, 0, 0]), slot_s_marks=np.array([0.4] * 3), clock_slot_marks=np.array([1400, 1450, 1500]), rounding="ceil")
    ma, mb = A.mint_states(*args, **kw), B.mint_states(*args, **kw)
    assert ma.keys() == mb.keys()
    for k in ma:
        if isinstance(ma[k], dict):
            assert all(np.array_equal(ma[k][l], mb[k][l]) for l in ma[k])
        else:
            assert np.array_equal(ma[k], mb[k]), k
    for lat in (1.3, 1.9):
        for sps in (0.2, 0.3159, 0.4, 0.55):
            for rounding in ("round", "ceil"):
                assert A.k_slots(lat, sps, rounding) == FZ.k_slots(lat, sps, rounding)
    for sps in (0.2, 0.3, 0.4):
        assert A.exit_lag_slots(sps) == FZ.exit_lag_slots(sps)
