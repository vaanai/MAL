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


def bs(pool, slot, lam=1, s0=1000, **kw):
    return {"type": "boost_slice", "pool": pool, "slot": slot, "s0": s0, "lamports": lam, "signature": f"sig{pool[:4]}{slot}", "event_index": 0, "sps": 0.4, **kw}


def test_horizon_comes_from_the_shadow_constant():
    from tools.h5_shadow import POOL_LIFE_S
    assert P.POOL_LIFE_S == POOL_LIFE_S == 400.0
    assert P.horizon_slots(0.4) == 1000 and P.horizon_slots(None) == 800


def test_pool_selection_follow_window_cap_and_fields(tmp_path):
    a, b, c, old = pk(1), pk(2), pk(3), pk(4)
    write_hour(tmp_path, "h5-shadow-2026110300.jsonl", [bs(old, 100, s0=100)])
    write_hour(tmp_path, "h5-shadow-2026110301.jsonl", [
        bs(a, 5000, s0=4500, price=9.9),          # followed until 5500: ahead of newest 5900? no -> 5500 < 5900 dropped
        bs(b, 5900, s0=5800, pnl=1),              # followed until 6800: kept
        bs(c, 5300, s0=5200),                     # until 6200: kept
        {"type": "trigger", "pool": pk(6), "slot": 9_999_999, "s0": 1},   # not parsed
        {"type": "pool", "pool": pk(7), "slot": 9_999_999, "s0": 1},
        bs(pk(5), "x"),
    ])
    assert P.recent_pools(str(tmp_path), 40) == [b, c]   # newest s0 first; old and a are past s0 + 400 s
    assert P.recent_pools(str(tmp_path), 1) == [b]
    assert P.recent_pools(str(tmp_path / "nope"), 5) == []


def test_pool_selection_uses_the_shadows_own_sps(tmp_path):
    a = pk(1)
    write_hour(tmp_path, "h5-shadow-2026110301.jsonl", [bs(a, 1100, s0=1000, sps=0.5), bs(pk(2), 1900, s0=1800)])
    # a: 400 / 0.5 = 800 slots -> until 1800, newest 1900: dropped
    assert P.recent_pools(str(tmp_path), 40) == [pk(2)]


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
    write_hour(tmp_path, "h5-shadow-2026110301.jsonl", [bs(pk(i), 1000 + i, s0=1000) for i in range(1, 9)])
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
    write_hour(tmp_path, "h5-shadow-2026110301.jsonl", [bs(pk(1), 1000, s0=1000)])
    clk = Clock()
    rpc = FakeRpc(clk)
    out = tmp_path / "r.jsonl"
    n = P.poll(str(tmp_path), str(out), 20, 40, clk.t + 65, rpc, clock=clk.now, sleep=clk.sleep, limiter=P.Limiter(clock=clk.now, sleep=clk.sleep))
    assert n == 4 and clk.t <= 1065 + 1e-9
    assert set(json.loads(next(iter(out.read_text().splitlines()))).keys()) == {"pool", "vault", "ctx_slot", "amount", "reason", "t_ms"}


def _reads(path, rows):
    """rows: (pool, ctx_slot, amount[, t_ms])"""
    path.write_text("".join(json.dumps({"pool": r[0], "vault": "v", "ctx_slot": r[1], "amount": r[2], "reason": "ok" if r[2] is not None else "boost_vault_missing",
                                        "t_ms": r[3] if len(r) > 3 else 0}) + "\n" for r in rows))


def _shadow(tmp_path, rows):
    sd = tmp_path / "shadow"
    sd.mkdir(exist_ok=True)
    write_hour(sd, "h5-shadow-2026110301.jsonl", rows)
    return sd


