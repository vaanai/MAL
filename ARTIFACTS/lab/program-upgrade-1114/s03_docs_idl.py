"""Docs watch: pump-public-docs main head vs the pinned 639b87fb..., sha256 of the three IDLs vs tools/a3_redeploy_pins.json 'idl'.
Unauthenticated GitHub API / raw (no token). Saves the IDLs off-repo under /data2/mal/scratch/a3-repin-1114/docs. Run from this directory."""
import hashlib, json, urllib.request
from pathlib import Path
OLD = "639b87fb45c3adeffa6f7c4a1bf0cb85e392bf22"
D = Path("/data2/mal/scratch/a3-repin-1114/docs"); D.mkdir(parents=True, exist_ok=True)
H = {"User-Agent": "mal-lab-structure-check"}


def get(url):
    return urllib.request.urlopen(urllib.request.Request(url, headers=H), timeout=60).read()


api = "https://api.github.com/repos/pump-fun/pump-public-docs"
head = json.loads(get(api + "/commits/main"))
cmp_ = json.loads(get(f"{api}/compare/{OLD}...main"))
res = {"main_sha": head["sha"], "main_date": head["commit"]["committer"]["date"], "compare_status": cmp_.get("status"), "ahead_by": cmp_.get("ahead_by"),
       "total_commits": cmp_.get("total_commits"), "files_changed": [f["filename"] for f in cmp_.get("files", [])], "idl": {}}
pins = json.load(open("../../../tools/a3_redeploy_pins.json"))["idl"]
res["pins_idl"] = pins
for n in ("pump", "pump_amm", "pump_fees"):
    for ref, tag in (("main", "main"), (OLD, "639b87f")):
        b = get(f"https://raw.githubusercontent.com/pump-fun/pump-public-docs/{ref}/idl/{n}.json")
        (D / f"{n}.{tag}.json").write_bytes(b)
        res["idl"][f"{n}@{tag}"] = hashlib.sha256(b).hexdigest()
print(json.dumps(res, indent=1))
Path("out/s03_docs_idl.json").write_text(json.dumps(res, indent=1, sort_keys=True) + "\n")
