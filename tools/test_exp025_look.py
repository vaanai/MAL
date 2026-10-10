"""Tests for tools/exp025_look.py (EXP-025 P4, the one locked job of a look). Fixtures only: nothing here opens /data/mal, a real tape hour,
forward-1002, forward-1002ev, walk 2, an October label or an outcome. Every October-looking row below is synthetic."""

from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
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

    def __init__(self, d, r2_share=0.99, drop_hours=(), bad_hours=(), wrong_block=None, pred=0.05, oracle_ans=False, fail_cmd=None,
                 r3_share=0.99, v_flag=True):
        self.d = d; self.O = os.path.join(d, "look1"); os.makedirs(self.O)
        self.calls, self.priced = [], 0
        self.pred, self.oracle_ans, self.fail_cmd, self.r3_share, self.v_flag = pred, oracle_ans, fail_cmd, r3_share, v_flag
        self.r3_pools = None
        mk, lg = os.path.join(d, "FINAL_WRITTEN"), os.path.join(d, "FINAL_READS.jsonl")
        open(mk, "w").close(); open(lg, "w").write('{"x":1}\n')
        blobs = os.path.join(d, "blobs.json"); json.dump({"forward-1002ev": "a" * 40, "walk2": "a" * 40}, open(blobs, "w"))
        e0 = os.path.join(d, "e0"); os.makedirs(e0)
        json.dump({"pass": True}, open(os.path.join(e0, "p3_e0_2026-09-20.json"), "w")); json.dump({"e0_pass": True, "tool_blob": R.tool_blob()}, open(os.path.join(e0, "p4_e0.json"), "w"))
        xc = os.path.join(d, "r1_crosscheck.json")
        json.dump({"schema": K.CROSSCHECK_SCHEMA, "hours": {h: {"match_rate": 1.0} for s_, h in R.allowlisted_hours(1) if s_ == "forward-1002ev"}},
                  open(xc, "w"))
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
        self.X["wb5_n"] = 4; self.X["wb5_new"] = 0.25; self.X["ws5_n"] = 2; self.X["ws5_new"] = 0.5   # 3 + 1 of 6 wallets known: 2/3
        self.a = types.SimpleNamespace(now=R.ep("2026-10-17T03"), final_marker=mk, final_ledger=lg, decoder_blobs=blobs, e0_dir=e0, p7=p7,
                                       p2_universe=self.p2, ledger=os.path.join(d, "LOOK_READS.jsonl"), o_dir=self.O, r1_crosscheck=xc,
                                       oracle_live=[os.path.join(d, "live.jsonl")], oracle_replay=None, oracle_stale_s=None)

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

    def v_flag_missing(self, look, tape_dir, guard):
        return [] if self.v_flag else ["2026-10-09T00"]

    def r3_coverage(self, look, tape_dir, pools, guard):
        self.calls.append("r3"); self.r3_pools = list(pools)
        return dict(share=self.r3_share, n=1000, n_v=int(self.r3_share * 1000), basis="fixture",
                    by_date={d: dict(prints=100, with_v=int(self.r3_share * 100), share=self.r3_share) for d in DATES})

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
            self.assertEqual(F.events(), ["lock", "decisions", "read"])
            ev = R.ledger_events(F.a.ledger, 1)
            self.assertEqual(ev[1]["md5_look1_window"], ev[1]["decisions_md5"]); self.assertEqual(ev[1]["decisions_md5"], ev[2]["decisions_md5"])
            inp = ev[0]["inputs"]                                                          # r3 item 1: the lock records the inputs' sha256
            self.assertEqual(inp["look_assembly"][os.path.join(F.O, "look_assembly.json")], R.sha256(os.path.join(F.O, "look_assembly.json")))
            self.assertEqual(len(inp["manifests"]), 3); self.assertTrue(all(inp["manifests"].values()))
            self.assertEqual(inp["p7"][F.a.p7], R.sha256(F.a.p7)); self.assertEqual(inp["r1_crosscheck"][F.a.r1_crosscheck], R.sha256(F.a.r1_crosscheck))
            self.assertIn(F.a.decoder_blobs, inp["decoder_blobs"]); self.assertIsNone(inp["oracle_live"][F.a.oracle_live[0]])
            self.assertEqual(F.priced, 1)
            self.assertEqual(F.calls[0], "10_meta.py"); self.assertIn("retrain_explo", F.calls)
            self.assertLess(F.calls.index("retrain_explo"), F.calls.index("retrain"))
            pc = json.load(open(os.path.join(F.O, "precount.json")))
            self.assertEqual(sum(v["kept"] for v in pc["dates"].values()), 126)          # 140 selections minus the 14 capped (h_top1 0.7)
            self.assertFalse(any(k in json.dumps(pc) for k in ("pnl", "mean", "ciT", "days_pos")))
            self.assertAlmostEqual(pc["shares"]["ledger_coverage"], 4 / 6); self.assertEqual(pc["shares"]["v_coverage"], 0.99)
            day = pc["dates"]["2026-10-12"]
            self.assertAlmostEqual(day["ledger_coverage"], 4 / 6); self.assertAlmostEqual(day["ledger_coverage_kept"], 4 / 6)
            self.assertEqual((day["ledger_wallets"], day["rows_without_ledger"], day["v_coverage"]), (120, 0, 0.99))
            self.assertLess(F.calls.index("r3"), F.calls.index("11_passA.py"))                # R3 after 10_meta, before the lock
            self.assertEqual(len(F.r3_pools), 143)
            res = json.load(open(rec["result"]))
            self.assertEqual(res["n_kept"], 126); self.assertIn("report_only", res); self.assertEqual(res["r9"], "n/a: Look 1")
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

    def test_r7_oracle_constructor_error_or_stale_source(self):
        with tempfile.TemporaryDirectory() as d:
            bad = types.SimpleNamespace(oracle_live=[os.path.join(d, "live.jsonl")], oracle_replay=None, final_marker=None, oracle_stale_s="x")
            never = types.SimpleNamespace(oracle_live=[os.path.join(d, "never_beat.jsonl")], oracle_replay=None, final_marker=None,
                                          oracle_stale_s=None)
            for a in (bad, never):
                with self.assertRaises(R.Refusal) as c:
                    K.Steps(a).oracle()
                self.assertEqual(c.exception.code, "R7")

            class Built(Fixture):
                def oracle(self):
                    return K.Steps(bad).oracle()
            self._nd(Built(d), "R7", False)                                               # terminal, as section 11.4 says

    def test_r3_from_the_tape_before_the_lock(self):
        with tempfile.TemporaryDirectory() as d:
            F = Fixture(d, r3_share=0.94)
            self._nd(F, "R3", False)
            self.assertIn("10_meta.py", F.calls)

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
            self.assertEqual(c.exception.code, "SEAL"); self.assertEqual(F.events(), ["ready"])
            self.assertFalse(os.path.exists(os.path.join(F.O, "READ.lock")))

    def test_ready_mode_never_locks(self):
        with tempfile.TemporaryDirectory() as d:
            F = Fixture(d)
            self.assertTrue(K.run_look(1, F.a, F, lock=False)["ready"])
            G = Fixture(os.path.join(d, "g") if os.makedirs(os.path.join(d, "g")) is None else d, r2_share=0.5)
            with self.assertRaises(R.Refusal) as c:
                K.run_look(1, G.a, G, lock=False)
            self.assertEqual(c.exception.code, "R2")
            self.assertEqual(F.events() + G.events(), ["ready", "ready"]); self.assertEqual(F.calls + G.calls, [])
            ef, eg = R.ledger_events(F.a.ledger, 1)[0], R.ledger_events(G.a.ledger, 1)[0]
            self.assertEqual((ef["mode"], ef["ok"], ef["code"]), ("ready", True, None))
            self.assertEqual((eg["mode"], eg["ok"], eg["code"]), ("ready", False, "R2"))
            self.assertEqual(ef["hours"]["fallback"], 0); self.assertIn("manifest_v_share", ef["hours"]); self.assertEqual(eg["r2_share"], 0.5)

    def test_tool_crash_before_lock_is_not_terminal(self):
        with tempfile.TemporaryDirectory() as d:
            F = Fixture(d, fail_cmd="10_meta.py")
            with self.assertRaises(R.Refusal) as c:
                K.run_look(1, F.a, F)
            self.assertEqual(c.exception.code, "RUN"); self.assertEqual(F.events(), ["ready"])
            self.assertEqual(R.ledger_events(F.a.ledger, 1)[0]["mode"], "run")

    def _not_ready(self, F, lock=True):
        with self.assertRaises(R.Refusal) as c:
            K.run_look(1, F.a, F, lock=lock)
        self.assertEqual(c.exception.code, "NOT_READY")
        self.assertEqual(F.events(), ["ready"]); self.assertEqual(F.priced, 0)
        self.assertFalse(os.path.exists(os.path.join(F.O, "READ.lock")))
        return c.exception

    def test_missing_oracle_flag_is_not_ready(self):
        with tempfile.TemporaryDirectory() as d:
            F = Fixture(d); F.a.oracle_live = None
            self.assertIn("--oracle-live", str(self._not_ready(F))); self.assertEqual(F.calls, [])

    def test_no_per_print_v_flag_is_not_ready(self):
        with tempfile.TemporaryDirectory() as d:
            for lock in (True, False):
                F = Fixture(os.path.join(d, str(lock)) if os.makedirs(os.path.join(d, str(lock))) is None else d, v_flag=False)
                self.assertIn("v_ok", str(self._not_ready(F, lock)))

    def test_not_ready_after_the_lock_is_terminal(self):
        # review r7: a NOT_READY raised after the lock (price_rows with vmiss hours and no v_ok) spends the look as NOT_DECIDABLE
        class LateNotReady(Fixture):
            def price_fn(self, rows, tape_dir, guard, hour_source=None, vmiss_hours=None):
                self.priced += 1
                raise R.Refusal("NOT_READY", "simulated: v_ok absent at pricing")
        with tempfile.TemporaryDirectory() as d:
            F = LateNotReady(d)
            rec = K.run_look(1, F.a, F)
            self.assertEqual((rec["verdict"], rec["refused"]), ("NOT_DECIDABLE", "NOT_READY"), rec)
            self.assertEqual(F.events(), ["lock", "decisions", "not_decidable"])          # r3 item 3: the md5 survives a late refusal
            self.assertTrue(os.path.exists(os.path.join(F.O, "READ.lock")))
            with self.assertRaises(R.Refusal) as c:
                K.run_look(1, F.a, F)                                                   # spent: no re-run
            self.assertEqual(c.exception.code, "LOCK")

    def test_missing_assembly_is_not_ready_not_r8(self):
        with tempfile.TemporaryDirectory() as d:
            F = Fixture(d); os.remove(os.path.join(F.O, "look_assembly.json"))
            self._not_ready(F)


