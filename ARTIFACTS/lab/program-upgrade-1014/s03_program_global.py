"""Step 3: the redeployed pump program and the pump Global account.

Public RPC: pump program -> programdata (one full read, kept off-repo in RAW), the last upgrade tx of the programdata
account, the pump Global account (1088 bytes). Offline: strings in the new ELF against the IDL copies of the docs repo
(heads 2293f9a = before, 639b87f = after). The previous pump binary (sha 8c8f6aae...) is not on disk, so the binaries are
NOT diffed; what is compared is the new binary's strings with the old and new IDL text.
"""
import hashlib
import json
import re
import struct
import urllib.request

from common import RAW, REPO, client, m, write_json, acct

DOCS_RAW = "https://raw.githubusercontent.com/pump-fun/pump-public-docs/{sha}/idl/pump.json"
IDL_SHAS = {"old": "2293f9a66c654e9fe82dc5e8f4618538f24bb35f", "new": "639b87fb45c3adeffa6f7c4a1bf0cb85e392bf22"}
(RAW / "idl").mkdir(exist_ok=True)
idl = {}
for k, sha in IDL_SHAS.items():
    p = RAW / "idl" / f"pump_{k}.json"
    if not p.exists():
        p.write_bytes(urllib.request.urlopen(urllib.request.Request(DOCS_RAW.format(sha=sha), headers={"User-Agent": "mal-upgrade-review/1"}), timeout=60).read())
    idl[k] = json.loads(p.read_text())

c = client(max_calls=30)
out = {}
# --- program account -> programdata -> full read
pa, _, _ = acct(c, m.PUMP_PROGRAM)
pd_addr = m.b58encode(pa[4:36])
elf_path = RAW / "pump_programdata_new.bin"
d, v, ctx = acct(c, pd_addr)
elf_path.write_bytes(d)
out["programdata"] = dict(address=pd_addr, ctx_slot=ctx, len=len(d), sha256=hashlib.sha256(d).hexdigest(), sha256_elf_after_45=hashlib.sha256(d[45:]).hexdigest(),
                          deploy_slot=struct.unpack("<Q", d[4:12])[0], upgrade_authority=m.b58encode(d[13:45]) if d[12] else None, lamports=v["lamports"])
pins = json.load(open(REPO / "tools" / "pump_structure_pins.json"))["programs"]["pump"]
out["pin_equal"] = dict(sha256=out["programdata"]["sha256"] == pins["sha256"], deploy_slot=out["programdata"]["deploy_slot"] == pins["deploy_slot"], data_len=len(d) == pins["data_len"])
print(json.dumps(out["programdata"]), out["pin_equal"])
# --- upgrade tx
sigs = c.call("getSignaturesForAddress", [pd_addr, {"limit": 5, "commitment": "finalized"}])
out["recent_sigs"] = [dict(slot=s["slot"], sig=s["signature"][:16], err=s.get("err"), bt=s.get("blockTime")) for s in sigs]
up = next(s for s in sigs if s["slot"] == out["programdata"]["deploy_slot"])
tx = m.fetch_tx(c, up["signature"])
keys = m.tx_keys(tx)
msg = tx["transaction"]["message"]
out["upgrade_tx"] = dict(sig=up["signature"], slot=tx["slot"], block_time=tx["blockTime"], utc=m.iso(tx["blockTime"]), err=tx["meta"]["err"], signers=m.tx_signers(tx), fee=tx["meta"]["fee"],
                         top_programs=[keys[i["programIdIndex"]] for i in msg["instructions"]],
                         inner_programs=[keys[i["programIdIndex"]] for g in tx["meta"].get("innerInstructions") or [] for i in g["instructions"]],
                         squads_in_keys=[k for k in keys if k.startswith("SQDS")], authority_in_keys="7gZufwwAo17y5kg8FMyJy2phgpvv9RSdzWtdXiWHjFr8" in keys,
                         upgraded_log=[l[:90] for l in tx["meta"]["logMessages"] if "Upgraded" in l])
print(json.dumps(out["upgrade_tx"])[:900])
# --- pump Global
g, gv, gctx = acct(c, m.PUMP_GLOBAL)
out["global"] = dict(address=m.PUMP_GLOBAL, ctx_slot=gctx, len=len(g), sha256=hashlib.sha256(g).hexdigest(), sha256_first_1087=hashlib.sha256(g[:1087]).hexdigest(), last_byte_max_curve_depth=g[1087],
                     fee_basis_points=struct.unpack("<Q", g[105:113])[0], initial_virtual_token_reserves=struct.unpack("<Q", g[73:81])[0], initial_virtual_sol_reserves=struct.unpack("<Q", g[81:89])[0],
                     initial_real_token_reserves=struct.unpack("<Q", g[89:97])[0], initial_virtual_quote_reserves=struct.unpack("<Q", g[1005:1013])[0], create_v2_enabled=g[450], mayhem_mode_enabled=g[515],
                     is_holder_reward_enabled=g[1086], whitelisted_quote_mints=[m.b58encode(g[1013:1045])])
print(json.dumps(out["global"]))

# --- strings in the new ELF vs the IDL
txt = set(x.decode() for x in re.findall(rb"[ -~]{6,}", d))
big = "\n".join(txt)


def camel(s):
    return "".join(p.capitalize() for p in s.split("_"))


names_new = [camel(i["name"]) for i in idl["new"]["instructions"]]
found = set()
for s in txt:
    found.update(re.findall(r"Instruction: (\w+)", s))
out["strings"] = dict(
    n_idl_instructions=len(names_new),
    idl_instructions_not_in_binary=[n for n in names_new if ("Instruction: " + n) not in big],
    binary_instruction_names_not_in_idl=sorted(found - set(names_new)),
    n_binary_instruction_names=len(found),
)
errs_new = {e["code"]: e.get("msg") or e["name"] for e in idl["new"]["errors"]}
errs_old = {e["code"]: e.get("msg") or e["name"] for e in idl["old"]["errors"]}
changed = [k for k in errs_new if errs_old.get(k) != errs_new[k]]
out["strings"]["idl_error_msgs_new_present"] = sum(1 for k, s in errs_new.items() if s in big)
out["strings"]["n_idl_errors_new"] = len(errs_new)
out["strings"]["errors_changed_in_docs_commit"] = {str(k): dict(new_msg_in_binary=errs_new[k] in big, old_msg_in_binary=(errs_old.get(k) in big) if errs_old.get(k) else None) for k in changed}
needles = ["BuyV3", "SellV3", "BuyExactQuoteInV3", "MultiHopCurveSwap", "SetMaxCurveDepth", "SweepProtocolFee", "SweepCreatorFee", "PostCompleteBuy", "BuyV2", "SellV2", "MigrateV2", "BuyExactQuoteInV2", "CreateV2"]
out["strings"]["needles_instruction_count"] = {n: big.count("Instruction: " + n) for n in needles}
print(json.dumps(out["strings"], indent=1))
write_json("s03_program_global.json", out)
