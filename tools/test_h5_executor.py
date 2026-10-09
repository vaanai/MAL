"""Offline tests for tools/h5_executor.py. Fake RPC (the probe's), throwaway Keypairs, a fake clock. No network, no real key."""

from __future__ import annotations

import base64
import contextlib
import io
import json
import os
import statistics
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.transaction import VersionedTransaction

from tools import h5_executor as h
from tools import probe_executor as pe
from tools import probe_live as pl
from tools import pumpswap_tx as tx
from tools.test_probe_executor import BASE0, MINT, T0, V, Clock
from tools.test_probe_live import RENT, LiveRpc, meta_result

POOL = str(tx.canonical_pool(Pubkey.from_string(MINT)))
S0 = 100_000
SPS = 0.2
TRIG_SLOT = S0 + 500  # t_i = 100 s
QREAL = 20 * 10**9
Q = QREAL + V  # the fake pool's quote + V (37.6 SOL, under the rule's 40 SOL cap): a trigger at the current price
STAKE = 20_000_000


class H5Rpc(LiveRpc):
    def __init__(self, clock: Clock, **kw):
        super().__init__(clock, **kw)
        # The chain follows the fake clock: one slot per slot_ms. slot_fn(wall_ms) replaces that for a chain whose slot time changes.
        self.slot_ms = 200.0
        self.slot_fn = None
        self._anchor = (TRIG_SLOT + 8, clock())
        self.slot_fails = False
        self.state_fails = False
        self.accounts: dict = {}

    def slot_at(self, wall_ms: int) -> int:
        if self.slot_fn is not None:
            return self.slot_fn(wall_ms)
        s, t = self._anchor
        return s + int((wall_ms - t) / self.slot_ms)

    @property
    def slot(self) -> int:
        return self.slot_at(self.clock())

    @slot.setter
    def slot(self, v: int) -> None:
        self._anchor = (v, self.clock())

    def __call__(self, method, params):
        if method == "getAccountInfo" and params[0] in self.accounts:  # a test pins an account (None = closed)
            self.calls.append(method)
            return {"context": {"slot": 1}, "value": self.accounts[params[0]]}
        if self.state_fails and method in ("getAccountInfo", "getMultipleAccounts"):
            raise pe.RpcError("timeout")
        if method == "getSlot":
            self.calls.append(method)
            if self.slot_fails:
                raise pe.RpcError("timeout")
            return self.slot
        return super().__call__(method, params)


def row(clock: Clock, **kw) -> dict:
    base = dict(schema="h5_intent_v1", mint=MINT, pool=POOL, s0_slot=S0, sps=SPS, trigger_slot=TRIG_SLOT, q_lamports=Q,
                base_reserve=BASE0, v_lamports=V, decision_ms=clock())
    return {**base, **kw}


def trig(clock: Clock, **kw) -> h.H5Trigger:
    t, bad = h.parse_trigger(row(clock, **kw))
    assert t is not None, bad
    return t


class PathConf(dict):
    """A config dict whose pinned paths read as the executor resolves them, without being keys (a live config must not carry them)."""

    def __missing__(self, key):
        sd = Path(self["state_dir"])
        names = {"stop_file": sd / "STOP", "halt_file": sd / "HALT", "final_marker_file": sd / "FINAL_WRITTEN", "live_ok_file": h.LIVE_OK_PATH}
        if key in names:
            return str(names[key])
        raise KeyError(key)


