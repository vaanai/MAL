"""P33 post-hoc decode diagnostic: migration_option of the configs of (a) m10 decision pools, (b) completions with no DAMM v2 migration; MigrateMeteoraDamm ix counts. Exploration hours only."""
import json, base64, sys, collections
from datetime import datetime, timezone
sys.path.insert(0, '/data2/mal/wt/claude-p33-dbc-p18')
from tools import dbc_config as C, dbc_price as L
O = '/data/mal/hunt-1008/push-1016/p33/measure-1'
raw = json.load(open('/data/mal/hunt-1008/push-1016/hunt28/p17/cfg_raw_all.json'))
def mo(cfg):
    v = raw.get(cfg)
    if not v: return 'unmapped'
    a = C.decode_account(base64.b64decode(v['data_b64']))
    return a['config'].get('migration_option') if a['kind'] == 'PoolConfig' else a['kind']
pool_cfg, ixc = {}, collections.Counter()
for h in L.exploration_hours(datetime(2026,10,14,tzinfo=timezone.utc), datetime(2026,10,17,tzinfo=timezone.utc)):
    for line in L._zstd_lines(L.hour_file(h), None):
        if b'"ix"' in line and b'"type":"ix"' in line.replace(b' ', b''):
            r = json.loads(line)
            for n in (r.get('ix') or []): ixc[n] += 1
        elif b'"pool"' in line and (b'"type":"trade"' in line or b'"type":"curve_complete"' in line or b'"type":"create"' in line):
            r = json.loads(line)
            if r.get('config'): pool_cfg.setdefault(r['pool'], r['config'])
per = [json.loads(l) for l in open(f'{O}/damm_per_pool.jsonl')]
recs = [json.loads(l) for l in open(f'{O}/decisions_measured.jsonl')]
out = {'ix_counts': dict(ixc)}
out['completions_by_status_and_migopt'] = dict(collections.Counter(f"{e['status']}|{mo(pool_cfg.get(e['dbc_pool'],''))}" for e in per))
for era in ('swap2', 'swap3', None):
    R = [r for r in recs if era is None or r['era'] == era]
    out[f'm10_decisions_{era or "all"}_by_migopt'] = dict(collections.Counter(str(mo(pool_cfg.get(r['pool'], ''))) for r in R))
    out[f'm10_open_or_cbe_{era or "all"}_by_migopt'] = dict(collections.Counter(f"{r['cls']}|{mo(pool_cfg.get(r['pool'], ''))}" for r in R if r['cls'] != 'not_open_at_completion'))
    out[f'm10_upper_bound_{era or "all"}_by_migopt'] = dict(collections.Counter(str(mo(pool_cfg.get(r['pool'], ''))) for r in R if r['upper_bound_open']))
print(json.dumps(out, indent=1))