@unittest.skipIf(np is None, "numpy/pandas/duckdb missing")
class AdapterInterface(unittest.TestCase):
    def test_hour_status(self):
        with tempfile.TemporaryDirectory() as d:
            F = Fixture(d, drop_hours=["2026-10-12T03"])
            _, mans = K.load_assembly(1, F.O)
            mans[1]["files"][-1]["pumpswap_rows_with_v"] = 9                             # one V-incomplete hour
            hs = K.hour_status(1, mans, K.load_crosscheck(F.a.r1_crosscheck))
            self.assertEqual(hs["unwalked"], ["2026-10-12T03"]); self.assertIn("2026-10-12T03", hs["bad"])
            self.assertEqual(hs["vmiss"], [mans[1]["files"][-1]["hour"]])
            self.assertLess(hs["manifest_v_share"], 1.0); self.assertNotIn("v_coverage", hs)
            self.assertFalse(any(h < K.V_COVER_START for h in hs["vmiss"]))             # forward-1002 hours carry no counted price

    def test_price_rows_has_no_hour_level_v_proxy(self):
        P = PricingLayerFixture()
        with tempfile.TemporaryDirectory() as d:
            P._tape(d)
            for f in os.listdir(d):
                df = pd.read_parquet(os.path.join(d, f)); df["hour"] = f[:-8]; df.to_parquet(os.path.join(d, f), index=False)
            base = R.price_rows(P._rows(), d, R.ExplorationGuard()).iloc[0]
            self.assertFalse(bool(base.vmiss_p_END_l055))                                 # no v_ok, no vmiss hours: the E0 path, unchanged
            with self.assertRaises(R.Refusal) as c:                                        # the hour column is no longer a V status
                R.price_rows(P._rows(), d, R.ExplorationGuard(), vmiss_hours={"2026-09-20T00"})
            self.assertEqual(c.exception.code, "NOT_READY")
            d2 = os.path.join(d, "vok"); P._tape(d2, v_ok=lambda df: df.block_time < R.ep("2026-09-20T00") + 1300)
            a = R.price_rows(P._rows(), d2, R.ExplorationGuard()).iloc[0]
            b = R.price_rows(P._rows(), d2, R.ExplorationGuard(), vmiss_hours={"2026-09-20T00"}).iloc[0]
            self.assertTrue(bool(a.vmiss_p_END_l055))                                      # per print, from v_ok
            self.assertEqual((a.pnl_p_END_l055, bool(a.vmiss_p_END_l055)), (b.pnl_p_END_l055, bool(b.vmiss_p_END_l055)))

    def test_r3_and_v_flag_from_the_tape(self):
        with tempfile.TemporaryDirectory() as d:
            class G:
                def check_hour(self, s, h):
                    pass
            t0 = R.ep("2026-10-09T00")
            # T00: universe pool P1 has 4 PumpSwap prints (3 with V) and one bonding print; non-universe P9 has 5 PumpSwap prints, none with V
            pd.DataFrame(dict(venue=["pumpswap"] * 9 + ["bonding"], pool=["P1"] * 4 + ["P9"] * 5 + ["P1"], block_time=t0 + np.arange(10),
                              v_ok=[True, True, True, False] + [False] * 5 + [False])).to_parquet(os.path.join(d, "2026-10-09T00.parquet"), index=False)
            pd.DataFrame(dict(venue=["pumpswap"], pool=["P1"], block_time=[t0 + 3600])).to_parquet(os.path.join(d, "2026-10-09T01.parquet"), index=False)
            self.assertEqual(K.v_flag_missing(1, d, G()), ["2026-10-09T01"])
            pd.DataFrame(dict(venue=["pumpswap"], pool=["P1"], block_time=[t0 + 3600], v_ok=[True])).to_parquet(
                os.path.join(d, "2026-10-09T01.parquet"), index=False)
            self.assertEqual(K.v_flag_missing(1, d, G()), [])
            r3 = K.r3_coverage(1, d, ["P1"], G())
            self.assertEqual((r3["n"], r3["n_v"], r3["share"]), (5, 4, 0.8))                # the manifest share would be 4 / 10
            self.assertEqual(r3["by_date"], {"2026-10-09": dict(prints=5, with_v=4, share=0.8)})
            with self.assertRaises(R.Refusal) as c:
                R.r3_v_coverage(r3["share"])
            self.assertEqual(c.exception.code, "R3")
            self.assertIsNone(K.r3_coverage(1, d, [], G())["share"])

    def test_landing_pad_covers_one_slot_over_1_9_s(self):
        h = R.ep("2026-10-10T01")
        self.assertIn("2026-10-10T01", K.attempt_hours(h - 7200, h - 362))              # 1.9 s + 0.5 s + 360 s crosses the hour

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
            with self.assertRaises(R.Refusal) as c:                                        # r3 item 6: no tool_blob, no cover
                R.check_e0_records(d)
            self.assertEqual(c.exception.code, "R13")
            json.dump({"e0_pass": True, "tool_blob": "0" * 40}, open(os.path.join(d, "p4_e0.json"), "w"))
            with self.assertRaises(R.Refusal):
                R.check_e0_records(d)
            json.dump({"e0_pass": True, "tool_blob": R.tool_blob()}, open(os.path.join(d, "p4_e0.json"), "w"))
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


