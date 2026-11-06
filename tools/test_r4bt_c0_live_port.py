"""Tests of the r4-b-T C0 LIVE PORT (tools/r4bt_c0_executor.py: LIVE_BUILT, R4btLivePort, live_buy_problem, fill_facts, run). Synthetic only: a fake inner port, no key,
no network, no RPC send, no solana stack.

  /data/mal/venv/bin/python -m pytest tools/test_r4bt_c0_live_port.py
(run it in its own pytest process, apart from the twin tests)
"""

from __future__ import annotations

import ast
import json
import os
import types
from pathlib import Path

import pytest

from tools import arena_live_bridge as B
from tools import r4bt_c0_executor as T
from tools import r4bt_c0_parity as PAR
from tools.test_r4bt_c0_executor import Clock, World, feed, ms, mark, jwrite, iso, _env  # noqa: F401  (autouse fixture)


class FakeEx:
    def __init__(self):
        self.rows = []
        self.limits = types.SimpleNamespace(priority_lamports=500_000)

    def _log(self, kind, mint, **kw):
        self.rows.append((kind, mint, kw))


class FakeInner(B.OrderPort):
    """Stands in for B.make_live_port's LiveExecutorPort: records sends, logs fill rows through ex._log like probe_live, returns scripted events."""

    name = "live"
    live = True

    def __init__(self):
        self.ex = FakeEx()
        self.buys, self.sells, self.events, self.hook = [], [], [], None

    def mint_for_pool(self, pool, meta):
        return meta.get("mint")

    def universe_problem(self, pool, mint):
        return None

    def mint_screen_problem(self, mint):
        if self.hook:
            self.hook()
        return None

    def balance(self):
        return 500_000_000

    def holdings(self):
        return {}

    def buy(self, order):
        self.buys.append(order)
        return True, None

    def sell(self, order, reason):
        self.sells.append((order, reason))
        return True, None

    def warm(self, mints):
        pass

    def land(self, kind, event, mint, **row):
        self.ex._log(kind, mint, **row)
        self.events.append({"mint": mint, "event": event, "landed_slot": row.get("landed_slot"), **({"pnl_lamports": row["pnl_lamports"]} if "pnl_lamports" in row else {})})

    def poll(self):
        out, self.events = self.events, []
        return out


BUY_ROW = dict(landed=True, fee_lamports=505_000, spend_lamports=50_000_000, tokens_received=2_000_000_000, landed_slot=2000, confirm_seen_ms=1_000)
SELL_ROW = dict(landed=True, fee_lamports=505_000, sol_received_lamports=52_000_000, tokens=2_000_000_000, pnl_lamports=1_000_000, landed_slot=2100, confirm_seen_ms=2_000)


def live_world(tmp_path, **over):
    w = World(tmp_path, mode="live", **over)
    w.start()
    w.entry(0, "MINT", 0)
    return w


def live_bridge(w, clk=None):
    clk = clk or Clock(w.at)
    cfg = w.cfg()
    inner = FakeInner()
    port = T.R4btLivePort(inner, cfg)
    br = T.R4btBridge(cfg, port, now_ms=clk, root_checks=False)
    w.parity(cfg)
    return br, inner, clk


# ------------------------------------------------------------------------------------------------ the single constant and DRY never sends
def test_live_built_is_one_constant_assigned_once():
    tree = ast.parse(Path(T.__file__).read_text())
    assigns = [n for n in ast.walk(tree) if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "LIVE_BUILT" for t in n.targets)]
    assert len(assigns) == 1 and T.LIVE_BUILT is True


def test_dry_config_never_takes_a_live_port_or_builds_one(tmp_path, monkeypatch):
    w = World(tmp_path)
    inner = FakeInner()
    with pytest.raises(SystemExit):
        T.R4btLivePort(inner, w.cfg())  # a DRY config cannot even wrap a live port
    with pytest.raises(SystemExit):
        T.R4btBridge(w.cfg(), inner, now_ms=Clock(w.at), root_checks=False)
    called = []
    for raw_mode, flag in (("dry", False), ("dry", True), ("live", False)):  # no combination except live + --live reaches the live port factory
        assert T.run({**w.d, "mode": raw_mode}, live_flag=flag, once=True, root_checks=False, now_ms=Clock(w.at), port_factory=lambda c: called.append(c)) == 0
    assert called == []
    w.start()
    w.entry(0, "MINT", 0)
    clk = Clock(w.at)
    br = T.R4btBridge(w.cfg(), B.DryPort(1_000_000_000), now_ms=clk, root_checks=False)
    w.parity()
    feed(br, clk)
    assert any(o["live"] is False for o in br.state.orders.values()) and inner.buys == []


