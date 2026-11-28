"""Offline (no RPC). 2026-11-27 pump-only redeploy: string-set diff of each program's kept previous binary against the new one
(pump 46bf6c8e... -> c6dd30fd...), plus both new binaries against the docs IDL (pump-public-docs main e244b35).
Strings = printable-ASCII runs >= 8 bytes, split at 'Instruction: '. A reading aid, not a proof of equal behaviour. Report-only."""
import hashlib, json, re, difflib
from pathlib import Path
P = Path("/data2/mal/scratch/a3-repin-1128")
PREV = {"pump": P / "prev/pump_old.bin"}   # the 471854454 binary (kept from the 2026-11-20 review)
NEW = {"pump": P / "pd/pump.bin"}
IDL = {k: json.load(open(P / f"docs/{k}.main.json")) for k in ("pump",)}
IDLNAME = {"pump": "pump", "pumpswap": "pump_amm", "fees": "pump_fees"}
OUT = Path(__file__).resolve().parent / "out"
RUN = re.compile(rb"[\x20-\x7e]{8,}")

def runs(d): return {m.group().decode() for m in RUN.finditer(d)}
def camel(s): return "".join(p.capitalize() for p in s.split("_"))
def idl_check(d, idl):
    names = [camel(i["name"]) for i in idl["instructions"]]
    conf = {}
    for n in names:
        pat = re.compile(rb"Instruction: " + re.escape(n.encode()) + rb"(?=Instruction: |[^A-Za-z0-9_]|\Z)")
        conf[n] = len(pat.findall(d))
    # 'Instruction: X' strings in the binary whose X is not an IDL name (split on the marker, longest-prefix match to an IDL name)
    marker = re.findall(rb"Instruction: ([A-Za-z0-9_]+)", d)
    # a captured token may be several names glued (e.g. BuyV2Instruction is split by the marker already); take tokens as-is
    toks = {(t.decode()[:-len("Instruction")] if t.decode().endswith("Instruction") else t.decode()) for t in marker}
    extra = sorted(toks - set(names))
    errs = [e.get("msg") or e["name"] for e in idl.get("errors", [])]
    return {"n_idl_instructions": len(names), "idl_names_without_bare_occurrence": sorted(n for n, v in conf.items() if v == 0),
            "binary_instruction_tokens_not_idl_names": extra, "n_idl_errors": len(errs), "idl_errors_missing_in_binary": [e for e in errs if e.encode() not in d]}

def pieces(rs):
    out = set()
    for r in rs:
        for p in r.split("Instruction: "):
            if p: out.add(p)
    return out

def diff(name):
    old = PREV[name].read_bytes(); new = NEW[name].read_bytes()
    res = {"old_sha256": hashlib.sha256(old).hexdigest(), "new_sha256": hashlib.sha256(new).hexdigest(), "old_len": len(old), "new_len": len(new)}
    ro, rn = runs(old), runs(new)
    res["runs"] = {"old": len(ro), "new": len(rn), "only_old": len(ro - rn), "only_new": len(rn - ro)}
    po, pn = pieces(ro), pieces(rn)
    res["instruction_pieces"] = {"n_only_old": len(po - pn), "n_only_new": len(pn - po)}
    res["pieces_only_old"] = sorted(x[:400] for x in (po - pn))[:80]
    res["pieces_only_new"] = sorted(x[:400] for x in (pn - po))[:80]
    pg = 4096
    res["pages_4k"] = {"total": len(old) // pg, "differing": sum(1 for i in range(0, len(old), pg) if old[i:i + pg] != new[i:i + pg])}
    res["new_vs_idl"] = idl_check(new, IDL[IDLNAME[name]])
    res["old_vs_idl"] = idl_check(old, IDL[IDLNAME[name]])
    nn = [r for r in rn if r not in ro]; oo = [r for r in ro if r not in rn]
    res["changed_runs_diff"] = []
    for a in oo[:60]:
        if not nn: break
        b = max(nn, key=lambda x: difflib.SequenceMatcher(None, a[:3000], x[:3000], autojunk=False).quick_ratio())
        sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
        res["changed_runs_diff"].append([{"op": t, "old": a[i1:i2][:300], "new": b[j1:j2][:300], "ctx_old": a[max(0, i1 - 30):i1]} for t, i1, i2, j1, j2 in sm.get_opcodes() if t != "equal"][:30])
    res["new_standalone_runs"] = [r[:300] for r in nn if len(r) < 200][:200]
    res["old_standalone_runs"] = [r[:300] for r in oo if len(r) < 200][:200]
    res["n_only_new_runs"] = len(nn); res["n_only_old_runs"] = len(oo)
    return res

res = {k: diff(k) for k in ("pump",)}
print(json.dumps(res, indent=1)[:6000])
(OUT / "s04_binary_strings.json").write_text(json.dumps(res, indent=1, sort_keys=True) + "\n")
