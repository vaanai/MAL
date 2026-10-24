"""Tests for tools/dbc_fwd.py (P18 m10 engine). Synthetic tape on the real DBC curve (constant L in sqrt-price space, fees per mode) written as tape hours into a
temporary backfill root, so the replay runs through the same guarded loader as the screen. No network, no key, no real tape hour."""
from __future__ import annotations

import contextlib
import gzip
import json
import os
import random
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from tools import dbc_decode as DD
from tools import dbc_fwd as F
from tools import dbc_price as L
from tools.arena_declog import DecisionLog, LogChainError

Q = 1 << 128
T0 = int(datetime(2026, 10, 14, 0, tzinfo=timezone.utc).timestamp())
SLOT0 = 456_600_000
WSOL = L.WSOL


@contextlib.contextmanager
def _raises(exc):
    try:
        yield
    except exc:
        return
    raise AssertionError("did not raise")


class Pool:
    def __init__(self, name, cfg, mode=0, thr=85 * 10**9, qres=5 * 10**9, event="EvtSwap2"):
        self.name, self.cfg, self.mode, self.thr, self.qres, self.event = name, cfg, mode, thr, qres, event
        self.S = (1 << 64) // 1000
        self.L = int(4e9 * (1 << 64) / (0.07 * 1e-3))  # about 4 SOL of buys moves the sqrt price ~7%
        self.n = 0

    def buy(self, q):
        fee_q = q // 100 if self.mode == 1 else 0
        qin = q - fee_q
        S0, S1 = self.S, self.S + qin * Q // self.L
        gross = self.L * (S1 - S0) // (S0 * S1)
        if self.mode == 1:
            fee, aout = fee_q, gross
        else:
            fee = gross // 100
            aout = gross - fee
        self.S, self.qres = S1, self.qres + qin
        return {"side": "buy", "amount_in": q, "amount_out": aout, "trading_fee": fee}

    def sell(self, b):
        S0 = self.S
        S1 = (self.L * S0) // (self.L + b * S0)  # 1/S1 = 1/S0 + b/L
        gross = self.L * (S0 - S1) // Q
        fee = gross // 100
        self.S, self.qres = S1, self.qres - gross
        return {"side": "sell", "amount_in": b, "amount_out": gross - fee, "trading_fee": fee}

    def base_for(self, q):
        """Base atoms worth q lamports at the current spot (spot = S^2 / 2^128 quote per base)."""
        return int(q * Q / (self.S * self.S))

    def row(self, t, slot, leg):
        self.n += 1
        return {"v": 1, "venue": "meteora_dbc", "type": "trade", "slot": slot, "block_time": T0 + int(t), "t_recv_ms": (T0 + int(t)) * 1000, "signature": f"sig-{self.name}-{self.n}",
                "pool": self.name, "config": self.cfg, "event": self.event, "base_mint": "Mint" + self.name, "quote_mint": WSOL, "protocol_fee": 0, "referral_fee": 0,
                "next_sqrt_price": str(self.S), "quote_reserve_post": self.qres, "migration_threshold": self.thr, **leg}