def test_live_flag_and_config_mode_must_agree_at_start(tmp_path):
    w = live_world(tmp_path)
    assert T.start_refusal(w.cfg(), live=False, root_checks=False) == "live_flag_mode_mismatch"
    assert T.start_refusal(w.cfg(), live=True, root_checks=False) is None
    w2 = World(tmp_path / "b")
    assert T.start_refusal(w2.cfg(), live=True, root_checks=False) == "live_flag_mode_mismatch"


def test_run_live_refuses_before_the_factory_when_a_gate_is_missing(tmp_path):
    w = live_world(tmp_path)
    (tmp_path / "LIVE_OK").unlink()
    called = []
    assert T.run(w.d, live_flag=True, once=True, root_checks=False, now_ms=Clock(w.at), port_factory=lambda c: called.append(c)) == 2 and called == []


def test_run_live_builds_the_port_only_through_the_factory(tmp_path):
    w = live_world(tmp_path)
    inner = FakeInner()
    seen = []

    def factory(cfg):
        seen.append(cfg.mode)
        return T.R4btLivePort(inner, cfg)

    assert T.run(w.d, live_flag=True, once=True, root_checks=False, now_ms=Clock(w.at), port_factory=factory) == 0 and seen == ["live"]


def test_x_module_is_untouched_and_no_key_is_read_here():
    src = Path(T.__file__).read_text()
    assert "load_probe_key()" in src and "open(" not in src.split("def build_live_port")[1].split("def run(")[0]
    assert "CREDENTIALS" not in src.replace("$CREDENTIALS_DIRECTORY", "")


# ------------------------------------------------------------------------------------------------ the happy live buy
def test_all_gates_open_sends_one_live_buy(tmp_path):
    w = live_world(tmp_path)
    br, inner, clk = live_bridge(w)
    feed(br, clk)
    assert [o["order_id"] for o in inner.buys] == [f"MINT:{mark(0)}"] and not br.state.latches
    assert br.state.orders[f"MINT:{mark(0)}"]["live"] is True


# ------------------------------------------------------------------------------------------------ every gate, re-checked immediately before the buy
def _rm(name):
    return lambda w, t: (t / name).unlink()


def _write(name, text):
    return lambda w, t: (t / name).write_text(text)


GATE_BREAKS = {
    "live_ok_missing": _rm("LIVE_OK"),
    "live_ok_other_book": _write("LIVE_OK", "other-book\n"),
    "tier_not_t0": _write("TIER", "T1\n"),
    "tier_missing": _rm("TIER"),
    "owner_missing": _rm("owner.md"),
    "owner_wrong_bot": _write("owner.md", "OWNER_CANARY_CONFIRMED: bot_id=hv4-x\n"),
    "read_record_missing": _rm("read.json"),
    "read_record_other_sha": lambda w, t: jwrite(t / "read.json", {**json.loads((t / "read.json").read_text()), "read_result_sha256": "ee" * 32}),
    "carry_written_stale": lambda w, t: jwrite(t / "a3" / "carry.json", {**json.loads((t / "a3" / "carry.json").read_text()), "written_utc": iso(w.at - 3 * 3_600_000)}),
    "carry_missing": lambda w, t: (t / "a3" / "carry.json").unlink(),
    "carry_halt": lambda w, t: jwrite(t / "a3" / "carry.json", {**json.loads((t / "a3" / "carry.json").read_text()), "verdict": "HALT"}),
    "twin_run_stale": lambda w, t: jwrite(t / "twin" / T.RUN_RECORD_NAME, {**json.loads((t / "twin" / T.RUN_RECORD_NAME).read_text()), "run_ms": w.at - 3 * 3_600_000, "heartbeat_ms": w.at - 3 * 3_600_000}),
    "twin_missing": lambda w, t: (t / "twin" / T.RUN_RECORD_NAME).unlink(),
    "parity_stale": lambda w, t: jwrite(t / "state" / T.PARITY_STATE_NAME, {**json.loads((t / "state" / T.PARITY_STATE_NAME).read_text()), "at_ms": w.at - 120_000}),
    "parity_future": lambda w, t: jwrite(t / "state" / T.PARITY_STATE_NAME, {**json.loads((t / "state" / T.PARITY_STATE_NAME).read_text()), "at_ms": w.at + 6_000}),
    "parity_ledger_sha": lambda w, t: jwrite(t / "state" / T.PARITY_STATE_NAME, {**json.loads((t / "state" / T.PARITY_STATE_NAME).read_text()), "ledger_sha256": "0" * 64}),
    "parity_attempts_sha": lambda w, t: jwrite(t / "state" / T.PARITY_STATE_NAME, {**json.loads((t / "state" / T.PARITY_STATE_NAME).read_text()), "attempts_sha256": "0" * 64}),
    "parity_stop_required": lambda w, t: jwrite(t / "state" / T.PARITY_STATE_NAME, {**json.loads((t / "state" / T.PARITY_STATE_NAME).read_text()), "stop_required": True}),
    "parity_missing": lambda w, t: (t / "state" / T.PARITY_STATE_NAME).unlink(),
}


