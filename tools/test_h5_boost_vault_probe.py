"""tools/h5_boost_vault_probe.py. Synthetic fixtures and a fake RPC only: nothing here reads /data/mal, a shadow dir, a tape, a key or the network."""
from __future__ import annotations

import base64
import json
import struct

from solders.pubkey import Pubkey

from tools import h5_boost_vault_probe as P
from tools import pumpswap_tx as tx


def pk(i: int) -> str:
    return str(Pubkey.from_bytes(bytes([i]) * 32))


def acct(pool: str, amount: int) -> dict:
    _v, auth = P.boost_vault_address(pool)
    data = bytearray(165)
    data[0:32] = bytes(tx.WSOL_MINT)
    data[32:64] = bytes(Pubkey.from_string(auth))
    struct.pack_into("<Q", data, 64, amount)
    data[108] = 1
    return {"owner": str(tx.TOKEN_PROGRAM), "data": [base64.b64encode(bytes(data)).decode(), "base64"], "lamports": 1}


def write_hour(d, name, rows):
    (d / name).write_text("".join(json.dumps(r) + "\n" for r in rows))


class Clock:
    def __init__(self, t=1000.0):
        self.t = t
        self.sleeps = []

    def now(self):
        return self.t

    def sleep(self, s):
        self.sleeps.append(s)
        self.t += s


class FakeRpc:
    def __init__(self, clock, responses=None):
        self.clock, self.calls, self.responses = clock, [], responses or {}

    def __call__(self, method, params):
        self.calls.append((self.clock.t, method, params[0]))
        r = self.responses.get(params[0])
        if callable(r):
            r = r()
        return r if r is not None else {"result": {"context": {"slot": 5}, "value": None}}


def test_pool_selection_window_cap_and_fields(tmp_path):
    a, b, c, old = pk(1), pk(2), pk(3), pk(4)
    write_hour(tmp_path, "h5-shadow-2026110300.jsonl", [{"type": "pool", "pool": old, "slot": 100}])
    write_hour(tmp_path, "h5-shadow-2026110301.jsonl", [
        {"type": "pool", "pool": a, "slot": 100_000, "price": 9.9},
        {"type": "boost_slice", "pool": b, "slot": 100_500, "lamports": 5, "pnl": 1},
        {"type": "trigger", "pool": c, "slot": 100_900},
        {"type": "pool", "pool": c, "slot": 50_000},  # older than 60 min of slots from the newest
        {"type": "pool", "pool": pk(5), "slot": "x"},
    ])
    assert P.recent_pools(str(tmp_path), 40) == [b, a]
    assert P.recent_pools(str(tmp_path), 1) == [b]
    assert P.recent_pools(str(tmp_path / "nope"), 5) == []


def test_read_vault_parses_and_nulls():
    pool = pk(1)
    clk = Clock()
    lim = P.Limiter(clock=clk.now, sleep=clk.sleep)
    vault, _ = P.boost_vault_address(pool)
    ok = FakeRpc(clk, {vault: {"result": {"context": {"slot": 77}, "value": acct(pool, 123)}}})
    r = P.read_vault(ok, pool, lim, lambda: 1)
    assert (r["pool"], r["vault"], r["ctx_slot"], r["amount"], r["reason"]) == (pool, vault, 77, 123, "ok")
    missing = P.read_vault(FakeRpc(clk), pool, lim, lambda: 1)
    assert missing["amount"] is None and missing["reason"] == "boost_vault_missing" and missing["ctx_slot"] == 5
    bad = FakeRpc(clk, {vault: {"error": "x"}})
    assert P.read_vault(bad, pool, lim, lambda: 1)["reason"] == "rpc_no_result"

    def boom():
        raise OSError("down")
    assert P.read_vault(FakeRpc(clk, {vault: boom}), pool, lim, lambda: 1)["reason"] == "rpc_error_OSError"


def test_429_backs_off_then_succeeds_and_gives_up():
    pool = pk(1)
    vault, _ = P.boost_vault_address(pool)
    clk = Clock()
    lim = P.Limiter(clock=clk.now, sleep=clk.sleep)
    n = {"i": 0}

    def flaky():
        n["i"] += 1
        if n["i"] <= 2:
            raise P.RateLimited()
        return {"result": {"context": {"slot": 9}, "value": acct(pool, 1)}}
    rpc = FakeRpc(clk, {vault: flaky})
    r = P.read_vault(rpc, pool, lim, lambda: 1)
    assert r["amount"] == 1 and len(rpc.calls) == 3
    assert rpc.calls[2][0] - rpc.calls[1][0] >= 4.0  # backoff 2 s then 4 s
    always = FakeRpc(clk, {vault: lambda: (_ for _ in ()).throw(P.RateLimited())})
    assert P.read_vault(always, pool, lim, lambda: 1)["reason"] == "rate_limited"


def test_rate_limit_at_most_4_calls_per_second(tmp_path):
    write_hour(tmp_path, "h5-shadow-2026110301.jsonl", [{"type": "pool", "pool": pk(i), "slot": 1000 + i} for i in range(1, 9)])
    clk = Clock()
    rpc = FakeRpc(clk)
    out = tmp_path / "reads.jsonl"
    lim = P.Limiter(clock=clk.now, sleep=clk.sleep)
    n = P.poll(str(tmp_path), str(out), 20, 40, clk.t + 10, rpc, clock=clk.now, sleep=clk.sleep, limiter=lim)
    assert n == len(rpc.calls) == 8
    times = [c[0] for c in rpc.calls]
    assert all(b - a >= 0.25 - 1e-9 for a, b in zip(times, times[1:]))
    assert len(out.read_text().splitlines()) == 8


