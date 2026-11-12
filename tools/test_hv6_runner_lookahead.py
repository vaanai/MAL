"""Fix C, runner side: the lookahead admits between scans and the pre-clock warm-up. Synthetic tape, fake chain and the paced prefetcher with a fake clock;
no RPC, no API, no /data/mal/arena path.

  1. live only: a replay (live False), or a chain without HV6's paced prefetcher, never runs a lookahead or a warm-up; the replay path is byte-identical;
  2. a lookahead admits exactly the filter-pass pools the store does not know, and writes no row, card, cooldown or X cap;
  3. the warm-up hands the prefetcher at :02 exactly the watch set the scan would hand it, polls the state up to the clock, and decides nothing;
  4. a resumed run (clock already passed) does not warm up.
"""

from __future__ import annotations

from tools import hv6_cards as H
from tools import hv6_prefetch as P6
from tools import hv6_runner as R
from tools.test_hv4_runner import T0
from tools.test_hv6_prefetch import Chain, Clock, compose
from tools.test_hv6_runner import _env, make, rows_of  # noqa: F401  (_env is autouse)

MIN = 60_000
T_BOARD = T0 + 15 * MIN + H.SCAN_SECOND_MS          # a :02 board scan inside the synthetic tape's active window
assert H.is_board_second(T_BOARD)


class Recorder:
    """A prefetch stand-in that records watch() and admit() and serves nothing (cards are refused chain_unavailable)."""

    def __init__(self) -> None:
        self.watches, self.admits, self.pools, self.policy = [], [], {}, "v2"

    def watch(self, items, hot, now_ms, vols=None):
        self.watches.append((list(items), set(hot), dict(vols or {})))

    def admit(self, items, now_ms):
        items = list(items)
        self.admits.append(items)
        return len(items)

    def part_at(self, pool, mint, T, slot):
        return None

    def stats(self):
        return {}


def _paced_source(clock):
    chain = Chain(clock)
    return R.ChainSource(chain, P6.PacedPrefetcher(chain, compose, clock=clock, policy="v2", hourly_budget=R.RPC_CALLS_PER_H))


# ------------------------------------------------------------------------------------------------------------------------ 1. live only
def test_lookahead_and_warmup_are_live_only(tmp_path_factory):
    clock = Clock(T0)
    sim = make(tmp_path_factory.mktemp("lsim"), chain=_paced_source(clock))
    assert not sim.lookahead_on() and sim.prewarm(T0 + 60 * MIN) == 0
    live = make(tmp_path_factory.mktemp("llive"), chain=_paced_source(clock), cfg_kw={"live": True}, now_fn=lambda: T0)
    assert live.lookahead_on()
    base = make(tmp_path_factory.mktemp("lbase"), chain=R.ChainSource(Chain(clock), object()), cfg_kw={"live": True}, now_fn=lambda: T0)
    assert not base.lookahead_on()                                              # no admit(): HV4's own class, a test chain


def test_a_replay_never_looks_ahead_even_with_an_admitting_chain(tmp_path_factory):
    rec = Recorder()
    run = make(tmp_path_factory.mktemp("lra"), chain=R.ChainSource(Chain(Clock(T0)), rec))
    run.run(T0 + 9 * MIN + 2000, T0 + 40 * MIN)
    assert rec.watches and not rec.admits                                       # the scans ran; no lookahead, no warm-up
    assert "lookahead_passes" not in run.counters and "prewarm_seconds" not in run.counters


