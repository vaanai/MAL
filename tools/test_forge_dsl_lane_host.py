"""Tests for tools/forge/dsl_lane_host.py. Synthetic tape only; no real tape is read, no key, no network."""
from __future__ import annotations

import json
import os
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
    fol = D.TapeFollower(tape, C.hour_ms(since))
    while True:
        lines = fol.read()
        if not lines:
            break
        for ln in lines:
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


def test_before_live_from_is_shadow_no_rows(world):
    tmp_path, tape, out, data = world
    lf = HS0 + 2 * 3_600_000
    eng, sink = run_engine(tape, SPEC, live_from=lf, lookback=60)
    assert eng.counters["shadow_entries"] > 0
    assert all(b["meta"]["t_event_ms"] >= lf for k, _, b in sink.rows if k == "entry")
    want = [x for x in evaluator_trades(data, SPEC) if lf <= x[1] < CUT]  # the shadow state before lf is rebuilt from the tape
    assert [x for x in host_trades(sink) if x[1] < CUT] == want


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


def test_cli_register_replay_verify(world, capsys):
    tmp_path, tape, out, data = world
    d = cfg_for(tmp_path, tape)
    cfgp = tmp_path / "cfg.json"
    cfgp.write_text(json.dumps(d))
    assert D.main(["register", "--config", str(cfgp)]) == 0
    assert D.main(["replay", "--config", str(cfgp), "--since-hour", H0, "--from-ms", str(HS0), "--to-ms", str(HS0 + 3 * 3_600_000)]) == 0
    assert D.main(["verify", "--config", str(cfgp)]) == 0
    assert D.main(["replay", "--config", str(cfgp)]) == 2  # --since-hour missing
    rows = DecisionLog(d["root"]).rows("dsl-test-1")
    assert any(r["kind"] == "entry" for r in rows)



def test_gap_lookback_refuses_entries_and_holds_the_pool(world):
    tmp_path, tape, out, data = world
    want, got60, _, _ = compare(tape, data, SPEC, 1_000_000)
    eng, sink = run_engine(tape, SPEC, lookback=3600)
    refused = [b for k, _, b in sink.rows if k == "refused"]
    got = [g for g in host_trades(sink) if g[1] < CUT]
    assert eng.counters.get("entries_refused_gap", 0) > 0 and refused and refused[0]["kind"] == "entry_refused_gap"
    assert set(got) < set(got60)  # fewer entries than the 60 s book; a refused entry is still tracked as a shadow position
    assert all(g in want for g in got)  # on this tape (gap row announced at its start) no entry appears that the evaluator lacks


def extra_rows():
    """The synthetic tape plus pools that make the evaluator drop events the host could otherwise enter: PoolNF prints every 15 min (no fill within 10 min);
    PoolLQ alternates a 3 SOL and a 1 SOL real quote, so the event row passes the 2 SOL floor and the fill state does not."""
    rows = synth_rows()
    t = HS0 + 5_000
    while t < HS0 + 3 * 3_600_000:
        rows.append(ps_row(t, "buy", b=1e15, q=10e9, sol=1_000_000, pool="PoolNF", mint="MintNF"))
        t += 900_000
    t = HS0 + 7_000
    k = 0
    while t < HS0 + 3 * 3_600_000:
        rows.append(ps_row(t, "buy", b=1e15, q=3e9 if k % 2 == 0 else 1e9, sol=1_000_000, pool="PoolLQ", mint="MintLQ"))
        t += 3_100
        k += 1
    LAST_T[0] = max(r["t_recv_ms"] for r in rows)
    return rows


def test_host_only_entries_are_zero_with_nofill_and_floor_cases(tmp_path):
    tape = tmp_path / "tape"
    write_tapes(tape, extra_rows())
    out, data = batch(tmp_path, tape)
    spec = {**SPEC, "entry": [], "cooldown_s": 0, "exit": {"tp": 0.05, "sl": 0.05, "trail": None, "max_hold_s": 120}}
    want = {(w[0], w[1]) for w in evaluator_trades(data, spec) if w[1] < CUT}
    eng, sink = run_engine(tape, spec, lookback=60)
    ents = {}
    for k, _, b in sink.rows:
        if k == "entry" and b["meta"]["t_event_ms"] < CUT:
            ents[(EV._h64("p:" + b["pool"]) if b["meta"]["venue"] == 1 else EV._h64(b["meta"]["mint"]), b["meta"]["t_event_ms"])] = b
    host_only = set(ents) - want
    assert not host_only, sorted(host_only)[:5]
    assert set(ents) == want and len(want) > 100
    assert eng.counters["events_no_usable_fill"] > 0  # the NF / LQ events were dropped before entry, as the evaluator does
    assert not any(b["pool"] in ("PoolNF",) for b in ents.values())


