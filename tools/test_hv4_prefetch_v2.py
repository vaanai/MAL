"""HV4 v2 prefetch policy (A) and observability rows (B): budget proof on a synthetic hot set, ordering, and the obs rows.

Paper only. Offline: a fake chain with the real CreditCounter (per-minute counts), a replay clock, no RPC.
"""
from __future__ import annotations

import json
import types

from tools import hv4_chain as H
from tools import hv4_cards as hc
from tools import hv4_obs as O
from tools import hv4_prefetch as P
from tools import hv4_runner as R

MIN = 60_000
HOUR = 60 * MIN
T0 = 1_790_000_000_000 - (1_790_000_000_000 % HOUR)  # an hour boundary
SLOT = 400_000_000


class Clock:
    def __init__(self, ms: int) -> None:
        self.ms = ms

    def __call__(self) -> int:
        return self.ms


class CountingChain:
    """Every method counts its calls on the REAL CreditCounter (per-minute buckets), like HV4Chain. holders cost 2 on a mint's first read (supply + accounts), else 1."""

    def __init__(self, clock: Clock, *, holders_extra: int = 0) -> None:
        self.clock = clock
        self.counter = H.CreditCounter("helius", now_ms=clock)
        self.rpc = types.SimpleNamespace(provider="helius")
        self._mh: set[str] = set()
        self._supply: set[str] = set()
        self.holders_extra = holders_extra

    def credit_report(self):
        return self.counter.snapshot()

    def mint_facts(self, mint, force=False):
        self.counter.record_call("getAccountInfo")
        return H.MintFacts(mint=mint, ok=True, error=None, token_program_id=H.SPL_TOKEN_PROGRAM, token_program="spl-token", mint_authority=None,
                           freeze_authority=None, as_of_slot=SLOT, as_of_ms=self.clock.ms)

    def mayhem_flag(self, mint, *, tape_is_mayhem=None, pool=None, verify=False):
        if pool not in self._mh:
            self.counter.record_call("getAccountInfo")
            self._mh.add(pool)
        return H.MayhemFlag(mint=mint, is_mayhem=False, source="pool_account", as_of_slot=SLOT, as_of_ms=self.clock.ms)

    def largest_holders(self, mint, *, token_program=None, pool=None, max_age_s=900, **kw):
        n = 1 + self.holders_extra + (0 if mint in self._supply else 1)
        self._supply.add(mint)
        for _ in range(n):
            self.counter.record_call("getTokenLargestAccounts")
        return H.Holders(mint=mint, ok=True, error=None, top1_frac=0.1, top10_frac=0.3, as_of_slot=SLOT, as_of_ms=self.clock.ms) if hasattr(H, "Holders") else None


def _holders_ok() -> bool:
    try:
        H.Holders(mint="m", ok=True, error=None, top1_frac=0.1, top10_frac=0.3, as_of_slot=SLOT, as_of_ms=1)
        return True
    except TypeError:
        return False


def pools(n: int, tag: str = "P"):
    return [(f"{tag}{i:043d}", f"m{tag}{i:042d}") for i in range(n)]


def simulate(policy: str, *, n_pools: int, n_hot: int, hours: int, budget: int = P.HOURLY_BUDGET, extra: int = 0):
    """A synthetic hot set: n_pools filter-pass pools, the first n_hot of them hot. Scans every 5 min (watch, then a card read at T for hot pools AND for 3 pools that
    were cold until this scan and are tagged now). Returns (prefetcher, counter, stale_card_reads, card_reads, per-hour counter calls)."""
    clock = Clock(T0)
    chain = CountingChain(clock, holders_extra=extra)
    pf = P.ChainPrefetcher(chain, R.HV4ChainSource.compose, clock=clock, hourly_budget=budget, policy=policy)
    allp = pools(n_pools)
    hot_ids = [p for p, _ in allp[:n_hot]]
    stale = reads = 0
    for step in range(hours * 12):
        T = T0 + step * 5 * MIN + 2000
        clock.ms = T
        newly_tagged = [allp[(n_hot + 7 * step + k) % n_pools][0] for k in range(3)]
        pf.watch(allp, set(hot_ids) | set(newly_tagged), T)
        if step >= 3:  # after the warm-up: the card reads of this scan
            for pid, mint in allp:
                if pid in hot_ids or pid in newly_tagged:
                    part = pf.part_at(pid, mint, T, SLOT + 10)
                    reads += 1
                    if part is None or T - part["as_of_ms"] > hc.CHAIN_MINT_MAX_AGE_MS:
                        stale += 1
        for sec in range(0, 5 * MIN, 5000):  # the prefetch passes of this interval, every CYCLE_S
            clock.ms = T + 100 + sec
            pf.run_once(clock.ms)
    return pf, chain.counter, stale, reads


