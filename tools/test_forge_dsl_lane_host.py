"""Tests for tools/forge/dsl_lane_host.py. Synthetic tape only; no real tape is read, no key, no network."""
from __future__ import annotations

import json
import random
import subprocess
from pathlib import Path

import numpy as np
import pytest

from tools import arena_rules as R
from tools.arena_declog import DecisionLog
from tools.forge import common as C
from tools.forge import dsl_lane_host as D
from tools.forge import evaluate as E
from tools.forge import events as EV
from tools.test_forge import curve_row, ps_row, write_tape

H0 = "2026-11-04T10"
HS0 = C.hour_ms(H0)
EVENT_HOURS = ["2026-11-04T10", "2026-11-04T11", "2026-11-04T12"]
TAPE_HOURS = C.hour_range("2026-11-04T04", "2026-11-04T14")
SPEC = {"entry": [{"f": "n_1m", "op": ">=", "v": 4}, {"f": "ret_1m", "op": "<", "v": 0.02}], "venue": "any", "cooldown_s": 90, "one_per_pool": True,
        "exit": {"tp": 0.06, "sl": 0.05, "trail": 0.04, "max_hold_s": 900}, "stake_sol": 0.03}
LAST_T = [0]
CUT = HS0 + 3 * 3_600_000 - 1_000_000  # events must have 1000 s of tape after them and lie in the evaluator's event hours


def synth_rows(seed=7, n_pools=6, gap=True):
    """Random-walk PumpSwap pools (the base reserve walks, so the spot walks) plus one curve pool that completes; ms jitter so no two rows tie."""
    rnd = random.Random(seed)
    rows = []
    st = {f"Pool{i}": {"b": 1e15, "q": rnd.choice([8e9, 12e9, 20e9]), "mint": f"Mint{i}"} for i in range(n_pools)}
    t = HS0
    end = HS0 + 3 * 3_600_000 + 20 * 60_000
    k = 0
    while t < end:
        t += rnd.randint(300, 2500)
        k += 1
        pn = rnd.choice(list(st))
        s = st[pn]
        side = rnd.choice(["buy", "sell"])
        drift = 1.0 + rnd.gauss(0, 0.004) + (0.02 if rnd.random() < 0.01 else 0) - (0.03 if rnd.random() < 0.01 else 0)
        s["b"] = max(1e13, s["b"] / drift)
        rows.append(ps_row(t, side, b=s["b"], q=s["q"], sol=rnd.randint(1, 50) * 1_000_000, pool=pn, mint=s["mint"], trader=f"T{rnd.randint(0, 5)}"))
        if k % 5 == 0:
            rows.append(curve_row(t + 1, rnd.choice(["buy", "sell"]), q=30e9 + (k % 40) * 1e8, b=1.073e15 - (k % 40) * 1e12, mint="CurveM", trader=f"T{rnd.randint(0, 5)}", rq=5e9))
    if gap:
        g0 = HS0 + 3_600_000 + 20 * 60_000
        rows.append({"type": "gap", "prog": "pumpswap", "t_recv_ms": g0, "data_loss": True, "from_ms": g0, "to_ms": g0 + 40_000, "kind": "slot_jump"})
        rows = [r for r in rows if not (r.get("venue") == "pumpswap" and g0 < r["t_recv_ms"] < g0 + 40_000)]
    rows.append({"type": "complete", "t_recv_ms": HS0 + 3_600_000 + 30 * 60_000, "mint": "CurveM"})
    LAST_T[0] = max(r["t_recv_ms"] for r in rows)
    return rows


def write_tapes(tape: Path, rows):
    tape.mkdir(exist_ok=True)
    byh: dict[str, list] = {}
    for r in rows:
        byh.setdefault(C.hour_str(r["t_recv_ms"]), []).append(r)
    for h, rr in byh.items():
        write_tape(tape, h, rr)


def run_engine(tape: Path, spec, since=EVENT_HOURS[0], hook=None, live_from=0, lookback=None):
    sink = D.ListSink()
    eng = D.Engine(spec, sink, live_from_ms=live_from, gap_lookback_s=lookback)
    if hook:
        eng.feat_hook = hook
    for ln in D.TapeFollower(tape, C.hour_ms(since)).read():
        eng.feed_line(ln)
    return eng, sink


