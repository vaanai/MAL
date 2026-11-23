"""Tests for floor_cpmm_forward_rec on SYNTHETIC account bytes and a fake RPC. No network, no key, no real account."""
import base64
import json
import struct
from pathlib import Path

import pytest

from tools import floor_cpmm_forward_rec as R
from tools import launchlab_cpmm_fetch as C

B58 = C._B58


def b58enc(b: bytes) -> str:
    n = int.from_bytes(b, "big")
    s = ""
    while n:
        n, r = divmod(n, 58)
        s = B58[r] + s
    return "1" * (len(b) - len(b.lstrip(b"\0"))) + s


def addr(n: int) -> str:
    return b58enc(bytes([n]) * 32)


POOL, V0, V1, LPM, TOK, CFG = addr(1), addr(2), addr(3), addr(4), addr(5), addr(6)
WSOL_B = C.b58decode(C.WSOL)
TOKEN_PROG = R.M.TOKEN_PROGRAM


def pool_bytes(mint0: bytes, mint1: bytes, lp_supply: int = 777, tail: bytes = bytes(range(32))) -> bytes:
    b = bytearray(637)
    b[8:40] = C.b58decode(CFG)
    b[72:104] = C.b58decode(V0)
    b[104:136] = C.b58decode(V1)
    b[136:168] = C.b58decode(LPM)
    b[168:200], b[200:232] = mint0, mint1
    struct.pack_into("<Q", b, 333, lp_supply)
    b[341:373] = tail
    struct.pack_into("<Q", b, 373, 1_795_000_000)  # open_time
    b[389], b[390] = 1, 1  # creator_fee_on, enable_creator_fee
    struct.pack_into("<QQ", b, 397, 11, 22)  # creator_fees_token_0/1
    return bytes(b)


def token_acc(amount: int) -> bytes:
    b = bytearray(165)
    struct.pack_into("<Q", b, 64, amount)
    return bytes(b)


def mint_bytes(supply: int, auth: bool) -> bytes:
    b = bytearray(82)
    if auth:
        struct.pack_into("<I", b, 0, 1)
        b[4:36] = bytes([9]) * 32
    struct.pack_into("<Q", b, 36, supply)
    b[45] = 1
    return bytes(b)


def cfg_bytes(rate: int) -> bytes:
    b = bytearray(120)
    struct.pack_into("<Q", b, 12, rate)
    struct.pack_into("<Q", b, 20, 120)
    struct.pack_into("<Q", b, 28, 40)
    struct.pack_into("<Q", b, 108, 77)
    return bytes(b)


def acc(data: bytes, owner: str) -> dict:
    return {"data": [base64.b64encode(data).decode(), "base64"], "owner": owner}


class Fake:
    """Fake RPC keyed by address; records calls."""

    def __init__(self, accounts: dict, supply: int = 1000, sigs=None, txs=None):
        self.accounts, self.supply, self.sigs, self.txs, self.calls = accounts, supply, sigs or [], txs or {}, []

    def __call__(self, method, params):
        self.calls.append((method, params))
        if method == "getMultipleAccounts":
            assert len(params[0]) <= 100
            return {"context": {"slot": 42}, "value": [self.accounts.get(a) for a in params[0]]}
        if method == "getTokenSupply":
            return {"value": {"amount": str(self.supply)}}
        if method == "getSignaturesForAddress":
            return list(self.sigs)
        if method == "getTransaction":
            return self.txs.get(params[0])
        raise AssertionError(method)


def good_accounts(quote_first: bool = True) -> dict:
    m0, m1 = (WSOL_B, C.b58decode(TOK)) if quote_first else (C.b58decode(TOK), WSOL_B)
    return {POOL: acc(pool_bytes(m0, m1), C.CPMM), V0: acc(token_acc(5_000), TOKEN_PROG), V1: acc(token_acc(9_000), TOKEN_PROG),
            LPM: acc(mint_bytes(1234, True), TOKEN_PROG), TOK: acc(mint_bytes(10**15, False), TOKEN_PROG), CFG: acc(cfg_bytes(2500), C.CPMM)}


