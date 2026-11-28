"""Item (c1) of QP-RULING-1115: the shape of PumpSwap buy / buy_exact_quote_in / sell instructions that landed (top-level or inner), post (A1 + M) vs pre (P + M_pre),
WSOL-quoted (account 4 == WSOL): 8-byte discriminator, data length, account count; at the fixed and PDA positions the address the executor derives (tools/pumpswap_tx.py
at e85447f8, imported unchanged from a scratch copy: pda/ata/event_authority/global_volume_accumulator/user_volume_accumulator/creator_vault_authority/pool_v2/fee_config);
failed txs carrying these discriminators by error word; and the owner program of each account position (getMultipleAccounts, NOW, for up to 200 instructions per side per group).
Nothing is built, signed, simulated or sent. Run with /data/mal/venv/bin/python (solders)."""
import collections, gzip, importlib.util, json, os, sys
from pathlib import Path
sys.argv = [sys.argv[0]]
exec(open("s06_parity.py").read().split("acc = dict(")[0])
from common import LClient, OUT
spec = importlib.util.spec_from_file_location("pst", "/data2/mal/scratch/a3-repin-1128/pumpswap_tx_e85447f8.py"); pst = importlib.util.module_from_spec(spec); sys.modules["pst"] = pst; spec.loader.exec_module(pst)
from solders.pubkey import Pubkey
P = Pubkey.from_string
BASE = Path(os.environ.get("A3_BASE", "/data2/mal/scratch/a3-repin-1128"))
MIG = json.load(open(BASE / "raw" / "migrate_index.json"))
AMM = PID["pump_amm"]; WSOL = td.WSOL_MINT
DISC = {"c62e1552b4d9e870": "buy_exact_quote_in", "66063d1201daebea": "buy", "33e685a4017f83ad": "sell"}
SHAPE = {"buy_exact_quote_in": (25, 26), "buy": (25, 26), "sell": (24, 24)}   # executor: buy_exact_quote_in 25 bytes/26 accts, sell 24/24; `buy` listed for reference only
def D(x): return str(x)
FIXED = {2: str(pst.GLOBAL_CONFIG), 4: str(pst.WSOL_MINT), 13: str(pst.SYSTEM_PROGRAM), 14: str(pst.ATA_PROGRAM), 15: str(pst.event_authority()), 16: str(pst.PUMPSWAP_PROGRAM)}
FIXED_BUY = {19: str(pst.global_volume_accumulator()), 21: str(pst.fee_config()), 22: str(pst.FEE_PROGRAM)}
FIXED_SELL = {19: str(pst.fee_config()), 20: str(pst.FEE_PROGRAM)}

def txs(root, label):
    idx = json.load(open(root / "sample_index.json"))["a"]
    for p in sorted((root / "blocks").glob("*.json.gz")):
        blk = json.loads(gzip.open(p).read())["r"]
        for tx in (blk or {}).get("transactions", []):
            if tx["transaction"]["signatures"][0] in idx: yield tx
    seen = set(idx)
    for row in MIG["post" if label == "post" else "pre"]:
        if row["sig"] in seen: continue
        pth = BASE / "raw" / "txs" / f"{row['sig']}.json.gz"
        if pth.exists():
            r_ = json.loads(gzip.open(pth).read())["r"]
            if r_: seen.add(row["sig"]); yield r_

def amm_ixs(tx):
    keys = keys_of(tx); msg = tx["transaction"]["message"]; out = []
    allix = [("top", i) for i in msg["instructions"]] + [("inner", i) for g in (tx["meta"].get("innerInstructions") or []) for i in g["instructions"]]
    for where, ins in allix:
        if ins["programIdIndex"] < len(keys) and keys[ins["programIdIndex"]] == AMM:
            d = b58d(ins["data"])
            if d[:8].hex() in DISC: out.append((where, d, [keys[a] for a in ins["accounts"]]))
    return out

def errword(err):
    if err is None: return None
    if isinstance(err, dict):
        k = next(iter(err)); v = err[k]
        if k == "InstructionError" and isinstance(v, list) and len(v) == 2:
            e = v[1]; return "InstructionError:" + (e if isinstance(e, str) else next(iter(e)) + ("=" + str(e[next(iter(e))]) if not isinstance(e[next(iter(e))], (dict, list)) else ""))
        return k
    return str(err)

