"""H5 executor: the scale ladder (owner decision). The tier comes from a root-owned file read before every buy; T2 is refused if
T2_IMPACT_OK is False (it is True: IMPACT.md covers 0.30 only, and the ladder ends at T2); the total stop is also capped at 35% of the wallet at
tier start. Fixtures: test_h5_executor."""

from __future__ import annotations

import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools import h5_executor as h
from tools.test_h5_executor import MINT, T0, Case, Env, buy_args

SOL = 1_000_000_000
DAY = h.day_key(T0)


class TierCase(Case):
    def write_tier(self, text: str = "T1\n", mode: int = 0o644) -> None:
        h.TIER_FILE_PATH.unlink(missing_ok=True)
        h.TIER_FILE_PATH.write_text(text)
        os.chmod(h.TIER_FILE_PATH, mode)

    def tier_env(self, tier: str | None = None, balance: int = 10 * SOL, **kw) -> Env:
        if tier:
            self.write_tier(tier + "\n")
        e = self.env(**kw)
        e.rpc.balance = balance
        return e

    def changes(self, e: Env) -> list[tuple]:
        return [(r["from_tier"], r["to_tier"], r["problem"]) for r in e.ledger("tier_change")]


class TierTableTests(unittest.TestCase):
    def test_the_table_is_the_owners_ladder(self):
        sol = lambda x: round(x * SOL)  # noqa: E731
        self.assertEqual(h.TIERS, {
            "T0": {"stake_lamports": sol(0.02), "max_open": 2, "max_trades_per_day": 30, "daily_loss_lamports": sol(0.08), "total_loss_lamports": sol(0.12)},
            "T1": {"stake_lamports": sol(0.10), "max_open": 3, "max_trades_per_day": 40, "daily_loss_lamports": sol(0.40), "total_loss_lamports": sol(0.60)},
            "T2": {"stake_lamports": sol(0.30), "max_open": 3, "max_trades_per_day": 40, "daily_loss_lamports": sol(1.20), "total_loss_lamports": sol(1.80)},
        })
        self.assertEqual(h.TIER_WALLET_FRAC, 0.35)
        self.assertIs(h.T2_IMPACT_OK, True)  # the impact check for 0.30 is done (h5-work/IMPACT.md, replay model); nothing above 0.30 is allowed
        self.assertEqual(h.H5Limits.from_config({}).stake_lamports, h.TIERS["T0"]["stake_lamports"])  # no tier means T0

    def test_the_ladder_ends_at_t2_and_the_top_stake_is_0_30_sol(self):
        # Any tier above 0.30 needs a new price-impact check with live-fill evidence (IMPACT.md covers 0.30 only; 0.50 fails in thinner pools).
        self.assertEqual(list(h.TIERS), ["T0", "T1", "T2"])
        self.assertEqual(max(t["stake_lamports"] for t in h.TIERS.values()), 300_000_000)
        self.assertEqual(h.TIERS["T2"]["stake_lamports"], 300_000_000)
        self.assertEqual([h.TIERS[k]["stake_lamports"] for k in h.TIERS], sorted(h.TIERS[k]["stake_lamports"] for k in h.TIERS))
        with tempfile.TemporaryDirectory() as d:
            for bigger in ("T3", "T0.50", "0.50"):  # a tier file naming anything above T2 is invalid: the lowest tier applies
                f = Path(d) / "TIER_big"
                f.write_text(bigger + "\n")
                self.assertEqual(h.read_tier(f, False)[0], "T0", bigger)
                self.assertIsNotNone(h.read_tier(f, False)[1], bigger)

    def test_constants_in_source(self):
        src = Path(h.__file__).read_text()
        self.assertIn('TIER_FILE_PATH = Path("/etc/mal-h5/TIER")', src)
        self.assertIn("T2_IMPACT_OK = True", src)
        self.assertIn("h5-work/IMPACT.md", src)  # the flip cites the check
        self.assertIn("THE LADDER ENDS AT T2 (0.30 SOL)", src)

    def test_config_can_only_tighten_within_the_active_tier(self):
        for tier, vals in h.TIERS.items():
            big = h.H5Limits.from_config({"stake_lamports": 10**10, "max_open": 50, "max_trades_per_day": 500, "daily_loss_lamports": 10**12,
                                          "total_loss_lamports": 10**12}, tier)
            self.assertEqual((big.stake_lamports, big.max_open, big.max_trades_per_day, big.daily_loss_lamports, big.total_loss_lamports),
                             tuple(vals.values()), tier)
        t1 = h.H5Limits.from_config({"stake_lamports": 50_000_000, "max_open": 1}, "T1")
        self.assertEqual((t1.stake_lamports, t1.max_open, t1.max_trades_per_day), (50_000_000, 1, 40))
        with self.assertRaises(ValueError):
            h.H5Limits.from_config({}, "T9")


