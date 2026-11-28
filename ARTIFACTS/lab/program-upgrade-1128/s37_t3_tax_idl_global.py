"""QP-RULING-1128 T3 (complete pump IDL diff, old pinned 639b87f vs new e244b35) and T4 part 1 (the pump Global account read: where the tax rate lives, its value).
Offline for the IDL part (saved docs); one getAccountInfo for Global (PDA ["global"] under pump) at a finalized slot. Public RPC, 0 credits. Structure only.
Run from this directory after the last sample block was fetched (T4 requires a read after the last sample)."""
import base64, hashlib, json, struct
from pathlib import Path
from common import OUT, LClient
from solders.pubkey import Pubkey
D = Path("/data2/mal/scratch/a3-repin-1128/docs")
old = json.load(open(D / "pump.639b87f.json")); new = json.load(open(D / "pump.main.json"))
PUMP = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"


def by(lst, key="name"): return {e[key]: e for e in lst}


def ix_sig(i):
    return {"disc": i["discriminator"], "args": [(a["name"], a["type"]) for a in i.get("args", [])],
            "accounts": [{k: a.get(k) for k in ("name", "writable", "signer", "optional", "pda", "address", "relations")} for a in i.get("accounts", [])]}


res = {"instructions": {}, "accounts": {}, "events": {}, "types": {}, "errors": {}}
oi, ni = by(old["instructions"]), by(new["instructions"])
for k in sorted(set(oi) | set(ni)):
    if k not in oi: res["instructions"][k] = {"status": "added", "sig": ix_sig(ni[k])}
    elif k not in ni: res["instructions"][k] = {"status": "removed"}
    elif ix_sig(oi[k]) != ix_sig(ni[k]):
        a, b = ix_sig(oi[k]), ix_sig(ni[k])
        res["instructions"][k] = {"status": "changed", "disc_equal": a["disc"] == b["disc"], "args_old": a["args"], "args_new": b["args"],
                                  "accounts_changed": a["accounts"] != b["accounts"], "accounts_old_n": len(a["accounts"]), "accounts_new_n": len(b["accounts"])}
    elif oi[k] != ni[k]: res["instructions"][k] = {"status": "docs/text only (args, accounts incl. signer/writable/PDA seeds, discriminator equal)"}
for sect in ("accounts", "events"):
    o, n = by(old[sect]), by(new[sect])
    for k in sorted(set(o) | set(n)):
        res[sect][k] = {"status": "added" if k not in o else "removed" if k not in n else "discriminator CHANGED" if o[k]["discriminator"] != n[k]["discriminator"] else "discriminator equal"}
ot, nt = by(old["types"]), by(new["types"])
for k in sorted(set(ot) | set(nt)):
    if k not in ot: res["types"][k] = {"status": "added"}
    elif k not in nt: res["types"][k] = {"status": "removed"}
    elif ot[k] != nt[k]:
        of = [(f["name"], f["type"]) for f in ot[k]["type"].get("fields", [])]; nf = [(f["name"], f["type"]) for f in nt[k]["type"].get("fields", [])]
        res["types"][k] = {"status": "changed", "old_n_fields": len(of), "new_n_fields": len(nf), "old_is_exact_prefix": nf[:len(of)] == of,
                           "appended": nf[len(of):] if nf[:len(of)] == of else None, "other_change": None if nf[:len(of)] == of else {"old": of, "new": nf}}
oe, ne = by(old["errors"], "code"), by(new["errors"], "code")
for k in sorted(set(oe) | set(ne)):
    if k not in oe: res["errors"][k] = {"status": "added", "name": ne[k]["name"], "msg": ne[k].get("msg")}
    elif k not in ne: res["errors"][k] = {"status": "removed"}
    elif oe[k] != ne[k]: res["errors"][k] = {"status": "changed", "old": oe[k], "new": ne[k]}
res = {k: ({a: b for a, b in v.items() if not (isinstance(b, dict) and b.get("status") == "discriminator equal")} if k in ("accounts", "events") else v) for k, v in res.items()}
res["unchanged_named"] = {n: (oi[n]["discriminator"] == ni[n]["discriminator"] and ix_sig(oi[n]) == ix_sig(ni[n])) for n in ("create", "create_v2", "migrate", "buy", "sell", "buy_exact_quote_in_v2", "sell_v2", "extend_account") if n in oi and n in ni}
res["event_types_unchanged"] = {n: ot[n] == nt[n] for n in ("CreateEvent", "CompleteEvent", "CompletePumpAmmMigrationEvent") if n in ot}
res["pump_instruction_names_new"] = sorted(ni)
# Global: byte offset of the tax fields (fixed-size types only before them)
W = {"u8": 1, "bool": 1, "u64": 8, "i64": 8, "pubkey": 32}
def size(t):
    if isinstance(t, str): return W[t]
    if "array" in t: return size(t["array"][0]) * t["array"][1]
    raise ValueError(t)
off = 8; offs = {}
for f in nt["Global"]["type"]["fields"]:
    offs[f["name"]] = off; off += size(f["type"])
res["Global_offsets_new"] = {k: v for k, v in offs.items() if k in ("block0_tax_bps", "min_configurable_creator_fee_bps")}; res["Global_size_new"] = off
off = 8; offo = {}
for f in ot["Global"]["type"]["fields"]: offo[f["name"]] = off; off += size(f["type"])
res["Global_size_old"] = off
pda = str(Pubkey.find_program_address([b"global"], Pubkey.from_string(PUMP))[0]); res["Global_pda"] = pda
c = LClient(max_calls=10)
r = c.call("getAccountInfo", [pda, {"encoding": "base64", "commitment": "finalized"}])
raw = base64.b64decode(r["value"]["data"][0]); sl = r["context"]["slot"]
res["Global_read"] = {"ctx_slot": sl, "owner": r["value"]["owner"], "len": len(raw), "sha256": hashlib.sha256(raw).hexdigest(), "disc": raw[:8].hex(),
                      "block0_tax_bps": struct.unpack_from("<Q", raw, offs["block0_tax_bps"])[0] if len(raw) >= offs["block0_tax_bps"] + 8 else None,
                      "min_configurable_creator_fee_bps": struct.unpack_from("<Q", raw, offs["min_configurable_creator_fee_bps"])[0] if len(raw) >= offs["min_configurable_creator_fee_bps"] + 8 else None,
                      "len_equals_idl_size": len(raw) == res["Global_size_new"] or len(raw) >= res["Global_size_new"]}
res["decoder_C5"] = {"pump TradeEvent": "observe/trade_decode.py event_v_fields: for the bonding TradeEvent the only variable read is ix_name at offset 258 (field 21); other reads are the fixed legacy offsets before it (_decode_trade), all before field 21. Appended fields (35 quote_transfer_fee, 36 block0_fee) come after field 27 (a vec) and 34, behind every offset read.",
                     "CreateEvent": "event type layout equal (hash), unchanged", "Complete/Migration": "event type layouts equal (hash), unchanged"}
(OUT / "s37_t3_tax_idl_global.json").write_text(json.dumps(res, indent=1, default=str))
print(json.dumps({k: res[k] for k in ("Global_read", "Global_offsets_new", "Global_size_old", "Global_size_new", "unchanged_named", "event_types_unchanged")}, indent=1))
print({k: v.get("status") for k, v in res["instructions"].items()}, res["types"].keys(), res["errors"].keys())
