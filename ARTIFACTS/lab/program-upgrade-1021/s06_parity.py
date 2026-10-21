"""Decode parity of the frozen H5 decoder (observe/trade_decode.py + tools/pump_history_backfill.py, blobs identical at 1f7a89c) against an
independent IDL-driven borsh decode, on the pre-declared sample fetched by s05 (rule: review doc section 3). Offline: reads the gz cache only.
Structure only: counts, discriminators, lengths, equality of emitted fields. Nothing is priced and no outcome is computed.
Non-decode categories N1..N6 are defined in the review doc."""
import os, base64, collections, gzip, hashlib, json, re, struct, sys
from pathlib import Path
from common import REPO, RAW, OUT
from observe import trade_decode as td
from tools import pump_history_backfill as bf

DOCS = Path("/data2/mal/scratch/a3-repin-1021/docs")
PID = {"pump": "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P", "pump_amm": "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA", "pump_fees": "pfeeUxB6jkeY1Hxd7CsFCAjcbHA9rWtchMGdZ6VojVZ"}
PNAME = {v: k for k, v in PID.items()}
IDL = {k: json.load(open(DOCS / f"{k}.main.json")) for k in PID}
TYPES = {k: {t["name"]: t for t in IDL[k]["types"]} for k in IDL}
EV = {}   # disc bytes -> (idl, name)
INS = {}  # (program id, disc) -> name
for k, d in IDL.items():
    for e in d["events"]:
        EV[(k, bytes(e["discriminator"]))] = e["name"]
    for i in d["instructions"]:
        INS[(PID[k], bytes(i["discriminator"]))] = i["name"]
EV_BY_PROG = collections.defaultdict(dict)
for (k, disc), n in EV.items():
    EV_BY_PROG[PID[k]][disc] = n
TAG = bytes.fromhex("e445a52e51cb9a1d")
A58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"

def b58e(b):
    n = int.from_bytes(b, "big"); s = ""
    while n: n, r = divmod(n, 58); s = A58[r] + s
    return "1" * (len(b) - len(b.lstrip(b"\0"))) + s
def b58d(s):
    n = 0
    for ch in s: n = n * 58 + A58.index(ch)
    out = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return b"\0" * (len(s) - len(s.lstrip("1"))) + out

class Short(Exception): pass
def walk(idl, ty, buf, off):
    if isinstance(ty, str):
        w = {"u8": 1, "i8": 1, "bool": 1, "u16": 2, "i16": 2, "u32": 4, "i32": 4, "u64": 8, "i64": 8, "u128": 16, "i128": 16, "pubkey": 32, "f32": 4, "f64": 8}
        if ty in w:
            n = w[ty]
            if off + n > len(buf): raise Short
            seg = buf[off:off + n]
            if ty == "pubkey": return b58e(seg), off + n
            if ty == "bool": return bool(seg[0]), off + n
            return int.from_bytes(seg, "little", signed=ty.startswith("i")), off + n
        if ty in ("string", "bytes"):
            if off + 4 > len(buf): raise Short
            n = struct.unpack_from("<I", buf, off)[0]; off += 4
            if off + n > len(buf): raise Short
            return buf[off:off + n].decode("utf-8", "replace") if ty == "string" else buf[off:off + n], off + n
        raise ValueError(ty)
    if "vec" in ty:
        if off + 4 > len(buf): raise Short
        n = struct.unpack_from("<I", buf, off)[0]; off += 4; out = []
        if n > 100000: raise Short
        for _ in range(n):
            v, off = walk(idl, ty["vec"], buf, off); out.append(v)
        return out, off
    if "option" in ty:
        if off + 1 > len(buf): raise Short
        f = buf[off]; off += 1
        if not f: return None, off
        return walk(idl, ty["option"], buf, off)
    if "array" in ty:
        t, n = ty["array"]; out = []
        for _ in range(n):
            v, off = walk(idl, t, buf, off); out.append(v)
        return out, off
    if "defined" in ty:
        d = ty["defined"]; name = d["name"] if isinstance(d, dict) else d
        t = TYPES[idl][name]["type"]
        if t["kind"] == "struct":
            out = {}
            for f in t.get("fields", []):
                out[f["name"]], off = walk(idl, f["type"], buf, off)
            return out, off
        if t["kind"] == "enum":
            if off + 1 > len(buf): raise Short
            vi = buf[off]; off += 1; v = t["variants"][vi]
            return {"variant": v["name"]}, off
    raise ValueError(ty)

