"""HV4 live C0, removal precondition 8 residuals (QP #970 R2 / N7 / N-D and ruling 2): every send row carries the priority SIGNED for its own signature and its
`last_valid_block_height`, and N2 / N4 of the same review.

The real hv4_c0 live port (tools.arena_live_bridge.make_live_port -> BridgeExecutor) runs over the probe tests' stub RPC and a throwaway Keypair; its fill rows go through
the real ParityRecorder, tools.hv4_c0_tieout, tools.arena_tieout_fetch.cut_ledger and tools.arena_tieout.reconcile. Synthetic fixtures only: no HV4 ledger, decision row,
read record, bots.json or job log is read; no network, no key, nothing is sent anywhere (sendTransaction is a recorder)."""

from __future__ import annotations

import base64
import copy
import json
import contextlib
import io
import unittest
from pathlib import Path
from unittest import mock

from tools import arena_live_bridge as alb
from tools import arena_tieout as ato
from tools import arena_tieout_fetch as fetch
from tools import arena_tieout_ledger as atl
from tools import hv4_c0_tieout as tie
from tools import hv4_live_c0 as c0
from tools import probe_executor as pe
from tools import probe_live as pl
from tools.h5_arena_gates import ParityRecorder
from tools.test_arena_live_bridge import H, TmpCase
from tools.test_hv4_live_c0 import ARM, FUNDED
from tools.test_hv4_live_c0_pr2 import decode_tx, priority_lamports
from tools.test_probe_executor import MINT, T0 as PT0, Clock as PClock
from tools.test_probe_live import LVBH, RENT, LiveRpc, land_sell, meta_result

FAIL = {"InstructionError": [3, {"Custom": 6004}]}
BASE_FEE = 5_000


class Rpc(LiveRpc):
    """LiveRpc whose every getLatestBlockhash carries its own, increasing lastValidBlockHeight (the stub's is a constant), so rows can be told apart by height."""

    def __call__(self, method, params):
        res = super().__call__(method, params)
        if method == "getLatestBlockhash":
            res = {**res, "value": {**res["value"], "lastValidBlockHeight": LVBH + 100 * self.fetches}}
        return res


def signed_prio(b64: str) -> int:
    return priority_lamports(decode_tx(b64))


