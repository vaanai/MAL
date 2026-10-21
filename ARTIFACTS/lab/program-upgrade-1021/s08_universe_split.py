"""C3' (c) and (d) of QP-RULING-1021-repin, from the cached samples (offline) plus one public-RPC read of mint accounts (0 credits).
(c) The 8-byte trailing field by H5-universe class: PumpSwap events by the quote mint(s) of the tx's pump_amm trade instructions (WSOL-only / non-WSOL-only /
    mixed / no trade instruction), pump TradeEvents by the event's own quote_mint. Non-zero counts by quote mint. Also: log-truncation rates and CPI-only
    PumpSwap events on WSOL pools, with the pool of the 10-21 episode split out.
(d) WSOL mint owner program; base mints of every CreatePool and CompletePumpAmmMigrationEvent in the post sample: owner program and Token-2022 TransferFeeConfig.
Structure only: no price, no outcome."""
import collections, gzip, json, os, sys
from pathlib import Path
sys.argv = [sys.argv[0]]
os.environ.setdefault("UPGRADE_RAW", "/data2/mal/scratch/a3-repin-1021/raw")
exec(open("s06_parity.py").read().split("acc = dict(")[0])
from common import LClient, OUT
WSOL = td.WSOL_MINT
ZERO = "11111111111111111111111111111111"
ROOTS = {"post": Path("/data2/mal/scratch/a3-repin-1021/raw"), "pre": Path("/data2/mal/scratch/a3-repin-1021/raw_pre")}
EPISODE_POOL = "34tiphuqXUgkH9c2KCrFqCiyuhz58HEbhQBk61ub46bU"

def txs_of(root, label):
    idx = json.load(open(root / "sample_index.json"))
    for p in sorted((root / "blocks").glob("*.json.gz")):
        blk = json.loads(gzip.open(p).read())["r"]
        if not blk: continue
        for tx in blk["transactions"]:
            if tx["transaction"]["signatures"][0] in idx["a"]:
                yield "A", tx
    if label == "post":
        seen = set(idx["a"])
        for sig in idx["b"]:
            p = root / "txs" / f"{sig}.json.gz"
            if p.exists() and sig not in seen:
                r_ = json.loads(gzip.open(p).read())["r"]
                if r_: seen.add(sig); yield "B", r_
        for c in idx["c"]:
            for sig in c["picked"]:
                p = root / "txs" / f"{sig}.json.gz"
                if p.exists() and sig not in seen:
                    r_ = json.loads(gzip.open(p).read())["r"]
                    if r_: seen.add(sig); yield "C", r_

def events_of(tx):
    meta = tx.get("meta") or {}
    logs = meta.get("logMessages") or []
    evs = [(pid, blob, "log") for pid, blob in log_events(logs)]
    ms = collections.Counter(b for _, b, _ in evs)
    keys = keys_of(tx)
    for g in meta.get("innerInstructions") or []:
        for ins in g["instructions"]:
            pid = keys[ins["programIdIndex"]] if ins["programIdIndex"] < len(keys) else None
            if pid in PNAME and ins.get("data"):
                d = b58d(ins["data"])
                if d[:8] == TAG and len(d) > 8:
                    if ms.get(d[8:], 0) > 0: ms[d[8:]] -= 1
                    else: evs.append((pid, d[8:], "cpi_only"))
    return evs, any(l.startswith("Log truncated") for l in logs)