def test_decode_pool_and_token_amount():
    d = R.decode_pool(pool_bytes(WSOL_B, C.b58decode(TOK)))
    assert (d["vault0"], d["vault1"], d["lp_mint"], d["amm_config"], d["lp_supply"]) == (V0, V1, LPM, CFG, 777)
    assert base64.b64decode(d["ps_tail_b64"])[:32] == bytes(range(32)) and len(base64.b64decode(d["ps_tail_b64"])) == 72
    assert (d["creator_fees_token_0"], d["creator_fees_token_1"], d["open_time"], d["creator_fee_on"], d["enable_creator_fee"]) == (11, 22, 1_795_000_000, 1, True)
    assert d["protocol_fees_token_0"] == int.from_bytes(bytes(range(8)), "little")
    assert R.decode_token_amount(token_acc(123)) == 123
    with pytest.raises(R.M.Unreadable):
        R.decode_pool(b"\0" * 50)
    with pytest.raises(R.M.Unreadable):
        R.decode_token_amount(b"\0" * 10)


@pytest.mark.parametrize("quote_first", [True, False])
def test_discover_and_poll(quote_first):
    f = Fake(good_accounts(quote_first))
    ctr = R.Counters()
    p = R.discover_pool(f, ctr, {"pool": POOL, "token_mint": TOK, "bt": 1, "src": "list"})
    assert p["wsol_is_token0"] is quote_first
    assert (p["lp_supply_mint"], p["lp_burned"], p["mint_authority_set"], p["freeze_authority_set"], p["token_2022_transfer_fee"], p["token_program"]) == (1234, None, False, False, False, "token")
    assert (p["trade_fee_rate"], p["protocol_fee_rate"], p["fund_fee_rate"], p["creator_fee_rate"]) == (2500, 120, 40, 77)
    (row,) = R.poll_rows(f, ctr, [p], 1000)
    q, b = (5_000, 9_000) if quote_first else (9_000, 5_000)
    assert (row["quote_reserve"], row["base_reserve"], row["lp_supply"], row["slot"], row["token_supply"], row["err"]) == (q, b, 777, 42, 10**15, None)
    assert row["token_supply_ms"] == 1000 and row["creator_fees_token_0"] == 11 and row["creator_fee_rate"] == 77 and row["freeze_authority_set"] is False
    assert row["mint_authority_set"] is False and row["trade_fee_rate"] == 2500


def test_discover_refuses_wrong_program_or_mint():
    a = good_accounts()
    a[POOL] = acc(pool_bytes(WSOL_B, C.b58decode(TOK)), "SomeoneElse1111111111111111111111111111111")
    assert R.discover_pool(Fake(a), R.Counters(), {"pool": POOL, "token_mint": TOK}) is None
    assert R.discover_pool(Fake(good_accounts()), R.Counters(), {"pool": POOL, "token_mint": addr(77)}) is None


def test_poll_missing_account_gives_err_row():
    f = Fake(good_accounts())
    ctr = R.Counters()
    p = R.discover_pool(f, ctr, {"pool": POOL, "token_mint": TOK})
    f.accounts.pop(V1)
    (row,) = R.poll_rows(f, ctr, [p], 5)
    assert row["err"] == "account_missing" and row["base_reserve"] is None and ctr["row_errors"] == 1


def test_gma_batches_at_100():
    f = Fake({})
    accs, slot = R.gma(f, R.Counters(), [addr(i % 200 + 1) for i in range(250)])
    assert len(accs) == 250 and [len(c[1][0]) for c in f.calls] == [100, 100, 50] and slot == 42


def test_hourly_rotation_exclusive_and_fsync(tmp_path):
    ctr = R.Counters()
    w = R.HourlyWriter(tmp_path, ctr)
    h0 = 1_795_000_000_000 // 3_600_000 * 3_600_000
    w.write(h0 + 5, [{"a": 1}])
    w.write(h0 + 60_000, [{"a": 2}])
    w.write(h0 + 3_600_000 + 1, [{"a": 3}])
    w.close()
    names = sorted(p.name for p in tmp_path.iterdir())
    assert len(names) == 2 and ctr["files_closed"] == 2 and ctr["rows"] == 3
    assert [json.loads(x)["a"] for x in (tmp_path / names[0]).read_text().splitlines()] == [1, 2]
    # a restart inside the same hour never overwrites: next free .rN file
    w2 = R.HourlyWriter(tmp_path, R.Counters())
    w2.write(h0 + 9, [{"a": 4}])
    w2.close()
    again = sorted(p.name for p in tmp_path.iterdir())
    assert len(again) == 3 and any(n.endswith(".r1.jsonl") for n in again)
    assert (tmp_path / names[0]).read_text().count("\n") == 2


