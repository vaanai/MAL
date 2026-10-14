"""Structural diff of two IDL JSON files (docs repo copies), plus Global / BondingCurve account sizes.
usage: idldiff.py OLD.json NEW.json
Counts and code facts only. No network."""
import json
import sys

old = json.load(open(sys.argv[1]))
new = json.load(open(sys.argv[2]))


def idx(j, k, key="name"):
    return {x[key]: x for x in j.get(k, [])}


for k in ("instructions", "accounts", "events", "types", "errors"):
    key = "code" if k == "errors" else "name"
    o, n = idx(old, k, key), idx(new, k, key)
    print(k, "old", len(o), "new", len(n), "added", sorted(set(n) - set(o)), "removed", sorted(set(o) - set(n)))
    ch_nodoc, ch_any = [], []
    for name in set(o) & set(n):
        a, b = dict(o[name]), dict(n[name])
        if json.dumps(a, sort_keys=True) != json.dumps(b, sort_keys=True):
            ch_any.append(name)
        a.pop("docs", None)
        b.pop("docs", None)
        if json.dumps(a, sort_keys=True) != json.dumps(b, sort_keys=True):
            ch_nodoc.append(name)
    print("   changed excluding docs:", sorted(ch_nodoc))
    print("   changed including docs:", sorted(ch_any))
print("address", old.get("address"), new.get("address"))
print("metadata", old.get("metadata"), new.get("metadata"))

PRIM = {"u8": 1, "i8": 1, "bool": 1, "u16": 2, "i16": 2, "u32": 4, "i32": 4, "u64": 8, "i64": 8, "u128": 16, "i128": 16, "pubkey": 32, "f64": 8, "f32": 4}


def tsize(t, types):
    if isinstance(t, str):
        return PRIM[t]
    if "array" in t:
        return tsize(t["array"][0], types) * t["array"][1]
    if "defined" in t:
        return structsize(types[t["defined"]["name"]], types)
    raise ValueError(t)


def structsize(d, types):
    k = d["type"]
    if k["kind"] == "struct":
        return sum(tsize(f["type"], types) for f in k["fields"])
    raise ValueError(k)


for nm, j in (("old", old), ("new", new)):
    types = idx(j, "types")
    for a in ("Global", "BondingCurve"):
        try:
            flds = [f["name"] for f in types[a]["type"]["fields"]]
            print(nm, a, "8 + fields =", 8 + structsize(types[a], types), "n_fields", len(flds), "last fields", flds[-6:])
        except Exception as e:  # noqa: BLE001
            print(nm, a, "ERR", e)