class Harness(TmpCase):
    def setUp(self):
        super().setUp()
        try:
            from solders.keypair import Keypair
        except ModuleNotFoundError as e:
            self.skipTest(str(e))
        self.kp = Keypair()
        self.clock = PClock()
        self.rpc = Rpc(self.clock)
        raw = {"bot_id": ARM, "frozen_sha": c0.FROZEN_SHA, "root": self.tmp, "state_dir": str(Path(self.tmp) / "st"), "end_ms": PT0 + 86_400_000, "max_hold_ms": H,
               "mode": "live", "profile": c0.PROFILE, "funded_lamports": FUNDED}
        cfg = alb.Config.from_dict(raw)
        self.cfg = alb.Config(**{**cfg.__dict__, "stop_file": str(Path(self.tmp) / "STOP"), "halt_file": str(Path(self.tmp) / "HALT")})  # hermetic: nothing under /var/lib
        lp = mock.patch.object(pe, "LIVE_DIR", Path(self.tmp) / "live")
        lp.start()
        self.addCleanup(lp.stop)
        self.port = self.new_port()
        self.ex = self.port.ex
        self.fill_path = Path(self.cfg.state_dir) / "probe" / "fills.jsonl"
        self.oracle: dict[str, tuple[int, int]] = {}  # signature -> (priority decoded from the tx bytes by the TEST, lvbh of the pending entry)

    def new_port(self):
        return alb.make_live_port(self.cfg, self.rpc, self.kp, now_ms=self.clock)

    # -- steps
    def note(self, ex=None) -> str:
        """Record the oracle for the pending entry (called right after each send) and return its signature."""
        p = (ex or self.ex).state.pending[MINT]
        self.oracle[p["signature"]] = (signed_prio(p["tx_b64"]), p["lvbh"])
        return p["signature"]

    def buy_and_land(self, fee=60_000):
        ok, why = self.port.buy({"order_id": "o1", "mint": MINT, "decision_ms": self.clock(), "stake_lamports": self.cfg.stake_lamports})
        self.assertTrue(ok, why)
        sig = self.note()
        p = self.ex.state.pending[MINT]
        tok = p["q_tokens"]
        self.rpc.statuses[sig] = {"slot": 2_000, "confirmationStatus": "confirmed", "err": None}
        self.rpc.txs[sig] = meta_result(self.ex, MINT, delta=-(p["spend"] + fee + RENT), fee=fee, tok_delta=tok, ata_post=RENT, slot=2_000)
        self.rpc.token_balance = tok
        self.ex.advance_pending()
        self.assertIn(MINT, self.ex.state.open)
        return sig

    def sell(self) -> str:
        ok, why = self.port.sell({"mint": MINT}, "book_exit")
        self.assertTrue(ok, why)
        return self.note()

    def expire_pending(self):
        p = self.ex.state.pending[MINT]
        self.rpc.height = p["lvbh"] + 1
        for _ in range(3):  # first sighting, then two rechecks EXPIRY_RECHECK_MS apart (history search on, status null)
            self.ex.advance_pending()
            self.clock.t += pl.EXPIRY_RECHECK_MS
        self.assertNotIn(MINT, self.ex.state.pending)

    def rows(self):
        return [json.loads(x) for x in self.fill_path.read_text().splitlines() if x.strip()]

    def send_rows(self):
        return [r for r in self.rows() if r["kind"] in ("buy", "sell") and "landed" in r]

    def state(self, status="closed"):
        return {"orders": {"o1": {"order_id": "o1", "mint": MINT, "status": status, "stake_lamports": self.cfg.stake_lamports}}, "latches": {}}

    def recorder_trips(self):
        """What Bridge.feed_ledger does with the executor's own rows: buys and sells into the real ParityRecorder."""
        rec = ParityRecorder(Path(self.tmp) / "par")
        for kind, mint, kw in self.port.drain_ledger():
            if kind == "buy":
                rec.on_buy(mint, kw, self.clock(), self.clock() + 10_000_000)
            else:
                rec.on_sell(mint, kw, self.clock())
        return rec

    def escalated_trip(self):
        """buy (55k, lands) -> sell 0 (55k, lands FAILED on chain) -> sell 1 (55k, EXPIRES) -> sell 2 (150k, lands). Returns the signatures in order."""
        sigs = [self.buy_and_land()]
        self.port.poll()
        sigs.append(self.sell())
        land_sell(self.ex, self.rpc, proceeds=0, err=FAIL, fee=60_000)
        self.port.poll()  # the failed sell leaves the bridge's exit_reason set: sell 1 is built at once, still 55,000 after one revert
        sigs.append(self.note())
        self.expire_pending()
        self.port.poll()  # two failures: the escalated rung
        sigs.append(self.note())
        land_sell(self.ex, self.rpc, proceeds=45_000_000, fee=155_000)
        self.assertNotIn(MINT, self.ex.state.open)
        return sigs


