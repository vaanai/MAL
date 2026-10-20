"""HV4 v2 prefetch policy (A) and observability rows (B): budget proof on a synthetic hot set, ordering, and the obs rows.

Paper only. Offline: a fake chain with the real CreditCounter (per-minute counts), a replay clock, no RPC.
"""
from __future__ import annotations

import json
import types
from pathlib import Path

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


# ---- B at the runner level: the rows exist, and the decision logs do not move with them


def _decision_rows(run):
    return {name: run.log.rows(name) for name in (*run.arm_names, "SCAN", "RISK")}


def test_runner_obs_rows_are_written_and_the_decision_logs_are_identical_with_obs_on_and_off(tmp_path, monkeypatch):
    from tools.test_hv4_runner import T_END, T_START, make

    monkeypatch.setenv("MAL_ARENA_TEST", "1")  # scratch registry overrides, as the runner tests do
    monkeypatch.setenv("MAL_HV4_OBS", "1")
    on = make(tmp_path / "on")
    on.run(T_START, T_END)
    monkeypatch.setenv("MAL_HV4_OBS", "0")
    off = make(tmp_path / "off")
    off.run(T_START, T_END)
    assert on.cfg.obs is True and off.cfg.obs is False
    assert _decision_rows(on) == _decision_rows(off)  # every row of every log, chain fields included: the same decisions
    def log_bytes(run):  # and the files themselves: every decision-log file under <root>/decisions, byte for byte
        base = Path(run.cfg.root) / "decisions"
        return {str(p.relative_to(base)): p.read_bytes() for p in sorted(base.rglob("*")) if p.is_file()}

    assert log_bytes(on) == log_bytes(off) and log_bytes(on)
    assert on.counters == off.counters and on.hook_errors == off.hook_errors == []
    rows = O.read_rows(tmp_path / "on" / "hv4" / O.OBS_FILE)
    kinds = [r["kind"] for r in rows]
    assert kinds[0] == "runner_start" and "runner_hour" in kinds
    hour = next(r for r in rows if r["kind"] == "runner_hour")
    assert hour["hour_end_ms"] - hour["hour_start_ms"] == HOUR and hour["n_hook_errors"] == 0 and hour["obs_write_errors"] == 0
    assert not (tmp_path / "off" / "hv4" / O.OBS_FILE).exists()


# ---- QP 6279836164 R1: run mode is pinned (policy v2, obs on); the start row says so


def test_run_mode_refuses_any_policy_but_v2_and_obs_off(monkeypatch, capsys):
    assert R.unpinned_run_mode({}) is None and R.unpinned_run_mode({"MAL_HV4_PREFETCH_POLICY": "v2", "MAL_HV4_OBS": "1"}) is None
    for bad in ("v1", "V1", " v1 ", "v3", "", "none"):
        why = R.unpinned_run_mode({"MAL_HV4_PREFETCH_POLICY": bad})
        assert why and "v2 only" in why, bad
    assert "MAL_HV4_OBS=0" in R.unpinned_run_mode({"MAL_HV4_OBS": "0"})
    argv = ["--frozen-sha", "abcdef1234567", "--clock-start-ms", "1", "--feed-delay-slots", "7", "--stub"]
    monkeypatch.setenv("MAL_HV4_PREFETCH_POLICY", "v1")
    assert R.main(argv) == 2 and "MAL_HV4_PREFETCH_POLICY" in capsys.readouterr().err  # main() stops before it opens the tape, the engine or the ledger
    monkeypatch.delenv("MAL_HV4_PREFETCH_POLICY")
    monkeypatch.setenv("MAL_HV4_OBS", "0")
    assert R.main(argv) == 2 and "MAL_HV4_OBS" in capsys.readouterr().err


def test_the_live_source_ignores_the_policy_variable_and_a_live_runner_refuses_v1_or_obs_off(tmp_path, monkeypatch):
    import pytest
    from tools.test_hv4_runner import T0, make

    monkeypatch.setenv("MAL_ARENA_TEST", "1")  # scratch registry overrides, as the runner tests do
    monkeypatch.setenv("MAL_HV4_PREFETCH_POLICY", "v1")
    src = R.live_chain_source(tmp_path / "root", {"MAL_HV4_RPC": "public"})
    assert src.prefetch.policy == P.POLICY_V2  # the run-mode path does not read the variable at all
    monkeypatch.delenv("MAL_HV4_PREFETCH_POLICY")
    clock = Clock(T0)
    bnd = {"git_sha": "a" * 40, "dirty": False}
    base = make(tmp_path / "x")

    def live_runner(chain, root, obs=True):
        cfg = R.RunnerConfig(root=str(tmp_path / root), frozen_sha="a" * 40, clock_start_ms=T0, live=True, lane_h=False, obs=obs)
        return R.Runner(cfg, base.state, base.engine, base.model, chain, base.guard, binding=bnd)

    v1_chain = R.HV4ChainSource(CountingChain(clock), prefetch=P.ChainPrefetcher(CountingChain(clock), R.HV4ChainSource.compose, clock=clock, policy=P.POLICY_V1))
    with pytest.raises(RuntimeError, match="v2 only"):
        live_runner(v1_chain, "l1")
    v2_chain = R.HV4ChainSource(CountingChain(clock), prefetch=P.ChainPrefetcher(CountingChain(clock), R.HV4ChainSource.compose, clock=clock, policy=P.POLICY_V2))
    with pytest.raises(RuntimeError, match="obs rows off"):
        live_runner(v2_chain, "l2", obs=False)
    run = live_runner(v2_chain, "l3")  # v2 and obs on: starts
    run.resume()
    start = [r for r in run.log.rows("SCAN") if r["kind"] == "start"]
    assert len(start) == 1 and start[0]["prefetch_policy"] == "v2" and start[0]["obs"] is True and start[0]["lane_h"] is False and start[0]["frozen_sha"] == "a" * 40
    # a replay's start row is unchanged: no new key outside run mode
    sim = R.Runner(R.RunnerConfig(root=str(tmp_path / "sim"), frozen_sha="a" * 40, clock_start_ms=T0, live=False, lane_h=False), base.state, base.engine, base.model,
                   base.chain, base.guard, binding=bnd)
    sim.resume()
    srow = [r for r in sim.log.rows("SCAN") if r["kind"] == "start"][0]
    assert "prefetch_policy" not in srow and "obs" not in srow


