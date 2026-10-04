"""Offline tests for tools/pumpswap_tx.py against saved real PumpSwap transactions."""

from __future__ import annotations

import base64
import json
import re
import struct
import unittest
from pathlib import Path

from solders.compute_budget import ID as COMPUTE_BUDGET_ID
from solders.pubkey import Pubkey

from tools import pumpswap_tx as t

FIX = Path(__file__).parent / "fixtures" / "pumpswap"
P = Pubkey.from_string


def load(name: str) -> dict:
    return json.loads((FIX / f"{name}.json").read_text())


def state_from_fixture(fx: dict) -> tuple[t.PoolState, Pubkey, dict]:
    accts = fx["pamm_ix"]["accounts"]
    keys = [a["pubkey"] for a in accts]
    ps = t.pool_state_from_b64(
        fx["pool"],
        fx["pool_account_b64"],
        base_token_program=P(keys[11]),
        protocol_fee_recipient=P(keys[9]),
        buyback_fee_recipient=P(keys[-2]),
    )
    return ps, P(keys[1]), fx


def metas(ixmetas):
    return [(str(m.pubkey), m.is_signer, m.is_writable) for m in ixmetas]


def real_metas(fx):
    return [(a["pubkey"], a["is_signer"], a["is_writable"]) for a in fx["pamm_ix"]["accounts"]]


class AccountOrderTests(unittest.TestCase):
    def test_buy_accounts_match_real_tx(self):
        for name in ("buy_exact_quote_in_a", "buy_exact_quote_in_b"):
            ps, user, fx = state_from_fixture(load(name))
            self.assertEqual(metas(t.swap_accounts(ps, user, side="buy")), real_metas(fx), name)

    def test_sell_accounts_match_real_tx(self):
        for name in ("sell_a", "sell_b_token2022_or_legacy"):
            ps, user, fx = state_from_fixture(load(name))
            self.assertEqual(metas(t.swap_accounts(ps, user, side="sell")), real_metas(fx), name)

    def test_token2022_base_program_in_fixture(self):
        ps, _, _ = state_from_fixture(load("buy_exact_quote_in_a"))
        self.assertEqual(ps.base_token_program, t.TOKEN_2022_PROGRAM)

    def test_legacy_aug_buy_head_matches(self):
        # 2026-08-28 tx: same first 20 accounts; the trailing set was shorter.
        fx = load("legacy_buy_aug2026")
        keys = [a["pubkey"] for a in fx["pamm_ix"]["accounts"]]
        ps = t.pool_state_from_b64(fx["pool"], fx["pool_account_b64"], base_token_program=P(keys[11]),
                                   protocol_fee_recipient=P(keys[9]), buyback_fee_recipient=P(keys[-2]))
        ours = metas(t.swap_accounts(ps, P(keys[1]), side="buy"))
        self.assertEqual(ours[:19], real_metas(fx)[:19])


