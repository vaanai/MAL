"""Offline tests for tools/arena_tieout_fetch.py. Recorded JSON only: five REAL public PumpSwap txs (tools/fixtures/tieout_fetch, fetched once from public RPC for
traders that are not MAL wallets) plus small hand-built synthetic txs (transfer / failed / ata close). The RPC is a fake. No network, no key, no MAL address."""
from __future__ import annotations

import copy
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from tools import arena_tieout as T
from tools import arena_tieout_fetch as F

FIX = Path(__file__).parent / "fixtures" / "tieout_fetch"
B58 = F.B58


def real(label: str) -> tuple[dict, str]:
    return json.loads((FIX / f"{label}.json").read_text()), (FIX / f"{label}.wallet").read_text().strip()


def b58encode(b: bytes) -> str:
    n = int.from_bytes(b, "big")
    s = ""
    while n:
        n, r = divmod(n, 58)
        s = B58[r] + s
    return "1" * (len(b) - len(b.lstrip(b"\x00"))) + s


W = "6X6dsDsb46bbeciPaydE27Gg9iC4ySjrHxtsj8BV78g6"  # a public trader from the sell_a fixture
OTHER = "9yRKoQykdFdWDjPK78NqTsm7kcoWSYcFEmhHCaFxyWfh"
ATA = "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL"


def synth(sig, slot, keys, ixs, pre, post, fee=5000, err=None, nsig=1, pre_tok=(), post_tok=(), block_time=1_792_800_000):
    """ixs: [(program_key_index, [account indices], data bytes)]"""
    return {"slot": slot, "blockTime": block_time, "transaction": {"signatures": [sig], "message": {
        "header": {"numRequiredSignatures": nsig}, "accountKeys": keys,
        "instructions": [{"programIdIndex": p, "accounts": a, "data": b58encode(d)} for p, a, d in ixs]}},
        "meta": {"err": err, "fee": fee, "preBalances": pre, "postBalances": post, "preTokenBalances": list(pre_tok), "postTokenBalances": list(post_tok)}}


def transfer_tx(sig, slot, src, dst, lamports, wallet_is_src=True, pre=1_000_000_000, **kw):
    keys = [src, dst, F.SYSTEM_PROGRAM]
    data = b"\x02\x00\x00\x00" + lamports.to_bytes(8, "little")
    if wallet_is_src:
        pre_b, post_b = [pre, 0, 1], [pre - lamports - 5000, lamports, 1]
    else:
        pre_b, post_b = [10**10, pre, 1], [10**10 - lamports - 5000, pre + lamports, 1]
    return synth(sig, slot, keys, [(2, [0, 1], data)], pre_b, post_b, **kw)


