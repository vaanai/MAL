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


HOUR_MS = 1_793_667_600_000   # 2026-11-03T01:00:00Z, the hour of the fixture file h5-shadow-2026110301.jsonl
T0 = HOUR_MS + 1_800_000      # default read time: half past


def hbs(lo=T0 - 1_000_000, hi=T0 + 1_000_000, step=60_000, lag=2_000, stale_from=None, skip=()):
    """hb rows in the shape of housekeeping(): {"type": "hb", **status_snapshot} with t_ms and last_event_ms (synthetic values)."""
    rows = []
    t = lo
    while t <= hi:
        if not any(a <= t <= b for a, b in skip):
            last = t - lag if stale_from is None or t < stale_from else stale_from - lag
            rows.append({"type": "hb", "v": 1, "schema": "x", "t_ms": t, "last_event_ms": last, "open_pools": 2, "hw_slot": 5})
        t += step
    return rows


def _reads(path, rows):
    """rows: (pool, ctx_slot, amount[, t_ms])"""
    path.write_text("".join(json.dumps({"pool": r[0], "vault": "v", "ctx_slot": r[1], "amount": r[2], "reason": "ok" if r[2] is not None else "boost_vault_missing",
                                        "t_ms": r[3] if len(r) > 3 else T0}) + "\n" for r in rows))


def _shadow(tmp_path, rows, hb=True, name="h5-shadow-2026110301.jsonl"):
    sd = tmp_path / "shadow"
    sd.mkdir(exist_ok=True)
    write_hour(sd, name, list(rows) + (hbs() if hb is True else (hb or [])))
    return sd


def test_constants_are_derived_from_the_shadow():
    from tools import h5_shadow
    assert P.COVER_BACK_MS == int((h5_shadow.POOL_LIFE_S + 30) * 1000) == 430_000
    assert P.HB_PERIOD_MS == 60_000 and P.HB_MAX_SPACING_MS == 120_000 and P.SILENCE_MS == int(h5_shadow.SILENCE_S * 1000)


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
    assert res["boundary"] == 1 and res["no_slices"] == 2 and res["gap"] == 0
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
    assert (res["active_exact"], res["quiet_delta0"], res["uncovered"]) == (1, 1, 2)
    assert res["quiet_delta_nonzero"] == 0 and res["active_mismatch"] == 0


def _one_pair(tmp_path, extra=(), hb=True, ta=T0, tb=T0 + 10_000, name="h5-shadow-2026110301.jsonl"):
    """One exact pair (slice 30, delta 30) with the given extra shadow rows; returns the join result."""
    A = pk(1)
    sd = _shadow(tmp_path, [bs(A, 1015, 30)] + list(extra), hb=hb, name=name)
    rd = tmp_path / "r.jsonl"
    _reads(rd, [(A, 1010, 1000, ta), (A, 1020, 970, tb)])
    return P.join(str(rd), str(sd))


def test_healthy_shadow_is_scored(tmp_path):
    res = _one_pair(tmp_path)
    assert res["active_exact"] == 1 and res["gap"] == 0


def test_a_gap_row_in_the_window_voids_the_pair_and_one_outside_does_not(tmp_path):
    g = {"type": "gap", "kind": "silence", "open_pools": 1, "flags_pools": True, "silent_ms": 9000, "v": 1, "schema": "x"}
    assert _one_pair(tmp_path, [dict(g, t_ms=T0 - 429_000)])["gap"] == 1          # inside [ta - 430 s, tb + 5 s]
    assert _one_pair(tmp_path, [dict(g, t_ms=T0 + 14_000)])["gap"] == 1           # at most 5 s after tb
    assert _one_pair(tmp_path, [dict(g, t_ms=T0 - 431_000)])["active_exact"] == 1  # outside
    assert _one_pair(tmp_path, [dict(g, t_ms=T0 + 16_000)])["active_exact"] == 1


