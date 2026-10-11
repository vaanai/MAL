#!/usr/bin/env python3
"""EXP-025 deterministic wallet ledger. Replaces scripts/01_wallet_daily_ORIGINAL_nondeterministic.py (sha256 0580faa0...).

What was wrong. The original summed float SOL (`sum(CASE ... sol_lamports ELSE 0 END)/1e9`) in a parallel DuckDB group-by. Float addition is not
associative, so two runs of one day differ on 38,107-69,660 wallet rows (<= 3e-8 SOL), `cash` changes sign for about 3 wallets per day, and the
`skill = cash > 0` feature in 11_passA.py flips (c1nf-verify/VERIFY.md section 2).

What this does.
  1. Every per-(wallet, mint) and per-wallet total is summed in INTEGER lamports (BIGINT inputs, exact, order-independent).
  2. `nwin` compares the exact integer sums (`sell_l > buy_l`), not float sums.
  3. SOL columns (`buy`, `sell`, `cash`) are written on a 2^-20 SOL grid: round(lamports * 2^20 / 1e9) / 2^20. Every value is then an integer multiple
     of 2^-20 below 2^33 SOL, so the cross-day float sums inside 11_passA.py (`sum(cash)`, `sum(buy)` over prior days, DuckDB, 3 threads) are exact
     whatever the add order. The grid error is at most 4.77e-7 SOL per wallet-day. The pinned 11_passA.py is NOT edited.
  4. Rows are written ORDER BY th, zstd, one file per UTC day, so the file bytes do not depend on the thread count.
Schema equals the original: th UBIGINT, n DOUBLE, nm BIGINT, nbond DOUBLE, buy DOUBLE, sell DOUBLE, nwin DOUBLE, nrt DOUBLE, cash DOUBLE.

Usage: 01_wallet_daily_det.py --tape DIR --out DIR --tmp DIR [--threads 3] [--day YYYY-MM-DD ...]
The tape directory holds <YYYY-MM-DD>T<HH>.parquet trade files (the hunt layout of ref/convert.py). An existing output day is skipped.
Pin: ARTIFACTS/exp025/SHA256SUMS.
"""
from __future__ import annotations

import argparse
import glob
import os
import sys
import time

GRID = 2.0 ** 20  # SOL grid: 2^-20
LAMPORTS = 1e9
_SCALE = GRID / LAMPORTS

COLS = ("th", "n", "nm", "nbond", "buy", "sell", "nwin", "nrt", "cash")


def to_grid(lamports):
    """Integer lamports (numpy int64 array) -> float64 SOL on the 2^-20 grid. Pure IEEE operations, so the result is deterministic."""
    import numpy as np

    return np.rint(np.asarray(lamports, dtype=np.float64) * _SCALE) / GRID


def build_day(con, files: list[str], out_path: str) -> int:
    """Write one day's ledger to out_path (via a .tmp file and rename). Returns the number of wallet rows."""
    import numpy as np

    lst = "['" + "','".join(sorted(files)) + "']"
    df = con.execute(
        f"""
        WITH pm AS (
          SELECT hash(trader) th, mint, count(*) n, sum((venue='pump_bonding')::INT) nbond,
                 sum(CASE WHEN side='buy' THEN sol_lamports ELSE 0 END)::BIGINT buy_l,
                 sum(CASE WHEN side='sell' THEN sol_lamports ELSE 0 END)::BIGINT sell_l,
                 sum((side='buy')::INT) nb, sum((side='sell')::INT) ns
          FROM read_parquet({lst}) WHERE venue IN ('pump_bonding','pumpswap') GROUP BY 1, 2)
        SELECT th, sum(n)::BIGINT n, count(*)::BIGINT nm, sum(nbond)::BIGINT nbond,
               sum(buy_l)::BIGINT buy_l, sum(sell_l)::BIGINT sell_l,
               sum((ns > 0 AND nb > 0 AND sell_l > buy_l)::INT)::BIGINT nwin,
               sum((ns > 0 AND nb > 0)::INT)::BIGINT nrt,
               sum(sell_l - buy_l)::BIGINT cash_l
        FROM pm GROUP BY 1 ORDER BY 1"""
    ).df()
    out = df[["th"]].copy()
    out["n"] = df["n"].to_numpy(np.int64).astype(np.float64)
    out["nm"] = df["nm"].to_numpy(np.int64)
    out["nbond"] = df["nbond"].to_numpy(np.int64).astype(np.float64)
    out["buy"] = to_grid(df["buy_l"].to_numpy(np.int64))
    out["sell"] = to_grid(df["sell_l"].to_numpy(np.int64))
    out["nwin"] = df["nwin"].to_numpy(np.int64).astype(np.float64)
    out["nrt"] = df["nrt"].to_numpy(np.int64).astype(np.float64)
    out["cash"] = to_grid(df["cash_l"].to_numpy(np.int64))
    out = out[list(COLS)].sort_values("th", kind="mergesort").reset_index(drop=True)
    con.register("out_df", out)
    tmp = out_path + ".tmp"
    con.execute(f"COPY (SELECT * FROM out_df ORDER BY th) TO '{tmp}' (FORMAT parquet, COMPRESSION zstd)")
    con.unregister("out_df")
    os.rename(tmp, out_path)
    return len(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--tape", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--tmp", required=True)
    ap.add_argument("--threads", type=int, default=3)
    ap.add_argument("--memory-limit", default="6GB")
    ap.add_argument("--day", action="append", default=[])
    a = ap.parse_args(argv)
    import duckdb

    os.makedirs(a.out, exist_ok=True)
    os.makedirs(a.tmp, exist_ok=True)
    con = duckdb.connect()
    con.execute(f"SET memory_limit='{a.memory_limit}'; SET threads={int(a.threads)}; SET temp_directory='{a.tmp}'; SET preserve_insertion_order=false")
    days = sorted({os.path.basename(p)[:10] for p in glob.glob(f"{a.tape}/*.parquet")})
    if a.day:
        days = [d for d in days if d in set(a.day)]
    for d in days:
        o = f"{a.out}/{d}.parquet"
        if os.path.exists(o):
            continue
        t0 = time.time()
        fs = sorted(glob.glob(f"{a.tape}/{d}T*.parquet"))
        n = build_day(con, fs, o)
        print(d, len(fs), n, round(time.time() - t0, 1), flush=True)
    print("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
