"""H5 executor: the scale ladder (owner decision). The tier comes from a root-owned file read before every buy; T2 is refused until
T2_IMPACT_OK; the total stop is also capped at 35% of the wallet at tier start. Fixtures: test_h5_executor."""

from __future__ import annotations

import copy
import json
import os
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
        self.assertIs(h.T2_IMPACT_OK, False)  # the shipped value: T2 is refused until the price-impact check is done and this is reviewed
        self.assertEqual(h.H5Limits.from_config({}).stake_lamports, h.TIERS["T0"]["stake_lamports"])  # no tier means T0

    def test_constants_in_source(self):
        src = Path(h.__file__).read_text()
        self.assertIn('TIER_FILE_PATH = Path("/etc/mal-h5/TIER")', src)
        self.assertIn("T2_IMPACT_OK = False", src)

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

    def test_t1_buy_is_100m_and_t2_is_refused_while_the_impact_check_is_pending(self):
        e = self.tier_env("T1")
        e.fire()
        self.assertEqual((buy_args(e.sent()[0])[0], e.refusals()), (100_000_000, []))
        sub = self.tmp / "t2"
        sub.mkdir()
        self.write_tier("T2\n")
        e2 = Env(sub)
        e2.rpc.balance = 10 * SOL
        e2.fire()
        self.assertEqual((e2.refusals(), e2.rpc.sent, e2.ex.tier), (["t2_impact_unchecked"], [], "T2"))
        with mock.patch.object(h, "T2_IMPACT_OK", True):
            sub = self.tmp / "t2ok"
            sub.mkdir()
            e3 = Env(sub)
            e3.rpc.balance = 10 * SOL
            e3.fire()
            self.assertEqual((e3.refusals(), buy_args(e3.sent()[0])[0]), ([], 300_000_000))

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
