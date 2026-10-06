"""Tests for tools/fast_tip_follower.py. No network: the RPC is a mock."""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tools import fast_tip_follower as ftf
from tools.fast_tip_follower import SKIP_CODES, TipFollower, TransientError, hour_of_ms
from tools.pump_history_backfill import redact_rpc_url, rows_from_block

FIXTURES = Path(__file__).resolve().parent.parent / "observe" / "fixtures"
SIG = "sig" + "1" * 77
BLOCK_TIME = 1790318932


def _line(name: str) -> str:
    blob = (FIXTURES / name).read_text(encoding="utf-8").strip()
    return blob if blob.startswith("Program data") else "Program data: " + blob


def _block(slot: int, n: int = 1, parent: int | None = None) -> dict:
    txs = [
        {
            "meta": {"err": None, "logMessages": [_line("pump_trade_event.b64")]},
            "transaction": {"signatures": [f"{SIG}{slot}x{i}"]},
        }
        for i in range(n)
    ]
    return {"slot": slot, "blockTime": BLOCK_TIME, "parentSlot": slot - 1 if parent is None else parent, "transactions": txs}


class Clock:
    def __init__(self, ms: int) -> None:
        self.ms = ms

    def __call__(self) -> int:
        self.ms += 7
        return self.ms


class Rpc:
    """Scripted RPC. script[slot] is a list of outcomes consumed per call: block, int code, 'T'."""

    def __init__(self, tip: int, skipped=(), script=None) -> None:
        self.tip = tip
        self.skipped = set(skipped)
        self.script = script or {}
        self.calls: list[int] = []

    def get_tip(self) -> int:
        return self.tip

    def fetch(self, slot: int):
        self.calls.append(slot)
        queue = self.script.get(slot)
        if queue:
            item = queue.pop(0)
            if item == "T":
                raise TransientError("boom")
            if isinstance(item, int):
                return None, item
            return item, None
        if slot in self.skipped:
            return None, -32007
        parent = slot - 1
        while parent in self.skipped:
            parent -= 1
        return _block(slot, parent=parent), None


def _follower(tmp_path, rpc, clock=None, **kw):
    kw.setdefault("sleep", lambda s: None)
    kw.setdefault("log", lambda m: None)
    return TipFollower(
        out_dir=tmp_path / "out",
        creates_dir=tmp_path / "creates",
        state_dir=tmp_path / "state",
        fetch=rpc.fetch,
        get_tip=rpc.get_tip,
        clock_ms=clock or Clock(1_790_318_933_000),
        **kw,
    )


def _rows(path: Path):
    return [json.loads(x) for x in path.read_text().splitlines()]


def _all(tmp_path, kind="trades"):
    out = []
    for p in sorted((tmp_path / "out").glob(f"{kind}-*.jsonl")):
        out += _rows(p)
    return out


def _seed(f, last):
    f.last_done = last
    f._checkpoint(None)


def test_every_slot_once_in_order(tmp_path):
    rpc = Rpc(tip=110, skipped={103, 107})
    f = _follower(tmp_path, rpc)
    _seed(f, 99)
    assert f.step() == 11
    assert rpc.calls.count(100) == 1
    fetched = [s for s in rpc.calls if s not in (103, 107)]
    assert fetched == [100, 101, 102, 104, 105, 106, 108, 109, 110]
    assert sorted(set(rpc.calls)) == list(range(100, 111))
    slots = [r["slot"] for r in _all(tmp_path)]
    assert slots == sorted(slots) and set(slots) == set(fetched)
    assert f.last_done == 110
    rpc.calls.clear()
    assert f.step() == 0 and rpc.calls == []


def test_no_checkpoint_starts_at_tip(tmp_path):
    rpc = Rpc(tip=500)
    f = _follower(tmp_path, rpc)
    f.step()
    assert rpc.calls == [500]


def test_skipped_slots_recorded_not_retried_forever(tmp_path):
    rpc = Rpc(tip=12, skipped={11})
    f = _follower(tmp_path, rpc, skip_rechecks=1)
    _seed(f, 9)
    f.step()
    assert rpc.calls.count(11) == 2  # one fetch + one recheck, then recorded
    assert f.skipped == 1 and f.gaps == 0
    rows = [r for p in (tmp_path / "out").glob("skipped-slots-*.jsonl") for r in _rows(p)]
    assert [(r["slot"], r["kind"]) for r in rows] == [(11, "skipped")]
    assert f.last_done == 12
    assert set(SKIP_CODES) == {-32007, -32009}


