from __future__ import annotations

import json
import math
import tempfile
import unittest
from pathlib import Path

import tools.exp012_runner_latency_export as ex

B = "exp012_book"
T0 = 1_000_000_000_000


def _gate(mint, mig):
    return {"book": B, "mint": mint, "mig_ms": mig, "score": 0.9, "entered": True}


def _dec(mint, t, recv=0, action="enter", ledger="shadow", reason=None, book=B):
    return {
        "book": book,
        "mint": mint,
        "ledger": ledger,
        "decision_t_ms": t,
        "action": action,
        "reason": reason,
        "score": 0.9,
        "pnl": 1.0,
        "latency": {"recv_to_decision_ms": recv, "applied_latency_ms": 5} if action == "enter" else None,
    }


class ExportTests(unittest.TestCase):
    def test_only_allowlisted_keys_and_shadow_only(self) -> None:
        gates = [_gate("a", T0), _gate("b", T0), _gate("c", T0)]
        decs = [
            _dec("a", T0 + 600, 10),
            _dec("a", T0 + 600, 10, ledger="ceiling"),
            _dec("b", T0 + 600, None, action="skip", reason="stale_recv"),
            _dec("c", T0 + 600, None, action="skip", reason="below_threshold"),
            _dec("z", T0 + 600, 1),  # no gate row: not EXP-012
            _dec("a", T0 + 600, 1, book="other"),
        ]
        rows = ex.build_export(gates, decs)
        self.assertEqual([r["mint"] for r in rows], ["a", "b"])
        for r in rows:
            self.assertEqual(set(r), set(ex.EXPORT_KEYS))
        self.assertTrue(rows[1]["stale"])
        self.assertNotIn("pnl", json.dumps(rows))

    def test_forbidden_paths_refused(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            for name in ("positions.jsonl", "runner-status.json", "runner-status-2.json"):
                p = Path(d) / name
                p.write_text("{}\n")
                with self.assertRaises(ex.Refused):
                    ex._read_jsonl(p)
                self.assertEqual(ex.main(["--gate", str(p), "--decisions", str(p), "--verify", str(p), "--from", "0", "--to", "1"]), 2)

    def test_k_formula(self) -> None:
        slot = 268.0
        # L = t + recv - (mig + 500)
        r = {"mig_ms": T0, "decision_t_ms": T0 + 500, "recv_to_decision_ms": 0}
        self.assertEqual(ex.k_value(r, slot), 1)  # L=0
        r["recv_to_decision_ms"] = 1
        self.assertEqual(ex.k_value(r, slot), 2)  # ceil(1/268)=1
        r["recv_to_decision_ms"] = 268
        self.assertEqual(ex.k_value(r, slot), 2)
        r["recv_to_decision_ms"] = 269
        self.assertEqual(ex.k_value(r, slot), 3)
        self.assertTrue(math.isinf(ex.k_value({"stale": True}, slot)))

    def test_negative_latency_floors_at_min_one_in_percentile(self) -> None:
        rows = [{"mint": str(i), "mig_ms": T0, "decision_t_ms": T0, "recv_to_decision_ms": 0, "ledger": "shadow", "action": "enter", "stale": False} for i in range(100)]
        res = ex.compute(rows, 268.0, T0 - 1, T0 + 1, n_hours_used=24)
        # L = -500 -> ceil(-1.87) = -1 -> k = 0 -> floored to 1
        self.assertEqual(res["k_p50"], 1)
        self.assertEqual(res["k_p90"], 1)

    def test_percentile_convention(self) -> None:
        v = [1, 2, 3, 4]
        self.assertEqual(ex.pct(v, 0.5), 3)  # round(1.5)=2 (banker's) -> v[2]
        self.assertEqual(ex.pct(v, 0.9), 4)  # round(2.7)=3
        self.assertEqual(ex.pct(list(range(101)), 0.9), 90)
        self.assertEqual(ex.pct([], 0.5), 0.0)

    def _rows(self, n, stale=0):
        out = []
        for i in range(n):
            out.append(
                {
                    "mint": f"m{i}",
                    "mig_ms": T0,
                    "decision_t_ms": T0 + 500 + 100,
                    "recv_to_decision_ms": 0 if i < n - stale else None,
                    "ledger": "shadow",
                    "action": "enter" if i < n - stale else "skip",
                    "stale": i >= n - stale,
                }
            )
        return out

    def test_not_decidable_below_100(self) -> None:
        res = ex.compute(self._rows(99), 268.0, T0, T0 + 10_000, n_hours_used=24)
        self.assertEqual(res["verdict"], "NOT_DECIDABLE")
        self.assertEqual(res["n"], 99)
        res = ex.compute(self._rows(100), 268.0, T0, T0 + 10_000, n_hours_used=24)
        self.assertEqual(res["verdict"], "OK")
        self.assertEqual(res["k_p50"], 2)  # L=100 -> 1+1
        self.assertEqual(len(res["in_window_rows_sha256"]), 64)

    def test_not_decidable_without_slot(self) -> None:
        self.assertEqual(ex.compute(self._rows(100), None, T0, T0 + 10_000, n_hours_used=24)["verdict"], "NOT_DECIDABLE")

    def test_stale_is_infinite_and_drives_p90(self) -> None:
        res = ex.compute(self._rows(100, stale=20), 268.0, T0, T0 + 10_000, n_hours_used=24)
        self.assertEqual(res["n_stale"], 20)
        self.assertEqual(res["k_p50"], 2)
        self.assertEqual(res["k_p90"], "inf")

    def test_window_is_half_open(self) -> None:
        rows = self._rows(100)
        rows[0]["decision_t_ms"] = T0 + 10_000  # == to: excluded
        res = ex.compute(rows, 268.0, T0, T0 + 10_000, n_hours_used=24)
        self.assertEqual(res["n"], 99)

    def test_two_shas_in_cli(self) -> None:
        import contextlib
        import hashlib
        import io

        gates = [_gate("a", T0)]
        decs = [_dec("a", T0 + 600, 10)]
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            (d / "g.jsonl").write_text("".join(json.dumps(x) + "\n" for x in gates), encoding="utf-8")
            (d / "d.jsonl").write_text("".join(json.dumps(x) + "\n" for x in decs), encoding="utf-8")
            (d / "verify.jsonl").write_text("", encoding="utf-8")
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = ex.main(["--gate", str(d / "g.jsonl"), "--decisions", str(d / "d.jsonl"), "--verify", str(d / "verify.jsonl"), "--from", "0", "--to", str(T0 * 2), "--export-out", str(d / "out.jsonl")])
            self.assertEqual(rc, 0)
            res = json.loads(buf.getvalue())
            self.assertEqual(res["export_file_sha256"], hashlib.sha256((d / "out.jsonl").read_bytes()).hexdigest())
            self.assertIn("in_window_rows_sha256", res)
            self.assertNotIn("export_sha256", res)

    def test_slot_ms_from_verify_uses_only_clean_hours(self) -> None:
        from datetime import datetime, timezone

        f = 1_790_000_000_000
        t = int(datetime(2026, 10, 6, tzinfo=timezone.utc).timestamp() * 1000)
        v = [
            {"hour": "2026-10-06T00", "stats_present": True, "start_slot": 100, "end_slot": 100 + 13_432, "slot_span": 13_432, "issues": []},
            {"hour": "2026-10-06T01", "stats_present": True, "start_slot": 0, "end_slot": 13_432, "issues": []},  # span derived
            {"hour": "2026-10-06T02", "stats_present": True, "slot_span": 1, "issues": ["x"]},  # issues
            {"hour": "2026-10-06T03", "stats_present": False, "slot_span": 1, "issues": []},  # no stats
            {"hour": "2026-10-06T04", "stats_present": True, "slot_span": 1},  # issues key missing
            {"hour": "2026-10-09T00", "stats_present": True, "slot_span": 1, "issues": []},  # outside window
        ]
        slot, used, skipped = ex.slot_ms_from_verify(v, t, t + 86_400_000)
        self.assertAlmostEqual(slot, 3_600_000 / 13_432)
        self.assertEqual((used, skipped), (2, 3))
        self.assertEqual(ex.slot_ms_from_verify(v, f, f + 1), (None, 0, 0))

    def test_not_decidable_when_fewer_than_24_hours_per_window_day(self) -> None:
        day = 86_400_000
        rows = self._rows(100)
        ok = ex.compute(rows, 268.0, T0, T0 + 2 * day - 1, n_hours_used=48, n_hours_skipped=0)
        self.assertEqual(ok["window_days"], 2)
        self.assertEqual(ok["verdict"], "OK")
        short = ex.compute(rows, 268.0, T0, T0 + 2 * day - 1, n_hours_used=47, n_hours_skipped=1)
        self.assertEqual(short["verdict"], "NOT_DECIDABLE")
        self.assertEqual((short["n_hours_used"], short["n_hours_skipped"]), (47, 1))
        self.assertEqual(ex.compute(rows, 268.0, T0, T0 + 1)["verdict"], "NOT_DECIDABLE")  # hours unknown

    def test_n_dropped_missing_fields(self) -> None:
        rows = self._rows(100)
        rows[0]["recv_to_decision_ms"] = None
        rows[1]["mig_ms"] = None
        rows[2]["decision_t_ms"] = None
        res = ex.compute(rows, 268.0, T0, T0 + 10_000, n_hours_used=24)
        self.assertEqual(res["n_dropped_missing_fields"], 3)
        self.assertEqual(res["n"], 97)

    def test_forbidden_names_are_case_insensitive(self) -> None:
        for name in ("Positions.JSONL", "RUNNER-STATUS.json", "Runner-Status-1.json"):
            with self.assertRaises(ex.Refused):
                ex.refuse_forbidden(Path("/x") / name)
        ex.refuse_forbidden(Path("/x/decisions.jsonl"))


if __name__ == "__main__":
    unittest.main()