class ClassifyReal(unittest.TestCase):
    def test_real_txs_reconcile_to_the_lamport_independently_of_the_wallet_delta(self):
        for label, kind in (("buy_exact_quote_in_a", "buy"), ("buy_exact_quote_in_b", "buy"), ("legacy_buy_aug2026", "buy"),
                            ("sell_a", "sell"), ("sell_b_token2022_or_legacy", "sell")):
            tx, w = real(label)
            r = F.classify_tx(tx, w)
            self.assertEqual(r["kind"], kind, label)
            d = r["post_balance"] - r["pre_balance"]
            fee = r["fee_lamports"] + r["priority_fee_lamports"]
            if kind == "buy":
                self.assertEqual(d, -(fee + r["sol_paid"] + r["rent_paid"]), label)
            else:
                self.assertEqual(d, r["sol_received"] + r["rent_refunded"] - fee, label)
            self.assertEqual(r["fee_lamports"], 5000)
            self.assertEqual(r["fee_lamports"] + r["priority_fee_lamports"], tx["meta"]["fee"])

    def test_sell_that_closes_the_ata_has_rent_refunded(self):
        tx, w = real("sell_b_token2022_or_legacy")
        self.assertGreater(F.classify_tx(tx, w)["rent_refunded"], 0)

    def test_buy_that_creates_the_base_ata_has_rent_paid(self):
        tx, w = real("buy_exact_quote_in_a")
        mod = copy.deepcopy(tx)
        idx = [b["accountIndex"] for b in mod["meta"]["postTokenBalances"] if b["owner"] == w and b["mint"] != F.WSOL][0]
        rent = mod["meta"]["postBalances"][idx]
        mod["meta"]["preBalances"][idx] = 0  # as if the ATA did not exist before
        mod["meta"]["preTokenBalances"] = [b for b in mod["meta"]["preTokenBalances"] if b["accountIndex"] != idx]
        self.assertEqual(F.classify_tx(mod, w)["rent_paid"], rent)

    def test_side_mismatch_is_other(self):
        tx, w = real("sell_a")
        bad = copy.deepcopy(tx)
        # make the wallet's base-token balance rise in a SELL: swap pre and post token balances
        bad["meta"]["preTokenBalances"], bad["meta"]["postTokenBalances"] = tx["meta"]["postTokenBalances"], tx["meta"]["preTokenBalances"]
        r = F.classify_tx(bad, w)
        self.assertEqual(r["kind"], "other")
        self.assertTrue(r["detail"].startswith("side_mismatch"))

    def test_unknown_discriminator_and_program_are_other(self):
        tx, w = real("sell_a")
        keys = F._keys(tx)
        bad = copy.deepcopy(tx)
        for ix in bad["transaction"]["message"]["instructions"]:
            if keys[ix["programIdIndex"]] == F.PUMPSWAP:
                ix["data"] = b58encode(b"\x01" * 8 + b"\x00" * 16)
        self.assertEqual(F.classify_tx(bad, w)["detail"], "unknown_pumpswap_discriminator")
        bad2 = copy.deepcopy(tx)
        bad2["transaction"]["message"]["accountKeys"][-1] = OTHER  # the last key is a program of one instruction or a readonly account
        r = F.classify_tx(bad2, w)
        self.assertIn(r["kind"], ("other", "buy", "sell"))  # (only to prove it does not crash; the unknown-program case is next)

    def test_unknown_program_in_swap_tx(self):
        tx, w = real("sell_a")
        bad = copy.deepcopy(tx)
        keys = bad["transaction"]["message"]["accountKeys"]
        keys.append(OTHER)
        bad["transaction"]["message"]["instructions"].append({"programIdIndex": len(keys) - 1, "accounts": [], "data": ""})
        r = F.classify_tx(bad, w)
        self.assertEqual((r["kind"], r["detail"]), ("other", "unknown_program:" + OTHER))

    def test_failed_swap_keeps_kind_and_zero_fields(self):
        tx, w = real("sell_a")
        bad = copy.deepcopy(tx)
        bad["meta"]["err"] = {"InstructionError": [3, {"Custom": 6004}]}
        r = F.classify_tx(bad, w)
        self.assertEqual((r["status"], r["kind"], r["sol_received"], r["rent_refunded"]), ("failed", "sell", 0, 0))


class ClassifySynthetic(unittest.TestCase):
    def test_outbound_and_inbound_transfer(self):
        r = F.classify_tx(transfer_tx("s1", 5, W, OTHER, 7_000_000), W)
        self.assertEqual((r["kind"], r["counterparty"], r["fee_lamports"], r["priority_fee_lamports"]), ("transfer", OTHER, 5000, 0))
        self.assertEqual(r["post_balance"] - r["pre_balance"], -7_005_000)
        r = F.classify_tx(transfer_tx("s2", 6, OTHER, W, 3_000_000, wallet_is_src=False), W)
        self.assertEqual((r["kind"], r["counterparty"], r["fee_lamports"]), ("transfer", OTHER, 0))  # someone else paid the fee
        self.assertEqual(r["post_balance"] - r["pre_balance"], 3_000_000)

    def test_priority_fee_is_meta_fee_minus_base(self):
        r = F.classify_tx(transfer_tx("s3", 7, W, OTHER, 1, fee=5000 + 155_000), W)
        self.assertEqual((r["fee_lamports"], r["priority_fee_lamports"]), (5000, 155_000))

    def test_transfer_not_touching_wallet_and_double_transfer_are_other(self):
        tx = transfer_tx("s4", 8, OTHER, "11111111111111111111111111111112", 5)
        with self.assertRaises(F.FetchError):
            F.classify_tx(tx, W)  # the wallet is not even in the keys
        keys = [W, OTHER, F.SYSTEM_PROGRAM]
        d = b"\x02\x00\x00\x00" + (5).to_bytes(8, "little")
        two = synth("s5", 9, keys, [(2, [0, 1], d), (2, [0, 1], d)], [100, 0, 1], [80, 10, 1])
        self.assertEqual(F.classify_tx(two, W)["kind"], "other")

    def test_ata_close_only_tx_is_other_with_detail_and_refund(self):
        keys = [W, "AtaAcct1111111111111111111111111111111111111", F.TOKEN_PROGRAM]
        close = (2, [1, 0, 0], b"\x09")
        pre_tok = [{"accountIndex": 1, "mint": "MintX", "owner": W, "uiTokenAmount": {"amount": "0"}}]
        r = F.classify_tx(synth("s6", 10, keys, [close], [1000, 2_039_280, 1], [1000 + 2_039_280 - 5000, 0, 1], pre_tok=pre_tok), W)
        self.assertEqual((r["kind"], r["detail"], r["rent_refunded"]), ("other", "ata_close", 2_039_280))

    def test_b58_roundtrip(self):
        for b in (b"\x00\x00\x01", bytes(range(40)), b"\x02\x00\x00\x00" + (5).to_bytes(8, "little")):
            self.assertEqual(F.b58decode(b58encode(b)), b)


