"""FLOOR dry executor: pins, decision equivalence on a replayed synthetic hour set, restart and resume, exit-gap rules, fail-closed refusals. Synthetic tape only.
Nothing here says the rule makes money; no price, fill or P&L is produced."""

from __future__ import annotations

import collections
import json
from pathlib import Path

import pytest

from tools import arena_live_bridge as B
from tools import arena_rule_l1 as L
from tools import floor_arena_executor as F
from tools.arena_declog import DecisionLog
from tools.arena_pool_table import PoolTable  # noqa: F401  (import check only)
from tools.test_arena_rule_l1 import H0, HOUR, SOLX, SHA, entries, pool_rows, row, run_host  # noqa: F401  (fixtures of the rule's own tests)
import tools.test_arena_rule_l1 as T

SHA40 = F.FROZEN_SHA
V = 17_600_000_000


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("MAL_ARENA_TEST", "1")
    monkeypatch.setattr("tools.arena_paper.shutil.disk_usage", lambda path: collections.namedtuple("Usage", "total used free")(1000, 500, 500))
    monkeypatch.setattr(T, "SHA", SHA40)


class Clock:
    def __init__(self, t=0):
        self.t = t

    def __call__(self):
        return self.t


def cfg_dict(tmp, **kw):
    d = {"root": str(tmp / "rules"), "state_dir": str(tmp / "state"), "frozen_sha": SHA40, "end_ms": H0 + 400 * HOUR}
    d.update(kw)
    return d


def meta(**kw):
    m = {"rule": L.BOT_ID, "cell": "l1", "hold_s": 86400.0, "tp": 1.5, "mint": "MP", "v": V, "age_s": 30 * 3600, "vol24_sol": 5.0, "r6": 0.5}
    m.update(kw)
    return m


class Log:
    """Hand-written decision log in the host's row format (kinds from tools/arena_rules.py)."""

    def __init__(self, root):
        self.d = DecisionLog(str(root / "rules"))

    def start(self, ms, sha=SHA40):
        self.d.append(F.BOT_ID, ms, {"kind": "start", "binding": None, "frozen_sha": sha, "rules": [F.BOT_ID]})

    def entry(self, ms, oid, pool="P", stake_sol=0.25, **mk):
        self.d.append(F.BOT_ID, ms, {"kind": "entry", "order_id": oid, "pool": pool, "meta": meta(mint="M" + pool, **mk), "decision_slot": 1, "stake_sol": stake_sol, "attempt_key": None})

    def exit(self, ms, oid, reason=L.REASON_TP, pool="P"):
        self.d.append(F.BOT_ID, ms, {"kind": "exit", "order_id": oid, "pool": pool, "reason": reason, "decision_slot": 2, "attempt": 1})

    def other(self, ms, **body):
        self.d.append(F.BOT_ID, ms, body)


def bridge(tmp, clk, port=None, **kw):
    cfg = F.make_config(cfg_dict(tmp, **kw))
    port = port or B.DryPort(cfg.dry_balance_lamports)
    return F.FloorBridge(cfg, port, now_ms=clk), port


def feed(br, clk, ms=None):
    """Read what the log holds now, as of `ms` (default: just after the newest row)."""
    rows = br.read_new_rows()
    for r in rows:
        clk.t = r["ms"] + 50 if ms is None else ms
        br.handle_row(r)
        br._poll_ports()
    br._save()
    return rows


# ------------------------------------------------------------------------------------------------ pins
def test_constants_follow_the_rule_and_the_dec():
    assert F.HOLD_MS == L.HOLD_MS == 86_401_900 and F.BOT_ID == "l1-floor-v2" and F.PAPER_STAKE_SOL == 0.25
    assert F.SEND_FEE_LAMPORTS == 160_000 and F.SELL_RESERVE_LAMPORTS == 1_600_000
    assert F.C0 == {"stake_lamports": 50_000_000, "max_open": 2, "max_trades_per_day": 30, "daily_loss_lamports": 100_000_000, "total_loss_lamports": 200_000_000}
    assert F.LIVE_BUILT is False
    assert F.FOLLOWED_EXIT_REASONS == (L.REASON_TP, L.REASON_TIME)


