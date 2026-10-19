"""Tests for tools/hv4_prefetch.py (the background chain-fact refresher behind the shakedown #1219 fix). No network, no files except the tmp runner root.

The chain is a fake with the three HV4Chain methods the refresher uses, stamping every answer with an injected clock; the cards module is the real one.
"""
from __future__ import annotations

import json
import subprocess
import sys
import types
from pathlib import Path

import pytest

from tools import hv4_cards as hc
from tools import hv4_chain as H
from tools import hv4_prefetch as P
from tools import hv4_runner as R
from tools.test_hv4_cards import SLOT, T, POOL, base_chain, base_prints, base_snap
from tools.test_hv4_runner import MIN, T0, T_START, make, rows_of

@pytest.fixture(scope="module", autouse=True)
def _arena_test_env():
    with pytest.MonkeyPatch.context() as mp:  # register_bot's test overrides need MAL_ARENA_TEST=1 (as in test_hv4_runner)
        mp.setenv("MAL_ARENA_TEST", "1")
        yield


MINT = "M" * 44
SIGNED = ("as_of_ms", "as_of_slot", "holders_as_of_ms", "top1_frac", "top10_frac", "mayhem")


class Clock:
    def __init__(self, ms: int) -> None:
        self.ms = ms

    def __call__(self) -> int:
        return self.ms


class FakeChain:
    """Stamps each answer when it 'returns' (clock + 5 ms), counts every call like the credit counter, and caches the Mayhem flag after its first read."""

    def __init__(self, clock: Clock, *, provider: str = "helius", slot: int = SLOT, fail: tuple[str, ...] = ()) -> None:
        self.clock, self.calls, self.log, self.slot, self.fail = clock, 0, [], slot, set(fail)
        self.rpc = types.SimpleNamespace(provider=provider)
        owner = self

        class Counter:  # the two reads CreditCounter has: the cumulative count and the per-UTC-day count
            calls = property(lambda self: owner.calls)

            def calls_today(self):
                return owner.calls

        self.counter = Counter()
        self._mh: set[str] = set()
        self.holder_max_age: list[float] = []

    def credit_report(self):
        return {"calls": self.calls}

    def mint_facts(self, mint, force=False):
        self.calls += 1
        self.log.append(("mint", mint))
        if "mint" in self.fail:
            return H._failed_mint(mint, "rpc_failed", self.clock.ms)
        self.clock.ms += 5
        return H.MintFacts(mint=mint, ok=True, error=None, token_program_id=H.SPL_TOKEN_PROGRAM, token_program="spl-token", mint_authority=None,
                           freeze_authority=None, as_of_slot=self.slot, as_of_ms=self.clock.ms)

    def mayhem_flag(self, mint, *, tape_is_mayhem=None, pool=None, verify=False):
        if pool not in self._mh:
            self.calls += 1
            self._mh.add(pool)
        self.log.append(("mayhem", pool))
        return H.MayhemFlag(mint=mint, is_mayhem=False, source="pool_account", as_of_slot=self.slot, as_of_ms=self.clock.ms)

    def largest_holders(self, mint, *, token_program=None, pool=None, max_age_s=900, **kw):
        self.holder_max_age.append(max_age_s)
        self.calls += 1
        self.log.append(("holders", pool))
        if "holders" in self.fail:
            return H.Holders(mint=mint, ok=False, error="rpc_failed", as_of_ms=self.clock.ms)
        self.clock.ms += 5
        return H.Holders(mint=mint, ok=True, error=None, top1_frac=0.05, top10_frac=0.3, as_of_slot=self.slot, as_of_ms=self.clock.ms)


def mk(t0: int = T - 3_600_000, **kw):
    clock = Clock(t0)
    chain = FakeChain(clock, **kw)
    return clock, chain, P.ChainPrefetcher(chain, R.HV4ChainSource.compose, clock=clock)


def vetoes_for(part: dict, at: int = T):
    keep = {k: v for k, v in base_chain().items() if k not in SIGNED}
    return hc.code_vetoes(base_snap(), base_prints(), {**keep, **part}, as_of_ms=at, as_of_slot=SLOT)


