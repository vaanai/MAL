"""Scan-speed hedge (claude/hv4-scan-perf): every speed-up must be decision-neutral. These tests pin that, with no network, no API call, no key.

  * prescreen_fails never rejects a pool the full filters pass or would refuse as a leak, and the V code it reports equals the full filters' V check;
  * lite_snapshot equals the keys of the real ArenaState.snapshot it stands in for;
  * the fast paths of _num, _norm_prints and assert_as_of give the same values and the same errors as the code they replace;
  * build_card(feats=...) is the same card as build_card();
  * a whole Runner replay with the speed-ups ON writes the same decision logs (timing rows left out), the same counters and the same distinct V pools as the same replay
    with them OFF (prescreen disabled, the scan's features and prints not passed to the card).
"""

from __future__ import annotations

import collections
import math
import random

import pytest

from tools import hv4_cards as C
from tools import hv4_runner as R
from tools.arena_state import WSOL_MINT, ArenaState, Pool
from tools.hv4_scanprof import decision_md5_no_timing
from tools.test_hv4_runner import H, T0, T_END, T_START, WSOL, make, trade_rows

SOL = 1_000_000_000
MIN = 60_000


@pytest.fixture(scope="module", autouse=True)
def _arena_test_env():
    """register_bot's now_ms / verify_sha=False overrides are refused outside tests unless MAL_ARENA_TEST=1 (arena_paper); the same fixture as test_hv4_runner."""
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("MAL_ARENA_TEST", "1")
        yield


# ------------------------------------------------------------------------------------------------------------------------------ references (the code before the change)
def ref_num(x):
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return None
    return float(x) if math.isfinite(x) else None


def ref_norm_prints(prints):
    if prints is None:
        return None
    out = []
    for p in prints:
        try:
            t, sol, buy, who = p[0], p[1], p[2], p[3]
        except (TypeError, IndexError, KeyError) as exc:
            raise ValueError("print is not (t_ms, sol_lamports, is_buy, trader)") from exc
        if isinstance(t, bool) or not isinstance(t, int) or isinstance(sol, bool) or not isinstance(sol, (int, float)) or sol < 0:
            raise ValueError("print has a bad time or amount")
        out.append((t, int(sol), bool(buy), str(who)))
    return out


def outcome(fn, *a, **k):
    try:
        return ("ok", fn(*a, **k))
    except Exception as e:  # noqa: BLE001 - the type and the text are what must match
        return ("err", type(e).__name__, str(e))


# ------------------------------------------------------------------------------------------------------------------------------ small fast paths
class _F(float):
    pass


def test_num_fast_path_matches_the_original_on_every_kind_of_input():
    huge = 10 ** 400
    vals = [0.0, -0.0, 1.5, -2.25, 1e308, -1e308, 5e-324, float("nan"), float("inf"), float("-inf"), 0, 7, -3, 2 ** 70, huge, -huge, True, False, None, "1.5", b"x", [1.0], (1.0,),
            _F(2.5), _F("nan"), complex(1, 2)]
    for v in vals:
        a, b = outcome(C._num, v), outcome(ref_num, v)
        assert a == b or (a[0] == b[0] == "ok" and a[1] != a[1] and b[1] != b[1]), v


def test_norm_prints_fast_path_matches_the_original_values_and_errors():
    good = [(1, 2, True, "a"), (3, 0, False, ""), (5, 10 ** 12, True, "Trader" + "1" * 30)]
    mixed = good + [[7, 8, 1, "x"], (9, 4.0, 0, "y"), (11, 5, True, None), (13, 6, "yes", 99), (15, 3, True, "z", "extra")]
    bad = [(True, 1, True, "a"), (1.5, 1, True, "a"), (1, -1, True, "a"), (1, True, True, "a"), (1, "5", True, "a"), (1, 2, True), 5, None]
    for case in (good, mixed, [], iter(good)):
        cases = list(case) if not isinstance(case, list) else case
        assert outcome(C._norm_prints, cases) == outcome(ref_norm_prints, cases)
    for b in bad:
        assert outcome(C._norm_prints, good + [b]) == outcome(ref_norm_prints, good + [b]), b
    assert C._norm_prints(None) is None
    once = C.norm_prints(good)
    assert C.norm_prints(once) is once and once == ref_norm_prints(good)  # a second call returns it as is
    assert isinstance(once, list) and once == list(once)


