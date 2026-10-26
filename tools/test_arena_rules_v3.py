"""P35 PR 5: HostV3 segment hand-off on the arena_rules testkit's synthetic tape (B-T2, B-T3, B-T4). No network, no key, no real arena tape.

B-T2 (md5 decision equivalence): one continuous replay vs segment 1 to HO + segment_end, then NEW engine / state / host objects resume and run to
the end. The test covers 0 and 1 open positions at HO, with D = 0. Decision rows (minus the hand-off rows and chain fields) and ledger rows must be
identical, with no exclusion.
B-T3 (down interval): D = 4.9 s live_lag (identical); D = 5.1 s, D = 30 s and D = 4.9 s with a slow dispatch (stale switch) take the probe. Exactly
the positions whose exit fell in D are booked handoff_down, an entry in D is a counted miss, and everything else is identical.
B-T4: a second segment cannot take the book lock.
The tape is synthetic and flat: nothing here says any rule makes money.
"""
from __future__ import annotations

import collections
import hashlib
import json
from pathlib import Path

import pytest

from tools import arena_rules as R
from tools.arena_paper_ckpt import EngineCkpt
from tools.arena_rule_h2ps import make_rule
from tools.arena_rules_testkit import CLOCK, SHA, SHA40, T0, TICK_START, at, scenario_rows, write_tape
from tools.arena_rules_v3 import HostV3
from tools.arena_state_ckpt import StateCkpt

BOT = "h2ps-a1"
T_END = T0 + 28 * 60_000
HANDOFF_KINDS = {"segment_end", "segment_resume", "handoff_down_exit", "handoff_down_missed", "handoff_stale_switch"}


@pytest.fixture(scope="module", autouse=True)
def _arena_test_env():
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("MAL_ARENA_TEST", "1")
        yield


@pytest.fixture(autouse=True)
def _plenty_of_disk(monkeypatch):
    monkeypatch.setattr("tools.arena_paper.shutil.disk_usage", lambda path: collections.namedtuple("Usage", "total used free")(1000, 500, 500))


def rule():
    r = make_rule()
    r.table_mode = "first_print"  # HostV2 hosts rules that declare a pool-table mode; h2ps never reads the table
    return r


ROWS = scenario_rows(make_rule())


def host(tmp: Path, live: bool = False, **kw) -> HostV3:
    tape = tmp / "tape"
    if not tape.exists():
        write_tape(tape, ROWS)
    reg = tmp / "bots.json"
    rules = [rule()]
    if not reg.exists():
        R.register_rules(rules, str(reg), SHA, CLOCK, now_ms=T0 - 1, verify_sha=False)
    state = StateCkpt(tape, start_ms=0)
    engine = EngineCkpt(tmp / "ledger", reg, cfg=R.stack_config(7), sps_fn=lambda: 0.4, code_state_fn=lambda: (SHA40, False))
    cfg = R.HostConfig(root=str(tmp / "rules"), registry=str(reg), frozen_sha=SHA, clock_start_ms=CLOCK, live=live)
    return HostV3(cfg, rules, state, engine, **kw)


def whole(tmp: Path) -> HostV3:
    h = host(tmp)
    h.resume()
    h.run(TICK_START, T_END)
    h.close()
    return h


def split(tmp: Path, ho: int, **kw) -> tuple[HostV3, HostV3]:
    a = host(tmp)
    a.resume()
    a.run(TICK_START, T_END, segment_end_ms=ho)
    b = host(tmp, **kw)
    b.resume()
    b.run(ho, T_END)
    b.close()
    return a, b


def decisions(h: HostV3) -> list[dict]:
    return [{k: v for k, v in r.items() if k not in ("seq", "prev", "sha")} for r in h.log.rows(BOT) if r["kind"] not in HANDOFF_KINDS]


def md5(rows) -> str:
    return hashlib.md5("\n".join(json.dumps(r, sort_keys=True) for r in rows).encode()).hexdigest()


def ledger(h: HostV3) -> list[dict]:
    return [{k: v for k, v in r.items() if k not in ("engine_id", "pid")} for r in h.engine.ledger.read_bot(BOT) if r.get("kind") != "start"]