# ----------------------------------------------------------------------------------------------------------------------- a pool newly passing the filters
def test_a_newly_passing_pool_has_no_facts_until_one_pass_and_then_has_them_without_rpc_in_the_scan():
    clock, chain, pf = mk(T - 120_000)
    pf.watch([(POOL, MINT)], [], clock.ms)
    assert pf.part_at(POOL, MINT, T, SLOT) is None  # the scan that first sees the pool cannot card it: no fact exists yet
    assert chain.calls == 0
    pf.run_once()  # one refresh interval
    calls = chain.calls
    part = pf.part_at(POOL, MINT, T, SLOT)
    assert part is not None and chain.calls == calls  # the scan only reads
    assert part["as_of_ms"] < T and part["as_of_slot"] <= SLOT and part["mayhem"] is False
    assert vetoes_for(part).reasons == ()


def test_the_unwatched_or_mintless_pool_is_unavailable_not_guessed():
    clock, _, pf = mk()
    assert pf.part_at(POOL, MINT, T, SLOT) is None and pf.part_at(POOL, None, T, SLOT) is None


# ----------------------------------------------------------------------------------------------------------------------- the as-of cut
def scenario_future_facts_are_never_used(mod) -> None:
    """Three mint refreshes: one 10 min before T, one stamped exactly T, one after T. Only the first may be served, whatever the later ones say."""
    clock = Clock(T - 10 * MIN)
    chain = FakeChain(clock)
    pf = mod.ChainPrefetcher(chain, R.HV4ChainSource.compose, clock=clock)
    pf.watch([(POOL, MINT)], [POOL], clock.ms)
    pf.run_once()  # stamps at T - 10 min + 5 ms
    old = pf.part_at(POOL, MINT, T, SLOT)
    assert old is not None
    for at in (T - 5, T + 7):  # a refresh whose stamp is exactly T (clock T - 5, +5 ms for the call), then one after T
        clock.ms = at
        pf.pools[POOL].mints.append(chain.mint_facts(MINT))
        pf.pools[POOL].holders.append(chain.largest_holders(MINT, token_program=H.SPL_TOKEN_PROGRAM, pool=POOL, max_age_s=0))
    assert [m.as_of_ms for m in pf.pools[POOL].mints][-2:] == [T, T + 12]
    got = pf.part_at(POOL, MINT, T, SLOT)
    assert got["as_of_ms"] == old["as_of_ms"] < T  # not the fact stamped at T, not the one after
    assert "holders_as_of_ms" not in got or got["holders_as_of_ms"] < T
    # and a fact whose slot is after the arena's latest slot is not used either
    late = pf.pools[POOL].mints[-3]
    assert pf.part_at(POOL, MINT, T + 1, SLOT - 1) is None or pf.part_at(POOL, MINT, T + 1, SLOT - 1)["as_of_slot"] <= SLOT - 1
    assert late.as_of_slot == SLOT
    # only future facts: nothing is served
    pf.pools[POOL].mints.clear()
    pf.pools[POOL].mints.append(chain.mint_facts(MINT))
    assert pf.part_at(POOL, MINT, T - 3 * MIN, SLOT) is None


def load_mutant(src_edit) -> types.ModuleType:
    src = Path(P.__file__).read_text()
    mut = src_edit(src)
    assert mut != src, "the mutation did not change the source"
    mod = types.ModuleType("hv4_prefetch_mutant")
    mod.__file__ = P.__file__
    sys.modules["hv4_prefetch_mutant"] = mod  # dataclasses looks its module up by name
    exec(compile(mut, "hv4_prefetch_mutant", "exec"), mod.__dict__)
    return mod


def test_a_fact_stamped_at_or_after_T_is_never_used():
    scenario_future_facts_are_never_used(P)


@pytest.mark.parametrize("edit", [
    lambda s: s.replace("m.as_of_ms is not None and m.as_of_ms < T and", "m.as_of_ms is not None and m.as_of_ms <= T and"),  # mint: before -> at or before
    lambda s: s.replace("m.as_of_ms is not None and m.as_of_ms < T and", "m.as_of_ms is not None and"),  # mint: no cut at all
    lambda s: s.replace("h.as_of_ms is not None and h.as_of_ms < T and", "h.as_of_ms is not None and"),  # holders: no cut at all
], ids=["mint_le", "mint_uncut", "holders_uncut"])
def test_the_as_of_cut_is_mutation_tested(edit):
    with pytest.raises(AssertionError):
        scenario_future_facts_are_never_used(load_mutant(edit))


