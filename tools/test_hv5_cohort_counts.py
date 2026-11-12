"""Tests for tools/hv5_cohort_counts.py (HV5 counts-only cohort builder). Fixtures only: no tape, no network, no model, no API. Needs numpy (pool_setups); the
DuckDB driver `run` is exercised on the host, not here. The key test is the as-of mutation: everything at or after the decision is perturbed and no count may move."""
from __future__ import annotations

import ast
import copy
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")

from tools import hv5_cohort_counts as cc

MIN = 60_000
SRC = Path(cc.__file__)


# ----------------------------------------------------------------------------------------------------------------------------------- H5F
V = 17_584_505_288.0


def _pool(extra=None, boost_buys=4):
    """First prints: a BOOST-like wallet B buys 0.5 SOL x boost_buys, then trader X sells and drains Q to <= 40 SOL (post-trade)."""
    rows = []  # (slot, bt, isbuy, sol, q, th)
    t = 1000
    q = 10e9  # real quote 10 SOL; Q = q + V = 27.6 SOL
    for k in range(boost_buys):
        rows.append((100 + k, t + k, True, 0.5e9, q, 7))
    for k in range(3):  # organic noise buys
        rows.append((110 + k, t + 8 + k, True, 0.05e9, q, 100 + k))
    rows.append((120, t + 20, False, 0.3e9, q, 200))  # first sell: Q post = 10 + 17.58 - 0.3 = 27.3 SOL <= 40
    for k in range(8):
        rows.append((130 + k, t + 30 + k, False, 0.1e9, q, 300 + k))
    if extra:
        rows += extra
    return rows


def _cols(rows):
    return ([r[0] for r in rows], [r[1] for r in rows], [r[2] for r in rows], [r[3] for r in rows], [r[4] for r in rows], [r[5] for r in rows])


def test_h5f_trigger_fires_on_first_qualifying_sell_with_boost_detected():
    tr = cc.h5f_trigger(*_cols(_pool()), V)
    assert tr is not None and tr["i"] == 7 and tr["t_sec"] == 20 and tr["boost_detected"] is True
    assert tr["boost_spent_sol"] == pytest.approx(2.0)
    assert tr["qpost_sol"] == pytest.approx((10e9 + V - 0.3e9) / 1e9)


def test_h5f_trigger_without_boost_wallet_still_fires_but_flag_false():
    tr = cc.h5f_trigger(*_cols(_pool(boost_buys=2)), V)
    assert tr is not None and tr["boost_detected"] is False and tr["boost_spent_sol"] == 0.0


def test_h5f_trigger_none_when_pool_not_drained_or_too_late():
    rows = _pool()
    rich = [(r[0], r[1], r[2], r[3], 40e9, r[5]) for r in rows]  # Q = 57 SOL: never <= 40
    assert cc.h5f_trigger(*_cols(rich), V) is None
    late = [(r[0], r[1] + (400 if not r[2] else 0), r[2], r[3], r[4], r[5]) for r in rows]  # every sell after 300 s
    assert cc.h5f_trigger(*_cols(late), V) is None


def test_h5f_trigger_is_as_of_strict_mutation():
    """Cut or scramble every print AFTER the trigger: the trigger, its time and its flags do not move. Also scramble the trigger print's own post-state reading of later rows."""
    rows = _pool()
    base = cc.h5f_trigger(*_cols(rows), V)
    i = base["i"]
    cut = rows[: i + 1]
    assert cc.h5f_trigger(*_cols(cut), V) == base
    mut = copy.deepcopy(rows)
    for k in range(i + 1, len(mut)):
        s = mut[k]
        mut[k] = (s[0], s[1], not s[2], s[3] * 50, s[4] * 9, 7)
    assert cc.h5f_trigger(*_cols(mut), V) == base


def test_h5f_boost_budget_spent_blocks_trigger():
    big = [(100 + k, 1000 + k, True, 1.9e9, 10e9, 7) for k in range(9)]  # BOOST-like wallet, 17.1 SOL spent: below 99.9% of 17.585
    sell = (120, 1030, False, 0.3e9, 10e9, 200)
    ok = cc.h5f_trigger(*_cols(big + [sell]), V)
    assert ok is not None and ok["boost_detected"] is True and ok["boost_spent_sol"] == pytest.approx(17.1)
    done = big + [(110, 1020, True, 0.5e9, 10e9, 7), sell]  # 17.6 SOL spent: budget done, no trigger
    assert cc.h5f_trigger(*_cols(done), V) is None


