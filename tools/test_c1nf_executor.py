"""Offline tests for tools/c1nf_executor.py. The H5 test fixtures (the probe's fake RPC, a chain that follows the fake clock, throwaway Keypairs), no
network, no real key, no live run. The H5 suites are not touched: they must still pass unchanged beside these."""

from __future__ import annotations

import json
import math
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from solders.keypair import Keypair

from tools import c1nf_executor as c
from tools import h5_executor as h5
from tools import probe_executor as pe
from tools import pumpswap_tx as tx
from tools.test_h5_executor import MINT, POOL, QREAL, H5Rpc, buy_args, decode_tx, sell_args
from tools.test_probe_executor import BASE0, T0, V, Clock
from tools.test_probe_live import RENT, meta_result

STAKE = 20_000_000
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "c1nf_pick_v1.jsonl"
MINUTE = 60_000


class Case(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        self.addCleanup(self._td.cleanup)
        self.etc = self.tmp / "etc-mal-c1nf"  # stand-in for /etc/mal-c1nf (root-owned there, owned by this test's uid here)
        self.etc.mkdir()
        os.chmod(self.etc, 0o755)
        self.probe_dir = self.tmp / "probe-live-dir"
        self.probe_dir.mkdir()
        self.state_dir = self.tmp / "state"
        for target, attr, val in ((c, "LIVE_OK_PATH", self.etc / "LIVE_OK"), (c, "LIVE_STATE_DIR", self.state_dir), (h5, "LIVE_OK_UID", os.getuid()),
                                  (h5, "LIVE_OK_GID", os.getgid()), (pe, "LIVE_DIR", self.probe_dir)):
            p = mock.patch.object(target, attr, val)
            p.start()
            self.addCleanup(p.stop)

    @staticmethod
    def make_live_ok(mode: int = 0o644) -> None:
        c.LIVE_OK_PATH.unlink(missing_ok=True)
        c.LIVE_OK_PATH.write_text("")
        os.chmod(c.LIVE_OK_PATH, mode)

    def env(self, **kw) -> "Env":
        return Env(self.tmp, self.state_dir, **kw)

    def fresh(self, name: str, **kw) -> "Env":
        """A second, independent executor (own temp dir, own state dir, which live is then pinned to)."""
        d = self.tmp / name
        d.mkdir()
        p = mock.patch.object(c, "LIVE_STATE_DIR", d / "state")
        p.start()
        self.addCleanup(p.stop)
        return Env(d, d / "state", **kw)


class Env:
    """One C1-NF executor on the H5 fake chain. live=True builds a keyed executor with LIVE_OK and EXP-025 in place. The chain follows the clock."""

    def __init__(self, tmp: Path, state_dir: Path, live: bool = True, oracle=None, exp025: bool = True, live_ok: bool = True, slot_ms: float = 200.0,
                 quote: int = QREAL, **cfg):
        self.tmp = tmp
        self.clock = Clock(T0 + 1_500)
        self.rpc = H5Rpc(self.clock, quote=quote)
        self.rpc.slot_ms = slot_ms
        self.rpc._anchor = (100_000, T0)
        self.root = tmp / "repo"
        (self.root / "EXP").mkdir(parents=True, exist_ok=True)
        if exp025:
            (self.root / c.EXP025_PART1).write_text("# EXP-025 Part 1 (test stub)\n")
        self.conf = dict(intents_file=str(tmp / "intents.jsonl"), state_dir=str(state_dir), mode="live" if live else "dryrun", poll_s=5.0)
        self.conf.update(cfg)
        if live:
            if live_ok:
                c.LIVE_OK_PATH.write_text("")
                os.chmod(c.LIVE_OK_PATH, 0o644)
            else:
                c.LIVE_OK_PATH.unlink(missing_ok=True)
        self.kp = Keypair() if live else None
        self.ex = c.C1NFExecutor(self.rpc, self.conf, self.kp, now_ms=self.clock, pick_oracle=oracle, root=self.root)
        self.seed()

    def seed(self) -> None:
        """Our own getSlot history, as 150 s of the prewarm loop would have left it: the measured slot rate is the chain's."""
        self.ex.slots = h5.SlotClock()
        now = self.clock()
        for k in range(75, -1, -1):
            self.ex.slots.observe(self.rpc.slot_at(now - k * 2_000), now - k * 2_000)

    def set_clock(self, when_ms: int) -> None:
        self.clock.t = when_ms
        self.seed()

    def row(self, minute: int = T0, age_ms: int = 1_500, **kw) -> dict:
        """A pick for the decision minute `minute`, `age_ms` old on the chain's clock (the clock is moved there)."""
        self.set_clock(minute + age_ms)
        base = {"type": "c1nf_pick", "mint": MINT, "pool": POOL, "decision_T_ms": minute, "SD_slot": self.rpc.slot_at(minute + 200), "pred": 0.05,
                "h_top1": 0.1, "stage1": True, "feature_hash": "abcdef0123456789"}
        return {**base, **kw}

    def pick(self, **kw) -> c.C1NFPick:
        p, bad = c.parse_pick(self.row(**kw))
        assert p is not None, bad
        return p

    def fire(self, **kw) -> None:
        self.ex.handle_pick(self.pick(**kw))

    def ledger(self, kind: str | None = None) -> list[dict]:
        p = Path(self.ex.fills.path)
        rows = [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []
        return [r for r in rows if kind is None or r.get("kind") == kind]

    def refusals(self) -> list[str]:
        return [r["reason"] for r in self.ledger("skip")]

    def sent(self) -> list[dict]:
        return [decode_tx(b64) for _t, b64 in self.rpc.sent]

    def land_buy(self, slot: int) -> None:
        p = self.ex.state.pending[MINT]
        tok = p["q_tokens"]
        self.rpc.statuses[p["signature"]] = {"slot": slot, "confirmationStatus": "confirmed", "err": None}
        self.rpc.txs[p["signature"]] = meta_result(self.ex, MINT, delta=-(p["spend"] + 60_000 + RENT), fee=60_000, tok_delta=tok, ata_post=RENT, slot=slot)
        self.rpc.token_balance = tok
        self.ex.advance_pending()

    def land_sell(self, slot: int, proceeds: int = 21_000_000) -> None:
        p = self.ex.state.pending[MINT]
        self.rpc.statuses[p["signature"]] = {"slot": slot, "confirmationStatus": "confirmed", "err": None}
        self.rpc.txs[p["signature"]] = meta_result(self.ex, MINT, delta=proceeds - 60_000 + RENT, fee=60_000, tok_delta=-p["tokens"], ata_pre=RENT, ata_post=0, slot=slot)
        self.ex.advance_pending()

    def open_position(self, land_after_ms: int = 1_000, **kw) -> dict:
        """A pick, its buy sent now and landed `land_after_ms` later in the slot the chain is in then."""
        self.fire(**kw)
        self.clock.t += land_after_ms
        self.land_buy(self.rpc.slot)
        assert MINT in self.ex.state.open, self.refusals()
        return self.ex.state.open[MINT]

    def at_slot(self, slot: int) -> None:
        delta = slot - self.rpc.slot
        if delta > 0:
            self.clock.t += int(delta * self.rpc.slot_ms)
        self.ex.exit_tick(self.clock())

    def plan(self) -> dict:
        return self.ex.state.open[MINT]["h5"]["plan"]

    def step_until_sent(self, n_sent: int, limit_ms: int = 400_000, step_ms: int = 100) -> int:
        """Advance the clock until `n_sent` transactions have been sent; the wall time of the send."""
        end = self.clock() + limit_ms
        while len(self.rpc.sent) < n_sent and self.clock() < end:
            self.clock.t += step_ms
            self.ex.exit_tick(self.clock())
        return self.clock()


# --- limits and config ----------------------------------------------------------------------------------------------------


class LimitsTests(Case):
    def test_defaults_are_the_canary_limits(self):
        l = c.c1nf_limits({})
        self.assertEqual((l.stake_lamports, l.max_open, l.max_trades_per_day, l.daily_loss_lamports, l.total_loss_lamports),
                         (20_000_000, 3, 40, 80_000_000, 150_000_000))
        self.assertEqual((l.buy_priority_lamports, l.entry_tolerance_bps), (55_000, 1500))

    def test_config_cannot_raise_a_maximum_and_can_lower_one(self):
        l = c.c1nf_limits({"stake_lamports": 10**10, "max_open": 50, "max_trades_per_day": 500, "daily_loss_lamports": 10**12, "total_loss_lamports": 10**12,
                           "max_attempts": 10**6, "max_days": 99, "buy_priority_lamports": 10**8, "entry_tolerance_bps": 9000})
        for k, cap in c.C1NF_MAX.items():
            if k not in ("sell_priority_lamports", "escalated_priority_lamports"):
                self.assertEqual(getattr(l, k), cap, k)
        lo = c.c1nf_limits({"stake_lamports": 5_000_000, "max_open": 1, "daily_loss_lamports": 1})
        self.assertEqual((lo.stake_lamports, lo.max_open, lo.daily_loss_lamports), (5_000_000, 1, 1))

    def test_the_h5_rules_knobs_and_this_rules_constants_are_not_configurable(self):
        for key, val in (("exit_land_offset_s", 0.55), ("deadline_s", 370), ("trigger_variant", "pv"), ("max_trigger_age_ms", 3_000), ("late_sell_min_n", 1),
                         ("sell_priority_lamports", 55_000), ("escalated_priority_lamports", 150_000)):
            with self.assertRaises(ValueError, msg=key):
                c.c1nf_limits({key: val})
        for bad in ({"max_pick_age_s": 0.1}, {"max_pick_age_s": True}, {"intents_glob": "../x"}, {"jito_enabled": True}, {"stake_lamports": 0}):
            with self.assertRaises(ValueError, msg=str(bad)):
                c.c1nf_limits(bad)

    def test_the_shipped_configs(self):
        root = Path(__file__).resolve().parent.parent / "scripts" / "mal-fast"
        dry, live = json.loads((root / "c1nf-executor.json").read_text()), json.loads((root / "c1nf-executor-live.json").read_text())
        self.assertEqual((dry["mode"], live["mode"]), ("dryrun", "live"))
        self.assertNotIn("end_ms", live)  # the manager sets it at deploy; live refuses to start without it
        self.assertNotIn("end_ms", dry)
        for cfg in (dry, live):
            l = c.c1nf_limits(cfg)
            self.assertEqual((l.stake_lamports, l.max_open, l.max_trades_per_day, l.daily_loss_lamports, l.total_loss_lamports),
                             (20_000_000, 3, 40, 80_000_000, 150_000_000))
            self.assertEqual(cfg["state_dir"], "/var/lib/mal-live/c1nf")
            self.assertEqual(cfg["feed_heartbeat_max_age_ms"], 150_000)
            for pinned in h5.PINNED_PATH_KEYS:
                self.assertNotIn(pinned, cfg)
        self.assertEqual({k: v for k, v in dry.items() if k != "mode"}, {k: v for k, v in live.items() if k != "mode"})

    def test_the_h5_module_is_h5s_again_after_every_use(self):
        before = {k: getattr(h5, k) for k in c._SCOPE_PATCHES}
        c.c1nf_limits({})
        c.c1nf_live_ok_valid()
        e = self.env(live=False)
        self.assertEqual({k: getattr(h5, k) for k in c._SCOPE_PATCHES}, before)
        self.assertEqual((h5.H5_MAX["max_open"], h5.H5_MAX["max_trades_per_day"], h5.H5_MAX["total_loss_lamports"]), (2, 30, 120_000_000))
        self.assertEqual(str(h5.RULE_ID), "H5-BOOSTFLOOR-v1")
        self.assertEqual(e.ex.h5.max_open, 3)
        with self.assertRaises(ValueError):
            c.c1nf_limits({"stake_lamports": -1})
        self.assertEqual({k: getattr(h5, k) for k in c._SCOPE_PATCHES}, before)  # also after a refusal inside the scope

    def test_state_files_are_per_mode_under_the_c1nf_dir_and_book_is_c1nf(self):
        e = self.env(live=False)
        self.assertEqual(e.ex.state_path.parent, self.state_dir / "dryrun")
        self.assertEqual(e.ex.book, c.BOOK)
        start = e.ledger("start")[0]
        self.assertEqual((start["rule"], start["book"]), (c.RULE_ID, c.BOOK))
        self.assertEqual(e.ledger("c1nf_start")[0]["limits"]["max_open"], 3)


# --- the pick -------------------------------------------------------------------------------------------------------------


class PickParseTests(Case):
    def good(self, **kw):
        row = {"type": "c1nf_pick", "mint": MINT, "pool": POOL, "decision_T_ms": T0, "SD_slot": 100_000, "pred": 0.05, "h_top1": 0.1, "stage1": True,
               "feature_hash": "abcdef0123456789", **kw}
        return row

    def test_the_fixture_is_a_good_pick_and_outcome_and_matches_the_schema(self):
        rows = [json.loads(x) for x in FIXTURE.read_text().splitlines()]
        pick, bad = c.parse_pick(rows[0])
        self.assertIsNone(bad)
        self.assertEqual((pick.mint, pick.pool, pick.pick_id), (MINT, POOL, f"{MINT}:{T0}"))
        self.assertTrue(set(c.PICK_SCHEMA["required"]) <= set(rows[0]))
        self.assertTrue(set(rows[0]) - {"type"} <= set(c.PICK_SCHEMA["required"]) | set(c.PICK_SCHEMA["optional"]))
        out, bad = c.parse_outcome(rows[1])
        self.assertEqual((out, bad), ((MINT, T0, 6.25), None))
        self.assertEqual(c.parse_pick(rows[2]), (None, None))

    def test_a_good_pick_and_the_rules_edges(self):
        self.assertIsNone(c.parse_pick(self.good())[1])
        self.assertIsNone(c.parse_pick(self.good(h_top1=0.5))[1])  # exactly 0.5 stays
        self.assertIsNone(c.parse_pick(self.good(pred=0.0201))[1])
        self.assertEqual(c.parse_pick(self.good(pred=0.02))[1], "bad_pick:pred_not_above_threshold")  # the rule buys only pred > 0.02
        self.assertEqual(c.parse_pick(self.good(h_top1=0.5001))[1], "bad_pick:h_top1_over_cap")

    def test_refusals_malformed_and_off_rule(self):
        cases = {
            "bad_pick:h_top1": [self.good(h_top1=float("nan")), self.good(h_top1=-0.1), self.good(h_top1=1.5), self.good(h_top1="0.1"), self.good(h_top1=True)],
            "bad_pick:pred": [self.good(pred=float("inf")), self.good(pred="0.05"), self.good(pred=None)],
            "bad_pick:stage1": [self.good(stage1=False), self.good(stage1="true"), self.good(stage1=1)],
            "bad_pick:off_grid": [self.good(decision_T_ms=T0 + 1_000), self.good(decision_T_ms=T0 + 59_999)],
            "bad_pick:decision_T_ms": [self.good(decision_T_ms=0), self.good(decision_T_ms=True), self.good(decision_T_ms=float(T0))],
            "bad_pick:SD_slot": [self.good(SD_slot=0), self.good(SD_slot="1")],
            "bad_pick:feature_hash": [self.good(feature_hash=""), self.good(feature_hash="short"), self.good(feature_hash=7), self.good(feature_hash="a b" * 5)],
            "bad_pick:ids": [self.good(mint="nope"), self.good(pool=7), self.good(mint="")],
            "bad_pick:not_canonical": [self.good(pool="11111111111111111111111111111111")],
            "bad_pick:ref_state": [self.good(q_lamports=10**10), self.good(base_reserve=10**10), self.good(q_lamports=0, base_reserve=5), self.good(q_lamports="1", base_reserve=1)],
            "bad_pick:suppressed": [self.good(suppressed=True)],
        }
        for reason, rows in cases.items():
            for row in rows:
                self.assertEqual(c.parse_pick(row), (None, reason), row)
        for key in ("mint", "pool", "decision_T_ms", "SD_slot", "pred", "h_top1", "stage1", "feature_hash"):
            row = self.good()
            del row[key]
            self.assertEqual(c.parse_pick(row), (None, f"bad_pick:missing_{key}"))
        self.assertEqual(c.parse_pick("x"), (None, None))
        self.assertEqual(c.parse_pick({"type": "trigger"}), (None, None))

    def test_outcome_parse(self):
        self.assertEqual(c.parse_outcome({"type": "c1nf_outcome", "mint": MINT, "decision_T_ms": T0, "outcome_pct": -3.5}), ((MINT, T0, -3.5), None))
        for bad in ({"mint": "x"}, {"decision_T_ms": 0}, {"outcome_pct": float("nan")}, {"outcome_pct": "1"}, {"outcome_pct": -101}):
            row = {"type": "c1nf_outcome", "mint": MINT, "decision_T_ms": T0, "outcome_pct": 1.0, **bad}
            self.assertIsNone(c.parse_outcome(row)[0], bad)
            self.assertTrue(c.parse_outcome(row)[1].startswith("bad_outcome:"), bad)
        self.assertEqual(c.parse_outcome({"type": "pick"}), (None, None))


# --- the exit plan and the landing anchor -------------------------------------------------------------------------------------


class ExitPlanTests(Case):
    L = c.c1nf_limits({})

    def test_plan_at_270ms_slots(self):
        p = c.c1nf_exit_plan(10_000, 0.27, self.L, 1_000_000)
        self.assertEqual(p.exit_slot, 10_000 + 1111)  # round(300 / 0.27)
        self.assertEqual(p.land_slot, p.exit_slot + 3)  # the 0.55 s landing offset: ceil(2.037) slots
        self.assertEqual(p.send_slot, p.land_slot - 2)  # 500 ms lead: ceil(1.85)
        self.assertEqual(p.arm_slot, p.send_slot - 8)  # 2 s: ceil(7.4)
        self.assertEqual(p.escalate_slot, 10_000 + round(315 / 0.27))
        self.assertEqual(p.deadline_slot, 10_000 + round(370 / 0.27))
        self.assertEqual(p.late_slot, 10_000 + round(305.55 / 0.27))

    def test_plan_at_200ms_slots(self):
        p = c.c1nf_exit_plan(10_000, 0.2, self.L, 1_000_000)
        self.assertEqual(p.exit_slot, 10_000 + 1500)
        self.assertEqual(p.land_slot, p.exit_slot + 3)  # ceil(2.75)
        self.assertEqual(p.send_slot, p.land_slot - 3)  # ceil(2.5)
        self.assertEqual(p.arm_slot, p.send_slot - 10)
        self.assertEqual((p.escalate_slot, p.deadline_slot), (10_000 + 1575, 10_000 + 1850))

    def test_wall_stages_hang_off_the_anchor(self):
        p = c.c1nf_exit_plan(10_000, 0.2, self.L, 1_000_000)
        self.assertEqual(p.s0_wall_ms, 1_000_000)
        self.assertEqual(p.send_wall_ms, 1_000_000 + 300_550 - 500)  # lands at landing + 300 s + 0.55 s
        self.assertEqual(p.arm_wall_ms, p.send_wall_ms - 2_000)
        self.assertEqual((p.escalate_wall_ms, p.deadline_wall_ms), (1_000_000 + 315_000, 1_000_000 + 370_000))
        self.assertEqual(p.late_wall_ms, 1_000_000 + 305_550)
        self.assertIsNone(c.c1nf_exit_plan(10_000, 0.2, self.L).send_wall_ms)

    def test_exit_slot_uses_the_scorers_rounding(self):
        for sps in (0.15001, 0.2, 0.267, 0.27, 0.3, 0.4, 0.416, 0.59):
            self.assertEqual(c.c1nf_exit_plan(0, sps, self.L).exit_slot, int(round(300.0 / sps)))


class LandingAnchoredExitTests(Case):
    def check_flow(self, slot_ms: float, sps: float):
        e = self.env(slot_ms=slot_ms)
        e.fire()
        self.assertIn(MINT, e.ex.state.pending)
        prov = e.ex.state.pending[MINT]["h5"]["plan"]
        sent_at = e.clock()
        self.assertEqual(prov["s0_wall_ms"], sent_at)  # provisional: anchored on the send
        e.clock.t += 2_300  # the buy lands later than the send
        landed = e.rpc.slot_at(sent_at + 1_900)
        e.land_buy(landed)
        plan = e.plan()
        true_wall = T0 + int((landed - 100_000) * slot_ms)  # the chain's own wall time of the landed slot
        self.assertEqual(plan["s0_slot"], landed)  # re-anchored on the landing slot, not the decision slot
        self.assertLess(abs(plan["s0_wall_ms"] - true_wall), slot_ms + 1)
        self.assertLess(abs(plan["sps"] / sps - 1.0), 0.01)  # the rate is OUR measurement of the chain (150 s of getSlot), not a number from the pick
        self.assertEqual(plan["exit_slot"], landed + round(300 / plan["sps"]))
        self.assertEqual(plan["land_slot"], plan["exit_slot"] + math.ceil(0.55 / plan["sps"] - 1e-9))
        self.assertLessEqual(abs(plan["exit_slot"] - (landed + round(300 / sps))), 8)
        self.assertEqual((plan["escalate_wall_ms"] - plan["s0_wall_ms"], plan["deadline_wall_ms"] - plan["s0_wall_ms"]), (315_000, 370_000))
        self.assertEqual(plan["send_wall_ms"] - plan["s0_wall_ms"], 300_050)
        anchor = e.ex.state.open[MINT]["h5"]["anchor"]
        self.assertEqual((anchor["landed_slot"], anchor["source"]), (landed, "slot_map"))
        self.assertEqual(e.ex.counters.plans[MINT]["plan"], plan)  # the durable copy is the landing-anchored one
        self.assertEqual(e.ledger("exit_anchored")[0]["landed_slot"], landed)
        # nothing is sold before the landing-anchored time, and the sell goes out at it
        e.clock.t = plan["send_wall_ms"] - 3_000
        e.ex.exit_tick(e.clock())
        e.ex.exit_tick(e.clock())
        self.assertEqual(len(e.rpc.sent), 1)
        t_send = e.step_until_sent(2)
        self.assertEqual(len(e.rpc.sent), 2)
        self.assertLessEqual(abs(t_send - plan["send_wall_ms"]), 400)
        sell = e.sent()[1]
        self.assertEqual((sell["closes"], sell["priority"]), (2, 55_000))  # the token ATA and the WSOL account are closed in the sell
        # the sell lands 0.55 s after landing + 300 s: not late, no halt
        e.clock.t += 480
        e.land_sell(e.rpc.slot)
        self.assertNotIn(MINT, e.ex.state.open)
        self.assertEqual(e.ledger("exit_landing")[0]["late"], False)
        self.assertEqual(e.ex.counters.halts, {})

    def test_exit_anchored_on_the_landing_at_270ms_slots(self):
        self.check_flow(270.0, 0.27)

    def test_exit_anchored_on_the_landing_at_200ms_slots(self):
        self.check_flow(200.0, 0.2)

    def test_the_exit_time_moves_with_the_landing_not_the_pick(self):
        a, b = self.env(), None
        a.fire()
        a.clock.t += 1_000
        a.land_buy(a.rpc.slot)
        plan_a = a.plan()
        a2 = self.fresh("second")
        a2.fire()
        a2.clock.t += 4_000  # four seconds later
        a2.land_buy(a2.rpc.slot)
        self.assertGreater(a2.plan()["send_wall_ms"], plan_a["send_wall_ms"] + 2_900)

    def test_escalation_at_315_s_and_the_deadline_at_370_s_after_landing(self):
        e = self.env()
        e.open_position()
        plan = e.plan()
        e.clock.t = plan["send_wall_ms"]
        e.ex.exit_tick(e.clock())
        e.step_until_sent(2)
        self.assertEqual(len(e.rpc.sent), 2)  # the timed sell; no status ever arrives
        e.clock.t = plan["s0_wall_ms"] + 314_000
        e.ex.exit_tick(e.clock())
        self.assertEqual(len(e.rpc.sent), 3)  # only the 2 s no-status rung so far: 55k
        self.assertEqual(e.sent()[2]["priority"], 55_000)
        e.clock.t = plan["s0_wall_ms"] + 315_000
        e.ex.exit_tick(e.clock())
        esc = e.sent()[-1]
        self.assertEqual(esc["priority"], 150_000)  # escalated at landing + 315 s
        e.clock.t = plan["s0_wall_ms"] + 369_000
        e.ex.exit_tick(e.clock())
        self.assertNotIn(h5.EMERGENCY_MIN_OUT, [sell_args(s)[1] for s in e.sent()[1:]])  # no emergency sell before landing + 370 s
        e.clock.t = plan["s0_wall_ms"] + 370_000
        e.ex.exit_tick(e.clock())
        emg = e.sent()[-1]
        self.assertEqual((sell_args(emg)[1], emg["priority"]), (h5.EMERGENCY_MIN_OUT, 150_000))  # the emergency market sell at landing + 370 s
        self.assertEqual(e.ledger("halt_latched")[-1]["reason"], "stuck_position")

    def test_a_restart_before_the_reanchor_exits_on_the_provisional_plan(self):
        e = self.env()
        e.fire()
        sent_at = e.clock()
        plan = e.ex.counters.plans[MINT]["plan"]
        self.assertEqual(plan["s0_wall_ms"], sent_at)
        self.assertEqual(plan["send_wall_ms"], sent_at + 300_050)

    def test_a_late_entry_is_out_of_the_rule_and_still_exits_on_its_own_landing(self):
        e = self.env()
        e.fire()
        e.clock.t += 7_000
        slot = e.rpc.slot
        e.land_buy(slot)
        self.assertIn("out_of_rule_entry", e.ex.counters.halts)
        self.assertTrue(e.ex.state.open[MINT]["h5"]["immediate_exit"])
        self.assertEqual(e.plan()["s0_slot"], slot)


# --- the book: one position per mint, the cooldown -------------------------------------------------------------------------------


class BookTests(Case):
    def test_an_open_or_pending_mint_is_never_bought_again(self):
        e = self.env()
        e.fire()
        e.fire(minute=T0 + MINUTE)
        self.assertEqual(e.refusals(), ["already_held"])  # buy still pending
        e.clock.t += 800
        e.land_buy(e.rpc.slot)
        e.fire(minute=T0 + 2 * MINUTE)
        self.assertEqual(e.refusals(), ["already_held", "already_held"])
        self.assertEqual(len(e.rpc.sent), 1)

    def test_reentry_60_s_after_the_exit_and_not_before(self):
        e = self.env()
        e.open_position()
        plan = e.plan()
        e.at_slot(plan["send_slot"])
        e.step_until_sent(2)
        e.clock.t += 480
        e.land_sell(e.rpc.slot)
        self.assertNotIn(MINT, e.ex.state.open)
        exit_ms = e.ex.extra.last_exit_ms[MINT]
        self.assertLessEqual(abs(exit_ms - e.clock()), 400)
        self.assertEqual(e.ledger("exit_recorded")[0]["cooldown_until_ms"], exit_ms + 60_000)
        first_minute = (exit_ms // MINUTE + 1) * MINUTE  # the next whole-minute decision after the exit: less than 60 s later unless the exit was on :00
        self.assertGreaterEqual(first_minute, exit_ms)
        before = len(e.rpc.sent)
        e.fire(minute=first_minute)  # decision time < exit + 60 s unless the minute is the one after
        if first_minute < exit_ms + 60_000:
            self.assertEqual(e.refusals()[-1], "cooldown")
            self.assertEqual(len(e.rpc.sent), before)
        ok_minute = ((exit_ms + 60_000 + MINUTE - 1) // MINUTE) * MINUTE  # the first grid minute at or after exit + 60 s
        self.assertGreaterEqual(ok_minute, exit_ms + 60_000)
        e.fire(minute=ok_minute)
        self.assertEqual(len(e.rpc.sent), before + 1)  # re-entry allowed: a second buy of the same mint
        self.assertIn(MINT, e.ex.state.pending)

    def test_the_cooldown_is_exactly_60_s_on_the_decision_time(self):
        e = self.env()
        e.ex.extra.last_exit_ms[MINT] = T0 - 30_000  # the decision at T0 is 30 s after the exit: short
        e.fire(minute=T0)
        self.assertEqual(e.refusals(), ["cooldown"])
        e.ex.extra.last_exit_ms[MINT] = T0 + MINUTE - 60_000 + 1  # decision time one ms short of exit + 60 s: refused
        e.fire(minute=T0 + MINUTE)
        self.assertEqual(e.refusals(), ["cooldown", "cooldown"])
        e.ex.extra.last_exit_ms[MINT] = T0 + 2 * MINUTE - 60_000  # decision time == exit + 60 s: taken
        e.fire(minute=T0 + 2 * MINUTE)
        self.assertIn(MINT, e.ex.state.pending)

    def test_the_cooldown_survives_a_restart(self):
        e = self.env(live=False)
        e.ex.extra.last_exit_ms[MINT] = T0 - 20_000
        e.ex._save_extra()
        again = c.C1NFExecutor(e.rpc, e.conf, None, now_ms=e.clock, root=e.root)
        self.assertEqual(again.extra.last_exit_ms[MINT], T0 - 20_000)

    def test_max_open_counts_open_and_pending_buys(self):
        e = self.env()
        for i in range(3):
            e.ex.state.open[f"X{i}"] = {"mint": f"X{i}", "spend": STAKE}
        e.fire()
        self.assertEqual(e.refusals(), ["max_open"])
        self.assertEqual(e.rpc.sent, [])

    def test_the_h5_entry_rules_do_not_apply(self):
        # Q = 60 + V SOL (H5 refuses above 40), a pick for a mint H5 would call already bought, any pool age: all taken.
        e = self.env(quote=60 * 10**9)
        e.ex.state.bought.append(MINT)
        e.fire()
        self.assertEqual(e.refusals(), [])
        self.assertIn(MINT, e.ex.state.pending)


# --- refusals ---------------------------------------------------------------------------------------------------------------------


class RefusalTests(Case):
    def test_stale_is_judged_in_chain_age(self):
        e = self.env()
        limit = h5.ceil_slots(3.0, 0.2)
        for extra, ok in ((0, True), (1, False)):
            e2 = self.fresh(f"s{extra}")
            row = e2.row(age_ms=1_000)
            row["SD_slot"] = e2.rpc.slot - limit - extra  # est == rpc.slot in the fake chain
            e2.ex.handle_pick(c.parse_pick(row)[0])
            self.assertEqual(e2.refusals(), [] if ok else ["stale_pick"], extra)
        e.fire(age_ms=3_400)  # 17 slots at 200 ms
        self.assertEqual(e.refusals(), ["stale_pick"])
        self.assertEqual(e.rpc.sent, [])

    def test_a_wall_clock_far_behind_the_decision_is_refused_even_if_the_slot_looks_fresh(self):
        e = self.env()
        row = e.row(age_ms=1_000)
        e.set_clock(T0 + 7_000)  # 7 s by the wall clock (more than 2 x 3 s) ...
        row["SD_slot"] = e.rpc.slot  # ... with a decision slot claimed to be now
        e.ex.handle_pick(c.parse_pick(row)[0])
        self.assertEqual(e.refusals(), ["stale_pick"])

    def test_config_can_lower_the_age_limit_but_not_raise_it(self):
        self.assertEqual(self.env(max_pick_age_s=1.0).ex.max_pick_age_s, 1.0)
        self.assertEqual(self.fresh("hi").ex.max_pick_age_s, 3.0)
        self.assertEqual(self.fresh("hi2", max_pick_age_s=30).ex.max_pick_age_s, 3.0)

    def test_no_measured_slot_rate_no_buy(self):
        e = self.env()
        row = e.row()
        e.ex.slots = h5.SlotClock()
        e.ex.handle_pick(c.parse_pick(row)[0])
        self.assertEqual(e.refusals(), ["sps_unmeasured"])

    def test_feed_hold_and_heartbeat(self):
        e = self.env(feed_heartbeat_max_age_ms=150_000)
        e.fire()
        self.assertEqual(e.refusals(), ["feed_stale"])  # no heartbeat seen yet
        row = e.row(minute=T0 + MINUTE)
        e.ex.feed_last_ms = e.clock()
        e.ex._gap_until_ms = e.clock() + 20_000
        e.ex.handle_pick(c.parse_pick(row)[0])
        self.assertEqual(e.refusals()[-1], "feed_gap")
        self.assertEqual(e.rpc.sent, [])

    def test_stop_and_halt_files_and_a_latched_halt_stop_buys(self):
        e = self.env()
        Path(e.ex.limits.stop_file).write_text("")
        e.fire()
        Path(e.ex.limits.stop_file).unlink()
        e.ex._latch("test_halt")
        e.fire(minute=T0 + MINUTE)
        self.assertEqual(e.refusals(), ["stop_file", "halt_latched:test_halt"])

    def test_the_daily_trade_cap_is_40_and_the_loss_stops_hold(self):
        e = self.env()
        e.ex.counters.day(h5.day_key(e.clock()))["trades"] = 40
        e.fire()
        self.assertEqual(e.refusals(), ["max_trades_day"])
        e2 = self.fresh("loss")
        e2.ex.state.realized_lamports = -(150_000_000 - STAKE + 1)  # worst case: this stake would take the total to the cap
        e2.fire()
        self.assertEqual(e2.refusals(), ["total_loss_stop"])

    def test_price_moved_beyond_the_guard_is_refused_without_a_send(self):
        e = self.env()
        e.fire(q_lamports=(QREAL + V) * 7 // 10, base_reserve=BASE0)  # the decision state was 30% cheaper than the read at receipt
        self.assertEqual(e.refusals(), ["price_moved"])
        self.assertEqual(e.rpc.sent, [])
        self.assertEqual(e.ledger("pick_status")[-1]["status"], "unfilled")

    def test_the_guard_reference_is_the_decision_state_when_the_pick_carries_it_else_the_receipt_read(self):
        e = self.env()
        e.fire(q_lamports=QREAL + V, base_reserve=BASE0)
        _spend, min_ref = buy_args(e.sent()[0])
        self.assertEqual(min_ref, h5.entry_terms(QREAL + V, BASE0, STAKE, 1500)["min_out"])
        self.assertEqual(e.ledger("decision")[0]["guard_ref"], "decision_state")
        e2 = self.fresh("r")
        e2.fire()
        self.assertEqual(buy_args(e2.sent()[0]), (STAKE, h5.entry_terms(QREAL + V, BASE0, STAKE, 1500)["min_out"]))
        self.assertEqual(e2.ledger("decision")[0]["guard_ref"], "receipt_snapshot")
        e3 = self.fresh("m")
        e3.fire(q_lamports=(QREAL + V) * 9 // 10, base_reserve=BASE0)  # a decision state 10% cheaper than the read: inside the 15% guard, taken
        self.assertEqual(buy_args(e3.sent()[0])[1], h5.entry_terms((QREAL + V) * 9 // 10, BASE0, STAKE, 1500)["min_out"])  # the guard is the decision state's

    def test_the_buy_is_the_canaries_size_and_the_ledger_names_the_pick(self):
        e = self.env()
        e.fire()
        self.assertEqual(buy_args(e.sent()[0])[0], STAKE)
        d = e.ledger("decision")[0]
        self.assertEqual((d["pick_id"], d["pred"], d["h_top1"], d["feature_hash"], d["book"]), (f"{MINT}:{T0}", 0.05, 0.1, "abcdef0123456789", c.BOOK))

    def test_h5_triggers_and_boost_rows_are_not_this_executors(self):
        e = self.env()
        with self.assertRaises(RuntimeError):
            e.ex.handle_trigger(None)
        with self.assertRaises(RuntimeError):
            e.ex.on_boost_row(MINT, 1, 0.2, 2, 3.0)


# --- the seal (EXP-022, identical to H5's) ------------------------------------------------------------------------------------------


class SealTests(Case):
    def test_the_seal_window_fails_closed_without_an_oracle(self):
        e = self.env()
        e.fire(minute=h5.SEAL_START_MS - 5 * MINUTE + MINUTE)  # inside the window? no: 10-16T00:56 is before it
        self.assertNotIn("seal_window_no_oracle", e.refusals())
        e2 = self.fresh("w")
        e2.fire(minute=h5.SEAL_START_MS + 10 * MINUTE)
        self.assertEqual(e2.ex.counters.seal_skips, 1)
        self.assertEqual(e2.refusals(), [])  # a count only: no skip row, no pick row, no mint
        self.assertEqual(e2.ledger("pick_status"), [])
        self.assertEqual(e2.ex.extra.picks, {})
        self.assertEqual(e2.rpc.sent, [])

    def test_oracle_false_buys_true_refuses_error_refuses_but_only_after_the_final(self):
        minute = h5.ORACLE_EARLIEST_MS + 10 * MINUTE
        for name, oracle, final, expect_sent in (("ok", lambda m: False, True, 1), ("pick", lambda m: True, True, 0), ("err", lambda m: 1 / 0, True, 0),
                                                 ("nonbool", lambda m: "no", True, 0), ("nofinal", lambda m: False, False, 0)):
            e = self.fresh(name, oracle=oracle)
            if final:
                Path(e.ex.final_marker).write_text("")
            e.fire(minute=minute)
            self.assertEqual(len(e.rpc.sent), expect_sent, name)
            self.assertEqual(e.refusals(), [], name)  # seal reasons are never ledgered per mint
            self.assertEqual(e.ex.counters.seal_skips, 1 - expect_sent, name)

    def test_an_oracle_hit_is_not_tracked_by_the_monitor(self):
        e = self.fresh("t", oracle=lambda m: True)
        Path(e.ex.final_marker).write_text("")
        minute = h5.ORACLE_EARLIEST_MS + 10 * MINUTE
        e.fire(minute=minute)
        e.ex.on_outcome(MINT, minute, 5.0)  # an outcome for it is not evidence and is not stored
        self.assertEqual((e.ex.extra.picks, e.ex.extra.outcomes_unmatched), ({}, 1))


# --- LIVE_OK and the EXP-025 gate ----------------------------------------------------------------------------------------------------


class GateTests(Case):
    def test_live_ok_is_checked_before_every_buy_and_its_checks_are_h5s(self):
        e = self.env(live_ok=False)
        e.fire()
        self.assertEqual(e.refusals(), ["live_ok_missing"])
        self.assertEqual(e.rpc.sent, [])
        for mode in (0o664, 0o646, 0o666, 0o600, 0o640, 0o755):
            self.make_live_ok(mode)
            self.assertEqual(c.c1nf_live_ok_valid(), "live_ok_unsafe", oct(mode))
        self.make_live_ok(0o644)
        self.assertIsNone(c.c1nf_live_ok_valid())
        with mock.patch.object(h5, "LIVE_OK_UID", os.getuid() + 1):
            self.assertEqual(c.c1nf_live_ok_valid(), "live_ok_unsafe")  # not root-owned
        with mock.patch.object(h5, "LIVE_OK_GID", os.getgid() + 1):
            self.assertEqual(c.c1nf_live_ok_valid(), "live_ok_unsafe")
        c.LIVE_OK_PATH.unlink()
        real = self.tmp / "real"
        real.write_text("")
        os.chmod(real, 0o644)
        c.LIVE_OK_PATH.symlink_to(real)
        self.assertEqual(c.c1nf_live_ok_valid(), "live_ok_unsafe")  # a symlink, even to a good file

    def test_h5s_live_ok_does_not_open_the_c1nf_gate(self):
        other = self.tmp / "etc-mal-h5"
        other.mkdir()
        os.chmod(other, 0o755)
        (other / "LIVE_OK").write_text("")
        os.chmod(other / "LIVE_OK", 0o644)
        with mock.patch.object(h5, "LIVE_OK_PATH", other / "LIVE_OK"):
            self.assertEqual(h5.live_ok_valid(), None)
            self.assertEqual(c.c1nf_live_ok_valid(), "live_ok_missing")
            self.assertEqual(h5.LIVE_OK_PATH, other / "LIVE_OK")  # restored after the C1-NF check

    def test_the_prereg_file_must_be_in_the_tree_and_exp024_does_not_stand_in(self):
        e = self.env(exp025=False)
        (e.root / h5.EXP024_PART1).write_text("# EXP-024")
        e.fire()
        self.assertEqual(e.refusals(), ["exp025_part1_missing"])
        (e.root / c.EXP025_PART1).write_text("")  # empty is not present
        e.fire(minute=T0 + MINUTE)
        self.assertEqual(e.refusals()[-1], "exp025_part1_missing")
        (e.root / c.EXP025_PART1).write_text("# EXP-025")
        e.fire(minute=T0 + 2 * MINUTE)
        self.assertEqual(len(e.rpc.sent), 1)

    def test_start_refusal_order_and_content(self):
        root = self.tmp / "repo"
        (root / "EXP").mkdir(parents=True)
        cfg = {"state_dir": str(self.state_dir)}
        self.assertEqual(c.start_refusal({**cfg, "stop_file": "/x"}, root), "config_path_override:stop_file")
        self.assertEqual(c.start_refusal({"state_dir": "/var/lib/mal-live/h5"}, root), "state_dir_not_c1nf")
        self.assertEqual(c.start_refusal(cfg, root), "live_ok_missing")
        self.make_live_ok()
        self.assertEqual(c.start_refusal(cfg, root), "exp025_part1_missing")
        (root / c.EXP025_PART1).write_text("# EXP-025")
        self.assertEqual(c.start_refusal(cfg, root), "end_ms_missing")
        self.assertIsNone(c.start_refusal({**cfg, "end_ms": 1792000000000}, root))

    def test_live_refuses_a_state_dir_that_is_not_the_c1nf_one(self):
        self.make_live_ok()
        with self.assertRaises(SystemExit):
            c.C1NFExecutor(H5Rpc(Clock()), {"intents_file": str(self.tmp / "i"), "state_dir": str(self.tmp / "elsewhere"), "mode": "live"}, Keypair(),
                           now_ms=Clock(), root=self.tmp)

    def test_main_live_without_live_ok_exits_2_before_any_key(self):
        cfg = {**json.loads((Path(__file__).resolve().parent.parent / "scripts/mal-fast/c1nf-executor-live.json").read_text()), "state_dir": str(self.state_dir),
               "intents_file": str(self.tmp / "i")}
        path = self.tmp / "cfg.json"
        path.write_text(json.dumps(cfg))
        with mock.patch.object(pl_load := __import__("tools.probe_live", fromlist=["x"]), "load_probe_key", side_effect=AssertionError("key touched")):
            self.assertEqual(c.main(["--config", str(path), "--live"]), 2)
        self.assertEqual(c.main(["--config", str(path), "--status"]), 0)

    def test_the_credential_is_c1nf_wallet(self):
        with mock.patch.dict(os.environ, {"CREDENTIALS_DIRECTORY": "/run/creds"}):
            self.assertEqual(c.credential_path(), "/run/creds/c1nf-wallet")


# --- the fill-selection monitor ----------------------------------------------------------------------------------------------------------


def mk(i: int, status: str, pct: float | None, monitored: bool = True, oseq: int | None = None) -> tuple[str, dict]:
    return f"M{i}:{i}", {"mint": f"M{i}", "T": i, "status": status, "reason": None, "monitored": monitored, "outcome_pct": pct,
                         "oseq": (i + 1 if pct is not None else 0) if oseq is None else oseq}


class MonitorMathTests(unittest.TestCase):
    def stats(self, picks, floor=0):
        ex = c.C1NFExtra(picks=dict(picks), monitor_floor=floor)
        return c.monitor_stats(ex)

    def test_eight_unfilled_and_a_gap_above_three_points_is_adverse(self):
        picks = [mk(i, "unfilled", 4.0 + 3.001) for i in range(8)] + [mk(100 + i, "filled", 4.0) for i in range(22)]
        s = self.stats(picks)
        self.assertEqual((s["n"], s["n_unfilled"], s["n_filled"]), (30, 8, 22))
        self.assertTrue(s["adverse"])

    def test_exactly_three_points_and_seven_unfilled_are_not(self):
        self.assertFalse(self.stats([mk(i, "unfilled", 7.0) for i in range(8)] + [mk(100 + i, "filled", 4.0) for i in range(22)])["adverse"])
        self.assertFalse(self.stats([mk(i, "unfilled", 30.0) for i in range(7)] + [mk(100 + i, "filled", 4.0) for i in range(22)])["adverse"])

    def test_filled_better_than_unfilled_is_not_adverse(self):
        self.assertFalse(self.stats([mk(i, "unfilled", -20.0) for i in range(10)] + [mk(100 + i, "filled", 4.0) for i in range(20)])["adverse"])

    def test_the_window_is_the_last_30_by_decision_time(self):
        old = [mk(i, "unfilled", 50.0) for i in range(8)]  # decision times 0..7: the oldest
        newer = [mk(100 + i, "filled", 1.0) for i in range(30)]
        s = self.stats(old + newer)
        self.assertEqual((s["n"], s["n_unfilled"], s["adverse"]), (30, 0, False))

    def test_no_filled_means_no_comparison(self):
        self.assertFalse(self.stats([mk(i, "unfilled", 50.0) for i in range(30)])["adverse"])

    def test_not_monitored_pending_and_outcome_less_picks_do_not_count(self):
        picks = ([mk(i, "unfilled", 50.0, monitored=False) for i in range(8)] + [mk(20 + i, "pending", 50.0) for i in range(8)] + [mk(40 + i, "unfilled", None) for i in range(8)]
                 + [mk(100 + i, "filled", 1.0) for i in range(5)])
        s = self.stats(picks)
        self.assertEqual((s["n"], s["n_unfilled"]), (5, 0))

    def test_outcomes_at_or_below_the_floor_do_not_count(self):
        picks = [mk(i, "unfilled", 50.0, oseq=5) for i in range(8)] + [mk(100 + i, "filled", 1.0, oseq=5) for i in range(22)]
        self.assertTrue(self.stats(picks, floor=4)["adverse"])
        self.assertEqual(self.stats(picks, floor=5)["n"], 0)


class MonitorFlowTests(Case):
    def seed_picks(self, e: Env, n_unfilled: int, n_filled: int) -> list[str]:
        ids = []
        for i in range(n_unfilled + n_filled):
            mint = f"Mint{i:040d}"[:44]
            ex_pick = c.C1NFPick(mint, "P", T0 + i * MINUTE, 1, 0.05, 0.1, "abcdef0123456789")
            e.ex._set_status(ex_pick, "unfilled" if i < n_unfilled else "filled", "price_moved" if i < n_unfilled else None)
            ids.append(ex_pick)
        return ids

    def feed(self, e: Env, picks, unfilled_pct: float, filled_pct: float, n_unfilled: int) -> None:
        for i, p in enumerate(picks):
            e.ex.on_outcome(p.mint, p.decision_T_ms, unfilled_pct if i < n_unfilled else filled_pct)

    def test_the_halt_latches_on_the_eighth_unfilled_outcome_and_stops_buys(self):
        e = self.env()
        picks = self.seed_picks(e, 8, 22)
        unfilled, filled = picks[:8], picks[8:]
        for p in filled:
            e.ex.on_outcome(p.mint, p.decision_T_ms, 4.0)
        for p in unfilled[:7]:
            e.ex.on_outcome(p.mint, p.decision_T_ms, 9.0)  # five points worse than the filled, but only seven unfilled
            self.assertEqual(e.ex.counters.halts, {})
        e.ex.on_outcome(unfilled[7].mint, unfilled[7].decision_T_ms, 9.0)
        self.assertIn("fill_selection_adverse", e.ex.counters.halts)
        halt = e.ledger("halt_latched")
        self.assertEqual([h["reason"] for h in halt], ["fill_selection_adverse"])
        self.assertEqual((halt[0]["n"], halt[0]["n_unfilled"], halt[0]["n_filled"]), (30, 8, 22))
        e.fire()
        self.assertEqual(e.refusals(), ["halt_latched:fill_selection_adverse"])  # live: the latch stops new buys
        self.assertEqual(e.rpc.sent, [])
        self.assertFalse(e.ledger("pick_status")[-1]["monitored"])  # a refusal by a stop is not a fill failure

    def test_a_dry_run_records_the_halt_but_does_not_enforce_it(self):
        e = self.env(live=False)
        self.feed(e, self.seed_picks(e, 8, 22), 9.0, 4.0, 8)
        self.assertIn("fill_selection_adverse", e.ex.counters.halts)
        e.fire()
        self.assertEqual(e.ledger("decision")[0]["would_have_halted"], "halt_latched:fill_selection_adverse")

    def test_no_latch_at_seven_unfilled_or_at_exactly_three_points(self):
        e = self.env(live=False)
        self.feed(e, p7 := self.seed_picks(e, 7, 23), 90.0, 4.0, 7)
        self.assertEqual(e.ex.counters.halts, {})
        e2 = self.fresh("b", live=False)
        self.feed(e2, self.seed_picks(e2, 8, 22), 7.0, 4.0, 8)
        self.assertEqual(e2.ex.counters.halts, {})

    def test_after_a_clear_the_same_evidence_does_not_relatch_but_fresh_evidence_does(self):
        e = self.env(live=False)
        picks = self.seed_picks(e, 8, 22)
        self.feed(e, picks, 9.0, 4.0, 8)
        self.assertIn("fill_selection_adverse", e.ex.counters.halts)
        e.ex.counters.halts.pop("fill_selection_adverse")  # as --clear-halt does
        e.ex.counters.save(e.ex.counters_path)
        late = [c.C1NFPick(f"Late{i:039d}"[:44], "P", T0 + (100 + i) * MINUTE, 1, 0.05, 0.1, "abcdef0123456789") for i in range(30)]
        for i, p in enumerate(late):
            e.ex._set_status(p, "unfilled" if i < 8 else "filled", "buy_expired" if i < 8 else None)
        e.ex.on_outcome(late[29].mint, late[29].decision_T_ms, 4.0)
        self.assertEqual(e.ex.counters.halts, {})  # one fresh outcome is not enough
        for i, p in enumerate(late[:29]):
            e.ex.on_outcome(p.mint, p.decision_T_ms, 9.0 if i < 8 else 4.0)
        self.assertIn("fill_selection_adverse", e.ex.counters.halts)  # a full fresh window latches again

    def test_the_monitor_state_survives_a_restart_and_the_halt_is_in_the_counters(self):
        e = self.env(live=False)
        self.feed(e, self.seed_picks(e, 8, 22), 9.0, 4.0, 8)
        again = c.C1NFExecutor(e.rpc, e.conf, None, now_ms=e.clock, root=e.root)
        self.assertIn("fill_selection_adverse", again.counters.halts)
        self.assertEqual(len(again.extra.picks), 30)
        self.assertEqual(again.extra.monitor_floor, e.ex.extra.monitor_floor)

    def test_outcomes_of_unknown_repeated_and_unmonitored_picks_are_not_evidence(self):
        e = self.env(live=False)
        picks = self.seed_picks(e, 1, 1)
        e.ex.on_outcome("Nope" + "1" * 40, T0, 5.0)
        e.ex.on_outcome(picks[0].mint, picks[0].decision_T_ms, 5.0)
        e.ex.on_outcome(picks[0].mint, picks[0].decision_T_ms, 99.0)  # a repeat: the first stands
        self.assertEqual(e.ex.extra.picks[picks[0].pick_id]["outcome_pct"], 5.0)
        self.assertEqual(e.ex.extra.outcomes_unmatched, 2)

    def test_book_refusals_are_not_monitored_and_their_outcomes_are_unmatched(self):
        e = self.env()
        e.ex.extra.last_exit_ms[MINT] = T0 - 30_000
        e.fire()  # cooldown
        row = e.ledger("pick_status")[-1]
        self.assertEqual((row["status"], row["reason"], row["monitored"]), ("unfilled", "cooldown", False))
        e.ex.on_outcome(MINT, T0, 20.0)
        self.assertEqual(e.ex.extra.outcomes_unmatched, 1)

    def test_a_duplicate_pick_keeps_the_first_rows_status(self):
        e = self.env()
        e.fire()
        self.assertEqual(e.ex.extra.picks[f"{MINT}:{T0}"]["status"], "pending")
        e.ex.handle_pick(e.pick())
        self.assertEqual(e.refusals(), ["duplicate_pick"])
        self.assertEqual(e.ex.extra.picks[f"{MINT}:{T0}"]["status"], "pending")


class FillStatusTests(Case):
    def status(self, e: Env) -> dict:
        return e.ex.extra.picks[f"{MINT}:{T0}"]

    def test_every_pick_is_ledgered_filled_or_unfilled(self):
        e = self.env()
        e.fire()
        self.assertEqual(self.status(e)["status"], "pending")
        e.clock.t += 800
        e.land_buy(e.rpc.slot)
        self.assertEqual(self.status(e)["status"], "filled")
        rows = e.ledger("pick_status")
        self.assertEqual([(r["status"], r["reason"], r["monitored"]) for r in rows], [("filled", None, True)])

    def test_expired_and_failed_buys_are_unfilled(self):
        e = self.env()
        e.fire()
        p = e.ex.state.pending[MINT]
        e.rpc.height = p["lvbh"] + 5
        for _ in range(3):
            e.clock.t += 6_000
            e.ex.advance_pending()
        self.assertEqual((self.status(e)["status"], self.status(e)["reason"]), ("unfilled", "buy_expired"))
        f = self.fresh("f")
        f.fire()
        p = f.ex.state.pending[MINT]
        f.rpc.statuses[p["signature"]] = {"slot": f.rpc.slot, "confirmationStatus": "confirmed", "err": {"InstructionError": [3, {"Custom": 6004}]}}
        f.rpc.txs[p["signature"]] = meta_result(f.ex, MINT, delta=-60_000, fee=60_000, err={"InstructionError": [3, {"Custom": 6004}]}, slot=f.rpc.slot)
        f.ex.advance_pending()
        self.assertEqual((f.ex.extra.picks[f"{MINT}:{T0}"]["status"], f.ex.extra.picks[f"{MINT}:{T0}"]["reason"]), ("unfilled", "buy_failed"))

    def test_a_dry_run_pick_is_filled_when_the_simulation_passes(self):
        e = self.env(live=False)
        e.fire()
        self.assertEqual(self.status(e)["status"], "filled")
        self.assertIn(MINT, e.ex.state.open)
        self.assertEqual(e.rpc.sent, [])
        plan = e.plan()
        self.assertEqual(plan["send_wall_ms"], e.clock() + 300_050)  # a dry run cannot see a landing: the provisional plan stands
        # the dry exit records the exit for the cooldown
        e.clock.t = plan["send_wall_ms"]
        e.ex.exit_tick(e.clock())
        self.assertNotIn(MINT, e.ex.state.open)
        self.assertEqual(e.ex.extra.last_exit_ms[MINT], e.clock())


# --- the feed ---------------------------------------------------------------------------------------------------------------------------


class FeedTests(Case):
    def write(self, path: Path, rows: list) -> None:
        with path.open("a") as fh:
            for r in rows:
                fh.write((r if isinstance(r, str) else json.dumps(r)) + "\n")

    def test_picks_outcomes_heartbeats_and_gaps_from_a_file(self):
        e = self.env(live=False, feed_heartbeat_max_age_ms=150_000)
        path = Path(e.conf["intents_file"])
        path.write_text("")
        e.ex.intent_tick()  # first look: starts at the end
        row = e.row()
        self.write(path, [{"type": "hb"}, {"type": "trigger", "variant": "pv", "mint": MINT}, {"schema": "h5_intent_v1"}, "not json", row,
                          {"type": "c1nf_outcome", "mint": MINT, "decision_T_ms": T0, "outcome_pct": 3.0}])
        self.assertEqual(e.ex.intent_tick(), 1)
        self.assertIn(MINT, e.ex.state.open)  # dry run: the simulated position
        self.assertEqual(e.ex.extra.picks[f"{MINT}:{T0}"]["outcome_pct"], 3.0)
        self.assertEqual(e.refusals(), [])
        self.write(path, [{"type": "gap", "kind": "reconnect", "flags_pools": False}])
        e.ex.intent_tick()
        self.assertEqual(e.ledger("feed_reconnect_redundant")[0]["kind_"], "reconnect")
        self.assertEqual(e.ex._gap_until_ms, 0)
        self.write(path, [{"type": "gap", "kind": "disconnect", "flags_pools": True}])
        e.ex.intent_tick()
        self.assertGreater(e.ex._gap_until_ms, e.clock())

    def test_bad_picks_are_ledgered_and_alert_past_five_an_hour(self):
        e = self.env(live=False)
        path = Path(e.conf["intents_file"])
        path.write_text("")
        e.ex.intent_tick()
        bad = e.row()
        bad["h_top1"] = 0.9
        self.write(path, [bad] * 7)
        e.ex.intent_tick()
        self.assertEqual([r["reason"] for r in e.ledger("skip")], ["bad_pick:h_top1_over_cap"] * 7)
        self.assertEqual(len(e.ledger("alert")), 1)
        self.assertEqual(e.ex.extra.picks, {})

    def test_the_newest_hourly_file_is_followed_and_the_previous_drained_on_a_roll(self):
        d = self.tmp / "shadow"
        d.mkdir()
        e = self.env(live=False, intents_file=str(d))
        a, b = d / "c1nf-shadow-2026-10-10T00.jsonl", d / "c1nf-shadow-2026-10-10T01.jsonl"
        a.write_text("")
        e.ex.intent_tick()
        row = e.row()
        self.write(a, [{"type": "hb"}, row])
        self.write(b, [{"type": "hb"}])
        e.clock.t += 1_100
        e.ex._glob_path = None  # the glob is cached for a second: the next look finds the new hour and drains the old one first
        self.assertEqual(e.ex.intent_tick(), 1)  # the pick written to the old hour just before the roll is not lost
        self.assertIn(MINT, e.ex.state.open)

    def test_the_fixture_runs_through_the_tail(self):
        e = self.env(live=False)
        path = Path(e.conf["intents_file"])
        path.write_text("")
        e.ex.intent_tick()
        e.set_clock(T0 + 1_500)
        e.rpc.slot = 100_000 + 7
        rows = [json.loads(x) for x in FIXTURE.read_text().splitlines()]
        rows[0]["SD_slot"] = e.rpc.slot_at(T0 + 200)
        self.write(path, rows)
        self.assertEqual(e.ex.intent_tick(), 1)
        self.assertEqual(e.ex.extra.picks[f"{MINT}:{T0}"]["outcome_pct"], 6.25)
        self.assertGreater(e.ex._gap_until_ms, 0)


if __name__ == "__main__":
    unittest.main()