@pytest.mark.parametrize("name", sorted(GATE_BREAKS))
def test_gate_missing_or_stale_before_the_entry_means_no_live_buy(tmp_path, name):
    w = live_world(tmp_path)
    br, inner, clk = live_bridge(w)
    GATE_BREAKS[name](w, tmp_path)
    feed(br, clk)
    assert inner.buys == []  # either routed DRY (live gate) or refused at the entry (a3 / twin / parity)


@pytest.mark.parametrize("name", sorted(set(GATE_BREAKS) - {"parity_missing"}))
def test_every_gate_is_rechecked_immediately_before_the_send(tmp_path, name):
    """The gate is good through handle_entry's own checks and breaks during the mint-screen RPC (after the entry gates, before the send): the port still refuses."""
    w = live_world(tmp_path)
    br, inner, clk = live_bridge(w)
    inner.hook = lambda: GATE_BREAKS[name](w, tmp_path)
    feed(br, clk)
    assert inner.buys == []
    o = br.state.orders[f"MINT:{mark(0)}"]
    assert o["status"] == "refused" and str(o["refused"]).startswith("gate:")


def test_a_latch_stop_file_or_caps_stop_the_send(tmp_path):
    w = live_world(tmp_path)
    br, inner, clk = live_bridge(w)
    order = {"order_id": "o", "stake_lamports": 50_000_000}
    assert br.live_buy_gate(order) is None
    assert br.live_buy_gate({**order, "stake_lamports": 50_000_001}) == "caps:stake"
    assert br.live_buy_gate({**order, "stake_lamports": True}) == "caps:stake"
    (Path(br.cfg.state_dir) / T.STOP_NAME).write_text("x")
    assert br.live_buy_gate(order) == "stop_file"
    (Path(br.cfg.state_dir) / T.STOP_NAME).unlink()
    br.state.realized_lamports = -200_000_000
    assert br.live_buy_gate(order) == "caps:total_loss"
    br.state.realized_lamports = 0
    br._latch("x", "y")
    assert br.live_buy_gate(order).startswith("latched:")


def test_live_buy_problem_caps_counters(tmp_path):
    w = live_world(tmp_path)
    br, inner, clk = live_bridge(w)
    kw = dict(root_checks=False)
    o = {"stake_lamports": 50_000_000}
    args = (br.cfg, w.at, br.state.orders, o, br.state)
    assert T.live_buy_problem(*args, open_count=2, **kw) is None
    assert T.live_buy_problem(*args, open_count=3, **kw) == "caps:max_open"
    assert T.live_buy_problem(*args, day_trades=31, **kw) == "caps:max_trades_per_day"
    assert T.live_buy_problem(*args, day_realized=-100_000_000, **kw) == "caps:daily_loss"


def test_port_without_a_pre_buy_check_never_buys():
    cfg = T.make_config({**World.__dict__.get("d", {})}) if False else None  # noqa: F841
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        w = World(Path(td), mode="live")
        inner = FakeInner()
        port = T.R4btLivePort(inner, w.cfg())
        assert port.buy({"order_id": "o", "mint": "M", "stake_lamports": 1}) == (False, "gate:no_pre_buy_check") and inner.buys == []


# ------------------------------------------------------------------------------------------------ parity-state future skew
def test_parity_state_future_skew_is_about_five_seconds(tmp_path):
    w = World(tmp_path)
    br, port, clk = w.bridge()
    sd = br.cfg.state_dir
    st = json.loads((Path(sd) / T.PARITY_STATE_NAME).read_text())
    assert T.PARITY_FUTURE_SKEW_MS == 5_000
    for at, ok in ((w.at + 4_000, True), (w.at + 6_000, False), (w.at - 59_000, True), (w.at - 61_000, False)):
        jwrite(Path(sd) / T.PARITY_STATE_NAME, {**st, "at_ms": at})
        assert (T.parity_state_problem(sd, w.at) is None) is ok


