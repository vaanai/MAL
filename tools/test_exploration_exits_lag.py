"""exit_land_k (exit lag) in tools/exploration_exits.py. Synthetic prints, no tape, no network. Exploration only."""

from __future__ import annotations

import unittest

from tools.exploration_exits import SPECS, _curve, eval_spec, score_migration
from tools.latency_curve import SLOT_MS, _Mint, _state_index, _try_buy
from tools.test_exploration_exits import _print

T0 = 1_700_000_000_000
BASE = 400_000_000_000_000


def _mint(prints: list, mig_slot: int = 100) -> _Mint:
    mint = _Mint(prints[0].slot, prints[0].t_recv_ms, 0, None)
    mint.had_bond = True
    for pr in prints:
        mint.add(pr)
    mint.mig_slot = mig_slot
    mint.mig_ms = T0
    return mint


def _path() -> list:
    # entry at 100; +60% at slot 105 (tp50 trigger); the price then falls at 106 and again at 107
    return [
        _print(T0, 100, 80_000_000_000, BASE),
        _print(T0 + 2_000, 105, 128_000_000_000, BASE),
        _print(T0 + 2_400, 106, 104_000_000_000, BASE),
        _print(T0 + 2_800, 107, 90_000_000_000, BASE),
        _print(T0 + 3_200, 108, 90_000_000_000, BASE),
    ]


def _by_spec(rows: list) -> dict:
    return {r["spec"]: r for r in rows}


class ExitLagParityTests(unittest.TestCase):
    def test_default_zero_reproduces_existing_rows_for_every_spec(self) -> None:
        through = T0 + 40 * 60 * 1000
        base = score_migration(_mint(_path()), _curve(), tape_through_ms=through)
        zero = score_migration(_mint(_path()), _curve(), tape_through_ms=through, exit_land_k=0)
        self.assertEqual(len(base), len(SPECS))
        self.assertEqual(base, zero)

    def test_zero_equals_the_frozen_entry_constant_for_event_exits(self) -> None:
        # the frozen exit lands ENTRY_LAND_K = 1 slot after the trigger, so exit_land_k=1 is identical to 0 for tp/sl
        through = T0 + 40 * 60 * 1000
        a = _by_spec(score_migration(_mint(_path()), _curve(), tape_through_ms=through, exit_land_k=0))
        b = _by_spec(score_migration(_mint(_path()), _curve(), tape_through_ms=through, exit_land_k=1))
        for sid in ("tpsl_tp50_sl30", "tpsl_tp50_sl20", "tpsl_tp75_sl30"):
            self.assertEqual(a[sid], b[sid])


class ExitLagFillTests(unittest.TestCase):
    def _eval(self, spec_id: str, k: int, through: int = T0 + 40 * 60 * 1000):
        fills = _path()
        idx = _state_index(fills, 100 + 1, "start")
        state = fills[idx]
        buy = _try_buy(state, 500_000_000, 0, None)
        self.assertIsNotNone(buy)
        spec = next(s for s in SPECS if s["id"] == spec_id)
        return eval_spec(spec, fills, idx, state.price_sol, buy, state.venue, state.t_recv_ms, through, 500_000_000, k)

    def test_lag_moves_the_fill_to_the_later_state(self) -> None:
        now = self._eval("tpsl_tp50_sl30", 0)  # trigger print slot 105 -> fill state: last print before slot 106 (the +60% print)
        later = self._eval("tpsl_tp50_sl30", 2)  # last print before slot 107 (the slot-106 print, price down)
        self.assertIsNotNone(now)
        self.assertIsNotNone(later)
        self.assertGreater(now[0], later[0])  # net0: filling at the lower price pays less
        self.assertGreater(now[1], later[1])  # gross too

    def test_lag_past_the_tape_end_is_censored(self) -> None:
        # trigger print at T0+2000; the k=2 chain ends at T0+2000+800
        self.assertIsNotNone(self._eval("tpsl_tp50_sl30", 2, through=T0 + 2_800))
        self.assertIsNone(self._eval("tpsl_tp50_sl30", 2, through=T0 + 2_799))

    def test_time_cap_exit_lands_k_slots_after_the_deadline(self) -> None:
        # a flat path never triggers: the 5 min cap fires at landing + 5 min
        fills = [_print(T0, 100, 80_000_000_000, BASE), _print(T0 + 100_000, 110, 80_000_000_000, BASE)]
        state = fills[0]
        buy = _try_buy(state, 500_000_000, 0, None)
        spec = next(s for s in SPECS if s["id"] == "timecap_5m_tp50_sl30")
        deadline = state.t_recv_ms + 5 * 60 * 1000

        def run(through: int, k: int):
            return eval_spec(spec, fills, 0, state.price_sol, buy, state.venue, state.t_recv_ms, through, 500_000_000, k)

        self.assertIsNotNone(run(deadline, 0))
        self.assertIsNone(run(deadline, 2))
        self.assertIsNotNone(run(deadline + 2 * SLOT_MS, 2))


if __name__ == "__main__":
    unittest.main()
