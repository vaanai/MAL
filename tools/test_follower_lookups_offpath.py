"""Tests for the follower lookup change (claude/follower-lookups-offpath), QP-RULING-1125-follower-fetch-workers Addendum 2.
Synthetic only: mocked RPC with fixed responses, no network, no tape, 0 credits.

  /data/mal/venv/bin/python -m pytest -q -p no:cacheprovider tools/test_follower_lookups_offpath.py

Design (a): the pool lookup and the V lookup of one block run concurrently (always on).
Design (b): --prefetch-lookups, the fetch worker reads both after its getBlock; the main thread applies at commit.

1. md5 of every file the follower writes (tape, creates, gaps.jsonl, checkpoint.json) equals BASE_COMMIT e2a8bb03, at fetch
   workers 1/8/16, flag off and on, 5 seeds, with injected lookup failures. Deterministic mock V.
2. A V that varies with the read instant: only singular `virtual_quote_reserve` may differ, and every first read lies in
   [getBlock completion, commit] (E2).
3. E1: no cache is written off the main thread; the cache state after every commit equals the sequential run (1200 slots,
   16 workers, random latency); a prefetched pool resolved by an earlier block is discarded (E1/E3).
4. Throughput with 0.5 s mocked lookups.
"""

from __future__ import annotations

import hashlib
import json
import random
import subprocess
import threading
import time
import types
from pathlib import Path

import pytest

from observe.trade_decode import WSOL_MINT
from tools import fast_tip_follower as ftf
from tools.test_fast_tip_follower import FIXTURES, _line
from tools.test_follower_mainthread_speedup import FIRST, DelayRpc, digest
from tools.test_tip_follower_inner_migration import plain_tx
from tools.test_tip_migration_gap import HOUR0, SetClock, block, swap_trade

REPO = Path(__file__).resolve().parent.parent
BASE_COMMIT = "e2a8bb03"


def base_module():
    r = subprocess.run(["git", "-C", str(REPO), "show", f"{BASE_COMMIT}:tools/fast_tip_follower.py"],
                       capture_output=True, stdin=subprocess.DEVNULL, timeout=120)
    if r.returncode != 0:
        pytest.skip(f"{BASE_COMMIT} not in this clone")
    mod = types.ModuleType("fast_tip_follower_base_e2a8bb03")
    mod.__file__ = "fast_tip_follower_base2.py"
    exec(compile(r.stdout.decode(), mod.__file__, "exec"), mod.__dict__)
    assert "KeepAliveRpc" in mod.__dict__ and "_VJob" not in mod.__dict__ and "prefetch_lookups" not in r.stdout.decode()
    return mod


# ------------------------------------------------------------------ mocks
def h(p: str) -> int:
    return int(hashlib.md5(p.encode()).hexdigest()[:8], 16)


class Mock:
    """Fixed responses. accounts() is the one getMultipleAccounts: found mints and V of every pool. A call whose chunk
    touches a pool of `fail` raises (kind-agnostic, so a block's calls fail the same in every design). `vary` makes V
    depend on the call sequence number (the read instant); otherwise V is a function of the pool only."""

    def __init__(self, delay=0.0, fail=(), vary=False):
        self.delay, self.fail, self.vary = delay, set(fail), vary
        self.lock = threading.Lock()
        self.calls = 0
        self.seq = 0
        self.reads: list[tuple[str, float, float, int | None]] = []  # pool, t_start, t_end, v

    def accounts(self, pools):
        t0 = time.monotonic()
        with self.lock:
            self.calls += 1
            self.seq += 1
            seq = self.seq
        if self.delay:
            time.sleep(self.delay)
        if self.fail & set(pools):
            raise RuntimeError("injected lookup failure")
        found, vs = {}, {}
        for p in pools:
            found[p] = (f"M{p[:6]}", WSOL_MINT)
            vs[p] = None if h(p) % 5 == 0 else (h(p) % 1000 + 1) * 10**6 + (seq if self.vary else 0)
        t1 = time.monotonic()
        with self.lock:
            self.reads += [(p, t0, t1, vs[p]) for p in pools]
        return found, vs

    def v_lookup(self, pools):
        return self.accounts(pools)[1]

    def make_lookup(self):
        def lookup(pools):
            if not pools:
                return {}
            found, vs = self.accounts(pools)
            lookup.v_seen.update(vs)  # disconnected from the follower's _v_seen exactly as in make_rpc (empty dict is falsy)
            return found

        lookup.v_seen = {}
        lookup.accounts = self.accounts
        return lookup


