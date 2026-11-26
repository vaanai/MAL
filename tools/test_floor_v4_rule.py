"""l1-floor-v4 plumbing: v3 at vault 1.0 SOL with the take-profit multiple pinned by config in {1.3, 1.5}, no default. Rule module, lane host, executor. Synthetic fixtures only.
Nothing here says any TP makes money.

  /data/mal/venv/bin/python -m pytest tools/test_floor_v4_rule.py
"""

from __future__ import annotations

import json

import pytest

from tools import arena_live_bridge as B
from tools import arena_rule_l1 as L
from tools import arena_rule_l1_v3 as V3
from tools import arena_rule_l1_v4 as V4
from tools import floor_arena_executor as F
from tools import floor_lane_host as H
from tools import floor_lane_executor as X
from tools.arena_declog import DecisionLog
from tools.arena_rules import HostError
from tools.test_floor_arena_executor import Clock, cfg_dict, meta, _env  # noqa: F401  (autouse fixture)
from tools.test_floor_lane_executor import T0, feed, lane_raw, mk  # noqa: F401
from tools.test_floor_lane_host import _env as _env_h  # noqa: F401
from tools.test_floor_lane_host_v3 import mid_vault_rows, run_rule
from tools.test_arena_rule_l1 import HOUR, H0, pool_rows, row

V4_ID = "l1-floor-v4"
V3_ID = "l1-floor-v3"
ONE = 1_000_000_000
TPS = (1.3, 1.5)


# ------------------------------------------------------------------------------------------------ rule module
@pytest.mark.parametrize("bad", [None, 0, 1, 1.0, 1.4, 1.2, 2.0, 1.30000001, "1.3", "1.5", True, [1.3], -1.5, float("nan")])
def test_rule_refuses_missing_or_unpinned_tp(bad):
    with pytest.raises(HostError):
        V4.make_rule(bad)
    with pytest.raises(HostError):
        V4.check_tp(bad)
    with pytest.raises(HostError):
        H.check_rule_cfg(V4_ID, None, bad)
    with pytest.raises(HostError):
        H.build_rules("first_print", H.LOOKBACK_MIN_S, [None], V4_ID, None, bad)


def test_make_rule_has_no_default():
    with pytest.raises(HostError):
        V4.make_rule()


@pytest.mark.parametrize("tp", TPS)
def test_rule_pins_tp_vault_and_reason(tp):
    r = V4.make_rule(tp)
    assert r.bot_id == V4_ID == H.RULE_V4 == F.RULE_V4 and r.tp == tp and r.q_star == ONE and r.reason_tp == f"tp_{tp}x"
    assert V4.TP_ALLOWED == F.TP_ALLOWED == TPS


def test_frozen_constants_are_restored_and_not_edited():
    r = V4.make_rule(1.3)
    assert (L.TP, L.REASON_TP, L.Q_STAR) == (1.5, "tp_1.5x", 0.5e9)

    class Ctx:
        positions: list = []

        @property
        def state(self):
            raise RuntimeError("boom")
    row_ = {"type": "trade", "prog": "pumpswap", "venue": "pumpswap", "quote_mint": L.WSOL, "pool": "P", "side": "sell"}
    with pytest.raises(RuntimeError):
        r.on_row(Ctx(), row_)
    assert (L.TP, L.REASON_TP, L.Q_STAR) == (1.5, "tp_1.5x", 0.5e9)


def entries(root, bot):
    return [r for r in DecisionLog(str(root)).rows(bot) if r["kind"] == "entry"]


def _run_v4(tmp, rows, tp, hours=60):
    from tools.arena_rules import register_rules
    from tools.arena_rules_testkit import FakeEngine, write_tape
    from tools.test_floor_lane_host import BINDING, SHA40
    tape = tmp / "tape"
    if not tape.exists():
        write_tape(tape, rows)
    reg = tmp / "bots.json"
    rule = H._load_rule(V4_ID, ONE, tp)
    rule.table_mode = "first_print"
    if not reg.exists():
        register_rules([rule], str(reg), SHA40, H0, now_ms=H0 - 1, verify_sha=False)
    h = H.make_lane_host(root=str(tmp / "rules"), ledger_root=str(tmp / "ledger"), registry=str(reg), tape_dir=str(tape), clock_start_ms=H0, live=False, binding=BINDING,
                         start_ms=0, table_mode="first_print", engine=FakeEngine(tmp / "rules"),
                         rule_id=V4_ID, q_star_lamports=None, tp=tp)
    h.resume()
    h.run(H0, H0 + hours * HOUR)
    return h


