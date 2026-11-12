"""Fix C (smoke #1806): tools/hv6_prefetch.PacedPrefetcher. A fake chain and a fake clock only; no RPC, no arena path.

  1. pacing: under a demand far above the budget, no rolling hour reaches the plan stop and no minute after the start is a blackout; the unpaced HV4 class at
     HV6's old 1,000/h shows the blackout (the smoke's failure) on the same demand;
  2. a cold store fills in the first minute (the bucket's burst);
  3. a restart never starts with more than the rolling hour has left;
  4. strict priority inside a pass; a skipped refresh books nothing and rewrites no spend file;
  5. admit(): only new pools, first served, nothing else of the store moves; the worker is woken.
"""

from __future__ import annotations

import types
from collections import Counter

import pytest

from tools import hv4_chain as HC
from tools import hv4_prefetch as PF
from tools import hv6_prefetch as P6
from tools import hv6_runner as R

T = 1_793_000_000_000 - 1_793_000_000_000 % 3_600_000
MIN = 60_000


class Clock:
    def __init__(self, ms: int) -> None:
        self.ms = ms

    def __call__(self) -> int:
        return self.ms


class Chain:
    """Answers at once, stamped at the clock; counts every call per minute (the per-minute record the smoke's spend file showed)."""

    def __init__(self, clock: Clock) -> None:
        self.clock, self.calls, self.per_min = clock, 0, Counter()
        self.rpc = types.SimpleNamespace(provider="helius")
        owner = self

        class Ctr:
            calls = property(lambda self: owner.calls)

            def calls_today(self):
                return owner.calls

        self.counter = Ctr()
        self.kinds: list[tuple[str, str]] = []

    def _call(self, kind: str, mint: str) -> None:
        self.calls += 1
        self.per_min[self.clock.ms // MIN] += 1
        self.kinds.append((kind, mint))

    def mint_facts(self, mint, force=False):
        self._call("mint", mint)
        return HC.MintFacts(mint=mint, ok=True, error=None, token_program_id=HC.SPL_TOKEN_PROGRAM, token_program="spl-token", mint_authority=None,
                            freeze_authority=None, as_of_slot=1, as_of_ms=self.clock.ms)

    def mayhem_flag(self, mint, *, tape_is_mayhem=None, pool=None, verify=False):
        self._call("mayhem", mint)
        return HC.MayhemFlag(mint=mint, is_mayhem=False, source="pool_account", as_of_slot=1, as_of_ms=self.clock.ms)

    def largest_holders(self, mint, *, token_program=None, pool=None, max_age_s=900, **kw):
        self._call("holders", mint)
        return HC.Holders(mint=mint, ok=True, error=None, top1_frac=0.05, top10_frac=0.3, as_of_slot=1, as_of_ms=self.clock.ms)


def compose(mf, mh, hd):
    return {"as_of_ms": mf.as_of_ms}


def drive(pf, clock, chain, minutes: int, n_pools: int = 150, n_hot: int = 50) -> int:
    """About 1,650 calls/h of demand (150 pools: mint every 10 min, hot and warm holders every 12 min): every minute the scan reads the store at :02 for every
    watched pool, as cards would, then watches them; the worker passes every 5 s. Returns the pool-minutes from minute 2 on whose card would be refused on
    chain facts (no fact stamped before the scan: chain_unavailable; or a mint fact older than 15 min: chain_stale)."""
    items = [(f"pool{i:03d}", f"mint{i:03d}") for i in range(n_pools)]
    hot = {p for p, _ in items[:n_hot]}
    refused = 0
    for m in range(minutes):
        scan = clock.ms + 2_000
        if m >= 2:
            for pool, mint in items:
                part = pf.part_at(pool, mint, scan, 10)
                refused += part is None or scan - part["as_of_ms"] > 15 * MIN
        pf.watch(items, hot, clock.ms, vols={p: float(n_pools - i) for i, (p, _) in enumerate(items)})
        for _ in range(12):
            pf.run_once(clock.ms)
            clock.ms += 5_000
    return refused


def windows(per_min: Counter, start_min: int, minutes: int) -> list[int]:
    return [sum(per_min.get(m, 0) for m in range(s, s + 60)) for s in range(start_min, start_min + minutes - 59)]


# ------------------------------------------------------------------------------------------------------------------------ 1. pacing
def test_paced_never_reaches_the_plan_stop_and_never_lets_a_fact_go_stale():
    clock = Clock(T)
    chain = Chain(clock)
    pf = P6.PacedPrefetcher(chain, compose, clock=clock, policy="v2", hourly_budget=R.RPC_CALLS_PER_H)
    refused = drive(pf, clock, chain, 180)
    m0 = T // MIN
    hard = P6.hard_cap(2000)
    assert hard == 1980 and pf.pace_per_min == (1980 - 360) / 60 == 27.0
    assert max(windows(chain.per_min, m0 - 59, 240)) <= hard                    # no rolling hour reaches the base class's plan stop
    assert refused == 0                                                         # no chain_stale / chain_unavailable card in three hours
    late = [chain.per_min.get(m, 0) for m in range(m0 + 60, m0 + 180)]
    assert sum(late) / len(late) <= 27.0 + 0.5                                  # steady state at or under the refill rate
    assert pf.stats_.get("paced_deferred", 0) > 0 and pf.stats_.get("budget_deferred", 0) == 0   # the bucket, not the hour cap, did the limiting


def test_unpaced_hv4_class_at_the_old_budget_lets_facts_go_stale_on_the_same_demand():
    """The smoke-#1806 pattern, reproduced: free-running spend until the rolling hour is full, then whole minutes with no call and stale mint facts."""
    clock = Clock(T)
    chain = Chain(clock)
    pf = PF.ChainPrefetcher(chain, compose, clock=clock, policy="v2", hourly_budget=1_000)
    refused = drive(pf, clock, chain, 180)
    m0 = T // MIN
    assert refused > 0
    assert max(windows(chain.per_min, m0 - 59, 240)) > 950                      # the hour fills to the cap
    run, longest = 0, 0
    for m in range(m0 + 1, m0 + 180):
        run = run + 1 if chain.per_min.get(m, 0) == 0 else 0
        longest = max(longest, run)
    assert longest >= 5                                                         # and then stops for minutes


# ------------------------------------------------------------------------------------------------------------------------ 2. cold start
def test_a_cold_store_fills_in_the_first_minute():
    clock = Clock(T)
    chain = Chain(clock)
    pf = P6.PacedPrefetcher(chain, compose, clock=clock, policy="v2", hourly_budget=2_000)
    drive(pf, clock, chain, 1, n_pools=120, n_hot=0)
    got = {m for k, m in chain.kinds if k == "mint"}
    assert len(got) == 120                                                      # every pool has its first mint read within the first minute
    assert chain.per_min[T // MIN] <= P6.PACE_BURST + 27


# ------------------------------------------------------------------------------------------------------------------------ 3. restart
def test_a_restart_starts_with_no_more_than_the_hour_has_left(tmp_path):
    spend = tmp_path / "spend.json"
    clock = Clock(T)
    chain = Chain(clock)
    first = P6.PacedPrefetcher(chain, compose, clock=clock, policy="v2", hourly_budget=2_000, spend_path=spend)
    first._book(clock.ms, 1_800)                                                # a busy hour on record
    clock.ms += 10 * MIN
    again = P6.PacedPrefetcher(Chain(clock), compose, clock=clock, policy="v2", hourly_budget=2_000, spend_path=spend)
    assert again.tokens(clock.ms) == pytest.approx(1980 - 1800)                 # min(burst 360, 1,980 - 1,800)
    fresh = P6.PacedPrefetcher(Chain(clock), compose, clock=clock, policy="v2", hourly_budget=2_000)
    assert fresh.tokens(clock.ms) == 360
    with pytest.raises(ValueError):
        P6.PacedPrefetcher(Chain(clock), compose, clock=clock, policy="v1", hourly_budget=2_000)
    with pytest.raises(ValueError):
        P6.PacedPrefetcher(Chain(clock), compose, clock=clock, policy="v2", hourly_budget=2_000, pace_burst=1_980)


# ------------------------------------------------------------------------------------------------------------------------ 4. priority, bookings
def test_strict_priority_in_a_pass_and_skips_book_nothing(tmp_path):
    spend = tmp_path / "spend.json"
    clock = Clock(T)
    chain = Chain(clock)
    pf = P6.PacedPrefetcher(chain, compose, clock=clock, policy="v2", hourly_budget=2_000, spend_path=spend, pace_burst=5)
    items = [(f"pool{i}", f"mint{i}") for i in range(10)]
    pf.watch(items, set(), clock.ms)
    pf.run_once(clock.ms)
    assert chain.calls == 4                                                     # two new pools (mint + Mayhem = 2 each); the third waits, and so does every later one
    assert [m for k, m in chain.kinds if k == "mint"] == ["mint0", "mint1"]     # the base class's order (new pools, oldest first), never skipped over
    assert pf.stats_["paced_deferred"] == 10                                  # the 8 later new pools, and the 2 served pools' warm holders reads
    before = spend.read_text()
    mtime = spend.stat().st_mtime_ns
    pf.run_once(clock.ms)                                                       # same instant: nothing refills, nothing is called or booked
    assert chain.calls == 4 and spend.read_text() == before and spend.stat().st_mtime_ns == mtime


# ------------------------------------------------------------------------------------------------------------------------ 5. admit
def test_admit_adds_only_new_pools_first_served_and_moves_nothing_else():
    clock = Clock(T)
    chain = Chain(clock)
    pf = P6.PacedPrefetcher(chain, compose, clock=clock, policy="v2", hourly_budget=2_000)
    pf.watch([("old", "mintold")], {"old"}, clock.ms)
    pf.run_once(clock.ms)
    hot_until, last_watch, seen = pf.pools["old"].hot_until_ms, pf._last_watch_ms, pf.pools["old"].last_seen_ms
    clock.ms += 30_000
    assert not pf._wake.is_set()
    n = pf.admit([("old", "mintold"), ("new", "mintnew"), ("nomint", None)], clock.ms)
    assert n == 1 and set(pf.pools) == {"old", "new"} and pf._wake.is_set()
    assert (pf.pools["old"].hot_until_ms, pf._last_watch_ms, pf.pools["old"].last_seen_ms) == (hot_until, last_watch, seen)
    assert pf.pools["new"].first_ms == pf.pools["new"].last_seen_ms == clock.ms and pf.stats_["pools_admitted"] == 1
    k0 = len(chain.kinds)
    pf.run_once(clock.ms)
    assert chain.kinds[k0] == ("mint", "mintnew")                              # a new pool's mint read is served first
    part = pf.part_at("new", "mintnew", clock.ms + 1, 10)
    assert part is not None and part["as_of_ms"] < clock.ms + 1                 # a scan right after has a fact stamped before it


def test_worker_thread_wakes_on_admit():
    import threading
    import time

    calls = threading.Event()

    class WallChain(Chain):
        def mint_facts(self, mint, force=False):
            calls.set()
            return super().mint_facts(mint, force)

    clock = Clock(T)
    pf = P6.PacedPrefetcher(WallChain(clock), compose, clock=clock, policy="v2", hourly_budget=2_000)
    pf.start()
    try:
        time.sleep(0.2)                                                         # the first (empty) pass has run; the next is CYCLE_S (5 s) away
        t = time.perf_counter()
        pf.admit([("p", "m")], clock.ms)
        assert calls.wait(2.0) and time.perf_counter() - t < 2.0               # served long before the 5 s cycle
    finally:
        pf.stop()
    assert "tokens" in pf.stats() and pf.stats()["pace_burst"] == 360


def _longest_zero_run(per_min: Counter, m0: int, minutes: int) -> int:
    run = longest = 0
    for m in range(m0 + 1, m0 + minutes):
        run = run + 1 if per_min.get(m, 0) == 0 else 0
        longest = max(longest, run)
    return longest


def test_under_overload_pacing_spreads_the_shortfall_instead_of_a_blackout():
    """Demand well above 2,000/h (300 pools): pacing cannot make calls it does not have, but it never stops for minutes, and far fewer card instants see a
    stale or missing fact than with the same budget spent free-running (measured here: 2,372 against 14,560 pool-minutes; asserted with margin)."""
    out = {}
    for name, cls in (("paced", P6.PacedPrefetcher), ("free", PF.ChainPrefetcher)):
        clock = Clock(T)
        chain = Chain(clock)
        pf = cls(chain, compose, clock=clock, policy="v2", hourly_budget=2_000)
        out[name] = (drive(pf, clock, chain, 180, n_pools=300, n_hot=60), _longest_zero_run(chain.per_min, T // MIN, 180))
    assert out["paced"][1] == 0 and out["free"][1] >= 10
    assert out["paced"][0] * 3 < out["free"][0]
