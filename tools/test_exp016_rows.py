from __future__ import annotations

import random
import tracemalloc
import unittest

from tools.exp016_rows import FIELDS, Interner, RowStore

B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def b58(rnd, n):
    return "".join(rnd.choice(B58) for _ in range(n))


def realistic_row(rnd, mint, pool, wallets, i):
    """A PumpSwap trade row with the fields the screen reads (and a few it does not)."""
    return {
        "type": "trade", "venue": "pumpswap", "side": rnd.choice(["buy", "sell"]), "trader": rnd.choice(wallets), "slot": 330_000_000 + i, "token_raw": rnd.randrange(10**9, 10**15),
        "sol_lamports": rnd.randrange(10**7, 10**11), "quote_reserve": rnd.randrange(10**10, 10**12), "base_reserve": rnd.randrange(10**14, 10**16), "signature": b58(rnd, 88),
        "event_index": rnd.randrange(0, 4), "tx_index": rnd.randrange(0, 900), "block_time": 1_790_000_000 + i // 2, "t_recv_ms": (1_790_000_000 + i // 2) * 1000 + rnd.randrange(0, 999),
        "mint": mint, "pool": pool, "quote_is_wsol": True, "price_sol": rnd.random() * 1e-6, "market_cap_sol": rnd.random() * 100, "unread_extra_field": "x" * 20,
    }


class RowStoreTests(unittest.TestCase):
    def test_round_trip_keeps_every_read_field_and_drops_the_rest(self):
        rnd = random.Random(1)
        wallets = [b58(rnd, 44) for _ in range(50)]
        rows = [realistic_row(rnd, "M", "POOL", wallets, i) for i in range(200)]
        st = RowStore("M", Interner())
        for r in rows:
            st.append(r)
        got = list(st)
        self.assertEqual(len(st), 200)
        for r, g in zip(rows, got):
            self.assertEqual(g, {k: v for k, v in r.items() if k in FIELDS})
            self.assertEqual(type(g["quote_is_wsol"]), bool)
            self.assertEqual(type(g["slot"]), int)

    def test_absent_none_false_and_slim_signature(self):
        st = RowStore("M", Interner())
        st.append({"venue": "pump_bonding", "side": "buy", "slot": 5, "quote_is_wsol": False, "t_recv_ms": None, "signature": "abc", "trader": "w"}, slim=True)
        st.append({"venue": "pumpswap", "slot": 6, "signature": "abc", "pool": "p"})
        a, b = list(st)
        self.assertEqual(a, {"mint": "M", "venue": "pump_bonding", "side": "buy", "slot": 5, "quote_is_wsol": False, "trader": "w"})  # slim: no signature; None dropped; False kept
        self.assertEqual(b, {"mint": "M", "venue": "pumpswap", "slot": 6, "signature": "abc", "pool": "p"})
        self.assertNotIn("t_recv_ms", a)

    def test_rows_that_do_not_fit_the_columns_are_kept_whole(self):
        st = RowStore("M", Interner())
        odd = [{"slot": "12"}, {"slot": 1 << 70}, {"slot": 1, "side": 5}, {"slot": 1, "price_sol": 3}, {"slot": 1, "quote_is_wsol": 1}, {"slot": 1, "mint": "OTHER"}]
        for r in odd:
            st.append(r)
        self.assertEqual(list(st), odd)  # unchanged, including the unread extra key

    def test_iterating_twice_gives_the_same_dicts_and_empty_is_falsey(self):
        st = RowStore("M", Interner())
        self.assertFalse(st)
        st.append({"slot": 1, "venue": "pumpswap"})
        self.assertEqual(list(st), list(st))
        self.assertTrue(st)

    def test_bytes_per_retained_row_are_well_under_the_dict_form(self):
        """Realistic rows (88-char signatures, 44-char wallets drawn from a pool of wallets, big reserves). Measured with tracemalloc over the
        stores plus the shared interner, against the dict rows the loader kept before (about 1.35 KB per row in job #296)."""
        rnd = random.Random(7)
        wallets = [b58(rnd, 44) for _ in range(3000)]
        n_mints, per = 40, 500
        raw = [[realistic_row(rnd, f"M{m}", f"POOL{m}", wallets, i) for i in range(per)] for m in range(n_mints)]

        def measure(slim):
            tracemalloc.start()
            before = tracemalloc.get_traced_memory()[0]
            it = Interner()
            stores = []
            for m, rows in enumerate(raw):
                st = RowStore(f"M{m}", it)
                for r in rows:
                    st.append(r, slim=slim)
                stores.append(st)
            used = tracemalloc.get_traced_memory()[0] - before
            tracemalloc.stop()
            return used / (n_mints * per), stores

        full, stores = measure(False)
        slim, _ = measure(True)
        self.assertLess(full, 300)  # migrated mints: every field, signature included
        self.assertLess(slim, 200)  # never-migrated mints: no signature
        self.assertEqual(sum(len(s) for s in stores), n_mints * per)
        print(f"[bytes per kept row] full={full:.0f} slim={slim:.0f}")  # shown with -s; the numbers are also quoted in the PR


if __name__ == "__main__":
    unittest.main()
