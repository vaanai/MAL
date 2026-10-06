"""Tests for tools/pumpswap_lp_history.py. Fixtures only; the RPC is a fake, nothing touches the network."""

from __future__ import annotations

import base64

import pytest
from solders.pubkey import Pubkey

import tools.pumpswap_lp_history as lph


def pk(i: int) -> str:
    return str(Pubkey.from_bytes(bytes([i]) * 32))


def event_bytes(kind: str, pool: str, lp: int, s_before: int, ts: int = 1_790_000_000) -> bytes:
    disc = lph.DEPOSIT_DISC if kind == "deposit" else lph.WITHDRAW_DISC
    u64 = [lp, 11, 22, 33, 44, 55, 66, 77, 88, s_before]
    raw = disc + ts.to_bytes(8, "little", signed=True) + b"".join(x.to_bytes(8, "little") for x in u64)
    for k in (pool, pk(201), pk(202), pk(203), pk(204)):
        raw += bytes(Pubkey.from_string(k))
    return raw


def log_line(raw: bytes) -> str:
    return "Program data: " + base64.b64encode(raw).decode()


def ev(s: int, d: int, slot: int = 1, t: int | None = None) -> dict:
    return {"s_before": s, "lp_delta": d, "slot": slot, "block_time": t}


# ---- decode -----------------------------------------------------------------------------------------------------


def test_decode_deposit_and_withdraw_fields() -> None:
    pool = pk(1)
    d = lph.decode_event(event_bytes("deposit", pool, 81461253, 4193388325949))
    assert d is not None
    assert (d["kind"], d["lp_token_amount"], d["lp_mint_supply"], d["pool"], d["timestamp"]) == ("deposit", 81461253, 4193388325949, pool, 1_790_000_000)
    assert (d["max_or_min_base"], d["base_amount"], d["quote_amount"], d["user"]) == (11, 77, 88, pk(201))
    w = lph.decode_event(event_bytes("withdraw", pool, 5428194497, 4198816479321))
    assert w is not None and w["kind"] == "withdraw" and w["user_pool_token_account"] == pk(204)


def test_decode_rejects_other_discriminator_and_short() -> None:
    raw = event_bytes("deposit", pk(1), 1, 2)
    assert lph.decode_event(b"\x00" * 8 + raw[8:]) is None
    assert lph.decode_event(raw[:-1]) is None


def test_events_from_logs_filters_pool_and_signs_delta() -> None:
    pool, other = pk(1), pk(2)
    logs = [
        "Program log: Instruction: Deposit",
        log_line(event_bytes("deposit", pool, 100, 1000)),
        log_line(event_bytes("withdraw", other, 7, 1100)),  # another pool: dropped
        log_line(event_bytes("withdraw", pool, 40, 1100)),
        "Program data: !!notbase64",
        log_line(b"\x01" * 300),  # some other event
    ]
    got = lph.events_from_logs(logs, pool)
    assert [(e["kind"], e["s_before"], e["lp_delta"]) for e in got] == [("deposit", 1000, 100), ("withdraw", 1100, -40)]


# ---- replay -----------------------------------------------------------------------------------------------------


def test_replay_lab_note_single_ops() -> None:
    assert lph.replay_forward(17584505649, [ev(4193388325949, 81461253)]) == 17584847247  # 8ewuF2o8
    assert lph.replay_forward(17607267834, [ev(4198816479321, -5428194497)]) == 17584505306  # AgcjmdfX


CYJW = [(4213950216455, 11302677139), (4225252893594, 4519406819), (4229772300413, 9938317900), (4239710618313, 111562129), (4239822180442, 573074383), (4240395254825, 4844616701), (4245239871526, -573074383), (4244666797143, 120435448)]


def test_replay_cyjwkn_eight_op_chain() -> None:
    events = [ev(s, d, slot=i) for i, (s, d) in enumerate(CYJW)]
    assert lph.replay_forward(17670729486, events) == 17800041063
    # supplies chain, as the real event stream does
    for (s, d), (s2, _) in zip(CYJW, CYJW[1:]):
        assert s + d == s2


def test_inverse_within_one_lamport_per_event() -> None:
    events = [ev(s, d, slot=i) for i, (s, d) in enumerate(CYJW)]
    back = lph.v0_before(17800041063, events)
    assert abs(back - 17670729486) <= len(events)
    assert abs(lph.replay_forward(back, events) - 17800041063) <= len(events)
    one = [ev(4193388325949, 81461253)]
    assert abs(lph.v0_before(17584847247, one) - 17584505649) <= 1