def synth_rows(seed=1):
    """Rows in tape (slot) order for 3 hours. Pools: A (output-fee config, bursts incl. one inside the cooldown and one at the data edge), B (input-fee config), C (transfer-hook
    pool, never trades), E (curve completes 2 slots after its trigger: the screen refuses, the live book has sealed it). Some slots carry two rows of a pool in reversed tape order."""
    rng = random.Random(seed)
    ev: list[tuple[float, int, dict]] = []  # (t, tie, row)
    pools = {"A": Pool("PoolAaaa", "CfgOut1"), "B": Pool("PoolBbbb", "CfgIn1", mode=1), "C": Pool("PoolCccc", "CfgOut1", event="EvtSwap2WithTransferHook"),
             "E": Pool("PoolEeee", "CfgOut1")}
    tie = [0]

    def emit(p, t, leg_fn, pair=False):
        slot = SLOT0 + int(t / 0.4)
        if pair:  # two rows in one slot, written in REVERSED execution order (the tape order is not the execution order)
            r1 = p.row(t, slot, leg_fn())
            r2 = p.row(t, slot, leg_fn())
            for r in (r2, r1):
                tie[0] += 1
                ev.append((slot, tie[0], r))
        else:
            tie[0] += 1
            ev.append((slot, tie[0], p.row(t, slot, leg_fn())))

    def background(p, t0, t1, step):
        t = t0
        k = 0
        while t < t1:
            k += 1
            if rng.random() < 0.55:
                emit(p, t, lambda: p.buy(int(rng.uniform(0.02, 0.15) * 1e9)), pair=(k % 9 == 0))
            else:
                emit(p, t, lambda: p.sell(max(1, p.base_for(int(rng.uniform(0.02, 0.12) * 1e9)))), pair=(k % 9 == 0))
            t += step * rng.uniform(0.6, 1.4)

    def burst(p, t, n=15, q=0.4):
        for j in range(n):
            emit(p, t + 2 * j, lambda: p.buy(int(q * 1e9)))
        return t + 2 * n

    def dump(p, t, frac):
        target = p.S * (1 - frac)
        while p.S > target:
            emit(p, t, lambda: p.sell(max(1, p.base_for(int(0.5e9)))))
            t += 1.2
        return t

    A, B, C, E = pools["A"], pools["B"], pools["C"], pools["E"]
    # A: background all along; bursts at 3700 (entry), 3900 (inside the cooldown), 5600 (entry), 10500 (exit window passes the data end: block_edge)
    background(A, 5, 3690, 12)
    t = burst(A, 3700)
    t = dump(A, t + 20, 0.12)  # stop-loss region
    background(A, t + 5, 3895, 10)
    t = burst(A, 3900, n=12)
    background(A, t + 5, 5590, 15)
    t = burst(A, 5600)
    background(A, t + 5, 10490, 15)
    burst(A, 10500)
    # B: input-fee config, mostly buys, one burst at 4500
    background(B, 7, 4490, 9)
    t = burst(B, 4500, n=14, q=0.45)
    background(B, t + 5, 10700, 14)
    # C: hook pool with a burst (excluded)
    background(C, 11, 4990, 20)
    burst(C, 5000)
    # E: burst at 8000; the curve completes two slots after the burst's last print
    background(E, 13, 7900, 16)
    t = burst(E, 8000, n=10, q=0.45)  # first possible trigger is the 10th (last) print
    comp_slot = SLOT0 + int((t - 2) / 0.4) + 2
    tie[0] += 1
    ev.append((comp_slot, tie[0], {"v": 1, "venue": "meteora_dbc", "type": "curve_complete", "slot": comp_slot, "t_recv_ms": (T0 + int(t)) * 1000, "signature": "sig-E-complete",
                                   "pool": E.name, "config": E.cfg, "base_reserve": 0, "quote_reserve": E.qres}))
    ev.sort(key=lambda x: (x[0], x[1]))
    return [r for _, _, r in ev]


def write_tape(root: Path, rows, hours=3):
    root.mkdir(parents=True, exist_ok=True)
    for h in range(hours):
        name = f"tape-2026-10-14T{h:02d}.jsonl"
        lo, hi = (T0 + 3600 * h) * 1000, (T0 + 3600 * (h + 1)) * 1000
        lines = [json.dumps({"v": 1, "venue": "meteora_dbc", "type": "backfill_hour", "hour": h}, separators=(",", ":"))]
        lines += [json.dumps(r, separators=(",", ":")) for r in rows if lo <= r["t_recv_ms"] < hi]
        lines.append(json.dumps({"v": 1, "venue": "meteora_dbc", "type": "backfill_hour_end"}, separators=(",", ":")))
        (root / name).write_text("\n".join(lines) + "\n")
        subprocess.run(["zstd", "-q", "--rm", "-f", str(root / name), "-o", str(root / (name + ".zst"))], check=True)


@contextlib.contextmanager
def tape_root(rows=None):
    old = L.BACKFILL_ROOT
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "meteora_dbc"
        write_tape(root, rows if rows is not None else synth_rows())
        L.BACKFILL_ROOT = str(root)
        try:
            yield root
        finally:
            L.BACKFILL_ROOT = old


START = datetime(2026, 10, 14, 0, tzinfo=timezone.utc)
END = datetime(2026, 10, 14, 3, tzinfo=timezone.utc)


# ------------------------------------------------------------------------------------------------------------------------------ guards
def test_replay_guard_refuses_spent_and_forward_hours():
    d = lambda *a: datetime(*a, tzinfo=timezone.utc)  # noqa: E731
    for s, e in ((d(2026, 10, 16, 0), d(2026, 10, 16, 5)), (d(2026, 10, 17, 0), d(2026, 10, 18, 0)), (d(2026, 10, 23, 0), d(2026, 10, 24, 0)),
                 (d(2026, 10, 25, 0), d(2026, 10, 26, 0)), (d(2026, 10, 13, 23), d(2026, 10, 14, 2)), (d(2026, 10, 14, 5), d(2026, 10, 14, 5))):
        with _raises(F.ReplayRefused):
            F.replay_guard(s, e)
    for root in ("/data2/mal/venue-tape-backfill/fwdv1/meteora_dbc", "/data2/mal/venue-tape-backfill/meteora_dbc_holdout", "/x/p17_union"):
        with _raises(F.ReplayRefused):
            F.replay_guard(START, END, root)
    F.replay_guard(START, END)
    F.replay_guard(d(2026, 10, 14, 0), d(2026, 10, 16, 4))


