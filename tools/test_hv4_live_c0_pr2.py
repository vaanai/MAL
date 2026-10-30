"""Offline tests for the PR 2 pieces of the HV4 live adapter (tools/hv4_live_c0.py + tools/arena_live_bridge.py): the frozen_sha pin, the universe check,
the sell builder pin and the 55k / 55k / 150k sell priority schedule. Synthetic fixtures only: no HV4 ledger, decision row, bots.json or job log is
read, no network, no key (the live-port tests use a throwaway Keypair and the probe tests' fake RPC)."""

from __future__ import annotations

import base64
import unittest
from unittest import mock
from pathlib import Path

from tools import arena_live_bridge as alb
from tools import hv4_live_c0 as c0
from tools.test_arena_live_bridge import BOT, H, SHA, Env, TmpCase
from tools.test_hv4_live_c0 import ARM, FUNDED, Hv4Env


def decode_tx(b64: str):
    from solders.transaction import VersionedTransaction
    return VersionedTransaction.from_bytes(base64.b64decode(b64)).message


def priority_lamports(msg) -> int:
    """Total priority lamports of a message: compute unit price (micro-lamports) x limit / 1e6, rounded up as the builder does."""
    keys = list(msg.account_keys)
    lim = price = None
    for ix in msg.instructions:
        if str(keys[ix.program_id_index]) == "ComputeBudget111111111111111111111111111111":
            d = bytes(ix.data)
            if d[:1] == b"\x02":
                lim = int.from_bytes(d[1:], "little")
            elif d[:1] == b"\x03":
                price = int.from_bytes(d[1:], "little")
    return -(-price * lim // 1_000_000)


class SubDirCase(TmpCase):
    def _sub(self, name: str) -> str:
        p = Path(self.tmp) / name
        p.mkdir(parents=True, exist_ok=True)
        return str(p)


class FrozenShaPinTests(SubDirCase):
    def test_pin_is_the_runner_sha(self):
        self.assertEqual(c0.FROZEN_SHA, "9ed4c5ff63935696b2a89150e13565e123047754")
        self.assertIsNone(c0.guard_problem())

    def test_config_must_name_the_pin(self):
        base = {"bot_id": ARM, "funded_lamports": FUNDED}
        self.assertIsNone(c0.config_refusal({**base, "frozen_sha": c0.FROZEN_SHA}, live=True))
        for bad in ("a" * 40, c0.FROZEN_SHA[:8], c0.FROZEN_SHA.upper(), None, ""):
            self.assertEqual(c0.config_refusal({**base, "frozen_sha": bad}, live=True), "frozen_sha_not_pinned", bad)
        d = {"bot_id": ARM, "frozen_sha": "b" * 40, "root": "/r", "state_dir": "/s", "end_ms": 1, "max_hold_ms": 1, "profile": c0.PROFILE, "funded_lamports": FUNDED}
        with self.assertRaises(ValueError) as cm:
            alb.Config.from_dict(d)
        self.assertIn("frozen_sha_not_pinned", str(cm.exception))

    def test_guard_trips_on_edited_pin(self):
        old = c0.FROZEN_SHA
        try:
            c0.FROZEN_SHA = "9ed4c5ff"  # short
            self.assertEqual(c0.guard_problem(), "c0_guard:frozen_sha")
            c0.FROZEN_SHA = "1" * 40
            self.assertEqual(c0.guard_problem(), "c0_guard:frozen_sha")
        finally:
            c0.FROZEN_SHA = old

    def test_start_row_repeating_the_pin_buys(self):
        e = Hv4Env(self.tmp)
        e.start()
        e.entry_sz("o1")
        b = e.bridge()
        b.tick()
        self.assertEqual(b.state.latches, {})
        self.assertEqual(len([c for c in e.port.calls if c[0] == "buy"]), 1)

    def test_start_row_mismatch_latches_and_never_buys(self):
        for i, wrong in enumerate(("a" * 40, c0.FROZEN_SHA[:8], None)):
            with self.subTest(wrong=wrong):
                e = Hv4Env(self._sub("m%d" % i))
                e.start(sha=wrong)
                e.entry_sz("o1")
                b = e.bridge()
                b.tick()
                self.assertIn("frozen_sha_mismatch", b.state.latches)
                self.assertEqual([c for c in e.port.calls if c[0] == "buy"], [])
                self.assertEqual(e.log_rows("entry_refused")[-1]["why"], "latched:frozen_sha_mismatch")


class UniverseTests(SubDirCase):
    def test_band_mirrors_the_bridge_and_edges(self):
        self.assertEqual(c0.UNIVERSE_V_RANGE, alb.UNIVERSE_V_RANGE)
        lo, hi = c0.UNIVERSE_V_RANGE
        self.assertIsNone(c0.universe_problem(c0.WSOL_MINT, lo))
        self.assertIsNone(c0.universe_problem(c0.WSOL_MINT, hi))
        self.assertEqual(c0.universe_problem(c0.WSOL_MINT, lo - 1), "universe_v_out_of_range")
        self.assertEqual(c0.universe_problem(c0.WSOL_MINT, hi + 1), "universe_v_out_of_range")
        for bad in (None, True, "17.6e9", float("nan")):
            self.assertEqual(c0.universe_problem(c0.WSOL_MINT, bad), "universe_v_out_of_range", bad)

    def test_wsol_check(self):
        self.assertEqual(c0.universe_problem("11111111111111111111111111111111", 17_600_000_000), "quote_not_wsol")
        self.assertEqual(c0.universe_problem(None, 17_600_000_000), "quote_not_wsol")
        try:
            from tools import pumpswap_tx as tx
        except ModuleNotFoundError as e:
            self.skipTest(str(e))
        self.assertEqual(str(tx.WSOL_MINT), c0.WSOL_MINT)

    def test_band_skip_is_a_skip_not_a_cap(self):
        for label in c0.SKIP_UNIVERSE_LABELS:
            with self.subTest(label=label):
                e = Hv4Env(self._sub(label))
                e.port.universe = label
                e.start()
                e.entry_sz("o1")
                b = e.bridge()
                b.tick()
                self.assertEqual([c for c in e.port.calls if c[0] == "buy"], [])
                self.assertEqual(e.log_rows("entry_refused"), [])
                sk = e.log_rows("universe_skip")
                self.assertEqual([r["why"] for r in sk], [label])
                self.assertEqual(b.state.days, {})  # no trade counted against max_trades_per_day
                self.assertEqual(b.state.attempts, 0)
                self.assertEqual(b.state.bought, [])
                self.assertNotIn("total_loss_cap", b.state.latches)

    def test_skip_leaves_headroom_for_the_next_entry(self):
        e = Hv4Env(self.tmp)
        e.start()
        e.port.universe = "universe_v_out_of_range"
        for i in range(5):
            e.entry_sz(f"o{i}", pool=f"P{i}")
        b = e.bridge()
        b.tick()
        e.port.universe = None
        e.entry_sz("ok1", pool="PX")
        b.tick()
        self.assertEqual(len([c for c in e.port.calls if c[0] == "buy"]), 1)
        self.assertEqual(len(e.log_rows("universe_skip")), 5)

    def test_other_refusals_stay_refusals(self):
        e = Hv4Env(self.tmp)
        e.port.universe = "pool_unreadable:x"
        e.start()
        e.entry_sz("o1")
        e.bridge().tick()
        self.assertEqual(e.log_rows("entry_refused")[0]["why"], "pool_unreadable:x")
        self.assertEqual(e.log_rows("universe_skip"), [])

    def test_h5_path_keeps_the_refusal(self):
        e = Env(self.tmp)
        e.port.universe = "universe_v_out_of_range"
        e.start()
        e.entry("o1")
        e.bridge().tick()
        self.assertEqual(e.log_rows("entry_refused")[0]["why"], "universe_v_out_of_range")
        self.assertEqual(e.log_rows("universe_skip"), [])


class SellPinTests(unittest.TestCase):
    def setUp(self):
        try:
            from tools import probe_live  # noqa: F401
        except ModuleNotFoundError as e:
            self.skipTest(str(e))

    def test_pin_recomputes(self):
        self.assertIsNone(c0.sell_pin_problem())
        want = {"path", "symbol", "symbol_sha256", "_sign_sha256", "ptx.constants_sha256", "pe.exit_check_sha256", "c0.ladder_sha256"}
        want |= {f"{n}_sha256" for n in c0.CALLEES_PL} | {f"ptx.{n}_sha256" for n in c0.CALLEES_PTX}
        want |= {k for k in c0.SELL_BUILDER_PIN if k.startswith(("pe.", "bridge.", "x."))}  # PR 5a keys are listed in test_hv4_live_c0_pr5a
        self.assertEqual(set(c0.SELL_BUILDER_PIN), want)
        for n in ("sell_data", "ata", "create_ata_idempotent", "sync_native", "close_account", "swap_accounts", "compute_budget_ixs"):
            self.assertIn(f"ptx.{n}_sha256", c0.SELL_BUILDER_PIN)

    def test_pin_matches_independent_recompute(self):
        import hashlib
        import inspect

        from tools import probe_live as pl
        from tools import pumpswap_tx as ptx
        sha = lambda o: hashlib.sha256(inspect.getsource(o).encode()).hexdigest()  # noqa: E731
        pin = c0.SELL_BUILDER_PIN
        self.assertEqual(sha(pl.LiveExecutor._start_sell), pin["symbol_sha256"])
        self.assertEqual(sha(pl.close_token_account), pin["close_token_account_sha256"])
        self.assertEqual(sha(ptx.sell_instructions), pin["ptx.sell_instructions_sha256"])
        self.assertEqual(sha(ptx.sell_data), pin["ptx.sell_data_sha256"])
        self.assertEqual(sha(ptx.ata), pin["ptx.ata_sha256"])

    def test_every_pin_field_mismatch_is_named(self):
        for k in c0.SELL_BUILDER_PIN:
            if k in ("path", "symbol"):
                continue
            bad = {**c0.SELL_BUILDER_PIN, k: "0" * 64}
            self.assertEqual(c0.sell_pin_problem(bad), f"sell_builder_pin_mismatch:{k}", k)
            gone = {kk: v for kk, v in c0.SELL_BUILDER_PIN.items() if kk != k}
            self.assertEqual(c0.sell_pin_problem(gone), f"sell_builder_pin_mismatch:{k}", k)

    def test_editing_any_callee_trips_the_pin(self):
        """An actual code edit (the function object replaced by one with different source), not just a doctored hash."""
        from unittest import mock

        from tools import probe_live as pl
        from tools import pumpswap_tx as ptx

        def edited(*a, **k):  # different source from every pinned function
            return b"edited"

        for n in c0.CALLEES_PTX:
            with self.subTest(callee=n), mock.patch.object(ptx, n, edited):
                self.assertEqual(c0.sell_pin_problem(), f"sell_builder_pin_mismatch:ptx.{n}_sha256")
        for n in c0.CALLEES_PL:
            with self.subTest(callee=n), mock.patch.object(pl, n, edited):
                self.assertEqual(c0.sell_pin_problem(), f"sell_builder_pin_mismatch:{n}_sha256")
        with mock.patch.object(pl.LiveExecutor, "_start_sell", edited):
            self.assertEqual(c0.sell_pin_problem(), "sell_builder_pin_mismatch:symbol_sha256")
        with mock.patch.object(pl.LiveExecutor, "_sign", edited):
            self.assertEqual(c0.sell_pin_problem(), "sell_builder_pin_mismatch:_sign_sha256")
        for n in c0.CONSTANTS_PTX:  # a changed constant (discriminator, program id, CU limit)
            old = getattr(ptx, n)
            new = old + 1 if isinstance(old, int) else (b"\x00" + old if isinstance(old, bytes) else (ptx.TOKEN_PROGRAM if old != ptx.TOKEN_PROGRAM else ptx.WSOL_MINT))
            with self.subTest(constant=n), mock.patch.object(ptx, n, new):
                self.assertEqual(c0.sell_pin_problem(), "sell_builder_pin_mismatch:ptx.constants_sha256")
        self.assertIsNone(c0.sell_pin_problem())  # patches undone

    @mock.patch.object(c0, "LIVE_REFUSAL_ACTIVE", True)  # the default state, pinned: flipping the constant to False must not change what this test asserts
    def test_main_live_start_stays_refused_by_start_refusal_first(self):
        # PR1's start_refusal is the outermost gate: a live start is refused as hv4_c0_incomplete whatever the pin says (the pin check sits behind it)
        import contextlib, io, json, tempfile
        from unittest import mock

        with tempfile.TemporaryDirectory() as d:
            e = Hv4Env(d)
            full = {k: v for k, v in e.raw.items() if k not in ("stop_file", "halt_file")}
            cp = Path(d) / "cfg.json"
            from tools.test_hv4_live_c0 import live_main_env
            paths, st = live_main_env(d, full["bot_id"])
            cp.write_text(json.dumps({**full, **paths, "mode": "live"}))
            for pin, want in (({**c0.SELL_BUILDER_PIN, "symbol_sha256": "0" * 64}, c0.LIVE_REFUSAL), (c0.SELL_BUILDER_PIN, c0.LIVE_REFUSAL)):
                buf = io.StringIO()
                with mock.patch.object(c0, "SELL_BUILDER_PIN", pin), contextlib.redirect_stdout(buf), live_main_env(d, full["bot_id"])[1]:
                    rc = alb.main(["--config", str(cp), "--live"])
                self.assertEqual(rc, 2)
                self.assertIn(want, buf.getvalue())

    def test_make_live_port_refuses_on_pin_mismatch(self):
        from unittest import mock

        cfg = alb.Config.from_dict({"bot_id": ARM, "frozen_sha": c0.FROZEN_SHA, "root": "/r", "state_dir": "/s", "end_ms": 1, "max_hold_ms": 1,
                                    "profile": c0.PROFILE, "funded_lamports": FUNDED, "mode": "live"})
        with mock.patch.object(c0, "SELL_BUILDER_PIN", {**c0.SELL_BUILDER_PIN, "symbol_sha256": "0" * 64}):
            with self.assertRaises(RuntimeError) as cm:
                alb.make_live_port(cfg, lambda m, p: {}, object())
        self.assertIn("sell_builder_pin_mismatch:symbol_sha256", str(cm.exception))

    def test_built_sell_decodes_one_tx_closing_the_base_ata_on_token_and_token_2022(self):
        from solders.keypair import Keypair
        from solders.message import Message
        from solders.hash import Hash

        from tools import probe_live as pl
        from tools import pumpswap_tx as ptx
        from tools.test_probe_executor import POOL_B64
        import base64 as b64

        ps0 = ptx.parse_pool_account(b64.b64decode(POOL_B64))
        user = Keypair().pubkey()
        for tp in (ptx.TOKEN_PROGRAM, ptx.TOKEN_2022_PROGRAM):
            with self.subTest(token_program=str(tp)):
                ps = ptx.PoolState(pool=ps0["pool"] if "pool" in ps0 else ptx.canonical_pool(ps0["base_mint"]), base_mint=ps0["base_mint"], quote_mint=ptx.WSOL_MINT,
                                   base_vault=ps0["base_vault"], quote_vault=ps0["quote_vault"], coin_creator=ps0["coin_creator"], base_token_program=tp,
                                   protocol_fee_recipient=user, buyback_fee_recipient=user)
                ata = ptx.ata(user, ps.base_mint, tp)
                ixs = [*ptx.sell_instructions(ps, user, 1_000_000, 1, priority_total_lamports=c0.PRIORITY_PIN["escalated"]),
                       pl.close_token_account(ata, user, tp)]
                msg = Message.new_with_blockhash(ixs, user, Hash.default())
                keys = list(msg.account_keys)
                dec = [(keys[i.program_id_index], bytes(i.data), [keys[a] for a in i.accounts]) for i in msg.instructions]
                closes = [d for d in dec if d[1] == b"\x09"]
                self.assertEqual(len(closes), 2)  # WSOL close and the base token account close, one transaction
                self.assertEqual(dec.index(closes[-1]), len(dec) - 1)  # base close is last, after the swap
                self.assertEqual(closes[-1][0], tp)  # under the mint's own token program
                self.assertEqual(closes[-1][2][0], ata)
                self.assertEqual(closes[0][0], ptx.TOKEN_PROGRAM)  # the WSOL close is always plain Token
                self.assertTrue(any(d[1][:8] == ptx.DISC_SELL for d in dec))
                self.assertEqual(priority_lamports(msg), c0.PRIORITY_PIN["escalated"])


class SellPriorityScheduleTests(unittest.TestCase):
    def test_pure_schedule(self):
        self.assertEqual(c0.sell_priority(0), 55_000)
        self.assertEqual(c0.sell_priority(0, stuck=False), c0.PRIORITY_PIN["sell"])
        self.assertEqual(c0.sell_priority(1), 55_000)  # one revert: still 55,000 (DEC-032 s3: after 2 reverts)
        self.assertEqual(c0.sell_priority(2), 150_000)
        self.assertEqual(c0.sell_priority(7), 150_000)
        self.assertEqual(c0.sell_priority(0, stuck=True), 150_000)
        self.assertEqual((c0.ESCALATE_AFTER_REVERTS, c0.ESCALATE_AFTER_MS), (2, 345_000))
        self.assertEqual(c0.sell_priority(0, elapsed_ms=344_999), 55_000)  # time bound, whichever comes first
        self.assertEqual(c0.sell_priority(0, elapsed_ms=345_000), 150_000)
        self.assertEqual(c0.sell_priority(1, elapsed_ms=345_000), 150_000)
        self.assertEqual(c0.sell_priority(2, elapsed_ms=0), 150_000)


class LivePortScheduleTests(TmpCase):
    def _port(self, profile: bool):
        try:
            from solders.keypair import Keypair
        except ModuleNotFoundError as e:
            self.skipTest(str(e))
        from tools.test_probe_executor import Clock as PClock, T0 as PT0
        from tools.test_probe_live import LiveRpc

        clock = PClock()
        rpc = LiveRpc(clock)
        raw = {"bot_id": ARM if profile else BOT, "frozen_sha": c0.FROZEN_SHA if profile else SHA, "root": self.tmp, "state_dir": str(Path(self.tmp) / "st"),
               "end_ms": PT0 + 86_400_000, "max_hold_ms": H, "mode": "live"}
        if profile:
            raw.update(profile=c0.PROFILE, funded_lamports=FUNDED)
        cfg = alb.Config.from_dict(raw)
        return alb.make_live_port(cfg, rpc, Keypair(), now_ms=clock), rpc, clock, cfg

    def _buy(self, port, rpc, clock, cfg):
        from tools.test_probe_executor import MINT
        from tools.test_probe_live import land_buy
        ok, why = port.buy({"order_id": "o1", "mint": MINT, "decision_ms": clock(), "stake_lamports": cfg.stake_lamports})
        self.assertTrue(ok, why)
        buy_prio = priority_lamports(decode_tx(rpc.sent[-1][1]))
        land_buy(port.ex, rpc)
        port.poll()
        return MINT, buy_prio

    def test_hv4_c0_schedule_55_55_150(self):
        from tools.test_probe_live import land_sell
        port, rpc, clock, cfg = self._port(True)
        mint, buy_prio = self._buy(port, rpc, clock, cfg)
        self.assertEqual(buy_prio, 55_000)
        ok, why = port.sell({"mint": mint}, "book_exit")
        self.assertTrue(ok, why)
        self.assertEqual(priority_lamports(decode_tx(rpc.sent[-1][1])), 55_000)  # first sell attempt
        fail = {"InstructionError": [3, {"Custom": 6004}]}
        land_sell(port.ex, rpc, proceeds=0, err=fail, fee=60_000)  # revert 1
        n = len(rpc.sent)
        port.poll()  # the failed sell leaves the bridge's exit_reason set: the executor re-sends at once
        self.assertEqual(port.ex.state.open[mint]["sell_attempts"], 1)
        self.assertGreater(len(rpc.sent), n)
        self.assertEqual(priority_lamports(decode_tx(rpc.sent[-1][1])), 55_000)  # first retry, one revert: still 55,000
        land_sell(port.ex, rpc, proceeds=0, err=fail, fee=60_000)  # revert 2
        n = len(rpc.sent)
        port.poll()
        self.assertEqual(port.ex.state.open[mint]["sell_attempts"], 2)
        self.assertGreater(len(rpc.sent), n)
        msg = decode_tx(rpc.sent[-1][1])
        self.assertEqual(priority_lamports(msg), 150_000)  # after 2 reverts: escalated; validate_message passed at the same cap
        keys = list(msg.account_keys)
        self.assertEqual(str(keys[msg.instructions[-1].program_id_index]), str(port.ex.state.open[mint]["base_tp"]))  # sell + base ATA close in one tx

    def test_time_bound_escalates_a_first_retry(self):
        from tools.test_probe_live import land_sell
        port, rpc, clock, cfg = self._port(True)
        mint, _ = self._buy(port, rpc, clock, cfg)
        port.sell({"mint": mint}, "book_exit")
        self.assertEqual(priority_lamports(decode_tx(rpc.sent[-1][1])), 55_000)
        self.assertIn("c0_first_sell_ms", port.ex.state.open[mint])
        land_sell(port.ex, rpc, proceeds=0, err={"InstructionError": [3, {"Custom": 6004}]}, fee=60_000)
        clock.t += c0.ESCALATE_AFTER_MS  # the bound passes with only ONE revert
        n = len(rpc.sent)
        port.poll()
        self.assertEqual(port.ex.state.open[mint]["sell_attempts"], 1)
        self.assertGreater(len(rpc.sent), n)
        self.assertEqual(priority_lamports(decode_tx(rpc.sent[-1][1])), 150_000)

    def test_stuck_position_sells_at_escalated(self):
        port, rpc, clock, cfg = self._port(True)
        mint, _ = self._buy(port, rpc, clock, cfg)
        pos = port.ex.state.open[mint]
        pos["stuck"] = True
        self.assertEqual(port.ex._priority(mint), 150_000)
        pos["stuck"] = False
        pos["sell_attempts"] = 0
        self.assertEqual(port.ex._priority(mint), 55_000)

    def test_plain_profile_unchanged_500k(self):
        from tools.test_probe_live import land_sell
        port, rpc, clock, cfg = self._port(False)
        mint, buy_prio = self._buy(port, rpc, clock, cfg)
        self.assertEqual(buy_prio, 500_000)
        port.sell({"mint": mint}, "book_exit")
        self.assertEqual(priority_lamports(decode_tx(rpc.sent[-1][1])), 500_000)
        self.assertEqual(port.ex._priority(mint), port.ex.limits.priority_lamports)

    def test_plain_config_has_no_c0_state(self):
        port, rpc, clock, cfg = self._port(False)
        self.assertEqual(cfg.profile, "")
        self.assertEqual(port.ex._priority("anything"), port.ex.limits.priority_lamports)


class H5PathUnchangedPr2Tests(TmpCase):
    def test_plain_universe_refusal_and_sha_not_pinned(self):
        # a plain bridge accepts any frozen_sha (its own config), and a different one latches as before
        e = Env(self.tmp)
        e.start(sha="c" * 64)
        e.entry("o1")
        b = e.bridge()
        b.tick()
        self.assertIn("frozen_sha_mismatch", b.state.latches)

    def test_plain_config_may_use_any_sha(self):
        cfg = alb.Config.from_dict({"bot_id": BOT, "frozen_sha": "d" * 64, "root": "/r", "state_dir": "/s", "end_ms": 1, "max_hold_ms": 1})
        self.assertEqual(cfg.frozen_sha, "d" * 64)


class DecisionsSymlinkTests(SubDirCase):
    """QP PR1 condition 1. A scratch dir under the test tmp stands in for /data/mal/arena (SEALED_ROOT_PREFIX is patched to it); no real arena path is touched."""

    def _setup(self):
        import os
        stand_in = Path(os.path.realpath(self._sub("arena_stand_in")))
        root = Path(self._sub("marked_root"))
        (root / c0.SYNTHETIC_MARKER).write_text("")
        (root / "decisions").mkdir()
        return stand_in, root

    def _patched(self, stand_in):
        from unittest import mock
        return mock.patch.object(c0, "SEALED_ROOT_PREFIX", str(stand_in))

    def test_clean_marked_root_is_allowed(self):
        stand_in, root = self._setup()
        (root / "decisions" / ARM).mkdir()
        (root / "decisions" / ARM / "2026-10-06.jsonl").write_text("")
        with self._patched(stand_in):
            self.assertIsNone(c0.start_refusal(str(root), False, ARM))
            self.assertIsNone(c0.start_refusal(str(root), False))  # no bot_id: path check skipped, marker rule only

    def test_symlinked_bot_dir_into_the_arena_is_refused(self):
        stand_in, root = self._setup()
        (stand_in / ARM).mkdir()
        (root / "decisions" / ARM).symlink_to(stand_in / ARM)
        with self._patched(stand_in):
            self.assertEqual(c0.start_refusal(str(root), False, ARM), c0.LIVE_REFUSAL)

    def test_symlinked_day_file_into_the_arena_is_refused(self):
        stand_in, root = self._setup()
        (root / "decisions" / ARM).mkdir()
        (stand_in / "day.jsonl").write_text("")
        (root / "decisions" / ARM / "2026-10-06.jsonl").symlink_to(stand_in / "day.jsonl")
        (root / "decisions" / ARM / "2026-10-07.jsonl").write_text("")
        with self._patched(stand_in):
            self.assertEqual(c0.start_refusal(str(root), False, ARM), c0.LIVE_REFUSAL)

    def test_main_refuses_a_symlinked_decisions_dir(self):
        import contextlib, io, json
        from unittest import mock
        stand_in, root = self._setup()
        (stand_in / ARM).mkdir()
        (root / "decisions" / ARM).symlink_to(stand_in / ARM)
        e = Hv4Env(self.tmp)
        full = {k: v for k, v in e.raw.items() if k not in ("stop_file", "halt_file")}
        cp = Path(self.tmp) / "cfg_sym.json"
        cp.write_text(json.dumps({**full, "root": str(root), "mode": "dry"}))
        buf = io.StringIO()
        with self._patched(stand_in), contextlib.redirect_stdout(buf):
            rc = alb.main(["--config", str(cp), "--once"])
        self.assertEqual(rc, 2)
        self.assertIn(c0.LIVE_REFUSAL, buf.getvalue())
        (root / "decisions" / ARM).unlink()  # control: same root without the link starts
        (root / "decisions" / ARM).mkdir()
        buf = io.StringIO()
        with self._patched(stand_in), contextlib.redirect_stdout(buf):
            rc = alb.main(["--config", str(cp), "--once"])
        self.assertEqual(rc, 0, buf.getvalue())


class ReadPathGuardTests(SubDirCase):
    """QP #913: the sealed-path check runs on EVERY decision-log read inside the Bridge object (not only in main). A scratch dir stands in for the arena
    (SEALED_ROOT_PREFIX patched); no real arena path is touched."""

    def setUp(self):
        import os
        from unittest import mock
        super().setUp()
        self.stand_in = Path(os.path.realpath(self._sub("arena_stand_in")))
        (self.stand_in / "x.jsonl").write_text("")
        self.scratch = Path(self._sub("scratch"))
        (self.scratch / "y.jsonl").write_text("")
        p = mock.patch.object(c0, "SEALED_ROOT_PREFIX", str(self.stand_in))
        p.start()
        self.addCleanup(p.stop)

    def _env(self):
        e = Hv4Env(self._sub("env"))
        e.start()
        return e

    def _dir(self, e):
        return e.tmp / "arena" / "decisions" / ARM

    def _buys(self, e):
        return [c for c in e.port.calls if c[0] == "buy"]

    def _run_clean_then(self, e, mutate, expect_reason):
        b = e.bridge()
        b.tick()
        self.assertEqual(b.state.latches, {})
        mutate()
        e.entry_sz("o_after")
        b.tick()
        self.assertIn("sealed_path", b.state.latches)
        self.assertIn(expect_reason, b.state.latches["sealed_path"])
        self.assertEqual(self._buys(e), [])  # nothing read after the hit, nothing bought
        n_rows = b.state.seq
        e.entry_sz("o_after2", pool="P2")
        b.tick()
        self.assertEqual((b.state.seq, self._buys(e)), (n_rows, []))  # latched: reads nothing more

    def test_day_file_symlink_added_after_start(self):
        e = self._env()
        self._run_clean_then(e, lambda: (self._dir(e) / "2099-01-01.jsonl").symlink_to(self.scratch / "y.jsonl"), "symlink")

    def test_day_file_symlink_into_arena_added_after_start(self):
        e = self._env()
        self._run_clean_then(e, lambda: (self._dir(e) / "2099-01-01.jsonl").symlink_to(self.stand_in / "x.jsonl"), "realpath_under_arena")

    def test_hard_link_added_after_start(self):
        import os
        e = self._env()
        self._run_clean_then(e, lambda: os.link(self.stand_in / "x.jsonl", self._dir(e) / "2099-01-01.jsonl"), "hardlink")

    def test_hard_link_of_a_non_arena_file_also_refused(self):
        import os
        e = self._env()
        self._run_clean_then(e, lambda: os.link(self.scratch / "y.jsonl", self._dir(e) / "2099-01-01.jsonl"), "hardlink")

    def test_bot_dir_swapped_for_a_symlink_after_start(self):
        import os
        e = self._env()

        def swap():
            d = self._dir(e)
            os.rename(d, d.parent / "moved")
            d.symlink_to(d.parent / "moved")

        self._run_clean_then(e, swap, "symlink_or_not_dir")

    def test_bot_dir_swapped_for_an_arena_symlink_after_start(self):
        import os
        e = self._env()

        def swap():
            d = self._dir(e)
            os.rename(d, d.parent / "moved")
            (self.stand_in / ARM).mkdir()
            d.symlink_to(self.stand_in / ARM)

        self._run_clean_then(e, swap, "realpath_under_arena")

    def test_decisions_dir_symlink_after_start(self):
        import os
        e = self._env()

        def swap():
            dec = e.tmp / "arena" / "decisions"
            os.rename(dec, e.tmp / "arena" / "decisions_moved")
            dec.symlink_to(e.tmp / "arena" / "decisions_moved")

        self._run_clean_then(e, swap, "symlink_or_not_dir")

    def test_inode_swap_between_check_and_open(self):
        import os
        from unittest import mock
        e = self._env()
        f = self._dir(e)
        name = next(iter(os.listdir(f)))
        real_open = os.open

        def swap_then_open(path, flags, *a, **k):
            if str(path).endswith(name):
                tmp = Path(str(path) + ".new")
                tmp.write_bytes(b"")
                os.replace(tmp, path)  # a different inode now sits at the checked path
            return real_open(path, flags, *a, **k)

        with mock.patch.object(os, "open", swap_then_open):
            with self.assertRaises(c0.SealedPath) as cm:
                c0.guarded_read(str(e.tmp / "arena"), ARM, name)
        self.assertEqual(str(cm.exception), "inode_swapped")

    def test_o_nofollow_on_a_symlink_made_after_lstat(self):
        import os
        from unittest import mock
        e = self._env()
        f = self._dir(e)
        name = next(iter(os.listdir(f)))
        real_open = os.open

        def link_then_open(path, flags, *a, **k):
            if str(path).endswith(name):
                os.unlink(path)
                os.symlink(self.scratch / "y.jsonl", path)
            return real_open(path, flags, *a, **k)

        with mock.patch.object(os, "open", link_then_open):
            with self.assertRaises(c0.SealedPath) as cm:
                c0.guarded_read(str(e.tmp / "arena"), ARM, name)
        self.assertEqual(str(cm.exception), "open_nofollow_failed")

    def test_directly_constructed_bridge_with_a_hard_link_present_latches_at_construction(self):
        import os
        e = self._env()
        os.link(self.stand_in / "x.jsonl", self._dir(e) / "2099-01-01.jsonl")
        e.entry_sz("o1")
        b = e.bridge()  # Bridge(...) built directly, no main
        self.assertIn("hardlink", b.state.latches.get("sealed_path", ""))
        b.tick()
        self.assertEqual(self._buys(e), [])

    def test_directly_constructed_bridge_with_a_symlinked_bot_dir_latches(self):
        import os
        e = self._env()
        d = self._dir(e)
        os.rename(d, d.parent / "moved")
        d.symlink_to(self.stand_in)
        b = e.bridge()
        self.assertIn("sealed_path", b.state.latches)
        b.tick()
        self.assertEqual(self._buys(e), [])

    def test_clean_files_are_read_normally(self):
        e = self._env()
        e.entry_sz("o1")
        b = e.bridge()
        b.tick()
        self.assertEqual(b.state.latches, {})
        self.assertEqual(len(self._buys(e)), 1)

    def test_plain_profile_does_not_use_the_guard(self):
        import os
        e = Env(self._sub("plain"))
        e.start()
        e.entry("o1")
        os.link(self.stand_in / "x.jsonl", e.tmp / "arena" / "decisions" / BOT / "2099-01-01.jsonl")
        b = e.bridge()
        self.assertFalse(b.guarded)
        self.assertNotIn("sealed_path", b.state.latches)


if __name__ == "__main__":
    unittest.main()