def test_assert_as_of_normalised_prints_fast_path_raises_exactly_what_the_loop_raises():
    snap = {"id": "P", "now_ms": 90, "last_ms": 90}
    ok = [(10, 1, True, "a"), (99, 2, False, "b")]
    for prints in (ok, [(10, 1, True, "a"), (100, 2, False, "b"), (150, 1, True, "c")], [(101, 1, True, "a")], []):
        plain = outcome(C.assert_as_of, snap, 100, prints=list(prints))
        fast = outcome(C.assert_as_of, snap, 100, prints=C.norm_prints(prints))
        assert plain == fast, prints
    assert outcome(C.assert_as_of, snap, 100, prints=C.norm_prints(ok))[0] == "ok"
    assert outcome(C.assert_as_of, snap, 100, prints=C.norm_prints([(100, 1, True, "a")]))[0] == "err"


# ------------------------------------------------------------------------------------------------------------------------------ prescreen and lite snapshot
def random_pool(rng: random.Random, i: int, now: int) -> Pool:
    first = now - rng.choice([1, 50, 3_000, 40 * MIN, 3 * 3600_000, 30 * 3600_000, 9 * 86400_000]) * 1
    p = Pool(f"Pool{i:04d}" + "1" * 36, "pumpswap", first, 0)
    p.quote_mint = WSOL_MINT
    p.mint = f"Mint{i:04d}" + "1" * 36
    p.created_ms = rng.choice([None, first - rng.choice([0, 1, 7_200_000, 90_000_000, 700_000_000])])
    p.last_ms = max(first, now - rng.choice([0, 1, 500, 119_999, 120_000, 120_001, 121_000, 5 * MIN, 3600_000]))
    p.q_real = rng.choice([None, 0, 29 * SOL, 30 * SOL - 1, 30 * SOL, 31 * SOL, 80 * SOL, 4000 * SOL])
    p.v = rng.choice([0, -5, 17_499_999_999, 17_500_000_000, 17_584_505_288, 17_700_000_000, 17_700_000_001, 30 * SOL])
    p.base = 1e12
    p.price = 1e-6
    p.ath = rng.choice([None, 2e-6])
    p.ath_ms = rng.choice([None, first + 5])
    p.complete_ms = rng.choice([None, None, first + 9])
    p.origin_create_ms = rng.choice([None, None, first - 3])
    p.last_trade = rng.choice([None, {"t_ms": p.last_ms}, {"t_ms": p.last_ms}, {"t_ms": now + rng.choice([0, 5])}])
    n = rng.choice([0, 3, 40, 400])
    traders = [f"w{k}" for k in range(rng.choice([2, 12, 60]))]
    for _ in range(n):
        p.events.append((now - rng.randrange(0, 65 * MIN), rng.randrange(1, 3 * SOL), rng.random() < 0.6, rng.choice(traders)))
    p.events = collections.deque(sorted(p.events))
    for t, sol, buy, w in p.events:
        p.net[w] = p.net.get(w, 0) + (sol if buy else -sol)
    return p


