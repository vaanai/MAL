"""r4-b dry executor: pins, C0 caps in rank order, no stop loss and the 49 h time stop, re-entry, exits the canary does not hold, the sd(T) latency origin,
the P5 latch, the r4-b paper twin, and decision equivalence on a replayed synthetic walker dir. SYNTHETIC data only (tmp dirs); nothing here reads /data/mal.
Nothing here says r4-b makes money; no price, fill or P&L is produced by the executor.

The replay tests need duckdb and lightgbm (as tools/test_r4b_live.py):
  PYTHONPATH=/data/mal/audit-1008/venv/lib/python3.12/site-packages /data/mal/venv/bin/python -m pytest tools/test_r4b_arena_executor.py
"""

from __future__ import annotations

import ast
import collections
import inspect
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest

from tools import arena_live_bridge as B
from tools import r4b_arena_executor as X
from tools import r4b_book as BK
from tools import r4b_frozen as FZ
from tools.arena_declog import DecisionLog, utc_day

SHA40 = "ab" * 20
BT0 = int(datetime(2026, 8, 14, tzinfo=timezone.utc).timestamp())
TM0 = BT0 + 2 * 3600
V = 17_600_000_000
SD0 = 438_000_000


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setattr("tools.arena_paper.shutil.disk_usage", lambda path: collections.namedtuple("Usage", "total used free")(1000, 500, 500))


class Clock:
    def __init__(self, t=0):
        self.t = t

    def __call__(self):
        return self.t


def mark(i):
    return TM0 + 3600 * i


def cfg_dict(tmp, **kw):
    d = {"root": str(tmp / "rules"), "state_dir": str(tmp / "state"), "frozen_sha": SHA40, "end_ms": (BT0 + 400 * 86400) * 1000, "slot_s": 0.4}
    d.update(kw)
    return d


class Log:
    """Hand-written decision log in the r4b_live format (tools/r4b_live.log_actions): exits first, then entries in rank order."""

    def __init__(self, root):
        self.d = DecisionLog(str(root / "rules"))

    def start(self, ms, sha=SHA40):
        self.d.append(X.BOT_ID, ms, {"kind": "start", "book": "r4-b", "exp": "EXP-027", "mode": "dry", "schema": "r4b_live_v1", "frozen_sha": sha})

    def entry(self, i, mint, rank_, dt=1200, meta=None, **over):
        tm = mark(i)
        body = {"kind": "entry", "order_id": f"{mint}:{tm}", "pool": "PDA_" + mint, "meta": {"mint": mint, "v0": V, "mbt": tm - 7200, "s0": 1}, "stake_sol": 0.25,
                "decision_slot": SD0 + 9000 * i, "t_mark": tm, "rank": rank_, "score": 0.5}
        if meta:
            body["meta"] = {**body["meta"], **meta}
        body.update(over)
        self.d.append(X.BOT_ID, tm * 1000 + dt, body)

    def exit(self, i, mint, t0_i, reason="rank", dt=1100):
        self.d.append(X.BOT_ID, mark(i) * 1000 + dt, {"kind": "exit", "order_id": f"{mint}:{mark(t0_i)}", "mint": mint, "reason": reason, "t_mark": mark(i)})

    def restart_exit(self, at_ms, mint, t0_i, t_mark_i):
        """The row tools.r4b_live.restart_exits writes: reason restart, decision_slot None, t_mark the mark at or before the restart."""
        self.d.append(X.BOT_ID, at_ms, {"kind": "exit", "order_id": f"{mint}:{mark(t0_i)}", "mint": mint, "reason": "restart", "t_mark": mark(t_mark_i), "decision_slot": None})

    def gap(self, at_ms, kind="data_loss", frm=None, to=None):
        """The row tools.r4b_live.log_gap writes."""
        self.d.append(X.BOT_ID, at_ms, {"kind": "gap", "gap_kind": kind, "from_ms": frm, "to_ms": to, "data_loss": True})


def ms(i, dt=1200):
    return mark(i) * 1000 + dt


class SlotPort(B.DryPort):
    """DryPort stub that lands every order `lag` slots after the decision slot, so the landed latency is measurable."""

    def __init__(self, bal=600_000_000, buy_lag=8, sell_lag=8):
        super().__init__(bal)
        self.buy_lag, self.sell_lag = buy_lag, sell_lag

    def buy(self, order):
        ok, why = super().buy(order)
        self._events[-1]["landed_slot"] = order["decision_slot"] + self.buy_lag
        return ok, why

    def sell(self, order, reason):
        ok, why = super().sell(order, reason)
        ds = order.get("exit_decision_slot")
        self._events[-1]["landed_slot"] = ds + self.sell_lag if isinstance(ds, int) else None
        return ok, why


class HoldSellPort(B.DryPort):
    """A sell stays in flight until release()."""

    def __init__(self, bal=600_000_000):
        super().__init__(bal)
        self.held = []

    def sell(self, order, reason):
        self.would_send.append({"side": "sell", "order_id": order["order_id"], "mint": order["mint"], "reason": reason})
        self.held.append({"order_id": order["order_id"], "event": "sell_landed", "pnl_lamports": 0, "landed_slot": None, "dry": True})
        return True, None

    def release(self):
        self._events += self.held
        self.held = []


class FailBuyPort(B.DryPort):
    def buy(self, order):
        self.would_send.append({"side": "buy", "order_id": order["order_id"], "mint": order["mint"], "stake_lamports": order["stake_lamports"]})
        self._events.append({"order_id": order["order_id"], "event": "buy_failed", "pnl_lamports": -505_000, "label": "expired"})
        return True, None


class RefuseBuyPort(B.DryPort):
    def buy(self, order):
        return False, "port_says_no"


class DeepRedPort(B.DryPort):
    def position_return(self, mint):
        return -0.9


def bridge(tmp, clk, port=None, **kw):
    cfg = X.make_config(cfg_dict(tmp, **kw))
    port = port or B.DryPort(cfg.dry_balance_lamports)
    return X.R4bBridge(cfg, port, now_ms=clk), port


def feed(br, clk, at=None):
    rows = br.read_new_rows()
    for r in rows:
        clk.t = r["ms"] + 50 if at is None else at
        br.handle_row(r)
        br._poll_ports()
    br._save()
    return rows


def sends(port):
    return [(w["side"], w["order_id"]) for w in port.would_send]


def oid(mint, i):
    return f"{mint}:{mark(i)}"


# ------------------------------------------------------------------------------------------------ pins
def test_constants_follow_the_book_and_the_dec():
    assert X.BOT_ID == "r4b-exp027" and X.LIVE_BUILT is False
    assert X.C0 == {"stake_lamports": 50_000_000, "max_open": 2, "max_trades_per_day": 30, "daily_loss_lamports": 100_000_000, "total_loss_lamports": 200_000_000}
    assert X.PAPER_STAKE_SOL == 0.25 and X.K == 10 and X.MARK_S == 3600
    assert X.FOLLOWED_EXIT_REASONS == ("rank", "gap", "end", "restart") and X.REASON_RESTART == "restart"
    assert X.MAX_HOLD_MS == 49 * 3_600_000 and X.TIME_STOP_MS == 49 * 3_600_000 + 60_000  # 48 h + 1 h + 60 s
    assert X.SEND_FEE_LAMPORTS == 505_000 == FZ.SEND_FEE_DECIDING and X.SELL_RESERVE_LAMPORTS == 10 * 505_000
    assert (X.P5_WINDOW, X.P5_SOFT_HALT_S, X.MATCH_P50_S, X.TWIN_LEG_S) == (20, 3.0, 1.9, 1.9)
    assert 1.9 in FZ.LATENCIES_S  # the twin leg is one of the book's registered legs


def test_constants_equal_the_pinned_modules_where_they_overlap():
    lv = pytest.importorskip("tools.r4b_live")
    rl = pytest.importorskip("tools.r4b_layout")
    assert X.BOT_ID == lv.BOT_ID and X.BOOK == lv.BOOK and X.MARK_S == lv.MARK_S and X.REASON_RESTART == lv.RESTART_REASON
    assert (X.V_LO, X.V_HI) == (rl.V_LO, rl.V_HI) and (X.GRID_LO_S, X.GRID_HI_S) == (lv.GRID_LO_S, lv.GRID_HI_S)


