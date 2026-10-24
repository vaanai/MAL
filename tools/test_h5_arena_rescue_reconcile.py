"""QP #839 condition 1 (ruling #816 B4): a rescue-closed arena trip is measured. Full offline path: a live position -> the rescue marker -> the arena executor
refuses to start -> the base --mark-closed (h5_executor.mark_closed) on the ARENA state -> the executor starts, closes the parity trip at the trip's chain
delta (live_net_lamports, exit_reason manual_close) -> the tie-out exporter shows a closed trip with the rescue signature and the same realized delta.
Fixtures and a fake RPC only: no network, no key material beyond a throwaway Keypair, nothing sent."""
from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
from unittest import mock

from tools import arena_tieout_ledger as L
from tools import h5_arena_executor as ax
from tools import h5_executor as h
from tools import pumpswap_tx as tx
from tools.test_h5_arena_exec_wiring import LiveCase
from tools.test_h5_executor import MINT, RENT, T0
from tools.test_probe_live import meta_result

SIG = "R" * 87 + "1"


class RescueReconcileTests(LiveCase):
    def sell_tx(self, e, pos, delta):
        res = meta_result(e.ex, MINT, delta=delta, fee=60_000, tok_delta=-pos["tokens"], err=None, ata_pre=RENT, ata_post=0, slot=200_000)
        keys = res["transaction"]["message"]["accountKeys"]
        keys.append(str(tx.PUMPSWAP_PROGRAM))
        res["transaction"]["signatures"] = [SIG]
        res["transaction"]["message"]["header"] = {"numRequiredSignatures": 1}
        res["transaction"]["message"]["instructions"] = [{"programIdIndex": len(keys) - 1, "accounts": [], "data": ""}]
        res["blockTime"] = T0 // 1000
        e.rpc.txs[SIG] = res

    def rebuild(self, e):
        self.arena_tier.write_text("T1\n")  # a live start needs T1 (AEnv drops it to T0 after construction for the base-tier tests)
        with mock.patch.object(ax, "FEED_NOTE_MERGED", True):
            return e.build()

    def test_rescue_marker_blocks_the_start_then_mark_closed_measures_the_trip(self):
        e = self.live()
        pos = e.open_position()
        self.assertIn(MINT, e.ex.gates.recorder.trips)
        self.assertFalse(e.ex.gates.recorder.trips[MINT]["exit_landed"])
        sd = Path(e.conf["state_dir"])
        (sd / f"RESCUE-{MINT}.json").write_text(json.dumps({"mint": MINT, "signature": SIG}))
        with self.assertRaises(SystemExit) as cm:
            self.rebuild(e)
        self.assertIn("rescue_reconcile_pending", str(cm.exception))

        delta = 21_000_000 - 60_000 + RENT  # what the wallet gained in the rescue sell, rent refund included
        self.sell_tx(e, pos, delta)
        e.rpc.accounts = {pos["base_ata"]: None}  # the token account is closed
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(h.mark_closed(e.conf, MINT, SIG, e.rpc, now_ms=T0 + 60_000), 0)  # the base reconcile, on the arena state, unchanged

        ex2 = self.rebuild(e)  # the marker stays as a record; the mint is no longer open, so the start is allowed
        trip = ex2.gates.recorder.trips[MINT]
        expected = delta - pos["buy_cost_lamports"]
        self.assertTrue(trip["exit_landed"])
        self.assertEqual(trip["exit_reason"], "manual_close")
        self.assertEqual(trip["live_net_lamports"], expected)  # the trip's chain delta enters parity d
        self.assertAlmostEqual(trip["live_sell_price"], (delta + 60_000 - RENT) / pos["tokens"])

        rows = [json.loads(x) for x in (sd / "live" / "h5-ledger.jsonl").read_text().splitlines()]
        led = L.export(rows, {"open": dict(ex2.state.open)})
        t = [x for x in led["trips"] if x["trip_id"] == MINT][0]
        self.assertEqual((t["closed"], t["sell_sig"], t["realized_lamports"]), (True, SIG, expected))
        self.assertIn(SIG, {s["signature"] for s in led["sends"] if s["status"] == "landed"})

    def test_a_restart_does_not_book_the_manual_close_twice(self):
        e = self.live()
        pos = e.open_position()
        delta = 20_000_000
        self.sell_tx(e, pos, delta)
        e.rpc.accounts = {pos["base_ata"]: None}
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(h.mark_closed(e.conf, MINT, SIG, e.rpc, now_ms=T0 + 60_000), 0)
        ex2 = self.rebuild(e)
        seq = ex2.gates.recorder.seq
        ex3 = self.rebuild(e)
        self.assertEqual(ex3.gates.recorder.trips[MINT]["live_net_lamports"], delta - pos["buy_cost_lamports"])
        self.assertLessEqual(ex3.gates.recorder.seq, seq)  # nothing re-applied on a closed trip

    def test_an_unreadable_manual_close_row_refuses_the_start(self):
        e = self.live()
        e.open_position()
        with (Path(e.conf["state_dir"]) / "live" / "h5-ledger.jsonl").open("a") as fh:
            fh.write(json.dumps({"schema": "x", "mode": "live", "kind": "manual_close", "mint": MINT, "signature": SIG, "tokens_sold": 0}) + "\n")
        with self.assertRaises(SystemExit) as cm:
            self.rebuild(e)
        self.assertIn("manual_close_unreadable", str(cm.exception))

    def test_the_rescue_check_is_in_the_c0_tripwire(self):
        self.assertIn("_rescue_problem", ax.GUARD_METHODS)
        self.assertIsNone(ax.c0_guard_problem())
