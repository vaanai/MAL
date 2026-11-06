"""Tests for tools/r4bt_c0_parity.py. SYNTHETIC fixtures only (tmp dirs); no real twin, ledger, tape or declog is opened."""

from __future__ import annotations

import hashlib
import json
import os
import unittest
from pathlib import Path
import tempfile

from tools import r4bt_c0_parity as RP
from tools.r4bt_c0_executor import ENGINE_SHA256, LEDGER_NAME, REGISTERED_W, RUN_RECORD_NAME, RUN_SCHEMA, TWIN_ROW_GRACE_MS, TWIN_SCHEMA, TWINS_NAME

NOW = 2_000_000_000_000
STAKE_SOL = 0.05
STAKE_LAMPORTS = 50_000_000
PNL = 1_000_000


def order(i, *, d_land=2, k19=4, k3=6, sps=2.5, buy_px=1.0, sell_px=1.0, net=PNL, reason="time_stop", gap=False, restart=False, landed=True, exit_landed=True,
          twin="priced", entry_ms=None, exit_late=False, with_exit_ms=True):
    """(ledger row, twin row) for one synthetic order. The twin's corrected_slot is 1000 + 10 i; the buy lands d_land slots after it."""
    oid = f"o{i:04d}"
    e = entry_ms if entry_ms is not None else NOW - 30 * 3_600_000 + i * 1000
    corrected = 1000 + 10 * i
    led = {"order_id": oid, "sent": True, "pool": "pumpswap", "mint": f"M{i}", "rank": 1, "t_mark": 60, "stake_sol": STAKE_SOL, "entry_ms": e,
           "exit_ms": e + 3_600_000 if with_exit_ms else None, "entry_fee_lamports": 505_000, "exit_fee_lamports": 505_000, "restart": restart, "gap_hold": gap,
           "decision_slot": corrected - 3, "landed_slot_entry": corrected + d_land if landed else None, "landed_slot_exit": corrected + 9000 if exit_landed else None,
           "exit_reason": reason, "live_buy_price": buy_px}
    if exit_landed:
        led.update(live_sell_price=sell_px, live_net_lamports=net)
        if exit_late:
            led["exit_landed_ms"] = e + RP.MAX_HOLD_MS + RP.BACKSTOP_EXTRA_MS + 5_000
    t = {"schema": TWIN_SCHEMA, "order_id": oid, "status": twin, "stake_sol": STAKE_SOL, "decision_slot_sd_t": corrected - 3,
         "entry_leg": {"effective_slot": corrected - 3, "corrected_slot": corrected, "sps_entry": sps, "k_1p9": k19, "k_3p0": k3, "landing_slot": corrected + k19}}
    if twin == "priced":
        t["entry_leg"].update(Qe=100.0, Be=100.0)
        t["exit_leg"] = {"Qx": 200.0, "Bx": 100.0}
        leg = {"pnl_e1_excluded_lamports": PNL, "pnl_e1_charged_lamports": PNL - 50_000, "e1_floor_extra_lamports": 50_000, "state": "filled", "filled": True, "strand": False}
        t["nofail"] = {"total": dict(leg), "sc": dict(leg)}
        t["rent_charged"] = {"total": dict(leg), "sc": dict(leg)}
    elif twin == "pending_tape":
        pass
    return led, t


def write_dir(d, pairs, *, drop_twin=(), run_overrides=None, attempts=None):
    d = Path(d)
    (d / LEDGER_NAME).write_text("".join(json.dumps(p[0]) + "\n" for p in pairs))
    tw = "".join(json.dumps(p[1]) + "\n" for p in pairs if p[0]["order_id"] not in drop_twin)
    (d / TWINS_NAME).write_text(tw)
    rec = {"schema": RUN_SCHEMA, "status": "ok", "run_ms": NOW - 1000, "heartbeat_ms": NOW - 1000, "w_sha": REGISTERED_W, "engine_sha256": ENGINE_SHA256,
           "twins_sha256": hashlib.sha256(tw.encode()).hexdigest()}
    rec.update(run_overrides or {})
    (d / RUN_RECORD_NAME).write_text(json.dumps(rec))
    if attempts is not None:
        (d / "attempts.jsonl").write_text("".join(json.dumps(a) + "\n" for a in attempts))
        return str(d / "attempts.jsonl")
    return None