def test_age_features_refused_and_other_features_equal_with_rows_before_start(tmp_path):
    for f in ("age_s", "mig_age_s"):
        with pytest.raises(R.HostError):
            D.Engine({**SPEC, "entry": [{"f": f, "op": "<", "v": 100}]}, D.ListSink())
    rows = synth_rows()
    for h in range(1, 9):  # rows of Pool0 / Mint0 long before the warm-up start: the host's first-seen would be wrong for age_s, which is why age specs are refused
        rows.append(ps_row(C.hour_ms("2026-11-04T01") + h * 60_000 + 3_600_000 * (h - 1), "buy", b=1e15, q=10e9, pool="Pool0", mint="Mint0"))
    tape = tmp_path / "tape"
    write_tapes(tape, rows)
    out, data = batch(tmp_path, tape)
    got = {}
    run_engine(tape, SPEC, since="2026-11-04T04", hook=lambda ph, t, x: got.__setitem__((ph, t), x))  # warm-up from T04: nothing older is read
    keep = np.array([f not in ("age_s", "mig_age_s", "dd_high", "rise_low") for f in C.FEATURES])
    n = 0
    for i in range(data.n):
        k = (int(data.pool[i]), int(data.t[i]))
        if k in got:
            n += 1
            np.testing.assert_array_equal(np.nan_to_num(data.X[i][keep], nan=-7), np.nan_to_num(got[k][keep], nan=-7))
    assert n > 100


# ---------------------------------------------------------------------------------------------- follower
def test_follower_missing_hour_is_a_gap_on_both_venues(tmp_path):
    tape = tmp_path / "tape"
    tape.mkdir()
    (tape / f"tape-{H0}.jsonl").write_bytes(b'{"a":1}\n')
    (tape / f"tape-{C.hour_str(HS0 + 2 * 3_600_000)}.jsonl").write_bytes(b'{"b":1}\n')
    lines = []
    fol = D.TapeFollower(tape, HS0)
    while True:
        x = fol.read()
        if not x:
            break
        lines += x
    gaps = [json.loads(ln) for ln in lines if b"missing_hour" in ln]
    assert {g["prog"] for g in gaps} == {"pumpswap", "pump_bonding"} and all(g["from_ms"] == HS0 + 3_600_000 for g in gaps) and len(lines) == 4


def test_follower_rereads_previous_hour_after_next_exists(tmp_path):
    tape = tmp_path / "tape"
    tape.mkdir()
    h0, h1 = tape / f"tape-{H0}.jsonl", tape / f"tape-{C.hour_str(HS0 + 3_600_000)}.jsonl"
    h0.write_bytes(b'{"a":1}\n')
    fol = D.TapeFollower(tape, HS0)
    assert fol.read() == [b'{"a":1}\n']
    h1.write_bytes(b'{"b":1}\n')
    assert fol.read() == [b'{"b":1}\n']
    with open(h0, "ab") as fh:  # the writer keeps the old hour open for its grace window
        fh.write(b'{"a":2}\n')
    with open(h1, "ab") as fh:
        fh.write(b'{"b":2}\n')
    got = fol.read()
    assert sorted(got) == [b'{"a":2}\n', b'{"b":2}\n']
    (tape / f"tape-{C.hour_str(HS0 + 2 * 3_600_000)}.jsonl").write_bytes(b'{"c":1}\n')
    with open(h1, "ab") as fh:  # the hour before the newest is still read; the one before that is final
        fh.write(b'{"b":3}\n')
    assert sorted(fol.read()) == [b'{"b":3}\n', b'{"c":1}\n']


def test_follower_survives_file_deleted_between_exists_and_open(tmp_path, monkeypatch):
    tape = tmp_path / "tape"
    tape.mkdir()
    f = tape / f"tape-{H0}.jsonl"
    f.write_bytes(b'{"a":1}\n')
    subprocess.run(["zstd", "-q", str(f), "-o", str(tape / f"tape-{H0}.jsonl.zst")], check=True)
    f.unlink()
    fol = D.TapeFollower(tape, HS0)
    real = D.Path.exists
    monkeypatch.setattr(D.Path, "exists", lambda self: True if self.name.endswith(".jsonl") else real(self))  # exists() lies: the .jsonl is already gone
    assert fol.read() == []  # no exception; retried, nothing read this poll
    monkeypatch.setattr(D.Path, "exists", real)
    assert fol.read() == [b'{"a":1}\n']


