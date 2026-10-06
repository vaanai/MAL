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
    for (s, d), (s2, _) in zip(CYJW, CYJW[1:]):  # supplies chain, as the real event stream does
        assert s + d == s2


def test_inverse_is_smallest_preimage() -> None:
    events = [ev(s, d, slot=i) for i, (s, d) in enumerate(CYJW)]
    back = lph.v0_before(17800041063, events)
    assert back is not None
    assert lph.replay_forward(back, events) == 17800041063  # a true preimage
    assert lph.replay_forward(back - 1, events) < 17800041063  # and the smallest one
    assert back <= 17670729486 <= lph.v0_before_interval(17800041063, events)[1]  # the real value is inside the preimage interval
    one = [ev(4193388325949, 81461253)]
    b1 = lph.v0_before(17584847247, one)
    assert lph.replay_forward(b1, one) == 17584847247 and lph.replay_forward(b1 - 1, one) < 17584847247
    assert b1 <= 17584505649


def test_inverse_none_when_no_preimage() -> None:
    # a 3x expansion skips integers: floor(x * 3) never equals 4
    assert lph.v0_before(4, [ev(10, 20)]) is None
    assert lph.v0_before(3, [ev(10, 20)]) == 1


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
    assert lph.consistent(a, lph.replay_forward(a, [e2]), [], [e1, e2]) is True  # e1 fell before the first read
    both = lph.replay_forward(a, [e1, e2])
    assert lph.consistent(a, both, [], [e1, e2]) is True
    assert lph.consistent(a, both + 10_000, [], [e1, e2]) is False
    assert lph.consistent(a, both, [e1], [e2]) is True
    assert lph.consistent(a, lph.replay_forward(a, [e1]), [e1], [e2]) is True


def test_consistent_too_many_ambiguous_is_none() -> None:
    evs = [ev(10_000 + i, 1, slot=i) for i in range(11)]
    assert lph.consistent(5, 6, [], evs) is None
    assert lph.consistent(5, 5, [], evs) is True  # equal needs no events


def test_possible_values_helper() -> None:
    e1 = ev(1000, 100, slot=1)
    a = 17_000_000_000
    assert lph.possible_values(a, [], [e1]) == {a, lph.replay_forward(a, [e1])}  # placements disagree: more than one value
    assert lph.possible_values(a, [e1], []) == {lph.replay_forward(a, [e1])}  # nothing ambiguous: one value
    assert lph.possible_values(a, [], [ev(10_000 + i, 1, slot=i) for i in range(11)]) is None
    back = lph.possible_values(17_100_000_000, [e1], [], direction="backward")
    assert back == {lph.v0_before(17_100_000_000, [e1])}


def test_v0_at_slot_relative_to_anchor_and_trade() -> None:
    a = 17_000_000_000
    e = ev(1000, 100, slot=50)
    fwd = lph.replay_forward(a, [e])
    r = lph.v0_at(70, a, (60, 61), [e])  # anchor read after the event, trade after it: one value, no move
    assert r == {"values": {a}, "anchor_ambiguous": False, "target_ambiguous": False}
    r = lph.v0_at(70, a, (10, 11), [e])  # anchor before the event: moved forward
    assert r == {"values": {fwd}, "anchor_ambiguous": False, "target_ambiguous": False}
    r = lph.v0_at(40, fwd, (60, 61), [e])  # trade before the event, anchor after: invert (smallest preimage)
    assert r["values"] == {lph.v0_before(fwd, [e])} and not r["anchor_ambiguous"] and not r["target_ambiguous"]
    r = lph.v0_at(70, a, (49, 51), [e])  # the event sits inside the anchor's slot span: anchor ambiguity only
    assert r == {"values": {a, fwd}, "anchor_ambiguous": True, "target_ambiguous": False}
    r = lph.v0_at(50, a, (10, 11), [e])  # the event sits in the trade's own slot: target ambiguity only
    assert r == {"values": {a, fwd}, "anchor_ambiguous": False, "target_ambiguous": True}
    assert lph.v0_at(70, a, (10, 11), [])["values"] == {a}
    assert lph.v0_at(5, a, (10, 11), [ev(10_000 + i, 1, slot=10) for i in range(11)]) is None