def test_the_slot_cut_is_mutation_tested():
    clock, chain, pf = mk(T - 10 * MIN, slot=SLOT + 50)  # the chain's slot is ahead of the arena's latest slot at T
    pf.watch([(POOL, MINT)], [], clock.ms)
    pf.run_once()
    assert pf.part_at(POOL, MINT, T, SLOT) is None  # the same rule as assert_as_of: a slot after the arena's is a leak
    mod = load_mutant(lambda s: s.replace("_slot_ok(m.as_of_slot, slot)", "True"))
    pf2 = mod.ChainPrefetcher(chain, R.HV4ChainSource.compose, clock=clock)
    pf2.pools = pf.pools
    assert pf2.part_at(POOL, MINT, T, SLOT) is not None


# ----------------------------------------------------------------------------------------------------------------------- freshness limits still bite
def test_a_mint_fact_older_than_the_limit_is_served_so_the_card_refuses_it():
    clock, _, pf = mk(T - hc.CHAIN_MINT_MAX_AGE_MS - 10_000)
    pf.watch([(POOL, MINT)], [], clock.ms)
    pf.run_once()
    clock.ms = T
    pf.pools[POOL].next_try_ms["mint"] = 10 ** 18  # the refresher is down (or starved): nothing newer arrives
    part = pf.part_at(POOL, MINT, T, SLOT)
    assert part is not None and T - part["as_of_ms"] > hc.CHAIN_MINT_MAX_AGE_MS
    assert vetoes_for(part).reasons == ("chain_stale",)  # fail closed, same reason as before
    assert pf.stats()["served_mint_stale"] == 1


def test_holders_older_than_five_minutes_are_left_out_not_served_stale():
    clock, _, pf = mk(T - 6 * MIN)
    pf.watch([(POOL, MINT)], [POOL], clock.ms)
    pf.run_once()
    fresh = pf.part_at(POOL, MINT, T - 2 * MIN, SLOT)
    assert fresh["top10_frac"] == 0.3 and fresh["holders_as_of_ms"] < T - 2 * MIN
    old = pf.part_at(POOL, MINT, T, SLOT)
    assert "top10_frac" not in old and "holders_as_of_ms" not in old  # a null holders block, like a failed read
    assert vetoes_for(old).reasons == ()


def test_no_holder_calls_on_the_public_endpoint_which_refuses_them():
    clock, chain, pf = mk(T - 120_000, provider="public")
    pf.watch([(POOL, MINT)], [POOL], clock.ms)
    pf.run_once()
    assert [k for k, _ in chain.log if k == "holders"] == [] and "top10_frac" not in pf.part_at(POOL, MINT, T, SLOT)


def test_a_failed_read_is_not_retried_for_a_minute_and_is_never_invented():
    clock, chain, pf = mk(T - 120_000, fail=("mint",))
    pf.watch([(POOL, MINT)], [], clock.ms)
    pf.run_once()
    assert chain.calls == 1 and pf.part_at(POOL, MINT, T, SLOT) is None
    clock.ms += 30_000
    pf.run_once()
    assert chain.calls == 1  # backed off
    clock.ms += 31_000
    pf.run_once()
    assert chain.calls == 2 and pf.stats()["mint_errors"] == 2


# ----------------------------------------------------------------------------------------------------------------------- steady state: no chain_stale
def test_hot_and_cold_pools_stay_inside_both_limits_at_every_scan_for_two_hours():
    clock, chain, pf = mk(T - 3 * 3600_000)
    pools = {"hot": ("H" * 44, "m" * 44), "cold": ("C" * 44, "n" * 44)}
    seen = {"hot": 0, "cold": 0}
    for t in range(clock.ms, clock.ms + 2 * 3600_000, 30_000):  # the worker wakes every 30 s; a scan every 5 min
        clock.ms = t
        if (t - (T - 3 * 3600_000)) % 300_000 == 0:
            for name, (pool, mint) in pools.items():
                part = pf.part_at(pool, mint, t, SLOT)
                if part is not None:
                    seen[name] += 1
                    ages = (t - part["as_of_ms"], t - part.get("holders_as_of_ms", t))
                    assert ages[0] <= hc.CHAIN_MINT_MAX_AGE_MS and ages[1] <= hc.CHAIN_HOLDERS_MAX_AGE_MS, (name, ages)
            pf.watch([(p, m) for p, m in pools.values()], [pools["hot"][0]], t)
        pf.run_once()
    assert seen["hot"] >= 22 and seen["cold"] >= 22  # every scan but the first of each pool had facts, all inside both limits (asserted above)
    assert chain.calls < 2 * 3600 // 60 * 3  # a handful of calls an hour for two pools