def test_transient_retried_then_ok(tmp_path):
    rpc = Rpc(tip=10, script={10: ["T", "T", _block(10)]})
    f = _follower(tmp_path, rpc)
    _seed(f, 9)
    f.step()
    assert rpc.calls == [10, 10, 10]
    assert f.retries == 2 and f.gaps == 0
    assert len(_all(tmp_path)) == 1


def test_persistent_failure_is_counted_gap(tmp_path):
    rpc = Rpc(tip=11, script={10: ["T"] * 50})
    f = _follower(tmp_path, rpc, max_retries=3)
    _seed(f, 9)
    f.step()
    assert rpc.calls.count(10) == 4
    assert f.gaps == 1 and f.last_done == 11
    gaps = _rows(tmp_path / "out" / "gaps.jsonl")
    assert [(g["slot"], g["kind"]) for g in gaps] == [(10, "gap")]
    assert f.status()["gaps"] == 1
    assert [r["slot"] for r in _all(tmp_path)] == [11]


def test_restart_from_checkpoint_no_hole_no_dup(tmp_path):
    rpc = Rpc(tip=102)
    f = _follower(tmp_path, rpc)
    _seed(f, 99)
    f.step()
    f.writer.close()
    rpc2 = Rpc(tip=106)
    f2 = _follower(tmp_path, rpc2)
    assert f2.last_done == 102
    f2.step()
    assert rpc2.calls == [103, 104, 105, 106]
    assert [r["slot"] for r in _all(tmp_path)] == list(range(100, 107))


def test_crash_mid_write_is_truncated_on_restart(tmp_path):
    rpc = Rpc(tip=101)
    clock = Clock(1_790_318_933_000)
    f = _follower(tmp_path, rpc, clock)
    _seed(f, 99)
    f.step()
    f.writer.close()
    hour = hour_of_ms(f.last_t_recv_ms)
    path = tmp_path / "out" / f"trades-{hour}.jsonl"
    size = path.stat().st_size
    f._checkpoint({"slot": 102, "offsets": {path.name: size}})
    with open(path, "ab") as fh:
        fh.write(b'{"slot":102,"partial":')
    rpc2 = Rpc(tip=102)
    f2 = _follower(tmp_path, rpc2, clock)
    f2.step()
    assert rpc2.calls == [102]
    assert [r["slot"] for r in _all(tmp_path)] == [100, 101, 102]


def test_rows_schema_identical_to_walker(tmp_path):
    rpc = Rpc(tip=300)
    f = _follower(tmp_path, rpc)
    _seed(f, 299)
    f.step()
    got = _all(tmp_path)
    want = rows_from_block(_block(300), {}, ftf.FEED)["trades"]
    assert len(got) == len(want) == 1
    g = dict(got[0])
    assert isinstance(g.pop("t_recv_ms"), int)
    w = dict(want[0])
    assert w.pop("t_recv_ms") is None
    assert (g.pop("source"), w.pop("source")) == ("tip", "backfill")
    assert g == w
    assert list(got[0].keys()) == list(want[0].keys())


def test_pumpswap_unresolved_resolved_via_lookup(tmp_path):
    from observe.trade_decode import WSOL_MINT

    blk = _block(5)
    blk["transactions"][0]["meta"]["logMessages"] = [_line("pumpswap_sell_event.b64")]
    rpc = Rpc(tip=5, script={5: [blk]})
    seen = []

    def lookup(pools):
        seen.append(pools)
        return {pools[0]: ("Mint111", WSOL_MINT)}

    f = _follower(tmp_path, rpc, lookup=lookup)
    _seed(f, 4)
    f.step()
    rows = _all(tmp_path)
    assert len(seen) == 1 and rows[0]["mint"] == "Mint111" and rows[0]["v"] == 2


def test_t_recv_non_decreasing_even_if_clock_steps_back(tmp_path):
    rpc = Rpc(tip=120)
    times = iter([5_000, 4_000, 6_000, 3_000] + [7_000] * 100)
    f = _follower(tmp_path, rpc, lambda: next(times))
    _seed(f, 115)
    f.step()
    t = [r["t_recv_ms"] for r in _all(tmp_path)]
    assert t == sorted(t) and len(t) == 5


def test_hourly_rotation(tmp_path):
    base = int(datetime(2026, 10, 6, 10, 59, 59, tzinfo=timezone.utc).timestamp() * 1000)
    ticks = iter([base, base + 400, base + 1500, base + 1600, base + 1700] + [base + 9000] * 10)
    rpc = Rpc(tip=4)
    logs = []
    f = _follower(tmp_path, rpc, lambda: next(ticks), log=logs.append)
    _seed(f, 0)
    f.step()
    names = sorted(p.name for p in (tmp_path / "out").glob("trades-*.jsonl"))
    assert names == ["trades-2026-10-06T10.jsonl", "trades-2026-10-06T11.jsonl"]
    assert any("hour 2026-10-06T10 counts" in m for m in logs)
    for n in names:
        t = [r["t_recv_ms"] for r in _rows(tmp_path / "out" / n)]
        assert hour_of_ms(t[0]) == n[7:20]