# ---------------------------------------------------------------------------------------------------------------------------------- flags
def test_creator_index_prior_count_is_strictly_before_and_excludes_self():
    ci = cc.CreatorIndex([("m1", "c", 100), ("m2", "c", 200), ("m3", "c", 300), ("m4", "c", 400), ("x", "d", 50)])
    assert ci.prior_count("c", "m4", 400) == 3
    assert ci.prior_count("c", "m4", 300) == 2  # m3 at 300 is not strictly before 300
    assert ci.prior_count("c", "zz", 1000) == 4
    assert ci.prior_count(None, "m", 1000) == 0 and ci.prior_count("nobody", "m", 1000) == 0


def test_feature_flags_as_of():
    ci = cc.CreatorIndex([(f"m{k}", "c", k * MIN) for k in range(5)])
    t = 100 * MIN
    f = cc.feature_flags(decision_ms=t, create_ms=t - 13 * MIN, has_create=True, creator="c", mint="mx", creators=ci, buyer_minutes=12, boost_flag=True)
    assert f == {"holders": True, "creator": True, "cohort": True, "boost": True, "all4": True}
    f = cc.feature_flags(decision_ms=t, create_ms=t - 11 * MIN, has_create=True, creator="c", mint="mx", creators=ci, buyer_minutes=9, boost_flag=False)
    assert f["holders"] is False and f["cohort"] is False and f["boost"] is False and f["all4"] is False
    f = cc.feature_flags(decision_ms=t, create_ms=None, has_create=False, creator=None, mint="mx", creators=ci, buyer_minutes=None, boost_flag=False)
    assert not any(f.values())
    # creator history younger than CREATOR_LAG_MS does not count
    ci2 = cc.CreatorIndex([(f"n{k}", "c", t - 5 * MIN) for k in range(5)])
    assert cc.feature_flags(decision_ms=t, create_ms=0, has_create=True, creator="c", mint="mx", creators=ci2, buyer_minutes=20, boost_flag=True)["creator"] is False


# ---------------------------------------------------------------------------------------------------------------------------- CON / BIG setups
M0 = 6_000_000  # a minute index divisible by 5
T0 = M0 * MIN


def _pullback_bars(n=200):
    minute, close, high, low, npr, nsg, gross, net, qs = [], [], [], [], [], [], [], [], []
    for k in range(n):
        if k < 150:
            c = 1.0
        elif k <= 170:
            c = 1.0 + 0.3 * (k - 150) / 20
        elif k <= 190:
            c = 1.3 - 0.1 * (k - 170) / 15 if k <= 185 else 1.2
        else:
            c = 1.2
        minute.append(T0 + k * MIN); close.append(c); high.append(c * 1.01); low.append(c * 0.99)
        npr.append(3); nsg.append(2); gross.append(1.0); net.append(0.1); qs.append(35.0)
    return dict(minute=minute, close=close, high=high, low=low, n_prints=npr, n_signers=nsg, gross=gross, net=net, quote_sol=qs)


def _scan_T(j_plus_1):
    return (M0 + j_plus_1) * MIN + 2000  # scan at :02 of minute M = M0 + j + 1


def _fires(b, t, hi=None):
    return cc.pool_setups(b["minute"], b["close"], b["high"], b["low"], b["n_prints"], b["n_signers"], b["gross"], b["net"], b["quote_sol"], V, T0,
                          scan_lo_ms=t, scan_hi_ms=(t + 1) if hi is None else hi)


def test_pool_setups_fires_pullback_through_hv4_cards():
    b = _pullback_bars()
    out = _fires(b, _scan_T(185))
    assert [x["type"] for x in out] == ["PULLBACK"] and out[0]["persona"] == "CON" and out[0]["T"] == _scan_T(185)


def test_pool_setups_is_as_of_mutation():
    """Perturb every bar from minute M (the scan minute) on: the decision at T cannot change."""
    b = _pullback_bars(200)
    t = _scan_T(185)
    base = _fires(b, t)
    assert base
    k = 185  # dense index of minute M; bars k.. are at or after T
    m = copy.deepcopy(b)
    for key in ("close", "high", "low"):
        for i in range(k, 200):
            m[key][i] = 9.9
    for key in ("n_prints", "n_signers", "gross", "net", "quote_sol"):
        for i in range(k, 200):
            m[key][i] = 1e6 if key != "quote_sol" else 1.0
    assert _fires(m, t) == base
    cut = {key: v[:k] for key, v in b.items()}
    assert _fires(cut, t) == base


