"""Step 10 (round 1): save what the review reads from GitHub, and the outputs that depend on it. GitHub only; NO Solana RPC.

Fetches, unauthenticated, from github.com / api.github.com / raw.githubusercontent.com (host-checked; any other host raises):
  - the commit JSON of pump-public-docs 639b87f (stats, file list, message, date) and its .diff
  - idl/pump.json, idl/pump_fees.json, idl/pump_amm.json at 2293f9a (before) and 639b87f (after)
Saves under out/docs/: the .diff, a slim commit JSON, the six IDL files gzipped, and a manifest of sha256 values (of the uncompressed bytes).
Then writes the structural IDL diffs (s00_idldiff.py) to out/s00_idldiff_{pump,fees,amm}.txt, the account / event offsets
(s00_offsets.py) to out/s00_offsets_*.txt, and the docs lines the review quotes to out/s10_docs_quotes.txt.
"""
import gzip
import hashlib
import json
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "out"
DOCS = OUT / "docs"
DOCS.mkdir(parents=True, exist_ok=True)
TMP = Path("/data/mal/program-upgrade-1014/raw/docs")
TMP.mkdir(parents=True, exist_ok=True)
ALLOWED = {"github.com", "api.github.com", "raw.githubusercontent.com"}
REPO = "pump-fun/pump-public-docs"
NEW, OLD = "639b87fb45c3adeffa6f7c4a1bf0cb85e392bf22", "2293f9a66c654e9fe82dc5e8f4618538f24bb35f"
# sha256 of the same URLs as fetched by hand (curl) in the first round, from that session's output
FIRST_ROUND = {
    "pump_new.json": "7616681ea557975f9f623ac211080f22a93060781eaf4765b0a488d980997207",
    "pump_old.json": "6a19bad7758fb822cee79a91575e51a7bd60391c670200408676643e00a79a6d",
    "fees_new.json": "6b9fa3eccc6e06273076a47b581f9fcb9e531bb7c3c88acbcd44db55a4002b29",
    "fees_old.json": "f111d2e5c9aa3d4e64d6a4e6b6f34300ccb1eaf6abc46834fe491836dc90aa74",
    "amm_new.json": "b7d8c57a4d9c4dd0109a9ab893052352333252d4eedced10ac091d4d66cab89b",
    "amm_old.json": "b7d8c57a4d9c4dd0109a9ab893052352333252d4eedced10ac091d4d66cab89b",
    "commit.json": "bec341407ea844aaab3597f22d99b3aa073728f0d05560060263514d948b376c",
}


