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
    assert X.FOLLOWED_EXIT_REASONS == ("rank", "gap", "end")
    assert X.MAX_HOLD_MS == 49 * 3_600_000 and X.TIME_STOP_MS == 49 * 3_600_000 + 60_000  # 48 h + 1 h + 60 s
    assert X.SEND_FEE_LAMPORTS == 505_000 == FZ.SEND_FEE_DECIDING and X.SELL_RESERVE_LAMPORTS == 10 * 505_000
    assert (X.P5_WINDOW, X.P5_SOFT_HALT_S, X.MATCH_P50_S, X.TWIN_LEG_S) == (20, 3.0, 1.9, 1.9)
    assert 1.9 in FZ.LATENCIES_S  # the twin leg is one of the book's registered legs


def test_constants_equal_the_pinned_modules_where_they_overlap():
    lv = pytest.importorskip("tools.r4b_live")
    rl = pytest.importorskip("tools.r4b_layout")
    assert X.BOT_ID == lv.BOT_ID and X.BOOK == lv.BOOK and X.MARK_S == lv.MARK_S
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
    assert mods <= {"__future__", "argparse", "hashlib", "json", "re", "statistics", "sys", "dataclasses", "pathlib", "typing", "tools.arena_live_bridge", "tools.r4b_book",
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


@pytest.mark.parametrize("reason", ["rank", "gap", "end"])
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
    s = X.latency_summary(_landed(25, 8), 0.4)  # 3.2 s
    c = X.latency_check(s)
    assert s["landed_buys"] == 25 and s["window_n"] == 20 and s["window_p50_s"] == pytest.approx(3.2) and c["enough"] and c["p5_soft_halt"] and not c["match_latency_ok"]
    s = X.latency_summary(_landed(25, 7), 0.4)  # 2.8 s: under the P5 halt, over the match clause
    c = X.latency_check(s)
    assert not c["p5_soft_halt"] and not c["match_latency_ok"]
    s = X.latency_summary(_landed(25, 4), 0.4)  # 1.6 s
    c = X.latency_check(s)
    assert not c["p5_soft_halt"] and c["match_latency_ok"]
    s = X.latency_summary(_landed(25, 5), 0.4)  # 2.0 s > 1.9 s
    assert not X.latency_check(s)["match_latency_ok"]
    s = X.latency_summary(_landed(19, 20), 0.4)  # 19 landed buys: not enough, no halt
    c = X.latency_check(s)
    assert not c["enough"] and not c["p5_soft_halt"] and not c["match_latency_ok"]
    # the rolling window follows the last 20, the match clause all of them
    o = {**_landed(30, 4), **{f"p{i:03d}": {"decision_slot": 5000 + i, "buy_landed_slot": 5000 + i + 9, "buy_landed_ms": 99_000 + i} for i in range(20)}}  # 20 slow buys at the end: 3.6 s
    s = X.latency_summary(o, 0.4)
    assert s["window_p50_s"] == pytest.approx(3.6) and s["all_p50_s"] == pytest.approx(1.6) and X.latency_check(s)["p5_soft_halt"]
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
    assert "p5_landing_latency" in br.state.latches and "4.000 s" in br.state.latches["p5_landing_latency"]
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
def stamp(src, dst, sha=SHA40):
    """Copy a closed r4b decision log with the `start` row carrying frozen_sha, as the live decision host must write it. tools.r4b_live.replay_walker's own
    start row has none (it is not a host). Every decision row is copied unchanged apart from the chain fields."""
    out = DecisionLog(dst)
    for r in DecisionLog(src).rows(X.BOT_ID):
        body = {k: v for k, v in r.items() if k not in ("seq", "arm", "ms", "prev", "sha")}
        if body["kind"] == "start":
            body["frozen_sha"] = sha
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
    lv.replay_walker(tmp / "walk", lvt.H0, lvt.H1, lvt.W0, lvt.E, static_map=None, universe_min_mbt=lvt.BT0, boosters=rs.load_boosters(), pda_fn=lvt.pda_of, allow_dir=True,
                     log_root=tmp / "raw")
    stamp(tmp / "raw", tmp / "stamped")
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
    assert raw == new  # the stamped copy carries the engine's decisions unchanged


def test_replay_with_the_c0_caps_on_is_a_rank_ordered_capped_subset_with_every_skip_logged(replayed):
    res = X.replay_equivalence(rcfg(replayed, "on"), caps=True)
    assert res["ok"] and res["caps"] is True and res["subset"] and res["skips_logged"] and res["rank_ordered"] and res["sells_match"], res
    assert 0 < res["bought"] < res["entries"] and res["skipped"] == res["entries"] - res["bought"] and res["skips"].get("max_open", 0) > 0
    assert sum(res["skips"].values()) == res["skipped"] and res["exits_not_held_expected"] > 0 and not res["latches"]
    st = json.loads((replayed / "st-on" / "bridge-state-dry.json").read_text())
    first_mark = min(o["t_mark"] for o in st["orders"].values())
    assert sorted(o["rank"] for o in st["orders"].values() if o["t_mark"] == first_mark) == [0, 1]  # the first two ranks of the first mark, nothing else


def test_replay_of_the_raw_replay_log_is_refused_by_the_frozen_sha_pin(replayed):
    res = X.replay_equivalence(rcfg(replayed, "raw", root="raw"))
    assert not res["ok"] and "frozen_sha_mismatch" in res["latches"] and res["orders"] == 0  # replay_walker's start row has no frozen_sha: the pin does its job


def test_replay_of_a_log_from_another_sha_or_a_tampered_log_fails(replayed, tmp_path):
    other = X.replay_equivalence(rcfg(replayed, "sha", frozen_sha="cd" * 20))
    assert not other["ok"] and "frozen_sha_mismatch" in other["latches"] and other["orders"] == 0
    stamp(replayed / "raw", tmp_path / "t")
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
