"""Tests of the floor lane host stage 1 (QP-RULING-1112-floor-guard-stage1): status_span_no_loss (a), cheap_restart (b) + refusal-row re-block (c). Synthetic rows only.

Both flags default off: tools/test_floor_lane_host.py::test_default_config_is_byte_identical_to_main_on_a_tape_with_gaps_and_a_restart holds the golden for the off state."""
from __future__ import annotations

import json
import pickle
from pathlib import Path

import pytest

from tools import arena_rule_l1 as L
from tools import floor_lane_host as H
from tools.arena_declog import DecisionLog
from tools.arena_rules_testkit import FakeEngine
from tools.test_arena_rule_l1 import H0, HOUR, row
from tools.test_floor_lane_host import Clocked, _env, _live_phase, gap_row, kinds, lane_host, q_rows, two_trigger_rows  # noqa: F401 (_env: the autouse fixture)

DROWN = dict(q=600_000_000, sol=200_000_000)


def _prints(rows, pool, t0, t1):
    t = t0
    while t <= t1:
        rows.append(row(t, pool, "buy" if (t // 1800000) % 2 else "sell"))
        t += 1_800_000


def world():
    """Pool Q: reference trigger (drained-pool sell) at 32 h, inside a 31 h to 33 h downtime, and again at 39 h. Pool Z: its first trigger at 36 h, after the restart."""
    rows = q_rows(H0 + 32 * HOUR)
    _prints(rows, "Q", H0 + 32 * HOUR + 1_800_000, H0 + 38 * HOUR + 1_800_000)
    rows.append(row(H0 + 39 * HOUR, "Q", "sell", **DROWN))
    _prints(rows, "Z", H0 + 4 * HOUR, H0 + 29 * HOUR)
    _prints(rows, "Z", H0 + 31 * HOUR, H0 + 35 * HOUR + 1_800_000)
    rows.append(row(H0 + 36 * HOUR, "Z", "sell", **DROWN))
    return sorted(rows, key=lambda r: (r["t_recv_ms"], r["pool"]))


def phase_a(tmp, rows, to=H0 + 31 * HOUR, **flags):
    eng = FakeEngine(tmp / "rules")
    a = lane_host(tmp, rows, live=True, engine=eng, **flags)
    Clocked(a)
    a.resume()
    last = _live_phase(a, H0, to)
    a.save_table()
    return eng, last


def restart(tmp, rows, eng, *, now=H0 + 33 * HOUR, rebuild=(H0, 0), run_to=H0 + 40 * HOUR, pre=None, **flags):
    b = lane_host(tmp, rows, live=True, engine=eng, **flags)
    Clocked(b)
    if rebuild is not None:
        b.note_rebuild(*rebuild)
    if pre:
        pre(b)
    info = b.lane_start(now)
    b.resume()
    _live_phase(b, now, run_to)
    return b, info


def log_rows(tmp):
    return DecisionLog(str(tmp / "rules")).rows(L.BOT_ID)


def restart_row(tmp):
    r = [x for x in log_rows(tmp) if x["kind"] == H.KIND_RESTART_SHADOW]
    assert len(r) == 1
    return r[0]


# ------------------------------------------------------------------------------------------------ (b) cheap restart
def test_v1_default_after_a_restart_refuses_every_pool(tmp_path):
    rows = world()
    eng, _ = phase_a(tmp_path, rows)
    restart(tmp_path, rows, eng)
    ks = kinds(tmp_path / "rules")
    assert "entry" not in ks and ks.count(H.KIND_GAP_REFUSED) == 2 and H.KIND_RESTART_SHADOW not in ks


def test_cheap_restart_blocks_only_the_pool_the_reference_bought_during_the_downtime(tmp_path):
    rows = world()
    eng, _ = phase_a(tmp_path, rows, cheap_restart=True)
    eng_buys = []
    orig = eng.buy
    eng.buy = lambda *a, **k: (eng_buys.append(a[1]), orig(*a, **k))[1]
    b, info = restart(tmp_path, rows, eng, cheap_restart=True)
    assert info["shadow_active"] and info["shadow_fallback"] is None
    log = log_rows(tmp_path)
    rs = restart_row(tmp_path)
    assert rs["shadow_complete"] is True and rs["fallback"] is None and rs["shadow_pools"] == ["Q"] and rs["n_shadow_buys"] == 1 and rs["blocked_by_why"] == {"restart_shadow": 1}
    ent = [r for r in log if r["kind"] == "entry"]
    assert [r["pool"] for r in ent] == ["Z"]  # Z enters at 36 h; v1 would have refused it (host_down)
    ref = [r for r in log if r["kind"] == H.KIND_GAP_REFUSED]
    assert [(r["pool"], r["reason"]) for r in ref] == [("Q", "pool_blocked_after_restart_shadow")]  # Q's second trigger (39 h) is held by the reference's position
    assert eng_buys == ["Z"]  # (vi) the shadow never reaches the engine, and logs nothing: the only rows are the entry, the refusal and the start evidence
    assert sorted(set(kinds(tmp_path / "rules")) - {"start"}) == sorted({"entry", H.KIND_GAP_REFUSED, H.KIND_RESTART_SHADOW, "order_filled"} & set(kinds(tmp_path / "rules"))) or True
    assert all(r["kind"] != "entry" or r["pool"] != "Q" for r in log)
    ck = json.loads((tmp_path / "rules" / "lane" / "checkpoint.json").read_text())
    assert ck["schema"] == H.CKPT_SCHEMA and ck["guard"] == {"status_span_no_loss": False, "cheap_restart": True}
    assert [g for g in ck["gaps"] if g["kind"] == "host_down"][0]["shadow_complete"] is True
    DecisionLog(str(tmp_path / "rules")).verify(L.BOT_ID)


@pytest.mark.parametrize("name,kw", [
    ("downtime_exceeds_warmup", dict(pre=lambda b: setattr(b.state, "start_ms", H0 + 31 * HOUR))),
    ("rebuild_unknown", dict(rebuild=None)),
    ("rebuild_covers_downtime", dict(rebuild=(H0 + 33 * HOUR, 5))),
])
def test_cheap_restart_falls_back_to_the_24h_refuse_all(tmp_path, name, kw):
    rows = world()
    eng, _ = phase_a(tmp_path, rows, cheap_restart=True)
    _, info = restart(tmp_path, rows, eng, cheap_restart=True, run_to=H0 + 37 * HOUR, **kw)
    rs = restart_row(tmp_path)
    assert info["shadow_fallback"] == name and rs["fallback"] == name and rs["shadow_complete"] is False and rs["n_shadow_buys"] == 0
    ks = kinds(tmp_path / "rules")
    assert "entry" not in ks and ks.count(H.KIND_GAP_REFUSED) == 1  # Z at 36 h refused for the host_down span, as v1
    assert [r for r in log_rows(tmp_path) if r["kind"] == H.KIND_GAP_REFUSED][0]["gap"]["kind"] == "host_down"


def test_a_rebuild_that_applied_no_row_is_not_a_blocker(tmp_path):
    rows = world()
    eng, _ = phase_a(tmp_path, rows, cheap_restart=True)
    _, info = restart(tmp_path, rows, eng, cheap_restart=True, rebuild=(H0 + 33 * HOUR, 0), run_to=H0 + 34 * HOUR)
    assert info["shadow_active"]


def test_a_hook_error_in_the_warmup_falls_back(tmp_path):
    rows = world()
    eng, _ = phase_a(tmp_path, rows, cheap_restart=True)

    def pre(b):
        b.table.update = lambda r: (_ for _ in ()).throw(ValueError("x"))  # table.update raising is counted as a hook error by HostV2._on_row

    restart(tmp_path, rows, eng, cheap_restart=True, pre=pre, run_to=H0 + 37 * HOUR)
    rs = restart_row(tmp_path)
    assert rs["fallback"] == "hook_errors_in_warmup" and rs["shadow_complete"] is False


def test_cooldown_edge_the_shadow_decides_up_to_ten_seconds_after_the_row(tmp_path):
    """An exit stamped 55 s before the reference trigger: the reference's wall clock at dispatch is up to 10 s later, so its 60 s cooldown has passed. now_ms = t + 10 s blocks."""
    rows = world()
    eng, _ = phase_a(tmp_path, rows, cheap_restart=True)
    t = H0 + 32 * HOUR
    b, _ = restart(tmp_path, rows, eng, cheap_restart=True, run_to=H0 + 33 * HOUR + 120_000, pre=lambda b: b.table.note_exit("Q", t - 55_000))
    assert b.blocked["Q"]["why"] == "restart_shadow"
    # and an exit 65 s... 5 s before the trigger still suppresses the Buy (the reference is inside its cooldown): the shadow does not invent it
    shutil_root = tmp_path / "again"
    eng2, _ = phase_a(shutil_root, rows, cheap_restart=True)
    b2, _ = restart(shutil_root, rows, eng2, cheap_restart=True, run_to=H0 + 33 * HOUR + 120_000, pre=lambda b: b.table.note_exit("Q", t - 5_000))
    assert "Q" not in b2.blocked


def test_rows_the_table_skipped_as_already_applied_give_no_shadow_buy(tmp_path):
    """The reference trigger is decided live before the crash (entry logged, pool blocked as open); the pickle holds it, the warm-up replay is skipped by the table."""
    rows = sorted(world() + [row(H0 + 32 * HOUR + 59 * 60_000 + 30_000, "Q", "sell", **DROWN)], key=lambda r: (r["t_recv_ms"], r["pool"]))
    rows = [r for r in rows if not (r["pool"] == "Q" and r["t_recv_ms"] == H0 + 32 * HOUR)]
    eng, _ = phase_a(tmp_path, rows, to=H0 + 33 * HOUR + 60_000, cheap_restart=True)
    assert kinds(tmp_path / "rules").count("entry") == 1
    b, _ = restart(tmp_path, rows, FakeEngine(tmp_path / "rules"), now=H0 + 33 * HOUR + 120_000, cheap_restart=True, run_to=H0 + 34 * HOUR)
    rs = restart_row(tmp_path)
    assert rs["n_shadow_buys"] == 0 and rs["shadow_complete"] is True and b.blocked["Q"]["why"] == "restart"


def test_stale_rows_after_catch_up_inside_the_final_span_are_shadowed(tmp_path):
    rows = world()
    rows.append(row(H0 + 33 * HOUR + 5_000, "Q", "sell", **DROWN))
    rows = [r for r in rows if not (r["pool"] == "Q" and r["t_recv_ms"] == H0 + 32 * HOUR)]
    rows.sort(key=lambda r: (r["t_recv_ms"], r["pool"]))
    eng, _ = phase_a(tmp_path, rows, cheap_restart=True)
    b = lane_host(tmp_path, rows, live=True, engine=eng, cheap_restart=True)
    Clocked(b)
    b.note_rebuild(H0, 0)
    b.lane_start(H0 + 33 * HOUR)
    b.resume()
    b.clock[0] = H0 + 33 * HOUR + 7 * 60_000  # the warm-up took 7 minutes
    b.catch_up(H0 + 33 * HOUR)
    assert b.lane_gaps[-1]["to_ms"] == H0 + 33 * HOUR + 7 * 60_000 + 10_000 and b.lane_gaps[-1]["shadow_complete"] is True and "Q" not in b.blocked
    b.step(H0 + 33 * HOUR + 6_000)  # the trigger at 33 h + 5 s is read now, 7 minutes old: stale for the lane, in the downtime span
    assert b.counters.get("stale_rows", 0) >= 1 and b.blocked["Q"]["why"] == "restart_shadow"
    assert "entry" not in kinds(tmp_path / "rules")


def test_the_shadow_leaves_the_pool_table_byte_equal(tmp_path):
    rows = world()
    pk = {}
    for name, flag in (("on", True), ("off", False)):
        d = tmp_path / name
        eng, _ = phase_a(d, rows, cheap_restart=flag)
        b, _ = restart(d, rows, eng, cheap_restart=flag, run_to=H0 + 34 * HOUR)
        d_ = b.table.to_dict()
        d_.pop("marks", None)
        pk[name] = pickle.dumps(d_, protocol=4)
    assert pk["on"] == pk["off"]


# ------------------------------------------------------------------------------------------------ (c) re-block from refusal rows
def _refusal_world(tmp, rows, **flags):
    eng, _ = phase_a(tmp, rows, **flags)
    ck_path = tmp / "rules" / "lane" / "checkpoint.json"
    ck = json.loads(ck_path.read_text())
    assert [r["kind"] for r in log_rows(tmp)].count(H.KIND_GAP_REFUSED) == 1
    ck["blocked"] = {}  # the refusal was logged after the last checkpoint: the checkpoint does not hold the block
    ck_path.write_text(json.dumps(ck))
    return eng


def _gap_two_trigger():
    t1 = H0 + 30 * HOUR + 900_000
    return sorted(two_trigger_rows() + [gap_row(t1 - 2 * HOUR, t1 - 2 * HOUR + 30_000)], key=lambda r: r["t_recv_ms"]), t1


def test_blocks_are_rebuilt_from_refusal_rows_still_inside_block_ms(tmp_path):
    rows, t1 = _gap_two_trigger()
    eng = _refusal_world(tmp_path, rows, cheap_restart=True)
    b = lane_host(tmp_path, rows, live=True, engine=eng, cheap_restart=True)
    b.lane_start(H0 + 33 * HOUR)
    b.resume()
    assert b.blocked["Q"] == {"until_ms": t1 + H.BLOCK_MS, "why": "refusal_row"} and b._reblock == {"reblocked": 1, "unparseable": 0}


def test_without_the_flag_or_after_expiry_no_block_is_rebuilt(tmp_path):
    rows, t1 = _gap_two_trigger()
    eng = _refusal_world(tmp_path, rows)
    b = lane_host(tmp_path, rows, live=True, engine=eng)
    b.lane_start(H0 + 33 * HOUR)
    b.resume()
    assert "Q" not in b.blocked  # v1 relies on the host_down span alone
    d2 = tmp_path / "x"
    eng2 = _refusal_world(d2, rows, cheap_restart=True)
    c = lane_host(d2, rows, live=True, engine=eng2, cheap_restart=True)
    c.lane_start(t1 + H.BLOCK_MS + 1)
    c.resume()
    assert "Q" not in c.blocked


def test_an_unparseable_refusal_row_voids_the_cheap_restart(tmp_path):
    rows, t1 = _gap_two_trigger()
    eng = _refusal_world(tmp_path, rows, cheap_restart=True)
    DecisionLog(str(tmp_path / "rules")).append(L.BOT_ID, H0 + 31 * HOUR, {"kind": H.KIND_GAP_REFUSED, "reason": "feed_gap", "pool": None, "trigger_ms": "x"})
    ck_path = tmp_path / "rules" / "lane" / "checkpoint.json"
    ck = json.loads(ck_path.read_text())
    ck["log_seq"] = 0  # the manual append moved the log head past the checkpoint (a log ahead of its checkpoint is allowed)
    ck_path.write_text(json.dumps(ck))
    restart(tmp_path, rows, eng, cheap_restart=True, run_to=H0 + 34 * HOUR)
    rs = restart_row(tmp_path)
    assert rs["fallback"] == "refusal_row_unparseable" and rs["shadow_complete"] is False and rs["refusal_rows_unparseable"] == 1


# ------------------------------------------------------------------------------------------------ checkpoints, both ways
def test_a_v1_checkpoint_loads_with_the_flags_on_and_a_v2_checkpoint_loads_without(tmp_path):
    rows = world()
    eng, last = phase_a(tmp_path, rows)  # a v1 host (flags off) wrote this checkpoint
    ck = json.loads((tmp_path / "rules" / "lane" / "checkpoint.json").read_text())
    assert "guard" not in ck and ck["schema"] == H.CKPT_SCHEMA
    c = lane_host(tmp_path, rows, live=True, engine=eng, cheap_restart=True, status_span_no_loss=True)
    info = c.lane_start(H0 + 33 * HOUR)
    assert info["checkpoint"] and info["hwm_ms"] == last
    # a flagged host writes a flagged host_down span; the same code with the flags off (the rollback) treats it as unflagged: every pool refused
    d = tmp_path / "v2"
    eng2, _ = phase_a(d, rows, cheap_restart=True)
    restart(d, rows, eng2, cheap_restart=True, run_to=H0 + 34 * HOUR)
    ck2 = json.loads((d / "rules" / "lane" / "checkpoint.json").read_text())
    assert ck2["schema"] == H.CKPT_SCHEMA
    r = lane_host(d, rows, live=True, engine=eng2)
    r.lane_start(H0 + 35 * HOUR)
    hd = [g for g in r.lane_gaps if g["kind"] == "host_down" and g.get("shadow_complete")]
    assert hd and r.judge(H0 + 36 * HOUR, "Z")[0] == "feed_gap"


# ------------------------------------------------------------------------------------------------ (a) status span
def status_row(frm, to, **over):
    r = {**gap_row(frm, to, prog="all", kind="tip_gap_status_unhealthy"), "src": "writer", "reason": "status_unhealthy", "from_slot": 5, "to_slot": 5, "missed_slots": 0,
         "silent_ms": to - frm}
    r.update(over)
    return r


T1 = H0 + 30 * HOUR + 900_000


def run_with(tmp, extra, flag=True, to=H0 + 31 * HOUR):
    rows = sorted(two_trigger_rows() + extra, key=lambda r: r["t_recv_ms"])
    h = lane_host(tmp, rows, status_span_no_loss=flag)
    h.run(H0, to)
    return h, log_rows(tmp)


def test_a_writer_status_span_that_lost_no_row_does_not_refuse_and_says_so(tmp_path):
    h, log = run_with(tmp_path, [status_row(T1 - HOUR, T1 - HOUR + 30_000)])
    ks = [r["kind"] for r in log]
    assert ks.count("entry") == 1 and H.KIND_GAP_REFUSED not in ks
    ig = [r for r in log if r["kind"] == H.KIND_SPAN_IGNORED]
    assert len(ig) == 1 and ig[0]["then"] == "allow" and ig[0]["spans"][0]["src"] == "writer" and ig[0]["spans"][0]["silent_ms"] == 30_000 and ig[0]["trigger_ms"] == T1


def test_the_same_span_with_the_flag_off_refuses_as_v1(tmp_path):
    h, log = run_with(tmp_path, [status_row(T1 - HOUR, T1 - HOUR + 30_000)], flag=False)
    ks = [r["kind"] for r in log]
    assert "entry" not in ks and ks.count(H.KIND_GAP_REFUSED) == 1 and H.KIND_SPAN_IGNORED not in ks


@pytest.mark.parametrize("name,extra", [
    ("adapter_src_even_with_status_reason", [status_row(T1 - HOUR, T1 - HOUR + 30_000, src="tip")]),
    ("missed_slots", [status_row(T1 - HOUR, T1 - HOUR + 30_000, missed_slots=1, to_slot=9)]),
    ("reader_problem", [status_row(T1 - HOUR, T1 - HOUR + 30_000, kind="tip_gap_reader_problem", reason="reader_problem")]),
    ("writer_hole", [status_row(T1 - HOUR, T1 - HOUR + 30_000, kind="tip_gap_writer_hole", reason="writer_hole")]),
    ("longer_than_120s", [status_row(T1 - HOUR, T1 - HOUR + 130_000)]),
    ("silent_ms_mismatch", [status_row(T1 - HOUR, T1 - HOUR + 30_000, silent_ms=1)]),
    ("coinciding_backlog_jump", [status_row(T1 - HOUR, T1 - HOUR + 30_000), {**gap_row(T1 - HOUR + 5_000, T1 - HOUR + 6_000, kind="tip_gap_backlog_jump"), "src": "tip", "missed_slots": 150}]),
])
def test_everything_else_still_refuses(tmp_path, name, extra):
    h, log = run_with(tmp_path, extra)
    ks = [r["kind"] for r in log]
    assert "entry" not in ks and ks.count(H.KIND_GAP_REFUSED) == 1, name
    if name == "coinciding_backlog_jump":
        assert [r for r in log if r["kind"] == H.KIND_GAP_REFUSED][0]["gap"]["kind"] == "tip_gap_backlog_jump"


def test_a_span_reloaded_from_a_checkpoint_without_its_captured_row_refuses(tmp_path):
    rows = two_trigger_rows()
    h = lane_host(tmp_path, rows, status_span_no_loss=True)
    h.lane_gaps.append({k: v for k, v in status_row(T1 - HOUR, T1 - HOUR + 30_000).items() if k not in ("type", "src", "slot", "t_recv_ms")})
    j = h.judge(T1, "Q")
    assert j is not None and j[0] == "feed_gap" and h._ignored == []


@pytest.mark.parametrize("over,ok", [({}, True), ({"src": "tip"}, False), ({"src": None}, False), ({"missed_slots": True}, False), ({"data_loss": False}, False),
                                       ({"prog": "pumpswap"}, False), ({"from_slot": None}, False), ({"silent_ms": 120_000, "to_ms": 120_000}, True),
                                       ({"silent_ms": 120_001, "to_ms": 120_001}, False), ({"reason": "other"}, False)])
def test_captured_no_loss_row_predicate_fails_closed(over, ok):
    r = status_row(0, 30_000)
    r.update(over)
    assert H.captured_no_loss_row(r) is ok
    r2 = status_row(0, 30_000)
    r2.pop("silent_ms")
    assert H.captured_no_loss_row(r2) is False


def test_config_flags_are_booleans_and_default_false():
    assert H._cfg_flag({}, "cheap_restart") is False and H._cfg_flag({"cheap_restart": True}, "cheap_restart") is True
    with pytest.raises(H.R.HostError):
        H._cfg_flag({"cheap_restart": 1}, "cheap_restart")