@unittest.skipIf(np is None, "numpy/pandas/duckdb missing")
class QuantProofR3(unittest.TestCase):
    def test_cli_has_no_overrides(self):
        for flag in ("--ledger", "--o-dir", "--final-marker", "--final-ledger", "--e0-dir", "--p2-universe", "--oracle-stale-s", "--look1-record"):
            with self.assertRaises(SystemExit) as c, contextlib.redirect_stderr(io.StringIO()):
                K.main(["run", "--look", "1", "--decoder-blobs", "x", flag, "x"])
            self.assertEqual(c.exception.code, 2, flag)
        for flag in ("--final-marker", "--final-ledger"):
            with self.assertRaises(SystemExit) as c, contextlib.redirect_stderr(io.StringIO()):
                R.main(["check", "--look", "1", flag, "x"])
            self.assertEqual(c.exception.code, 2, flag)

    def test_r8_after_the_deadline_spends_the_look(self):
        self.assertEqual((R.LOOKS[1]["deadline"], R.LOOKS[2]["deadline"]), ("2026-10-18T12:00", "2026-10-26T12:00"))
        with tempfile.TemporaryDirectory() as d:
            F = Fixture(d); F.a.now = R.ep("2026-10-18T12:01")
            rec = K.run_look(1, F.a, F)
            self.assertEqual((rec["verdict"], rec["refused"]), ("NOT_DECIDABLE", "R8"), rec)
            self.assertEqual(F.events(), ["lock", "not_decidable"]); self.assertEqual(F.calls, []); self.assertEqual(F.priced, 0)
        with tempfile.TemporaryDirectory() as d:
            F = Fixture(d); F.a.now = R.ep("2026-10-18T12:00")                         # at the deadline: not after it
            self.assertEqual(K.run_look(1, F.a, F)["verdict"], "PASS")

    def test_look2_after_a_look1_pass_refuses_without_a_lock(self):
        with tempfile.TemporaryDirectory() as d:
            F = Fixture(d)
            self.assertEqual(K.run_look(1, F.a, F)["verdict"], "PASS")
            F.a.now = R.ep("2026-10-24T03")
            with self.assertRaises(R.Refusal) as c:
                K.run_look(2, F.a, F)
            self.assertEqual(c.exception.code, "LOOK2")
            self.assertEqual([e["event"] for e in R.ledger_events(F.a.ledger, 2)], ["ready"])

    def test_look2_gate(self):
        with tempfile.TemporaryDirectory() as d:
            L = os.path.join(d, "L.jsonl")
            with self.assertRaises(R.Refusal) as c:
                K.look2_gate(L)
            self.assertEqual(c.exception.code, "NOT_READY")
            R.ledger_event(L, 1, "lock", token="t"); R.ledger_event(L, 1, "not_decidable", code="R11")
            self.assertEqual(K.look2_gate(L), "look1 not_decidable")
            L2 = os.path.join(d, "L2.jsonl"); R.ledger_event(L2, 1, "read", verdict="FAIL")
            self.assertEqual(K.look2_gate(L2), "look1 FAIL")

    def test_r9_from_look_reads(self):
        with tempfile.TemporaryDirectory() as d:
            L = os.path.join(d, "L.jsonl")
            self.assertEqual(R.r9_check(2, "m", L), "n/a: Look 1 refused before selection")    # Look 2 still runs after an early NOT_DECIDABLE
            R.ledger_event(L, 1, "decisions", decisions_md5="m", md5_look1_window="m", n_kept=3)
            self.assertEqual(R.r9_check(2, "m", L), "match")
            with self.assertRaises(R.Refusal) as c:
                R.r9_check(2, "x", L)
            self.assertEqual(c.exception.code, "R9")
            self.assertEqual(R.r9_check(1, "x", L), "n/a: Look 1")

    def test_r12_extra_tape_hour_before_pass_a(self):
        with tempfile.TemporaryDirectory() as d:
            F = Fixture(d)
            td = os.path.join(F.O, "tape", "trades"); os.makedirs(td)
            open(os.path.join(td, "2026-10-16T05.parquet"), "w").close()
            self.assertEqual(K.tape_outside_allowlist(1, F.O), [])
            open(os.path.join(td, "2026-10-17T02.parquet"), "w").close()                  # walk 2's first counted hour
            os.makedirs(os.path.join(F.O, "tape", "creates")); open(os.path.join(F.O, "tape", "creates", "junk.parquet"), "w").close()
            self.assertEqual(K.tape_outside_allowlist(1, F.O), ["trades/2026-10-17T02.parquet", "creates/junk.parquet"])
            RefusalsBeforeAnyPnl._nd(self, F, "R12", False)
            self.assertNotIn("10_meta.py", F.calls)

    def test_crosscheck_bad_hours_and_missing_record(self):
        with tempfile.TemporaryDirectory() as d:
            F = Fixture(d)
            _, mans = K.load_assembly(1, F.O)
            xc = K.load_crosscheck(F.a.r1_crosscheck)
            self.assertEqual(K.hour_status(1, mans, xc)["bad"], [])                     # forward-1002 / walk-2 hours need no cross-check
            xc["2026-10-11T04"] = 0.9949; del xc["2026-10-12T07"]; xc["2026-10-13T00"] = 0.995
            hs = K.hour_status(1, mans, xc)
            self.assertEqual(hs["xcheck_bad"], ["2026-10-11T04", "2026-10-12T07"]); self.assertEqual(hs["bad"], hs["xcheck_bad"])
            json.dump({"schema": K.CROSSCHECK_SCHEMA, "hours": {"2026-10-09T00": {"match_rate": None}}}, open(F.a.r1_crosscheck, "w"))
            self.assertIsNone(K.load_crosscheck(F.a.r1_crosscheck)["2026-10-09T00"])
            os.remove(F.a.r1_crosscheck)
            with self.assertRaises(R.Refusal) as c:
                K.run_look(1, F.a, F)
            self.assertEqual(c.exception.code, "NOT_READY"); self.assertEqual(F.events(), ["ready"])
            self.assertFalse(os.path.exists(os.path.join(F.O, "READ.lock")))

    def test_lock_refusal_after_the_lock_is_terminal(self):
        class LockGone(Fixture):
            def retrain(self, look, october, gtime, out, exploration_only, log):
                if not exploration_only:
                    os.remove(os.path.join(self.O, "READ.lock"))
                return Fixture.retrain(self, look, october, gtime, out, exploration_only, log)
        with tempfile.TemporaryDirectory() as d:
            F = LockGone(d)
            rec = K.run_look(1, F.a, F)
            self.assertEqual((rec["verdict"], rec["refused"]), ("NOT_DECIDABLE", "LOCK"), rec)
            self.assertEqual(F.events(), ["lock", "not_decidable"]); self.assertEqual(F.priced, 0)

    def test_e0_record_is_pinned_and_covers_this_code(self):
        self.assertIn("e0/p4_e0.json", R.check_pins())
        r = subprocess.run(["git", "hash-object", R.__file__], capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60)
        if r.returncode == 0:
            self.assertEqual(r.stdout.strip(), R.tool_blob())



