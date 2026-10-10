"""DEC-026 tests for the C1-NF executor profile (items 9 and 23): the limits as code constants (stops fire, config only lowers, a restart cannot
reset them, TIER missing or invalid means T1), the exposure test at stake 0.05 / total stop 0.175 / 2 open, the profile's own files, synthetic pools
traded with no class on any record, the pick oracle failing closed from 2026-10-16T01Z, the landing p50 halt, and H5's module left as it was.
Offline: the H5 fake RPC and chain, throwaway keys, no network, no real key."""

from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
from unittest import mock

from tools import c1nf_executor as c
from tools import h5_executor as h5
from tools import probe_executor as pe
from tools import pumpswap_tx as tx
from tools.cap_pick_oracle import PickOracle
from solders.pubkey import Pubkey
from tools.test_c1nf_executor import MINUTE, STAKE, TEST_KP, TEST_SHA, Case, Env, H5Rpc, outcome, touch_streams, write_stream
from tools.test_h5_executor import MINT, POOL, QREAL, sell_args
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
            self.assertEqual(e.ex.counters.seal_skips, 0 if name == "pick" else 1, name)  # a pick: counted in this process only (#540)
            self.assertEqual(e.ex.seal_picks_in_process, 1 if name == "pick" else 0, name)
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


    def test_a_malformed_pick_on_a_sealed_mint_leaves_no_countable_trace(self):
        """Manager decision on #552 (quant-proof (d) on #540): a malformed C1-NF pick whose mint is a CAP-PICK pick, or not known to be a non-pick,
        is counted in this process only. No hourly row, no c1nf-extra.json or counters value, no log line, no alert, nothing in --status."""
        when = h5.ORACLE_EARLIEST_MS + 10 * MINUTE
        for name, oracle, final, sealed in (("pick", lambda m: True, True, True), ("undecided", lambda m: None, True, True), ("no_oracle", None, True, True),
                                            ("no_final", lambda m: False, False, True), ("not_a_pick", lambda m: False, True, False)):
            e = self.fresh(name, oracle=oracle)
            if final:
                Path(e.ex.final_marker).write_text("")
            touch_streams(e.shadow_dir)
            e.ex.intent_tick()
            bad = e.row(minute=when)
            bad["h_top1"] = 0.9  # bad_pick:h_top1_over_cap
            write_stream(e.shadow_dir, [bad] * 7)  # more than the five an hour that raise bad_intent_rate when they are counted
            n = len(e.ledger())
            e.ex.intent_tick()
            e.ex._hour_roll(e.clock() + 2 * 3_600_000)  # flush the hour's refusal_counts row
            new = json.dumps(e.ledger()[n:])
            if sealed:
                self.assertEqual(e.ex.seal_bad_picks_in_process, 7, name)
                self.assertEqual(e.ex.extra.counts, {}, name)
                self.assertEqual(e.ex.counters.seal_skips, 0, name)
                self.assertNotIn(MINT, new, name)
                self.assertNotIn("seal_bad_pick", new, name)  # no hourly row, no log line
                self.assertEqual([r for r in e.ledger() if r.get("kind") == "refusal_counts"], [], name)
                self.assertEqual([a["alert"] for a in e.ledger("alert") if a["alert"] not in ("config_clamps_tier", "seal_oracle_missing",
                                                                                              "seal_final_marker_missing")], [], name)
                self.assertEqual((e.ledger("skip"), e.ex.extra.picks), ([], {}), name)
                on_disk = e.ex.extra_path.read_text() + e.ex.counters_path.read_text()
                self.assertNotIn("seal_bad_pick", on_disk, name)
                self.assertNotIn("seal_bad_pick", c.status_report(e.conf), name)
            else:  # control: a mint the oracle says is not a pick still gets the ordinary bad_pick rows and alert
                self.assertEqual(e.ex.seal_bad_picks_in_process, 0, name)
                self.assertEqual([r["reason"] for r in e.ledger("skip")], ["bad_pick:h_top1_over_cap"] * 7, name)


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