def test_constants_equal_the_h5_arena_executor_where_they_overlap():
    h5 = pytest.importorskip("tools.h5_arena_executor")
    assert h5.C0_TIER == F.C0 and (h5.FUNDED_MIN_LAMPORTS, h5.FUNDED_MAX_LAMPORTS) == (F.FUNDED_MIN_LAMPORTS, F.FUNDED_MAX_LAMPORTS)
    assert h5.AM8_PRIORITY == F.PRIORITY_LAMPORTS and h5.SELL_RESERVE_SENDS == F.SELL_RESERVE_SENDS
    # The gap hold no longer overlaps: DEC-032 Am.5 (DRAFT) scopes the 60 s data-loss hold to the H5 shadow feed (h5_arena_executor.gap_loses_data);
    # the FLOOR bridge's gaps (host / engine restarts) keep s5 item 2 as written, 10 minutes, and the H5 hold is never longer than that.
    assert F.GAP_HOLD_MS == 600_000 and h5.GAP_HOLD_FLOOR_MS <= h5.GAP_HOLD_MS <= F.GAP_HOLD_MS
    for funded in (None, 350_000_000, 600_000_000, 1_000_000_000):
        assert h5.wallet_floor_lamports(funded, 0) == F.wallet_floor_lamports(funded, 0)


@pytest.mark.parametrize("bad", [{"mode": "live"}, {"bot_id": "h5v1"}, {"frozen_sha": "5e12f8f5"}, {"frozen_sha": "0" * 40}, {"frozen_sha": F.FROZEN_SHA[:39] + "0"}, {"max_hold_ms": 86_400_000},
                                 {"end_ms": None}, {"end_ms": True}, {"funded_lamports": 100_000_000}, {"funded_lamports": 2_000_000_000}, {"stake_lamports": 0},
                                 {"root": ""}, {"per_trade_loss_lamports": -1}])
def test_config_refusals(tmp_path, bad):
    with pytest.raises(ValueError):
        F.make_config(cfg_dict(tmp_path, **bad))


def test_config_only_tightens(tmp_path):
    c = F.make_config(cfg_dict(tmp_path, stake_lamports=900_000_000, max_open=9, max_trades_per_day=99, daily_loss_lamports=10 ** 9, total_loss_lamports=10 ** 9, funded_lamports=600_000_000))
    assert (c.stake_lamports, c.max_open, c.max_trades_per_day, c.daily_loss_lamports, c.total_loss_lamports) == tuple(F.C0.values())
    assert c.wallet_floor_lamports == 350_000_000 and c.max_hold_ms == F.HOLD_MS and c.mode == B.DRY
    c2 = F.make_config(cfg_dict(tmp_path, stake_lamports=20_000_000, max_open=1))
    assert c2.stake_lamports == 20_000_000 and c2.max_open == 1


def test_a_live_port_or_live_config_cannot_start(tmp_path):
    cfg = F.make_config(cfg_dict(tmp_path))
    live_port = B.DryPort(1)
    live_port.live = True
    with pytest.raises(SystemExit):
        F.FloorBridge(cfg, live_port, now_ms=Clock())
    with pytest.raises(SystemExit):
        F.FloorBridge(B.Config(**{**cfg.__dict__, "mode": B.LIVE}), B.DryPort(1), now_ms=Clock())
    with pytest.raises(SystemExit):
        F.FloorBridge(B.Config(**{**cfg.__dict__, "max_hold_ms": 1}), B.DryPort(1), now_ms=Clock())