def parse_event(idl, name, blob):
    out = {}; off = 8
    for f in TYPES[idl][name]["type"]["fields"]:
        out[f["name"]], off = walk(idl, f["type"], blob, off)
    return out, off

def keys_of(tx):
    meta = tx.get("meta") or {}
    la = meta.get("loadedAddresses") or {}
    return list(tx["transaction"]["message"]["accountKeys"]) + list(la.get("writable", [])) + list(la.get("readonly", []))

def log_events(logs):
    """(program id, blob) for every Program data line, program from the invoke stack."""
    stack, out = [], []
    for ln in logs:
        m = re.match(r"^Program (\S+) invoke \[\d+\]", ln)
        if m: stack.append(m.group(1)); continue
        if re.match(r"^Program (\S+) (success|failed)", ln):
            if stack: stack.pop()
            continue
        if ln.startswith("Program data: "):
            try: out.append((stack[-1] if stack else None, base64.b64decode(ln[len("Program data: "):].strip() + "==")))
            except Exception: out.append((stack[-1] if stack else None, b""))
    return out

def quote_mints(tx):
    """quote_mint account of every pump_amm trade-family instruction in the tx (IDL account order)."""
    keys = keys_of(tx); out = set()
    amm = {i["discriminator"] and bytes(i["discriminator"]): i for i in IDL["pump_amm"]["instructions"]}
    def one(ins):
        if ins["programIdIndex"] < len(keys) and keys[ins["programIdIndex"]] == PID["pump_amm"] and ins.get("data"):
            i = amm.get(b58d(ins["data"])[:8])
            if i and is_trade_ix(i["name"]):
                names = [a["name"] for a in i["accounts"]]
                if "quote_mint" in names and names.index("quote_mint") < len(ins["accounts"]):
                    out.add(keys[ins["accounts"][names.index("quote_mint")]])
    for ins in tx["transaction"]["message"]["instructions"]: one(ins)
    for g in (tx.get("meta") or {}).get("innerInstructions") or []:
        for ins in g["instructions"]: one(ins)
    return out

def instructions(tx):
    keys = keys_of(tx); res = []
    def one(ins, where):
        pid = keys[ins["programIdIndex"]] if ins["programIdIndex"] < len(keys) else None
        if pid in PNAME:
            d = b58d(ins["data"]) if ins.get("data") else b""
            res.append((pid, d, where))
    for ins in tx["transaction"]["message"]["instructions"]: one(ins, "top")
    for g in (tx.get("meta") or {}).get("innerInstructions") or []:
        for ins in g["instructions"]: one(ins, "inner")
    return res

HANDLED_BONDING = {"CreateEvent", "CompleteEvent", "CompletePumpAmmMigrationEvent", "TradeEvent", "PostCompleteBuyEvent", "SweepBondingCurveFeeEvent"}
HANDLED_AMM = {"BuyEvent", "SellEvent", "CreatePoolEvent", "BoostBuyAndBurnEvent", "SweepPoolFeeEvent", "InitBoostEvent"}
TRADE_EV = {"TradeEvent", "BuyEvent", "SellEvent"}
def is_trade_ix(n): return (n.startswith("buy") or n.startswith("sell")) and "buyback" not in n and "sweep" not in n

def frozen_blob(prog_name, name, blob, line):
    """Did the frozen path turn this blob into a row/event? returns (ok, row)"""
    if name in TRADE_EV or name == "CreatePoolEvent":
        row = td.decode_program_data(blob, event_v=True)
        if row is None: return False, None
        if name != "CreatePoolEvent" and not bf._event_v_complete(blob[:8], row): return False, row
        return True, row
    if name in ("CreateEvent", "CompleteEvent", "CompletePumpAmmMigrationEvent"):
        cr, mv = bf.lifecycle_from_logs([line]) if line else ([], [])
        if name == "CompletePumpAmmMigrationEvent" and not line: return (bf.decode_migration_event(blob) is not None), None
        return bool(cr or mv), None
    if name in HANDLED_BONDING | HANDLED_AMM:
        return td.decode_extra_event(blob) is not None, None
    return True, None  # event the rule does not read