@pytest.fixture(scope="module")
def mid_runs(tmp_path_factory):
    """One 34 h host run per arm on the mid-vault fixture (the slow part of this file), shared by the tests below: v3 at 1.0 SOL, v4 at 1.3, v4 at 1.5."""
    import collections
    import tools.test_arena_rule_l1 as T
    from tools.test_floor_lane_host import SHA40
    base = tmp_path_factory.mktemp("mid")
    with pytest.MonkeyPatch.context() as mp:  # the function-scoped autouse fixture of test_floor_lane_host, reproduced for a module-scoped fixture
        mp.setenv("MAL_ARENA_TEST", "1")
        mp.setattr("tools.arena_paper.shutil.disk_usage", lambda path: collections.namedtuple("Usage", "total used free")(1000, 500, 500))
        mp.setattr(T, "SHA", SHA40)
        run_rule(base / "v3", mid_vault_rows(), V3_ID, ONE, hours=34)
        _run_v4(base / "v4_13", mid_vault_rows(), 1.3, hours=34)
        _run_v4(base / "v4_15", mid_vault_rows(), 1.5, hours=34)
    return base


@pytest.mark.parametrize("tp,d", [(1.3, "v4_13"), (1.5, "v4_15")])
def test_v4_admits_the_mid_vault_pool_stamps_meta_and_start_row(mid_runs, tp, d):
    log = DecisionLog(str(mid_runs / d / "rules")).rows(V4_ID)
    ent = [r for r in log if r["kind"] == "entry"]
    assert len(ent) == 1 and ent[0]["meta"]["rule"] == V4_ID and ent[0]["meta"]["tp"] == tp and ent[0]["meta"]["q_star_lamports"] == ONE
    assert ent[0]["meta"]["hold_s"] == L.HOLD_S and ent[0]["stake_sol"] == 0.25
    start = [r for r in log if r["kind"] == "start"]
    assert start and all(r["binding"]["tp"] == tp and r["binding"]["q_star_lamports"] == ONE for r in start)


def test_v4_entries_equal_v3_at_one_sol_except_rule_id_and_tp(mid_runs):
    from tools.arena_paper import canon
    a = entries(mid_runs / "v3" / "rules", V3_ID)
    b = entries(mid_runs / "v4_15" / "rules", V4_ID)
    assert len(a) == len(b) == 1

    def n(r, bot):
        r = {k: v for k, v in r.items() if k not in ("prev", "sha", "order_id")}
        r["meta"] = {k: v for k, v in r["meta"].items() if k != "tp"}
        return canon(r).replace(bot, "X")
    assert n(a[0], V3_ID) == n(b[0], V4_ID) and a[0]["meta"]["tp"] == b[0]["meta"]["tp"] == 1.5


def test_v3_rows_carry_no_tp_binding(mid_runs):
    log = DecisionLog(str(mid_runs / "v3" / "rules")).rows(V3_ID)
    assert log and all("tp" not in (r.get("binding") or {}) for r in log)
    assert [r["meta"]["tp"] for r in log if r["kind"] == "entry"] == [1.5]


def between_rows():
    """Pool A enters on the drained-pool sell at 30 h 15 min; its landing print follows 10 s later; at +60 s a buy puts the post-trade spot (with the position kept) at about 1.40 x the
    post-buy mark (computed from the rule's own formulas: 1.3 < 1.40 < 1.5), then nothing."""
    rows = [r for r in pool_rows(p="A")]
    t = max(r["t_recv_ms"] for r in rows if r["pool"] == "A")
    rows.append(row(t + 10_000, "A", "sell", q=400_000_000, sol=1_000_000))
    rows.append(row(t + 60_000, "A", "buy", q=5_000_000_000, sol=100_000_000))
    return sorted(rows, key=lambda r: r["t_recv_ms"])


def exits(root, bot):
    return [r for r in DecisionLog(str(root)).rows(bot) if r["kind"] == "exit"]


def test_a_print_between_1_3x_and_1_5x_takes_profit_at_tp_1_3_and_not_at_1_5(tmp_path):
    _run_v4(tmp_path / "a", between_rows(), 1.3, hours=34)
    _run_v4(tmp_path / "b", between_rows(), 1.5, hours=34)
    assert len(entries(tmp_path / "a" / "rules", V4_ID)) == 1 and len(entries(tmp_path / "b" / "rules", V4_ID)) == 1
    assert [r["reason"] for r in exits(tmp_path / "a" / "rules", V4_ID)] == ["tp_1.3x"]
    assert exits(tmp_path / "b" / "rules", V4_ID) == []  # no print reaches 1.5 x: the position is still open


