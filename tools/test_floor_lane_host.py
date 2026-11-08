"""FLOOR lane decision host: decision equivalence with the arena paper host, gap refusal, checkpoint / restart, row format accepted by FloorBridge, hash chain, pins.
Synthetic fixtures only (tools/test_arena_rule_l1.py's pool rows); no real tape, ledger, registry or key is opened. Nothing here says the rule makes money."""

from __future__ import annotations

import collections
import hashlib
import json
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tools import arena_live_bridge as B
from tools import arena_rules as R_
from tools.arena_pool_table import PoolTable
from tools import arena_rule_l1 as L
from tools import floor_arena_executor as F
from tools import floor_lane_host as H
from tools.arena_declog import DecisionLog, LogChainError
from tools.arena_rules import HostConfig, HostError, register_rules
from tools.arena_rules_testkit import FakeEngine, write_tape
from tools.arena_rules_v2 import HostV2
from tools.arena_state import ArenaState
from tools.test_arena_rule_l1 import H0, HOUR, SOLX, _first_print_rules, pool_rows, row
from tools.test_floor_arena_executor import Clock, _host_log_scenarios, bridge, cfg_dict, feed
import tools.test_arena_rule_l1 as T

SHA40 = H.FROZEN_SHA
BINDING = {"git_sha": SHA40, "dirty": False, "test": 1}
REPO = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("MAL_ARENA_TEST", "1")
    monkeypatch.setattr("tools.arena_paper.shutil.disk_usage", lambda path: collections.namedtuple("Usage", "total used free")(1000, 500, 500))
    monkeypatch.setattr(T, "SHA", SHA40)


def gap_row(frm, to, prog="pumpswap", kind="slot_jump", loss=True):
    return {"v": 1, "type": "gap", "prog": prog, "slot": 0, "t_recv_ms": to, "signature": None, "event_index": None, "kind": kind, "data_loss": loss, "from_ms": frm, "to_ms": to}


def lane_host(tmp, rows, *, live=False, engine=None, now_fn=None, **kw):
    tape = tmp / "tape"
    if not tape.exists():
        write_tape(tape, rows)
    reg = tmp / "bots.json"
    if not reg.exists():
        register_rules(_first_print_rules(), str(reg), SHA40, H0, now_ms=H0 - 1, verify_sha=False)
    return H.make_lane_host(root=str(tmp / "rules"), ledger_root=str(tmp / "ledger"), registry=str(reg), tape_dir=str(tape), clock_start_ms=H0, live=live,
                            binding=BINDING, start_ms=0, table_mode="first_print", engine=engine or FakeEngine(tmp / "rules"), now_fn=now_fn, **kw)


def plain_host(tmp, rows):
    """The arena paper host (HostV2, no guard) over the same rows, as tools/test_floor_arena_executor.host_with_start builds it."""
    tape = tmp / "tape"
    write_tape(tape, rows)
    rules = _first_print_rules()
    reg = tmp / "bots.json"
    register_rules(rules, str(reg), SHA40, H0, now_ms=H0 - 1, verify_sha=False)
    cfg = HostConfig(root=str(tmp / "rules"), registry=str(reg), frozen_sha=SHA40, clock_start_ms=H0)
    return HostV2(cfg, rules, ArenaState(tape, start_ms=0), FakeEngine(tmp / "rules"), binding=BINDING)


def kinds(root):
    return [r["kind"] for r in DecisionLog(str(root)).rows(L.BOT_ID)]


# ------------------------------------------------------------------------------------------------ pins
def test_pins_hold_on_this_checkout():
    got = H.verify_pins()
    assert set(got) == set(H.PINS)


def test_pin_mismatch_refuses(tmp_path):
    (tmp_path / "tools").mkdir()
    for rel in H.PINS:
        (tmp_path / rel).write_text("x")
    with pytest.raises(HostError, match="pin mismatch"):
        H.verify_pins(tmp_path)
    with pytest.raises(HostError, match="unreadable"):
        H.verify_pins(tmp_path / "nowhere")


def test_identical_pins_equal_the_frozen_blobs_when_the_commit_is_present():
    try:
        subprocess.run(["git", "-C", str(REPO), "cat-file", "-e", H.FROZEN_SHA + "^{commit}"], check=True, capture_output=True, timeout=60)
    except (subprocess.SubprocessError, OSError):
        pytest.skip("frozen commit not in this clone")
    for rel in H.IDENTICAL_AT_FROZEN:
        blob = subprocess.run(["git", "-C", str(REPO), "show", f"{H.FROZEN_SHA}:{rel}"], check=True, capture_output=True, timeout=60).stdout
        assert hashlib.sha256(blob).hexdigest() == H.PINS[rel], rel


