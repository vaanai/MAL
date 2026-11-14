"""MIGRATE sample (QP ruling 384d99f3 Q1 items 3 and 4), declared in the review file section 3b before this ran. CHAIN-ONLY, public RPC, 0 credits.
Candidates: getSignaturesForAddress(mon.MIGRATION_FEE_ACCOUNT) paged back from now (the MIGRATE_K rule of tools/a3_redeploy_check.py migrate_sample).
  post (M):    the first 40 SETTLED (blockTime <= now - 480 s) successful candidates with slot >= 469055660+150, oldest first, each fetched whole
               (getTransaction json v1: logs + inner instructions) and kept when mon.parse_migrate_tx reads a CompletePumpAmmMigrationEvent.
  pre  (M_pre): the LAST 40 successful candidates with slot <= 469055660-150 (newest of them first, walking backwards), same keep rule.
Writes RAW/txs/<sig>.json.gz (shared cache layout) and RAW/migrate_index.json. Structure only."""
import gzip, json, time
from common import RAW, OUT, LClient, m
DEPLOY = 469055660
K = 40
(RAW / "txs").mkdir(parents=True, exist_ok=True)
c = LClient(max_calls=3000, min_interval=0.5)
now = time.time(); cutoff = int(now) - m.SETTLE_S_DEFAULT
cands, before, pages = [], None, 0
while pages < 40:
    opts = {"limit": 1000, "commitment": "finalized"}
    if before: opts["before"] = before
    sigs = c.call("getSignaturesForAddress", [m.MIGRATION_FEE_ACCOUNT, opts]) or []
    pages += 1
    for s in sigs:
        before = s["signature"]
        if s.get("err") is None and s.get("blockTime") is not None:
            cands.append(s)
    # enough pre candidates once at least 400 signatures are older than the pre cutoff (fee-account sigs include non-migrate txs)
    if len(sigs) < 1000 or sum(1 for s in cands if s["slot"] <= DEPLOY - 150) >= 400:
        break
post = sorted([s for s in cands if s["slot"] >= DEPLOY + 150 and s["blockTime"] <= cutoff], key=lambda s: s["slot"])
pre = sorted([s for s in cands if s["slot"] <= DEPLOY - 150], key=lambda s: -s["slot"])


def fetch(sig):
    p = RAW / "txs" / f"{sig}.json.gz"
    if p.exists():
        return json.loads(gzip.open(p).read())["r"]
    r = c.call("getTransaction", [sig, {"encoding": "json", "commitment": "finalized", "maxSupportedTransactionVersion": 1}])
    with gzip.open(p, "wt") as fh: json.dump({"r": r}, fh)
    return r


def take(lst):
    out, skipped = [], {"unfetchable": 0, "not_a_migrate_tx": 0}
    for s in lst:
        tx = fetch(s["signature"])
        if tx is None: skipped["unfetchable"] += 1; continue
        g = m.parse_migrate_tx(tx)
        if g is None: skipped["not_a_migrate_tx"] += 1; continue
        out.append({"sig": s["signature"], "slot": s["slot"], "blockTime": s["blockTime"], "pool": g["pool"], "mint": g["mint"], "quote_wsol": g["quote_wsol"], "quote_mint": g["quote_mint"]})
        if len(out) >= K: break
    return out, skipped


M, sk_post = take(post)
P, sk_pre = take(pre)
idx = {"post": M, "pre": P, "skipped_post": sk_post, "skipped_pre": sk_pre, "n_cands": len(cands), "pages": pages, "cutoff_bt": cutoff}
(RAW / "migrate_index.json").write_text(json.dumps(idx))
(OUT / "s10_migrate_sample.json").write_text(json.dumps({**idx, "rpc_attempts": c.total_calls}, indent=1))
print("post", len(M), "wsol", sum(1 for x in M if x["quote_wsol"]), "pre", len(P), "wsol", sum(1 for x in P if x["quote_wsol"]), sk_post, sk_pre, "rpc", c.total_calls)
