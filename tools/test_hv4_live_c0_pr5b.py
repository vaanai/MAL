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
from tools import test_arena_twin_writer as TW
from tools import test_hv4_live_c0_pr3 as pr3
from tools.arena_twin_writer import _day as twin_day

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

    def test_a_missing_parity_trip_field_is_a_mismatch_one_per_field(self):
        rows = trip_rows("M1", T0, T0)
        good = {"trip_id": "M1", "stake_lamports": S, "buy_fee_lamports": BUY_FEE, "sell_fee_lamports": SELL_FEE_TOTAL, "exit_landed": True}
        for k in ("buy_fee_lamports", "stake_lamports", "exit_landed", "sell_fee_lamports"):
            t = {x: v for x, v in good.items() if x != k}
            with self.assertRaises(tie.TieoutMismatch, msg=k):
                tie.build(rows, self._state(), T0 + DAY_MS, {"M1": t})
        with self.assertRaises(tie.TieoutMismatch):  # exit_landed false while the rows have a landed sell
            tie.build(rows, self._state(), T0 + DAY_MS, {"M1": {**good, "exit_landed": False}})
        with self.assertRaises(tie.TieoutMismatch):  # exit_landed true with no landed sell in the rows
            tie.build(rows[:3], self._state(status="open"), T0 + DAY_MS, {"M1": good})

    def test_module_doc_declares_the_priority_residual(self):
        self.assertNotIn("not recomputed", tie.__doc__)
        for w in ("DECLARED RESIDUAL", "FALSE `tieout_mismatch`", "last_valid_block_height", "unresolved_hard_halts == []"):
            self.assertIn(w, tie.__doc__)

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


RENT = 2_039_280


def chain_for(rows, baseline=5_000_000_000):
    """The synthetic chain of one trip: txs with pre/post balances in slot order, consistent with the fill rows (base fee 5,000 + priority per send)."""
    txs, bal, slot = [], baseline, 100
    for r in rows:
        if not isinstance(r.get("landed"), bool):
            continue
        prio = r["priority_lamports"]
        if r["kind"] == "buy" and r["landed"]:
            d, extra = -r["sol_spent_lamports"], {"kind": "buy", "sol_paid": r["spend_lamports"], "rent_paid": RENT}
        elif r["kind"] == "sell" and r["landed"]:
            d, extra = r["sell_net_lamports"], {"kind": "sell", "sol_received": r["sol_received_lamports"], "rent_refunded": RENT}
        else:
            d, extra = -(5_000 + prio), {"kind": "sell", "status": "failed", "sol_received": 0}
        slot += 10
        txs.append({"signature": r["signature"], "slot": slot, "status": extra.pop("status", "ok"), "pre_balance": bal, "post_balance": bal + d,
                    "fee_lamports": 5_000, "priority_fee_lamports": prio, **extra})
        bal += d
    balance = {"lamports": bal, "context_slot": slot + 5, "commitment": "finalized", "finalized_block_height": 10_000}
    return txs, baseline, balance