def batch(tmp_path, tape: Path):
    out = str(tmp_path / "out")
    for h in TAPE_HOURS:
        EV.ingest_hour(h, str(tape), out)
    fs = EV.first_seen(out, TAPE_HOURS)
    for h in EVENT_HOURS:
        EV.write_hour(h, out, fs)
    return out, E.Split(out, EVENT_HOURS)


@pytest.fixture()
def world(tmp_path):
    tape = tmp_path / "tape"
    write_tapes(tape, synth_rows())
    out, data = batch(tmp_path, tape)
    return tmp_path, tape, out, data


def test_features_equal_batch(world):
    tmp_path, tape, out, data = world
    got = {}
    run_engine(tape, SPEC, hook=lambda ph, t, x: got.__setitem__((ph, t), x))
    n = 0
    fi = C.FIDX
    exact = np.array([f not in ("dd_high", "rise_low") for f in C.FEATURES])
    near = [fi["dd_high"], fi["rise_low"]]
    for i in range(data.n):
        k = (int(data.pool[i]), int(data.t[i]))
        if k not in got:
            continue
        n += 1
        a, b = data.X[i], got[k]
        np.testing.assert_array_equal(np.nan_to_num(a[exact], nan=-7), np.nan_to_num(b[exact], nan=-7))
        # the batch adds a rank offset (rank * 1e4) before its running max / min; the host has one pool, so these two may differ by float noise before the float32 cast
        np.testing.assert_allclose(a[near], b[near], rtol=1e-5, atol=1e-6)
    assert n > 200


def evaluator_trades(data, spec):
    r = E.run_trades(data, E.validate_spec(spec))
    return sorted((int(p), int(t), int(x), E.REASONS[int(c)]) for p, t, x, c in zip(r["pool"], r["t"], r["exit_t"], r["reason"]))


def host_trades(sink):
    ent, ex = {}, {}
    for kind, _ms, b in sink.rows:
        if kind == "entry":
            ent[b["order_id"]] = b
        elif kind == "exit":
            ex[b["order_id"]] = b
    out = []
    for oid, b in ent.items():
        if oid in ex and ex[oid]["reason"] != "no_fill":
            ph = EV._h64("p:" + b["pool"]) if b["meta"]["venue"] == 1 else EV._h64(b["meta"]["mint"])
            out.append((ph, b["meta"]["t_event_ms"], ex[oid]["exec_ms"], ex[oid]["reason"]))
    return sorted(out)


def compare(tape, data, spec, guard_ms, lookback=60):
    cut = min(LAST_T[0] - guard_ms, HS0 + 3 * 3_600_000)  # the evaluator table covers the three event hours only
    want = [w for w in evaluator_trades(data, spec) if w[1] < cut]
    eng, sink = run_engine(tape, spec, lookback=lookback)
    got = [g for g in host_trades(sink) if g[1] < cut]
    return want, got, eng, sink


def test_equivalence_entries_exits_reasons(world):
    tmp_path, tape, out, data = world
    want, got, eng, sink = compare(tape, data, SPEC, 1_000_000)
    assert len(want) >= 15, len(want)
    assert got == want
    assert len({w[3] for w in want}) >= 2  # more than one exit reason is exercised


@pytest.mark.parametrize("spec", [
    {**SPEC, "exit": {"tp": 0.03, "sl": None, "trail": None, "max_hold_s": 300}},
    {**SPEC, "exit": {"tp": None, "sl": 0.02, "trail": 0.03, "max_hold_s": 600}, "cooldown_s": 0},
    {**SPEC, "one_per_pool": False, "cooldown_s": 200, "exit": {"tp": 0.1, "sl": 0.1, "trail": None, "max_hold_s": 120}},
    {**SPEC, "venue": 1, "entry": [{"f": "buy_share_1m", "op": ">", "v": 0.5}]},
    {**SPEC, "venue": 0, "entry": [{"f": "n_1m", "op": ">=", "v": 2}], "exit": {"tp": 0.5, "sl": None, "trail": None, "max_hold_s": 3000}},
])
def test_equivalence_spec_variants(world, spec):
    tmp_path, tape, out, data = world
    want, got, eng, sink = compare(tape, data, spec, 4_000_000)
    assert len(want) >= 5, len(want)
    assert got == want


def test_curve_migration_reason_is_exercised(world):
    tmp_path, tape, out, data = world
    spec = {**SPEC, "venue": 0, "entry": [{"f": "n_1m", "op": ">=", "v": 2}], "cooldown_s": 600, "exit": {"tp": None, "sl": None, "trail": None, "max_hold_s": 3000}}
    want, got, eng, sink = compare(tape, data, spec, 4_000_000)
    assert any(w[3] == "migrated" for w in want)
    assert got == want