def test_frozen_sha_is_the_executors_pin_and_the_rule_is_not_edited():
    assert H.FROZEN_SHA == F.FROZEN_SHA and H.BOT_ID == F.BOT_ID == "l1-floor-v2"
    assert H.PINS["tools/arena_rule_l1.py"] == hashlib.sha256((REPO / "tools" / "arena_rule_l1.py").read_bytes()).hexdigest()


def test_lookback_is_bounded():
    for bad in (3600, 25 * 3600):
        with pytest.raises(HostError):
            H.build_rules(None, bad, [None])


def test_config_refuses_arena_output_paths_and_other_sha(tmp_path):
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"root": "/data/mal/arena/rules", "tape_dir": "x", "clock_start_ms": 1}))
    with pytest.raises(HostError, match="not the G1"):
        H._load_cfg(str(p))
    p.write_text(json.dumps({"root": str(tmp_path / "r"), "tape_dir": "x", "clock_start_ms": 1, "frozen_sha": "0" * 40}))
    with pytest.raises(HostError, match="frozen_sha"):
        H._load_cfg(str(p))
    p.write_text(json.dumps({"root": str(tmp_path / "r"), "tape_dir": "x", "clock_start_ms": 1}))
    d = H._load_cfg(str(p))
    assert d["registry"].startswith(str(tmp_path)) and d["ledger_root"].startswith(str(tmp_path))


# ------------------------------------------------------------------------------------------------ equivalence with the arena paper host
def test_decisions_equal_the_paper_host_md5_on_a_fixture(tmp_path):
    rows = _host_log_scenarios(tmp_path)
    a, b = tmp_path / "paper", tmp_path / "lane"
    pa = plain_host(a, rows)
    pa.resume()
    pa.run(H0, H0 + 60 * HOUR)
    lh = lane_host(b, rows)
    lh.resume()
    lh.run(H0, H0 + 60 * HOUR)
    la = DecisionLog(str(a / "rules")).rows(L.BOT_ID)
    lb = DecisionLog(str(b / "rules")).rows(L.BOT_ID)
    ent, ex = [r for r in lb if r["kind"] == "entry"], [r for r in lb if r["kind"] == "exit"]
    assert len(ent) == 3 and sorted(r["reason"] for r in ex) == sorted([L.REASON_TP, L.REASON_TIME, L.REASON_TIME])  # non-vacuous
    assert H.decision_digest(a / "rules") == H.decision_digest(b / "rules")
    assert DecisionLog(str(a / "rules")).md5() == DecisionLog(str(b / "rules")).md5()  # the whole files, start row and chain included
    assert la == lb and not [r for r in lb if r["kind"] == H.KIND_GAP_REFUSED]


# ------------------------------------------------------------------------------------------------ gaps
def test_gap_inside_the_lookback_refuses_the_entry_and_says_why(tmp_path):
    ctl = lane_host(tmp_path / "ctl", pool_rows())
    ctl.run(H0, H0 + 34 * HOUR)
    assert kinds(tmp_path / "ctl" / "rules").count("entry") == 1
    trig = H0 + 30 * HOUR + 900_000
    rows = sorted(pool_rows() + [gap_row(trig - 2 * HOUR, trig - 2 * HOUR + 30_000)], key=lambda r: r["t_recv_ms"])
    h = lane_host(tmp_path / "gap", rows)
    h.run(H0, H0 + 34 * HOUR)
    log = DecisionLog(str(tmp_path / "gap" / "rules")).rows(L.BOT_ID)
    assert "entry" not in [r["kind"] for r in log] and not h.books[L.BOT_ID].open
    ref = [r for r in log if r["kind"] == H.KIND_GAP_REFUSED]
    assert len(ref) == 1 and h.gap_refusals == 1 and h.counters["entries_refused_gap"] == 1
    r = ref[0]
    assert (r["reason"], r["rule_sha"], r["pool"], r["mint"], r["trigger_ms"]) == ("feed_gap", SHA40, "P", "MP", trig)
    assert r["trigger_slot"] == 1000 + (trig - H0) // 400 and r["gap"]["prog"] == "pumpswap" and r["gap"]["kind"] == "slot_jump"
    DecisionLog(str(tmp_path / "gap" / "rules")).verify(L.BOT_ID)  # the chain holds with the refusal row in it