def test_join_exact_quiet_boundary_null_order_and_allowlist(tmp_path):
    A, B, C, D = pk(1), pk(2), pk(3), pk(4)
    sd = _shadow(tmp_path, [
        bs(A, 11, 30, price=1.5, pnl=3), bs(A, 12, 20, reserve=7), bs(A, 10, 999),   # slice at 10 = ctx_slot_a of the first pair: boundary
        bs(B, 1005, 40),
        bs(D, 1005, "x"),                                                           # bad lamports ignored -> D has no slices
        {"type": "trigger", "pool": C, "slot": 11, "lamports": 1000, "s0": 0},     # other type ignored -> C has no slices
    ])
    rd = tmp_path / "reads.jsonl"
    _reads(rd, [
        (A, 10, 1000), (A, 13, 950),    # boundary
        (A, 20, 900),                    # S=0, delta=50 -> quiet nonzero
        (B, 1000, 500), (B, 1010, 460),  # active exact S=40
        (B, 1020, 450),                  # S=0 delta=10 quiet nonzero
        (B, 1020, 440),                  # same slot: skipped (order)
        (C, 1, 5), (C, 2, 5),            # no slices
        (D, 1, 5), (D, 2, 5), (D, 3, None), (D, 4, 5),  # no slices; null pairs skipped
    ])
    res = P.join(str(rd), str(sd))
    assert res["boundary"] == 1 and res["no_slices"] == 2
    assert res["pairs_skipped_order"] == 1 and res["pairs_skipped_null"] == 2
    assert res["pairs_total"] == 6 and res["active"] == 1 and res["active_exact"] == 1 and res["active_mismatch"] == 0
    assert res["quiet_delta0"] == 0 and sorted(res["quiet_nonzero_deltas"]) == [10, 50]
    assert res["pools_read"] == 4 and res["reads_by_reason"] == {"ok": 12, "boost_vault_missing": 1}


def test_join_quiet_zero_and_active_mismatch(tmp_path):
    A = pk(1)
    sd = _shadow(tmp_path, [bs(A, 1015, 30, s0=1000), bs(A, 1018, 20, s0=1000)])
    rd = tmp_path / "r.jsonl"
    _reads(rd, [(A, 1010, 1000), (A, 1020, 940), (A, 1030, 940)])  # S=50 delta=60 -> +10; then quiet zero
    res = P.join(str(rd), str(sd))
    assert res["active_mismatch"] == 1 and res["active_mismatch_delta_minus_S"] == [10] and res["quiet_delta0"] == 1


def test_join_uncovered_after_follow_window(tmp_path):
    A = pk(1)
    sd = _shadow(tmp_path, [bs(A, 1015, 30, s0=1000)])  # sps 0.4 -> followed to slot 2000
    rd = tmp_path / "r.jsonl"
    _reads(rd, [(A, 1010, 1000), (A, 1990, 970), (A, 2000, 970), (A, 2001, 900), (A, 2500, 800)])
    res = P.join(str(rd), str(sd))
    # (1010,1990] active exact; (1990,2000] quiet zero (sb == s0 + horizon is inside); the last two end after the horizon
    assert (res["active_exact"], res["quiet_delta0"], res["uncovered"]) == (1, 1, 2)
    assert res["quiet_delta_nonzero"] == 0 and res["active_mismatch"] == 0


def test_join_gap_rows_void_pairs(tmp_path):
    A = pk(1)
    # gap shapes as h5_shadow.Engine._gap writes them: emit() adds v, schema, t_ms (wall); slot_jump carries from_slot/to_slot/missed_slots, silence silent_ms
    sd = _shadow(tmp_path, [
        bs(A, 1015, 30),
        {"type": "gap", "kind": "slot_jump", "open_pools": 3, "flags_pools": True, "from_slot": 1100, "to_slot": 1150, "missed_slots": 49, "v": 1, "schema": "x", "t_ms": 5},
        {"type": "gap", "kind": "silence", "open_pools": 3, "flags_pools": True, "silent_ms": 9000, "v": 1, "schema": "x", "t_ms": 50_000, "price": 3},
    ])
    rd = tmp_path / "r.jsonl"
    _reads(rd, [(A, 1010, 1000, 100_000), (A, 1020, 970, 101_000),   # exact (silence covers only [41 s, 50 s]; slot gap far)
                (A, 1090, 970, 102_000), (A, 1120, 900, 103_000),    # slot_jump 1100..1150 meets (1090, 1120] -> gap
                (A, 1160, 900, 104_000),                             # (1120,1160] meets 1100..1150 -> gap
                (A, 1170, 880, 105_000)])                            # (1160,1170]: no slot gap, silence far in time -> scored: S=0, delta=20
    res = P.join(str(rd), str(sd))
    assert res["active_exact"] == 1 and res["quiet_delta0"] == 1 and res["gap"] == 2 and res["quiet_nonzero_deltas"] == [20]
    assert res["active_mismatch"] == 0


