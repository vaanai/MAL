#!/usr/bin/env python3
"""EXP-025 (C1-NF Part 1): the look driver, adapter side (section 4, section 10 P3 / P6, section 11.2).

    python3 tools/exp025_look_driver.py look --look 1 --decoder-blobs J    # after the DEC-016 FINAL and the look's last hour
    python3 tools/exp025_look_driver.py e0                                  # exploration day 2026-09-20 only, into a scratch O

It materialises a look's allowlisted October hours into the look's fixed O with tools/exp025_adapter.py and writes
<O>/look_assembly.json in the schema tools/exp025_look.py validates (load_assembly):

    {"schema": "exp025_look_assembly_v1", "look": "look1", "manifests": [<O>/assembly/manifest-<block>.json, ...],
     "r2": <october_vmap's r2 dict>, "append_tokens": <append_tokens' dict>, ...counts and blobs...}

The look's O, its tape directory, the block directories, the FINAL marker and ledger, the exploration hunt-shared and the E0
scratch O are fixed in code (exp025_read.LOOKS[look]["O"], exp025_adapter.LOOK_TAPE / SOURCES / EXPLORATION_SH). The CLI
takes the look number and the decoder-blob record only: no path override, so it cannot assemble a second O for a look.

Order (`look`). Nothing October is opened before every check that needs no October file has passed; any refusal exits 2.
  1. SEAL: exp025_read.LookGuard (the FINAL marker exists, the FINAL ledger has an entry, the look's last allowlisted hour has
     ended). Nothing else, O included, is touched before it passes. The adapter repeats the FINAL check per hour
     (cap_pick_gate_replay.require_final, the ledger's EXP-012 FINAL marker row) before it opens a source file.
  2. R13: exp025_read.check_decoder_blobs on --decoder-blobs (forward-1002ev and walk 2 decoded by one 40-hex blob).
  3. R12: the adapter's allowlist for the look must equal exp025_read.LOOKS[look]["allow"] (sources mapped by
     exp025_look.BLOCK_OF_SOURCE), each hour passes LookGuard.check_hour, the tape directory is O/tape, the look is not spent
     (LOCK, the runner's own test in exp025_look._run_look: O/READ.lock exists OR LOOK_READS holds a `lock` event for the
     look; read from exp025_read.LOOK_LEDGER at call time), and O/tape holds no file of an hour outside the allowlist.
  4. One adapter convert per block, in allowlist order: forward-1002 [10-02T15, 10-09T00) without event V (no V there, P6
     item 2); forward-1002ev [10-09T00, 10-16T01) and walk 2 (Look 1 [10-16T01, 10-17T02), Look 2 to 10-24T02) with event V,
     so every V-covered trades hour gets its v_ok sidecar. A walk-2 hour is read only from the pinned block directory
     (/data/mal/blocks/forward-1016), after the FINAL and after it has closed; a missing hour is in the manifest's
     `missing` and the runner counts it unwalked (R1). Before the first convert, any look_assembly.json an earlier assembly left
     in O is removed, so a re-assembly that refuses or crashes part-way leaves no record that no longer describes the tape.
     v_ok merge: the runner reads `v_ok` as a column of O/tape/trades/<hour>.parquet (exp025_look.v_flag_missing,
     r3_coverage, exp025_read.price_rows); the adapter writes it only to the sidecar O/tape/v_ok/<hour>.parquet. For every
     trades file of every event-V manifest, merge_v_ok rewrites the trades file as `t.* + s.v_ok` (POSITIONAL JOIN, one
     thread, insertion order kept), refused NOT_READY unless both files have the same row count and every row's
     (slot, tx_index, event_index) is equal. So the trades files carry one column beyond ref/convert.py's TR_COLS
     (section 11.2); pinned 11_passA and the det ledger select named columns, so their output is unchanged. Each manifest
     is then kept as <O>/assembly/manifest-<block>.json, with files[i]['sha256_with_v_ok'] for the merged trades files
     (files[i]['sha256'] stays convert's). Then the tape must hold exactly the files this assembly's manifests list
     (each manifest's kind/hour files, plus v_ok/<hour> for the trades files of event-V manifests), NOT_READY otherwise:
     convert never deletes an earlier assembly's file for an allowlisted hour whose source file is now missing, which the
     runner would count unwalked (R1) while pass A still read it.
  4b. History (section 11.2 items 2 and 3; always in look mode): copy_p2_history copies P2's 36 out/mout files and 36 wl days
     from P2's working directory (P2_SRC) into <O>/out/mout and <O>/wl, each file's sha256 checked against Amendment 4's
     mout_sha256.txt / wl_sha256.txt (whose own sha256 is pinned in P2_HISTORY); a file already there with the right hash is
     kept; leftover *.tmp and every non-P2 wl day are removed. build_wl_days then deletes and rebuilds every October wl day
     (OCT_WL_FIRST to the day before LOOKS[look]['end']) with the pinned ledger/01_wallet_daily_det.py (sha256 checked
     against SHA256SUMS) on <O>/tape/trades, after checking every input trades file against this assembly's manifests
     (sha256_with_v_ok for merged files). The provenance (the script's sha256; per day the input files' sha256s, the
     wallet-row count and the output's sha256) goes into the record's history.wl_provenance. NOT_READY on any mismatch.
  5. V0 rows: V at each pool's first print over the same V0 files the adapter's convert used (look_trade_files minus the
     manifests' v0_bad), via the adapter's collect_v0. In a look, every event-V manifest must list the same v0_bad files and
     have v0_files equal to the driver's V0 file count (NOT_READY otherwise: tokens.v0_lamports would not be the V0 the tape's
     quote_reserve used, section 2.4). Completed mints (mint, is_mayhem) from the tape's migrations and
     creates; october_vmap gives the vmap and the R2 record. build_shared (pinned build_shared.py) builds the October
     tokens.parquet and bars_1m in <O>/october-shared with that vmap. The completes are then re-read from the built
     tokens.parquet (grad_src = 'complete', as the adapter's E0 does); if they differ, the vmap is rebuilt from them and the
     build re-run once (refused if still different), so R2 is always over the pinned tokens' completes.
  6. <O>/hunt-shared/tokens.parquet = append_tokens(exploration tokens.parquet, sha256 pinned, then the October rows);
     <O>/hunt-shared/bars_1m/<block> = symlinks to the exploration block directories and the October ones (a block name in
     both refuses at a look).
  7. check_look_history (section 11.2 items 2 and 3) refuses NOT_READY unless <O>/out/mout holds exactly P2's 36
     mout files with Amendment 4's mout_sha256.txt hashes (12_passC reads out/mout/*.parquet) and <O>/wl holds exactly P2's
     36 days per wl_sha256.txt plus one file per October date from 2026-10-02 to the day before LOOKS[look]['end']
     (11_passA lists O/wl), each October day with the sha256 step 4b's ledger run wrote. Counts and sha256s go into the record.
  8. <O>/look_assembly.json, written atomically, then exp025_look.load_assembly(look, O) on it (the runner's own check).

Not here: the R1 cross-check record, P7, and any October pass-A output in out/mout (the runner writes it inside the lock;
a non-P2 file in out/mout at assembly time refuses NOT_READY). The driver prices, labels and picks nothing; stdout carries counts only (no pool, mint, V0, pick, label, fill,
exit or P&L).

`e0` runs steps 4 to 6 and 8 in exploration mode (adapter convert with look=None: exploration hours only, sealed paths refused)
on the adapter E0's own inputs (exp025_adapter.E0_SRC / E0_BLOCK, day E0_DAY) into E0_O, then shows that
exp025_look.load_assembly accepts the record and that exp025_look.hour_status refuses it R12 (its hours are not a look's).
The E0 day has no virtual_quote_reserves (it predates event V), so its vmap is empty and every v_ok is false. The E0
record also carries october_tokens_sha256 and v_ok_missing (the E0 tape's trades files with no v_ok column, by
parquet_schema); `pass` needs v_ok_missing == 0. The E0 also runs step 4b with no October day (P2's history copied into
E0_O, check_look_history on it) and runs build_wl_days on the E0 tape's own E0_DAY into <E0_O>/assembly/e0-wl (never wl/):
`pass` needs that day's sha256 to equal P2's wl_sha256.txt entry for E0_DAY.
Run with the audit venv (/data/mal/audit-1008/venv/bin/python: duckdb, pyarrow, numpy).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

TOOLS = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(TOOLS)
for _p in (REPO, TOOLS):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import exp025_read as R  # noqa: E402
import exp025_look as L  # noqa: E402
from tools import exp025_adapter as A  # noqa: E402

E0_O = "/data/mal/exp025/e0/look-driver-0920"     # the E0's scratch O (never a look's O)
E0_RECORD = "look_driver_e0_2026-09-20.json"      # written into E0_O; not a p3_e0 / p4_e0 record (R13 reads only those)
THREADS = 2
# Amendment 4 (section 11.2 items 2-3): P2's sha256 manifests (the sha256sum-checked backed-up copy) and their pinned sha256
P2_HISTORY = {
    "mout": ("/data/mal/c1nf/p2/mout_sha256.txt", "64239ce8d5f1e7e21bdc228f83c49fcab3b4847ff762570b03bd77eb718d913c"),
    "wl": ("/data/mal/c1nf/p2/wl_sha256.txt", "026310101a60f8a6d7c1c34f4c38374ca5015d6d7f42d907adaee2c0cd2d6431"),
}
P2_DAYS = 36
P2_SRC = "/data/mal/hunt-1008/c1nf-p2"   # P2's working directory (out/mout, wl), kept for look assembly (Amendment 4)
LEDGER_REL = "ledger/01_wallet_daily_det.py"   # the pinned deterministic wallet ledger (ARTIFACTS/exp025, SHA256SUMS)
OCT_WL_FIRST = "2026-10-02"   # the first October tape date (forward-1002 from 10-02T15)
SRC_OF_BLOCK = {b: s for s, b in L.BLOCK_OF_SOURCE.items()}


def _lst(fs) -> str:
    return "[" + ",".join(A._q(str(f)) for f in fs) + "]"


def _write_json(p: str, obj) -> None:
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w") as f:
        f.write(json.dumps(obj, indent=1, sort_keys=True) + "\n")
    os.replace(tmp, p)


# ----------------------------------------------------------------------------------------------------------------- the plan
def look_plan(look: int) -> list:
    """[(block, src dir, hours, event_v)] per adapter block of the look, in allowlist order. R12 if the adapter's allowlist is
    not the read tool's, or a block straddles V_COVER_START."""
    mine = {h: L.BLOCK_OF_SOURCE[src] for src, h in R.allowlisted_hours(look)}
    if mine != A.look_allowlist(f"look{look}"):
        raise R.Refusal("R12", f"the adapter's look{look} allowlist differs from exp025_read.LOOKS[{look}]['allow']")
    plan = []
    for blk in dict.fromkeys(mine.values()):
        hours = sorted(h for h, b in mine.items() if b == blk)
        ev = {h >= A.V_COVER_START for h in hours}
        if len(ev) != 1:
            raise R.Refusal("R12", f"{blk} straddles V_COVER_START {A.V_COVER_START}")
        plan.append((blk, A.SOURCES[blk], hours, ev.pop()))
    return plan


