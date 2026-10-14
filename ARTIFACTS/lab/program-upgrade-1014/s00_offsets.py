import json
import sys

j = json.load(open(sys.argv[1]))
types = {x["name"]: x for x in j["types"]}
PRIM = {"u8": 1, "i8": 1, "bool": 1, "u16": 2, "i16": 2, "u32": 4, "i32": 4, "u64": 8, "i64": 8, "u128": 16, "i128": 16, "pubkey": 32}


def tsize(t):
    if isinstance(t, str):
        return PRIM[t]
    if "array" in t:
        return tsize(t["array"][0]) * t["array"][1]
    if "defined" in t:
        return sum(tsize(f["type"]) for f in types[t["defined"]["name"]]["type"]["fields"])
    raise ValueError(t)


for name in sys.argv[2:]:
    off = 8
    print("==", name)
    disc = [a for a in j["accounts"] if a["name"] == name]
    print("discriminator", disc[0]["discriminator"] if disc else None)
    for f in types[name]["type"]["fields"]:
        s = tsize(f["type"])
        print(f"  {off:5d} {s:4d} {f['name']} {f['type']}")
        off += s
    print("  total", off)