class DataTests(unittest.TestCase):
    def _real_data(self, name):
        return bytes.fromhex(load(name)["pamm_ix"]["data_hex"])

    def test_buy_exact_quote_in_bytes_reproduce(self):
        for name in ("buy_exact_quote_in_a", "buy_exact_quote_in_b"):
            real = self._real_data(name)
            self.assertEqual(real[:8], t.DISC_BUY_EXACT_QUOTE_IN)
            spend, min_out, track = struct.unpack("<QQB", real[8:])
            self.assertEqual(t.buy_exact_quote_in_data(spend, min_out, bool(track)), real)

    def test_sell_bytes_reproduce(self):
        for name in ("sell_a", "sell_b_token2022_or_legacy"):
            real = self._real_data(name)
            amount, min_out = struct.unpack("<QQ", real[8:])
            self.assertEqual(t.sell_data(amount, min_out), real)

    def test_legacy_buy_bytes_reproduce(self):
        real = self._real_data("legacy_buy_aug2026")
        self.assertEqual(real[:8], t.DISC_BUY)
        out, max_in = struct.unpack("<QQ", real[8:24])
        self.assertEqual(t.buy_data(out, max_in, track_volume=None if len(real) == 24 else bool(real[24])), real)

    def test_full_buy_ix_reproduces_real_ix_from_real_args(self):
        fx = load("buy_exact_quote_in_a")
        ps, user, _ = state_from_fixture(fx)
        real = bytes.fromhex(fx["pamm_ix"]["data_hex"])
        spend, min_out, _ = struct.unpack("<QQB", real[8:])
        ixs = t.buy_instructions(ps, user, spend, expected_base_out=min_out, max_slippage_bps=0)
        swap = [i for i in ixs if i.program_id == t.PUMPSWAP_PROGRAM][0]
        self.assertEqual(bytes(swap.data), real)
        self.assertEqual(metas(swap.accounts), real_metas(fx))

    def test_full_sell_ix_reproduces_real_ix_from_real_args(self):
        fx = load("sell_a")
        ps, user, _ = state_from_fixture(fx)
        real = bytes.fromhex(fx["pamm_ix"]["data_hex"])
        amount, min_out = struct.unpack("<QQ", real[8:])
        ixs = t.sell_instructions(ps, user, amount, min_out)
        swap = [i for i in ixs if i.program_id == t.PUMPSWAP_PROGRAM][0]
        self.assertEqual(bytes(swap.data), real)
        self.assertEqual(metas(swap.accounts), real_metas(fx))

    def test_surrounding_instruction_kinds_match_real_buy(self):
        fx = load("buy_exact_quote_in_a")
        real_progs = [x["program"] for x in fx["top_level"]]
        ps, user, _ = state_from_fixture(fx)
        ours = [str(i.program_id) for i in t.buy_instructions(ps, user, 10**8, 10**9, 100)]
        # real tx skipped base-ATA creation (already held); ours always creates it idempotently.
        self.assertEqual([p for p in ours if p != str(t.ATA_PROGRAM)][:2], real_progs[:2])
        for needed in (str(t.SYSTEM_PROGRAM), str(t.TOKEN_PROGRAM), str(t.PUMPSWAP_PROGRAM)):
            self.assertIn(needed, ours)


class ComputeBudgetTests(unittest.TestCase):
    def test_priority_500k_lamports_exact(self):
        price = t.priority_price_for_total(500_000, 250_000)
        self.assertEqual(price, 2_000_000)
        self.assertEqual(t.priority_fee_lamports(price, 250_000), 500_000)

    def test_priority_rounds_up_never_below_total(self):
        for limit in (150_000, 200_000, 333_333, 1_400_000):
            price = t.priority_price_for_total(500_000, limit)
            self.assertGreaterEqual(t.priority_fee_lamports(price, limit), 500_000)
            self.assertLessEqual(t.priority_fee_lamports(price, limit), 500_000 + 2)

    def test_buy_and_sell_start_with_limit_then_price(self):
        ps, user, _ = state_from_fixture(load("buy_exact_quote_in_a"))
        for ixs, limit in (
            (t.buy_instructions(ps, user, 5 * 10**8, 10**9, 100), t.DEFAULT_BUY_CU_LIMIT),
            (t.sell_instructions(ps, user, 10**9, 1), t.DEFAULT_SELL_CU_LIMIT),
        ):
            self.assertEqual(ixs[0].program_id, COMPUTE_BUDGET_ID)
            self.assertEqual(ixs[1].program_id, COMPUTE_BUDGET_ID)
            self.assertEqual(bytes(ixs[0].data), b"\x02" + struct.pack("<I", limit))
            price = struct.unpack("<Q", bytes(ixs[1].data)[1:])[0]
            self.assertEqual(bytes(ixs[1].data)[0], 3)
            self.assertEqual(t.priority_fee_lamports(price, limit), 500_000)

    def test_real_compute_budget_data_layout(self):
        # Real tx: 03 + u64 price, 02 + u32 limit.
        top = load("buy_exact_quote_in_a")["top_level"]
        self.assertEqual(top[0]["data_hex"][:2], "03")
        self.assertEqual(top[1]["data_hex"][:2], "02")