def stray_tape_files(look: int, tape: str) -> list:
    """Files in tape/{trades,creates,migrations,v_ok} whose hour is outside the look's allowlist (or not an hour)."""
    allow = {h for _, h in R.allowlisted_hours(look)}
    out = []
    for k in A.KINDS + ("v_ok",):
        d = os.path.join(tape, k)
        for n in (sorted(os.listdir(d)) if os.path.isdir(d) else ()):
            if n.endswith(".parquet") and n[:-8] not in allow:
                out.append(f"{k}/{n}")
    return out


# ----------------------------------------------------------------------------------------------------------------- v_ok, history
def merge_v_ok(tape: str, hour: str) -> str:
    """Join the sidecar tape/v_ok/<hour>.parquet into tape/trades/<hour>.parquet as its last column (the runner reads v_ok
    from the trades file). The adapter writes the sidecar in the trades file's row order, so this is a POSITIONAL JOIN,
    refused NOT_READY unless both files have the same row count and every row's (slot, tx_index, event_index) is equal.
    One thread, insertion order kept, written to .tmp then os.replace: the trades rows and their order are unchanged.
    Returns the merged file's sha256."""
    t = os.path.join(tape, "trades", f"{hour}.parquet")
    s = os.path.join(tape, "v_ok", f"{hour}.parquet")
    for p in (t, s):
        if not os.path.isfile(p):
            raise R.Refusal("NOT_READY", f"{p} missing: no v_ok to merge for {hour}")
    tq, sq, tmp = A._q(t), A._q(s), t + ".tmp"
    con = A._connect(threads=1, tmp=os.path.join(tape, "tmp_duck"))   # threads=1, preserve_insertion_order=true
    try:
        if con.execute(f"SELECT count(*) FROM parquet_schema({tq}) WHERE name = 'v_ok'").fetchone()[0]:
            raise R.Refusal("NOT_READY", f"{t} already has v_ok: convert did not rewrite it")
        nt = con.execute(f"SELECT count(*) FROM read_parquet({tq})").fetchone()[0]
        ns = con.execute(f"SELECT count(*) FROM read_parquet({sq})").fetchone()[0]
        mis = con.execute(f"""SELECT count(*) FILTER (WHERE t.slot IS DISTINCT FROM s.slot OR t.tx_index IS DISTINCT FROM s.tx_index
                                                      OR t.event_index IS DISTINCT FROM s.event_index)
                               FROM read_parquet({tq}) t POSITIONAL JOIN read_parquet({sq}) s""").fetchone()[0]
        if nt != ns or mis:
            raise R.Refusal("NOT_READY", f"{hour}: the v_ok sidecar does not align with the trades file "
                                         f"({nt} vs {ns} rows, {mis} misaligned)")
        con.execute(f"COPY (SELECT t.*, s.v_ok FROM read_parquet({tq}) t POSITIONAL JOIN read_parquet({sq}) s) "
                    f"TO {A._q(tmp)} (FORMAT parquet, COMPRESSION zstd)")
    finally:
        con.close()
    os.replace(tmp, t)
    return A.sha256_file(t)


