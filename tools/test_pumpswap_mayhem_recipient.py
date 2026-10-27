"""T22 diagnosis fixtures: PumpSwap pools with is_mayhem_mode = true require a RESERVED fee recipient, not one of global_config.protocol_fee_recipients.

Fixtures are real public mainnet account bytes (tools/fixtures/pumpswap/t22_mayhem_accounts.json). The IDL layout is in
ARTIFACTS/lab/program-upgrade-1014/out/docs/amm_new.json.gz. The last test records what the builder does TODAY (it picks protocol_fee_recipients[1]
for every pool); it documents the gap and must be flipped when the pinned builder (pumpswap_simulate / probe_executor) is fixed."""
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
        self.assertEqual(str(cfg["buyback_fee_recipients"][0]), "9M4giFFMxmFGXtc3feFzRai56WbBqehoSeRE5GK7gf7")  # offset 675 still holds (IDL order shifted by one pubkey)

    def test_builder_today_picks_a_protocol_recipient_for_the_mayhem_pool(self):
        """Documents the gap (ErrorCode 6013 at swap/mod.rs:164). Flip this test when the builder honours is_mayhem_mode."""
        import base64

        def rpc(method, params):
            if method == "getAccountInfo":
                return {"value": {"owner": str(tx.PUMPSWAP_PROGRAM), "data": [base64.b64encode(POOL_A).decode(), "base64"]}}
            keys = params[0]
            blob = lambda b, o: {"owner": o, "data": [base64.b64encode(b).decode(), "base64"]}  # noqa: E731
            return {"value": [blob(GC, str(tx.PUMPSWAP_PROGRAM)), blob(bytes(165), str(tx.TOKEN_2022_PROGRAM)) if hasattr(tx, "TOKEN_2022_PROGRAM") else blob(bytes(165), "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"),
                              blob(bytes(165), str(tx.TOKEN_PROGRAM)), blob(bytes(165), str(tx.TOKEN_PROGRAM)), None][:len(keys)]}

        ps, _ = sim.fetch_pool_state(rpc, FX["pool_a_mayhem"], Pubkey.default())
        cfg = tx.parse_global_config(GC)
        self.assertEqual(ps.protocol_fee_recipient, cfg["protocol_fee_recipients"][1])
        self.assertNotIn(ps.protocol_fee_recipient, reserved(GC))


if __name__ == "__main__":
    unittest.main()
