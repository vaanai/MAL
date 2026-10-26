"""Scan speed (prescreen + once-normalised prints + candle series from the Pool) is decision-neutral: byte-identical decision logs, snapshots and features, and it is
faster at a scale with many candidates. Synthetic tapes only; no real tape, no API, no RPC."""

from __future__ import annotations

import time

import pytest

from tools import hv4_cards as C
from tools import hv6_memscale as MS
from tools import hv6_runner as R
from tools.arena_state import CANDLE_MIN, LAMPORTS
from tools.test_hv4_runner import T0
from tools.test_hv6_runner import _env, make  # noqa: F401  (autouse: path allowlist for tmp dirs)

MIN = 60_000


@pytest.fixture(scope="module")
def scaled_tape(tmp_path_factory):
    d = tmp_path_factory.mktemp("r6sp")
    MS.write_tape(d / "tape", MS.T0, 1, 3.0, 300_000, seed=11, pre_hours=4, mean_life_h=6.0, sigma=2.5)
    return d / "tape"


def test_decisions_are_byte_identical_on_the_hv4_style_tape(tmp_path_factory):
    out = {}
    for fast in (True, False):
        run = make(tmp_path_factory.mktemp("r6spa"), cfg_kw={"fast_scan": fast})
        run.run(T0 + 9 * MIN + 2000, T0 + 110 * MIN)
        out[fast] = (run.log.md5(), {a: list(b.entries_ms) for a, b in run.books.items()}, {a: dict(b.realized) for a, b in run.books.items()})
    assert out[True] == out[False] and any(out[True][1][a] for a in ("L", "F", "X"))               # the whole log, SCAN counts included


def test_decisions_are_byte_identical_on_a_scaled_tape_with_many_prescreen_failures(tmp_path_factory, scaled_tape):
    res = {}
    for fast in (True, False):
        run = MS.build_runner(tmp_path_factory.mktemp("r6spb"), scaled_tape, bound=True)
        run.cfg.fast_scan = fast
        run.run(MS.T0 + 2000, MS.T0 + 12 * MIN)
        res[fast] = run
    on, off = res[True], res[False]
    assert on.log.md5() == off.log.md5() and not on.hook_errors
    scans = [r for r in on.log.rows("SCAN") if r["kind"] == "scan"]
    assert scans and max(r["counts"]["pools"] for r in scans) > 5 * max(r["counts"]["candidates"] for r in scans), "most pools must fail the prescreen"


def test_lite_snapshot_and_candle_series_equal_the_real_snapshot(tmp_path_factory, scaled_tape):
    run = MS.build_runner(tmp_path_factory.mktemp("r6spc"), scaled_tape, bound=True)
    run.run(MS.T0 + 2000, MS.T0 + 5 * MIN)
    st, n = run.state, 0
    for p in st.pools_wsol(lambda p: p.prog == "pumpswap"):
        lite, snap = R.lite_snapshot(p, st.now_ms), st.snapshot(p.id, candles=False)
        full = st.snapshot(p.id)
        for k in ("now_ms", "last_ms", "ath_ms", "created_ms", "complete_ms", "origin_create_ms", "created_known", "age_s", "age_since_first_seen_s"):
            assert lite[k] == snap[k], k
        assert lite["last_trade_ms"] == (snap["last_trade"]["t_ms"] if snap.get("last_trade") else None)
        for k in ("quote_sol", "v_sol", "quote_eff_sol"):
            assert lite["reserves"][k] == snap["reserves"][k], k
        assert full == {**snap, "candles": st.candles(p)}                                           # snapshot(pid) == candle-less snapshot + candles
        assert C.series_from_pool_candles(p.candles, st.now_ms // MIN, CANDLE_MIN, LAMPORTS) == C._series(full)
        n += 1
    assert n > 100


def test_features_of_every_candidate_are_identical_on_both_paths(tmp_path_factory, scaled_tape):
    feats = {}
    for fast in (True, False):
        run = MS.build_runner(tmp_path_factory.mktemp("r6spd"), scaled_tape, bound=True)
        run.cfg.fast_scan = fast
        got = []
        orig = R.H.scan

        def spy(*a, **k):
            r = orig(*a, **k)
            got.append((dict(r.feats), list(r.candidates), [t.pool_id for t in r.tags], list(r.board)))
            return r

        R.H.scan = spy
        try:
            run.run(MS.T0 + 2000, MS.T0 + 8 * MIN)
        finally:
            R.H.scan = orig
        feats[fast] = got
    assert feats[True] == feats[False] and len(feats[True][-1][1]) > 50


def test_incremental_normalised_prints_equal_a_full_normalisation_at_every_call(tmp_path_factory, scaled_tape):
    run = MS.build_runner(tmp_path_factory.mktemp("r6spf"), scaled_tape, bound=True)
    orig, n, rebuilt = run._norm_events, [0], [0]

    def checked(p):
        got = orig(p)
        want = C.norm_prints(list(p.events))
        assert list(got) == list(want) and type(got) is type(want)
        assert all(a is b for a, b in zip(got, p.events))                    # the same tuple objects, in the same order
        n[0] += 1
        return got

    run._norm_events = checked
    run.run(MS.T0 + 2000, MS.T0 + 15 * MIN)
    assert n[0] > 500 and len(run._norm) < 400                              # many calls, and only the pools read in the last scan keep a cache entry


def test_scan_is_faster_at_a_scale_with_many_candidates_and_prints_p50_p95(tmp_path_factory, scaled_tape, capsys):
    stats = {}
    for fast in (False, True):
        run = MS.build_runner(tmp_path_factory.mktemp("r6spe"), scaled_tape, bound=True)
        run.cfg.fast_scan = fast
        walls, orig = [], run.scan

        def scan(T, orig=orig, walls=walls):
            t = time.perf_counter()
            orig(T)
            walls.append((time.perf_counter() - t) * 1000.0)

        run.scan = scan
        run.run(MS.T0 + 2000, MS.T0 + 12 * MIN)
        w = sorted(walls[1:])  # the first scan reads the warm-up
        cands = [r["counts"]["candidates"] for r in run.log.rows("SCAN") if r["kind"] == "scan"]
        stats[fast] = {"p50": round(w[len(w) // 2], 1), "p95": round(w[max(0, int(0.95 * len(w)) - 1)], 1), "candidates_p50": sorted(cands)[len(cands) // 2]}
    with capsys.disabled():
        print("\nscan_ms (synthetic, 300k prints/h): before", stats[False], "after", stats[True])
    assert stats[True]["p50"] < stats[False]["p50"] * 0.6 and stats[True]["p95"] < stats[False]["p95"]