def look_october_wl_days(look: int) -> list:
    """The October wl days a look needs: OCT_WL_FIRST to the day before LOOKS[look]['end']."""
    return [R.date_str(x) for x in range(R.ep(f"{OCT_WL_FIRST}T00"), R.ep(R.LOOKS[look]["end"]), 86400)]


def _p2_manifest(kind: str) -> tuple:
    path, pinned = P2_HISTORY[kind]
    if not os.path.isfile(path) or A.sha256_file(path) != pinned:
        raise R.Refusal("NOT_READY", f"P2's {kind} manifest {path} is missing or is not Amendment 4's ({pinned[:8]})")
    rows = {}
    with open(path) as fh:
        for line in fh:
            if line.strip():
                h, n = line.split(None, 1)
                rows[os.path.basename(n.strip().lstrip("*"))] = h
    if len(rows) != P2_DAYS:
        raise R.Refusal("NOT_READY", f"P2's {kind} manifest lists {len(rows)} files, not {P2_DAYS}")
    return path, pinned, rows


def _hist_dirs(root: str) -> dict:
    """Where pass C (out/mout) and pass A (wl) read the history, under P2's working directory or a look's O."""
    return {"mout": os.path.join(root, "out", "mout"), "wl": os.path.join(root, "wl")}


def copy_p2_history(O: str, src: str = P2_SRC) -> dict:
    """Section 11.2 items 2-3, part (a): P2's 36 out/mout files and 36 wl days, from P2's working directory (Amendment 4: kept
    for look assembly) into O/out/mout and O/wl, each checked against Amendment 4's manifests (_p2_manifest: the manifest's
    own sha256 is pinned). A destination file that already has the manifest's hash (and is not a symlink) is kept; any other is
    replaced: the source must have the manifest's hash, it is copied to <name>.tmp, the copy re-hashed, then os.replace.
    Leftover *.tmp files go, and in O/wl every *.parquet that is not one of P2's days goes too (October days are rebuilt by
    build_wl_days from this assembly's tape; anything else in wl would be read by 11_passA). Nothing outside O/out/mout and
    O/wl is written, and the source is only read. NOT_READY on any hash mismatch."""
    out = {}
    sd, dd = _hist_dirs(src), _hist_dirs(O)
    for kind in ("mout", "wl"):
        path, pinned, p2 = _p2_manifest(kind)
        d = dd[kind]
        os.makedirs(d, exist_ok=True)
        removed = 0
        for n in sorted(os.listdir(d)):
            if n.endswith(".tmp") or (kind == "wl" and n.endswith(".parquet") and n not in p2):
                os.unlink(os.path.join(d, n))
                removed += 1
        copied = kept = 0
        for n, h in sorted(p2.items()):
            dst = os.path.join(d, n)
            if os.path.isfile(dst) and not os.path.islink(dst) and A.sha256_file(dst) == h:
                kept += 1
                continue
            s = os.path.join(sd[kind], n)
            if not os.path.isfile(s) or A.sha256_file(s) != h:
                raise R.Refusal("NOT_READY", f"P2's {kind} file {s} is missing or differs from {os.path.basename(path)}")
            tmp = dst + ".tmp"
            if os.path.lexists(dst) and not os.path.isfile(dst):
                raise R.Refusal("NOT_READY", f"{dst} is not a file")
            shutil.copyfile(s, tmp)
            if A.sha256_file(tmp) != h:
                os.unlink(tmp)
                raise R.Refusal("NOT_READY", f"the copy of {s} does not hash to {os.path.basename(path)}'s {h[:8]}")
            os.replace(tmp, dst)
            copied += 1
        out[kind] = {"src": sd[kind], "dir": d, "copied": copied, "kept": kept, "removed": removed}
    return out