def test_constants_equal_the_floor_executor_where_they_overlap():
    F = pytest.importorskip("tools.floor_arena_executor")
    assert F.C0 == X.C0 and (F.FUNDED_MIN_LAMPORTS, F.FUNDED_MAX_LAMPORTS) == (X.FUNDED_MIN_LAMPORTS, X.FUNDED_MAX_LAMPORTS)
    assert F.CANARY_MAX_MS == X.CANARY_MAX_MS and F.GAP_HOLD_MS == X.GAP_HOLD_MS and F.SELL_RESERVE_SENDS == X.SELL_RESERVE_SENDS
    for funded in (None, 350_000_000, 600_000_000, 1_000_000_000):
        assert F.wallet_floor_lamports(funded, 0) == X.wallet_floor_lamports(funded, 0)


@pytest.mark.parametrize("bad", [{"mode": "live"}, {"bot_id": "h5v1"}, {"frozen_sha": "ab" * 4}, {"frozen_sha": "AB" * 20}, {"frozen_sha": None}, {"max_hold_ms": 86_400_000},
                                 {"sl_backstop": 0.3}, {"sl_backstop": 0.6}, {"end_ms": None}, {"end_ms": True}, {"slot_s": None}, {"slot_s": 0}, {"slot_s": 5.0},
                                 {"funded_lamports": 100_000_000}, {"funded_lamports": 2_000_000_000}, {"stake_lamports": 0}, {"root": ""}, {"per_trade_loss_lamports": -1}])
def test_config_refusals(tmp_path, bad):
    with pytest.raises(ValueError):
        X.make_config(cfg_dict(tmp_path, **bad))


def test_missing_frozen_sha_and_slot_s_refuse(tmp_path):
    for k in ("frozen_sha", "slot_s", "end_ms"):
        d = cfg_dict(tmp_path)
        del d[k]
        with pytest.raises(ValueError):
            X.make_config(d)


def test_config_only_tightens(tmp_path):
    c = X.make_config(cfg_dict(tmp_path, stake_lamports=900_000_000, max_open=9, max_trades_per_day=99, daily_loss_lamports=10 ** 9, total_loss_lamports=10 ** 9,
                               funded_lamports=600_000_000))
    assert (c.stake_lamports, c.max_open, c.max_trades_per_day, c.daily_loss_lamports, c.total_loss_lamports) == tuple(X.C0.values())
    assert c.wallet_floor_lamports == 350_000_000 and c.max_hold_ms == X.MAX_HOLD_MS and c.mode == B.DRY and c.slot_s == 0.4
    assert c.per_trade_loss_lamports == 50_000_000  # no stop: the whole stake by default
    c2 = X.make_config(cfg_dict(tmp_path, stake_lamports=20_000_000, max_open=1, per_trade_loss_lamports=5_000_000))
    assert c2.stake_lamports == 20_000_000 and c2.max_open == 1 and c2.per_trade_loss_lamports == 5_000_000
    assert X.make_config(cfg_dict(tmp_path, per_trade_loss_lamports=10 ** 12)).per_trade_loss_lamports == 50_000_000


def test_a_live_port_or_live_config_cannot_start(tmp_path):
    cfg = X.make_config(cfg_dict(tmp_path))
    live_port = B.DryPort(1)
    live_port.live = True
    with pytest.raises(SystemExit):
        X.R4bBridge(cfg, live_port, now_ms=Clock())
    with pytest.raises(SystemExit):
        X.R4bBridge(X.R4bConfig(**{**cfg.__dict__, "mode": B.LIVE}), B.DryPort(1), now_ms=Clock())
    with pytest.raises(SystemExit):
        X.R4bBridge(X.R4bConfig(**{**cfg.__dict__, "max_hold_ms": 1}), B.DryPort(1), now_ms=Clock())
    with pytest.raises(SystemExit):
        X.R4bBridge(X.R4bConfig(**{**cfg.__dict__, "slot_s": 0.0}), B.DryPort(1), now_ms=Clock())
    with pytest.raises(SystemExit):
        X.R4bBridge(B.Config(**{k: v for k, v in cfg.__dict__.items() if k != "slot_s"}), B.DryPort(1), now_ms=Clock())  # a plain bridge Config is not the book's
    with pytest.raises(SystemExit):
        X.R4bBridge(X.R4bConfig(**{**cfg.__dict__, "frozen_sha": "5e12f8f5"}), B.DryPort(1), now_ms=Clock())


def test_no_key_wallet_rpc_or_send_path_in_the_module():
    src = Path(X.__file__).read_text()
    tree = ast.parse(src)
    mods = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            mods |= {a.name for a in n.names}
        elif isinstance(n, ast.ImportFrom):
            mods |= {f"tools.{a.name}" for a in n.names} if n.module == "tools" else {n.module}
    assert mods <= {"__future__", "argparse", "hashlib", "json", "re", "statistics", "sys", "dataclasses", "datetime", "pathlib", "typing", "tools.arena_live_bridge", "tools.r4b_book",
                    "tools.r4b_frozen", "tools.arena_declog", "tools.arena_paper"}, mods
    low = src.lower()
    for bad in ("keypair", "load_probe_key", "make_live_port", "helius", "sendtransaction", "solders", ".env", "private_key", "/etc/mal-arena-live", "urllib", "socket", "requests"):
        assert bad not in low, bad


# ------------------------------------------------------------------------------------------------ entry, exit, re-entry
def test_entry_and_rank_exit_are_bought_and_sold_with_the_c0_stake(tmp_path):
    clk = Clock()
    lg = Log(tmp_path)
    lg.start(ms(0, 0))
    lg.entry(0, "MA", 0)
    lg.exit(1, "MA", 0)
    br, port = bridge(tmp_path, clk)
    feed(br, clk)
    assert sends(port) == [("buy", oid("MA", 0)), ("sell", oid("MA", 0))]
    assert port.would_send[0]["stake_lamports"] == 50_000_000 and br.state.orders[oid("MA", 0)]["status"] == "closed"
    assert br.state.orders[oid("MA", 0)]["decision_slot"] == SD0 and br.es["first_send_ms"] is not None


@pytest.mark.parametrize("reason", ["rank", "gap", "end", "restart"])
def test_each_book_exit_reason_is_followed(tmp_path, reason):
    clk = Clock()
    lg = Log(tmp_path)
    lg.start(ms(0, 0))
    lg.entry(0, "MA", 0)
    lg.exit(1, "MA", 0, reason=reason)
    br, port = bridge(tmp_path, clk)
    feed(br, clk)
    assert [s for s, _ in sends(port)] == ["buy", "sell"] and port.would_send[1]["reason"] == reason


def test_an_unknown_exit_reason_is_not_followed_and_the_position_stays_open(tmp_path):
    clk = Clock()
    lg = Log(tmp_path)
    lg.start(ms(0, 0))
    lg.entry(0, "MA", 0)
    lg.exit(1, "MA", 0, reason="sl_5pct")  # the book has no stop
    br, port = bridge(tmp_path, clk)
    feed(br, clk)
    assert sends(port) == [("buy", oid("MA", 0))] and br.state.orders[oid("MA", 0)]["status"] == "open" and br.es["exits_not_followed"] == {"sl_5pct": 1}


def test_re_entry_after_the_sell_landed_but_not_before(tmp_path):
    clk = Clock()
    lg = Log(tmp_path)
    lg.start(ms(0, 0))
    lg.entry(0, "MA", 0)
    lg.exit(1, "MA", 0)  # sell sent, not landed yet (HoldSellPort)
    lg.entry(1, "MA", 3, dt=1300)  # the mint is back in the top 10 at the same mark: the engine never re-buys at the mark it sold, but a log may say so
    port = HoldSellPort()
    br, _ = bridge(tmp_path, clk, port=port)
    feed(br, clk)
    assert sends(port) == [("buy", oid("MA", 0)), ("sell", oid("MA", 0))] and br.state.counts.get("refused_already_bought") == 1
    port.release()
    br._poll_ports()
    assert br.state.orders[oid("MA", 0)]["status"] == "closed" and "MA" not in br.state.bought  # X7: released once the sell landed
    lg.entry(2, "MA", 1)  # a later mark
    feed(br, clk)
    assert sends(port)[-1] == ("buy", oid("MA", 2)) and br.state.orders[oid("MA", 2)]["status"] == "open"


