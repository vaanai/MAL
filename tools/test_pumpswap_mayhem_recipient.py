"""T22 diagnosis fixtures: PumpSwap pools with is_mayhem_mode = true require a RESERVED fee recipient, not one of global_config.protocol_fee_recipients.

Fixtures are real public mainnet account bytes (tools/fixtures/pumpswap/t22_mayhem_accounts.json). The IDL layout is in
ARTIFACTS/lab/program-upgrade-1014/out/docs/amm_new.json.gz. The builder tests below were flipped when the shared helper tx.fee_recipient landed: a mayhem pool now gets a reserved recipient on buy and sell."""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from solders.pubkey import Pubkey

from tools import pumpswap_simulate as sim
from tools import pumpswap_tx as tx

FX = json.loads((Path(__file__).parent / "fixtures" / "pumpswap" / "t22_mayhem_accounts.json").read_text())
POOL_A, POOL_B, GC = (bytes.fromhex(FX[k]) for k in ("pool_a_hex", "pool_b_hex", "global_config_hex"))

POOL_MAYHEM_OFFSET = 243  # IDL Pool: coin_creator ends at 243, then is_mayhem_mode bool, is_cashback_coin bool, virtual_quote_reserves i128
GC_RESERVED_OFFSET = 385  # IDL GlobalConfig: reserved_fee_recipient pubkey
GC_MAYHEM_ENABLED_OFFSET = 417
GC_RESERVED_LIST_OFFSET = 418  # reserved_fee_recipients [pubkey; 7]


def reserved(gc: bytes) -> list[Pubkey]:
    return [Pubkey.from_bytes(gc[GC_RESERVED_OFFSET:GC_RESERVED_OFFSET + 32])] + [Pubkey.from_bytes(gc[GC_RESERVED_LIST_OFFSET + 32 * i:GC_RESERVED_LIST_OFFSET + 32 * (i + 1)]) for i in range(7)]


class MayhemFixtures(unittest.TestCase):
    def test_pool_flags(self):
        self.assertEqual(len(POOL_A), 301)
        self.assertEqual(POOL_A[POOL_MAYHEM_OFFSET], 1)  # AqjAEL...: the pool whose sell failed 6013
        self.assertEqual(POOL_B[POOL_MAYHEM_OFFSET], 0)  # 6Btazs...: the Token pool whose sell reached the swap
        self.assertEqual(tx.parse_pool_account(POOL_A)["flags"][:2], "01")
        self.assertEqual(tx.parse_pool_account(POOL_B)["flags"][:2], "00")

    def test_global_config_reserved_set(self):
        self.assertEqual(GC[GC_MAYHEM_ENABLED_OFFSET], 1)
        res = reserved(GC)
        self.assertEqual(str(res[0]), "GesfTA3X2arioaHp8bbKdjG9vJtskViWACZoYvxp4twS")
        self.assertEqual(str(res[1]), "4budycTjhs9fD6xw62VBducVTNgMgJJ5BgtKq7mAZwn6")
        self.assertEqual(len(set(res)), 8)
        cfg = tx.parse_global_config(GC)
        self.assertFalse(set(res) & set(cfg["protocol_fee_recipients"]))  # disjoint sets: a protocol recipient is never valid on a mayhem pool
        self.assertEqual(str(cfg["buyback_fee_recipients"][0]), "9M4giFFMxmFGXtc3feFzRai56WbBqehoSeRE5GK7gf7")  # offset 675 is IDL buyback_fee_recipients[1] (the array starts at 643); parse_global_config reads from 675 and only [0] is used


import base64  # noqa: E402

from tools import probe_executor as pe  # noqa: E402

TOK22 = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"
USER = Pubkey.new_unique()


def _blob(b, owner):
    return {"owner": owner, "data": [base64.b64encode(b).decode(), "base64"]}


def make_rpc(pool_bytes, gc_bytes=GC, counter=None):
    def rpc(method, params):
        if counter is not None:
            counter.append(method)
        if method == "getAccountInfo":
            return {"value": _blob(pool_bytes, str(tx.PUMPSWAP_PROGRAM))}
        keys = params[0]
        vals = {str(tx.GLOBAL_CONFIG): _blob(gc_bytes, str(tx.PUMPSWAP_PROGRAM))}
        pool = tx.parse_pool_account(pool_bytes)
        vals[str(pool["base_mint"])] = _blob(bytes(82), TOK22)
        vals[str(pool["base_vault"])] = _blob(bytes(165), TOK22)
        vals[str(pool["quote_vault"])] = _blob(bytes(165), str(tx.TOKEN_PROGRAM))
        return {"value": [vals.get(k) for k in keys], "context": {"slot": 1}}
    return rpc


def legacy_choice(gc_bytes, user):
    recips = [r for r in tx.parse_global_config(gc_bytes)["protocol_fee_recipients"] if r != user]
    return recips[1] if len(recips) > 1 else recips[0]


def ix_bytes(ps, user):
    buy = tx.build_buy(ps, user, 100_000_000, 100, 1_000_000)
    sell = tx.build_sell(ps, user, 1_000_000, 1)
    return bytes(buy.serialize()) if hasattr(buy, "serialize") else bytes(buy), bytes(sell.serialize()) if hasattr(sell, "serialize") else bytes(sell)


