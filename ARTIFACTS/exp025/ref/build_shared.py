#!/usr/bin/env python3
"""Build the shared hunt data layer from the exploration Parquet tape.  See README.md in this directory for every column and convention.

  python build_shared.py [--tape DIR] [--out DIR] [--from YYYY-MM-DDTHH] [--to YYYY-MM-DDTHH (exclusive)]

Phases (all resumable; every output is written to <name>.tmp and renamed):
  1  per UTC day: read the day's trade hours once, split by hash(mint) into 16 bucket files, then per bucket write
       - the 1-minute bars (without the new-wallet columns)   -> tmp/bars/<block>/<day>.parquet
       - (trader, mint, venue, block) partials for the day     -> tmp/wm/<bucket>/<day>.parquet
       - (mint, venue, pool, block) token partials             -> tmp/tok/<day>.parquet
  2  per bucket over all days: wallet_mint/<block>.parquet, distinct-trader counts, and the "new wallet" buy table
  3  tokens.parquet and the final bars_1m/<block>/<day>.parquet (bars + new-wallet columns)

Exploration tape only.  Memory: one day at a time, DuckDB memory_limit 6GB, threads 4, spill under <out>/tmp/duck.
"""
from __future__ import annotations

import argparse
import calendar
import json
import os
import shutil
import sys
import time
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

DEFAULT_TAPE = "/data/mal/audit-1008/tape"
DEFAULT_OUT = "/data/mal/hunt-shared"
VMAP_DIR = "/data/mal/pumpswap-virtual"
# V maps in priority order (pool -> virtual quote lamports).  pool_v_0909 is the map cap_pick_score reads; the others fill pools it lacks.
VMAP_FILES = ("pool_v_0909.json", "pool_v_exp016.json", "pool_v_0814.json", "pool_v.json")
WSOL = "So11111111111111111111111111111111111111112"  # a "mint" on PumpSwap pools where WSOL is the base side: not a token, excluded
V_MAX = 30_000_000_000  # a map V outside [0, 30 SOL] (the map has negative and 5e12 garbage for non-pump pools) is NOT added to the price (V := 0)
NB = 16  # hash(mint) buckets
ZSTD_LEVEL = 3
CUTOFF_HOUR = "2026-10-02T10"
EXP009 = ("2026-09-15T12", "2026-09-18T23")  # [lo, hi) never read
FORBIDDEN = ("fresh-0802", "fresh-0808", "fresh-0828", "forward", "oracle-live", "arm-audit", "exp012-gate", "positions", "heartbeat", "runner")
BUCKET_SQL = "((ascii(substr(mint,1,1))*31 + ascii(substr(mint,2,1))*7 + ascii(substr(mint,3,1))) % 16)"


def log(msg: str) -> None:
    print(time.strftime("%H:%M:%S"), msg, flush=True)


def check_path(p: str) -> None:
    real = os.path.realpath(p).lower()
    for f in FORBIDDEN:
        if f in p.lower() or f in real:
            raise SystemExit(f"refused: forbidden path part {f!r} in {p}")