def test_a_failed_or_refused_buy_releases_the_mint(tmp_path):
    clk = Clock()
    lg = Log(tmp_path)
    lg.start(ms(0, 0))
    lg.entry(0, "MA", 0)
    lg.entry(1, "MA", 0)
    br, port = bridge(tmp_path, clk, port=FailBuyPort(600_000_000))
    feed(br, clk)
    assert [o["status"] for o in br.state.orders.values()] == ["closed", "closed"] and br.state.counts.get("refused_already_bought") is None
    assert br.state.realized_lamports == -1_010_000 and br.state.bought == []
    clk2 = Clock()
    br2, port2 = bridge(tmp_path, clk2, port=RefuseBuyPort(600_000_000), state_dir=str(tmp_path / "s2"))
    feed(br2, clk2)
    assert [o["status"] for o in br2.state.orders.values()] == ["refused", "refused"] and br2.state.bought == [] and br2.state.counts["port_refused_buy"] == 2


def test_exit_rows_for_positions_the_canary_does_not_hold_are_ignored_and_counted(tmp_path):
    clk = Clock()
    lg = Log(tmp_path)
    lg.start(ms(0, 0))
    for r, m in enumerate(("MA", "MB", "MC")):
        lg.entry(0, m, r, dt=1200 + r)
    lg.exit(1, "MC", 0)  # MC was capped out (max open 2): the canary never held it
    lg.exit(1, "ZZ", 0)  # never an entry in this log
    lg.exit(1, "MA", 0)
    lg.exit(1, "MA", 0, dt=1200)  # a second exit row for an order already sold
    br, port = bridge(tmp_path, clk)
    feed(br, clk)
    assert sends(port) == [("buy", oid("MA", 0)), ("buy", oid("MB", 0)), ("sell", oid("MA", 0))]
    assert br.es["exits_not_held"] == {"unknown_order": 2, "closed": 1} and br.state.counts["exit_not_held"] == 3 and not br.state.latches
    assert br.state.orders[oid("MB", 0)]["status"] == "open"


# ------------------------------------------------------------------------------------------------ caps in rank order
def test_max_open_two_takes_ranks_in_order_and_logs_every_skip(tmp_path):
    clk = Clock()
    lg = Log(tmp_path)
    lg.start(ms(0, 0))
    for r, m in enumerate(("MA", "MB", "MC", "MD")):
        lg.entry(0, m, r, dt=1200 + r)
    br, port = bridge(tmp_path, clk)
    feed(br, clk)
    assert [o for _, o in sends(port)] == [oid("MA", 0), oid("MB", 0)] and br.state.counts["refused_max_open"] == 2
    log = [json.loads(x) for x in Path(br.log_path).read_text().splitlines()]
    skips = [(x["order_id"], x["why"]) for x in log if x["kind"] == "entry_refused"]
    assert skips == [(oid("MC", 0), "max_open"), (oid("MD", 0), "max_open")]


def test_rank_must_rise_within_a_mark(tmp_path):
    clk = Clock()
    lg = Log(tmp_path)
    lg.start(ms(0, 0))
    lg.entry(0, "MA", 3)
    lg.entry(0, "MB", 1, dt=1201)  # rank falls within the mark
    lg.entry(0, "MC", 3, dt=1202)  # rank repeats
    br, port = bridge(tmp_path, clk)
    feed(br, clk)
    assert sends(port) == [("buy", oid("MA", 0))] and br.state.counts["refused_rank_order"] == 2
    lg.entry(1, "MD", 0)  # a new mark starts the ranks again
    feed(br, clk)
    assert sends(port)[-1] == ("buy", oid("MD", 1))


def test_daily_buy_cap_daily_stop_total_stop_and_expiry(tmp_path):
    clk = Clock()
    lg = Log(tmp_path)
    lg.start(ms(0, 0))
    for i in range(4):
        lg.entry(i, f"M{i}", 0)
        lg.exit(i, f"M{i}", i, reason="end", dt=1300)
    br, port = bridge(tmp_path, clk, max_trades_per_day=3)
    feed(br, clk)
    assert [s for s, _ in sends(port)].count("buy") == 3 and br.state.counts["refused_max_trades_per_day"] == 1
    # daily stop and total stop
    lg2 = Log(tmp_path / "b")
    lg2.start(ms(0, 0))
    lg2.entry(0, "MA", 0)
    br2, port2 = bridge(tmp_path / "b", clk)
    br2.state.days[utc_day(ms(0))] = {"trades": 0, "realized": -100_000_000}
    feed(br2, clk)
    assert port2.would_send == [] and br2.state.counts["refused_daily_loss_cap"] == 1
    br3, port3 = bridge(tmp_path / "b", clk, state_dir=str(tmp_path / "s3"))
    br3.state.realized_lamports = -200_000_000
    feed(br3, clk)
    assert port3.would_send == [] and br3.state.counts["refused_total_loss_cap"] == 1
    # expiry: 14 days after the first buy attempt
    lg4 = Log(tmp_path / "c")
    lg4.start(ms(0, 0))
    lg4.entry(0, "MA", 0)
    br4, port4 = bridge(tmp_path / "c", clk)
    feed(br4, clk)
    lg4.entry(14 * 24 + 1, "MB", 0)
    feed(br4, clk)
    assert br4.state.counts.get("refused_canary_expired") == 1 and [o for _, o in sends(port4)] == [oid("MA", 0)]


def test_wallet_floor_and_committed_loss_refuse(tmp_path):
    clk = Clock()
    lg = Log(tmp_path)
    lg.start(ms(0, 0))
    lg.entry(0, "MA", 0)
    lg.entry(0, "MB", 1, dt=1201)
    # need for one buy: 50M + 0.505M + 2.1M + 5.05M + floor (0.35 SOL funded -> 0.10 SOL) = 157.655M
    br, port = bridge(tmp_path, clk, dry_balance_lamports=160_000_000, funded_lamports=350_000_000)
    feed(br, clk)
    assert [o for _, o in sends(port)] == [oid("MA", 0)] and br.state.counts["refused_wallet_floor"] == 1  # the second needs more than the wallet holds
    br2, port2 = bridge(tmp_path, clk, dry_balance_lamports=150_000_000, funded_lamports=350_000_000, state_dir=str(tmp_path / "s2"))
    feed(br2, clk)
    assert port2.would_send == [] and br2.state.counts["refused_wallet_floor"] == 2
    br3, port3 = bridge(tmp_path, clk, state_dir=str(tmp_path / "s3"), total_loss_lamports=100_000_000)
    br3.state.realized_lamports = -60_000_000
    feed(br3, clk)
    assert port3.would_send == [] and br3.state.counts["refused_committed_loss_stop"] == 2


@pytest.mark.parametrize("over,why", [({"stake_sol": 0.5}, "paper_stake_pin"), ({"rank": 10}, "rank_pin"), ({"rank": -1}, "rank_pin"), ({"rank": True}, "rank_pin"),
                                      ({"decision_slot": None}, "decision_slot_missing"), ({"decision_slot": True}, "decision_slot_missing"),
                                      ({"order_id": "MA:1"}, "order_id_pin"), ({"pool": None}, "no_pool_or_mint"), ({"pool": ""}, "no_pool_or_mint"),
                                      ({"t_mark": mark(0) + 60, "order_id": f"MA:{mark(0) + 60}"}, "t_mark_pin"),
                                      ({"meta": {"v0": 25 * 10 ** 9}}, "v_band"), ({"meta": {"v0": None}}, "v_band"), ({"meta": {"v0": 17_499_999_999}}, "v_band"),
                                      ({"meta": {"mbt": mark(0) - 599}}, "age_pin"), ({"meta": {"mbt": mark(0) - 48 * 3600 - 1}}, "age_pin"), ({"meta": {"mbt": None}}, "age_pin")])
def test_decision_row_pins_refuse(tmp_path, over, why):
    clk = Clock()
    lg = Log(tmp_path)
    lg.start(ms(0, 0))
    lg.entry(0, "MA", 0, **over)
    br, port = bridge(tmp_path, clk)
    feed(br, clk)
    assert port.would_send == [] and br.state.counts.get("refused_" + why) == 1, br.state.counts


def test_pin_edges_that_must_pass(tmp_path):
    clk = Clock()
    lg = Log(tmp_path)
    lg.start(ms(0, 0))
    lg.entry(0, "MA", 0, meta={"mbt": mark(0) - 600})  # exactly 10 min after graduation
    lg.entry(0, "MB", 1, dt=1201, meta={"mbt": mark(0) - 48 * 3600, "v0": 17_500_000_000})  # exactly 48 h, V at the band edge
    br, port = bridge(tmp_path, clk)
    feed(br, clk)
    assert len(port.would_send) == 2