class SlippageTests(unittest.TestCase):
    def test_min_out(self):
        self.assertEqual(t.min_out_with_slippage(1_000_000, 100), 990_000)
        self.assertEqual(t.min_out_with_slippage(999, 100), 989)  # floor
        self.assertEqual(t.min_out_with_slippage(5, 0), 5)
        self.assertEqual(t.min_out_with_slippage(5, 10_000), 0)

    def test_max_in(self):
        self.assertEqual(t.max_in_with_slippage(500_000_000, 100), 505_000_000)
        self.assertEqual(t.max_in_with_slippage(101, 100), 103)  # ceil

    def test_bad_bps(self):
        with self.assertRaises(ValueError):
            t.min_out_with_slippage(1, 10_001)
        with self.assertRaises(ValueError):
            t.min_out_with_slippage(1, -1)

    def test_buy_ix_embeds_slippage_bound(self):
        ps, user, _ = state_from_fixture(load("buy_exact_quote_in_a"))
        ixs = t.buy_instructions(ps, user, 500_000_000, 1_000_000, 250)
        swap = [i for i in ixs if i.program_id == t.PUMPSWAP_PROGRAM][0]
        spend, min_out, _ = struct.unpack("<QQB", bytes(swap.data)[8:])
        self.assertEqual((spend, min_out), (500_000_000, 975_000))

    def test_buy_variant_wraps_max_in(self):
        ps, user, _ = state_from_fixture(load("buy_exact_quote_in_a"))
        ixs = t.buy_instructions(ps, user, 500_000_000, 1_000_000, 100, exact_quote_in=False)
        swap = [i for i in ixs if i.program_id == t.PUMPSWAP_PROGRAM][0]
        out, max_in = struct.unpack("<QQ", bytes(swap.data)[8:24])
        self.assertEqual((out, max_in), (1_000_000, 505_000_000))
        transfer_ix = [i for i in ixs if i.program_id == t.SYSTEM_PROGRAM][0]
        self.assertEqual(struct.unpack("<Q", bytes(transfer_ix.data)[4:])[0], 505_000_000)

    def test_cp_math_monotone(self):
        a = t.cp_buy_out(500_000_000, 100 * 10**9, 10**15, 3_000)
        b = t.cp_buy_out(500_000_000, 100 * 10**9, 10**15, 10_000)
        self.assertGreater(a, b)
        self.assertLess(t.cp_sell_out(a, 100 * 10**9, 10**15, 3_000), 500_000_000)


class BuildTests(unittest.TestCase):
    def test_messages_are_unsigned_and_fit(self):
        ps, user, _ = state_from_fixture(load("buy_exact_quote_in_a"))
        for msg in (
            t.build_buy(ps, user, 500_000_000, 100, 10**9),
            t.build_sell(ps, user, 10**9, 1),
        ):
            tx = t.unsigned_transaction(msg)
            self.assertTrue(all(str(s) == "1" * 64 for s in tx.signatures))
            self.assertLessEqual(t.serialized_size(msg), t.TX_SIZE_LIMIT)
            self.assertEqual(msg.account_keys[0], user)

    def test_pool_parse_roundtrip(self):
        fx = load("buy_exact_quote_in_a")
        p = t.parse_pool_account(base64.b64decode(fx["pool_account_b64"]))
        keys = [a["pubkey"] for a in fx["pamm_ix"]["accounts"]]
        self.assertEqual(str(p["base_mint"]), keys[3])
        self.assertEqual(str(p["base_vault"]), keys[7])
        self.assertEqual(str(p["quote_vault"]), keys[8])

    def test_global_config_recipients_contain_real_ones(self):
        gc = json.loads((FIX / "global_config.json").read_text())
        g = t.parse_global_config(base64.b64decode(gc["data_b64"]))
        proto = {str(x) for x in g["protocol_fee_recipients"]}
        buyback = {str(x) for x in g["buyback_fee_recipients"]}
        for name in ("buy_exact_quote_in_a", "buy_exact_quote_in_b", "sell_a", "sell_b_token2022_or_legacy"):
            keys = [a["pubkey"] for a in load(name)["pamm_ix"]["accounts"]]
            self.assertIn(keys[9], proto, name)
            self.assertIn(keys[-2], buyback, name)


class NoKeyMaterialTests(unittest.TestCase):
    PATTERNS = [r"Keypair", r"from_seed", r"from_base58_string", r"secret", r"\.sign\(", r"sendTransaction", r"send_transaction",
                r"private", r"id\.json"]

    def test_modules_have_no_key_loading_or_send(self):
        for name in ("pumpswap_tx.py", "pumpswap_simulate.py"):
            p = Path(__file__).parent / name
            if not p.exists():
                continue
            src = p.read_text()
            # strip comments/docstring mentions of the forbidden words in a line that says "never"
            for pat in self.PATTERNS:
                for line in src.splitlines():
                    if re.search(pat, line, re.IGNORECASE) and not re.search(r"never|no key|keyless|not send|nothing", line, re.IGNORECASE):
                        self.fail(f"{name}: forbidden pattern {pat!r} in line: {line.strip()[:80]}")

    def test_fixtures_have_no_secrets(self):
        for f in FIX.glob("*.json"):
            s = f.read_text()
            self.assertNotIn("api-key", s.lower())
            self.assertNotIn("helius", s.lower())


if __name__ == "__main__":
    unittest.main()