# ------------------------------------------------------------------------------------------------------------------------ 2. lookahead
def test_lookahead_admits_only_new_filter_pass_pools_and_writes_nothing(tmp_path_factory):
    rec = Recorder()
    run = make(tmp_path_factory.mktemp("lla"), chain=R.ChainSource(Chain(Clock(T0)), rec), cfg_kw={"live": True}, now_fn=lambda: T0 + 12 * MIN)
    T = T0 + 12 * MIN + 12_000
    run.poll_to(T)
    limiter_before, xcap_before = dict(run.limiter._last), list(run.xcap.entries_ms)
    n = run.lookahead(T)
    assert n == len(rec.admits[0]) > 0
    admitted = {p for p, _ in rec.admits[0]}
    assert all(m for _, m in rec.admits[0])
    # exactly the scan's candidate set at T (H.scan over the same state, on a scratch cooldown)
    scratch = R.C.SetupLimiter()
    items = ((s, R.C.norm_prints(list(p.events))) for p, s in run._prefiltered(T))
    assert admitted == set(H.scan(items, as_of_ms=T, limiter=scratch, board_on=False).candidates)
    # known pools are not handed over again
    rec.pools = {p: None for p in admitted}
    assert run.lookahead(T) == 0 and rec.admits[-1:] == [rec.admits[0]]
    assert all(not run.log.rows(a) for a in (*R.ARM_ORDER, "SCAN"))
    assert not (run.root / "cards").exists() or not any((run.root / "cards").iterdir())
    assert dict(run.limiter._last) == limiter_before and list(run.xcap.entries_ms) == xcap_before
    assert run.counters["lookahead_passes"] == 2 and run.counters["lookahead_admitted"] == n


def test_tick_runs_the_lookahead_at_its_seconds_only(tmp_path_factory):
    rec = Recorder()
    run = make(tmp_path_factory.mktemp("llt"), chain=R.ChainSource(Chain(Clock(T0)), rec), cfg_kw={"live": True}, now_fn=lambda: T0 + 12 * MIN)
    base = T0 + 12 * MIN
    for s in range(3, 60):                                                      # not the scan second (:02)
        run.tick(base + s * 1000)
    assert run.counters.get("lookahead_passes") == len(R.LOOKAHEAD_SECONDS) == 5
    assert not rec.watches


# ------------------------------------------------------------------------------------------------------------------------ 3. warm-up
def test_warmup_hands_over_the_scans_watch_set_and_decides_nothing(tmp_path_factory):
    rec_w, rec_s = Recorder(), Recorder()
    wall = [T_BOARD - 20 * MIN]
    slept = []

    def sleep(s):
        slept.append(s)
        wall[0] += int(s * 1000)

    warm = make(tmp_path_factory.mktemp("lw"), chain=R.ChainSource(Chain(Clock(T0)), rec_w), cfg_kw={"live": True}, now_fn=lambda: wall[0])
    n = warm.prewarm(T_BOARD + 1000, sleep)                                     # the clock is one second after a board scan instant
    assert n == R.PREWARM_MS // 1000 and warm.counters["prewarm_seconds"] == n
    assert warm.counters["prewarm_watches"] == 15 and warm.counters["lookahead_passes"] == 15 * 5
    assert all(not warm.log.rows(a) for a in (*R.ARM_ORDER, "SCAN"))            # no decision, no SCAN row, no card
    assert warm.state.now_ms <= T_BOARD and warm.state.now_ms > T_BOARD - MIN   # polled up to the clock, as the first tick would
    # the scan at T_BOARD (replay runner, same tape) hands over the same watch set as the warm-up's last :02
    scan = make(tmp_path_factory.mktemp("ls"), chain=R.ChainSource(Chain(Clock(T0)), rec_s))
    scan.poll_to(T_BOARD)
    scan._begin_tick(T_BOARD)
    scan.scan(T_BOARD)
    assert rec_s.watches and rec_w.watches[-1] == rec_s.watches[-1]
    assert rec_s.watches[-1][0] and rec_s.watches[-1][1]                        # non-empty items and hot sets (board on)


def test_a_resumed_run_does_not_warm_up(tmp_path_factory):
    rec = Recorder()
    run = make(tmp_path_factory.mktemp("lres"), chain=R.ChainSource(Chain(Clock(T0)), rec), cfg_kw={"live": True}, now_fn=lambda: T_BOARD)
    assert run.prewarm(T_BOARD - 3000, lambda s: None) == 0 and not rec.watches and not rec.admits