def test_token_bucket_rate_and_burst():
    t = [0.0]
    slept = []

    def sl(s):
        slept.append(s)
        t[0] += s

    b = R.TokenBucket(2.0, clock=lambda: t[0], sleep=sl)
    for _ in range(5):
        b.take()
    assert t[0] == pytest.approx(2.0)  # first token free, then 0.5 s each
    t[0] += 10.0
    slept.clear()
    b.take()
    assert slept == []  # idle time refills, capped at one token
    b.take()
    assert slept == [pytest.approx(0.5)]
    with pytest.raises(ValueError):
        R.TokenBucket(0)


def test_http_429_backoff_then_success():
    import urllib.error

    seq = [429, 429, "ok"]
    slept = []

    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps({"result": 7}).encode()

    def opener(req, timeout):
        x = seq.pop(0)
        if x == 429:
            raise urllib.error.HTTPError("u", 429, "slow", {}, None)
        return Resp()

    ctr = R.Counters()
    call = R.http_call(R.TokenBucket(1000.0), ctr, sleep=slept.append, opener=opener)
    assert call("getSlot", []) == 7
    assert slept == [1.0, 2.0] and ctr["rpc_429"] == 2 and ctr["rpc_calls"] == 3


def test_load_list_checks_sha(tmp_path):
    p = tmp_path / "l.json"
    p.write_text(json.dumps([{"pool": POOL, "tokmint": TOK, "quote": C.WSOL, "bt": 5}, {"pool": addr(9), "tokmint": TOK, "quote": "other"}]))
    import hashlib

    pre = hashlib.sha256(p.read_bytes()).hexdigest()[:8]
    assert [r["pool"] for r in R.load_list(str(p), pre)] == [POOL]
    with pytest.raises(R.Refused):
        R.load_list(str(p), "00000000")


def migration_tx(pool: str, bt: int, with_ll: bool = True) -> dict:
    keys = [addr(30), addr(31), CFG, addr(33), pool, C.WSOL, TOK, LPM, addr(40), addr(41), addr(42), V0, V1, C.CPMM, C.LAUNCHLAB]
    disc = list(next(iter(C.INIT_DISCS)))
    ixs = [{"programIdIndex": 13, "accounts": list(range(13)), "data": b58enc(bytes(disc) + b"\0")}]
    if with_ll:
        ixs.append({"programIdIndex": 14, "accounts": [0, 1], "data": b58enc(b"\x01\x02")})
    return {"blockTime": bt, "transaction": {"message": {"accountKeys": keys, "instructions": ixs}}, "meta": {}}


def test_discover_new_forward_only_and_cursor():
    pool = addr(50)
    f = Fake({}, sigs=[{"signature": "new1", "blockTime": 2000, "err": None}, {"signature": "bad", "blockTime": 2000, "err": {"x": 1}},
                       {"signature": "old", "blockTime": 900, "err": None}, {"signature": "nonll", "blockTime": 2001, "err": None}],
          txs={"new1": migration_tx(pool, 2000), "nonll": migration_tx(addr(51), 2001, with_ll=False)})
    st = {"cursor": None, "pending": {}}
    ctr = R.Counters()
    got = R.discover_new(f, ctr, st, 1000)
    assert [(g["pool"], g["src"], g["token_mint"]) for g in got] == [(pool, "forward", TOK)]
    assert st["cursor"] == "new1" and st["pending"] == {} and ctr["not_launchlab_wsol"] == 1
    assert not any(c[0] == "getTransaction" and c[1][0] in ("old", "bad") for c in f.calls)
    f.calls.clear()
    R.discover_new(f, ctr, st, 1000)
    assert f.calls[0][1][1]["until"] == "new1"


