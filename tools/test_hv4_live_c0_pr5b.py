"""PR 5b of the HV4 live adapter: the daily tie-out exporter (removal precondition 8) and the trip-bearing end-to-end (precondition 4).

Synthetic fixtures only: no HV4 ledger, decision row, read record, bots.json or job log is read; no network, no key. The "executor" is the existing fake (a port
that hands the bridge fill rows, as `LiveExecutorPort.drain_ledger` does) and the "chain" is the tie-out's own fixture shape (txs with pre/post balances)."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from tools import arena_live_parity as alp
from tools import arena_live_bridge as alb
from tools import arena_tieout as ato
from tools import arena_tieout_ledger as atl
from tools import hv4_c0_parity as c0p
from tools import hv4_c0_tieout as tie
from tools import hv4_c0_twin as tw
from tools import hv4_live_c0 as c0
from tools.test_arena_live_bridge import T0, TmpCase
from tools.test_arena_twin_writer import ledger_row
from tools.test_hv4_live_c0 import ARM
from tools.test_hv4_live_c0_pr3 import GateWiringTests, _Runner  # noqa: F401 - the env and the in-process parity runner

DAY_MS = 86_400_000
BUY_FEE, FAIL_FEE, LAND_FEE = 60_000, 60_000, 155_000  # 55k + 5k, 55k + 5k (x2 reverts), 150k + 5k
SELL_FEE_TOTAL = FAIL_FEE * 2 + LAND_FEE
S = c0.C0_LIMITS["stake_lamports"]


def fill(kind, mint, ts, sig, landed, prio, **kw):
    r = {"kind": kind, "ts_ms": ts, "mint": mint, "book": "c0", "signature": sig, "landed": landed, "priority_lamports": prio, "fee_lamports": prio + 5_000,
         "confirm_seen_ms": ts, **kw}
    return r


def trip_rows(mint, ts, decision_ms, spend=S, buy_prio=55_000, sell_prios=(55_000, 55_000, 150_000)):
    """One landed buy and one escalated sell: two reverted sells (fee only) then the landed one at the escalated priority. Wallet deltas are consistent."""
    tokens = 1_000_000
    rows = [fill("buy", mint, ts, "sigB", True, buy_prio, spend_lamports=spend, tokens_received=tokens, sol_spent_lamports=spend + 2_039_280 + 5_000 + buy_prio,
                 expected_tokens=tokens, tokens_source="meta", decision_t_ms=decision_ms)]
    for i, p in enumerate(sell_prios[:-1]):
        rows.append(fill("sell", mint, ts + 5_000 * (i + 1), f"sigF{i}", False, p, fail_class="slippage", err="x", sell_attempt=i))
    got = spend - 700_000
    rows.append(fill("sell", mint, ts + 20_000, "sigS", True, sell_prios[-1], tokens=tokens, sol_received_lamports=got, sell_net_lamports=got + 2_039_280 - sell_prios[-1] - 5_000,
                     buy_cost_lamports=spend + 2_039_280 + 5_000 + buy_prio, pnl_lamports=-1_000_000, exit_reason="book_exit", sell_attempts=len(sell_prios),
                     failed_attempt_cost_lamports=sum(p + 5_000 for p in sell_prios[:-1])))
    return rows


class ExporterUnitTests(TmpCase):
    def _state(self, mint="M1", status="closed", stake=S):
        return {"orders": {"o1": {"order_id": "o1", "mint": mint, "status": status, "stake_lamports": stake}}, "latches": {}}

    def test_export_has_the_three_lists_the_gate_requires_and_the_fees_signed(self):
        rows = trip_rows("M1", T0, T0 - 100)
        doc = tie.build(rows, self._state(), T0 + DAY_MS)
        for k in ato.LEDGER_KEYS:
            self.assertIsInstance(doc[k], list)
        self.assertEqual([s["status"] for s in doc["sends"]], ["landed"] * 4)
        self.assertEqual(sorted(s["side"] for s in doc["sends"]), ["buy", "sell", "sell", "sell"])
        (t,) = doc["trips"]
        self.assertEqual((t["trip_id"], t["buy_sig"], t["sell_sig"], t["closed"]), ("M1", "sigB", "sigS", True))
        self.assertEqual(doc["open_mints"], [])
        f = doc["bridge"]["trip_fees"]["M1"]
        self.assertEqual((f["buy_fee_lamports"], f["sell_fee_lamports"], f["sell_priorities"]), (BUY_FEE, SELL_FEE_TOTAL, [55_000, 55_000, 150_000]))
        self.assertEqual(f["sell_fee_lamports"], c0.fees_sent(1, [55_000, 55_000, 150_000])[1])
        self.assertEqual([x["priority_lamports"] for x in doc["bridge"]["fills"]], [55_000, 55_000, 55_000, 150_000])
        # the tie-out's own input check accepts the document (extra "bridge" key ignored)
        ato.check_inputs(doc, {"programs_covered": list(ato.TOKEN_PROGRAMS), "accounts": []},
                         {"lamports": 1, "context_slot": 1, "commitment": "finalized", "finalized_block_height": 1})

    def test_open_position_is_exported_open_and_positions_listed(self):
        rows = trip_rows("M1", T0, T0 - 100)[:1]
        doc = tie.build(rows, self._state(status="open"), T0 + DAY_MS)
        self.assertEqual(doc["open_mints"], ["M1"])
        self.assertEqual(doc["trips"][0]["closed"], False)
        self.assertEqual([p["mint"] for p in doc["bridge"]["positions"]], ["M1"])

    def test_rows_after_the_day_are_cut(self):
        rows = trip_rows("M1", T0, T0 - 100)
        for r in rows[2:]:
            r["ts_ms"] = T0 + DAY_MS + 1
        doc = tie.build(rows, self._state(status="open"), T0 - T0 % DAY_MS + DAY_MS)
        self.assertEqual(doc["open_mints"], ["M1"])

    def test_latches_are_exported(self):
        st = self._state()
        st["latches"] = {"stake_mismatch": "x"}
        self.assertEqual(tie.build(trip_rows("M1", T0, T0), st, T0 + DAY_MS)["bridge"]["latches"], {"stake_mismatch": "x"})

    def test_fee_audit_refusals(self):
        def bad(rows, **kw):
            with self.assertRaises(tie.TieoutMismatch):
                tie.build(rows, self._state(**kw), T0 + DAY_MS)
        rows = trip_rows("M1", T0, T0)
        rows[0]["fee_lamports"] += 1  # fee != priority + 5,000
        bad(rows)
        bad(trip_rows("M1", T0, T0, buy_prio=56_000))  # buy not at the pin
        bad(trip_rows("M1", T0, T0, sell_prios=(55_000, 55_000, 100_000)))  # a priority outside the pin
        bad(trip_rows("M1", T0, T0, sell_prios=(55_000, 55_000, 55_000)))  # third attempt below the schedule (escalated after 2 reverts)
        bad(trip_rows("M1", T0, T0, spend=S - 1))  # landed buy spent other than the order's S
        rows = trip_rows("M1", T0, T0)
        del rows[1]["fee_lamports"]
        bad(rows)

    def test_parity_trips_must_carry_the_same_fees_and_stake(self):
        rows = trip_rows("M1", T0, T0)
        good = {"M1": {"trip_id": "M1", "stake_lamports": S, "buy_fee_lamports": BUY_FEE, "sell_fee_lamports": SELL_FEE_TOTAL, "exit_landed": True}}
        tie.build(rows, self._state(), T0 + DAY_MS, good)
        for k, v in (("sell_fee_lamports", SELL_FEE_TOTAL - 1), ("buy_fee_lamports", BUY_FEE + 1), ("stake_lamports", S + 1)):
            with self.assertRaises(tie.TieoutMismatch):
                tie.build(rows, self._state(), T0 + DAY_MS, {"M1": {**good["M1"], k: v}})
        with self.assertRaises(tie.TieoutMismatch):
            tie.build(rows, self._state(), T0 + DAY_MS, {"NOPE": good["M1"]})

    def test_unusable_inputs_are_refused(self):
        with self.assertRaises(atl.BadLedger):
            tie.build(trip_rows("M1", T0, T0), {"latches": {}}, T0 + DAY_MS)
        r = trip_rows("M1", T0, T0)
        del r[0]["ts_ms"]
        with self.assertRaises(atl.BadLedger):
            tie.build(r, self._state(), T0 + DAY_MS)
        r = trip_rows("M1", T0, T0)
        r[1]["landed"] = 1  # exporter: landed must be a bool
        with self.assertRaises(atl.BadLedger):
            tie.build(r, self._state(), T0 + DAY_MS)
        with self.assertRaises(atl.BadLedger):
            tie.day_bounds("2026-13-40")

    def test_expired_send_without_a_block_height_is_exported_pending_for_the_gate(self):
        rows = trip_rows("M1", T0, T0)
        rows.append({"kind": "buy", "ts_ms": T0 + 50, "mint": "M2", "signature": "sigX", "landed": False, "fail_class": "expired", "spend_lamports": S})
        doc = tie.build(rows, self._state(), T0 + DAY_MS)
        x = [s for s in doc["sends"] if s["signature"] == "sigX"][0]
        self.assertEqual((x["status"], x["last_valid_block_height"]), ("expired", None))  # the tie-out then calls it pending, not a confirmed drop: fail closed

    def test_write_once_daily(self):
        d = Path(self.tmp) / "tie"
        doc = tie.build(trip_rows("M1", T0, T0), self._state(), T0 + DAY_MS)
        self.assertEqual(tie.write_once(d, "2026-10-26", doc), "created")
        p = d / "LEDGER-2026-10-26.json"
        self.assertEqual(oct(p.stat().st_mode & 0o777), "0o644")
        self.assertEqual(json.loads(p.read_text())["day"], "2026-10-26")
        # same book, later state: no-op
        doc2 = json.loads(json.dumps(doc))
        doc2["bridge"]["latches"] = {"x": "y"}
        before = p.read_bytes()
        self.assertEqual(tie.write_once(d, "2026-10-26", doc2), "same")
        self.assertEqual(p.read_bytes(), before)
        # a different book never overwrites
        doc3 = json.loads(json.dumps(doc))
        doc3["bridge"]["trip_fees"]["M1"]["sell_fee_lamports"] += 1
        with self.assertRaises(tie.WriteConflict):
            tie.write_once(d, "2026-10-26", doc3)
        self.assertEqual(p.read_bytes(), before)
        p.write_text("{torn")
        with self.assertRaises(tie.WriteConflict):
            tie.write_once(d, "2026-10-26", doc)

    def test_only_a_closed_day_is_exported_and_cli(self):
        d = Path(self.tmp)
        fills, state = d / "fills.jsonl", d / "state.json"
        fills.write_text("".join(json.dumps(r) + "\n" for r in trip_rows("M1", T0, T0)))
        state.write_text(json.dumps(self._state()))
        day = ato.utc_day_str(T0)
        start, end = tie.day_bounds(day)
        argv = ["--fills", str(fills), "--state", str(state), "--out-dir", str(d / "tie"), "--day", day]
        self.assertEqual(tie.main(argv + ["--now-ms", str(end - 1)]), 2)  # the day has not ended
        self.assertFalse((d / "tie").exists())
        self.assertEqual(tie.main(argv + ["--now-ms", str(end)]), 0)
        self.assertEqual(tie.main(argv + ["--now-ms", str(end + 5)]), 0)  # same
        rows = trip_rows("M1", T0, T0, sell_prios=(55_000, 55_000, 100_000))
        fills.write_text("".join(json.dumps(r) + "\n" for r in rows))
        self.assertEqual(tie.main(argv + ["--now-ms", str(end)]), 3)  # fee audit
        self.assertEqual(tie.main(argv[:-2] + ["--day", "bad", "--now-ms", str(end)]), 2)


if __name__ == "__main__":
    unittest.main()
