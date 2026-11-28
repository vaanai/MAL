"""Docs watch for the 2026-11-27T22:20:22Z pump redeploy: pump-public-docs main head vs the pinned 639b87fb... (the head at the 2026-11-20 review),
sha256 of the three IDLs vs tools/a3_redeploy_pins.json 'idl', and a structural IDL diff (pump IDL old pinned vs new, by instruction, account and event
name; per-name layout hash for events and accounts; instruction args and accounts) for the old (639b87fb) vs new (head) pump IDL.
Unauthenticated GitHub API / raw (no token). Saves the IDLs off-repo under /data2/mal/scratch/a3-repin-1128/docs. Run from this directory."""
import hashlib, json, urllib.request
from pathlib import Path
OLD = "639b87fb45c3adeffa6f7c4a1bf0cb85e392bf22"
D = Path("/data2/mal/scratch/a3-repin-1128/docs"); D.mkdir(parents=True, exist_ok=True)
H = {"User-Agent": "mal-lab-structure-check"}


def get(url):
    return urllib.request.urlopen(urllib.request.Request(url, headers=H), timeout=60).read()


api = "https://api.github.com/repos/pump-fun/pump-public-docs"
head = json.loads(get(api + "/commits/main"))
HEAD = head["sha"]
cmp_ = json.loads(get(f"{api}/compare/{OLD}...{HEAD}"))
res = {"main_sha": HEAD, "main_date": head["commit"]["committer"]["date"], "main_message": head["commit"]["message"].splitlines()[0], "compare_status": cmp_.get("status"), "ahead_by": cmp_.get("ahead_by"),
       "total_commits": cmp_.get("total_commits"), "commits": [(c["sha"][:7], c["commit"]["committer"]["date"], c["commit"]["message"].splitlines()[0]) for c in cmp_.get("commits", [])],
       "files_changed": [(f["filename"], f["status"], f.get("additions"), f.get("deletions")) for f in cmp_.get("files", [])], "idl": {}}
pins = json.load(open("../../../tools/a3_redeploy_pins.json"))["idl"]
res["pins_idl"] = pins
blobs = {}
for n in ("pump", "pump_amm", "pump_fees"):
    for ref, tag in ((HEAD, "main"), (OLD, "639b87f")):
        b = get(f"https://raw.githubusercontent.com/pump-fun/pump-public-docs/{ref}/idl/{n}.json")
        (D / f"{n}.{tag}.json").write_bytes(b); blobs[(n, tag)] = json.loads(b)
        res["idl"][f"{n}@{tag}"] = hashlib.sha256(b).hexdigest()
    res["idl"][f"{n}_main_equals_pin"] = res["idl"][f"{n}@main"] == pins[n]["sha256"]
    res["idl"][f"{n}_old_equals_pin"] = res["idl"][f"{n}@639b87f"] == pins[n]["sha256"]


def h(x): return hashlib.sha256(json.dumps(x, sort_keys=True).encode()).hexdigest()[:12]


def idx(lst, key="name"): return {e[key]: e for e in lst}


def struct_diff(old, new):
    out = {}
    for sect in ("instructions", "accounts", "events", "types", "errors"):
        o, n = idx(old.get(sect, []), "name") if sect != "errors" else idx(old.get(sect, []), "code"), idx(new.get(sect, []), "name") if sect != "errors" else idx(new.get(sect, []), "code")
        d = {"added": sorted(set(n) - set(o), key=str), "removed": sorted(set(o) - set(n), key=str), "changed": sorted(k for k in set(o) & set(n) if h(o[k]) != h(n[k]))}
        if sect in ("instructions",):
            d["changed_detail"] = {k: {"args_old": [(a["name"], a["type"]) for a in o[k].get("args", [])], "args_new": [(a["name"], a["type"]) for a in n[k].get("args", [])],
                                       "n_accounts_old": len(o[k].get("accounts", [])), "n_accounts_new": len(n[k].get("accounts", [])),
                                       "accounts_old": [a["name"] for a in o[k].get("accounts", [])], "accounts_new": [a["name"] for a in n[k].get("accounts", [])],
                                       "disc_old": o[k].get("discriminator"), "disc_new": n[k].get("discriminator")} for k in d["changed"]}
        if sect in ("events", "accounts"):
            d["disc_changed"] = sorted(k for k in set(o) & set(n) if o[k].get("discriminator") != n[k].get("discriminator"))
        out[sect] = d
    # event / account layouts live in types in this IDL dialect: compare the type definition of every event and account name
    ot, nt = idx(old.get("types", [])), idx(new.get("types", []))
    out["event_and_account_type_layouts_changed"] = sorted(k for k in set(ot) & set(nt) if h(ot[k]) != h(nt[k]) and (k in idx(old.get("events", [])) or k in idx(old.get("accounts", []))))
    out["event_type_layouts"] = {k: {"old": ot[k].get("type"), "new": nt[k].get("type")} for k in out["event_and_account_type_layouts_changed"]}
    out["metadata_old"] = old.get("metadata") or old.get("address"); out["metadata_new"] = new.get("metadata") or new.get("address")
    return out


res["pump_idl_structure_diff_old_vs_new"] = struct_diff(blobs[("pump", "639b87f")], blobs[("pump", "main")])
for n in ("pump_amm", "pump_fees"):
    res[f"{n}_idl_changed"] = res["idl"][f"{n}@main"] != res["idl"][f"{n}@639b87f"]
print(json.dumps(res, indent=1)[:12000])
Path("out").mkdir(exist_ok=True)
Path("out/s03_docs_idl.json").write_text(json.dumps(res, indent=1, sort_keys=True) + "\n")