def test_gap_older_than_the_lookback_or_on_another_program_does_not_refuse(tmp_path):
    trig = H0 + 30 * HOUR + 900_000
    old = gap_row(trig - 7 * HOUR, trig - 7 * HOUR + 1000)
    other = gap_row(trig - HOUR, trig - HOUR + 1000, prog="pumpfun")
    soft = gap_row(trig - HOUR, trig - HOUR + 1000, loss=False, kind="socket_reconnect")
    rows = sorted(pool_rows() + [old, other, soft], key=lambda r: r["t_recv_ms"])
    h = lane_host(tmp_path, rows)
    h.run(H0, H0 + 34 * HOUR)
    assert kinds(tmp_path / "rules").count("entry") == 1 and h.gap_refusals == 0


def test_a_process_restart_gap_on_all_programs_refuses_and_a_longer_lookback_sees_older_gaps(tmp_path):
    trig = H0 + 30 * HOUR + 900_000
    rows = sorted(pool_rows() + [gap_row(trig - 10 * HOUR, trig - 10 * HOUR + 1000, prog="all", kind="process_restart")], key=lambda r: r["t_recv_ms"])
    h6 = lane_host(tmp_path / "six", rows)
    h6.run(H0, H0 + 34 * HOUR)
    assert h6.gap_refusals == 0
    h24 = lane_host(tmp_path / "day", rows, lookback_s=24 * 3600)
    h24.run(H0, H0 + 34 * HOUR)
    assert h24.gap_refusals == 1 and "entry" not in kinds(tmp_path / "day" / "rules")


def test_the_bridge_accepts_the_stream_and_ignores_the_refusal_row(tmp_path):
    trig = H0 + 30 * HOUR + 900_000
    rows = sorted(pool_rows() + [gap_row(trig - 2 * HOUR, trig - 2 * HOUR + 1000)], key=lambda r: r["t_recv_ms"])
    h = lane_host(tmp_path, rows)
    h.resume()
    h.run(H0, H0 + 34 * HOUR)
    clk = Clock()
    br, port = bridge(tmp_path, clk)
    got = feed(br, clk)
    assert [r["kind"] for r in got] == ["start", H.KIND_GAP_REFUSED] and not port.would_send and not br.state.latches


# ------------------------------------------------------------------------------------------------ row format and chain, through FloorBridge
@pytest.fixture(scope="module")
def scenario_dir(tmp_path_factory):
    """One lane-host run over the three-pool fixture (slow: a 60 h tick run), copied per test that reads or mutates it."""
    mp = pytest.MonkeyPatch()
    mp.setenv("MAL_ARENA_TEST", "1")
    mp.setattr("tools.arena_paper.shutil.disk_usage", lambda path: collections.namedtuple("Usage", "total used free")(1000, 500, 500))
    mp.setattr(T, "SHA", SHA40)
    d = tmp_path_factory.mktemp("scenario")
    h = lane_host(d, _host_log_scenarios(d))
    h.resume()
    h.run(H0, H0 + 60 * HOUR)
    mp.undo()
    return d


@pytest.fixture
def scen(scenario_dir, tmp_path):
    dst = tmp_path / "s"
    shutil.copytree(scenario_dir, dst)
    return dst


def test_entry_and_exit_rows_are_accepted_by_floorbridge_handle_entry(scen):
    tmp_path = scen
    log = DecisionLog(str(tmp_path / "rules")).rows(L.BOT_ID)
    assert log[0]["kind"] == "start" and log[0]["frozen_sha"] == F.FROZEN_SHA
    ent = [r for r in log if r["kind"] == "entry"]
    for r in ent:  # the fields the bridge pins (floor_arena_executor.entry_refusal)
        m = r["meta"]
        assert r["stake_sol"] == F.PAPER_STAKE_SOL and (m["rule"], m["cell"], m["hold_s"], m["tp"]) == (L.BOT_ID, "l1", L.HOLD_S, L.TP)
        assert m["mint"] and r["pool"] and m["signal_slot"] and m["signal_t_recv_ms"] and r["decision_slot"]
    # the pure replay: every decision becomes exactly one order, none refused, no latch
    res = F.replay_equivalence(cfg_dict(tmp_path))
    assert res["equal"] and res["orders"] == 6 and res["decisions"] == 6 and not res["refused"] and not res["latches"], res
    # and the live path: handle_entry on the first entry row (age < 5 s, caps on) is accepted and goes to a would-send buy
    clk = Clock()
    br, port = bridge(tmp_path, clk, state_dir=str(tmp_path / "state2"))
    first = next(r for r in br.read_new_rows() if r["kind"] == "start")
    clk.t = first["ms"] + 50
    br.handle_row(first)
    e = ent[0]
    e = {**e, "ms": 0}
    clk.t = 50
    br.handle_entry(e)
    o = br.state.orders[e["order_id"]]
    assert o["status"] != "refused" and not br.state.latches, o