def test_pool_setups_filters_and_cooldown():
    b = _pullback_bars()
    t = _scan_T(185)
    assert cc.pool_setups(b["minute"], b["close"], b["high"], b["low"], b["n_prints"], b["n_signers"], b["gross"], b["net"], b["quote_sol"], 0.0, T0, scan_lo_ms=t, scan_hi_ms=t + 1) == []  # V band
    low_q = dict(b, quote_sol=[10.0] * 200)
    assert _fires(low_q, t) == []  # quote floor
    young = cc.pool_setups(b["minute"], b["close"], b["high"], b["low"], b["n_prints"], b["n_signers"], b["gross"], b["net"], b["quote_sol"], V, T0 + 120 * MIN, scan_lo_ms=t, scan_hi_ms=t + 1)
    assert young == []  # age 65 min < 2 h
    run = _fires(b, _scan_T(180), hi=_scan_T(200))
    ts = [x["T"] for x in run if x["type"] == "PULLBACK"]
    assert all(b2 - a >= 30 * MIN for a, b2 in zip(ts, ts[1:]))  # 30 min cooldown


def test_pool_setups_idle_pool_does_not_fire():
    b = _pullback_bars()
    cut = {key: v[:180] for key, v in b.items()}  # last bar is minute 179; scanning minute 185 -> last print too old
    assert _fires(cut, _scan_T(185)) == []


# -------------------------------------------------------------------------------------------------------------------------------- summarize
def test_worst_window_and_bar():
    days = [f"2026-08-{d:02d}" for d in range(14, 29)]
    assert cc.worst_window({d: 15 for d in days}, days) == 105
    assert cc.worst_window({d: 15 for d in days[:5]}, days[:5]) is None
    assert cc.worst_window({d: 15 for d in days[:-1]}, days) == 90  # a missing day counts as zero
    ents = []
    for i, d in enumerate(days):
        ms = 1_786_708_800_000 + i * 86_400_000
        for _ in range(21):
            ents.append({"decision_ms": ms, "pool": f"p{i}", "flags": {"holders": True, "creator": False, "cohort": True, "boost": True, "all4": False}})
    s = cc.summarize(ents, days, 15.0, cc.FLAG_KEYS)
    assert s["attempts"] == 315 and s["attempts_per_day"] == pytest.approx(21.0) and s["worst_7day"] == 147 and s["bar_met"] is True
    assert s["share"]["holders"] == 1.0 and s["share"]["creator"] == 0.0 and s["all4_attempts"] == 0
    s2 = cc.summarize(ents[:100], days, 15.0, cc.FLAG_KEYS)
    assert s2["bar_met"] is False


def test_render_md_has_bar_and_no_outcome_words():
    days = [f"2026-08-{d:02d}" for d in range(14, 29)]
    res = {"H5F": cc.summarize([], days, 14.0, cc.FLAG_KEYS)}
    md = cc.render_md(res, {"written": "x", "declare_sha": "d", "tool_sha": "t", "cards_sha": "c", "notes": ["n"]})
    assert "NOT MET" in md and "H5F" in md and "no outcome" in md


# ---------------------------------------------------------------------------------------------------------------------------------- guards
def test_guard_path_refuses_sealed_and_outside():
    assert cc.guard_path("/data/mal/hunt-shared/bars_1m/explore-0814")
    for bad in ("/data/mal/blocks/forward-1002/x", "/data/mal/clean-view/fresh-0828", "/var/lib/mal/sealed/x", "/data/mal/arena/tape", "/data/mal/kalshi/x", "/etc/passwd",
                "/data/mal/hunt-shared/probe-out"):
        with pytest.raises(cc.GuardError):
            cc.guard_path(bad)


def test_source_calls_no_network_or_api_and_reads_no_outcome_columns():
    tree = ast.parse(SRC.read_text())
    imported = {n.names[0].name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import)} | {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    assert not (imported & {"requests", "urllib", "http", "socket", "anthropic", "httpx", "aiohttp", "subprocess"})
    text = SRC.read_text()
    for banned in ("ps_last_ms", "grad_ms", "complete_ms", "ps_buy_sol", "ps_sell_sol", "bc_last_ms", "pnl_est"):
        assert banned not in text, banned