def test_retention(tmp_path):
    rpc = Rpc(tip=1)
    f = _follower(tmp_path, rpc, lambda: int(datetime(2026, 10, 6, 12, tzinfo=timezone.utc).timestamp() * 1000))
    out = tmp_path / "out"
    for name in ("trades-2026-10-01T05.jsonl", "creates-2026-10-05T13.jsonl", "trades-2026-10-06T11.jsonl", "gaps.jsonl"):
        (out / name).write_text("x")
    (tmp_path / "creates" / "observe-2026-10-01.jsonl").write_text("x")
    (tmp_path / "creates" / "observe-2026-10-05.jsonl").write_text("x")
    assert f.retain(2) == 2
    assert not (out / "trades-2026-10-01T05.jsonl").exists()
    assert (out / "creates-2026-10-05T13.jsonl").exists() and (out / "gaps.jsonl").exists()


def test_status_fields_and_no_key(tmp_path):
    key = "SECRETKEY123456"
    from tools.pump_history_backfill import helius_http_url

    url = helius_http_url(key)
    assert key not in redact_rpc_url(f"getBlock failed {url}")
    rpc = Rpc(tip=3)
    msgs = []
    f = _follower(tmp_path, rpc, log=msgs.append, credits=lambda: 42)
    _seed(f, 1)
    f.step()
    f.heartbeat(force=True)
    st = json.loads((tmp_path / "state" / "status.json").read_text())
    for k in ("tip_slot", "last_done_slot", "lag_slots", "lag_ms", "retries", "gaps", "skipped", "credits_used"):
        assert k in st
    assert st["credits_used"] == 42 and st["lag_slots"] == 0
    blob = json.dumps(st) + "".join(msgs) + "".join(
        p.read_text() for p in (tmp_path / "out").iterdir() if p.is_file()
    )
    assert key not in blob


def test_rpc_errors_do_not_leak_key(monkeypatch):
    key = "SECRETKEY123456"
    url = f"https://example.invalid/?api-key={key}"

    def boom(*a, **k):
        raise RuntimeError(f"getBlock transport {url}")

    monkeypatch.setattr(ftf, "rpc_call", boom)
    fetch, get_tip, credits, _ = ftf.make_rpc(url, 1000.0)
    with pytest.raises(TransientError) as ei:
        fetch(1)
    assert key not in str(ei.value)
    with pytest.raises(TransientError) as ei:
        get_tip()
    assert key not in str(ei.value)


def test_refuses_without_key(monkeypatch):
    monkeypatch.delenv("HELIUS_API_KEY", raising=False)
    assert ftf.main(["--out", "/nonexistent-x"]) == 2


def test_run_loop_stops(tmp_path):
    rpc = Rpc(tip=3)
    f = _follower(tmp_path, rpc)
    stop = threading.Event()
    orig = f.step

    def step():
        n = orig()
        stop.set()
        return n

    f.step = step
    f.run(stop)
    assert (tmp_path / "state" / "status.json").is_file()


def _create_log() -> str:
    import base64

    from tools.pump_history_backfill import _CREATE_DISC

    def s(t: str) -> bytes:
        b = t.encode()
        return len(b).to_bytes(4, "little") + b

    mint, user, creator = bytes([1] * 32), bytes([2] * 32), bytes([3] * 32)
    raw = (
        _CREATE_DISC + s("Name") + s("SYM") + s("uri") + mint + bytes([9] * 32) + user + creator
        + (1_790_318_900).to_bytes(8, "little", signed=True)
        + (1_073_000_000_000_000).to_bytes(8, "little")  # vtok
        + (30_000_000_000).to_bytes(8, "little")  # vsol
        + (793_000_000_000_000).to_bytes(8, "little")
        + (1_000_000_000_000_000).to_bytes(8, "little")
    )
    return "Program data: " + base64.b64encode(raw).decode()


def _create_block(slot: int) -> dict:
    blk = _block(slot)
    blk["transactions"][0]["meta"]["logMessages"] = [_create_log()]
    return blk