class ReviewFixTests(Case):
    """#530 review: the model and wallet pins, the late-sell alert (no halt), the stale-checked seal oracle."""

    def test_a_pick_or_heartbeat_naming_another_model_is_a_halt(self):
        e = self.env()
        e.fire(model_sha="cd" * 32)
        self.assertEqual(e.refusals(), [c.MODEL_HALT])
        self.assertIn(c.MODEL_HALT, e.ex.counters.halts)
        self.assertFalse(e.ex.extra.picks[f"{MINT}:{e.t0}"]["monitored"])  # a stop, not a fill failure
        e.fire(minute=e.t0 + MINUTE)
        self.assertEqual(e.refusals()[-1], "halt_latched:" + c.MODEL_HALT)
        self.assertEqual(e.rpc.sent, [])
        e2 = self.fresh("hb")
        e2.ex._on_heartbeat({"type": "c1nf_heartbeat", "model_shas": [TEST_SHA]})
        self.assertEqual(e2.ex.counters.halts, {} if isinstance(e2.ex.counters.halts, dict) else set())
        e2.ex._on_heartbeat({"type": "c1nf_heartbeat", "model_shas": [TEST_SHA, "cd" * 32]})
        self.assertIn(c.MODEL_HALT, e2.ex.counters.halts)

    def test_with_no_pinned_model_live_refuses_and_a_dry_run_says_so(self):
        with mock.patch.object(c, "C1NF_MODEL_SHA256", frozenset()):
            with self.assertRaises(SystemExit):
                self.fresh("live")
            e = self.fresh("dry", live=False)
            self.assertFalse(e.ledger("c1nf_start")[0]["model_pinned"])
            e.fire(model_sha="cd" * 32)
            self.assertIn(MINT, e.ex.state.open)  # dry run: nothing to compare against, ledgered as unpinned
            self.assertEqual(c.start_refusal({"state_dir": str(c.LIVE_STATE_DIR), "end_ms": c.C1NF_END_MAX_MS, "intents_file": "/x"},
                                             root=e.root), "model_unpinned")

    def test_the_wallet_is_never_h5s_and_must_be_the_pinned_c1nf_wallet(self):
        pub = str(TEST_KP.pubkey())
        self.assertIsNone(c.wallet_refusal(pub))
        self.assertEqual(c.wallet_refusal(c.H5_WALLET_PUBKEY), "wallet_is_h5")
        self.assertEqual(c.wallet_refusal("11111111111111111111111111111111"), "wallet_not_pinned_c1nf")
        with mock.patch.object(c, "C1NF_WALLET_PUBKEY", None):
            self.assertEqual(c.wallet_refusal(pub), "wallet_not_pinned_c1nf")
            with self.assertRaises(SystemExit):
                self.fresh("unpinned")
            self.assertEqual(c.start_refusal({"state_dir": str(c.LIVE_STATE_DIR), "end_ms": c.C1NF_END_MAX_MS, "intents_file": "/x"},
                                             root=self.tmp), "exp025_part1_missing")
        with mock.patch.object(c, "H5_WALLET_PUBKEY", pub), self.assertRaises(SystemExit) as cm:
            self.fresh("h5")
        self.assertIn("wallet_is_h5", str(cm.exception))
        self.assertEqual(c.H5_WALLET_PUBKEY, "5n95HyhZqjZNkjdp44QGJoAqk4ZFjDgMKuUzWcQqSugk")

    def test_late_sells_alert_past_10pct_of_the_last_20_and_never_halt(self):
        e = self.env()
        lim = c.c1nf_limits({})
        plan = c.c1nf_exit_plan(10_000, 0.2, lim).public()
        self.assertEqual(plan["late_slot"], 10_000 + 1525)  # landing + 305 s
        def land(late: bool) -> None:
            e.ex._note_sell_landing(MINT, dict(plan), plan["late_slot"] + (1 if late else 0), False)
        for _ in range(9):
            land(False)
        land(True)  # 1 of 10: not above 10%
        self.assertEqual([a for a in e.ledger("alert") if a["alert"] == "exit_late_share_gt_10pct"], [])
        land(True)  # 2 of 11
        land(True)  # still over: no second alert
        alerts = [a for a in e.ledger("alert") if a["alert"] == "exit_late_share_gt_10pct"]
        self.assertEqual(len(alerts), 1)
        self.assertFalse(e.ex.counters.halts)  # DEC-026 section 7 rule 5: report and alert, no halt
        for _ in range(20):
            land(False)
        self.assertFalse(e.ex.extra.late_alert_on)
        land(True), land(True), land(True)
        self.assertEqual(len([a for a in e.ledger("alert") if a["alert"] == "exit_late_share_gt_10pct"]), 2)  # a new crossing alerts again
        self.assertFalse(e.ex.counters.halts)
        e.fire()
        self.assertEqual(e.refusals(), [])  # buys go on

    def test_the_seal_oracle_is_h5s_three_state_pick_oracle_and_fails_closed_when_its_heartbeat_is_older_than_60_s(self):
        now = [h5.ORACLE_EARLIEST_MS + 120_000]
        f = self.tmp / "cap-picks.jsonl"
        f.write_text('{"mint":"%s","pick":false,"t_ms":1}\n{"mint":"So11111111111111111111111111111111111111112","pick":true,"t_ms":2}\n{"hb":true,"t_ms":%d}\n'
                     % (MINT, now[0] - 10_000))
        o = c.build_pick_oracle({"pick_file": str(f)}, now_ms=lambda: now[0])
        self.assertIsInstance(o, PickOracle)
        self.assertIs(type(o), type(h5.build_pick_oracle({"pick_file": str(f)})))  # the very reader H5 uses
        self.assertIs(o(MINT), False)  # fresh heartbeat, decided, not a pick: the only answer that lets a buy go ahead
        self.assertIsNone(o("11111111111111111111111111111111"))  # undecided: None, never an exception, never False
        now[0] += 51_000  # 61 s after the heartbeat
        self.assertIsNone(o(MINT))  # stale: a False cannot be trusted
        self.assertIs(o("So11111111111111111111111111111111111111112"), True)  # a pick never flips; True refuses the buy anyway
        with f.open("a") as fh:
            fh.write('{"hb":true,"t_ms":%d}\n' % (now[0] + 6_000))  # more than 5 s in the future: a clock fault
        self.assertIsNone(o(MINT))
        g = self.tmp / "no-hb.jsonl"
        g.write_text('{"mint":"%s","pick":false,"t_ms":1}\n' % MINT)
        og = c.build_pick_oracle({"pick_file": str(g)}, now_ms=lambda: now[0])
        self.assertIsNone(og(MINT))  # no heartbeat at all
        self.assertIsNone(c.build_pick_oracle({"pick_file": str(self.tmp / "missing.jsonl")}, now_ms=lambda: now[0])(MINT))  # a missing file
        # through the executor's seal: False passes, everything else is seal_oracle_error (a count only), a pick is seal_pick
        e = self.env(oracle=og)
        Path(e.ex.final_marker).parent.mkdir(parents=True, exist_ok=True)
        Path(e.ex.final_marker).write_text("")
        e.set_clock(now[0])
        self.assertEqual(e.ex._seal_reason(c._MintOnly(MINT), now[0]), "seal_oracle_error")
        h = self.tmp / "fresh.jsonl"
        h.write_text('{"mint":"%s","pick":false,"t_ms":1}\n{"hb":true,"t_ms":%d}\n' % (MINT, now[0] - 1_000))
        e.ex.pick_oracle = c.build_pick_oracle({"pick_file": str(h)}, now_ms=lambda: now[0])
        self.assertIsNone(e.ex._seal_reason(c._MintOnly(MINT), now[0]))
        i = self.tmp / "pick.jsonl"
        i.write_text('{"mint":"%s","pick":true,"t_ms":1}\n{"hb":true,"t_ms":%d}\n' % (MINT, now[0] - 1_000))
        e.ex.pick_oracle = c.build_pick_oracle({"pick_file": str(i)}, now_ms=lambda: now[0])
        self.assertEqual(e.ex._seal_reason(c._MintOnly(MINT), now[0]), "seal_pick")

    def test_the_stale_limit_is_60_s_whatever_the_config_or_h5_says(self):
        f = self.tmp / "p.jsonl"
        f.write_text('{"hb":true,"t_ms":1}\n')
        self.assertEqual(c.build_pick_oracle({"pick_file": str(f)}).stale_s, 60.0)
        self.assertEqual(c.build_pick_oracle({"pick_file": str(f), "pick_stale_s": 600, "stale_s": 600}).stale_s, 60.0)  # no config key moves it
        with mock.patch.object(h5, "PICK_STALE_S", 600.0):  # and a looser H5 constant never loosens C1-NF's own ceiling
            self.assertEqual(c.build_pick_oracle({"pick_file": str(f)}).stale_s, c.SEAL_ORACLE_STALE_S)
        self.assertEqual(c.SEAL_ORACLE_STALE_S, 60.0)

    def test_no_pick_file_is_no_oracle_and_a_malformed_one_is_a_value_error(self):
        self.assertIsNone(c.build_pick_oracle({}))
        self.assertIsNone(c.build_pick_oracle({"pick_file": ""}))
        with self.assertRaises(ValueError):
            c.build_pick_oracle({"pick_file": 5})
        with self.assertRaises(ValueError):
            c.build_pick_oracle({"pick_file": str(self.tmp / "p.jsonl"), "pick_replay_files": "not-a-list"})
        r = self.tmp / "replay.jsonl"
        r.write_text('{"mint":"%s","pick":true,"t_ms":1}\n' % MINT)
        o = c.build_pick_oracle({"pick_file": str(self.tmp / "p.jsonl"), "pick_replay_files": [str(r)]})
        self.assertIs(o(MINT), True)  # H5's builder, so H5's replay union too (a known True answers True even with no live file)

    def test_the_shipped_live_config_builds_the_oracle_from_its_pick_file(self):
        """Imports tools.c1nf_executor (the break on main after #540: JsonlPickOracle was gone) and builds its oracle from the live config."""
        root = Path(c.__file__).resolve().parents[1] / "scripts" / "mal-fast"
        live, dry = json.loads((root / "c1nf-executor-live.json").read_text()), json.loads((root / "c1nf-executor.json").read_text())
        self.assertEqual(live["pick_file"], "/srv/mal-cap-pick/picks.jsonl")
        o = c.build_pick_oracle(live)
        self.assertIsInstance(o, PickOracle)
        self.assertEqual([(str(s.tail.path), s.live) for s in o._src], [("/srv/mal-cap-pick/picks.jsonl", True)])
        self.assertEqual(o.stale_s, 60.0)
        self.assertIsNone(c.build_pick_oracle(dry))  # the keyless dry run has no pick_file (its round trips are before the seal window)
        # the same config pointed at a real file: the executor takes it and reports the reader it uses at start
        f = self.tmp / "picks.jsonl"
        f.write_text('{"hb":true,"t_ms":%d}\n' % h5.ORACLE_EARLIEST_MS)
        o2 = c.build_pick_oracle({**live, "pick_file": str(f)}, now_ms=lambda: h5.ORACLE_EARLIEST_MS + 1_000)
        e = self.fresh("cfg", live=False, oracle=o2)
        start = [r for r in e.ledger() if r.get("kind") == "c1nf_start"]
        self.assertEqual(start[-1]["seal_oracle"], "PickOracle")

    def test_main_refuses_a_malformed_pick_config_like_h5(self):
        e = self.env(live=False)
        cfg_path = self.tmp / "cfg.json"
        cfg_path.write_text(json.dumps({**e.conf, "pick_file": str(self.tmp / "p.jsonl"), "pick_replay_files": "not-a-list"}))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(c.main(["--config", str(cfg_path), "--once"]), 2)
        self.assertIn("startup_refused pick_oracle_config", out.getvalue())

    def test_a_refusal_the_seal_would_now_refuse_writes_no_pick_status_row(self):
        """#540 made H5's _refuse ask the seal again before it writes a per-mint row. If the answer moved since handle_pick's first ask (the feed went
        stale, or the gate's pick row arrived), the refusal is counted as a seal reason and C1-NF must not add a pick_status row for it either."""
        when = h5.ORACLE_EARLIEST_MS + 10 * MINUTE
        for name, later, want_skips, want_picks in (("same", False, 0, 0), ("stale", None, 1, 0), ("pick", True, 0, 1)):
            answer = {"v": False}
            e = self.fresh(name, oracle=lambda m, a=answer: a["v"])
            Path(e.ex.final_marker).write_text("")

            def flip(pick, now, a=answer, later=later):
                a["v"] = later  # handle_pick asked once and got False; the answer moves before the refusal is written
                return "stale_pick"
            with mock.patch.object(c.C1NFExecutor, "_pick_refusal", side_effect=flip):
                e.fire(minute=when)
            self.assertEqual((e.ex.counters.seal_skips, e.ex.seal_picks_in_process), (want_skips, want_picks), name)
            if later is False:  # the answer did not move: the ordinary refusal row and the monitor's unfilled pick, as before
                self.assertEqual(e.refusals(), ["stale_pick"], name)
                self.assertEqual(e.ex.extra.picks[f"{MINT}:{when}"]["status"], "unfilled", name)
            else:
                self.assertEqual((e.refusals(), e.ledger("pick_status"), e.ex.extra.picks), ([], [], {}), name)