class SignedPriorityPerSignatureTests(Harness):
    def test_every_send_row_carries_the_priority_and_height_of_its_own_signature(self):
        sigs = self.escalated_trip()
        rows = self.send_rows()
        self.assertEqual([r["signature"] for r in rows], sigs)
        self.assertEqual([self.oracle[s][0] for s in sigs], [55_000, 55_000, 55_000, 150_000])  # the bytes, decoded by the test
        for r in rows:
            prio, lvbh = self.oracle[r["signature"]]
            self.assertEqual(r["priority_lamports"], prio, r["kind"])
            self.assertEqual(r["last_valid_block_height"], lvbh, r["kind"])
        self.assertGreaterEqual(len({lv for _, lv in self.oracle.values()}), 3)  # the heights differ per signature: this is not one constant copied around
        self.assertEqual([(r["kind"], r["landed"], r.get("fail_class")) for r in rows],
                         [("buy", True, None), ("sell", False, "slippage_exceeded"), ("sell", False, "expired"), ("sell", True, None)])
        for r in rows:
            if r["landed"] or r["fail_class"] != "expired":
                self.assertEqual(r["fee_lamports"], r["priority_lamports"] + BASE_FEE)
        self.assertEqual(rows[2]["priority_lamports"], 55_000)  # the expired row records what was signed too, though it paid nothing
        self.assertNotIn("c0_signed_priority", json.dumps(rows))

    def test_ledger_rows_the_recorder_reads_carry_the_same_values(self):
        self.escalated_trip()
        led = self.port.drain_ledger()
        self.assertEqual([(k, kw["priority_lamports"], kw["last_valid_block_height"]) for k, _m, kw in led],
                         [(r["kind"], r["priority_lamports"], r["last_valid_block_height"]) for r in self.send_rows()])
        self.assertTrue(all("c0_signed_priority" not in kw for _k, _m, kw in led))

    def test_an_earlier_rung_that_lands_after_the_escalation_logs_its_own_priority(self):
        """QP #970 R2: rung A (55,000) is signed, the escalated rung B (150,000) is signed for the same mint, then A's status arrives. The old per-mint memory said
        150,000 for A (fee 60,000 != 150,000 + 5,000: a false tieout_mismatch). A is made pending again after B was signed, which is what a late status for A does."""
        self.buy_and_land()
        self.port.poll()
        sig_a = self.sell()
        p_a = copy.deepcopy(self.ex.state.pending[MINT])
        del self.ex.state.pending[MINT]
        self.ex.state.open[MINT]["sell_attempts"] = 2
        self.ex.poll_positions()
        sig_b = self.note()
        self.assertNotEqual(sig_a, sig_b)
        self.assertEqual((self.oracle[sig_a][0], self.oracle[sig_b][0]), (55_000, 150_000))
        self.ex.state.pending[MINT] = p_a
        land_sell(self.ex, self.rpc, proceeds=45_000_000, fee=60_000)
        row = [r for r in self.send_rows() if r["kind"] == "sell"][-1]
        self.assertEqual((row["signature"], row["priority_lamports"], row["fee_lamports"]), (sig_a, 55_000, 60_000))
        tie.build(self.rows(), self.state(), self.clock() + 1)  # the audit sees no mismatch

    def test_a_landing_seen_after_a_restart_logs_the_signed_priority_not_a_recomputation(self):
        """The old memory was in-process only. After a restart the base class recomputes: 345 s after the first sell the schedule says 150,000, but this rung was
        signed at 55,000 and paid 60,000."""
        self.buy_and_land()
        self.port.poll()
        sig_a = self.sell()
        self.clock.t += c0.ESCALATE_AFTER_MS + 1_000  # still pending: the stub's block height never passes its lvbh
        self.ex.save()  # the loop's next periodic save (it persists c0_first_sell_ms, set just after the write-ahead)
        port2 = self.new_port()
        self.assertEqual(port2.ex.state.pending[MINT]["signature"], sig_a)
        self.assertEqual(port2.ex._priority(MINT), 150_000)  # what a recomputation would say now
        land_sell(port2.ex, self.rpc, proceeds=45_000_000, fee=60_000)
        row = [r for r in self.send_rows() if r["kind"] == "sell"][-1]
        self.assertEqual((row["signature"], row["priority_lamports"], row["last_valid_block_height"]), (sig_a, 55_000, self.oracle[sig_a][1]))

    def test_rebroadcasts_keep_the_signature_and_the_row_is_one_per_signature(self):
        self.buy_and_land()
        self.port.poll()
        sig_a = self.sell()
        n = len(self.rpc.sent)
        for _ in range(3):
            self.clock.t += pl.REBROADCAST_MS
            self.ex.advance_pending()
        self.assertGreater(len(self.rpc.sent), n)
        self.assertEqual({signed_prio(b) for _t, b in self.rpc.sent[n:]}, {55_000})
        land_sell(self.ex, self.rpc, proceeds=45_000_000, fee=60_000)
        self.assertEqual([r["signature"] for r in self.send_rows() if r["kind"] == "sell"], [sig_a])

    def test_an_unreadable_signed_tx_logs_null_and_the_audit_refuses_the_row(self):
        self.buy_and_land()
        self.port.poll()
        self.sell()
        self.ex.state.pending[MINT]["tx_b64"] = "!not base64!"
        land_sell(self.ex, self.rpc, proceeds=45_000_000, fee=60_000)
        row = [r for r in self.send_rows() if r["kind"] == "sell"][-1]
        self.assertIsNone(row["priority_lamports"])
        with self.assertRaises(tie.TieoutMismatch):
            tie.build(self.rows(), self.state(), self.clock() + 1)

    def test_a_signed_priority_that_differs_from_the_one_asked_for_alerts_and_sends_unchanged(self):
        with mock.patch.object(alb, "signed_priority_lamports", return_value=1), contextlib.redirect_stdout(io.StringIO()) as out:
            ok, why = self.port.buy({"order_id": "o1", "mint": MINT, "decision_ms": self.clock(), "stake_lamports": self.cfg.stake_lamports})
        self.assertTrue(ok, why)
        self.assertEqual(len(self.rpc.sent), 1)
        self.assertEqual(signed_prio(self.rpc.sent[0][1]), 55_000)
        self.assertIn("c0_signed_priority_differs", out.getvalue())
        self.assertTrue(any(r.get("alert") == "c0_signed_priority_differs" for r in self.rows()))

    def test_signed_priority_lamports_reads_the_bytes(self):
        from solders.hash import Hash
        from solders.message import Message
        from solders.transaction import VersionedTransaction

        from tools import pumpswap_tx as ptx

        self.buy_and_land()
        self.assertEqual(alb.signed_priority_lamports(self.rpc.sent[0][1]), 55_000)

        def b64(ixs):
            t = VersionedTransaction(Message.new_with_blockhash(ixs, self.kp.pubkey(), Hash.default()), [self.kp])
            return base64.b64encode(bytes(t)).decode()

        self.assertEqual(alb.signed_priority_lamports(b64(ptx.compute_budget_ixs(150_000, 200_000))), 150_000)
        self.assertEqual(alb.signed_priority_lamports(b64(ptx.compute_budget_ixs(55_000, 300_000))), 55_001)  # rounded up like the runtime (183,334 x 300,000 / 1e6)
        self.assertIsNone(alb.signed_priority_lamports(b64(ptx.compute_budget_ixs(55_000, 250_000) * 2)))  # two limits and two prices: not known
        self.assertIsNone(alb.signed_priority_lamports(b64([])))  # no budget instruction
        for bad in (None, "", "!!", 5, "AAAA"):
            self.assertIsNone(alb.signed_priority_lamports(bad), bad)


