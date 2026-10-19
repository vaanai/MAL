"""ArenaEngine.trim_history(): the trim removes only prints no later pricing call can read, never drops a pool or its scalar state, keeps the duplicate
check, and leaves every ledger row byte-identical to an engine that never trims (DEC-031 section 1.4: tools/arena_paper.py is pinned)."""
from __future__ import annotations

import collections
import os
import random

import pytest

import tools.arena_paper as AP
from tools.arena_paper import ArenaEngine, ArenaRefused, StackConfig, TEST_ENV, register_bot as _register_bot

os.environ[TEST_ENV] = "1"
V = 17_584_505_288
T0 = 1_789_000_000_000
BOT = "botA"
NEW = ("hist_trimmed_prints", "rows_below_trim_floor", "refused_history_trimmed")
CFG = StackConfig(feed_delay_slots=2)


@pytest.fixture(autouse=True)
def _plenty_of_disk(monkeypatch):
    monkeypatch.setattr("tools.arena_paper.shutil.disk_usage", lambda path: collections.namedtuple("Usage", "total used free")(1000, 500, 500))


@pytest.fixture(autouse=True)
def _restore_keep():
    saved = AP.TRIM_KEEP_SLOTS
    yield
    AP.TRIM_KEEP_SLOTS = saved  # the tests shrink the keep window to run short tapes


def tms(slot):
    return T0 + slot * 400  # 0.4 s per slot


def mk(tmp, name):
    reg = tmp / f"{name}-bots.json"
    _register_bot(BOT, "abc1234", 0, reg, deciding_stake=0.25, model_ids=[], now_ms=0, verify_sha=False)
    eng = ArenaEngine(tmp / f"{name}-ledger", reg, CFG, sps_fn=lambda: 0.4, code_state_fn=lambda: ("abc1234", False), now_fn=lambda: 0)
    eng.now_fn = lambda: eng.hw_ms or 0
    eng.engine_id = "0" * 16
    return eng


def row(i, slot, pool="P1", venue="pumpswap", v=V, side=None, sig=None, t=None, sol=None):
    side = side or ("buy" if i % 3 else "sell")
    return {"type": "trade", "prog": "pumpswap" if venue == "pumpswap" else "pumpfun", "venue": venue, "pool": pool if venue == "pumpswap" else None, "mint": "M" + pool,
            "side": side, "sol_lamports": sol if sol is not None else 1_000_000_000 + (i % 7) * 100_000_000, "token_raw": 1e11,
            "quote_reserve": 60e9 + ((i * 7919) % 1000) * 1e7, "base_reserve": 1e15 - i * 1e11, "slot": slot, "tx_index": None, "event_index": i, "event_ts": None,
            "signature": sig or f"sig{pool}{i}", "t_recv_ms": tms(slot) if t is None else t, "virtual_quote_reserves": v if venue == "pumpswap" else None}


