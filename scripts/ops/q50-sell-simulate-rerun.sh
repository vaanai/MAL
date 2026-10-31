#!/bin/sh
# q50-sell-simulate-rerun.sh [REF] : the Q50 C7 sell-simulate rerun (QP-PRERULING-1026-n2-n4 N4 and addendum O1). FILL:simulate_ref.
# POSIX sh. Public RPC only (api.mainnet-beta.solana.com), 0 Helius credits, ~1 h. Nothing is signed or sent. NOT run by its author; the manager submits it.
#   tool       tools/arena_sell_simulate.py --holder --priority default   (the book's own 55,000 / 55,000 / 150,000; NEVER p155)
#   Token leg  FIXED: mint 12rgUrX6g3nd2G6oXMz657mys5DFfyoLWm8YTL57pump, pool 4JfV6QHCFTWUdQJ6MFj72r1JmJybJowHNYWDs6MUovsX. QP O1: its oldest signature is
#              2026-09-25T15:08:46Z (September; C7 s0 test met). This script re-checks that and decodes the oldest tx as the pool create.
#   Token-2022 leg  SELECTED here from the creation fact only (QP O1: FQ75g9yW... is unknown or too late; a DIFFERENT mint is required):
#              candidates = mints in the Sept audit tape `migrations` (2026-09-20 hours, the exploration day, before 2026-09-25T06Z), in tape order (slot, tx_index,
#              event_index), nothing else read; the first one whose mint is owned by Token-2022 on chain AND whose canonical pool's oldest signature (public RPC,
#              paged, at most 200 pages of 1000) has blockTime in September 2026 AND decodes as the pool create. No outcome, V, price or H5 rule is read or used.
#   Recorded per mint: mint, pool, s0 slot and blockTime with source, token_program, full_sell_ok, control_reverts_at_base_close, instruction decode (shape),
#   record sha256. No proceeds, min_out or balances are printed. Pass = full_sell_ok True AND control_reverts_at_base_close True, both programs.
# Seals: reads only mint, slot, tx_index, event_index from the Sept audit tape migrations files of 2026-09-20 (creation facts); not arena tape, not October, not DBC,
# not HV4/Q50 output. Check docs/HOLDOUT_LEDGER.md for the 2026-09-20 block before submitting; this script prices nothing.
# Resources for miscusi_job_submit: memory <= 4 GB, one job on the host, network = public Solana RPC only.
set -u
REF=${1:-0526d5cd7ab6e1bfbc05e295185c37f7c31d72f1}   # the tool blob is identical at 0526d5cd and main (QP N4 item 4); pass X once it exists
REPO=${MAL_REPO:-/home/claude/.miscusi/repos/vaanai/MAL}
PY=${MAL_PY:-/data/mal/venv/bin/python}                  # needs solders
DPY=${MAL_DUCKDB_PY:-/data/mal/c1nf-parity-venv/bin/python}   # needs duckdb (tape candidate list only)
RPC=https://api.mainnet-beta.solana.com
OUT=${SIM_OUT_DIR:-/data2/mal/scratch/l1g/q50-sell-simulate-rerun}
TOKEN_MINT=12rgUrX6g3nd2G6oXMz657mys5DFfyoLWm8YTL57pump
TOKEN_POOL=4JfV6QHCFTWUdQJ6MFj72r1JmJybJowHNYWDs6MUovsX
TAPE=/data/mal/audit-1008/tape/migrations
die() { echo "REFUSED: $*" >&2; exit 1; }
case "$REF" in *[!0-9a-f]*) die "REF must be lowercase hex" ;; esac
[ "${#REF}" -eq 40 ] || die "REF must be a full 40-hex sha"
[ -x "$PY" ] && [ -x "$DPY" ] || die "python missing"
git -C "$REPO" cat-file -e "$REF^{commit}" 2>/dev/null || git -C "$REPO" fetch -q origin 2>/dev/null
git -C "$REPO" cat-file -e "$REF^{commit}" 2>/dev/null || die "ref not in $REPO"
WORK=$(mktemp -d "${TMPDIR:-/tmp}/q50sim.XXXXXX") || die "mktemp"
trap 'rm -rf "$WORK"' EXIT INT TERM
git -C "$REPO" archive "$REF" | tar -x -C "$WORK" || die "export failed"
mkdir -p "$OUT" || die "out dir"
echo "q50-sell-simulate-rerun: $(date -u +%Y-%m-%dT%H:%M:%SZ) ref=$REF tool sha256 $(sha256sum "$WORK/tools/arena_sell_simulate.py" | cut -d' ' -f1) rpc=public(0 Helius) priority=default"