def test_parity_tool_takes_the_real_clock_when_now_ms_is_not_given():
    import inspect
    src = inspect.getsource(PAR.main)
    assert "time.time()" in src


# ------------------------------------------------------------------------------------------------ fees and fill facts
def test_fill_facts_units_match_the_twins_pool_price():
    """PINNED UNIT: lamports per raw base-token unit, the same ratio the twin's Qe / Be gives (parity._price). On a constant-product pool a 0.05 SOL buy at reserves
    (Qe, Be) fills at (Qe + s) / Be ~ Qe / Be; the live price must agree with the twin's within the stake's own price impact (0.3 %), never a factor of 1e9 or 10**decimals off."""
    Qe, Be, s = 17_600_000_000, 700_000_000_000_000, 50_000_000
    tokens = Be * s // (Qe + s)
    live = T.fill_facts("buy_landed", {"spend_lamports": s, "tokens_received": tokens, "fee_lamports": 505_000})["live_buy_price"]
    twin = PAR._price(Qe, Be)
    assert T.PRICE_UNIT == "lamports per raw base-token unit"
    assert abs(live / twin - 1) < 0.003
    out = T.fill_facts("sell_landed", {"sol_received_lamports": 24_000_000, "tokens": 1_000_000_000_000, "fee_lamports": 505_000, "pnl_lamports": -1})
    assert out["live_sell_price"] == 24_000_000 / 1_000_000_000_000 and out["live_net_lamports"] == -1


def test_fill_facts_expired_pays_nothing_and_missing_facts_are_left_out():
    assert T.fill_facts("buy_failed", {"fail_class": "expired"})["fees_lamports"] == 0
    assert "fees_lamports" not in T.fill_facts("buy_failed", {"landed": False})
    assert "live_buy_price" not in T.fill_facts("buy_landed", {"spend_lamports": 5, "tokens_received": 0, "fee_lamports": 1})
    assert T.fill_facts("sell_failed", {"fee_lamports": 505_000, "landed": False})["fees_lamports"] == 505_000


def _trip(br, inner, clk, w):
    feed(br, clk)
    inner.land("buy", "buy_landed", "MINT", **BUY_ROW)
    br._poll_ports()
    br.handle_row({"kind": "exit", "order_id": f"MINT:{mark(0)}", "mint": "MINT", "reason": "rank", "t_mark": mark(0), "ms": ms(0) + 3_600_000, "seq": 99})


def test_fees_are_the_sum_of_every_send_on_the_leg_including_resends(tmp_path):
    w = live_world(tmp_path)
    br, inner, clk = live_bridge(w)
    _trip(br, inner, clk, w)
    inner.land("sell", "sell_failed", "MINT", landed=False, fail_class="slippage", fee_lamports=505_000, confirm_seen_ms=1_500)  # first sell landed but failed: paid
    inner.land("sell", "sell_failed", "MINT", landed=False, fail_class="expired")  # a resend that expired: nothing paid
    inner.land("sell", "sell_landed", "MINT", **SELL_ROW)  # the escalated resend lands: paid
    br._poll_ports()
    oid = f"MINT:{mark(0)}"
    o = br.state.orders[oid]
    assert o["entry_fee_lamports"] == 505_000 and o["exit_fee_lamports"] == 1_010_000 and not br.state.latches
    row = [json.loads(ln) for ln in (Path(br.cfg.state_dir) / T.LEDGER_NAME).read_text().splitlines()][0]
    assert row["order_id"] == oid and row["entry_fee_lamports"] == 505_000 and row["exit_fee_lamports"] == 1_010_000
    assert row["live_buy_price"] == 50_000_000 / 2_000_000_000 and row["live_sell_price"] == 52_000_000 / 2_000_000_000
    assert row["live_net_lamports"] == 1_000_000 and row["exit_landed_ms"] == 2_000 and row["fee_source"] is None


def test_attempts_file_carries_the_live_buy_outcome(tmp_path):
    w = live_world(tmp_path)
    br, inner, clk = live_bridge(w)
    _trip(br, inner, clk, w)
    rows = [json.loads(ln) for ln in (Path(br.cfg.state_dir) / T.ATTEMPTS_NAME).read_text().splitlines()]
    assert rows and rows[0]["landed"] is True and rows[0]["trip_id"] == f"MINT:{mark(0)}" and rows[0]["landed_ms"] == 1_000


