"""HV7 smoke, hourly design, SYNTHETIC (no RPC, no tape, no network, no API): the smoke's own period_plan (31 hourly H:02 screens, first one after warm-up plus one full
prefetch window) driven against the REAL gated prefetcher as live_chain_source builds it (500 calls/h, policy v2), with the chain, market and screen model of
tools/test_hv7_prefetch_phase.py. For every start minute and K = 100/300/1000 it counts, as the smoke's refusal rule does, cards attempted (tried until 8 fresh) and refused
(no usable mint fact, or older than the card's 15 min limit), the screens with 8 fresh cards, and the chain calls. A model market: it says nothing about real pools or edge."""
from __future__ import annotations

import random
from concurrent.futures import ProcessPoolExecutor

import pytest

from tools import hv4_cards as C4
from tools import hv7_runner as R
from tools import hv7_smoke as S
from tools import test_hv7_prefetch_phase as T

MIN, HOUR, STEP = T.MIN, T.HOUR, T.STEP


def simulate_smoke(k: int, start_minute: int, periods: int = S.PERIODS) -> dict:
    rng = random.Random(1000 * k + start_minute)
    base = 100 * HOUR
    now = [base + start_minute * MIN + 30_000]
    t_start = now[0]
    clock = lambda: now[0]  # noqa: E731
    chain = T._Chain(clock)
    day_calls: dict[int, int] = {}
    orig_c = chain._c

    def counted(n):
        day_calls[clock() // (24 * HOUR)] = day_calls.get(clock() // (24 * HOUR), 0) + n
        orig_c(n)

    chain._c = counted
    pf = R._gated_prefetcher_class()(chain, lambda mf, mh, hd: {"mint_ms": mf.as_of_ms, "holders": hd is not None}, clock=clock, holders=True,
                                     hourly_budget=R.HV7_RPC_CALLS_PER_H, policy="v2")
    mkt = T._Market(k, rng)
    mkt.plan(now[0])
    grid, _last_te, _end = S.period_plan(t_start, S.WARMUP_MS, S.PERIOD_MS, periods, R.TICK_MS, R.T_E_OFFSET_MS - R.T_D_OFFSET_MS)
    tds = set(grid)
    end = grid[-1] + MIN
    last_hot, tried_n, ref, noh, full = [], 0, 0, 0, 0
    while now[0] < end:
        t = now[0]
        if t % MIN == 0:
            vol = mkt.at(t)
            items, hot, vols = R.watch_sets(vol, last_hot)
            pf.watch([(q, "m" + q) for q in items], hot, t, vols=vols)
            if t in tds:
                order = sorted((q for q in vol if R.R._vol_ok(vol[q])), key=lambda q: (-vol[q], q))
                tried, fresh, hold = [], 0, 0
                for q in order:
                    if fresh >= 8:
                        break
                    tried.append(q)
                    part = pf.part_at(q, "m" + q, t, 10)
                    if part is not None and t - part["mint_ms"] <= C4.CHAIN_MINT_MAX_AGE_MS:
                        fresh += 1
                        hold += part["holders"]
                tried_n += len(tried)
                ref += len(tried) - fresh
                noh += fresh - hold
                full += fresh == 8
                last_hot = list(dict.fromkeys([*order[:8], *tried]))[:R.WATCH_HOT_MAX]
            if t % HOUR == R.T_D_OFFSET_MS:
                mkt.plan(t)
        pf.run_once(t)
        now[0] += STEP
    mins = chain.counter.minutes
    roll = max((sum(v for m, v in mins.items() if a <= m < a + HOUR) for a in mins), default=0)
    return {"k": k, "start": start_minute, "tried": tried_n, "refused": ref, "no_holders": noh, "full": full, "calls": chain.calls, "max_roll_h": roll, "max_day": max(day_calls.values()),
            "span_h": (end - t_start) / HOUR}


def _cell(a):
    return simulate_smoke(*a)


@pytest.mark.parametrize("k", [100, 300, 1000])
def test_hourly_smoke_clears_the_refusal_rule_at_every_start_minute(k):
    with ProcessPoolExecutor(max_workers=4) as ex:
        res = list(ex.map(_cell, [(k, s) for s in range(60)]))
    for r in res:
        assert r["refused"] / r["tried"] <= 0.05 / 2, r          # margin: at most half of the pinned 5%
        assert r["full"] >= 29 and r["max_roll_h"] <= R.HV7_RPC_CALLS_PER_H and r["max_day"] <= R.HV7_RPC_CALLS_PER_DAY, r


if __name__ == "__main__":
    import sys

    with ProcessPoolExecutor(max_workers=int(sys.argv[1]) if len(sys.argv) > 1 else 2) as ex:
        res = list(ex.map(_cell, [(k, s) for k in (100, 300, 1000) for s in range(60)]))
    for k in (100, 300, 1000):
        rs = [r for r in res if r["k"] == k]
        print(f"K={k:5d} worst_full={min(r['full'] for r in rs)}/31 (start {min(rs, key=lambda r: r['full'])['start']}) worst_refusal={max(r['refused']/r['tried'] for r in rs):.4f} "
              f"mean_refusal={sum(r['refused'] for r in rs)/sum(r['tried'] for r in rs):.4f} no_holders_max={max(r['no_holders'] for r in rs)} "
              f"calls total min/max={min(r['calls'] for r in rs)}/{max(r['calls'] for r in rs)} max_roll_h={max(r['max_roll_h'] for r in rs)} max_day={max(r['max_day'] for r in rs)} "
              f"span_h={min(r['span_h'] for r in rs):.1f}-{max(r['span_h'] for r in rs):.1f}")
