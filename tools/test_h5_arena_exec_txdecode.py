"""Built transactions of the arena executor, decoded from their wire bytes (reviewer on #793): compute-budget price and limit, fee payer and signer, close destination
and authority, swap amount and minimum out. For the default books and for a p155 book (155,000 priority + 5,000 base = 160,000 per send). Fake RPC, throwaway Keypair."""

from __future__ import annotations

import base64
import dataclasses
from unittest import mock

from solders.transaction import VersionedTransaction

from tools import h5_arena_executor as ax
from tools import h5_executor as h
from tools import probe_live as pl
from tools import pumpswap_tx as tx
from tools.test_h5_arena_executor import A2, ACase, AEnv, write_canary_record
from tools.test_h5_executor import Q, STAKE, buy_args, sell_args
from tools.test_probe_executor import BASE0
from tools.test_h5_executor_rules import quote_out

NEW = "h5v1-p155-txdecode"
BASE_FEE = 5_000


def wire(b64: str) -> dict:
    """Everything below is read from the bytes of the signed transaction, not from the executor's own bookkeeping."""
    t = VersionedTransaction.from_bytes(base64.b64decode(b64))
    msg = t.message
    keys = list(msg.account_keys)
    out = {"payer": keys[0], "n_sigs": msg.header.num_required_signatures, "signers": keys[:msg.header.num_required_signatures], "n_signatures": len(t.signatures),
           "limit": None, "price": None, "swap": None, "closes": [], "ixs": []}
    for ix in msg.instructions:
        pid, data, acc = keys[ix.program_id_index], bytes(ix.data), [keys[a] for a in ix.accounts]
        out["ixs"].append((pid, data[:1]))
        if pid == pl.COMPUTE_BUDGET_PROGRAM and data[:1] == b"\x02":
            out["limit"] = int.from_bytes(data[1:], "little")
        elif pid == pl.COMPUTE_BUDGET_PROGRAM and data[:1] == b"\x03":
            out["price"] = int.from_bytes(data[1:], "little")
        elif pid == tx.PUMPSWAP_PROGRAM:
            out["swap"] = data
        elif pid in (tx.TOKEN_PROGRAM, tx.TOKEN_2022_PROGRAM) and data == b"\x09":
            out["closes"].append({"program": pid, "account": acc[0], "destination": acc[1], "authority": acc[2]})
    out["priority_lamports"] = -(-out["price"] * out["limit"] // 1_000_000)
    out["total_fee"] = out["priority_lamports"] + BASE_FEE * out["n_sigs"]
    return out


class TxDecodeTests(ACase):
    def live_env(self, **kw):
        write_canary_record(self.tmp / "repo", A2, NEW)
        (self.tmp / "FREEZE.md").write_text("x")
        kw.setdefault("book_id", A2)
        kw.setdefault("funded_lamports", 500_000_000)
        e = AEnv(self.tmp, live=True, replay_only=True, end_ms=ax.SEAL_END_MS, **kw)
        e.ex.feed_verified, e.ex._start_ms, e.ex.slices_taken = True, 0, 1
        e.ex.abook = dataclasses.replace(e.ex.abook, clock_start_ms=0)
        return e

    def run_trip(self, **kw):
        """Open a position, send the timed sell; returns (env, position, wire(buy), wire(sell))."""
        with mock.patch.object(ax, "LIVE_BUILT", True):
            e = self.live_env(**kw)
            pos = dict(e.open_position())
            plan = e.plan()
            e.at_slot(plan["send_slot"])
            sent = [b64 for _t, b64 in e.rpc.sent]
        self.assertEqual(len(sent), 2)
        return e, pos, wire(sent[0]), wire(sent[1])

    def check_common(self, e, pos, buy, sell, prio):
        user = e.ex.user
        for w in (buy, sell):
            self.assertEqual((w["payer"], w["n_sigs"], w["signers"], w["n_signatures"]), (user, 1, [user], 1))  # fee payer is the only signer
            self.assertEqual(w["priority_lamports"], prio)
            self.assertEqual(w["total_fee"], prio + BASE_FEE)
            self.assertEqual(w["limit"], e.ex.h5.buy_cu_limit if w is buy else e.ex.h5.sell_cu_limit)
        # buy: spends the stake, minimum out from the entry terms
        spend, min_out = buy_args(buy)
        self.assertEqual((spend, min_out), (STAKE, h.entry_terms(Q, BASE0, STAKE, 1500)["min_out"]))
        self.assertEqual([(c["program"], c["destination"], c["authority"]) for c in buy["closes"]], [(tx.TOKEN_PROGRAM, user, user)])  # only the WSOL account
        # sell: all the tokens, minimum out = 0.85 x the quote (ladder level 0), WSOL then base account closed to the user, authority the user
        tokens, smin = sell_args(sell)
        self.assertEqual(tokens, pos["tokens"])
        self.assertEqual(smin, quote_out(tokens) * 8500 // 10_000)
        self.assertEqual(len(sell["closes"]), 2)
        wsol, base = sell["closes"]
        self.assertEqual((wsol["program"], wsol["destination"], wsol["authority"]), (tx.TOKEN_PROGRAM, user, user))
        self.assertEqual(str(base["account"]), pos["base_ata"])
        self.assertEqual((str(base["program"]), base["destination"], base["authority"]), (pos["base_tp"], user, user))

    def test_default_book_55k_priority_55k_sell(self):
        e, pos, buy, sell = self.run_trip()
        self.check_common(e, pos, buy, sell, 55_000)

    def test_p155_book_155k_priority_160k_total_per_send(self):
        nb = dataclasses.replace(ax.BOOKS[A2], bot_id=NEW, priority=ax.PRIORITY_P155)
        with mock.patch.dict(ax.BOOKS, {NEW: nb}), mock.patch.object(ax, "FEED_NOTE_BOOKS", ax.FEED_NOTE_BOOKS | {NEW}):
            e, pos, buy, sell = self.run_trip(book_id=NEW, priority_pin=ax.PRIORITY_P155.as_dict())
        self.check_common(e, pos, buy, sell, 155_000)
        self.assertEqual((buy["total_fee"], sell["total_fee"]), (160_000, 160_000))