def template_unresolved():
    blk = {"slot": 5, "blockTime": HOUR0 - 100, "parentSlot": 4,
           "transactions": [{"meta": {"err": None, "logMessages": [_line("pumpswap_sell_event.b64")]},
                             "transaction": {"signatures": ["TPLSIG"]}}]}
    res = ftf.rows_from_block(blk, {}, ftf.FEED)
    assert res["unresolved"], FIXTURES
    return res["unresolved"][0]


TEMPLATE = None


def make_spec(seed: int, n: int, n_pend=12, n_known=10):
    rng = random.Random(seed * 1000 + 17)
    spec: dict[int, tuple[list[str], list[str]]] = {}
    for i in range(n):
        slot = FIRST + i
        pend = [f"PEND{rng.randrange(n_pend):03d}" for _ in range(rng.choice([0, 0, 1, 1, 2]))]
        known = [f"KNOWN{rng.randrange(n_known):03d}" for _ in range(rng.choice([0, 1, 1, 2]))]
        spec[slot] = (list(dict.fromkeys(pend)), list(dict.fromkeys(known)))
    return spec


def install(mod, spec):
    """Wrap mod.rows_from_block: after the real decode, add the spec's PumpSwap rows. A pending pool already in the cache the
    decoder was given is a decoded trade, else an unresolved record: the cache-dependent split the real decoder makes."""
    global TEMPLATE
    TEMPLATE = TEMPLATE or template_unresolved()
    real = mod.rows_from_block

    def wrapped(blk, cache, feed, **kw):
        res = real(blk, cache, feed, **kw)
        slot = blk["slot"]
        pend, known = spec.get(slot, ([], []))
        trades = list(res["trades"])
        unres = list(res["unresolved"])
        for k, p in enumerate(pend):
            if p in cache:
                r = swap_trade(0, slot, k + 20, blk["blockTime"])
                r["pool"] = p
                r["signature"] = f"d{slot}x{k}"
                trades.append(r)
            else:
                rec = json.loads(json.dumps(TEMPLATE))
                rec.update({"pool": p, "signature": f"u{slot}x{k}", "slot": slot})
                unres.append(rec)
        for k, p in enumerate(known):
            r = swap_trade(0, slot, k + 40, blk["blockTime"])
            r["pool"] = p
            r["signature"] = f"k{slot}x{k}"
            trades.append(r)
        res["trades"], res["unresolved"] = trades, unres
        return res

    mod.rows_from_block = wrapped
    return lambda: setattr(mod, "rows_from_block", real)


def make_blocks(n: int, seed: int):
    rng = random.Random(seed)
    out = {}
    for i in range(n):
        slot = FIRST + i
        if i not in (0, n - 1) and rng.random() < 0.08:
            continue
        out[slot] = block(slot, [plain_tx(f"s{slot}")])
    return out


class MainOnly(dict):
    pass


