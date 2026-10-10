"""Audit 2026-10-08: compact Parquet copy of EXPLORATION-POOL tape only (docs/HOLDOUT_LEDGER.md).
Excluded on purpose: fresh-0802/0808/0828 (sealed), EXP-009 block [09-15T12,09-18T23), forward walk (>=10-02).
Oracle in-sample hours stop at 09-25T06 (T07 belongs to the Oracle live-tape row)."""
import duckdb, glob, os, sys, time, json, re
OUT = '/data/mal/audit-1008/tape'
SRC = [
  ('explore-0814', '/data/mal/clean-view/explore-0814/w*'),
  ('fresh-0903',   '/data/mal/blocks-clean/fresh-0903/w*'),
  ('exp011-0909',  '/data/mal/clean-view/exp011-0909/[bc]'),
  ('fast-pool-0918', '/data/mal/clean-view/fast-pool-2026-09-18T23_2026-09-22T00'),
  ('oracle-insample-0922', '/data/mal/clean-view/oracle-insample-2026-09-22_25'),
]
FORBIDDEN = ('fresh-0802','fresh-0808','fresh-0828','forward')
TR_COLS = "{venue:'VARCHAR',mint:'VARCHAR',trader:'VARCHAR',side:'VARCHAR',sol_lamports:'BIGINT',token_raw:'HUGEINT',quote_reserve:'HUGEINT',base_reserve:'HUGEINT',pool:'VARCHAR',slot:'BIGINT',tx_index:'INTEGER',event_index:'INTEGER',block_time:'BIGINT',lp_fee:'BIGINT',protocol_fee:'BIGINT',creator_fee:'BIGINT'}"
CR_COLS = "{mint:'VARCHAR',creator:'VARCHAR',trader:'VARCHAR',name:'VARCHAR',symbol:'VARCHAR',is_mayhem_mode:'BOOLEAN',quote_reserve:'HUGEINT',base_reserve:'HUGEINT',real_token_reserves:'HUGEINT',token_raw:'HUGEINT',slot:'BIGINT',tx_index:'INTEGER',event_index:'INTEGER',block_time:'BIGINT',signature:'VARCHAR'}"
MG_COLS = "{type:'VARCHAR',mint:'VARCHAR',trader:'VARCHAR',bonding_curve:'VARCHAR',slot:'BIGINT',tx_index:'INTEGER',event_index:'INTEGER',block_time:'BIGINT',signature:'VARCHAR'}"
con = duckdb.connect()
con.execute("SET memory_limit='6GB'; SET threads=8; SET temp_directory='/data/mal/audit-1008/tmp'; SET preserve_insertion_order=false")
hour_re = re.compile(r'(trades|creates|migrations)-(\d{4}-\d{2}-\d{2}T\d{2})(\.deduped)?\.jsonl\.zst$')
done = 0; t0 = time.time(); manifest = []
jobs = []
for block, pat in SRC:
    for d in sorted(glob.glob(pat)):
        for kind in ('trades','creates','migrations'):
            for f in sorted(glob.glob(f'{d}/{kind}/*.jsonl.zst')):
                assert not any(x in f for x in FORBIDDEN), f
                m = hour_re.search(os.path.basename(f));
                if not m: continue
                hour = m.group(2)
                if block == 'oracle-insample-0922' and hour >= '2026-09-25T07': continue
                if '2026-09-15T12' <= hour < '2026-09-18T23': raise SystemExit('EXP-009 hour in source: '+f)
                jobs.append((block, kind, hour, f))
print('files', len(jobs), flush=True)
for i,(block, kind, hour, f) in enumerate(jobs):
    od = f'{OUT}/{kind}'; os.makedirs(od, exist_ok=True)
    out = f'{od}/{hour}.parquet'
    if os.path.exists(out):
        manifest.append({'block':block,'kind':kind,'hour':hour,'src':f,'lenient':None}); continue
    cols = {'trades':TR_COLS,'creates':CR_COLS,'migrations':MG_COLS}[kind]
    lenient = False
    try:
        con.execute(f"COPY (SELECT *, '{block}' AS block, '{hour}' AS hour FROM read_json('{f}', format='newline_delimited', compression='zstd', columns={cols})) TO '{out}.tmp' (FORMAT parquet, COMPRESSION zstd)")
    except duckdb.InvalidInputException as e:
        lenient = True
        print('LENIENT', f, str(e)[:200], flush=True)
        con.execute(f"COPY (SELECT *, '{block}' AS block, '{hour}' AS hour FROM read_json('{f}', format='newline_delimited', compression='zstd', ignore_errors=true, columns={cols})) TO '{out}.tmp' (FORMAT parquet, COMPRESSION zstd)")
        with open(f'{OUT}/LENIENT.txt','a') as lf: lf.write(f + '\n')
    os.rename(out+'.tmp', out)
    manifest.append({'block':block,'kind':kind,'hour':hour,'src':f,'lenient':lenient})
    if i % 50 == 0:
        print(i, len(jobs), round(time.time()-t0), flush=True)
        p = os.environ.get('MISCUSI_PROGRESS')
        if p: open(p,'w').write(json.dumps({'pct': int(100*i/len(jobs)), 'note': f'{i}/{len(jobs)} files'}))
json.dump(manifest, open(f'{OUT}/manifest.json','w'))
n = con.execute(f"select count(*) from '{OUT}/trades/*.parquet'").fetchone()[0]
print('trades rows', n, 'secs', round(time.time()-t0))
open(f'{OUT}/READY','w').write(f'trades_rows={n}\n')