META_KEYS = {"type", "kind", "venue", "mint_source", "event_source", "init_boost", "quote_is_wsol"}
def frozen_nontrade(name, blob):
    if name == "CreateEvent": return bf.decode_create_event(blob)
    if name == "CompleteEvent": return bf.decode_complete_event(blob)
    if name == "CompletePumpAmmMigrationEvent": return bf.decode_migration_event(blob)
    if name == "CreatePoolEvent": return td.decode_program_data(blob, event_v=True)
    return td.decode_extra_event(blob)

def flat_vals(o, out):
    if isinstance(o, dict):
        for v in o.values(): flat_vals(v, out)
    elif isinstance(o, list):
        for v in o: flat_vals(v, out)
    elif o is not None and not isinstance(o, bool): out.append(o)
    return out

def nt_unmatched(fro, ev):
    """frozen keys (non-meta) whose value equals no value of the independent IDL parse (value containment)."""
    pool = flat_vals(ev, [])
    # the frozen create/complete/migration decoders store the native-SOL zero pubkey as WSOL (bf._quote_or_wsol): by design, accepted
    zero = "11111111111111111111111111111111" in pool
    return [k for k, v in fro.items() if k not in META_KEYS and v is not None and not isinstance(v, bool) and v not in pool and not (k == "quote_mint" and zero and v == td.WSOL_MINT)]

def compare(name, row, ev):
    bad = []
    def eq(a, b, f, g):
        if a != b: bad.append((f, a, b))
    if name == "TradeEvent":
        for f, g in (("mint", "mint"), ("sol_lamports", "sol_amount"), ("token_raw", "token_amount"), ("trader", "user"), ("event_ts", "timestamp"), ("quote_reserve", "virtual_sol_reserves"), ("base_reserve", "virtual_token_reserves"), ("ix_name", "ix_name")):
            eq(row.get(f), ev[g], f, g)
        eq(row["side"], "buy" if ev["is_buy"] else "sell", "side", "is_buy")
    else:
        buy = name == "BuyEvent"
        pairs = [("pool", "pool"), ("trader", "user"), ("event_ts", "timestamp"), ("quote_reserve", "pool_quote_token_reserves"), ("base_reserve", "pool_base_token_reserves"),
                 ("lp_fee", "lp_fee"), ("protocol_fee", "protocol_fee"), ("creator_fee", "coin_creator_fee"), ("virtual_quote_reserves", "virtual_quote_reserves"),
                 ("buyback_fee", "buyback_fee"), ("creator_fee_unclaimed", "creator_fee_unclaimed")]
        pairs += [("token_raw", "base_amount_out"), ("sol_lamports", "user_quote_amount_in"), ("pool_quote_amount", "quote_amount_in"), ("ix_name", "ix_name")] if buy else \
                 [("token_raw", "base_amount_in"), ("sol_lamports", "user_quote_amount_out"), ("pool_quote_amount", "quote_amount_out"), ("supply_raw", "base_supply")]
        for f, g in pairs: eq(row.get(f), ev[g], f, g)
        eq(row.get("fee_recipient_zero"), ev["protocol_fee_recipient"] == "11111111111111111111111111111111", "fee_recipient_zero", "protocol_fee_recipient")
        eq(row["side"], "buy" if buy else "sell", "side", "name")
    return bad

def load(kind, sig):
    p = RAW / kind / f"{sig}.json.gz"
    return json.loads(gzip.open(p).read())["r"] if p.exists() else None

def tx_is_migrate(evs):
    return any(b[:8] == bytes.fromhex("bde95db95c94ea94") for _, b, _, _ in evs)

