"""Offline tests for tools/arena_live_bridge.py. Stubbed port, synthetic hash-chained decision logs, no network, no key (the live-port test uses a
throwaway Keypair and the probe tests' fake RPC)."""

from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools import arena_live_bridge as alb
from tools.arena_declog import DecisionLog

BOT = "bot-x"
SHA = "a" * 64
T0 = 1_791_300_000_000
H = 600_000  # the book's max hold, ms


class Clock:
    def __init__(self, t: int = T0 + 1_000) -> None:
        self.t = t

    def __call__(self) -> int:
        return self.t


class StubPort(alb.OrderPort):
    """Records every call; events are scripted by the test."""

    name = "stub"

    def __init__(self, live: bool = False, balance: int = 600_000_000) -> None:
        self.live = live
        self._balance = balance
        self.calls: list[tuple] = []
        self.events: list[dict] = []
        self.returns: dict[str, float] = {}
        self.universe: str | None = None
        self.warmed: list[list[str]] = []
        self.pool_mint: str | None = None

    held: dict | None = None

    def holdings(self):
        return self.held

    def mint_for_pool(self, pool, meta):
        return self.pool_mint or meta.get("mint") or f"M-{pool}"

    def universe_problem(self, pool, mint):
        return self.universe

    screen: str | None = None  # the mint screen's answer (tools/test_arena_live_bridge_mint_screen.py sets it)

    def mint_screen_problem(self, mint):
        return self.screen

    def balance(self):
        return self._balance

    def buy(self, order):
        self.calls.append(("buy", order["order_id"], order["mint"], order["stake_lamports"]))
        return True, None

    def sell(self, order, reason):
        self.calls.append(("sell", order["order_id"], reason))
        if self.held is not None:
            self.held[order["mint"]] = "pending"
        return True, None

    def warm(self, mints):
        self.warmed.append(list(mints))

    def position_return(self, mint):
        return self.returns.get(mint)

    def poll(self):
        out, self.events = self.events, []
        return out