# --- step 1: candidate list (creation facts only: mint, slot, tx_index, event_index), 2026-09-20 hours -------------------------------------------
cat > "$WORK/cand.py" <<'PYEOF'
import sys, glob, duckdb
files = sorted(glob.glob(sys.argv[1] + "/2026-09-20T??.parquet"))
if not files:
    raise SystemExit("no tape files")
rows = duckdb.sql("select mint from read_parquet(%s) where mint is not null order by slot, tx_index, event_index limit 400" % repr(files)).fetchall()
seen = []
for (m,) in rows:
    if m not in seen:
        seen.append(m)
print("\n".join(seen))
PYEOF
"$DPY" -I "$WORK/cand.py" "$TAPE" > "$WORK/candidates.txt" || die "candidate list failed"
echo "q50-sell-simulate-rerun: candidates=$(wc -l < "$WORK/candidates.txt") (creation order, 2026-09-20 migrations; mint column only)"

# --- step 2: creation-fact check (public RPC) and Token-2022 selection ---------------------------------------------------------------------------
cat > "$WORK/pick.py" <<'PYEOF'
import sys, json, time, urllib.request
work, token_mint, token_pool, cand_file, out = sys.argv[1:6]
sys.path.insert(0, work)
from solders.pubkey import Pubkey
from tools import pumpswap_tx as tx
RPC = "https://api.mainnet-beta.solana.com"
SEPT = (1788220800, 1790812800)   # [2026-09-01T00:00:00Z, 2026-10-01T00:00:00Z)
T22 = str(tx.TOKEN_2022_PROGRAM); T1 = str(tx.TOKEN_PROGRAM)
last = [0.0]; calls = []
def rpc(method, params):
    d = 1.0 - (time.time() - last[0])
    if d > 0: time.sleep(d)
    last[0] = time.time()
    req = urllib.request.Request(RPC, json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode(), {"Content-Type": "application/json"})
    for i in range(6):
        try:
            r = json.load(urllib.request.urlopen(req, timeout=30))
            if "error" in r: raise RuntimeError(str(r["error"])[:120])
            calls.append(method); return r["result"]
        except Exception:
            time.sleep(2 * (i + 1))
    raise SystemExit("rpc failed: " + method)
def oldest(pool, cap=200):
    before = None; pages = 0; last_sig = None
    while pages < cap:
        p = {"limit": 1000}
        if before: p["before"] = before
        s = rpc("getSignaturesForAddress", [pool, p]); pages += 1
        if not s: break
        last_sig = s[-1]; before = last_sig["signature"]
        if len(s) < 1000: return last_sig, pages, True
    return last_sig, pages, last_sig is None
def decode_create(sig):
    t = rpc("getTransaction", [sig, {"maxSupportedTransactionVersion": 0, "encoding": "json"}])
    logs = (t or {}).get("meta", {}).get("logMessages") or []
    for want in ("Instruction: CreatePool", "Instruction: Migrate", "Instruction: create_pool", "Instruction: migrate"):
        if any(want in l for l in logs): return want
    return None
def creation_fact(mint, pool):
    sig, pages, exhausted = oldest(pool)
    if sig is None or not exhausted:
        return {"mint": mint, "pool": pool, "status": "undetermined", "pages": pages}
    bt = sig.get("blockTime")
    rec = {"mint": mint, "pool": pool, "s0_slot": sig["slot"], "s0_blockTime": bt, "s0_signature": sig["signature"], "source": "public RPC getSignaturesForAddress oldest (pages=%d)" % pages}
    rec["in_sept_2026"] = bt is not None and SEPT[0] <= bt < SEPT[1]
    rec["decoded_as"] = decode_create(sig["signature"]) if rec["in_sept_2026"] else None
    rec["status"] = "ok" if rec["in_sept_2026"] and rec["decoded_as"] else "not_september_or_not_create"
    return rec