class TierSelectionTests(TierCase):
    def test_a_missing_file_means_t0(self):
        e = self.tier_env()
        e.fire()
        self.assertEqual((e.ex.tier, self.changes(e)), ("T0", [(None, "T0", "tier_file_missing")]))
        self.assertEqual(buy_args(e.sent()[0])[0], 20_000_000)

    def test_an_invalid_file_means_t0_with_an_alert(self):
        for text in ("T9\n", "t1\n", "", "T1 T2\n", "TIER=T1\n", "\x00\xff\n"):
            sub = self.tmp / f"i{abs(hash(text))}"
            sub.mkdir()
            self.write_tier(text)
            e = Env(sub)
            e.fire()
            self.assertEqual(e.ex.tier, "T0", repr(text))
            self.assertEqual(self.changes(e)[0][2], "tier_file_invalid", repr(text))
            self.assertTrue([r for r in e.ledger("alert") if r.get("alert") == "tier_file_problem"], repr(text))

    def test_the_file_needs_the_same_ownership_and_mode_as_live_ok(self):
        self.write_tier("T1\n", 0o664)  # group-writable
        e = self.tier_env()
        e.fire()
        self.assertEqual((e.ex.tier, self.changes(e)[0][2]), ("T0", "tier_file_unsafe"))
        h.TIER_FILE_PATH.unlink()
        real = self.tmp / "real-tier"
        real.write_text("T1\n")
        os.chmod(real, 0o644)
        h.TIER_FILE_PATH.symlink_to(real)  # a symlink
        sub = self.tmp / "sym"
        sub.mkdir()
        e2 = Env(sub)
        e2.fire()
        self.assertEqual((e2.ex.tier, self.changes(e2)[0][2]), ("T0", "tier_file_unsafe"))
        h.TIER_FILE_PATH.unlink()
        self.write_tier("T1\n")
        sub = self.tmp / "owner"
        sub.mkdir()
        with mock.patch.object(h, "LIVE_OK_UID", os.getuid() + 1):  # not root-owned
            e3 = Env(sub)
            e3.fire()
        self.assertEqual((e3.ex.tier, self.changes(e3)[0][2]), ("T0", "tier_file_unsafe"))
        for mode in (0o600, 0o640, 0o444):
            self.write_tier("T1\n", mode)
            self.assertEqual(h.read_tier(h.TIER_FILE_PATH, True), ("T0", "tier_file_unsafe"), oct(mode))
        self.write_tier("T1\n", 0o644)
        self.assertEqual(h.read_tier(h.TIER_FILE_PATH, True), ("T1", None))
        self.write_tier("  T2 \n\n", 0o644)
        self.assertEqual(h.read_tier(h.TIER_FILE_PATH, True), ("T2", None))

    def test_a_dry_run_reads_state_dir_tier_without_root_checks_and_defaults_to_t0(self):
        e = Env(self.tmp, live=False)
        e.fire()
        self.assertEqual(e.ex.tier, "T0")
        sub = self.tmp / "dry"
        sub.mkdir()
        d = Env(sub, live=False)
        p = Path(d.conf["state_dir"]) / "TIER"
        p.write_text("T1\n")
        os.chmod(p, 0o600)  # not 0644, not root: fine for a dry run
        d.rpc.balance = 10 * SOL
        d.fire()
        self.assertEqual(d.ex.tier, "T1")
        self.assertEqual(d.ledger("decision")[0]["stake_lamports"], 100_000_000)
        self.assertEqual(d.ledger("start")[0]["paths"]["tier_file"], str(p))

    def test_a_live_config_may_not_override_the_tier_file(self):
        self.assertIn("tier_file", h.PINNED_PATH_KEYS)
        with self.assertRaises(SystemExit):
            Env(self.tmp, tier_file="/tmp/elsewhere")
        e = self.tier_env()
        self.assertEqual(e.ledger("start")[0]["paths"]["tier_file"], str(h.TIER_FILE_PATH))

    def test_the_tier_is_reread_before_every_buy_and_each_change_is_ledgered(self):
        e = self.tier_env("T1")
        e.fire()
        self.assertEqual(buy_args(e.sent()[0])[0], 100_000_000)
        self.write_tier("T0\n")
        e.ex.state.bought.clear()
        e.ex.state.pending.clear()
        e.fire()
        self.assertEqual(buy_args(e.sent()[1])[0], 20_000_000)
        self.assertEqual(self.changes(e), [(None, "T1", None), ("T1", "T0", None)])
        rows = e.ledger("tier_change")
        self.assertEqual((rows[1]["wallet_lamports"], rows[1]["limits"]["stake_lamports"]), (10 * SOL, 20_000_000))

    def test_a_restart_keeps_the_tier_it_was_on(self):
        e = self.tier_env("T1")
        e.ex.prewarm()
        self.assertEqual(e.ex.tier, "T1")
        e.ex = e.build()
        self.assertEqual(e.ex.tier, "T1")
        self.assertEqual(self.changes(e), [(None, "T1", None)])  # not a new change: the file still says T1