def test_poll_exits_at_end_epoch_and_repeats_each_period(tmp_path):
    write_hour(tmp_path, "h5-shadow-2026110301.jsonl", [{"type": "pool", "pool": pk(1), "slot": 1000}])
    clk = Clock()
    rpc = FakeRpc(clk)
    out = tmp_path / "r.jsonl"
    n = P.poll(str(tmp_path), str(out), 20, 40, clk.t + 65, rpc, clock=clk.now, sleep=clk.sleep, limiter=P.Limiter(clock=clk.now, sleep=clk.sleep))
    assert n == 4 and clk.t <= 1065 + 1e-9
    assert set(json.loads(next(iter(out.read_text().splitlines()))).keys()) == {"pool", "vault", "ctx_slot", "amount", "reason", "t_ms"}


def _reads(path, rows):
    path.write_text("".join(json.dumps({"pool": p, "vault": "v", "ctx_slot": s, "amount": a, "reason": "ok" if a is not None else "boost_vault_missing", "t_ms": 0}) + "\n"
                            for p, s, a in rows))


def test_join_exact_mismatch_quiet_boundary_null_and_allowlist(tmp_path):
    A, B, C, D = pk(1), pk(2), pk(3), pk(4)
    sd = tmp_path / "shadow"
    sd.mkdir()
    write_hour(sd, "h5-shadow-2026110301.jsonl", [
        {"type": "boost_slice", "pool": A, "slot": 11, "lamports": 30, "price": 1.5, "pnl": 3},
        {"type": "boost_slice", "pool": A, "slot": 12, "lamports": 20, "reserve": 7},
        {"type": "boost_slice", "pool": A, "slot": 10, "lamports": 999},  # at ctx_slot_a of the first pair: boundary; before the second pair
        {"type": "boost_slice", "pool": B, "slot": 105, "lamports": 40},
        {"type": "boost_slice", "pool": D, "slot": 205, "lamports": "x"},  # bad type ignored
        {"type": "trigger", "pool": A, "slot": 11, "lamports": 1000},  # other type ignored
    ])
    rd = tmp_path / "reads.jsonl"
    _reads(rd, [
        (A, 10, 1000), (A, 13, 950),    # boundary (slice at slot 10)
        (A, 20, 900),                    # (13,20]: S=0, delta=50 -> quiet nonzero
        (B, 100, 500), (B, 110, 460),   # active exact S=40
        (B, 120, 450),                   # (110,120] S=0, delta=10 quiet nonzero
        (B, 120, 440),                   # same slot: not a pair
        (C, 1, 5), (C, 2, 5), (C, 3, None), (C, 4, 5),  # quiet zero; null breaks pairs
        (D, 200, 300), (D, 210, 290),    # slice lamports not int -> S=0, delta=10 quiet nonzero
    ])
    res = P.join(str(rd), str(sd))
    assert res["boundary"] == 1
    assert res["pairs_total"] == 6
    assert res["active"] == 1 and res["active_exact"] == 1 and res["active_mismatch"] == 0
    assert res["quiet_delta0"] == 1
    assert sorted(res["quiet_nonzero_deltas"]) == [10, 10, 50] and res["quiet_delta_nonzero"] == 3
    assert res["pools_read"] == 4 and res["reads_by_reason"] == {"ok": 12, "boost_vault_missing": 1}


def test_join_active_mismatch_lists_delta_minus_s(tmp_path):
    A = pk(1)
    sd = tmp_path / "s"
    sd.mkdir()
    write_hour(sd, "h5-shadow-2026110301.jsonl", [{"type": "boost_slice", "pool": A, "slot": 15, "lamports": 30}, {"type": "boost_slice", "pool": A, "slot": 18, "lamports": 20}])
    rd = tmp_path / "r.jsonl"
    _reads(rd, [(A, 10, 1000), (A, 20, 940), (A, 30, 930)])  # (10,20]: S=50 delta=60 -> +10 (slice at 18); (20,30]: S=0 delta=10
    res = P.join(str(rd), str(sd))
    assert res["active"] == 1 and res["active_mismatch"] == 1 and res["active_mismatch_delta_minus_S"] == [10]
    assert res["quiet_nonzero_deltas"] == [10]


def test_join_output_has_no_forbidden_names(tmp_path, capsys):
    sd = tmp_path / "s"
    sd.mkdir()
    write_hour(sd, "h5-shadow-2026110301.jsonl", [{"type": "boost_slice", "pool": pk(1), "slot": 1, "lamports": 1, "price": 1}])
    rd = tmp_path / "r.jsonl"
    _reads(rd, [(pk(1), 1, 5), (pk(1), 2, 5)])
    assert P.main(["join", "--reads", str(rd), "--shadow-dir", str(sd)]) == 0
    out = capsys.readouterr().out
    for word in ("price", "reserve", "pnl", "trigger", "outcome"):
        assert word not in out.lower()
    assert "pairs_total: 1" in out
