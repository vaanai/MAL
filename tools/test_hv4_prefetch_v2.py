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
    assert _decision_rows(on) == _decision_rows(off)  # every row of every log, chain fields included: byte-for-byte the same decisions
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

    pf = P.ChainPrefetcher(Slow(clock), R.HV4ChainSource.compose, clock=clock, policy=P.POLICY_V2)
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
