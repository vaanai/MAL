"""DEC-026 tests for the C1-NF executor profile (items 9 and 23): the limits as code constants (stops fire, config only lowers, a restart cannot
reset them, TIER missing or invalid means T1), the exposure test at stake 0.05 / total stop 0.175 / 2 open, the profile's own files, synthetic pools
traded with no class on any record, the pick oracle failing closed from 2026-10-16T01Z, the landing p50 halt, and H5's module left as it was.
Offline: the H5 fake RPC and chain, throwaway keys, no network, no real key."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest import mock

from tools import c1nf_executor as c
from tools import h5_executor as h5
from tools import probe_executor as pe
from tools.test_c1nf_executor import MINUTE, STAKE, Case, outcome, touch_streams, write_stream
from tools.test_h5_executor import MINT, POOL, QREAL
from tools.test_probe_executor import BASE0, T0, V

SOL = 1_000_000_000


class TierCase(Case):
    def setUp(self):
        super().setUp()
        p = mock.patch.object(c, "TIER_FILE_PATH", self.etc / "TIER")
        p.start()
        self.addCleanup(p.stop)

    def write_tier(self, text: str, mode: int = 0o644) -> None:
        c.TIER_FILE_PATH.unlink(missing_ok=True)
        c.TIER_FILE_PATH.write_text(text)
        os.chmod(c.TIER_FILE_PATH, mode)


class TierTests(TierCase):
    def test_the_table_is_dec026s_and_t1_is_the_lowest_and_only_active_tier(self):
        self.assertEqual(c.C1NF_TIERS, {"T1": {"stake_lamports": 100_000_000, "max_open": 2, "max_trades_per_day": 30, "daily_loss_lamports": 200_000_000,
                                               "total_loss_lamports": 300_000_000}})
        self.assertEqual((c.C1NF_LOWEST_TIER, c.C1NF_INACTIVE_TIERS), ("T1", ("T2",)))
        self.assertEqual(h5.TIER_WALLET_FRAC, 0.35)  # the 35% cap the total stop inherits

    def test_missing_invalid_unsafe_or_inactive_means_t1(self):
        self.assertEqual(c.read_tier(c.TIER_FILE_PATH, True), ("T1", "tier_file_missing"))
        for text in ("T0", "T3", "t1", "", "T1 T2", "garbage"):
            self.write_tier(text)
            self.assertEqual(c.read_tier(c.TIER_FILE_PATH, True), ("T1", "tier_file_invalid"), text)
        self.write_tier("T2\n")
        self.assertEqual(c.read_tier(c.TIER_FILE_PATH, True), ("T1", "tier_file_inactive"))
        self.write_tier("T1\n")
        self.assertEqual(c.read_tier(c.TIER_FILE_PATH, True), ("T1", None))
        self.write_tier("T1", mode=0o666)
        self.assertEqual(c.read_tier(c.TIER_FILE_PATH, True), ("T1", "tier_file_unsafe"))

    def test_a_tier_file_saying_t2_runs_t1_and_alerts(self):
        e = self.env()
        self.write_tier("T2")
        e.fire()
        self.assertEqual(e.ex.tier, "T1")
        self.assertEqual(e.ex.h5.stake_lamports, STAKE)
        self.assertEqual([a["problem"] for a in e.ledger("alert") if a["alert"] == "tier_file_problem"], ["tier_file_inactive"])
        self.assertIn(MINT, e.ex.state.pending)
        from tools.test_h5_executor import buy_args
        self.assertEqual(buy_args(e.sent()[0])[0], STAKE)

    def test_h5s_lowest_tier_name_never_reaches_c1nf(self):
        e = self.env(live=False)
        e.ex.tier = "T0"  # what H5's own code writes at start and on a problem read
        self.assertEqual(e.ex.tier, "T1")
        self.assertEqual(e.ex.h5.max_open, 2)
        self.assertEqual(e.ex.counters.tier_state.get("tier"), None)  # nothing started a tier before the first pick
        e.fire()
        self.assertEqual(e.ex.counters.tier_state["tier"], "T1")


class LimitTests(TierCase):
    def test_config_only_lowers(self):
        l = c.c1nf_limits({"stake_lamports": 2 * 10**8, "max_open": 5, "max_trades_per_day": 99, "daily_loss_lamports": SOL, "total_loss_lamports": SOL,
                           "buy_priority_lamports": 10**6, "end_ms": c.C1NF_END_MAX_MS + 86_400_000})
        self.assertEqual((l.stake_lamports, l.max_open, l.max_trades_per_day, l.daily_loss_lamports, l.total_loss_lamports, l.buy_priority_lamports, l.end_ms),
                         (100_000_000, 2, 30, 200_000_000, 300_000_000, 505_000, c.C1NF_END_MAX_MS))
        lo = c.c1nf_limits({"stake_lamports": 50_000_000, "end_ms": c.C1NF_END_MAX_MS - 1})
        self.assertEqual((lo.stake_lamports, lo.end_ms), (50_000_000, c.C1NF_END_MAX_MS - 1))
        with self.assertRaises(ValueError):
            c.c1nf_limits({}, "T2")  # an inactive tier has no limits
        self.assertEqual(c.C1NF_END_MAX_MS, 1792801800000)  # 2026-10-24T00:30:00Z

    def test_the_end_instant_stops_buys(self):
        e = self.env(end_ms=c.C1NF_END_MAX_MS)  # (C1NF_END_MAX_MS is inside the CAP-PICK seal: there the seal refuses first, a count only)
        e.fire(minute=c.C1NF_END_MAX_MS // MINUTE * MINUTE + MINUTE)
        self.assertEqual((e.refusals(), e.ex.counters.seal_skips), ([], 1))
        e2 = self.fresh("early", end_ms=e.t0 + 5 * MINUTE)
        e2.fire(minute=e2.t0 + 6 * MINUTE)
        self.assertEqual(e2.refusals(), ["end_instant"])


class ExposureTests(TierCase):
    """DEC-026 section 6 at a 0.5 SOL wallet: total stop min(0.30, 0.35 x 0.5) = 0.175; every open or in-flight stake and this one count as lost."""

    def env05(self, name: str, wallet: int = SOL // 2):
        e = self.fresh(name)
        e.rpc.balance = wallet
        e.ex._bal = None
        e.ex._refresh_tier(e.clock())
        return e

    def test_the_total_stop_is_0_175_at_a_0_5_sol_wallet(self):
        e = self.env05("a")
        self.assertEqual(e.ex._total_stop_lamports(), 175_000_000)
        e2 = self.env05("b", wallet=2 * SOL)
        self.assertEqual(e2.ex._total_stop_lamports(), 300_000_000)  # the owner's 0.30 binds only from about 0.857 SOL

    def test_first_buy_needs_tier_realized_above_minus_0_125(self):
        e = self.env05("a")
        e.ex.state.realized_lamports = -125_000_000
        e.fire()
        self.assertEqual(e.refusals(), ["total_loss_stop"])
        e2 = self.env05("b")
        e2.ex.state.realized_lamports = -124_999_999
        e2.fire()
        self.assertIn(MINT, e2.ex.state.pending)

    def test_a_second_concurrent_buy_needs_tier_realized_above_minus_0_075(self):
        for name, realized, taken in (("a", -75_000_000, False), ("b", -74_999_999, True)):
            e = self.env05(name)
            e.ex.state.open["Other111111111111111111111111111111111111111"] = {"mint": "Other", "spend": STAKE}
            e.ex.state.realized_lamports = realized
            e.fire()
            self.assertEqual(MINT in e.ex.state.pending, taken, name)
            if not taken:
                self.assertEqual(e.refusals(), ["total_loss_stop"])

    def test_two_open_are_reachable_from_the_first_send_and_a_third_is_not(self):
        e = self.env05("a")
        e.ex.state.open["Other111111111111111111111111111111111111111"] = {"mint": "Other", "spend": STAKE}
        e.fire()
        self.assertIn(MINT, e.ex.state.pending)
        e2 = self.env05("b")
        for i in range(2):
            e2.ex.state.open[f"Other{i}"] = {"mint": f"Other{i}", "spend": STAKE}
        e2.fire()
        self.assertEqual(e2.refusals(), ["max_open"])

    def test_the_daily_stop_0_20_counts_open_exposure(self):
        e = self.env05("a", wallet=2 * SOL)
        e.ex.state.realized_lamports = -150_000_000
        e.ex.counters.day(h5.day_key(e.clock()))["realized"] = -150_000_000
        e.fire()
        self.assertEqual(e.refusals(), ["daily_loss_stop"])
        e2 = self.env05("b", wallet=2 * SOL)
        e2.ex.state.realized_lamports = -149_999_999
        e2.ex.counters.day(h5.day_key(e2.clock()))["realized"] = -149_999_999
        e2.fire()
        self.assertIn(MINT, e2.ex.state.pending)


class RestartTests(TierCase):
    def test_a_restart_keeps_the_loss_the_tier_baseline_and_the_stop(self):
        e = self.env()
        e.rpc.balance = SOL // 2
        e.ex._bal = None
        e.ex._refresh_tier(e.clock())
        e.ex.state.realized_lamports = -125_000_000
        e.ex.save()
        e.ex.counters.save(e.ex.counters_path)
        e2 = self.env()  # the same state dir: a restart
        e2.rpc.balance = 5 * SOL  # a bigger wallet now cannot widen the stop: the baseline is the wallet at tier start
        self.assertEqual(e2.ex.state.realized_lamports, -125_000_000)
        e2.fire()
        self.assertEqual(e2.refusals(), ["total_loss_stop"])
        self.assertEqual(e2.ex._total_stop_lamports(), 175_000_000)

    def test_deleting_the_counters_cannot_reset_the_limits(self):
        e = self.env()
        e.fire()
        e.ex.counters_path.unlink()
        with self.assertRaises(SystemExit):
            self.env()


class FileTests(TierCase):
    def test_the_profiles_own_files(self):
        self.assertEqual((str(c.LIVE_OK_PATH.parent), c.LIVE_OK_PATH.name), (str(self.etc), "LIVE_OK"))
        src = Path(c.__file__).read_text()
        for literal in ('Path("/etc/mal-c1nf/LIVE_OK")', 'Path("/etc/mal-c1nf/TIER")', 'Path("/var/lib/mal-live/c1nf")', 'CREDENTIAL_NAME = "c1nf-wallet"'):
            self.assertIn(literal, src)
        e = self.env()
        self.assertEqual((e.ex.paths["stop"], e.ex.paths["halt"]), (str(self.state_dir / "STOP"), str(self.state_dir / "HALT")))
        self.assertEqual((e.ex.paths["live_ok"], e.ex.paths["tier_file"]), (str(c.LIVE_OK_PATH), str(c.TIER_FILE_PATH)))
        self.assertEqual(e.ledger("c1nf_start")[0]["tier_file"], str(c.TIER_FILE_PATH))

    def test_the_wallet_wide_stop_also_stops_it(self):
        e = self.env()
        (Path(pe.LIVE_DIR) / "STOP").write_text("")
        e.fire()
        self.assertEqual(e.refusals(), ["stop_file"])

    def test_live_start_refusals(self):
        base = {"state_dir": str(c.LIVE_STATE_DIR), "end_ms": c.C1NF_END_MAX_MS, "intents_file": "/x"}
        self.make_live_ok()
        root = self.tmp / "repo"
        (root / "EXP").mkdir(parents=True)
        (root / c.EXP025_PART1).write_text("x")
        self.assertIsNone(c.start_refusal(base, root))
        self.assertEqual(c.start_refusal({**base, "tier_file": "/tmp/T"}, root), "config_path_override:tier_file")
        self.assertEqual(c.start_refusal({k: v for k, v in base.items() if k != "end_ms"}, root), "end_ms_missing")
        self.assertEqual(c.start_refusal({**base, "state_dir": "/var/lib/mal-live/h5"}, root), "state_dir_not_c1nf")


class SyntheticTests(TierCase):
    """EXP-025 Amendment 2 / DEC-026 9.3: synthetic pools are traded, and no record that carries an outcome carries the class (item 5)."""

    @staticmethod
    def keys_and_values(obj, out):
        if isinstance(obj, dict):
            for k, v in obj.items():
                out.append(str(k))
                SyntheticTests.keys_and_values(v, out)
        elif isinstance(obj, list):
            for v in obj:
                SyntheticTests.keys_and_values(v, out)
        else:
            out.append(str(obj))
        return out

    def test_a_synthetic_pool_is_traded_and_the_class_is_on_no_record(self):
        e = self.env(live=False)
        touch_streams(e.shadow_dir)
        e.ex.intent_tick()
        row = e.row()
        rows = [{"type": "c1nf_heartbeat"}, {"type": "excluded", "pool": POOL, "mint": MINT, "reason": "synthetic"},
                {**row, "synthetic": True, "synthetic_src": "ws", "synthetic_class": "synthetic"}]
        write_stream(e.shadow_dir, rows)
        e.ex.intent_tick()
        self.assertEqual(e.refusals(), [])
        self.assertIn(MINT, e.ex.state.open)  # the dry run's virtual position: the pick was taken
        e.ex.on_outcome(MINT, row["decision_T_ms"], 4.0)
        write_stream(e.shadow_dir, [{**outcome(MINT, row["decision_T_ms"], 4.0), "synthetic": True}])
        e.ex.intent_tick()
        records = e.ledger() + [json.loads(e.ex.extra_path.read_text()), json.loads(Path(e.ex.state_path).read_text())]
        seen = " ".join(self.keys_and_values(records, [])).lower()
        self.assertNotIn("synthetic", seen)
        self.assertNotIn("excluded", seen)
        counters = json.loads(e.ex.counters_path.read_text())
        self.assertEqual((counters["synthetic_unconfirmed"], counters["excluded"]), (0, {}))  # H5's schema fields, never filled by C1-NF
        self.assertEqual(c.parse_pick({**row, "synthetic": False})[0], c.parse_pick({**row, "synthetic": True})[0])  # the class is never read


class SealTests(TierCase):
    def seal_env(self, oracle, final: bool = True):
        e = self.env(oracle=oracle)
        if final:
            Path(e.ex.final_marker).write_text("")
        return e

    def test_before_10_16t01_no_oracle_is_needed(self):
        e = self.env(oracle=None)
        e.fire(minute=h5.SEAL_START_MS - 2 * MINUTE)
        self.assertIn(MINT, e.ex.state.pending)

    def test_from_10_16t01_it_fails_closed_and_counts_only(self):
        when = h5.ORACLE_EARLIEST_MS + 10 * MINUTE
        cases = (("none", None, True), ("undecided", lambda m: None, True), ("raises", lambda m: (_ for _ in ()).throw(RuntimeError("x")), True),
                 ("nonbool", lambda m: 1, True), ("pick", lambda m: True, True), ("no_final", lambda m: False, False))
        for name, oracle, final in cases:
            e = self.fresh(name, oracle=oracle)
            if final:
                Path(e.ex.final_marker).write_text("")
            e.fire(minute=when)
            self.assertNotIn(MINT, e.ex.state.pending, name)
            self.assertEqual(e.ex.counters.seal_skips, 1, name)
            self.assertNotIn(MINT, json.dumps(e.ledger("skip")), name)  # never recorded per mint
            self.assertNotIn(MINT, json.dumps(e.ledger("pick_status")), name)
        e = self.fresh("open", oracle=lambda m: False)
        Path(e.ex.final_marker).write_text("")
        e.fire(minute=when)
        self.assertIn(MINT, e.ex.state.pending)
        e2 = self.fresh("first_hour", oracle=lambda m: False)  # 01Z to 02Z: H5's rule, no gate log before the FINAL is written
        Path(e2.ex.final_marker).write_text("")
        e2.fire(minute=h5.SEAL_START_MS + 5 * MINUTE)
        self.assertEqual(e2.ex.counters.seal_skips, 1)


class LandingTests(TierCase):
    def test_the_landing_p50_rule_is_1_9_s_over_20(self):
        e = self.env()
        for _ in range(19):
            e.ex._note_buy_landing(1_000, 1_005, 0.4)  # 2.0 s
        self.assertEqual(e.ex.counters.halts, {})
        e.ex._note_buy_landing(1_000, 1_005, 0.4)
        self.assertIn(c.LANDING_P50_HALT, e.ex.counters.halts)
        e2 = self.fresh("b")
        for _ in range(40):
            e2.ex._note_buy_landing(1_000, 1_019, 0.1)  # 1.9 s exactly: not above
        self.assertEqual(e2.ex.counters.halts, {})


class H5UntouchedTests(TierCase):
    def test_h5s_constants_are_its_own_after_a_c1nf_run(self):
        e = self.env()
        e.fire()
        e.ex.tick()
        self.assertEqual(h5.TIERS, {
            "T0": {"stake_lamports": 20_000_000, "max_open": 2, "max_trades_per_day": 30, "daily_loss_lamports": 80_000_000, "total_loss_lamports": 120_000_000},
            "T1": {"stake_lamports": 100_000_000, "max_open": 3, "max_trades_per_day": 40, "daily_loss_lamports": 400_000_000, "total_loss_lamports": 600_000_000},
            "T2": {"stake_lamports": 300_000_000, "max_open": 3, "max_trades_per_day": 40, "daily_loss_lamports": 1_200_000_000, "total_loss_lamports": 1_800_000_000}})
        self.assertEqual(h5.H5_DEFAULT, h5.H5_MAX)
        self.assertEqual((h5.H5_DEFAULT["stake_lamports"], h5.H5_DEFAULT["buy_priority_lamports"], h5.H5_DEFAULT["sell_priority_lamports"]),
                         (20_000_000, 55_000, 55_000))
        self.assertEqual((str(h5.LIVE_OK_PATH), str(h5.TIER_FILE_PATH), h5.RULE_ID), ("/etc/mal-h5/LIVE_OK", "/etc/mal-h5/TIER", "H5-BOOSTFLOOR-v1"))
        self.assertIs(h5.h5_precheck, c._H5_PRECHECK)
        self.assertIs(h5.build_probe_cfg, c._H5_BUILD_PROBE_CFG)
        self.assertIsInstance(h5.H5Executor.__dict__["h5"], property)
        self.assertNotIn("tier", h5.H5Executor.__dict__)  # the tier property is the subclass's only
        self.assertEqual(h5.H5Limits.from_config({}).max_open, 2)
