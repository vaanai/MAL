"""Tests for the follower main-thread speed-up (claude/follower-mainthread-speedup). Synthetic only: no network, no tape.

  /data/mal/venv/bin/python -m pytest tools/test_follower_mainthread_speedup.py -q

1. md5 equivalence of everything the follower writes (tape, creates, gaps, checkpoint.json) between this head and
   BASE_COMMIT 0bde508f, at fetch workers 1, 8 and 16, with seeded random fetch delays so that completion order varies.
   The clock is constant, so t_recv_ms is the same in both runs (t_recv_ms is the only field allowed to differ).
2. b58decode_fast / cpi_events_fast against the repo functions on many inputs.
3. KeepAliveRpc against a local HTTP/1.1 server: connection reuse, a peer-closed idle connection, errors, accounting.
4. Throughput with a mocked lookup delay: the pipeline keeps fetching while the main thread commits.
"""

from __future__ import annotations

import hashlib
import json
import random
import socket
import subprocess
import threading
import time
import types
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from observe.trade_decode import b58decode, b58encode
from tools import fast_tip_follower as ftf
from tools.pump_history_backfill import CreditBudget, RateLimiter, cpi_events_from_tx
from tools.test_tip_follower_inner_migration import bad_pool_blob, cblob, plain_tx
from tools.test_tip_migration_gap import HOUR0, SetClock, block, inner_cpi, short_logs, swap_trade, truncated_logs, tx

REPO = Path(__file__).resolve().parent.parent
BASE_COMMIT = "0bde508f"


def base_module():
    r = subprocess.run(["git", "-C", str(REPO), "show", f"{BASE_COMMIT}:tools/fast_tip_follower.py"],
                       capture_output=True, stdin=subprocess.DEVNULL, timeout=120)
    if r.returncode != 0:
        pytest.skip(f"{BASE_COMMIT} not in this clone")
    mod = types.ModuleType("fast_tip_follower_base_0bde508f")
    mod.__file__ = "fast_tip_follower_base.py"
    exec(compile(r.stdout.decode(), mod.__file__, "exec"), mod.__dict__)
    assert "KeepAliveRpc" not in mod.__dict__ and "cpi_events_fast" not in mod.__dict__
    return mod


# ------------------------------------------------------------------ 1. md5 equivalence
FIRST = 1000


def make_blocks(seed: int, n: int) -> dict[int, dict]:
    rng = random.Random(seed)
    out: dict[int, dict] = {}
    for i in range(n):
        slot = FIRST + i
        kind = rng.choice(["plain", "plain", "plain", "mig_inner", "mig_log", "bad_pool", "skip", "skip"])
        if kind == "skip":
            continue
        txs = [plain_tx(f"s{slot}a"), plain_tx(f"s{slot}b")]
        if kind == "mig_inner":
            txs.append(tx(f"s{slot}m", truncated_logs(), [inner_cpi(cblob(i % 40))]))
        elif kind == "mig_log":
            txs.append(tx(f"s{slot}m", short_logs(cblob(i % 40))))
        elif kind == "bad_pool":
            txs.append(tx(f"s{slot}m", truncated_logs(), [inner_cpi(bad_pool_blob(i % 40))]))
        out[slot] = block(slot, txs)
    # a skip contradicted by the next block: FIRST+50 is skipped, FIRST+51 names it as its parent
    out.pop(FIRST + 50, None)
    out[FIRST + 51] = dict(block(FIRST + 51, [plain_tx("s51x")]), parentSlot=FIRST + 50)
    out[FIRST] = block(FIRST, [plain_tx("first")])
    out[FIRST + n - 1] = block(FIRST + n - 1, [plain_tx("last")])
    return out


class DelayRpc:
    def __init__(self, blocks, seed, n, fixed_delay=None, max_delay=0.01):
        self.blocks = blocks
        self.n = n
        rng = random.Random(seed)
        self.delay = {s: (rng.random() * max_delay if fixed_delay is None else fixed_delay) for s in range(FIRST, FIRST + n + 5)}

    def get_tip(self):
        return FIRST + self.n - 1

    def fetch(self, slot):
        time.sleep(self.delay.get(slot, 0.0))
        b = self.blocks.get(slot)
        if b is None:
            return None, -32007
        return json.loads(json.dumps(b)), None


