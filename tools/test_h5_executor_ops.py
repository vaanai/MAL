"""H5 executor operator paths: the wallet-wide STOP / HALT of the probe's directory, and the offline --mark-closed reconcile after a root
sell-and-close. Fixtures from test_h5_executor (the probe's fake RPC, the consistent fake chain)."""

from __future__ import annotations

import contextlib
import io
import json
import os
import unittest
from pathlib import Path
from unittest import mock

from tools import h5_executor as h
from tools import probe_executor as pe
from tools import pumpswap_tx as tx
from tools.test_h5_executor import MINT, RENT, T0, Case, Env
from tools.test_probe_executor import tok_acct
from tools.test_probe_live import meta_result

SIG = "S" * 87 + "1"


class WalletSwitchTests(Case):
    def switch(self, name: str) -> Path:
        p = self.probe_dir / name  # the patched /var/lib/mal-live
        p.write_text("")
        self.addCleanup(p.unlink, missing_ok=True)
        return p

    def test_the_wallet_wide_stop_blocks_a_buy(self):
        e = self.env()
        self.switch("STOP")
        e.fire()
        self.assertEqual((e.refusals(), e.rpc.sent, e.ex.state.attempts), (["stop_file"], [], 0))

    def test_the_wallet_wide_stop_stops_buy_rebroadcasts_but_not_exits(self):
        e = self.env()
        e.fire()
        stop = self.switch("STOP")
        e.clock.t += 450
        e.ex.housekeeping(e.clock())
        self.assertEqual(len(e.rpc.sent), 1)  # no rebroadcast of the buy
        stop.unlink()
        e.land_buy()
        self.switch("STOP")
        e.at_slot(e.plan()["send_slot"])
        self.assertEqual(len(e.rpc.sent), 2)  # the exit still goes out

    def test_the_wallet_wide_halt_freezes_everything(self):
        e = self.env()
        e.open_position()
        halt = self.switch("HALT")
        e.fire(mint="Other" + "1" * 38)
        self.assertEqual(e.refusals()[-1], "halt_file")  # no new buy
        e.at_slot(e.plan()["send_slot"])
        self.assertEqual(len(e.rpc.sent), 1)  # no sell
        e.clock.t += 450
        e.ex.housekeeping(e.clock())
        self.assertEqual(len(e.rpc.sent), 1)  # no rebroadcast
        halt.unlink()
        e.at_slot(e.plan()["send_slot"] + 2)
        self.assertEqual(len(e.rpc.sent), 2)  # and it resumes when the file goes

    def test_the_state_dir_switches_still_work_and_both_paths_are_in_the_start_row(self):
        e = self.env()
        paths = e.ledger("start")[0]["paths"]
        self.assertEqual((paths["wallet_stop"], paths["wallet_halt"]), (str(self.probe_dir / "STOP"), str(self.probe_dir / "HALT")))
        Path(e.conf["stop_file"]).write_text("")
        e.fire()
        self.assertEqual(e.refusals(), ["stop_file"])

    @unittest.skipIf(os.geteuid() == 0, "root ignores directory modes")
    def test_an_unreadable_wallet_directory_fails_closed_for_stop_only_and_a_dry_run_ignores_it(self):
        e = self.env()
        d = self.tmp / "d"
        d.mkdir()
        dry = Env(d, live=False)
        os.chmod(self.probe_dir, 0)
        self.addCleanup(os.chmod, self.probe_dir, 0o700)
        self.assertTrue(e.ex._stop_present())  # live: cannot see it, so no new buys
        self.assertFalse(e.ex._halt_present())  # but exits are not frozen by a directory it cannot read
        self.assertEqual((dry.ex._stop_present(), dry.ex._halt_present()), (False, False))