def tracking(mod, log, states, guard_thread=None):
    class T(mod.TipFollower):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            if guard_thread is not None:
                self.pool_cache = guarded(self.pool_cache, guard_thread, "pool_cache")
                self.v_cache = guarded(self.v_cache, guard_thread, "v_cache")
                self._v_retry_at = guarded_dict(self._v_retry_at, guard_thread, "_v_retry_at")
                self._v_fails = guarded_dict(self._v_fails, guard_thread, "_v_fails")

        def _emit(self, slot, *a, **k):
            self._cur_slot = slot
            return super()._emit(slot, *a, **k)

        def _note_v(self, pool, v, now):
            if isinstance(v, int) and pool not in self.v_cache:
                log.append((self._cur_slot, pool, v, time.monotonic()))
            return super()._note_v(pool, v, now)

        def _commit(self, slot, *a, **k):
            r = super()._commit(slot, *a, **k)
            if states is not None:
                states.append((slot, tuple(self.pool_cache.items()), tuple(self.v_cache.items()),
                               tuple(sorted(self._v_fails.items()))))
            return r

    return T


def guarded(cache, thread, name):
    cls = type(cache)

    class G(cls):
        def __setitem__(self, k, v):
            assert threading.current_thread() is thread, f"{name} written off the main thread"
            super().__setitem__(k, v)

    g = G(cache.cap) if hasattr(cache, "cap") else G()
    g.update(cache)
    return g


def guarded_dict(d, thread, name):
    class G(dict):
        def __setitem__(self, k, v):
            assert threading.current_thread() is thread, f"{name} written off the main thread"
            super().__setitem__(k, v)

        def pop(self, *a):
            assert threading.current_thread() is thread, f"{name} popped off the main thread"
            return super().pop(*a)

    g = G()
    g.update(d)
    return g


def run(mod, root: Path, workers: int, seed: int, n=70, flag=None, fail=(), vary=False, delay=0.0, fixed_fetch=None,
        spec=None, log=None, states=None, guard=False, mock_delay=0.0):
    blocks = make_blocks(n, seed)
    spec = spec if spec is not None else make_spec(seed, n)
    mock = Mock(delay=mock_delay, fail=fail, vary=vary)
    rpc = DelayRpc(blocks, seed, n, fixed_fetch)
    done_at: dict[int, float] = {}

    def fetch(slot):
        res = rpc.fetch(slot)
        done_at[slot] = time.monotonic()
        return res

    restore = install(mod, spec)
    try:
        kw = {} if flag is None else {"prefetch_lookups": flag}
        cls = tracking(mod, log if log is not None else [], states, threading.current_thread() if guard else None)
        f = cls(out_dir=root / "tip", creates_dir=root / "creates", state_dir=root / "state", fetch=fetch,
                get_tip=rpc.get_tip, lookup=mock.make_lookup(), v_lookup=mock.v_lookup, fetch_workers=workers,
                clock_ms=SetClock((HOUR0 + 30) * 1000), sleep=lambda s: None, log=lambda m: None, max_retries=1,
                backlog_slots=100_000, **kw)
        f.last_done = FIRST - 1
        f._checkpoint(None)
        t0 = time.monotonic()
        for _ in range(100_000):
            if f.last_done >= FIRST + n - 1:
                break
            f.step()
        elapsed = time.monotonic() - t0
        assert f.last_done == FIRST + n - 1
        f.close_pool()
        f.writer.close()
        f.cwriter.close()
    finally:
        restore()
    f.mock, f.done_at = mock, done_at
    return f, elapsed


def counters(f):
    return {k: getattr(f, k) for k in ("blocks", "gaps", "skipped", "unresolved_dropped", "lookup_failures", "backlog_jumps")}


FAIL_MODES = {
    "none": (),
    "pool_fail": tuple(f"PEND{i:03d}" for i in (1, 4, 7)),
    "v_fail": tuple(f"KNOWN{i:03d}" for i in (2, 5)),
    "both": tuple(f"PEND{i:03d}" for i in (3, 9)) + tuple(f"KNOWN{i:03d}" for i in (0, 7)),
}