# ----------------------------------------------------------------------------------------------------------------------- budget and priority
def test_the_hourly_call_budget_is_never_exceeded_and_new_pools_come_first():
    clock = Clock(T - 3_600_000)
    chain = FakeChain(clock)
    pf = P.ChainPrefetcher(chain, R.HV4ChainSource.compose, clock=clock)  # default budget for the start-up burst: every pool's first read
    old = [(f"O{i:042d}", f"o{i:043d}") for i in range(80)]
    pf.watch(old, [], clock.ms)
    pf.run_once()
    assert len(pf.pools) == 80 and all(p.mints for p in pf.pools.values())
    pf.hourly_budget = 60  # a small budget, to see it bind
    clock.ms += 61 * MIN  # an hour on: all 80 are due for a mint refresh (cold, not hot)
    pf.watch(old, [], clock.ms)
    base = chain.calls
    for _ in range(5):
        pf.run_once()
    cold_calls = chain.calls - base
    assert 0 < cold_calls <= 60 * P.COLD_SHARE and pf.stats()["budget_deferred"] > 0  # the cold tier may use only its share
    new = [(f"N{i:042d}", f"n{i:043d}") for i in range(5)]
    pf.watch(old + new, [n[0] for n in new], clock.ms)
    chain.log.clear()
    pf.run_once()
    first_seen = [p for k, p in chain.log if k == "mayhem"]
    assert set(first_seen[:5]) == {n[0] for n in new}  # newly passing pools are served before any cold pool
    for _ in range(10):
        pf.run_once()
    assert chain.calls - base <= 60  # the hour's budget, never more
    clock.ms += 61 * MIN  # the window rolls
    before = chain.calls
    pf.run_once()
    assert chain.calls > before


def test_a_dropped_pool_leaves_the_store_after_thirty_minutes():
    clock, _, pf = mk(T - 3_600_000)
    pf.watch([(POOL, MINT)], [], clock.ms)
    pf.watch([("Z" * 44, "z" * 44)], [], clock.ms + P.DROP_AFTER_MS + 1)
    assert POOL not in pf.pools and pf.stats()["pools_dropped"] == 1


# ----------------------------------------------------------------------------------------------------------------------- the runner with the prefetcher
def drive(run, clock, pf, t_end):
    """Live-like loop: the wall clock reads T + 100 ms when the tick runs (so a fetch made inside the scan is stamped after T), the refresher runs after the tick."""
    for t in range(T_START, t_end, R.TICK_MS):
        clock.ms = t + 100
        run.step(t)
        if pf is not None and t % 30_000 == R.SCAN_SECOND_MS:
            pf.run_once()


def test_card_time_fetch_is_always_stamped_after_T_which_is_the_shakedown_root_cause(tmp_path):
    clock = Clock(T_START)
    chain = FakeChain(clock)
    run = make(tmp_path)
    run.chain = R.HV4ChainSource(chain)  # the old path: fetch at card time, no prefetcher
    drive(run, clock, None, T0 + 40 * MIN)
    refusals = rows_of(run, "SCAN", "as_of_refusal")
    assert refusals and all("chain.as_of_ms=" in r["detail"] and "is not before" in r["detail"] for r in refusals)
    assert not [r for r in rows_of(run, "SCAN", "scan") if r["n_cards"]]  # no card is ever built from a fresh fetch (the cache never refreshes under live clocks)


def test_the_runner_never_fetches_in_the_scan_and_the_shakedown_failure_is_gone(tmp_path):
    clock = Clock(T_START)
    chain = FakeChain(clock)
    pf = P.ChainPrefetcher(chain, R.HV4ChainSource.compose, clock=clock)
    run = make(tmp_path)
    run.chain = R.HV4ChainSource(chain, prefetch=pf)
    scan_calls = []
    orig_scan = run.scan

    def scan(T):
        before = chain.calls
        orig_scan(T)
        scan_calls.append(chain.calls - before)

    run.scan = scan
    drive(run, clock, pf, T0 + 40 * MIN)
    assert scan_calls and sum(scan_calls) == 0, "the scan made an RPC call"
    assert chain.holder_max_age and set(chain.holder_max_age) == {0}  # the refresher always goes to the chain, never to a cached copy
    assert not run.hook_errors and run.counters.get("leakage_refused", 0) == 0 and not rows_of(run, "SCAN", "as_of_refusal")
    refused = rows_of(run, "SCAN", "card_refused")
    assert not [r for r in refused if "chain_stale" in r["reasons"]]
    assert {e["pool"] for e in rows_of(run, "CON-X", "entry")}  # cards were built from prefetched facts and traded
    rep = run.chain.credit_report()["prefetch"]
    assert rep["served_ok"] > 0 and rep["pools"] == 2 and rep["calls_last_hour"] <= rep["hourly_budget"]


