"""Tests for tools/exp025_look.py (EXP-025 P4, the one locked job of a look). Fixtures only: nothing here opens /data/mal, a real tape hour,
forward-1002, forward-1002ev, walk 2, an October label or an outcome. Every October-looking row below is synthetic."""

from __future__ import annotations

import json
import os
import tempfile
import types
import unittest

try:
    import numpy as np
    import pandas as pd
    import duckdb  # noqa: F401
except ImportError:  # pragma: no cover
    np = pd = None

if np is not None:
    import sys
    sys.path.insert(0, os.path.dirname(__file__))
    import exp025_read as R
    import exp025_look as K
    from test_exp025_read import PricingLayerFixture

DATES = [f"2026-10-{d}" for d in range(10, 17)]
TAGS = [f"{lat}_END_{lg}" for lat in ("p", "b") for lg in ("l055", "l2", "l5")] + [f"{lat}_{b}_l055" for lat in ("p", "b") for b in ("START", "WORST")]


class Fixture:
    """A synthetic look 1: 3 exploration tokens (mid 1..3) and 20 October tokens per counted date, one decision row each."""

    def __init__(self, d, r2_share=0.99, drop_hours=(), bad_hours=(), wrong_block=None, pred=0.05, oracle_ans=False, fail_cmd=None):
        self.d = d; self.O = os.path.join(d, "look1"); os.makedirs(self.O)
        self.calls, self.priced = [], 0
        self.pred, self.oracle_ans, self.fail_cmd = pred, oracle_ans, fail_cmd
        mk, lg = os.path.join(d, "FINAL_WRITTEN"), os.path.join(d, "FINAL_READS.jsonl")
        open(mk, "w").close(); open(lg, "w").write('{"x":1}\n')
        blobs = os.path.join(d, "blobs.json"); json.dump({"forward-1002ev": "a" * 40, "walk2": "a" * 40}, open(blobs, "w"))
        e0 = os.path.join(d, "e0"); os.makedirs(e0)
        json.dump({"pass": True}, open(os.path.join(e0, "p3_e0_2026-09-20.json"), "w")); json.dump({"e0_pass": True}, open(os.path.join(e0, "p4_e0.json"), "w"))
        p7 = os.path.join(d, "p7.json"); json.dump({"cp": [995, 1000, 995, 1000], "fee": [990, 1000, 990, 1000]}, open(p7, "w"))
        mans = {}
        for src, h in R.allowlisted_hours(1):
            blk = K.BLOCK_OF_SOURCE[src] if h != wrong_block else "forward-1002ev"
            m = mans.setdefault(blk, {"schema": K.MANIFEST_SCHEMA, "block": blk, "event_v": blk != "forward-1002", "files": [], "bad": []})
            if h in drop_hours:
                continue
            if h in bad_hours:
                m["bad"].append({"kind": "trades", "hour": h}); continue
            m["files"].append({"kind": "trades", "hour": h, "pumpswap_rows": 10, "pumpswap_rows_with_v": 10 if blk != "forward-1002" else 0})
        paths = []
        for blk, m in mans.items():
            p = os.path.join(d, f"manifest_{blk}.json"); json.dump(m, open(p, "w")); paths.append(p)
        json.dump({"schema": K.ASSEMBLY_SCHEMA, "look": "look1", "manifests": paths,
                   "r2": {"non_mayhem_completes": 100, "pda_pool_in_tape": int(r2_share * 100), "share": r2_share}},
                  open(os.path.join(self.O, "look_assembly.json"), "w"))
        ex = [dict(mid=i, mint=f"E{i}", pool=f"EP{i}", v=17.6e9, g=float(R.ep("2026-09-20T00") + i), gday="2026-09-20", cbt=R.ep("2026-09-19T20"))
              for i in (1, 2, 3)]
        octo = []
        for k, day in enumerate(DATES):
            for i in range(20):
                g = R.ep(day + "T00") + 240 * i
                octo.append(dict(mid=4 + 20 * k + i, mint=f"M{k}_{i}", pool=f"P{k}_{i}", v=17.6e9, g=float(g), gday=day, cbt=g - 3600))
        self.U = pd.DataFrame(ex + octo)
        self.p2 = os.path.join(d, "p2_universe.parquet"); self.U.iloc[:3].to_parquet(self.p2, index=False)
        self.X = pd.DataFrame(dict(mid=[r["mid"] for r in octo], t=[int(r["g"]) + 120 for r in octo]))
        self.X["h_top1"] = np.where(self.X.mid % 10 == 0, 0.7, 0.3); self.X["h_top5"] = 0.6
        self.a = types.SimpleNamespace(now=R.ep("2026-10-17T03"), final_marker=mk, final_ledger=lg, decoder_blobs=blobs, e0_dir=e0, p7=p7,
                                       p2_universe=self.p2, ledger=os.path.join(d, "LOOK_READS.jsonl"), o_dir=self.O, look1_record=None,
                                       oracle_live=None, oracle_replay=None, oracle_stale_s=None)

    # ---- the Steps interface
    def preflight(self, look):
        return K.Steps(self.a).preflight(look)

    def apply_patch(self, look, W):
        return R.LOOKS[look]["applied_sha"]

    def run(self, cmd, log):
        name = os.path.basename(cmd[1]); self.calls.append(name)
        if self.fail_cmd == name:
            raise R.Refusal("RUN", f"{name} exited 1")
        if name == "10_meta.py":
            os.makedirs(os.path.join(self.O, "work"), exist_ok=True); self.U.to_parquet(os.path.join(self.O, "work", "universe.parquet"), index=False)
        if name == "14_export.py":
            U = pd.read_parquet(os.path.join(self.O, "work", "universe.parquet"))
            X = self.X[self.X.mid.isin(U.mid)].sort_values(["t", "mid"]).reset_index(drop=True)
            os.makedirs(os.path.join(self.O, "ml"), exist_ok=True); os.makedirs(os.path.join(self.O, "out", "candx"), exist_ok=True)
            np.savez(os.path.join(self.O, "ml", "october.npz"), t=X.t.values, mid=X.mid.values)
            for day in DATES:
                X[[R.date_str(t) == day for t in X.t]].to_parquet(os.path.join(self.O, "out", "candx", f"{day}.parquet"), index=False)

    def retrain(self, look, october, gtime, out, exploration_only, log):
        self.calls.append("retrain_explo" if exploration_only else "retrain")
        z = np.load(october)
        return dict(t=z["t"], mid=z["mid"], pred=np.full(len(z["t"]), self.pred), s1=np.ones(len(z["t"]), bool))

    def fallback_hours(self, look, tape_dir, guard):
        return []

    def first_print_ms(self, look, tape_dir, pools, guard):
        return {p: int(g) * 1000 for p, g in zip(self.U.pool, self.U.g)}

    def oracle(self):
        return lambda mint: self.oracle_ans

    def price_fn(self, rows, tape_dir, guard, hour_source=None, vmiss_hours=None):
        self.priced += 1
        out = []
        for r in rows.itertuples():
            guard.check_hour(hour_source(R.hour_str(int(r.t))), R.hour_str(int(r.t)))
            rec = dict(idx=r.idx, err="", ssb_p=0, nearby_p=0.0, ssb_b=0, nearby_b=0.0)
            for tg in TAGS:
                # synthetic gross P&L with a date-to-date spread (equal date means give sd 0, no day-level t, and section 7 item 8 fails)
                rec.update({f"g_{tg}": 0, f"pnl_{tg}": 20e6 + (r.idx % 5) * 1e6 + (int(r.t) // 86400 % 3) * 2e6, f"xt_{tg}": int(r.t) + 301,
                            f"vmiss_{tg}": False})
            out.append(rec)
        return pd.DataFrame(out)

    def events(self):
        return [e["event"] for e in R.ledger_events(self.a.ledger, 1)]


@unittest.skipIf(np is None, "numpy/pandas/duckdb missing")
class LockedRun(unittest.TestCase):
    def test_full_locked_read_then_no_second_read(self):
        with tempfile.TemporaryDirectory() as d:
            F = Fixture(d)
            rec = K.run_look(1, F.a, F)
            self.assertEqual(rec["verdict"], "PASS", rec)
            self.assertEqual(F.events(), ["lock", "read"])
            self.assertEqual(F.priced, 1)
            self.assertEqual(F.calls[0], "10_meta.py"); self.assertIn("retrain_explo", F.calls)
            self.assertLess(F.calls.index("retrain_explo"), F.calls.index("retrain"))
            pc = json.load(open(os.path.join(F.O, "precount.json")))
            self.assertEqual(sum(v["kept"] for v in pc["dates"].values()), 126)          # 140 selections minus the 14 capped (h_top1 0.7)
            self.assertFalse(any(k in json.dumps(pc) for k in ("pnl", "mean", "ciT", "days_pos")))
            res = json.load(open(rec["result"]))
            self.assertEqual(res["n_kept"], 126); self.assertIn("report_only", res)
            with self.assertRaises(R.Refusal) as c:
                K.run_look(1, F.a, F)
            self.assertEqual(c.exception.code, "LOCK")
            with self.assertRaises(R.Refusal) as c:
                R.read_after_lock(1, pd.DataFrame(), {"t": [], "mid": [], "pred": []}, None, os.path.join(d, "x.json"), o_dir=F.O, ledger=F.a.ledger)
            self.assertEqual(c.exception.code, "LOCK")

    def test_read_needs_the_lock(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(R.Refusal) as c:
                R.read_after_lock(1, pd.DataFrame(), {"t": [], "mid": [], "pred": []}, None, os.path.join(d, "x.json"), o_dir=d,
                                  ledger=os.path.join(d, "L.jsonl"))
            self.assertEqual(c.exception.code, "LOCK")


@unittest.skipIf(np is None, "numpy/pandas/duckdb missing")
class RefusalsBeforeAnyPnl(unittest.TestCase):
    def _nd(self, F, code, labels_built):
        rec = K.run_look(1, F.a, F)
        self.assertEqual((rec["verdict"], rec["refused"]), ("NOT_DECIDABLE", code), rec)
        self.assertEqual(F.events(), ["lock", "not_decidable"])
        self.assertEqual(F.priced, 0)
        self.assertEqual("11_passA.py" in F.calls, labels_built)
        self.assertTrue(os.path.exists(os.path.join(F.O, "READ.lock")))
        with self.assertRaises(R.Refusal):
            K.run_look(1, F.a, F)                                                       # spent: no re-run

    def test_r2_before_the_lock_spends_the_look(self):
        with tempfile.TemporaryDirectory() as d:
            self._nd(Fixture(d, r2_share=0.9), "R2", False)

    def test_r12_wrong_block(self):
        with tempfile.TemporaryDirectory() as d:
            self._nd(Fixture(d, wrong_block="2026-10-16T05"), "R12", False)

    def test_r1_unwalked_hours(self):
        with tempfile.TemporaryDirectory() as d:
            self._nd(Fixture(d, drop_hours=[R.hour_str(R.ep("2026-10-03T00") + 3600 * i) for i in range(8)]), "R1", False)

    def test_r7_oracle_non_boolean(self):
        with tempfile.TemporaryDirectory() as d:
            self._nd(Fixture(d, oracle_ans=None), "R7", False)

    def test_r14_p7_missing(self):
        with tempfile.TemporaryDirectory() as d:
            F = Fixture(d); os.remove(F.a.p7)
            self._nd(F, "R14", False)

    def test_r4_precount_after_lock(self):
        with tempfile.TemporaryDirectory() as d:
            F = Fixture(d, pred=0.0)
            self._nd(F, "R4", True)
            self.assertNotIn("retrain", F.calls)                                         # the labelled retrain never ran

    def test_r1_attempts_need_a_bad_hour(self):
        with tempfile.TemporaryDirectory() as d:
            self._nd(Fixture(d, bad_hours=["2026-10-10T00"]), "R1", True)

    def test_crash_after_lock_is_not_decidable(self):
        with tempfile.TemporaryDirectory() as d:
            self._nd(Fixture(d, fail_cmd="12_passC.py"), "RUN", True)


@unittest.skipIf(np is None, "numpy/pandas/duckdb missing")
class NotTerminal(unittest.TestCase):
    def test_readiness_refusal_takes_no_lock(self):
        with tempfile.TemporaryDirectory() as d:
            F = Fixture(d); os.remove(F.a.final_marker)
            with self.assertRaises(R.Refusal) as c:
                K.run_look(1, F.a, F)
            self.assertEqual(c.exception.code, "SEAL"); self.assertEqual(F.events(), [])

    def test_ready_mode_never_locks(self):
        with tempfile.TemporaryDirectory() as d:
            F = Fixture(d)
            self.assertTrue(K.run_look(1, F.a, F, lock=False)["ready"])
            G = Fixture(os.path.join(d, "g") if os.makedirs(os.path.join(d, "g")) is None else d, r2_share=0.5)
            with self.assertRaises(R.Refusal) as c:
                K.run_look(1, G.a, G, lock=False)
            self.assertEqual(c.exception.code, "R2")
            self.assertEqual(F.events() + G.events(), []); self.assertEqual(F.calls + G.calls, [])

    def test_tool_crash_before_lock_is_not_terminal(self):
        with tempfile.TemporaryDirectory() as d:
            F = Fixture(d, fail_cmd="10_meta.py")
            with self.assertRaises(R.Refusal) as c:
                K.run_look(1, F.a, F)
            self.assertEqual(c.exception.code, "RUN"); self.assertEqual(F.events(), [])


@unittest.skipIf(np is None, "numpy/pandas/duckdb missing")
class AdapterInterface(unittest.TestCase):
    def test_hour_status(self):
        with tempfile.TemporaryDirectory() as d:
            F = Fixture(d, drop_hours=["2026-10-12T03"])
            _, mans = K.load_assembly(1, F.O)
            mans[1]["files"][-1]["pumpswap_rows_with_v"] = 9                             # one V-incomplete hour
            hs = K.hour_status(1, mans)
            self.assertEqual(hs["unwalked"], ["2026-10-12T03"]); self.assertIn("2026-10-12T03", hs["bad"])
            self.assertEqual(hs["vmiss"], [mans[1]["files"][-1]["hour"]])
            self.assertLess(hs["v_coverage"], 1.0)
            self.assertFalse(any(h < K.V_COVER_START for h in hs["vmiss"]))             # forward-1002 hours carry no counted price

    def test_price_rows_vmiss_hours_from_the_hour_column(self):
        P = PricingLayerFixture()
        with tempfile.TemporaryDirectory() as d:
            P._tape(d)
            for f in os.listdir(d):
                df = pd.read_parquet(os.path.join(d, f)); df["hour"] = f[:-8]; df.to_parquet(os.path.join(d, f), index=False)
            base = R.price_rows(P._rows(), d, R.ExplorationGuard()).iloc[0]
            vm = R.price_rows(P._rows(), d, R.ExplorationGuard(), vmiss_hours={"2026-09-20T00"}).iloc[0]
            self.assertFalse(bool(base.vmiss_p_END_l055)); self.assertTrue(bool(vm.vmiss_p_END_l055))
            self.assertLess(vm.pnl_p_END_l055, base.pnl_p_END_l055)
            d2 = os.path.join(d, "nohour"); P._tape(d2)
            with self.assertRaises(R.Refusal) as c:
                R.price_rows(P._rows(), d2, R.ExplorationGuard(), vmiss_hours={"2026-09-20T00"})
            self.assertEqual(c.exception.code, "R3")

    def test_fallback_hours_follow_pass_a_clock_rule(self):
        with tempfile.TemporaryDirectory() as d:
            t0 = R.ep("2026-10-09T00")
            for k, span in ((0, 3500), (1, 1000)):
                h = t0 + 3600 * k
                pd.DataFrame(dict(block_time=[h, h + span], slot=[10 + 10000 * k, 20 + 10000 * k + span])).to_parquet(
                    os.path.join(d, R.hour_str(h) + ".parquet"), index=False)

            class G:
                def check_hour(self, s, h):
                    pass
            fb = K.fallback_hours(1, d, G())
            self.assertNotIn("2026-10-09T00", fb); self.assertIn("2026-10-09T01", fb); self.assertIn("2026-10-09T02", fb)

    def test_e0_records_as_p3_writes_them(self):
        with tempfile.TemporaryDirectory() as d:
            json.dump({"pass": True}, open(os.path.join(d, "p3_e0_2026-09-20.json"), "w"))
            with self.assertRaises(R.Refusal):
                R.check_e0_records(d)
            json.dump({"e0_pass": True}, open(os.path.join(d, "p4_e0.json"), "w"))
            R.check_e0_records(d)
            json.dump({"pass": False}, open(os.path.join(d, "p3_e0_2026-09-21.json"), "w"))
            with self.assertRaises(R.Refusal):
                R.check_e0_records(d)

    def test_constants_equal_the_adapter_when_importable(self):
        try:
            import exp025_adapter as A
        except Exception:  # noqa: BLE001 - #563 not merged into this branch
            self.skipTest("tools/exp025_adapter.py not on this branch")
        self.assertEqual((A.R2_MIN, A.R3_MIN, A.R1_MAX_BAD, A.V_COVER_START), (K.R2_MIN, K.R3_MIN, 0.02, K.V_COVER_START))
        for k, n in (("look1", 1), ("look2", 2)):     # same (hour -> block) sets; look 2's walk-2 range is one tuple there, two here
            self.assertEqual({h: K.BLOCK_OF_SOURCE[s] for s, h in R.allowlisted_hours(n)}, dict(A.look_allowlist(k)))


if __name__ == "__main__":
    unittest.main()