def test_discover_new_unavailable_tx_stays_pending():
    f = Fake({}, sigs=[{"signature": "s", "blockTime": 2000, "err": None}])
    st = {"cursor": None, "pending": {}}
    assert R.discover_new(f, R.Counters(), st, 1000) == [] and st["pending"] == {"s": [2000, 1]}


def test_run_resume_and_exit(tmp_path):
    f = Fake(good_accounts())
    clock = [1_795_000_000.0]
    lst = [{"pool": POOL, "token_mint": TOK, "bt": 1, "src": "list"}]

    def sleep(s):
        clock[0] += s

    def go(max_hours, polls):
        return R.run(tmp_path, f, R.Counters(), lst, interval_s=60, max_hours=max_hours, discover_s=10**9, now=lambda: clock[0], sleep=sleep, max_polls=polls)

    assert go(1.0, 3) == 0
    st = json.loads((tmp_path / "status.json").read_text())
    assert st["start_ms"] == 1_795_000_000_000 and st["counters"]["polls"] == 3 and st["pools"] == 1 and st["done"] is False
    assert set(st) == {"schema", "start_ms", "deadline_ms", "last_ms", "last_ok_ms", "pools", "waiting", "consecutive_failed_polls", "gaps", "done", "counters"}
    f.calls.clear()
    assert go(1.0, None) == 0  # resume: same start, no rediscovery of the pool, runs to the deadline
    st2 = json.loads((tmp_path / "status.json").read_text())
    assert st2["start_ms"] == st["start_ms"] and st2["done"] is True and st2["counters"]["polls"] > 3 and st2["counters"]["pools_discovered"] == 1
    assert not any(c[0] == "getMultipleAccounts" and len(c[1][0]) == 1 for c in f.calls)
    rows = [json.loads(x) for p in sorted(tmp_path.glob("*.jsonl")) for x in p.read_text().splitlines()]
    assert len(rows) == st2["counters"]["rows"] and all(r["pool"] == POOL for r in rows)
    assert "supply_calls" not in st2["counters"]  # token supply rides the poll call (mint account), no getTokenSupply