# ---- R3: the restart hour is measurable


def test_the_state_file_keeps_the_last_61_minutes_and_the_next_process_writes_them_as_prior_minutes(tmp_path):
    clock = Clock(T0)
    path = tmp_path / "chain-credits.json"
    a = H.CreditCounter("helius", now_ms=clock, state_path=path)
    for k in range(70):  # one call a minute for 70 minutes
        clock.ms = T0 + k * MIN + 5000
        a.record_call("getAccountInfo")
    doc = json.loads(path.read_text())
    assert len(doc["minutes"]) == 61 and doc["minutes"][0][0] == T0 + 9 * MIN and doc["minutes"][-1] == [T0 + 69 * MIN, 1, 1] and doc["calls_total"] == 70
    assert set(doc) >= {"schema", "by_day", "credits_by_day", "saved_ms", "minutes", "calls_total"}
    clock.ms = T0 + 75 * MIN
    b = H.CreditCounter("helius", now_ms=clock, state_path=path)
    assert b.prior_calls_total == 70 and b.prior_minutes == doc["minutes"] and b.minute_counts(T0, T0 + 80 * MIN)[-1][1] == 0  # own record starts empty
    chain = CountingChain(clock)
    chain.counter = b
    pf = P.ChainPrefetcher(chain, R.HV4ChainSource.compose, clock=clock, policy=P.POLICY_V2)
    obs = O.ObsLog(tmp_path)
    pf.set_obs(obs)
    row = next(r for r in O.read_rows(tmp_path / O.OBS_FILE) if r["kind"] == "prefetch_start")
    assert row["prior_minutes"] == doc["minutes"] and row["prior_calls_total"] == 70 and row["prior_minutes_error"] is None
    b.record_call("getAccountInfo")  # the second process's own call lands in its own record and the file carries both
    d2 = json.loads(path.read_text())
    assert d2["calls_total"] == 71 and d2["minutes"][-1] == [T0 + 75 * MIN, 1, 1] and d2["minutes"][-2][0] == T0 + 69 * MIN  # minutes before the window of 61 fall off
    assert b.calls == 1 and b.minute_counts(T0 + 75 * MIN, T0 + 76 * MIN) == [[T0 + 75 * MIN, 1, 1]]


def test_a_state_file_without_the_tail_or_with_a_bad_one_loads_and_keeps_the_daily_cap(tmp_path):
    clock = Clock(T0)
    path = tmp_path / "s.json"
    day = time_day = __import__("time").strftime("%Y-%m-%d", __import__("time").gmtime(T0 / 1000))
    base = {"schema": H.STATE_SCHEMA, "by_day": {day: 7}, "credits_by_day": {day: 7}, "saved_ms": T0}
    path.write_text(json.dumps(base))  # the d1dbcd6 format
    a = H.CreditCounter("helius", now_ms=clock, state_path=path)
    assert a.prior_minutes == [] and a.prior_calls_total == 0 and a.prior_error is None and a.by_day == {day: 7}
    path.write_text(json.dumps({**base, "minutes": [[1, "x", 3]], "calls_total": 4}))
    b = H.CreditCounter("helius", now_ms=clock, state_path=path)
    assert b.prior_minutes == [] and b.prior_error == "minutes_malformed" and b.by_day == {day: 7}  # the cap survives; the tail is reported, not trusted


# ---- R4: a v2 refresh is booked at the minute it was made


def test_v2_books_each_refresh_at_the_minute_it_really_ran():
    clock = Clock(T0)

    class Slow(CountingChain):
        def mint_facts(self, mint, force=False):
            r = super().mint_facts(mint, force)
            self.clock.ms += 20_000  # 20 s a read: a start-up pass of 12 new pools runs over four minutes
            return r

    pf = P.ChainPrefetcher(Slow(clock), R.HV4ChainSource.compose, clock=clock, policy=P.POLICY_V2, holders=False)
    pf.watch(pools(12), [], T0)
    pf.run_once(T0)
    buckets = [b for b, _ in pf._spent]
    assert len(buckets) >= 4 and buckets == sorted(buckets) and buckets[-1] >= T0 + 3 * MIN and sum(n for _, n in pf._spent) == pf.chain.counter.calls == 24


# ---- R5: a failing obs writer is counted apart and never stops a pass, a scan or a decision


class RaisingObs:
    enabled = True
    scan_ms = None
    write_errors = build_errors = rows_written = 0

    def write(self, *a, **k):
        raise RuntimeError("boom")

    write_many = write


