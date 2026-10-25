"""The prefetcher watch-set cap (WATCH_MAX_POOLS) never cuts a pool the book still needs (QP 930). Synthetic tape; a chain that, like the prefetcher, serves facts only
for pools watched at an earlier scan. No real tape, no API, no RPC."""

from __future__ import annotations

import json

import pytest

from tools import hv4_cards as C
from tools import hv6_memscale as MS
from tools import hv6_runner as R
from tools.test_hv6_runner import _env  # noqa: F401  (autouse: path allowlist for tmp dirs)

MIN = 60_000


@pytest.fixture(scope="module")
def tape(tmp_path_factory):
    d = tmp_path_factory.mktemp("r6wc")
    MS.write_tape(d / "tape", MS.T0, 1, 1.0, 120_000, seed=7, pre_hours=4)
    return d / "tape"


class GatedChain:
    def __init__(self):
        self.last, self.calls = {}, []

    def watch(self, items, hot, now_ms, vols=None):
        ids = [p for p, _ in items]
        self.calls.append((now_ms, ids, set(hot)))
        for p in ids:
            self.last[p] = now_ms

    def chain_part(self, pool, mint, now_ms, slot):
        seen = self.last.get(pool)
        if seen is None or seen >= now_ms or now_ms - seen > 30 * MIN:
            return None
        return {"as_of_ms": now_ms - 1000, "as_of_slot": slot, "mint_authority": None, "freeze_authority": None, "token_program": C.SPL_TOKEN_PROGRAM,
                "extensions": [], "mayhem": False, "top1_frac": 0.05, "top10_frac": 0.30, "holders_as_of_ms": now_ms - 1000}


def go(tmp_path_factory, tape, cap, minutes, prep=None):
    run = MS.build_runner(tmp_path_factory.mktemp("r6wcr"), tape, bound=True)
    run.chain = GatedChain()
    seen = []
    orig = R.H.scan

    def spy(*a, **k):
        res = orig(*a, **k)
        seen.append(list(res.candidates))
        return res

    R.H.scan, old = spy, R.WATCH_MAX_POOLS
    R.WATCH_MAX_POOLS = cap
    try:
        if prep:
            prep(run)
        run.run(MS.T0 + 2000, MS.T0 + minutes * MIN)
    finally:
        R.H.scan, R.WATCH_MAX_POOLS = orig, old
    return run, seen


def pos_for(pool):
    return R.Pos(arm="L", pid="held" + pool[-6:], pool=pool, mint=None, size=0.25, entry_ms=MS.T0, entry_price=1.0, tokens_eq=1.0, stop_pct=None, target_pct=None,
                 trail_pct=None, time_min=None, hard_stop_pct=-99.0, max_hold_ms=10 ** 12, young=False)


def test_open_position_and_arm_watch_pools_are_never_cut(tmp_path_factory, tape):
    _, seen = go(tmp_path_factory, tape, 10 ** 6, 12)
    cands = seen[-1]
    assert len(cands) > 40, "the fixture needs more candidates than the hot set"
    probe_run, _ = go(tmp_path_factory, tape, 10 ** 6, 12)
    vol_rank = [p for p in probe_run.chain.calls[-1][1]]
    cold = vol_rank[-2:]  # candidate order is stable; any two pools work, they just must not be in the hot set
    hot_last = probe_run.chain.calls[-1][2]
    cold = [p for p in vol_rank if p not in hot_last][-2:]

    def prep(run):
        run.books["L"].open["held"] = pos_for(cold[0])                                              # an open position past the cap
        run.books["F"].watches.append({"id": "w1", "pool": cold[1], "terms": [["ret_15m", ">", 1.0]], "when": "ret_15m > 1", "expires_ms": MS.T0 + 10 ** 10,
                                       "card_sha256": "x"})                                           # an active arm watch past the cap

    run, _ = go(tmp_path_factory, tape, 3, 12, prep)
    assert run.counters.get("scans_cap_bound", 0) > 0 and run.counters.get("watch_capped", 0) > 0
    now, ids, _ = run.chain.calls[-1]
    assert cold[0] in ids and cold[1] in ids and len(ids) < len(cands)                                # kept outside the cap, and the cap still cut others
    for p in cold:
        assert run.chain.chain_part(p, None, now + 1000, 0) is not None                               # its facts are still served
    assert any(r["watch_capped"] > 0 for r in run.log.rows("SCAN") if r["kind"] == "scan")           # logged per scan
    assert run.counters["max_candidates_per_scan"] >= len(cands) - 5


def test_a_cap_that_cuts_only_cold_pools_changes_no_decision(tmp_path_factory, tape):
    big, _ = go(tmp_path_factory, tape, 10 ** 6, 25)
    small, _ = go(tmp_path_factory, tape, 5, 25)
    assert big.counters.get("scans_cap_bound", 0) == 0 and small.counters.get("scans_cap_bound", 0) > 0
    strip = lambda run, name: [{k: v for k, v in r.items() if k not in ("watch_capped", "seq", "prev", "sha")} for r in run.log.rows(name)]  # noqa: E731
    for name in (*R.ARM_ORDER, "SCAN"):
        assert strip(big, name) == strip(small, name), name
    assert json.dumps(strip(big, "SCAN")) == json.dumps(strip(small, "SCAN")) and not small.hook_errors