def test_frozen_sha_mismatch_and_a_start_row_without_one_latch(tmp_path):
    for k, sha in enumerate(("cd" * 20, None)):
        clk = Clock()
        lg = Log(tmp_path / str(k))
        if sha is None:
            lg.d.append(X.BOT_ID, ms(0, 0), {"kind": "start", "book": "r4-b", "mode": "replay"})  # the shape tools.r4b_live.replay_walker writes
        else:
            lg.start(ms(0, 0), sha=sha)
        lg.entry(0, "MA", 0)
        br, port = bridge(tmp_path / str(k), clk)
        feed(br, clk)
        assert "frozen_sha_mismatch" in br.state.latches and port.would_send == []


def test_entry_before_a_start_row_is_refused_and_stale_and_files_refuse(tmp_path):
    clk = Clock()
    lg = Log(tmp_path)
    lg.entry(0, "M0", 0)
    lg.start(ms(1, 0))
    lg.entry(1, "M1", 0)
    br, port = bridge(tmp_path, clk)
    rows = br.read_new_rows()
    clk.t = ms(1) + 60_000
    for r in rows:
        br.handle_row(r)
    assert port.would_send == [] and br.state.counts.get("refused_no_start_row") == 1 and br.state.counts.get("refused_stale_decision") == 1
    stop = tmp_path / "STOP"
    stop.write_text("")
    lg.entry(2, "M2", 0)
    br2, port2 = bridge(tmp_path, clk, stop_file=str(stop), state_dir=str(tmp_path / "s2"))
    clk.t = ms(2) + 100
    for r in br2.read_new_rows():
        br2.handle_row(r)
    assert port2.would_send == [] and br2.state.counts.get("refused_stop_file", 0) >= 1


def test_halt_file_freezes_reads(tmp_path):
    clk = Clock()
    halt = tmp_path / "HALT"
    halt.write_text("")
    lg = Log(tmp_path)
    lg.start(ms(0, 0))
    lg.entry(0, "MA", 0)
    br, port = bridge(tmp_path, clk, halt_file=str(halt))
    clk.t = ms(0) + 100
    assert br.tick() == 0 and port.would_send == []


def test_a_decision_host_restart_holds_buys_ten_minutes_and_marks_open_positions_gap(tmp_path):
    clk = Clock()
    lg = Log(tmp_path)
    lg.start(ms(0, 0))
    lg.entry(0, "MA", 0)
    lg.start(ms(1, 0))  # the engine restarted: it starts flat, MA gets no exit row from it
    lg.entry(1, "MB", 0, dt=5 * 60_000)  # inside the 10 minute hold
    lg.entry(1, "MC", 1, dt=11 * 60_000)  # after it
    br, port = bridge(tmp_path, clk)
    feed(br, clk)
    assert [o for _, o in sends(port)] == [oid("MA", 0), oid("MC", 1)] and br.state.counts["refused_gap_hold"] == 1
    assert br.state.orders[oid("MA", 0)]["gap"] is True and br.es["gaps"][0]["why"] == "host_restart"


# ------------------------------------------------------------------------------------------------ X6: no stop loss, only the 49 h time stop
def test_no_stop_loss_backstop_the_bridge_default_is_replaced(tmp_path):
    clk = Clock()
    lg = Log(tmp_path)
    lg.start(ms(0, 0))
    lg.entry(0, "MA", 0)
    br, port = bridge(tmp_path, clk, port=DeepRedPort(600_000_000))
    feed(br, clk)
    clk.t = ms(0) + 3_600_000
    for _ in range(3):
        br.tick()
    assert sends(port) == [("buy", oid("MA", 0))] and br.state.orders[oid("MA", 0)]["status"] == "open"  # -90 % on the warm quote sells nothing
    plain = B.Bridge(X.make_config(cfg_dict(tmp_path, state_dir=str(tmp_path / "plain"))), DeepRedPort(600_000_000), now_ms=clk)
    plain.state.orders["x"] = {"order_id": "x", "mint": "MX", "status": "open", "t_open_ms": clk.t - 10, "live": False, "stake_lamports": 1, "decision_ms": 0}
    plain.backstops()
    assert plain.state.orders["x"]["status"] != "open"  # the unpatched bridge WOULD have sold: the override matters


def test_time_stop_counts_from_the_entry_decision_and_survives_a_restart(tmp_path):
    clk = Clock()
    lg = Log(tmp_path)
    lg.start(ms(0, 0))
    lg.entry(0, "MA", 0)
    br, port = bridge(tmp_path, clk)
    feed(br, clk)
    dec = br.state.orders[oid("MA", 0)]["decision_ms"]
    assert dec == ms(0) and br.state.orders[oid("MA", 0)]["t_open_ms"] == ms(0) + 50  # the landing is later than the decision: the stop does not use it
    clk.t = dec + 10 * 3_600_000
    br2, port2 = bridge(tmp_path, clk)  # a restart over the same state
    assert feed(br2, clk, at=clk.t) == [] and port2.would_send == []
    clk.t = dec + X.TIME_STOP_MS - 1
    br2.tick()
    assert port2.would_send == []
    clk.t = dec + X.TIME_STOP_MS  # 48 h + 1 h + 60 s after the entry decision
    br2.tick()
    br2.tick()
    assert sends(port2) == [("sell", oid("MA", 0))] and port2.would_send[0]["reason"] == "time_stop_backstop"
    assert br2.state.orders[oid("MA", 0)]["exit_decision_ms"] == dec + X.TIME_STOP_MS and br2.state.orders[oid("MA", 0)]["status"] == "closed" and br2.es["time_stops"] == 1


# ------------------------------------------------------------------------------------------------ X9: the latency origin is sd(T)
def test_orders_record_the_decision_slot_and_the_landed_latency_counts_from_it(tmp_path):
    clk = Clock()
    lg = Log(tmp_path)
    lg.start(ms(0, 0))
    lg.entry(0, "MA", 0, sd_seen_ms=mark(0) * 1000 + 100)  # the decision host saw slot sd(T) at T + 0.1 s; the row is written 1.2 s after T
    lg.exit(1, "MA", 0)  # the engine writes no decision_slot on exit rows
    lg.entry(1, "MB", 0, dt=1300)
    br, port = bridge(tmp_path, clk, port=SlotPort(buy_lag=10, sell_lag=6))
    feed(br, clk)
    a = br.state.orders[oid("MA", 0)]
    assert a["decision_slot"] == SD0 and a["buy_landed_slot"] == SD0 + 10 and a["sd_seen_ms"] == mark(0) * 1000 + 100
    assert a["exit_decision_slot"] == SD0 + 9000 and a["sell_landed_slot"] == SD0 + 9000 + 6  # sd of the exit mark, learnt from the entry row of the same mark
    rep = br.latency_report()["summary"]
    assert rep["landed_buys"] == 2 and rep["measured"] == 2 and rep["all_p50_s"] == pytest.approx(4.0) and rep["sells_p50_s"] == pytest.approx(2.4)
    assert rep["wall_measured"] == 1 and rep["wall_missing"] == 1  # a missing sd_seen_ms is counted, not replaced
    assert rep["wall_all_p50_s"] == pytest.approx((ms(0, 0) + 1250 - (mark(0) * 1000 + 100)) / 1000.0)  # the bridge's observation (row + 50 ms), from sd(T) seen
    log = [json.loads(x) for x in Path(br.log_path).read_text().splitlines()]
    assert [x["decision_to_landed_slots"] for x in log if x["kind"] == "buy_landed"] == [10, 10]


def test_latency_is_never_counted_from_the_row_or_the_send():
    orders = {"o1": {"decision_slot": 1000, "buy_landed_slot": 1010, "buy_landed_ms": 5_000_000, "decision_ms": 4_999_900, "send_ms": 4_999_950, "ms": 4_999_800}}
    r = X.landed_buy_latencies(orders, 0.4)
    assert r[0]["slot_s"] == pytest.approx(4.0) and r[0]["wall_s"] is None  # from sd(T) = 4.0 s; the 0.1 s from the row or 0.05 s from the send is not used
    assert X.slot_latency_s(1000, 1010, 0.2) == pytest.approx(2.0) and X.slot_latency_s(1000, 1000, 0.4) == 0.0
    for fn in (X.slot_latency_s, X.wall_latency_s, X.landed_buy_latencies, X.latency_summary, X.latency_check):
        src = inspect.getsource(fn)
        assert "send_ms" not in src and "decision_ms" not in src and '"ms"' not in src, fn.__name__