# ------------------------------------------------------------------------------------------------ decision equivalence on a replayed hour set
def host_with_start(tmp, rows, hours):
    """The v2 host as `arena_rules_v2 run` builds it: a binding, so `resume()` writes the `start` row the bridge needs (tests/test_arena_rule_l1.run_host has none)."""
    from tools.arena_rules import HostConfig, register_rules
    from tools.arena_rules_testkit import FakeEngine, write_tape
    from tools.arena_rules_v2 import HostV2
    from tools.arena_state import ArenaState

    tape = tmp / "tape"
    write_tape(tape, rows)
    rules = T._first_print_rules()
    reg = tmp / "bots.json"
    register_rules(rules, str(reg), SHA40, H0, now_ms=H0 - 1, verify_sha=False)
    cfg = HostConfig(root=str(tmp / "rules"), registry=str(reg), frozen_sha=SHA40, clock_start_ms=H0)
    host = HostV2(cfg, rules, ArenaState(tape, start_ms=0), FakeEngine(tmp / "rules"), binding={"test": 1})
    host.resume()
    host.run(H0, H0 + hours * HOUR)
    return host


def _host_log_scenarios(tmp):
    """Three pools, each enters on the rule's drained-pool sell: A takes profit, B and C time out. Run through the real v2 host (synthetic tape, FakeEngine)."""
    rows = []
    for p, dt in (("A", 0), ("B", 600_000), ("C", 1_200_000)):
        pr = [r for r in pool_rows(p=p) if r["pool"] == p]
        for r in pr:
            r["t_recv_ms"] += dt
            r["signature"] += "x"
        rows += pr
    rows.append(row(H0 + 1000, "X"))
    t = max(r["t_recv_ms"] for r in rows if r["pool"] == "A")
    rows.append(row(t + 10_000, "A", "sell", q=400_000_000, sol=1_000_000))  # the landing print of A's buy
    rows.append(row(t + 60_000, "A", "buy", q=60 * SOLX, sol=50_000_000))  # A: spot far above 1.5 x mark -> take profit
    return sorted(rows, key=lambda r: r["t_recv_ms"])


def test_decision_equivalence_on_a_replayed_set(tmp_path):
    host = host_with_start(tmp_path, _host_log_scenarios(tmp_path), hours=60)
    log = host.log.rows(F.BOT_ID)
    ent = [r for r in log if r["kind"] == "entry"]
    ex = [r for r in log if r["kind"] == "exit"]
    reasons = sorted(r["reason"] for r in ex)
    assert len(ent) == 3 and reasons == sorted([L.REASON_TP, L.REASON_TIME, L.REASON_TIME]), (len(ent), reasons)
    res = F.replay_equivalence(cfg_dict(tmp_path, state_dir=str(tmp_path / "st-a")))
    assert res["equal"] and res["orders"] == 6 and res["decisions"] == 6 and not res["refused"] and not res["latches"], res
    again = F.replay_equivalence(cfg_dict(tmp_path, state_dir=str(tmp_path / "st-b")))
    assert again["md5_orders"] == res["md5_orders"] == res["md5_decisions"]  # deterministic
    # the md5 is of (side, order_id) pairs only: every order the bridge sends is a decision of the frozen rule and every decision is sent
    assert [d[0] for d in F.decisions_from_log(str(tmp_path / "rules"))].count("buy") == 3


def test_replay_check_fails_when_the_log_is_tampered(tmp_path):
    host_with_start(tmp_path, pool_rows(), hours=34)
    f = next(x for x in sorted((tmp_path / "rules" / "decisions" / F.BOT_ID).glob("*.jsonl")) if '"stake_sol"' in x.read_text())
    text = f.read_text()
    assert '"stake_sol":0.25' in text
    f.write_text(text.replace('"stake_sol":0.25', '"stake_sol":0.26', 1))
    res = F.replay_equivalence(cfg_dict(tmp_path))
    assert not res["equal"] and "chain_break" in res["latches"]


def test_replay_of_a_log_from_another_sha_latches(tmp_path):
    lg = Log(tmp_path)
    lg.start(H0, sha="0" * 40)  # the book's start row repeats a different sha than the pin
    lg.entry(H0 + 1000, "o1")
    res = F.replay_equivalence(cfg_dict(tmp_path))
    assert not res["equal"] and "frozen_sha_mismatch" in res["latches"] and res["orders"] == 0