class TierLimitsBindTests(TierCase):
    def check_stop(self, e: Env, realized: int | None = None, day_realized: int | None = None, trades: int | None = None):
        e.ex.state.realized_lamports = realized or 0
        d = e.ex.counters.day(DAY)
        d["realized"], d["trades"] = day_realized or 0, trades or 0
        return e.ex._budget_stop(e.clock())

    def test_t1_limits_bind(self):
        e = self.tier_env("T1")
        stake = 100_000_000
        e.ex._refresh_tier(e.clock())
        self.assertEqual((e.ex.tier, e.ex.h5.stake_lamports, e.ex.h5.max_open), ("T1", stake, 3))
        self.assertIsNone(self.check_stop(e, realized=-499_999_999))  # total 0.60: realized - this stake <= -0.60 refuses
        self.assertEqual(self.check_stop(e, realized=-500_000_000), "total_loss_stop")
        self.assertIsNone(self.check_stop(e, day_realized=-299_999_999))  # daily 0.40
        self.assertEqual(self.check_stop(e, day_realized=-300_000_000), "daily_loss_stop")
        self.assertIsNone(self.check_stop(e, trades=39))  # 40 a day
        self.assertEqual(self.check_stop(e, trades=40), "max_trades_day")

    def test_t0_limits_still_bind_and_a_third_position_needs_t1(self):
        e = self.tier_env()
        e.ex._refresh_tier(e.clock())
        self.assertEqual(self.check_stop(e, realized=-100_000_000), "total_loss_stop")  # 0.12 SOL: -0.10 - 0.02
        self.assertEqual(self.check_stop(e, trades=30), "max_trades_day")
        e.ex.state.open.update({"a": {}, "b": {}})
        e.fire()
        self.assertEqual(e.refusals(), ["max_open"])  # T0: two
        sub = self.tmp / "t1"
        sub.mkdir()
        self.write_tier("T1\n")
        e1 = Env(sub)
        e1.rpc.balance = 10 * SOL
        e1.ex.state.open.update({"a": {}, "b": {}})
        e1.fire()
        self.assertEqual((e1.refusals(), len(e1.rpc.sent)), ([], 1))  # T1: three
        e1.ex.state.pending.clear()
        e1.ex.state.open["c"] = {}
        e1.ex.state.bought.clear()
        e1.fire()
        self.assertEqual(e1.refusals(), ["max_open"])

    def test_t1_buy_is_100m_and_t2_buys_300m_now_the_impact_check_is_done(self):
        e = self.tier_env("T1")
        e.fire()
        self.assertEqual((buy_args(e.sent()[0])[0], e.refusals()), (100_000_000, []))
        sub = self.tmp / "t2"
        sub.mkdir()
        self.write_tier("T2\n")
        e2 = Env(sub)
        e2.rpc.balance = 10 * SOL
        e2.fire()
        self.assertEqual((e2.refusals(), buy_args(e2.sent()[0])[0], e2.ex.tier), ([], 300_000_000, "T2"))

    def test_t2_is_refused_again_if_the_impact_flag_is_set_back_to_false(self):
        self.write_tier("T2\n")
        with mock.patch.object(h, "T2_IMPACT_OK", False):
            e = Env(self.tmp)
            e.rpc.balance = 10 * SOL
            e.fire()
            self.assertEqual((e.refusals(), e.rpc.sent, e.ex.tier), (["t2_impact_unchecked"], [], "T2"))

    def test_the_probes_own_size_limit_does_not_refuse_a_larger_tier_stake(self):
        e = self.tier_env("T1")
        self.assertEqual(e.ex.limits.size_lamports, 20_000_000)  # the probe's Limits object was built from T0 (and clamps at its 0.05 SOL max) ...
        e.fire()
        self.assertEqual((len(e.rpc.sent), buy_args(e.sent()[0])[0]), (1, 100_000_000))  # ... the signer's spend cap is the ACTIVE tier's stake

    def test_the_wallet_cap_binds_the_total_stop(self):
        e = self.tier_env("T1", balance=1 * SOL)  # 35% of 1 SOL = 0.35 < the tier's 0.60
        e.ex._refresh_tier(e.clock())
        self.assertEqual(e.ex.counters.tier_state["wallet_lamports"], 1 * SOL)
        self.assertEqual(e.ex._total_stop_lamports(), 350_000_000)
        self.assertIsNone(self.check_stop(e, realized=-249_999_999))  # -0.25 - this 0.10 stake <= -0.35 refuses
        self.assertEqual(self.check_stop(e, realized=-250_000_000), "total_loss_stop")
        e.rpc.balance = 10 * SOL  # the cap is the wallet MEASURED AT TIER START, not the live balance
        e.ex._bal = None
        self.assertEqual(e.ex._total_stop_lamports(), 350_000_000)
        self.assertEqual(self.check_stop(e, realized=-250_000_000), "total_loss_stop")
        self.write_tier("T0\n")  # a new tier starts: the wallet is measured again
        e.ex._refresh_tier(e.clock())
        self.assertEqual(e.ex.counters.tier_state["wallet_lamports"], 10 * SOL)
        self.assertEqual(e.ex._total_stop_lamports(), 120_000_000)  # now the tier's own 0.12 binds (35% of 10 SOL is far above it)

    def test_the_cap_is_35_percent_in_the_source_and_binds_a_small_wallet_at_t0_too(self):
        e = self.tier_env(balance=250_000_000)  # the 0.25 SOL canary: 35% = 0.0875 < T0's 0.12
        e.ex._refresh_tier(e.clock())
        self.assertEqual(e.ex._total_stop_lamports(), 87_500_000)

    def test_an_unreadable_wallet_at_tier_start_fails_closed_and_recovers(self):
        e = self.tier_env()
        with mock.patch.object(e.ex, "_balance", return_value=None):
            e.fire()
        self.assertEqual((e.refusals(), e.rpc.sent), (["balance_unreadable"], []))
        self.assertIsNone(e.ex.counters.tier_state["wallet_lamports"])
        e.fire()  # readable now: the wallet is filled in and the buy goes
        self.assertEqual((len(e.rpc.sent), e.ex.counters.tier_state["wallet_lamports"]), (1, 10 * SOL))


class TotalStopSinceTierStartTests(TierCase):
    """G1: the total stop (and its 35% wallet cap) limits the loss since the TIER started, not the run's cumulative realized P&L."""

    def start_t0_with_profit_then_t1(self, profit: int = 300_000_000, balance: int = 1_300_000_000) -> Env:
        e = self.tier_env("T0", balance=balance)
        e.ex._refresh_tier(e.clock())
        e.ex.state.realized_lamports = profit  # T0 made money
        self.write_tier("T1\n")
        e.ex._refresh_tier(e.clock())
        return e

    def stop_at(self, e: Env, realized: int):
        e.ex.state.realized_lamports = realized
        return e.ex._budget_stop(e.clock())

    def test_profit_before_the_tier_does_not_widen_the_35_percent_cap(self):
        e = self.start_t0_with_profit_then_t1()  # wallet 1.30 SOL: min(0.60, 0.35 x 1.30) = 0.455
        self.assertEqual((e.ex._total_stop_lamports(), e.ex.counters.tier_state["realized_at_start"]), (455_000_000, 300_000_000))
        # the loss since T1 started, plus this 0.10 SOL stake, must stay above -0.455: a loss of 0.355 is the last one that lets a buy go
        self.assertIsNone(self.stop_at(e, 300_000_000 - 354_999_999))
        self.assertEqual(self.stop_at(e, 300_000_000 - 355_000_000), "total_loss_stop")
        # the reviewer's repro: -0.55 since the tier started (42% of the wallet then) is refused, not allowed through
        self.assertEqual(self.stop_at(e, 300_000_000 - 550_000_000), "total_loss_stop")

    def test_only_the_since_tier_start_check_refuses_after_a_prior_profit(self):
        # the run-cumulative figure alone (-0.25 - 0.10 stake) is still above -0.455: it is the since-tier-start loss that refuses
        e = self.start_t0_with_profit_then_t1()
        e.ex.state.realized_lamports = 300_000_000 - 550_000_000
        self.assertGreater(e.ex.state.realized_lamports - 100_000_000, -e.ex._total_stop_lamports())
        self.assertEqual(e.ex._budget_stop(e.clock()), "total_loss_stop")

    def test_a_loss_before_the_tier_is_still_counted_by_the_run_cumulative_check(self):
        e = self.tier_env("T0", balance=10 * SOL)
        e.ex._refresh_tier(e.clock())
        e.ex.state.realized_lamports = -50_000_000  # T0 lost 0.05 (inside its 0.12)
        self.write_tier("T1\n")
        e.ex._refresh_tier(e.clock())
        self.assertEqual(e.ex.counters.tier_state["realized_at_start"], -50_000_000)
        # since the tier started the loss may reach 0.50, but the run total (-0.05 + that + this 0.10 stake) may not reach T1's own 0.60
        self.assertIsNone(self.stop_at(e, -499_999_999))
        self.assertEqual(self.stop_at(e, -500_000_000), "total_loss_stop")

    def test_realized_at_start_survives_a_restart(self):
        e = self.start_t0_with_profit_then_t1()
        e.ex.save()
        e.ex = e.build()
        self.assertEqual(e.ex.tier, "T1")
        self.assertEqual(e.ex.counters.tier_state["realized_at_start"], 300_000_000)
        self.assertEqual(self.stop_at(e, 300_000_000 - 550_000_000), "total_loss_stop")  # the restarted executor still counts from the tier start
        self.assertEqual([r["realized_at_start"] for r in e.ledger("tier_change")], [0, 300_000_000])

    def test_a_problem_read_does_not_rebaseline_the_tier(self):
        e = self.tier_env("T1", balance=10 * SOL)  # 35% of 10 SOL is far above the tier's 0.60, so the tier's own total binds
        e.ex._refresh_tier(e.clock())
        held = dict(e.ex.counters.tier_state)
        changes = len(e.ledger("tier_change"))
        e.ex.state.realized_lamports = -500_000_000  # T1 lost 0.50: one more 0.10 buy is the last that the 0.60 total allows
        h.TIER_FILE_PATH.write_text("garbage\n")  # a problem read ...
        e.ex._refresh_tier(e.clock())
        self.assertEqual((e.ex.tier, e.ex.counters.tier_state, len(e.ledger("tier_change"))), ("T0", held, changes))  # T0's limits, nothing re-started
        self.write_tier("T1\n")  # ... and then T1 again, the tier the counters hold: the baselines are kept
        e.ex._refresh_tier(e.clock())
        self.assertEqual((e.ex.tier, e.ex.counters.tier_state, len(e.ledger("tier_change"))), ("T1", held, changes))
        self.assertEqual(self.stop_at(e, -500_000_000), "total_loss_stop")  # since T1's real start: -0.50 and this 0.10 stake reach 0.60
        self.assertIsNone(self.stop_at(e, -499_999_999))