def test_hash_chain_detects_tampering_and_a_torn_line(scen):
    tmp_path = scen
    root = str(tmp_path / "rules")
    head, seq = DecisionLog(root).verify(L.BOT_ID)
    assert seq >= 6 and len(head) == 64
    assert H.main(["verify", "--config", str(_write_cfg(tmp_path))]) == 0
    f = next(x for x in sorted((tmp_path / "rules" / "decisions" / L.BOT_ID).glob("*.jsonl")) if '"stake_sol":0.25' in x.read_text())
    data = f.read_text()
    f.write_text(data.replace('"stake_sol":0.25', '"stake_sol":0.30', 1))
    with pytest.raises(LogChainError):
        DecisionLog(root).verify(L.BOT_ID)
    f.write_text(data + '{"seq":')
    with pytest.raises(LogChainError, match="torn"):
        DecisionLog(root).verify(L.BOT_ID)


def _write_cfg(tmp):
    p = tmp / "cfg.json"
    p.write_text(json.dumps({"root": str(tmp / "rules"), "tape_dir": str(tmp / "tape"), "clock_start_ms": H0}))
    return p


# ------------------------------------------------------------------------------------------------ checkpoint, restart, resume
def q_rows(drop_at):
    """Pool Q as pool_rows' P (prints every 30 min from 4 h to 29 h) with the drained-pool sell at `drop_at`."""
    rows = [r for r in pool_rows(with_drop=False, p="Q")]
    rows.append(row(drop_at, "Q", "sell", q=600_000_000, sol=200_000_000))
    return sorted(rows, key=lambda r: r["t_recv_ms"])


def _live_phase(host, t_from, t_to, step_ms=60_000):
    """Drive a live host by hand: a fake wall clock that is T + 100 ms at each step (coarse steps: the stale-row limit is lifted)."""
    host.cfg.stale_row_ms = 10 ** 9
    host.catch_up(t_from)
    T = t_from
    while T < t_to:
        host.clock[0] = T + 100
        host.step(T)
        T += step_ms
    return T - step_ms


class Clocked:
    def __init__(self, host):
        host.clock = [0]
        host.now_fn = lambda: host.clock[0]


def test_restart_resumes_from_the_checkpoint_and_the_downtime_is_a_gap(tmp_path):
    drop = H0 + 33 * HOUR + 1_800_000
    rows = q_rows(drop)
    # control: no restart, the drop triggers an entry
    c = lane_host(tmp_path / "ctl", rows, live=True)
    Clocked(c)
    c.resume()
    _live_phase(c, H0, H0 + 34 * HOUR)
    assert kinds(tmp_path / "ctl" / "rules").count("entry") == 1
    # phase A runs to 31 h, "crashes"; phase B restarts at 33 h (2 h down) on the same root, the same engine ledger, the same table checkpoint
    eng = FakeEngine(tmp_path / "rules")
    a = lane_host(tmp_path, rows, live=True, engine=eng)
    Clocked(a)
    a.resume()
    last = _live_phase(a, H0, H0 + 31 * HOUR)
    a.save_table()
    ck = json.loads((tmp_path / "rules" / "lane" / "checkpoint.json").read_text())
    assert ck["schema"] == H.CKPT_SCHEMA and ck["hwm_ms"] == last and ck["frozen_sha"] == SHA40 and ck["log_seq"] == 0
    b = lane_host(tmp_path, rows, live=True, engine=eng)
    Clocked(b)
    info = b.lane_start(H0 + 33 * HOUR)
    assert info["checkpoint"] and info["hwm_ms"] == last and info["down_ms"] == H0 + 33 * HOUR - last
    assert b.lane_gaps[-1]["kind"] == "host_down" and (b.lane_gaps[-1]["from_ms"], b.lane_gaps[-1]["to_ms"]) == (last, H0 + 33 * HOUR)
    res = b.resume()
    assert res["open"] == 0 and res["reopened"] == 0
    _live_phase(b, H0 + 33 * HOUR, H0 + 34 * HOUR)
    log = DecisionLog(str(tmp_path / "rules")).rows(L.BOT_ID)
    ks = [r["kind"] for r in log]
    assert ks.count("start") == 2 and "entry" not in ks and ks.count(H.KIND_GAP_REFUSED) == 1  # the trigger's lookback holds the host's own downtime
    assert [r for r in log if r["kind"] == H.KIND_GAP_REFUSED][0]["gap"]["kind"] == "host_down"
    DecisionLog(str(tmp_path / "rules")).verify(L.BOT_ID)