# ------------------------------------------------------------------ 1. md5 equivalence with the base
@pytest.mark.parametrize("mode", list(FAIL_MODES))
@pytest.mark.parametrize("workers", [1, 8, 16])
@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
def test_md5_equal_to_base_flag_off_and_on(tmp_path, workers, mode, seed):
    fail = FAIL_MODES[mode]
    base = base_module()
    fb, _ = run(base, tmp_path / "base", workers, seed, fail=fail)
    f0, _ = run(ftf, tmp_path / "off", workers, seed, fail=fail, flag=False)
    f1, _ = run(ftf, tmp_path / "on", workers, seed, fail=fail, flag=True)
    db, d0, d1 = digest(tmp_path / "base"), digest(tmp_path / "off"), digest(tmp_path / "on")
    assert db == d0  # design (a) alone: byte-identical, failures included
    assert "state/checkpoint.json" in db and any(k.startswith("tip/trades-") for k in db)
    assert counters(fb) == counters(f0)
    if mode in ("none", "pool_fail"):
        assert db == d1 and counters(fb) == counters(f1)
    else:
        # The injected V failure is keyed on chunk membership: the base reads a block's V in ONE call with the pools its pool
        # lookup just resolved, so one failing pool nulls the others in that chunk; the prefetch reads the pool call's V and
        # the known-swap V as separate calls. Same failure rule per call, different chunks. Allowed: a row whose V the base
        # lost to that coupling carries the (deterministic) V instead. Nothing else may differ, and no drop or gap is added.
        assert {k: v for k, v in db.items() if "/trades-" not in k} == {k: v for k, v in d1.items() if "/trades-" not in k}
        for name in db:
            if "/trades-" in name:
                ra = [json.loads(x) for x in (tmp_path / "base" / name).read_text().splitlines()]
                rb = [json.loads(x) for x in (tmp_path / "on" / name).read_text().splitlines()]
                assert len(ra) == len(rb)
                for x, y in zip(ra, rb):
                    vx, vy = x.pop("virtual_quote_reserve"), y.pop("virtual_quote_reserve")
                    assert x == y and (vx == vy or (vx is None and vy == (h(y["pool"]) % 1000 + 1) * 10**6))
        cb, c1 = counters(fb), counters(f1)
        assert c1.pop("lookup_failures") <= cb.pop("lookup_failures") and c1 == cb
    assert f0.prefetch_hits == f0.prefetch_failures == 0 and not f0.prefetch_lookups
    if workers > 1 and mode == "none":
        assert f1.prefetch_hits > 0  # the flag really took the prefetch path
        assert f1.mock.calls <= fb.mock.calls + f1.prefetch_discards  # credits: extra calls are only discarded prefetches (E4)
    # (a) alone makes the same calls as base when nothing fails
    if mode == "none":
        assert f0.mock.calls == fb.mock.calls, (f0.mock.calls, fb.mock.calls)


# ------------------------------------------------------------------ 2. time-varying V: E2
@pytest.mark.parametrize("workers", [1, 8, 16])
@pytest.mark.parametrize("flag", [False, True])
def test_only_singular_v_differs_and_reads_lie_in_window(tmp_path, workers, flag):
    base = base_module()
    run(base, tmp_path / "base", workers, 4, vary=True)
    log: list = []
    f, _ = run(ftf, tmp_path / "new", workers, 4, vary=True, flag=flag, log=log)

    def rows(root):
        out = []
        for p in sorted((root / "tip").glob("trades-*.jsonl")):
            out += [json.loads(x) for x in p.read_text().splitlines()]
        return out

    ro, rn = rows(tmp_path / "base"), rows(tmp_path / "new")
    assert len(ro) == len(rn) > 0
    for a, b in zip(ro, rn):
        va, vb = a.pop("virtual_quote_reserve"), b.pop("virtual_quote_reserve")
        assert a == b  # every other key of every row, in order
        _ = va != vb  # a moved singular V is the allowed difference (E2); the window check below bounds it
    # E2 window: each first V applied for a pool was read at or after its block was in hand, and before the commit join
    by_value = {}
    for pool, t0, t1, v in f.mock.reads:
        if isinstance(v, int):
            by_value[(pool, v)] = (t0, t1)
    assert log
    for slot, pool, v, t_join in log:
        t0, t1 = by_value[(pool, v)]
        assert t0 >= f.done_at[slot] - 1e-3, (slot, pool)
        assert t1 <= t_join + 1e-3, (slot, pool)