def test_join_silence_covers_t_ms_minus_silent_ms(tmp_path):
    A = pk(1)
    sd = _shadow(tmp_path, [bs(A, 1015, 30), {"type": "gap", "kind": "silence", "silent_ms": 9000, "t_ms": 50_000, "open_pools": 1, "flags_pools": True}])
    rd = tmp_path / "r.jsonl"
    # silence = [t_ms - silent_ms, t_ms] = [41 s, 50 s], margin 5 s: a pair is void if t_b >= 36 s and t_a <= 55 s
    _reads(rd, [(A, 1010, 1000, 30_000), (A, 1020, 970, 35_000), (A, 1030, 970, 37_000), (A, 1040, 970, 60_000)])
    res = P.join(str(rd), str(sd))
    assert res["active_exact"] == 1 and res["gap"] == 2 and res["quiet_delta0"] == 0   # [30,35] clear; [35,37] and [37,60] void


def test_join_start_row_is_a_gap_at_its_t_ms(tmp_path):
    A = pk(1)
    start = {"type": "start", "rule": "r", "argv": ["--x"], "pid": 1, "t_ms": 200_000, "v": 1}
    sd = _shadow(tmp_path, [bs(A, 1015, 30), start])
    rd = tmp_path / "r.jsonl"
    _reads(rd, [(A, 1010, 1000, 100_000), (A, 1020, 970, 110_000), (A, 1030, 970, 190_000), (A, 1040, 970, 210_000)])
    res = P.join(str(rd), str(sd))
    assert (res["active_exact"], res["gap"], res["quiet_delta0"]) == (1, 1, 1)   # the pair [190 s, 210 s] contains 200 s; [110 s, 190 s] is clear


def test_join_unusable_gap_voids_every_pair_of_its_hour(tmp_path):
    A = pk(1)
    hour_ms = 1_793_667_600_000   # 2026-11-03T01:00:00Z
    sd = _shadow(tmp_path, [bs(A, 1015, 30), {"type": "gap", "kind": "weird", "open_pools": 1, "flags_pools": True}])
    rd = tmp_path / "r.jsonl"
    _reads(rd, [(A, 1010, 1000, hour_ms + 600_000), (A, 1020, 970, hour_ms + 700_000),           # inside the hour -> gap
                (A, 1030, 970, hour_ms + 7_200_000), (A, 1040, 970, hour_ms + 7_300_000)])       # 2 h later -> scored
    res = P.join(str(rd), str(sd))
    assert res["gap"] == 2 and res["quiet_delta0"] == 1 and res["active_exact"] == 0  # the middle pair spans into the hour


def test_join_unusable_gap_in_an_unnamed_hour_voids_everything(tmp_path):
    A = pk(1)
    sd = tmp_path / "shadow"
    sd.mkdir()
    write_hour(sd, "oddname.jsonl", [bs(A, 1015, 30), {"type": "gap", "kind": "weird"}])
    rd = tmp_path / "r.jsonl"
    _reads(rd, [(A, 1010, 1000, 5), (A, 1020, 970, 6)])
    # the glob only reads h5-shadow-*.jsonl: rename to a matching name without a readable hour
    (sd / "oddname.jsonl").rename(sd / "h5-shadow-xx.jsonl")
    res = P.join(str(rd), str(sd))
    assert res["gap"] == 1 and res["active_exact"] == 0


def test_prefilter_exact_type_strings_both_spacings_and_gap_key_lines(tmp_path, monkeypatch):
    A = pk(1)
    sd = tmp_path / "shadow"
    sd.mkdir()
    lines = [json.dumps(bs(A, 1015, 30), separators=(",", ":")),                                    # compact, as the writer serialises
             json.dumps({"type": "outcome", "pool": A, "gap": True, "pnl": 5}, separators=(",", ":")),   # `"gap":true` key, not a gap row
             json.dumps({"type": "trigger", "pool": A, "gap": True}),
             json.dumps({"type": "trigger", "pool": A, "gap": True, "note": "type gap"})]
    (sd / "h5-shadow-2026110301.jsonl").write_text("\n".join(lines) + "\n")
    rd = tmp_path / "r.jsonl"
    _reads(rd, [(A, 1010, 1000, 0), (A, 1020, 970, 1000)])
    parsed = []
    real = json.loads
    monkeypatch.setattr(P.json, "loads", lambda s, *a, **k: (parsed.append(s), real(s, *a, **k))[1])
    res = P.join(str(rd), str(sd))
    assert not [s for s in parsed if "outcome" in s or "trigger" in s]
    assert res["gap"] == 0 and res["active_exact"] == 1
    assert P._has('{"type":"gap"}', P.GAP_TAGS) and P._has('{"type": "gap"}', P.GAP_TAGS) and not P._has('{"gap":true}', P.GAP_TAGS)