# ------------------------------------------------------------------------------------------------ restart and resume of open positions
def test_bridge_restart_keeps_the_open_position_and_its_deadline(tmp_path):
    clk = Clock()
    lg = Log(tmp_path)
    lg.start(H0)
    lg.entry(H0 + 1000, "o1")
    br, port = bridge(tmp_path, clk)
    feed(br, clk)
    o = br.state.orders["o1"]
    assert o["status"] == "open" and [w["side"] for w in port.would_send] == ["buy"] and br.fs["first_send_ms"] is not None
    # restart: a new process over the same state dir and log, a fresh dry port
    clk.t = H0 + 10 * HOUR
    br2, port2 = bridge(tmp_path, clk)
    assert br2.state.orders["o1"]["status"] == "open" and br2.state.orders["o1"]["decision_ms"] == H0 + 1000
    assert feed(br2, clk, ms=clk.t) == [] and port2.would_send == []  # cursor persisted: nothing re-read, nothing re-bought
    br2.tick()
    assert port2.would_send == []  # not due yet
    clk.t = H0 + 1000 + F.HOLD_MS - 1
    br2.tick()
    assert port2.would_send == []
    clk.t = H0 + 1000 + F.HOLD_MS
    br2.tick()
    assert [w["side"] for w in port2.would_send] == ["sell"] and port2.would_send[0]["reason"] == L.REASON_TIME  # the clock exit, with no host row at all
    br2.tick()
    assert br2.state.orders["o1"]["status"] == "closed" and len(port2.would_send) == 1  # never a second sell


def test_restart_with_the_old_log_rows_replayed_does_not_double_buy(tmp_path):
    clk = Clock()
    lg = Log(tmp_path)
    lg.start(H0)
    lg.entry(H0 + 1000, "o1")
    br, port = bridge(tmp_path, clk)
    feed(br, clk)
    st = Path(br.state_path)
    d = json.loads(st.read_text())
    d["cursor"] = {}  # simulate a lost cursor: the bridge reads the whole log again
    d["prev"], d["seq"] = B.GENESIS, -1
    st.write_text(json.dumps(d))
    br2, port2 = bridge(tmp_path, clk)
    feed(br2, clk, ms=H0 + 2000)
    assert port2.would_send == [] and br2.state.counts.get("duplicate_entry") == 1


def test_host_restart_exit_is_not_followed_and_buys_hold_ten_minutes(tmp_path):
    clk = Clock()
    lg = Log(tmp_path)
    lg.start(H0)
    lg.entry(H0 + 1000, "o1")
    lg.start(H0 + HOUR)  # the host restarted
    lg.other(H0 + HOUR + 1, kind="resume_reopen", order_id="o1", exit_reason="resume_reopen")
    lg.exit(H0 + HOUR + 2000, "o1", reason="resume_reopen")  # the host sells its re-opened book at the first tick
    lg.entry(H0 + HOUR + 5 * 60_000, "o2", pool="Q")  # inside the 10 minute hold
    lg.entry(H0 + HOUR + 11 * 60_000, "o3", pool="R")  # after it
    br, port = bridge(tmp_path, clk)
    feed(br, clk)
    assert [w["order_id"] for w in port.would_send] == ["o1", "o3"] and all(w["side"] == "buy" for w in port.would_send)
    assert br.state.orders["o1"]["status"] == "open" and br.state.orders["o1"].get("gap") is True
    assert br.fs["exits_not_followed"] == {"resume_reopen": 1} and br.state.counts.get("refused_gap_hold") == 1 and "o2" not in br.state.orders


def test_clock_exit_late_is_flagged_gap(tmp_path):
    clk = Clock()
    lg = Log(tmp_path)
    lg.start(H0)
    lg.entry(H0 + 1000, "o1")
    br, port = bridge(tmp_path, clk)
    feed(br, clk)
    clk.t = H0 + 1000 + F.HOLD_MS + F.EXIT_LATE_MS + 5000  # the host was down through the deadline
    br.tick()
    o = br.state.orders["o1"]
    assert [w["side"] for w in port.would_send] == ["buy", "sell"] and o["late"] and o["gap"] and br.fs["late_exits"][0]["order_id"] == "o1"
    assert o["exit_decision_ms"] == H0 + 1000 + F.HOLD_MS  # the rule's instant, not the instant we noticed