def test_replay_refuses_when_spent_window_enabled():
    old = L.SPENT_ROOT
    L.SPENT_ROOT = "/some/union"
    try:
        with _raises(F.ReplayRefused):
            F.replay_guard(START, END)
    finally:
        L.SPENT_ROOT = old


def test_cli_refuses_without_replay_and_outside_window():
    assert F.main(["--start", "2026-10-14T00", "--end", "2026-10-14T03", "--out", "/tmp/x-never"]) == 2
    assert F.main(["--replay", "--start", "2026-10-17T00", "--end", "2026-10-17T03", "--out", "/tmp/x-never"]) == 3
    assert F.main(["--replay", "--start", "2026-10-25T00", "--end", "2026-10-25T03", "--out", "/tmp/x-never"]) == 3
    assert not os.path.exists("/tmp/x-never")


def test_engine_needs_swap3_path():
    old = L.FOB_EVIDENCE
    L.FOB_EVIDENCE = False
    try:
        with _raises(RuntimeError):
            F.Engine(0.0)
    finally:
        L.FOB_EVIDENCE = old


# ------------------------------------------------------------------------------------------------------------------------------ reader
def test_read_hour_ordered_matches_read_hour():
    with tape_root() as root:
        for h in range(3):
            f = str(root / f"tape-2026-10-14T{h:02d}.jsonl.zst")
            a = F.read_hour_ordered(f)
            b = L.read_hour(f)
            assert a["incomplete"] == b["incomplete"] is False
            assert [r for r in a["rows"] if r["type"] == "trade"] == b["trades"]
            assert [r for r in a["rows"] if r["type"] == "curve_complete"] == b["completes"]


# ------------------------------------------------------------------------------------------------------------------------------ equivalence
def _replay(tmp=None):
    with tape_root():
        with tempfile.TemporaryDirectory() as td:
            res = F.replay(START, END, None, None, td + "/declog")
            log = DecisionLog(td + "/declog")
            rows = log.rows(F.BOOK)
            files = [(f.name, f.read_bytes()) for f in log.files(F.BOOK)]
    return res, rows, files


_CACHE: dict = {}


def replayed():
    if "r" not in _CACHE:
        _CACHE["r"] = _replay()
    return _CACHE["r"]


def test_synthetic_md5_equivalence_all_variants():
    res, _, _ = replayed()
    scr = res["screen"]
    assert scr["trigger_prints"] > 0 and scr["entries"] >= 3, scr
    assert res["equivalence_pass"] is True
    for name, v in res["variants"].items():  # the synthetic pools start with isolated slots, so even the causal engine has nothing to look ahead for
        assert v["equal_triggers"] and v["equal_entries"], (name, v)
        assert v["md5_triggers"] == scr["md5_triggers"] and v["md5_entries_screen_policy"] == scr["md5_entries"]


def test_synthetic_covers_cooldown_refusals_and_hook():
    res, _, _ = replayed()
    ref = res["screen"]["refused"]
    assert ref["cooldown"] > 0 and ref["completed_before_entry"] >= 1 and ref["block_edge"] >= 1, ref
    v = res["variants"]["causal"]
    assert v["refused_screen_policy"] == ref
    assert not any("PoolCccc" in k for k in v["examples"]["triggers_engine_only"])
    # live policy: the trigger the screen refuses (completed before entry / block edge) is SEALED by the book
    assert v["live_decisions_not_screen_entries"] >= 2 and v["screen_entries_not_live_decisions"] == 0, v


def test_decision_log_chain_and_content():
    res, rows, files = replayed()
    kinds = [r["type"] for r in rows]
    assert kinds[0] == "start" and kinds.count("hb") == 3
    dec = [r for r in rows if r["type"] == "decision"]
    assert len(dec) == res["variants"]["causal"]["decisions_live_policy"]
    for r in dec:
        assert r["entry_slot"] == r["slot"] + r["k_in"] and r["exit_rule"] == F.EXIT_RULE and r["signature"].startswith("sig-")
        assert set(r["features"]) == {"n_buys_60s", "net_sol_60s", "move", "prog_post", "thr_sol", "imp25", "fee_mode", "mode_known"}
        assert r["features"]["n_buys_60s"] >= 10 and r["features"]["net_sol_60s"] >= 4.0 and 0.05 <= r["features"]["move"] <= 0.40
        assert not any(k in r for k in ("pnl", "price", "fill", "proceeds"))
    ex = [r for r in rows if r["type"] == "exit"]
    assert len(ex) == len(dec) and all(e["decision_seq"] in {d["seq"] for d in dec} for e in ex)
    assert [r["seq"] for r in rows] == list(range(len(rows)))