def analyse(tx, slot, sig, src, acc):
    meta = tx.get("meta") or {}
    if meta.get("err") is not None:
        acc["failed_txs"] += 1; return
    acc["txs"][src] += 1
    logs = meta.get("logMessages") or []
    flag = lambda cat, **kw: acc["nondecode"].append(dict(cat=cat, sig=sig, slot=slot, src=src, **kw))
    if any(l.startswith("Log truncated") for l in logs): acc["log_truncated_txs"] += 1
    evs = []  # (prog id, name, blob, line, source)
    for ln in logs:
        pass
    stackev = log_events(logs); li = 0
    for ln in logs:
        if ln.startswith("Program data: "):
            pid, blob = stackev[li]; li += 1
            evs.append((pid, blob, ln, "log"))
    log_multiset = collections.Counter(b for _, b, _, _ in evs)
    keys = keys_of(tx)
    for g in meta.get("innerInstructions") or []:
        for ins in g["instructions"]:
            pid = keys[ins["programIdIndex"]] if ins["programIdIndex"] < len(keys) else None
            if pid in PNAME and ins.get("data"):
                d = b58d(ins["data"])
                if d[:8] == TAG:
                    blob = d[8:]
                    if log_multiset.get(blob, 0) > 0: log_multiset[blob] -= 1
                    else: evs.append((pid, blob, None, "cpi_only"))
    ntr_ev = 0; ntr_frozen = 0; ncap_log = 0; trunc = any(l.startswith('Log truncated') for l in logs)
    for pid, blob, line, srcname in evs:
        if pid not in PNAME: continue
        if len(blob) == 0 and srcname == "cpi_only":
            acc["empty_tag_cpi"] += 1; continue  # bare Anchor event-authority self-CPI (tag only, no event): not an event
        if len(blob) < 8: flag("N2", detail="short blob", program=PNAME[pid]); continue
        name = EV_BY_PROG[pid].get(blob[:8])
        idl = PNAME[pid]
        if name is None:
            acc["unknown_disc"][f"{idl}:{blob[:8].hex()}"] += 1; flag("N2", program=idl, disc=blob[:8].hex(), len=len(blob)); continue
        acc["events"][f"{idl}.{name}"] += 1
        try:
            ev, end = parse_event(idl, name, blob)
        except Short:
            flag("N3", program=idl, event=name, len=len(blob), why="short"); continue
        acc["layout_len"][f"{idl}.{name}"][len(blob)] += 1
        if end != len(blob):
            if len(blob) - end == 8:
                acc["trail8"][f"{idl}.{name}"] += 1
                acc["trail8_by_slot"].append((slot, f"{idl}.{name}", int.from_bytes(blob[end:], "little")))
            else:
                flag("N3", program=idl, event=name, len=len(blob), consumed=end, why="trailing bytes != 8"); continue
        if name in TRADE_EV: ntr_ev += 1
        read = name in HANDLED_BONDING | HANDLED_AMM
        if read and name not in TRADE_EV:
            fro = frozen_nontrade(name, blob)
            key = f"{idl}.{name}|{srcname}"
            acc["nt_total"][key] += 1
            if fro is None: acc["nt_none"][key] += 1
            else:
                um = nt_unmatched(fro, ev)
                if um: acc["nt_unmatched"][key] += 1; flag("N6", program=idl, event=name, unmatched_keys=um, source=srcname)
                else: acc["nt_equal"][key] += 1
            if tx_is_migrate(evs): acc["nt_in_migrate_tx"][key] += 1
        if read:
            acc["read_events"][f"{idl}.{name}"] += 1
            if srcname == "cpi_only":
                acc["cpi_only"][f"{idl}.{name}"] += 1
                # the log path (shadow) cannot see it; the walker decodes only migration and the extra events from CPIs
                if name in TRADE_EV or name in ("CreateEvent", "CompleteEvent", "CreatePoolEvent"):
                    flag("N1", program=idl, event=name, truncated=trunc, why="CPI-only (not in logs): invisible to records_from_logs")
                    continue
            ok, row = frozen_blob(idl, name, blob, line)
            if not ok:
                qm = sorted(quote_mints(tx))
                big = None
                if name in TRADE_EV:
                    big = ev.get("user_quote_amount_in", ev.get("user_quote_amount_out", ev.get("sol_amount")))
                    why = "sol_lamports > cap" if (big or 0) > td._MAX_SWAP_SOL_LAMPORTS and idl == "pump_amm" else ("sol_amount > bonding cap" if idl == "pump" and (big or 0) > td._MAX_BONDING_SOL_LAMPORTS else "other")
                    if not td._sane_ts(int(ev["timestamp"])): why = "ts outside window"
                    acc["refused"][(f"{idl}.{name}", why)] += 1
                    acc["refused_rows"].append(dict(sig=sig, slot=slot, event=f"{idl}.{name}", why=why, amount=big, quote_mints=qm))
                    if idl == "pump_amm" and why == "sol_lamports > cap": acc["refused_quote"].update(qm)
                    if srcname == "log": ncap_log += 1
                    continue
                flag("N1", program=idl, event=name, len=len(blob), ix_name=ev.get("ix_name")); continue
            if name in TRADE_EV and row is not None:
                ntr_frozen += 1
                bad = compare(name, row, ev)
                acc["compared"][name] += 1
                if bad: flag("N6", program=idl, event=name, diffs=[(f, str(a), str(b)) for f, a, b in bad][:6])
    # instructions
    n_trade_ix = 0; multihop = False
    for pid, d, where in instructions(tx):
        if len(d) >= 8 and d[:8] == TAG: continue
        nm = INS.get((pid, d[:8]))
        if nm is None:
            acc["unknown_ix"][f"{PNAME[pid]}:{d[:8].hex()}"] += 1; flag("N4", program=PNAME[pid], disc=d[:8].hex(), len=len(d)); continue
        acc["ix"][f"{PNAME[pid]}.{nm}"] += 1
        if "multi_hop" in nm: multihop = True
        elif pid != PID["pump_fees"] and is_trade_ix(nm): n_trade_ix += 1
    acc["trade_ix_total"] += n_trade_ix
    if not multihop and ntr_ev != n_trade_ix:
        flag("N5", trade_events=ntr_ev, trade_ix=n_trade_ix, truncated=trunc, why="events != trade instructions")
    if multihop: acc["multihop_txs"] += 1
    # N5 second half: frozen trade rows from the logs path vs independent log-sourced trade events
    rows = td.records_from_logs(logs, slot=slot, signature=sig, t_recv_ms=0, commitment="finalized", feed="parity", pool_mints={}, event_v=True)
    n_log_trade = sum(1 for pid, b, ln, s in evs if s == "log" and pid in PNAME and EV_BY_PROG[pid].get(b[:8]) in TRADE_EV)
    acc["frozen_rows"] += len(rows); acc["indep_log_trade_events"] += n_log_trade
    if len(rows) != n_log_trade:
        flag("N5", frozen_rows=len(rows), indep_log_trade_events=n_log_trade, capped_log_events=ncap_log, explained_by_cap=(len(rows) == n_log_trade - ncap_log), why="records_from_logs row count differs")
    for r in rows:
        if r.get("venue") == "pumpswap" and not r.get("quote_mint"): acc["unresolved_mint_rows"] += 1

