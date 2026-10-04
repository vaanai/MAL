"""canonical_pool(mint) against real (mint, pool) pairs from the 2026-09-25..27 oracle trade tape
(venue pumpswap, quote WSOL). pool_v2 is NOT the pool."""

from __future__ import annotations

import unittest

from solders.pubkey import Pubkey

from tools import pumpswap_tx as tx

PAIRS = [
    ("D9eGoqmTHBZtDf7g1S2uRtDLN3s92SqS3v4pfKJepump", "JAbeCETwTAdeBLe44BoJzHx5aCsRfvSSRJXWGdvFGxwM"),
    ("FiC91M3wDHG8ZwdWPHCanFqe3FxtXrLoctZjfnjfpump", "Em2uUHo4NqSt36UM7QJAwVLkkd7kM4fqEaZtDPzA15HB"),
    ("DRMnFyekQiCTMrajtgsTycqp4ie6r1tZnh6qpAGjpump", "Azyy8ibM2bbPAb4BS9JkDm5MbcCHohBCJZCNPyaCVdCj"),
    ("CqZfPwiafwweoau1xqoPLpsBBX65NEffNDYPfWgTpump", "12bUqVJNRP9L7rrniKM8ZY4dnXu1p91mKmdWHL46f26Z"),
    ("5g9PWcRUHpmqm5uKycpShKXJB18sGLeCEzgB3qrRpump", "Ftjga524YrS7RPzGCcezMuQnFDYa8PcvWDY5mFmZk5dy"),
]


class CanonicalPoolTests(unittest.TestCase):
    def test_matches_tape_pool(self):
        for mint, pool in PAIRS:
            self.assertEqual(str(tx.canonical_pool(Pubkey.from_string(mint))), pool)

    def test_pool_v2_is_a_different_account(self):
        for mint, pool in PAIRS:
            self.assertNotEqual(str(tx.pool_v2(Pubkey.from_string(mint))), pool)


if __name__ == "__main__":
    unittest.main()