@unittest.skipIf(np is None, "numpy/pandas/duckdb missing")
class QuantProofR4(unittest.TestCase):
    """r4: R8 runs before every readiness check, so a readiness item still unmet at the deadline ends the look (section 3, 11.4 R8)."""

    UNMET = {
        "FINAL marker absent (SEAL)": lambda F: os.remove(F.a.final_marker),
        "FINAL ledger empty (SEAL)": lambda F: open(F.a.final_ledger, "w").close(),
        "decoder blobs absent (R13)": lambda F: os.remove(F.a.decoder_blobs),
        "E0 record for other code (R13)": lambda F: json.dump({"e0_pass": True, "tool_blob": "0" * 40},
                                                              open(os.path.join(F.a.e0_dir, "p4_e0.json"), "w")),
        "no look assembly": lambda F: os.remove(os.path.join(F.O, "look_assembly.json")),
        "no R1 cross-check record": lambda F: os.remove(F.a.r1_crosscheck),
        "no --oracle-live": lambda F: setattr(F.a, "oracle_live", None),
        "no per-print v_ok": lambda F: setattr(F, "v_flag", False),
    }

    def test_r8_before_every_readiness_check(self):
        for name, unmet in self.UNMET.items():
            with self.subTest(name), tempfile.TemporaryDirectory() as d:
                F = Fixture(d); unmet(F)
                with self.assertRaises(R.Refusal) as c:                                # before the deadline: not terminal
                    K.run_look(1, F.a, F)
                self.assertNotEqual(c.exception.code, "R8"); self.assertEqual(F.events(), ["ready"])
                self.assertFalse(os.path.exists(os.path.join(F.O, "READ.lock")))
                F.a.now = R.ep("2026-10-18T12:01")                                     # after the deadline, still unmet: R8 ends the look
                rec = K.run_look(1, F.a, F)
                self.assertEqual((rec["verdict"], rec["refused"], rec["phase"]), ("NOT_DECIDABLE", "R8", "deadline"))
                self.assertEqual(F.events(), ["ready", "lock", "not_decidable"]); self.assertEqual(F.calls, []); self.assertEqual(F.priced, 0)
                nd = R.ledger_events(F.a.ledger, 1)[-1]
                self.assertEqual((nd["code"], nd["phase"]), ("R8", "deadline"))
                self.assertEqual(json.load(open(os.path.join(F.O, "look1_result.json")))["refused"], "R8")
                with self.assertRaises(R.Refusal) as c:                                # spent: no second terminal event
                    K.run_look(1, F.a, F)
                self.assertEqual(c.exception.code, "LOCK")
                self.assertEqual(F.events(), ["ready", "lock", "not_decidable", "ready"])
                # Look 1's R8 opens Look 2's gate; Look 2 past its own deadline with the same item unmet is NOT_DECIDABLE R8 too
                F.a.o_dir = os.path.join(d, "look2"); F.a.now = R.ep("2026-10-26T12:01")
                rec2 = K.run_look(2, F.a, F)
                self.assertEqual((rec2["verdict"], rec2["refused"], rec2["look2_gate"]), ("NOT_DECIDABLE", "R8", "look1 not_decidable"))
                self.assertEqual([e["event"] for e in R.ledger_events(F.a.ledger, 2)], ["lock", "not_decidable"])
                self.assertTrue(os.path.exists(os.path.join(d, "look2", "READ.lock")))

    def test_ready_mode_after_the_deadline_never_locks(self):
        with tempfile.TemporaryDirectory() as d:
            F = Fixture(d); F.a.now = R.ep("2026-10-18T12:01")
            self.assertTrue(K.run_look(1, F.a, F, lock=False)["ready"])
            self.assertEqual(F.events(), ["ready"]); self.assertFalse(os.path.exists(os.path.join(F.O, "READ.lock")))

    def test_look2_after_a_look1_pass_is_look2_not_r8(self):
        with tempfile.TemporaryDirectory() as d:
            F = Fixture(d)
            self.assertEqual(K.run_look(1, F.a, F)["verdict"], "PASS")
            F.a.o_dir = os.path.join(d, "look2"); F.a.now = R.ep("2026-10-26T12:01")    # past Look 2's deadline: still never runs
            with self.assertRaises(R.Refusal) as c:
                K.run_look(2, F.a, F)
            self.assertEqual(c.exception.code, "LOOK2")
            self.assertEqual([e["event"] for e in R.ledger_events(F.a.ledger, 2)], ["ready"])
            self.assertFalse(os.path.exists(os.path.join(d, "look2", "READ.lock")))

    def test_look2_before_look1_is_terminal_stays_not_ready(self):
        with tempfile.TemporaryDirectory() as d:
            F = Fixture(d); F.a.o_dir = os.path.join(d, "look2"); F.a.now = R.ep("2026-10-26T12:01")
            with self.assertRaises(R.Refusal) as c:
                K.run_look(2, F.a, F)
            self.assertEqual(c.exception.code, "NOT_READY")
            self.assertEqual([e["event"] for e in R.ledger_events(F.a.ledger, 2)], ["ready"])
            F.a.o_dir = F.O
            self.assertEqual(K.run_look(1, F.a, F)["refused"], "R8")                    # running Look 1 now ends it (R8) ...
            F.a.o_dir = os.path.join(d, "look2")
            self.assertEqual(K.run_look(2, F.a, F)["refused"], "R8")                    # ... and Look 2 then ends too


if __name__ == "__main__":
    unittest.main()