def _manifest_trades_sha(mans) -> dict:
    """{hour: sha256 of tape/trades/<hour>.parquet as this assembly left it} (sha256_with_v_ok for merged files)."""
    return {f["hour"]: f.get("sha256_with_v_ok") or f["sha256"] for m in mans for f in m.get("files", []) if f.get("kind") == "trades"}


def build_wl_days(O: str, tape: str, mans, days, *, out_dir: str | None = None) -> dict:
    """Section 11.2 items 2-3, part (b): one wl/<day>.parquet per day in `days` (a look: OCT_WL_FIRST to the day before
    LOOKS[look]['end']) with the pinned ledger/01_wallet_daily_det.py (sha256 checked against SHA256SUMS before it runs) on
    tape/trades, written to out_dir (default O/wl). Every day's output is deleted first (the pinned script skips an existing
    day, so a re-assembly would otherwise keep a day built from an earlier tape). NOT_READY unless the ledger script is the
    pinned one, every day has at least one trades hour in the tape, every input file hashes to this assembly's manifest
    (sha256_with_v_ok for merged files), the script exits 0, reports the same file count per day, and writes every day.
    Returns the provenance for the record: the script and its sha256, per day the input files' sha256s, the wallet-row count
    the script prints, and the output's sha256."""
    script = A.ART / LEDGER_REL
    pinned = A.pinned_sha(LEDGER_REL)
    got = A.sha256_file(script)
    if got != pinned:
        raise R.Refusal("NOT_READY", f"{LEDGER_REL}: sha256 {got[:8]} is not the pinned {pinned[:8]}")
    out_dir = out_dir or os.path.join(O, "wl")
    trades = os.path.join(tape, "trades")
    want = _manifest_trades_sha(mans)
    prov = {"ledger_script": LEDGER_REL, "ledger_script_sha256": got, "tape": trades, "out": out_dir, "days": {}}
    names = sorted(os.listdir(trades)) if os.path.isdir(trades) else []
    for d in days:
        hrs = [n[:-8] for n in names if n.startswith(f"{d}T") and n.endswith(".parquet")]
        if not hrs:
            raise R.Refusal("NOT_READY", f"no trades hour of {d} in {trades}: its wl day cannot be built")
        ins = {h: A.sha256_file(os.path.join(trades, f"{h}.parquet")) for h in hrs}
        bad = [h for h in hrs if ins[h] != want.get(h)]
        if bad:
            raise R.Refusal("NOT_READY", f"{len(bad)} trades files of {d} differ from this assembly's manifests (first {bad[0]})")
        prov["days"][d] = {"inputs": {f"trades/{h}.parquet": ins[h] for h in hrs}}
    if not days:
        return prov
    os.makedirs(out_dir, exist_ok=True)
    for d in days:
        Path(out_dir, f"{d}.parquet").unlink(missing_ok=True)
        Path(out_dir, f"{d}.parquet.tmp").unlink(missing_ok=True)
    tmpd = os.path.join(O, "assembly", "tmp_wl")
    cmd = [sys.executable, str(script), "--tape", trades, "--out", out_dir, "--tmp", tmpd, "--threads", str(THREADS)]
    r = subprocess.run(cmd + [x for d in days for x in ("--day", d)], capture_output=True, text=True, stdin=subprocess.DEVNULL)
    shutil.rmtree(tmpd, ignore_errors=True)
    if r.returncode != 0:
        tail = (r.stderr.strip().splitlines() or [""])[-1][:200]
        raise R.Refusal("NOT_READY", f"{LEDGER_REL} exited {r.returncode}: {tail}")
    printed = {p[0]: p for p in (ln.split() for ln in r.stdout.splitlines()) if len(p) == 4 and p[0] in prov["days"]}
    for d in days:
        p = os.path.join(out_dir, f"{d}.parquet")
        if d not in printed or not os.path.isfile(p):
            raise R.Refusal("NOT_READY", f"{LEDGER_REL} wrote no wl day {d}")
        if int(printed[d][1]) != len(prov["days"][d]["inputs"]):
            raise R.Refusal("NOT_READY", f"{LEDGER_REL} read {printed[d][1]} trades files for {d}, not "
                                         f"{len(prov['days'][d]['inputs'])}")
        prov["days"][d].update(wallet_rows=int(printed[d][2]), sha256=A.sha256_file(p))
    return prov


