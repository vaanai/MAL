"""FLOOR executor arm selection: config `rule_id` follows decisions/l1-floor-v3 only when it says so; v2 is the default and unchanged. Synthetic only: no key, no network, no tape.
Plumbing only: nothing here says any q_star makes money.

  /data/mal/venv/bin/python -m pytest tools/test_floor_executor_v3.py
"""

from __future__ import annotations

import json

import pytest

from tools import arena_live_bridge as B
from tools import arena_rule_l1_v3 as V3
from tools import floor_arena_executor as F
from tools import floor_lane_executor as X
from tools.arena_declog import DecisionLog
from tools.test_floor_arena_executor import Clock, cfg_dict, meta, _env  # noqa: F401  (autouse fixture)
from tools.test_floor_lane_executor import T0, FakeInner, feed, lane_raw, mk, write_live_ok  # noqa: F401

Q = 750_000_000


class Log3:
    """Hand-written decision log of the v3 arm, in the host's row format."""

    def __init__(self, tmp, bot=F.RULE_V3):
        self.d = DecisionLog(str(tmp / "rules"))
        self.bot = bot

    def start(self, ms, q=Q, binding="q"):
        b = {"q_star_lamports": q} if binding == "q" else binding
        self.d.append(self.bot, ms, {"kind": "start", "binding": b, "frozen_sha": F.FROZEN_SHA, "rules": [self.bot]})

    def entry(self, ms, oid="o1", q=Q, rule=F.RULE_V3):
        m = meta(mint="MP", rule=rule, q_star_lamports=q)
        self.d.append(self.bot, ms, {"kind": "entry", "order_id": oid, "pool": "P", "meta": m, "decision_slot": 1, "stake_sol": 0.25, "attempt_key": None})


def v3_raw(tmp, **kw):
    return lane_raw(tmp, rule_id=F.RULE_V3, **kw)


def run_v3(tmp, *, start_q=Q, expected=None, binding="q", entry_q=Q):
    raw = v3_raw(tmp) | ({"expected_q_star_lamports": expected} if expected is not None else {})
    clk = Clock(T0)
    br, inner = mk(tmp, clk, raw=raw, expected_q_star=expected)
    lg = Log3(tmp)
    lg.start(T0 - 1000, q=start_q, binding=binding)
    lg.entry(T0, q=entry_q)
    feed(br, clk)
    return br, inner


def test_pins_equal_the_rules_own_table():
    assert F.RULE_V3 == V3.BOT_ID == "l1-floor-v3" and F.RULE_V2 == "l1-floor-v2" and F.RULE_IDS == ("l1-floor-v2", "l1-floor-v3")
    assert F.Q_STAR_ALLOWED == V3.Q_STAR_ALLOWED


def test_default_is_v2_and_a_stray_expected_key_is_refused(tmp_path):
    assert F.make_config(cfg_dict(tmp_path)).bot_id == "l1-floor-v2"
    assert F.make_config(cfg_dict(tmp_path, rule_id="l1-floor-v2")).bot_id == "l1-floor-v2"
    cfg, _lane, _ok = X.make_config(lane_raw(tmp_path))
    assert cfg.bot_id == "l1-floor-v2"
    for bad in ({"expected_q_star_lamports": Q}, {"expected_q_star_lamports": None}, {"rule_id": "l1-floor-v2", "expected_q_star_lamports": Q}):
        with pytest.raises(ValueError):
            F.make_config(cfg_dict(tmp_path, **bad))


@pytest.mark.parametrize("rid", ["l1-floor-v1", "l1-floor-v4", "h5v1", "", None, 3, ["l1-floor-v3"], "L1-FLOOR-V3", "l1-floor-v3 "])
def test_unpinned_rule_id_refuses(tmp_path, rid):
    with pytest.raises(ValueError):
        F.make_config(cfg_dict(tmp_path, rule_id=rid))
    assert X.run(lane_raw(tmp_path, mode="dry", rule_id=rid), live_flag=False, root_checks=False) == 2
    assert not X.check(lane_raw(tmp_path, mode="dry", rule_id=rid))["ok"]


def test_bot_id_must_agree_with_rule_id(tmp_path):
    with pytest.raises(ValueError):
        F.make_config(cfg_dict(tmp_path, rule_id="l1-floor-v3", bot_id="l1-floor-v2"))
    with pytest.raises(ValueError):
        F.make_config(cfg_dict(tmp_path, bot_id="l1-floor-v3"))  # a v3 arm needs rule_id too
    assert F.make_config(cfg_dict(tmp_path, rule_id="l1-floor-v3", bot_id="l1-floor-v3")).bot_id == "l1-floor-v3"


@pytest.mark.parametrize("bad", [0, 1, 600_000_000, 1_500_000_000, True, 7.5e8, "750000000", [Q]])
def test_expected_q_star_must_be_pinned(tmp_path, bad):
    with pytest.raises(ValueError):
        F.make_config(cfg_dict(tmp_path, rule_id="l1-floor-v3", expected_q_star_lamports=bad))