# ---- fetch_lp_history with a fake chain --------------------------------------------------------------------------


def pool_account(lp_mint: str, lp_supply: int = 1) -> dict:
    data = bytearray(301)
    data[107:139] = bytes(Pubkey.from_string(lp_mint))
    data[203:211] = lp_supply.to_bytes(8, "little")
    return {"data": [base64.b64encode(bytes(data)).decode(), "base64"]}


class FakeChain:
    """accounts: pool -> lp_mint or None; sigs: lp_mint -> [(sig, slot, blockTime, err, [events or raw log strings])]
    newest first; supply: lp_mint -> lp_supply the pool account shows (default: the newest event's S_after)."""

    def __init__(self, accounts: dict, sigs: dict, fail_tx: set | None = None, fail_sigs: int | bool = 0, supply: dict | None = None, tx_fail_times: int = 0):
        self.accounts, self.sigs, self.fail_tx, self.fail_sigs, self.supply = accounts, sigs, fail_tx or set(), int(fail_sigs), supply or {}
        self.tx_fail_times = tx_fail_times
        self.calls: list[tuple[str, int]] = []
        self.sleeps: list[float] = []

    def _supply(self, mint: str) -> int:
        if mint in self.supply:
            return self.supply[mint]
        for r in self.sigs.get(mint, []):
            for x in r[4]:
                if isinstance(x, bytes):
                    d = lph.decode_event(x)
                    return d["lp_mint_supply"] + (d["lp_token_amount"] if d["kind"] == "deposit" else -d["lp_token_amount"])
        return 1

    def __call__(self, method: str, params: list):
        self.calls.append((method, 0))
        if method == "getMultipleAccounts":
            return {"context": {"slot": 5}, "value": [None if self.accounts.get(p) is None else pool_account(self.accounts[p], self._supply(self.accounts[p])) for p in params[0]]}
        if method == "getSignaturesForAddress":
            if self.fail_sigs > 0:
                self.fail_sigs -= 1
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
            if self.tx_fail_times > 0:
                self.tx_fail_times -= 1
                raise SystemExit("rpc failed")
            for rows in self.sigs.values():
                for r in rows:
                    if r[0] == params[0] and isinstance(r[4], dict):
                        return {"slot": r[1], "blockTime": r[2], **r[4]}
                    if r[0] == params[0]:
                        logs = [log_line(x) if isinstance(x, bytes) else x for x in r[4]]
                        return {"slot": r[1], "blockTime": r[2], "meta": {"err": None, "logMessages": logs}}
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
    assert [(x["sig"], x["slot"], x["block_time"], x["kind"], x["s_before"], x["lp_delta"], x["idx"]) for x in e["events"]] == [("s2", 20, 2000, "deposit", 1000, 100, 0), ("s2", 20, 2000, "deposit", 1100, 0, 1), ("s4", 40, 4000, "withdraw", 1100, -30, 2)]
    assert e["lp_supply"] == 1070 and e["attempts"] == 1  # the end supply was read and matches the last S_after
    assert calls == len(chain.calls) == 1 + 1 + 2 + 1  # accounts, one signature page, two transactions, end-supply read


def test_fetch_history_rate_limit_gap() -> None:
    pool, mint = pk(1), pk(50)
    chain = FakeChain({pool: mint}, {mint: [("s1", 1, 100, None, [])]})
    run_hist(chain, [pool], 0, 1000)
    assert chain.sleeps and all(abs(x - 0.2) < 1e-9 for x in chain.sleeps)
    with pytest.raises(ValueError):
        lph.fetch_lp_history(chain, [pool], 0, 1, rps=50)


def test_fetch_history_account_missing_unresolved_and_not_retried() -> None:
    chain = FakeChain({pk(1): None}, {})
    att: list = []
    out, _ = run_hist(chain, [pk(1)], 0, 10, attempts=att)
    assert out[pk(1)]["resolved"] is False and out[pk(1)]["reason"] == "account_missing"
    assert len(att) == 1  # deterministic: no retry pass


