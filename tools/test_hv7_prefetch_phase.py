"""HV7 prefetch phase lock: the REAL ChainPrefetcher (policy v2, 500/h) as live_chain_source builds it (Hv7ChainPrefetcher), a fake chain with an emulation of the live
chain's 61-minute `_hour_calls` counter, a fake clock, Hv7Runner's own watch_sets once a minute, hourly 30% churn of the top pools. For every start minute, 72 h, K = 100, 300
and 1,000: at least 90% of the screens after the first must have 8 fresh mint cards, all with a holders block. SYNTHETIC: no RPC, no tape, no network. This measures the
prefetch cadence on a model market (not real pools); nothing here says HV7 makes money."""
from __future__ import annotations

import math
import random
from concurrent.futures import ProcessPoolExecutor
from types import SimpleNamespace

import pytest

from tools import hv4_cards as C4
from tools import hv7_runner as R

MIN = 60_000
HOUR = 3_600_000
STEP = 10_000
HOURS = 72
CHURN = 0.3


def test_window_ends_at_t_d_and_is_14_minutes_long():
    assert not R.prefetch_window_open(47 * MIN + 59_999) and R.prefetch_window_open(48 * MIN) and R.prefetch_window_open(59 * MIN)
    assert R.prefetch_window_open(HOUR) and R.prefetch_window_open(HOUR + 2 * MIN - 1) and not R.prefetch_window_open(HOUR + 2 * MIN)
    assert (R.PREFETCH_WINDOW_END_MS - R.PREFETCH_WINDOW_START_MS) % HOUR == 14 * MIN
    assert R.PREFETCH_WINDOW_END_MS == R.T_D_OFFSET_MS
    assert R.PREFETCH_WINDOW_END_MS - R.PREFETCH_WINDOW_START_MS + HOUR < C4.CHAIN_MINT_MAX_AGE_MS


class _Counter:
    def __init__(self):
        self.minutes, self.prior_minutes = {}, []

    def calls_between(self, a, b):
        return sum(v for t, v in self.minutes.items() if a <= t < b)


class _Chain:
    def __init__(self, clock):
        self.clock, self.counter, self.calls = clock, _Counter(), 0
        self.rpc = SimpleNamespace(provider="helius")

    def _c(self, n):
        self.calls += n
        b = self.clock() - self.clock() % MIN
        self.counter.minutes[b] = self.counter.minutes.get(b, 0) + n
        for old in [m for m in self.counter.minutes if m < b - 70 * MIN]:      # the live counter keeps about an hour of minutes
            del self.counter.minutes[old]

    def mint_facts(self, mint, force=False):
        self._c(1)
        return SimpleNamespace(ok=True, as_of_ms=self.clock(), as_of_slot=1, token_program_id="tp")

    def mayhem_flag(self, mint, pool=None):
        self._c(1)
        return SimpleNamespace(is_mayhem=False, as_of_ms=self.clock())

    def largest_holders(self, mint, token_program=None, pool=None, max_age_s=0):
        self._c(2)
        return SimpleNamespace(ok=True, as_of_ms=self.clock(), as_of_slot=1)


def _zipf(r):
    return 1000.0 / r


