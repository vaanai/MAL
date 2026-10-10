#!/usr/bin/env python3
"""EXP-025 (C1-NF) P4: the one locked job of a look (section 10, "In each locked job"; section 11.3; section 11.4).

    python3 tools/exp025_look.py ready --look 1 --decoder-blobs J --p7 P7.json [--oracle-live F ...]   # phase 0 only, no lock, codes and counts
    python3 tools/exp025_look.py run   --look 1 --decoder-blobs J --p7 P7.json [--oracle-live F ...]   # the locked read, once

Run it with the audit venv (numpy, pandas, duckdb); the two retrains run as subprocesses in the ML venv (numpy, lightgbm).

Order. Nothing below prices a trade before every refusal that can be decided without a price has been decided.
  Phase 0, readiness (not terminal; `ready` stops here): section 0 lines, EXP file clean against HEAD, SHA256SUMS pins, the FINAL marker and
    ledger entry and the end of the look's last allowlisted hour (LookGuard), R13 decoder blobs and E0 records, no lock yet, the look assembly
    record of the adapter's look driver (R8 if absent), the pinned patch applied (applied sha256 pinned).
  Phase 1, outcome-blind data refusals before the lock (terminal: a refusal takes the lock and writes `not_decidable`, so the look cannot be
    re-run on the same hours, section 11.5): R12 (manifest block vs the allowlist), R1 hour level (bad + unwalked hours), R2 (PDA match, the
    adapter's october_vmap record), R3 (V coverage), R6 (slot-time fallback hours of the pinned pass A's clock rule), 10_meta (universe only),
    R13 `mid` continuity against P2, R7 (pick oracle, booleans only, before pass A), R14 (the P7 record).
  Lock (O_EXCL READ.lock + LOOK_READS.jsonl `lock`). After it there is no resume.
  Phase 2, inside the lock: pinned pass A per graduation day 10-09 .. end-1, 12_passC, 14_export (labels exist only from here), the rows file,
    the 11.3 precount (exploration-only model; counts only) and R4, the labelled daily retrain, R1 attempt exclusion (> 5% refuses), then
    exp025_read.read_after_lock: R9, pricing, R11, section 7, report-only. Any refusal or crash here writes `not_decidable`.

Adapter interface (#563, tools/exp025_adapter.py). The look driver writes <O>/look_assembly.json:
    {"schema": "exp025_look_assembly_v1", "look": "look1", "manifests": [<convert() manifest.json per block>], "r2": <october_vmap's r2 dict>,
     "append_tokens": <append_tokens' dict>}
  and the hunt layout under <O>/tape (trades carry `block` and `hour`; PumpSwap quote_reserve is event-V mapped where the print had V and the
  pool a V0). A manifest's trade file record carries pumpswap_rows / pumpswap_rows_with_v. The adapter writes no per-print V flag; until it does
  (a `v_ok` column), an hour whose PumpSwap prints are not all V-mapped counts every print as V-missing for pricing (fail closed, section 6).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import traceback

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import exp025_read as R  # noqa: E402

AUDIT_PY = "/data/mal/audit-1008/venv/bin/python"
ML_PY = "/data/mal/venv/bin/python"
P2_UNIVERSE = "/data/mal/hunt-1008/c1nf-p2/work/universe.parquet"
ASSEMBLY_SCHEMA = "exp025_look_assembly_v1"
MANIFEST_SCHEMA = "exp025_adapter_manifest_v1"
V_COVER_START = "2026-10-09T00"          # P6 item 2 (the adapter's V_COVER_START)
BLOCK_OF_SOURCE = {"forward-1002": "forward-1002", "forward-1002ev": "forward-1002ev", "forward-1016": "walk2"}   # read-tool source -> adapter block
R2_MIN = R3_MIN = 0.95                    # equal to tools/exp025_adapter.R2_MIN / R3_MIN (a test checks it when the adapter is importable)
LANDING_PAD_S = 1.9 + 300 + 60           # R1: an attempt needs hours through landing (<= 1.9 s, B1) + 300 s + 60 s


# ----------------------------------------------------------------------------------------------------------------- adapter outputs
def load_assembly(look: int, o_dir: str):
    p = os.path.join(o_dir, "look_assembly.json")
    if not os.path.exists(p):
        raise R.Refusal("R8", f"{p} missing: the adapter's look driver has not assembled look {look}")
    A = json.load(open(p))
    if A.get("schema") != ASSEMBLY_SCHEMA or A.get("look") != f"look{look}":
        raise R.Refusal("R8", f"{p}: schema {A.get('schema')!r} / look {A.get('look')!r}")
    mans = []
    for m in A.get("manifests", []):
        d = json.load(open(m))
        if d.get("schema") != MANIFEST_SCHEMA:
            raise R.Refusal("R8", f"{m}: not an adapter manifest")
        mans.append(d)
    if not mans:
        raise R.Refusal("R8", f"{p} lists no manifest")
    return A, mans


def hour_status(look: int, mans) -> dict:
    """From the adapter manifests: bad hours (strict-line failures of any kind, plus allowlisted hours with no trades file = not walked),
    V-incomplete hours (from V_COVER_START: event_v false, counts missing, or pumpswap_rows_with_v < pumpswap_rows), and the V-coverage counts.
    R12: a trades file from a block other than the allowlist's for its hour, or an hour outside the allowlist, refuses."""
    allow = dict((h, src) for src, h in R.allowlisted_hours(look))
    files, bad = {}, set()
    for m in mans:
        for b in m.get("bad", []):
            bad.add(b["hour"])
        for f in m.get("files", []):
            if f.get("kind") != "trades":
                continue
            h = f["hour"]
            if h not in allow:
                raise R.Refusal("R12", f"manifest holds {h}, outside look {look}'s allowlist")
            if m.get("block") != BLOCK_OF_SOURCE[allow[h]]:
                raise R.Refusal("R12", f"{h} came from block {m.get('block')!r}; the allowlist says {allow[h]}")
            files[h] = (m, f)
    unwalked = sorted(h for h in allow if h not in files and h not in bad)
    vmiss, n_ps, n_v = set(), 0, 0
    for h, (m, f) in files.items():
        if R.ep(h) < R.ep(V_COVER_START):
            continue
        ps, wv = f.get("pumpswap_rows"), f.get("pumpswap_rows_with_v")
        if not m.get("event_v") or ps is None or wv is None:
            vmiss.add(h)
            continue
        n_ps += int(ps); n_v += int(wv)
        if int(wv) < int(ps):
            vmiss.add(h)
    return dict(bad=sorted(bad | set(unwalked)), unwalked=unwalked, vmiss=sorted(vmiss), n_ps=n_ps, n_v=n_v,
                v_coverage=(n_v / n_ps) if n_ps else 0.0, v_basis="manifest: all PumpSwap prints of V-covered hours")