res = {"token_leg": creation_fact(token_mint, token_pool), "token2022_leg": None, "token2022_tried": []}
if res["token_leg"]["status"] != "ok":
    json.dump(res, open(out, "w"), sort_keys=True); print(json.dumps(res["token_leg"])); raise SystemExit(3)
for mint in [l.strip() for l in open(cand_file) if l.strip()][:60]:
    info = rpc("getAccountInfo", [mint, {"encoding": "base64", "dataSlice": {"offset": 0, "length": 0}}])
    owner = ((info or {}).get("value") or {}).get("owner")
    if owner != T22:
        res["token2022_tried"].append({"mint": mint, "owner": "token" if owner == T1 else str(owner)}); continue
    pool = str(tx.canonical_pool(Pubkey.from_string(mint)))
    f = creation_fact(mint, pool); res["token2022_tried"].append({"mint": mint, "status": f["status"]})
    if f["status"] == "ok":
        res["token2022_leg"] = f; break
json.dump(res, open(out, "w"), sort_keys=True)
print(json.dumps({"token_leg": res["token_leg"], "token2022_leg": res["token2022_leg"], "tried": len(res["token2022_tried"])}))
raise SystemExit(0 if res["token2022_leg"] else 3)
PYEOF
"$PY" -I "$WORK/pick.py" "$WORK" "$TOKEN_MINT" "$TOKEN_POOL" "$WORK/candidates.txt" "$OUT/selection.json"
rc=$?
echo "q50-sell-simulate-rerun: selection rc=$rc (record $OUT/selection.json sha256 $(sha256sum "$OUT/selection.json" 2>/dev/null | cut -d' ' -f1))"
[ "$rc" -eq 0 ] || die "no September Token-2022 pool found among the candidates (or the Token leg failed C7): STOP, tell the manager"
T22_MINT=$("$PY" -I -c 'import json,sys;print(json.load(open(sys.argv[1]))["token2022_leg"]["mint"])' "$OUT/selection.json")
T22_POOL=$("$PY" -I -c 'import json,sys;print(json.load(open(sys.argv[1]))["token2022_leg"]["pool"])' "$OUT/selection.json")

# --- step 3: the holder simulate, default priority, both programs ------------------------------------------------------------------------------
cd "$WORK" || die "cd"
for leg in "token:$TOKEN_MINT:$TOKEN_POOL" "token2022:$T22_MINT:$T22_POOL"; do
  name=${leg%%:*}; rest=${leg#*:}; mint=${rest%%:*}; pool=${rest#*:}
  "$PY" -m tools.arena_sell_simulate --mint "$mint" --pool "$pool" --holder --priority default --rpc "$RPC" --out "$OUT/sell-simulate-$name.json" > "$OUT/$name.stdout" || die "simulate failed for $name"
  # print only the allowed fields: no proceeds, min_out or balances
  "$PY" -I -c '
import json, sys
r = json.load(open(sys.argv[1])); v = r["verdict"]; full = r["runs"]["full_balance"]
print("RESULT leg=%s mint=%s pool=%s token_program=%s priority=%s full_sell_ok=%s control_reverts_at_base_close=%s shape=%s" % (sys.argv[3], r["mint"], r["pool"], r["token_program"], r["priority"].get("profile", r["priority"]), v["full_sell_ok"], v["control_reverts_at_base_close"], json.dumps(full["shape"].get("instructions", full["shape"]), sort_keys=True)[:600]))
' "$OUT/sell-simulate-$name.json" x "$name"
  echo "RECORD $name sha256=$(sed -n 's/^\([0-9a-f]*\) .*/\1/p' "$OUT/sell-simulate-$name.json.sha256")"
done
"$PY" -I -c '
import json, sys
ok = True
for n in ("token", "token2022"):
    v = json.load(open("%s/sell-simulate-%s.json" % (sys.argv[1], n)))["verdict"]
    ok = ok and v["full_sell_ok"] is True and v["control_reverts_at_base_close"] is True
print("q50-sell-simulate-rerun VERDICT:", "PASS (both programs)" if ok else "NOT_PASS (investigate; no retry with another mint chosen by outcome)")
sys.exit(0 if ok else 4)
' "$OUT"