def run(mod, root: Path, workers: int, seed: int, n: int = 90, v_delay: float = 0.0, fixed_delay=None):
    blocks = make_blocks(seed, n)
    inject = {s: [swap_trade(s % 17, s, s, HOUR0 - 100 + s % 7), swap_trade(s % 11, s, s + 1, HOUR0 - 100 + s % 7)]
              for s in blocks if s % 3 != 1}
    real = mod.rows_from_block

    def wrapped(blk, cache, feed, **kw):
        res = real(blk, cache, feed, **kw)
        if not kw:
            res["trades"] = list(res["trades"]) + [dict(r) for r in inject.get(blk["slot"], [])]
        return res

    def v_lookup(pools):
        if v_delay:
            time.sleep(v_delay)
        return {p: (sum(map(ord, p)) * 1000 if sum(map(ord, p)) % 5 else None) for p in pools}

    mod.rows_from_block = wrapped
    try:
        rpc = DelayRpc(blocks, seed, n, fixed_delay)
        f = mod.TipFollower(out_dir=root / "tip", creates_dir=root / "creates", state_dir=root / "state", fetch=rpc.fetch,
                            get_tip=rpc.get_tip, v_lookup=v_lookup, lookup=lambda p: {}, fetch_workers=workers,
                            clock_ms=SetClock((HOUR0 + 30) * 1000), sleep=lambda s: None, log=lambda m: None,
                            max_retries=1, backlog_slots=100_000)
        f.last_done = FIRST - 1
        f._checkpoint(None)
        t0 = time.monotonic()
        for _ in range(5000):
            if f.last_done >= FIRST + n - 1:
                break
            f.step()
        elapsed = time.monotonic() - t0
        assert f.last_done == FIRST + n - 1
        f.close_pool()
        f.writer.close()
        f.cwriter.close()
    finally:
        mod.rows_from_block = real
    return f, elapsed


def digest(root: Path) -> dict[str, str]:
    out = {}
    for sub in ("tip", "creates", "state"):
        d = root / sub
        out.update({f"{sub}/{p.name}": hashlib.md5(p.read_bytes()).hexdigest() for p in sorted(d.iterdir())
                    if p.is_file() and p.name != "status.json"})
    return out


@pytest.mark.parametrize("workers", [1, 8, 16])
@pytest.mark.parametrize("seed", [1, 7])
def test_output_and_checkpoint_md5_equal_to_base(tmp_path, workers, seed):
    base = base_module()
    fo, _ = run(base, tmp_path / "old", workers, seed)
    fn, _ = run(ftf, tmp_path / "new", workers, seed)
    do, dn = digest(tmp_path / "old"), digest(tmp_path / "new")
    assert do == dn
    assert "state/checkpoint.json" in dn and any(k.startswith("tip/trades-") for k in dn)
    assert any(k.startswith("tip/migrations-") for k in dn) and any(k.startswith("tip/skipped") for k in dn)
    assert fn.blocks == fo.blocks and fn.gaps == fo.gaps and fn.skipped == fo.skipped
    so, sn = fo.status(), fn.status()
    assert set(sn) - set(so) == {"lookup_pool_ms", "lookup_pool_calls", "lookup_v_ms", "lookup_v_calls", "lookup_overlap_blocks", "prefetch_lookups", "prefetch_hits", "prefetch_discards", "prefetch_failures", "prefetch_v_values", "v_read_lead_ms_p50", "v_read_lead_ms_p90", "v_read_lead_ms_max"}
    assert fn.lookup_v_calls >= 1


def test_workers_agree_with_each_other(tmp_path):
    ds = []
    for w in (1, 8, 16):
        run(ftf, tmp_path / f"w{w}", w, 3)
        ds.append(digest(tmp_path / f"w{w}"))
    assert ds[0] == ds[1] == ds[2]


