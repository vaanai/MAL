"""HV7 runner memory bound: growth without it, a bound with it, byte-identical decision logs either way. SYNTHETIC scaled tape (tools.hv6_memscale writer) only.
No real tape, no API, no network. Nothing here says HV7 makes money."""
from __future__ import annotations

import tracemalloc

import pytest

from tools import hv6_agents as A
from tools import hv6_memscale as MS
from tools import hv7_runner as R
from tools.arena_paper import ArenaEngine, register_bot
from tools.arena_state import ArenaState
from tools.test_hv4_runner import FakeChain
from tools.test_hv7_runner import _env, rows_of  # noqa: F401  (_env is autouse)

HOURS, PRE, PRINTS_PER_HOUR = 4, 3, 250_000      # about 1.75M tape rows over 7 hours, 4 simulated


def _script(req):
    import hashlib
    import json

    order = sorted(req.ids, key=lambda i: hashlib.sha256(i.encode()).hexdigest())
    return json.dumps([{"id": i, "why": "f.vol_60m is highest"} for i in order])


def _build(base, tape, bound):
    root = base / "v7"
    guard = R.Hv7Guard(root)
    model = A.StubModel(_script, guard=guard, latency_ms=50)
    reg = base / "reg.json"
    eng = ArenaEngine(base / "eng", reg, cfg=R.stack_config(2), sps_fn=lambda: 0.4)
    for arm in R.ARM_ORDER:
        register_bot(R.bot_id(arm), "abcdef1234567", MS.T0 - 60 * MS.MIN, str(reg), deciding_stake=0.25, model_ids=[A.MODEL_ID] if arm == "L" else [],
                     now_ms=MS.T0 - 61 * MS.MIN, verify_sha=False)
    cfg = R.Hv7Config(root=str(root), frozen_sha="abcdef1234567", clock_start_ms=MS.T0 - 60 * MS.MIN, bound_memory=bound)
    return R.Hv7Runner(cfg, ArenaState(tape, start_ms=0), eng, model, FakeChain(), guard)


def _held(run):
    return sum(len(h.slot) for h in run.engine.pools.values()), sum(len(v) for v in run.quotes.t.values())


@pytest.fixture(scope="module")
def tape(tmp_path_factory):
    d = tmp_path_factory.mktemp("h7memtape") / "tape"
    gen = MS.write_tape(d, MS.T0, HOURS, 3.0, PRINTS_PER_HOUR, seed=5, pre_hours=PRE)
    return d, gen


def test_unbounded_growth_before_and_bounded_after_with_identical_decisions(tape, tmp_path_factory):
    d, gen = tape
    out = {}
    for bound in (False, True):
        run = _build(tmp_path_factory.mktemp("h7mem"), d, bound)
        tracemalloc.start()
        run.run(MS.T0 + 2000, MS.T0 + HOURS * MS.HOUR)
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        out[bound] = {"peak": peak, "held": _held(run), "md5": run.decision_md5(), "run": run}
    on, off = out[True], out[False]
    assert gen["prints"] + gen["pools"] >= 1_000_000                                   # scaled: over a million synthetic rows
    assert on["md5"] == off["md5"]                                                      # decision log byte-identical
    assert len(rows_of(on["run"], "SCAN", "period")) > 0 and not on["run"].hook_errors and not off["run"].hook_errors
    assert off["run"].counters.get("engine_trims", 0) == 0                              # before: nothing ever trimmed
    assert on["run"].counters["engine_trims"] > 0 and on["run"].counters["engine_prints_trimmed"] > 0
    assert off["held"][0] > 0.9 * gen["prints"] * 0.5                                   # before: the engine keeps (nearly) every print it saw in the window
    assert on["held"][0] < 0.5 * off["held"][0]                                         # after: well under half the prints held
    assert on["peak"] < 0.75 * off["peak"], (on["peak"], off["peak"])                   # tracemalloc peak bytes drop
    print("MEM", {"prints_generated": gen["prints"], "held_off": off["held"], "held_on": on["held"], "peak_mb_off": off["peak"] / 2**20, "peak_mb_on": on["peak"] / 2**20})