def check_look_history(look: int, O: str, october_days=None, wl_prov: dict | None = None) -> dict:
    """Section 11.2 items 2 and 3, before look_assembly.json is written. NOT_READY unless
    (a) O/out/mout holds exactly P2's 36 mout files, each with mout_sha256.txt's hash (12_passC reads out/mout/*.parquet), and
    (b) O/wl holds exactly P2's 36 days per wl_sha256.txt plus one file per date of october_days (default: the look's,
        OCT_WL_FIRST to the day before LOOKS[look]['end']; the E0 passes none) (11_passA lists O/wl: no wl/ crashes pass A
        inside the lock; a missing day is silent creator-history and new-wallet drift), and
    (c) with wl_prov (build_wl_days' provenance), each October day's sha256 is the one the pinned ledger just wrote.
    Returns counts and sha256s for the record."""
    october_days = look_october_wl_days(look) if october_days is None else list(october_days)
    out = {}
    for kind, d in _hist_dirs(O).items():
        path, pinned, p2 = _p2_manifest(kind)
        want = set(p2) | ({f"{x}.parquet" for x in october_days} if kind == "wl" else set())
        have = {n for n in os.listdir(d) if n.endswith(".parquet")} if os.path.isdir(d) else set()
        miss, extra = sorted(want - have), sorted(have - want)
        if miss or extra:
            raise R.Refusal("NOT_READY", f"{d}: {len(miss)} files missing (first {miss[:1]}), {len(extra)} unexpected "
                                         f"(first {extra[:1]}); section 11.2 items 2-3")
        sha = {n: A.sha256_file(os.path.join(d, n)) for n in sorted(want)}
        bad = sorted(n for n, h in p2.items() if sha[n] != h)
        if bad:
            raise R.Refusal("NOT_READY", f"{d}: {len(bad)} of P2's files differ from {os.path.basename(path)} (first {bad[0]})")
        oct_sha = {n: sha[n] for n in sorted(sha) if n not in p2}
        if kind == "wl" and wl_prov is not None:
            built = {f"{x}.parquet": v.get("sha256") for x, v in wl_prov["days"].items()}
            if built != oct_sha:
                raise R.Refusal("NOT_READY", f"{d}: the October days on disk are not the ones {LEDGER_REL} just built")
        out[kind] = {"dir": d, "files": len(sha), "p2_files": len(p2), "p2_manifest": path, "p2_manifest_sha256": pinned,
                     "october_files": len(oct_sha), "october_sha256": oct_sha}
    return out


# ----------------------------------------------------------------------------------------------------------------- completes, bars
def tape_completes(con, tape: str, hours) -> list:
    """(mint, is_mayhem) of every mint with a `complete` migration in the tape, is_mayhem as build_shared's `cr` CTE takes it
    (arg_min over slot, rows with a block_time), NULL as false (the adapter E0's coalesce)."""
    fs = {k: [p for h in hours if os.path.exists(p := os.path.join(tape, k, f"{h}.parquet"))] for k in ("creates", "migrations")}
    if not fs["migrations"]:
        return []
    cr = (f"(SELECT mint, arg_min(is_mayhem_mode, slot) AS m FROM read_parquet({_lst(fs['creates'])}) WHERE block_time IS NOT NULL "
          f"GROUP BY mint)") if fs["creates"] else "(SELECT NULL::VARCHAR AS mint, NULL::BOOLEAN AS m WHERE false)"
    q = (f"SELECT g.mint, coalesce(c.m, false) FROM (SELECT DISTINCT mint FROM read_parquet({_lst(fs['migrations'])}) "
         f"WHERE type = 'complete' AND block_time IS NOT NULL) g LEFT JOIN {cr} c USING (mint) ORDER BY g.mint")
    return [(m, bool(x)) for m, x in con.execute(q).fetchall()]


def tokens_completes(con, tokens: str) -> list:
    q = (f"SELECT mint, coalesce(is_mayhem_mode, false) FROM read_parquet({A._q(str(tokens))}) WHERE grad_src = 'complete' "
         f"ORDER BY mint")
    return [(m, bool(x)) for m, x in con.execute(q).fetchall()]