@pytest.mark.parametrize("ds,ls,slot_s", [(None, 10, 0.4), (10, None, 0.4), (True, 10, 0.4), (10, 9, 0.4), (10, 12, 0.0), (10, 12, None), (10.5, 12, 0.4)])
def test_unmeasurable_latency_is_none(ds, ls, slot_s):
    assert X.slot_latency_s(ds, ls, slot_s) is None


def test_wall_latency():
    assert X.wall_latency_s(1_000, 3_500) == 2.5 and X.wall_latency_s(None, 3_500) is None and X.wall_latency_s(4_000, 3_500) is None and X.wall_latency_s(True, 3) is None


def _landed(n, lag_slots, slot_s=0.4, gap_every=0):
    return {f"o{i:03d}": {"decision_slot": 1000 + i * 100, "buy_landed_slot": 1000 + i * 100 + lag_slots, "buy_landed_ms": 10_000 + i, "gap": bool(gap_every and i % gap_every == 0)}
            for i in range(n)}


def test_latency_summary_and_check_thresholds():
    # A8 / Erratum 1: decided in slots. At slot_s 0.4: k30 = round(7.5) = 8 (half to even), k19 = round(4.75) = 5.
    s = X.latency_summary(_landed(25, 9), 0.4)  # 9 slots > 8
    c = X.latency_check(s)
    assert s["landed_buys"] == 25 and s["window_n"] == 20 and s["window_x30_p50_slots"] == 1 and c["enough"] and c["p5_soft_halt"] and not c["match_latency_ok"]
    s = X.latency_summary(_landed(25, 8), 0.4)  # 8 slots = k30 (3.2 s in seconds, a diagnostic): not "above"
    c = X.latency_check(s)
    assert s["window_p50_s"] == pytest.approx(3.2) and not c["p5_soft_halt"] and not c["match_latency_ok"]
    s = X.latency_summary(_landed(25, 4), 0.4)
    c = X.latency_check(s)
    assert not c["p5_soft_halt"] and c["match_latency_ok"]
    s = X.latency_summary(_landed(25, 5), 0.4)  # 5 slots = k19 (2.0 s in seconds): matches in slots
    assert X.latency_check(s)["match_latency_ok"] and s["all_x19_p50_slots"] == 0
    assert not X.latency_check(X.latency_summary(_landed(25, 6), 0.4))["match_latency_ok"]
    s = X.latency_summary(_landed(19, 20), 0.4)  # 19 landed buys: not enough, no halt
    c = X.latency_check(s)
    assert not c["enough"] and not c["p5_soft_halt"] and not c["match_latency_ok"]
    # the rolling window follows the last 20, the match clause all of them
    o = {**_landed(30, 4), **{f"p{i:03d}": {"decision_slot": 5000 + i, "buy_landed_slot": 5000 + i + 9, "buy_landed_ms": 99_000 + i} for i in range(20)}}  # 20 slow buys at the end: 3.6 s
    s = X.latency_summary(o, 0.4)
    assert s["window_p50_s"] == pytest.approx(3.6) and s["all_p50_s"] == pytest.approx(1.6) and X.latency_check(s)["p5_soft_halt"]  # window 9 slots > 8
    edge = X.latency_summary(_landed(25, 6), 0.5)  # exactly 3.0 s: the halt is "above" 3.0 s
    assert edge["window_p50_s"] == 3.0 and not X.latency_check(edge)["p5_soft_halt"] and X.latency_check(X.latency_summary(_landed(25, 7), 0.5))["p5_soft_halt"]
    assert X.latency_summary(_landed(25, 8, gap_every=2), 0.4)["non_gap_p50_s"] == pytest.approx(3.2)
    s = X.latency_summary({}, 0.4)
    assert s["landed_buys"] == 0 and s["all_p50_s"] is None and not X.latency_check(s)["enough"]


def test_unmeasured_landings_do_not_count_toward_p5():
    o = {f"o{i}": {"decision_slot": 10, "buy_landed_slot": None, "buy_landed_ms": 1000 + i} for i in range(30)}  # the dry port: no landed slot
    s = X.latency_summary(o, 0.4)
    assert s["landed_buys"] == 30 and s["measured"] == 0 and s["unmeasured"] == 30 and not X.latency_check(s)["enough"]


def _pairs(tmp, n, port, **kw):
    clk = Clock()
    lg = Log(tmp)
    lg.start(ms(0, 0))
    for i in range(n):
        lg.entry(i, f"M{i:02d}", 0)
        lg.exit(i, f"M{i:02d}", i, reason="end", dt=1300)
    br, _ = bridge(tmp, clk, port=port, **kw)
    return br, clk, lg


def test_p5_latches_new_buys_at_the_20th_slow_landing_but_exits_keep_running(tmp_path):
    br, clk, lg = _pairs(tmp_path, 19, SlotPort(buy_lag=10))  # 4.0 s each
    feed(br, clk)
    assert br.latency_report()["summary"]["landed_buys"] == 19 and "p5_landing_latency" not in br.state.latches
    lg.entry(19, "M19", 0)  # the 20th landed buy trips P5 ...
    lg.entry(19, "M20", 1, dt=1300)  # ... so this one is not bought
    lg.exit(20, "M19", 19, reason="end")  # ... and the open position still exits
    feed(br, clk)
    assert "p5_landing_latency" in br.state.latches and "slots over round(3.0 / slot_s)" in br.state.latches["p5_landing_latency"]
    assert br.state.counts["refused_latched"] == 1 and oid("M20", 19) not in br.state.orders
    assert br.state.orders[oid("M19", 19)]["status"] == "closed"
    lg.entry(22, "M21", 0)
    feed(br, clk)
    assert br.state.counts["refused_latched"] == 2


def test_fast_landings_do_not_trip_p5_and_the_match_clause_reads_true(tmp_path):
    br, clk, _ = _pairs(tmp_path, 22, SlotPort(buy_lag=4))  # 1.6 s each
    feed(br, clk)
    rep = br.latency_report()
    assert not br.state.latches and rep["check"]["enough"] and rep["check"]["match_latency_ok"] and not rep["check"]["p5_soft_halt"]


def test_the_dry_port_has_no_landed_slot_so_p5_cannot_trip_and_says_so(tmp_path):
    br, clk, _ = _pairs(tmp_path, 22, B.DryPort(600_000_000))
    feed(br, clk)
    rep = br.latency_report()
    assert rep["summary"]["landed_buys"] == 22 and rep["summary"]["measured"] == 0 and not rep["check"]["enough"] and not br.state.latches


# ------------------------------------------------------------------------------------------------ N7: the r4-b paper twin
Q0, B0 = 100_000_000_000.0, 200_000_000_000_000.0
SPOT0 = Q0 / B0


def test_twin_equals_the_registered_round_trip_at_stake_s_and_the_executor_fee():
    st = 50_000_000
    t = X.twin_round_trip(Q0, B0, SPOT0, Q0 * 1.002, B0 * 0.998, stake_lamports=st, send_fee_lamports=505_000)
    pnl, filled, stranded = BK.round_trip(float(st), Q0, B0, SPOT0, Q0 * 1.002, B0 * 0.998, send=505_000.0, rent=0, return_stranded=True)
    assert t["pnl_lamports"] == float(pnl) and t["filled"] is True and t["stranded"] is False and t["bps_of_stake"] == pytest.approx(float(pnl) / st * 1e4)
    assert (t["leg_s"], t["fail_model"], t["e1"], t["stake_lamports"], t["send_fee_lamports"]) == (1.9, "nofail", "excluded", st, 505_000)


def test_twin_fee_enters_exactly_twice_and_defaults_to_the_executor_fee():
    st = 50_000_000
    a = X.twin_round_trip(Q0, B0, SPOT0, Q0, B0, stake_lamports=st, send_fee_lamports=505_000)
    b = X.twin_round_trip(Q0, B0, SPOT0, Q0, B0, stake_lamports=st, send_fee_lamports=160_000)
    assert b["pnl_lamports"] - a["pnl_lamports"] == pytest.approx(2 * (505_000 - 160_000))
    assert a["pnl_lamports"] < -2 * 505_000  # fixed cost, fee tier, haircuts and impact on a flat pool
    assert X.twin_round_trip(Q0, B0, SPOT0, Q0, B0, stake_lamports=st) == a
    s2 = X.twin_round_trip(Q0, B0, SPOT0, Q0, B0, stake_lamports=250_000_000, send_fee_lamports=505_000)
    assert s2["bps_of_stake"] > a["bps_of_stake"]  # the same fee is a smaller share of a larger stake