class ProblemReadTests(TierCase):
    """H2: a problem read of the tier file (invalid, unsafe, unreadable) applies T0's limits to that trigger without re-starting the tier."""

    def profit_then_t1(self) -> Env:
        e = self.tier_env("T0", balance=1_300_000_000)
        e.ex._refresh_tier(e.clock())
        e.ex.state.realized_lamports = 300_000_000
        self.write_tier("T1\n")
        e.ex._refresh_tier(e.clock())
        return e

    def test_the_prior_profit_flicker_repro_cannot_widen_the_35_percent_cap(self):
        e = self.profit_then_t1()
        self.assertEqual(e.ex._total_stop_lamports(), 455_000_000)
        e.ex.state.realized_lamports = 300_000_000 - 350_000_000  # T1 lost 0.35: allowed
        self.assertIsNone(e.ex._budget_stop(e.clock()))
        e.rpc.balance = 950_000_000
        e.ex._bal = None  # the wallet reads lower now: a re-baseline would take 35% of THIS
        h.TIER_FILE_PATH.write_text("")  # the truncate-then-write window of a non-atomic `echo T1 > TIER`
        e.ex._refresh_tier(e.clock())
        self.write_tier("T1\n")
        e.ex._refresh_tier(e.clock())
        ts = e.ex.counters.tier_state
        self.assertEqual((e.ex.tier, ts["realized_at_start"], ts["wallet_lamports"], e.ex._total_stop_lamports()), ("T1", 300_000_000, 1_300_000_000, 455_000_000))
        self.assertEqual(len(e.ledger("tier_change")), 2)  # T0 at the start, T1: the flicker added none
        # the loss since the ORIGINAL T1 start is still held to 0.455 including the next stake (it used to reach about 0.7)
        e.ex.state.realized_lamports = 300_000_000 - 354_999_999
        self.assertIsNone(e.ex._budget_stop(e.clock()))
        e.ex.state.realized_lamports = 300_000_000 - 355_000_000
        self.assertEqual(e.ex._budget_stop(e.clock()), "total_loss_stop")

    def test_a_trigger_during_a_problem_read_is_judged_at_t0_with_the_held_baseline(self):
        e = self.profit_then_t1()
        held = dict(e.ex.counters.tier_state)
        h.TIER_FILE_PATH.write_text("garbage\n")
        e.fire()  # T0's 0.02 stake and T0's limits (T1 has lost nothing yet since its start)
        self.assertEqual((e.ex.tier, e.refusals(), buy_args(e.sent()[0])[0], e.ex.counters.tier_state), ("T0", [], 20_000_000, held))
        self.assertEqual(len(e.alerts("tier_file_problem")), 1)
        sub = self.tmp / "lost"
        sub.mkdir()
        h.TIER_FILE_PATH.write_text("T1\n")
        e2 = Env(sub)
        e2.rpc.balance = 10 * SOL
        e2.ex._refresh_tier(e2.clock())
        e2.ex.state.realized_lamports = -130_000_000  # T1 lost 0.13: fine for T1 (0.60), over T0's 0.12
        h.TIER_FILE_PATH.write_text("garbage\n")
        e2.fire()
        self.assertEqual((e2.ex.tier, e2.refusals(), e2.rpc.sent), ("T0", ["total_loss_stop"], []))  # fails safe: T0's limit on the held baseline
        self.assertEqual(len(e2.alerts("t0_budget_stop")), 1)  # and it is not silent
        self.write_tier("T1\n")
        e2.fire()
        self.assertEqual((e2.ex.tier, buy_args(e2.sent()[0])[0]), ("T1", 100_000_000))  # the file is whole again: T1 carries on

    def test_unsafe_unreadable_and_invalid_reads_are_all_problem_reads(self):
        for i, (text, mode) in enumerate((("garbage\n", 0o644), ("T1\n", 0o666), ("", 0o644), ("T1 T2\n", 0o644))):
            sub = self.tmp / f"p{i}"
            sub.mkdir()
            self.write_tier("T1\n")
            e = Env(sub)
            e.rpc.balance = 10 * SOL
            e.ex._refresh_tier(e.clock())
            held, changes = dict(e.ex.counters.tier_state), len(e.ledger("tier_change"))
            self.write_tier(text, mode)
            e.ex._refresh_tier(e.clock())
            self.assertEqual((e.ex.tier, e.ex.counters.tier_state, len(e.ledger("tier_change"))), ("T0", held, changes), (text, oct(mode)))
            self.assertEqual(len(e.alerts("tier_file_problem")), 1, (text, oct(mode)))

    def test_a_missing_file_is_the_documented_t0_and_does_restart_the_tier(self):
        e = self.profit_then_t1()
        h.TIER_FILE_PATH.unlink()  # Helm removes the file to go down: a real, deliberate T0
        e.ex._refresh_tier(e.clock())
        self.assertEqual((e.ex.tier, e.ex.counters.tier_state["tier"]), ("T0", "T0"))
        self.assertEqual(self.changes(e)[-1], ("T1", "T0", "tier_file_missing"))
        self.assertEqual(e.alerts("tier_file_problem"), [])  # a missing file is not a problem alert

    def test_a_problem_read_on_a_fresh_start_starts_t0_with_the_problem_recorded(self):
        self.write_tier("garbage\n")
        e = self.env()
        e.rpc.balance = 10 * SOL
        e.ex._refresh_tier(e.clock())
        self.assertEqual((e.ex.tier, e.ex.counters.tier_state["tier"]), ("T0", "T0"))
        self.assertEqual(self.changes(e), [(None, "T0", "tier_file_invalid")])

    def test_a_restart_during_a_problem_keeps_the_held_tier_and_its_baselines(self):
        e = self.profit_then_t1()
        held = dict(e.ex.counters.tier_state)
        e.ex.save()
        h.TIER_FILE_PATH.write_text("garbage\n")
        e.ex = e.build()
        e.ex._refresh_tier(e.clock())
        self.assertEqual((e.ex.tier, e.ex.counters.tier_state), ("T0", held))
        self.write_tier("T1\n")
        e.ex._refresh_tier(e.clock())
        self.assertEqual((e.ex.tier, e.ex.counters.tier_state), ("T1", held))