def test_precreate_next_hour_and_day(tmp_path):
    h23 = int(datetime(2026, 10, 6, 23, 59, 56, tzinfo=timezone.utc).timestamp() * 1000)
    f = _follower(tmp_path, Rpc(tip=1), lambda: h23)
    f.precreate(h23)
    out, cr = tmp_path / "out", tmp_path / "creates"
    for n in ("trades-2026-10-06T23.jsonl", "trades-2026-10-07T00.jsonl", "migrations-2026-10-07T00.jsonl"):
        assert (out / n).is_file() and (out / n).stat().st_size == 0, n
    assert (cr / "observe-2026-10-06.jsonl").is_file() and (cr / "observe-2026-10-07.jsonl").is_file()
    mid = int(datetime(2026, 10, 6, 10, 30, tzinfo=timezone.utc).timestamp() * 1000)
    f.precreate(mid)
    assert not (out / "trades-2026-10-06T11.jsonl").exists()


def test_first_block_of_hour_lands_in_precreated_file(tmp_path):
    t = int(datetime(2026, 10, 6, 10, 59, 57, tzinfo=timezone.utc).timestamp() * 1000)
    f = _follower(tmp_path, Rpc(tip=1), lambda: t)
    f.precreate(t)
    nxt = tmp_path / "out" / "trades-2026-10-06T11.jsonl"
    assert nxt.is_file() and nxt.stat().st_size == 0  # exists before any row, so a tail starts at 0
    f2 = _follower(tmp_path, Rpc(tip=5), lambda: t + 4000)
    f2.last_done = 4
    f2.step()
    assert nxt.stat().st_size > 0


def test_backlog_jump_records_range_and_never_stamps_old_blocks(tmp_path):
    rpc = Rpc(tip=1000)
    f = _follower(tmp_path, rpc, backlog_slots=50)
    _seed(f, 100)
    f.step()
    gaps = _rows(tmp_path / "out" / "gaps.jsonl")
    jump = [g for g in gaps if g.get("reason") == "backlog_jump"]
    assert len(jump) == 1 and (jump[0]["from_slot"], jump[0]["to_slot"]) == (101, 999)
    assert rpc.calls == [1000]  # only the tip was fetched
    assert f.gaps == 899 and f.backlog_jumps == 1 and f.last_done == 1000


def test_small_lag_is_caught_up_not_jumped(tmp_path):
    rpc = Rpc(tip=140)
    f = _follower(tmp_path, rpc, backlog_slots=50)
    _seed(f, 100)
    while f.step():
        pass
    assert f.backlog_jumps == 0 and rpc.calls == list(range(101, 141))


def test_lookup_failure_counted_and_logged_per_slot(tmp_path):
    blk = _block(5)
    blk["transactions"][0]["meta"]["logMessages"] = [_line("pumpswap_sell_event.b64")]

    def bad(pools):
        raise RuntimeError("net")

    f = _follower(tmp_path, Rpc(tip=5, script={5: [blk]}), lookup=bad)
    _seed(f, 4)
    f.step()
    assert f.unresolved_dropped == 1 and f.lookup_failures == 1
    gaps = _rows(tmp_path / "out" / "gaps.jsonl")
    assert [(g["slot"], g["kind"], g["n"]) for g in gaps] == [(5, "unresolved_dropped", 1)]
    assert _all(tmp_path) == []


def test_lookup_goes_through_limiter_and_budget(monkeypatch):
    calls = []

    def fake(url, method, params, limiter, timeout=60.0, budget=None, header_out=None):
        calls.append((method, limiter is not None, budget is not None))
        return {"value": [None]}, 0, None

    monkeypatch.setattr(ftf, "rpc_call", fake)
    _f, _t, _credits, lookup = ftf.make_rpc("https://example.invalid/?api-key=K", 1000.0)
    assert lookup(["PoolA"]) == {}
    assert calls == [("getMultipleAccounts", True, True)]


def test_credits_per_call_is_a_flag(monkeypatch):
    seen = []
    real = ftf.CreditBudget

    def spy(cap, per_call, used=0):
        seen.append(per_call)
        return real(cap, per_call, used)

    monkeypatch.setattr(ftf, "CreditBudget", spy)
    ftf.make_rpc("https://example.invalid/?api-key=K", 10.0)
    ftf.make_rpc("https://example.invalid/?api-key=K", 10.0, credits_per_call=10)
    assert seen == [1, 10]