def by_pid(rows):
    out = collections.defaultdict(list)
    for r in rows:
        out[r.get("position_id") or r.get("order_id")].append(r)
    return out


@pytest.fixture(scope="module")
def reference(tmp_path_factory):
    h = whole(tmp_path_factory.mktemp("whole"))
    kinds = collections.Counter(r["kind"] for r in decisions(h))
    led = collections.Counter(r["kind"] for r in ledger(h))
    assert kinds["entry"] == 2 and kinds["exit"] == 2 and led["trade"] == 6 and led["excluded"] == 0  # the replay is not vacuous
    return h


# ---- B-T2 --------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("ho,n_open", [(T0 + 240_000, 0), (T0 + 390_000, 1), (T0 + 900_000, 1)])
def test_state_carrying_handoff_is_md5_equivalent(tmp_path, reference, ho, n_open):
    a, b = split(tmp_path, ho)
    seg = [r for r in b.log.rows(BOT) if r["kind"] == "segment_end"]
    assert len(seg) == 1 and seg[0]["engine"]["positions"] == n_open
    res = [r for r in b.log.rows(BOT) if r["kind"] == "segment_resume"][0]
    assert res["path"] == "live_lag" and res["d_ms"] == 0 and res["engine_digest"] == seg[0]["engine"]["state_digest"]
    assert md5(decisions(b)) == md5(decisions(reference))
    assert ledger(b) == ledger(reference)  # byte-equal rows: the open position is priced, not engine_restart
    b.log.verify(BOT)


def test_a_crash_without_segment_end_is_still_engine_restart(tmp_path, reference):
    a = host(tmp_path)
    a.resume()
    a.run(TICK_START, T0 + 390_000)
    a.close()  # killed: no segment_end
    b = host(tmp_path)
    b.resume()
    b.run(T0 + 390_000, T_END)
    ex = [r for r in ledger(b) if r["kind"] == "excluded"]
    assert len(ex) == 3 and {r["exclusion_reason"] for r in ex} == {"engine_restart"}