# ------------------------------------------------------------------------------------------------ host: config, pins, start row, resume
def test_host_pins_and_unchanged_v2_v3_pins():
    assert H.pins_for(H.RULE_V2) == H.PINS
    assert "tools/arena_rule_l1_v4.py" not in H.pins_for(V3_ID) and "tools/arena_rule_l1_v4.py" not in H.pins_for(H.RULE_V2)
    assert "tools/arena_rule_l1_v4.py" in H.pins_for(V4_ID) and "tools/arena_rule_l1_v3.py" in H.pins_for(V4_ID)
    H.verify_pins(pins=H.pins_for(V4_ID))


@pytest.mark.parametrize("tp", TPS)
def test_check_rule_cfg_v4(tp):
    assert H.check_rule_cfg(V4_ID, None, tp) == (V4_ID, ONE)
    assert H.check_rule_cfg(V4_ID, ONE, tp) == (V4_ID, ONE)
    for q in (500_000_000, 750_000_000, 0, True):
        with pytest.raises(HostError):
            H.check_rule_cfg(V4_ID, q, tp)


def test_v2_and_v3_refuse_a_tp_value():
    for rid, q in ((H.RULE_V2, None), (V3_ID, ONE)):
        for tp in TPS:
            with pytest.raises(HostError):
                H.check_rule_cfg(rid, q, tp)
    assert H.check_rule_cfg(H.RULE_V2, None, None) == (H.RULE_V2, None)
    assert H.check_rule_cfg(V3_ID, ONE, None) == (V3_ID, ONE)


def test_config_loading(tmp_path):
    base = {"root": str(tmp_path / "r"), "tape_dir": "x", "clock_start_ms": 1}
    p = tmp_path / "c.json"

    def load(**kw):
        p.write_text(json.dumps({**base, **kw}))
        return H._load_cfg(str(p))
    d = load(rule_id=V4_ID, tp=1.3)
    assert (d["rule_id"], d["tp"], d["q_star_lamports"]) == (V4_ID, 1.3, ONE)
    assert load(rule_id=V4_ID, tp=1.5, q_star_lamports=ONE)["tp"] == 1.5
    for bad in ({"rule_id": V4_ID}, {"rule_id": V4_ID, "tp": None}, {"rule_id": V4_ID, "tp": 1.4}, {"rule_id": V4_ID, "tp": 1.3, "q_star_lamports": 500_000_000},
                {"tp": 1.3}, {"tp": None}, {"rule_id": V3_ID, "q_star_lamports": ONE, "tp": 1.3}, {"rule_id": V3_ID, "q_star_lamports": ONE, "tp": None}):
        with pytest.raises(HostError):
            load(**bad)
    assert load()["tp"] is None and load(rule_id=V3_ID, q_star_lamports=ONE)["tp"] is None


def test_main_refuses_v4_without_tp(tmp_path, capsys):
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"root": str(tmp_path / "r"), "tape_dir": "x", "clock_start_ms": 1, "rule_id": V4_ID}))
    assert H.main(["verify", "--config", str(p)]) == 2
    assert "tp" in capsys.readouterr().err


def test_resume_with_a_different_tp_refuses_and_the_same_continues(mid_runs, tmp_path):
    import shutil
    shutil.copytree(mid_runs / "v4_13", tmp_path / "r")
    tmp = tmp_path / "r"
    root = tmp / "rules"
    before = DecisionLog(str(root)).rows(V4_ID)
    with pytest.raises(HostError, match="tp"):
        _run_v4(tmp, mid_vault_rows(), 1.5, hours=1)
    assert DecisionLog(str(root)).rows(V4_ID) == before  # a start refusal: no decision row was written
    _run_v4(tmp, mid_vault_rows(), 1.3, hours=1)
    after = DecisionLog(str(root)).rows(V4_ID)
    assert len(after) > len(before) and after[:len(before)] == before


# ------------------------------------------------------------------------------------------------ v2 / v3 paths unchanged
def test_v2_v3_decision_md5s_are_pinned_elsewhere():
    """The v2 decision md5 on origin/main (V2_DECISION_DIGEST_MAIN) is asserted by tools/test_floor_lane_host_v3.py, and legs (i)/(ii) of this PR
    (ARTIFACTS/lab/floor-v4-rule/) show v2 and v3 byte-identical base vs head; this file keeps its v2/v3 runs out to stay fast."""
    assert H.pins_for(H.RULE_V2) == H.PINS and L.TP == 1.5 and L.REASON_TP == "tp_1.5x"