def test_observe_create_row_feeds_runner_reader(tmp_path):
    from tools.paper_price_path import create_from_observe_row

    f = _follower(tmp_path, Rpc(tip=7, script={7: [_create_block(7)]}))
    _seed(f, 6)
    f.step()
    day_files = list((tmp_path / "creates").glob("observe-*.jsonl"))
    rows = [r for p in day_files for r in _rows(p)]
    assert len(rows) == 1
    row = rows[0]
    for k in ("event_ts", "block_time", "initialBuy", "solAmount"):
        assert k not in row
    assert row["stream"] == "subscribeNewToken" and row["txType"] == "create"
    assert row["t_ws"].endswith("+00:00") and "." in row["t_ws"]
    sig = create_from_observe_row(row)
    assert sig is not None
    assert sig.mint == row["mint"] and sig.creator == row["traderPublicKey"]
    assert sig.t_signal_ms == f.last_t_recv_ms
    assert sig.v_sol == 30.0 and sig.v_token_ui == 1_073_000_000.0
    assert sig.initial_buy_ui is None and sig.sol_amount is None
    assert day_files[0].name == "observe-" + hour_of_ms(f.last_t_recv_ms)[:10] + ".jsonl"


def test_idle_backoff_on_tip_failures(tmp_path):
    waits = []

    class Stop(threading.Event):
        def wait(self, timeout=None):
            waits.append(timeout)
            if len(waits) >= 9:
                self.set()
            return self.is_set()

    def tip():
        raise TransientError("getSlot transport")

    f = _follower(tmp_path, Rpc(tip=1))
    f.get_tip = tip
    f.run(Stop())
    assert waits[:6] == [0.4, 0.8, 1.6, 3.2, 6.4, 12.8]
    assert max(waits) <= 30.0 and waits[6:8] == [25.6, 30.0]


def test_tip_breaker_pauses_on_repeated_429(tmp_path):
    waits = []

    class Stop(threading.Event):
        def wait(self, timeout=None):
            waits.append(timeout)
            if len(waits) >= 4:
                self.set()
            return self.is_set()

    def tip():
        raise TransientError("getSlot gave up after 429")

    f = _follower(tmp_path, Rpc(tip=1), breaker_after=2, breaker_pause_s=60.0)
    f.get_tip = tip
    f.run(Stop())
    assert f.breaker_trips >= 1 and 60.0 in waits


def test_fetch_breaker_pauses_on_repeated_429(tmp_path):
    sleeps = []
    state = {"n": 6}

    def fetch(slot):
        if state["n"] > 0:
            state["n"] -= 1
            raise TransientError("getBlock http 429")
        return _block(slot, parent=slot - 1), None

    f = _follower(tmp_path, Rpc(tip=10), breaker_after=3, breaker_pause_s=60.0, sleep=sleeps.append)
    f.fetch = fetch
    _seed(f, 9)
    f.step()
    assert f.breaker_trips == 2 and sleeps.count(60.0) == 2 and f.gaps == 0


def test_skip_contradicted_by_parent_becomes_gap_and_is_refetched(tmp_path):
    # 103 reports skipped, but block 104 has parentSlot 103: the skip was wrong.
    rpc = Rpc(tip=104, script={103: [-32007, -32007, _block(103, parent=102)]})
    f = _follower(tmp_path, rpc, skip_rechecks=1)
    _seed(f, 102)
    f.step()
    gaps = _rows(tmp_path / "out" / "gaps.jsonl")
    assert [g["reason"] for g in gaps] == ["skip_contradicted"]
    assert f.skipped == 0
    assert [r["slot"] for r in _all(tmp_path)] == [103, 104]  # refetched block is written before 104


def test_confirmed_skip_stays_a_skip(tmp_path):
    rpc = Rpc(tip=105, skipped={103, 104})
    f = _follower(tmp_path, rpc)
    _seed(f, 102)
    f.step()
    assert f.skipped == 2 and f.gaps == 0


def test_pool_cache_is_lru_capped():
    c = ftf.LRUPools(3)
    for i in range(5):
        c[f"p{i}"] = ("b", "q")
    assert list(c) == ["p2", "p3", "p4"]
    c.get("p2")
    c["p5"] = ("b", "q")
    assert "p2" in c and "p3" not in c
    c.update({"p6": ("b", "q")})
    assert len(c) == 3


def test_getblock_version_matches_walker(monkeypatch):
    seen = {}

    def fake(url, method, params, *a, **k):
        seen["p"] = params
        return None, 0, -32007

    monkeypatch.setattr(ftf, "rpc_call", fake)
    fetch, *_ = ftf.make_rpc("https://example.invalid/?api-key=K", 10.0)
    fetch(5)
    from tools.pump_history_backfill import _getblock_params

    walker = _getblock_params(5, full=True)[1]
    assert seen["p"][1]["maxSupportedTransactionVersion"] == walker["maxSupportedTransactionVersion"] == 1
    assert seen["p"][1]["transactionDetails"] == "full"


# ---------------------------------------------------------------- PumpSwap virtual quote reserve (V)