def test_fetch_history_pagination_reaches_t_from(monkeypatch) -> None:
    monkeypatch.setattr(lph, "SIG_PAGE", 2)
    pool, mint = pk(1), pk(50)
    rows = [(f"s{i}", 100 - i, 1000 - i * 10, None, []) for i in range(10)]  # newest first, blockTime 1000 .. 910
    chain = FakeChain({pool: mint}, {mint: rows})
    out, _ = run_hist(chain, [pool], 975, 2000)
    assert out[pool]["resolved"] is True
    assert len([c for c in chain.calls if c[0] == "getSignaturesForAddress"]) == 2  # [1000,990] [980,970]: 970 < 975, stop


def test_fetch_history_paging_cannot_reach_t_from_is_unresolved(monkeypatch) -> None:
    monkeypatch.setattr(lph, "SIG_PAGE", 2)
    pool, mint = pk(1), pk(50)
    rows = [(f"s{i}", 100 - i, 1000 - i * 10, None, []) for i in range(10)]
    chain = FakeChain({pool: mint}, {mint: rows})
    att: list = []
    out, _ = run_hist(chain, [pool], 0, 2000, max_pages=2, attempts=att)
    assert out[pool]["resolved"] is False and out[pool]["reason"] == "paging_did_not_reach_t_from"
    assert len(att) == 1


def test_fetch_history_tx_failure_unresolved_after_first_pass_plus_three_retries_and_url_not_leaked() -> None:
    pool, mint = pk(1), pk(50)
    rows = [("s1", 1, 100, None, [event_bytes("deposit", pool, 1, 10)])]
    chain = FakeChain({pool: mint}, {mint: rows}, fail_tx={"s1"})
    att: list = []
    out, _ = run_hist(chain, [pool], 0, 1000, attempts=att)
    assert out[pool]["resolved"] is False and out[pool]["reason"].startswith("tx_fetch_failed") and out[pool]["attempts"] == 4
    assert [(a["pass"], a["n_pools"], a["n_resolved"]) for a in att] == [(1, 1, 0), (2, 1, 0), (3, 1, 0), (4, 1, 0)]
    chain = FakeChain({pool: mint}, {mint: rows}, fail_sigs=99)
    out, _ = run_hist(chain, [pool], 0, 1000)
    assert out[pool]["reason"].startswith("signatures_fetch_failed")
    assert "SECRET" not in repr(out) and "api-key" not in repr(out)


def test_fetch_history_transient_failure_recovers_on_retry_pass() -> None:
    pool, mint = pk(1), pk(50)
    rows = [("s1", 1, 100, None, [event_bytes("deposit", pool, 1, 10)])]
    chain = FakeChain({pool: mint}, {mint: rows}, tx_fail_times=1)
    att: list = []
    out, _ = run_hist(chain, [pool], 0, 1000, attempts=att)
    assert out[pool]["resolved"] is True and out[pool]["attempts"] == 2
    assert [(a["pass"], a["n_resolved"]) for a in att] == [(1, 0), (2, 1)]


def test_fetch_history_supply_chain_break_unresolved_not_retried() -> None:
    pool, mint = pk(1), pk(50)
    rows = [("s2", 2, 200, None, [event_bytes("deposit", pool, 5, 777)]), ("s1", 1, 100, None, [event_bytes("deposit", pool, 10, 1000)])]
    chain = FakeChain({pool: mint}, {mint: rows})
    att: list = []
    out, _ = run_hist(chain, [pool], 0, 1000, attempts=att)
    assert out[pool]["resolved"] is False and out[pool]["reason"] == "supply_chain_break"
    assert len(att) == 1


B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def b58e(raw: bytes) -> str:
    n = int.from_bytes(raw, "big")
    out = ""
    while n:
        n, r = divmod(n, 58)
        out = B58[r] + out
    return "1" * (len(raw) - len(raw.lstrip(b"\x00"))) + out


