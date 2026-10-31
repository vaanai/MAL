"""Fix B (smoke #1806, chunk latency p95 41,257 ms): the entry calls of one scan go out at once in live mode. Synthetic tape, fakes and models only; no RPC, no
API, no /data/mal/arena path.

What is proved here:
  1. equivalence: with a deterministic model the concurrent path writes byte-identical decision logs and cards to the serial path (a full synthetic run);
  2. the calls really overlap (every planned chunk x four arms in flight at once) and the wall time is about one call, not the sum;
  3. the call cap plans the scan's earlier chunks exactly as the serial path counts them (entry_call_cap, seq numbers);
  4. a model call that raises on a worker thread is a preflight_exception deny exactly as on the serial path;
  5. sim/replay runs keep the serial code unless the flag asks otherwise; live runs use the concurrent path.
"""

from __future__ import annotations

import json
import threading
import time

from tools import drill_hv6_item6 as D
from tools import hv6_agents as A
from tools import hv6_runner as R
from tools.hv4_model import ModelReply
from tools.hv4_testkit import make_cards
from tools.test_hv4_runner import FakeChain, T0
from tools.test_hv6_runner import _env, make, rows_of  # noqa: F401  (_env is autouse)
from tools.test_hv6_runner_qp918 import FakeEngine, FakeState

MIN = 60_000


def _skips(req):
    return json.dumps([{"id": i, "action": "SKIP", "why": "f.vol_60m low and f.ret_15m flat"} for i in req.ids])


class SlowModel:
    """Sleeps `delay` s per call and records how many calls were in flight at once. Thread-safe and deterministic in its replies."""

    def __init__(self, delay: float, raise_arms: tuple = ()) -> None:
        self.model = A.MODEL_ID
        self.delay, self.raise_arms = delay, set(raise_arms)
        self.lock = threading.Lock()
        self.inflight = self.peak = self.n = 0
        self.seqs: list[tuple[str, int]] = []

    def complete(self, req, now_ms):
        with self.lock:
            self.inflight += 1
            self.n += 1
            self.peak = max(self.peak, self.inflight)
            self.seqs.append((req.arm, req.seq))
        try:
            time.sleep(self.delay)
            if req.arm in self.raise_arms:
                raise RuntimeError("boom")
            return ModelReply(text=_skips(req), stop_reason="end_turn", latency_ms=int(self.delay * 1000))
        finally:
            with self.lock:
                self.inflight -= 1