class Pair:
    """Engine A never trims; engine B calls trim_history() whenever asked. Both see the same rows and the same orders."""

    def __init__(self, tmp, keep=200):
        AP.TRIM_KEEP_SLOTS = keep
        self.a, self.b = mk(tmp, "A"), mk(tmp, "B")
        self.open: list[list] = []  # [due slot, pid-by-engine {name: pid}, pool]
        self.outcomes = {"A": [], "B": []}
        self.t_event = None  # hw_ms just before the first row below a floor or the first history_trimmed refusal: from there on the engines may differ

    def _events(self):
        return self.b.counters.get("rows_below_trim_floor", 0) + self.b.counters.get("refused_history_trimmed", 0)

    def _mark(self, pre_ms, n0):
        if self.t_event is None and self._events() > n0:
            self.t_event = pre_ms or 0

    def feed(self, r):
        pre, n0 = self.b.hw_ms, self._events()
        for e in (self.a, self.b):
            e.on_row(r)
        self._mark(pre, n0)

    def buy(self, pool, slot):
        pids = {}
        pre, n0 = self.b.hw_ms, self._events()
        for nm, e in (("A", self.a), ("B", self.b)):
            try:
                pids[nm] = e.buy(BOT, pool, 0.25, int(e.hw_ms), int(slot), {})
                self.outcomes[nm].append(("buy", pool, "ok"))
            except ArenaRefused as exc:
                self.outcomes[nm].append(("buy", pool, str(exc).split(":")[0]))
        self._mark(pre, n0)
        return pids

    def ledgers_before_event(self):
        """Both ledgers' rows written before the first below-floor row or history_trimmed refusal (all rows, if none occurred)."""
        out = []
        for e in (self.a, self.b):
            rows = e.ledger.read_bot(BOT)
            assert all("written_ms" in r for r in rows)
            out.append([r for r in rows if self.t_event is None or r["written_ms"] < self.t_event])
        return out

    def sell(self, pids, slot):
        for nm, e in (("A", self.a), ("B", self.b)):
            if nm in pids:
                try:
                    e.sell(BOT, pids[nm], 1.0, int(e.hw_ms), int(slot), "t")
                    self.outcomes[nm].append(("sell", pids[nm], "ok"))
                except ArenaRefused as exc:
                    self.outcomes[nm].append(("sell", pids[nm], str(exc).split(":")[0]))

    def finish(self):
        self.a.flush()
        self.b.flush()

    def ledger(self, e):
        return e.ledger.read_bot(BOT)

    def assert_equivalent(self, min_trades=1):
        self.finish()
        la, lb = self.ledger(self.a), self.ledger(self.b)
        assert la == lb
        assert sum(1 for r in la if r["kind"] == "trade") >= min_trades  # not vacuous
        assert self.outcomes["A"] == self.outcomes["B"]
        ca = {k: v for k, v in self.a.counters.items() if k not in NEW}
        cb = {k: v for k, v in self.b.counters.items() if k not in NEW}
        assert ca == cb
        assert "refused_history_trimmed" not in self.b.counters
        assert set(self.a.pools) == set(self.b.pools)  # the trim never drops a pool
        for p, ha in self.a.pools.items():
            hb = self.b.pools[p]
            assert (hb.mint, hb.venue, hb.last_v_row) == (ha.mint, ha.venue, ha.last_v_row)  # nor its scalar state
            off = len(ha.keys) - len(hb.keys)
            assert off >= 0
            for f in ("keys", "slot", "isbuy", "sol", "tok", "q", "b", "v", "vsrc"):  # every print B holds is A's print: (slot, v, vsrc, ...) equal
                assert getattr(hb, f) == getattr(ha, f)[off:]
            assert all(hb.slot[i] >= (hb.floor or 0) for i in range(1, len(hb.slot)))  # anchor + everything from the floor on


def make_tape(seed, n_slots=2500, venues=("pumpswap", "pumpswap", "pumpswap", "bonding", "bonding"), dup_back=60):
    """Rows of five pools with long silences, V out of band / missing, duplicates of old prints and slot jitter (up to 30 slots, the engine's own order tolerance)."""
    rng = random.Random(seed)
    pools = [f"P{i}" for i in range(len(venues))]
    rows, recent, i = [], [], 0
    for slot in range(100, 100 + n_slots):
        phase = (slot - 100) // 500
        live = [[0, 1, 2, 3], [0, 1], [0, 2, 4], [1, 3, 4], [0, 1, 2, 3, 4]][phase % 5]
        for _ in range(rng.choice((0, 1, 1, 2))):
            k = rng.choice(live)
            vv = V if rng.random() < 0.85 else rng.choice((-5_000_000_000, 40_000_000_000, None, V + 1_000_000))
            s = max(100, slot - (rng.randint(0, 30) if rng.random() < 0.05 else 0))
            r = row(i, s, pools[k], venues[k], v=vv, t=tms(slot) + rng.randint(0, 300))
            rows.append(r)
            recent.append((slot, r))
            i += 1
        if recent and rng.random() < 0.03:  # a late duplicate of an older print (same signature, event index and slot), as the real feed emits
            s0, r0 = rng.choice(recent[-dup_back:])  # at most about dup_back / 1.2 slots old
            rows.append(dict(r0, t_recv_ms=tms(slot) + rng.randint(0, 300)))
    return rows