class Ordering(unittest.TestCase):
    def test_same_slot_in_balance_chain_order(self):
        rows = [{"signature": "c", "slot": 5, "pre_balance": 70, "post_balance": 60}, {"signature": "a", "slot": 5, "pre_balance": 100, "post_balance": 90},
                {"signature": "b", "slot": 5, "pre_balance": 90, "post_balance": 70}, {"signature": "z", "slot": 4, "pre_balance": 0, "post_balance": 100}]
        self.assertEqual([r["signature"] for r in F.chain_order(rows)], ["z", "a", "b", "c"])

    def test_ambiguous_keeps_input_order(self):
        rows = [{"signature": "x", "slot": 5, "pre_balance": 10, "post_balance": 10}, {"signature": "y", "slot": 5, "pre_balance": 10, "post_balance": 10}]
        self.assertEqual([r["signature"] for r in F.chain_order(rows)], ["x", "y"])


class FakeChain:
    """A fake RPC for one wallet. `txs` maps signature -> getTransaction result; `sigs` is the newest-first signature list."""

    def __init__(self, wallet, txs, balance, height=1000, accounts=None, statuses=None):
        self.wallet, self.txs, self.balance, self.height, self.statuses = wallet, txs, balance, height, statuses or {}
        self.sigs = sorted(({"signature": s, "slot": t["slot"], "blockTime": t["blockTime"], "err": t["meta"]["err"]} for s, t in txs.items()), key=lambda x: -x["slot"])
        self.accounts = accounts or {}
        self.calls: dict[str, int] = {}
        self.log: list[tuple[str, list]] = []

    def __call__(self, method, params):
        self.calls[method] = self.calls.get(method, 0) + 1
        self.log.append((method, params))
        if method == "getSignaturesForAddress":
            cfg = params[1]
            assert cfg["commitment"] == "finalized"
            lim, before = cfg["limit"], cfg.get("before")
            rest = self.sigs
            if before:
                rest = rest[[s["signature"] for s in rest].index(before) + 1:]
            return rest[:lim]
        if method == "getTransaction":
            assert params[1]["commitment"] == "finalized" and params[1]["maxSupportedTransactionVersion"] == 0
            return self.txs.get(params[0])
        if method == "getBalance":
            assert params[1] == {"commitment": "finalized"}
            return {"context": {"slot": max([900, *(t["slot"] for t in self.txs.values() if t)]) + 5}, "value": self.balance}
        if method == "getBlockHeight":
            return self.height
        if method == "getTokenAccountsByOwner":
            assert params[2]["commitment"] == "finalized"
            return {"context": {"slot": 901}, "value": self.accounts.get(params[1]["programId"], [])}
        if method == "getSignatureStatuses":
            assert params[1] == {"searchTransactionHistory": True}
            return {"context": {"slot": 902}, "value": [self.statuses.get(s) for s in params[0]]}
        raise AssertionError(method)


NOW = lambda: datetime(2026, 10, 24, 0, 5, 0, tzinfo=timezone.utc)  # noqa: E731
DAY = "2026-10-23"
AFTER = 0


