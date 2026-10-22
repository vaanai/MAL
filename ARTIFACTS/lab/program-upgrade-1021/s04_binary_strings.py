"""Offline (no RPC). (1) pump: string-set diff of the 10-13 binary (sha ba1f91ac, kept by the 10-14 review) against the 10-19 binary.
(2) pump and pumpswap new binaries against the docs IDL (pump-public-docs 639b87f == main): instruction names ('Instruction: <Name>'),
error messages. Strings = printable-ASCII runs >= 8 bytes. Rust string pieces are stored back to back, so a run is a concatenation;
the diff is on runs split at the 'Instruction: ' marker and on the IDL names, and is a reading aid, not a proof of equal behaviour."""
import hashlib, json, re
from pathlib import Path
OLD = Path("/data/mal/program-upgrade-1014/raw/pump_programdata_new.bin")
NEW = {"pump": Path("/data2/mal/scratch/a3-repin-1021/pd/pump.bin"), "pumpswap": Path("/data2/mal/scratch/a3-repin-1021/pd/pumpswap.bin")}
IDL = {k: json.load(open(f"/data2/mal/scratch/a3-repin-1021/docs/{k}.main.json")) for k in ("pump", "pump_amm", "pump_fees")}
OUT = Path(__file__).resolve().parent / "out"
RUN = re.compile(rb"[\x20-\x7e]{8,}")

def runs(d): return {m.group().decode() for m in RUN.finditer(d)}
def camel(s): return "".join(p.capitalize() for p in s.split("_"))

old = OLD.read_bytes(); new = NEW["pump"].read_bytes()
res = {"old_sha256": hashlib.sha256(old).hexdigest(), "new_sha256": hashlib.sha256(new).hexdigest(), "old_len": len(old), "new_len": len(new)}
ro, rn = runs(old), runs(new)
res["pump_runs"] = {"old": len(ro), "new": len(rn), "only_old": len(ro - rn), "only_new": len(rn - ro)}
def pieces(rs):
    out = set()
    for r in rs:
        for p in r.split("Instruction: "):
            if p: out.add(p)
    return out
po, pn = pieces(ro), pieces(rn)
res["pump_instruction_pieces"] = {"n_only_old": len(po - pn), "n_only_new": len(pn - po)}
# byte-level: how many 4 KiB pages differ (both 10,485,760 bytes)
pg = 4096
diff_pages = sum(1 for i in range(0, len(old), pg) if old[i:i+pg] != new[i:i+pg])
res["pump_pages_4k"] = {"total": len(old)//pg, "differing": diff_pages}
# IDL name checks
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
res["pump_vs_idl_new"] = idl_check(new, IDL["pump"])
res["pump_old_vs_idl_new"] = idl_check(old, IDL["pump"])
res["pumpswap_vs_idl"] = idl_check(NEW["pumpswap"].read_bytes(), IDL["pump_amm"])
import difflib
nn = [r for r in rn if r not in ro]; oo = [r for r in ro if r not in rn]
res["pump_changed_runs_diff"] = []
for a in oo:
    b = max(nn, key=lambda x: difflib.SequenceMatcher(None, a[:3000], x[:3000], autojunk=False).quick_ratio())
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    res["pump_changed_runs_diff"].append([{"op": t, "old": a[i1:i2], "new": b[j1:j2], "ctx_old": a[max(0, i1-30):i1]} for t, i1, i2, j1, j2 in sm.get_opcodes() if t != "equal"])
res["pump_new_standalone_runs"] = [r for r in nn if len(r) < 200]
res["pump_binary_error_new_text_in_idl"] = {t: (t.encode() in new) for t in ["quote_mint must be writable for a quote with a Token-2022 transfer fee: its withheld fees are harvested to the mint"]}
res["docs_idl_contains_QuoteMintNotWritable"] = any("QuoteMintNotWritable" in json.dumps(v) for v in IDL.values())
print(json.dumps(res, indent=1))
(OUT / "s04_binary_strings.json").write_text(json.dumps(res, indent=1, sort_keys=True) + "\n")