def drive(pair, seed, trim_from=0, after_jitter=False, dup_back=60):
    """Feed make_tape(seed) with random buys (decision_slot = the row's slot) and fixed-hold sells; trim every 60 slots from slot `trim_from`. after_jitter: buy
    (p = 0.8) right after a row that arrived behind the highest slot, the moment slot_at lags (the regime the guard exists for)."""
    rng = random.Random(1000 + seed)
    pending: list[tuple[int, dict]] = []
    next_trim = trim_from
    for r in make_tape(seed, dup_back=dup_back):
        lag = pair.a.hw_slot - r["slot"]
        pair.feed(r)
        pool = r["pool"] or r["mint"]
        hw = pair.a.hw_slot
        for item in [x for x in pending if x[0] <= hw]:
            pending.remove(item)
            pair.sell(item[1], hw)
        if (rng.random() < 0.04 or (after_jitter and lag > 5 and rng.random() < 0.8)) and pool in pair.a.pools:
            pids = pair.buy(pool, r["slot"])
            if pids:
                pending.append((hw + rng.randint(40, 160), pids))
        if hw >= next_trim:
            pair.b.trim_history()
            next_trim = hw + 60


@pytest.mark.parametrize("seed", range(6))
def test_ledger_and_every_held_print_equal_untrimmed_on_a_random_tape(tmp_path, seed):
    pair = Pair(tmp_path, keep=200)
    drive(pair, seed)
    pair.assert_equivalent(min_trades=5)
    assert pair.b.counters.get("hist_trimmed_prints", 0) > 0  # the trim did work
    assert sum(len(h.slot) for h in pair.b.pools.values()) < sum(len(h.slot) for h in pair.a.pools.values())
    assert pair.t_event is None and "rows_below_trim_floor" not in pair.b.counters  # this tape never reached the floor: full equality above is the whole claim


def test_random_tapes_that_reach_the_floor_count_it_and_are_equal_up_to_the_first_event(tmp_path):
    """keep = 8 slots against 30 slots of jitter and late duplicates: rows fall below the floor and slot_at lags the floor. Equal wherever no such event has happened;
    the counters are non-zero when it did; the trimmed engine's extra rows_dup are exactly its floor drops."""
    refused = 0
    for seed in range(4):
        d = tmp_path / f"s{seed}"
        d.mkdir()
        pair = Pair(d, keep=8)
        drive(pair, seed, trim_from=1000, after_jitter=True, dup_back=300)
        pair.finish()
        b, a = pair.b.counters, pair.a.counters
        assert b.get("rows_below_trim_floor", 0) > 0 and pair.t_event is not None and pair.t_event >= tms(1000)
        la, lb = pair.ledgers_before_event()
        assert la == lb and sum(1 for r in la if r["kind"] == "trade") >= 3
        assert 0 <= b["rows_dup"] - a.get("rows_dup", 0) <= b["rows_below_trim_floor"]
        refused += b.get("refused_history_trimmed", 0)
        assert b.get("refused_history_trimmed", 0) == sum(1 for r in pair.ledger(pair.b) if r["kind"] == "refused" and "history_trimmed" in r["detail"])
    assert refused > 0  # the guard fired somewhere on these tapes


@pytest.mark.parametrize("venue", ["pumpswap", "bonding"])
def test_a_revived_pool_with_out_of_band_v_prices_identically_with_v_src(tmp_path, venue):
    """Trim, revive the quiet pool with an out-of-band V print (pool_prev must carry the last in-band V), then buy and sell: the priced rows, v_src included, are equal."""
    pair = Pair(tmp_path, keep=200)
    i = 0
    for s in range(100, 160):  # P1 trades with V in band; P9 keeps the clock moving
        pair.feed(row(i, s, "P1", venue))
        i += 1
    for s in range(160, 1500, 4):
        pair.feed(row(i, s, "P9", "pumpswap"))
        i += 1
        if s % 100 == 0:
            pair.b.trim_history()
    key = "P1" if venue == "pumpswap" else "MP1"
    assert len(pair.b.pools[key].slot) == 1 and pair.b.pools[key].floor is not None  # cut back to its anchor
    assert len(pair.a.pools[key].slot) == 60
    for s in range(1500, 1560):  # revived with V out of band, then in band again only at the end
        pair.feed(row(i, s, "P1", venue, v=-3_000_000_000 if s < 1550 else V))
        i += 1
        if s == 1505:
            pids = pair.buy(key, s)
    pair.feed(row(i, 1700, "P9", "pumpswap"))
    pair.sell(pids, 1700)
    for s in range(1701, 1760):
        pair.feed(row(i + s, s, "P1", venue))
    pair.assert_equivalent()
    trades = [r for r in pair.ledger(pair.b) if r["kind"] == "trade"]
    assert trades
    if venue == "pumpswap":
        assert trades[0]["v_src"]["entry"] == "pool_prev" and trades[0]["v_src"]["span"].get("pool_prev", 0) > 0
        assert pair.b.pools[key].vsrc[:1] == pair.a.pools[key].vsrc[-len(pair.b.pools[key].vsrc):][:1]