class FeeRecipientHelper(unittest.TestCase):
    def test_non_mayhem_is_the_old_choice(self):
        for user in (None, USER, tx.parse_global_config(GC)["protocol_fee_recipients"][1]):
            self.assertEqual(tx.fee_recipient(GC, POOL_B, user), legacy_choice(GC, user))
            self.assertEqual(tx.fee_recipient(tx.parse_global_config(GC), POOL_B, user), legacy_choice(GC, user))

    def test_mayhem_gives_reserved_key_385(self):
        r = tx.fee_recipient(GC, POOL_A, USER)
        self.assertEqual(str(r), "GesfTA3X2arioaHp8bbKdjG9vJtskViWACZoYvxp4twS")
        self.assertIn(r, reserved(GC))
        self.assertNotIn(r, tx.parse_global_config(GC)["protocol_fee_recipients"])

    def test_mayhem_skips_user_if_user_is_the_reserved_key(self):
        self.assertEqual(str(tx.fee_recipient(GC, POOL_A, reserved(GC)[0])), "4budycTjhs9fD6xw62VBducVTNgMgJJ5BgtKq7mAZwn6")

    def test_refuses_mayhem_pool_when_mayhem_disabled(self):
        gc = bytearray(GC)
        gc[GC_MAYHEM_ENABLED_OFFSET] = 0
        with self.assertRaisesRegex(ValueError, "mayhem_mode_enabled = 0"):
            tx.fee_recipient(bytes(gc), POOL_A, USER)
        self.assertEqual(tx.fee_recipient(bytes(gc), POOL_B, USER), legacy_choice(GC, USER))  # non-mayhem pool unaffected

    def test_mayhem_needs_raw_bytes(self):
        with self.assertRaises(ValueError):
            tx.fee_recipient(tx.parse_global_config(GC), POOL_A, USER)
        with self.assertRaises(ValueError):
            tx.fee_recipient(GC[:500], POOL_A, USER)


class BuilderPaths(unittest.TestCase):
    def test_simulate_non_mayhem_bytes_identical(self):
        ps, _ = sim.fetch_pool_state(make_rpc(POOL_B), FX["pool_b_plain"], USER)
        self.assertEqual(ps.protocol_fee_recipient, legacy_choice(GC, USER))
        old = tx.pool_state_from_accounts(Pubkey.from_string(FX["pool_b_plain"]), POOL_B, base_token_program=ps.base_token_program,
                                          protocol_fee_recipient=legacy_choice(GC, USER), buyback_fee_recipient=tx.parse_global_config(GC)["buyback_fee_recipients"][0])
        self.assertEqual(ix_bytes(ps, USER), ix_bytes(old, USER))

    def test_snapshot_non_mayhem_bytes_identical_cached_and_uncached(self):
        old = tx.pool_state_from_accounts(Pubkey.from_string(FX["pool_b_plain"]), POOL_B, base_token_program=Pubkey.from_string(TOK22),
                                          protocol_fee_recipient=legacy_choice(GC, USER), buyback_fee_recipient=tx.parse_global_config(GC)["buyback_fee_recipients"][0])
        snap = pe.fetch_snapshot(make_rpc(POOL_B), FX["pool_b_plain"], "processed", USER)
        self.assertEqual(ix_bytes(snap.ps, USER), ix_bytes(old, USER))
        st = pe.StaticCache()
        st.put(tx.parse_global_config(GC), 1000)
        calls = []
        snap2 = pe.fetch_snapshot(make_rpc(POOL_B, counter=calls), FX["pool_b_plain"], "processed", USER, static=st, now_ms=1000)
        self.assertEqual(ix_bytes(snap2.ps, USER), ix_bytes(old, USER))

    def test_mayhem_buy_and_sell_use_reserved_recipient_simulate_path(self):
        ps, _ = sim.fetch_pool_state(make_rpc(POOL_A), FX["pool_a_mayhem"], USER)
        self.assertIn(ps.protocol_fee_recipient, reserved(GC))
        for side in ("buy", "sell"):
            metas = tx.swap_accounts(ps, USER, side=side)
            self.assertIn(metas[9].pubkey, reserved(GC), side)
            self.assertNotIn(metas[9].pubkey, tx.parse_global_config(GC)["protocol_fee_recipients"], side)

    def test_mayhem_snapshot_path_even_with_a_warm_cache(self):
        st = pe.StaticCache()
        st.put(tx.parse_global_config(GC), 1000)  # parsed cache has no reserved keys: the mayhem pool must bypass it
        snap = pe.fetch_snapshot(make_rpc(POOL_A), FX["pool_a_mayhem"], "processed", USER, static=st, now_ms=1000)
        self.assertNotIsInstance(snap, str)
        self.assertIn(snap.ps.protocol_fee_recipient, reserved(GC))
        for side in ("buy", "sell"):
            self.assertIn(tx.swap_accounts(snap.ps, USER, side=side)[9].pubkey, reserved(GC), side)

    def test_refusal_in_both_paths(self):
        gc = bytearray(GC)
        gc[GC_MAYHEM_ENABLED_OFFSET] = 0
        snap = pe.fetch_snapshot(make_rpc(POOL_A, bytes(gc)), FX["pool_a_mayhem"], "processed", USER)
        self.assertIsInstance(snap, str)
        self.assertTrue(snap.startswith("mayhem_refused:"))
        with self.assertRaises(SystemExit):
            sim.fetch_pool_state(make_rpc(POOL_A, bytes(gc)), FX["pool_a_mayhem"], USER)


if __name__ == "__main__":
    unittest.main()