class ConfirmedDropSignOffTests(Harness):
    """Ruling 2: with the height on the expired row, the real tie-out calls the expired send a confirmed drop and the day signs off."""

    def chain(self, rows):
        txs, bal, slot = [], 5_000_000_000, 100
        baseline = bal
        for r in rows:
            if r["kind"] not in ("buy", "sell") or "landed" not in r or r.get("fail_class") == "expired":
                continue  # an expired send never reached the chain
            fee = r["fee_lamports"]
            if r["kind"] == "buy" and r["landed"]:
                d, extra = -r["sol_spent_lamports"], {"kind": "buy", "sol_paid": r["spend_lamports"], "rent_paid": r["rent_charged_lamports"]}
            elif r["kind"] == "sell" and r["landed"]:
                d, extra = r["sell_net_lamports"], {"kind": "sell", "sol_received": r["sol_received_lamports"], "rent_refunded": r["rent_refunded_lamports"]}
            else:
                d, extra = -fee, {"kind": "sell", "status": "failed", "sol_received": 0}
            slot += 10
            txs.append({"signature": r["signature"], "slot": slot, "status": extra.pop("status", "ok"), "pre_balance": bal, "post_balance": bal + d,
                        "fee_lamports": BASE_FEE, "priority_fee_lamports": fee - BASE_FEE, **extra})
            bal += d
        return txs, baseline, bal, slot

    def reconcile(self, rows, doc, finalized_height, mutate=None):
        txs, baseline, bal, slot = self.chain(rows)
        ledger = {k: doc[k] for k in ato.LEDGER_KEYS}
        no_status = lambda method, params: {"value": [None] * len(params[0])}  # getSignatureStatuses with searchTransactionHistory: null for every signature
        cut, _report = fetch.cut_ledger(no_status, ledger, {t["signature"] for t in txs}, 0)
        if mutate:
            mutate(cut)
        balance = {"lamports": bal, "context_slot": slot + 5, "commitment": "finalized", "finalized_block_height": finalized_height}
        return ato.reconcile(txs, cut, {"programs_covered": list(ato.TOKEN_PROGRAMS), "accounts": []}, baseline, balance, {})

    def test_expired_send_with_its_height_and_a_null_status_is_a_confirmed_drop_and_the_day_signs_off(self):
        sigs = self.escalated_trip()
        rec = self.recorder_trips()
        rows = self.rows()
        doc = tie.build(rows, self.state(), self.clock() + 1, rec.trips)  # fee / stake audit incl. the parity trips: no false tieout_mismatch
        f = doc["bridge"]["trip_fees"][MINT]
        self.assertEqual((f["buy_fee_lamports"], f["sell_fee_lamports"], f["sell_priorities"]), (60_000, 60_000 + 155_000, [55_000, 150_000]))
        sends = {s["signature"]: s for s in doc["sends"]}
        expired = sends[sigs[2]]
        self.assertEqual((expired["status"], expired["last_valid_block_height"]), ("expired", self.oracle[sigs[2]][1]))
        fin = max(lv for _, lv in self.oracle.values()) + 1
        r = self.reconcile(rows, doc, fin)
        self.assertEqual(r["problems"], [])
        self.assertTrue(r["signed_off"])
        self.assertEqual((r["confirmed_drops"], r["pending_sends"]), (1, 0))
        self.assertEqual(r["trip_diffs"][0]["diff"], 0)

        def kinds(res):
            return [p["kind"] for p in res["problems"]]

        # without the height (what the rows looked like before this change) the expired send is pending and the day does not sign off
        def drop_height(cut):
            for s in cut["sends"]:
                s["last_valid_block_height"] = None
        r = self.reconcile(rows, doc, fin, drop_height)
        self.assertFalse(r["signed_off"])
        self.assertEqual(kinds(r), ["send_pending"])
        # the finalized height not yet past the send's height: still pending
        r = self.reconcile(rows, doc, self.oracle[sigs[2]][1])
        self.assertEqual(kinds(r), ["send_pending"])
        # a status that is not null (the tx is known to the cluster) is not a drop

        def known(cut):
            for s in cut["sends"]:
                if "status_check" in s:
                    s["status_check"]["result"] = {"slot": 1, "confirmationStatus": "processed", "err": None}
        r = self.reconcile(rows, doc, fin, known)
        self.assertEqual(kinds(r), ["send_pending"])

    def test_an_expired_buy_carries_its_height_and_is_a_confirmed_drop_too(self):
        ok, why = self.port.buy({"order_id": "o1", "mint": MINT, "decision_ms": self.clock(), "stake_lamports": self.cfg.stake_lamports})
        self.assertTrue(ok, why)
        sig = self.note()
        self.expire_pending()
        (row,) = self.send_rows()
        self.assertEqual((row["kind"], row["landed"], row["fail_class"], row["signature"]), ("buy", False, "expired", sig))
        self.assertEqual((row["last_valid_block_height"], row["priority_lamports"]), (self.oracle[sig][1], 55_000))
        doc = tie.build(self.rows(), {"orders": {"o1": {"mint": MINT, "stake_lamports": self.cfg.stake_lamports, "status": "closed"}}, "latches": {}}, self.clock() + 1)
        r = self.reconcile(self.rows(), doc, self.oracle[sig][1] + 1)
        self.assertEqual((r["problems"], r["signed_off"], r["confirmed_drops"]), ([], True, 1))