class EndToEnd(unittest.TestCase):
    def run_sell(self, ledger_extra=None, statuses=None, accounts=None):
        tx, w = real("sell_a")
        tx = copy.deepcopy(tx)
        tx["blockTime"] = 1_792_800_000  # inside 2026-10-23
        sig = tx["transaction"]["signatures"][0]
        chain = FakeChain(w, {sig: tx}, tx["meta"]["postBalances"][F._keys(tx).index(w)], accounts=accounts, statuses=statuses)
        ledger = {"sends": [{"signature": sig, "side": "sell", "status": "landed"}, *(ledger_extra or [])], "trips": [], "open_mints": []}
        d = Path(tempfile.mkdtemp())
        m = F.run(chain, w, DAY, ledger, d, after_slot=AFTER, now=NOW)
        return m, d, chain, w, tx

    def test_outputs_feed_arena_tieout_and_sign_off(self):
        m, d, chain, w, tx = self.run_sell()
        load = lambda n: json.loads((d / n).read_text())  # noqa: E731
        res = T.reconcile(load("txs.json"), load("ledger.json"), load("tokens.json"), tx["meta"]["preBalances"][F._keys(tx).index(w)], load("balance.json"),
                          {"deposit_from": [], "withdraw_to": []})
        self.assertTrue(res["signed_off"], res["problems"])
        self.assertEqual(res["split"]["sells"], 626598)
        self.assertEqual(m["txs"], 1)
        self.assertTrue(m["balance_context_at_or_after_last_tx"])
        self.assertEqual(m["files_sha256"].keys(), {"txs.json", "balance.json", "tokens.json", "ledger.json"})

    def test_balance_read_is_finalized_with_height_and_both_token_programs(self):
        m, d, chain, w, tx = self.run_sell()
        bal = json.loads((d / "balance.json").read_text())
        self.assertEqual(bal, {"lamports": bal["lamports"], "context_slot": bal["context_slot"], "commitment": "finalized", "finalized_block_height": 1000})
        progs = [p[1]["programId"] for meth, p in chain.log if meth == "getTokenAccountsByOwner"]
        self.assertEqual(progs, list(F.TOKEN_PROGRAMS))
        self.assertEqual(json.loads((d / "tokens.json").read_text())["programs_covered"], list(F.TOKEN_PROGRAMS))
        order = [meth for meth, _ in chain.log]
        self.assertGreater(order.index("getBalance"), order.index("getTransaction"))  # balance after the tx list: its slot is at or after the last tx

    def test_send_not_on_chain_gets_status_check_and_null_confirms_with_tieout(self):
        extra = {"signature": "dropped1", "side": "buy", "status": "dropped", "last_valid_block_height": 500}
        m, d, chain, w, tx = self.run_sell([extra], statuses={})
        led = json.loads((d / "ledger.json").read_text())
        s = [x for x in led["sends"] if x["signature"] == "dropped1"][0]
        self.assertEqual(s["status_check"], {"search_transaction_history": True, "result": None})
        self.assertNotIn("status_check", led["sends"][0])  # on chain: untouched
        self.assertEqual(m["sends_checked_not_on_chain"], 1)
        txs, tok, bal = (json.loads((d / n).read_text()) for n in ("txs.json", "tokens.json", "balance.json"))
        res = T.reconcile(txs, led, tok, txs[0]["pre_balance"], bal, {"deposit_from": [], "withdraw_to": []})
        self.assertEqual(res["confirmed_drops"], 1)
        self.assertTrue(res["signed_off"], res["problems"])

    def test_status_that_exists_is_recorded_so_tieout_keeps_it_pending(self):
        extra = {"signature": "late1", "side": "buy", "status": "dropped", "last_valid_block_height": 500}
        m, d, *_ = self.run_sell([extra], statuses={"late1": {"slot": 1, "confirmationStatus": "finalized", "err": None}})
        led = json.loads((d / "ledger.json").read_text())
        s = [x for x in led["sends"] if x["signature"] == "late1"][0]
        self.assertEqual(s["status_check"]["result"]["confirmationStatus"], "finalized")
        txs, tok, bal = (json.loads((d / n).read_text()) for n in ("txs.json", "tokens.json", "balance.json"))
        res = T.reconcile(txs, led, tok, txs[0]["pre_balance"], bal, {"deposit_from": [], "withdraw_to": []})
        self.assertFalse(res["signed_off"])

    def test_token_accounts_zero_amount_kept(self):
        acc = {F.TOKEN_PROGRAM: [{"pubkey": "TA1", "account": {"data": {"parsed": {"info": {"mint": "M1", "tokenAmount": {"amount": "0"}}}}}}],
               F.TOKEN_2022_PROGRAM: [{"pubkey": "TA2", "account": {"data": {"parsed": {"info": {"mint": "M2", "tokenAmount": {"amount": "5"}}}}}}]}
        m, d, *_ = self.run_sell(accounts=acc)
        tok = json.loads((d / "tokens.json").read_text())
        self.assertEqual([(a["token_account"], a["amount"]) for a in tok["accounts"]], [("TA1", "0"), ("TA2", "5")])

    def test_refuses_overwrite_and_bad_ledger_and_null_tx(self):
        m, d, chain, w, tx = self.run_sell()
        with self.assertRaises(FileExistsError):
            F.run(chain, w, DAY, {"sends": [], "trips": [], "open_mints": []}, d, after_slot=AFTER, now=NOW)
        with self.assertRaises(F.FetchError):
            F.run(chain, w, DAY, {"sends": []}, Path(tempfile.mkdtemp()), after_slot=AFTER, now=NOW)
        sig = tx["transaction"]["signatures"][0]
        chain.txs[sig] = None
        with self.assertRaises(F.FetchError):
            F.run(chain, w, DAY, {"sends": [], "trips": [], "open_mints": []}, Path(tempfile.mkdtemp()), after_slot=AFTER, now=NOW)

    def test_wallet_must_be_a_public_key(self):
        with self.assertRaises(ValueError):
            F.run(FakeChain("x", {}, 0), "not-a-key", DAY, {"sends": [], "trips": [], "open_mints": []}, Path(tempfile.mkdtemp()), after_slot=AFTER, now=NOW)

    def test_empty_day_still_writes_all_files(self):
        d = Path(tempfile.mkdtemp())
        m = F.run(FakeChain(W, {}, 123), W, DAY, {"sends": [], "trips": [], "open_mints": []}, d, after_slot=AFTER, now=NOW)
        self.assertEqual(m["txs"], 0)
        self.assertEqual(json.loads((d / "txs.json").read_text()), [])