# ---------------------------------------------------------------------------------------------- bounded passes (smoke #3306 fix)
def many_pools(n: int):
    """n distinct pools sharing one token mint / LP mint / config. Returns (list records, accounts)."""
    recs, accs = [], {}
    accs[LPM] = acc(mint_bytes(1, True), TOKEN_PROG)
    accs[TOK] = acc(mint_bytes(10, False), TOKEN_PROG)
    accs[CFG] = acc(cfg_bytes(2500), C.CPMM)
    accs[V0] = acc(token_acc(5_000), TOKEN_PROG)
    accs[V1] = acc(token_acc(9_000), TOKEN_PROG)
    for i in range(n):
        pool = b58enc(bytes([100 + i // 256, i % 256]) + bytes(30))
        accs[pool] = acc(pool_bytes(WSOL_B, C.b58decode(TOK)), C.CPMM)
        recs.append({"pool": pool, "token_mint": TOK, "bt": 1, "src": "list"})
    return recs, accs


class Clock:
    def __init__(self, t=1_795_000_000.0):
        self.t = t

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += s


class SlowFake(Fake):
    """Every RPC call costs `cost` seconds on the fake clock."""

    def __init__(self, clk, cost, *a, **k):
        super().__init__(*a, **k)
        self.clk, self.cost = clk, cost

    def __call__(self, method, params):
        self.clk.t += self.cost
        return super().__call__(method, params)


def test_batch_discovery_242_pools_few_gma_calls():
    recs, accs = many_pools(242)
    f = Fake(accs)
    out = R.discover_batch(f, R.Counters(), recs)
    assert len(out) == 242 and all(o and o["vault0"] == V0 and o["trade_fee_rate"] == 2500 for o in out)
    assert all(c[0] == "getMultipleAccounts" for c in f.calls) and len(f.calls) <= 10
    assert all(len(c[1][0]) <= 100 for c in f.calls)


def test_batch_discovery_returns_none_for_unreadable():
    recs, accs = many_pools(3)
    accs.pop(recs[1]["pool"])
    out = R.discover_batch(Fake(accs), R.Counters(), recs)
    assert [o is None for o in out] == [False, True, False]


def test_first_pass_is_bounded_and_polls_resolved_pools_at_once(tmp_path):
    recs, accs = many_pools(242)
    clk, lines = Clock(), []
    f = Fake(accs)
    R.run(tmp_path, f, R.Counters(), recs, interval_s=60, max_hours=10, discover_s=10**9, now=clk.now, sleep=clk.sleep, max_polls=1, max_pools=100, progress=lines.append)
    st = json.loads((tmp_path / "status.json").read_text())
    assert st["pools"] == 100 and st["waiting"] == 142 and st["counters"]["polls"] == 1
    rows = [json.loads(x) for p in tmp_path.glob("*.jsonl") for x in p.read_text().splitlines()]
    assert len(rows) == 100 and all(r["err"] is None for r in rows)
    # later passes drain the queue
    R.run(tmp_path, f, R.Counters(), recs, interval_s=60, max_hours=10, discover_s=10**9, now=clk.now, sleep=clk.sleep, max_polls=2, max_pools=100, progress=lines.append)
    st = json.loads((tmp_path / "status.json").read_text())
    assert st["pools"] == 242 and st["waiting"] == 0


def test_save_after_every_discovery_batch_and_poll(tmp_path, monkeypatch):
    recs, accs = many_pools(150)
    saves = []
    real = R.atomic_write
    monkeypatch.setattr(R, "atomic_write", lambda path, obj: (saves.append(path.name), real(path, obj)))
    clk = Clock()
    R.run(tmp_path, Fake(accs), R.Counters(), recs, interval_s=60, max_hours=10, discover_s=10**9, now=clk.now, sleep=clk.sleep, max_polls=1, max_pools=100,
          progress=lambda s: None)
    # one pass: discovery save, batch save, end-of-pass save, final save; each writes pools.json and status.json
    assert saves.count("status.json") >= 4 and saves.count("pools.json") == saves.count("status.json")


def test_progress_line_fields(tmp_path):
    recs, accs = many_pools(5)
    clk, lines = Clock(), []
    R.run(tmp_path, Fake(accs), R.Counters(), recs, interval_s=60, max_hours=10, discover_s=10**9, now=clk.now, sleep=clk.sleep, max_polls=2, progress=lines.append)
    assert len(lines) == 2
    for k in ("pools=5", "waiting=0", "polls=", "rpc_calls=", "rpc_429=", "secs="):
        assert k in lines[0]


def test_deadline_honoured_inside_a_pass(tmp_path):
    """Each call costs 30 s on the fake clock and the deadline is 100 s out: the pass stops mid-discovery, exits cleanly, final status written."""
    recs, accs = many_pools(242)
    clk = Clock()
    f = SlowFake(clk, 30.0, accs, sigs=[{"signature": f"s{i}", "blockTime": 1_795_000_001, "err": None} for i in range(60)])
    assert R.run(tmp_path, f, R.Counters(), recs, interval_s=60, max_hours=100 / 3600, discover_s=10**9, now=clk.now, sleep=clk.sleep, progress=lambda s: None) == 0
    st = json.loads((tmp_path / "status.json").read_text())
    assert st["done"] is True
    assert len(f.calls) == 4  # calls start at 0, 30, 60, 90 s; none after the 100 s deadline


def test_discover_stop_checked_before_each_call():
    n = [0]

    def stop():
        n[0] += 1
        return n[0] > 3

    f = Fake({}, sigs=[{"signature": f"s{i}", "blockTime": 2000 + i, "err": None} for i in range(10)])
    st = {"cursor": None, "pending": {}}
    R.discover_new(f, R.Counters(), st, 1000, stop=stop)
    assert len([c for c in f.calls if c[0] == "getTransaction"]) <= 2


def test_pending_tx_cap_per_pass():
    sigs = [{"signature": f"s{i:03d}", "blockTime": 2000 + i, "err": None} for i in range(80)]
    f = Fake({}, sigs=sigs)
    st = {"cursor": None, "pending": {}}
    ctr = R.Counters()
    R.discover_new(f, ctr, st, 1000, max_tx=50)
    assert ctr["tx_calls"] == 50 and len(st["pending"]) == 80  # txs unavailable: all stay pending, only 50 tried
    f2 = Fake({}, sigs=sigs, txs={s["signature"]: migration_tx(addr(50), s["blockTime"], with_ll=False) for s in sigs})
    st2, ctr2 = {"cursor": None, "pending": {}}, R.Counters()
    R.discover_new(f2, ctr2, st2, 1000, max_tx=50)
    assert ctr2["tx_calls"] == 50 and len(st2["pending"]) == 30


# ---------------------------------------------------------------------------------------------- QP / reviewer round (head fdfb2220 CHANGES)
class SlotFake(Fake):
    """Slot = number of getMultipleAccounts calls so far; records each call's address list; optional per-method failure."""

    def __init__(self, *a, fail=None, lock=None, **k):
        super().__init__(*a, **k)
        self.n, self.fail, self.lock = 0, fail or {}, lock or []

    def __call__(self, method, params):
        if self.fail.get(method):
            self.fail[method] -= 1
            raise R.RpcError(f"{method} boom")
        if method == "getTokenAccountsByOwner":
            self.calls.append((method, params))
            return {"value": self.lock}
        if method == "getMultipleAccounts":
            self.n += 1
            self.calls.append((method, params))
            return {"context": {"slot": 1000 + self.n}, "value": [self.accounts.get(a) for a in params[0]]}
        return super().__call__(method, params)


def test_default_discovery_address_is_the_live_one():
    assert R.DISCOVERY_ADDRESS == "DNXgeM9EiiaAbaWvwjHj9fQQLAX5ZsfHyvmYUNRAdNC8"


def test_discovery_counts_sigs_seen_and_migrations_found():
    pool = addr(50)
    f = Fake({}, sigs=[{"signature": "a", "blockTime": 2000, "err": None}, {"signature": "b", "blockTime": 2001, "err": {"e": 1}}], txs={"a": migration_tx(pool, 2000)})
    ctr = R.Counters()
    R.discover_new(f, ctr, {"cursor": None, "pending": {}}, 1000)
    assert ctr["sigs_seen"] == 1 and ctr["migrations_found"] == 1
    assert f.calls[0][1][0] == R.DISCOVERY_ADDRESS


def test_rpc_error_codes_are_retried_and_counted():
    import urllib.error  # noqa: F401

    seq = [{"error": {"code": -32015, "message": "x"}}, {"error": {"code": -32015, "message": "x"}}, {"result": 9}]
    slept = []

    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps(seq.pop(0)).encode()

    ctr = R.Counters()
    call = R.http_call(R.TokenBucket(1000.0), ctr, sleep=slept.append, opener=lambda req, timeout: Resp())
    assert call("getTransaction", []) == 9
    assert ctr["rpc_err_-32015"] == 2 and slept == [1.0, 2.0] and ctr["rpc_calls"] == 3


def test_rpc_gives_up_with_rpcerror_not_a_crash_of_the_run():
    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps({"error": {"code": -32603, "message": "x"}}).encode()

    ctr = R.Counters()
    call = R.http_call(R.TokenBucket(1000.0), ctr, sleep=lambda s: None, opener=lambda req, timeout: Resp())
    with pytest.raises(R.RpcError):
        call("getSlot", [])
    assert ctr["rpc_calls"] == 8 and ctr["rpc_gave_up"] == 1


def test_failed_gettransaction_counts_as_a_try_and_pending_is_dropped():
    class Boom(Fake):
        def __call__(self, method, params):
            if method == "getTransaction":
                raise R.RpcError("x")
            return super().__call__(method, params)

    f = Boom({}, sigs=[{"signature": "s", "blockTime": 2000, "err": None}])
    st, ctr = {"cursor": None, "pending": {}}, R.Counters()
    for _ in range(R.MAX_PENDING_TRIES - 1):
        assert R.discover_new(f, ctr, st, 1000) == []
    assert st["pending"]["s"][1] == R.MAX_PENDING_TRIES - 1 and ctr["tx_errors"] == R.MAX_PENDING_TRIES - 1
    R.discover_new(f, ctr, st, 1000)
    assert "s" not in st["pending"] and ctr["tx_dropped"] == 1


def test_listing_error_is_counted_not_raised():
    class Boom(Fake):
        def __call__(self, method, params):
            raise R.RpcError("x")

    st, ctr = {"cursor": "c", "pending": {}}, R.Counters()
    assert R.discover_new(Boom({}), ctr, st, 1000) == [] and ctr["sig_errors"] == 1 and st["cursor"] == "c"


def test_disc_gap_counted_when_page_cap_hit():
    sigs = [{"signature": f"s{i}", "blockTime": 2000, "err": None} for i in range(100)]
    f = Fake({}, sigs=sigs)
    ctr = R.Counters()
    R.discover_new(f, ctr, {"cursor": "old", "pending": {}}, 1000, max_pages=2, max_tx=0)
    assert ctr["disc_gap"] == 1


def test_run_survives_step_errors_and_ends_only_after_n_failed_polls(tmp_path):
    recs, accs = many_pools(3)
    clk = Clock()
    f = SlotFake(accs, fail={"getSignaturesForAddress": 1})
    f.fail["getMultipleAccounts"] = 0
    lines = []
    assert R.run(tmp_path, f, R.Counters(), recs, interval_s=60, max_hours=10, discover_s=10**9, now=clk.now, sleep=clk.sleep, max_polls=3, progress=lines.append) == 0
    st = json.loads((tmp_path / "status.json").read_text())
    assert st["counters"]["polls"] == 3 and st["consecutive_failed_polls"] == 0
    # polls fail every time: the run ends after max_failed_polls consecutive failures with exit 2 and a final status
    d3 = tmp_path / "c"
    f3 = SlotFake(accs)
    pre = R.run(d3, f3, R.Counters(), recs, interval_s=60, max_hours=10, discover_s=10**9, now=clk.now, sleep=clk.sleep, max_polls=1, progress=lambda s: None)
    assert pre == 0
    f3.fail["getMultipleAccounts"] = 10**6
    rc = R.run(d3, f3, R.Counters(), recs, interval_s=60, max_hours=10, discover_s=10**9, now=clk.now, sleep=clk.sleep, max_failed_polls=5, progress=lambda s: None)
    st = json.loads((d3 / "status.json").read_text())
    assert rc == 2 and st["done"] is True and st["consecutive_failed_polls"] == 5 and st["counters"]["poll_chunk_errors"] >= 5


def test_gap_recorded_after_failed_polls(tmp_path):
    recs, accs = many_pools(3)
    clk = Clock()
    f = SlotFake(accs)
    R.run(tmp_path, f, R.Counters(), recs, interval_s=60, max_hours=10, discover_s=10**9, now=clk.now, sleep=clk.sleep, max_polls=1, progress=lambda s: None)
    clk.t += 600  # recorder was down / failing for 10 minutes
    R.run(tmp_path, f, R.Counters(), recs, interval_s=60, max_hours=10, discover_s=10**9, now=clk.now, sleep=clk.sleep, max_polls=1, progress=lambda s: None)
    st = json.loads((tmp_path / "status.json").read_text())
    assert len(st["gaps"]) == 1 and st["gaps"][0][1] - st["gaps"][0][0] >= 600_000 and st["counters"]["gap_ms"] >= 600_000


def test_pools_packed_whole_and_rows_carry_own_call_slot():
    recs, accs = many_pools(242)
    f = SlotFake(accs)
    ctr = R.Counters()
    pools = [p for p in R.discover_batch(f, ctr, recs)]
    f.calls.clear()
    f.n = 0
    rows = R.poll_rows(f, ctr, pools, 5)
    sizes = [len(c[1][0]) for c in f.calls]
    assert all(s % 4 == 0 and s <= 100 for s in sizes) and sum(sizes) == 242 * 4 and len(sizes) == 10
    # every pool's four accounts sit in one call; each row carries that call's slot (1 + index of the call)
    for i, row in enumerate(rows):
        assert row["slot"] == 1001 + i // R.POOLS_PER_CALL and row["pool"] == pools[i]["pool"]
    assert len({r["slot"] for r in rows}) == 10


def test_lock_share_and_mint_flags(tmp_path):
    lock_info = {"account": {"data": {"parsed": {"info": {"mint": LPM, "owner": R.M.LOCK_AUTH, "tokenAmount": {"amount": "300"}}}}}}
    accs = good_accounts()
    b = bytearray(pool_bytes(WSOL_B, C.b58decode(TOK), lp_supply=1000))
    accs[POOL] = acc(bytes(b), C.CPMM)
    accs[LPM] = acc(mint_bytes(900, True), TOKEN_PROG)  # 100 burned
    tk = bytearray(mint_bytes(10**15, False))
    struct.pack_into("<I", tk, 46, 1)  # freeze authority set
    tk[50:82] = bytes([7]) * 32
    accs[TOK] = acc(bytes(tk), TOKEN_PROG)
    f = SlotFake(accs, lock=[lock_info])
    p = R.discover_pool(f, R.Counters(), {"pool": POOL, "token_mint": TOK})
    assert p["lp_burned"] == 100 and p["freeze_authority_set"] is True and p["lock_done"] is False
    R.lock_one(f, p)
    assert p["lp_locked_lock_authority"] == 300 and p["lp_locked_frac"] == pytest.approx(0.4) and p["lock_done"] is True
    (row,) = R.poll_rows(f, R.Counters(), [p], 7)
    assert (row["lp_locked_frac"], row["lp_burned"], row["lp_supply_mint"], row["freeze_authority_set"]) == (pytest.approx(0.4), 100, 900, True)


def test_lock_step_bounded_per_pass(tmp_path):
    recs, accs = many_pools(10)
    clk = Clock()
    f = SlotFake(accs)
    R.run(tmp_path, f, R.Counters(), recs, interval_s=60, max_hours=10, discover_s=10**9, now=clk.now, sleep=clk.sleep, max_polls=1, max_lock=4, progress=lambda s: None)
    assert len([c for c in f.calls if c[0] == "getTokenAccountsByOwner"]) == 4
    rows = [json.loads(x) for p in tmp_path.glob("*.jsonl") for x in p.read_text().splitlines()]
    assert len(rows) == 10  # polled while the lock reads are still pending


def test_token_2022_transfer_fee_flag():
    b = bytearray(190)
    b[:82] = mint_bytes(5, False)
    b[165] = 1
    struct.pack_into("<HH", b, 166, 1, 4)  # TransferFeeConfig extension
    accs = good_accounts()
    accs[TOK] = acc(bytes(b), R.M.TOKEN_2022)
    p = R.discover_pool(Fake(accs), R.Counters(), {"pool": POOL, "token_mint": TOK})
    assert p["token_2022_transfer_fee"] is True and p["token_program"] == "token-2022"


def test_unreadable_pool_dropped_after_n_tries(tmp_path):
    clk = Clock()
    recs = [{"pool": POOL, "token_mint": TOK, "bt": 1, "src": "list"}]
    ctr = R.Counters()
    R.run(tmp_path, Fake({}), ctr, recs, interval_s=60, max_hours=10, discover_s=10**9, now=clk.now, sleep=clk.sleep, max_polls=3, max_discover_tries=3, progress=lambda s: None)
    st = json.loads((tmp_path / "status.json").read_text())
    assert st["waiting"] == 0 and st["counters"]["pools_dropped"] == 1 and st["counters"]["discover_retry"] == 2


@pytest.mark.parametrize("url,ok", [("https://api.mainnet-beta.solana.com", True), ("https://mainnet.helius-rpc.com/?api-key=abc", False), ("https://x.example/HELIUS", False),
                                    ("https://x.example/?apikey=1", False), ("https://x.example/?a=1&api_key=1", False), ("http://api.mainnet-beta.solana.com", False)])
def test_paid_endpoint_guard(url, ok):
    if ok:
        assert R.check_rpc_url(url) == url
        R.http_call(R.TokenBucket(1.0), R.Counters(), url=url)
    else:
        with pytest.raises(R.Refused):
            R.check_rpc_url(url)
        with pytest.raises(R.Refused):
            R.http_call(R.TokenBucket(1.0), R.Counters(), url=url)
        with pytest.raises(R.Refused):
            R.main(["--out-dir", "/nonexistent-x", "--rpc-url", url])