res = {}; own_inputs = {}
for label, root in (("post", BASE / "raw"), ("pre", BASE / "raw_pre")):
    shape = collections.Counter(); failed = collections.Counter(); chk = collections.defaultdict(collections.Counter); samples = {"buy_exact_quote_in": [], "sell": [], "buy": []}
    for tx in txs(root, label):
        meta = tx.get("meta") or {}; ok = meta.get("err") is None
        ixs = amm_ixs(tx)
        if not ixs: continue
        # event for coin_creator by pool
        cc = {}
        for pid, blob in log_events(meta.get("logMessages") or []):
            if PNAME.get(pid) == "pump_amm" and len(blob) >= 8 and EV_BY_PROG[pid].get(blob[:8]) in ("BuyEvent", "SellEvent"):
                try: ev, _ = parse_event("pump_amm", EV_BY_PROG[pid][blob[:8]], blob); cc[ev["pool"]] = ev["coin_creator"]
                except Short: pass
        for where, d, acc in ixs:
            nm = DISC[d[:8].hex()]
            if len(acc) < 5 or acc[4] != WSOL: continue
            if not ok: failed[f"{nm}|{errword(meta['err'])}"] += 1; continue
            shape[f"{nm}|{d[:8].hex()}|len={len(d)}|accts={len(acc)}"] += 1
            sell = nm == "sell"; fx = dict(FIXED); fx.update(FIXED_SELL if sell else FIXED_BUY)
            for pos, a in fx.items():
                if pos < len(acc): chk[nm][f"pos{pos}"] += 1 if acc[pos] == a else 0
            chk[nm]["n"] += 1
            try:
                pdas = {"pool_v2": (acc[21] if sell else acc[23], str(pst.pool_v2(P(acc[3]))))}
                if not sell: pdas["user_volume_accumulator"] = (acc[20], str(pst.user_volume_accumulator(P(acc[1]))))
                c_ = cc.get(acc[0])
                if c_: pdas["creator_vault_authority"] = (acc[18], str(pst.creator_vault_authority(P(c_)))); chk[nm]["have_creator"] += 1
                tp12 = P(acc[12]); tp11 = P(acc[11])
                pdas["user_base_ata"] = (acc[5], str(pst.ata(P(acc[1]), P(acc[3]), tp11))); pdas["user_quote_ata"] = (acc[6], str(pst.ata(P(acc[1]), pst.WSOL_MINT, tp12)))
                pdas["pool_base_vault"] = (acc[7], str(pst.ata(P(acc[0]), P(acc[3]), tp11))); pdas["pool_quote_vault"] = (acc[8], str(pst.ata(P(acc[0]), pst.WSOL_MINT, tp12)))
                pdas["fee_recipient_ata"] = (acc[10], str(pst.ata(P(acc[9]), pst.WSOL_MINT, tp12)))
                rec = acc[22] if sell else acc[24]; rec_ata = acc[23] if sell else acc[25]
                pdas["buyback_recipient_ata"] = (rec_ata, str(pst.ata(P(rec), pst.WSOL_MINT, tp12)))
                if "creator_vault_authority" in pdas: pdas["creator_vault_ata"] = (acc[17], str(pst.ata(P(acc[18]), pst.WSOL_MINT, tp12)))
                for k, (a, b) in pdas.items(): chk[nm][f"pda_{k}_eq"] += int(a == b); chk[nm][f"pda_{k}_n"] += 1
            except Exception as e:
                chk[nm]["derive_error"] += 1
            if len(samples[nm]) < 5000: samples[nm].append(acc)
    res[label] = {"shape_counts": dict(shape), "failed_by_error_word": dict(failed), "position_checks": {k: dict(v) for k, v in chk.items()}}
    own_inputs[label] = samples
# owner programs, up to 200 instructions per side (buy_exact_quote_in + buy as 'buy side'), evenly spread
c = LClient(max_calls=400, min_interval=0.5)
def pick(lst, n=200):
    if len(lst) <= n: return lst
    return [lst[i * len(lst) // n] for i in range(n)]
own = {}
for label, samples in own_inputs.items():
    for side, names in (("buy", ("buy_exact_quote_in", "buy")), ("sell", ("sell",))):
        lst = [a for nm in names for a in samples[nm]]
        lst = [a for a in lst if len(a) == (24 if side == "sell" else 26)]
        sel = pick(lst); addrs = sorted({a for acc in sel for a in acc})
        owner = {}
        for i in range(0, len(addrs), 100):
            r = c.call("getMultipleAccounts", [addrs[i:i + 100], {"encoding": "base64", "dataSlice": {"offset": 0, "length": 0}, "commitment": "finalized"}])
            for a, v in zip(addrs[i:i + 100], r["value"]): owner[a] = v["owner"] if v else "(closed or absent now)"
        pos = collections.defaultdict(collections.Counter)
        for acc in sel:
            for i, a in enumerate(acc): pos[i][owner[a]] += 1
        own[f"{label}|{side}"] = {"n_instructions": len(sel), "n_available": len(lst), "owner_by_position": {str(i): dict(v) for i, v in sorted(pos.items())}}
res["owner_programs_now"] = own
res["executor_expectation"] = {"buy_exact_quote_in": {"disc": "c62e1552b4d9e870", "data_len": 25, "accounts": 26}, "sell": {"disc": "33e685a4017f83ad", "data_len": 24, "accounts": 24}}
res["rpc_attempts"] = c.total_calls
Path(os.environ.get("UPGRADE_OUT", "out") + "/s18_instruction_shapes.json").write_text(json.dumps(res, indent=1, sort_keys=True))
for l in ("post", "pre"): print(l, json.dumps(res[l]["shape_counts"]), json.dumps(res[l]["failed_by_error_word"]))