OTHER_MINT = "So11111111111111111111111111111111111111112"
OTHER_POOL = str(tx.canonical_pool(Pubkey.from_string(OTHER_MINT)))


class Round3FixTests(TierCase):
    """#530 review round 2 (head 8ea24e6): the stuck line at landing + 600 s, the seal-provisioning start alerts, the outcomes offset after
    processing, the c1nf-extra anti-reset guard, no exit plan on slot 0, the per-exit balance reserve."""

    # -- MEDIUM 1: DEC-026 section 7 rule 4, stuck_position at landing + 600 s (not at the 370 s emergency sell) ---------------------------
    def to_emergency(self, e: Env) -> dict:
        """An open position whose sells never get a status, walked to the emergency sell at landing + 370 s."""
        e.open_position()
        plan = e.plan()
        e.clock.t = plan["send_wall_ms"]
        e.ex.exit_tick(e.clock())
        e.step_until_sent(2)
        e.clock.t = plan["s0_wall_ms"] + 370_000
        e.ex.exit_tick(e.clock())
        self.assertEqual((sell_args(e.sent()[-1])[1], e.sent()[-1]["priority"]), (h5.EMERGENCY_MIN_OUT, 1_010_000))
        return plan

    def test_stuck_position_latches_at_landing_plus_600_s_and_not_at_the_370_s_emergency_sell(self):
        e = self.env()
        plan = self.to_emergency(e)
        self.assertTrue(e.ex.state.open[MINT]["emergency_attempted"])  # H5's deadline path ran: the emergency sell went out
        self.assertEqual(e.ex.counters.halts, {})
        self.assertEqual(e.ledger("emergency_deadline_passed")[0]["mint"], MINT)
        e.clock.t = plan["s0_wall_ms"] + 599_000
        e.ex.exit_tick(e.clock())
        self.assertEqual(e.ex.counters.halts, {})
        e.clock.t = plan["s0_wall_ms"] + 600_000
        e.ex.exit_tick(e.clock())
        self.assertEqual(set(e.ex.counters.halts), {c.STUCK_HALT})
        halt = e.ex.counters.halts[c.STUCK_HALT]
        self.assertEqual((halt["position_mint"], halt["why"], halt["landing_slot"]), (MINT, "not_closed_by_landing_600s", plan["s0_slot"]))
        self.assertTrue(halt["sell_pending"])
        self.assertEqual([a["alert"] for a in e.ledger("alert") if a["alert"] == "halt_stuck_position"], ["halt_stuck_position"])
        e.clock.t += 60_000
        e.ex.exit_tick(e.clock())
        self.assertEqual(len(e.ledger("halt_latched")), 1)  # once per position
        e.fire(minute=e.t0 + 20 * MINUTE, mint=OTHER_MINT, pool=OTHER_POOL)
        self.assertEqual(e.refusals()[-1], "halt_latched:" + c.STUCK_HALT)  # new buys stop until --clear-halt

    def test_a_sell_landing_between_370_and_600_s_never_latches(self):
        e = self.env()
        plan = self.to_emergency(e)
        e.clock.t = plan["s0_wall_ms"] + 450_000
        e.land_sell(e.rpc.slot)
        self.assertNotIn(MINT, e.ex.state.open)
        for dt in (600_000, 700_000):
            e.clock.t = plan["s0_wall_ms"] + dt
            e.ex.exit_tick(e.clock())
        self.assertEqual(e.ex.counters.halts, {})

    def test_the_600_s_line_survives_a_restart_after_the_emergency_sell(self):
        e = self.env()
        plan = self.to_emergency(e)
        e.ex = c.C1NFExecutor(e.rpc, e.conf, e.kp, now_ms=e.clock, root=e.root)  # a restart: H5's emergency_attempted is in the saved position
        e.seed()
        self.assertTrue(e.ex.state.open[MINT]["emergency_attempted"])
        e.clock.t = plan["s0_wall_ms"] + 600_000
        e.ex.exit_tick(e.clock())
        self.assertIn(c.STUCK_HALT, e.ex.counters.halts)

    def test_halt_freezes_the_stuck_check_like_h5s_exit_tick(self):
        e = self.env()
        plan = self.to_emergency(e)
        (self.state_dir / "HALT").write_text("")
        e.clock.t = plan["s0_wall_ms"] + 600_000
        e.ex.exit_tick(e.clock())
        self.assertEqual(e.ex.counters.halts, {})
        (self.state_dir / "HALT").unlink()
        e.ex.exit_tick(e.clock())
        self.assertIn(c.STUCK_HALT, e.ex.counters.halts)

    def test_stuck_due_is_landing_plus_600_s_on_h5s_wall_bounded_rule(self):
        lim = c.c1nf_limits({})
        w0 = 1_000_000
        plan = c.c1nf_exit_plan(10_000, 0.2, lim, w0).public()
        self.assertFalse(c.stuck_due(plan, None, w0 + 599_999))
        self.assertTrue(c.stuck_due(plan, None, w0 + 600_000))  # the wall time binds even with no slot clock
        stuck_slot = 10_000 + 3_000  # 600 s / 0.2 s
        self.assertTrue(c.stuck_due(plan, stuck_slot, w0 + 600_000 - h5.EARLY_TOLERANCE_MS))
        self.assertFalse(c.stuck_due(plan, stuck_slot, w0 + 600_000 - h5.EARLY_TOLERANCE_MS - 1))  # a slot never fires it early
        far = 10**15
        stand_in = {"s0_slot": 0, "sps": 0.4, "deadline_slot": far, "deadline_wall_ms": far, "s0_wall_ms": None}
        self.assertFalse(c.stuck_due(stand_in, 10**9, 10**13))  # H5's position_without_plan: no landing to count from
        self.assertEqual(c.STUCK_S - c.DEADLINE_S, 230.0)

    # -- MEDIUM 3: the seal window, provisioned or not, is said at start ------------------------------------------------------------------
    def test_start_alerts_when_the_seal_window_is_not_provisioned(self):
        e = self.env()
        a = [x for x in e.ledger("alert") if x["alert"] == "seal_oracle_missing"]
        self.assertEqual(len(a), 1)
        self.assertEqual((a[0]["seal_start_ms"], a[0]["end_ms"]), (h5.SEAL_START_MS, c.C1NF_END_MAX_MS))
        start = e.ledger("c1nf_start")[0]
        self.assertEqual((start["seal_oracle"], start["final_marker_present"], start["seal_start_ms"]), (None, False, h5.SEAL_START_MS))
        o = self.fresh("oracle", oracle=lambda mint: False)
        self.assertEqual([x for x in o.ledger("alert") if x["alert"].startswith("seal_")], [])
        short = self.fresh("short", live=False, end_ms=h5.SEAL_START_MS)
        self.assertEqual([x for x in short.ledger("alert") if x["alert"].startswith("seal_")], [])  # a run that ends before the seal needs neither
        late = h5.ORACLE_EARLIEST_MS + 60_000
        m = self.tmp / "marker" / "FINAL_WRITTEN"
        nm = self.fresh("late", live=False, t0=late, final_marker_file=str(m))
        self.assertEqual(sorted(x["alert"] for x in nm.ledger("alert") if x["alert"].startswith("seal_")),
                         ["seal_final_marker_missing", "seal_oracle_missing"])
        m.parent.mkdir()
        m.write_text("")
        ok = self.fresh("late2", live=False, t0=late, final_marker_file=str(m), oracle=lambda mint: False)
        self.assertEqual([x for x in ok.ledger("alert") if x["alert"].startswith("seal_")], [])
        live_cfg = json.loads((Path(c.__file__).resolve().parents[1] / "scripts" / "mal-fast" / "c1nf-executor-live.json").read_text())
        self.assertGreater(live_cfg["end_ms"], h5.SEAL_START_MS)  # the shipped live config reaches into the seal window, so it needs the oracle:
        self.assertEqual(live_cfg["pick_file"], "/srv/mal-cap-pick/picks.jsonl")  # DEC-026 Amendment 1 item B (the read-only bind in the unit)

    # -- LOW 4: the outcomes offset is persisted after the rows are processed; c1nf-extra.json has an anti-reset guard ------------------------
    def test_a_crash_while_processing_outcomes_loses_none(self):
        e = self.env(live=False)
        touch_streams(e.shadow_dir)
        e.ex.intent_tick()
        write_stream(e.shadow_dir, [e.row()])
        e.ex.intent_tick()
        write_stream(e.shadow_dir, [outcome(MINT, T0, 4.0)])
        with mock.patch.object(e.ex, "on_outcome", side_effect=RuntimeError("crash")), self.assertRaises(RuntimeError):
            e.ex.intent_tick()
        e2 = Env(self.tmp, self.state_dir, live=False, intents_file=str(e.shadow_dir))
        e2.ex.intent_tick()
        self.assertEqual(e2.ex.extra.picks[f"{MINT}:{T0}"]["outcome_pct"], 4.0)
        self.assertEqual((e2.ex.extra.oseq, e2.ex.extra.outcomes_unmatched), (1, 0))
        e2.ex._out_tail, e2.ex._out_path = c._Tail(started=True, offset=0, inode=None), None  # the same rows read again (a crash after on_outcome)
        e2.ex._outcomes_tick()
        self.assertEqual((e2.ex.extra.oseq, e2.ex.extra.outcomes_unmatched), (1, 1))  # a repeat: counted, never evidence twice

    def test_deleting_c1nf_extra_after_a_run_refuses_live_and_alerts_a_dry_run(self):
        e = self.env()
        e.fire()  # a buy in flight: its durable plan is in the counters
        self.assertTrue(e.ex.counters.plans)
        e.ex.extra_path.unlink()
        with self.assertRaises(SystemExit) as cm:
            self.env()
        self.assertIn("c1nf_extra_missing", str(cm.exception))
        self.assertFalse(e.ex.extra_path.exists())  # refused before any file is touched
        d = self.fresh("dry", live=False)
        touch_streams(d.shadow_dir)
        d.ex.intent_tick()  # a picks stream followed: tail_path is in the counters
        d.ex.extra_path.unlink()
        d2 = Env(d.tmp, d.ex.counters_path.parents[1], live=False)
        self.assertEqual([a["why"] for a in d2.ledger("alert") if a["alert"] == "c1nf_extra_reset"], ["c1nf_extra_missing"])
        f = self.fresh("unused")
        f.ex.extra_path.unlink()  # nothing ran yet: nothing to guard
        f2 = Env(f.tmp, f.ex.counters_path.parents[1])
        self.assertEqual([a for a in f2.ledger("alert") if a["alert"] == "c1nf_extra_reset"], [])

    # -- LOW 5: no slot estimate, no buy (never an exit plan anchored on slot 0) -----------------------------------------------------------
    def test_no_slot_estimate_at_the_send_refuses_instead_of_anchoring_on_slot_0(self):
        e = self.env()
        pick = e.pick()
        with mock.patch.object(e.ex, "_pick_refusal", return_value=None), mock.patch.object(e.ex.slots, "est", return_value=None):
            e.ex.handle_pick(pick)
        self.assertEqual(e.refusals(), ["stale_pick"])
        self.assertEqual((e.rpc.sent, e.ex.state.pending, e.ex.counters.plans), ([], {}, {}))

    # -- LOW 6: the balance guard reserves one escalated send per exit ---------------------------------------------------------------------
    def test_the_per_exit_reserve_covers_one_escalated_send(self):
        lim = c.c1nf_limits({"stake_lamports": STAKE})
        self.assertEqual(c.c1nf_exit_reserve(lim), 1_015_000)
        self.assertEqual(c.c1nf_exit_reserve(h5.H5Limits.from_config({})), h5.SELL_RESERVE_LAMPORTS)  # H5's own numbers keep H5's reserve
        first = STAKE + 505_000 + 5_000 + h5.RENT_RESERVE_LAMPORTS + 1_015_000 + 50_000_000
        self.assertEqual(first, 103_625_000)  # DEC-026 section 6: "about 0.104 SOL"
        for name, wallet, n_open, taken in (("a", first, 0, True), ("b", first - 1, 0, False),
                                            ("c", first + 1_015_000, 1, True), ("d", first + 1_014_999, 1, False)):
            e = self.fresh(name)
            e.rpc.balance = SOL // 2  # the tier starts on a 0.5 SOL wallet (the total stop's baseline), then the balance drops
            e.ex._bal = None
            e.ex._refresh_tier(e.clock())
            for i in range(n_open):
                e.ex.state.open[f"Other{i}"] = {"mint": f"Other{i}", "spend": STAKE}
            e.rpc.balance = wallet
            e.ex._bal = None
            e.fire()
            self.assertEqual(MINT in e.ex.state.pending, taken, name)
            if not taken:
                self.assertEqual(e.refusals(), ["balance_floor"], name)

    # -- H5 stays H5 -------------------------------------------------------------------------------------------------------------------------
    def test_h5s_methods_and_reserve_are_its_own(self):
        e = self.env()
        self.to_emergency(e)  # the deferred latch ran through the subclass
        for name in ("_latch", "exit_tick", "_balance_refusal", "_due"):
            self.assertIn(name, h5.H5Executor.__dict__)
            self.assertEqual(getattr(h5.H5Executor, name).__module__, "tools.h5_executor", name)
        for name in ("_latch", "exit_tick", "_balance_refusal"):  # overridden in the subclass only
            self.assertEqual(c.C1NFExecutor.__dict__[name].__module__, "tools.c1nf_executor", name)
        self.assertEqual((h5.SELL_RESERVE_LAMPORTS, h5.H5_DEFAULT["escalated_priority_lamports"]), (1_000_000, 150_000))
        self.assertEqual(h5.balance_need(STAKE, 505_000, 0, 0, 50_000_000), 103_610_000)  # H5's function, unchanged