class TripEndToEndTests(TmpCase):
    """Precondition 4: one landed buy and one ESCALATED sell (2 reverts, then the 0.65 rung at the 150,000 priority) go fake executor rows -> bridge ->
    recorder -> pinned twin wrapper -> arena_live_parity (the real tool) -> tie-out export -> the real tie-out arithmetic. Seeded mismatches latch (the bridge's latch is what blocks the buy), and a stuck escalated sell is the trip-bearing rc 11."""

    _env = pr3.GateWiringTests._env
    _prime_twins = pr3.GateWiringTests._prime_twins
    _bridge = pr3.GateWiringTests._bridge

    def _setup(self, spend=None, sell_prios=(55_000, 55_000, 150_000)):
        e = self._env()
        e.start()
        e.entry_sz("o1")
        self._prime_twins(e)
        b = self._bridge(e, pr3._Runner())
        b.tick()
        o = b.state.orders["o1"]
        mint = o["mint"]
        rows = trip_rows(mint, e.clock(), o["decision_ms"], spend=o["stake_lamports"] if spend is None else spend, sell_prios=sell_prios)
        fd = Path(self.tmp) / "state" / "probe"
        fd.mkdir(parents=True, exist_ok=True)
        (fd / "fills.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
        e.port.ledger = [(r["kind"], mint, {k: v for k, v in r.items() if k not in ("kind", "mint", "ts_ms", "book")}) for r in rows]
        self._rows = rows
        return e, b, o, mint, rows

    def _land(self, b, mint, e):
        b.apply_events([{"event": "buy_landed", "mint": mint, "landed_slot": 110}, {"event": "sell_landed", "mint": mint, "landed_slot": 150, "pnl_lamports": -1_000_000}])
        b.feed_ledger()
        e.clock.t = max(r["ts_ms"] for r in self._rows) + 1_000  # no stamp may be older than the last landed tx

    def _twin(self, e, b, o, mint):
        d = Path(self.tmp) / "synthetic-ledger-e2e"
        d.mkdir(exist_ok=True)
        row = ledger_row(mint=mint, entry_ms=o["decision_ms"] + 400, bot_id=ARM)
        row["stack"] = dict(c0.HV4_STACK_PIN, sps_entry=0.4, sps_exit=0.4)
        (d / f"trades-{twin_day(o['decision_ms'] + 400)}.jsonl").write_text(json.dumps(row) + "\n")
        return tw.main(["--trips", str(b.gates.recorder.trips_path), "--ledger-dir", str(d), "--bot-id", ARM, "--out", e.raw["twins_file"], "--now-ms", str(e.clock())])

    def test_escalated_trip_flows_through_to_rc0_and_a_matching_tieout(self):
        e, b, o, mint, rows = self._setup()
        self._land(b, mint, e)
        t = b.gates.recorder.trips[mint]
        self.assertEqual((t["stake_lamports"], t["buy_fee_lamports"], t["sell_fee_lamports"], t["exit_landed"]), (S, BUY_FEE, SELL_FEE_TOTAL, True))
        self.assertEqual(self._twin(e, b, o, mint), 0)
        twin = json.loads(Path(e.raw["twins_file"]).read_text().splitlines()[0])
        self.assertEqual((twin["state"], twin["buy_send_lamports"], twin["sell_send_lamports"]), ("filled", BUY_FEE, SELL_FEE_TOTAL))  # twin priced at the fees SENT
        self.assertEqual(self._run(e, b), 0)
        res = json.loads((Path(self.tmp) / "par" / "parity-result.json").read_text())
        self.assertIsInstance(res, dict)
        self.assertEqual(b.state.latches, {})
        self.assertIsNone(b.gate_refusal(e.clock()))
        # the day closes; the exporter writes the book once
        day = ato.utc_day_str(rows[0]["ts_ms"])
        e.clock.t = tie.day_bounds(day)[1] + 60_000
        self.assertEqual(b.export_tieout(day), "created")
        self.assertEqual(b.export_tieout(day), "same")
        doc = json.loads((Path(self.tmp) / "tie" / tie.ledger_name(day)).read_text())
        self.assertEqual((len(doc["sends"]), doc["open_mints"], doc["trips"][0]["closed"]), (4, [], True))
        self.assertEqual(doc["bridge"]["trip_fees"][mint]["sell_priorities"], [55_000, 55_000, 150_000])
        self.assertEqual(doc["bridge"]["positions"], [])
        # the real tie-out arithmetic on a chain that matches the fills
        txs, base, balance = chain_for(rows)
        r = ato.reconcile(txs, doc, {"programs_covered": list(ato.TOKEN_PROGRAMS), "accounts": []}, base, balance, {})
        self.assertEqual(r["problems"], [])
        self.assertTrue(r["signed_off"])
        self.assertEqual(r["trip_diffs"][0]["diff"], 0)
        self.assertEqual(r["split"]["failed_tx_fees"], 2 * FAIL_FEE)
        # a chain that charged one lamport more than the fills say does not tie
        txs[-1]["post_balance"] -= 1
        bad = ato.reconcile(txs, doc, {"programs_covered": list(ato.TOKEN_PROGRAMS), "accounts": []}, base, balance, {})
        self.assertFalse(bad["signed_off"])

    def _run(self, e, b):
        b._chain_ok_ms = e.clock()  # a verified decision-log read just now (what tick() stamps)
        return b.gates.run_parity(e.clock())

    def _parity_result(self):
        return json.loads((Path(self.tmp) / "par" / "parity-result.json").read_text())

    def test_seeded_wrong_stake_latches_and_the_parity_result_lists_it(self):
        e, b, o, mint, rows = self._setup(spend=S - 5_000_000)
        self._land(b, mint, e)
        self.assertIn("stake_mismatch", b.state.latches)
        self.assertEqual(self._twin(e, b, o, mint), 0)
        rc = self._run(e, b)
        res = self._parity_result()
        # FINDING (see the PR body): arena_live_parity exits 11 only for P4 / out_of_rule_entry / stuck_position. `unresolved_hard_halts` in its state.json makes the
        # verdict "parity not shown" and is listed, but does not change the exit code, so the BRIDGE's latch is what blocks the buy.
        self.assertEqual(res["unresolved_hard_halts"], ["stake_mismatch"])
        self.assertFalse(res["match"])
        self.assertEqual(rc, 0)
        e.entry_sz("o2")
        b.tick()
        self.assertEqual(e.log_rows("entry_refused")[-1]["why"], "latched:stake_mismatch")
        # the exporter refuses the same book: the landed buy did not spend the order's S
        day = ato.utc_day_str(rows[0]["ts_ms"])
        e.clock.t = tie.day_bounds(day)[1] + 60_000
        self.assertEqual(b.export_tieout(day), "mismatch")
        self.assertIn("tieout_mismatch", b.state.latches)
        self.assertFalse((Path(self.tmp) / "tie" / tie.ledger_name(day)).exists())

    def test_seeded_wrong_fee_latches_tieout_mismatch(self):
        e, b, o, mint, rows = self._setup()
        self._land(b, mint, e)
        self.assertEqual(b.state.latches, {})
        b.gates.recorder.trips[mint]["sell_fee_lamports"] -= 155_000 - 60_000  # the trip says the sell went out unescalated: the twin would be priced at the wrong fee
        b.gates.recorder._save()
        day = ato.utc_day_str(rows[0]["ts_ms"])
        e.clock.t = tie.day_bounds(day)[1] + 60_000
        self.assertEqual(b.export_tieout(day), "mismatch")
        self.assertIn("sell_fee_lamports", b.state.latches["tieout_mismatch"])
        self.assertFalse((Path(self.tmp) / "tie" / tie.ledger_name(day)).exists())
        self._run(e, b)
        self.assertIn("tieout_mismatch", self._parity_result()["unresolved_hard_halts"])
        self.assertIn("tieout_mismatch", c0p.HARD_LATCHES)

    def test_escalated_sell_that_never_lands_is_rc11_and_latches(self):
        """The trip-bearing hard exit of the real parity tool: the buy landed, two sells reverted, the escalated resend never landed. 600 s after the exit bound the
        position is stuck (P4-class hard halt): rc 11, STOP, and the bridge latches parity_hard_halt."""
        e, b, o, mint, rows = self._setup()
        rows = rows[:3]  # buy + the two reverted sells; no landed sell
        e.port.ledger = e.port.ledger[:3]
        b.apply_events([{"event": "buy_landed", "mint": mint, "landed_slot": 110}])
        b.feed_ledger()
        t = b.gates.recorder.trips[mint]
        self.assertFalse(t["exit_landed"])
        e.clock.t = t["exit_bound_ms"] + 650_000
        self.assertEqual(self._twin(e, b, o, mint), 0)
        self.assertEqual(self._run(e, b), 11)
        self.assertTrue(Path(c0p.parity_stop_path(e.raw["parity_dir"])).exists())  # the tools' STOP lives in parity_dir (tools/test_hv4_c0_parity_stop.py)
        self.assertTrue(b._files()[1])
        self.assertIn("stuck_position", self._parity_result()["hard_halts"])
        b._latch_parity_hard()
        self.assertIn("parity_hard_halt", b.state.latches)

    def test_wrong_signed_priority_in_the_fills_is_a_mismatch(self):
        e, b, o, mint, rows = self._setup(sell_prios=(55_000, 55_000, 100_000))
        self._land(b, mint, e)
        day = ato.utc_day_str(rows[0]["ts_ms"])
        e.clock.t = tie.day_bounds(day)[1] + 60_000
        self.assertEqual(b.export_tieout(day), "mismatch")
        self.assertIn("tieout_mismatch", b.state.latches)

    def test_open_book_exports_the_position_and_the_latches(self):
        e, b, o, mint, rows = self._setup()
        b.apply_events([{"event": "buy_landed", "mint": mint, "landed_slot": 110}])
        e.port.ledger = e.port.ledger[:1]
        (Path(self.tmp) / "state" / "probe" / "fills.jsonl").write_text(json.dumps(rows[0]) + "\n")
        b.feed_ledger()
        b._latch("stuck_sell", "synthetic")
        day = ato.utc_day_str(rows[0]["ts_ms"])
        e.clock.t = tie.day_bounds(day)[1] + 60_000
        self.assertEqual(b.export_tieout(day), "created")
        doc = json.loads((Path(self.tmp) / "tie" / tie.ledger_name(day)).read_text())
        self.assertEqual(doc["open_mints"], [mint])
        self.assertEqual([(p["mint"], p["status"]) for p in doc["bridge"]["positions"]], [(mint, "open")])
        self.assertIn("stuck_sell", doc["bridge"]["latches"])

    def test_an_open_day_is_refused(self):
        e = self._env()
        b = self._bridge(e, pr3._Runner())
        with self.assertRaises(ValueError):
            b.export_tieout(ato.utc_day_str(e.clock()))  # the day has not ended

    def test_latch_names_are_hard_and_a_landed_buy_without_an_order_is_a_stake_mismatch(self):
        for name in ("tieout_mismatch", c0.FEED_BREAK_LATCH, c0.A3_LATCH):
            self.assertIn(name, alb.LATCHES)
            self.assertIn(name, c0p.HARD_LATCHES)
        e, b, o, mint, rows = self._setup()
        e.port.ledger = [("buy", "NO-SUCH-MINT", {k: v for k, v in rows[0].items() if k not in ("kind", "mint", "ts_ms", "book")})]
        b.feed_ledger()
        self.assertIn("stake_mismatch", b.state.latches)
        self.assertIn("landed_buy_without_order", b.state.latches["stake_mismatch"])

    def test_removal_precondition_still_refuses(self):
        self.assertEqual(c0.start_refusal("/nonexistent-root", True, ARM), "hv4_c0_incomplete")


if __name__ == "__main__":
    unittest.main()