def test_gap_lookback_refuses_and_keeps_subset(world):
    tmp_path, tape, out, data = world
    want, got60, _, _ = compare(tape, data, SPEC, 1_000_000)
    eng, sink = run_engine(tape, SPEC, lookback=3600)
    refused = [b for k, _, b in sink.rows if k == "refused"]
    got = [g for g in host_trades(sink) if g[1] < CUT]
    assert eng.counters.get("entries_refused_gap", 0) > 0 and refused and refused[0]["kind"] == "entry_refused_gap"
    assert set(got) < set(got60)  # a strict subset of the evaluator-equal book
    assert all(g in want for g in got)


def test_before_live_from_is_shadow_no_rows(world):
    tmp_path, tape, out, data = world
    lf = HS0 + 2 * 3_600_000
    eng, sink = run_engine(tape, SPEC, live_from=lf, lookback=60)
    assert eng.counters["shadow_entries"] > 0
    assert all(b["meta"]["t_event_ms"] >= lf for k, _, b in sink.rows if k == "entry")
    want = [x for x in evaluator_trades(data, SPEC) if lf <= x[1] < CUT]  # the shadow state before lf is rebuilt from the tape
    assert [x for x in host_trades(sink) if x[1] < CUT] == want


def test_no_fill_exit_row(tmp_path):
    tape = tmp_path / "tape"
    rows = [ps_row(HS0, "buy", b=1e15, q=10e9, sol=1_000_000)]
    rows.append(ps_row(HS0 + 20 * 60_000, "buy", b=1e15, q=10e9))  # the next print comes after 10 min: the earlier events have no fill
    write_tapes(tape, rows)
    eng, sink = run_engine(tape, {**SPEC, "entry": [], "cooldown_s": 0}, lookback=60)
    assert "no_fill" in {b["reason"] for k, _, b in sink.rows if k == "exit"}


# ---------------------------------------------------------------------------------------------- follower, log, safety
def test_follower_incremental_and_zst_switch(tmp_path):
    tape = tmp_path / "tape"
    tape.mkdir()
    f = tape / f"tape-{H0}.jsonl"
    f.write_bytes(b'{"a":1}\n{"a":2}\n{"a":')
    fol = D.TapeFollower(tape, HS0)
    assert fol.read() == [b'{"a":1}\n', b'{"a":2}\n']  # the torn last line waits
    f.write_bytes(b'{"a":1}\n{"a":2}\n{"a":3}\n')
    assert fol.read() == [b'{"a":3}\n']
    subprocess.run(["zstd", "-q", str(f)], check=True)
    f.unlink()
    assert fol.read() == []  # the same three lines, none new
    (tape / f"tape-{C.hour_str(HS0 + 3_600_000)}.jsonl").write_bytes(b'{"b":1}\n')
    assert fol.read() == [b'{"b":1}\n']


def test_follower_missing_hour_is_a_gap_line(tmp_path):
    tape = tmp_path / "tape"
    tape.mkdir()
    (tape / f"tape-{H0}.jsonl").write_bytes(b'{"a":1}\n')
    (tape / f"tape-{C.hour_str(HS0 + 2 * 3_600_000)}.jsonl").write_bytes(b'{"b":1}\n')
    lines = D.TapeFollower(tape, HS0).read()
    g = json.loads(lines[1])
    assert len(lines) == 3 and g["data_loss"] is True and g["prog"] == "all" and g["from_ms"] == HS0 + 3_600_000


def cfg_for(tmp_path, tape):
    spec_p = tmp_path / "spec.json"
    spec_p.write_text(json.dumps(SPEC))
    return {"root": str(tmp_path / "root"), "tape_dir": str(tape), "bot_id": "dsl-test-1", "spec": str(spec_p), "clock_start_ms": HS0, "gap_lookback_s": 60}


def test_run_refuses_dirty_checkout():
    with pytest.raises(R.HostError):
        D.run_binding(hb={"git_sha": "a" * 40, "dirty": True})
    with pytest.raises(R.HostError):
        D.run_binding(hb={"git_sha": None, "dirty": None})
    b = D.run_binding(hb={"git_sha": "a" * 40, "dirty": False})
    assert b["git_sha"] == "a" * 40 and set(D.PIN_FILES) == set(b["pins"])