def atomic_write_table(table: pa.Table, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    pq.write_table(table, tmp, compression="zstd", compression_level=ZSTD_LEVEL, row_group_size=1_000_000)
    os.replace(tmp, path)


def connect(out: Path) -> duckdb.DuckDBPyConnection:
    spill = out / "tmp" / "duck"
    spill.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.execute("SET memory_limit='6GB'")
    con.execute("SET threads=4")
    con.execute(f"SET temp_directory='{spill}'")
    con.execute("SET preserve_insertion_order=false")
    return con


def load_vmap(con: duckdb.DuckDBPyConnection) -> None:
    """vmap(pool, v, src): the first map in VMAP_FILES that has a non-null V for the pool wins."""
    seen: dict[str, tuple[int, str]] = {}
    for fn in VMAP_FILES:
        p = Path(VMAP_DIR) / fn
        if not p.exists():
            log(f"vmap {fn} missing, skipped")
            continue
        for pool, v in json.loads(p.read_text())["v"].items():
            if v is not None and pool not in seen:
                seen[pool] = (int(v), fn.removesuffix(".json"))
    t = pa.table({"pool": list(seen), "v": [x[0] for x in seen.values()], "src": [x[1] for x in seen.values()]})
    con.register("vmap_arrow", t)
    con.execute("CREATE OR REPLACE TABLE vmap AS SELECT * FROM vmap_arrow")
    con.unregister("vmap_arrow")
    log(f"vmap pools={len(seen)}")


def list_hours(tape: Path, lo: str | None, hi: str | None) -> list[str]:
    hours = sorted(p.stem for p in (tape / "trades").glob("*.parquet"))
    keep = []
    for h in hours:
        if h >= CUTOFF_HOUR or EXP009[0] <= h < EXP009[1]:
            continue
        if (lo and h < lo) or (hi and h >= hi):
            continue
        keep.append(h)
    return keep


# --------------------------------------------------------------------------------------------------------------- phase 1
def phase1_day(con: duckdb.DuckDBPyConnection, tape: Path, out: Path, day: str, hours: list[str]) -> None:
    tmp = out / "tmp"
    marker = tmp / "done" / f"p1-{day}.json"
    if marker.exists():
        log(f"p1 {day} skip (done)")
        return
    t0 = time.time()
    stage = tmp / "stage" / day
    shutil.rmtree(stage, ignore_errors=True)
    stage.mkdir(parents=True)
    files = [str(tape / "trades" / f"{h}.parquet") for h in hours]
    flist = "[" + ",".join(f"'{f}'" for f in files) + "]"
    raw = con.execute(f"SELECT count(*) FROM read_parquet({flist})").fetchone()[0]
    # price: SOL per token. PumpSwap rows are PRE-trade reserves, price = (quote + V)/(base*1000) with the pool's V; bonding rows are POST-trade, V = 0.
    con.execute(f"""
        COPY (
          SELECT {BUCKET_SQL} AS bk, t.venue, t.mint, t.trader, (t.side = 'buy') AS is_buy, t.sol_lamports::DOUBLE AS lam, t.token_raw AS tok,
                 t.quote_reserve AS q, t.base_reserve AS b, v.v AS vlam, t.slot,
                 (coalesce(t.tx_index,0)::BIGINT * 100000 + least(coalesce(t.event_index,0), 99999))::BIGINT AS ord,
                 (CAST(substr(t.hour,12,2) AS BIGINT) * 10000000 + t.file_row_number)::BIGINT AS seq,
                 t.block_time * 1000 AS ms, t.block, t.pool,
                 (CASE WHEN t.venue = 'pumpswap' THEN (t.quote_reserve + CASE WHEN v.v BETWEEN 0 AND {V_MAX} THEN v.v ELSE 0 END) / (t.base_reserve * 1000.0)
                       ELSE t.quote_reserve / (t.base_reserve * 1000.0) END) AS price,
                 (coalesce(t.lp_fee,0) + coalesce(t.protocol_fee,0) + coalesce(t.creator_fee,0))::DOUBLE AS fee_lam
          FROM read_parquet({flist}, file_row_number=true) t LEFT JOIN vmap v ON v.pool = t.pool  -- equality only (an extra non-equi term makes DuckDB plan a nested loop); bonding rows have a NULL pool
          WHERE t.mint <> '{WSOL}' AND t.block_time IS NOT NULL AND t.base_reserve > 0 AND t.quote_reserve IS NOT NULL
        ) TO '{stage}' (FORMAT PARQUET, PARTITION_BY (bk), COMPRESSION zstd, OVERWRITE_OR_IGNORE)""")
    staged = con.execute(f"SELECT count(*) FROM read_parquet('{stage}/*/*.parquet')").fetchone()[0]
    wsol = con.execute(f"SELECT count(*) FROM read_parquet({flist}) WHERE mint = '{WSOL}'").fetchone()[0]
    unmapped = con.execute(f"SELECT count(*) FROM read_parquet('{stage}/*/*.parquet') WHERE venue='pumpswap' AND vlam IS NULL").fetchone()[0]
    log(f"p1 {day} staged {staged:,} of {raw:,} raw rows (wsol {wsol:,}) in {time.time()-t0:.0f}s")

    bars_parts: dict[str, list[pa.Table]] = {}
    tok_parts: list[pa.Table] = []
    n_wm = 0
    for k in range(NB):
        d = stage / f"bk={k}"
        if not d.exists():
            continue
        con.execute(f"""CREATE OR REPLACE TEMP TABLE d AS
            SELECT *, row_number() OVER (ORDER BY slot, ord, seq) AS rn FROM read_parquet('{d}/*.parquet')""")
        bars = con.execute("""
            SELECT block, mint, venue, (ms // 60000) * 60000 AS minute_ms,
              arg_min(price, rn) AS open, max(price) AS high, min(price) AS low, arg_max(price, rn) AS close,
              sum(lam) FILTER (WHERE is_buy) / 1e9 AS buy_sol, sum(lam) FILTER (WHERE NOT is_buy) / 1e9 AS sell_sol,
              (sum(lam) / nullif(sum(tok), 0)) / 1000.0 AS vwap,
              count(*) FILTER (WHERE is_buy)::INTEGER AS n_buys, count(*) FILTER (WHERE NOT is_buy)::INTEGER AS n_sells,
              count(DISTINCT trader) FILTER (WHERE is_buy)::INTEGER AS n_distinct_buyers,
              count(DISTINCT trader) FILTER (WHERE NOT is_buy)::INTEGER AS n_distinct_sellers,
              (coalesce(sum(lam) FILTER (WHERE is_buy),0) - coalesce(sum(lam) FILTER (WHERE NOT is_buy),0)) / 1e9 AS net_sol,
              max(lam) FILTER (WHERE is_buy) / 1e9 AS max_buy_sol, max(lam) FILTER (WHERE NOT is_buy) / 1e9 AS max_sell_sol,
              arg_max(q, rn) AS last_quote_reserve, arg_max(b, rn) AS last_base_reserve, max(slot) AS last_slot,
              max(vlam) AS v_lamports
            FROM d GROUP BY block, mint, venue, ms // 60000""").fetch_arrow_table()
        # split by block
        for blk in sorted(set(bars["block"].to_pylist())):
            bars_parts.setdefault(blk, []).append(bars.filter(pc.equal(bars["block"], blk)).drop(["block"]))
        wm_dir = tmp / "wm" / f"{k:02d}"
        wm_dir.mkdir(parents=True, exist_ok=True)
        wm_tmp = wm_dir / f"{day}.parquet.tmp"
        con.execute(f"""COPY (
            WITH pf AS (SELECT trader, mint, min(ms) AS pair_first_ms FROM d GROUP BY 1, 2)
            SELECT d.trader, d.mint, d.venue, d.block, '{day}' AS day,
              min(d.ms) AS first_ms, max(d.ms) AS last_ms,
              coalesce(sum(d.lam) FILTER (WHERE d.is_buy),0) AS buy_lam, coalesce(sum(d.lam) FILTER (WHERE NOT d.is_buy),0) AS sell_lam,
              count(*) FILTER (WHERE d.is_buy)::INTEGER AS n_buys, count(*) FILTER (WHERE NOT d.is_buy)::INTEGER AS n_sells,
              coalesce(sum(d.tok) FILTER (WHERE d.is_buy),0) AS buy_tok, coalesce(sum(d.tok) FILTER (WHERE NOT d.is_buy),0) AS sell_tok,
              sum(d.fee_lam) AS fee_lam,
              min(d.slot) FILTER (WHERE d.is_buy) AS first_buy_slot, min(d.ms) FILTER (WHERE d.is_buy) AS first_buy_ms,
              arg_min(d.price, d.rn) FILTER (WHERE d.is_buy) AS first_buy_price,
              min(pf.pair_first_ms) AS pair_first_ms,
              coalesce(sum(d.lam) FILTER (WHERE d.is_buy AND d.ms // 60000 = pf.pair_first_ms // 60000),0) AS firstmin_buy_lam,
              (count(*) FILTER (WHERE d.is_buy AND d.ms // 60000 = pf.pair_first_ms // 60000))::INTEGER AS firstmin_n_buys
            FROM d JOIN pf ON pf.trader = d.trader AND pf.mint = d.mint
            GROUP BY d.trader, d.mint, d.venue, d.block
        ) TO '{wm_tmp}' (FORMAT PARQUET, COMPRESSION zstd)""")
        n_wm += pq.ParquetFile(wm_tmp).metadata.num_rows
        os.replace(wm_tmp, wm_dir / f"{day}.parquet")
        tok_parts.append(con.execute("""
            SELECT mint, venue, pool, block, min(ms) AS first_ms, max(ms) AS last_ms, min(slot) AS first_slot, max(slot) AS last_slot,
              coalesce(sum(lam) FILTER (WHERE is_buy),0) AS buy_lam, coalesce(sum(lam) FILTER (WHERE NOT is_buy),0) AS sell_lam, count(*)::BIGINT AS n_trades,
              arg_min(q, rn) AS first_q, arg_min(b, rn) AS first_b, max(vlam) AS vlam
            FROM d GROUP BY mint, venue, pool, block""").fetch_arrow_table())
    n_bars = 0
    for blk, parts in bars_parts.items():
        t = pa.concat_tables(parts)
        t = t.take(pc.sort_indices(t, sort_keys=[("mint", "ascending"), ("venue", "ascending"), ("minute_ms", "ascending")]))
        n_bars += t.num_rows
        atomic_write_table(t, tmp / "bars" / blk / f"{day}.parquet")
    atomic_write_table(pa.concat_tables(tok_parts), tmp / "tok" / f"{day}.parquet")
    shutil.rmtree(stage, ignore_errors=True)
    stats = dict(day=day, hours=len(hours), raw_rows=raw, wsol_rows_excluded=wsol, staged_rows=staged, pumpswap_rows_without_v=unmapped,
                 bar_rows=n_bars, wallet_partial_rows=n_wm, seconds=round(time.time() - t0, 1))
    (marker.parent).mkdir(parents=True, exist_ok=True)
    marker.with_name(marker.name + ".tmp").write_text(json.dumps(stats))
    os.replace(marker.with_name(marker.name + ".tmp"), marker)
    log(f"p1 {day} done {stats}")


# --------------------------------------------------------------------------------------------------------------- phase 2
WM_FINAL_SQL = """
SELECT trader, mint, block, min(first_ms) AS first_ms, max(last_ms) AS last_ms,
  sum(buy_lam) / 1e9 AS buy_sol, sum(sell_lam) / 1e9 AS sell_sol, sum(n_buys)::INTEGER AS n_buys, sum(n_sells)::INTEGER AS n_sells,
  sum(buy_tok) AS buy_tok, sum(sell_tok) AS sell_tok,
  min(first_buy_ms) AS first_buy_ms, arg_min(first_buy_price, first_buy_slot) FILTER (WHERE first_buy_slot IS NOT NULL) AS first_buy_price,
  arg_min(venue, first_buy_slot) FILTER (WHERE first_buy_slot IS NOT NULL) AS first_buy_venue,
  sum(fee_lam) / 1e9 AS fee_sol_reported,
  (sum(sell_lam) - sum(buy_lam) - sum(fee_lam)) / 1e9 AS pnl_est_sol
FROM src GROUP BY trader, mint, block"""


def phase2(con: duckdb.DuckDBPyConnection, out: Path, days: list[str]) -> None:
    tmp = out / "tmp"
    blocks = sorted(p.name for p in (tmp / "bars").iterdir())
    final = {b: out / "wallet_mint" / f"{b}.parquet" for b in blocks}
    need_wm = [b for b in blocks if not final[b].exists()]
    need_aux = not all((tmp / "done" / f"p2-{k:02d}").exists() for k in range(NB))
    if not need_wm and not need_aux:
        log("p2 skip (done)")
        return
    writers: dict[str, pq.ParquetWriter] = {}
    schema = None
    (out / "wallet_mint").mkdir(exist_ok=True)
    (tmp / "ntr").mkdir(exist_ok=True)
    (tmp / "newbuy").mkdir(exist_ok=True)
    for k in range(NB):
        d = tmp / "wm" / f"{k:02d}"
        if not d.exists():
            continue
        t0 = time.time()
        con.execute(f"CREATE OR REPLACE TEMP VIEW src AS SELECT * FROM read_parquet('{d}/*.parquet')")
        if need_wm:
            rdr = con.execute(WM_FINAL_SQL).fetch_record_batch(500_000)
            for batch in rdr:
                tb = pa.Table.from_batches([batch])
                for b in need_wm:
                    sub = tb.filter(pc.equal(tb["block"], b)).drop(["block"])
                    if sub.num_rows == 0:
                        continue
                    if b not in writers:
                        writers[b] = pq.ParquetWriter(str(final[b]) + ".tmp", sub.schema, compression="zstd", compression_level=ZSTD_LEVEL)
                    writers[b].write_table(sub.cast(writers[b].schema), row_group_size=500_000)
        if need_aux:
            # distinct traders per (mint, venue) and per mint over ALL days and blocks of this bucket
            con.execute(f"""COPY (SELECT mint, venue, count(DISTINCT trader)::INTEGER AS n_traders FROM src GROUP BY 1, 2)
                            TO '{tmp / 'ntr' / f'{k:02d}.parquet'}' (FORMAT PARQUET, COMPRESSION zstd)""")
            con.execute(f"""COPY (SELECT mint, 'ALL' AS venue, count(DISTINCT trader)::INTEGER AS n_traders FROM src GROUP BY 1)
                            TO '{tmp / 'ntr' / f'{k:02d}_all.parquet'}' (FORMAT PARQUET, COMPRESSION zstd)""")
            # buys of wallets in the minute of their first-ever print in the mint (pair first day = min(day) over all partials)
            con.execute(f"""COPY (
                WITH f AS (SELECT trader, mint, min(day) AS fd FROM src GROUP BY 1, 2)
                SELECT s.mint, s.venue, (s.pair_first_ms // 60000) * 60000 AS minute_ms,
                       sum(s.firstmin_buy_lam) / 1e9 AS new_buy_sol, count(*) FILTER (WHERE s.firstmin_n_buys > 0)::INTEGER AS n_new_buyers
                FROM src s JOIN f ON f.trader = s.trader AND f.mint = s.mint AND f.fd = s.day
                GROUP BY s.mint, s.venue, (s.pair_first_ms // 60000) * 60000
            ) TO '{tmp / 'newbuy' / f'{k:02d}.parquet'}' (FORMAT PARQUET, COMPRESSION zstd)""")
            (tmp / "done").mkdir(exist_ok=True)
            (tmp / "done" / f"p2-{k:02d}").write_text("ok")
        log(f"p2 bucket {k:02d} done in {time.time()-t0:.0f}s")
    for b, w in writers.items():
        w.close()
        os.replace(str(final[b]) + ".tmp", final[b])
        log(f"wallet_mint/{b}.parquet rows={pq.ParquetFile(final[b]).metadata.num_rows:,}")


# --------------------------------------------------------------------------------------------------------------- phase 3
def phase3_bars(con: duckdb.DuckDBPyConnection, out: Path) -> None:
    tmp = out / "tmp"
    con.execute(f"CREATE OR REPLACE TEMP VIEW newbuy AS SELECT * FROM read_parquet('{tmp}/newbuy/*.parquet')")
    for bdir in sorted((tmp / "bars").iterdir()):
        for f in sorted(bdir.glob("*.parquet")):
            dest = out / "bars_1m" / bdir.name / f.name
            if dest.exists():
                continue
            day = f.stem
            lo = calendar.timegm(time.strptime(day, "%Y-%m-%d")) * 1000
            hi = lo + 86_400_000
            tb = con.execute(f"""
                SELECT b.mint, b.venue, b.minute_ms, b.open, b.high, b.low, b.close, b.vwap, b.buy_sol, b.sell_sol, b.n_buys, b.n_sells,
                       b.n_distinct_buyers, b.n_distinct_sellers, b.net_sol, b.max_buy_sol, b.max_sell_sol,
                       b.last_quote_reserve, b.last_base_reserve, b.last_slot, b.v_lamports,
                       coalesce(n.new_buy_sol, 0) AS new_buy_sol, coalesce(n.n_new_buyers, 0)::INTEGER AS n_new_buyers,
                       coalesce(n.new_buy_sol, 0) / nullif(b.buy_sol, 0) AS new_buyer_share
                FROM read_parquet('{f}') b
                LEFT JOIN (SELECT * FROM newbuy WHERE minute_ms >= {lo} AND minute_ms < {hi}) n
                  ON n.mint = b.mint AND n.venue = b.venue AND n.minute_ms = b.minute_ms
                ORDER BY b.mint, b.venue, b.minute_ms""").fetch_arrow_table()
            atomic_write_table(tb, dest)
            log(f"bars_1m/{bdir.name}/{f.name} rows={tb.num_rows:,} bytes={dest.stat().st_size:,}")


TOKENS_SQL = """
WITH tp AS (  -- (mint, venue, pool) over all days
  SELECT mint, venue, pool, min(first_ms) AS first_ms, max(last_ms) AS last_ms, min(first_slot) AS first_slot,
         sum(buy_lam) AS buy_lam, sum(sell_lam) AS sell_lam, sum(n_trades) AS n_trades,
         arg_min(first_q, first_slot) AS first_q, arg_min(first_b, first_slot) AS first_b, arg_min(block, first_slot) AS block
  FROM read_parquet('{tmp}/tok/*.parquet') GROUP BY mint, venue, pool),
bc AS (SELECT mint, min(first_ms) AS first_ms, max(last_ms) AS last_ms, sum(buy_lam)/1e9 AS buy_sol, sum(sell_lam)/1e9 AS sell_sol, sum(n_trades) AS n_trades,
              arg_min(block, first_slot) AS block
       FROM tp WHERE venue = 'pump_bonding' GROUP BY mint),
pp AS (SELECT *, row_number() OVER (PARTITION BY mint ORDER BY first_slot, pool) AS r_first,
                 row_number() OVER (PARTITION BY mint ORDER BY buy_lam + sell_lam DESC, pool) AS r_vol,
                 count(*) OVER (PARTITION BY mint) AS n_pools
       FROM tp WHERE venue = 'pumpswap'),
ps AS (SELECT mint, min(first_ms) AS first_ms, max(last_ms) AS last_ms, sum(buy_lam)/1e9 AS buy_sol, sum(sell_lam)/1e9 AS sell_sol, sum(n_trades) AS n_trades,
              max(n_pools) AS n_pools, arg_min(block, first_slot) AS block
       FROM pp GROUP BY mint),
canon AS (SELECT pp.mint, pp.pool, pp.first_ms AS pool_first_ms, pp.first_slot AS pool_first_slot, pp.first_q, pp.first_b,
                 vm.v AS v0_lamports, vm.src AS v0_source
          FROM pp LEFT JOIN vmap vm ON vm.pool = pp.pool WHERE r_first = 1),
topv AS (SELECT mint, pool AS pool_max_volume FROM pp WHERE r_vol = 1),
cr AS (SELECT mint, arg_min(creator, slot) AS creator, arg_min(name, slot) AS name, arg_min(symbol, slot) AS symbol,
              arg_min(is_mayhem_mode, slot) AS is_mayhem_mode, min(block_time) * 1000 AS create_ms, min(slot) AS create_slot, arg_min(block, slot) AS cblock
       FROM read_parquet({creates}) WHERE block_time IS NOT NULL GROUP BY mint),
mg AS (SELECT mint, min(block_time) * 1000 AS complete_ms, min(slot) AS complete_slot
       FROM read_parquet({migs}) WHERE type = 'complete' AND block_time IS NOT NULL GROUP BY mint),
ntr AS (SELECT mint,
          max(n_traders) FILTER (WHERE venue = 'pump_bonding') AS bc_n_traders,
          max(n_traders) FILTER (WHERE venue = 'pumpswap') AS ps_n_traders,
          max(n_traders) FILTER (WHERE venue = 'ALL') AS n_traders_all
        FROM read_parquet('{tmp}/ntr/*.parquet') GROUP BY mint),
allm AS (SELECT mint FROM cr UNION SELECT mint FROM bc UNION SELECT mint FROM ps)
SELECT a.mint, cr.create_ms, cr.create_slot, cr.creator, cr.name, cr.symbol, cr.is_mayhem_mode,
       (cr.mint IS NOT NULL) AS has_create,
       coalesce(cr.cblock, bc.block, ps.block) AS block,
       coalesce(mg.complete_ms, c.pool_first_ms) AS grad_ms,
       CASE WHEN mg.complete_ms IS NOT NULL THEN 'complete' WHEN c.pool_first_ms IS NOT NULL THEN 'first_pumpswap' END AS grad_src,
       mg.complete_ms, mg.complete_slot,
       c.pool, tv.pool_max_volume, coalesce(ps.n_pools, 0)::INTEGER AS n_pumpswap_pools,
       c.v0_lamports, c.v0_source,
       (c.first_q + CASE WHEN c.v0_lamports BETWEEN 0 AND {V_MAX} THEN c.v0_lamports ELSE 0 END) / (c.first_b * 1000.0) AS ps_first_price,
       bc.first_ms AS bc_first_ms, bc.last_ms AS bc_last_ms, bc.buy_sol AS bc_buy_sol, bc.sell_sol AS bc_sell_sol, bc.n_trades AS bc_n_trades, ntr.bc_n_traders,
       ps.first_ms AS ps_first_ms, ps.last_ms AS ps_last_ms, ps.buy_sol AS ps_buy_sol, ps.sell_sol AS ps_sell_sol, ps.n_trades AS ps_n_trades, ntr.ps_n_traders,
       ntr.n_traders_all
FROM allm a LEFT JOIN cr USING (mint) LEFT JOIN bc USING (mint) LEFT JOIN ps USING (mint) LEFT JOIN mg USING (mint)
     LEFT JOIN canon c USING (mint) LEFT JOIN topv tv USING (mint) LEFT JOIN ntr USING (mint)
ORDER BY a.mint"""


def phase3_tokens(con: duckdb.DuckDBPyConnection, tape: Path, out: Path, hours: list[str]) -> None:
    dest = out / "tokens.parquet"
    if dest.exists():
        log("tokens skip (exists)")
        return
    cf = [str(tape / "creates" / f"{h}.parquet") for h in hours if (tape / "creates" / f"{h}.parquet").exists()]
    mf = [str(tape / "migrations" / f"{h}.parquet") for h in hours if (tape / "migrations" / f"{h}.parquet").exists()]
    lst = lambda xs: "[" + ",".join(f"'{x}'" for x in xs) + "]"
    sql = TOKENS_SQL.replace("{V_MAX}", str(V_MAX)).replace("{tmp}", str(out / "tmp")).replace("{creates}", lst(cf)).replace("{migs}", lst(mf))
    tb = con.execute(sql).fetch_arrow_table()
    atomic_write_table(tb, dest)
    log(f"tokens.parquet rows={tb.num_rows:,} bytes={dest.stat().st_size:,}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tape", default=DEFAULT_TAPE)
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--from", dest="lo", default=None)
    ap.add_argument("--to", dest="hi", default=None)
    ap.add_argument("--keep-tmp", action="store_true")
    a = ap.parse_args()
    check_path(a.tape)
    check_path(a.out)
    tape, out = Path(a.tape), Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / ".nobackup").touch()
    hours = list_hours(tape, a.lo, a.hi)
    if not hours:
        raise SystemExit("no hours selected")
    days = sorted({h[:10] for h in hours})
    log(f"hours={len(hours)} days={len(days)} first={hours[0]} last={hours[-1]}")
    con = connect(out)
    load_vmap(con)
    for day in days:
        phase1_day(con, tape, out, day, [h for h in hours if h[:10] == day])
    phase2(con, out, days)
    phase3_bars(con, out)
    phase3_tokens(con, tape, out, hours)
    stats = [json.loads(p.read_text()) for p in sorted((out / "tmp" / "done").glob("p1-*.json"))]
    (out / "stats.json").write_text(json.dumps(stats, indent=1))
    con.close()
    if not a.keep_tmp:
        shutil.rmtree(out / "tmp", ignore_errors=True)
    log("build complete")


if __name__ == "__main__":
    main()
