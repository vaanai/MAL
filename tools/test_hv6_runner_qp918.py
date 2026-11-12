"""QP 918 changes 1-8 for hv6_runner. Synthetic tape, fakes and StubModel only; no RPC, no API, no /data/mal/arena path."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from tools import hv4_arms
from tools import hv4_cards as C
from tools import hv6_agents as A
from tools import hv6_cards as H
from tools import hv6_runner as R
from tools.arena_paper import ArenaRefused
from tools.hv4_testkit import buy, reply, skip
from tools.test_hv4_runner import FakeChain, T0
from tools.test_hv6_runner import _env, full, make, rows_of, script, tagged_ids  # noqa: F401  (_env is autouse)

MIN = 60_000


# ------------------------------------------------------------------------------------------------------------------------ 1. chain stamped before T
class WallChain:
    """Stamps and caches like HV4Chain: every fetch is stamped with the wall clock `wall` at fetch time."""

    def __init__(self):
        self.wall, self.fetch_at, self.counter, self.rpc = 0, [], NS(calls=0), NS(provider="helius")
        self.slot_fn = lambda: 0

    def _stamp(self):
        self.fetch_at.append(self.wall)
        self.counter.calls += 1
        return self.wall

    def mint_facts(self, mint, force=False):
        return NS(ok=True, as_of_ms=self._stamp(), as_of_slot=self.slot_fn(), mint_authority=None, freeze_authority=None, token_program_id=C.SPL_TOKEN_PROGRAM,
                  extensions=(), ext_info={})

    def mayhem_flag(self, mint, pool=None):
        return NS(is_mayhem=False, as_of_ms=self._stamp())

    def largest_holders(self, mint, token_program=None, pool=None, max_age_s=None):
        return NS(ok=True, as_of_ms=self._stamp(), as_of_slot=self.slot_fn(), top1_frac=0.05, top10_frac=0.30)

    def credit_report(self):
        return {"calls": self.counter.calls}


def test_chain_source_run_path_with_wall_stamped_caching_chain(tmp_path_factory):
    from tools.hv4_prefetch import ChainPrefetcher

    d = tmp_path_factory.mktemp("r6pf")
    wc = WallChain()
    pf = ChainPrefetcher(wc, R.ChainSource.compose, clock=lambda: wc.wall, policy="v2", hourly_budget=R.RPC_CALLS_PER_H, spend_path=d / "v6" / R.CHAIN_SPEND_FILE)
    run = make(d, chain=R.ChainSource(wc, pf))
    wc.slot_fn = lambda: run.slot
    orig = run.tick

    def tick(T):
        if (T // 1000) % 60 == 30:  # the background refresher runs between scans
            wc.wall = T
            pf.run_once(T)
        wc.wall = T + 1000  # live: a fetch made inside the tick would be stamped after T
        n = len(wc.fetch_at)
        orig(T)
        assert len(wc.fetch_at) == n, "RPC in the scan path"

    run.tick = tick
    run.run(T0 + 9 * MIN + 2000, T0 + 60 * MIN)
    ent = {a: len(rows_of(run, a, "entry")) for a in R.ARM_ORDER}
    print("probe entries (ChainSource + prefetcher, live-like stamps):", ent)
    assert ent["L"] > 0 and ent["F"] > 0 and ent["X"] > 0 and ent["L-nr"] == 0 and ent["P"] == 0
    assert wc.counter.calls <= R.RPC_CALLS_PER_H
    assert all(t < T0 + 60 * MIN for t in wc.fetch_at)
    scans = rows_of(run, "SCAN", "scan")
    assert scans[0]["tagged"] == [] and any(r["tagged"] for r in scans)  # the first scan had no facts; its tags did not burn the cooldown
    assert run.summary()["chain_credits"]["calls"] == wc.counter.calls and "prefetch" in run.summary()["chain_credits"]


def test_card_time_chain_without_prefetcher_is_refused_live(tmp_path_factory):
    """The old path (what QP's probe showed): facts stamped at or after T give no entry. Kept as the reason the prefetcher is the run path."""
    d = tmp_path_factory.mktemp("r6nopf")
    wc = WallChain()
    run = make(d, chain=R.ChainSource(wc))
    wc.slot_fn = lambda: run.slot
    orig = run.tick
    run.tick = lambda T: (setattr(wc, "wall", T + 1000), orig(T))[1]
    run.run(T0 + 9 * MIN + 2000, T0 + 30 * MIN)
    assert all(not rows_of(run, a, "entry") for a in R.ARM_ORDER)


def test_holders_limit_follows_the_policy():
    assert R.ChainSource(WallChain(), NS(policy="v2")).holders_max_age_ms == C.CHAIN_HOLDERS_MAX_AGE_MS_V2
    assert R.ChainSource(WallChain()).holders_max_age_ms == C.CHAIN_HOLDERS_MAX_AGE_MS


def test_live_chain_source_pins_policy_budget_provider_and_paths(tmp_path_factory):
    base = tmp_path_factory.mktemp("r6live")
    env = {"MAL_HV4_RPC": "public", "MAL_HV4_PREFETCH_POLICY": "v1"}
    src = R.live_chain_source(base / "v6", env)
    assert src.prefetch.policy == "v2" and src.prefetch.hourly_budget == R.RPC_CALLS_PER_H == 2000   # fix C: HV4 v2's 2,000/h, paced (was 1,000)
    assert src.chain.rpc.daily_call_cap == 48_000 and src.chain.rpc.provider == "public"
    from tools.hv6_prefetch import PacedPrefetcher
    assert type(src.prefetch) is PacedPrefetcher and src.prefetch.pace_burst == 360 and src.prefetch.pace_per_min == 27.0
    assert Path(src.prefetch.spend_path) == base / "v6" / R.CHAIN_SPEND_FILE
    with pytest.raises(RuntimeError):
        R.live_chain_source(base / "v6", {})  # the provider must be set (pinned)
    with pytest.raises(RuntimeError):
        R.live_chain_source(base / "v6", {"MAL_HV4_RPC": "helius", "MAL_HV4_CHAIN_STATE": "/x"})
    assert H.forbidden_imports("from tools import hv4_prefetch, hv4_cards") == []
    assert H.forbidden_imports("from tools import hv4_read") == ["hv4_read"]


# ------------------------------------------------------------------------------------------------------------------------ 2. compose equality
def test_compose_equals_hv4_lane_g_including_extensions():
    from tools.hv4_runner import HV4ChainSource

    mf = NS(as_of_ms=1, as_of_slot=2, mint_authority=None, freeze_authority=None, token_program_id="T22", extensions=("transferFeeConfig", "defaultAccountState"),
            ext_info={"transfer_fee_config": {"older_bps": 10, "newer_bps": 25}, "default_account_state": {"state": "frozen"}})
    plain = NS(as_of_ms=1, as_of_slot=2, mint_authority=None, freeze_authority=None, token_program_id="T", extensions=())
    mh = NS(is_mayhem=False)
    for m in (mf, plain):
        for hd in (None, NS(ok=True, as_of_ms=5, top1_frac=0.1, top10_frac=0.4), NS(ok=False, as_of_ms=None)):
            assert R.ChainSource.compose(m, mh, hd) == HV4ChainSource.compose(m, mh, hd)
    assert R.ChainSource.compose(mf, mh, None)["transfer_fee_bps"] == 25 and R.ChainSource.compose(mf, mh, None)["default_account_state"] == "frozen"
    assert R.ChainSource._ext_values(mf) == HV4ChainSource._ext_values(mf)


# ------------------------------------------------------------------------------------------------------------------------ 3. arm order
class LatModel:
    def __init__(self, inner, lat):
        self.inner, self.lat, self.model = inner, lat, inner.model

    def complete(self, req, now_ms):
        return dataclasses.replace(self.inner.complete(req, now_ms), latency_ms=self.lat[req.arm])


def _entries_with_latencies(tmp_path_factory, lat):
    run = make(tmp_path_factory.mktemp("r6ord"))
    run.model = LatModel(run.model, lat)
    run.run(T0 + 9 * MIN + 2000, T0 + 14 * MIN)
    return {a: len(rows_of(run, a, "entry")) for a in R.ARM_ORDER}, {a: [r["reasons"] for r in rows_of(run, a, "skip")] for a in run.llm_arm_names}


def test_permuting_arm_order_changes_no_verdict(tmp_path_factory):
    arms = ["L", "L-nr", "F", "P"]
    for lats in ((1000, 2000, 3000, 4000), (4000, 3000, 2000, 1000), (2000, 4000, 1000, 3000)):
        ent, skips = _entries_with_latencies(tmp_path_factory, dict(zip(arms, lats)))
        assert ent["L"] == ent["F"] > 0 and ent["X"] > 0
    for lats in ((1000, 2000, 3000, 12_000), (12_000, 3000, 2000, 1000)):  # the slowest call makes the whole chunk STALE for everyone
        ent, skips = _entries_with_latencies(tmp_path_factory, dict(zip(arms, lats)))
        assert ent["L"] == ent["F"] == 0 and ent["X"] > 0
        assert skips["L"] == skips["F"] and skips["P"] == skips["L-nr"]  # same script, same verdict, whatever the arm's place in the order


# ------------------------------------------------------------------------------------------------------------------------ fakes for the unit tests
class FakeEngine:
    def __init__(self):
        self.positions, self.recovered, self.refuse, self.sold, self.now_fn = {}, 0, [], [], None

    def on_row(self, row):
        pass

    def recover(self):
        self.recovered += 1
        return 0

    def buy(self, bot, pool, size, ms, slot, meta=None):
        if self.refuse and self.refuse[0][0] == "buy":
            raise ArenaRefused(self.refuse.pop(0)[1])
        self.positions[meta["order_id"]] = pool
        return meta["order_id"]

    def sell(self, bot, pid, frac, ms, slot, reason=""):
        if self.refuse and self.refuse[0][0] == "sell":
            raise ArenaRefused(self.refuse.pop(0)[1])
        self.positions.pop(pid)
        self.sold.append(pid)


class FakeState:
    def __init__(self):
        self.apply = lambda row: None
        self.now_ms = 0

    def poll(self, until_ms=None):
        pass

    def snapshot(self, pool, candles=True):
        return {"id": pool, "mint": "M", "price": 1.0, "quote_wsol": True, "reserves": {"quote_eff_sol": 100.0}}

    def pool(self, pid):
        return None

    def pools_wsol(self, pred=None):
        return []


def fake_runner(tmp, engine=None):
    root = tmp / "v6"
    cfg = R.RunnerConfig(root=str(root), frozen_sha="abcdef1234567")
    return R.Runner(cfg, FakeState(), engine or FakeEngine(), A.StubModel(script), FakeChain(), None)


CARD = {"id": "POOLX", "mint": "M", "f": {"age_tier": "established", "quote_eff_sol": 200.0, "age_s": 99999.0}}
PLAN = {"stop_pct": -10.0, "target_pct": 20.0, "time_min": 60}


def total(run, arm="L"):
    return sum(run.books[arm].realized.values())


# ------------------------------------------------------------------------------------------------------------------------ 4. resume
def test_resume_counts_pnl_once_keeps_refused_position_and_recovers(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("r6res")
    eng = FakeEngine()
    r1 = fake_runner(tmp, eng)
    pid = r1._open("L", CARD, 0.25, PLAN, T0, {}, {})
    pos = r1.books["L"].open[pid]
    ret = R.mark_ret(pos, r1.state.snapshot("POOLX"))
    est = ret * pos.size
    eng.refuse = [("sell", "no ... slower entry leg yet"), ("sell", "FOREIGN-LINE bot hv9.x refused")]
    assert not r1._sell(pos, "time", T0 + 10_000, ret)
    assert not r1._sell(pos, "time", T0 + 12_000, ret)
    assert total(r1) == 0 and pid in r1.books["L"].open
    # restart #1: after two refused attempts (two sell rows, two refusal rows)
    r2 = fake_runner(tmp, eng)
    n = r2.resume()
    assert eng.recovered == 1 and pid in r2.books["L"].open and total(r2) == 0 and n["open"] == 1
    assert r2._sell(r2.books["L"].open[pid], "time", T0 + 20_000, ret) and eng.sold == [pid]
    assert total(r2) == pytest.approx(est)
    # restart #2: after the fill, a third sell row exists; the P&L is counted once, the position is closed
    r3 = fake_runner(tmp, eng)
    r3.resume()
    assert eng.recovered == 2 and not r3.books["L"].open and total(r3) == pytest.approx(est)
    # QP 918 (7): no engine exception text anywhere in the log
    blob = json.dumps([r for a in (*R.ARM_ORDER, "SCAN") for r in r3.log.rows(a)])
    assert "FOREIGN" not in blob and "slower entry leg" not in blob
    refused = [r for r in r3.log.rows("L") if r["kind"] == "order_refused"]
    assert len(refused) == 2 and all(set(r) >= {"error_class", "reason"} and "detail" not in r for r in refused)


def test_unpriced_sell_is_reopened_after_restart(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("r6reo")
    eng = FakeEngine()
    r1 = fake_runner(tmp, eng)
    pid = r1._open("L", CARD, 0.25, PLAN, T0, {}, {})
    r1.log.append("L", T0 + 5000, {"kind": "sell", "pid": pid, "pool": "POOLX", "reason": "time", "est_pnl_sol": 0.01})  # sent, the engine never priced it
    r2 = fake_runner(tmp, eng)
    assert r2.resume()["reopened"] == 1 and pid in r2.books["L"].open


def test_buy_refusal_logs_class_only_and_leaves_no_entry(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("r6buy")
    eng = FakeEngine()
    eng.refuse = [("buy", "FOREIGN-LINE")]
    r = fake_runner(tmp, eng)
    assert r._open("L", CARD, 0.25, PLAN, T0, {}, {}) is None
    assert "FOREIGN" not in json.dumps(r.log.rows("L")) and not r.books["L"].entries_ms
    r2 = fake_runner(tmp, eng)
    r2.resume()
    assert not r2.books["L"].open and not r2.books["L"].entries_ms


# ------------------------------------------------------------------------------------------------------------------------ 5. plans, probes, WATCH
def verdict(action, **fields):
    return A.Verdict("POOLX", action, fields)


def test_only_the_validated_plan_is_executed(tmp_path_factory):
    r = fake_runner(tmp_path_factory.mktemp("r6plan"))
    r._on_verdict("L", verdict("BUY", size=0.25, conviction=4, plan={"stop_pct": -10.0, "target_pct": 20.0}), CARD, T0)
    pos = next(iter(r.books["L"].open.values()))
    assert (pos.stop_pct, pos.target_pct, pos.trail_pct, pos.time_min) == (-10.0, 20.0, None, None)  # nothing filled from -25/+100/30/360
    r._on_verdict("L-nr", verdict("BUY", size=0.25, conviction=4), {**CARD, "id": "POOLY"}, T0)
    p2 = next(iter(r.books["L-nr"].open.values()))
    assert (p2.stop_pct, p2.target_pct, p2.trail_pct, p2.time_min) == (None, None, None, None)


def test_x_uses_x_exit(full):
    run, _ = full
    p = rows_of(run, "X", "entry")[0]["plan"]
    assert p == A.x_exit()


def test_probe_trades_are_capped_at_025_and_tagged(tmp_path_factory):
    r = fake_runner(tmp_path_factory.mktemp("r6prb2"))
    v = A.RegimeRuleVerdict(True, None, {"extra_entries_per_h": 0, "target": "stop_pct"}, "probe", "regime_probe", T0 + 3_600_000, True)
    assert r.probes.add("L", v, T0)
    r._on_verdict("L", verdict("BUY", size=1.0, conviction=4, plan=PLAN), CARD, T0)
    assert r.books["L"].open and next(iter(r.books["L"].open.values())).size == A.PROBE_STAKE == 0.25
    assert rows_of(r, "L", "entry")[0]["tag"] == "regime_probe" and next(iter(r.books["L"].open.values())).probe
    r._on_verdict("F", verdict("BUY", size=1.0, conviction=4, plan=PLAN), CARD, T0)  # F has no probe: 1.0 stays
    assert next(iter(r.books["F"].open.values())).size == 1.0 and rows_of(r, "F", "entry")[0]["tag"] is None
    r.probes.by_arm["P"] = [{"junk": 1}]  # a malformed record still caps and tags (fail closed)
    assert r.probes.active("P", T0)


def test_watch_is_added_capped_expires_and_survives_restart(tmp_path_factory):
    assert R.WATCH_MAX_PER_ARM == hv4_arms.WATCH_MAX_PER_ARM
    tmp = tmp_path_factory.mktemp("r6wat")
    r = fake_runner(tmp)
    w = {"terms": [["ret_15m", ">", 1.0]], "when": "ret_15m > 1", "ttl_min": 30}
    for i in range(R.WATCH_MAX_PER_ARM + 1):
        r._on_verdict("L", verdict("WATCH", watch=w, conviction=3), {**CARD, "id": f"W{i}"}, T0 + i, T0)
    assert len(r.books["L"].watches) == R.WATCH_MAX_PER_ARM and rows_of(r, "L", "skip")[-1]["reasons"] == ["watch_cap"]
    r2 = fake_runner(tmp)
    r2.resume()
    assert len(r2.books["L"].watches) == R.WATCH_MAX_PER_ARM
    r2.watches(T0 + 31 * MIN)
    assert not r2.books["L"].watches and len(rows_of(r2, "L", "watch_end")) == R.WATCH_MAX_PER_ARM


def test_watch_fires_into_a_fresh_card_and_can_buy(tmp_path_factory):
    def watch_then_buy(req):
        if req.kind == "entry" and req.arm == "L":
            if len(req.ids) == 1 and req.seq > 0:
                return reply(buy(req.ids[0], "BIG"))
            return reply(*[{"id": i, "action": "WATCH", "conviction": 3, "watch": {"when": "ret_15m > -1000", "ttl_min": 30},
                            "why": "net_flow_60m is positive and ret_15m is a shallow dip"} if i in tagged_ids(req) else skip(i) for i in req.ids])
        return A.m4.default_reply(req)

    run = make(tmp_path_factory.mktemp("r6wf"), model_script=watch_then_buy)
    run.run(T0 + 9 * MIN + 2000, T0 + 40 * MIN)
    assert rows_of(run, "L", "watch_add") and any(r["why"] == "fired" for r in rows_of(run, "L", "watch_end"))
    assert rows_of(run, "L", "entry"), "the watch's second look bought"
    assert run.counters["watch_added.L"] >= 1 and run.counters["watch_fired.L"] >= 1


# ------------------------------------------------------------------------------------------------------------------------ 8. cooldown, min age
def test_refused_card_does_not_burn_the_cooldown(tmp_path_factory):
    class NoFacts:
        def chain_part(self, *a):
            return None

    run = make(tmp_path_factory.mktemp("r6cd"), chain=NoFacts())
    run.run(T0 + 9 * MIN + 2000, T0 + 12 * MIN)
    assert run.limiter._last == {} and all(r["tagged"] == [] for r in rows_of(run, "SCAN", "scan"))


def test_check_min_age_uses_the_smaller_age_on_a_real_built_card(full):
    run, base = full
    day = next((Path(run.cfg.root) / "cards").glob("*.jsonl"))
    card = json.loads(day.read_text().splitlines()[0])["card"]
    assert card["f"]["age_s"] >= 7200
    A.check_min_age([card])
    with pytest.raises(A.PromptError):
        A.check_min_age([{**card, "age_h": 0.5}])  # age_h cannot override a smaller f.age_s ... and a smaller age_h wins
    with pytest.raises(A.PromptError):
        A.check_min_age([{**card, "age_h": 99.0, "f": {**card["f"], "age_s": 100.0}}])
    with pytest.raises(A.PromptError):
        A.check_min_age([{**card, "f": {**card["f"], "age_s": None}}])