def get(url: str) -> bytes:
    host = urllib.parse.urlparse(url).hostname
    if host not in ALLOWED:
        raise RuntimeError(f"refused host {host}: this script fetches GitHub only")
    req = urllib.request.Request(url, headers={"User-Agent": "mal-upgrade-review/1"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


sha = lambda b: hashlib.sha256(b).hexdigest()  # noqa: E731
manifest = {"repo": REPO, "new": NEW, "old": OLD, "fetched": {}}
commit_raw = get(f"https://api.github.com/repos/{REPO}/commits/{NEW}")
diff_raw = get(f"https://github.com/{REPO}/commit/{NEW}.diff")
(DOCS / "639b87f.diff").write_bytes(diff_raw)
c = json.loads(commit_raw)
slim = {"sha": c["sha"], "date": c["commit"]["author"]["date"], "message": c["commit"]["message"], "parents": [p["sha"] for p in c["parents"]], "stats": c["stats"],
        "n_files": len(c["files"]), "files": [{k: f[k] for k in ("filename", "status", "additions", "deletions")} for f in c["files"]]}
(DOCS / "639b87f.commit.slim.json").write_text(json.dumps(slim, indent=1) + "\n")
manifest["fetched"]["commit.json"] = {"sha256": sha(commit_raw), "bytes": len(commit_raw), "first_round_sha256": FIRST_ROUND["commit.json"], "equal_to_first_round": sha(commit_raw) == FIRST_ROUND["commit.json"]}
manifest["fetched"]["639b87f.diff"] = {"sha256": sha(diff_raw), "bytes": len(diff_raw), "saved_as": "out/docs/639b87f.diff"}
idl_paths = {}
for prog, fname in (("pump", "pump.json"), ("fees", "pump_fees.json"), ("amm", "pump_amm.json")):
    for tag, rev in (("old", OLD), ("new", NEW)):
        raw = get(f"https://raw.githubusercontent.com/{REPO}/{rev}/idl/{fname}")
        key = f"{prog}_{tag}.json"
        (TMP / key).write_bytes(raw)
        idl_paths[key] = TMP / key
        with gzip.open(DOCS / f"{key}.gz", "wb", compresslevel=9) as g:
            g.write(raw)
        manifest["fetched"][key] = {"url_path": f"{REPO}@{rev[:7]}/idl/{fname}", "sha256": sha(raw), "bytes": len(raw), "saved_as": f"out/docs/{key}.gz (gzip of the exact bytes)",
                                    "first_round_sha256": FIRST_ROUND[key], "equal_to_first_round": sha(raw) == FIRST_ROUND[key]}
s3 = Path("/data/mal/program-upgrade-1014/raw/idl")
manifest["s03_copies_equal"] = {k: (sha((s3 / f"pump_{k}.json").read_bytes()) == manifest["fetched"][f"pump_{k}.json"]["sha256"]) for k in ("old", "new") if (s3 / f"pump_{k}.json").exists()}

# structural IDL diffs and offsets
for prog in ("pump", "fees", "amm"):
    r = subprocess.run([sys.executable, str(HERE / "s00_idldiff.py"), str(idl_paths[f"{prog}_old.json"]), str(idl_paths[f"{prog}_new.json"])], capture_output=True, text=True, check=True)
    (OUT / f"s00_idldiff_{prog}.txt").write_text(r.stdout)
for name, prog, types in (("pump", "pump", ["BondingCurve", "Global", "CompletePumpAmmMigrationEvent"]), ("amm", "amm", ["Pool"])):
    r = subprocess.run([sys.executable, str(HERE / "s00_offsets.py"), str(idl_paths[f"{prog}_new.json"])] + types, capture_output=True, text=True, check=True)
    (OUT / f"s00_offsets_{name}.txt").write_text(r.stdout)

# the docs lines the review quotes: added lines of the diff that contain each phrase
PHRASES = [
    "Starting **October 13**",
    "Depth 2 is enabled on October 13",
    "up to 5 planned" ,
    "program's cap of 5",
    "A coin paired with a pump coin has the quote coin's depth plus one",
    "Q must not be a mayhem-mode coin",
    "The new coin cannot be in mayhem mode",
    "`@pump-fun/pump-sdk` 4.1.0",
    "`pump-rust-client` 0.4.1",
]
cur, found = None, {p: [] for p in PHRASES}
removed_phrase = {p: [] for p in PHRASES}
for line in diff_raw.decode("utf-8", "replace").splitlines():
    if line.startswith("diff --git"):
        cur = line.split(" b/", 1)[1]
    elif line.startswith("+") and not line.startswith("+++"):
        for p in PHRASES:
            if p in line:
                found[p].append((cur, line[1:]))
    elif line.startswith("-") and not line.startswith("---"):
        for p in PHRASES:
            if p in line:
                removed_phrase[p].append((cur, line[1:]))
    elif line.startswith(" "):  # unchanged context line shown by the diff
        for p in PHRASES:
            if p in line:
                found[p].append((f"{cur} [unchanged context line]", line[1:]))
with open(OUT / "s10_docs_quotes.txt", "w", encoding="utf-8") as fh:
    fh.write(f"Added ('+') lines, and unchanged context lines shown by the diff, of {REPO} commit {NEW[:7]} that contain each phrase; file, then the full line. '[none]' = no such line contains it (a context line only shows when it sits within 3 lines of a change).\n\n")
    for p in PHRASES:
        fh.write(f"### {p}\n")
        if not found[p]:
            fh.write("[none]\n")
            if removed_phrase[p]:
                fh.write(f"(removed lines containing it: {len(removed_phrase[p])}; first: {removed_phrase[p][0][0]}: {removed_phrase[p][0][1][:300]})\n")
        for f, l in found[p]:
            fh.write(f"{f}: {l}\n")
        fh.write("\n")
manifest["quote_phrase_added_line_counts"] = {p: len(v) for p, v in found.items()}
manifest["n_github_requests_this_script"] = 2 + 6
(OUT / "s10_docs_manifest.json").write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n")
print(json.dumps({k: v for k, v in manifest.items() if k != "fetched"}, indent=1))
print({k: (v.get("equal_to_first_round"), v["sha256"][:12]) for k, v in manifest["fetched"].items()})
print(slim["stats"], slim["n_files"], slim["date"])