CREATE_IX = bytes.fromhex("e992d18ecf6840bc")
CREATE_POOL_EVENT = bytes.fromhex("b1310cd2a076a774")
UNKNOWN_EVENT = bytes.fromhex("ae7c4af90451f611")


def mk_tx(pool: str, outer: list, inner: list, logs: list, loaded: list | None = None) -> dict:
    """keys: 0 payer, 1 PumpSwap program, 2 this pool, 3 another pool, 4 pump program (+ loaded). Instruction specs are
    (program index, [account indexes], data bytes); `inner` hangs off outer instruction 0."""
    keys = [pk(90), lph.PROGRAM_ID, pool, pk(3), pk(91)]
    mkix = lambda spec: {"programIdIndex": spec[0], "accounts": spec[1], "data": b58e(spec[2])}  # noqa: E731
    la = {"writable": loaded or [], "readonly": []}
    return {"transaction": {"message": {"accountKeys": keys, "instructions": [mkix(x) for x in outer]}}, "meta": {"err": None, "logMessages": logs, "loadedAddresses": la, "innerInstructions": [{"index": 0, "instructions": [mkix(x) for x in inner]}]}}


def cpi_event(raw_event: bytes) -> tuple:
    return (1, [], lph.EVENT_IX_TAG + raw_event)


def resolve_tx(tx: dict, pool: str, mint: str, supply: int = 1) -> dict:
    rows = [("s1", 1, 100, None, tx)]
    chain = FakeChain({pool: mint}, {mint: rows}, supply={mint: supply})
    out, _ = run_hist(chain, [pool], 0, 1000)
    return out[pool]


def test_truncated_migration_tx_with_create_pool_only_resolves_with_no_events() -> None:
    pool, mint = pk(1), pk(50)
    tx = mk_tx(pool, [(4, [], b"migrate")], [(1, [2, 3], CREATE_IX + b"\x00" * 16), cpi_event(CREATE_POOL_EVENT + b"\x07" * 300), cpi_event(UNKNOWN_EVENT + b"\x01" * 50)], ["Program log: x", "Log truncated"])
    e = resolve_tx(tx, pool, mint, supply=555)
    assert e["resolved"] is True and e["events"] == [] and e["lp_supply"] == 555


def test_truncated_tx_with_deposit_ix_and_matching_self_cpi_event_resolves() -> None:
    pool, mint = pk(1), pk(50)
    dep = event_bytes("deposit", pool, 100, 1000)
    other = event_bytes("deposit", pk(3), 5, 9)  # another pool's deposit in the same tx
    tx = mk_tx(pool, [(4, [], b"x")], [(1, [2], lph.DEPOSIT_IX + b"\x00" * 24), cpi_event(dep), (1, [3], lph.DEPOSIT_IX + b"\x00" * 24), cpi_event(other)], ["Log truncated"])
    e = resolve_tx(tx, pool, mint, supply=1100)
    assert e["resolved"] is True
    assert [(x["sig"], x["kind"], x["s_before"], x["lp_delta"]) for x in e["events"]] == [("s1", "deposit", 1000, 100)]


def test_truncated_tx_with_deposit_ix_and_no_decodable_event_is_unrecoverable() -> None:
    pool, mint = pk(1), pk(50)
    tx = mk_tx(pool, [(4, [], b"x")], [(1, [2], lph.DEPOSIT_IX + b"\x00" * 24)], ["Log truncated"])
    e = resolve_tx(tx, pool, mint)
    assert e["resolved"] is False and e["reason"] == "logs_truncated_unrecoverable"
    # a withdraw instruction is not satisfied by a deposit event either
    tx = mk_tx(pool, [(4, [], b"x")], [(1, [2], lph.WITHDRAW_IX + b"\x00" * 24), cpi_event(event_bytes("deposit", pool, 1, 10))], ["Log truncated"])
    assert resolve_tx(tx, pool, mint)["reason"] == "logs_truncated_unrecoverable"