# ------------------------------------------------------------------------------------------------ exits
def test_take_profit_row_is_followed_and_the_pool_can_be_re_entered(tmp_path):
    clk = Clock()
    lg = Log(tmp_path)
    lg.start(H0)
    lg.entry(H0 + 1000, "o1")
    lg.exit(H0 + 90_000, "o1", reason=L.REASON_TP)
    lg.entry(H0 + 200_000, "o2")  # the rule allows a re-entry 60 s after an exit decision
    br, port = bridge(tmp_path, clk)
    feed(br, clk)
    assert [(w["side"], w["order_id"]) for w in port.would_send] == [("buy", "o1"), ("sell", "o1"), ("buy", "o2")]
    assert br.state.orders["o1"]["status"] == "closed" and br.state.orders["o2"]["status"] == "open"


def test_an_unknown_exit_reason_is_not_followed_and_the_position_keeps_its_own_clock(tmp_path):
    clk = Clock()
    lg = Log(tmp_path)
    lg.start(H0)
    lg.entry(H0 + 1000, "o1")
    lg.exit(H0 + 5000, "o1", reason="sl_5pct")  # the book has no stop: not a FLOOR reason
    br, port = bridge(tmp_path, clk)
    feed(br, clk)
    assert [w["side"] for w in port.would_send] == ["buy"] and br.state.orders["o1"]["status"] == "open" and br.fs["exits_not_followed"] == {"sl_5pct": 1}


class DeepRedPort(B.DryPort):
    def position_return(self, mint):
        return -0.9


def test_no_stop_loss_backstop_the_bridge_default_is_replaced(tmp_path):
    clk = Clock()
    lg = Log(tmp_path)
    lg.start(H0)
    lg.entry(H0 + 1000, "o1")
    br, port = bridge(tmp_path, clk, port=DeepRedPort(600_000_000))
    feed(br, clk)
    clk.t = H0 + 3_600_000
    for _ in range(3):
        br.tick()
    assert [w["side"] for w in port.would_send] == ["buy"] and br.state.orders["o1"]["status"] == "open"  # -90 % on the warm quote sells nothing
    plain = B.Bridge(F.make_config(cfg_dict(tmp_path, state_dir=str(tmp_path / "plain"))), DeepRedPort(600_000_000), now_ms=clk)
    plain.state.orders["x"] = {"order_id": "x", "mint": "MX", "status": "open", "t_open_ms": clk.t - 10, "live": False, "stake_lamports": 1, "decision_ms": 0}
    plain.backstops()
    assert plain.state.orders["x"]["status"] != "open"  # the unpatched bridge WOULD have sold: the override matters


# ------------------------------------------------------------------------------------------------ caps and fail-closed refusals
def test_max_open_two_skips_are_logged_in_decision_order(tmp_path):
    clk = Clock()
    lg = Log(tmp_path)
    lg.start(H0)
    for i, p in enumerate("ABCD"):
        lg.entry(H0 + 1000 + i, f"o{i}", pool=p)
    br, port = bridge(tmp_path, clk)
    feed(br, clk)
    assert [w["order_id"] for w in port.would_send] == ["o0", "o1"] and br.state.counts["refused_max_open"] == 2  # first in decision order, no selection
    log = [json.loads(x) for x in Path(br.log_path).read_text().splitlines()]
    assert [x["order_id"] for x in log if x["kind"] == "entry_refused"] == ["o2", "o3"]


@pytest.mark.parametrize("kw,why", [({"stake_sol": 0.5}, "paper_stake_pin"), ({"v": 25 * SOLX}, "v_band"), ({"v": None}, "v_band"), ({"age_s": 3600}, "age_pin"),
                                    ({"hold_s": 1.0}, "meta_pin"), ({"tp": 2.0}, "meta_pin"), ({"rule": "x"}, "meta_pin"), ({"cell": "y"}, "meta_pin")])
