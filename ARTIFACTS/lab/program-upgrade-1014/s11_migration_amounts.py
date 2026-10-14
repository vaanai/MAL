"""Step 11 (round 1, OFFLINE, no RPC): the pool-opening amounts the PUMP program sets at migration, read from cached migrate transactions,
against the scorer's seed constants.

The guard seed of EXP-022 section 4.3 is seed_p = (SEED_Q + V0) / SEED_B with SEED_Q = 67,405,853,863 and SEED_B = 206,900,000,000,000
(tools/cap_pick_score.py:135-136, :459). Those are the pool's opening real quote (lamports) and base (raw units); the pump program sets them
when it migrates a curve. Read per graduate:
  - the CompletePumpAmmMigrationEvent fields mint_amount (offset 72), sol_amount (80), pool_migration_fee (88) (IDL offsets, out/s00_offsets_pump.txt)
  - the pool's two vault balances after the migrate tx (meta.postTokenBalances, owner = the event's pool), which include anything else the same tx did
Two sets, both from cache files of getTransaction responses (public chain data fetched earlier; no file of a walk, forward run or runner is read):
  POST  the 60 graduates of step 5 (RAW/cache of this review), all after the pump deploy slot 456494698
  PRE   the migrate txs in the 2026-10-13 fees review's response cache (/data/mal/hunt-1008/h5-work/program-upgrade-1013/raw/cache), those with slot < 456494698
This script has no client and no network code. Aggregates (counts, min / median / max, the commonest exact values) only are saved.
"""
import json
import statistics
from collections import Counter
from pathlib import Path

from common import OUT, RAW, m

SEED_Q, SEED_B = 67_405_853_863, 206_900_000_000_000
DEPLOY_SLOT = 456494698
PRE_CACHE = Path("/data/mal/hunt-1008/h5-work/program-upgrade-1013/raw/cache")
s5 = json.load(open(OUT / "s05_graduations.json"))


def load_migs(cache_dir, keep):
    migs, n_files = {}, 0
    for p in cache_dir.glob("*.json"):
        d = json.loads(p.read_text())
        if d["method"] != "getTransaction" or not d["r"]:
            continue
        n_files += 1
        g = m.parse_migrate_tx(d["r"])
        if g is None or g["block_time"] is None or not keep(g):
            continue
        migs.setdefault(g["mint"], (g, d["r"]))
    return sorted(migs.values(), key=lambda x: (x[0]["block_time"], x[0]["slot"]), reverse=True), n_files


def u64(b, o):
    return int.from_bytes(b[o : o + 8], "little")


def dist(xs):
    xs = [x for x in xs if x is not None]
    return dict(n=len(xs), min=min(xs), median=statistics.median(xs), max=max(xs)) if xs else dict(n=0)


def analyse(migs):
    rows = []
    for g, tx in migs:
        blob = next(b for b in m.tx_event_blobs(tx) if b[:8] == m.DISC_MIGRATE and m.b58encode(b[40:72]) == g["mint"])
        pool, quote = m.b58encode(blob[136:168]), m.b58encode(blob[168:200])
        post = {b["mint"]: int(b["uiTokenAmount"]["amount"]) for b in tx["meta"]["postTokenBalances"] if b.get("owner") == pool}
        qm = m.WSOL_MINT if quote == m.NATIVE_SOL else quote
        rows.append(dict(qclass="wsol" if qm == m.WSOL_MINT else "other", mint_amount=u64(blob, 72), sol_amount=u64(blob, 80), fee=u64(blob, 88), base_post=post.get(g["mint"]), quote_post=post.get(qm),
                         n_pool_vaults_pre=sum(1 for b in tx["meta"]["preTokenBalances"] if b.get("owner") == pool), migrate_ix=g["migrate_ix"]))
    out = {}
    for cls in ("wsol", "other"):
        rs = [r for r in rows if r["qclass"] == cls]
        if not rs:
            out[cls] = dict(n=0)
            continue
        out[cls] = dict(
            n=len(rs),
            mint_amount=dict(dist([r["mint_amount"] for r in rs]), equal_SEED_B=sum(r["mint_amount"] == SEED_B for r in rs), top_values=Counter(r["mint_amount"] for r in rs).most_common(3)),
            sol_amount=dict(dist([r["sol_amount"] for r in rs]), equal_SEED_Q=sum(r["sol_amount"] == SEED_Q for r in rs), top_values=Counter(r["sol_amount"] for r in rs).most_common(3)),
            pool_migration_fee=dict(dist([r["fee"] for r in rs]), top_values=Counter(r["fee"] for r in rs).most_common(3)),
            sol_amount_minus_fee=dict(dist([r["sol_amount"] - r["fee"] for r in rs]), equal_SEED_Q=sum(r["sol_amount"] - r["fee"] == SEED_Q for r in rs)),
            pool_base_vault_post=dict(dist([r["base_post"] for r in rs]), equal_SEED_B=sum(r["base_post"] == SEED_B for r in rs), n_missing=sum(r["base_post"] is None for r in rs), top_values=Counter(r["base_post"] for r in rs).most_common(3)),
            pool_quote_vault_post=dict(dist([r["quote_post"] for r in rs]), equal_SEED_Q=sum(r["quote_post"] == SEED_Q for r in rs), n_missing=sum(r["quote_post"] is None for r in rs), top_values=Counter(r["quote_post"] for r in rs).most_common(3),
                                       mode_minus_SEED_Q=(Counter(r["quote_post"] for r in rs).most_common(1)[0][0] - SEED_Q) if any(r["quote_post"] for r in rs) else None),
            pool_vault_entries_pre_in_tx=dict(Counter(r["n_pool_vaults_pre"] for r in rs)),
            by_migrate_ix=dict(Counter(r["migrate_ix"] for r in rs)),
        )
    return out


post_migs, n_files = load_migs(RAW / "cache", lambda g: g["block_time"] <= s5["block_time_max"])
post = post_migs[:60]
slots = [g["slot"] for g, _ in post]
res = dict(seed_constants=dict(SEED_Q=SEED_Q, SEED_B=SEED_B), deploy_slot=DEPLOY_SLOT,
           post=dict(n=len(post), n_cached_getTransaction_files_read=n_files, is_step5_sample=(len(post) == 60 and min(slots) == s5["slot_min"] and max(slots) == s5["slot_max"]),
                     slot_min=min(slots), slot_max=max(slots), all_after_deploy_slot=min(slots) > DEPLOY_SLOT, by_class=analyse(post)))
if PRE_CACHE.exists():
    pre_migs, n_pre_files = load_migs(PRE_CACHE, lambda g: g["slot"] < DEPLOY_SLOT)
    ps = [g["slot"] for g, _ in pre_migs]
    res["pre"] = dict(n=len(pre_migs), n_cached_getTransaction_files_read=n_pre_files, slot_min=min(ps, default=None), slot_max=max(ps, default=None), all_before_deploy_slot=all(s < DEPLOY_SLOT for s in ps), by_class=analyse(pre_migs) if pre_migs else {})
print(json.dumps(res, indent=1, default=str)[:6000])
(OUT / "s11_migration_amounts.json").write_text(json.dumps(res, indent=1, sort_keys=True, default=str) + "\n")