def test_log_and_self_cpi_events_must_match_when_logs_are_complete() -> None:
    pool, mint = pk(1), pk(50)
    logs = [log_line(event_bytes("deposit", pool, 100, 1000))]
    same = mk_tx(pool, [(4, [], b"x")], [(1, [2], lph.DEPOSIT_IX + b"\x00" * 24), cpi_event(event_bytes("deposit", pool, 100, 1000))], logs)
    e = resolve_tx(same, pool, mint, supply=1100)
    assert e["resolved"] and len(e["events"]) == 1
    diff = mk_tx(pool, [(4, [], b"x")], [(1, [2], lph.DEPOSIT_IX + b"\x00" * 24), cpi_event(event_bytes("deposit", pool, 101, 1000))], logs)
    e = resolve_tx(diff, pool, mint, supply=1100)
    assert e["resolved"] is False and e["reason"] == "event_source_mismatch"
    assert e["attempts"] == 1  # not a transient reason


def test_truncated_tx_with_loaded_address_pool_and_unknown_pool_index() -> None:
    pool, mint = pk(1), pk(50)
    # the pool account arrives through a lookup table: index 5 = loadedAddresses.writable[0]
    tx = mk_tx(pool, [(4, [], b"x")], [(1, [5], lph.DEPOSIT_IX + b"\x00" * 24), cpi_event(event_bytes("deposit", pool, 100, 1000))], ["Log truncated"], loaded=[pool])
    tx["transaction"]["message"]["accountKeys"][2] = pk(77)  # slot 2 is no longer the pool
    assert resolve_tx(tx, pool, mint, supply=1100)["resolved"] is True
    # an invocation whose pool account cannot be read: every invocation must have an event, whichever pool
    tx = mk_tx(pool, [(4, [], b"x")], [(1, [], lph.DEPOSIT_IX + b"\x00" * 24), cpi_event(event_bytes("deposit", pool, 100, 1000))], ["Log truncated"])
    assert resolve_tx(tx, pool, mint, supply=1100)["resolved"] is True
    tx = mk_tx(pool, [(4, [], b"x")], [(1, [], lph.DEPOSIT_IX + b"\x00" * 24), (1, [], lph.DEPOSIT_IX + b"\x00" * 24), cpi_event(event_bytes("deposit", pool, 100, 1000))], ["Log truncated"])
    assert resolve_tx(tx, pool, mint, supply=1100)["reason"] == "logs_truncated_unrecoverable"


def test_truncated_plain_log_only_tx_without_instructions_is_not_recoverable_with_events_missing() -> None:
    # logs truncated, no transaction body at all: nothing to count, nothing decoded: no events, resolved only by the supply check
    pool, mint = pk(1), pk(50)
    rows = [("s1", 1, 100, None, [event_bytes("deposit", pool, 10, 1000), "Log truncated"])]
    out, _ = run_hist(FakeChain({pool: mint}, {mint: rows}, supply={mint: 1000}), [pool], 0, 1000)
    assert out[pool]["resolved"] is True and out[pool]["events"] == []  # the log event is NOT used when logs are truncated
    out, _ = run_hist(FakeChain({pool: mint}, {mint: rows}, supply={mint: 1010}), [pool], 0, 1000)
    assert out[pool]["resolved"] is True  # zero events: nothing to chain against; completeness is the instruction count


def test_fetch_history_creation_tx_with_mint_logs_is_ignored() -> None:
    pool, mint = pk(1), pk(50)
    creation = ["Program log: Instruction: InitializeMint2", "Program log: Instruction: MintTo", "Program log: Instruction: Burn"]  # no Deposit/Withdraw event
    rows = [("s2", 2, 200, None, creation), ("s1", 1, 100, None, creation)]
    chain = FakeChain({pool: mint}, {mint: rows}, supply={mint: 777})
    out, _ = run_hist(chain, [pool], 0, 1000)
    assert out[pool]["resolved"] is True and out[pool]["events"] == []
    assert out[pool]["lp_supply"] == 777 and out[pool]["supply_slot"] == 5  # zero events: the supply and its slot are still recorded