class StepDownTests(TierCase):
    """H1 (manager ruling): after a step-down T0 keeps trading. The run-cumulative check is against the highest total stop of any tier entered in
    this run (persisted), and T0's own since-start limit, min(0.12, 35% of the wallet at T0 start), binds. A stop at T0 alerts."""

    def stepped_down(self, t1_loss: int = 300_000_000, balance: int = 10 * SOL) -> Env:
        e = self.tier_env("T1", balance=balance)
        e.ex._refresh_tier(e.clock())
        e.ex.state.realized_lamports = -t1_loss  # T1 lost this much (inside its 0.60)
        self.write_tier("T0\n")
        e.ex._refresh_tier(e.clock())
        return e

    def stop_at(self, e: Env, realized: int):
        e.ex.state.realized_lamports = realized
        return e.ex._budget_stop(e.clock())

    def test_step_down_after_t1_losses_keeps_t0_trading(self):
        e = self.stepped_down()  # the reviewer's repro, inverted: it used to refuse every T0 buy for good
        self.assertEqual((e.ex.tier, e.ex.counters.tier_state["realized_at_start"], e.ex._tier_realized()), ("T0", -300_000_000, 0))
        self.assertIsNone(e.ex._budget_stop(e.clock()))
        e.ex.counters.days.clear()
        self.assertIsNone(e.ex._budget_stop(e.clock() + 2 * 86_400_000))  # and the next day
        e.fire()
        self.assertEqual((e.refusals(), buy_args(e.sent()[0])[0]), ([], 20_000_000))
        self.assertEqual(e.alerts("t0_budget_stop"), [])

    def test_t0s_own_since_start_limit_binds_after_the_step_down(self):
        e = self.stepped_down()
        self.assertIsNone(self.stop_at(e, -300_000_000 - 99_999_999))  # 0.12 from T0's start, this 0.02 stake included
        self.assertEqual(self.stop_at(e, -300_000_000 - 100_000_000), "total_loss_stop")

    def test_t0s_35_percent_of_its_own_start_wallet_binds_too(self):
        e = self.stepped_down(t1_loss=100_000_000, balance=250_000_000)  # 35% of the 0.25 SOL wallet at T0's start = 0.0875
        self.assertEqual(e.ex._total_stop_lamports(), 87_500_000)
        self.assertIsNone(self.stop_at(e, -100_000_000 - 67_499_999))
        self.assertEqual(self.stop_at(e, -100_000_000 - 67_500_000), "total_loss_stop")

    def test_the_run_cumulative_check_is_against_the_highest_tier_total_entered_and_is_persisted(self):
        e = self.stepped_down()
        self.assertEqual(e.ex.counters.max_total_loss_lamports, 600_000_000)  # T1's, entered earlier in this run
        self.assertEqual(e.ex._run_total_stop_lamports(), 600_000_000)
        e.ex.save()
        e.ex = e.build()
        self.assertEqual((e.ex.tier, e.ex._run_total_stop_lamports()), ("T0", 600_000_000))  # a restart keeps it
        e.ex.state.realized_lamports = -500_000_000  # (T0 lost 0.20 on top; its own limit is not what this test is about)
        self.write_tier("T1\n")  # a real tier change: T1 starts afresh, so only the run-cumulative check can still bind
        e.ex._refresh_tier(e.clock())
        self.assertEqual((e.ex.tier, e.ex._tier_realized(), e.ex._run_total_stop_lamports()), ("T1", 0, 600_000_000))
        self.assertEqual(self.stop_at(e, -500_000_000), "total_loss_stop")  # -0.50 and this 0.10 stake reach the highest total, 0.60
        self.assertIsNone(self.stop_at(e, -499_999_999))

    def test_the_stored_maximum_is_the_effective_total_after_config_tightens_it(self):
        e = self.tier_env("T1", balance=10 * SOL, total_loss_lamports=300_000_000)
        e.ex._refresh_tier(e.clock())
        self.assertEqual(e.ex.counters.max_total_loss_lamports, 300_000_000)

    def test_a_stop_at_t0_alerts_once_per_reason_and_day(self):
        e = self.tier_env()
        e.ex._refresh_tier(e.clock())
        e.ex.state.realized_lamports = -100_000_000  # T0: -0.10 - this 0.02 stake <= -0.12
        e.fire()
        e.fire()
        alerts = e.alerts("t0_budget_stop")
        self.assertEqual((e.refusals(), len(alerts), alerts[0]["why"]), (["total_loss_stop"] * 2, 1, "total_loss_stop"))
        e.jump(86_400_000)  # the next UTC day
        e.fire()
        self.assertEqual(len(e.alerts("t0_budget_stop")), 2)
        e.ex.counters.day(h.day_key(e.clock()))["trades"] = 30  # a different reason the same day
        e.ex.state.realized_lamports = 0
        e.fire()
        self.assertEqual([a["why"] for a in e.alerts("t0_budget_stop")], ["total_loss_stop", "total_loss_stop", "max_trades_day"])

    def test_no_t0_alert_at_a_higher_tier_or_for_a_latched_halt_or_in_a_dry_run(self):
        e = self.tier_env("T1")
        e.ex._refresh_tier(e.clock())
        e.ex.state.realized_lamports = -500_000_000
        e.fire()
        self.assertEqual((e.refusals(), e.alerts("t0_budget_stop")), (["total_loss_stop"], []))  # T1: the step-down alert is the one that fires
        d = Env(self.sub("dry"), live=False)
        d.ex._refresh_tier(d.clock())
        d.ex.state.realized_lamports = -100_000_000
        d.fire()
        self.assertEqual(d.alerts("t0_budget_stop"), [])  # a dry run records the stop as would_have_halted, it refuses nothing

    def sub(self, name: str):
        d = self.tmp / name
        d.mkdir()
        return d


