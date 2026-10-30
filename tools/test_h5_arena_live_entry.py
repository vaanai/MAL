"""Tests for the live entry of tools/h5_arena_executor.py (LIVE_BUILT True; QP-RULING-1023-wiring ruling 7 item 11). Every refusal path fails closed.
Fixtures and stubs only: no key material (a throwaway Keypair where a key object is needed), no network, nothing is sent, no host file is read."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest import mock

from solders.keypair import Keypair

from tools import h5_arena_executor as ax
from tools import h5_arena_gates as g
from tools import h5_executor as h
from tools.test_h5_arena_executor import A2, A2V2, Q50, ACase, write_canary_record
from tools.test_h5_arena_exec_wiring import LiveCase


class C0GuardTests(ACase):
    def test_head_passes(self):
        self.assertIsNone(ax.c0_guard_problem())
        self.assertTrue(ax.LIVE_BUILT)

    def test_dec032_c0_column_is_the_dec_text(self):
        self.assertEqual(ax.DEC032_C0, {"stake_lamports": 50_000_000, "max_open": 2, "max_trades_per_day": 30, "daily_loss_lamports": 100_000_000,
                                        "total_loss_lamports": 200_000_000})
        dec = (Path(__file__).resolve().parent.parent / "DEC" / "DEC-032-arena-canary-template.md").read_text()
        for row in ("| Stake per entry | S, at most **0.05 SOL** |", "| Max open | **2** |", "| Buy attempts per UTC day (guard reverts count) | **30** |",
                    "| Daily stop (realized loss in a UTC day; no new buys until 00:00Z) | **0.10 SOL** |", "| Total stop (see below) | **0.20 SOL** |"):
            self.assertIn(row, dec)

    def test_every_loosened_c0_field_refuses(self):
        for k, cap in ax.DEC032_C0.items():
            for bad in (cap + 1, 0, -1, True, float(cap), None):
                with self.subTest(k=k, bad=bad), mock.patch.dict(ax.C0_TIER, {k: bad}):
                    self.assertEqual(ax.c0_guard_problem(), f"c0_guard:{k}")

    def test_every_loosened_constant_refuses(self):
        cases = [(ax, "GAP_HOLD_MS", 59_999, "gap_hold"), (ax, "GAP_HOLD_FLOOR_MS", 59_999, "gap_hold"), (ax, "GAP_REDUNDANT_KIND", "silence", "gap_rule"), (ax, "SIGNAL_AGE_MAX_MS", 5_001, "signal_age"), (g, "PARITY_INTERVAL_S_MAX", 61, "parity_interval"),
                 (ax, "FUNDED_MAX_LAMPORTS", 1_000_000_001, "funded_range"), (ax, "FUNDED_MIN_LAMPORTS", 349_999_999, "funded_range"),
                 (ax, "FLOOR_BELOW_FUNDED_LAMPORTS", 250_000_001, "floor_below_funded"), (ax, "SELL_RESERVE_FLOOR_LAMPORTS", 1_599_999, "sell_reserve"),
                 (ax, "ARENA_LIVE_TIER", "T2", "tier"), (ax, "A3_MAX_AGE_MS", 36 * 3_600_000 + 1, "a3_max_age"), (ax, "ENTRY_GAP_BPS", 201.0, "entry_gap"),
                 (ax, "EXIT_GAP_BPS", 201.0, "exit_gap"), (ax, "LANDING_EXCESS_PP", 10.5, "landing"), (ax, "PAPER_FAIL_RATE", 0.1, "landing"),
                 (g, "HARD_ARENA_HALTS", g.HARD_ARENA_HALTS - {"feed_break"}, "hard_halts"), (ax, "ARENA_HALTS", ax.ARENA_HALTS - {ax.H_LANDING}, "arena_halts")]
        for mod, name, bad, why in cases:
            with self.subTest(name=name), mock.patch.object(mod, name, bad):
                self.assertEqual(ax.c0_guard_problem(), f"c0_guard:{why}")

    def test_a_guard_override_that_is_gone_refuses(self):
        with mock.patch.object(ax, "GUARD_METHODS", ax.GUARD_METHODS + ("no_such_method",)):
            self.assertEqual(ax.c0_guard_problem(), "c0_guard:overrides")
        for m in ax.GUARD_METHODS:
            self.assertIn(m, ax.ArenaExecutor.__dict__, m)


class LiveEntryRefusalTests(ACase):
    def cfg(self, **kw):
        c = {"book_id": A2, "mode": "live", "a3_record_file": "/x/a3.jsonl", "intents_file": "/x/feed"}
        c.update(kw)
        return {k: v for k, v in c.items() if v is not None}

    def lok(self, text: str, mode: int = 0o644) -> None:
        ax.ARENA_LIVE_OK_PATH.write_text(text)
        os.chmod(ax.ARENA_LIVE_OK_PATH, mode)

    def refusal(self, book=A2, merged=True, **kw):
        with mock.patch.object(ax, "FEED_NOTE_MERGED", merged):
            return ax.live_entry_refusal(ax.BOOKS[book], self.cfg(book_id=book, **kw))

    def test_all_conditions_met(self):
        self.lok(A2 + "\n")
        self.assertIsNone(self.refusal())

    def test_feed_note_unmerged_refuses_first(self):
        self.lok(A2 + "\n")
        self.assertEqual(self.refusal(merged=False), "live_feed_note_unmerged")
        self.assertTrue(ax.FEED_NOTE_MERGED)  # flipped after the EXP-024 Note under Amendment 10 (#851)

    def test_config_refusals(self):
        self.lok(A2 + "\n")
        for kw, why in (({"replay_only": True}, "live_replay_only_set"), ({"replay_only": False}, "live_replay_only_set"),
                        ({"mode": "dryrun"}, "live_config_mode_not_live"), ({"mode": None}, "live_config_mode_not_live"),
                        ({"a3_record_file": None}, "a3_record_file_missing"), ({"a3_record_file": ""}, "a3_record_file_missing"),
                        ({"intents_file": None}, "intents_file_missing")):
            with self.subTest(kw=kw):
                self.assertEqual(self.refusal(**kw), why)

    def test_arena_live_ok_refusals(self):
        self.assertEqual(self.refusal(), "arena_live_ok_missing")
        for text, mode, why in ((A2 + "\n", 0o666, "arena_live_ok_unsafe"), (A2 + "\n", 0o600, "arena_live_ok_unsafe"), ("", 0o644, "arena_live_ok_other_book"),
                                (Q50 + "\n", 0o644, "arena_live_ok_other_book"), (A2 + "\n" + Q50 + "\n", 0o644, "arena_live_ok_other_book"),
                                (A2 + "x\n", 0o644, "arena_live_ok_other_book")):
            with self.subTest(text=text, mode=oct(mode)):
                self.lok(text, mode)
                self.assertEqual(self.refusal(), why)

    def test_arena_live_ok_symlink_refuses(self):
        real = self.tmp / "elsewhere"
        real.write_text(A2 + "\n")
        os.chmod(real, 0o644)
        ax.ARENA_LIVE_OK_PATH.symlink_to(real)
        self.assertEqual(self.refusal(), "arena_live_ok_unsafe")

    def test_v2_book_checks_its_rule_source_live(self):
        self.lok(A2V2 + "\n")
        (self.tmp / "FREEZE.md").write_text("x")
        with mock.patch.object(ax, "V2_RULE_SHA256", "0" * 64):
            self.assertEqual(self.refusal(book=A2V2), "v2_rule_source_sha_mismatch")
        with mock.patch.object(ax, "V2_RULE_SHA256", self._sha(self.tmp / "FREEZE.md")):
            self.assertIsNone(self.refusal(book=A2V2))

    def test_c0_guard_refusal_comes_first(self):
        self.lok(A2 + "\n")
        with mock.patch.dict(ax.C0_TIER, {"stake_lamports": 50_000_001}):
            self.assertEqual(self.refusal(merged=False), "c0_guard:stake_lamports")

    def test_arena_start_refusal_runs_the_live_entry_checks(self):
        write_canary_record(self.tmp / "repo")
        self.lok(A2 + "\n")
        c = self.cfg(end_ms=1, funded_lamports=500_000_000)
        with mock.patch.object(ax, "FEED_NOTE_MERGED", False):
            self.assertEqual(ax.arena_start_refusal(ax.BOOKS[A2], c, self.tmp / "repo", live=True), "live_feed_note_unmerged")
        self.assertIsNone(ax.arena_start_refusal(ax.BOOKS[A2], {**c, "replay_only": True}, self.tmp / "repo", live=False))  # the dry path is unchanged


class PerBuyArenaLiveOkTests(LiveCase):
    def test_removing_the_arena_live_ok_stops_new_buys(self):
        e = self.live()
        ax.ARENA_LIVE_OK_PATH.unlink()
        n0 = len(e.rpc.sent)
        e.fire()
        self.assertEqual(len(e.rpc.sent), n0)
        self.assertEqual(e.ex._refusal_counts.get("arena_live_ok_missing"), 1)

    def test_an_arena_live_ok_naming_another_book_stops_new_buys(self):
        e = self.live()
        ax.ARENA_LIVE_OK_PATH.write_text(Q50 + "\n")
        n0 = len(e.rpc.sent)
        e.fire()
        self.assertEqual(len(e.rpc.sent), n0)
        self.assertEqual(e.ex._refusal_counts.get("arena_live_ok_other_book"), 1)

    def test_removing_the_arena_live_ok_after_the_tx_is_built_stops_the_send(self):
        """Reviewer #836 item 1: the socket-time gate (_live_gate) reads the arena LIVE_OK, not only the shared one."""
        e = self.live()
        real_send = ax.h.H5Executor._send

        def drop_then_send(ex, p, mint):
            if p.get("kind") == "buy" and p.get("sends", 0) == 0 and ax.ARENA_LIVE_OK_PATH.exists():
                ax.ARENA_LIVE_OK_PATH.unlink()  # the tx is built and signed; the operator removes the arena gate now
            return real_send(ex, p, mint)

        n0 = len(e.rpc.sent)
        with mock.patch.object(ax.h.H5Executor, "_send", drop_then_send):
            e.fire()
        self.assertEqual(len(e.rpc.sent), n0)
        self.assertFalse(ax.ARENA_LIVE_OK_PATH.exists())

    def test_live_gate_reads_both_files(self):
        e = self.live()
        self.assertIsNone(e.ex._live_gate())
        ax.ARENA_LIVE_OK_PATH.write_text(Q50 + "\n")
        self.assertEqual(e.ex._live_gate(), "arena_live_ok_other_book")
        ax.ARENA_LIVE_OK_PATH.write_text(A2 + "\n")
        ax.h.LIVE_OK_PATH.unlink()
        self.assertEqual(e.ex._live_gate(), "live_ok_missing")

    def test_with_the_file_in_place_the_buy_goes_out(self):
        e = self.live()
        n0 = len(e.rpc.sent)
        e.fire()
        self.assertEqual(len(e.rpc.sent), n0 + 1)