def test_silence_with_no_end_row_voids_via_hb_last_event(tmp_path):
    # no gap row at all (the file ends before it is written); the hb shows the feed silent: last_event_ms stuck 40 s behind
    res = _one_pair(tmp_path, hb=hbs(stale_from=T0 - 200_000, lag=2_000))
    assert res["gap"] == 1 and res["active_exact"] == 0


def test_shadow_down_between_reads_voids_the_pair(tmp_path):
    res = _one_pair(tmp_path, hb=hbs(skip=[(T0 + 1, T0 + 400_000)]), ta=T0, tb=T0 + 300_000)   # no hb after tb within 2 periods
    assert res["gap"] == 1 and res["active_exact"] == 0


def test_hb_spacing_more_than_twice_the_period_voids_and_exactly_twice_is_ok(tmp_path):
    assert _one_pair(tmp_path, hb=hbs(skip=[(T0 - 100_000, T0 - 1)]))["gap"] == 1            # hbs at T0-180 s then T0: 180 s apart... skip leaves a 180 s hole
    ok = [h for h in hbs() if (h["t_ms"] - hbs()[0]["t_ms"]) % 120_000 == 0]                 # every second hb: spaced exactly 2 x period
    assert _one_pair(tmp_path, hb=ok)["active_exact"] == 1


def test_no_hb_at_or_before_the_window_start_voids(tmp_path):
    assert _one_pair(tmp_path, hb=hbs(lo=T0 - 300_000))["gap"] == 1


def test_restart_with_pools_open_voids_pairs_around_it(tmp_path):
    start = {"type": "start", "rule": "r", "argv": ["--x"], "pid": 1, "t_ms": T0 - 60_000, "v": 1}
    stop = {"type": "stop", "open_pools": 4, "t_ms": T0 - 61_000, "v": 1}
    assert _one_pair(tmp_path, [start])["gap"] == 1
    assert _one_pair(tmp_path, [stop])["gap"] == 1
    far = dict(start, t_ms=T0 - 900_000)
    assert _one_pair(tmp_path, [far])["active_exact"] == 1


def test_slot_jump_overlapping_the_pool_life_slots_voids(tmp_path):
    jump = {"type": "gap", "kind": "slot_jump", "from_slot": 600, "to_slot": 700, "missed_slots": 99, "open_pools": 1, "flags_pools": True, "t_ms": T0 - 900_000}
    assert _one_pair(tmp_path, [jump])["gap"] == 1                      # (1010 - 1000, 1020] = (10, 1020] contains 600..700 (0.4 s/slot: 1000 slots)
    far = dict(jump, from_slot=5, to_slot=9)
    assert _one_pair(tmp_path, [far])["active_exact"] == 1


def test_a_gap_with_no_usable_time_voids_its_whole_hour_and_an_unnamed_hour_voids_all(tmp_path):
    weird = {"type": "gap", "kind": "weird", "open_pools": 1, "flags_pools": True}
    assert _one_pair(tmp_path, [weird])["gap"] == 1
    later = _one_pair(tmp_path, [weird], ta=T0 + 7_200_000, tb=T0 + 7_210_000, hb=hbs(lo=T0 + 7_200_000 - 1_000_000, hi=T0 + 7_200_000 + 1_000_000))
    assert later["active_exact"] == 1 and later["gap"] == 0              # 2 h later, hour of the file does not meet [ta - 430 s, tb]
    assert _one_pair(tmp_path, [weird], name="h5-shadow-xx.jsonl")["gap"] == 1


