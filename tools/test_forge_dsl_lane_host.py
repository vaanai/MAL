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
    PoolLQ alternates a 3 SOL and a 1 SOL real quote, so the event row passes the 2 SOL floor and the fill state does not; PoolBad's fill state has V below V_LO (dropped by the parser)."""
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