class LegacyCountersTests(TierCase):
    """H3: counters that hold a tier but no stored realized_at_start (written before the baselines existed) are not trusted: alert at load, refuse
    buys until a tier_change re-baselines."""

    def legacy_env(self, tier: str = "T1", live: bool = True) -> Env:
        self.write_tier(tier + "\n")
        e = Env(self.tmp, live=live)
        e.rpc.balance = 10 * SOL
        e.ex._refresh_tier(e.clock())
        e.ex.save()
        path = Path(e.ex.counters_path)
        raw = json.loads(path.read_text())
        raw["tier_state"].pop("realized_at_start")  # as an older build wrote it
        raw.pop("max_total_loss_lamports", None)
        path.write_text(json.dumps(raw))
        e.ex = e.build()
        return e

    def test_alert_at_load_and_buys_refused(self):
        e = self.legacy_env()
        alerts = e.alerts("tier_state_legacy")
        self.assertEqual([(a["tier"]) for a in alerts], ["T1"])
        e.ex._refresh_tier(e.clock())  # the file still says T1: the same tier, so nothing re-baselines
        e.fire()
        self.assertEqual((e.refusals(), e.rpc.sent), (["tier_state_legacy"], []))
        self.assertNotIn("realized_at_start", e.ex.counters.tier_state)

    def test_a_tier_change_rebaselines_and_trading_resumes(self):
        e = self.legacy_env()
        e.fire()
        self.assertEqual(e.refusals(), ["tier_state_legacy"])
        e.ex.state.realized_lamports = -40_000_000
        self.write_tier("T0\n")
        e.fire()  # the file now says T0: a tier_change, which stores the baseline
        ts = e.ex.counters.tier_state
        self.assertEqual((ts["tier"], ts["realized_at_start"], e.refusals(), buy_args(e.sent()[0])[0]), ("T0", -40_000_000, ["tier_state_legacy"], 20_000_000))
        self.assertEqual(self.changes(e)[-1], ("T1", "T0", None))
        e.ex.save()
        e.ex = e.build()
        self.assertEqual(len(e.alerts("tier_state_legacy")), 1)  # no second alert after the restart: the baseline is stored now

    def test_a_problem_read_does_not_rebaseline_a_legacy_tier(self):
        e = self.legacy_env()
        h.TIER_FILE_PATH.write_text("garbage\n")
        e.fire()
        self.assertEqual((e.refusals(), e.rpc.sent), (["tier_state_legacy"], []))
        self.assertNotIn("realized_at_start", e.ex.counters.tier_state)

    def test_counters_with_a_stored_baseline_or_no_tier_at_all_are_not_legacy(self):
        e = self.tier_env("T1")
        e.ex._refresh_tier(e.clock())
        e.ex.save()
        e.ex = e.build()
        self.assertEqual((e.alerts("tier_state_legacy"), e.ex._tier_state_legacy), ([], False))
        sub = self.tmp / "fresh"
        sub.mkdir()
        f = Env(sub)  # a first start: no tier held yet
        self.assertEqual((f.alerts("tier_state_legacy"), f.ex._tier_state_legacy), ([], False))

    def test_a_dry_run_records_it_as_would_have_halted(self):
        e = self.legacy_env(live=False)
        e.fire()
        self.assertEqual(e.ledger("decision")[0]["would_have_halted"], "tier_state_legacy")


