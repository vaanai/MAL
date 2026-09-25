"""Paper signal scorers: follow, crowd, curve, clean-launch. Fixture-only."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools.paper_curve_math import (
    INITIAL_VIRTUAL_SOL_LAMPORTS,
    TOKEN_RAW_OFFSET,
    bonding_progress,
    curve_progress,
)
from tools.paper_price_path import CreateSignal, MintPath, TapePrint
from tools.paper_signal_clean import creator_prior_rugs, emit_clean_signals, sniper_share_as_of
from tools.paper_signal_core import (
    Signal,
    beats_baseline,
    evaluate_variant,
    features_as_of,
    first_per_mint,
    pick_best_exit,
    simulate_signals,
    split_mints_by_time,
)
from tools.paper_signal_crowd import emit_crowd_signals, veto_sniper_bot_wallets
from tools.paper_signal_curve import emit_curve_threshold_signals, emit_migration_signals
from tools.paper_signal_follow import emit_follow_signals, load_follow_jsonl
from tools.paper_tape_scoreboard import EXIT_RULES
from tools.wallet_leaderboard import Trade

T0 = 10_000_000_000
Q0 = 30_000_000_000
B0 = 1_073_000_000_000_000


def _print(
    t_ms: int,
    quote: int = Q0,
    base: int = B0,
    *,
    venue: str = "pump_bonding",
    side: str = "buy",
    slot: int = 10,
    event_index: int = 0,
    sol_lamports: int = 1_000_000,
) -> TapePrint:
    price = quote / (base * 1000)
    return TapePrint(
        t_recv_ms=t_ms,
        slot=slot,
        event_index=event_index,
        venue=venue,
        side=side,
        sol_lamports=sol_lamports,
        quote_reserve=quote,
        base_reserve=base,
        price_sol=price,
        market_cap_sol=price * 1_000_000_000,
    )


def _create(mint: str = "MintA", t_ms: int = T0, **kwargs: object) -> CreateSignal:
    fields: dict[str, object] = dict(
        mint=mint,
        t_signal_ms=t_ms,
        creator="CreatorA",
        signature="sigA",
        v_sol=30.0,
        v_token_ui=1_073_000_000.0,
        mcap_sol=30.0 / 1_073_000_000.0 * 1_000_000_000,
        initial_buy_ui=0.0,
        sol_amount=0.0,
    )
    fields.update(kwargs)
    return CreateSignal(**fields)  # type: ignore[arg-type]


def _path(prints: list[TapePrint], mint: str = "MintA", t_ms: int = T0, **kwargs: object) -> MintPath:
    return MintPath(create=_create(mint, t_ms, **kwargs), prints=list(prints))


def _trade(
    *,
    mint: str,
    trader: str,
    side: str,
    t_ms: int,
    slot: int,
    sol: int = 100_000_000,
    token: int = 1_000_000,
    venue: str = "pump_bonding",
    signature: str | None = None,
) -> Trade:
    return Trade(
        t_ms=t_ms,
        slot=slot,
        mint=mint,
        trader=trader,
        side=side,
        sol_lamports=sol,
        token_raw=token,
        signature=signature or f"sig-{mint}-{trader}-{t_ms}-{side}",
        venue=venue,
        event_index=0,
    )


class CurveProgressTests(unittest.TestCase):
    def test_start_is_zero_graduation_is_one(self) -> None:
        self.assertAlmostEqual(bonding_progress(B0), 0.0)
        self.assertAlmostEqual(bonding_progress(TOKEN_RAW_OFFSET), 1.0)
        self.assertEqual(curve_progress("pumpswap", 1), 1.0)

    def test_features_as_of_ignore_later_prints(self) -> None:
        path = _path([_print(T0), _print(T0 + 5_000, quote=50_000_000_000, base=B0 // 2)])
        feats = features_as_of(path, T0)
        self.assertEqual(feats["f_tape_n"], 1)
        self.assertLess(feats["f_curve_progress"], 0.2)
        later = features_as_of(path, T0 + 5_000)
        self.assertEqual(later["f_tape_n"], 2)
        self.assertGreater(later["f_curve_progress"], feats["f_curve_progress"])


class FollowTests(unittest.TestCase):
    def test_copyable_drops_slot_zero_two_and_keeps_first_mint(self) -> None:
        rows = [
            {
                "type": "follow_signal",
                "mint": "M1",
                "signal_t_ms": T0 + 100,
                "wallet": "L1",
                "features": {"delta_slot_from_create": 2, "buyer_rank": 1},
            },
            {
                "type": "follow_signal",
                "mint": "M1",
                "signal_t_ms": T0 + 400,
                "wallet": "L2",
                "features": {"delta_slot_from_create": 9, "buyer_rank": 4},
            },
            {
                "type": "follow_signal",
                "mint": "M2",
                "signal_t_ms": T0 + 50,
                "wallet": "L1",
                "features": {"delta_slot_from_create": 20},
            },
        ]
        all_sigs = first_per_mint(emit_follow_signals(rows, variant="noisy_v0", copyable_only=False))
        copyable = first_per_mint(emit_follow_signals(rows, variant="noisy_v0_copyable", copyable_only=True))
        self.assertEqual([s.mint for s in all_sigs], ["M2", "M1"])
        self.assertEqual(all_sigs[1].signal_t_ms, T0 + 100)
        self.assertEqual([s.mint for s in copyable], ["M2", "M1"])
        self.assertEqual(copyable[1].signal_t_ms, T0 + 400)

    def test_load_jsonl_skips_bad_lines(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "follow.jsonl"
            path.write_text(
                json.dumps({"type": "follow_signal", "mint": "M1", "signal_t_ms": T0}) + "\nnot json\n",
                encoding="utf-8",
            )
            rows = load_follow_jsonl(path)
            self.assertEqual(len(rows), 1)


class CrowdTests(unittest.TestCase):
    def test_nth_unique_wallet_inside_window_fires(self) -> None:
        trades = [
            _trade(mint="M1", trader="C", side="buy", t_ms=T0, slot=1),
            _trade(mint="M1", trader="S", side="buy", t_ms=T0 + 10, slot=2),  # sniper Δslot=1
            _trade(mint="M1", trader="A", side="buy", t_ms=T0 + 1_000, slot=10),
            _trade(mint="M1", trader="B", side="buy", t_ms=T0 + 1_400, slot=11),
            _trade(mint="M1", trader="D", side="buy", t_ms=T0 + 1_800, slot=12),
        ]
        sigs = emit_crowd_signals(
            trades,
            n=3,
            window_ms=2_000,
            excluded_wallets=set(),
            create_slots={"M1": 1},
            creators={"M1": "C"},
            allowed_mints={"M1"},
        )
        self.assertEqual(len(sigs), 1)
        self.assertEqual(sigs[0].signal_t_ms, T0 + 1_800)
        self.assertEqual(sigs[0].features["unique_wallets"], 3)

    def test_window_expiry_and_vetoed_wallet_do_not_count(self) -> None:
        trades = [
            _trade(mint="M1", trader="A", side="buy", t_ms=T0 + 1_000, slot=10),
            _trade(mint="M1", trader="B", side="buy", t_ms=T0 + 1_100, slot=10),
            _trade(mint="M1", trader="BOT", side="buy", t_ms=T0 + 1_200, slot=10),
            _trade(mint="M1", trader="D", side="buy", t_ms=T0 + 5_000, slot=20),
        ]
        sigs = emit_crowd_signals(
            trades,
            n=3,
            window_ms=1_000,
            excluded_wallets={"BOT"},
            create_slots={"M1": 1},
            creators={},
            allowed_mints={"M1"},
        )
        self.assertEqual(sigs, [])

    def test_sniper_frac_veto(self) -> None:
        trades = []
        for i in range(6):
            trades.append(_trade(mint=f"M{i}", trader="SNIPE", side="buy", t_ms=T0 + i, slot=1))
        trades.append(_trade(mint="MX", trader="ORG", side="buy", t_ms=T0, slot=20))
        trades.append(_trade(mint="MX", trader="ORG", side="buy", t_ms=T0 + 8_000, slot=40))
        slots = {f"M{i}": 1 for i in range(6)}
        slots["MX"] = 1
        vetoed = veto_sniper_bot_wallets(trades, slots)
        self.assertIn("SNIPE", vetoed)
        self.assertNotIn("ORG", vetoed)


class CurveSignalTests(unittest.TestCase):
    def test_threshold_fires_on_crossing_print(self) -> None:
        # ~55% of real tokens bought.
        base = TOKEN_RAW_OFFSET + int(0.45 * (B0 - TOKEN_RAW_OFFSET))
        path = _path([_print(T0), _print(T0 + 2_000, quote=50_000_000_000, base=base, slot=20)])
        sigs = emit_curve_threshold_signals({"MintA": path}, threshold=0.50)
        self.assertEqual(len(sigs), 1)
        self.assertEqual(sigs[0].signal_t_ms, T0 + 2_000)
        self.assertGreaterEqual(sigs[0].features["progress"], 0.50)

    def test_already_past_threshold_on_create_fires_at_t(self) -> None:
        path = _path(
            [],
            v_sol=40.0,
            v_token_ui=30.0 * 1_073_000_000.0 / 40.0,
        )
        sigs = emit_curve_threshold_signals({"MintA": path}, threshold=0.30)
        self.assertEqual(len(sigs), 1)
        self.assertEqual(sigs[0].signal_t_ms, T0)
        self.assertTrue(sigs[0].features["at_create"])

    def test_migration_is_first_pumpswap_print(self) -> None:
        path = _path(
            [
                _print(T0),
                _print(T0 + 3_000, quote=80_000_000_000, base=10_000_000_000, venue="pumpswap", slot=50),
            ]
        )
        sigs = emit_migration_signals({"MintA": path})
        self.assertEqual(sigs[0].signal_t_ms, T0 + 3_000)
        self.assertEqual(sigs[0].variant, "migrate")


class CleanLaunchTests(unittest.TestCase):
    def test_sniper_share_and_prior_rug_gate(self) -> None:
        trades = [
            _trade(mint="OLD", trader="C", side="buy", t_ms=T0 - 10_000, slot=1, venue="pump_bonding"),
            _trade(mint="OLD", trader="C", side="sell", t_ms=T0 - 9_500, slot=2),
            _trade(mint="NEW", trader="C", side="buy", t_ms=T0, slot=10, venue="pump_bonding"),
            _trade(mint="NEW", trader="S1", side="buy", t_ms=T0 + 200, slot=10, sol=80_000_000),
            _trade(mint="NEW", trader="O1", side="buy", t_ms=T0 + 800, slot=20, sol=20_000_000),
        ]
        creates = {
            "OLD": _create("OLD", T0 - 10_000, creator="C"),
            "NEW": _create("NEW", T0, creator="C"),
        }
        creators = {"OLD": "C", "NEW": "C"}
        rugs = creator_prior_rugs(creates, trades, creators)
        self.assertEqual(rugs["NEW"], 1)
        share, *_ = sniper_share_as_of(
            [t for t in trades if t.mint == "NEW"],
            t_ms=T0 + 1_000,
            slot0=10,
        )
        self.assertIsNotNone(share)
        assert share is not None
        self.assertGreater(share, 0.7)
        blocked = emit_clean_signals(
            {"NEW": creates["NEW"]},
            trades,
            create_slots={"NEW": 10},
            creators=creators,
            max_sniper_share=0.50,
            require_no_prior_rug=True,
            prior_rugs=rugs,
            variant="s50_norug",
        )
        self.assertEqual(blocked, [])
        open_gate = emit_clean_signals(
            {"NEW": creates["NEW"]},
            trades,
            create_slots={"NEW": 10},
            creators=creators,
            max_sniper_share=None,
            require_no_prior_rug=False,
            prior_rugs=rugs,
            variant="any",
        )
        self.assertEqual(len(open_gate), 1)


class FillAndSplitTests(unittest.TestCase):
    def test_fill_uses_signal_time_plus_latency(self) -> None:
        path = _path([_print(T0), _print(T0 + 5_000, quote=32_000_000_000, base=B0)])
        sig = Signal(mint="MintA", signal_t_ms=T0 + 4_000, family="follow", variant="t", features={})
        rows = simulate_signals(
            [sig],
            {"MintA": path},
            latency_s=1.0,
            rules=EXIT_RULES[:1],
            tape_end_ms=T0 + 60_000,
        )
        self.assertEqual(rows[0]["t_signal_ms"], T0 + 4_000)
        self.assertEqual(rows[0]["entry_t_ms"], T0 + 5_000)
        self.assertEqual(rows[0]["entry_status"], "filled")

    def test_split_is_earlier_mints_then_later(self) -> None:
        sigs = [
            Signal(mint="B", signal_t_ms=T0 + 20, family="x", variant="v"),
            Signal(mint="A", signal_t_ms=T0 + 10, family="x", variant="v"),
            Signal(mint="C", signal_t_ms=T0 + 30, family="x", variant="v"),
            Signal(mint="D", signal_t_ms=T0 + 40, family="x", variant="v"),
        ]
        train, test = split_mints_by_time(sigs)
        self.assertEqual(train, {"A", "B"})
        self.assertEqual(test, {"C", "D"})

    def test_best_exit_picked_on_train_median(self) -> None:
        # Two mints, train=A. hold_30s loses on A, hold_1m wins on A.
        # OOS mint B is the opposite, so a full-sample pick would differ.
        def rows_for(mint: str, t: int, hold30: int, hold1m: int) -> list[dict]:
            return [
                {
                    "mint": mint,
                    "exit_rule": "hold_30s",
                    "entry_status": "filled",
                    "exit_status": "realized",
                    "pnl_lamports": hold30,
                    "t_signal_ms": t,
                },
                {
                    "mint": mint,
                    "exit_rule": "hold_1m",
                    "entry_status": "filled",
                    "exit_status": "realized",
                    "pnl_lamports": hold1m,
                    "t_signal_ms": t,
                },
            ]

        rows = rows_for("A", T0, -2_000_000, 3_000_000) + rows_for("B", T0 + 10, 9_000_000, -9_000_000)
        picked, meta = pick_best_exit(
            rows,
            mints={"A"},
            size_lamports=50_000_000,
            rules=EXIT_RULES[:2],
            min_n=1,
        )
        self.assertEqual(picked, "hold_1m")
        self.assertEqual(meta["reason"], "max_train_median")

    def test_oos_median_flag(self) -> None:
        self.assertTrue(beats_baseline({"median_sol": -0.001}, {"median_sol": -0.002}))
        self.assertFalse(beats_baseline({"median_sol": -0.003}, {"median_sol": -0.002}))
        self.assertFalse(beats_baseline({"median_sol": None}, {"median_sol": -0.002}))

    def test_evaluate_variant_writes_family_and_features(self) -> None:
        path = _path([_print(T0 + i * 1_000, quote=Q0 + i * 10_000_000) for i in range(40)])
        sigs = [
            Signal(
                mint="MintA",
                signal_t_ms=T0,
                family="clean",
                variant="norug",
                features={"kind": "clean_launch", "creator_prior_rugs": 0},
            )
        ]
        ev = evaluate_variant(sigs, {"MintA": path}, latency_s=1.0, tape_end_ms=T0 + 120_000)
        self.assertEqual(ev["signals_n"], 1)
        self.assertIn(ev["best_exit"], {r.rule_id for r in EXIT_RULES})
        row = ev["labels"][0]
        self.assertEqual(row["f_sig_kind"], "clean_launch")
        self.assertEqual(row["signal_family"], "clean")


if __name__ == "__main__":
    unittest.main()