class Paging(unittest.TestCase):
    def mk(self, n=2500):
        txs = {}
        for i in range(n):  # newest first by slot: slot 10_000 - i
            txs[f"sig{i:05d}"] = synth(f"sig{i:05d}", 10_000 - i, [W, OTHER, F.SYSTEM_PROGRAM], [], [1, 1, 1], [1, 1, 1])
        return txs

    def test_pages_until_at_or_before_after_slot_and_drops_them(self):
        chain = FakeChain(W, self.mk(), 1)
        got = F.page_signatures(chain, W, 10_000 - 2200)  # slots 7800..: i <= 2199 are strictly after
        self.assertEqual(len(got), 2200)
        self.assertEqual(chain.calls["getSignaturesForAddress"], 3)
        self.assertTrue(all(g["slot"] > 7800 for g in got))

    def test_window_starts_strictly_after_the_slot_not_at_midnight(self):
        # a tx with blockTime long before 00:00Z of the day but slot after the previous sign-off is IN; the slot of the baseline itself is OUT
        txs = {"old": synth("old", 100, [W, OTHER, F.SYSTEM_PROGRAM], [], [1, 1, 1], [1, 1, 1], block_time=1),
               "base": synth("base", 200, [W, OTHER, F.SYSTEM_PROGRAM], [], [1, 1, 1], [1, 1, 1], block_time=1),
               "new": synth("new", 201, [W, OTHER, F.SYSTEM_PROGRAM], [], [1, 1, 1], [1, 1, 1], block_time=1)}
        chain = FakeChain(W, txs, 1)
        self.assertEqual([g["signature"] for g in F.page_signatures(chain, W, 200)], ["new"])
        rows = F.fetch_txs(chain, W, F.page_signatures(chain, W, 200), 200)
        self.assertEqual([r["signature"] for r in rows], ["new"])

    def test_after_slot_required_and_validated(self):
        for bad in (-1, "5", None, True):
            with self.assertRaises(F.FetchError):
                F.run(FakeChain(W, {}, 1), W, DAY, {"sends": [], "trips": [], "open_mints": []}, Path(tempfile.mkdtemp()), after_slot=bad, now=NOW)

    def test_manifest_records_window(self):
        m = F.run(FakeChain(W, {}, 1), W, DAY, {"sends": [], "trips": [], "open_mints": []}, Path(tempfile.mkdtemp()), after_slot=77, now=NOW)
        self.assertEqual((m["window_after_slot"], m["day_label"]), (77, DAY))