V_LAMPORTS = 17_580_000_000


def _pool_account(v: int | None, short_tail: bool = False) -> bytes:
    disc = bytes.fromhex("f19a6d0411b16dbc")
    body = bytes([1]) + (0).to_bytes(2, "little") + b"".join(bytes([i]) * 32 for i in range(1, 7))
    body += (5).to_bytes(8, "little") + bytes([9]) * 32
    if v is not None:
        body += b"\x00\x00" + v.to_bytes(8, "little")
    elif not short_tail:
        body += b"\x00" * 10
    return disc + body


def test_decode_pool_virtual_reads_v_and_never_zero_fills():
    from tools.pumpswap_tx import parse_pool_account

    raw = _pool_account(V_LAMPORTS)
    assert ftf.decode_pool_virtual(raw) == V_LAMPORTS == parse_pool_account(raw)["virtual_quote_reserves"]
    assert ftf.decode_pool_virtual(_pool_account(0)) == 0  # a real on-chain 0 is kept as 0
    assert ftf.decode_pool_virtual(_pool_account(None, short_tail=True)) is None  # no tail: unknown
    assert ftf.decode_pool_virtual(b"\x00" * 50) is None


def _swap_block(slot):
    blk = _block(slot)
    blk["transactions"][0]["meta"]["logMessages"] = [_line("pumpswap_sell_event.b64")]
    return blk


def test_make_rpc_lookup_records_v_and_v_lookup_returns_it(monkeypatch):
    import base64

    good, bare = _pool_account(V_LAMPORTS), _pool_account(None, short_tail=True)

    def fake(url, method, params, limiter, timeout=60.0, budget=None, header_out=None):
        enc = lambda b: {"data": [base64.b64encode(b).decode(), "base64"]}
        return {"value": [enc(good), enc(bare), None]}, 0, None

    monkeypatch.setattr(ftf, "rpc_call", fake)
    *_, lookup = ftf.make_rpc("https://example.invalid/?api-key=K", 1000.0)
    found = lookup(["P1", "P2", "P3"])
    assert set(found) == {"P1", "P2"}
    assert lookup.v_seen == {"P1": V_LAMPORTS, "P2": None}
    assert lookup.v_lookup(["P1", "P2", "P3"]) == {"P1": V_LAMPORTS, "P2": None}  # P3 absent: retried later


def test_pumpswap_trade_rows_carry_virtual_quote_reserve(tmp_path):
    from observe.trade_decode import WSOL_MINT

    asked = []

    def lookup(pools):
        return {pools[0]: ("Mint111", WSOL_MINT)}

    def v_lookup(pools):
        asked.append(list(pools))
        return {pools[0]: V_LAMPORTS}

    f = _follower(tmp_path, Rpc(tip=6, script={5: [_swap_block(5)], 6: [_swap_block(6)]}), lookup=lookup, v_lookup=v_lookup)
    _seed(f, 4)
    f.step()
    rows = _all(tmp_path)
    assert len(rows) == 2 and all(r["virtual_quote_reserve"] == V_LAMPORTS for r in rows)
    assert all(type(r["virtual_quote_reserve"]) is int for r in rows)
    assert len(asked) == 1  # cached per pool: one read for both rows


def test_unreadable_v_is_null_not_zero_and_is_retried(tmp_path):
    from observe.trade_decode import WSOL_MINT

    answers = [{}, {}]  # the first two reads return nothing for the pool

    def v_lookup(pools):
        return answers.pop(0) if answers else {pools[0]: V_LAMPORTS}

    f = _follower(tmp_path, Rpc(tip=5, script={5: [_swap_block(5)]}),
                  lookup=lambda p: {p[0]: ("Mint111", WSOL_MINT)}, v_lookup=v_lookup)
    _seed(f, 4)
    f.step()
    rows = _all(tmp_path)
    assert rows[0]["virtual_quote_reserve"] is None and "virtual_quote_reserve" in rows[0]
    pool = rows[0]["pool"]
    assert pool not in f.v_cache  # a failed read is not cached
    f._v_retry_at.clear()
    rows2 = [{"venue": "pumpswap", "pool": pool}]
    f._stamp_virtual(rows2)
    assert rows2[0]["virtual_quote_reserve"] is None  # second empty answer
    f._v_retry_at.clear()
    f._stamp_virtual(rows2)
    assert rows2[0]["virtual_quote_reserve"] == V_LAMPORTS