# ------------------------------------------------------------------ 3. E1 and the races
def test_caches_written_only_on_main_thread_and_state_equals_sequential_every_commit(tmp_path):
    n = 1200
    spec = make_spec(11, n, n_pend=150, n_known=60)
    seq_states: list = []
    run(ftf, tmp_path / "seq", 1, 11, n=n, spec=spec, flag=False, states=seq_states)
    for flag in (False, True):
        states: list = []
        f, _ = run(ftf, tmp_path / f"p{flag}", 16, 11, n=n, spec=spec, flag=flag, states=states, guard=True,
                   fixed_fetch=None)
        assert len(states) == len(seq_states) > 1000
        assert states == seq_states  # pool_cache, v_cache and V retry bookkeeping equal after EVERY commit
        assert digest(tmp_path / "seq") == digest(tmp_path / f"p{flag}")
        if flag:
            assert f.prefetch_hits > 0


def test_prefetched_pool_resolved_by_an_earlier_block_is_discarded(tmp_path):
    """Two blocks both name pool X as pending and both are prefetched before either commits: the second commit finds X
    cached, so its prefetched X is never applied (discarded) and the bytes equal the sequential path."""
    spec = {FIRST: (["PEND001"], []), FIRST + 1: (["PEND001"], ["KNOWN001"]), FIRST + 2: ([], ["KNOWN002"])}
    blocks = {s: block(s, [plain_tx(f"s{s}")]) for s in spec}
    out = {}
    for mode in ("seq", "pf"):
        mock = Mock()
        restore = install(ftf, spec)
        try:
            f = ftf.TipFollower(out_dir=tmp_path / mode / "tip", creates_dir=tmp_path / mode / "creates", state_dir=tmp_path / mode / "state",
                                fetch=lambda s: (json.loads(json.dumps(blocks[s])), None), get_tip=lambda: FIRST + 2,
                                lookup=mock.make_lookup(), v_lookup=mock.v_lookup, fetch_workers=2,
                                clock_ms=SetClock((HOUR0 + 30) * 1000), sleep=lambda s: None, log=lambda m: None,
                                backlog_slots=1000, prefetch_lookups=(mode == "pf"))
            f.last_done = FIRST - 1
            pfs = {s: (f._prefetch(s, json.loads(json.dumps(blocks[s]))) if mode == "pf" else None) for s in sorted(spec, reverse=True)}  # later slot first: both claim X
            if mode == "pf":
                assert "PEND001" in pfs[FIRST].pool_req and "PEND001" in pfs[FIRST + 1].pool_req  # the stale snapshot
            for s in sorted(spec):
                f._commit(s, json.loads(json.dumps(blocks[s])), "ok", pfs[s])
            f.writer.close()
            f.cwriter.close()
        finally:
            restore()
        out[mode] = (digest(tmp_path / mode), f)
    assert out["seq"][0] == out["pf"][0]
    fpf = out["pf"][1]
    assert not fpf._pf_claims
    assert fpf.prefetch_discards >= 1 and fpf.prefetch_hits >= 1 and fpf.prefetch_failures == 0