acc = dict(failed_txs=0, txs=collections.Counter(), nondecode=[], log_truncated_txs=0, unknown_disc=collections.Counter(), events=collections.Counter(), layout_len=collections.defaultdict(collections.Counter),
           read_events=collections.Counter(), cpi_only=collections.Counter(), compared=collections.Counter(), unknown_ix=collections.Counter(), ix=collections.Counter(), trade_ix_total=0,
           multihop_txs=0, empty_tag_cpi=0, nt_total=collections.Counter(), nt_none=collections.Counter(), nt_unmatched=collections.Counter(), nt_equal=collections.Counter(), nt_in_migrate_tx=collections.Counter(), trail8=collections.Counter(), trail8_by_slot=[], refused=collections.Counter(), refused_rows=[], refused_quote=collections.Counter(), frozen_rows=0, indep_log_trade_events=0, unresolved_mint_rows=0)
idx = json.load(open(RAW / "sample_index.json"))
slots = sorted({v["slot"] for v in idx["a"].values()})
walker = collections.Counter(); wk = collections.defaultdict(collections.Counter)
pool_mints = {}
nblocks = 0
for p in sorted((RAW / "blocks").glob("*.json.gz")):
    blk = json.loads(gzip.open(p).read())["r"]
    if not blk: continue
    nblocks += 1; slot = int(p.name.split(".")[0]); blk["slot"] = slot
    wres = bf.rows_from_block(blk, pool_mints, event_v=True)
    for k in ("trades", "creates", "migrations", "unresolved", "events"): walker[k] += len(wres[k])
    for k in ("unknown_disc", "bad_disc", "cpi_disc", "cpi_only_disc"):
        for kk, vv in wres[k].items(): wk[k][kk] += vv
    for tx in blk["transactions"]:
        sig = tx["transaction"]["signatures"][0]
        if sig in idx["a"]:
            analyse(tx, slot, sig, "A", acc)
