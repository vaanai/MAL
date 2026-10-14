"""Step 12 (round 1, OFFLINE, no RPC): boundary-aware version of the s03 binary-string check, on the saved pump programdata
(/data/mal/program-upgrade-1014/raw/pump_programdata_new.bin, the 10,485,760-byte account read in round 0; its sha256 must equal the pin).

s03 used a substring test; 8 IDL instruction names are prefixes of others (Buy, Sell, Create, Migrate, Initialize, ClaimCashback, CollectCreatorFee,
DistributeCreatorFees). Here an instruction name counts only when "Instruction: <Name>" is followed by a byte that cannot continue an
identifier (not [A-Za-z0-9_]) or by the next piece "Instruction: " (the ELF stores these Rust string pieces back to back, e.g. "...Instruction: BuyV2Instruction: BuyExactQuoteInV2..."). Error messages: Rust string pieces are stored back to back with no separator, so for the one old message that
is a prefix of its new text (6105) the count of old-text occurrences NOT followed by the new text's continuation is reported."""
import hashlib
import json
import re
from pathlib import Path

from common import OUT, REPO

BIN = Path("/data/mal/program-upgrade-1014/raw/pump_programdata_new.bin")
IDL = {k: json.load(open(f"/data/mal/program-upgrade-1014/raw/idl/pump_{k}.json")) for k in ("old", "new")}
pins = json.load(open(REPO / "tools" / "pump_structure_pins.json"))["programs"]["pump"]
d = BIN.read_bytes()
res = dict(bin_sha256=hashlib.sha256(d).hexdigest(), equals_pin_sha256=hashlib.sha256(d).hexdigest() == pins["sha256"], bin_len=len(d))


def camel(s):
    return "".join(p.capitalize() for p in s.split("_"))


names = [camel(i["name"]) for i in IDL["new"]["instructions"]]
bare = {}
for n in names:
    pat = re.compile(rb"Instruction: " + re.escape(n.encode()) + rb"(?=Instruction: |[^A-Za-z0-9_]|\Z)")
    bare[n] = len(pat.findall(d))
res["unconfirmed_name_followed_by"] = {
    n: [d[mm.end() : mm.end() + 40].decode("latin-1") for mm in list(re.finditer(rb"Instruction: " + re.escape(n.encode()), d))[:6]]
    for n, v in bare.items() if v == 0
}  # what follows each "Instruction: <Name>" where no piece boundary could be confirmed (up to 6 occurrences, 40 bytes)
res["n_idl_instructions"] = len(names)
res["instructions_with_bare_occurrence"] = sum(1 for v in bare.values() if v > 0)
res["instructions_without_bare_occurrence"] = [n for n, v in bare.items() if v == 0]
prefixes = sorted(n for n in names if any(o != n and o.startswith(n) for o in names))
res["prefix_names_and_bare_counts"] = {n: bare[n] for n in prefixes}
errs = {e["code"]: e.get("msg") or e["name"] for e in IDL["new"]["errors"]}
old_errs = {e["code"]: e.get("msg") or e["name"] for e in IDL["old"]["errors"]}
changed = [k for k in errs if old_errs.get(k) != errs[k]]
msgs = {}
for k in changed:
    new_t = errs[k].encode()
    old_t = old_errs.get(k, "").encode()
    row = dict(new_text_count=d.count(new_t))
    if old_t:
        row["old_text_count"] = d.count(old_t)
        row["old_text_occurrences_not_part_of_new_text"] = d.count(old_t) - d.count(new_t) if new_t.startswith(old_t) else d.count(old_t)
        row["old_is_prefix_of_new"] = new_t.startswith(old_t)
    msgs[str(k)] = row
res["changed_error_messages"] = msgs
res["n_idl_error_messages_new_text_present"] = sum(1 for t in errs.values() if t.encode() in d)
res["n_idl_errors_new"] = len(errs)
print(json.dumps(res, indent=1))
(OUT / "s12_binary_names.json").write_text(json.dumps(res, indent=1, sort_keys=True) + "\n")