def link_bars(ex_dir: str, oct_dir: str, out: str, *, allow_dup: bool = False) -> dict:
    """out/<block> -> symlink to each exploration bars_1m block directory, then each October one. A block in both refuses
    (allow_dup: the exploration E0, whose day is already in the exploration file; the exploration block is kept)."""
    os.makedirs(out, exist_ok=True)
    for n in os.listdir(out):
        p = os.path.join(out, n)
        if not os.path.islink(p):
            raise R.Refusal("NOT_READY", f"{p} is not a driver symlink")
        os.unlink(p)
    ex = sorted(os.listdir(ex_dir))
    oc = sorted(os.listdir(oct_dir)) if os.path.isdir(oct_dir) else []
    dup = sorted(set(ex) & set(oc))
    if dup and not allow_dup:
        raise R.Refusal("NOT_READY", f"bars_1m block {dup[0]!r} is in both the exploration and the October bars")
    for n in ex:
        os.symlink(os.path.join(ex_dir, n), os.path.join(out, n))
    for n in oc:
        if n not in dup:
            os.symlink(os.path.join(oct_dir, n), os.path.join(out, n))
    return {"exploration_blocks": len(ex), "october_blocks": len(oc) - len(dup), "dup_blocks_dropped": len(dup)}


# ----------------------------------------------------------------------------------------------------------------- assembly
def assemble(look_label: int, O: str, tape: str, plan, *, lookname, final_ledger, now, exploration: bool = False,
             exploration_sh: str = A.EXPLORATION_SH, exploration_sha256: str = A.EXPLORATION_TOKENS_SHA256,
             history: bool | None = None, p2_src: str = P2_SRC) -> dict:
    """Steps 4 to 8 of the module docstring. lookname None = exploration mode (adapter convert with look=None).
    history (step 4b): always in look mode; in exploration mode only when asked (the E0), with no October wl day."""
    history = True if not exploration else bool(history)
    Path(O, "look_assembly.json").unlink(missing_ok=True)   # a failed re-assembly must leave no record of the old tape
    asm = os.path.join(O, "assembly")
    os.makedirs(asm, exist_ok=True)
    mans = []
    for blk, src, hours, ev in plan:
        mans.append(A.convert(src, blk, tape, hours, look=lookname, event_v=ev, final_ledger=final_ledger, now=now))
    Path(tape, "manifest.json").unlink(missing_ok=True)   # convert's per-call copy; the per-block copies are the record
    for m in mans:   # the runner reads v_ok as a trades column (v_flag_missing, r3_coverage, price_rows)
        if m.get("event_v"):
            for f in m.get("files", []):
                if f.get("kind") == "trades":
                    f["sha256_with_v_ok"] = merge_v_ok(tape, f["hour"])
    listed = {f"{f['kind']}/{f['hour']}.parquet" for m in mans for f in m.get("files", [])} | {f"v_ok/{f['hour']}.parquet" for m in mans if m.get("event_v") for f in m.get("files", []) if f.get("kind") == "trades"}
    on_disk = {f"{k}/{n}" for k in A.KINDS + ("v_ok",) if os.path.isdir(os.path.join(tape, k)) for n in os.listdir(os.path.join(tape, k)) if n.endswith(".parquet")}
    if on_disk != listed: raise R.Refusal("NOT_READY", f"tape: {len(on_disk - listed)} files in no manifest of this assembly, {len(listed - on_disk)} listed files absent")
    if history:   # step 4b (section 11.2 items 2-3): P2's history copied in, the October wl days built from this tape
        oct_days = [] if exploration else look_october_wl_days(look_label)
        hist_copy = copy_p2_history(O, p2_src)
        wl_prov = build_wl_days(O, tape, mans, oct_days)
    paths = []
    for (blk, *_), m in zip(plan, mans):
        p = os.path.join(asm, f"manifest-{blk}.json")
        _write_json(p, m)
        paths.append(p)
    all_hours = sorted({h for _, _, hs, _ in plan for h in hs})
    v0_files = []
    if any(ev for *_, ev in plan):   # the V0 files convert used: look_trade_files (look) or the call's own trade files
        if lookname is not None:
            v0_files, _ = A.look_trade_files(lookname, final_ledger=final_ledger, now=now)
        else:
            v0_files = [f for _, src, hs, ev in plan if ev for h in hs if (f := A.src_file(src, "trades", h))]
        bad = {b["file"] for m in mans for b in m.get("v0_bad", [])}
        v0_files = [f for f in v0_files if str(f) not in bad]
    if lookname is not None:   # section 2.4: tokens.v0_lamports must come from the V0 files convert priced the tape with
        ev_m = [m for m in mans if m.get("event_v")]
        if (len({frozenset(b["file"] for b in m["v0_bad"]) for m in ev_m}) > 1
                or any(m["v0_files"] != len(v0_files) for m in ev_m)):
            raise R.Refusal("NOT_READY", "the driver's V0 files differ from convert's")
    con = A._connect(threads=THREADS, tmp=os.path.join(asm, "tmp_duck"))
    A.collect_v0(con, v0_files, A.pinned_cols())
    v0_rows = [(p, None if v is None else int(v)) for p, v in con.execute("SELECT pool, v0 FROM v0map ORDER BY pool").fetchall()]
    comp = tape_completes(con, tape, all_hours)
    shared = os.path.join(O, "october-shared")
    vmap, r2 = A.october_vmap(v0_rows, comp)
    if os.path.exists(shared):   # pinned build_shared.py skips an existing tokens.parquet / bars day (E0 #610): build from this tape only
        shutil.rmtree(shared)
    tok = str(A.build_shared(tape, shared, all_hours, vmap=vmap))
    comp_tok = tokens_completes(con, tok)
    rebuilt = comp_tok != comp
    if rebuilt:   # R2 is over the pinned tokens' completes; they do not depend on the vmap, so one rebuild settles it
        vmap, r2 = A.october_vmap(v0_rows, comp_tok)
        shutil.rmtree(shared)   # the rebuild's tokens.v0_lamports and bars must come from the new vmap
        tok = str(A.build_shared(tape, shared, all_hours, vmap=vmap))
        if tokens_completes(con, tok) != comp_tok:
            con.close()
            raise R.Refusal("NOT_READY", "the built tokens' completes changed with the vmap")
    con.close()
    vmap_path = os.path.join(asm, "vmap.json")
    _write_json(vmap_path, vmap)
    sh = os.path.join(O, "hunt-shared")
    os.makedirs(sh, exist_ok=True)
    app = A.append_tokens(os.path.join(exploration_sh, "tokens.parquet"), tok, os.path.join(sh, "tokens.parquet"),
                          exploration_sha256=exploration_sha256)
    bars = link_bars(os.path.join(exploration_sh, "bars_1m"), os.path.join(shared, "bars_1m"), os.path.join(sh, "bars_1m"),
                     allow_dup=exploration)
    v_ok = [f for m in mans for f in m.get("files", []) if f.get("kind") == "trades"]
    rec = {
        "schema": L.ASSEMBLY_SCHEMA, "look": f"look{look_label}", "mode": "exploration" if exploration else "look",
        "manifests": paths, "r2": r2, "append_tokens": app,
        "blocks": [{"block": b, "hours": len(hs), "event_v": ev, "files": len(m["files"]), "bad_hours": len(m["bad_hours"]),
                    "missing": len(m["missing"]), "v0_bad": len(m["v0_bad"])} for (b, _, hs, ev), m in zip(plan, mans)],
        "v0_files": len(v0_files), "vmap": {"pools": len(vmap), "file": vmap_path, "sha256": A.sha256_file(vmap_path)},
        "completes": {"n": len(comp_tok), "non_mayhem": r2["non_mayhem_completes"], "rebuilt": rebuilt},
        "pumpswap_rows": sum(int(f.get("pumpswap_rows") or 0) for f in v_ok),
        "pumpswap_rows_with_v": sum(int(f.get("pumpswap_rows_with_v") or 0) for f in v_ok),
        "october_tokens_sha256": A.sha256_file(tok), "bars_1m": bars,
        "blobs": {"tools/exp025_look_driver.py": A.git_blob(__file__), "tools/exp025_adapter.py": A.git_blob(A.__file__)},
    }
    if history:   # section 11.2 items 2-3: no record until O holds the history pass A / pass C read
        rec["history"] = check_look_history(look_label, O, oct_days, wl_prov)
        rec["history"]["copy"] = hist_copy
        rec["history"]["wl_provenance"] = wl_prov
    _write_json(os.path.join(O, "look_assembly.json"), rec)
    L.load_assembly(look_label, O)   # the runner's own validation of what was just written
    return rec