def v_hours(look: int):
    return [h for _, h in R.allowlisted_hours(look) if R.ep(h) >= R.ep(V_COVER_START)]


def _tape_files(look: int, tape_dir: str, hours, guard):
    allow = dict((h, src) for src, h in R.allowlisted_hours(look))
    fs = []
    for h in hours:
        guard.check_hour(allow.get(h, "outside"), h)
        f = os.path.join(tape_dir, f"{h}.parquet")
        R.assert_not_closed(f)
        if os.path.exists(f):
            fs.append(f)
    return fs


def fallback_hours(look: int, tape_dir: str, guard) -> list:
    """R6: hours of [V_COVER_START, hours_end) for which the pinned pass A's clock rule (11_passA.py: an hour has its own seconds-per-slot only if
    its block_time span is >= 1800 s and its slot rises) gives no value, so pass A uses the nearest hour's. Counts only."""
    import duckdb
    hrs = v_hours(look)
    fs = _tape_files(look, tape_dir, hrs, guard)
    if not fs:
        return list(hrs)
    con = duckdb.connect()
    L = "['" + "','".join(fs) + "']"
    CK = con.execute(f"SELECT block_time bt, min(slot) s FROM read_parquet({L}) WHERE block_time IS NOT NULL GROUP BY 1 ORDER BY 1").df()
    con.close()
    bt, sl = CK.bt.values.astype(np.int64), CK.s.values.astype(np.int64)
    ok = set()
    for h in np.unique(bt // 3600):
        m = (bt // 3600) == h
        if bt[m][-1] - bt[m][0] >= 1800 and sl[m][-1] > sl[m][0]:
            ok.add(int(h) * 3600)
    return [h for h in hrs if R.ep(h) not in ok]


def first_print_ms(look: int, tape_dir: str, pools, guard) -> dict:
    """Section 5.3: each universe pool's first PumpSwap print (ms) on the look's V-covered tape (a universe pool has V0, so its s0 is there)."""
    import duckdb
    import pandas as pd
    fs = _tape_files(look, tape_dir, v_hours(look), guard)
    if not fs:
        return {}
    con = duckdb.connect()
    con.register("pp", pd.DataFrame({"pool": sorted(set(pools))}))
    L = "['" + "','".join(fs) + "']"
    df = con.execute(f"""SELECT pool, min(block_time) bt FROM read_parquet({L}) WHERE venue = 'pumpswap' AND pool IN (SELECT pool FROM pp)
                         GROUP BY pool""").df()
    con.close()
    return {p: int(b) * 1000 for p, b in zip(df.pool, df.bt)}


# ----------------------------------------------------------------------------------------------------------------- rows, precount, R1 attempts
def build_rows(october_npz: str, universe_parquet: str, cand_dir: str, days):
    """The read's rows from 14_export's october.npz (t, mid, row order = idx) + the look universe (mint, pool, v, g, gday, cbt) + pass A's
    out/candx/<D>.parquet h_top1 / h_top5 in float64 (14_export's own row source, pass A's h_top1 carried by 12_passC; the export's F is
    float32 and the cap compares h_top1 <= 0.5 on pass A's value).
    Returns (rows, gtime {mid: graduation epoch})."""
    import pandas as pd
    z = np.load(october_npz, allow_pickle=False)
    rows = pd.DataFrame({"idx": np.arange(len(z["t"]), dtype=np.int64), "t": z["t"].astype(np.int64), "mid": z["mid"].astype(np.int64)})
    U = pd.read_parquet(universe_parquet, columns=["mid", "mint", "pool", "v", "g", "gday", "cbt"])
    rows = rows.merge(U, on="mid", how="left", validate="many_to_one")
    if rows.mint.isna().any():
        raise R.Refusal("R13", f"{int(rows.mint.isna().sum())} export rows have a mid outside the look universe")
    C = pd.concat([pd.read_parquet(os.path.join(cand_dir, f"{d}.parquet"), columns=["mid", "t", "h_top1", "h_top5"]) for d in days
                   if os.path.exists(os.path.join(cand_dir, f"{d}.parquet"))], ignore_index=True)
    C = C.rename(columns={"h_top1": "f_h_top1", "h_top5": "f_h_top5"}).astype({"mid": np.int64, "t": np.int64})
    rows = rows.merge(C, on=["mid", "t"], how="left", validate="one_to_one", indicator=True)
    if (rows._merge != "both").any():
        raise R.Refusal("R13", f"{int((rows._merge != 'both').sum())} export rows have no pass-A candidate row")
    rows = rows.drop(columns="_merge").sort_values("idx").reset_index(drop=True)
    return rows, {str(int(m)): int(g) for m, g in zip(U.mid, U.g)}


def attempt_hours(cbt, t) -> list:
    """R1: the hours an attempt needs, from its mint's create hour through the hour holding landing + 300 s + 60 s."""
    h0 = int(cbt) // 3600 * 3600
    return [R.hour_str(h) for h in range(h0, int(t + LANDING_PAD_S) // 3600 * 3600 + 1, 3600)]


def r1_attempt_keep(look: int, rows, kept, bad) -> np.ndarray:
    """R1 attempt level. The refusal (> 5% of the kept attempts need a bad hour) is decided on the kept attempts; the returned mask (False =
    excluded and counted) covers every row, so the report-only books exclude the same attempts."""
    need = [attempt_hours(c, t) for c, t in zip(rows.cbt.values, rows.t.values)]
    R.r1_bad_hours(look, bad, [a for a, k in zip(need, kept) if k])
    b = set(bad)
    return np.array([not (set(a) & b) for a in need], bool)


def precount(look: int, rows, preds, U, excluded_by_gday: dict, hs: dict, fb: list, r2: dict) -> dict:
    """Section 11.3: per UTC date of the window, counts and shares only. No price, fill, exit, pnl, mean, CI or day sign."""
    import pandas as pd
    cap = R._cap()
    cfg = json.load(open(os.path.join(R.ART, "rule.json")))
    L = R.LOOKS[look]
    P = pd.DataFrame({"t": np.asarray(preds["t"], np.int64), "mid": np.asarray(preds["mid"], np.int64), "pred": np.asarray(preds["pred"], float),
                      "s1": np.asarray(preds["s1"], bool)})
    X = rows[["t", "mid", "gday", "f_h_top1"]].merge(P, on=["t", "mid"], how="left", validate="one_to_one")
    X["date"] = [R.date_str(x) for x in X.t]
    sel = X.pred.notna().values & (X.pred.values > cfg["threshold"])
    X["sel"] = sel
    X["kept"] = cap.apply_cap_before_book(X.f_h_top1.values, sel)
    dates = [R.date_str(x) for x in range(R.ep(L["start"]), R.ep(L["end"]), 86400)]
    out = {}
    for d in dates:
        m = X.date.values == d
        out[d] = dict(graduations=int((U.gday == d).sum() + excluded_by_gday.get(d, 0)), universe_rows=int(m.sum()), stage1=int(X.s1.values[m].sum()),
                      selections=int(X.sel.values[m].sum()), kept=int(X.kept.values[m].sum()), oracle_dropped=int(excluded_by_gday.get(d, 0)),
                      bad_hours=sum(1 for h in hs["bad"] if h.startswith(d)), fallback_hours=sum(1 for h in fb if h.startswith(d)))
    shares = dict(pda_match=r2.get("share"), v_coverage=hs["v_coverage"], v_basis=hs["v_basis"])
    return dict(look=look, dates=out, shares=shares)


# ----------------------------------------------------------------------------------------------------------------- steps (injectable for tests)
class Steps:
    """The real side effects. Tests replace these with fixtures; the order and the refusals live in run_look."""

    def __init__(self, a):
        self.a = a

    def preflight(self, look: int):
        a = self.a
        R.section0_lines(open(R.EXP_FILE).read()); R.exp_clean_against_head(); R.check_pins()
        guard = R.LookGuard(look, a.now, a.final_marker, a.final_ledger)
        R.check_decoder_blobs(a.decoder_blobs); R.check_e0_records(a.e0_dir)
        return guard

    def apply_patch(self, look: int, W: str):
        return R.apply_look_patch(look, W)

    def run(self, cmd, log):
        with open(log, "a") as f:
            f.write("$ " + " ".join(cmd) + "\n"); f.flush()
            r = subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=f, stderr=subprocess.STDOUT)
        if r.returncode != 0:
            raise R.Refusal("RUN", f"{os.path.basename(cmd[1])} exited {r.returncode} (log {log})")

    def retrain(self, look: int, october: str, gtime: str, out: str, exploration_only: bool, log: str):
        cmd = [ML_PY, os.path.join(R.ROOT, "tools", "exp025_read.py"), "retrain", "--look", str(look), "--october", october, "--gtime", gtime,
               "--out", out] + (["--exploration-only"] if exploration_only else [])
        self.run(cmd, log)
        return dict(np.load(out, allow_pickle=False))

    def fallback_hours(self, look, tape_dir, guard):
        return fallback_hours(look, tape_dir, guard)

    def first_print_ms(self, look, tape_dir, pools, guard):
        return first_print_ms(look, tape_dir, pools, guard)

    def oracle(self):
        a = self.a
        if not a.oracle_live:
            return None
        from cap_pick_oracle import PickOracle
        kw = {} if a.oracle_stale_s is None else dict(stale_s=a.oracle_stale_s)
        return PickOracle(a.oracle_live, a.oracle_replay or (), final_marker=a.final_marker, **kw)

    price_fn = staticmethod(R.price_rows)


# ----------------------------------------------------------------------------------------------------------------- the runner
def _p7(path):
    if not path or not os.path.exists(path):
        raise R.Refusal("R14", "P7 record missing")
    d = json.load(open(path))
    R.r14_p7(tuple(d["cp"]), tuple(d["fee"]))
    return d


def run_look(look: int, a, steps=None, lock: bool = True) -> dict:
    """lock=False is `ready`: phase 0 and the phase-1 counts, no lock, no 10_meta, no oracle. Returns the record (also printed by main)."""
    S = steps or Steps(a)
    L = R.LOOKS[look]; O = a.o_dir or L["O"]; ledger = a.ledger
    log = os.path.join(O, "run_look.log")
    rec = dict(look=look, phase="readiness")
    # ---- phase 0: readiness (not terminal)
    guard = S.preflight(look)
    if os.path.exists(os.path.join(O, "READ.lock")) or any(e["event"] == "lock" for e in R.ledger_events(ledger, look)):
        raise R.Refusal("LOCK", f"look {look} is already locked (no resume)")
    A, mans = load_assembly(look, O)
    W = os.path.join(O, "work_read")
    rec["applied_sha256"] = S.apply_patch(look, W)
    _, cmds = R.pipeline_commands(look)
    out_path = os.path.join(O, f"look{look}_result.json")
    took = False
    try:
        # ---- phase 1: outcome-blind data refusals (terminal)
        rec["phase"] = "pre-lock refusals"
        hs = hour_status(look, mans)                                                   # R12 on the manifests
        rec["hours"] = dict(bad=len(hs["bad"]), unwalked=len(hs["unwalked"]), v_incomplete=len(hs["vmiss"]), v_coverage=hs["v_coverage"])
        R.r1_bad_hours(look, hs["bad"], [])                                            # R1, hour level
        fb = S.fallback_hours(look, os.path.join(O, "tape", "trades"), guard)
        rec["hours"]["fallback"] = len(fb)
        R.share_refusals(float(A["r2"]["share"]), hs["v_coverage"], len(fb), len(v_hours(look)))   # R2, R3, R6
        if not lock:
            rec["ready"] = True
            return rec
        p7 = _p7(a.p7)                                                                 # R14
        rec["p7"] = {k: p7[k] for k in ("cp", "fee") if k in p7}
        os.makedirs(O, exist_ok=True)
        S.run(cmds[0], log)                                                            # 10_meta: the universe (no label)
        upath = os.path.join(O, "work", "universe.parquet")
        rec["mid_checked"] = R.check_mid_continuity(upath, a.p2_universe)              # R13
        import pandas as pd
        U = pd.read_parquet(upath)
        fp = S.first_print_ms(look, os.path.join(O, "tape", "trades"), U.pool.values, guard)
        keep, n_ex = R.oracle_exclude(list(U.mint), [fp.get(p) for p in U.pool], S.oracle())   # R7, booleans only, before pass A
        ex_by_gday = U.gday[~keep].value_counts().to_dict()
        if n_ex:
            os.replace(upath, os.path.join(O, "work", "universe_pre_oracle.parquet"))
            U[keep].to_parquet(upath, index=False)
        rec["oracle_excluded"] = n_ex
        # ---- the lock
        R.take_lock(look, ledger, O)
        took = True
        rec["phase"] = "locked"
        # ---- phase 2: labels exist from here
        for c in cmds[1:]:
            S.run(c, log)                                                              # 11_passA per day, 12_passC, 14_export
        days = [R.date_str(x) for x in range(R.ep("2026-10-09T00"), R.ep(L["end"]), 86400)]
        rows, gt = build_rows(os.path.join(O, "ml", "october.npz"), upath, os.path.join(O, "out", "candx"), days)
        gpath = os.path.join(O, "ml", "gtime.json"); json.dump(gt, open(gpath, "w"))
        pe = S.retrain(look, os.path.join(O, "ml", "october.npz"), gpath, os.path.join(O, "ml", "preds_exploration_only.npz"), True, log)
        pc = precount(look, rows, pe, U[keep], ex_by_gday, hs, fb, A["r2"])
        json.dump(pc, open(os.path.join(O, "precount.json"), "w"), indent=1)
        print(json.dumps(dict(precount=pc)), flush=True)
        R.r4_precount(look, {d: v["kept"] for d, v in pc["dates"].items()})            # R4 (R5: never on a high count)
        pl = S.retrain(look, os.path.join(O, "ml", "october.npz"), gpath, os.path.join(O, "ml", "preds.npz"), False, log)
        cfg = json.load(open(os.path.join(R.ART, "rule.json")))
        P = pd.DataFrame({"t": pl["t"].astype(np.int64), "mid": pl["mid"].astype(np.int64), "pred": pl["pred"].astype(float)})
        X = rows.merge(P, on=["t", "mid"], how="left", validate="one_to_one")
        counted = (X.t >= R.ep(L["start"])).values & (X.t < R.ep(L["end"])).values
        st2 = counted & X.pred.notna().values & (X.pred.values > cfg["threshold"])
        kept = R._cap().apply_cap_before_book(X.f_h_top1.values, st2)
        rows["r1_keep"] = r1_attempt_keep(look, rows, kept, hs["bad"])                # R1, attempt level
        res = R.read_after_lock(look, rows, pl, guard, out_path, o_dir=O, ledger=ledger, look1_record=a.look1_record,
                                vmiss_hours=hs["vmiss"], price_fn=S.price_fn)
        rec.update(phase="read", verdict=res["verdict"], result=out_path)
        return rec
    except R.Refusal as e:
        # not terminal: `ready` (no lock ever), a LOCK refusal, and a tool crash before the lock (10_meta: outcome-blind, fixable, no outcome seen)
        if not lock or e.code == "LOCK" or (e.code == "RUN" and not took):
            raise
        return _not_decidable(look, ledger, O, took, rec, e.code, str(e), out_path)
    except Exception as e:  # noqa: BLE001 - after the lock a crash is a non-run of the look (section 11.5)
        if not took or not lock:
            raise
        return _not_decidable(look, ledger, O, took, rec, "RUN", f"{type(e).__name__}: {e} | {traceback.format_exc(limit=3)}"[:2000], out_path)


def _not_decidable(look, ledger, O, took, rec, code, reason, out_path) -> dict:
    if not took:
        R.take_lock(look, ledger, O)             # a data refusal before the lock still spends the look: lock it so it cannot be re-run
    R.ledger_event(ledger, look, "not_decidable", code=code, reason=reason[:500], phase=rec.get("phase"))
    rec.update(verdict="NOT_DECIDABLE", refused=code, reason=reason)
    json.dump(rec, open(out_path, "w"), indent=1, default=str)
    return rec


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    for name in ("ready", "run"):
        c = sp.add_parser(name)
        c.add_argument("--look", type=int, choices=(1, 2), required=True)
        c.add_argument("--decoder-blobs", required=True); c.add_argument("--p7")
        c.add_argument("--final-marker", default=R.FINAL_MARKER); c.add_argument("--final-ledger", default=R.FINAL_LEDGER)
        c.add_argument("--e0-dir", default=R.E0_DIR); c.add_argument("--p2-universe", default=P2_UNIVERSE)
        c.add_argument("--ledger", default=R.LOOK_LEDGER); c.add_argument("--o-dir")
        c.add_argument("--oracle-live", nargs="*"); c.add_argument("--oracle-replay", nargs="*"); c.add_argument("--oracle-stale-s", type=float)
        c.add_argument("--look1-record")
    a = ap.parse_args(argv)
    a.now = None
    try:
        rec = run_look(a.look, a, lock=(a.cmd == "run"))
    except R.Refusal as e:
        print(json.dumps(dict(look=a.look, refused=e.code, reason=str(e), terminal=False)))
        return 2
    print(json.dumps({k: rec.get(k) for k in ("look", "phase", "ready", "verdict", "refused", "result")}))
    return 0 if rec.get("verdict") in (None, "PASS", "FAIL") else 2


if __name__ == "__main__":
    sys.exit(main())