# ------------------------------------------------------------------ 4. throughput
def test_pipeline_refills_while_main_thread_commits(tmp_path):
    """Fetch 0.25 s, V lookup 0.04 s on the main thread per block: the base lets the pool idle during each drain."""
    times = {}
    for name, mod in (("base", base_module()), ("new", ftf)):
        _f, times[name] = run(mod, tmp_path / name, 8, 5, n=64, v_delay=0.04, fixed_delay=0.25)
    assert digest(tmp_path / "base") == digest(tmp_path / "new")
    print("throughput_s", times)
    assert times["new"] < 0.9 * times["base"], times


# ------------------------------------------------------------------ 2. base58 and CPI scan
def test_b58decode_fast_equals_repo_function():
    rng = random.Random(58)
    cases = ["", "1", "11", "111", "2", "z", "1z", "11112", "0", "O", "I", "l", "1O1", "abc!", "é", "1é", "١", " ", "1 ", "\x00",
             "zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz", "1" * 32, "1" * 9 + "2", "1" * 10 + "2", "1" * 11]
    for n in list(range(0, 120)) + [200, 500, 1232]:
        for zeros in (0, 1, 2, 5, 9, 10, 11):
            raw = b"\x00" * zeros + bytes(rng.randrange(256) for _ in range(n))
            cases.append(b58encode(raw))
            cases.append(b58encode(raw) + "0")
    alphabet = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    for _ in range(3000):
        cases.append("".join(rng.choice(alphabet) for _ in range(rng.randrange(0, 90))))
    for _ in range(300):
        cases.append("".join(rng.choice(alphabet + "0OIl+/=é ") for _ in range(rng.randrange(1, 40))))
    assert len(cases) > 4000
    for c in cases:
        want = b58decode(c)
        assert ftf.b58decode_fast(c) == want, repr(c)
        assert ftf._b58decode_cached(c) == want, repr(c)
        assert ftf._b58decode_cached(c) == want  # a cache hit


def test_cpi_events_fast_equals_repo_function():
    txs = [plain_tx("p"), tx("a", truncated_logs(), [inner_cpi(cblob(1))]),
           tx("b", [], [inner_cpi(cblob(2), 2), inner_cpi(cblob(3), 3)]),
           tx("c", [], [{"programIdIndex": 1, "data": "0OIl"}, {"programIdIndex": 1}, {"programIdIndex": "x", "data": "1"},
                        {"programIdIndex": 1, "data": ""}, {"programIdIndex": 1, "data": "1" * 40}, inner_cpi(cblob(4))]),
           {"transaction": {}, "meta": None}, {"meta": {"innerInstructions": [None, {"instructions": None}]}}]
    for t in txs:
        assert ftf.cpi_events_fast(t) == cpi_events_from_tx(t)
    assert any(ftf.cpi_events_fast(t) for t in txs)


# ------------------------------------------------------------------ 3. keep-alive RPC
class _Server:
    def __init__(self, handler_cls):
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
        self.srv.daemon_threads = True
        self.t = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.t.start()
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}/rpc?api-key=K"

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


def _handler(state):
    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def setup(self):
            super().setup()
            state["conns"] += 1

        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            state["bodies"].append((self.path, self.headers.get("Content-Type"), self.headers.get("Accept-Encoding"), body))
            n = len(state["bodies"])
            if state.get("status", {}).get(n):
                self.send_response(state["status"][n])
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            payload = json.dumps(state.get("reply", {"jsonrpc": "2.0", "id": 1, "result": 42})).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            if state.get("close_after") == n:
                self.send_header("Connection", "close")
                self.close_connection = True
            self.end_headers()
            self.wfile.write(payload)

    return H