def drive_look(look: int, decoder_blobs: str, *, now: int | None = None, final_marker: str = R.FINAL_MARKER,
               final_ledger: str = R.FINAL_LEDGER) -> dict:
    """Assemble look 1 or 2 into exp025_read.LOOKS[look]['O']. Refusal (exit 2) before any path is opened unless the FINAL holds."""
    if look not in R.LOOKS:
        raise R.Refusal("R12", f"unknown look {look!r}")
    guard = R.LookGuard(look, now, final_marker, final_ledger)      # SEAL first: nothing else is touched before it passes
    R.check_decoder_blobs(decoder_blobs)                             # R13
    plan = look_plan(look)                                           # R12: the adapter's allowlist is the read tool's
    for blk, _, hours, _ in plan:
        for h in hours:
            guard.check_hour(SRC_OF_BLOCK[blk], h)
    O = R.LOOKS[look]["O"]
    tape = A.LOOK_TAPE[f"look{look}"]
    if os.path.realpath(tape) != os.path.realpath(os.path.join(O, "tape")):
        raise R.Refusal("R12", f"look {look}'s tape {tape} is not {O}/tape")
    if os.path.exists(os.path.join(O, "READ.lock")) or any(e["event"] == "lock" for e in R.ledger_events(R.LOOK_LEDGER, look)):
        raise R.Refusal("LOCK", f"look {look} is locked: its O is not re-assembled")
    stray = stray_tape_files(look, tape)
    if stray:
        raise R.Refusal("R12", f"{len(stray)} tape files outside look {look}'s allowlist (first {stray[0]})")
    when = datetime.fromtimestamp(now, timezone.utc) if now is not None else datetime.now(timezone.utc)
    return assemble(look, O, tape, plan, lookname=f"look{look}", final_ledger=final_ledger, now=when)