def test_late_row_updates_history_only(world):
    tmp_path, tape, out, data = world
    eng = D.Engine({**SPEC, "entry": []}, D.ListSink(), gap_lookback_s=60)
    eng.feed_line(json.dumps(ps_row(HS0 + 50_000, "buy", b=1e15, q=10e9), separators=(",", ":")).encode() + b"\n")
    n_events = eng.counters["events"]
    eng.feed_line(json.dumps(ps_row(HS0 + 10_000, "buy", b=1e15, q=10e9), separators=(",", ":")).encode() + b"\n")
    assert eng.counters["late_rows"] == 1 and eng.counters["events"] == n_events and eng.clock == HS0 + 50_000


# ---------------------------------------------------------------------------------------------- host: chain, restart, monotone ms
def run_host(d, hb, t_to, live_from=HS0, now=HS0):
    host = D.Host(d, binding=D.run_binding(hb=hb), live=True, since_ms=HS0, live_from_ms=live_from, now_fn=lambda: now)
    while host.step(t_to):
        pass
    host.checkpoint()
    return host


def test_host_writes_chain_start_row_and_checkpoint_at_start(world):
    tmp_path, tape, out, data = world
    d = cfg_for(tmp_path, tape)
    hb = {"git_sha": "b" * 40, "dirty": False}
    host = D.Host(d, binding=D.run_binding(hb=hb), live=True, since_ms=HS0, live_from_ms=HS0, now_fn=lambda: HS0)
    assert (Path(d["root"]) / "dsl_host" / "checkpoint.json").exists()  # a kill during the catch-up leaves a restartable root
    while host.step():
        pass
    host.checkpoint()
    log = DecisionLog(d["root"])
    rows = log.rows("dsl-test-1")
    assert rows[0]["kind"] == "start" and rows[0]["frozen_sha"] == "b" * 40 and rows[0]["spec_sha256"] == D.spec_hash(E.validate_spec(SPEC))
    assert {"entry", "exit"} <= {r["kind"] for r in rows}
    ent = next(r for r in rows if r["kind"] == "entry")
    assert {"order_id", "pool", "meta", "decision_slot", "stake_sol", "attempt_key"} <= set(ent) and ent["stake_sol"] == 0.03 and ent["meta"]["rule"] == "dsl"
    assert ent["ms"] >= ent["meta"]["t_fill_ms"] > ent["meta"]["t_event_ms"]
    _, seq = log.verify("dsl-test-1")
    assert seq == len(rows) - 1
    ms = [r["ms"] for r in rows]
    assert ms == sorted(ms)  # row ms is monotone in the chain
    (Path(d["root"]) / "dsl_host" / "checkpoint.json").unlink()  # a log without its checkpoint is refused
    with pytest.raises(R.HostError):
        D.Host(d, binding=D.run_binding(hb=hb), live=True, since_ms=HS0, now_fn=lambda: HS0 + 10 ** 9)


def test_restart_with_open_position_closes_it_and_starts(tmp_path):
    tape = tmp_path / "tape"
    write_tapes(tape, synth_rows(gap=False))
    d = cfg_for(tmp_path, tape)
    hb = {"git_sha": "c" * 40, "dirty": False}
    t_stop = HS0 + 3_600_000 + 15 * 60_000
    run_host(d, hb, t_stop)  # the "crash": the process stops with whatever is open
    log = DecisionLog(d["root"])
    open_before = D.open_in_log(log, "dsl-test-1")
    assert open_before, "the scenario needs an open position"
    ck = json.loads((Path(d["root"]) / "dsl_host" / "checkpoint.json").read_text())
    down_to = t_stop + 20 * 60_000
    host2 = D.Host(d, binding=D.run_binding(hb=hb), live=True, since_ms=HS0, now_fn=lambda: down_to)  # the real path: no hand-written rows
    rows = DecisionLog(d["root"]).rows("dsl-test-1")
    closes = [r for r in rows if r["kind"] == "exit" and r["reason"] == "restart_close"]
    assert {r["order_id"] for r in closes} == set(open_before) and D.open_in_log(DecisionLog(d["root"]), "dsl-test-1") == {}
    assert sum(1 for r in rows if r["kind"] == "start") == 2
    span = host2.down[-1]
    assert span["kind"] == "host_down" and span["to_ms"] == down_to and span["from_ms"] == ck["hwm_ms"]
    assert host2.engine._gap_refusal(down_to - 10_000 - host2.engine.lookback_ms, down_to - 10_000, 1)["kind"] == "host_down"
    DecisionLog(d["root"]).verify("dsl-test-1")
    ms = [r["ms"] for r in rows]
    assert ms == sorted(ms)
    while host2.step():  # and it runs on: entries before the restart instant are shadow, none is opened inside the down span
        pass
    after = [r for r in DecisionLog(d["root"]).rows("dsl-test-1") if r["kind"] == "entry" and r["seq"] > rows[-1]["seq"]]
    assert all(r["meta"]["t_event_ms"] >= HS0 for r in after)