def test_prefilter_exact_type_strings_both_spacings_and_gap_key_lines(tmp_path, monkeypatch):
    A = pk(1)
    sd = tmp_path / "shadow"
    sd.mkdir()
    compact = lambda r: json.dumps(r, separators=(",", ":"))   # noqa: E731 - how the writer serialises
    lines = [compact(bs(A, 1015, 30))] + [compact(h) for h in hbs()] + [
        compact({"type": "outcome", "pool": A, "gap": True, "pnl": 5}),   # `"gap":true` key, not a gap row
        json.dumps({"type": "trigger", "pool": A, "gap": True}),
        json.dumps({"type": "trigger", "pool": A, "gap": True, "note": "type gap"}),
        json.dumps({"type": "outcome", "pool": A, "hb": True, "start": 1, "stop": 2})]
    (sd / "h5-shadow-2026110301.jsonl").write_text("\n".join(lines) + "\n")
    rd = tmp_path / "r.jsonl"
    _reads(rd, [(A, 1010, 1000), (A, 1020, 970)])
    parsed = []
    real = json.loads
    monkeypatch.setattr(P.json, "loads", lambda s, *a, **k: (parsed.append(s), real(s, *a, **k))[1])
    res = P.join(str(rd), str(sd))
    assert not [s for s in parsed if "outcome" in s or "trigger" in s]
    assert res["gap"] == 0 and res["active_exact"] == 1
    for tags, name in ((P.GAP_TAGS, "gap"), (P.HB_TAGS, "hb"), (P.START_TAGS, "start"), (P.STOP_TAGS, "stop"), (P.SLICE_TAGS, "boost_slice")):
        assert P._has('{"type":"%s"}' % name, tags) and P._has('{"type": "%s"}' % name, tags) and not P._has('{"%s":true}' % name, tags)


# One boost_slice record in the exact key order and value shapes of h5_shadow.Engine._boost_slice (all values synthetic), written compact.
WRITER_SHAPE_SLICE = {"type": "boost_slice", "pool": pk(9), "mint": pk(8), "s0": 1000, "slot": 1015, "tx_index": None, "event_index": 2, "signature": "SYNTHSIG",
                      "lamports": 30, "sol": 3e-8, "block_time": 1, "t_recv_ms": 1, "t_i_s": 6.0, "sps": 0.4, "boost_src": "pda", "boost_id": pk(7), "boost_n": 1,
                      "boost_spent_sol": 3e-8, "v": 1, "schema": "x", "t_ms": 1}


def test_writer_shaped_slice_row_is_scored(tmp_path):
    A = WRITER_SHAPE_SLICE["pool"]
    sd = tmp_path / "shadow"
    sd.mkdir()
    rows = [WRITER_SHAPE_SLICE] + hbs()
    (sd / "h5-shadow-2026110301.jsonl").write_text("".join(json.dumps(r, separators=(",", ":")) + "\n" for r in rows))
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


def test_join_output_has_no_forbidden_names(tmp_path, capsys):
    sd = _shadow(tmp_path, [bs(pk(1), 1, 1, price=1)])
    rd = tmp_path / "r.jsonl"
    _reads(rd, [(pk(1), 1, 5), (pk(1), 2, 5)])
    assert P.main(["join", "--reads", str(rd), "--shadow-dir", str(sd)]) == 0
    out = capsys.readouterr().out
    for word in ("price", "reserve", "pnl", "trigger", "outcome"):
        assert word not in out.lower()
    assert "pairs_total: 1" in out


# ---- level subcommand (QP-RULING-1103-oc-premise Ruling 2) ----
B = P.BUDGET


def _lv(tmp_path, slices, reads, hb=True, extra=()):
    sd = _shadow(tmp_path, list(slices) + list(extra), hb=hb)
    rd = tmp_path / "lr.jsonl"
    _reads(rd, reads)
    return P.level(str(rd), str(sd))


def test_level_budget_comes_from_the_shadow():
    from tools.h5_shadow import BOOST_BUDGET
    assert P.BUDGET == int(BOOST_BUDGET) == 17_585_000_000


def test_level_k_is_amount_plus_earlier_slices_only(tmp_path):
    A = pk(1)
    # slices at 1010 (400) and 1015 (600); read at 1020 sees both; a slice at 1030 is after the read and ignored
    res = _lv(tmp_path, [bs(A, 1010, 400), bs(A, 1015, 600), bs(A, 1030, 7)], [(A, 1020, B - 1000)])
    assert res["clean_reads"] == 1 and res["k_equal_budget"] == 1 and res["k_not_equal_budget"] == 0