def clean_attempts(n=30, fail=0, p=0.1):
    return [{"attempt_id": f"a{i}", "decision_ms": NOW - 40 * 3_600_000 + i, "landed": i >= fail, "landed_ms": NOW if i >= fail else None, "failed_or_expired": i < fail,
             "p_fail_press": p} for i in range(n)]


class Base(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.d = self._t.name
        self.addCleanup(self._t.cleanup)

    def go(self, pairs, **kw):
        att = write_dir(self.d, pairs, attempts=kw.pop("attempts", None), **kw)
        return RP.run(self.d, None, NOW, att)

    def main(self, pairs, **kw):
        att = write_dir(self.d, pairs, attempts=kw.pop("attempts", None), **kw)
        argv = ["--state-dir", self.d, "--now-ms", str(NOW)] + (["--attempts", att] if att else [])
        return RP.main(argv)


class TestPasses(Base):
    def test_match_with_clean_everything(self):
        res = self.go([order(i) for i in range(100)], attempts=clean_attempts())
        st = RP.p_status(res)
        self.assertTrue(all(v == "PASS" for v in st.values()), st)
        self.assertEqual(res["verdict"], "LIVE FILLS MATCH PAPER")
        self.assertEqual(res["stats"]["n_eligible"], 100)
        self.assertEqual(res["r4bt"]["landing_p50_minus_k1p9_slots"], -2.0)  # d_land 2 - k_1p9 4

    def test_no_stop_and_exit_zero_on_pass(self):
        rc = self.main([order(i) for i in range(5)], attempts=clean_attempts())
        self.assertEqual(rc, 0)
        self.assertFalse((Path(self.d) / "STOP").exists())

    def test_p2_unevaluated_blocks_match_but_not_a_stop(self):
        res = self.go([order(i) for i in range(100)])
        self.assertEqual(RP.p_status(res)["P2"], "UNEVALUATED")
        self.assertEqual(res["verdict"], "parity not shown")
        self.assertFalse(res["stop_required"])


class TestEachP(Base):
    def test_p1_entry_gap(self):
        res = self.go([order(i, buy_px=1.03) for i in range(10)], attempts=clean_attempts())  # +300 bps over the 10 first fills
        self.assertEqual(RP.p_status(res)["P1"], "FAIL")

    def test_p1_not_before_ten_fills(self):
        res = self.go([order(i, buy_px=1.03) for i in range(9)], attempts=clean_attempts())
        self.assertEqual(RP.p_status(res)["P1"], "PASS")

    def test_p2_from_attempts(self):
        res = self.go([order(i) for i in range(3)], attempts=clean_attempts(30, fail=15))  # 50% failed vs max(15%, 10%)
        self.assertEqual(RP.p_status(res)["P2"], "FAIL")
        self.assertIn("P2", res["soft_halts"])

    def test_p3_rolling_mean(self):
        res = self.go([order(i, net=PNL - 20_000_000) for i in range(20)], attempts=clean_attempts())  # d = -2000 bps
        self.assertEqual(RP.p_status(res)["P3"], "FAIL")
        self.assertEqual(RP.p_status(res)["P4"], "PASS")

    def test_p4_hard_after_100(self):
        rc = self.main([order(i, net=PNL - 20_000_000) for i in range(100)], attempts=clean_attempts())
        res = self.go([order(i, net=PNL - 20_000_000) for i in range(100)], attempts=clean_attempts())
        self.assertEqual(RP.p_status(res)["P4"], "FAIL")
        self.assertEqual(rc, 11)
        self.assertTrue(res["hard_halts"])

    def test_p5_landing_above_3s_origin_a8(self):
        # d_land - k_3p0 = 1 > 0 on every landed buy -> P5 (the sps_entry / k are per trip)
        res = self.go([order(i, d_land=7, k3=6) for i in range(20)], attempts=clean_attempts())
        self.assertEqual(RP.p_status(res)["P5"], "FAIL")

    def test_p5_not_when_p50_at_k3(self):
        res = self.go([order(i, d_land=6, k3=6) for i in range(20)], attempts=clean_attempts())
        self.assertEqual(RP.p_status(res)["P5"], "PASS")
        self.assertNotEqual(res["verdict"], "LIVE FILLS MATCH PAPER")  # d_land 6 > k_1p9 4: the 1.9 s match clause fails

    def test_p5_uses_each_trips_own_k(self):
        # each trip's own k_3p0 (own sps_entry): (d_land - k) is 0 for ten trips and +2 for ten; the p50 is +1 > 0 -> P5. One global k would not give this.
        pairs = [order(i, d_land=6, k3=6, sps=2.5) for i in range(10)] + [order(10 + i, d_land=5, k19=2, k3=3, sps=1.2) for i in range(10)]
        res = self.go(pairs, attempts=clean_attempts())
        self.assertEqual(RP.p_status(res)["P5"], "FAIL")

    def test_p5_sell_late_share(self):
        pairs = [order(i, exit_late=(i < 3)) for i in range(20)]  # 3/20 = 15% > 5%
        res = self.go(pairs, attempts=clean_attempts())
        self.assertEqual(RP.p_status(res)["P5"], "FAIL")

    def test_p6_stop_loss_exit_gap(self):
        res = self.go([order(i, reason="sl_backstop", sell_px=0.9) for i in range(10)], attempts=clean_attempts())  # -1000 bps
        self.assertEqual(RP.p_status(res)["P6"], "FAIL")
        self.assertEqual(res["stats"]["stop_loss_exits"], 10)

    def test_p7_no_twin_small_sample(self):
        res = self.go([order(i) for i in range(4)], drop_twin=("o0000", "o0001"), attempts=clean_attempts())
        # the missing twins are past the grace (entry long ago) -> no_twin x2 before 20 trips = P7
        self.assertEqual(RP.p_status(res)["P7"], "FAIL")

    def test_p7_gap_share_is_not_decidable_at_c0_not_a_halt(self):
        res = self.go([order(i, gap=(i < 3)) for i in range(10)], attempts=clean_attempts())
        self.assertEqual(RP.p_status(res)["P7"], "PASS")
        self.assertTrue(res["not_decidable"])
        self.assertEqual(res["verdict"], "parity not shown")
        self.assertEqual(res["r4bt"]["counts"]["gap_hold_trips"], 3)

    def test_p7_stale_run_record_is_refused(self):
        write_dir(self.d, [order(0)], run_overrides={"run_ms": NOW - TWIN_ROW_GRACE_MS - 1})
        with self.assertRaises(RP.Refused):
            RP.run(self.d, None, NOW)

    def test_p7_unreadable_inputs_stop(self):
        (Path(self.d) / LEDGER_NAME).write_text("not json\n")
        self.assertEqual(RP.main(["--state-dir", self.d, "--now-ms", str(NOW)]), 2)
        self.assertTrue((Path(self.d) / "STOP").exists())


class TestExclusionsAndOrigin(Base):
    def test_restart_and_unlanded_excluded_and_counted(self):
        pairs = [order(0), order(1, restart=True), order(2, landed=False), order(3, reason="restart")]
        res = self.go(pairs, attempts=clean_attempts())
        c = res["r4bt"]["counts"]
        self.assertEqual((c["excluded_restart"], c["excluded_buy_not_landed"], res["shares"]["trips"]), (2, 1, 1))

    def test_twin_pending_inside_grace_is_not_no_twin(self):
        recent = NOW - 1_000_000
        res = self.go([order(0), order(1, twin="pending_tape", entry_ms=recent, with_exit_ms=False)], attempts=clean_attempts())
        self.assertEqual(res["r4bt"]["counts"]["twin_pending"], 1)
        self.assertEqual(res["shares"]["no_twin"], 0)

    def test_pending_tape_past_grace_is_no_twin(self):
        old = NOW - TWIN_ROW_GRACE_MS - 10_000_000
        res = self.go([order(0), order(1, twin="pending_tape", entry_ms=old)], attempts=clean_attempts())
        self.assertEqual(res["shares"]["no_twin"], 1)

    def test_slot_origin_is_twin_corrected_slot_not_sd(self):
        # landed 1 slot after corrected_slot (= sd + 3): d_land = 1, but landed - sd(T) = 4 (diagnostic only)
        res = self.go([order(i, d_land=1) for i in range(5)], attempts=clean_attempts())
        self.assertEqual(res["r4bt"]["landing_p50_minus_k1p9_slots"], 1 - 4)
        self.assertEqual(res["r4bt"]["diag_sdT_p50_slots"], 4.0)

    def test_missing_live_fill_field_is_refused_fail_closed(self):
        led, t = order(0)
        del led["live_buy_price"]
        self.assertEqual(self.main([(led, t)]), 2)
        self.assertTrue((Path(self.d) / "STOP").exists())

    def test_actual_fees_decide_fee_gap_not_read(self):
        led, t = order(0)
        t["fee_gap_bps"] = -99999.0
        res = self.go([(led, t)], attempts=clean_attempts())
        self.assertEqual(res["stats"]["mean_d_bps"], 0.0)


class TestStopWiringAndOutput(Base):
    def test_stop_written_in_state_dir_on_soft_fail(self):
        rc = self.main([order(i, net=PNL - 20_000_000) for i in range(20)], attempts=clean_attempts())
        self.assertEqual(rc, 10)
        stop = Path(self.d) / "STOP"
        body = json.loads(stop.read_text())
        self.assertEqual(body["schema"], "arena_live_parity_stop_v1")
        self.assertIn("P3", body["ids"])
        self.assertFalse(body["hard"])

    def test_existing_stop_never_overwritten_or_removed(self):
        stop = Path(self.d) / "STOP"
        stop.write_text("manager's file\n")
        write_dir(self.d, [order(i) for i in range(20)])
        self.assertIn(RP.main(["--state-dir", self.d, "--now-ms", str(NOW)]), (0, 10))
        self.assertEqual(stop.read_text(), "manager's file\n")

    def test_stop_path_override_and_unwritable(self):
        write_dir(self.d, [order(i, net=PNL - 20_000_000) for i in range(20)], attempts=None)
        rc = RP.main(["--state-dir", self.d, "--now-ms", str(NOW), "--stop-file", os.path.join(self.d, "nodir", "STOP")])
        self.assertEqual(rc, 3)

    def test_executor_gate_name_is_STOP_in_state_dir(self):
        self.assertEqual(RP.STOP_NAME, "STOP")

    def test_output_has_no_per_trip_rows_or_order_ids(self):
        import io
        from contextlib import redirect_stdout
        write_dir(self.d, [order(i) for i in range(12)], attempts=None)
        buf = io.StringIO()
        with redirect_stdout(buf):
            RP.main(["--state-dir", self.d, "--now-ms", str(NOW), "--json", os.path.join(self.d, "out.json")])
        out = buf.getvalue() + (Path(self.d) / "out.json").read_text()
        self.assertNotIn("o0001", out)
        self.assertNotIn("ineligible_log", out)
        for p in RP.PARITY_IDS:
            self.assertIn(f"{p}:", buf.getvalue())

    def test_module_has_no_keys_or_network(self):
        src = Path(RP.__file__).read_text()
        for bad in ("socket", "requests", "urllib", "keypair", "solana"):
            self.assertNotIn("import " + bad, src)


if __name__ == "__main__":
    unittest.main()