res = {}
base_mints = {}   # mint -> set of (kind, quote class)
for label, root in ROOTS.items():
    cls = collections.defaultdict(lambda: collections.Counter())        # class -> {zero, nonzero}
    nz_by_mint = collections.Counter(); ev_by_mint = collections.Counter()
    pump_q = collections.Counter(); pump_q_nz = collections.Counter()
    tx_cls = collections.Counter(); tx_cls_trunc = collections.Counter()
    cpi_wsol_sell = collections.Counter(); wsol_sell = collections.Counter(); wsol_buy = collections.Counter(); cpi_wsol_buy = collections.Counter()
    tx_trunc_noepi = collections.Counter(); tx_noepi = collections.Counter(); n_tx = 0
    for src, tx in txs_of(root, label):
        meta = tx.get("meta") or {}
        if meta.get("err") is not None: continue
        n_tx += 1
        evs, trunc = events_of(tx)
        qm = quote_mints(tx)
        klass = "no_amm_trade_ix" if not qm else ("wsol_only" if qm == {WSOL} else ("non_wsol_only" if WSOL not in qm else "mixed"))
        has_amm_trade = False; in_episode = False
        for pid, blob, s in evs:
            if pid not in PNAME or len(blob) < 8: continue
            nm = EV_BY_PROG[pid].get(blob[:8]); idl = PNAME[pid]
            if nm is None: continue
            try: ev, end = parse_event(idl, nm, blob)
            except Short: continue
            tr = int.from_bytes(blob[end:], "little") if len(blob) > end else None
            if idl == "pump_amm" and nm in ("BuyEvent", "SellEvent"):
                has_amm_trade = True
                cls[klass]["nonzero" if tr else "zero"] += 1
                for m_ in (qm or {"(none)"}):
                    ev_by_mint[m_] += 1
                    if tr: nz_by_mint[m_] += 1
                if klass == "wsol_only":
                    (cpi_wsol_sell if nm == "SellEvent" else cpi_wsol_buy)[s == "cpi_only"] += 0
                    tgt = wsol_sell if nm == "SellEvent" else wsol_buy
                    tgt["total"] += 1
                    if s == "cpi_only": tgt["cpi_only"] += 1
                    if ev["pool"] == EPISODE_POOL:
                        tgt["episode_pool_total"] += 1
                        if s == "cpi_only": tgt["episode_pool_cpi_only"] += 1
                        in_episode = True
            elif idl == "pump" and nm == "TradeEvent":
                q = ev["quote_mint"]
                pump_q[q] += 1
                if tr: pump_q_nz[q] += 1
                cls["pump_trade_quote_wsol_or_zero" if q in (WSOL, ZERO) else "pump_trade_quote_other"]["nonzero" if tr else "zero"] += 1
            elif label == "post" and idl == "pump_amm" and nm == "CreatePoolEvent":
                base_mints.setdefault(ev["base_mint"], set()).add(("CreatePool", ev["quote_mint"]))
            elif label == "post" and idl == "pump" and nm == "CompletePumpAmmMigrationEvent":
                qq = ev.get("quote_mint") or ev.get("pump_amm_quote_mint") or ZERO
                base_mints.setdefault(ev["mint"], set()).add(("Migration", WSOL if qq == ZERO else qq))
        if has_amm_trade:
            tx_cls[klass] += 1
            if trunc: tx_cls_trunc[klass] += 1
            if klass == "wsol_only":
                tx_noepi["x"] += 0 if in_episode else 1
                if trunc and not in_episode: tx_trunc_noepi["x"] += 1
    res[label] = dict(n_tx=n_tx, trailing_by_class={k: dict(v) for k, v in cls.items()},
                      pumpswap_events_by_quote_mint=dict(ev_by_mint), pumpswap_nonzero_by_quote_mint=dict(nz_by_mint),
                      pump_trade_events_by_event_quote_mint=dict(pump_q), pump_trade_nonzero_by_event_quote_mint=dict(pump_q_nz),
                      amm_txs_by_class=dict(tx_cls), amm_txs_truncated_by_class=dict(tx_cls_trunc),
                      wsol_only_SellEvents=dict(wsol_sell), wsol_only_BuyEvents=dict(wsol_buy),
                      wsol_only_txs_excluding_episode_pool=tx_noepi["x"], wsol_only_txs_truncated_excluding_episode_pool=tx_trunc_noepi["x"])

# (d) mint accounts
import base64, struct
c = LClient(max_calls=40, min_interval=0.4)
def owner_and_ext(addrs):
    out = {}
    for i in range(0, len(addrs), 90):
        chunk = addrs[i:i + 90]
        r = c.call("getMultipleAccounts", [chunk, {"encoding": "base64", "commitment": "finalized"}])
        for a, v in zip(chunk, r["value"]):
            if v is None: out[a] = {"closed": True}; continue
            d = base64.b64decode(v["data"][0]); o = v["owner"]
            e = {"owner": o, "len": len(d), "transfer_fee_config": None}
            if o == "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb" and len(d) > 166:
                off = 166; exts = []
                while off + 4 <= len(d):
                    t, ln = struct.unpack_from("<HH", d, off); off += 4
                    exts.append(t)
                    if t == 1 and off + 108 <= len(d):
                        ob, nb = struct.unpack_from("<H", d, off + 88)[0], struct.unpack_from("<H", d, off + 106)[0]
                        om, nm_ = struct.unpack_from("<Q", d, off + 80)[0], struct.unpack_from("<Q", d, off + 98)[0]
                        e["transfer_fee_config"] = {"older_bps": ob, "older_max": om, "newer_bps": nb, "newer_max": nm_, "nonzero": bool(ob or nb or om or nm_)}
                    off += ln
                e["extensions"] = exts
            out[a] = e
    return out
wsol_info = owner_and_ext([WSOL])[WSOL]
mints = sorted(base_mints)
info = owner_and_ext(mints)
rows = []
for mnt in mints:
    for kind, q in sorted(base_mints[mnt]):
        rows.append(dict(base_mint=mnt, kind=kind, quote_mint=q, **info[mnt]))
sumry = collections.Counter()
for r in rows:
    w = "wsol" if r["quote_mint"] == WSOL else "non_wsol"
    sumry[(w, r["kind"], "closed" if r.get("closed") else r["owner"])] += 1
    if r.get("transfer_fee_config"):
        sumry[(w, r["kind"], "token2022_transferfee_nonzero" if r["transfer_fee_config"]["nonzero"] else "token2022_transferfee_zero")] += 1
nzm = sorted(set(res["post"]["pumpswap_nonzero_by_quote_mint"]) | set(res["post"]["pump_trade_nonzero_by_event_quote_mint"]) - {WSOL, ZERO, "(none)"})
nzm = [m_ for m_ in nzm if m_ not in (WSOL, ZERO, "(none)")]
res["nonzero_field_quote_mints_account_info"] = owner_and_ext(nzm)   # hypothesis test only: do the quote mints that carry non-zero values have a TransferFeeConfig?
res["wsol_mint"] = wsol_info
res["base_mints"] = {"n_unique_base_mints": len(mints), "n_rows": len(rows), "summary": {"|".join(k): v for k, v in sorted(sumry.items())}, "rows": rows, "rpc_attempts": c.total_calls}
(OUT / "s08_universe_split.json").write_text(json.dumps(res, indent=1, sort_keys=True, default=str) + "\n")
print(json.dumps({k: v for k, v in res.items() if k != "base_mints"}, indent=1)[:6000]); print(json.dumps(res["base_mints"]["summary"], indent=1)); print(res["base_mints"]["n_unique_base_mints"], res["base_mints"]["n_rows"])