def test_level_mismatch_lists_k_minus_budget_and_fails(tmp_path):
    A = pk(1)
    res = _lv(tmp_path, [bs(A, 1010, 400)], [(A, 1020, B - 400 + 3)])
    assert res["k_minus_budget"] == [3] and res["verdict"] == "FAIL"


def test_level_boundary_slice_at_read_slot_is_excluded(tmp_path):
    A = pk(1)
    res = _lv(tmp_path, [bs(A, 1010, 400), bs(A, 1020, 5)], [(A, 1020, B - 400)])
    assert res["clean_reads"] == 0 and res["boundary"] == 1 and res["non_clean"]["boundary"] == 1


def test_level_pool_without_slices_and_null_reads(tmp_path):
    A, Bp = pk(1), pk(2)
    res = _lv(tmp_path, [bs(A, 1010, 4)], [(Bp, 1020, B), (A, None, B), (A, 1020, None)])
    assert res["non_clean"]["no_slices"] == 1 and res["non_clean"]["null"] == 2 and res["clean_reads"] == 0


def test_level_beyond_horizon_is_not_clean(tmp_path):
    A = pk(1)
    far = 1000 + P.horizon_slots(0.4) + 1
    res = _lv(tmp_path, [bs(A, 1010, 4)], [(A, far, B - 4), (A, 1000 + P.horizon_slots(0.4), B - 4)])
    assert res["non_clean"]["beyond_horizon"] == 1 and res["clean_reads"] == 1


def test_level_uses_positive_coverage_gap_and_no_hb(tmp_path):
    A = pk(1)
    gap = {"type": "gap", "t_ms": T0 - 100_000, "from_slot": 1, "to_slot": 2}
    assert _lv(tmp_path, [bs(A, 1010, 4)], [(A, 1020, B - 4)], extra=[gap])["non_clean"]["gap"] == 1
    d = tmp_path / "n"
    d.mkdir()
    assert _lv(d, [bs(A, 1010, 4)], [(A, 1020, B - 4)], hb=[])["non_clean"]["gap"] == 1


def test_level_late_reads_pools_and_per_pool_min_max(tmp_path):
    A = pk(1)
    late = int(0.9 * B)
    res = _lv(tmp_path, [bs(A, 1010, late), bs(A, 1015, 10)], [(A, 1012, B - late), (A, 1020, B - late - 10)])
    assert res["clean_reads"] == 2 and res["late_clean_reads"] == 2 and res["late_pools"] == 1
    assert res["pool_k_min_max"][A] == (B, B)


def _many(tmp_path, n_pools, reads_each, late=True, bad=None):
    tmp_path.mkdir()
    rows, reads = [], []
    for i in range(1, n_pools + 1):
        p = pk(i)
        e = int(0.95 * B) if late else 10
        rows.append(bs(p, 1010, e))
        for j in range(reads_each):
            reads.append((p, 1020 + j, B - e + (bad if bad is not None and i == 1 and j == 0 else 0)))
    return _lv(tmp_path, rows, reads)


def test_level_pass_fail_not_yet(tmp_path):
    assert _many(tmp_path / "a", 10, 3)["verdict"] == "PASS"
    assert _many(tmp_path / "b", 10, 3, bad=1)["verdict"] == "FAIL"
    assert _many(tmp_path / "c", 9, 4)["verdict"] == "NOT YET"      # 36 reads, 9 pools
    assert _many(tmp_path / "d", 10, 2)["verdict"] == "NOT YET"     # 20 reads
    assert _many(tmp_path / "e", 10, 3, late=False)["verdict"] == "NOT YET"  # no late pools


def test_level_cli_prints_counts_only(tmp_path, capsys):
    sd = _shadow(tmp_path, [bs(pk(1), 1010, 4)])
    rd = tmp_path / "r.jsonl"
    _reads(rd, [(pk(1), 1020, B - 4)])
    assert P.main(["level", "--reads", str(rd), "--shadow-dir", str(sd)]) == 0
    out = capsys.readouterr().out
    assert "verdict: NOT YET" in out and "clean_reads: 1" in out
    for word in ("price", "reserve", "pnl", "trigger", "outcome"):
        assert word not in out.lower()
