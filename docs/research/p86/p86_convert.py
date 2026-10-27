"""P86 step 0: extract PumpSwap trade rows (no outcome) from clean-view hour files to one parquet per hour.
Blocks: explore-0814, fresh-0903, exp011-0909 only. Usage: p86_convert.py OUTDIR [nthreads]"""
import glob, os, sys
import duckdb

OUT = sys.argv[1]
BLOCKS = ["explore-0814", "fresh-0903", "exp011-0909"]
files = []
for b in BLOCKS:
    files += sorted(glob.glob(f"/data/mal/clean-view/{b}/*/trades/trades-*.jsonl.zst"))
con = duckdb.connect()
con.execute(f"PRAGMA threads={int(sys.argv[2]) if len(sys.argv) > 2 else 4}; PRAGMA memory_limit='6GB'")
os.makedirs(OUT, exist_ok=True)
done = 0
for f in files:
    parts = f.split("/")
    name = parts[4] + "_" + parts[5] + "_" + os.path.basename(f).replace(".jsonl.zst", "")
    o = f"{OUT}/{name}.parquet"
    if os.path.exists(o):
        continue
    con.execute(f"""COPY (SELECT pool, mint, side, sol_lamports AS sol, token_raw AS tok, quote_reserve AS q, base_reserve AS b,
        slot, block_time AS bt, tx_index AS txi, event_index AS evi, trader, signature AS sig, quote_is_wsol AS wsol
        FROM read_json('{f}', format='newline_delimited', maximum_object_size=10000000, ignore_errors=true,
        columns={{venue:'VARCHAR',pool:'VARCHAR',mint:'VARCHAR',side:'VARCHAR',sol_lamports:'BIGINT',token_raw:'HUGEINT',quote_reserve:'HUGEINT',base_reserve:'HUGEINT',
                 slot:'BIGINT',block_time:'BIGINT',tx_index:'INTEGER',event_index:'INTEGER',trader:'VARCHAR',signature:'VARCHAR',quote_is_wsol:'BOOLEAN'}})
        WHERE venue='pumpswap') TO '{o}.tmp' (FORMAT parquet, COMPRESSION zstd)""")
    os.rename(o + ".tmp", o)
    done += 1
    if done % 20 == 0:
        print(done, "of", len(files), flush=True)
print("files", len(files), "done", done)