def test_prefetcher_obs_failures_are_counted_apart_from_pass_errors():
    clock = Clock(T0)
    chain = CountingChain(clock)
    pf = P.ChainPrefetcher(chain, R.HV4ChainSource.compose, clock=clock, policy=P.POLICY_V2)
    pf.set_obs(RaisingObs())
    a = pools(3)
    pf.watch(a, [a[0][0]], T0)  # first_seen write raises: caught
    pf.run_once(T0)
    clock.ms = T0 + 20 * MIN  # the mints are 20 min old: a card read is served a stale fact and its obs row raises too
    assert pf.part_at(a[0][0], a[0][1], clock.ms, SLOT + 10) is not None
    clock.ms = T0 + HOUR + MIN  # the hour closes: the hourly row cannot be written
    pf.run_once(clock.ms)
    assert pf.stats_.get("obs_errors", 0) >= 3 and "pass_errors" not in pf.stats_
    pf.obs = O.ObsLog(None)  # and a row that cannot be BUILT (a counter that raises) is caught in the same place
    chain.counter.minute_counts = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("bad counter"))
    pf._obs_hour = pf._obs_hour - 1
    pf.obs.enabled = True
    n = pf.stats_["obs_errors"]
    pf.run_once(clock.ms)
    assert pf.stats_["obs_errors"] == n + 1 and "pass_errors" not in pf.stats_


def test_a_runner_hour_row_that_cannot_be_built_is_counted_and_the_decision_logs_do_not_move(tmp_path, monkeypatch):
    from tools.test_hv4_runner import T_END, T_START, make

    monkeypatch.setenv("MAL_ARENA_TEST", "1")
    monkeypatch.setenv("MAL_HV4_OBS", "1")
    ok = make(tmp_path / "ok")
    ok.run(T_START, T_END)
    monkeypatch.setattr(R, "rss_mb", lambda: (_ for _ in ()).throw(RuntimeError("no /proc")))
    bad = make(tmp_path / "bad")
    bad.run(T_START, T_END)  # must not raise
    assert bad.obs.build_errors >= 1 and ok.obs.build_errors == 0
    assert _decision_rows(bad) == _decision_rows(ok) and bad.counters == ok.counters and bad.hook_errors == ok.hook_errors == []


# ---- every obs row carries the pid of the process that wrote it (CHECKS-3 check 3 joins on it)


def test_every_obs_kind_carries_one_integer_pid_equal_to_the_writers_taken_at_write_time(tmp_path, monkeypatch):
    import os

    from tools.test_hv4_runner import T_END, T_START, make

    monkeypatch.setenv("MAL_ARENA_TEST", "1")
    monkeypatch.setenv("MAL_HV4_OBS", "1")
    run = make(tmp_path / "r")  # runner_start, runner_hour
    run.run(T_START, T_END)
    clock = Clock(T0)
    obs = O.ObsLog(tmp_path / "p")
    pf = P.ChainPrefetcher(CountingChain(clock), R.HV4ChainSource.compose, clock=clock, policy=P.POLICY_V2)
    pf.set_obs(obs)  # prefetch_start
    obs.scan_ms = T0 + 2000
    a = pools(3)
    pf.watch(a, [a[0][0]], T0 + 2100)  # first_seen_by_scanner
    pf.run_once(T0 + 2100)
    clock.ms = T0 + 20 * MIN
    assert pf.part_at(a[0][0], a[0][1], clock.ms, SLOT + 10) is not None  # chain_stale_served: a mint fact older than 15 min
    clock.ms = T0 + HOUR + MIN
    pf.run_once(clock.ms)  # chain_hour
    rows = O.read_rows(Path(run.cfg.root) / O.OBS_FILE) + O.read_rows(tmp_path / "p" / O.OBS_FILE)
    assert {r["kind"] for r in rows} == {"runner_start", "runner_hour", "prefetch_start", "first_seen_by_scanner", "chain_stale_served", "holders_null_served", "chain_hour"}
    for r in rows:
        assert type(r["pid"]) is int and r["pid"] == os.getpid(), r["kind"]
    # taken at write time, and a row cannot carry another one: a child that forks after the log was built writes its own pid
    log = O.ObsLog(tmp_path / "q")
    monkeypatch.setattr(O.os, "getpid", lambda: 4242)
    log.write("x", pid=1)
    monkeypatch.setattr(O.os, "getpid", lambda: 4243)
    log.write("y")
    got = O.read_rows(tmp_path / "q" / O.OBS_FILE)
    assert [(r["kind"], r["pid"]) for r in got] == [("x", 4242), ("y", 4243)]


# ---- reviewer pre-review round (comment 6280231012): B1 and N1 to N13


def test_b1_a_new_untagged_pool_still_gets_its_first_mint_read_in_a_loaded_hour():
    clock = Clock(T0)
    chain = CountingChain(clock)
    pf = P.ChainPrefetcher(chain, R.HV4ChainSource.compose, clock=clock, policy=P.POLICY_V2, hourly_budget=P.HOURLY_BUDGET)  # the 1,500 numbers: holders 680, cold mint 400
    old = pools(1, "O")
    pf.watch(old, [], T0)
    pf.run_once(T0)  # O has a mint fact
    clock.ms = T0 + 11 * MIN  # and it is due again (cold)
    # the loaded hour: holders at their cap (680) and ~400 cold mint reads, so the lower classes stand at 1,080 of the 1,480
    pf._book(clock.ms, 680)
    pf._book(clock.ms, 400, mint=True)
    new = pools(1, "N")
    pf.watch(old + new, [], clock.ms)  # N passes the filters, untagged (not hot)
    n_before = len(pf.pools[old[0][0]].mints)
    pf.run_once(clock.ms)
    assert pf.pools[new[0][0]].mints, "the new pool's first read is a hot-class read"
    assert len(pf.pools[old[0][0]].mints) == n_before and pf.stats_.get("deferred_mint", 0) == 1  # the cold refresh is the one that waits


def test_n1_hour_rows_are_written_even_when_every_pass_raises(tmp_path):
    clock = Clock(T0)
    pf = P.ChainPrefetcher(CountingChain(clock), R.HV4ChainSource.compose, clock=clock, policy=P.POLICY_V2)
    obs = O.ObsLog(tmp_path)
    pf.set_obs(obs)
    pf.watch(pools(2), [], T0)

    def boom(*a, **k):
        raise RuntimeError("refresh failed")

    pf._refresh = boom  # type: ignore[method-assign]
    for ms in (T0, T0 + HOUR + MIN):
        try:
            pf.run_once(ms)
        except RuntimeError:
            pass
    rows = [r for r in O.read_rows(tmp_path / O.OBS_FILE) if r["kind"] == "chain_hour"]
    assert [r["hour_start_ms"] for r in rows] == [T0]