def test_row_ms_stays_monotone_across_utc_midnight(tmp_path):
    day = C.hour_ms("2026-11-04T23")
    rows = []
    t = day + 3_500_000
    for k in range(400):
        rows.append(ps_row(t, "buy" if k % 2 else "sell", b=1e15 * (1 + 0.01 * ((k // 20) % 2)), q=10e9, sol=1_000_000))
        t += 2_000
    # an out-of-order row (late_to_current style) does not move the clock backwards
    rows.append(ps_row(day + 3_590_000, "buy", b=1e15, q=10e9))
    tape = tmp_path / "tape"
    tape.mkdir()
    byh = {}
    for r in rows:
        byh.setdefault(C.hour_str(r["t_recv_ms"]), []).append(r)
    for h, rr in byh.items():
        with open(tape / f"tape-{h}.jsonl", "w") as fh:
            for r in rr:
                fh.write(json.dumps(r, separators=(",", ":")) + "\n")  # file order, not sorted: the late row stays late
    d = cfg_for(tmp_path, tape)
    d["spec"] = str(tmp_path / "spec2.json")
    (tmp_path / "spec2.json").write_text(json.dumps({**SPEC, "entry": [], "cooldown_s": 0, "one_per_pool": False, "exit": {"tp": 0.005, "sl": 0.005, "trail": None, "max_hold_s": 60}}))
    hb = {"git_sha": "d" * 40, "dirty": False}
    host = D.Host(d, binding=D.run_binding(hb=hb), live=True, since_ms=day, live_from_ms=day, now_fn=lambda: day)
    while host.step():
        pass
    log = DecisionLog(d["root"])
    log.verify("dsl-test-1")
    allrows = log.rows("dsl-test-1")
    assert any(r["kind"] == "entry" for r in allrows)
    ms = [r["ms"] for r in allrows]
    assert ms == sorted(ms) and ms[0] < day + 3_600_000 <= ms[-1]  # crosses UTC midnight and the chain still verifies


def test_follower_decompresses_each_hour_at_most_once(tmp_path, monkeypatch):
    tape = tmp_path / "tape"
    tape.mkdir()
    hrs = [C.hour_str(HS0 + k * 3_600_000) for k in range(3)]
    for k, h in enumerate(hrs):
        (tape / f"tape-{h}.jsonl").write_bytes(b'{"h":%d}\n' % k)
    fol = D.TapeFollower(tape, HS0)
    while fol.read():
        pass
    calls = []
    real = D._lines
    monkeypatch.setattr(D, "_lines", lambda p: (calls.append(p.name), real(p))[1])
    for h in hrs[:2]:  # the older hours are compacted after being read as .jsonl
        subprocess.run(["zstd", "-q", "--rm", str(tape / f"tape-{h}.jsonl")], check=True)
    for _ in range(5):
        assert fol.read() == []
    assert all(calls.count(c) <= 1 for c in set(calls)) and calls == [f"tape-{hrs[1]}.jsonl.zst"]  # <= 1 per hour over 5 polls; the hour two back is final and never reopened


# ---------------------------------------------------------------------------------------------- age_s / mig_age_s via a pinned first-seen snapshot
AGE_SPEC = {**SPEC, "entry": [{"f": "age_s", "op": ">=", "v": 0}, {"f": "mig_age_s", "op": ">=", "v": 0}]}
SNAP_HOURS = C.hour_range("2026-11-04T04", "2026-11-04T10")  # snapshot span [T04, T11): pools first printed from T11 on are NOT in it
NEW_T = C.hour_ms("2026-11-04T11") + 30 * 60_000


@pytest.fixture()
def age_world(tmp_path):
    rows = synth_rows()
    for h in range(5, 9):  # Pool0 / Mint0 are older than the host's warm-up (the host reads from T10)
        rows.append(ps_row(C.hour_ms(f"2026-11-04T0{h}") + 60_000 * h, "buy", b=1e15, q=10e9, pool="Pool0", mint="Mint0"))
    for i in range(1500):  # a pool and mint that first print at T11:30, after the snapshot span
        rows.append(ps_row(NEW_T + i * 1700, "buy" if i % 2 else "sell", b=1e15 - i * 2e10, q=15e9, sol=3_000_000, pool="PoolNew", mint="MintNew", trader=f"T{i % 4}"))
    tape = tmp_path / "tape"
    write_tapes(tape, rows)
    out, data = batch(tmp_path, tape)
    out2 = str(tmp_path / "out_snap")
    for h in SNAP_HOURS:
        EV.ingest_hour(h, str(tape), out2)
    snap_p = tmp_path / "fs.npz"
    info = EV.write_first_seen_snapshot(out2, str(snap_p), str(tape))
    return tmp_path, tape, out, data, snap_p, info


def run_age(tape, snap, spec=AGE_SPEC, since_ms=HS0):
    got = {}
    eng = D.Engine(spec, D.ListSink(), snapshot=snap, tape_start_ms=since_ms, gap_lookback_s=60)
    eng.feat_hook = lambda ph, t, x: got.__setitem__((ph, t), x)
    fol = D.TapeFollower(tape, since_ms)
    while True:
        lines = fol.read()
        if not lines:
            break
        for ln in lines:
            eng.feed_line(ln)
    return eng, got


def test_snapshot_round_trip_and_sha_pin(age_world, capsys):
    tmp_path, tape, out, data, snap_p, info = age_world
    snap = EV.load_first_seen_snapshot(str(snap_p), info["sha256"])
    want = EV.first_seen(str(tmp_path / "out_snap"), SNAP_HOURS)  # the batch's own function over the same span
    for nm in ("mint", "pool"):
        np.testing.assert_array_equal(snap[nm][0], want[nm][0])
        np.testing.assert_array_equal(snap[nm][1], want[nm][1])
    assert (snap["span_from"], snap["span_to"]) == (SNAP_HOURS[0], SNAP_HOURS[-1]) and snap["end_ms"] == C.hour_ms("2026-11-04T11")
    with pytest.raises(ValueError):
        EV.load_first_seen_snapshot(str(snap_p), "0" * 64)
    with pytest.raises(R.HostError):
        D.load_snapshot(str(snap_p), "0" * 64)
    bad = tmp_path / "bad.npz"
    bad.write_bytes(b"not an npz")
    with pytest.raises(R.HostError):
        D.load_snapshot(str(bad))
    cli = tmp_path / "cli.npz"
    EV.main(["first-seen-snapshot", "--root", str(tmp_path / "out_snap"), "--out", str(cli), "--tape-dir", str(tape)])
    assert json.loads(capsys.readouterr().out)["sha256"] == info["sha256"] == D.sha256_file(cli)  # deterministic bytes


def test_age_equals_batch_for_old_pool_and_new_pool_from_host_tape(age_world):
    tmp_path, tape, out, data, snap_p, info = age_world
    snap = D.load_snapshot(str(snap_p))
    eng, got = run_age(tape, snap)
    fi = C.FIDX
    n = old = new = 0
    for i in range(data.n):
        k = (int(data.pool[i]), int(data.t[i]))
        if k not in got:
            continue
        n += 1
        for f in ("age_s", "mig_age_s"):
            a, b = data.X[i][fi[f]], got[k][fi[f]]
            assert (np.isnan(a) and np.isnan(b)) or a == b, (f, a, b)
        old += data.X[i][fi["age_s"]] > 6 * 3600  # older than the host's warm-up: only the snapshot can know it
        new += data.t[i] >= NEW_T and data.pool[i] not in snap["pool"][0]  # absent from the snapshot: first print from the host tape
    assert n > 200 and old > 20 and new > 20
    assert eng.counters.get("events_age_unknown", 0) == 0


def test_unknown_key_is_refused_and_counted(age_world):
    tmp_path, tape, out, data, snap_p, info = age_world
    snap = dict(D.load_snapshot(str(snap_p)))
    fi = C.FIDX
    old_pool = {int(data.pool[i]) for i in range(data.n) if data.X[i][fi["age_s"]] > 6 * 3600}
    assert old_pool
    k, t = snap["pool"]
    keep = ~np.isin(k, list(old_pool))
    snap["pool"] = (k[keep], t[keep])  # inconsistent snapshot: the pool printed on the host tape INSIDE the span but is absent from it
    eng, got = run_age(tape, snap)
    assert eng.counters["events_age_unknown"] > 0
    assert not any(p in old_pool for p, _t in got) and got  # never decided, never approximated; the other pools still are


def test_gap_configuration_refuses_at_start_and_no_snapshot_keeps_old_refusal(age_world):
    tmp_path, tape, out, data, snap_p, info = age_world
    snap = D.load_snapshot(str(snap_p))
    D.Engine(AGE_SPEC, D.ListSink(), snapshot=snap, tape_start_ms=snap["end_ms"])  # starting exactly at the span end: overlap, accepted
    with pytest.raises(R.HostError, match="blind gap"):
        D.Engine(AGE_SPEC, D.ListSink(), snapshot=snap, tape_start_ms=snap["end_ms"] + 1)
    with pytest.raises(R.HostError, match="blind gap"):
        D.Engine(AGE_SPEC, D.ListSink(), snapshot=snap)  # no start instant: cannot show the overlap
    with pytest.raises(R.HostError, match="no first-seen snapshot"):
        D.Engine(AGE_SPEC, D.ListSink(), tape_start_ms=HS0)
    D.Engine(SPEC, D.ListSink())  # specs without age are untouched by all of this
    spec_p = tmp_path / "age_spec.json"
    spec_p.write_text(json.dumps(AGE_SPEC))
    d = {**cfg_for(tmp_path, tape), "spec": str(spec_p), "first_seen_snapshot": str(snap_p)}
    hb = {"git_sha": "c" * 40, "dirty": False}
    with pytest.raises(R.HostError, match="blind gap"):
        D.Host(d, binding=D.run_binding(hb=hb), live=True, since_ms=snap["end_ms"] + 3_600_000, live_from_ms=HS0, now_fn=lambda: HS0)
    d0 = {k: v for k, v in d.items() if k != "first_seen_snapshot"}
    with pytest.raises(R.HostError, match="no first-seen snapshot"):
        D.Host(d0, binding=D.run_binding(hb=hb), live=True, since_ms=HS0, live_from_ms=HS0, now_fn=lambda: HS0)
    assert not (Path(d["root"]) / "dsl_host").exists() and not DecisionLog(d["root"]).files("dsl-test-1")  # refused before anything was written


def test_register_pins_snapshot_sha_and_run_refuses_mismatch(age_world):
    tmp_path, tape, out, data, snap_p, info = age_world
    spec_p = tmp_path / "age_spec.json"
    spec_p.write_text(json.dumps(AGE_SPEC))
    d = {**cfg_for(tmp_path, tape), "spec": str(spec_p), "first_seen_snapshot": str(snap_p)}
    reg = D.register(d)
    assert reg["first_seen_sha256"] == info["sha256"] and reg["first_seen_span"] == [SNAP_HOURS[0], SNAP_HOURS[-1]]
    assert D.check_registry(d)["first_seen_sha256"] == info["sha256"]
    with pytest.raises(R.HostError, match="first-seen"):
        D.check_registry({k: v for k, v in d.items() if k != "first_seen_snapshot"})  # registered with a snapshot, run without
    other = tmp_path / "other.npz"
    other.write_bytes(snap_p.read_bytes() + b"\0")
    with pytest.raises(R.HostError, match="first-seen"):
        D.check_registry({**d, "first_seen_snapshot": str(other)})
    hb = {"git_sha": "c" * 40, "dirty": False}
    with pytest.raises(R.HostError, match="sha256 mismatch"):
        D.Host(d, binding=D.run_binding(hb=hb), live=True, since_ms=HS0, live_from_ms=HS0, now_fn=lambda: HS0, snapshot_sha256="1" * 64)
    host = D.Host(d, binding=D.run_binding(hb=hb), live=True, since_ms=HS0, live_from_ms=HS0, now_fn=lambda: HS0, snapshot_sha256=reg["first_seen_sha256"])
    assert DecisionLog(d["root"]).rows("dsl-test-1")[0]["first_seen_sha256"] == info["sha256"]
    while host.step(HS0 + 3_600_000):
        pass
    assert host.engine.counters["candidates"] > 0 and host.engine.counters.get("events_age_unknown", 0) == 0
    # a snapshot-less registration pins no snapshot, and configuring one afterwards is a mismatch
    (tmp_path / "x").mkdir()
    d2 = {**cfg_for(tmp_path / "x", tape), "root": str(tmp_path / "root2")}
    D.register(d2)
    with pytest.raises(R.HostError, match="first-seen"):
        D.check_registry({**d2, "first_seen_snapshot": str(snap_p)})


# ---------------------------------------------------------------------------------------------- QP/reviewer fixes on #1286
def test_since_before_span_start_is_refused(age_world):
    """QP probe: a snapshot over T06..T10 and a host starting at T05 made min() take prints the batch never saw (1489 of 1809 mig_age_s differed). Now refused at start."""
    tmp_path, tape, out, data, snap_p, info = age_world
    hrs = C.hour_range("2026-11-04T06", "2026-11-04T10")
    out3 = str(tmp_path / "out_snap3")
    for h in hrs:
        EV.ingest_hour(h, str(tape), out3)
    p3 = tmp_path / "fs3.npz"
    EV.write_first_seen_snapshot(out3, str(p3), str(tape))
    snap = D.load_snapshot(str(p3))
    with pytest.raises(R.HostError, match="before the first-seen snapshot span starts"):
        run_age(tape, snap, since_ms=C.hour_ms("2026-11-04T05"))
    batch_fs = EV.first_seen(out3, hrs)
    eng, got = run_age(tape, snap, since_ms=snap["start_ms"])  # starting exactly at the span start is exact
    fi = C.FIDX
    pk, pt = batch_fs["pool"]
    n = 0
    for (ph, t), x in got.items():
        i = np.searchsorted(pk, ph)
        if t >= snap["start_ms"] and i < len(pk) and pk[i] == ph and np.isfinite(x[fi["mig_age_s"]]):
            n += 1
            assert x[fi["mig_age_s"]] == np.float32((t - pt[i]) / 1000.0)
    assert n > 100


def test_host_print_earlier_than_snapshot_is_unknown_not_min(age_world):
    tmp_path, tape, out, data, snap_p, info = age_world
    snap = dict(D.load_snapshot(str(snap_p)))
    k, t = snap["pool"]
    snap["pool"] = (k, t + 100 * 3_600_000)  # every snapshot pool "first printed" later than the host's own print: impossible on the build's bytes
    eng, got = run_age(tape, snap)
    assert eng.counters["events_age_unknown"] > 0
    assert got and not any(p in set(k.tolist()) and x[C.FIDX["venue"]] == 1 for (p, _t), x in got.items())  # on PumpSwap only pools outside the snapshot are decided; none is approximated with min()


def test_tape_identity_is_checked(age_world):
    tmp_path, tape, out, data, snap_p, info = age_world
    spec_p = tmp_path / "age_spec.json"
    spec_p.write_text(json.dumps(AGE_SPEC))
    hb = {"git_sha": "c" * 40, "dirty": False}
    d = {**cfg_for(tmp_path, tape), "spec": str(spec_p), "first_seen_snapshot": str(snap_p)}
    other = tmp_path / "other_tape"
    other.mkdir()
    d_other = {**d, "tape_dir": str(other)}
    with pytest.raises(R.HostError, match="not the build tape"):
        D.register(d_other)
    with pytest.raises(R.HostError, match="not the build tape"):
        D.Host(d_other, binding=D.run_binding(hb=hb), live=True, since_ms=HS0, live_from_ms=HS0, now_fn=lambda: HS0)
    undeclared = tmp_path / "undeclared.npz"
    EV.write_first_seen_snapshot(str(tmp_path / "out_snap"), str(undeclared))
    with pytest.raises(R.HostError, match="does not declare its source tape"):
        D.Host({**d, "first_seen_snapshot": str(undeclared)}, binding=D.run_binding(hb=hb), live=True, since_ms=HS0, live_from_ms=HS0, now_fn=lambda: HS0)
    assert not DecisionLog(d["root"]).files("dsl-test-1")


@pytest.mark.parametrize("f,other", [("age_s", "mig_age_s"), ("mig_age_s", "age_s")])
def test_single_table_spec_equals_batch_and_logs_nan_for_unread_feature(age_world, f, other):
    tmp_path, tape, out, data, snap_p, info = age_world
    snap = D.load_snapshot(str(snap_p))
    eng, got = run_age(tape, snap, spec={**SPEC, "entry": [{"f": f, "op": ">=", "v": 0}]})
    fi = C.FIDX
    n = 0
    for i in range(data.n):
        k = (int(data.pool[i]), int(data.t[i]))
        if k in got:
            n += 1
            a, b = data.X[i][fi[f]], got[k][fi[f]]
            assert (np.isnan(a) and np.isnan(b)) or a == b
            assert np.isnan(got[k][fi[other]])  # a placeholder first-seen is never logged as a feature value
    assert n > 200
    sink = eng.sink
    feats = [b["meta"]["features"] for kind, _ms, b in sink.rows if kind == "entry"]
    assert feats and all(x[other] is None for x in feats)


def _row_line(t, pool, mint):
    return json.dumps(ps_row(t, "buy", pool=pool, mint=mint), separators=(",", ":")).encode()


def test_age_first_seen_state_is_bounded_and_prune_runs(age_world):
    tmp_path, tape, out, data, snap_p, info = age_world
    snap = D.load_snapshot(str(snap_p))
    eng = D.Engine(AGE_SPEC, D.ListSink(), snapshot=snap, tape_start_ms=HS0)
    h_pool = int(snap["pool"][0][0])
    new_t = snap["end_ms"] + 1000
    eng.feed_line(_row_line(new_t, "PoolFresh", "MintFresh"))
    fresh_p = [k for k in eng.age_last_p]
    fresh_m = [k for k in eng.age_last_m]
    assert len(fresh_p) == 1 and len(fresh_m) == 1 and fresh_p[0] in eng.first_pool and fresh_m[0] in eng.first_mint
    assert h_pool not in eng.age_last_p  # keys in the snapshot are not tracked: they are recoverable
    eng.feed_line(_row_line(new_t + 20 * 60_000, "PoolOther", "MintOther"))  # lets the pending candidate expire (a pending pool is never pruned)
    eng.feed_line(_row_line(new_t + 30 * 3_600_000, "PoolOther", "MintOther"))  # the clock moves 30 h: the quiet pool's history is pruned, its out-of-snapshot first-seen is NOT
    assert fresh_p[0] not in eng.hist and fresh_p[0] in eng.first_pool and fresh_m[0] in eng.first_mint
    eng.feed_line(_row_line(new_t + (D.AGE_KEEP_H + 5) * 3_600_000, "PoolLater", "MintLater"))  # past AGE_KEEP_H: dropped and counted
    assert fresh_p[0] not in eng.first_pool and fresh_m[0] not in eng.first_mint and fresh_p[0] not in eng.age_last_p
    assert eng.counters["age_keys_dropped"] >= 2
    assert len(eng.first_pool) <= 3 and len(eng.age_last_p) <= 3


def test_restart_with_age_spec_replays_and_continues(age_world):
    tmp_path, tape, out, data, snap_p, info = age_world
    spec_p = tmp_path / "age_spec.json"
    spec_p.write_text(json.dumps({**AGE_SPEC, "entry": [{"f": "age_s", "op": ">=", "v": 0}, {"f": "n_1m", "op": ">=", "v": 4}, {"f": "ret_1m", "op": "<", "v": 0.02}]}))
    d = {**cfg_for(tmp_path, tape), "spec": str(spec_p), "first_seen_snapshot": str(snap_p)}
    hb = {"git_sha": "c" * 40, "dirty": False}
    reg = D.register(d)
    t_stop = HS0 + 3_600_000 + 15 * 60_000
    h1 = D.Host(d, binding=D.run_binding(hb=hb), live=True, since_ms=HS0, live_from_ms=HS0, now_fn=lambda: HS0, snapshot_sha256=reg["first_seen_sha256"])
    while h1.step(t_stop):
        pass
    h1.checkpoint()
    assert D.open_in_log(DecisionLog(d["root"]), "dsl-test-1")
    h2 = D.Host(d, binding=D.run_binding(hb=hb), live=True, since_ms=HS0, now_fn=lambda: t_stop + 600_000, snapshot_sha256=reg["first_seen_sha256"])  # replays from the snapshot overlap
    rows = DecisionLog(d["root"]).rows("dsl-test-1")
    assert sum(1 for r in rows if r["kind"] == "start") == 2 and all(r.get("first_seen_sha256") == info["sha256"] for r in rows if r["kind"] == "start")
    while h2.step():
        pass
    assert h2.engine.counters.get("events_age_unknown", 0) == 0 and h2.engine.counters["candidates"] > 0
    DecisionLog(d["root"]).verify("dsl-test-1")
    with pytest.raises(R.HostError, match="blind gap"):  # a restart whose --since-hour moved past the snapshot span end is refused, nothing is written
        D.Host(d, binding=D.run_binding(hb=hb), live=True, since_ms=info_end(info) + 3_600_000, now_fn=lambda: t_stop + 900_000, snapshot_sha256=reg["first_seen_sha256"])


def info_end(info):
    return C.hour_ms(info["span_to"]) + 3_600_000