# ---- B-T3 --------------------------------------------------------------------------------------------------------
SELL_A = at("sell_A")  # the followed wallet's sell of the position opened at buy_A
HO_SELL = (SELL_A // 1000) * 1000 - 2_000  # on the tick grid, 2 s before the trigger


def a_pid(h):
    return next(r["order_id"] for r in h.log.rows(BOT) if r["kind"] == "entry" and r["pool"].startswith("PoolA"))


def test_d_4900_is_live_lag_and_identical(tmp_path, reference):
    _, b = split(tmp_path, HO_SELL, resume_ms=HO_SELL + 4_900)
    res = [r for r in b.log.rows(BOT) if r["kind"] == "segment_resume"][0]
    assert res["path"] == "live_lag" and res["d_ms"] == 4_900
    assert md5(decisions(b)) == md5(decisions(reference)) and ledger(b) == ledger(reference)


@pytest.mark.parametrize("d_ms,delay", [(5_100, 0), (30_000, 0), (4_900, 6_000)])
def test_exit_in_d_is_handoff_down_and_the_rest_identical(tmp_path, reference, d_ms, delay):
    _, b = split(tmp_path, HO_SELL, resume_ms=HO_SELL + d_ms, dispatch_delay_ms=delay)
    rows = b.log.rows(BOT)
    kinds = collections.Counter(r["kind"] for r in rows)
    if delay:
        assert kinds["handoff_stale_switch"] == 1  # 4.9 s alone would be live_lag; the slow dispatch makes D's first row stale -> probe
        sw = next(r for r in rows if r["kind"] == "handoff_stale_switch")
        first_d_row = min(r["t_recv_ms"] for r in ROWS if HO_SELL <= r["t_recv_ms"] < HO_SELL + d_ms)
        assert sw["at_row_ms"] == first_d_row and sw["rows_dispatched_before"] == 0 and sw["age_ms"] > 10_000  # the switch is at the first stale row
        assert b.d_rows_live == 0
    pid = a_pid(b)
    assert [r["order_id"] for r in rows if r["kind"] == "handoff_down_exit"] == [pid]
    led_b, led_r = by_pid(ledger(b)), by_pid(ledger(reference))
    assert {r["exclusion_reason"] for r in led_b[pid] if r["kind"] == "excluded"} == {"handoff_down"} and len([r for r in led_b[pid] if r["kind"] == "excluded"]) == 3
    assert not [r for r in led_b[pid] if r["kind"] == "trade"]
    for other in set(led_r) - {pid}:  # every other position is identical
        assert led_b[other] == led_r[other]
    summary = [r for r in rows if r["kind"] == "handoff_down_missed"][0]
    assert summary["n_exits"] == 1 and summary["n_missed_entries"] == 0


def test_entry_in_d_is_a_counted_miss(tmp_path, reference):
    t = at("buy_E")
    ho = (t // 1000) * 1000 - 2_000
    _, b = split(tmp_path, ho, resume_ms=ho + 5_100)
    summary = [r for r in b.log.rows(BOT) if r["kind"] == "handoff_down_missed"][0]
    assert summary["n_missed_entries"] == 1 and len(summary["missed_key_sha256"]) == 1
    entries_b = [r for r in decisions(b) if r["kind"] == "entry"]
    entries_r = [r for r in decisions(reference) if r["kind"] == "entry"]
    assert len(entries_b) == len(entries_r) - 1  # the entry in D was never made (no position, not decided late)


def test_time_exit_in_d_on_a_tick_is_handoff_down(tmp_path, reference):
    e_entry = next(r for r in reference.log.rows(BOT) if r["kind"] == "entry" and r["pool"].startswith("PoolE"))
    cap = e_entry["ms"] + 600_000  # the 600 s cap fires on the first tick at or after this
    ho = (cap // 1000) * 1000 - 1_000
    _, b = split(tmp_path, ho, resume_ms=ho + 5_100)
    pid = e_entry["order_id"]
    assert [r["order_id"] for r in b.log.rows(BOT) if r["kind"] == "handoff_down_exit"] == [pid]
    assert {r["exclusion_reason"] for r in by_pid(ledger(b))[pid] if r["kind"] == "excluded"} == {"handoff_down"}


# ---- B-T4 and refusals ---------------------------------------------------------------------------------------------
def test_second_segment_cannot_take_the_book_lock(tmp_path):
    a = host(tmp_path)
    with pytest.raises(R.HostError, match="book lock"):
        host(tmp_path)
    a.close()
    host(tmp_path).close()


def test_tampered_checkpoint_refuses_or_falls_back(tmp_path):
    ho = T0 + 390_000
    a = host(tmp_path)
    a.resume()
    a.run(TICK_START, T_END, segment_end_ms=ho)
    seg = [r for r in a.log.rows(BOT) if r["kind"] == "segment_end"][0]
    p = Path(seg["engine"]["path"])
    p.write_bytes(p.read_bytes().replace(b'"hw_slot":', b'"hw_slot":1', 1))
    b = host(tmp_path)
    with pytest.raises(R.HostError, match="refused"):
        b.resume()
    b.close()
    c = host(tmp_path, allow_fallback=True)
    c.resume()
    assert [r for r in c.log.rows(BOT) if r["kind"] == "segment_resume"][0]["path"] == "fallback_restart"
    c.run(ho, T_END)
    assert {r["exclusion_reason"] for r in ledger(c) if r["kind"] == "excluded"} == {"engine_restart"}


def test_segment_end_off_the_tick_grid_is_refused(tmp_path):
    a = host(tmp_path)
    a.resume()
    with pytest.raises(R.HostError, match="tick grid"):
        a.run(TICK_START, T_END, segment_end_ms=T0 + 390_500)


def test_d_max_above_5_s_is_refused(tmp_path):
    with pytest.raises(R.HostError):
        host(tmp_path, d_max_ms=5_001)


def test_tick_decision_at_exactly_ho_uses_the_carried_host_state(tmp_path, reference):
    """The first tick of segment 2 decides a time exit before any new row arrives: its decision_slot is the carried highest slot (no row reset it)."""
    e_entry = next(r for r in reference.log.rows(BOT) if r["kind"] == "entry" and r["pool"].startswith("PoolE"))
    ref_exit = next(r for r in reference.log.rows(BOT) if r["kind"] == "exit" and r["order_id"] == e_entry["order_id"])
    ho = ref_exit["ms"]  # the tick at which the time cap fired in the continuous run
    assert ho % 1000 == 0 and ref_exit["reason"] != "wallet_sell"
    _, b = split(tmp_path, ho)
    ex = [r for r in decisions(b) if r["kind"] == "exit" and r["order_id"] == e_entry["order_id"]]
    assert ex and ex[0]["ms"] == ho
    assert md5(decisions(b)) == md5(decisions(reference)) and ledger(b) == ledger(reference)


def test_stale_switch_mid_d_never_dispatches_a_stale_row(tmp_path, reference):
    """QP condition 4: the dispatch of D's rows stalls part-way (simulated): rows before the stall are a live lag (<= 10 s old), the first row that
    would be older than 10 s switches the rest of D to the probe, the position whose exit falls after the switch is handoff_down, and the scorer's
    exclusion accounting includes it."""
    ho, d = HO_SELL, 4_900
    stall_from = ho + 1_500  # rows received from here on are dispatched 9 s later than the resume: (ho + 4.9 s + 9 s) - t > 10 s for t < ho + 3.9 s

    def clock(t):
        return ho + d + (9_000 if t >= stall_from else 0)

    _, b = split(tmp_path, ho, resume_ms=ho + d, dispatch_clock=clock)
    rows = b.log.rows(BOT)
    sw = [r for r in rows if r["kind"] == "handoff_stale_switch"]
    d_rows = sorted(r["t_recv_ms"] for r in ROWS if ho <= r["t_recv_ms"] < ho + d)
    first_stale = min(t for t in d_rows if clock(t) - t > 10_000)
    assert len(sw) == 1 and sw[0]["at_row_ms"] == first_stale and sw[0]["rows_dispatched_before"] == sum(1 for t in d_rows if t < first_stale) > 0
    assert 0 < b.d_max_live_age_ms <= 10_000  # no row of D was dispatched live more than stale_row_ms old
    summary = [r for r in rows if r["kind"] == "handoff_down_missed"][0]
    assert summary["d_rows_live"] == sw[0]["rows_dispatched_before"] and summary["d_max_live_age_ms"] <= 10_000
    pid = a_pid(b)
    assert [r["order_id"] for r in rows if r["kind"] == "handoff_down_exit"] == [pid]  # the wallet sell at SELL_A falls after the switch
    # the scorer's exclusion accounting (tools.arena_score._score_bot step 3) counts handoff_down like any exclusion, toward the 5% cap
    from tools import arena_score as S

    led = b.engine.ledger.read_bot(BOT)
    reg = {BOT: {"frozen_sha": SHA, "clock_start_ms": CLOCK, "deciding_stake": 0.25, "model_ids": [], "stopped_at_ms": None, "time_source": "test_override"}}
    import unittest.mock as um

    with um.patch.object(S, "check_bot_rows", lambda *a, **k: {}), um.patch.object(S, "blind_read_instant", lambda *a, **k: (T0 // 86_400_000 + 1) * 86_400_000):
        out = S.score_bot(BOT, reg, led, now_ms=T0 + 10 * 86_400_000)
    ex = out["books"]["0.25"]["excluded"]
    assert ex.get("handoff_down") == 1 and out["books"]["0.25"]["excluded_share"] > 0


def test_live_host_refuses_an_unpinned_d_max(tmp_path):
    with pytest.raises(R.HostError, match="unpinned"):
        host(tmp_path, live=True)


def test_replay_without_d_max_logs_it_unpinned(tmp_path):
    _, b = split(tmp_path, T0 + 390_000)
    res = [r for r in b.log.rows(BOT) if r["kind"] == "segment_resume"][0]
    assert res["d_max_ms"] == 5_000 and res["d_max_pinned"] is False
    _, c = split(tmp_path / "p", T0 + 390_000, d_max_ms=2_000)
    assert [r for r in c.log.rows(BOT) if r["kind"] == "segment_resume"][0]["d_max_pinned"] is True