class StubExecutor:
    """Stands in for ArenaExecutor in main(): records what it was built with; never touches a network or a key."""
    built: list = []
    raise_with: str | None = None

    def __init__(self, rpc, cfg, keypair, **kw):
        if StubExecutor.raise_with:
            raise SystemExit(StubExecutor.raise_with)
        StubExecutor.built.append((rpc, cfg, keypair, kw))
        self.run_mode = h.LIVE if keypair is not None else h.DRYRUN
        self.user, self.paths, self.h5 = "pub", {}, "lim"
        self.ticks = 0
        self._start_ms = None

    def prime_feed_start(self):
        return "feed_start_record_missing"

    def tick(self):
        self.ticks += 1
        return 0

    def run_loop(self):
        raise AssertionError("run_loop is never reached in these tests (--once)")


class MainTests(ACase):
    def setUp(self):
        super().setUp()
        StubExecutor.built, StubExecutor.raise_with = [], None
        self.calls: list[str] = []
        self.kp = Keypair()
        write_canary_record(self.tmp / "repo")

    def write_cfg(self, **kw) -> str:
        c = {"book_id": A2, "mode": "live", "state_dir": str(self.tmp / "state"), "intents_file": str(self.tmp / "feed"), "a3_record_file": str(self.tmp / "a3.jsonl"),
             "end_ms": ax.SEAL_END_MS, "funded_lamports": 500_000_000, "exit_gap_file": str(self.tmp / "state" / "arena-parity" / "exit-gaps.jsonl"), "parity_twins_file": str(self.tmp / "tw.jsonl"),
             "tieout_dir": str(self.tmp / "tieout")}
        c.update(kw)
        c = {k: v for k, v in c.items() if v is not None}
        Path(c["state_dir"]).mkdir(parents=True, exist_ok=True)
        p = self.tmp / "cfg.json"
        p.write_text(json.dumps(c))
        return str(p)

    def files_ok(self) -> None:
        """Every Helm-owned file a live start reads, as the stand-ins of the test's patched paths."""
        h.LIVE_OK_PATH.write_text("")
        os.chmod(h.LIVE_OK_PATH, 0o644)
        ax.ARENA_LIVE_OK_PATH.write_text(A2 + "\n")
        os.chmod(ax.ARENA_LIVE_OK_PATH, 0o644)

    def run_main(self, argv, merged=True, env=None, rpc_env_rc=0, start_refusal=None):
        def key():
            self.calls.append("key")
            return self.kp

        patches = [mock.patch.object(ax, "FEED_NOTE_MERGED", merged), mock.patch.object(ax, "ArenaExecutor", StubExecutor),
                   mock.patch.object(h, "repo_root", lambda: self.tmp / "repo"), mock.patch.object(h, "exp024_part1_present", lambda root: True),
                   mock.patch.object(h.pl, "load_probe_key", key), mock.patch.object(h.pl, "harden_process", lambda: self.calls.append("harden")),
                   mock.patch.object(h.pe, "startup_rpc_env_check", lambda live: (self.calls.append("rpc_env"), rpc_env_rc)[1]),
                   mock.patch.dict(os.environ, env if env is not None else {"HELIUS_API_KEY": "k"}, clear=False)]
        if env is not None and "HELIUS_API_KEY" not in env:
            patches.append(mock.patch.dict(os.environ, {"HELIUS_API_KEY": ""}))
        if start_refusal is not None:
            patches.append(mock.patch.object(h, "start_refusal", lambda cfg, root=None: start_refusal))
        for p in patches:
            p.start()
        try:
            return ax.main(argv, rpc_factory=lambda url, env_file, use_env_file, rps: (self.calls.append(f"rpc:{url}:{use_env_file}"), "RPC")[1])
        finally:
            for p in reversed(patches):
                p.stop()

    def test_full_live_start_reads_the_key_last_and_builds_the_keyed_executor(self):
        self.files_ok()
        rc = self.run_main(["--config", self.write_cfg(), "--live", "--once"])
        self.assertEqual(rc, 0)
        self.assertEqual(self.calls, ["rpc_env", "harden", "key", "rpc:None:False"])
        (rpc, cfg, kp, kw), = StubExecutor.built
        self.assertIs(kp, self.kp)
        self.assertEqual((rpc, cfg["book_id"]), ("RPC", A2))

    def assert_refused_without_key(self, rc, out, why):
        self.assertEqual(rc, 2)
        self.assertIn(f"startup_refused {why}", out)
        self.assertNotIn("key", self.calls)
        self.assertEqual(StubExecutor.built, [])

    def capture(self, *a, **kw):
        with mock.patch("sys.stdout") as so:
            rc = self.run_main(*a, **kw)
        return rc, "".join(c.args[0] for c in so.write.call_args_list)

    def test_shared_live_ok_missing_refuses_before_the_key(self):
        ax.ARENA_LIVE_OK_PATH.write_text(A2 + "\n")
        os.chmod(ax.ARENA_LIVE_OK_PATH, 0o644)
        self.assert_refused_without_key(*self.capture(["--config", self.write_cfg(), "--live"]), "live_ok_missing")

    def test_each_live_entry_refusal_comes_before_the_key(self):
        cases = [({}, {"merged": False}, "live_feed_note_unmerged"), ({"replay_only": True}, {}, "live_replay_only_set"),
                 ({"a3_record_file": None}, {}, "a3_record_file_missing"), ({"funded_lamports": None}, {}, "funded_lamports_missing"),
                 ({"exit_gap_file": None}, {}, "exit_gap_file_missing"), ({"end_ms": None}, {}, "end_ms_missing"),
                 ({"stop_file": "/x/STOP"}, {}, "config_path_override:stop_file")]
        for cfg_kw, run_kw, why in cases:
            with self.subTest(why=why):
                self.calls, StubExecutor.built = [], []
                self.files_ok()
                self.assert_refused_without_key(*self.capture(["--config", self.write_cfg(**cfg_kw), "--live"], **run_kw), why)

    def test_arena_live_ok_and_tier_and_canary_line_refuse_before_the_key(self):
        self.files_ok()
        ax.ARENA_LIVE_OK_PATH.unlink()
        self.assert_refused_without_key(*self.capture(["--config", self.write_cfg(), "--live"]), "arena_live_ok_missing")
        self.files_ok()
        self.arena_tier.write_text("T0\n")
        self.calls = []
        self.assert_refused_without_key(*self.capture(["--config", self.write_cfg(), "--live"]), "arena_tier_not_t1:T0")
        self.arena_tier.write_text("T1\n")
        (self.tmp / "repo" / "DEC" / "DEC-033-h5-arena-canary-test.md").write_text(f"OWNER_CANARY_CONFIRMED: bot_id={Q50}\n")
        self.calls = []
        self.assert_refused_without_key(*self.capture(["--config", self.write_cfg(), "--live"]), "canary_record_for_bot_id_missing")

    def test_rpc_env_check_failure_stops_before_the_key(self):
        self.files_ok()
        rc, _ = self.capture(["--config", self.write_cfg(), "--live"], rpc_env_rc=2)
        self.assertEqual(rc, 2)
        self.assertEqual(self.calls, ["rpc_env"])

    def test_missing_rpc_key_and_key_path_refuse_before_the_key(self):
        self.files_ok()
        self.assert_refused_without_key(*self.capture(["--config", self.write_cfg(), "--live"], env={}), "rpc_key_missing")
        self.calls = []
        self.assert_refused_without_key(*self.capture(["--config", self.write_cfg(key_path="/x/k.json"), "--live"]), "key_path_set")

    def test_live_flag_with_a_dry_config_refuses(self):
        self.files_ok()
        self.assert_refused_without_key(*self.capture(["--config", self.write_cfg(mode="dryrun"), "--live"]), "live_flag_without_config_mode_live")

    def test_live_rejects_rpc_env(self):
        with self.assertRaises(SystemExit) as cm, mock.patch("sys.stderr"):
            ax.main(["--config", self.write_cfg(), "--live", "--rpc-env", "X"])
        self.assertEqual(cm.exception.code, 2)

    def test_constructor_refusal_is_exit_2_not_a_restart_loop(self):
        self.files_ok()
        StubExecutor.raise_with = "arena executor start refused: funded_balance_mismatch"
        rc, out = self.capture(["--config", self.write_cfg(), "--live", "--once"])
        self.assertEqual(rc, 2)
        self.assertIn("startup_refused funded_balance_mismatch", out)

    def test_unknown_book_and_bad_config(self):
        self.assert_refused_without_key(*self.capture(["--config", self.write_cfg(book_id="h5v1-boostfloor-a1"), "--live"]), "book_id_unknown")
        bad = self.tmp / "bad.json"
        bad.write_text("[1]")
        self.assert_refused_without_key(*self.capture(["--config", str(bad), "--live"]), "config_not_an_object")

    def test_dry_run_is_keyless_and_keeps_the_feed_note_rule(self):
        cfg = self.write_cfg(mode="dryrun")
        rc, out = self.capture(["--config", cfg, "--once"], merged=False)
        self.assertEqual(rc, 2)
        self.assertIn("startup_refused feed_note_unmerged_replay_only", out)
        rc, out = self.capture(["--config", self.write_cfg(mode="dryrun", replay_only=True), "--once"], merged=False)
        self.assertEqual(rc, 0)
        (_rpc, _cfg, kp, _kw), = StubExecutor.built
        self.assertIsNone(kp)
        self.assertNotIn("key", self.calls)
        self.assertNotIn("harden", self.calls)

    def test_status_is_keyless_and_names_the_first_refusal(self):
        self.files_ok()
        rc, out = self.capture(["--config", self.write_cfg(), "--status"], merged=False)
        self.assertEqual(rc, 0)
        self.assertIn("live_start=live_feed_note_unmerged", out)
        self.assertIn("arena_live_ok=valid", out)
        self.assertIn("c0_guard=ok", out)
        self.assertEqual(self.calls, [])
        rc, out = self.capture(["--config", self.write_cfg(), "--status"], merged=True)
        self.assertIn("live_start=none", out)
        self.assertEqual(StubExecutor.built, [])