def test_executor_v2_v3_constants_and_followed_reasons_unchanged():
    assert F.FOLLOWED_EXIT_REASONS == ("tp_1.5x", "time_24h") == F.followed_exit_reasons(None)
    assert F.followed_exit_reasons(1.3) == ("tp_1.3x", "time_24h") and F.followed_exit_reasons(1.5) == ("tp_1.5x", "time_24h")


# ------------------------------------------------------------------------------------------------ executor
def v4_raw(tmp, tp=1.3, **kw):
    return lane_raw(tmp, rule_id=F.RULE_V4, expected_tp=tp, **kw)


class Log4:
    def __init__(self, tmp):
        self.d = DecisionLog(str(tmp / "rules"))
        self.bot = V4_ID

    def start(self, ms, tp=1.3, q=ONE, binding=None):
        b = binding if binding is not None else {"q_star_lamports": q, "tp": tp}
        self.d.append(self.bot, ms, {"kind": "start", "binding": b, "frozen_sha": F.FROZEN_SHA, "rules": [self.bot]})

    def entry(self, ms, oid="o1", tp=1.3, q=ONE, rule=V4_ID):
        m = meta(mint="MP", rule=rule, q_star_lamports=q, tp=tp)
        self.d.append(self.bot, ms, {"kind": "entry", "order_id": oid, "pool": "P", "meta": m, "decision_slot": 1, "stake_sol": 0.25, "attempt_key": None})

    def exit(self, ms, oid="o1", reason="tp_1.3x"):
        self.d.append(self.bot, ms, {"kind": "exit", "order_id": oid, "reason": reason, "pool": "P"})


def run_v4(tmp, *, cfg_tp=1.3, start_tp=1.3, entry_tp=1.3, start_q=ONE, binding=None, exit_reason=None):
    raw = v4_raw(tmp, cfg_tp)
    clk = Clock(T0)
    br, inner = mk(tmp, clk, raw=raw, expected_q_star=None, expected_tp=cfg_tp)
    lg = Log4(tmp)
    lg.start(T0 - 1000, tp=start_tp, q=start_q, binding=binding)
    lg.entry(T0, tp=entry_tp)
    if exit_reason:
        lg.exit(T0 + 1, reason=exit_reason)
    feed(br, clk)
    return br, inner


@pytest.mark.parametrize("tp", TPS)
def test_executor_follows_v4_at_its_pinned_tp(tmp_path, tp):
    br, inner = run_v4(tmp_path, cfg_tp=tp, start_tp=tp, entry_tp=tp)
    assert br.cfg.bot_id == V4_ID and br.fs["tp"] == tp and len(inner.buys) == 1 and br.state.latches == {}
    assert br.followed_exits == (f"tp_{tp}x", "time_24h")


@pytest.mark.parametrize("bad", [None, 1.4, 1, True, "1.3", 0.0])
def test_executor_config_requires_a_pinned_tp_for_v4(tmp_path, bad):
    raw = lane_raw(tmp_path, rule_id=F.RULE_V4)
    if bad is not None:
        raw["expected_tp"] = bad
    with pytest.raises(ValueError):
        F.make_config(raw)
    assert not X.check(raw)["ok"]


def test_executor_v2_v3_refuse_expected_tp_even_null(tmp_path):
    for rid in (None, "l1-floor-v2", "l1-floor-v3"):
        for v in (1.3, None):
            d = cfg_dict(tmp_path, expected_tp=v, **({"rule_id": rid} if rid else {}))
            with pytest.raises(ValueError):
                F.make_config(d)


def test_executor_v4_expected_q_star_must_be_one_sol(tmp_path):
    F.make_config(cfg_dict(tmp_path, rule_id=V4_ID, expected_tp=1.3, expected_q_star_lamports=ONE))
    for q in (500_000_000, 750_000_000, True):
        with pytest.raises(ValueError):
            F.make_config(cfg_dict(tmp_path, rule_id=V4_ID, expected_tp=1.3, expected_q_star_lamports=q))


def test_entry_tp_other_than_the_pinned_one_is_refused(tmp_path):
    br, inner = run_v4(tmp_path, cfg_tp=1.3, start_tp=1.3, entry_tp=1.5)
    assert inner.buys == []
    assert any(json.loads(x).get("why") == "meta_pin" for x in br.log_path.read_text().splitlines() if '"entry_refused"' in x)