class LedgerCut(unittest.TestCase):
    """The ledger is cumulative; the fetch window is (after_slot, now]. Chain evidence (finalized status slot) decides what is earlier."""

    def run_cut(self, ledger, statuses, after=500, in_window=("w1",)):
        txs = {s: synth(s, 600, [W, OTHER, F.SYSTEM_PROGRAM], [], [1, 1, 1], [1, 1, 1]) for s in in_window}
        chain = FakeChain(W, txs, 1, statuses=statuses)
        d = Path(tempfile.mkdtemp())
        m = F.run(chain, W, DAY, ledger, d, after_slot=after, now=NOW)
        return m, json.loads((d / "ledger.json").read_text()), chain

    def st(self, slot):
        return {"slot": slot, "confirmationStatus": "finalized", "err": None}

    def test_earlier_sends_removed_window_and_unknown_kept(self):
        ledger = {"sends": [{"signature": "w1", "side": "buy", "status": "landed"}, {"signature": "e1", "side": "buy", "status": "landed"},
                            {"signature": "u1", "side": "buy", "status": "landed"}, {"signature": "d1", "side": "buy", "status": "dropped", "last_valid_block_height": 5}],
                  "trips": [], "open_mints": ["M"]}
        m, led, _ = self.run_cut(ledger, {"e1": self.st(499), "d1": None})
        self.assertEqual([s["signature"] for s in led["sends"]], ["w1", "u1", "d1"])
        self.assertEqual(m["cut_sends"], 1)
        self.assertEqual(led["open_mints"], ["M"])
        by = {s["signature"]: s for s in led["sends"]}
        self.assertNotIn("status_check", by["w1"])
        self.assertIsNone(by["u1"]["status_check"]["result"])  # unknown stays, the tie-out halts on a landed send missing on chain
        self.assertEqual(m["sends_checked_not_on_chain"], 2)

    def test_slot_equal_to_after_slot_is_earlier_and_slot_after_is_kept(self):
        ledger = {"sends": [{"signature": "e1", "side": "buy", "status": "landed"}, {"signature": "n1", "side": "buy", "status": "landed"}], "trips": [], "open_mints": []}
        m, led, _ = self.run_cut(ledger, {"e1": self.st(500), "n1": self.st(501)}, in_window=())
        self.assertEqual([s["signature"] for s in led["sends"]], ["n1"])  # landed after the signature list: kept, tie-out halts, retry

    def test_trips_cut_straddle_and_window(self):
        trips = [{"trip_id": "T_old", "buy_sig": "eb", "sell_sig": "es", "closed": True, "realized_lamports": 1},
                 {"trip_id": "T_straddle", "buy_sig": "eb2", "sell_sig": "w1", "closed": True, "realized_lamports": 2},
                 {"trip_id": "T_win", "buy_sig": "w2", "sell_sig": "w1", "closed": True, "realized_lamports": 3},
                 {"trip_id": "T_unknown", "buy_sig": "nb", "sell_sig": "w1", "closed": True, "realized_lamports": 4},
                 {"trip_id": "T_open", "buy_sig": "eb3", "closed": False}]
        ledger = {"sends": [], "trips": trips, "open_mints": []}
        m, led, _ = self.run_cut(ledger, {"eb": self.st(10), "es": self.st(20), "eb2": self.st(30), "eb3": self.st(40)}, in_window=("w1", "w2"))
        self.assertEqual([t["trip_id"] for t in led["trips"]], ["T_win", "T_unknown", "T_open"])
        self.assertEqual(m["cut_trips"], ["T_old"])
        self.assertEqual([x["trip_id"] for x in m["straddling_trips"]], ["T_straddle"])
        self.assertEqual(m["straddling_trips"][0]["legs"], {"buy_sig": "earlier", "sell_sig": "window"})

    def test_tieout_no_false_halt_on_cumulative_ledger_and_still_fails_closed(self):
        tx, w = real("sell_a")
        tx = copy.deepcopy(tx)
        tx["slot"], tx["blockTime"] = 600, 1_792_800_000
        sig = tx["transaction"]["signatures"][0]
        chain = FakeChain(w, {sig: tx}, tx["meta"]["postBalances"][F._keys(tx).index(w)], statuses={"old_buy": self.st(100), "old_sell": self.st(120)})
        ledger = {"sends": [{"signature": sig, "side": "sell", "status": "landed"}, {"signature": "old_buy", "side": "buy", "status": "landed"},
                            {"signature": "old_sell", "side": "sell", "status": "landed"}],
                  "trips": [{"trip_id": "T0", "buy_sig": "old_buy", "sell_sig": "old_sell", "closed": True, "realized_lamports": 7}], "open_mints": []}
        d = Path(tempfile.mkdtemp())
        F.run(chain, w, DAY, ledger, d, after_slot=500, now=NOW)
        load = lambda n: json.loads((d / n).read_text())  # noqa: E731
        base = tx["meta"]["preBalances"][F._keys(tx).index(w)]
        res = T.reconcile(load("txs.json"), load("ledger.json"), load("tokens.json"), base, load("balance.json"), {"deposit_from": [], "withdraw_to": []})
        self.assertTrue(res["signed_off"], res["problems"])
        # the same cumulative ledger WITHOUT the cut halts (what QP condition 2 describes)
        raw = T.reconcile(load("txs.json"), ledger, load("tokens.json"), base, load("balance.json"), {"deposit_from": [], "withdraw_to": []})
        self.assertFalse(raw["signed_off"])