class FeedStartTests(ACase):
    """The shadow writes its start record once; the base tailer starts at the end of the newest hour. prime_feed_start finds the newest start record."""

    def start_rec(self, **kw):
        r = {"type": "start", "code_sha": ax.A2_SHA, "fresh_forward": "ARENA", "dirty": False, "tip_union": "off", "rule": ax.A2_RULE, "start_ms": 5}
        r.update(kw)
        return r

    def feed(self, files: dict) -> Path:
        d = self.tmp / "feed"
        d.mkdir(exist_ok=True)
        for name, rows in files.items():
            (d / name).write_text("".join((r if isinstance(r, str) else json.dumps(r)) + "\n" for r in rows))
        return d

    def test_newest_start_record_across_hours(self):
        d = self.feed({"h5-shadow-2026-10-22T00.jsonl": [self.start_rec(start_ms=1), {"type": "trigger", "note": "start"}],
                       "h5-shadow-2026-10-22T01.jsonl": ["not json \"start\"", {"type": "gap"}],
                       "h5-shadow-2026-10-23T05.jsonl": [self.start_rec(start_ms=2)], "h5-shadow-status.json": [self.start_rec(start_ms=99)]})
        self.assertEqual(ax.latest_start_record(d)["start_ms"], 2)
        self.assertIsNone(ax.latest_start_record(self.feed({"h5-shadow-2026-10-24T00.jsonl": [{"type": "trigger"}]}).parent / "nope"))

    def env_on(self, d: Path):
        e = self.env(verified=False, intents_file=str(d))
        return e

    def test_prime_verifies_the_books_start_record(self):
        e = self.env_on(self.feed({"h5-shadow-2026-10-22T00.jsonl": [self.start_rec()]}))
        self.assertFalse(e.ex.feed_verified)
        self.assertIsNone(e.ex.prime_feed_start())
        self.assertTrue(e.ex.feed_verified)
        self.assertEqual(e.ex._start_ms, 5)

    def test_prime_with_another_books_start_record_latches_feed_break(self):
        e = self.env_on(self.feed({"h5-shadow-2026-10-22T00.jsonl": [self.start_rec(code_sha=ax.Q50_SHA)]}))
        self.assertEqual(e.ex.prime_feed_start(), "feed_start_record_rejected")
        self.assertFalse(e.ex.feed_verified)
        self.assertIn(ax.H_FEED_BREAK, e.ex.counters.halts)

    def test_prime_without_a_start_record_keeps_buys_refused(self):
        e = self.env_on(self.feed({"h5-shadow-2026-10-22T05.jsonl": [{"type": "gap"}]}))
        self.assertEqual(e.ex.prime_feed_start(), "feed_start_record_missing")
        self.assertFalse(e.ex.feed_verified)