def build_state(tmp_path, rng, n: int, now: int) -> ArenaState:
    st = ArenaState(tmp_path, start_ms=0)
    st.now_ms = now
    for i in range(n):
        p = random_pool(rng, i, now)
        st._pools[p.id] = p
    return st


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_prescreen_is_sound_and_lite_snapshot_matches_the_real_snapshot(tmp_path, seed):
    rng = random.Random(seed)
    now = 1_790_000_000_000
    st = build_state(tmp_path, rng, 700, now)
    rejected = leak_or_undecided = 0
    for T in (now + 1, now + 1000, now + 2000, now + 9_999, now + 10_001, now + 60_000):  # the state clock is T - 1 s in a scan; the others test the freshness edge
        for p in st.pools_wsol(lambda p: p.prog == "pumpswap"):
            lite = R.lite_snapshot(p, st.now_ms)
            full = st.snapshot(p.id, candles=False)
            for k in ("now_ms", "last_ms", "ath_ms", "created_ms", "complete_ms", "origin_create_ms", "created_known", "age_s", "age_since_first_seen_s"):
                assert lite[k] == full[k], (k, lite[k], full[k])
            assert lite["last_trade_ms"] == (full["last_trade"]["t_ms"] if full["last_trade"] else None)
            for k in ("quote_sol", "quote_eff_sol", "v_sol"):
                assert lite["reserves"][k] == full["reserves"][k]
            res = outcome(C.filters, full, as_of_ms=T)
            code = C.v_filter_code(lite)
            if C.prescreen_fails(lite, as_of_ms=T):
                rejected += 1
                assert res[0] == "ok" and not res[1].passed, (T, p.id)  # certain to fail, certain not to raise
            else:
                leak_or_undecided += 1
            if res[0] == "ok":  # the V check the scan records for a rejected pool is the one the full filters record
                assert ("v_missing" in res[1].failed) == (code == "v_missing") and ("v_band" in res[1].failed) == (code == "v_band")
                assert ("v_missing" in res[1].checks or "v_band" in res[1].checks)
    assert rejected > 1000 and leak_or_undecided > 100  # both branches were exercised


def test_prescreen_leaves_a_stamp_at_or_after_T_to_the_full_path(tmp_path):
    now = 1_790_000_000_000
    st = build_state(tmp_path, random.Random(9), 1, now)
    p = next(iter(st._pools.values()))
    p.last_ms = now - 500_000  # idle for a long time: the lite checks alone would reject it
    p.last_trade = None
    assert C.prescreen_fails(R.lite_snapshot(p, now), as_of_ms=now + 1)
    p.complete_ms = now + 1  # a stamp at T: the full path must raise (LeakageError), so the prescreen must not decide
    assert not C.prescreen_fails(R.lite_snapshot(p, now), as_of_ms=now + 1)
    with pytest.raises(C.LeakageError):
        C.filters(st.snapshot(p.id, candles=False), as_of_ms=now + 1)


# ------------------------------------------------------------------------------------------------------------------------------ cards and whole replays
def test_scan_candles_added_to_the_filter_snapshot_equal_a_second_full_snapshot(tmp_path):
    st = build_state(tmp_path, random.Random(4), 40, 1_790_000_000_000)
    for p in st.pools_wsol():
        snap = st.snapshot(p.id, candles=False)
        snap["candles"] = st.candles(p)
        assert snap == st.snapshot(p.id)


