"""Regression coverage for the atomic-signature fill bug in
ARTIFACTS/lab/migrate-holdout-outliers.md.

A pump.fun transaction is atomic: every inner instruction lands together or
not at all. The bug was a paper order landing *between* two Buy instructions
of the same signature -- a 67 SOL pool, then a 2k-11k SOL drain buy in the
same transaction -- because the reserve state after the first inner event
was exposed as an independently fillable market price. ``collapse_fillable``
and ``state_as_of`` must expose exactly one fillable state per
``(slot, signature)`` group: the reserves after the *last* inner event. This
file locks that in with the transaction geometry from the note (mint
``qhyWQAfBWW8p...pump``, signature ``3eJ5FYtdzW318BYD...``, slot
450235843).
"""

from __future__ import annotations

import unittest

from tools.paper_price_path import (
    CreateSignal,
    MintPath,
    TapePrint,
    collapse_fillable,
    finalize_prints,
    print_from_trade_row,
    state_as_of,
)

MINT = "qhyWQAfBWW8ppump"
SIGNATURE = "3eJ5FYtdzW318BYDatomicpair"
SLOT = 450235843
T_SHARED_MS = 1_758_800_000_000  # one synthetic receive time, shared by the whole signature


def _row(
    *,
    event_index: int,
    quote_reserve: int,
    base_reserve: int,
    sol_lamports: int,
    token_raw: int,
    t_recv_ms: int = T_SHARED_MS,
    signature: str = SIGNATURE,
) -> dict:
    """A PumpSwap trade row shaped like a decoded (or synthetically stamped) backfill row.

    ``quote_reserve``/``base_reserve`` are the pool *before* this row's trade,
    matching the tape schema documented in paper_price_path.pumpswap_post_trade_reserves.
    """
    return {
        "mint": MINT,
        "venue": "pumpswap",
        "side": "buy",
        "quote_is_wsol": True,
        "t_recv_ms": t_recv_ms,
        "slot": SLOT,
        "event_index": event_index,
        "signature": signature,
        "quote_reserve": quote_reserve,
        "base_reserve": base_reserve,
        "sol_lamports": sol_lamports,
        "token_raw": token_raw,
    }


def _atomic_pair_prints() -> tuple[TapePrint, TapePrint]:
    """The two Buy instructions from the note, same signature, same synthetic clock.

    First buy: ~1 SOL against a 67.4 SOL / 206.9e12 raw pool -> lands the
    migration print. Second buy in the *same signature*: ~10,997 SOL, drains
    the pool to ~54.5e12 raw. Both share one recv time because a correctly
    stamped synthetic clock draws once per signature, and a live receive
    time is one websocket notification per transaction (observe/trade_decode
    seals every inner event of one signature with the same t_recv_ms).
    """
    first = _row(
        event_index=0,
        quote_reserve=67_405_853_768,
        base_reserve=206_900_000_000_000,
        sol_lamports=1_000_000_000,
        token_raw=3_000_000_000_000,
    )
    second = _row(
        event_index=1,
        quote_reserve=68_393_353_768,  # pool after the first buy
        base_reserve=203_900_000_000_000,
        sol_lamports=10_997_450_000_000,
        token_raw=149_400_000_000_000,
    )
    p1 = print_from_trade_row(first)
    p2 = print_from_trade_row(second)
    assert p1 is not None and p2 is not None
    return p1[1], p2[1]


class CollapseFillableAtomicSignatureTests(unittest.TestCase):
    def test_same_signature_collapses_to_one_post_drain_state(self) -> None:
        pr1, pr2 = _atomic_pair_prints()
        # Sanity: the raw per-event states really are the two different
        # worlds the note describes -- a cheap pre-drain pool (pr1), then a
        # post-drain pool priced far higher (pr2) after the giant buy.
        self.assertGreater(pr2.price_sol, pr1.price_sol * 10)

        prints = finalize_prints([pr1, pr2])
        collapsed = collapse_fillable(prints)

        self.assertEqual(len(collapsed), 1, "one fillable state per signature, not two")
        self.assertEqual(collapsed[0].base_reserve, pr2.base_reserve)
        self.assertEqual(collapsed[0].quote_reserve, pr2.quote_reserve)
        self.assertEqual(collapsed[0].event_index, 1)

    def test_entry_after_migration_only_ever_sees_the_drained_pool(self) -> None:
        """No decision time can land 'inside' the atomic pair.

        Any t >= the shared signature time sees only the post-both-events
        (drained) reserves -- never the attractive pre-drain price the old
        per-event exposure allowed.
        """
        pr1, pr2 = _atomic_pair_prints()
        create = CreateSignal(
            mint=MINT,
            t_signal_ms=T_SHARED_MS - 5_000,
            creator=None,
            signature=None,
            v_sol=None,
            v_token_ui=None,
            mcap_sol=None,
            initial_buy_ui=None,
            sol_amount=None,
        )
        path = MintPath(create=create, prints=finalize_prints([pr1, pr2]))

        for t_ms in (T_SHARED_MS, T_SHARED_MS + 1, T_SHARED_MS + 500, T_SHARED_MS + 30_000):
            state = state_as_of(path, t_ms, allow_anchor=True)
            self.assertIsNotNone(state)
            self.assertEqual(
                state.quote_reserve,
                pr2.quote_reserve,
                f"t={t_ms} exposed the pre-drain state; a paper order landed inside the atomic tx",
            )

    def test_naive_uncollapsed_lookup_would_have_leaked_the_pre_drain_price(self) -> None:
        """Contrast case: this is what the bug looked like before collapsing by signature.

        Walking the *raw* per-event list (no collapse_fillable) and taking
        "the last print at or before t" lets an order land after the first
        Buy and before the second, exactly the exploit the note traced.
        """
        pr1, pr2 = _atomic_pair_prints()
        raw_prints = finalize_prints([pr1, pr2])

        def naive_state_at(prints: list[TapePrint], t_ms: int) -> TapePrint | None:
            chosen = None
            for pr in prints:
                if pr.t_recv_ms <= t_ms and pr.event_index == 0:
                    chosen = pr
            return chosen

        # A naive per-event walk that stops at the first inner event of the
        # signature exposes the cheap, pre-drain pool -- the bug.
        leaked = naive_state_at(raw_prints, T_SHARED_MS)
        self.assertIsNotNone(leaked)
        self.assertEqual(leaked.quote_reserve, pr1.quote_reserve)
        self.assertNotEqual(leaked.quote_reserve, pr2.quote_reserve)

        # The production path (collapse_fillable) never exposes that state.
        collapsed = collapse_fillable(raw_prints)
        self.assertEqual(len(collapsed), 1)
        self.assertNotEqual(collapsed[0].quote_reserve, leaked.quote_reserve)


class SignatureLagDrawTests(unittest.TestCase):
    """The synthetic receive clock must draw once per signature, not per event."""

    def test_per_signature_draw_matches_across_inner_events(self) -> None:
        from tools.graduated_swing import SignatureLag

        lags = SignatureLag()
        lags.begin_file("hour-1")
        draws = iter([111, 222, 333])
        first = lags.get({"signature": SIGNATURE}, lambda: next(draws))
        second = lags.get({"signature": SIGNATURE}, lambda: next(draws))
        other = lags.get({"signature": "different-tx"}, lambda: next(draws))
        self.assertEqual(first, 111)
        self.assertEqual(second, 111, "second inner event of the same signature must reuse the first draw")
        self.assertEqual(other, 222, "a different signature draws independently")


if __name__ == "__main__":
    unittest.main()
