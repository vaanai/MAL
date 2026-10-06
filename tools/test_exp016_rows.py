from __future__ import annotations

import random
import tracemalloc
import unittest

import tools.exp016_rug as rug
from tools.exp016_rows import FIELDS, IGNORABLE_KEYS, STORED_KEYS, Interner, RowStore
from tools.paper_price_path import print_from_trade_row

B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def b58(rnd, n):
    return "".join(rnd.choice(B58) for _ in range(n))


def realistic_row(rnd, mint, pool, wallets, i):
    """A PumpSwap v1 trade row: the fields the screen reads, fees included."""
    return {
        "type": "trade", "venue": "pumpswap", "side": rnd.choice(["buy", "sell"]), "trader": rnd.choice(wallets), "slot": 330_000_000 + i, "token_raw": rnd.randrange(10**9, 10**15),
        "sol_lamports": rnd.randrange(10**7, 10**11), "quote_reserve": rnd.randrange(10**10, 10**12), "base_reserve": rnd.randrange(10**14, 10**16), "signature": b58(rnd, 88),
        "event_index": rnd.randrange(0, 4), "tx_index": rnd.randrange(0, 900), "block_time": 1_790_000_000 + i // 2, "t_recv_ms": (1_790_000_000 + i // 2) * 1000 + rnd.randrange(0, 999),
        "mint": mint, "pool": pool, "quote_is_wsol": True, "price_sol": rnd.random() * 1e-6, "market_cap_sol": rnd.random() * 100,
        "pool_quote_amount": rnd.randrange(10**7, 10**11), "lp_fee": rnd.randrange(10**4, 10**8), "protocol_fee": rnd.randrange(10**4, 10**8), "creator_fee": rnd.randrange(10**4, 10**8),
    }


def store_of(rows, mint="M"):
    st = RowStore(mint, Interner())
    for r in rows:
        st.append(r)
    return st