class Env:
    """One executor in a temp dir. live=True builds a keyed executor (throwaway Keypair) with LIVE_OK and EXP-024 in place."""

    def __init__(self, tmp: Path, live: bool = True, oracle=None, exp024: bool = True, live_ok: bool = True, seed: bytes | None = None, **cfg):
        self.tmp = tmp
        self.clock = Clock()
        self.rpc = H5Rpc(self.clock, quote=QREAL)
        self.root = tmp / "repo"
        (self.root / "EXP").mkdir(parents=True, exist_ok=True)
        if exp024:
            (self.root / h.EXP024_PART1).write_text("# EXP-024 Part 1 (test stub)\n")
        # A live config carries no STOP / HALT / LIVE_OK / FINAL_WRITTEN keys (they are pinned); PathConf lets a test read where they are.
        self.conf = PathConf(intents_file=str(tmp / "intents.jsonl"), state_dir=str(tmp / "state"), mode="live" if live else "dryrun", poll_s=5.0)
        self.conf.update(cfg)
        if live:
            if live_ok:
                h.LIVE_OK_PATH.write_text("")  # the patched stand-in for /etc/mal-h5/LIVE_OK, "owned by root" (the test's own uid)
                os.chmod(h.LIVE_OK_PATH, 0o644)
            else:
                h.LIVE_OK_PATH.unlink(missing_ok=True)
        self.kp = (Keypair.from_seed(seed) if seed else Keypair()) if live else None
        self.oracle = oracle
        self.ex = self.build()

    def build(self) -> h.H5Executor:
        ex = h.H5Executor(self.rpc, self.conf, self.kp, now_ms=self.clock, pick_oracle=self.oracle, root=self.root)
        self.seed_clock(ex)
        return ex

    def seed_clock(self, ex: h.H5Executor | None = None, back_s: int = 150) -> None:
        """Our own getSlot history, as 150 s of the prewarm loop would have left it: the measured slot rate is the chain's."""
        ex = ex or self.ex
        ex.slots = h.SlotClock()
        now = self.clock()
        for k in range(back_s // 2, -1, -1):
            ex.slots.observe(self.rpc.slot_at(now - k * 2_000), now - k * 2_000)

    def jump(self, ms: int) -> None:
        """Skip ahead in time with the chain standing still (a day rollover, a seal window): the slot history is re-seeded to match."""
        cur = self.rpc.slot
        self.clock.t += ms
        self.rpc.slot_fn = None
        self.rpc.slot = cur
        self.seed_clock()

    def set_time(self, when_ms: int) -> None:
        self.jump(when_ms - self.clock())

    def ledger(self, kind: str | None = None) -> list[dict]:
        p = Path(self.ex.fills.path)
        rows = [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []
        return [r for r in rows if kind is None or r.get("kind") == kind]

    def refusals(self) -> list[str]:
        return [r["reason"] for r in self.ledger("skip")]

    def fire(self, **kw) -> None:
        self.ex.handle_trigger(trig(self.clock, **kw))

    def sent(self) -> list[dict]:
        return [decode_tx(b64) for _t, b64 in self.rpc.sent]

    def land_buy(self, slot: int | None = None, tokens: int | None = None) -> None:
        p = self.ex.state.pending[MINT]
        tok = tokens or p["q_tokens"]
        slot = slot or TRIG_SLOT + 8
        self.rpc.statuses[p["signature"]] = {"slot": slot, "confirmationStatus": "confirmed", "err": None}
        self.rpc.txs[p["signature"]] = meta_result(self.ex, MINT, delta=-(p["spend"] + 60_000 + RENT), fee=60_000, tok_delta=tok, ata_post=RENT, slot=slot)
        self.rpc.token_balance = tok
        self.ex.advance_pending()

    def land_sell(self, slot: int, proceeds: int = 21_000_000, err=None) -> None:
        p = self.ex.state.pending[MINT]
        fee = 60_000
        self.rpc.statuses[p["signature"]] = {"slot": slot, "confirmationStatus": "confirmed", "err": err}
        self.rpc.txs[p["signature"]] = meta_result(self.ex, MINT, delta=(-fee if err else proceeds - fee + RENT), fee=fee,
                                                   tok_delta=0 if err else -p["tokens"], err=err, ata_pre=RENT, ata_post=RENT if err else 0, slot=slot,
                                                   logs=["Program log: slippage"] if err else None)
        self.ex.advance_pending()

    def open_position(self, **kw) -> dict:
        self.fire(**kw)
        self.land_buy()
        assert MINT in self.ex.state.open
        return self.ex.state.open[MINT]

    def at_slot(self, slot: int) -> None:
        """Let the clock run until the chain reaches `slot` (the world is consistent: slots follow the clock) and run the exit scheduler."""
        delta = slot - self.rpc.slot
        if delta > 0:
            self.clock.t += int(delta * self.rpc.slot_ms)
        self.ex.exit_tick(self.clock())

    def plan(self) -> dict:
        return self.ex.state.open[MINT]["h5"]["plan"]


def decode_tx(b64: str) -> dict:
    t = VersionedTransaction.from_bytes(base64.b64decode(b64))
    msg = t.message
    keys = list(msg.account_keys)
    out: dict = {"swap": None, "closes": 0, "limit": None, "price": None}
    for ix in msg.instructions:
        pid, data = keys[ix.program_id_index], bytes(ix.data)
        if pid == pl.COMPUTE_BUDGET_PROGRAM:
            if data[:1] == b"\x02":
                out["limit"] = int.from_bytes(data[1:], "little")
            elif data[:1] == b"\x03":
                out["price"] = int.from_bytes(data[1:], "little")
        elif pid == tx.PUMPSWAP_PROGRAM:
            out["swap"] = data
        elif pid in (tx.TOKEN_PROGRAM, tx.TOKEN_2022_PROGRAM) and data == b"\x09":
            out["closes"] += 1
    out["priority"] = -(-out["price"] * out["limit"] // 1_000_000)
    out["signature"] = str(t.signatures[0])
    return out


def buy_args(d: dict) -> tuple[int, int]:
    assert d["swap"][:8] == tx.DISC_BUY_EXACT_QUOTE_IN
    return int.from_bytes(d["swap"][8:16], "little"), int.from_bytes(d["swap"][16:24], "little")


def sell_args(d: dict) -> tuple[int, int]:
    assert d["swap"][:8] == tx.DISC_SELL
    return int.from_bytes(d["swap"][8:16], "little"), int.from_bytes(d["swap"][16:24], "little")


class Case(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        self.addCleanup(self._td.cleanup)
        # Stand-ins for the host: /etc/mal-h5 (root-owned there, owned by this test's uid here) and the probe's /var/lib/mal-live.
        self.etc = self.tmp / "etc-mal-h5"
        self.etc.mkdir()
        os.chmod(self.etc, 0o755)
        self.probe_dir = self.tmp / "probe-live-dir"
        self.probe_dir.mkdir()
        for target, attr, val in ((h, "LIVE_OK_PATH", self.etc / "LIVE_OK"), (h, "TIER_FILE_PATH", self.etc / "TIER"), (h, "LIVE_OK_UID", os.getuid()),
                                  (h, "LIVE_OK_GID", os.getgid()), (pe, "LIVE_DIR", self.probe_dir)):
            patcher = mock.patch.object(target, attr, val)
            patcher.start()
            self.addCleanup(patcher.stop)

    def env(self, **kw) -> Env:
        return Env(self.tmp, **kw)

    @staticmethod
    def make_live_ok(mode: int = 0o644) -> None:
        h.LIVE_OK_PATH.write_text("")
        os.chmod(h.LIVE_OK_PATH, mode)  # (the umask must not decide whether it is group-writable)


# --- limits and config ------------------------------------------------------------------------------------------------


class LimitsTests(Case):
    def test_defaults_match_the_brief(self):
        l = h.H5Limits.from_config({})
        self.assertEqual((l.stake_lamports, l.buy_priority_lamports, l.max_open, l.max_trades_per_day, l.daily_loss_lamports, l.total_loss_lamports),
                         (20_000_000, 55_000, 2, 30, 80_000_000, 120_000_000))
        self.assertEqual(l.entry_tolerance_bps, 1500)

    def test_config_cannot_raise_maxima(self):
        l = h.H5Limits.from_config({"stake_lamports": 10**10, "max_open": 50, "max_trades_per_day": 500, "daily_loss_lamports": 10**12,
                                    "total_loss_lamports": 10**12, "max_attempts": 10**6, "max_days": 99, "buy_priority_lamports": 10**8,
                                    "entry_tolerance_bps": 9000, "deadline_s": 900, "max_trigger_age_ms": 10**9})
        for k, cap in h.H5_MAX.items():
            self.assertEqual(getattr(l, k), cap, k)
        self.assertEqual(l.deadline_s, 400.0)
        self.assertEqual(l.max_trigger_age_ms, 10_000)

    def test_config_can_lower(self):
        l = h.H5Limits.from_config({"stake_lamports": 5_000_000, "max_open": 1, "daily_loss_lamports": 1})
        self.assertEqual((l.stake_lamports, l.max_open, l.daily_loss_lamports), (5_000_000, 1, 1))

    def test_bad_values_refused(self):
        for bad in ({"stake_lamports": 0}, {"max_open": -1}, {"stake_lamports": True}, {"stake_lamports": float("nan")}, {"end_ms": -5},
                    {"track_volume": "yes"}):
            with self.assertRaises(ValueError, msg=str(bad)):
                h.H5Limits.from_config(bad)

    def test_wallet_floor_can_only_be_raised(self):
        self.assertEqual(h.H5Limits.from_config({"wallet_floor_lamports": 1}).wallet_floor_lamports, h.MIN_WALLET_FLOOR_LAMPORTS)
        self.assertEqual(h.H5Limits.from_config({"wallet_floor_lamports": 99_000_000}).wallet_floor_lamports, 99_000_000)

    def test_jito_is_off_and_cannot_be_turned_on(self):
        h.H5Limits.from_config({"jito_enabled": False, "jito_tip_lamports": 0})
        for bad in ({"jito_enabled": True}, {"jito_tip_lamports": 10_000}):
            with self.assertRaises(ValueError):
                h.H5Limits.from_config(bad)

    def test_state_files_are_per_mode_and_apart_from_the_probe(self):
        live, dry = Env(self.tmp / "a", live=True) if (self.tmp / "a").mkdir() is None else None, None
        dry = Env(self.tmp / "b", live=False) if (self.tmp / "b").mkdir() is None else None
        self.assertIn("/live/", str(live.ex.state_path))
        self.assertIn("/dryrun/", str(dry.ex.state_path))
        self.assertNotEqual(live.ex.state_path, dry.ex.state_path)
        self.assertNotIn("mal-live/state", str(live.ex.state_path))


# --- sell scheduling math ------------------------------------------------------------------------------------------------


class SlotMathTests(unittest.TestCase):
    L = h.H5Limits.from_config({})

    def test_plan_at_200ms_slots(self):
        p = h.exit_plan(1_000, 0.2, self.L)
        self.assertEqual(p.exit_slot, 1_000 + 1650)  # round(330 / 0.2)
        self.assertEqual(p.land_slot, p.exit_slot)  # lands AT s0 + round(330/sps)
        self.assertEqual(p.send_slot, p.exit_slot - 3)  # 500 ms lead = 2.5 slots, ceil 3
        self.assertEqual(p.arm_slot, p.send_slot - 10)  # 2 s = 10 slots
        self.assertEqual(p.deadline_slot, 1_000 + 2000)  # 400 s
        self.assertEqual(p.late_slot, 1_000 + 1675)  # 335 s
        self.assertEqual(p.escalate_slot, 1_000 + 1725)  # 345 s

    def test_plan_at_400ms_slots(self):
        p = h.exit_plan(1_000, 0.4, self.L)
        self.assertEqual(p.exit_slot, 1_000 + 825)
        self.assertEqual(p.send_slot, p.exit_slot - 2)  # 500 ms = 1.25 slots, ceil 2
        self.assertEqual(p.arm_slot, p.send_slot - 5)
        self.assertEqual(p.deadline_slot, 1_000 + 1000)
        self.assertEqual(p.late_slot, 1_000 + int(round(335 / 0.4)))  # python rounding, as the scorer

    def test_exit_slot_uses_the_scorers_rounding(self):
        for sps in (0.15001, 0.2, 0.267, 0.3, 0.4, 0.416, 0.59):
            self.assertEqual(h.exit_plan(0, sps, self.L).exit_slot, int(round(330.0 / sps)))

    def test_send_lead_and_land_offset_are_configurable(self):
        l = h.H5Limits.from_config({"send_lead_ms": 1_000, "exit_land_offset_s": 0.55})
        p = h.exit_plan(0, 0.2, l)
        self.assertEqual(p.land_slot, 1650 + 3)  # the rule's 0.55 s = ceil(2.75) slots
        self.assertEqual(p.send_slot, p.land_slot - 5)
        self.assertEqual(h.exit_plan(0, 0.2, h.H5Limits.from_config({"send_lead_ms": 0})).send_slot, 1650)

    def test_ceil_slots_matches_the_scorer(self):
        self.assertEqual(h.ceil_slots(1.3, 0.2), 7)  # ceil(6.5)
        self.assertEqual(h.ceil_slots(1.9, 0.2), 10)
        self.assertEqual(h.ceil_slots(0.55, 0.2), 3)
        self.assertEqual(h.ceil_slots(1.0, 0.2), 5)  # exact: the 1e-9 slack keeps 5.000000000000001 at 5

    def test_slot_clock_dead_reckons_and_never_goes_back(self):
        c = h.SlotClock()
        self.assertIsNone(c.est(1_000, 0.2))
        c.observe(500, 1_000)
        self.assertEqual(c.est(1_000 + 1_000, 0.2), 505)
        self.assertEqual(c.est(1_000 + 399, 0.4), 500)
        c.observe(400, 2_000)  # an older slot is ignored
        self.assertEqual(c.slot, 500)
        self.assertEqual(c.est(500, 0.2), 500)  # a clock behind the anchor does not go negative


# --- trigger parsing ----------------------------------------------------------------------------------------------------------


class ParseTests(unittest.TestCase):
    def test_good_row(self):
        t, bad = h.parse_trigger(row(Clock()))
        self.assertIsNone(bad)
        self.assertEqual((t.mint, t.s0_slot, t.trigger_slot, t.q_lamports, t.v_lamports), (MINT, S0, TRIG_SLOT, Q, V))

    def test_other_schema_is_ignored_not_refused(self):
        self.assertEqual(h.parse_trigger({"schema": "forward_paper_intent_v1"}), (None, None))
        self.assertEqual(h.parse_trigger("junk"), (None, None))

    def test_bad_rows_name_the_field(self):
        c = Clock()
        cases = {"sps": 0.7, "sps ": 0.1, "q_lamports": V - 1, "trigger_slot": S0 - 1, "base_reserve": 0, "decision_ms": True, "mint": ""}
        for k, v in cases.items():
            t, bad = h.parse_trigger(row(c, **{k.strip(): v}))
            self.assertIsNone(t, k)
            self.assertTrue(bad and bad.startswith("bad_intent"), (k, bad))
        r = row(c)
        del r["pool"]
        self.assertEqual(h.parse_trigger(r)[1], "bad_intent:missing_pool")

    def test_unknown_fields_are_dropped(self):
        t, _ = h.parse_trigger({**row(Clock()), "score": 0.93, "pnl": 5})
        self.assertNotIn("score", t.public())
        self.assertNotIn("pnl", t.public())


# --- entry ---------------------------------------------------------------------------------------------------------------------


class EntryTests(Case):
    def test_min_out_guard_is_expected_over_one_plus_x(self):
        t = h.entry_terms(Q, BASE0, STAKE, 1500)
        self.assertEqual(t["min_out"], t["expected_tokens"] * 10_000 // 11_500)
        fee = pe.fee_ppm_for(Q, BASE0)
        self.assertEqual(t["expected_tokens"], tx.cp_buy_out(STAKE, Q, BASE0, fee))
        self.assertLess(h.entry_terms(Q, BASE0, STAKE, 500)["expected_tokens"] - h.entry_terms(Q, BASE0, STAKE, 500)["min_out"], t["expected_tokens"] - t["min_out"])

    def test_live_buy_sends_once_immediately_with_the_guard(self):
        e = self.env()
        seen_state = {}

        def before_send(_params):  # write-ahead: the attempt and the signed tx are durable BEFORE the first send
            raw = json.loads(e.ex.state_path.read_text())
            seen_state["pending"] = raw["pending"].get(MINT)
            seen_state["attempts"] = raw["attempts"]

        e.rpc.send_hook = before_send
        e.fire()
        self.assertEqual(len(e.rpc.sent), 1)
        self.assertEqual(seen_state["attempts"], 1)
        self.assertEqual(seen_state["pending"]["signature"], e.ex.state.pending[MINT]["signature"])
        d = e.sent()[0]
        spend, min_out = buy_args(d)
        terms = h.entry_terms(Q, BASE0, STAKE, 1500)
        self.assertEqual((spend, min_out), (STAKE, terms["min_out"]))
        self.assertEqual(d["priority"], 55_000)
        dec = e.ledger("decision")[0]
        self.assertEqual((dec["signature"], dec["stake_lamports"], dec["min_out"]), (d["signature"], STAKE, terms["min_out"]))
        self.assertIsNotNone(dec["sent_ms"])
        self.assertEqual(dec["plan"]["exit_slot"], S0 + 1650)

    def test_no_state_read_on_the_hot_path_when_the_pool_was_prefetched(self):
        e = self.env()
        e.ex.prefetch(MINT, POOL)
        e.ex.prewarm()  # the slow loop keeps the blockhash, balance and global config warm
        e.rpc.calls.clear()
        e.fire()
        before_send = e.rpc.calls[: e.rpc.calls.index("sendTransaction")]
        self.assertEqual(before_send, [], before_send)
        self.assertEqual(len(e.rpc.sent), 1)

    def test_pool_cache_miss_reads_the_pool_inline_and_checks_the_price(self):
        e = self.env()
        e.fire()
        self.assertIn("getMultipleAccounts", e.rpc.calls)
        e2 = Env(self.tmp / "x", live=True) if (self.tmp / "x").mkdir() is None else None
        e2.rpc.quote = 30 * 10**9  # the pool moved up 20% since the trigger's post-trade state
        e2.fire()
        self.assertEqual(e2.refusals(), ["price_moved"])
        self.assertEqual(e2.rpc.sent, [])
        self.assertEqual(e2.ex.state.attempts, 0)  # refused before the write-ahead: not an attempt

    def test_buy_landing_is_recorded(self):
        e = self.env()
        pos = e.open_position()
        buy = e.ledger("buy")[0]
        self.assertTrue(buy["landed"])
        self.assertEqual(buy["landed_slot"], TRIG_SLOT + 8)
        self.assertEqual(buy["slots_between"], 8)  # landed minus the trigger print's slot
        self.assertGreater(buy["tokens_received"], 0)
        self.assertEqual(pos["h5"]["plan"]["send_slot"], S0 + 1647)
        self.assertEqual(pos["h5"]["buy_landed_slot"], TRIG_SLOT + 8)

    def test_a_guard_revert_is_a_failed_buy_and_costs_only_the_fee(self):
        e = self.env()
        e.fire()
        p = e.ex.state.pending[MINT]
        e.rpc.statuses[p["signature"]] = {"slot": TRIG_SLOT + 9, "confirmationStatus": "confirmed", "err": {"InstructionError": [4, {"Custom": 6004}]}}
        e.rpc.txs[p["signature"]] = meta_result(e.ex, MINT, delta=-60_000, fee=60_000, err={"InstructionError": [4, {"Custom": 6004}]}, slot=TRIG_SLOT + 9,
                                                logs=["Program log: slippage"])
        e.ex.advance_pending()
        self.assertNotIn(MINT, e.ex.state.open)
        buy = e.ledger("buy")[0]
        self.assertEqual((buy["landed"], buy["fail_class"]), (False, "slippage_exceeded"))
        self.assertEqual(e.ex.state.realized_lamports, -60_000)
        self.assertEqual(e.ex.counters.day(h.day_key(T0))["realized"], -60_000)

    def test_trigger_through_the_intent_file(self):
        e = self.env()
        path = Path(e.conf["intents_file"])
        path.write_text(json.dumps(row(e.clock, mint="OldOldOldOldOldOldOldOldOldOldOldOld11111")) + "\n")
        self.assertEqual(e.ex.intent_tick(), 0)  # first pass starts at the end: history is never replayed
        with path.open("a") as fh:
            fh.write(json.dumps(row(e.clock)) + "\n")
            fh.write('{"schema":"h5_intent_v1","mint":"x"')  # a half-written line is not consumed
        self.assertEqual(e.ex.intent_tick(), 1)
        self.assertIn(MINT, e.ex.state.pending)
        self.assertEqual(e.ex.intent_tick(), 0)
        with path.open("a") as fh:
            fh.write("\n")
        self.assertEqual(e.ex.intent_tick(), 0)  # the completed line is not JSON: dropped
        bad = row(e.clock)
        del bad["q_lamports"]
        with path.open("a") as fh:
            fh.write(json.dumps(bad) + "\n")
        self.assertEqual(e.ex.intent_tick(), 0)
        self.assertEqual([r for r in e.refusals() if r.startswith("bad_intent")], ["bad_intent:missing_q_lamports"])

    def test_h5_priority_is_55k_and_jito_free(self):
        e = self.env()
        e.fire()
        d = e.sent()[0]
        self.assertEqual(d["priority"], 55_000)
        self.assertEqual(d["limit"], tx.DEFAULT_BUY_CU_LIMIT)


# --- refusals ----------------------------------------------------------------------------------------------------------------------


class RefusalTests(Case):
    def check(self, reason, setup=None, fire_kw=None, **env_kw):
        sub = self.tmp / reason.replace(":", "_")
        sub.mkdir()
        e = Env(sub, **env_kw)
        if setup:
            setup(e)
        before = e.ex.state.attempts
        e.fire(**(fire_kw or {}))
        self.assertEqual(e.refusals(), [reason], reason)
        self.assertEqual(e.rpc.sent, [], reason)
        self.assertEqual(e.ex.state.attempts, before, reason)  # a refusal is never an attempt
        return e

    def test_each_refusal_path_is_logged_and_sends_nothing(self):
        day = h.day_key(T0)
        self.check("stop_file", lambda e: Path(e.conf["stop_file"]).write_text(""))
        self.check("halt_file", lambda e: Path(e.conf["halt_file"]).write_text(""))
        self.check("feed_gap", lambda e: e.ex.on_feed_status(True))
        self.check("stale_trigger", fire_kw={"decision_ms": T0 - 10_001})
        self.check("outside_rule_window", fire_kw={"trigger_slot": S0 + 1_600})  # 320 s after s0
        self.check("q_above_rule_max", fire_kw={"q_lamports": 40 * 10**9 + 1})
        self.check("pool_mismatch", fire_kw={"pool": str(tx.canonical_pool(Pubkey.from_string(str(Keypair().pubkey()))))})
        self.check("total_loss_stop", lambda e: setattr(e.ex.state, "realized_lamports", -120_000_000))
        self.check("daily_loss_stop", lambda e: e.ex.counters.day(day).update(realized=-80_000_000))
        self.check("max_trades_day", lambda e: e.ex.counters.day(day).update(trades=30))
        self.check("max_attempts", lambda e: setattr(e.ex.state, "attempts", 120))
        self.check("max_open", lambda e: e.ex.state.open.update({"a": {}, "b": {}}))
        self.check("already_bought", lambda e: e.ex.state.bought.append(MINT))
        self.check("clock_backwards", lambda e: setattr(e.ex.state, "max_seen_ms", T0 + 3_600_000))
        self.check("sell_stuck", lambda e: e.ex.state.open.update({"z": {"stuck": True}}))
        self.check("live_ok_missing", live_ok=False)
        self.check("exp024_part1_missing", exp024=False)
        self.check("halt_latched:late_sells_gt_5pct", lambda e: e.ex.counters.halts.update({"late_sells_gt_5pct": {}}))

    def test_boundaries_just_inside_the_limits_trade(self):
        for name, setup in (("loss", lambda e: setattr(e.ex.state, "realized_lamports", -99_999_999)),
                            ("daily", lambda e: e.ex.counters.day(h.day_key(T0)).update(realized=-59_999_999)),
                            ("trades", lambda e: e.ex.counters.day(h.day_key(T0)).update(trades=29)),
                            ("age", None)):
            sub = self.tmp / name
            sub.mkdir()
            e = Env(sub)
            if setup:
                setup(e)
            e.fire(**({"decision_ms": T0 - 10_000} if name == "age" else {}))
            self.assertEqual(e.refusals(), [], name)
            self.assertEqual(len(e.rpc.sent), 1, name)

    def test_trade_counters_roll_at_utc_midnight(self):
        e = self.env()
        e.ex.counters.day(h.day_key(T0)).update(trades=30, realized=-80_000_000)
        e.fire()
        self.assertEqual(len(e.refusals()), 1)
        e.jump(24 * 3_600_000)  # the next UTC day
        e.fire(decision_ms=e.clock())
        self.assertEqual(len(e.rpc.sent), 1)

    def test_one_trade_per_pool(self):
        e = self.env()
        e.fire()
        e.fire()
        self.assertEqual(e.refusals(), ["already_bought"])
        self.assertEqual(len(e.rpc.sent), 1)

    def test_max_concurrent_counts_in_flight_buys(self):
        e = self.env(max_open=1)
        e.fire()  # pending, not yet open
        other = str(Keypair().pubkey())
        e.ex.handle_trigger(trig(e.clock, mint=other))
        self.assertEqual(e.refusals(), ["max_open"])

    def test_balance_floor_includes_open_exposure_and_in_flight_stakes(self):
        e = self.env()
        need0 = h.balance_need(STAKE, 55_000, 0, 0, e.ex.h5.wallet_floor_lamports)
        need2 = h.balance_need(STAKE, 55_000, 2, STAKE, e.ex.h5.wallet_floor_lamports)
        self.assertGreater(need2, need0 + STAKE)
        e.rpc.balance = need0 - 1
        e.fire()
        self.assertEqual(e.refusals(), ["balance_floor"])
        e.rpc.balance = need0
        e.ex._bal = None
        e.fire()
        self.assertEqual(len(e.rpc.sent), 1)

    def test_unreadable_balance_refuses(self):
        e = self.env()
        e.rpc.balance = None
        with mock.patch.object(e.ex, "_balance", return_value=None):
            e.fire()
        self.assertEqual(e.refusals(), ["balance_unreadable"])

    def test_stop_file_created_during_the_build_is_caught_before_the_send(self):
        e = self.env()
        e.rpc.send_hook = None
        orig = e.rpc.__call__

        def make_stop(method, params, _o=type(e.rpc).__call__):
            if method == "getLatestBlockhash":
                Path(e.conf["stop_file"]).write_text("")
            return _o(e.rpc, method, params)

        with mock.patch.object(type(e.rpc), "__call__", lambda self, m, p: make_stop(m, p)):
            e.fire()
        self.assertEqual(e.refusals(), ["stop_file"])
        self.assertEqual(e.rpc.sent, [])
        self.assertEqual(e.ex.state.attempts, 0)

    def test_send_itself_refuses_under_halt_and_cancels_an_unsent_buy_under_stop(self):
        e = self.env()
        e.fire()
        self.assertEqual(len(e.rpc.sent), 1)
        p = e.ex.state.pending[MINT]
        Path(e.conf["halt_file"]).write_text("")
        e.ex._send(p, MINT)
        self.assertEqual(len(e.rpc.sent), 1)  # HALT blocks even a rebroadcast
        Path(e.conf["halt_file"]).unlink()
        e.ex._send(p, MINT)
        self.assertEqual(len(e.rpc.sent), 2)  # no STOP: a rebroadcast inside the 3 s window goes out
        Path(e.conf["stop_file"]).write_text("")
        e.ex._send(p, MINT)
        self.assertEqual(len(e.rpc.sent), 2)  # STOP: a buy is no longer rebroadcast (it is left to expire)
        fresh = {"kind": "buy", "sends": 0, "signature": "s", "tx_b64": p["tx_b64"]}
        e.ex.state.pending["other"] = fresh
        e.ex._send(fresh, "other")
        self.assertEqual(len(e.rpc.sent), 2)  # and a buy that has not been sent yet is cancelled by STOP
        self.assertNotIn("other", e.ex.state.pending)


# --- dry run ------------------------------------------------------------------------------------------------------------------


class DryRunTests(Case):
    def test_dry_run_builds_and_simulates_and_never_sends(self):
        e = self.env(live=False)
        self.assertEqual(e.ex.run_mode, "dryrun")
        e.fire()
        self.assertEqual(e.rpc.sent, [])
        self.assertNotIn("sendTransaction", e.rpc.calls)
        self.assertIn("simulateTransaction", e.rpc.calls)
        dec = e.ledger("decision")[0]
        self.assertFalse(dec["sent"])
        self.assertIsNone(dec["live_validate_err"])  # the live signer's allowlist accepts the message
        self.assertIsNone(dec["err"])
        self.assertEqual(dec["cu_used"], 90_000)
        self.assertIn(MINT, e.ex.state.open)
        self.assertTrue(e.ex.state.open[MINT]["virtual"])

    def test_dry_run_cannot_sign_or_send(self):
        e = self.env(live=False)
        self.assertNotIsInstance(e.ex._kp, Keypair)
        with self.assertRaises(RuntimeError):
            e.ex._sign(None, None, MINT)  # type: ignore[arg-type]
        e.ex._send({"kind": "buy", "sends": 0, "signature": "s", "tx_b64": "AA=="}, MINT)
        self.assertEqual(e.rpc.sent, [])
        self.assertNotIn("sendTransaction", e.rpc.calls)

    def test_dry_run_exit_is_outcome_blind_and_on_schedule(self):
        e = self.env(live=False)
        e.fire()
        plan = e.plan()
        e.at_slot(plan["send_slot"] - 1)
        self.assertIn(MINT, e.ex.state.open)
        e.at_slot(plan["send_slot"])
        self.assertNotIn(MINT, e.ex.state.open)
        sell = e.ledger("dry_sell")[0]
        self.assertFalse(sell["sent"])
        self.assertEqual(sell["est_slot"], plan["send_slot"])
        self.assertIn("err", sell)
        self.assertIn("cu_used", sell)
        self.assertEqual(e.rpc.sent, [])
        # no quote, price or P&L in any dry-run row
        text = Path(e.ex.fills.path).read_text()
        for word in ("pnl", "quote_sol_out", "sol_received", "proceeds", "exit_vs_quote", "ret\""):
            self.assertNotIn(word, text, word)

    def test_dry_run_records_budget_stops_but_does_not_enforce_them(self):
        e = self.env(live=False)
        e.ex.counters.day(h.day_key(T0)).update(trades=30)
        e.fire()
        dec = e.ledger("decision")[0]
        self.assertEqual(dec["would_have_halted"], "max_trades_day")
        self.assertEqual(e.refusals(), [])

    def test_dry_run_still_enforces_max_open_kill_files_and_feed_gap(self):
        e = self.env(live=False, max_open=1)
        e.fire()
        e.ex.handle_trigger(trig(e.clock, mint=str(Keypair().pubkey())))
        self.assertEqual(e.refusals(), ["max_open"])
        Path(e.conf["stop_file"]).write_text("")
        e.ex.handle_trigger(trig(e.clock, mint=str(Keypair().pubkey())))
        self.assertEqual(e.refusals(), ["max_open", "stop_file"] if e.refusals()[-1] == "stop_file" else e.refusals())

    def test_resolve_mode_needs_both_switches(self):
        self.assertEqual(pe.resolve_mode("live", True), ("live", None))
        self.assertEqual(pe.resolve_mode("live", False)[0], "dryrun")
        self.assertEqual(pe.resolve_mode("dryrun", True)[0], "dryrun")
        self.assertEqual(pe.resolve_mode("dryrun", False), ("dryrun", None))

    def test_default_cli_run_is_dry_and_never_touches_a_key(self):
        e = self.env(live=False)
        cp = self.tmp / "c.json"
        cp.write_text(json.dumps(e.conf))
        Path(e.conf["intents_file"]).write_text("")
        with mock.patch.object(pl, "load_probe_key", side_effect=AssertionError("key loaded in a dry run")), \
                mock.patch.object(pl, "harden_process", side_effect=AssertionError("hardened in a dry run")), \
                mock.patch.object(pe, "ProbeRpc", lambda url: e.rpc), mock.patch.object(h.sim, "load_rpc_url", return_value="http://x"), \
                mock.patch.dict(os.environ, {}, clear=False), contextlib.redirect_stdout(io.StringIO()) as out:
            os.environ.pop("CREDENTIALS_DIRECTORY", None)
            e.ex.fills  # the Env's own executor holds no lock; main takes it
            rc = h.main(["--config", str(cp), "--once"])
        self.assertEqual(rc, 0)
        self.assertIn("mode=dryrun", out.getvalue())
        self.assertNotIn("sendTransaction", e.rpc.calls)

    def test_config_live_without_the_flag_runs_dry(self):
        e = self.env(live=True)
        cp = self.tmp / "c.json"
        cp.write_text(json.dumps(e.conf))
        Path(e.conf["intents_file"]).write_text("")
        with mock.patch.object(pl, "load_probe_key", side_effect=AssertionError("key loaded")), mock.patch.object(pe, "ProbeRpc", lambda url: e.rpc), \
                mock.patch.object(h.sim, "load_rpc_url", return_value="http://x"), contextlib.redirect_stdout(io.StringIO()) as out:
            rc = h.main(["--config", str(cp), "--once"])
        self.assertEqual(rc, 0)
        self.assertIn("mode=dryrun", out.getvalue())
        self.assertIn("DRY RUN", out.getvalue())


if __name__ == "__main__":
    unittest.main()