class Env:
    def __init__(self, tmp: str, live: bool = False, **over) -> None:
        self.tmp = Path(tmp)
        self.clock = Clock()
        self.declog = DecisionLog(str(self.tmp / "arena"))
        raw = {"bot_id": BOT, "frozen_sha": SHA, "root": str(self.tmp / "arena"), "state_dir": str(self.tmp / "state"), "end_ms": T0 + 10 * 86_400_000,
               "max_hold_ms": H, "stop_file": str(self.tmp / "STOP"), "halt_file": str(self.tmp / "HALT"), "mode": "dry", **over}
        self.raw = raw
        self.port = StubPort(live=live)
        self.live_ok = self.tmp / "etc" / "LIVE_OK"
        self.tier = self.tmp / "etc" / "TIER"
        self.live_ok.parent.mkdir(exist_ok=True)
        self.live_cfg = live

    def bridge(self) -> alb.Bridge:
        raw = dict(self.raw)
        if self.live_cfg:
            raw.pop("stop_file"), raw.pop("halt_file")
            raw["mode"] = "live"
            cfg = alb.Config.from_dict(raw)
            cfg = alb.Config(**{**cfg.__dict__, "stop_file": self.raw["stop_file"], "halt_file": self.raw["halt_file"]})  # tests only: the pinned paths cannot be set via config
        else:
            cfg = alb.Config.from_dict(raw)
        return alb.Bridge(cfg, self.port, now_ms=self.clock, live_ok_path=self.live_ok, tier_path=self.tier, root_checks=False)

    def row(self, body: dict, ms: int | None = None) -> dict:
        return self.declog.append(BOT, self.clock.t - 100 if ms is None else ms, body)

    def start(self, sha: str = SHA) -> None:
        self.row({"kind": "start", "binding": "b", "frozen_sha": sha, "rules": [BOT]})

    def entry(self, oid: str, pool: str = "P1", ms: int | None = None, **meta) -> dict:
        return self.row({"kind": "entry", "order_id": oid, "pool": pool, "meta": meta, "decision_slot": 1000, "stake_sol": 0.05, "attempt_key": None}, ms)

    def exit(self, oid: str, reason: str = "tp", pool: str = "P1") -> dict:
        return self.row({"kind": "exit", "order_id": oid, "pool": pool, "reason": reason, "decision_slot": 1010, "attempt": 1})

    def log_rows(self, kind: str | None = None) -> list[dict]:
        p = self.tmp / "state" / f"bridge-log-{self.raw['mode'] if not self.live_cfg else 'live'}.jsonl"
        rows = [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []
        return [r for r in rows if kind is None or r["kind"] == kind]


class TmpCase(unittest.TestCase):
    def setUp(self) -> None:
        self._d = tempfile.TemporaryDirectory()
        self.addCleanup(self._d.cleanup)
        self.tmp = self._d.name


class EntryExitTests(TmpCase):
    def test_entry_then_exit_stub_port(self):
        e = Env(self.tmp)
        e.start()
        e.entry("o1")
        b = e.bridge()
        b.tick()
        self.assertEqual([c[:2] for c in e.port.calls], [("buy", "o1")])
        self.assertEqual(e.port.calls[0][3], alb.TIERS["T0"]["stake_lamports"])  # the canary stake, not the paper stake
        sent = e.log_rows("buy_sent")[0]
        self.assertEqual(sent["paper_stake_sol"], 0.05)
        self.assertEqual(sent["decision_to_send_ms"], 100)
        self.assertEqual(b.state.orders["o1"]["status"], "buy_sent")
        e.port.events.append({"order_id": "o1", "event": "buy_landed", "landed_slot": 1002})
        b.tick()
        self.assertEqual(b.state.orders["o1"]["status"], "open")
        self.assertEqual(e.log_rows("buy_landed")[0]["decision_to_landed_slots"], 2)
        e.exit("o1")
        b.tick()
        self.assertEqual(e.port.calls[-1], ("sell", "o1", "tp"))
        e.port.events.append({"order_id": "o1", "event": "sell_landed", "landed_slot": 1015, "pnl_lamports": -3_000_000})
        b.tick()
        self.assertEqual(b.state.orders["o1"]["status"], "closed")
        self.assertEqual(b.state.realized_lamports, -3_000_000)
        self.assertEqual(e.log_rows("sell_landed")[0]["decision_to_landed_slots"], 5)

    def test_duplicate_entry_order_id_one_buy(self):
        e = Env(self.tmp)
        e.start()
        e.entry("o1")
        e.entry("o1")  # a re-emitted decision with a new chained row
        b = e.bridge()
        b.tick()
        self.assertEqual(sum(1 for c in e.port.calls if c[0] == "buy"), 1)
        self.assertEqual(b.state.counts["duplicate_entry"], 1)

    def test_restart_does_not_rebuy_even_with_cursor_lost(self):
        e = Env(self.tmp)
        e.start()
        e.entry("o1")
        b = e.bridge()
        b.tick()
        b2 = e.bridge()
        b2.tick()
        self.assertEqual(sum(1 for c in e.port.calls if c[0] == "buy"), 1)
        # crash window: the order is on disk but the cursor is not
        b3 = e.bridge()
        b3.state.cursor, b3.state.prev, b3.state.seq, b3.state.start_seen = {}, alb.GENESIS, -1, False
        b3.tick()
        self.assertEqual(sum(1 for c in e.port.calls if c[0] == "buy"), 1)
        self.assertEqual(b3.state.counts["duplicate_entry"], 1)

    def test_exit_before_landing_waits_and_never_double_sells(self):
        e = Env(self.tmp)
        e.start()
        e.entry("o1")
        e.exit("o1")
        b = e.bridge()
        b.tick()
        self.assertEqual([c[0] for c in e.port.calls], ["buy"])  # the sell waits for the buy
        e.port.events.append({"order_id": "o1", "event": "buy_landed", "landed_slot": 1003})
        b.tick()
        self.assertEqual([c[0] for c in e.port.calls], ["buy", "sell"])
        e.exit("o1")  # the book retries the exit while our sell is in flight
        b.tick()
        self.assertEqual([c[0] for c in e.port.calls], ["buy", "sell"])
        self.assertEqual(b.state.counts["exit_while_selling"], 1)

    def test_exit_for_unknown_order_ignored(self):
        e = Env(self.tmp)
        e.start()
        e.exit("nope")
        b = e.bridge()
        b.tick()
        self.assertEqual(e.port.calls, [])

    def test_torn_line_at_start_latches(self):
        e = Env(self.tmp)
        e.start()
        f = e.declog.files(BOT)[-1]
        with f.open("ab") as fh:
            fh.write(b'{"seq":1,"arm"')  # the writer is mid-append
        b = e.bridge()
        b.tick()  # verify at start sees a torn line: latched, and nothing reads it
        self.assertIn("chain_break", b.state.latches)


class ChainTests(TmpCase):
    def test_chain_break_halts_buys(self):
        e = Env(self.tmp)
        e.start()
        e.entry("o1")
        f = e.declog.files(BOT)[-1]
        lines = f.read_bytes().splitlines(keepends=True)
        lines[1] = lines[1].replace(b'"order_id":"o1"', b'"order_id":"oX"')  # tamper: the sha no longer matches
        f.write_bytes(b"".join(lines))
        b = e.bridge()
        b.tick()
        self.assertIn("chain_break", b.state.latches)
        self.assertEqual(e.port.calls, [])
        e.entry("o2")  # a later (still chain-broken) row: still nothing
        b.tick()
        self.assertEqual(e.port.calls, [])

    def test_duplicated_line_is_a_break(self):
        e = Env(self.tmp)
        e.start()
        b = e.bridge()
        b.tick()
        e.entry("o1")
        f = e.declog.files(BOT)[-1]
        lines = f.read_bytes().splitlines(keepends=True)
        f.write_bytes(b"".join(lines) + lines[-1])  # the same row twice
        b.tick()
        self.assertIn("chain_break", b.state.latches)
        self.assertEqual(sum(1 for c in e.port.calls if c[0] == "buy"), 0)  # the row before the break is not traded either: the read stops at the first bad row of the pass

    def test_break_mid_run_keeps_open_position_exitable_by_backstop(self):
        e = Env(self.tmp)
        e.start()
        e.entry("o1")
        b = e.bridge()
        b.tick()
        e.port.events.append({"order_id": "o1", "event": "buy_landed", "landed_slot": 1001})
        b.tick()
        f = e.declog.files(BOT)[-1]
        f.write_bytes(f.read_bytes() + b'{"seq":9,"junk":1}\n')
        b.tick()
        self.assertIn("chain_break", b.state.latches)
        e.clock.t += H + alb.BACKSTOP_EXTRA_MS + 1
        b.tick()
        self.assertEqual(e.port.calls[-1], ("sell", "o1", "time_stop_backstop"))

    def test_frozen_sha_mismatch_latches(self):
        e = Env(self.tmp)
        e.start(sha="b" * 64)
        e.entry("o1")
        b = e.bridge()
        b.tick()
        self.assertIn("frozen_sha_mismatch", b.state.latches)
        self.assertEqual(e.port.calls, [])

    def test_no_start_row_no_buy(self):
        e = Env(self.tmp)
        e.entry("o1")
        b = e.bridge()
        b.tick()
        self.assertEqual(e.port.calls, [])


class GateTests(TmpCase):
    def run_entry(self, e: Env, **kw) -> alb.Bridge:
        e.start()
        e.entry("o1", **kw)
        b = e.bridge()
        b.tick()
        return b

    def test_floor_breach_refused(self):
        e = Env(self.tmp)
        e.port._balance = 400_000_000 + 20_000_000 + alb.FEE_RESERVE_LAMPORTS + alb.RENT_RESERVE_LAMPORTS + alb.BUFFER_LAMPORTS - 1
        b = self.run_entry(e)
        self.assertEqual(e.port.calls, [])
        self.assertEqual(e.log_rows("entry_refused")[0]["why"], "wallet_floor")
        e.port._balance += 1  # exactly at the floor: allowed
        e.entry("o2", pool="P2")
        b.tick()
        self.assertEqual(e.port.calls[0][:2], ("buy", "o2"))

    def test_open_stakes_count_at_zero(self):
        e = Env(self.tmp)
        floor = 400_000_000 + alb.FEE_RESERVE_LAMPORTS + alb.RENT_RESERVE_LAMPORTS + alb.BUFFER_LAMPORTS
        e.port._balance = floor + 2 * 20_000_000 + alb.SELL_FEE_RESERVE_LAMPORTS - 1  # one stake for the new buy, one for the open position: one short
        e.start()
        e.entry("o1")
        e.entry("o2", pool="P2")
        b = e.bridge()
        b.tick()
        self.assertEqual([c[1] for c in e.port.calls], ["o1"])  # the second needs a balance of floor + 2 stakes + its own
        self.assertEqual(e.log_rows("entry_refused")[0]["why"], "wallet_floor")

    def test_unreadable_balance_refused(self):
        e = Env(self.tmp)
        e.port._balance = None
        self.run_entry(e)
        self.assertEqual(e.log_rows("entry_refused")[0]["why"], "balance_unreadable")

    def test_stale_decision_refused(self):
        e = Env(self.tmp)
        e.start()
        e.entry("o1", ms=e.clock.t - alb.MAX_DECISION_AGE_MS - 1)
        b = e.bridge()
        b.tick()
        self.assertEqual(e.log_rows("entry_refused")[0]["why"], "stale_decision")
        self.assertEqual(b.state.latches, {})  # a refusal, not a halt

    def test_stop_and_halt_files(self):
        e = Env(self.tmp)
        Path(e.raw["stop_file"]).write_text("")
        b = self.run_entry(e)
        self.assertEqual(e.log_rows("entry_refused")[0]["why"], "stop_file")
        Path(e.raw["halt_file"]).write_text("")
        e.entry("o2", pool="P2")
        before = b.state.seq
        b.tick()
        self.assertEqual(b.state.seq, before)  # HALT: the tail is not even read
        self.assertEqual(e.port.calls, [])

    def test_stop_file_still_lets_exits_through(self):
        e = Env(self.tmp)
        e.start()
        e.entry("o1")
        b = e.bridge()
        b.tick()
        e.port.events.append({"order_id": "o1", "event": "buy_landed", "landed_slot": 1001})
        b.tick()
        Path(e.raw["stop_file"]).write_text("")
        e.exit("o1")
        b.tick()
        self.assertEqual(e.port.calls[-1][:2], ("sell", "o1"))

    def test_end_ms(self):
        e = Env(self.tmp, end_ms=T0 + 500)
        self.run_entry(e)
        self.assertEqual(e.log_rows("entry_refused")[0]["why"], "end_ms")

    def test_max_open_and_trades_per_day(self):
        e = Env(self.tmp, max_open=1, max_trades_per_day=2)
        e.start()
        for i in range(3):
            e.entry(f"o{i}", pool=f"P{i}")
        b = e.bridge()
        b.tick()
        self.assertEqual([c[1] for c in e.port.calls], ["o0"])
        self.assertEqual(e.log_rows("entry_refused")[0]["why"], "max_open")
        e.port.events.append({"order_id": "o0", "event": "buy_failed", "pnl_lamports": -505_000})
        b.tick()
        e.entry("o3", pool="P3")
        b.tick()
        e.port.events.append({"order_id": "o3", "event": "buy_failed", "pnl_lamports": -505_000})
        b.tick()
        e.entry("o4", pool="P4")
        b.tick()
        self.assertEqual([c[1] for c in e.port.calls], ["o0", "o3"])
        self.assertEqual(e.log_rows("entry_refused")[-1]["why"], "max_trades_per_day")

    def test_mint_never_rebought(self):
        e = Env(self.tmp)
        e.start()
        e.entry("o1", mint="MM")
        e.entry("o2", pool="P2", mint="MM")
        b = e.bridge()
        b.tick()
        self.assertEqual([c[1] for c in e.port.calls], ["o1"])
        self.assertEqual(e.log_rows("entry_refused")[0]["why"], "already_bought")

    def test_universe_refusal(self):
        e = Env(self.tmp)
        e.port.universe = "universe_v_out_of_range"
        self.run_entry(e)
        self.assertEqual(e.port.calls, [])

    def test_loss_caps(self):
        e = Env(self.tmp, daily_loss_lamports=10_000_000, per_trade_loss_lamports=8_000_000, total_loss_lamports=12_000_000)
        e.start()
        e.entry("o1", pool="P1")
        b = e.bridge()
        b.tick()
        e.port.events += [{"order_id": "o1", "event": "buy_landed", "landed_slot": 1}, {"order_id": "o1", "event": "sell_landed", "pnl_lamports": -7_000_000, "landed_slot": 2}]
        b.tick()
        self.assertEqual(b.state.latches, {})  # -7M: under the per-trade cap (8M)
        e.entry("o2", pool="P2")
        b.tick()
        self.assertEqual(e.port.calls[-1][:2], ("buy", "o2"))
        e.port.events += [{"order_id": "o2", "event": "buy_landed", "landed_slot": 1}, {"order_id": "o2", "event": "sell_landed", "pnl_lamports": -4_000_000, "landed_slot": 2}]
        b.tick()
        self.assertEqual(b.state.latches, {})  # -11M: over the daily cap (10M), under the total (12M)
        e.entry("o3", pool="P3")
        b.tick()
        self.assertEqual([c[1] for c in e.port.calls if c[0] == "buy"], ["o1", "o2"])
        self.assertEqual(e.log_rows("entry_refused")[-1]["why"], "daily_loss_cap")
        e.clock.t += 86_400_000  # next UTC day: the daily cap resets
        e.entry("o4", pool="P4", ms=e.clock.t - 10)
        b.tick()
        self.assertEqual(e.port.calls[-1][:2], ("buy", "o4"))
        e.port.events += [{"order_id": "o4", "event": "buy_landed", "landed_slot": 1}, {"order_id": "o4", "event": "sell_landed", "pnl_lamports": -1_000_000, "landed_slot": 2}]
        b.tick()
        self.assertIn("total_loss_cap", b.state.latches)  # -12M total: latched
        e.entry("o5", pool="P5", ms=e.clock.t - 10)
        b.tick()
        self.assertEqual(e.log_rows("entry_refused")[-1]["why"].split(":")[0], "latched")

    def test_per_trade_cap_latches(self):
        e = Env(self.tmp, per_trade_loss_lamports=8_000_000)
        e.start()
        e.entry("o1")
        b = e.bridge()
        b.tick()
        e.port.events += [{"order_id": "o1", "event": "buy_landed", "landed_slot": 1}, {"order_id": "o1", "event": "sell_landed", "pnl_lamports": -8_000_000, "landed_slot": 2}]
        b.tick()
        self.assertIn("per_trade_loss_cap", b.state.latches)
        e.entry("o2", pool="P2")
        b.tick()
        self.assertEqual([c[1] for c in e.port.calls if c[0] == "buy"], ["o1"])

    def test_stuck_sell_latches(self):
        e = Env(self.tmp)
        e.start()
        e.entry("o1")
        b = e.bridge()
        b.tick()
        e.port.events += [{"order_id": "o1", "event": "buy_landed", "landed_slot": 1}, {"order_id": "o1", "event": "sell_stuck"}]
        b.tick()
        self.assertIn("stuck_sell", b.state.latches)


class BackstopTests(TmpCase):
    def open_one(self, e: Env) -> alb.Bridge:
        e.start()
        e.entry("o1", mint="MM")
        b = e.bridge()
        b.tick()
        e.port.events.append({"order_id": "o1", "event": "buy_landed", "landed_slot": 1001})
        b.tick()
        return b

    def test_time_stop_backstop(self):
        e = Env(self.tmp)
        b = self.open_one(e)
        e.clock.t += H + alb.BACKSTOP_EXTRA_MS - 10
        b.tick()
        self.assertEqual([c[0] for c in e.port.calls], ["buy"])
        e.clock.t += 20
        b.tick()
        self.assertEqual(e.port.calls[-1], ("sell", "o1", "time_stop_backstop"))

    def test_sl_backstop(self):
        e = Env(self.tmp, sl_backstop=0.4)
        b = self.open_one(e)
        e.port.returns["MM"] = -0.39
        b.tick()
        self.assertEqual([c[0] for c in e.port.calls], ["buy"])
        e.port.returns["MM"] = -0.41
        b.tick()
        self.assertEqual(e.port.calls[-1], ("sell", "o1", "sl_backstop"))

    def test_failed_sell_retried_after_backoff(self):
        e = Env(self.tmp)
        b = self.open_one(e)
        e.exit("o1")
        b.tick()
        e.port.events.append({"order_id": "o1", "event": "sell_failed", "label": "slippage"})
        b.tick()
        self.assertEqual(b.state.orders["o1"]["status"], "open")
        e.exit("o1")
        b.tick()  # inside the retry window
        self.assertEqual([c[0] for c in e.port.calls], ["buy", "sell"])
        e.clock.t += alb.SELL_RETRY_MS + 1
        e.exit("o1")
        b.tick()
        self.assertEqual([c[0] for c in e.port.calls], ["buy", "sell", "sell"])

    def test_warm_plan_cadence_and_clamp(self):
        e = Env(self.tmp, warm_ms=100)  # clamped up to 500
        b = self.open_one(e)
        self.assertEqual(b.cfg.warm_ms, 500)
        n = len(e.port.warmed)
        e.clock.t += 100
        b.tick()
        self.assertEqual(len(e.port.warmed), n)
        e.clock.t += 500
        b.tick()
        self.assertEqual(len(e.port.warmed), n + 1)
        self.assertEqual(e.port.warmed[-1], ["MM"])
        self.assertEqual((alb.clamp_warm_ms(9_999), alb.clamp_warm_ms("x")), (2_000, 1_000))


class LiveGateTests(TmpCase):
    def test_live_ok_missing_means_dry_only(self):
        e = Env(self.tmp, live=True)
        e.start()
        e.entry("o1")
        b = e.bridge()
        b.tick()
        self.assertEqual(e.port.calls, [])  # the live port saw nothing
        row = e.log_rows("buy_sent")[0]
        self.assertTrue(row["would_send"])
        self.assertEqual(row["dry_reason"], "live_ok_missing")
        self.assertEqual(b.dry_port.would_send[0]["side"], "buy")

    def test_live_ok_present_sends_via_live_port(self):
        e = Env(self.tmp, live=True)
        e.live_ok.write_text("")
        e.start()
        e.entry("o1")
        b = e.bridge()
        b.tick()
        self.assertEqual(e.port.calls[0][:2], ("buy", "o1"))
        self.assertFalse(e.log_rows("buy_sent")[0]["would_send"])
        # LIVE_OK removed while the position is open: new buys go dry, the open position still exits live
        e.port.events.append({"order_id": "o1", "event": "buy_landed", "landed_slot": 5})
        b.tick()
        e.live_ok.unlink()
        e.entry("o2", pool="P2")
        e.exit("o1")
        b.tick()
        self.assertEqual([c[:2] for c in e.port.calls], [("buy", "o1"), ("sell", "o1")])
        self.assertEqual(b.dry_port.would_send[0]["order_id"], "o2")

    def test_unsafe_live_ok_is_not_ok(self):
        e = Env(self.tmp, live=True)
        e.live_ok.write_text("")  # owned by the test user, not root: fails the real root check
        b = e.bridge()
        b.root_checks = True
        self.assertEqual(b.effective_live(), (False, "live_ok_unsafe"))
        b.live_ok_path = e.tmp / "etc" / "absent"
        self.assertEqual(b.effective_live(), (False, "live_ok_missing"))

    def test_tier_file_read_only_and_t0_only(self):
        e = Env(self.tmp)
        b = e.bridge()
        self.assertEqual(b.tier_limits()[0:3:2], ("T0", "tier_file_missing"))
        e.tier.write_text("T2\n")
        self.assertEqual(b.tier_limits()[0:3:2], ("T0", "tier_file_invalid"))  # H5's T2 is not a bridge tier
        e.tier.write_text("T0\n")
        self.assertEqual(b.tier_limits()[0:3:2], ("T0", None))
        self.assertFalse(any(isinstance(n, str) and "write" in n for n in ("read_tier",)))  # the bridge never writes LIVE_OK or TIER
        src = Path(alb.__file__).read_text()
        self.assertNotIn("LIVE_OK_PATH.write", src)
        self.assertNotIn("tier_path.write", src)

    def test_mode_port_mismatch_refused(self):
        e = Env(self.tmp, live=True)
        raw = {**e.raw, "mode": "dry"}
        with self.assertRaises(SystemExit):
            alb.Bridge(alb.Config.from_dict(raw), StubPort(live=True), now_ms=e.clock)
        with self.assertRaises(SystemExit):
            alb.Bridge(alb.Config.from_dict({k: v for k, v in raw.items() if k not in ("stop_file", "halt_file")} | {"mode": "live"}), StubPort(live=False), now_ms=e.clock)


class ReconcileTests(TmpCase):
    def landed(self, e: Env) -> alb.Bridge:
        e.start()
        e.entry("o1", mint="MM")
        b = e.bridge()
        b.tick()
        e.port.events.append({"order_id": "o1", "event": "buy_landed", "landed_slot": 1003})
        b.tick()
        return b

    def test_crash_during_sell_backstop_sells_once(self):
        e = Env(self.tmp, live=True)
        e.live_ok.write_text("")
        b = self.landed(e)
        b.state.orders["o1"]["status"] = "sell_sending"  # the write-ahead was saved, the executor never heard of the sell
        b._save()
        e.port.held = {"MM": "open"}
        b2 = e.bridge()  # restart: reconciled at start
        self.assertEqual(b2.state.orders["o1"]["status"], "open")
        e.clock.t += H + alb.BACKSTOP_EXTRA_MS + 10_000
        e.port.returns = {"MM": -0.9}
        for _ in range(3):
            e.clock.t += 6000
            b2.tick()
        self.assertEqual([c[0] for c in e.port.calls], ["buy", "sell"])  # exactly one sell
        self.assertEqual(b2.state.orders["o1"]["status"], "sell_sent")

    def test_crash_during_sell_pending_tx_kept(self):
        e = Env(self.tmp, live=True)
        e.live_ok.write_text("")
        b = self.landed(e)
        b.state.orders["o1"]["status"] = "sell_sending"
        b._save()
        e.port.held = {"MM": "pending"}
        b2 = e.bridge()
        self.assertEqual(b2.state.orders["o1"]["status"], "sell_sent")
        e.exit("o1")
        b2.tick()
        self.assertEqual([c[0] for c in e.port.calls], ["buy"])  # the signed sell is in flight: no second sell

    def test_stale_selling_at_runtime_reopens_then_time_stop(self):
        e = Env(self.tmp, live=True)
        e.live_ok.write_text("")
        b = self.landed(e)
        e.exit("o1")
        b.tick()
        self.assertEqual(b.state.orders["o1"]["status"], "sell_sent")
        e.port.held = {"MM": "open"}  # the executor lost the sell (expired, dropped): nothing pending
        e.clock.t += alb.SELL_RETRY_MS + 1
        b.tick()
        self.assertEqual(b.state.orders["o1"]["status"], "open")
        e.clock.t += H + alb.BACKSTOP_EXTRA_MS
        b.tick()
        self.assertEqual([c[0] for c in e.port.calls], ["buy", "sell", "sell"])

    def test_sell_without_position_closed(self):
        e = Env(self.tmp, live=True)
        e.live_ok.write_text("")
        b = self.landed(e)
        b.state.orders["o1"]["status"] = "sell_sending"
        b._save()
        e.port.held = {}
        b2 = e.bridge()
        self.assertEqual(b2.state.orders["o1"]["status"], "closed")
        self.assertTrue(b2.state.orders["o1"]["closed_unknown_pnl"])

    def test_crash_during_buy(self):
        e = Env(self.tmp, live=True)
        e.live_ok.write_text("")
        e.start()
        e.entry("o1", mint="MM")
        b = e.bridge()
        b.tick()
        b.state.orders["o1"]["status"] = "buy_sending"
        b._save()
        for held, want in (({}, "refused"), ({"MM": "pending"}, "buy_sent"), ({"MM": "open"}, "open")):
            b.state.orders["o1"]["status"] = "buy_sending"
            b._save()
            e.port.held = held
            self.assertEqual(e.bridge().state.orders["o1"]["status"], want)
        e.port.held = {}
        b.state.orders["o1"]["status"] = "buy_sending"
        b._save()
        b3 = e.bridge()
        self.assertEqual(len([o for o in b3.open_orders().values()]), 0)  # the max_open slot is free again
        self.assertEqual(sum(1 for c in e.port.calls if c[0] == "buy"), 1)  # and no second buy

    def test_dry_orders_have_no_executor_to_reconcile(self):
        e = Env(self.tmp)
        e.start()
        e.entry("o1")
        b = e.bridge()
        b.tick()
        b.state.orders["o1"]["status"] = "sell_sending"
        b._save()
        self.assertEqual(e.bridge().state.orders["o1"]["status"], "closed")


class SpecGapTests(TmpCase):
    def test_partial_exit_refused(self):
        e = Env(self.tmp)
        e.start()
        e.entry("o1")
        b = e.bridge()
        b.tick()
        e.port.events.append({"order_id": "o1", "event": "buy_landed", "landed_slot": 1})
        b.tick()
        e.row({"kind": "exit", "order_id": "o1", "pool": "P1", "reason": "x", "decision_slot": 2, "frac": 0.5})
        b.tick()
        self.assertEqual([c[0] for c in e.port.calls], ["buy"])
        self.assertEqual(b.state.counts["exit_partial_refused"], 1)

    def test_close_blocked_hook_denies_on_error_and_true(self):
        for hook in (lambda m: True, lambda m: None, lambda m: 1 / 0):
            e = Env(tempfile.mkdtemp())
            e.start()
            e.entry("o1")
            b = e.bridge()
            b.close_blocked = hook
            b.tick()
            self.assertEqual(e.port.calls, [])
        e = Env(tempfile.mkdtemp())
        e.start()
        e.entry("o1")
        b = e.bridge()
        b.close_blocked = lambda m: False
        b.tick()
        self.assertEqual(len(e.port.calls), 1)

    def test_row_mint_must_match_pool_mint(self):
        e = Env(self.tmp)
        e.port.pool_mint = "REAL"
        e.start()
        e.entry("o1", mint="FAKE")
        b = e.bridge()
        b.tick()
        self.assertEqual(e.port.calls, [])
        self.assertEqual(e.log_rows("entry_refused")[0]["why"], "mint_pool_mismatch")

    def test_dry_routed_orders_do_not_use_live_headroom(self):
        e = Env(self.tmp, live=True, max_open=1)
        e.start()
        e.entry("o1", mint="A")
        b = e.bridge()
        b.tick()  # LIVE_OK missing: dry-routed
        self.assertEqual(e.port.calls, [])
        e.live_ok.write_text("")
        e.entry("o2", pool="P2", mint="B")
        b.tick()
        self.assertEqual([c[:2] for c in e.port.calls], [("buy", "o2")])  # max_open 1 was not used up by the dry order

    def test_presend_end_ms_recheck(self):
        e = Env(self.tmp)
        e.start()
        e.entry("o1")
        b = e.bridge()
        orig = e.port.balance

        def slow_balance():
            e.clock.t = e.raw["end_ms"] + 1  # the clock crosses end_ms during the pre-send RPCs
            return orig()

        e.port.balance = slow_balance
        b.tick()
        self.assertEqual(e.port.calls, [])
        self.assertEqual(e.log_rows("entry_refused")[-1]["why"], "end_ms")


class ConfigAndCliTests(TmpCase):
    def base(self, **kw):
        return {"bot_id": BOT, "frozen_sha": SHA, "root": self.tmp, "state_dir": self.tmp, "end_ms": T0, "max_hold_ms": H, **kw}

    def test_config_only_tightens(self):
        c = alb.Config.from_dict(self.base(stake_lamports=10**9, max_open=9, daily_loss_lamports=10**12, total_loss_lamports=10**12, wallet_floor_lamports=1))
        t = alb.TIERS["T0"]
        self.assertEqual((c.stake_lamports, c.max_open, c.daily_loss_lamports, c.total_loss_lamports), (t["stake_lamports"], t["max_open"], t["daily_loss_lamports"], t["total_loss_lamports"]))
        self.assertEqual(c.wallet_floor_lamports, alb.MIN_WALLET_FLOOR)
        c = alb.Config.from_dict(self.base(stake_lamports=5_000_000, max_open=1))
        self.assertEqual((c.stake_lamports, c.max_open), (5_000_000, 1))

    def test_bad_config(self):
        for bad in ({"stake_lamports": 0}, {"sl_backstop": 0.9}, {"mode": "paper"}, {"end_ms": None}, {"stake_lamports": True}):
            with self.assertRaises(ValueError):
                alb.Config.from_dict(self.base(**bad))
        with self.assertRaises(ValueError):
            alb.Config.from_dict(self.base(mode="live", stop_file="/x"))  # no path override in live

    def test_default_mode_is_dry(self):
        self.assertEqual(alb.Config.from_dict(self.base()).mode, "dry")
        self.assertEqual(alb.Config.__dataclass_fields__["mode"].default, "dry")

    def test_cli_live_needs_both_switches_and_dry_makes_no_live_port(self):
        arena = Path(self.tmp) / "arena"
        dl = DecisionLog(str(arena))
        now = int(__import__("time").time() * 1000)
        dl.append(BOT, now - 10, {"kind": "start", "binding": "b", "frozen_sha": SHA, "rules": [BOT]})
        dl.append(BOT, now - 5, {"kind": "entry", "order_id": "o1", "pool": "P1", "meta": {}, "decision_slot": 1, "stake_sol": 0.05, "attempt_key": None})
        for mode, flag in (("live", []), ("dry", ["--live"]), ("dry", [])):
            sd = Path(self.tmp) / f"s-{mode}-{len(flag)}"
            cfg = self.base(root=str(arena), state_dir=str(sd), mode=mode, end_ms=now + 10**9, stop_file=str(Path(self.tmp) / "nostop"), halt_file=str(Path(self.tmp) / "nohalt")) if mode == "dry" \
                else {k: v for k, v in self.base(root=str(arena), state_dir=str(sd), mode=mode, end_ms=now + 10**9).items()}
            cp = Path(self.tmp) / "c.json"
            cp.write_text(json.dumps(cfg))
            with mock.patch.object(alb, "make_live_port", side_effect=AssertionError("live port built")), contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(alb.main(["--config", str(cp), "--once", *flag]), 0)
            self.assertIn("mode=dry", out.getvalue())
            self.assertTrue((sd / "bridge-log-dry.jsonl").exists())
            rows = [json.loads(x) for x in (sd / "bridge-log-dry.jsonl").read_text().splitlines()]
            self.assertTrue(any(r["kind"] == "buy_sent" and r["would_send"] for r in rows))
            self.assertFalse((sd / "bridge-state-live.json").exists())

    def test_dry_state_never_shares_live_budget(self):
        e = Env(self.tmp)
        self.assertNotEqual(e.bridge().state_path.name, "bridge-state-live.json")


class LivePortTests(TmpCase):
    """The real BridgeExecutor over the probe tests' fake RPC: write-ahead buy, landing event, sell on the bridge's exit only."""

    def test_buy_land_sell_through_probe_send_path(self):
        from solders.keypair import Keypair

        from tools import probe_live as pl
        from tools.test_probe_executor import MINT, T0 as PT0, Clock as PClock
        from tools.test_probe_live import BAL, LiveRpc, land_buy, land_sell

        clock = PClock()
        rpc = LiveRpc(clock)
        raw = {"bot_id": BOT, "frozen_sha": SHA, "root": self.tmp, "state_dir": str(Path(self.tmp) / "st"), "end_ms": PT0 + 86_400_000, "max_hold_ms": H, "mode": "live"}
        cfg = alb.Config.from_dict(raw)
        port = alb.make_live_port(cfg, rpc, Keypair(), now_ms=clock)
        self.assertTrue(port.live)
        self.assertNotIn("secret", repr(port.ex).lower())
        order = {"order_id": "o1", "mint": MINT, "decision_ms": clock(), "stake_lamports": cfg.stake_lamports}
        self.assertEqual(port.universe_problem("pool", MINT) in (None, "universe_v_out_of_range"), True)
        ok, why = port.buy(order)
        self.assertTrue(ok, why)
        p = port.ex.state.pending[MINT]
        self.assertEqual(p["spend"], cfg.stake_lamports)
        self.assertTrue(rpc.sent)  # the existing send path sent it
        self.assertEqual(port.ex.state.attempts, 1)
        land_buy(port.ex, rpc)
        ev = port.ex.events
        self.assertEqual([(x["mint"], x["event"]) for x in ev], [(MINT, "buy_landed")])
        port.poll()
        # no exit_reason from the bridge: the probe's own tp/sl rules must NOT sell
        sent_before = len(rpc.sent)
        port.ex.poll_positions()
        self.assertEqual(len(rpc.sent), sent_before)
        ok, why = port.sell({"mint": MINT}, "book_exit")
        self.assertTrue(ok, why)
        self.assertEqual(port.ex.state.pending[MINT]["kind"], "sell")
        self.assertEqual(port.ex.state.pending[MINT]["reason"], "book_exit")
        land_sell(port.ex, rpc, proceeds=cfg.stake_lamports + 100_000)
        evs = port.poll()
        self.assertEqual(evs[-1]["event"], "sell_landed")
        self.assertIn("pnl_lamports", evs[-1])
        self.assertNotIn(MINT, port.ex.state.open)
        self.assertIsInstance(port.balance(), int)
        self.assertEqual(port.balance(), BAL)
        self.assertEqual(port.holdings(), {})
        self.assertEqual(port.ex.limits.end_ms, cfg.end_ms)  # N1: the executor enforces the bridge's end, not a far-future one


if __name__ == "__main__":
    unittest.main()