def v_ok_missing(tape: str) -> int:
    """The number of tape/trades/*.parquet files whose parquet_schema has no v_ok column."""
    d = os.path.join(tape, "trades")
    fs = sorted(os.path.join(d, n) for n in os.listdir(d) if n.endswith(".parquet")) if os.path.isdir(d) else []
    con = A._connect(threads=1, tmp=os.path.join(tape, "tmp_duck"))
    try:
        return sum(1 for f in fs
                   if not con.execute(f"SELECT count(*) FROM parquet_schema({A._q(f)}) WHERE name = 'v_ok'").fetchone()[0])
    finally:
        con.close()


def drive_e0(o_dir: str = E0_O) -> dict:
    """Exploration E0: assemble E0_DAY from the adapter E0's inputs into a scratch O, then the runner's checks on it."""
    if any(os.path.realpath(o_dir).startswith(os.path.realpath(L_["O"])) for L_ in R.LOOKS.values()):
        raise R.Refusal("SEAL", f"{o_dir} is inside a look's O")
    hours = A.hour_range(f"{A.E0_DAY}T00", "2026-09-21T00")
    plan = [(A.E0_BLOCK, A.E0_SRC, hours, True)]
    rec = assemble(1, o_dir, os.path.join(o_dir, "tape"), plan, lookname=None, final_ledger=None, now=None, exploration=True,
                   history=True)
    _, mans = L.load_assembly(1, o_dir)
    # the build_wl_days path on the E0 tape: the pinned ledger on E0_DAY (into a scratch dir, never O/wl) against P2's own day
    e0_wl = build_wl_days(o_dir, os.path.join(o_dir, "tape"), mans, [A.E0_DAY], out_dir=os.path.join(o_dir, "assembly", "e0-wl"))
    p2_day = _p2_manifest("wl")[2].get(f"{A.E0_DAY}.parquet")
    try:
        L.hour_status(1, mans, {})
        hs = None
    except R.Refusal as e:
        hs = e.code
    e0 = {"load_assembly": "accepted", "manifests": len(mans), "hour_status_on_exploration_hours": hs,
          "r2": rec["r2"], "append_tokens": rec["append_tokens"], "blocks": rec["blocks"], "completes": rec["completes"],
          "vmap_pools": rec["vmap"]["pools"], "pumpswap_rows": rec["pumpswap_rows"], "pumpswap_rows_with_v": rec["pumpswap_rows_with_v"],
          "bars_1m": rec["bars_1m"], "blobs": rec["blobs"],
          "assembly_sha256": A.sha256_file(os.path.join(o_dir, "look_assembly.json")),
          "october_tokens_sha256": rec["october_tokens_sha256"], "v_ok_missing": v_ok_missing(os.path.join(o_dir, "tape")),
          "history": {k: {x: rec["history"][k][x] for x in ("files", "p2_files", "october_files", "p2_manifest_sha256")}
                      for k in ("mout", "wl")},
          "history_copy": rec["history"]["copy"], "history_wl_provenance": rec["history"]["wl_provenance"],
          "e0_wl_day": {"day": A.E0_DAY, "inputs": len(e0_wl["days"][A.E0_DAY]["inputs"]),
                        "wallet_rows": e0_wl["days"][A.E0_DAY]["wallet_rows"], "sha256": e0_wl["days"][A.E0_DAY]["sha256"],
                        "ledger_script_sha256": e0_wl["ledger_script_sha256"], "p2_sha256": p2_day,
                        "equals_p2": e0_wl["days"][A.E0_DAY]["sha256"] == p2_day}}
    e0["pass"] = bool(hs == "R12" and rec["blocks"][0]["bad_hours"] == 0 and rec["blocks"][0]["files"] == 3 * len(hours)
                      and rec["append_tokens"]["october_rows"] == 0 and e0["v_ok_missing"] == 0
                      and all(e0["history"][k]["files"] == P2_DAYS and e0["history"][k]["october_files"] == 0 for k in ("mout", "wl"))
                      and e0["e0_wl_day"]["equals_p2"])
    _write_json(os.path.join(o_dir, E0_RECORD), e0)
    return e0


def _counts(rec: dict) -> dict:
    return {"look": rec["look"], "blocks": rec["blocks"], "r2": {k: rec["r2"][k] for k in ("non_mayhem_completes", "pda_pool_in_tape")},
            "append_tokens": {k: v for k, v in rec["append_tokens"].items() if k != "sha256"}, "vmap_pools": rec["vmap"]["pools"],
            "pumpswap_rows": rec["pumpswap_rows"], "pumpswap_rows_with_v": rec["pumpswap_rows_with_v"]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("look")
    c.add_argument("--look", type=int, required=True, choices=sorted(R.LOOKS))
    c.add_argument("--decoder-blobs", required=True)
    sub.add_parser("e0")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "look":
            print(json.dumps(_counts(drive_look(a.look, a.decoder_blobs))))
            return 0
        e0 = drive_e0()
        print(json.dumps({k: e0[k] for k in ("pass", "load_assembly", "manifests", "hour_status_on_exploration_hours", "blocks",
                                             "completes", "vmap_pools", "pumpswap_rows", "pumpswap_rows_with_v", "bars_1m",
                                             "history", "history_copy")} | {"e0_wl_day": {k: e0["e0_wl_day"][k] for k in
                                                                            ("day", "inputs", "wallet_rows", "equals_p2")}}))
        return 0 if e0["pass"] else 1
    except (R.Refusal, A.Refused) as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