def test_n2_prefetch_start_reports_what_was_loaded_not_what_was_booked_since(tmp_path):
    clock = Clock(T0)
    spend = tmp_path / "s.json"
    first = P.ChainPrefetcher(CountingChain(clock), R.HV4ChainSource.compose, clock=clock, spend_path=spend, policy=P.POLICY_V2)
    first._book(T0, 5)
    chain = CountingChain(clock)
    pf = P.ChainPrefetcher(chain, R.HV4ChainSource.compose, clock=clock, spend_path=spend, policy=P.POLICY_V2)  # the restart: loads 5
    pf._book(T0, 7)  # a call booked before the obs file is attached
    chain.counter.record_call("getAccountInfo")
    pf.set_obs(O.ObsLog(tmp_path))
    row = next(r for r in O.read_rows(tmp_path / O.OBS_FILE) if r["kind"] == "prefetch_start")
    assert row["spent_at_load"] == 5 and row["counter_calls_at_start"] == 0 and row["spend_state_reset"] == 0


def test_n3_the_gate_reads_the_real_clock_inside_a_long_pass():
    clock = Clock(T0)

    class Slow(CountingChain):
        def mint_facts(self, mint, force=False):
            r = super().mint_facts(mint, force)
            self.clock.ms += 2 * MIN
            return r

    pf = P.ChainPrefetcher(Slow(clock), R.HV4ChainSource.compose, clock=clock, policy=P.POLICY_V2)
    seen: list[int] = []
    orig = pf._hour_calls
    pf._hour_calls = lambda n: (seen.append(n), orig(n))[1]  # type: ignore[method-assign]
    pf.watch(pools(3), [], T0)
    pf.run_once(T0)
    assert seen[0] == T0 and seen[-1] >= T0 + 4 * MIN  # the third gate asks about the window that ends at the real minute, not at the pass start


def test_n4_a_lost_spend_file_cannot_hide_the_restart_crossing_hour(tmp_path):
    clock = Clock(T0)
    state = tmp_path / "chain-credits.json"
    a = H.CreditCounter("helius", now_ms=clock, state_path=state)
    for k in range(60):  # the previous process: 2 calls a minute for the whole hour
        for _ in range(2):
            clock.ms = T0 + k * MIN + 1000
            a.record_call("getAccountInfo")
    clock.ms = T0 + 60 * MIN + 5000

    def restarted(counter_path):
        chain = CountingChain(clock)
        chain.counter = H.CreditCounter("helius", now_ms=clock, state_path=counter_path)
        pf = P.ChainPrefetcher(chain, R.HV4ChainSource.compose, clock=clock, hourly_budget=100, policy=P.POLICY_V2)  # no spend file: it was deleted
        pf.watch(pools(1), [], clock.ms)
        pf.run_once(clock.ms)
        return pf

    pf = restarted(state)
    assert not pf.pools[pools(1)[0][0]].mints and pf.stats_["deferred_mint"] == 1  # 120 calls in the last hour against a budget of 100: it waits
    fresh = restarted(tmp_path / "other.json")  # control: the same prefetcher with no prior hour reads at once
    assert fresh.pools[pools(1)[0][0]].mints


def test_n5_an_unreadable_class_key_books_nothing_to_the_mint_classes(tmp_path):
    clock = Clock(T0)
    spend = tmp_path / "s.json"
    spend.write_text(json.dumps({"schema": P.SPEND_SCHEMA, "spent": [[T0 - T0 % MIN, 300]], "mint": "garbage", "hot_mint": [[1, 2]]}))
    pf = P.ChainPrefetcher(CountingChain(clock), R.HV4ChainSource.compose, clock=clock, spend_path=spend, policy=P.POLICY_V2)
    assert pf._spent_last_hour(T0) == 300 and pf._mint_spent_last_hour(T0) == 0 and pf._hot_spent_last_hour(T0) == 0 and pf.stats_["spend_class_reset"] == 1
    # holders_spent = spent - mint_spent = 300: the holders class looks as spent as possible, the fail-closed direction


def test_n6_dropped_pools_are_forgotten_after_a_day():
    clock = Clock(T0)
    pf = P.ChainPrefetcher(CountingChain(clock), R.HV4ChainSource.compose, clock=clock, policy=P.POLICY_V2)
    pf.watch(pools(2), [], T0)
    pf.watch(pools(1, "Z"), [], T0 + 40 * MIN)  # the two leave the set: dropped
    assert len(pf._dropped) == 2
    pf.watch(pools(1, "Z"), [], T0 + 25 * HOUR)
    assert pf._dropped == {}


def test_n7_the_stale_row_is_written_after_the_lock_is_released():
    clock = Clock(T0)
    held: list[bool] = []
    pf = P.ChainPrefetcher(CountingChain(clock), R.HV4ChainSource.compose, clock=clock, policy=P.POLICY_V2)

    class Spy:
        enabled = True
        scan_ms = None
        write_errors = build_errors = rows_written = 0

        def write(self, kind, **f):
            held.append(pf._lock._is_owned())  # type: ignore[attr-defined]

        write_many = write

    a = pools(1)
    pf.watch(a, [], T0)
    pf.run_once(T0)
    pf.obs = Spy()
    assert pf.part_at(a[0][0], a[0][1], T0 + 20 * MIN, SLOT + 10) is not None
    assert held and not any(held)  # chain_stale_served and holders_null_served, neither inside the lock