def test_twin_guard_strand_and_input_refusals():
    g = X.twin_round_trip(Q0, B0, SPOT0 * 0.5, Q0, B0, stake_lamports=50_000_000)  # the buy lands 2x above the decision spot: guard (> 1.15)
    assert g["filled"] is False and g["pnl_lamports"] == -505_000.0
    bad = X.twin_round_trip(Q0 / 100, B0, SPOT0, Q0, B0, stake_lamports=50_000_000)  # Qe below V_LO: an invalid entry state is priced as stranded
    assert bad["stranded"] is True and bad["pnl_lamports"] == -50_000_000 - 505_000 - FZ.RENT_STRANDED
    for kw in ({"stake_lamports": 0}, {"stake_lamports": 1.5}, {"stake_lamports": True}, {"stake_lamports": 5, "send_fee_lamports": -1}):
        with pytest.raises(ValueError):
            X.twin_round_trip(Q0, B0, SPOT0, Q0, B0, **kw)


def test_twin_entry_state_is_the_1_9_s_leg_of_mint_states():
    sl = np.arange(100, 112)
    q = np.linspace(85e9, 95e9, 12)
    b = np.linspace(206e12, 190e12, 12)
    kw = dict(sl=sl, isbuy=np.ones(12, bool), sol=np.full(12, 1e9), tok=np.full(12, 1e12), q=q, b=b, vq=17.6e9, sd=np.array([100]), slot_s_dec=0.4)
    S = BK.mint_states(**kw, lats=(1.3, 1.9))
    qe, be, spot = X.twin_entry_state(S, 0)
    assert qe == float(S["Qe"][1.9][0]) != float(S["Qe"][1.3][0]) and be == float(S["Be"][1.9][0]) and spot == float(S["spot"][0])
    with pytest.raises(ValueError):
        X.twin_entry_state(BK.mint_states(**kw, lats=(1.3,)), 0)
    t = X.twin_round_trip(qe, be, spot, qe * 1.01, be * 0.99, stake_lamports=50_000_000)
    assert t["leg_s"] == 1.9 and np.isfinite(t["pnl_lamports"])


# ------------------------------------------------------------------------------------------------ dry replay of a closed decision log
def copy_log(src, dst, frozen_sha=None):
    """Copy a closed r4b decision log row by row (chain fields rebuilt); frozen_sha, if given, replaces the start row's value (tests of the pin only)."""
    out = DecisionLog(dst)
    for r in DecisionLog(src).rows(X.BOT_ID):
        body = {k: v for k, v in r.items() if k not in ("seq", "arm", "ms", "prev", "sha")}
        if frozen_sha is not None and body["kind"] == "start":
            body["frozen_sha"] = frozen_sha
        out.append(X.BOT_ID, r["ms"], body)


@pytest.fixture(scope="module")
def replayed(tmp_path_factory):
    pytest.importorskip("duckdb")
    pytest.importorskip("lightgbm")
    lvt = pytest.importorskip("tools.test_r4b_live")
    lv = pytest.importorskip("tools.r4b_live")
    from tools import r4b_score as rs
    from tools.test_r4b_layout import make_walk

    tmp = tmp_path_factory.mktemp("r4bexec")
    make_walk(tmp / "walk", lvt.scenario())
    # the engine writes the start row's frozen_sha itself (#1130): no test-side stamping. "raw" carries another sha for the pin test.
    lv.replay_walker(tmp / "walk", lvt.H0, lvt.H1, lvt.W0, lvt.E, static_map=None, universe_min_mbt=lvt.BT0, boosters=rs.load_boosters(), pda_fn=lvt.pda_of, allow_dir=True,
                     log_root=tmp / "stamped", frozen_sha=SHA40)
    copy_log(tmp / "stamped", tmp / "raw", frozen_sha="ef" * 20)
    return tmp


def rcfg(replayed, name, root="stamped", **kw):
    return {"root": str(replayed / root), "state_dir": str(replayed / f"st-{name}"), "frozen_sha": SHA40, "end_ms": (BT0 + 400 * 86400) * 1000, "slot_s": 0.4, **kw}


def test_replay_with_the_capacity_gates_off_sends_exactly_the_logs_decisions(replayed):
    res = X.replay_equivalence(rcfg(replayed, "off"))
    assert res["ok"] and res["caps"] is False and res["entries"] > 10 and res["orders"] == res["decisions"] == 2 * res["entries"], res
    assert res["md5_orders"] == res["md5_decisions"] and not res["refused"] and not res["latches"] and not res["not_followed"] and not res["exits_not_held"]
    assert X.replay_equivalence(rcfg(replayed, "off2"))["md5_orders"] == res["md5_orders"]  # deterministic
    rows = DecisionLog(str(replayed / "stamped")).rows(X.BOT_ID)
    assert {r["reason"] for r in rows if r["kind"] == "exit"} >= {"rank", "end"}  # the scenario exercises the book's exits
    raw = [{k: v for k, v in r.items() if k not in ("seq", "arm", "ms", "prev", "sha", "frozen_sha")} for r in DecisionLog(str(replayed / "raw")).rows(X.BOT_ID)]
    new = [{k: v for k, v in r.items() if k not in ("seq", "arm", "ms", "prev", "sha", "frozen_sha")} for r in rows]
    assert raw == new  # the engine's own log, unchanged; exit rows carry decision_slot and the start row frozen_sha (#1130)
    assert all(r.get("decision_slot") is not None for r in rows if r["kind"] == "entry" or (r["kind"] == "exit" and r["reason"] != "end")) and rows[0]["frozen_sha"] == SHA40  # an end exit at E has no mark decision


def test_replay_with_the_c0_caps_on_is_a_rank_ordered_capped_subset_with_every_skip_logged(replayed):
    res = X.replay_equivalence(rcfg(replayed, "on"), caps=True)
    assert res["ok"] and res["caps"] is True and res["subset"] and res["skips_logged"] and res["rank_ordered"] and res["sells_match"], res
    assert 0 < res["bought"] < res["entries"] and res["skipped"] == res["entries"] - res["bought"] and res["skips"].get("max_open", 0) > 0
    assert sum(res["skips"].values()) == res["skipped"] and res["exits_not_held_expected"] > 0 and not res["latches"]
    st = json.loads((replayed / "st-on" / "bridge-state-dry.json").read_text())
    first_mark = min(o["t_mark"] for o in st["orders"].values())
    assert sorted(o["rank"] for o in st["orders"].values() if o["t_mark"] == first_mark) == [0, 1]  # the first two ranks of the first mark, nothing else


def test_replay_of_a_log_written_at_another_sha_is_refused_by_the_frozen_sha_pin(replayed):
    res = X.replay_equivalence(rcfg(replayed, "raw", root="raw"))
    assert not res["ok"] and "frozen_sha_mismatch" in res["latches"] and res["orders"] == 0  # the start row names another sha: the pin does its job


def test_replay_of_a_log_from_another_sha_or_a_tampered_log_fails(replayed, tmp_path):
    other = X.replay_equivalence(rcfg(replayed, "sha", frozen_sha="cd" * 20))
    assert not other["ok"] and "frozen_sha_mismatch" in other["latches"] and other["orders"] == 0
    copy_log(replayed / "stamped", tmp_path / "t")
    f = next(iter(sorted((tmp_path / "t" / "decisions" / X.BOT_ID).glob("*.jsonl"))))
    text = f.read_text()
    assert '"stake_sol":0.25' in text
    f.write_text(text.replace('"stake_sol":0.25', '"stake_sol":0.26', 1))
    bad = X.replay_equivalence({**rcfg(replayed, "tamper"), "root": str(tmp_path / "t")})
    assert not bad["ok"] and "chain_break" in bad["latches"]