def rolling_max(counter: H.CreditCounter, t_end: int) -> int:
    best = 0
    for t in range(T0, t_end - HOUR + 1, MIN):
        best = max(best, counter.calls_between(t, t + HOUR))
    return best


def test_v2_never_exceeds_the_hourly_cap_in_any_rolling_hour_and_the_day_cap_follows():
    pf, ctr, stale, reads = simulate(P.POLICY_V2, n_pools=150, n_hot=45, hours=6)
    peak = rolling_max(ctr, T0 + 6 * HOUR)
    assert peak <= P.HOURLY_BUDGET, peak  # counter-measured, every rolling hour (minute-aligned windows, one minute wide of the check's own)
    assert peak * 24 <= 40_000 and ctr.calls <= 6 * P.HOURLY_BUDGET
    assert reads > 0 and stale / reads <= 0.02, (stale, reads)  # check 2's limit on this synthetic hot set


def test_v2_holds_the_cap_under_overload_and_sheds_holders_before_mints():
    # 600 pools: far more mint demand than one hour can pay in full; retries make holders reads cost 2 extra calls each
    pf, ctr, stale, reads = simulate(P.POLICY_V2, n_pools=600, n_hot=60, hours=4, extra=2)
    assert rolling_max(ctr, T0 + 4 * HOUR) <= P.HOURLY_BUDGET
    st = pf.stats_
    assert st.get("deferred_hold", 0) > 0  # holders reads were shed
    t_last = T0 + (4 * 12 - 1) * 5 * MIN + 2000
    hot_ids = {x for x, _ in pools(60)}
    hot_mint_ages = [t_last - p.mints[-1].as_of_ms for p in pf.pools.values() if p.mints and p.pool in hot_ids]
    assert max(hot_mint_ages) <= hc.CHAIN_MINT_MAX_AGE_MS  # hot pools' mint facts stayed inside the card limit while holders were shed


def test_v1_leaves_pools_stale_on_the_same_synthetic_set_and_v2_does_not():
    _, c1, stale1, reads1 = simulate(P.POLICY_V1, n_pools=150, n_hot=45, hours=6)
    _, c2, stale2, reads2 = simulate(P.POLICY_V2, n_pools=150, n_hot=45, hours=6)
    assert reads1 == reads2 > 0
    assert stale1 / reads1 > 0.02 >= stale2 / reads2  # the diagnosis: v1's cold pools go stale once the hour's total passes the cold cap
    assert rolling_max(c1, T0 + 6 * HOUR) <= P.HOURLY_BUDGET and rolling_max(c2, T0 + 6 * HOUR) <= P.HOURLY_BUDGET  # neither policy buys freshness with more calls


def test_v2_order_new_pools_then_hot_mints_then_cold_mints_then_holders():
    clock = Clock(T0)
    chain = CountingChain(clock)
    pf = P.ChainPrefetcher(chain, R.HV4ChainSource.compose, clock=clock, policy=P.POLICY_V2)
    seen: list[tuple[str, str]] = []
    orig = pf._refresh
    pf._refresh = lambda p, now, nm, nh: (seen.append((p.pool, "mint" if nm else "hold")), orig(p, now, nm, nh))[1]  # type: ignore[method-assign]
    a = pools(3, "A")
    pf.watch(a, [a[0][0]], T0)
    pf.run_once(T0)
    clock.ms = T0 + 11 * MIN
    new = pools(1, "N")
    pf.watch(a + new, [a[0][0], a[1][0]], clock.ms)  # A0, A1 hot; A2 cold; N0 new
    seen.clear()
    pf.run_once(clock.ms)
    kinds = [k for _, k in seen]
    assert seen[0][0] == new[0][0] and seen[0][1] == "mint"  # a new pool first
    assert [p for p, k in seen[1:3]] == [a[0][0], a[1][0]] or {p for p, k in seen[1:3]} == {a[0][0], a[1][0]}  # then the hot mints
    assert seen[3] == (a[2][0], "mint")  # then the cold mint
    assert kinds[4:] and all(k == "hold" for k in kinds[4:])  # holders last