def test_a_clean_restart_far_from_the_trigger_still_enters(tmp_path):
    drop = H0 + 58 * HOUR  # the downtime ends at 33 h; the trigger is more than 24 h + 1.9 s later, and no pool is blocked
    rows = q_rows(drop)
    t = H0 + 40 * HOUR
    while t < drop - 600_000:
        rows.append(row(t, "Q", "buy" if (t // 1800000) % 2 else "sell"))
        t += 1_800_000
    rows.sort(key=lambda r: r["t_recv_ms"])
    eng = FakeEngine(tmp_path / "rules")
    a = lane_host(tmp_path, rows, live=True, engine=eng)
    Clocked(a)
    a.resume()
    _live_phase(a, H0, H0 + 31 * HOUR)
    b = lane_host(tmp_path, rows, live=True, engine=eng)
    Clocked(b)
    b.lane_start(H0 + 33 * HOUR)
    b.resume()
    _live_phase(b, H0 + 33 * HOUR, H0 + 59 * HOUR)
    ks = kinds(tmp_path / "rules")
    assert ks.count("entry") == 1 and H.KIND_GAP_REFUSED not in ks


def test_downtime_refuses_entries_for_the_paper_positions_longest_life(tmp_path):
    """A paper entry on any pool during the downtime is unseen: the host_down span refuses every pool until 24 h + 1.9 s after it ends."""
    drop = H0 + 56 * HOUR  # 23 h after the downtime ended at 33 h
    rows = q_rows(drop)
    t = H0 + 40 * HOUR
    while t < drop - 600_000:
        rows.append(row(t, "Q", "buy" if (t // 1800000) % 2 else "sell"))
        t += 1_800_000
    rows.sort(key=lambda r: r["t_recv_ms"])
    eng = FakeEngine(tmp_path / "rules")
    a = lane_host(tmp_path, rows, live=True, engine=eng)
    Clocked(a)
    a.resume()
    _live_phase(a, H0, H0 + 31 * HOUR)
    b = lane_host(tmp_path, rows, live=True, engine=eng)
    Clocked(b)
    b.lane_start(H0 + 33 * HOUR)
    b.resume()
    _live_phase(b, H0 + 33 * HOUR, H0 + 57 * HOUR)
    ks = kinds(tmp_path / "rules")
    assert "entry" not in ks and ks.count(H.KIND_GAP_REFUSED) == 1



def test_downtime_block_covers_paper_cooldown_window(tmp_path):
    """QP 6714966199: a trigger 30 s after downtime end + 24 h + 1.9 s (inside the paper rule's 60 s cooldown after a time exit) is still refused."""
    drop = H0 + 33 * HOUR + H.L_HOLD_MS + 30_000
    rows = q_rows(drop)
    t = H0 + 40 * HOUR
    while t < drop - 600_000:
        rows.append(row(t, "Q", "buy" if (t // 1800000) % 2 else "sell"))
        t += 1_800_000
    rows.sort(key=lambda r: r["t_recv_ms"])
    eng = FakeEngine(tmp_path / "rules")
    a = lane_host(tmp_path, rows, live=True, engine=eng)
    Clocked(a)
    a.resume()
    _live_phase(a, H0, H0 + 31 * HOUR)
    b = lane_host(tmp_path, rows, live=True, engine=eng)
    Clocked(b)
    b.lane_start(H0 + 33 * HOUR)
    b.resume()
    _live_phase(b, H0 + 33 * HOUR, H0 + 58 * HOUR)
    ks = kinds(tmp_path / "rules")
    assert "entry" not in ks and ks.count(H.KIND_GAP_REFUSED) == 1


def test_downtime_lasts_until_first_live_tick(tmp_path):
    """Reviewer 6715031678 item 2: lane_start at 33 h but the first live tick at 34 h; a trigger 30 s after 34 h + 24 h + 1.9 s is still refused."""
    drop = H0 + 34 * HOUR + H.L_HOLD_MS + 30_000
    rows = q_rows(drop)
    t = H0 + 40 * HOUR
    while t < drop - 600_000:
        rows.append(row(t, "Q", "buy" if (t // 1800000) % 2 else "sell"))
        t += 1_800_000
    rows.sort(key=lambda r: r["t_recv_ms"])
    eng = FakeEngine(tmp_path / "rules")
    a = lane_host(tmp_path, rows, live=True, engine=eng)
    Clocked(a)
    a.resume()
    _live_phase(a, H0, H0 + 31 * HOUR)
    b = lane_host(tmp_path, rows, live=True, engine=eng)
    Clocked(b)
    b.lane_start(H0 + 33 * HOUR)
    b.resume()
    b.begin_live(H0 + 34 * HOUR)
    assert b.lane_gaps[-1]["kind"] == "host_down" and b.lane_gaps[-1]["to_ms"] == H0 + 34 * HOUR
    _live_phase(b, H0 + 34 * HOUR, H0 + 59 * HOUR)
    ks = kinds(tmp_path / "rules")
    assert "entry" not in ks and ks.count(H.KIND_GAP_REFUSED) == 1

def two_trigger_rows():
    """Pool Q: the rule's drained-pool sell at 30 h 15 min, normal prints from 31 h, a second drained-pool sell at 38 h."""
    rows = q_rows(H0 + 30 * HOUR + 900_000)
    t = H0 + 31 * HOUR
    while t < H0 + 38 * HOUR - 600_000:
        rows.append(row(t, "Q", "buy" if (t // 1800000) % 2 else "sell"))
        t += 1_800_000
    rows.append(row(H0 + 38 * HOUR, "Q", "sell", q=600_000_000, sol=200_000_000))
    return sorted(rows, key=lambda r: r["t_recv_ms"])


def test_a_gap_blocked_pool_stays_blocked_until_the_paper_position_would_exit(tmp_path):
    t1 = H0 + 30 * HOUR + 900_000
    ctl = lane_host(tmp_path / "ctl", two_trigger_rows())  # no gap: the paper rule enters on the first trigger and holds the pool through the second
    ctl.run(H0, H0 + 40 * HOUR)
    assert kinds(tmp_path / "ctl" / "rules").count("entry") == 1 and ctl.gap_refusals == 0
    rows = sorted(two_trigger_rows() + [gap_row(t1 - 2 * HOUR, t1 - 2 * HOUR + 30_000)], key=lambda r: r["t_recv_ms"])
    h = lane_host(tmp_path / "gap", rows)  # the gap is 10 h before the second trigger, outside the 6 h lookback: only the pool block can refuse it
    h.run(H0, H0 + 40 * HOUR)
    log = DecisionLog(str(tmp_path / "gap" / "rules")).rows(L.BOT_ID)
    ref = [r for r in log if r["kind"] == H.KIND_GAP_REFUSED]
    assert "entry" not in [r["kind"] for r in log]  # lane entries are a subset of the paper rule's: here none
    assert [r["reason"] for r in ref] == ["feed_gap", "pool_blocked_after_gap"]
    assert ref[1]["blocked_until_ms"] == t1 + H.BLOCK_MS and ref[1]["pool"] == "Q" and ref[1]["trigger_ms"] == H0 + 38 * HOUR
    ck = json.loads((tmp_path / "gap" / "rules" / "lane" / "checkpoint.json").read_text())
    assert ck["blocked"]["Q"] == {"until_ms": H0 + 38 * HOUR + H.BLOCK_MS, "why": "gap"}  # the refused second trigger extended it


def test_a_restart_that_loses_the_paper_view_blocks_the_open_pool_and_persists_it(tmp_path):
    rows = two_trigger_rows()
    a = lane_host(tmp_path, rows, live=True, engine=FakeEngine(tmp_path / "rules"))
    Clocked(a)
    a.resume()
    _live_phase(a, H0, H0 + 31 * HOUR)
    ent = [r for r in DecisionLog(str(tmp_path / "rules")).rows(L.BOT_ID) if r["kind"] == "entry"]
    assert len(ent) == 1
    until = ent[0]["ms"] + H.BLOCK_MS
    # restart with an engine that has forgotten the position (the ArenaEngine restart limitation): resume_dropped, the book forgets Q
    b = lane_host(tmp_path, rows, live=True, engine=FakeEngine(tmp_path / "rules"))
    Clocked(b)
    b.lane_start(H0 + 33 * HOUR)
    res = b.resume()
    assert res["dropped"] == 1 and not b.books[L.BOT_ID].open and b.blocked["Q"] == {"until_ms": until, "why": "restart"}
    ck = json.loads((tmp_path / "rules" / "lane" / "checkpoint.json").read_text())
    assert ck["blocked"]["Q"]["until_ms"] == until  # persisted at once, before the next restart
    _live_phase(b, H0 + 33 * HOUR, H0 + 39 * HOUR)
    ref = [r for r in DecisionLog(str(tmp_path / "rules")).rows(L.BOT_ID) if r["kind"] == H.KIND_GAP_REFUSED]
    assert [r["reason"] for r in ref] == ["pool_blocked_after_restart"] and ref[0]["blocked_until_ms"] == until
    c = lane_host(tmp_path, rows, live=True, engine=FakeEngine(tmp_path / "rules"))  # a second restart still knows
    assert c.lane_start(H0 + 40 * HOUR)["blocked_pools"] == 1 and c.blocked["Q"]["until_ms"] == H0 + 38 * HOUR + H.BLOCK_MS


def test_missing_checkpoint_with_an_existing_log_refuses_to_start(tmp_path):
    a = lane_host(tmp_path, q_rows(H0 + 33 * HOUR), live=True)
    Clocked(a)
    a.resume()  # writes the start row: the log exists
    (tmp_path / "rules" / "lane").mkdir(parents=True, exist_ok=True)
    assert not (tmp_path / "rules" / "lane" / "checkpoint.json").exists()
    with pytest.raises(HostError, match="checkpoint is missing"):
        lane_host(tmp_path, [], live=True).lane_start(H0 + 3 * HOUR)


def test_lane_binding_refuses_a_dirty_or_unknown_checkout_and_records_the_host_sha(tmp_path):
    pins = H.verify_pins()
    for hb in ({"git_sha": "a" * 40, "dirty": True}, {"git_sha": "a" * 40, "dirty": None}, {"git_sha": None, "dirty": False}):
        with pytest.raises(HostError, match="dirty or its state is unknown"):
            H.lane_binding(pins, hb=hb)
    b = H.lane_binding(pins, hb={"git_sha": "a" * 40, "dirty": False})
    assert b["host_dirty"] is False and b["host_head"] == "a" * 40 and b["git_sha"] == SHA40
    assert b["host_sha256"] == hashlib.sha256((REPO / "tools" / "floor_lane_host.py").read_bytes()).hexdigest()
    h = lane_host(tmp_path, [row(H0 + 1000, "X")], live=True)
    h.binding = b
    h.resume()
    start = DecisionLog(str(tmp_path / "rules")).rows(L.BOT_ID)[0]
    assert start["kind"] == "start" and start["binding"]["host_sha256"] == b["host_sha256"]


def test_run_needs_since_hour_on_a_first_start_and_replay_seed_must_reach_back_201_h(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(R_, "head_binding", lambda repo=None: {"git_sha": "a" * 40, "dirty": False})
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"root": str(tmp_path / "r"), "tape_dir": str(tmp_path / "t"), "clock_start_ms": H0}))
    assert H.main(["run", "--config", str(p)]) == 2
    assert "--since-hour is required" in capsys.readouterr().err
    frm = H0 + 300 * HOUR
    ok_hour = datetime.fromtimestamp((frm - 200 * HOUR) / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H")
    assert H.main(["replay", "--config", str(p), "--from-ms", str(frm), "--to-ms", str(frm + HOUR), "--seed-from-hour", ok_hour]) == 2
    assert "at least 201 h before" in capsys.readouterr().err
    with pytest.raises(HostError):
        H.parse_hour("nonsense")


def test_the_table_seed_reads_only_hour_files_from_the_floor_hour(tmp_path, monkeypatch):
    rows = pool_rows()
    write_tape(tmp_path / "tape", rows)
    seen = []
    real = H.os.symlink
    monkeypatch.setattr(H.os, "symlink", lambda src, dst, *a, **k: (seen.append(Path(dst).name), real(src, dst, *a, **k))[1])
    since = H0 + 10 * HOUR
    tab = PoolTable("first_print")
    n = H.rebuild_table_since(tab, str(tmp_path / "tape"), H0 + 100 * HOUR, since)
    allh = sorted(f.name for f in (tmp_path / "tape").iterdir())
    floor = datetime.fromtimestamp(since / 1000, tz=timezone.utc).strftime("tape-%Y-%m-%dT%H")
    assert seen and all(nm[:16] >= floor[:16] for nm in seen) and len(seen) < len(allh) and n > 0
    assert not list(Path(tempfile.gettempdir()).glob("floor-lane-seed-*"))


def test_checkpoint_of_another_book_or_a_shorter_log_refuses(tmp_path):
    a = lane_host(tmp_path, q_rows(H0 + 33 * HOUR), live=True)
    Clocked(a)
    a.resume()
    _live_phase(a, H0, H0 + 2 * HOUR)
    p = tmp_path / "rules" / "lane" / "checkpoint.json"
    ck = json.loads(p.read_text())
    p.write_text(json.dumps({**ck, "bot_id": "other"}))
    with pytest.raises(HostError, match="not this book's"):
        lane_host(tmp_path, [], live=True).lane_start(H0 + 3 * HOUR)
    p.write_text(json.dumps({**ck, "log_seq": ck["log_seq"] + 50}))
    with pytest.raises(HostError, match="behind its checkpoint"):
        lane_host(tmp_path, [], live=True).lane_start(H0 + 3 * HOUR)


def test_no_checkpoint_is_a_first_start(tmp_path):
    h = lane_host(tmp_path, [row(H0 + 1000, "X")], live=True)
    assert h.lane_start(H0 + HOUR) == {"checkpoint": False} and h.lane_gaps == []


# ------------------------------------------------------------------------------------------------ CLI
def test_cli_register_and_replay_refuse_inside_the_arena_root(tmp_path, capsys):
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"root": "/data/mal/arena/lane", "tape_dir": str(tmp_path), "clock_start_ms": H0}))
    assert H.main(["replay", "--config", str(p), "--from-ms", "1", "--to-ms", "2"]) == 2
    assert "not the G1" in capsys.readouterr().err
    p.write_text(json.dumps({"root": str(tmp_path / "r"), "tape_dir": str(tmp_path), "clock_start_ms": H0}))
    assert H.main(["replay", "--config", str(p)]) == 2  # needs --from-ms / --to-ms


def test_every_refused_buy_extends_the_block_so_a_paper_reentry_after_take_profit_stays_covered(tmp_path):
    """Paper: enters at t1, takes profit and (after the cooldown) re-enters at t2, holding to t2 + 24 h. The lane refuses t1 (gap) and t2 (pool block, which extends the block),
    and still refuses t3, which is past t1's block end but inside the second paper position."""
    t1, t2, t3 = H0 + 30 * HOUR + 900_000, H0 + 38 * HOUR, H0 + 55 * HOUR
    assert t3 > t1 + H.BLOCK_MS and t3 < t2 + H.BLOCK_MS
    rows = two_trigger_rows()
    t = H0 + 38 * HOUR + 1_800_000
    while t < t3 - 600_000:
        rows.append(row(t, "Q", "buy" if (t // 1800000) % 2 else "sell"))
        t += 1_800_000
    rows.append(row(t3, "Q", "sell", q=600_000_000, sol=200_000_000))
    rows += [gap_row(t1 - 2 * HOUR, t1 - 2 * HOUR + 30_000)]
    rows.sort(key=lambda r: r["t_recv_ms"])
    h = lane_host(tmp_path / "gap", rows)
    h.run(H0, H0 + 57 * HOUR)
    log = DecisionLog(str(tmp_path / "gap" / "rules")).rows(L.BOT_ID)
    ref = [r for r in log if r["kind"] == H.KIND_GAP_REFUSED]
    assert "entry" not in [r["kind"] for r in log]
    assert [r["reason"] for r in ref] == ["feed_gap", "pool_blocked_after_gap", "pool_blocked_after_gap"]
    assert ref[1]["blocked_until_ms"] == t1 + H.BLOCK_MS and ref[2]["blocked_until_ms"] == t2 + H.BLOCK_MS and ref[2]["trigger_ms"] == t3


def test_block_covers_the_time_exit_and_the_sixty_second_cooldown(tmp_path):
    """The paper position exits by time at decision + 24 h + 1.9 s and the rule allows a re-entry 60 s after that exit decision. The block must outlast both, a tick and the margin."""
    assert H.BLOCK_MS == L.HOLD_MS + L.COOLDOWN_MS + 1_000 + 5_000 and L.COOLDOWN_MS == 60_000
    h = lane_host(tmp_path, [row(H0 + 1000, "X")])
    t1 = H0 + 30 * HOUR
    h.blocked["Q"] = {"until_ms": t1 + H.BLOCK_MS, "why": "gap"}
    exit_decision = t1 + L.HOLD_MS
    for t in (exit_decision, exit_decision + 30_000, exit_decision + L.COOLDOWN_MS, exit_decision + L.COOLDOWN_MS + 5_000):
        assert h.judge(t, "Q") is not None, t - exit_decision  # the paper rule's earliest re-entry is exit_decision + 60 s
    assert h.judge(t1 + H.BLOCK_MS, "Q") is None and h.judge(t1 + H.BLOCK_MS - 1, "Q") is not None