def test_fetch_history_account_and_supply_read_failures_are_retried() -> None:
    pool, mint = pk(1), pk(50)
    rows = [("s1", 1, 100, None, [event_bytes("deposit", pool, 10, 1000)])]

    class Flaky(FakeChain):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self.acct_calls = 0

        def __call__(self, method, params):
            if method == "getMultipleAccounts":
                self.acct_calls += 1
                if self.acct_calls in (1, 3):  # the first account read, and the first supply read after it recovers
                    self.calls.append((method, 0))
                    raise SystemExit("rpc failed")
            return super().__call__(method, params)

    chain = Flaky({pool: mint}, {mint: rows})
    att: list = []
    out, _ = run_hist(chain, [pool], 0, 1000, attempts=att)
    assert out[pool]["resolved"] is True and out[pool]["attempts"] == 3
    assert [(a["pass"], a["n_resolved"]) for a in att] == [(1, 0), (2, 0), (3, 1)]
    # every attempt is logged per pool with its pass and reason; earlier reasons are kept
    assert out[pool]["attempt_log"] == [{"pass": 1, "resolved": False, "reason": "accounts_fetch_failed:SystemExit"}, {"pass": 2, "resolved": False, "reason": "supply_read_failed:accounts_fetch_failed:SystemExit"}, {"pass": 3, "resolved": True, "reason": None}]


def test_fetch_history_successful_read_of_missing_supply_account_is_not_retried() -> None:
    pool, mint = pk(1), pk(50)
    rows = [("s1", 1, 100, None, [event_bytes("deposit", pool, 10, 1000)])]

    class Gone(FakeChain):
        n = 0

        def __call__(self, method, params):
            if method == "getMultipleAccounts":
                Gone.n += 1
                if Gone.n == 2:  # the supply read succeeds but the account is gone
                    self.calls.append((method, 0))
                    return {"context": {"slot": 9}, "value": [None]}
            return super().__call__(method, params)

    chain = Gone({pool: mint}, {mint: rows})
    att: list = []
    out, _ = run_hist(chain, [pool], 0, 1000, attempts=att)
    assert out[pool]["reason"] == "supply_account_missing" and out[pool]["attempts"] == 1 and len(att) == 1


def test_fetch_history_last_supply_must_equal_account_supply() -> None:
    pool, mint = pk(1), pk(50)
    rows = [("s1", 1, 100, None, [event_bytes("deposit", pool, 10, 1000)])]
    chain = FakeChain({pool: mint}, {mint: rows}, supply={mint: 1011})  # 1000 + 10 = 1010 expected
    out, _ = run_hist(chain, [pool], 0, 1000)
    assert out[pool]["reason"] == "last_supply_mismatch" and out[pool]["lp_supply"] == 1011
    chain = FakeChain({pool: mint}, {mint: rows}, supply={mint: 1010})
    out, _ = run_hist(chain, [pool], 0, 1000)
    assert out[pool]["resolved"] and out[pool]["lp_supply"] == 1010


def test_zero_event_pool_supply_must_not_move_between_first_and_end_read() -> None:
    pool, mint = pk(1), pk(50)

    class Moves(FakeChain):
        n = 0

        def __call__(self, method, params):
            if method == "getMultipleAccounts":
                Moves.n += 1
                self.supply = {mint: 1000 if Moves.n == 1 else 1010}  # a deposit lands after the listing began
            return super().__call__(method, params)

    chain = Moves({pool: mint}, {mint: [("s1", 1, 100, None, [])]})
    att: list = []
    out, _ = run_hist(chain, [pool], 0, 1000, attempts=att)
    assert out[pool]["resolved"] is False and out[pool]["reason"] == "last_supply_mismatch"
    assert out[pool]["lp_supply_first"] == 1000 and out[pool]["lp_supply"] == 1010 and len(att) == 1  # not retried
    out, _ = run_hist(FakeChain({pool: mint}, {mint: [("s1", 1, 100, None, [])]}, supply={mint: 1000}), [pool], 0, 1000)
    assert out[pool]["resolved"] is True  # unchanged supply: resolved