def test_guard_reference_survives_when_every_print_since_the_trim_is_in_the_newest_slot(tmp_path):
    """The pool's only recent prints are a burst in the newest slot: the last print BEFORE that slot (the anchor) is still the guard spot."""
    for venue in ("pumpswap", "bonding"):
        pair = Pair(tmp_path / venue if (tmp_path / venue).mkdir() is None else tmp_path, keep=200)
        i = 0
        for s in range(100, 140):
            pair.feed(row(i, s, "P1", venue, sol=2_000_000_000))
            i += 1
        for s in range(140, 2000, 3):
            pair.feed(row(i, s, "P9", "pumpswap"))
            i += 1
        pair.b.trim_history()
        key = "P1" if venue == "pumpswap" else "MP1"
        assert len(pair.b.pools[key].slot) == 1
        for _ in range(4):  # the burst, all in slot 2000 = the newest slot
            pair.feed(row(i, 2000, "P1", venue, t=tms(2000) + i))
            i += 1
        pids = pair.buy(key, 2000)
        for s in range(2003, 2400, 3):  # no slot jump above 150: that would be a feed gap and exclude the trade
            pair.feed(row(i, s, "P9", "pumpswap"))
            i += 1
            if s == 2201:
                pair.sell(pids, 2201)
        pair.assert_equivalent()
        t = [r for r in pair.ledger(pair.b) if r["kind"] == "trade"][0]
        assert t["calc"]["1p3"]["spot"] is not None and t["calc"]["1p3"]["spot"] > 0  # a guard reference exists (bonding: None would switch the guard off)


def test_a_pool_with_a_position_keeps_every_print_and_is_trimmed_once_priced(tmp_path):
    pair = Pair(tmp_path, keep=100)
    for i, s in enumerate(range(100, 400)):
        pair.feed(row(i, s, "P1"))
    pids = pair.buy("P1", 399)
    n_before = len(pair.b.pools["P1"].slot)
    for i, s in enumerate(range(400, 900, 5)):
        pair.feed(row(1000 + i, s, "P9"))
    assert pair.b.trim_history() == 0 or "P1" not in {p for p, h in pair.b.pools.items() if h.floor is not None}
    assert len(pair.b.pools["P1"].slot) == n_before and pair.b.pools["P1"].floor is None  # pinned by its position
    pair.sell(pids, 900)
    pair.feed(row(2000, 1000, "P9"))
    pair.feed(row(2001, 1200, "P1"))
    pair.b.trim_history()
    assert pair.b.positions == {} and pair.b.pools["P1"].floor is not None and len(pair.b.pools["P1"].slot) < n_before
    pair.assert_equivalent()


def test_a_repeated_old_print_never_counts_again_and_never_moves_the_clock(tmp_path):
    pair = Pair(tmp_path, keep=100)
    old = row(1, 120, "P1", sig="dupme")
    pair.feed(old)
    for i, s in enumerate(range(130, 1000, 2)):
        pair.feed(row(10 + i, s, "P9"))
        if s % 50 == 0:
            pair.b.trim_history()
    pair.feed(row(2, 140, "P1", sig="other"))
    for i, s in enumerate(range(1000, 1400, 2)):
        pair.feed(row(900 + i, s, "P1" if i % 5 == 0 else "P9"))
        pair.b.trim_history()
    hw_ms = (pair.a.hw_ms, pair.b.hw_ms, pair.a.hw_slot)
    pair.feed(dict(old, t_recv_ms=pair.a.hw_ms + 5_000))  # the same print again, 900 slots later and received later
    assert (pair.a.hw_ms, pair.b.hw_ms) != (None, None) and pair.a.hw_ms == pair.b.hw_ms and pair.a.hw_ms == hw_ms[0]
    assert pair.a.counters["rows_dup"] == pair.b.counters["rows_dup"] == 1
    assert pair.b.counters["rows_below_trim_floor"] == 1  # the trimmed engine told it by the floor, not by the key
    assert len(pair.a.pools["P1"].slot) == len(pair.b.pools["P1"].slot) + (len(pair.a.pools["P1"].slot) - len(pair.b.pools["P1"].slot))
    pair.assert_equivalent(min_trades=0)