class _Market:
    """K candidates; every hour at t_d a fraction CHURN of the top M=min(200, K) fade out and as many entrants (half new pool ids, half revivals) take random top ranks;
    each move is a linear ramp from a uniform minute of the hour lasting 1 to 30 minutes."""

    def __init__(self, k, rng):
        self.rng, self.n_new, self.ramps = rng, 0, {}
        self.vol = {f"p{i:06d}": _zipf(i + 1) * math.exp(rng.gauss(0, 0.1)) for i in range(k)}

    def plan(self, t):
        rng = self.rng
        m = min(200, len(self.vol))
        cur = self.at(t)
        self.vol, self.ramps = {q: cur.get(q, 0.0) for q in self.vol}, {}
        order = sorted(self.vol, key=lambda q: -self.vol[q])
        top, tail = order[:m], order[m:]
        n = int(round(CHURN * m))
        fade = rng.sample(top, n)
        for q in top:
            v = self.vol[q]
            self._ramp(q, t, v, v * rng.uniform(0.002, 0.02) if q in fade else v * math.exp(rng.gauss(0, 0.3)))
        for j in range(n):
            tgt = _zipf(rng.randint(1, m)) * math.exp(rng.gauss(0, 0.2))
            if j % 2 == 0 or not tail:
                self.n_new += 1
                q = f"n{self.n_new:06d}"
                if tail:
                    self.vol.pop(tail.pop(rng.randrange(len(tail))), None)
                self.vol[q] = None
                self._ramp(q, t, 0.0, tgt)
            else:
                q = tail.pop(rng.randrange(len(tail)))
                self._ramp(q, t, self.vol[q], tgt)

    def _ramp(self, q, t, v0, v1):
        t0 = t + self.rng.randint(0, 59) * MIN + self.rng.randint(0, 59) * 1000
        self.ramps[q] = (t0, t0 + self.rng.randint(1, 30) * MIN, v0, v1)

    def at(self, t):
        out = {}
        for q, v in self.vol.items():
            r = self.ramps.get(q)
            if r is not None:
                t0, t1, v0, v1 = r
                if t < t0:
                    if v is None:
                        continue
                else:
                    f = min(1.0, (t - t0) / (t1 - t0))
                    v = v0 + f * (v1 - v0)
                    if f >= 1.0:
                        self.vol[q] = v
            if v is None:
                continue
            out[q] = v
        return out


def simulate(k: int, start_minute: int, hours: int = HOURS, gated: bool = True) -> list[tuple[int, int]]:
    """(fresh mint cards, cards with holders) at every hourly screen after the first, for a process started at H:start_minute:30."""
    rng = random.Random(1000 * k + start_minute)
    base = 100 * HOUR
    now = [base + start_minute * MIN + 30_000]
    clock = lambda: now[0]  # noqa: E731
    chain = _Chain(clock)
    cls = R._gated_prefetcher_class() if gated else __import__("tools.hv4_prefetch", fromlist=["ChainPrefetcher"]).ChainPrefetcher
    pf = cls(chain, lambda mf, mh, hd: {"mint_ms": mf.as_of_ms, "holders": hd is not None}, clock=clock, holders=True, hourly_budget=R.HV7_RPC_CALLS_PER_H, policy="v2")
    mkt = _Market(k, rng)
    mkt.plan(now[0])
    last_hot, out, first = [], [], True
    end = now[0] + hours * HOUR
    while now[0] < end:
        t = now[0]
        if t % MIN == 0:
            vol = mkt.at(t)
            items, hot, vols = R.watch_sets(vol, last_hot)
            pf.watch([(q, "m" + q) for q in items], hot, t, vols=vols)
            if t % HOUR == R.T_D_OFFSET_MS:
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
                if not first:
                    out.append((fresh, hold))
                first = False
                last_hot = list(dict.fromkeys([*order[:8], *tried]))[:R.WATCH_HOT_MAX]
                mkt.plan(t)
        pf.run_once(t)
        now[0] += STEP
    return out


def _cell(args):
    k, s = args
    out = simulate(k, s)
    return k, s, out


@pytest.mark.parametrize("k", [100, 300, 1000])
def test_screens_are_full_with_holders_at_every_start_minute(k):
    with ProcessPoolExecutor(max_workers=24) as ex:
        res = list(ex.map(_cell, [(k, s) for s in range(60)]))
    assert len(res) == 60
    for _k, s, out in res:
        assert len(out) >= HOURS - 2
        good = sum(1 for fresh, hold in out if fresh == 8 and hold == 8)
        assert good >= 0.9 * len(out), (k, s, good, len(out), out)


def test_the_free_running_prefetcher_fails_the_same_test():
    """The sanity check that the grid is not vacuous: without the window (HV4's own thread loop, as before this fix) some start minute drops below 90%."""
    worst = min(sum(1 for f, h in simulate(300, s, hours=12, gated=False) if f == 8 and h == 8) / 11 for s in (1, 20))
    assert worst < 0.9