def _runner(tmp, model, *, live: bool, concurrent):
    cfg = R.RunnerConfig(root=str(tmp / "v6"), frozen_sha="abcdef1234567", live=live, concurrent_calls=concurrent)
    return R.Runner(cfg, FakeState(), FakeEngine(), model, FakeChain(), None, now_fn=lambda: time.time_ns() // 1_000_000)


# ------------------------------------------------------------------------------------------------------------------------ 1. equivalence
def test_concurrent_path_is_byte_identical_to_serial_on_a_full_synthetic_run(tmp_path_factory):
    start, end = T0 + 9 * MIN + 2000, T0 + 70 * MIN
    ser = make(tmp_path_factory.mktemp("cser"), cfg_kw={"concurrent_calls": False})
    ser.run(start, end)
    con = make(tmp_path_factory.mktemp("ccon"), cfg_kw={"concurrent_calls": True})
    con.run(start, end)
    assert con.counters.get("concurrent_calls", 0) > 0 and "concurrent_calls" not in ser.counters  # the concurrent path really ran
    assert sum(len(rows_of(ser, a, "entry")) for a in ("L", "F")) > 0                               # and it decided something
    assert D.log_md5(con.cfg.root) == D.log_md5(ser.cfg.root)
    assert D.cards_md5(con.cfg.root) == D.cards_md5(ser.cfg.root)
    assert {a: len(b.entries_ms) for a, b in con.books.items()} == {a: len(b.entries_ms) for a, b in ser.books.items()}


def test_default_is_serial_in_replay_and_concurrent_live(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("cdef")
    assert R.RunnerConfig().concurrent_calls is None
    assert not _runner(tmp / "a", SlowModel(0), live=False, concurrent=None).concurrent()
    assert _runner(tmp / "b", SlowModel(0), live=True, concurrent=None).concurrent()
    assert not _runner(tmp / "c", SlowModel(0), live=True, concurrent=False).concurrent()


# ------------------------------------------------------------------------------------------------------------------------ 2. the calls overlap
def test_all_chunks_and_arms_are_in_flight_at_once(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("cpar")
    T = time.time_ns() // 1_000_000
    cards = make_cards(12, as_of_ms=T)                                     # 3 chunks of 4
    m = SlowModel(0.3)
    run = _runner(tmp, m, live=True, concurrent=None)
    t = time.perf_counter()
    run._llm_entry_round(cards, T, R.compute_tick_policy(None, T))
    wall = time.perf_counter() - t
    assert m.n == 12 and m.peak == 12                                      # 3 chunks x 4 arms at once
    assert wall < 0.3 * 4                                                  # about one call, not twelve (serial would be >= 3.6 s)
    for a in run.llm_arm_names:
        calls = rows_of(run, a, "llm_call")
        assert [len(r["ids"]) for r in calls] == [4, 4, 4]
        dec = [r for r in run.log.rows(a) if r["kind"] == "skip"]
        assert len(dec) == 12
    # each chunk is decided at ITS common instant: the latest receive instant of its four calls
    for k in range(3):
        recvs = [rows_of(run, a, "llm_call")[k]["ms"] for a in run.llm_arm_names]
        ids = set(rows_of(run, "L", "llm_call")[k]["ids"])
        for a in run.llm_arm_names:
            assert {r["ms"] for r in run.log.rows(a) if r["kind"] == "skip" and r["pool"] in ids} == {max(recvs)}


def test_serial_live_path_is_one_call_at_a_time(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("cserl")
    T = time.time_ns() // 1_000_000
    m = SlowModel(0.05)
    run = _runner(tmp, m, live=True, concurrent=False)
    run._llm_entry_round(make_cards(8, as_of_ms=T), T, R.compute_tick_policy(None, T))
    assert m.n == 8 and m.peak == 1


# ------------------------------------------------------------------------------------------------------------------------ 3. cap and seq planning
def _cap_and_seq(tmp, concurrent: bool, prior_calls: int):
    T = T0 + 30 * MIN + 2000
    m = SlowModel(0)
    run = _runner(tmp, m, live=False, concurrent=concurrent)
    for a in run.llm_arm_names:
        run.calls[(a, "entry")] = [T - 10 * MIN] * prior_calls
        run.seq[(a, R.utc_day(T))] = 5
    run._llm_entry_round(make_cards(12, as_of_ms=T), T, R.compute_tick_policy(None, T))
    return run, m


def test_cap_counts_the_scans_earlier_chunks_like_the_serial_path(tmp_path_factory):
    for prior in (0, 9, 10, 11, 12):
        s_run, s_m = _cap_and_seq(tmp_path_factory.mktemp(f"cs{prior}"), False, prior)
        c_run, c_m = _cap_and_seq(tmp_path_factory.mktemp(f"cc{prior}"), True, prior)
        assert c_m.n == s_m.n == 4 * max(0, min(3, 12 - prior))
        assert c_run.counters.get("entry_call_cap", 0) == s_run.counters.get("entry_call_cap", 0) == 3 - max(0, min(3, 12 - prior))
        assert sorted(c_m.seqs) == sorted(s_m.seqs)                        # the same per-arm sequence numbers (5, 6, 7 ...)
        assert D.log_md5(c_run.cfg.root) == D.log_md5(s_run.cfg.root)


# ------------------------------------------------------------------------------------------------------------------------ 4. a raising call
def test_a_call_that_raises_on_a_worker_thread_is_a_symmetric_deny(tmp_path_factory):
    out = {}
    for conc in (False, True):
        T = T0 + 30 * MIN + 2000
        m = SlowModel(0, raise_arms=("F",))
        run = _runner(tmp_path_factory.mktemp(f"craise{conc}"), m, live=False, concurrent=conc)
        run._llm_entry_round(make_cards(4, as_of_ms=T), T, R.compute_tick_policy(None, T))
        assert run.counters.get("preflight_exception") == 1
        for a in run.llm_arm_names:
            ab = [r for r in run.log.rows(a) if r["kind"] == "symmetry_abstain"]
            assert len(ab) == 1 and ab[0]["errors"]["F"] == "preflight_exception"
            assert not [r for r in run.log.rows(a) if r["kind"] == "skip"]
        out[conc] = D.log_md5(run.cfg.root)
    assert out[True] == out[False]


def test_guarded_stub_calls_hold_and_release_while_concurrent(tmp_path_factory):
    """The real model classes hold their worst case in the guard while a concurrent call runs; nothing is left held afterwards."""
    tmp = tmp_path_factory.mktemp("chold")
    g = A.BudgetGuard(tmp / "g")
    seen = []
    lock = threading.Lock()

    def slow(req):
        with lock:
            seen.append(g.others_inflight_usd())
        time.sleep(0.2)
        return _skips(req)

    m = A.StubModel(slow, guard=g)
    run = R.Runner(R.RunnerConfig(root=str(tmp / "v6"), frozen_sha="abcdef1234567", live=True), FakeState(), FakeEngine(), m, FakeChain(), g,
                   now_fn=lambda: time.time_ns() // 1_000_000)
    T = time.time_ns() // 1_000_000
    run._llm_entry_round(make_cards(4, as_of_ms=T), T, R.compute_tick_policy(None, T))
    assert len(seen) == 4 and max(seen) > 0                                 # at least one call saw the others' holds
    assert g.others_inflight_usd() == 0.0
    assert all(r["error"] is None for a in run.llm_arm_names for r in rows_of(run, a, "llm_call"))