# One boost_slice record in the exact key order and value shapes of h5_shadow.Engine._boost_slice (all values synthetic), written compact.
WRITER_SHAPE_SLICE = {"type": "boost_slice", "pool": pk(9), "mint": pk(8), "s0": 1000, "slot": 1015, "tx_index": None, "event_index": 2, "signature": "SYNTHSIG",
                      "lamports": 30, "sol": 3e-8, "block_time": 1, "t_recv_ms": 1, "t_i_s": 6.0, "sps": 0.4, "boost_src": "pda", "boost_id": pk(7), "boost_n": 1,
                      "boost_spent_sol": 3e-8, "v": 1, "schema": "x", "t_ms": 1}


def test_writer_shaped_slice_row_is_scored(tmp_path):
    A = WRITER_SHAPE_SLICE["pool"]
    sd = tmp_path / "shadow"
    sd.mkdir()
    (sd / "h5-shadow-2026110301.jsonl").write_text(json.dumps(WRITER_SHAPE_SLICE, separators=(",", ":")) + "\n")
    assert P.recent_pools(str(sd), 5) == [A]
    rd = tmp_path / "r.jsonl"
    _reads(rd, [(A, 1010, 1000), (A, 1020, 970)])
    assert P.join(str(rd), str(sd))["active_exact"] == 1

def test_join_dedupes_slices_by_signature_and_fallback(tmp_path):
    A, B = pk(1), pk(2)
    s1 = bs(A, 1015, 30, signature="X", event_index=3)
    s1b = dict(s1, t_recv_ms=99)                      # re-emitted after a restart
    s2 = bs(A, 1016, 20, signature="X", event_index=4)   # same tx, other event: kept
    n1 = {k: v for k, v in bs(B, 1015, 40, tx_index=7).items() if k != "signature"}   # no signature: fallback key
    sd = _shadow(tmp_path, [s1, s1b, s2, n1, dict(n1), dict(n1, lamports=41)])
    rd = tmp_path / "r.jsonl"
    _reads(rd, [(A, 1010, 1000), (A, 1020, 950), (B, 1010, 1000), (B, 1020, 919)])
    res = P.join(str(rd), str(sd))
    assert res["slice_dupes_removed"] == 2
    assert res["active_exact"] == 2 and res["active_mismatch"] == 0


def test_join_lines_without_an_allowed_type_string_are_never_parsed(tmp_path, monkeypatch):
    A = pk(1)
    sd = _shadow(tmp_path, [bs(A, 1015, 30), {"type": "outcome", "pool": A, "slot": 1015, "pnl": 5}, {"type": "trigger", "pool": A}])
    rd = tmp_path / "r.jsonl"
    _reads(rd, [(A, 1010, 1000), (A, 1020, 970)])
    parsed = []
    real = json.loads
    monkeypatch.setattr(P.json, "loads", lambda s, *a, **k: (parsed.append(s), real(s, *a, **k))[1])
    P.join(str(rd), str(sd))
    shadow_parsed = [s for s in parsed if "outcome" in s or "trigger" in s]
    assert shadow_parsed == []


def test_join_output_has_no_forbidden_names(tmp_path, capsys):
    sd = _shadow(tmp_path, [bs(pk(1), 1, 1, price=1)])
    rd = tmp_path / "r.jsonl"
    _reads(rd, [(pk(1), 1, 5), (pk(1), 2, 5)])
    assert P.main(["join", "--reads", str(rd), "--shadow-dir", str(sd)]) == 0
    out = capsys.readouterr().out
    for word in ("price", "reserve", "pnl", "trigger", "outcome"):
        assert word not in out.lower()
    assert "pairs_total: 1" in out
