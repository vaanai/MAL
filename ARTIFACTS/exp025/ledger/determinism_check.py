#!/usr/bin/env python3
"""EXP-025 ledger determinism check (exploration tape only; no outcome, no forward row).

  A. Contrast: the ORIGINAL ledger SQL run twice on one day (threads 3) -> number of rows that differ.
  B. The deterministic ledger (01_wallet_daily_det.py) run twice on the same day, with different thread counts (3, then 1) and different temp dirs
     -> sha256 of the two files must be equal.
  C. (--full) The deterministic ledger over every tape day, twice -> every file sha256 equal; then the 11_passA.py aggregation SQL (sum of n, nbond,
     cash, nwin, nrt, count, buy over all prior days, GROUP BY th) run R times with threads 3, 2, 1 -> one result hash, equal to an exact
     python-integer reference.
Exit status 0 only if B (and C when run) hold. Run: nice -n 19 <duckdb python> determinism_check.py --tape T --work W [--day D] [--full]
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def load_det():
    spec = importlib.util.spec_from_file_location("wallet_det", os.path.join(HERE, "01_wallet_daily_det.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def original_day(con, files, out):
    lst = "['" + "','".join(sorted(files)) + "']"
    con.execute(f"""COPY (
      WITH pm AS (
        SELECT hash(trader) th, mint, count(*) n, sum((venue='pump_bonding')::INT) nbond,
               sum(CASE WHEN side='buy' THEN sol_lamports ELSE 0 END)/1e9 buy, sum(CASE WHEN side='sell' THEN sol_lamports ELSE 0 END)/1e9 sell,
               sum((side='buy')::INT) nb, sum((side='sell')::INT) ns
        FROM read_parquet({lst}) WHERE venue IN ('pump_bonding','pumpswap') GROUP BY 1, 2)
      SELECT th, sum(n) n, count(*) nm, sum(nbond) nbond, sum(buy) buy, sum(sell) sell,
             sum((ns > 0 AND nb > 0 AND sell > buy)::INT) nwin, sum((ns > 0 AND nb > 0)::INT) nrt, sum(sell - buy) cash
      FROM pm GROUP BY 1) TO '{out}' (FORMAT parquet)""")


def agg_hash(con, paths):
    lst = "['" + "','".join(paths) + "']"
    x = con.execute(f"""SELECT th, sum(n) n, sum(nbond) nbond, sum(cash) cash, sum(nwin) nwin, sum(nrt) nrt, count(*) ndays, sum(buy) buy
                        FROM read_parquet({lst}) GROUP BY 1 ORDER BY 1""").df()
    return hashlib.sha256(x.to_numpy().astype("float64").tobytes() + x["th"].to_numpy().tobytes()).hexdigest(), x


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tape", required=True)
    ap.add_argument("--work", required=True)
    ap.add_argument("--day", default="2026-09-20")
    ap.add_argument("--full", action="store_true")
    a = ap.parse_args()
    import duckdb
    import numpy as np

    det = load_det()
    os.makedirs(a.work, exist_ok=True)
    fs = sorted(glob.glob(f"{a.tape}/{a.day}T*.parquet"))
    print(f"day {a.day}: {len(fs)} hour files", flush=True)
    ok = True

    def conn(threads, tmp):
        os.makedirs(tmp, exist_ok=True)
        c = duckdb.connect()
        c.execute(f"SET memory_limit='6GB'; SET threads={threads}; SET temp_directory='{tmp}'; SET preserve_insertion_order=false")
        return c

    # A. contrast
    pa, pb = f"{a.work}/orig_a.parquet", f"{a.work}/orig_b.parquet"
    original_day(conn(3, a.work + "/ta"), fs, pa)
    original_day(conn(3, a.work + "/tb"), fs, pb)
    c = duckdb.connect()
    nd = c.execute(f"""SELECT count(*) FROM (SELECT * FROM '{pa}' EXCEPT ALL SELECT * FROM '{pb}')""").fetchone()[0]
    nrows = c.execute(f"SELECT count(*) FROM '{pa}'").fetchone()[0]
    sd = c.execute(f"""SELECT count(*) FROM '{pa}' a JOIN '{pb}' b USING (th) WHERE (a.cash > 0) <> (b.cash > 0)""").fetchone()[0]
    print(f"A. ORIGINAL ledger, two runs: {nd} of {nrows} wallet rows differ; cash sign flips (skill = cash > 0): {sd}", flush=True)

    # B. deterministic, two runs, different threads and temp dirs
    p1, p2 = f"{a.work}/det_a.parquet", f"{a.work}/det_b.parquet"
    for p in (p1, p2):
        if os.path.exists(p):
            os.remove(p)
    r1 = det.build_day(conn(3, a.work + "/t1"), fs, p1)
    r2 = det.build_day(conn(1, a.work + "/t2"), fs, p2)
    s1, s2 = sha(p1), sha(p2)
    print(f"B. DETERMINISTIC ledger: run1 threads 3 rows {r1} sha256 {s1}", flush=True)
    print(f"B. DETERMINISTIC ledger: run2 threads 1 rows {r2} sha256 {s2}", flush=True)
    same = s1 == s2
    print(f"B. file sha256 equal: {same}", flush=True)
    ok &= same
    grid_ok = c.execute(f"""SELECT bool_and(abs(cash * 1048576 - round(cash * 1048576)) = 0 AND abs(buy * 1048576 - round(buy * 1048576)) = 0
                                       AND abs(sell * 1048576 - round(sell * 1048576)) = 0) FROM '{p1}'""").fetchone()[0]
    print(f"B. every buy/sell/cash value is an integer multiple of 2^-20: {grid_ok}", flush=True)
    ok &= bool(grid_ok)

    if a.full:
        days = sorted({os.path.basename(p)[:10] for p in glob.glob(f"{a.tape}/*.parquet")})
        for tag, th in (("x", 3), ("y", 1)):
            out = f"{a.work}/full_{tag}"
            os.makedirs(out, exist_ok=True)
            det.main(["--tape", a.tape, "--out", out, "--tmp", f"{a.work}/tf{tag}", "--threads", str(th)])
        shx = {os.path.basename(p): sha(p) for p in glob.glob(f"{a.work}/full_x/*.parquet")}
        shy = {os.path.basename(p): sha(p) for p in glob.glob(f"{a.work}/full_y/*.parquet")}
        eq = shx == shy and len(shx) == len(days)
        print(f"C. {len(days)} tape days, two full builds (threads 3 and 1): {len(shx)} files, all sha256 equal: {eq}", flush=True)
        ok &= eq
        paths = [f"{a.work}/full_x/{d}.parquet" for d in days[:-1]]
        hs = []
        for th in (3, 3, 2, 1, 1):
            h, x = agg_hash(conn(th, f"{a.work}/ta{th}"), paths)
            hs.append(h)
        print(f"C. pass-A prior-day aggregation over {len(paths)} days, 5 runs (threads 3,3,2,1,1): hashes {sorted(set(hs))} -> equal: {len(set(hs)) == 1}", flush=True)
        ok &= len(set(hs)) == 1
        # exact reference: scale every cash to integer multiples of 2^-20 and add as python ints
        cc = duckdb.connect()
        ref = cc.execute(f"""SELECT th, sum(round(cash * 1048576)::HUGEINT) c FROM read_parquet(['{"','".join(paths)}']) GROUP BY 1 ORDER BY 1""").df()
        exact = np.array([float(int(v)) / 1048576.0 for v in ref["c"]])
        match = bool(np.array_equal(exact, x["cash"].to_numpy()))
        print(f"C. aggregated cash equals the exact integer reference on all {len(ref)} wallets: {match}", flush=True)
        ok &= match
    print("RESULT", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