def test_decision_row_pins_refuse(tmp_path, kw, why):
    clk = Clock()
    lg = Log(tmp_path)
    lg.start(H0)
    lg.entry(H0 + 1000, "o1", **kw)
    br, port = bridge(tmp_path, clk)
    feed(br, clk)
    assert port.would_send == [] and br.state.counts.get("refused_" + why) == 1


def test_stale_decision_missing_start_and_files_refuse(tmp_path):
    clk = Clock()
    lg = Log(tmp_path)
    lg.entry(H0 + 1000, "o0")  # no start row yet
    lg.start(H0 + 2000)
    lg.entry(H0 + 3000, "o1")
    br, port = bridge(tmp_path, clk)
    rows = br.read_new_rows()
    clk.t = H0 + 3000 + 60_000  # a minute late
    for r in rows:
        br.handle_row(r)
    assert port.would_send == [] and br.state.counts.get("refused_no_start_row") == 1 and br.state.counts.get("refused_stale_decision") == 1
    stop = tmp_path / "STOP"
    stop.write_text("")
    lg.entry(H0 + 4000, "o2")
    br2, port2 = bridge(tmp_path, clk, stop_file=str(stop), state_dir=str(tmp_path / "s2"))
    clk.t = H0 + 4100
    for r in br2.read_new_rows():
        br2.handle_row(r)
    assert port2.would_send == [] and br2.state.counts.get("refused_stop_file", 0) >= 1


def test_wallet_floor_and_committed_loss_refuse(tmp_path):
    clk = Clock()
    lg = Log(tmp_path)
    lg.start(H0)
    lg.entry(H0 + 1000, "o1")
    br, port = bridge(tmp_path, clk, dry_balance_lamports=300_000_000, funded_lamports=350_000_000)  # floor 100M; need 50M+0.16M+2.1M+1.6M+100M > 300M? no: 153.9M <= 300M
    feed(br, clk)
    assert [w["side"] for w in port.would_send] == ["buy"]
    lg.entry(H0 + 2000, "o2", pool="Q")
    lg.entry(H0 + 3000, "o3", pool="R")
    br2, port2 = bridge(tmp_path, clk, dry_balance_lamports=150_000_000, funded_lamports=350_000_000, state_dir=str(tmp_path / "s2"))
    feed(br2, clk)
    assert port2.would_send == [] and br2.state.counts.get("refused_wallet_floor") >= 1
    # committed loss: with realized loss on the books the next buy is refused before the wallet floor is looked at
    br3, port3 = bridge(tmp_path, clk, state_dir=str(tmp_path / "s3"), total_loss_lamports=100_000_000)
    br3.state.realized_lamports = -60_000_000
    feed(br3, clk)
    assert port3.would_send == [] and br3.state.counts.get("refused_committed_loss_stop") >= 1


def test_total_loss_cap_and_canary_expiry(tmp_path):
    clk = Clock()
    lg = Log(tmp_path)
    lg.start(H0)
    lg.entry(H0 + 1000, "o1")
    br, port = bridge(tmp_path, clk)
    feed(br, clk)
    lg.entry(H0 + 15 * 86_400_000, "o2", pool="Q")
    clk.t = H0 + 15 * 86_400_000 + 50
    for r in br.read_new_rows():
        br.handle_row(r)
    assert br.state.counts.get("refused_canary_expired") == 1 and [w["order_id"] for w in port.would_send] == ["o1"]


def test_halt_file_freezes_reads(tmp_path):
    clk = Clock()
    halt = tmp_path / "HALT"
    halt.write_text("")
    lg = Log(tmp_path)
    lg.start(H0)
    lg.entry(H0 + 1000, "o1")
    br, port = bridge(tmp_path, clk, halt_file=str(halt))
    clk.t = H0 + 1100
    assert br.tick() == 0 and port.would_send == []
