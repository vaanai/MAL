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


def _block(slot: int, n: int = 1) -> dict:
    txs = [
        {
            "meta": {"err": None, "logMessages": [_line("pump_trade_event.b64")]},
            "transaction": {"signatures": [f"{SIG}{slot}x{i}"]},
        }
        for i in range(n)
    ]
    return {"slot": slot, "blockTime": BLOCK_TIME, "parentSlot": slot - 1, "transactions": txs}


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
        return _block(slot), None


def _follower(tmp_path, rpc, clock=None, **kw):
    kw.setdefault("sleep", lambda s: None)
    kw.setdefault("log", lambda m: None)
    return TipFollower(
        out_dir=tmp_path / "out",
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
    assert f.retain(2) == 1
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