def test_replay_check_cli(replayed, tmp_path, capsys):
    cfgp = tmp_path / "cfg.json"
    cfgp.write_text(json.dumps(rcfg(replayed, "cli")))
    assert X.main(["replay-check", "--config", str(cfgp)]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True
    cfgp.write_text(json.dumps(rcfg(replayed, "cli2")))
    assert X.main(["replay-check", "--config", str(cfgp), "--caps"]) == 0
    live = tmp_path / "live.json"
    live.write_text(json.dumps({**rcfg(replayed, "cli3"), "mode": "live"}))
    with pytest.raises(ValueError):
        X.main(["replay-check", "--config", str(live)])


def test_latency_uses_the_twins_per_date_slot_s_and_k_rounding():
    """A8: thresholds per UTC date from the twin's slot_s; a missing date falls back to the provisional config value and is counted."""
    assert X.k_slots(1.9, 0.2) == 9 and X.k_slots(3.0, 0.2) == 15 and X.k_slots(1.9, 0.4) == 5 and X.k_slots(3.0, 0.4) == 8
    o = {"a": {"buy_landed_ms": 2, "decision_ms": 1_793_400_000_000,  # 2026-10-30T22:40Z
 "decision_slot": 100, "buy_landed_slot": 110}}
    r = X.landed_buy_latencies(o, 0.4, {"2026-10-30": 0.2})[0]
    assert (r["slots"], r["k19"], r["x19"], r["slot_s_source"]) == (10, 9, 1, "date")
    r = X.landed_buy_latencies(o, 0.4, {"2026-11-01": 0.2})[0]
    assert (r["k19"], r["x19"], r["slot_s_source"]) == (5, 5, "provisional")
    s = X.latency_summary(o, 0.4, slot_s_by_date={"2026-10-30": 0.2})
    assert s["provisional_slot_s"] == 0 and s["all_slots_p50"] == 10


# ------------------------------------------------------------------------------------------------ restart exits (the host's exit-on-start rows)
def test_a_restart_exit_sells_the_held_position_marks_the_trip_gap_and_borrows_no_sd(tmp_path):
    clk = Clock()
    lg = Log(tmp_path)
    lg.start(ms(0, 0))
    lg.entry(0, "MA", 0)
    lg.entry(0, "MB", 1, dt=1201)
    lg.exit(1, "MA", 0, dt=1100)  # a rank exit of the first process: its sd(T) is the mark's
    lg.entry(1, "MC", 0, dt=1200)  # carries mark(1)'s decision_slot, which a restart exit must NOT borrow
    restart = ms(1, 20 * 60_000)
    lg.start(restart)
    lg.restart_exit(restart, "MB", 0, 1)
    lg.restart_exit(restart, "MC", 1, 1)
    lg.restart_exit(restart, "ZZ", 0, 1)  # an entry the canary never held
    br, port = bridge(tmp_path, clk, port=SlotPort())
    feed(br, clk)
    assert sends(port) == [("buy", oid("MA", 0)), ("buy", oid("MB", 0)), ("sell", oid("MA", 0)), ("buy", oid("MC", 1)), ("sell", oid("MB", 0)), ("sell", oid("MC", 1))]
    assert [w["reason"] for w in port.would_send if w["side"] == "sell"] == ["rank", "restart", "restart"]
    for m, i in (("MB", 0), ("MC", 1)):
        o = br.state.orders[oid(m, i)]
        assert o["status"] == "closed" and o["gap"] is True and o["restart_exit"] is True and o["exit_decision_slot"] is None and o.get("sell_landed_slot") is None
    assert br.state.orders[oid("MA", 0)].get("gap") is None and br.state.orders[oid("MA", 0)]["exit_decision_slot"] == SD0 + 9000  # the rank exit keeps the mark's sd
    assert br.state.counts["exit_restart"] == 2 and br.es["exits_not_held"] == {"unknown_order": 1} and not br.es["exits_not_followed"] and not br.state.latches
    s = X.latency_summary(br.state.orders, 0.4)
    assert s["sells_measured"] == 1  # only the rank exit is a latency sample; the restart exits have no decision slot


def test_the_time_stop_still_closes_a_position_whose_restart_row_never_came(tmp_path):
    clk = Clock()
    lg = Log(tmp_path)
    lg.start(ms(0, 0))
    lg.entry(0, "MA", 0)
    lg.start(ms(0, 600_000))  # the host restarted but wrote no restart exit (e.g. it crashed before host_start finished)
    br, port = bridge(tmp_path, clk)
    feed(br, clk)
    assert br.state.orders[oid("MA", 0)]["status"] == "open" and br.state.orders[oid("MA", 0)]["gap"] is True
    clk.t = ms(0) + X.TIME_STOP_MS
    br.tick()
    br.tick()
    assert sends(port) == [("buy", oid("MA", 0)), ("sell", oid("MA", 0))] and port.would_send[1]["reason"] == "time_stop_backstop"


def test_an_engine_restart_mid_run_exits_the_open_positions_and_enters_nothing_during_the_warmup(tmp_path):
    lvt = pytest.importorskip("tools.test_r4b_live")
    lv = pytest.importorskip("tools.r4b_live")
    ev = lvt.event_stream(lvt.scenario())
    log = DecisionLog(str(tmp_path / "rules"))
    log.append(X.BOT_ID, lvt.MARKS[0] * 1000, {"kind": "start", "book": "r4-b", "exp": "EXP-027", "mode": "dry", "schema": lv.SCHEMA, "frozen_sha": SHA40})
    eng_a, res_a = lvt.drive(ev, lvt.MARKS[:2])  # the first process decides two marks and is then killed
    lvt._log_marks(log, eng_a, res_a)
    held = {f"{m}:{t0}" for m, t0 in eng_a.book.pos.items()}
    assert len(held) >= 8
    restart_ms = (lvt.MARKS[1] + 1200) * 1000
    out = lv.host_start(log, now_ms=restart_ms, frozen_sha=SHA40)
    assert {r["order_id"] for r in out["exits"]} == held
    # the new process: built the host's way (52 h warm-up), fed only what arrives after the restart, decides the next two marks
    eng_b = lv.new_host_engine(universe_min_mbt=lvt.BT0, pda_fn=lvt.pda_of, hash_fn=lv.DuckHash(), score_fn=lvt._score_ret)
    i = next(k for k, e in enumerate(ev) if e[0] >= restart_ms // 1000)
    res_b = []
    for T in lvt.MARKS[2:]:
        i = lvt.feed(eng_b, ev, i, T + 30)
        res_b.append(eng_b.decide(T, w0=lvt.W0, last_entry=lvt.E - 3600))
        lv.log_actions(log, res_b[-1], {m.mint: m for m in eng_b.members()}, T * 1000)
    assert all(r.warmup and r.actions == [] for r in res_b)
    after = [r["kind"] for r in log.rows(X.BOT_ID) if r["ms"] >= restart_ms]
    assert after == ["start"] + ["exit"] * len(held)  # nothing else: no entry row during the warm-up
    cfg = X.make_config(cfg_dict(tmp_path))
    clk, port = Clock(), B.DryPort(cfg.dry_balance_lamports)
    br = X.R4bBridge(cfg, port, now_ms=clk, dry_replay_caps=True)  # capacity gates off: every decision goes through, so every restart row finds its position
    feed(br, clk)
    sold = {w["order_id"] for w in port.would_send if w["side"] == "sell" and w["reason"] == "restart"}
    assert sold == held and br.state.counts["exit_restart"] == len(held)
    assert all(o["status"] == "closed" for o in br.state.orders.values()) and all(br.state.orders[o]["gap"] for o in held)  # nothing waits for the time stop
    assert not br.state.latches and not br.es["exits_not_followed"] and not br.es["exits_not_held"]
    again = lv.restart_exits(log, X.BOT_ID, restart_ms + 1000)
    assert again == []  # a second call writes nothing


# ------------------------------------------------------------------------------------------------ feed-gap buy hold
def refusals(br):
    return [(r["order_id"], r["why"]) for r in map(json.loads, Path(br.log_path).read_text().splitlines()) if r.get("kind") == "entry_refused"]


def test_a_feed_gap_holds_buys_until_ten_gap_free_minutes_after_its_end_and_exits_keep_running(tmp_path):
    clk = Clock()
    lg = Log(tmp_path)
    gap = ms(1, 0)
    lg.start(ms(0, 0))
    lg.entry(0, "MA", 0)
    lg.gap(gap, frm=gap - 120_000, to=gap)
    br, port = bridge(tmp_path, clk, port=SlotPort())
    feed(br, clk)
    assert br.state.orders[oid("MA", 0)]["gap"] is True and br.state.counts["feed_gap"] == 1 and br.es["feed_gap_end_ms"] == gap  # the trip open across the gap
    br, port = bridge(tmp_path, clk, port=port)  # a restart of the executor keeps the hold (persisted)
    lg.entry(1, "MB", 0, dt=5 * 60_000)  # inside the hold
    lg.exit(1, "MA", 0, dt=6 * 60_000)  # exits keep running
    lg.entry(1, "MC", 1, dt=10 * 60_000 - 1000)  # one second short of 10 gap-free minutes
    lg.entry(1, "MD", 2, dt=10 * 60_000)  # exactly 10 minutes after the end of the gap
    feed(br, clk)
    assert sends(port) == [("buy", oid("MA", 0)), ("sell", oid("MA", 0)), ("buy", oid("MD", 1))]
    assert refusals(br) == [(oid("MB", 1), "feed_gap_hold"), (oid("MC", 1), "feed_gap_hold")] and br.state.counts["refused_feed_gap_hold"] == 2
    assert port.would_send[1]["reason"] == "rank" and br.state.orders[oid("MD", 1)].get("gap") is None  # a trip opened after the hold is clean
    lat = X.landed_buy_latencies(br.state.orders, 0.4)
    assert [(r["order_id"], r["gap"], r["slots"]) for r in lat] == [(oid("MA", 0), True, 8), (oid("MD", 1), False, 8)]  # MA is measured but excluded from parity: open across the gap


def test_the_hold_counts_from_the_end_of_the_gap_not_from_its_row_or_its_start(tmp_path):
    clk = Clock()
    lg = Log(tmp_path)
    row = ms(1, 0)
    lg.start(ms(0, 0))
    lg.gap(row, frm=row - 3_600_000, to=row + 5 * 60_000)  # the row is logged at the start of a gap that ends 5 minutes later
    lg.entry(1, "MA", 0, dt=14 * 60_000)  # 9 minutes after the end
    lg.entry(1, "MB", 1, dt=15 * 60_000)  # 10 minutes after the end
    br, port = bridge(tmp_path, clk)
    feed(br, clk)
    assert [o for _, o in sends(port)] == [oid("MB", 1)] and refusals(br) == [(oid("MA", 1), "feed_gap_hold")] and br.es["feed_gap_end_ms"] == row + 5 * 60_000
    lg2 = Log(tmp_path / "late")  # a row logged after the gap ended (its ms was raised by log_gap): the hold is counted from the later of the two
    lg2.start(ms(0, 0))
    lg2.gap(row + 60_000, frm=row - 60_000, to=row)
    lg2.entry(1, "MC", 0, dt=60_000 + 599_000)
    lg2.entry(1, "MD", 1, dt=60_000 + 600_000)
    br2, port2 = bridge(tmp_path / "late", clk)
    feed(br2, clk)
    assert [o for _, o in sends(port2)] == [oid("MD", 1)]


def test_a_gap_that_halts_nothing_is_never_logged_and_a_data_loss_gap_holds(tmp_path):
    lv = pytest.importorskip("tools.r4b_live")
    from tools.r4b_feed_adapter import GapRecord

    outcomes = {}
    for name, rec in (("reconnect", GapRecord("socket_reconnect", "pumpfun", ms(1, 0), ms(1, 0) - 40_000, ms(1, 0), False, True)),
                      ("loss", GapRecord("data_loss", "pumpfun", ms(1, 0), ms(1, 0) - 40_000, ms(1, 0), True, True)),
                      ("unknown", GapRecord("gap", None, ms(1, 0), None, None, None, None))):
        clk = Clock()
        lg = Log(tmp_path / name)
        lg.start(ms(0, 0))
        written = lv.log_gap(lg.d, rec)
        lg.entry(1, "MA", 0, dt=1000)  # one second after the gap
        br, port = bridge(tmp_path / name, clk)
        feed(br, clk)
        outcomes[name] = (written is not None, [o for _, o in sends(port)], br.es["feed_gap_end_ms"])
    assert outcomes["reconnect"] == (False, [oid("MA", 1)], 0)  # halts_buys False: no row, no hold, the buy goes through
    assert outcomes["loss"] == (True, [], ms(1, 0)) and outcomes["unknown"][:2] == (True, [])  # data_loss True or unknown (fail closed): held


def test_a_live_style_bridge_needs_the_managers_review_naming_the_newest_gap_instant(tmp_path):
    clk = Clock()
    lg = Log(tmp_path)
    gap = ms(1, 0)
    review = tmp_path / "review"
    review.mkdir()
    lg.start(ms(0, 0))
    lg.gap(gap, frm=gap - 60_000, to=gap)
    lg.entry(1, "MA", 0, dt=11 * 60_000)  # 11 gap-free minutes, no review file yet
    cfg = X.make_config(cfg_dict(tmp_path))
    br = X.R4bBridge(cfg, B.DryPort(cfg.dry_balance_lamports), now_ms=clk, root_checks=False, gap_clear_dir=review, require_gap_review=True)
    feed(br, clk)
    assert br.state.counts["refused_feed_gap_hold"] == 1 and refusals(br) == [(oid("MA", 1), "feed_gap_hold:review")]
    (review / f"{X.BOT_ID}.json").write_text(json.dumps({"bot_id": X.BOT_ID, "reviewed_gap_ms": gap - 1}))  # a review of another instant does not clear
    lg.entry(1, "MB", 1, dt=12 * 60_000)
    feed(br, clk)
    assert br.state.counts["refused_feed_gap_hold"] == 2
    (review / f"{X.BOT_ID}.json").write_text(json.dumps({"bot_id": X.BOT_ID, "reviewed_gap_ms": gap}))
    lg.entry(1, "MC", 2, dt=13 * 60_000)
    feed(br, clk)
    assert [o for _, o in sends(br.port)] == [oid("MC", 1)]
    lg.gap(ms(1, 14 * 60_000), frm=ms(1, 14 * 60_000) - 1000, to=ms(1, 14 * 60_000))  # a newer gap: the old review no longer names the newest instant
    lg.entry(1, "MD", 3, dt=26 * 60_000)
    feed(br, clk)
    assert refusals(br)[-1] == (oid("MD", 1), "feed_gap_hold:review")
    dry, port = bridge(tmp_path, clk, state_dir=str(tmp_path / "dry-state"))  # the same log on a plain dry bridge: no review is asked for, only the 10 minutes
    feed(dry, clk)
    assert [o for _, o in sends(port)] == [oid("MA", 1), oid("MB", 1)] and not any(w.endswith(":review") for _, w in refusals(dry))  # max_open 2 stops MC; no review was asked for


def test_a_gap_row_without_an_instant_holds_from_now_and_an_unknown_kind_still_holds(tmp_path):
    clk = Clock(ms(1, 0))
    lg = Log(tmp_path)
    lg.start(ms(0, 0))
    br, port = bridge(tmp_path, clk)
    feed(br, clk)
    clk.t = ms(1, 0)
    br.handle_row({"kind": "gap", "ms": "soon", "seq": 9})  # hand-fed: a log row always has an int ms; a mangled one fails closed
    assert br.state.counts["feed_gap_row_malformed"] == 1 and br.es["feed_gap_end_ms"] == ms(1, 0) and br.es["feed_gaps"][0]["gap_kind"] == "unknown"
    assert br.feed_gap_refusal({"ms": ms(1, 599_000)}, ms(1, 599_000)) == "feed_gap_hold" and br.feed_gap_refusal({"ms": ms(1, 600_000)}, ms(1, 600_000)) is None


def test_replay_of_a_log_with_a_gap_row_and_restart_rows(tmp_path):
    lg = Log(tmp_path)
    gap = ms(1, 0)
    lg.start(ms(0, 0))
    lg.entry(0, "MA", 0)
    lg.gap(gap, frm=gap - 1000, to=gap)
    lg.entry(1, "MB", 0, dt=60_000)  # inside the hold
    lg.exit(1, "MA", 0, dt=90_000)
    restart = ms(1, 20 * 60_000)
    lg.start(restart)
    lg.restart_exit(restart, "MB", 1, 1)
    off = X.replay_equivalence(cfg_dict(tmp_path, state_dir=str(tmp_path / "s-off")))
    assert off["ok"] and off["orders"] == off["decisions"] == 4 and not off["refused"]  # caps off: one for one, the hold is not a capacity gate
    on = X.replay_equivalence(cfg_dict(tmp_path, state_dir=str(tmp_path / "s-on")), caps=True)
    assert on["ok"] and on["bought"] == 1 and on["skips"] == {"feed_gap_hold": 1} and on["exits_not_held_expected"] == 1  # MB was refused, so its restart row finds nothing