def test_register_pins_and_mismatch(tmp_path):
    tape = tmp_path / "tape"
    tape.mkdir()
    d = cfg_for(tmp_path, tape)
    reg = D.register(d)
    assert reg["spec_sha256"] == D.spec_hash(E.validate_spec(SPEC))
    with pytest.raises(R.HostError):
        D.register(d)  # already registered
    D.check_registry(d)
    other = tmp_path / "other.json"
    other.write_text(json.dumps({**SPEC, "stake_sol": 0.05}))
    with pytest.raises(R.HostError):
        D.check_registry({**d, "spec": str(other)})
    p = Path(d["root"]) / "registry.json"
    r = json.loads(p.read_text())
    r["pins"]["tools/forge/events.py"] = "0" * 64
    p.write_text(json.dumps(r))
    with pytest.raises(R.HostError):
        D.check_registry(d)


def test_arena_output_root_refused(tmp_path):
    cfg = tmp_path / "c.json"
    cfg.write_text(json.dumps({"root": "/data/mal/arena/x", "tape_dir": "/t", "bot_id": "a", "spec": "s", "clock_start_ms": 1}))
    with pytest.raises(R.HostError):
        D.load_cfg(str(cfg))


def test_host_writes_chain_start_row_and_restart_guards(world):
    tmp_path, tape, out, data = world
    d = cfg_for(tmp_path, tape)
    hb = {"git_sha": "b" * 40, "dirty": False}
    host = D.Host(d, binding=D.run_binding(hb=hb), live=True, since_ms=HS0, live_from_ms=HS0, now_fn=lambda: HS0)
    while host.step():
        pass
    host.checkpoint()
    log = DecisionLog(d["root"])
    rows = log.rows("dsl-test-1")
    assert rows[0]["kind"] == "start" and rows[0]["frozen_sha"] == "b" * 40 and rows[0]["spec_sha256"] == D.spec_hash(E.validate_spec(SPEC))
    assert {"entry", "exit"} <= {r["kind"] for r in rows}
    ent = next(r for r in rows if r["kind"] == "entry")
    assert {"order_id", "pool", "meta", "decision_slot", "stake_sol", "attempt_key"} <= set(ent) and ent["stake_sol"] == 0.03 and ent["meta"]["rule"] == "dsl"
    _, seq = log.verify("dsl-test-1")
    assert seq == len(rows) - 1
    if D.open_in_log(log, "dsl-test-1"):  # a restart with a position still open is refused
        with pytest.raises(R.HostError):
            D.Host(d, binding=D.run_binding(hb=hb), live=True, since_ms=HS0, now_fn=lambda: HS0 + 10 ** 9)
    (Path(d["root"]) / "dsl_host" / "checkpoint.json").unlink()  # a log without its checkpoint is refused
    with pytest.raises(R.HostError):
        D.Host(d, binding=D.run_binding(hb=hb), live=True, since_ms=HS0, now_fn=lambda: HS0 + 10 ** 9)


def test_restart_records_host_down_span_and_refuses_entries_in_it(tmp_path):
    tape = tmp_path / "tape"
    write_tapes(tape, synth_rows(gap=False))
    d = cfg_for(tmp_path, tape)
    hb = {"git_sha": "c" * 40, "dirty": False}
    t_end = HS0 + 3_600_000
    host = D.Host(d, binding=D.run_binding(hb=hb), live=True, since_ms=HS0, live_from_ms=HS0, now_fn=lambda: HS0)
    while host.step(t_end):
        pass
    host.checkpoint()
    log = DecisionLog(d["root"])
    for oid, row in D.open_in_log(log, "dsl-test-1").items():  # the operator waits for open positions to close before a restart
        log.append("dsl-test-1", t_end, {"kind": "exit", "order_id": oid, "pool": row["pool"], "reason": "time", "decision_slot": 0, "attempt": 1})
    ck = json.loads((Path(d["root"]) / "dsl_host" / "checkpoint.json").read_text())
    down_to = t_end + 30 * 60_000
    host2 = D.Host(d, binding=D.run_binding(hb=hb), live=True, since_ms=HS0, now_fn=lambda: down_to)
    span = host2.down[-1]
    assert span["kind"] == "host_down" and span["to_ms"] == down_to and span["from_ms"] == ck["hwm_ms"]
    assert host2.engine._gap_refusal(down_to - 10_000, 1) == {"prog": "all", "kind": "host_down", "from_ms": span["from_ms"], "to_ms": down_to}