def test_a_buy_that_would_read_below_the_trim_floor_is_a_counted_refusal_not_a_misprice(tmp_path):
    """The one regime outside the bound: the slot clock of the newest-received row sits behind the pool's floor. Never reached on the 9 h tape."""
    AP.TRIM_KEEP_SLOTS = 100
    e = mk(tmp_path, "B")
    for i, s in enumerate(range(100, 160)):
        e.on_row(row(i, s, "P1"))
    for i, s in enumerate(range(160, 1000, 4)):
        e.on_row(row(500 + i, s, "P9"))
    e.trim_history()
    floor = e.pools["P1"].floor
    assert floor is not None and floor > 800
    e.on_row(row(9000, 700, "P8", t=e.hw_ms + 1000))  # newest-received row (a new pool), slot 700: sd would sit ~300 slots behind the highest slot
    with pytest.raises(ArenaRefused, match="history_trimmed"):
        e.buy(BOT, "P1", 0.25, e.hw_ms, 700, {})
    assert e.counters["refused_history_trimmed"] == 1 and e.counters["refused_no_prints"] == 1
    r = [x for x in e.ledger.read_bot(BOT) if x["kind"] == "refused"]
    assert r and r[0]["reason"] == "no_prints" and "history_trimmed" in r[0]["detail"]


def test_prune_without_the_trim_is_unchanged(tmp_path):
    AP.TRIM_KEEP_SLOTS = 100
    e = mk(tmp_path, "A")
    for i, s in enumerate(range(100, 130)):
        e.on_row(row(i, s, "P1"))
    for i, s in enumerate(range(130, 30_000, 7)):
        e.on_row(row(100 + i, s, "P9"))
    assert e.prune() == 1 and "P1" not in e.pools  # the old rule: a pool quiet for HIST_KEEP_SLOTS is dropped whole
    assert all(h.floor is None for h in e.pools.values()) and "hist_trimmed_prints" not in e.counters


def test_history_is_capped_over_a_long_run_and_the_untrimmed_engine_grows(tmp_path):
    pair = Pair(tmp_path, keep=300)
    i = 0
    for s in range(100, 40_100):
        for k in range(20):
            if (s + k) % 2 == 0:
                pair.feed(row(i, s, f"Q{k}", t=tms(s) + k))
                i += 1
        if s % 250 == 0:
            pair.b.trim_history()
    held_b = sum(len(h.slot) for h in pair.b.pools.values())
    held_a = sum(len(h.slot) for h in pair.a.pools.values())
    per_pool_cap = (300 + 250 + 1) // 2 + 3  # a print every 2 slots: the window, one trim interval, the anchor
    assert held_b <= 20 * per_pool_cap and held_a > 50 * held_b
    assert all(len(h.seen) <= len(h.slot) for h in pair.b.pools.values())  # the dedupe keys are bounded with the prints
    pair.assert_equivalent(min_trades=0)