# ----------------------------------------------------------------------------------------------------------------------- the job script's RPC choice
JOB = Path(__file__).resolve().parent.parent / "scripts" / "research" / "hv4-runner-job.sh"


def rpc_for(mode: str, env: dict[str, str]) -> str:
    text = JOB.read_text()
    snippet = text[text.index("# --- chain rpc ---"):text.index("# --- end chain rpc ---")]
    out = subprocess.run(["sh", "-c", f'MODE={mode}\n{snippet}\nprintf %s "$MAL_HV4_RPC"'], capture_output=True, text=True,
                         env={"PATH": "/usr/bin:/bin", **env}, timeout=30)
    assert out.returncode == 0, out.stderr
    return out.stdout


def test_the_job_script_uses_helius_in_run_mode_public_in_stub_and_honours_an_explicit_override():
    assert subprocess.run(["sh", "-n", str(JOB)], capture_output=True, timeout=30).returncode == 0
    assert rpc_for("run", {}) == "helius"
    assert rpc_for("stub", {}) == "public" and rpc_for("register", {}) == "public"
    assert rpc_for("run", {"MAL_HV4_RPC": "public"}) == "public"
    assert rpc_for("stub", {"MAL_HV4_RPC": "helius"}) == "helius"


def test_a_stray_rpc_selector_refuses_at_start_in_the_script_and_in_python_and_is_never_echoed():
    stray = "https://mainnet.example/?api-key=SECRETISH123"
    text = JOB.read_text()
    snippet = text[text.index("# --- chain rpc ---"):text.index("# --- end chain rpc ---")]
    out = subprocess.run(["sh", "-c", f"MODE=run\n{snippet}\nprintf started"], capture_output=True, text=True,
                         env={"PATH": "/usr/bin:/bin", "MAL_HV4_RPC": stray}, timeout=30)
    assert out.returncode == 2 and "started" not in out.stdout and stray not in out.stdout + out.stderr and "api-key" not in out.stdout + out.stderr
    with pytest.raises(H.ChainError) as e:
        H.RpcConfig.from_env({"MAL_HV4_RPC": stray})
    assert stray not in str(e.value) and "api-key" not in str(e.value)
    assert H.RpcConfig.from_env({}).provider == "public" and H.RpcConfig.from_env({"MAL_HV4_RPC": " Public "}).provider == "public"


# ----------------------------------------------------------------------------------------------------------------------- the budget across UTC midnight and restarts
MIDNIGHT = 1_792_454_400_000  # 2026-10-20T00:00:00Z


class DayChain(FakeChain):
    """A FakeChain on the REAL CreditCounter (per-UTC-day counts), whose mint read straddles 00:00Z."""

    def __init__(self, clock: Clock, counter: H.CreditCounter) -> None:
        super().__init__(clock)
        self.counter = counter

    def mint_facts(self, mint, force=False):
        self.clock.ms += 1500
        self.counter.record_call("getAccountInfo")
        return super().mint_facts(mint, force)

    def mayhem_flag(self, mint, *, tape_is_mayhem=None, pool=None, verify=False):
        self.counter.record_call("getAccountInfo")
        return super().mayhem_flag(mint, tape_is_mayhem=tape_is_mayhem, pool=pool, verify=verify)

    def largest_holders(self, mint, **kw):
        self.counter.record_call("getTokenLargestAccounts")
        return super().largest_holders(mint, **kw)