def test_a_fee_above_the_configured_send_fee_latches(tmp_path):
    w = live_world(tmp_path)
    br, inner, clk = live_bridge(w)
    feed(br, clk)
    inner.land("buy", "buy_landed", "MINT", **{**BUY_ROW, "fee_lamports": 505_001})
    br._poll_ports()
    assert T.LATCH_FEE_OVER in br.state.latches


def test_an_event_without_fee_or_fill_facts_latches(tmp_path):
    w = live_world(tmp_path)
    br, inner, clk = live_bridge(w)
    feed(br, clk)
    inner.land("buy", "buy_landed", "MINT", landed=True, spend_lamports=1)  # no fee_lamports, no tokens_received
    br._poll_ports()
    assert T.LATCH_FEE in br.state.latches and T.LATCH_FILL in br.state.latches


def test_registered_fee_is_505000_and_an_override_sets_the_executors_priority(tmp_path):
    w = live_world(tmp_path)
    assert w.cfg().send_fee_lamports == 505_000
    inner = FakeInner()
    T.R4btLivePort(inner, w.cfg())
    assert inner.ex.limits.priority_lamports == 500_000  # untouched at the registered fee
    ovr = {"lamports": 255_000, "fill_fee": "FILL:fee", "owner_line": "o", "qp_comment_id": "6700000001"}
    w2 = World(tmp_path / "o", mode="live", send_fee_lamports=255_000, fee_override=ovr)
    inner2 = FakeInner()
    T.R4btLivePort(inner2, w2.cfg())
    assert inner2.ex.limits.priority_lamports == 250_000
    with pytest.raises(T.Refused):
        World(tmp_path / "p", mode="live", send_fee_lamports=255_000).cfg()


# ------------------------------------------------------------------------------------------------ health.json for the watchdog
def _health(w):
    return json.loads((w.tmp / "health" / "health.json").read_text())


def test_health_file_has_only_the_pinned_keys_and_no_values(tmp_path):
    w = live_world(tmp_path)
    br, inner, clk = live_bridge(w)
    feed(br, clk)
    inner.land("buy", "buy_landed", "MINT", **BUY_ROW)
    br._poll_ports()
    br.tick()
    h = _health(w)
    assert set(h) == set(T.HEALTH_KEYS) == {"schema", "at_ms", "latched", "latch_names", "stop_present", "live_ok_present", "tier", "dry"}
    assert h["schema"] == "r4bt_c0_health_v1" and h["at_ms"] == w.at and h["latched"] is False and h["latch_names"] == []
    assert h["stop_present"] is False and h["live_ok_present"] is True and h["tier"] == "T0" and h["dry"] is False
    assert oct(os.stat(w.tmp / "health" / "health.json").st_mode & 0o777) == "0o644"
    text = (w.tmp / "health" / "health.json").read_text()
    for forbidden in ("MINT", "50000000", "2000000000", "PDA_", "pnl", "price", "order"):
        assert forbidden not in text


def test_health_updates_on_latch_and_stop_and_dry(tmp_path):
    w = live_world(tmp_path)
    br, inner, clk = live_bridge(w)
    br.tick()
    br._latch("r4bt_a3", "a long reason with MINT and 12345 inside")
    h = _health(w)
    assert h["latched"] is True and h["latch_names"] == ["r4bt_a3"] and set(h) == set(T.HEALTH_KEYS) and "MINT" not in json.dumps(h)
    (Path(br.cfg.state_dir) / T.STOP_NAME).write_text("x")
    (w.tmp / "TIER").write_text("T1\n")
    (w.tmp / "LIVE_OK").unlink()
    br.tick()
    h = _health(w)
    assert h["stop_present"] is True and h["tier"] is None and h["live_ok_present"] is False and h["dry"] is True and set(h) == set(T.HEALTH_KEYS)


def test_health_dry_bridge_and_a_failed_write_never_stop_the_loop(tmp_path):
    w = World(tmp_path)
    br, port, clk = w.bridge()
    br.tick()
    assert _health(w)["dry"] is True
    bad = World(tmp_path / "b", health_path="/proc/nope/health.json")
    br2, _p, _c = bad.bridge()
    br2.tick()  # the write fails silently
    with pytest.raises(T.Refused):
        World(tmp_path / "c", health_path="rel/health.json").cfg()