def test_the_spend_file_keeps_the_v1_key_and_adds_the_mint_class(tmp_path):
    clock = Clock(T0)
    pf = P.ChainPrefetcher(CountingChain(clock), R.HV4ChainSource.compose, clock=clock, spend_path=tmp_path / "s.json", policy=P.POLICY_V2)
    pf.watch(pools(2), [], T0)
    pf.run_once(T0)
    doc = json.loads((tmp_path / "s.json").read_text())
    assert doc["schema"] == P.SPEND_SCHEMA and doc["spent"] and doc["mint"]
    again = P.ChainPrefetcher(CountingChain(clock), R.HV4ChainSource.compose, clock=clock, spend_path=tmp_path / "s.json", policy=P.POLICY_V2)
    assert again._spent_last_hour(T0) == pf._spent_last_hour(T0) and again._mint_spent_last_hour(T0) == pf._mint_spent_last_hour(T0)


# ---- B: the obs rows


def test_first_seen_rows_one_per_entry_with_scan_t_and_reentry(tmp_path):
    clock = Clock(T0)
    obs = O.ObsLog(tmp_path)
    pf = P.ChainPrefetcher(CountingChain(clock), R.HV4ChainSource.compose, clock=clock, policy=P.POLICY_V2)
    pf.set_obs(obs)
    obs.scan_ms = T0 + 2000
    a = pools(2)
    pf.watch(a, [a[0][0]], T0 + 2100)
    pf.watch(a, [], T0 + 5 * MIN + 2100)  # same pools again: no new row
    obs.scan_ms = T0 + 40 * MIN + 2000
    pf.watch(pools(1, "Z"), [], T0 + 40 * MIN + 2100)  # a is not seen for 35 min: dropped
    obs.scan_ms = T0 + 45 * MIN + 2000
    pf.watch(a[:1], [], T0 + 45 * MIN + 2100)  # a0 comes back: re-entry
    rows = [r for r in O.read_rows(tmp_path / O.OBS_FILE) if r["kind"] == "first_seen_by_scanner"]
    assert [(r["pool"], r["entry"], r["scan_ms"]) for r in rows] == [
        (a[0][0], "first", T0 + 2000), (a[1][0], "first", T0 + 2000), (pools(1, "Z")[0][0], "first", T0 + 40 * MIN + 2000), (a[0][0], "re_entry", T0 + 45 * MIN + 2000)]
    assert rows[0]["in_filter_pass"] is True and rows[0]["n_filter_pass"] == 2 and rows[0]["hot"] is True and rows[3]["since_last_seen_ms"] > 30 * MIN


def test_chain_hour_rows_carry_exact_per_minute_calls_and_the_error_counters(tmp_path):
    clock = Clock(T0)
    obs = O.ObsLog(tmp_path)
    chain = CountingChain(clock)
    pf = P.ChainPrefetcher(chain, R.HV4ChainSource.compose, clock=clock, policy=P.POLICY_V2)
    pf.set_obs(obs)
    pf.stats_["spend_write_errors"] = 2
    for k in range(3 * 12):  # three hours of 5-min watches
        clock.ms = T0 + k * 5 * MIN
        pf.watch(pools(5), pools(5)[0:1] and [pools(5)[0][0]], clock.ms)
        pf.run_once(clock.ms)
    rows = [r for r in O.read_rows(tmp_path / O.OBS_FILE) if r["kind"] == "chain_hour"]
    assert [r["hour_start_ms"] for r in rows] == [T0, T0 + HOUR]
    for r in rows:
        assert len(r["per_minute_calls"]) == 60 and sum(r["per_minute_calls"]) == r["calls"] == chain.counter.calls_between(r["hour_start_ms"], r["hour_end_ms"])
        assert r["spend_write_errors"] == 2 and r["counter_calls_total"] >= r["calls"] and r["policy"] == "v2"
    start = [r for r in O.read_rows(tmp_path / O.OBS_FILE) if r["kind"] == "prefetch_start"]
    assert len(start) == 1 and start[0]["spent_at_load"] == 0


def test_a_failed_obs_write_is_counted_and_never_raised(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    obs = O.ObsLog(blocker / "sub")  # the parent is a file: mkdir fails
    assert obs.write("x", a=1) is False and obs.write_errors == 1
    off = O.ObsLog(tmp_path, enabled=False)
    assert off.write("x") is False and not (tmp_path / O.OBS_FILE).exists()


def test_env_switches():
    assert O.obs_enabled({}) is True and O.obs_enabled({"MAL_HV4_OBS": "0"}) is False
    assert P.default_policy({}) == P.POLICY_V2 and P.default_policy({"MAL_HV4_PREFETCH_POLICY": "v1"}) == P.POLICY_V1