class ReEntryAndTripKeyTests(Harness):
    """QP #970 N2 (fees and stakes are keyed by mint) and N4 (a mint with fill rows but no parity trip)."""

    def test_n2_a_mint_cannot_be_bought_twice_after_a_close_or_after_an_expiry(self):
        self.escalated_trip()
        self.assertEqual(self.port.buy({"order_id": "o2", "mint": MINT, "decision_ms": self.clock(), "stake_lamports": self.cfg.stake_lamports}), (False, "already_bought"))
        self.assertIn(MINT, self.ex.state.bought)

    def test_n2_an_expired_buy_still_blocks_the_mint(self):
        ok, why = self.port.buy({"order_id": "o1", "mint": MINT, "decision_ms": self.clock(), "stake_lamports": self.cfg.stake_lamports})
        self.assertTrue(ok, why)
        self.expire_pending()
        self.assertEqual(self.port.buy({"order_id": "o2", "mint": MINT, "decision_ms": self.clock(), "stake_lamports": self.cfg.stake_lamports}), (False, "already_bought"))
        port2 = self.new_port()  # and a restart keeps it (state.bought is persisted)
        self.assertEqual(port2.buy({"order_id": "o3", "mint": MINT, "decision_ms": self.clock(), "stake_lamports": self.cfg.stake_lamports}), (False, "already_bought"))

    def test_n2_two_landed_buys_under_one_mint_are_refused_by_the_exporter(self):
        self.escalated_trip()
        rows = self.rows()
        buy = [r for r in rows if r["kind"] == "buy"][0]
        again = {**buy, "signature": "second-buy-signature", "ts_ms": buy["ts_ms"] + 1}
        with self.assertRaises(atl.BadLedger):
            tie.build(rows + [again], self.state(), self.clock() + 1)

    def test_n4_every_landed_buy_that_holds_a_position_gets_a_parity_trip(self):
        self.buy_and_land()
        (kind, mint, kw), = [x for x in self.port.drain_ledger() if x[0] == "buy"]

        n = iter(range(100))

        def run(**over):
            rec = ParityRecorder(Path(self.tmp) / f"par-{next(n)}")  # a fresh recorder (and trips file) per case
            err = rec.on_buy(mint, {**kw, **over}, self.clock(), self.clock() + 10_000_000)
            return rec, err

        rec, err = run()
        self.assertEqual((err, sorted(rec.trips)), (None, [MINT]))
        self.assertEqual(rec.trips[MINT]["buy_fee_lamports"], 60_000)
        rec, err = run(tokens_received=None)  # unreadable: a flagged trip, and the caller (Bridge.feed_ledger) latches landed_buy_unreadable
        self.assertEqual((err, rec.trips[MINT].get("unreadable")), ("landed_buy_unreadable", True))
        rec, err = run(tokens_source="quote_estimate")
        self.assertEqual((err, rec.trips[MINT].get("unreadable")), ("landed_buy_unreadable", True))
        rec, err = run(tokens_received=0)  # the one landed buy with no trip: SOL gone, no position
        self.assertEqual((err, rec.trips), (None, {}))

    def test_n4_a_landed_zero_token_buy_has_no_trip_and_the_tie_out_does_not_sign_off(self):
        self.buy_and_land()
        rows = [{**r, "tokens_received": 0} for r in self.rows() if r["kind"] == "buy"]
        doc = tie.build(rows, self.state(), self.clock() + 1)
        self.assertEqual([(t["closed"], t.get("zero_tokens"), t["sell_sig"]) for t in doc["trips"]], [(True, True, None)])
        txs, baseline, bal, slot = ConfirmedDropSignOffTests.chain(self, rows)
        balance = {"lamports": bal, "context_slot": slot + 5, "commitment": "finalized", "finalized_block_height": 10_000}
        r = ato.reconcile(txs, {k: doc[k] for k in ato.LEDGER_KEYS}, {"programs_covered": list(ato.TOKEN_PROGRAMS), "accounts": []}, baseline, balance, {})
        self.assertFalse(r["signed_off"])
        self.assertIn("closed_trip_signature_missing", [p["kind"] for p in r["problems"]])


class PinsUnmovedTests(unittest.TestCase):
    def test_the_sell_builder_pin_still_matches_and_probe_live_is_unedited_here(self):
        """The change sits in the bridge's executor subclass: SELL_BUILDER_PIN (incl. probe_live._start_sell / _sign / _send and the BridgeExecutor poll_positions /
        refresh_warm sources) is recomputed from the code on disk and matches."""
        try:
            self.assertIsNone(c0.sell_pin_problem())
        except ModuleNotFoundError as e:
            self.skipTest(str(e))


if __name__ == "__main__":
    unittest.main()