class MarkClosedTests(Case):
    def setUp(self):
        super().setUp()
        self.e = self.env()
        self.pos = self.e.open_position()
        self.wallet = str(self.e.ex.user)
        self.proceeds = 21_000_000
        self.delta = self.proceeds - 60_000 + RENT  # what the wallet gained in the sell, rent refund included
        self.e.rpc.accounts = {self.pos["base_ata"]: None}  # the token account is closed
        self.sd = Path(self.e.conf["state_dir"]) / "live"

    def sell_tx(self, *, sig=SIG, err=None, sold=True, program=True, signer=None, delta=None) -> dict:
        res = meta_result(self.e.ex, MINT, delta=self.delta if delta is None else delta, fee=60_000, tok_delta=-self.pos["tokens"] if sold else 0,
                          err=err, ata_pre=RENT, ata_post=0, slot=200_000)
        keys = res["transaction"]["message"]["accountKeys"]
        if signer:
            keys[0] = signer
        keys.append(str(tx.PUMPSWAP_PROGRAM) if program else str(tx.TOKEN_PROGRAM))
        res["transaction"]["signatures"] = [sig]
        res["transaction"]["message"]["header"] = {"numRequiredSignatures": 1}
        res["transaction"]["message"]["instructions"] = [{"programIdIndex": len(keys) - 1, "accounts": [], "data": ""}]
        res["blockTime"] = T0 // 1000
        self.e.rpc.txs[sig] = res
        return res

    def snapshot(self) -> tuple[bytes, ...]:
        return tuple((self.sd / n).read_bytes() for n in ("state-live.json", "h5-counters.json", "h5-ledger.jsonl"))

    def close(self, sig=SIG, mint=MINT) -> int:
        with contextlib.redirect_stdout(io.StringIO()):
            return h.mark_closed(self.e.conf, mint, sig, self.e.rpc, now_ms=T0 + 60_000)

    def test_a_verified_root_sell_closes_the_position_and_books_the_pnl(self):
        self.sell_tx()
        before_total = pe.State.load(self.sd / "state-live.json", "live").realized_lamports
        self.assertEqual(self.close(), 0)
        st = pe.State.load(self.sd / "state-live.json", "live")
        self.assertNotIn(MINT, st.open)
        expected = self.delta - self.pos["buy_cost_lamports"]
        self.assertEqual(st.realized_lamports, before_total + expected)
        c = h.H5Counters.load(self.sd / "h5-counters.json", "live")
        self.assertEqual(c.days[h.day_key(T0)]["realized"], expected)
        self.assertNotIn(MINT, c.plans)
        row = [json.loads(x) for x in (self.sd / "h5-ledger.jsonl").read_text().splitlines() if '"manual_close"' in x][0]
        self.assertEqual((row["kind"], row["mint"], row["signature"], row["pnl_lamports"], row["tokens_sold"]), ("manual_close", MINT, SIG, expected, self.pos["tokens"]))
        self.assertEqual(row["rent_refunded_lamports"], RENT)

    def test_the_loss_stops_see_the_booked_loss_and_the_slot_is_free(self):
        self.sell_tx(delta=1_000_000)  # a bad sell: the wallet got back almost nothing
        self.assertEqual(self.close(), 0)
        ex2 = self.e.build()
        self.assertEqual(ex2.state.open, {})
        self.assertLess(ex2.state.realized_lamports, -15_000_000)
        self.assertEqual(ex2.counters.days[h.day_key(T0)]["realized"], ex2.state.realized_lamports)
        # The closed position no longer counts as at-risk exposure: only this stake does (20M), so the daily stop (80M) is judged on realized - 20M.
        ex2.counters.day(h.day_key(T0))["realized"] = -59_999_999
        self.assertIsNone(ex2._budget_stop(self.e.clock()))
        ex2.counters.day(h.day_key(T0))["realized"] = -60_000_000
        self.assertEqual(ex2._budget_stop(self.e.clock()), "daily_loss_stop")

    def assert_refused_and_unchanged(self, **kw):
        before = self.snapshot()
        self.assertEqual(self.close(**{k: v for k, v in kw.items() if k in ("sig", "mint")}), 1, kw)
        self.assertEqual(self.snapshot(), before, kw)

    def test_each_failed_check_refuses_and_changes_nothing(self):
        self.sell_tx()
        self.assert_refused_and_unchanged(mint="Nope" + "1" * 39)  # no such position
        self.assert_refused_and_unchanged(sig="X" * 87 + "2")  # no such transaction
        self.sell_tx(err={"InstructionError": [3, {"Custom": 6004}]})
        self.assert_refused_and_unchanged()  # it failed on chain
        self.sell_tx(signer="Some" + "1" * 39)
        self.assert_refused_and_unchanged()  # our wallet did not sign it
        self.sell_tx(program=False)
        self.assert_refused_and_unchanged()  # not a PumpSwap call
        self.sell_tx(sold=False)
        self.assert_refused_and_unchanged()  # the token balance did not fall
        res = self.sell_tx()
        res["transaction"]["signatures"] = ["Z" * 87 + "3"]
        self.assert_refused_and_unchanged()  # the signature is not the one given
        self.sell_tx()
        self.e.rpc.accounts = {self.pos["base_ata"]: tok_acct(5)}
        self.assert_refused_and_unchanged()  # the token account still holds tokens
        self.e.rpc.accounts = {self.pos["base_ata"]: None}
        self.assertEqual(self.close(), 0)  # and with everything right it goes through

    def test_a_token_account_that_is_empty_but_open_is_accepted(self):
        self.sell_tx()
        self.e.rpc.accounts = {self.pos["base_ata"]: tok_acct(0)}
        self.assertEqual(self.close(), 0)

    def test_it_is_refused_while_the_unit_holds_the_lock(self):
        self.sell_tx()
        fd = h.acquire_lock(Path(self.e.conf["state_dir"]) / "h5-executor.lock")
        before = self.snapshot()
        try:
            with self.assertRaises(SystemExit) as cm:
                self.close()
            self.assertIn("holds the lock", str(cm.exception))
        finally:
            os.close(fd)
        self.assertEqual(self.snapshot(), before)

    def test_the_wallet_comes_from_our_ledger_not_the_command_line(self):
        self.assertEqual(h._live_wallet(self.sd / "h5-ledger.jsonl"), self.wallet)
        self.assertIsNone(h._live_wallet(self.sd / "no-such-ledger.jsonl"))

    def test_the_cli_needs_a_signature_and_runs_with_the_rpc_from_the_environment(self):
        self.sell_tx()
        cp = self.tmp / "c.json"
        cp.write_text(json.dumps(self.e.conf))
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as cm:
            h.main(["--config", str(cp), "--mark-closed", MINT])
        self.assertEqual(cm.exception.code, 2)
        with mock.patch.object(pe, "ProbeRpc", lambda url: self.e.rpc), mock.patch.dict(os.environ, {"H5_TEST_RPC": "https://rpc.example.test/?api-key=K"}), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            rc = h.main(["--config", str(cp), "--mark-closed", MINT, "--sig", SIG, "--rpc-env", "H5_TEST_RPC"])
        self.assertEqual(rc, 0)
        self.assertIn("closed by", out.getvalue())
        self.assertNotIn("api-key", out.getvalue())


if __name__ == "__main__":
    unittest.main()