class Rate(unittest.TestCase):
    def test_rate_limit_retry_and_no_url_leak(self):
        t = [0.0]
        slept: list[float] = []

        def sleep(s):
            slept.append(s)
            t[0] += s
        n = [0]

        def post(url, payload):
            n[0] += 1
            if n[0] == 1:
                raise OSError("https://secret.invalid/KEY down")
            return {"result": 7}
        rpc = F.Rpc("https://secret.invalid/KEY", rps=2.0, post=post, sleep=sleep, clock=lambda: t[0])
        self.assertEqual(rpc("getSlot", []), 7)
        self.assertEqual(rpc.calls["getSlot"], 2)
        rpc2 = F.Rpc("https://secret.invalid/KEY", rps=2.0, post=lambda u, p: (_ for _ in ()).throw(OSError("KEY")), sleep=sleep, clock=lambda: t[0])
        with self.assertRaises(F.FetchError) as cm:
            rpc2("getSlot", [])
        self.assertNotIn("KEY", str(cm.exception))
        self.assertNotIn("secret.invalid", str(cm.exception))

    def test_min_interval(self):
        t = [0.0]
        slept: list[float] = []
        rpc = F.Rpc("https://x.invalid", rps=2.0, post=lambda u, p: {"result": 1}, sleep=lambda s: (slept.append(s), t.__setitem__(0, t[0] + s)), clock=lambda: t[0])
        for _ in range(3):
            rpc("getSlot", [])
        self.assertTrue(all(abs(s - 0.5) < 1e-9 for s in slept))
        self.assertEqual(len(slept), 2)


class Cli(unittest.TestCase):
    def test_refuses_keyed_url_abbrev_and_missing_env(self):
        base = ["--wallet", W, "--day", DAY, "--after-slot", "5", "--ledger", "/nonexistent", "--out-dir", "/nonexistent/o"]
        self.assertEqual(F.main([*base, "--rpc", "https://x.invalid/?api-key=abc"]), 2)
        self.assertEqual(F.main([*base, "--rpc", "https://user@x.invalid"]), 2)
        self.assertEqual(F.main([*base, "--rpc-env", "NO_SUCH_VAR_FOR_TEST"]), 2)
        self.assertEqual(F.main([*base, "--rps", "50"]), 2)
        with self.assertRaises(SystemExit):
            F.main(["--wal", W, "--day", DAY, "--ledger", "x", "--out-dir", "y"])


if __name__ == "__main__":
    unittest.main()