class RowStoreTests(unittest.TestCase):
    def test_round_trip_is_exact_for_whitelisted_rows(self):
        rnd = random.Random(1)
        wallets = [b58(rnd, 44) for _ in range(50)]
        rows = [realistic_row(rnd, "M", "POOL", wallets, i) for i in range(200)]
        self.assertTrue(set(rows[0]) <= STORED_KEYS)
        st = store_of(rows)
        for r, g in zip(rows, st):
            self.assertEqual(g, r)
            self.assertEqual(type(g["quote_is_wsol"]), bool)
        self.assertEqual(st._it.n_fallback, 0)

    def test_pumpswap_v1_fees_reach_print_from_trade_row_identically(self):
        rnd = random.Random(2)
        base = realistic_row(rnd, "M", "POOL", ["w"], 0)
        base.update(side="sell", sol_lamports=2_000_000_000, token_raw=3_000_000_000, quote_reserve=50_000_000_000, base_reserve=700_000_000_000_000,
                    pool_quote_amount=1_900_000_000, lp_fee=4_000_000, protocol_fee=1_000_000, creator_fee=1_000_000)
        want = print_from_trade_row(base)
        got = print_from_trade_row(next(iter(store_of([base]))))
        self.assertIsNotNone(want)
        self.assertEqual(got, want)
        # the test has teeth: the same row WITHOUT the fee fields prices differently (the fee_ppm fallback), so dropping them would move fills
        stripped = {k: v for k, v in base.items() if k not in ("pool_quote_amount", "lp_fee", "protocol_fee", "creator_fee")}
        self.assertNotEqual(print_from_trade_row(stripped), want)

    def test_an_unknown_key_keeps_the_whole_row_and_is_counted(self):
        st = store_of([{"slot": 1, "venue": "pumpswap", "future_fee_field": 7}, {"slot": 2, "venue": "pumpswap", "future_fee_field": 8, "other_new": "x"}, {"slot": 3}])
        a, b, c = list(st)
        self.assertEqual(a, {"slot": 1, "venue": "pumpswap", "future_fee_field": 7})  # identical, nothing dropped
        self.assertEqual(b, {"slot": 2, "venue": "pumpswap", "future_fee_field": 8, "other_new": "x"})
        self.assertEqual(c, {"mint": "M", "slot": 3})
        self.assertEqual(st._it.unknown_keys, {"future_fee_field": 2, "other_new": 1})
        self.assertEqual(st._it.n_fallback, 2)

    def test_only_reviewed_ignorable_keys_may_be_dropped(self):
        self.assertEqual(IGNORABLE_KEYS, frozenset({"event_ts"}))
        (g,) = list(store_of([{"slot": 1, "event_ts": "2026-01-01T00:00:00Z"}]))
        self.assertEqual(g, {"mint": "M", "slot": 1})  # the one documented drop

    def test_absent_none_and_false(self):
        st = store_of([{"venue": "pump_bonding", "side": "buy", "slot": 5, "quote_is_wsol": False, "t_recv_ms": None, "signature": "abc", "trader": "w"},
                       {"venue": "pumpswap", "slot": 6, "signature": "abc", "pool": "p"}])
        a, b = list(st)
        self.assertEqual(a, {"mint": "M", "venue": "pump_bonding", "side": "buy", "slot": 5, "quote_is_wsol": False, "trader": "w", "signature": "abc"})
        self.assertEqual(b, {"mint": "M", "venue": "pumpswap", "slot": 6, "signature": "abc", "pool": "p"})
        self.assertNotIn("t_recv_ms", a)

    def test_rows_that_do_not_fit_the_columns_are_kept_whole(self):
        odd = [{"slot": "12"}, {"slot": 1 << 70}, {"slot": 1, "side": 5}, {"slot": 1, "price_sol": 3}, {"slot": 1, "quote_is_wsol": 1}, {"slot": 1, "mint": "OTHER"},
               {"slot": 1, "lp_fee": "5"}, {"slot": 1, "signature": ""}]
        self.assertEqual(list(store_of(odd)), odd)

    def test_stamp_rows_duplicate_detection_is_equal_for_never_migrated_mints(self):
        """Oracle-style rows (no tx_index), exact duplicates included: `stamp_rows` keys on the signature, so a slim store must keep it."""
        rnd = random.Random(3)
        rows = []
        for i in range(40):
            r = {"venue": "pump_bonding", "side": "buy", "trader": f"w{i % 5}", "slot": 100 + i // 4, "t_recv_ms": 1_000_000 + i * 100, "event_index": 0,
                 "signature": f"sig{i % 7}", "quote_reserve": 10**10 + i, "base_reserve": 10**15 - i, "token_raw": 5, "sol_lamports": 7, "block_time": 1000}
            rows.append(r)
            if i % 6 == 0:
                rows.append(dict(r))  # an exact duplicate
        rnd.shuffle(rows)
        want = rug.stamp_rows(rows)
        got = rug.stamp_rows(list(store_of(rows)))
        self.assertEqual([k for _r, k in got], [k for _r, k in want])
        self.assertEqual(rug.count_slot_inversions(rows), rug.count_slot_inversions(list(store_of(rows))))

    def test_iterating_twice_gives_the_same_dicts_and_empty_is_falsey(self):
        st = RowStore("M", Interner())
        self.assertFalse(st)
        st.append({"slot": 1, "venue": "pumpswap"})
        self.assertEqual(list(st), list(st))
        self.assertTrue(st)

    def test_bytes_per_retained_row_are_well_under_the_dict_form(self):
        """Realistic rows (88-char signatures, 44-char wallets drawn from a pool of wallets, big reserves, four fee fields), every row keeping its
        signature. Measured with tracemalloc over the stores plus the shared interner, against about 1.35 KB per dict row in job #296."""
        rnd = random.Random(7)
        wallets = [b58(rnd, 44) for _ in range(3000)]
        n_mints, per = 40, 500
        raw = [[realistic_row(rnd, f"M{m}", f"POOL{m}", wallets, i) for i in range(per)] for m in range(n_mints)]
        bonding = [[{k: v for k, v in r.items() if k not in ("pool", "pool_quote_amount", "lp_fee", "protocol_fee", "creator_fee")} | {"venue": "pump_bonding"} for r in rows] for rows in raw]

        def measure(data):
            tracemalloc.start()
            before = tracemalloc.get_traced_memory()[0]
            it = Interner()
            stores = []
            for m, rows in enumerate(data):
                st = RowStore(f"M{m}", it)
                for r in rows:
                    st.append(r)
                stores.append(st)
            used = tracemalloc.get_traced_memory()[0] - before
            tracemalloc.stop()
            assert it.n_fallback == 0
            return used / (n_mints * per), stores

        pumpswap, stores = measure(raw)
        curve, _ = measure(bonding)
        self.assertLess(pumpswap, 300)  # PumpSwap v1 rows: all fields, fees and signature
        self.assertLess(curve, 300)  # bonding rows (what a never-migrated mint keeps): no fees, signature kept
        self.assertEqual(sum(len(s) for s in stores), n_mints * per)
        print(f"[bytes per kept row] pumpswap_v1={pumpswap:.0f} bonding={curve:.0f}")


if __name__ == "__main__":
    unittest.main()