def test_no_lookup_means_null_and_bonding_rows_untouched(tmp_path):
    f = _follower(tmp_path, Rpc(tip=5, script={5: [_block(5)]}))
    _seed(f, 4)
    f.step()
    assert all("virtual_quote_reserve" not in r for r in _all(tmp_path))
    rows = [{"venue": "pumpswap", "pool": "X"}]
    f._stamp_virtual(rows)
    assert rows == [{"venue": "pumpswap", "pool": "X", "virtual_quote_reserve": None}]


def test_known_v_is_never_overwritten_and_no_v_is_retried_with_backoff(tmp_path):
    f = _follower(tmp_path, Rpc(tip=4))
    f._note_v("P", V_LAMPORTS, 0.0)
    f._note_v("P", None, 1.0)  # a failed re-read does not erase a known V
    assert f.v_cache.get("P") == V_LAMPORTS
    f._note_v("Q", None, 0.0)  # decoded without V: not cached, retried
    assert "Q" not in f.v_cache and f._v_retry_at["Q"] == ftf.V_RETRY_S
    f._note_v("Q", None, 0.0)
    assert f._v_retry_at["Q"] == 2 * ftf.V_RETRY_S
    for _ in range(10):
        f._note_v("Q", None, 0.0)
    assert f._v_retry_at["Q"] == ftf.V_RETRY_CAP_S
    f._note_v("Q", V_LAMPORTS, 5.0)  # later read succeeds
    assert f.v_cache.get("Q") == V_LAMPORTS and "Q" not in f._v_retry_at


# ---------------------------------------------------------------- parallel fetch, ordered commit
import random
import time as _time


class SlowRpc(Rpc):
    """Thread-safe fake chain with random per-slot latency; counts concurrency and limiter use."""

    def __init__(self, tip, skipped=(), max_lat=0.01, seed=3) -> None:
        super().__init__(tip, skipped)
        self.rng = random.Random(seed)
        self.lat = {}
        self.lock = threading.Lock()
        self.max_lat = max_lat
        self.active = 0
        self.max_active = 0
        self.max_buffered = 0

    def fetch(self, slot):
        with self.lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            lat = self.lat.setdefault(slot, self.rng.random() * self.max_lat)
        _time.sleep(lat)
        try:
            return super().fetch(slot)
        finally:
            with self.lock:
                self.active -= 1


def _run_steps(f, until_slot, timeout=20.0):
    t0 = _time.monotonic()
    while (f.last_done or 0) < until_slot and _time.monotonic() - t0 < timeout:
        f.step()
    f.close_pool()


def _snapshot(tmp_path):
    out = {}
    for base in ("out", "creates"):
        for p in sorted((tmp_path / base).glob("*.jsonl")):
            if p.name.startswith("gaps"):
                continue
            out[f"{base}/{p.name}"] = p.read_bytes()
    return out


def _fixed_clock():
    return Clock(1_790_318_933_000)


def test_parallel_output_byte_identical_to_sequential(tmp_path):
    skipped = {105, 106, 111, 130}
    seq_dir, par_dir = tmp_path / "seq", tmp_path / "par"
    seq = _follower(seq_dir, SlowRpc(140, skipped, max_lat=0), backlog_slots=1000)
    _seed(seq, 100)
    seq.step(); seq.step()
    assert seq.last_done == 140
    rpc = SlowRpc(140, skipped, max_lat=0.01)
    par = _follower(par_dir, rpc, fetch_workers=8, backlog_slots=1000)
    _seed(par, 100)
    _run_steps(par, 140)
    assert par.last_done == 140
    assert _snapshot(seq_dir) == _snapshot(par_dir)
    assert rpc.max_active > 1  # actually concurrent
    slots = [r["slot"] for r in _all(par_dir)]
    assert slots == sorted(slots)
    assert par.gaps == 0 and par.skipped == seq.skipped == 4


def test_parallel_no_gaps_and_bounded_buffer(tmp_path):
    rpc = SlowRpc(400, max_lat=0.005)
    f = _follower(tmp_path, rpc, fetch_workers=4, backlog_slots=1000)
    _seed(f, 100)
    seen = 0
    t0 = _time.monotonic()
    while f.last_done < 400 and _time.monotonic() - t0 < 20:
        f.step()
        seen = max(seen, len(f._futs))
    f.close_pool()
    assert f.last_done == 400 and f.gaps == 0 and f.backlog_jumps == 0
    assert seen <= 8 and rpc.max_active <= 4
    slots = [r["slot"] for r in _all(tmp_path)]
    assert slots == list(range(101, 401))


def test_parallel_backlog_jump_when_chain_outruns(tmp_path):
    rpc = SlowRpc(1000, max_lat=0)
    f = _follower(tmp_path, rpc, fetch_workers=4, backlog_slots=150)
    _seed(f, 100)
    f.step()
    f.close_pool()
    assert f.backlog_jumps == 1
    assert min(r["slot"] for r in _all(tmp_path)) >= 999
    assert f.status()["backlog_jumps"] == 1