@pytest.mark.parametrize("venue", ["pumpswap", "bonding"])
def test_the_hv4_calling_pattern_opens_on_a_trimmed_pool_exactly_as_on_an_untrimmed_engine(tmp_path, venue):
    """HV4 and arena_rules pass the highest trade slot seen as decision_slot: sd ~ hw_slot, far above the floor, even while slot_at lags (a stale newest-received
    row). The buy opens on both engines and the ledgers are byte-equal. (decision_slot = the stale 700 is the refused case, above.)"""
    pair = Pair(tmp_path, keep=100)
    i = 0
    for s in range(100, 160):
        pair.feed(row(i, s, "P1", venue))
        i += 1
    for s in range(160, 1000, 4):
        pair.feed(row(i, s, "P9", "pumpswap"))
        i += 1
    pair.b.trim_history()
    key = "P1" if venue == "pumpswap" else "MP1"
    floor = pair.b.pools[key].floor
    assert floor is not None and len(pair.b.pools[key].slot) == 1 and pair.a.hw_slot - floor >= 100
    pair.feed(row(900_001, 700, "P8", "pumpswap", t=pair.a.hw_ms + 1000))  # newest-received row: slot_at is now ~700, below the floor
    assert pair.b.slot_at(pair.b.hw_ms, 0.4) < floor and pair.b.hw_slot > floor
    pids = pair.buy(key, pair.b.hw_slot)  # decision_slot = the highest trade slot seen
    assert pair.outcomes["A"] == pair.outcomes["B"] == [("buy", key, "ok")] and set(pids) == {"A", "B"}
    for s in range(1001, 1400, 3):
        pair.feed(row(i, s, "P9", "pumpswap"))
        i += 1
        if s == 1202:
            pair.sell(pids, s)
    pair.assert_equivalent()
    assert "refused_history_trimmed" not in pair.b.counters and pair.t_event is None


def test_a_new_print_below_the_floor_is_dropped_and_counted_and_the_untrimmed_engine_adds_it(tmp_path):
    """The residual, pinned: a genuinely new (non-duplicate) print older than the floor. The trimmed engine drops it and counts rows_below_trim_floor; the untrimmed
    engine adds it, which also moves hw_ms / hw_ms_slot (the clock every bot's slot_at uses). From here the engines are NOT equivalent."""
    pair = Pair(tmp_path, keep=100)
    for i, s in enumerate(range(100, 160)):
        pair.feed(row(i, s, "P1"))
    for i, s in enumerate(range(160, 1000, 4)):
        pair.feed(row(500 + i, s, "P9"))
    pair.b.trim_history()
    floor = pair.b.pools["P1"].floor
    assert floor is not None and pair.a.counters.get("rows_dup", 0) == pair.b.counters.get("rows_dup", 0) == 0
    n_a, n_b = len(pair.a.pools["P1"].slot), len(pair.b.pools["P1"].slot)
    anchor_a, anchor_b = pair.a.hw_ms_slot, pair.b.hw_ms_slot
    assert anchor_a == anchor_b > floor
    pair.feed(row(7000, 130, "P1", sig="new-and-late", t=pair.a.hw_ms + 1000))  # not a repeat of any key, slot 130 << floor, received newest
    assert pair.b.counters["rows_below_trim_floor"] == 1 and pair.b.counters["rows_dup"] == 1  # dropped, counted
    assert pair.a.counters.get("rows_dup", 0) == 0 and "rows_below_trim_floor" not in pair.a.counters
    assert len(pair.a.pools["P1"].slot) == n_a + 1 and len(pair.b.pools["P1"].slot) == n_b  # A holds the print, B does not
    assert pair.a.hw_ms_slot == 130 and pair.b.hw_ms_slot == anchor_b  # A's slot clock moved back, B's did not
    assert pair.t_event is not None


def test_the_bound_is_derived_from_the_engine_constants():
    from tools.h5_shadow import SPS_HI, SPS_LO

    assert AP.TRIM_LOOKBACK_SLOTS == 13 == int(round(AP.F.NEARBY_WINDOW_S / SPS_LO))
    for k in range(1, 400):
        sps = SPS_LO + (SPS_HI - SPS_LO) * k / 401
        assert int(round(AP.F.NEARBY_WINDOW_S / sps)) <= AP.TRIM_LOOKBACK_SLOTS  # the widest back-look of any pricing leg, for every sps the engine accepts
    assert AP.TRIM_KEEP_SLOTS >= 5 * 928 > 5 * AP.TRIM_LOOKBACK_SLOTS  # 5x the largest feed disorder measured on 9 closed hours (928 slots)
    assert AP.TRIM_KEEP_SLOTS < AP.HIST_KEEP_SLOTS and AP.TRIM_KEEP_SLOTS > AP.LATE_KEEP_SLOTS