def test_keepalive_reuses_one_connection_and_counts_each_call():
    st = {"conns": 0, "bodies": []}
    srv = _Server(_handler(st))
    try:
        ka = ftf.KeepAliveRpc(srv.url)
        budget, limiter = CreditBudget(cap=100, per_call=1), RateLimiter(1000.0)
        for _ in range(5):
            res = ka.call("getSlot", [{"commitment": "confirmed"}], limiter, 5.0, budget)
            assert res[0] == 42 and res[2] is None
        assert st["conns"] == 1 and ka.connects == 1 and ka.reused == 4
        assert budget.used == 5
        path, ctype, enc, body = st["bodies"][0]
        assert path == "/rpc?api-key=K" and ctype == "application/json" and enc == "gzip"
        assert body == json.dumps({"jsonrpc": "2.0", "id": 1, "method": "getSlot", "params": [{"commitment": "confirmed"}]}).encode()
    finally:
        srv.close()


def test_keepalive_connection_per_thread_and_server_close():
    st = {"conns": 0, "bodies": [], "close_after": 2}
    srv = _Server(_handler(st))
    try:
        ka = ftf.KeepAliveRpc(srv.url)
        budget = CreditBudget(cap=100, per_call=1)
        for _ in range(4):  # the server announces close after call 2; call 3 opens a fresh connection
            assert ka.call("getSlot", [], None, 5.0, budget)[0] == 42
        assert st["conns"] == 2 and budget.used == 4
        got = []
        t = threading.Thread(target=lambda: got.append(ka.call("getSlot", [], None, 5.0, budget)[0]))
        t.start()
        t.join()
        assert got == [42] and st["conns"] == 3
    finally:
        srv.close()


def test_keepalive_silently_closed_idle_connection_is_resent_once():
    st = {"conns": 0, "bodies": []}
    srv = _Server(_handler(st))
    try:
        ka = ftf.KeepAliveRpc(srv.url)
        budget = CreditBudget(cap=100, per_call=1)
        assert ka.call("getSlot", [], None, 5.0, budget)[0] == 42
        ka._tls.conn.sock.shutdown(socket.SHUT_RDWR)  # the peer/NAT dropped it: the next send hits a dead socket
        assert ka.call("getSlot", [], None, 5.0, budget)[0] == 42
        assert st["conns"] == 2
        assert budget.used == 3  # the resend reserves its own credit: every request sent is counted
    finally:
        srv.close()


def test_keepalive_errors_like_rpc_call(monkeypatch):
    monkeypatch.setattr(ftf.time, "sleep", lambda s: None)
    st = {"conns": 0, "bodies": [], "status": {1: 429, 2: 503}}
    srv = _Server(_handler(st))
    try:
        ka = ftf.KeepAliveRpc(srv.url)
        budget = CreditBudget(cap=100, per_call=1)
        assert ka.call("getSlot", [], None, 5.0, budget)[0] == 42  # 429 then 503 retried, third answers
        assert budget.used == 3
        st["status"] = {n: 404 for n in range(4, 10)}
        with pytest.raises(RuntimeError, match="getSlot http 404"):
            ka.call("getSlot", [], None, 5.0, budget)
        st["status"] = {}
        st["reply"] = {"jsonrpc": "2.0", "id": 1, "error": {"code": -32007, "message": "x"}}
        res = ka.call("getBlock", [], None, 5.0, budget)
        assert res[0] is None and res[2] == -32007
        st["reply"] = {"jsonrpc": "2.0", "id": 1, "error": {"code": -32000, "message": "boom"}}
        with pytest.raises(RuntimeError, match="getSlot -32000 boom"):
            ka.call("getSlot", [], None, 5.0, budget)
        small = CreditBudget(cap=0, per_call=1)
        with pytest.raises(Exception, match="credit cap reached"):
            ka.call("getSlot", [], None, 5.0, small)
    finally:
        srv.close()
    dead = ftf.KeepAliveRpc("http://127.0.0.1:1/x?api-key=NOTAREALKEY")
    with pytest.raises(RuntimeError) as ei:
        dead.call("getSlot", [], None, 1.0, None)
    assert "NOTAREALKEY" not in str(ei.value) and "transport" in str(ei.value)