def test_parallel_checkpoint_is_highest_contiguous_after_midflight_stop(tmp_path):
    rpc = SlowRpc(200, max_lat=0)
    rpc.lat[104] = 0.5  # head of line blocks while 105.. finish
    f = _follower(tmp_path, rpc, fetch_workers=4, backlog_slots=1000)
    _seed(f, 103)
    f.step()  # commits nothing past 103 until 104 lands
    f.close_pool()
    assert f.last_done == 103
    ck = json.loads((tmp_path / "state" / "checkpoint.json").read_text())
    assert ck["last_done"] == 103
    assert not list((tmp_path / "out").glob("trades-*.jsonl")) or _all(tmp_path) == []


def test_parallel_limiter_shared_and_never_exceeded(monkeypatch):
    from tools import pump_history_backfill as phb

    stamps = []
    lim = phb.RateLimiter(50)
    orig = lim.acquire

    def acquire():
        orig()
        stamps.append(_time.monotonic())

    lim.acquire = acquire  # type: ignore[method-assign]
    stop = threading.Event()

    def worker():
        for _ in range(10):
            lim.acquire()

    ts = [threading.Thread(target=worker) for _ in range(8)]
    [t.start() for t in ts]; [t.join() for t in ts]
    stamps.sort()
    assert len(stamps) == 80
    # 50 rps: any 1 s window holds at most ~51 calls
    assert all(stamps[i + 51] - stamps[i] >= 0.95 for i in range(len(stamps) - 51))
    b = phb.CreditBudget(cap=100, per_call=1)
    got = []
    ts = [threading.Thread(target=lambda: got.extend(b.reserve() for _ in range(50))) for _ in range(8)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert sum(got) == 100 and b.used == 100


def test_parallel_status_metrics_and_summary(tmp_path):
    logs = []
    rpc = SlowRpc(130, max_lat=0.002)
    f = _follower(tmp_path, rpc, fetch_workers=4, backlog_slots=1000, log=logs.append, summary_every_s=0.0)
    _seed(f, 100)
    _run_steps(f, 130)
    st = f.status()
    for k in ("lag_slots", "block_lag_ms_p50", "block_lag_ms_p90", "fetch_ms_p50", "fetch_ms_p90", "inflight", "backlog_jumps"):
        assert k in st
    assert st["fetch_ms_p50"] is not None and st["lag_slots"] == 0
    assert any(m.startswith("summary ") for m in logs)


def test_parallel_workers_clamped_and_defaults():
    assert ftf.MAX_FETCH_WORKERS == 16
    ap_src = Path(ftf.__file__).read_text()
    assert "--fetch-workers" in ap_src and "default=15.0" in ap_src and "default=150" in ap_src


def _signed_pool_account(v: int, n: int) -> bytes:
    base = _pool_account(V_LAMPORTS)  # 253 bytes, V at 245..253
    raw = bytearray(base) + b"\x00" * max(0, n - len(base))
    raw[245:261] = v.to_bytes(16, "little", signed=True) if n >= 261 else (v & (2**64 - 1)).to_bytes(8, "little") + raw[253:261]
    return bytes(raw[:n])


def test_decode_pool_virtual_is_signed_and_never_above_i64():
    for n in (300, 301):
        for v in (V_LAMPORTS, 0, -1, -5_000_000_000):
            assert ftf.decode_pool_virtual(_signed_pool_account(v, n)) == v
    big = bytearray(_signed_pool_account(0, 301))
    big[245:261] = (2**63).to_bytes(16, "little")
    assert ftf.decode_pool_virtual(bytes(big)) is None


def test_stamp_writes_true_signed_v_and_nothing_above_2_63(tmp_path):
    f = _follower(tmp_path, Rpc(tip=4))
    for n in (300, 301):
        for v in (V_LAMPORTS, 0, -5_000_000_000):
            f.v_cache.clear()
            f._v_retry_at.clear()
            got = ftf.decode_pool_virtual(_signed_pool_account(v, n))
            f.v_lookup = lambda pools, got=got: {pools[0]: got}
            rows = [{"venue": "pumpswap", "pool": "P"}, {"venue": "bonding", "pool": "P"}]
            f._stamp_virtual(rows)
            assert rows[0]["virtual_quote_reserve"] == v and type(v) is int
            assert abs(rows[0]["virtual_quote_reserve"]) < 2**63
            assert "virtual_quote_reserve" not in rows[1]