class OneTierReadPerTriggerTests(TierCase):
    """G2: the tier file is read ONCE per trigger. A rewrite between two reads (the reviewer's TOCTOU repros) cannot make the buy go out at a
    tier that the earlier checks did not judge, and the T2 guard has a second line of defence in the build and in the signer."""

    def test_one_read_per_trigger(self):
        e = self.tier_env("T1")
        with mock.patch.object(h, "read_tier", wraps=h.read_tier) as rt:
            e.fire()
        self.assertEqual((rt.call_count, e.refusals(), buy_args(e.sent()[0])[0]), (1, [], 100_000_000))

    def test_a_t2_rewrite_mid_trigger_does_not_buy_at_t2_while_the_guard_is_off(self):
        e = self.tier_env("T1")
        orig = e.ex._hard_refusal

        def hard_refusal(trg, now):
            self.write_tier("T2\n")  # Helm writes T2 while this trigger is being handled
            return orig(trg, now)

        with mock.patch.object(h, "T2_IMPACT_OK", False), mock.patch.object(e.ex, "_hard_refusal", side_effect=hard_refusal):
            e.fire()
            self.assertEqual((e.ex.tier, e.refusals(), buy_args(e.sent()[0])[0]), ("T1", [], 100_000_000))  # the whole trigger was judged at T1
            e.ex.state.bought.clear()
            e.ex.state.pending.clear()
            e.fire()  # the NEXT trigger reads T2, and T2 is refused while the guard is off
        self.assertEqual((e.ex.tier, e.refusals(), len(e.rpc.sent)), ("T2", ["t2_impact_unchecked"], 1))

    def test_a_step_down_mid_trigger_is_judged_at_one_tier(self):
        e = self.tier_env("T1")
        e.ex.state.open.update({"a": {}, "b": {}})  # T1 allows a third position, T0 would not
        orig = e.ex._hard_refusal

        def hard_refusal(trg, now):
            r = orig(trg, now)  # max_open judged at T1 (3)
            self.write_tier("T0\n")
            return r

        with mock.patch.object(e.ex, "_hard_refusal", side_effect=hard_refusal):
            e.fire()
        self.assertEqual((e.ex.tier, e.refusals(), buy_args(e.sent()[0])[0]), ("T1", [], 100_000_000))  # not a T1 max_open with a T0 stake
        e.ex._refresh_tier(e.clock())
        self.assertEqual(e.ex.tier, "T0")  # the file is honoured at the next read

    def test_live_buy_refuses_t2_again_if_the_guard_turns_off_after_the_first_check(self):
        e = self.tier_env("T2")
        orig = e.ex._static_for

        def static_for(trg, now):
            h.T2_IMPACT_OK = False  # the guard is read again in _live_buy: it must not trust the earlier check
            return orig(trg, now)

        self.addCleanup(setattr, h, "T2_IMPACT_OK", True)
        with mock.patch.object(e.ex, "_static_for", side_effect=static_for):
            e.fire()
        self.assertEqual((e.refusals(), e.rpc.sent), (["t2_impact_unchecked"], []))

    def test_the_signer_cap_is_never_above_t1_while_t2_is_off(self):
        e = self.tier_env("T1")
        e.fire()
        ps = e.ex.pool_cache[MINT][0]
        bhash = e.ex.bh.get()[0]
        e.ex.tier = "T2"  # a T2-sized message reaches the signer by some path that skipped both guards
        msg = e.ex._buy_message(ps, e.ex.user, 1, bhash)
        self.assertEqual(e.ex.h5.stake_lamports, 300_000_000)
        with mock.patch.object(h, "T2_IMPACT_OK", False):
            self.assertEqual(e.ex._signer_spend_cap(), 100_000_000)
            with self.assertRaises(h.pl.UnsafeTx) as cm:
                e.ex._sign(msg, ps, MINT, cap=e.ex.h5.buy_priority_lamports)
            self.assertEqual(cm.exception.label, "unsafe_tx:spend_over_size")
            e.ex.tier = "T0"  # and a lower tier keeps its own, smaller cap
            self.assertEqual(e.ex._signer_spend_cap(), 20_000_000)
        self.assertEqual(e.ex._signer_spend_cap(), 20_000_000)
        e.ex.tier = "T2"
        self.assertEqual(e.ex._signer_spend_cap(), 300_000_000)  # with the guard on, T2's stake is signable
        sig, _b64 = e.ex._sign(msg, ps, MINT, cap=e.ex.h5.buy_priority_lamports)
        self.assertTrue(sig)


class ShippedLadderTests(TierCase):
    """G3: the shipped configs must not hold the five tier-scaled limits, or the ladder does nothing (config can only tighten)."""

    KEYS = ("stake_lamports", "max_open", "max_trades_per_day", "daily_loss_lamports", "total_loss_lamports")
    ROOT = Path(h.__file__).resolve().parent.parent

    def shipped(self, name: str) -> dict:
        return json.loads((self.ROOT / "scripts/mal-fast" / name).read_text())

    def test_the_shipped_configs_hold_none_of_the_tier_scaled_keys(self):
        for name in ("h5-executor-live.json", "h5-executor.json"):
            for k in self.KEYS:
                self.assertNotIn(k, self.shipped(name), f"{name}: {k} would clamp T1 and T2 to T0's value")

    def test_the_shipped_live_config_gives_each_tier_its_table_values(self):
        live = self.shipped("h5-executor-live.json")
        for tier, vals in h.TIERS.items():
            lim = h.H5Limits.from_config(live, tier)
            self.assertEqual({k: getattr(lim, k) for k in self.KEYS}, vals, tier)
        self.assertEqual(h.H5Limits.from_config(live, "T1").stake_lamports, 100_000_000)
        self.assertEqual(h.H5Limits.from_config(live, "T2").stake_lamports, 300_000_000)

    def test_an_executor_on_the_shipped_live_config_buys_the_tier_stake(self):
        live = {k: v for k, v in self.shipped("h5-executor-live.json").items()
                if k not in ("mode", "intents_file", "state_dir", "end_ms", "commitment")}  # paths and the run end belong to the test env
        for tier, stake in (("T0", 20_000_000), ("T1", 100_000_000), ("T2", 300_000_000)):
            sub = self.tmp / f"shipped-{tier}"
            sub.mkdir()
            self.write_tier(tier + "\n")
            e = Env(sub, **live)
            e.rpc.balance = 10 * SOL
            e.ex.feed_last_ms = e.clock()  # (the shipped config keeps the feed-heartbeat check on)
            e.fire()
            self.assertEqual((e.refusals(), buy_args(e.sent()[0])[0], e.ex._config_clamps()), ([], stake, {}), tier)
            self.assertEqual(e.alerts(), [], tier)

    def test_a_config_that_clamps_the_active_tier_raises_an_alert_on_a_tier_change_and_at_start(self):
        self.write_tier("T1\n")
        e = Env(self.tmp, stake_lamports=20_000_000, max_open=2)  # the old shipped values
        e.rpc.balance = 10 * SOL
        self.assertEqual(e.alerts("config_clamps_tier"), [])  # a fresh start runs at T0 until the file is read, and T0's table is the config's numbers
        e.ex._refresh_tier(e.clock())  # the file says T1: the tier starts here
        clamps = {"stake_lamports": {"table": 100_000_000, "effective": 20_000_000}, "max_open": {"table": 3, "effective": 2}}
        self.assertEqual(e.ex._config_clamps(), clamps)
        self.assertEqual([(a["when"], a["tier"], a["clamps"]) for a in e.alerts("config_clamps_tier")], [("tier_change", "T1", clamps)])
        self.assertEqual(e.ledger("tier_change")[-1]["config_clamps"], clamps)
        e.fire()
        self.assertEqual(buy_args(e.sent()[0])[0], 20_000_000)  # the ladder really is off: that is what the alert says
        e.ex = e.build()  # a restart keeps T1: the alert is raised again at start
        self.assertEqual([(a["when"], a["tier"]) for a in e.alerts("config_clamps_tier")], [("tier_change", "T1"), ("start", "T1")])
        self.assertEqual(e.ledger("start")[-1]["config_clamps"], clamps)
        self.write_tier("T2\n")
        e.ex._refresh_tier(e.clock())
        t2 = {"stake_lamports": {"table": 300_000_000, "effective": 20_000_000}, "max_open": {"table": 3, "effective": 2}}
        self.assertEqual([(a["when"], a["tier"]) for a in e.alerts("config_clamps_tier")][-1], ("tier_change", "T2"))
        self.assertEqual(e.ledger("tier_change")[-1]["config_clamps"], t2)

    def test_no_alert_when_the_config_does_not_clamp(self):
        self.write_tier("T1\n")
        e = Env(self.tmp, max_trades_per_day=40)  # equal to the table value is not a clamp
        e.ex._refresh_tier(e.clock())
        self.assertEqual((e.ex._config_clamps(), e.alerts("config_clamps_tier"), e.ledger("start")[0]["config_clamps"]), ({}, [], {}))