def test_replay_zero_supply_raises() -> None:
    with pytest.raises(ValueError):
        lph.replay_forward(5, [ev(0, 10)])
    with pytest.raises(ValueError):
        lph.v0_before(5, [ev(10, -10)])


# ---- consistent / ambiguity -------------------------------------------------------------------------------------


def test_consistent_equal_and_definite_event() -> None:
    assert lph.consistent(100, 100, [], []) is True
    e = ev(1000, 100)
    assert lph.consistent(17584505649, lph.replay_forward(17584505649, [e]), [e], []) is True
    assert lph.consistent(17584505649, 17584505649 + 5000, [e], []) is False
    assert lph.consistent(17584505649, 17584505649 + 5000, [], []) is False  # a move with no event is not explained


def test_consistent_ambiguous_placement_either_side() -> None:
    e1, e2 = ev(1000, 100, slot=1), ev(1100, -50, slot=2)
    a = 17_000_000_000
    only_e2 = lph.replay_forward(a, [e2])  # e1 fell before the first read: chain value is only used partly
    assert lph.consistent(a, only_e2, [], [e1, e2]) is True  # some placement (apply e2 only) reproduces it
    both = lph.replay_forward(a, [e1, e2])
    assert lph.consistent(a, both, [], [e1, e2]) is True
    assert lph.consistent(a, both + 10_000, [], [e1, e2]) is False
    # a definite event plus an ambiguous one
    assert lph.consistent(a, both, [e1], [e2]) is True
    assert lph.consistent(a, lph.replay_forward(a, [e1]), [e1], [e2]) is True


def test_consistent_too_many_ambiguous_is_none() -> None:
    evs = [ev(10_000 + i, 1, slot=i) for i in range(11)]
    assert lph.consistent(5, 6, [], evs) is None
    assert lph.consistent(5, 5, [], evs) is True  # equal needs no events


# ---- fetch_lp_history with a fake chain --------------------------------------------------------------------------


def pool_account(lp_mint: str, lp_supply: int = 1) -> dict:
    data = bytearray(301)
    data[107:139] = bytes(Pubkey.from_string(lp_mint))
    data[203:211] = lp_supply.to_bytes(8, "little")
    return {"data": [base64.b64encode(bytes(data)).decode(), "base64"]}


class FakeChain:
    """accounts: pool -> lp_mint or None; sigs: lp_mint -> [(sig, slot, blockTime, err, [events])] newest first."""

    def __init__(self, accounts: dict, sigs: dict, fail_tx: set | None = None, fail_sigs: bool = False):
        self.accounts, self.sigs, self.fail_tx, self.fail_sigs = accounts, sigs, fail_tx or set(), fail_sigs
        self.calls: list[tuple[str, int]] = []
        self.sleeps: list[float] = []

    def __call__(self, method: str, params: list):
        self.calls.append((method, 0))
        if method == "getMultipleAccounts":
            return {"context": {"slot": 5}, "value": [None if self.accounts.get(p) is None else pool_account(self.accounts[p]) for p in params[0]]}
        if method == "getSignaturesForAddress":
            if self.fail_sigs:
                raise SystemExit("rpc failed https://x/?api-key=SECRET")
            rows = self.sigs[params[0]]
            opts = params[1]
            start = 0
            if opts.get("before"):
                start = 1 + [r[0] for r in rows].index(opts["before"])
            page = rows[start : start + opts["limit"]]
            return [{"signature": r[0], "slot": r[1], "blockTime": r[2], "err": r[3]} for r in page]
        if method == "getTransaction":
            assert params[1] == {"encoding": "json", "maxSupportedTransactionVersion": 1}
            if params[0] in self.fail_tx:
                raise SystemExit("rpc failed")
            for rows in self.sigs.values():
                for r in rows:
                    if r[0] == params[0]:
                        return {"slot": r[1], "blockTime": r[2], "meta": {"err": None, "logMessages": [log_line(x) for x in r[4]]}}
        raise AssertionError(method)


def run_hist(chain: FakeChain, pools: list[str], t0: int, t1: int, **kw):
    sleeps: list[float] = []
    out, calls = lph.fetch_lp_history(chain, pools, t0, t1, sleep=sleeps.append, clock=lambda: 0.0, **kw)
    chain.sleeps = sleeps
    return out, calls