def test_n8_a_short_write_is_counted_and_the_next_row_starts_on_a_new_line(tmp_path, monkeypatch):
    log = O.ObsLog(tmp_path)
    real = O.os.write
    calls = {"n": 0}

    def short(fd, data):
        calls["n"] += 1
        return real(fd, data[: len(data) // 2]) if calls["n"] == 1 else real(fd, data)

    monkeypatch.setattr(O.os, "write", short)
    assert log.write("lost", a="x" * 40) is False and log.write_errors == 1
    assert log.write("kept", b=2) is True
    kinds = [r["kind"] for r in O.read_rows(tmp_path / O.OBS_FILE)]
    assert kinds == ["kept"]  # the torn line is skipped by the reader and did not swallow the next row
    assert (tmp_path / O.OBS_FILE).read_bytes().count(b"\n") == 2  # the newline that separated them


def test_n9_runner_hour_writes_one_row_per_closed_hour(tmp_path, monkeypatch):
    from tools.test_hv4_runner import make

    monkeypatch.setenv("MAL_ARENA_TEST", "1")
    monkeypatch.setenv("MAL_HV4_OBS", "1")
    run = make(tmp_path / "r")
    run._obs_hour_tick(T0 + 5 * MIN)  # sets the hour
    run._obs_hour_tick(T0 + 3 * HOUR + 5 * MIN)  # a stall of three hours
    rows = [r for r in O.read_rows(Path(run.cfg.root) / O.OBS_FILE) if r["kind"] == "runner_hour"]
    assert [(r["hour_start_ms"], r["hour_end_ms"]) for r in rows] == [(T0 + k * HOUR, T0 + (k + 1) * HOUR) for k in range(3)]
    assert {r["written_in_hour"] for r in rows} == {(T0 + 3 * HOUR) // HOUR}


def test_n11_chain_hour_counts_distinct_deferred_pools_by_class_and_the_booked_calls(tmp_path):
    clock = Clock(T0)
    pf = P.ChainPrefetcher(CountingChain(clock), R.HV4ChainSource.compose, clock=clock, hourly_budget=100, policy=P.POLICY_V2)
    pf.set_obs(O.ObsLog(tmp_path))
    pf.run_once(T0)  # the first pass fixes the hour
    clock.ms = T0 + 10 * MIN
    pf._book(clock.ms, 98)  # the budget is spent: every read waits
    a = pools(3)
    pf.watch(a, [a[0][0]], clock.ms)
    for k in range(3):  # three passes: the same three pools are deferred each time
        pf.run_once(clock.ms + k * 5000)
    clock.ms = T0 + HOUR + MIN
    pf.run_once(clock.ms)
    row = next(r for r in O.read_rows(tmp_path / O.OBS_FILE) if r["kind"] == "chain_hour")
    # the old counter is cumulative and per pass (3 pools x 4 passes, the fourth being the pass that closes the hour); the new ones are per distinct pool and per hour
    assert row["deferred_mint"] == 12 and row["deferred_hot_mint"] == 3 and row["deferred_cold_mint"] == 0  # a new pool is a hot-class read
    assert row["booked_calls_hour"] == 98 and "deferred_hold" in row and row["calls"] == 0


def test_n10_the_live_start_row_has_the_pid_and_the_replay_start_row_does_not(tmp_path, monkeypatch):
    import os

    from tools.test_hv4_runner import T0 as RT0
    from tools.test_hv4_runner import make

    monkeypatch.setenv("MAL_ARENA_TEST", "1")
    base = make(tmp_path / "x")
    clock = Clock(RT0)
    chain = R.HV4ChainSource(CountingChain(clock), prefetch=P.ChainPrefetcher(CountingChain(clock), R.HV4ChainSource.compose, clock=clock, policy=P.POLICY_V2))
    bnd = {"git_sha": "a" * 40, "dirty": False}
    cfg = R.RunnerConfig(root=str(tmp_path / "live"), frozen_sha="a" * 40, clock_start_ms=RT0, live=True, lane_h=False)
    live = R.Runner(cfg, base.state, base.engine, base.model, chain, base.guard, binding=bnd)
    live.resume()
    assert [r["pid"] for r in live.log.rows("SCAN") if r["kind"] == "start"] == [os.getpid()]


def test_n12_a_state_payload_that_cannot_be_built_never_reaches_the_rpc_path_and_record_call_reads_the_clock_once(tmp_path):
    reads = []

    def clock():
        reads.append(1)
        return T0 + 5000

    c = H.CreditCounter("helius", now_ms=clock, state_path=tmp_path / "s.json")
    c.record_call("getAccountInfo")
    assert len(reads) == 1  # one clock read per call: the day key, the minute bucket and the saved instant agree
    c._tail = lambda now: (_ for _ in ()).throw(RuntimeError("bad tail"))  # type: ignore[method-assign]
    c.record_call("getAccountInfo")  # must not raise
    assert c.state_write_errors == 1 and c.calls == 2


def test_n13_a_live_runner_needs_the_v2_prefetcher_and_survives_a_failing_attach(tmp_path, monkeypatch):
    import pytest
    from tools.test_hv4_runner import T0 as RT0
    from tools.test_hv4_runner import FakeChain, make

    monkeypatch.setenv("MAL_ARENA_TEST", "1")
    base = make(tmp_path / "x")
    bnd = {"git_sha": "a" * 40, "dirty": False}
    cfg = R.RunnerConfig(root=str(tmp_path / "l1"), frozen_sha="a" * 40, clock_start_ms=RT0, live=True, lane_h=False)
    with pytest.raises(RuntimeError, match="background chain prefetcher"):
        R.Runner(cfg, base.state, base.engine, base.model, FakeChain(), base.guard, binding=bnd)  # no .prefetch: the card-time fetch path

    class BadAttach:
        prefetch = types.SimpleNamespace(policy="v2")

        def __init__(self):
            self._c = FakeChain()

        def attach_obs(self, obs):
            raise RuntimeError("attach failed")

        def __getattr__(self, k):
            return getattr(self._c, k)

    cfg2 = R.RunnerConfig(root=str(tmp_path / "l2"), frozen_sha="a" * 40, clock_start_ms=RT0, live=True, lane_h=False)
    run = R.Runner(cfg2, base.state, base.engine, base.model, BadAttach(), base.guard, binding=bnd)
    assert run.obs.build_errors == 1


# ---- harness knobs for the PR #768 check 4 variants (constructor path only; run mode builds its prefetcher without them)


def test_cold_mint_refresh_knob_only_slows_pools_that_are_not_hot_and_run_mode_has_no_knob(tmp_path, monkeypatch):
    clock = Clock(T0)
    slow = P.ChainPrefetcher(CountingChain(clock), R.HV4ChainSource.compose, clock=clock, policy=P.POLICY_V2, cold_mint_refresh_ms=20 * MIN)
    dflt = P.ChainPrefetcher(CountingChain(clock), R.HV4ChainSource.compose, clock=clock, policy=P.POLICY_V2)
    a = pools(2)
    for pf in (slow, dflt):
        pf.watch(a, [a[0][0]], T0)  # A0 hot, A1 cold
        pf.run_once(T0)
    clock.ms = T0 + 12 * MIN
    for pf in (slow, dflt):
        pf.pools[a[0][0]].hot_until_ms = clock.ms + 1  # keep A0 hot at the check
    due = {k: [pf._due(pf.pools[pid], clock.ms)[0] for pid, _ in a] for k, pf in (("slow", slow), ("default", dflt))}
    assert due == {"slow": [True, False], "default": [True, True]}  # hot pools stay at 10 min; the cold one waits for 20; the default is unchanged
    clock.ms = T0 + 21 * MIN
    assert slow._due(slow.pools[a[1][0]], clock.ms)[0] is True
    # run mode: the live source takes no knob, and the policy variable does not reach it
    monkeypatch.setenv("MAL_HV4_PREFETCH_POLICY", "v2")
    src = R.live_chain_source(tmp_path / "root", {"MAL_HV4_RPC": "public"})
    assert src.prefetch.cold_mint_refresh_ms is None and src.prefetch.hourly_budget == P.HOURLY_BUDGET_V2


# ---- Note N4 (QP ruling 6281516193): holders limit 15 min under v2, hot and warm holders every 12 min, the order, P1 and P2


def _chain_with_holders(part: dict, t_part: int):
    """tools.test_hv4_cards' base chain with the holders block replaced by what the prefetcher served, re-based to that module's scan instant (same ages)."""
    from tools import test_hv4_cards as TC

    chain = TC.base_chain()
    for k in ("holders_as_of_ms", "top1_frac", "top10_frac"):
        chain.pop(k, None)
    if part.get("holders_as_of_ms") is not None:
        chain.update({"holders_as_of_ms": TC.T - (t_part - part["holders_as_of_ms"]), "top1_frac": part["top1_frac"], "top10_frac": part["top10_frac"]})
    return chain


def _card(chain: dict, limit: int):
    from tools import test_hv4_cards as TC

    return hc.build_card(TC.base_snap(), TC.base_prints(), chain, as_of_ms=TC.T, as_of_slot=TC.SLOT, holders_max_age_ms=limit)


def _served(policy: str, age_min: int):
    """A prefetcher with one holders read `age_min` minutes old at the card instant; returns (chain dict served, scan instant)."""
    clock = Clock(T0)
    pf = P.ChainPrefetcher(CountingChain(clock), R.HV4ChainSource.compose, clock=clock, policy=policy)
    a = pools(1)
    pf.watch(a, [a[0][0]], T0)
    pf.run_once(T0)
    assert pf.pools[a[0][0]].holders
    t = pf.pools[a[0][0]].holders[-1].as_of_ms + age_min * MIN + 1000
    return pf.part_at(a[0][0], a[0][1], t, SLOT + 10), t


def test_n4_one_holders_limit_per_policy_feeds_the_card_the_serve_and_the_direct_path(tmp_path, monkeypatch):
    from tools.test_hv4_runner import T0 as RT0
    from tools.test_hv4_runner import make

    monkeypatch.setenv("MAL_ARENA_TEST", "1")
    assert hc.holders_max_age_ms("v2") == hc.CHAIN_HOLDERS_MAX_AGE_MS_V2 == 15 * MIN
    assert hc.holders_max_age_ms("v1") == hc.holders_max_age_ms(None) == hc.CHAIN_HOLDERS_MAX_AGE_MS == 5 * MIN
    clock = Clock(RT0)
    pf2 = P.ChainPrefetcher(CountingChain(clock), R.HV4ChainSource.compose, clock=clock, policy=P.POLICY_V2)
    pf1 = P.ChainPrefetcher(CountingChain(clock), R.HV4ChainSource.compose, clock=clock, policy=P.POLICY_V1)
    src2, src1 = R.HV4ChainSource(CountingChain(clock), prefetch=pf2), R.HV4ChainSource(CountingChain(clock), prefetch=pf1)
    direct = R.HV4ChainSource(CountingChain(clock))  # no prefetcher: the card-time path
    assert (pf2.holders_max_age_ms, src2.holders_max_age_ms) == (15 * MIN, 15 * MIN) and (pf1.holders_max_age_ms, src1.holders_max_age_ms) == (5 * MIN, 5 * MIN)
    assert direct.holders_max_age_ms == 5 * MIN
    asked: list[float] = []
    chain = direct.chain
    orig = chain.largest_holders
    chain.largest_holders = lambda mint, **kw: (asked.append(kw["max_age_s"]), orig(mint, **kw))[1]  # type: ignore[method-assign]
    direct.chain_part("P" * 44, "m" * 44, RT0, SLOT + 10)
    assert asked == [300.0]  # the direct path reads the same constant
    base = make(tmp_path / "x")
    for src, want in ((src2, 15 * MIN), (src1, 5 * MIN), (direct, 5 * MIN)):
        run = R.Runner(R.RunnerConfig(root=str(tmp_path / f"r{want}{id(src)}"), frozen_sha="a" * 40, clock_start_ms=RT0, live=False, lane_h=False), base.state, base.engine,
                       base.model, src, base.guard)
        assert run.holders_max_age_ms == want  # and the card veto reads the runner's value (build_card(holders_max_age_ms=...), below)


def test_n4_under_v2_a_holders_read_5_to_15_min_old_is_a_block_with_its_age_and_no_holders_stale():
    part, t = _served(P.POLICY_V2, 10)
    assert part["top10_frac"] is not None and part["holders_as_of_ms"] is not None
    card = _card(_chain_with_holders(part, t), hc.holders_max_age_ms("v2"))
    assert card["risk"]["top10_frac"] is not None and 300 < card["risk"]["holders_age_s"] <= 900
    import pytest

    with pytest.raises(hc.CardRefused) as e:  # the SAME fact under the v1 limit is the d1dbcd6 veto, which is what a mismatch of the three places would give
        _card(_chain_with_holders(part, t), hc.holders_max_age_ms("v1"))
    assert "holders_stale" in e.value.reasons


def test_n4_under_v2_a_holders_read_older_than_15_min_is_a_null_block_and_no_veto():
    part, t = _served(P.POLICY_V2, 16)
    assert "holders_as_of_ms" not in part and "top10_frac" not in part  # left out by the serve
    card = _card(_chain_with_holders(part, t), hc.holders_max_age_ms("v2"))  # not refused
    assert card["risk"]["holders_as_of_ms"] is None and card["risk"]["top10_frac"] is None and card["risk"]["holders_age_s"] is None
    part15, t15 = _served(P.POLICY_V2, 14)  # and 14 min is still served
    assert part15["top10_frac"] is not None


def test_n4_v1_is_unchanged_at_5_min():
    part, t = _served(P.POLICY_V1, 6)
    assert "holders_as_of_ms" not in part  # left out at 6 min, as at d1dbcd6
    assert _served(P.POLICY_V1, 4)[0]["top10_frac"] is not None
    card = _card(_chain_with_holders(part, t), hc.holders_max_age_ms("v1"))
    assert card["risk"]["top10_frac"] is None
    assert P.HOT_HOLDERS_REFRESH_MS == 4 * MIN and P.HOURLY_BUDGET == 1_500 and P.MINT_REFRESH_MS == 10 * MIN
    assert P.ChainPrefetcher(CountingChain(Clock(T0)), R.HV4ChainSource.compose, policy=P.POLICY_V1).warm_holders_refresh_ms is None  # v1 reads no warm holders


def test_n4_the_module_asserts_hold_for_the_12_min_refreshes_against_15_min():
    assert P.V2_HOT_HOLDERS_REFRESH_MS == P.V2_WARM_HOLDERS_REFRESH_MS == 12 * MIN < hc.CHAIN_HOLDERS_MAX_AGE_MS_V2 == 15 * MIN
    assert P.HOT_HOLDERS_REFRESH_MS < hc.CHAIN_HOLDERS_MAX_AGE_MS and P.HOURLY_BUDGET_V2 == 2_000
    pf = P.ChainPrefetcher(CountingChain(Clock(T0)), R.HV4ChainSource.compose, policy=P.POLICY_V2)
    assert (pf.hot_holders_refresh_ms, pf.warm_holders_refresh_ms, pf.hourly_budget) == (12 * MIN, 12 * MIN, 2_000)
    assert H.DEFAULT_DAILY_CALL_CAP == 48_000


def _warm_setup(vol_taken_at: int):
    clock = Clock(T0)
    pf = P.ChainPrefetcher(CountingChain(clock), R.HV4ChainSource.compose, clock=clock, policy=P.POLICY_V2)
    a = pools(3)  # a0 hot; a1 and a2 warm
    pf.watch(a, [a[0][0]], T0)
    pf.run_once(T0)
    order: list[tuple[str, str]] = []
    orig = pf._refresh
    pf._refresh = lambda p, now, nm, nh: (order.append((p.pool, "mint" if nm else "hold")), orig(p, now, nm, nh))[1]  # type: ignore[method-assign]
    w = T0 + 13 * MIN
    clock.ms = w
    pf.watch(a, [a[0][0]], w, vols={a[1][0]: 5.0, a[2][0]: 50.0})
    pf._warm_vol_ms = vol_taken_at
    return pf, a, order, w


def test_n4_warm_holders_go_highest_volume_first_and_read_only_state_at_or_before_the_pass_clock():
    pf, a, order, w = _warm_setup(vol_taken_at=T0 + 13 * MIN)
    pf.run_once(w)  # pass clock = the instant the volumes were taken: used
    warm = [p for p, k in order if k == "hold" and p != a[0][0]]
    assert warm == [a[2][0], a[1][0]]  # a2 (50) before a1 (5)
    pf2, a2, order2, w2 = _warm_setup(vol_taken_at=T0 + 13 * MIN + 5000)  # volumes stamped AFTER the pass clock
    pf2.run_once(w2)
    assert [p for p, k in order2 if k == "hold" and p != a2[0][0]] == [a2[1][0], a2[2][0]]  # ignored: pool-id order, not the later volume
    # the order of the whole pass: hot holders before cold mint before warm holders
    kinds = [(p == a[0][0], k) for p, k in order]
    assert order.index((a[0][0], "hold")) < max(i for i, (p, k) in enumerate(order) if k == "hold" and p in (a[1][0], a[2][0]))


def test_n4_the_entry_and_risk_requests_carry_the_holders_stamp_and_age():
    """Traced with a REAL card (build_card of a 10 min old holders block under the v2 limit), not the reduced card of test_hv4_prompts: the model-facing text of the entry
    request and of the risk request holds risk.holders_as_of_ms and risk.holders_age_s."""
    import json as _json

    from tools import hv4_prompts as pr
    from tools.test_hv4_prompts import pb

    part, t = _served(P.POLICY_V2, 10)
    card = _card(_chain_with_holders(part, t), hc.holders_max_age_ms("v2"))
    assert 300 < card["risk"]["holders_age_s"] <= 900
    for req in (pr.build_entry_request("CON-L", pb("CON"), [card], 0), pr.build_risk_request(pb("RISK"), [card], 0)):
        sent = _json.loads(next(line for line in req.user_text.splitlines() if line.startswith("{")))
        assert sent["risk"]["holders_as_of_ms"] == card["risk"]["holders_as_of_ms"] and sent["risk"]["holders_age_s"] == card["risk"]["holders_age_s"], req.kind


def _loaded(book: int):
    """A v2 prefetcher (cap 2,000, plan stop 1,980) with `book` calls of cold-mint class spent in the hour and one pool of every kind due:
    N new, H hot (mint and holders), C cold (mint only: not in the latest scan), W warm (holders only)."""
    clock = Clock(T0)
    pf = P.ChainPrefetcher(CountingChain(clock), R.HV4ChainSource.compose, clock=clock, policy=P.POLICY_V2)
    h, c, w, n = pools(1, "H"), pools(1, "C"), pools(1, "W"), pools(1, "N")
    pf.watch(h + c + w, [h[0][0]], T0)
    pf.run_once(T0)
    clock.ms = T0 + 13 * MIN
    for pid in (w[0][0],):
        pf.pools[pid].mints.clear()  # W's mint is fresh below; keep its holders due only
    pf.pools[w[0][0]].mints.append(pf.chain.mint_facts(w[0][1], force=True))
    pf.watch(h + w + n, [h[0][0]], clock.ms, vols={w[0][0]: 10.0})  # C is not in the latest scan: cold, mint only
    order: list[tuple[str, str]] = []
    orig = pf._refresh
    pf._refresh = lambda p, now, nm, nh: (order.append((p.pool[0], "mint" if nm else "hold")), orig(p, now, nm, nh))[1]  # type: ignore[method-assign]
    pf._book(clock.ms, book, mint=True)
    pf.run_once(clock.ms)
    return order


def test_p1_p2_new_and_hot_mint_use_the_cap_the_lower_classes_stop_400_short_and_warm_holders_stop_600_short():
    # lower classes at 1,480 of the 1,980 plan stop: inside the 400 line (1,580) but past the warm line (1,380): everything but the warm holders read
    mid = _loaded(1480)
    assert ("N", "mint") in mid and ("H", "mint") in mid and ("H", "hold") in mid and ("C", "mint") in mid and ("W", "hold") not in mid
    # lower classes at 1,700: past the 400 line. New and hot mint still read (P1); hot holders, cold mint and warm holders are shed
    hi = _loaded(1700)
    assert ("N", "mint") in hi and ("H", "mint") in hi and ("H", "hold") not in hi and ("C", "mint") not in hi and ("W", "hold") not in hi
    # lower classes at 1,300: before the warm line, everything reads, and the order is new, hot mint, hot holders, cold mint, warm holders
    lo = _loaded(1300)
    assert lo.index(("N", "mint")) < lo.index(("H", "hold")) < lo.index(("C", "mint")) < lo.index(("W", "hold"))
    assert lo.index(("H", "mint")) < lo.index(("H", "hold"))


def test_holders_null_served_row_and_the_warm_chain_hour_keys(tmp_path):
    clock = Clock(T0)
    pf = P.ChainPrefetcher(CountingChain(clock), R.HV4ChainSource.compose, clock=clock, policy=P.POLICY_V2, hourly_budget=100)
    obs = O.ObsLog(tmp_path)
    pf.set_obs(obs)
    pf.run_once(T0)
    a = pools(2)
    clock.ms = T0 + MIN
    pf.watch(a, [], clock.ms, vols={a[0][0]: 3.0})
    pf.run_once(clock.ms)  # both get mints and (warm) holders under a budget of 100
    clock.ms = T0 + 20 * MIN
    pf.watch(a, [a[1][0]], clock.ms)  # a1 promoted at this scan from warm; its holders (read at +1 min) are 19 min old: null
    assert "holders_as_of_ms" not in pf.part_at(a[1][0], a[1][1], clock.ms, SLOT + 10)
    row = next(r for r in O.read_rows(tmp_path / O.OBS_FILE) if r["kind"] == "holders_null_served")
    assert row["pool"] == a[1][0] and row["scan_ms"] == clock.ms and row["promoted_at_scan"] is True and row["warm"] is True and row["hot"] is False
    assert 18 * MIN < row["newest_holders_age_ms"] < 20 * MIN
    pf.hourly_budget = 100
    clock.ms = T0 + HOUR + MIN
    pf.run_once(clock.ms)
    ch = next(r for r in O.read_rows(tmp_path / O.OBS_FILE) if r["kind"] == "chain_hour")
    assert {"deferred_warm_hold", "warm_holders_reads_hour", "warm_holders_calls_hour"} <= set(ch) and ch["warm_holders_reads_hour"] >= 1