def test_v3_config_follows_the_v3_decision_log(tmp_path):
    br, inner = run_v3(tmp_path)
    assert br.cfg.bot_id == "l1-floor-v3" and br.fs["q_star_lamports"] == Q
    assert len(inner.buys) == 1 and br.state.latches == {}
    assert (tmp_path / "rules" / "decisions" / "l1-floor-v3").is_dir() and not (tmp_path / "rules" / "decisions" / "l1-floor-v2").exists()
    starts = [json.loads(x) for x in (tmp_path / "lane" / "ledger.jsonl").read_text().splitlines() if '"start"' in x]
    assert starts and starts[0]["book"] == "l1-floor-v3"


def test_v3_with_matching_expected_q_star_follows(tmp_path):
    br, inner = run_v3(tmp_path, expected=Q)
    assert len(inner.buys) == 1 and br.state.latches == {}


def test_v3_does_not_read_the_v2_log(tmp_path):
    clk = Clock(T0)
    br, inner = mk(tmp_path, clk, raw=v3_raw(tmp_path))
    lg = Log3(tmp_path, bot=F.RULE_V2)  # a v2-arm log in the same root
    lg.start(T0 - 1000)
    lg.entry(T0, rule=F.RULE_V2)
    feed(br, clk)
    assert inner.buys == [] and br.fs["starts_seen"] == 0


def test_expected_q_star_mismatch_latches_and_buys_nothing(tmp_path):
    br, inner = run_v3(tmp_path, start_q=500_000_000, expected=Q, entry_q=500_000_000)
    assert F.Q_STAR_LATCH in br.state.latches and inner.buys == []


@pytest.mark.parametrize("q", [123, 0, 2_000_000_000, True, 7.5e8, "750000000", None])
def test_start_row_q_star_outside_the_pinned_set_latches(tmp_path, q):
    br, inner = run_v3(tmp_path, start_q=q, entry_q=Q)
    assert F.Q_STAR_LATCH in br.state.latches and inner.buys == []


@pytest.mark.parametrize("binding", [None, {}, "x", {"q_star_lamports": None}])
def test_start_row_without_q_star_latches(tmp_path, binding):
    br, inner = run_v3(tmp_path, binding=binding)
    assert F.Q_STAR_LATCH in br.state.latches and inner.buys == []


def test_entry_at_another_q_star_than_the_start_row_is_refused(tmp_path):
    br, inner = run_v3(tmp_path, start_q=Q, entry_q=1_000_000_000)
    assert inner.buys == [] and F.Q_STAR_LATCH not in br.state.latches
    assert any(json.loads(x).get("why") == "q_star_pin" for x in br.log_path.read_text().splitlines() if '"entry_refused"' in x)


def test_entry_without_a_start_row_is_refused(tmp_path):
    clk = Clock(T0)
    br, inner = mk(tmp_path, clk, raw=v3_raw(tmp_path))
    Log3(tmp_path).entry(T0)
    feed(br, clk)
    assert inner.buys == []


def test_a_second_start_row_at_another_q_star_latches(tmp_path):
    clk = Clock(T0)
    br, inner = mk(tmp_path, clk, raw=v3_raw(tmp_path))
    lg = Log3(tmp_path)
    lg.start(T0 - 2000, q=Q)
    lg.start(T0 - 1000, q=500_000_000)
    lg.entry(T0, q=Q)
    feed(br, clk)
    assert F.Q_STAR_LATCH in br.state.latches and inner.buys == []


def test_v2_arm_ignores_the_v3_checks(tmp_path):
    """A v2 bridge reads the v2 arm and has no q_star state: its fs gains no key."""
    clk = Clock(T0)
    br, inner = mk(tmp_path, clk)
    assert br.cfg.bot_id == "l1-floor-v2" and br.expected_q_star is None
    from tools.test_floor_lane_executor import entry
    entry(tmp_path)
    feed(br, clk)
    assert len(inner.buys) == 1 and "q_star_lamports" not in br.fs


def test_dry_replay_follows_v3(tmp_path):
    lg = Log3(tmp_path)
    lg.start(T0 - 1000)
    lg.entry(T0)
    res = F.replay_equivalence(cfg_dict(tmp_path, rule_id="l1-floor-v3", expected_q_star_lamports=Q))
    assert res["orders"] == 1 and res["decisions"] == 1 and not res["latches"], res
    bad = F.replay_equivalence(cfg_dict(tmp_path, rule_id="l1-floor-v3", expected_q_star_lamports=500_000_000, state_dir=str(tmp_path / "st2")))
    assert F.Q_STAR_LATCH in bad["latches"] and not bad["equal"]


def test_a_v2_bridge_refuses_an_expected_q_star(tmp_path):
    cfg = F.make_config(cfg_dict(tmp_path))
    with pytest.raises(SystemExit):
        F.FloorBridge(cfg, B.DryPort(cfg.dry_balance_lamports), now_ms=Clock(), expected_q_star=Q)