def test_v2_style_tp_entry_is_refused_by_v4_but_v2_still_requires_l_tp(tmp_path):
    br, inner = run_v4(tmp_path, cfg_tp=1.3, start_tp=1.3, entry_tp=L.TP)
    assert inner.buys == []
    clk = Clock(T0)
    (tmp_path / "v2").mkdir()
    b2, i2 = mk(tmp_path / "v2", clk)
    assert b2.expected_tp is None and b2.followed_exits == F.FOLLOWED_EXIT_REASONS


@pytest.mark.parametrize("start_tp", [1.5, 1.4, None, True, "1.3"])
def test_start_row_tp_mismatch_latches(tmp_path, start_tp):
    br, inner = run_v4(tmp_path, cfg_tp=1.3, start_tp=start_tp, entry_tp=1.3)
    assert F.TP_LATCH in br.state.latches and inner.buys == []


@pytest.mark.parametrize("binding", [{}, "x", {"q_star_lamports": ONE}])
def test_start_row_without_tp_latches(tmp_path, binding):
    br, inner = run_v4(tmp_path, binding=binding)
    assert F.TP_LATCH in br.state.latches and inner.buys == []


def test_start_row_q_star_not_one_sol_latches_for_v4(tmp_path):
    br, inner = run_v4(tmp_path, start_q=750_000_000)
    assert F.Q_STAR_LATCH in br.state.latches and inner.buys == []


def test_a_second_start_row_at_another_tp_latches(tmp_path):
    clk = Clock(T0)
    br, inner = mk(tmp_path, clk, raw=v4_raw(tmp_path, 1.3), expected_tp=1.3)
    lg = Log4(tmp_path)
    lg.start(T0 - 2000, tp=1.3)
    lg.start(T0 - 1000, tp=1.5)
    lg.entry(T0)
    feed(br, clk)
    assert F.TP_LATCH in br.state.latches and inner.buys == []


def test_exit_reason_follows_the_pinned_tp(tmp_path):
    br, _ = run_v4(tmp_path, exit_reason="tp_1.3x")
    assert br.fs["exits_not_followed"] == {}
    (tmp_path / "b").mkdir()
    br2, _ = run_v4(tmp_path / "b", exit_reason="tp_1.5x")
    assert br2.fs["exits_not_followed"] == {"tp_1.5x": 1}


def test_bridge_pin_pairing(tmp_path):
    cfg4 = F.make_config(cfg_dict(tmp_path, rule_id=V4_ID, expected_tp=1.3))
    cfg2 = F.make_config(cfg_dict(tmp_path / "x"))
    port = B.DryPort(cfg4.dry_balance_lamports)
    with pytest.raises(SystemExit):
        F.FloorBridge(cfg4, port, now_ms=Clock())  # v4 without a pinned tp
    with pytest.raises(SystemExit):
        F.FloorBridge(cfg2, B.DryPort(cfg2.dry_balance_lamports), now_ms=Clock(), expected_tp=1.3)  # v2 with one


def test_dry_replay_follows_v4(tmp_path):
    lg = Log4(tmp_path)
    lg.start(T0 - 1000)
    lg.entry(T0)
    lg.exit(T0 + 10)
    res = F.replay_equivalence(cfg_dict(tmp_path, rule_id=V4_ID, expected_tp=1.3, expected_q_star_lamports=ONE))
    assert res["orders"] == 2 and res["decisions"] == 2 and not res["latches"], res
    bad = F.replay_equivalence(cfg_dict(tmp_path, rule_id=V4_ID, expected_tp=1.5, state_dir=str(tmp_path / "st2")))
    assert F.TP_LATCH in bad["latches"] and not bad["equal"]


def test_executor_refuses_a_state_dir_persisted_at_another_tp(tmp_path):
    br, _ = run_v4(tmp_path, cfg_tp=1.3, start_tp=1.3, entry_tp=1.3)
    assert br.fs["tp"] == 1.3
    br._save()
    if hasattr(br, "release_lane_lock"):
        br.release_lane_lock()
    with pytest.raises(SystemExit, match="expected_tp"):
        mk(tmp_path, Clock(T0), raw=v4_raw(tmp_path, 1.5), expected_tp=1.5)
    mk(tmp_path, Clock(T0), raw=v4_raw(tmp_path, 1.3), expected_tp=1.3)  # the same TP restarts


def test_host_and_executor_both_refuse_a_float_q_star_for_v4(tmp_path):
    for q in (1e9, 1000000000.0):
        with pytest.raises(HostError):
            H.check_rule_cfg(V4_ID, q, 1.3)
        with pytest.raises(ValueError):
            F.make_config(cfg_dict(tmp_path, rule_id=V4_ID, expected_tp=1.3, expected_q_star_lamports=q))