for sig, meta in idx["b"].items():
    tx = load("txs", sig)
    if tx: analyse(tx, tx["slot"], sig, "B", acc)
    else: acc["nondecode"].append(dict(cat="FETCH", sig=sig, why="getTransaction returned null"))
cs = set()
for c in idx["c"]:
    for s in c["picked"]:
        if s in cs or s in idx["b"] or s in idx["a"]: continue
        cs.add(s); tx = load("txs", s)
        if tx: analyse(tx, tx["slot"], s, "C", acc)
        else: acc["nondecode"].append(dict(cat="FETCH", sig=s, why="getTransaction returned null"))
blob_sha = {}
import subprocess
for f in ("observe/trade_decode.py", "tools/pump_history_backfill.py", "tools/h5_shadow.py"):
    here = hashlib.sha1(b"blob %d\0" % len((REPO / f).read_bytes()) + (REPO / f).read_bytes()).hexdigest()
    frozen = subprocess.run(["git", "-C", str(REPO), "rev-parse", f"1f7a89c:{f}"], capture_output=True, text=True).stdout.strip()
    blob_sha[f] = dict(here=here, at_1f7a89c=frozen, equal=here == frozen)
res = dict(n_blocks_A=nblocks, n_A_txs_success_touching=len(idx["a"]), tx_analysed=dict(acc["txs"]), failed_txs=acc["failed_txs"], log_truncated_txs=acc["log_truncated_txs"],
    events=dict(acc["events"]), read_events=dict(acc["read_events"]), cpi_only=dict(acc["cpi_only"]), unknown_disc=dict(acc["unknown_disc"]), unknown_ix=dict(acc["unknown_ix"]),
    ix=dict(acc["ix"]), layout_len={k: dict(v) for k, v in acc["layout_len"].items()}, compared_fields_ok_or_not=dict(acc["compared"]), multihop_txs=acc["multihop_txs"],
    frozen_rows_records_from_logs=acc["frozen_rows"], indep_log_trade_events=acc["indep_log_trade_events"], unresolved_mint_rows=acc["unresolved_mint_rows"], trade_ix_total=acc["trade_ix_total"],
    walker_rows=dict(walker), walker_unknown_disc=dict(wk["unknown_disc"]), walker_bad_disc=dict(wk["bad_disc"]), walker_cpi_only_disc=dict(wk["cpi_only_disc"]),
    empty_tag_cpi=acc["empty_tag_cpi"], nontrade={k: dict(total=acc['nt_total'][k], frozen_none=acc['nt_none'][k], value_unmatched=acc['nt_unmatched'][k], equal=acc['nt_equal'][k], in_migrate_tx=acc['nt_in_migrate_tx'][k]) for k in acc['nt_total']}, trail8=dict(acc['trail8']), refused={f'{a}|{b}': v for (a, b), v in acc['refused'].items()}, refused_quote=dict(acc['refused_quote']), refused_rows=acc['refused_rows'], trail8_values=dict(collections.Counter(v for _, _, v in acc['trail8_by_slot']).most_common(10)), trail8_first_last_slot=[min((x[0] for x in acc['trail8_by_slot']), default=None), max((x[0] for x in acc['trail8_by_slot']), default=None)], nondecode_by_cat=dict(collections.Counter(n["cat"] for n in acc["nondecode"])), nondecode=acc["nondecode"], blob_sha=blob_sha,
    idl_sha256={k: hashlib.sha256((DOCS / f"{k}.main.json").read_bytes()).hexdigest() for k in IDL}, c_sample=idx["c"])
(OUT / ("s06_parity_pre.json" if os.environ.get("SAMPLE") == "pre" else "s06_parity.json")).write_text(json.dumps(res, indent=1, sort_keys=True, default=str) + "\n")
print(json.dumps({k: v for k, v in res.items() if k not in ("nondecode", "ix", "layout_len", "c_sample", "events", "read_events")}, indent=1, default=str)[:5000])