def test_decision_log_is_deterministic():
    _, _, f1 = replayed()
    _, _, f2 = _replay()
    assert f1 == f2


def test_decision_log_tamper_and_truncate_are_detected():
    _, _, files = replayed()
    with tempfile.TemporaryDirectory() as td:
        d = Path(td) / "decisions" / F.BOOK
        d.mkdir(parents=True)
        for n, b in files:
            (d / n).write_bytes(b)
        log = DecisionLog(td)
        log.verify(F.BOOK)
        name, data = files[0]
        lines = data.splitlines()
        i = next(k for k, ln in enumerate(lines) if b'"type":"decision"' in ln)
        (d / name).write_bytes(b"\n".join(lines[:i] + [lines[i].replace(b'"n_buys_60s":', b'"n_buys_60s":1')] + lines[i + 1:]) + b"\n")
        with _raises(LogChainError):
            DecisionLog(td).verify(F.BOOK)
        (d / name).write_bytes(data[:-5])
        with _raises(LogChainError):
            DecisionLog(td).verify(F.BOOK)
        (d / name).write_bytes(data)
        log = DecisionLog(td)
        h, s = log.verify(F.BOOK)
        row = log.append(F.BOOK, T0 * 1000 + 99, {"type": "start", "book": F.BOOK, "mode": "restart"})  # restart continues the chain from the verified head
        assert row["prev"] == h and row["seq"] == s + 1
        log.verify(F.BOOK)


def test_prefilter_and_tail_do_not_change_decisions():
    """The cheap prefilter and the tail evaluation are optimisations: forcing the full pipeline on the full history at every slot close gives the same keys."""
    L.enable_swap3_pricing()
    with tape_root():
        def run(force_full):
            e = F.Engine(T0 + 3600.0)
            if force_full:
                e._prefilter = lambda *a: True  # type: ignore[method-assign]
                e._tail_start = lambda *a: 0  # type: ignore[method-assign]
            F.stream_hours(e, START, END, None)
            return sorted(e.trig_keys), sorted(x["key"] for x in e.decisions_live), e.stats
        k1, d1, s1 = run(False)
        k2, d2, s2 = run(True)
    assert k1 == k2 and d1 == d2 and len(k1) > 0
    assert s1["tail_rows"] < s2["tail_rows"] and s2["full_evals"] > s1["full_evals"]


def test_exit_fire_matches_resolve_hold():
    """exit_fire (live cooldown clock) equals dbc_screen.resolve's hold for every accepted trigger."""
    from tools import dbc_screen as S

    L.enable_swap3_pricing()
    with tape_root():
        e = F.Engine(T0 + 3600.0, good_hours={T0 // 3600 + h for h in range(3)}, screen_policy=True)
        holds = []
        orig = S.resolve

        def spy(cell, P, idx, D, cs, *a):
            out, ref = orig(cell, P, idx, D, cs, *a)
            for att in out:
                ss = D["slot_s"][int(att["bt"] // 3600)]
                fs, _xb = F.exit_fire(P, att["i"], ss)
                holds.append(((fs - int(P.slot[att["i"]])) * ss, att["hold_real_s"]))
            return out, ref
        S.resolve = spy
        try:
            F.stream_hours(e, START, END, None)
        finally:
            S.resolve = orig
    assert holds and all(a == b for a, b in holds), holds


# ------------------------------------------------------------------------------------------------------------------------------ real-tx fixture
def test_engine_takes_decoded_real_txs():
    """The 85 real DBC transactions (2026-10-14T12, getTransaction json) decode and flow through the engine (no trigger expected on 85 txs)."""
    L.enable_swap3_pricing()
    txs = json.load(gzip.open(Path(__file__).parent / "fixtures" / "dbc_txs_2026-10-14T12.json.gz"))
    rows = [r for t in sorted(txs, key=lambda t: t["slot"]) for r in DD.decode_tx(t)]
    e = F.Engine(0.0)
    for r in rows:
        e.on_row(r)
    e.finish()
    assert e.stats["rows"] == sum(1 for r in rows if r["type"] == "trade") == 77
    assert e.stats["completes"] == 2 and e.stats["sol_rows"] > 0 and e.stats["groups_closed"] > 0


if __name__ == "__main__":
    for k, v in list(globals().items()):
        if k.startswith("test_"):
            v()
            print("ok", k)