def test_failed_prefetch_falls_back_without_a_drop_gap_or_failure_count(tmp_path):
    spec = {FIRST: (["PEND001"], ["KNOWN001"]), FIRST + 1: (["PEND002"], [])}
    blocks = {s: block(s, [plain_tx(f"s{s}")]) for s in spec}
    mock = Mock()
    calls = {"n": 0}
    real_acc = mock.accounts

    def flaky(pools):  # only calls from lookup threads fail (the prefetch); the commit's own lookups work
        if threading.current_thread().name.startswith("prefetch"):
            calls["n"] += 1
            raise RuntimeError("boom")
        return real_acc(pools)

    lk = mock.make_lookup()
    lk.accounts = flaky
    restore = install(ftf, spec)
    try:
        f = ftf.TipFollower(out_dir=tmp_path / "tip", creates_dir=tmp_path / "c", state_dir=tmp_path / "s",
                            fetch=lambda s: (json.loads(json.dumps(blocks[s])), None), get_tip=lambda: FIRST + 1, lookup=lk,
                            v_lookup=mock.v_lookup, fetch_workers=2, clock_ms=SetClock((HOUR0 + 30) * 1000),
                            sleep=lambda s: None, log=lambda m: None, backlog_slots=1000, prefetch_lookups=True)
        f.last_done = FIRST - 1
        for s in sorted(spec):
            pf = f._prefetch(s, json.loads(json.dumps(blocks[s])))
            f._commit(s, json.loads(json.dumps(blocks[s])), "ok", pf)
        f.writer.close()
        f.cwriter.close()
    finally:
        restore()
    assert calls["n"] >= 1 and f.prefetch_failures >= 1
    assert f.lookup_failures == 0 and f.unresolved_dropped == 0 and f.gaps == 0
    assert not (tmp_path / "tip" / "gaps.jsonl").exists()
    rows = [json.loads(x) for p in (tmp_path / "tip").glob("trades-*.jsonl") for x in p.read_text().splitlines()]
    assert {r["pool"] for r in rows} == {"PEND001", "KNOWN001", "PEND002"}
    assert all(isinstance(r["virtual_quote_reserve"], int) or h(r["pool"]) % 5 == 0 for r in rows)


def test_pool_overlay_reads_through_without_touching_the_base():
    base = ftf.LRUPools(10)
    base["a"] = ("x", "y")
    base["b"] = ("x", "z")
    before = list(base)
    ov = ftf._PoolOverlay(base)
    assert "a" in ov and ov.get("a") == ("x", "y") and ov["b"] == ("x", "z") and ov.get("q") is None and "q" not in ov
    ov["c"] = ("n", "m")
    assert "c" in ov and "c" not in base and list(base) == before
    with pytest.raises(KeyError):
        ov["nope"]


def test_cli_flag_default_off():
    import inspect

    assert inspect.signature(ftf.TipFollower.__init__).parameters["prefetch_lookups"].default is False
    src = inspect.getsource(ftf.main)
    assert '"--prefetch-lookups", action="store_true"' in src


# ------------------------------------------------------------------ 4. throughput with 0.5 s lookups
def test_throughput_with_half_second_lookups(tmp_path):
    """Every block names one new pool (pool lookup) and one new known swap pool (V lookup): per block the base spends
    0.5 s + 0.5 s on the main thread; (a) about 0.5 s; (a)+(b) takes both off the commit path."""
    n = 10
    spec = {FIRST + i: ([f"PEND{i:03d}"], [f"KNOWN{i:03d}"]) for i in range(n)}
    rates = {}
    for name, mod, flag in (("base", base_module(), None), ("a", ftf, False), ("a+b", ftf, True)):
        f, el = run(mod, tmp_path / name, 8, 1, n=n, spec=spec, flag=flag, mock_delay=0.5, fixed_fetch=0.05)
        rates[name] = (f.blocks / el, el)
    print("blocks_per_s", {k: round(v[0], 3) for k, v in rates.items()}, "elapsed_s", {k: round(v[1], 2) for k, v in rates.items()})
    assert digest(tmp_path / "base") == digest(tmp_path / "a") == digest(tmp_path / "a+b")
    assert rates["a"][0] > 1.5 * rates["base"][0], rates
    assert rates["a+b"][0] > 1.5 * rates["a"][0], rates
