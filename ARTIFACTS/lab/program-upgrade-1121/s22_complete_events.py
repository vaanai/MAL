"""CompleteEvent supplementary sample (QP-RULING-1115-a3-repin-dual-ADD1 C), rule declared in the review (section 3-supp) BEFORE this ran. Chain-only, public RPC, 0 credits.
For each WSOL migration of M (post) or M_pre (pre control), in slot order (post: oldest first; pre: newest first): page getSignaturesForAddress(<mint's bonding-curve PDA
["bonding-curve", mint] under the pump program>) back from the migration signature; fetch whole (getTransaction, logs and inner instructions) the successful txs newest first until
one emits pump CompleteEvent (at most 40 fetches per mint); keep post when slot >= D_pump+150 = 471854604, pre when slot <= E-150 = 471854259. Stop at K kept (post 8, pre 5).
Bars: 0 field inequalities frozen (bf.decode_complete_event) vs independent IDL walker, blob 144 bytes and trailing 0, 0 unknown discriminators in the txs.
Usage: SAMPLE=post|pre /data/mal/venv/bin/python s22_complete_events.py   (writes out/s22_complete_events_<SAMPLE>.json). Structure only."""
import collections, gzip, json, os, sys
sys.argv = [sys.argv[0]]
exec(open("s06_parity.py").read().split("acc = dict(")[0])
from common import LClient, OUT, RAW
from solders.pubkey import Pubkey
MODE = os.environ.get("SAMPLE", "post"); K = 8 if MODE == "post" else 5
D_PUMP = 471854454; D_FEES = 471854409   # post keep: slot >= D_PUMP+150; pre keep: slot <= E-150, E = D_FEES (earliest write)
MIG = json.load(open(os.environ.get("A3_BASE", "/data2/mal/scratch/a3-repin-1121") + "/raw/migrate_index.json"))
rows = [r for r in MIG["post" if MODE == "post" else "pre"] if r["quote_wsol"]]
rows = sorted(rows, key=lambda r: r["slot"] if MODE == "post" else -r["slot"])
c = LClient(max_calls=900, min_interval=0.5)
(RAW / "txs").mkdir(parents=True, exist_ok=True)
def fetch(sig):
    p = RAW / "txs" / f"{sig}.json.gz"
    if p.exists(): return json.loads(gzip.open(p).read())["r"]
    r = c.call("getTransaction", [sig, {"encoding": "json", "commitment": "finalized", "maxSupportedTransactionVersion": 1}])
    with gzip.open(p, "wt") as fh: json.dump({"r": r}, fh)
    return r
def complete_blobs(tx):
    return [b for pid, b in log_events((tx.get("meta") or {}).get("logMessages") or []) if PNAME.get(pid) == "pump" and len(b) >= 8 and EV_BY_PROG[pid].get(b[:8]) == "CompleteEvent"]
kept, tried = [], []
for r in rows:
    if len(kept) >= K: break
    bc = str(Pubkey.find_program_address([b"bonding-curve", bytes(Pubkey.from_string(r["mint"]))], Pubkey.from_string(PID["pump"]))[0])
    sigs = c.call("getSignaturesForAddress", [bc, {"limit": 100, "commitment": "finalized", "before": r["sig"]}]) or []
    found = None; nf = 0
    for s in sigs:
        if s.get("err") is not None or nf >= 40: continue
        tx = fetch(s["signature"]); nf += 1
        if tx and complete_blobs(tx): found = (s, tx); break
    t = {"migration_slot": r["slot"], "bc_sigs_listed": len(sigs), "fetched": nf, "found": bool(found)}
    if found:
        s, tx = found; t["slot"] = s["slot"]; t["sig"] = s["signature"]
        lo_ok = s["slot"] >= D_PUMP + 150 if MODE == "post" else s["slot"] <= D_FEES - 150
        t["slot_ok"] = lo_ok
        if lo_ok:
            blobs = complete_blobs(tx); bad = []; lens = []; trail = []; unk = 0
            for b in blobs:
                ev, end = parse_event("pump", "CompleteEvent", b); lens.append(len(b)); trail.append(len(b) - end)
                fro = bf.decode_complete_event(b)
                if fro is None: bad.append("frozen_none")
                else:
                    um = nt_unmatched(fro, ev)
                    if um: bad.append(um)
            for pid, b in log_events(tx["meta"].get("logMessages") or []):
                if pid in PNAME and len(b) >= 8 and EV_BY_PROG[pid].get(b[:8]) is None: unk += 1
            for pid, d, w in instructions(tx):
                if (pid, d[:8]) not in INS and not d[:8] == TAG: unk += 1
            t.update(blob_len=lens, trailing=trail, unmatched=bad, unknown_disc=unk)
            kept.append(t)
    tried.append(t)
res = {"mode": MODE, "K": K, "n_kept": len(kept), "n_candidates_tried": len(tried), "kept": kept, "tried": tried,
       "bars": {"field_inequalities": sum(1 for k in kept if k["unmatched"]), "blob_len_not_144": sum(1 for k in kept for l in k["blob_len"] if l != 144), "trailing_nonzero": sum(1 for k in kept for l in k["trailing"] if l != 0), "unknown_disc": sum(k["unknown_disc"] for k in kept)},
       "rpc_attempts": c.total_calls}
(OUT / f"s22_complete_events_{MODE}.json").write_text(json.dumps(res, indent=1))
print(json.dumps({k: v for k, v in res.items() if k not in ("kept", "tried")}), [ (k["slot"], k["sig"][:8]) for k in kept])