def test_fetch_history_events_sorted_resolved_and_err_skipped() -> None:
    pool, mint = pk(1), pk(50)
    rows = [  # newest first
        ("s4", 40, 4000, None, [event_bytes("withdraw", pool, 30, 1100)]),
        ("s3", 30, 3000, {"InstructionError": 1}, [event_bytes("deposit", pool, 999, 1100)]),  # failed tx: skipped
        ("s2", 20, 2000, None, [event_bytes("deposit", pool, 100, 1000), event_bytes("deposit", pool, 0, 1100)]),
        ("s1", 10, 1000, None, [event_bytes("deposit", pool, 1, 1)]),  # before t_from: not fetched
    ]
    chain = FakeChain({pool: mint}, {mint: rows})
    out, calls = run_hist(chain, [pool], 1500, 5000)
    e = out[pool]
    assert e["resolved"] and e["reason"] is None and e["lp_mint"] == mint
    assert [(x["sig"], x["slot"], x["block_time"], x["kind"], x["s_before"], x["lp_delta"]) for x in e["events"]] == [("s2", 20, 2000, "deposit", 1000, 100), ("s2", 20, 2000, "deposit", 1100, 0), ("s4", 40, 4000, "withdraw", 1100, -30)]
    assert calls == len(chain.calls) == 1 + 1 + 2  # accounts, one signature page, two transactions
    assert ("getTransaction", 0) in chain.calls


def test_fetch_history_rate_limit_gap() -> None:
    pool, mint = pk(1), pk(50)
    chain = FakeChain({pool: mint}, {mint: [("s1", 1, 100, None, [])]})
    run_hist(chain, [pool], 0, 1000)
    assert chain.sleeps and all(abs(x - 0.2) < 1e-9 for x in chain.sleeps)
    with pytest.raises(ValueError):
        lph.fetch_lp_history(chain, [pool], 0, 1, rps=50)


def test_fetch_history_account_missing_unresolved() -> None:
    chain = FakeChain({pk(1): None}, {})
    out, _ = run_hist(chain, [pk(1)], 0, 10)
    assert out[pk(1)]["resolved"] is False and out[pk(1)]["reason"] == "account_missing"


def test_fetch_history_pagination_reaches_t_from(monkeypatch) -> None:
    monkeypatch.setattr(lph, "SIG_PAGE", 2)
    pool, mint = pk(1), pk(50)
    rows = [(f"s{i}", 100 - i, 1000 - i * 10, None, []) for i in range(10)]  # newest first, blockTime 1000 .. 910
    chain = FakeChain({pool: mint}, {mint: rows})
    out, _ = run_hist(chain, [pool], 975, 2000)
    assert out[pool]["resolved"] is True
    pages = [c for c in chain.calls if c[0] == "getSignaturesForAddress"]
    assert len(pages) == 2  # [1000,990] [980,970]: the oldest 970 < 975, so paging stops


def test_fetch_history_paging_cannot_reach_t_from_is_unresolved(monkeypatch) -> None:
    monkeypatch.setattr(lph, "SIG_PAGE", 2)
    pool, mint = pk(1), pk(50)
    rows = [(f"s{i}", 100 - i, 1000 - i * 10, None, []) for i in range(10)]
    chain = FakeChain({pool: mint}, {mint: rows})
    out, _ = run_hist(chain, [pool], 0, 2000, max_pages=2)
    assert out[pool]["resolved"] is False and out[pool]["reason"] == "paging_did_not_reach_t_from"


def test_fetch_history_tx_failure_unresolved_and_url_not_leaked() -> None:
    pool, mint = pk(1), pk(50)
    rows = [("s1", 1, 100, None, [event_bytes("deposit", pool, 1, 10)])]
    chain = FakeChain({pool: mint}, {mint: rows}, fail_tx={"s1"})
    out, _ = run_hist(chain, [pool], 0, 1000)
    assert out[pool]["resolved"] is False and out[pool]["reason"].startswith("tx_fetch_failed")
    chain = FakeChain({pool: mint}, {mint: rows}, fail_sigs=True)
    out, _ = run_hist(chain, [pool], 0, 1000)
    assert out[pool]["reason"].startswith("signatures_fetch_failed")
    assert "SECRET" not in repr(out) and "api-key" not in repr(out)


def test_fetch_history_supply_chain_break_unresolved() -> None:
    pool, mint = pk(1), pk(50)
    rows = [("s2", 2, 200, None, [event_bytes("deposit", pool, 5, 777)]), ("s1", 1, 100, None, [event_bytes("deposit", pool, 10, 1000)])]
    chain = FakeChain({pool: mint}, {mint: rows})
    out, _ = run_hist(chain, [pool], 0, 1000)
    assert out[pool]["resolved"] is False and out[pool]["reason"] == "supply_chain_break"