class AttemptsPerTierTests(TierCase):
    def attempt(self, e: Env, **kw) -> None:
        e.ex.state.bought.clear()  # (the fixture has one pool: let the same mint be tried again)
        e.ex.state.pending.clear()
        e.ex.state.open.clear()
        e.fire(**kw)

    def test_the_cap_is_150_per_tier_and_binds_at_the_150th(self):
        self.assertEqual(h.H5Limits.from_config({}).max_attempts, 150)
        e = self.tier_env()
        e.ex._refresh_tier(e.clock())  # the first look starts the tier (and zeroes its count): seed the count after it
        e.ex.counters.tier_attempts = 149
        self.attempt(e)
        self.assertEqual((len(e.rpc.sent), e.ex.counters.tier_attempts), (1, 150))  # the 150th attempt goes
        self.attempt(e)
        self.assertEqual((e.refusals(), len(e.rpc.sent)), (["max_attempts"], 1))  # the 151st does not

    def test_a_tier_change_resets_the_per_tier_counter_and_the_lifetime_counter_keeps_counting(self):
        e = self.tier_env()
        e.ex._refresh_tier(e.clock())
        e.ex.counters.tier_attempts = 150
        e.ex.state.attempts = 150  # (150 spent at T0 in all)
        self.attempt(e)
        self.assertEqual(e.refusals(), ["max_attempts"])
        self.write_tier("T1\n")
        self.attempt(e)  # T1: a fresh 150
        self.assertEqual((len(e.rpc.sent), e.ex.counters.tier_attempts, e.ex.state.attempts), (1, 1, 151))
        row = e.ledger("tier_change")[-1]
        self.assertEqual((row["from_tier"], row["to_tier"], row["attempts_in_old_tier"], row["lifetime_attempts"]), ("T0", "T1", 150, 150))
        e.ex.counters.tier_attempts = 150
        self.attempt(e)
        self.assertEqual(e.refusals()[-1], "max_attempts")  # and T1 is capped at 150 on its own
        self.write_tier("T0\n")  # stepping down starts a fresh count too
        self.attempt(e)
        self.assertEqual((e.ex.counters.tier_attempts, e.ex.state.attempts), (1, 152))

    def test_the_per_tier_count_survives_a_restart_and_config_can_tighten_it(self):
        e = self.tier_env(max_attempts=5)
        for _ in range(5):
            self.attempt(e)
        self.assertEqual((len(e.rpc.sent), e.ex.counters.tier_attempts), (5, 5))
        e.ex = e.build()  # a restart does not reset it
        self.assertEqual(e.ex.counters.tier_attempts, 5)
        self.attempt(e)
        self.assertEqual((e.refusals(), len(e.rpc.sent)), (["max_attempts"], 5))

    def test_a_dry_run_counts_per_tier_too(self):
        e = Env(self.tmp, live=False)
        e.fire()
        self.assertEqual((e.ex.counters.tier_attempts, e.ex.state.attempts), (1, 1))


class TierChangeTests(TierCase):
    def test_a_tier_change_never_touches_an_open_position(self):
        e = self.tier_env("T1")
        pos = e.open_position()
        self.assertEqual(pos["spend"], 100_000_000)
        before = copy.deepcopy(e.ex.state.open[MINT])
        plan = e.plan()
        self.write_tier("T0\n")  # step down
        e.ex.prewarm()
        self.assertEqual(self.changes(e)[-1], ("T1", "T0", None))
        self.assertEqual(e.ledger("tier_change")[-1]["open_positions"], 1)
        self.assertEqual(e.ex.state.open[MINT], before)  # untouched
        self.write_tier("T2\n")  # and up
        e.ex.prewarm()
        self.assertEqual(e.ex.state.open[MINT], before)
        e.at_slot(plan["send_slot"])  # the exit still goes out on the position's own plan, selling the tokens it holds
        self.assertEqual(len(e.rpc.sent), 2)

    def test_a_step_down_is_due_after_a_halt_or_a_loss_stop_above_t0_and_the_file_is_not_touched(self):
        e = self.tier_env("T1")
        e.ex._refresh_tier(e.clock())
        due = lambda: [r for r in e.ledger("alert") if r.get("alert") == "tier_step_down_due"]  # noqa: E731
        e.ex._latch("late_sells_gt_5pct", late=1, landed=1)
        self.assertEqual((len(due()), due()[0]["tier"], due()[0]["why"]), (1, "T1", "late_sells_gt_5pct"))
        e.ex.state.realized_lamports = -500_000_000
        e.fire()
        e.fire()  # a second trigger the same day: one alert for the same reason
        self.assertEqual(sorted(r["why"] for r in due()), ["late_sells_gt_5pct"])  # (the latched halt answers first once it exists)
        self.assertEqual(h.TIER_FILE_PATH.read_text(), "T1\n")  # never edited by the executor

    def test_a_loss_stop_above_t0_raises_the_alert_once_a_day(self):
        e = self.tier_env("T1")
        e.ex.state.realized_lamports = -500_000_000
        e.fire()
        e.fire()
        due = [r for r in e.ledger("alert") if r.get("alert") == "tier_step_down_due"]
        self.assertEqual((len(due), due[0]["why"], e.refusals()), (1, "total_loss_stop", ["total_loss_stop", "total_loss_stop"]))

    def test_no_step_down_alert_at_t0(self):
        e = self.tier_env()
        e.ex._latch("late_sells_gt_5pct", late=1, landed=1)
        e.ex.state.realized_lamports = -100_000_000
        e.fire()
        self.assertEqual([r for r in e.ledger("alert") if r.get("alert") == "tier_step_down_due"], [])


if __name__ == "__main__":
    unittest.main()
