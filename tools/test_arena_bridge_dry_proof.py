"""Tests for tools/arena_bridge_dry_proof.py and the bridge's tx build path over a STUB RPC. No network, no key (throwaway Keypair), no send."""

from __future__ import annotations

import base64
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tools import arena_bridge_dry_proof as proof
from tools import arena_live_bridge as alb

REPO = Path(__file__).resolve().parent.parent


class DryProofTests(unittest.TestCase):
    def test_every_check_passes(self):
        res = proof.run_proof()
        bad = [(n, d) for n, ok, d in res if not ok]
        self.assertEqual(bad, [])
        names = {n for n, _, _ in res}
        for need in ("chain_verifies_clean", "restart_replays_nothing", "cursor_loss_no_double_buy", "tamper_latches_and_no_buy", "stop_file_blocks_buy",
                     "halt_file_blocks_buy", "age_6s_refused", "fake_balance_below_floor_refused", "max_open_cap", "past_end_ms_refused"):
            self.assertIn(need, names)

    def test_floor_edge_with_fake_balance(self):
        """Just above the floor formula passes, just below refuses. Fake balance source only."""
        stake = alb.TIERS["T0"]["stake_lamports"]
        need = alb.DEFAULT_WALLET_FLOOR + stake + alb.FEE_RESERVE_LAMPORTS + alb.RENT_RESERVE_LAMPORTS + alb.BUFFER_LAMPORTS
        for bal, expect in ((need + 1_000_000, True), (need - 1_000_000, False)):
            with tempfile.TemporaryDirectory() as d:
                r = proof.Rig(Path(d), balance=bal)
                r.start()
                r.entry("o1", "P1")
                r.bridge().tick()
                self.assertEqual(any(w["side"] == "buy" for w in r.port.would_send), expect, bal)

    def test_cli_exit_code_and_json(self):
        p = subprocess.run([sys.executable, "-m", "tools.arena_bridge_dry_proof"], capture_output=True, text=True, cwd=REPO)
        self.assertEqual(p.returncode, 0, p.stdout[-400:] + p.stderr[-400:])
        lines = [x for x in p.stdout.splitlines() if not x.startswith("arena_live_bridge ALERT")]
        self.assertTrue(json.loads("\n".join(lines))["all_ok"])

    def test_dry_bridge_never_imports_solana_stack(self):
        code = ("import sys, tools.arena_bridge_dry_proof as p; p.run_proof(); "
                "bad=[m for m in ('solders','solana','tools.probe_live','tools.h5_executor') if m in sys.modules]; "
                "print('BAD', bad) if bad else print('CLEAN')")
        p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=REPO)
        self.assertIn("CLEAN", p.stdout, p.stdout[-300:] + p.stderr[-300:])


class StubRpcBuildPathTests(unittest.TestCase):
    """The live port's tx build + sign path over the probe tests' stub RPC. sendTransaction is a recorder; nothing leaves the process."""

    def test_buy_and_sell_txs_are_built_signed_and_only_recorded(self):
        from solders.keypair import Keypair
        from solders.transaction import VersionedTransaction

        from tools import pumpswap_tx as tx_mod
        from tools.test_arena_live_bridge import BOT, H, SHA
        from tools.test_probe_executor import MINT, T0 as PT0, Clock as PClock
        from tools.test_probe_live import LiveRpc, land_buy, land_sell

        with tempfile.TemporaryDirectory() as d:
            clock = PClock()
            rpc = LiveRpc(clock)
            kp = Keypair()
            cfg = alb.Config.from_dict({"bot_id": BOT, "frozen_sha": SHA, "root": d, "state_dir": str(Path(d) / "st"), "end_ms": PT0 + 86_400_000,
                                        "max_hold_ms": H, "mode": "live"})
            port = alb.make_live_port(cfg, rpc, kp, now_ms=clock)
            ok, why = port.buy({"order_id": "o1", "mint": MINT, "decision_ms": clock(), "stake_lamports": cfg.stake_lamports})
            self.assertTrue(ok, why)
            self.assertEqual(len(rpc.sent), 1)
            land_buy(port.ex, rpc)
            port.poll()
            ok, why = port.sell({"mint": MINT}, "book_exit")
            self.assertTrue(ok, why)
            self.assertEqual(len(rpc.sent), 2)
            land_sell(port.ex, rpc, proceeds=cfg.stake_lamports)
            port.poll()
            txs = [VersionedTransaction.from_bytes(base64.b64decode(b64)) for _, b64 in rpc.sent]
            progs = []
            for tx in txs:
                self.assertEqual(len(tx.signatures), 1)
                self.assertEqual(tx.message.account_keys[0], kp.pubkey())  # fee payer is the throwaway signer
                self.assertGreaterEqual(len(tx.message.instructions), 3)
                progs.append([str(tx.message.account_keys[i.program_id_index]) for i in tx.message.instructions])
            for ps in progs:
                self.assertIn(str(tx_mod.PUMPSWAP_PROGRAM), ps)
            self.assertNotEqual(progs[0], progs[1])  # the sell leg carries the close-ATA instruction the buy does not
            self.assertEqual(port.holdings(), {})
            self.assertEqual(sum(1 for c in rpc.calls if c == "sendTransaction"), 2)  # stub only: both recorded, none sent anywhere


if __name__ == "__main__":
    unittest.main()