def dead_pool_rows() -> list[dict]:
    """Three more WSOL PumpSwap pools that printed once, an hour before the first scan and then went quiet: idle at every scan, so the prescreen rejects them. One has no
    virtual_quote_reserves (v_missing), one has V above the band (v_band), one has V in the band."""
    rows = []
    for k, (name, v) in enumerate((("DeadMiss", None), ("DeadBand", 20_000_000_000), ("DeadOk", 17_584_505_288))):
        pool, mint, t = name + "1" * (44 - len(name)), "Mint" + name + "1" * (40 - len(name)), T0 - H + k * 1000
        rows.append({"v": 1, "type": "pool_create", "prog": "pumpswap", "slot": 399_000_000, "t_recv_ms": T0 - 3 * H, "signature": f"dpc{k}", "event_index": 0, "pool": pool,
                     "mint": mint, "quote_mint": WSOL})
        r = {"v": 1, "type": "trade", "prog": "pumpswap", "venue": "pumpswap", "slot": 399_500_000, "t_recv_ms": t, "signature": f"dt{k}", "event_index": 0, "pool": pool, "mint": mint,
             "trader": f"D{k}", "side": "buy", "sol_lamports": SOL, "token_raw": 10 ** 9, "quote_reserve": 60 * SOL, "base_reserve": 10 ** 13, "quote_mint": WSOL, "event_ts": t // 1000}
        if v is not None:
            r["virtual_quote_reserves"] = v
        rows.append(r)
    return rows


def test_runner_replay_with_the_speed_ups_equals_the_replay_without_them(tmp_path, monkeypatch):
    fast, slow = tmp_path / "fast", tmp_path / "slow"
    fast.mkdir()
    slow.mkdir()
    rows = sorted(trade_rows() + dead_pool_rows(), key=lambda r: (r["t_recv_ms"], r["signature"]))
    a = make(fast, rows=rows)
    seen = {"rejected": 0, "cards_with_feats": 0}
    orig_pre, orig_card = C.prescreen_fails, R.Runner._card

    def spy_pre(lite, *, as_of_ms):
        r = orig_pre(lite, as_of_ms=as_of_ms)
        seen["rejected"] += int(r)
        return r

    def spy_card(self, pool_id, T, tag, feats=None, snap=None, prints=None):
        seen["cards_with_feats"] += int(feats is not None and prints is not None)
        return orig_card(self, pool_id, T, tag, feats=feats, snap=snap, prints=prints)

    monkeypatch.setattr(C, "prescreen_fails", spy_pre)
    monkeypatch.setattr(R.Runner, "_card", spy_card)
    a.run(T_START, T_END)
    assert seen["rejected"] > 0 and seen["cards_with_feats"] > 0  # the synthetic tape reaches both speed-ups
    monkeypatch.setattr(C, "prescreen_fails", lambda lite, *, as_of_ms: False)  # OFF: every pool takes the full snapshot and filters
    monkeypatch.setattr(R.Runner, "_card", lambda self, pool_id, T, tag, feats=None, snap=None, prints=None: orig_card(self, pool_id, T, tag, snap=snap))  # OFF: recompute inside
    b = make(slow, rows=rows)
    b.run(T_START, T_END)
    ma, mb = decision_md5_no_timing(a.cfg.root), decision_md5_no_timing(b.cfg.root)
    assert ma["rows"] > 100 and ma["md5"] == mb["md5"] and ma["per_log"] == mb["per_log"]
    assert a.counters.get("filter_v_missing", 0) > 0 and a.counters.get("filter_v_band", 0) > 0  # the dead pools reach the V counters through the prescreen
    assert a.counters == b.counters and a._v_checked == b._v_checked and a._v_missing_pools == b._v_missing_pools
    assert not a.hook_errors and not b.hook_errors


def test_decision_md5_no_timing_ignores_timing_rows_and_chain_fields(tmp_path):
    a = make(tmp_path)
    a.run(T_START, T_START + 12 * MIN)
    m0 = decision_md5_no_timing(a.cfg.root)
    assert m0["rows"] > 0
    a.log.append("SCAN", T_START + 1, {"kind": "scan_timing", "scan_wall_ms": 12345})  # a wall-clock row, and the chain moves on: the fingerprint does not change
    a.log.append("SCAN", T_START + 2, {"kind": "tick_timing", "minute_ms": T_START, "n_ticks": 1})
    assert decision_md5_no_timing(a.cfg.root)["md5"] == m0["md5"]
    a.log.append("SCAN", T_START + 3, {"kind": "scan", "n_filter_pass": 0})  # a real row does change it
    assert decision_md5_no_timing(a.cfg.root)["md5"] != m0["md5"]