def test_a_refresh_that_straddles_utc_midnight_books_its_real_calls_not_a_negative_number():
    clock = Clock(MIDNIGHT - 1000)
    ctr = H.CreditCounter("helius", now_ms=clock)
    for _ in range(1200):  # calls already made today
        ctr.record_call("getAccountInfo")
    chain = DayChain(clock, ctr)
    pf = P.ChainPrefetcher(chain, R.HV4ChainSource.compose, clock=clock)
    pf.watch([(POOL, MINT)], [POOL], clock.ms)
    before_day = ctr.calls_today()
    made = pf.run_once(clock.ms)
    assert ctr.calls_today() < before_day  # the day rolled inside the refresh: the per-day count fell back (this is what broke the old delta)
    assert made == 3 and pf._spent_last_hour(clock.ms) == 3  # mint + Mayhem + holders, not -1197
    assert all(n >= 0 for _, n in pf._spent)
    # and the bound still binds in the hour after midnight
    pf.hourly_budget = 5
    for k in range(6):
        pf.watch([(f"P{k:043d}", f"m{k:043d}")], [], clock.ms)
    pf.run_once(clock.ms)
    assert pf._spent_last_hour(clock.ms) <= 5 and pf.stats()["budget_deferred"] > 0


def test_a_counter_that_goes_backwards_is_clamped_at_zero():
    clock = Clock(T)
    chain = FakeChain(clock)
    box = {"n": 100}

    class Flaky:  # a cumulative count that falls between the two reads (a reset): never a negative spend
        @property
        def calls(self):
            box["n"] -= 50
            return box["n"]

    chain.counter = Flaky()
    pf = P.ChainPrefetcher(chain, R.HV4ChainSource.compose, clock=clock)
    pf.watch([(POOL, MINT)], [], clock.ms)
    assert pf.run_once(clock.ms) == 0 and pf._spent_last_hour(clock.ms) == 0


def test_the_hourly_spend_survives_a_restart_and_a_corrupt_file_counts_as_a_spent_hour(tmp_path):
    path = tmp_path / "spend.json"
    clock = Clock(T)
    a = P.ChainPrefetcher(FakeChain(clock), R.HV4ChainSource.compose, clock=clock, hourly_budget=6, spend_path=path)
    a.watch([(f"P{k:043d}", f"m{k:043d}") for k in range(5)], [], clock.ms)
    a.run_once()
    assert a._spent_last_hour(clock.ms) == 6 and a.chain.calls == 6  # three new pools at 2 calls (mint, Mayhem); the other two wait for the next hour
    assert [f.name for f in tmp_path.iterdir()] == ["spend.json"]  # atomic: no temp file left behind
    clock.ms += 30 * MIN
    b = P.ChainPrefetcher(FakeChain(clock), R.HV4ChainSource.compose, clock=clock, hourly_budget=6, spend_path=path)  # the restarted process
    assert b._spent_last_hour(clock.ms) == 6  # not reset to 0: a crash loop cannot spend 6 an hour for ever
    b.watch([("Z" * 44, "z" * 44)], [], clock.ms)
    b.run_once()
    assert b.chain.calls == 0 and b.stats()["budget_deferred"] == 1
    clock.ms += 31 * MIN  # an hour after the first spend: it has aged out of the window
    c = P.ChainPrefetcher(FakeChain(clock), R.HV4ChainSource.compose, clock=clock, hourly_budget=6, spend_path=path)
    assert c._spent_last_hour(clock.ms) == 0
    path.write_text("{not json")
    d = P.ChainPrefetcher(FakeChain(clock), R.HV4ChainSource.compose, clock=clock, hourly_budget=6, spend_path=path)
    assert d._spent_last_hour(clock.ms) == 6 and d.stats()["spend_state_reset"] == 1  # fail closed


def test_the_live_chain_source_keeps_both_bounds_in_files_under_the_runner_root(tmp_path):
    env = {"MAL_HV4_RPC": "public"}
    a = R.live_chain_source(tmp_path, env)
    for _ in range(7):
        a.chain.rpc.counter.record_call("getAccountInfo")
    a.prefetch._book(T, 4)
    assert sorted(f.name for f in tmp_path.iterdir()) == [R.CHAIN_CREDITS_FILE, R.CHAIN_SPEND_FILE]
    assert json.loads((tmp_path / R.CHAIN_SPEND_FILE).read_text())["spent"] == [[T - T % MIN, 4]]
    b = R.live_chain_source(tmp_path, env)  # the restart: the day's call count is back, so the 40k/day cap is not reset
    assert sum(b.chain.rpc.counter.by_day.values()) == 7 and b.chain.rpc.counter.calls_today() == 7
    with pytest.raises(H.ChainError):
        R.live_chain_source(tmp_path, {"MAL_HV4_RPC": "nope"})
