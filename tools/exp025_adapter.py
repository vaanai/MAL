#!/usr/bin/env python3
"""EXP-025 P3: the October adapter (EXP/EXP-025-c1nf-part1-prereg.md section 11.2). Walker JSONL.zst -> the hunt layout.

It adds no feature and changes no rule. Every column spec and every SQL statement that shapes a row is the PINNED file's
own text: the `TR_COLS` / `CR_COLS` / `MG_COLS` literals are parsed out of `ARTIFACTS/exp025/ref/convert.py` (never copied),
and `tokens.parquet` / `bars_1m` come from the pinned `ARTIFACTS/exp025/ref/build_shared.py` loaded by path after its
sha256 is checked against `ARTIFACTS/exp025/SHA256SUMS`. The adapter only drives them.

What the adapter does that the September scripts did not (section 11.2, all pre-declared):
  1. strict lines: a NUL byte, a non-blank line that is not a JSON object, or a row DuckDB cannot read with the pinned
     columns makes the hour BAD. Bad hours are written to the manifest and counted; there is no lenient fallback.
  2. event V: on PumpSwap prints that carry `virtual_quote_reserves`, quote_reserve := vault + V(t) - V0 (section 2.4,
     `event_v_map.map_quote_reserve`), V0 = V at the pool's first print s0; V0 goes to tokens.v0_lamports via the vmap.
  3. canonical pool by PDA: the October vmap holds V0 only for the PDA canonical pool of each completed mint, so the
     pinned TOKENS_SQL can only give a V0 (and pass the universe V band) to the canonical pool. R2 is reported.
  4. slot time is pass A's (pinned); nothing here.
  5. universe filter is the pinned 10_meta.py's; nothing here.
  6. allowlist: only a look's allowlisted hours (section 4) are materialised; any other October hour is refused (R12).

Seal (section 4). October hours are refused unless the DEC-016 FINAL ledger holds EXP-012's FINAL marker
(`tools.cap_pick_gate_replay.require_final`), the hour is in the look's allowlist, the hour has closed, and the source is
the pinned block directory. Exploration mode accepts only exploration-pool hours (never EXP-009 [09-15T12, 09-18T23),
never >= 2026-09-25T07) from a path with no sealed part. Nothing here prices, labels or scores a row.

  python3 tools/exp025_adapter.py convert --src DIR --block NAME --out TAPE_DIR --hours H0 H1 [--look look1|look2]
  python3 tools/exp025_adapter.py e0 --work /data/mal/exp025/e0/p3-0920 --record ARTIFACTS/exp025/e0/p3_e0_2026-09-20.json
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Sequence

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

ART = REPO / "ARTIFACTS" / "exp025"
CONVERT_PY = ART / "ref" / "convert.py"
BUILD_SHARED_PY = ART / "ref" / "build_shared.py"
SHA256SUMS = ART / "SHA256SUMS"
EXP_FILE = REPO / "EXP" / "EXP-025-c1nf-part1-prereg.md"

KINDS = ("trades", "creates", "migrations")
WSOL = "So11111111111111111111111111111111111111112"
PUMP_PROGRAM = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
PUMPSWAP_PROGRAM = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"

# ---- section 0 / section 4 pins (on-time merge; tests check them against the EXP file) ----
COUNT_START = "2026-10-10T00"
LOOK1_END = "2026-10-17T00"
COUNT_END = "2026-10-24T00"
OCTOBER_FIRST_HOUR = "2026-10-02T15"
V_COVER_START = "2026-10-09T00"  # P6 item 2: V coverage (forward-1002ev) starts here
SOURCES = {  # block -> pinned directory
    "forward-1002": "/data/mal/blocks/forward-1002",
    "forward-1002ev": "/data/mal/blocks/forward-1002ev",
    "walk2": "/data/mal/blocks/forward-1016",
}
LOOKS = {  # section 4 allowlist per look: (block, first hour, end hour exclusive)
    "look1": (("forward-1002", "2026-10-02T15", "2026-10-09T00"),
              ("forward-1002ev", "2026-10-09T00", "2026-10-16T01"),
              ("walk2", "2026-10-16T01", "2026-10-17T02")),
    "look2": (("forward-1002", "2026-10-02T15", "2026-10-09T00"),
              ("forward-1002ev", "2026-10-09T00", "2026-10-16T01"),
              ("walk2", "2026-10-16T01", "2026-10-24T02")),
}
# exploration (build and test only): the convert.py pool, minus EXP-009 and the Oracle live-tape hours
EXPLORATION_FIRST = "2026-08-14T00"
EXPLORATION_END = "2026-09-25T07"
EXP009 = ("2026-09-15T12", "2026-09-18T23")
E0_DAY = "2026-09-20"
E0_BLOCK = "fast-pool-0918"
E0_SRC = "/data/mal/clean-view/fast-pool-2026-09-18T23_2026-09-22T00"
E0_REF_TAPE = "/data/mal/audit-1008/tape"
SEALED_PARTS = ("fresh-0802", "fresh-0808", "fresh-0828", "oracle-live", "exp012-gate", "/OUT/", "rows.jsonl", "/scratch/",
                "report.json", "report.md", "v-map", "vmap-b")
OCTOBER_PATH_PARTS = ("forward", "walk2", "walk-2", "1016")

R2_MIN = 0.95  # PDA canonical-pool match, share of non-Mayhem completes
R3_MIN = 0.95  # per-print V coverage on canonical-pool prints of universe pools
R1_MAX_BAD = 0.02


class Refused(RuntimeError):
    """A pre-declared refusal. The message names the R-item (section 11.4) or the seal rule."""


class BadHour(RuntimeError):
    """A strict-line failure: the hour is bad (R1), never skipped silently."""


# ------------------------------------------------------------------------------------------------ pins
def sha256_file(p: str | Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def git_blob(p: str | Path) -> str:
    data = Path(p).read_bytes()
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def pinned_sha(rel: str) -> str:
    for line in SHA256SUMS.read_text().splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1] == rel:
            return parts[0]
    raise Refused(f"{rel} is not in {SHA256SUMS}")


def check_pinned(path: Path, rel: str) -> None:
    got = sha256_file(path)
    if got != pinned_sha(rel):
        raise Refused(f"{rel}: sha256 {got} is not the pinned one")


def pinned_cols() -> dict[str, str]:
    """TR_COLS / CR_COLS / MG_COLS as the pinned convert.py spells them (parsed, never executed)."""
    check_pinned(CONVERT_PY, "ref/convert.py")
    tree = ast.parse(CONVERT_PY.read_text())
    got: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            if name in ("TR_COLS", "CR_COLS", "MG_COLS"):
                got[name] = ast.literal_eval(node.value)
    return {"trades": got["TR_COLS"], "creates": got["CR_COLS"], "migrations": got["MG_COLS"]}


def load_build_shared():
    """The pinned build_shared.py as a module (its main() is not run)."""
    check_pinned(BUILD_SHARED_PY, "ref/build_shared.py")
    spec = importlib.util.spec_from_file_location("exp025_build_shared_pinned", BUILD_SHARED_PY)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


# ------------------------------------------------------------------------------------------------ hours and the seal
def hour_range(lo: str, hi: str) -> list[str]:
    t = datetime.strptime(lo, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc)
    end = datetime.strptime(hi, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc)
    out = []
    while t < end:
        out.append(t.strftime("%Y-%m-%dT%H"))
        t += timedelta(hours=1)
    return out


def look_allowlist(look: str) -> dict[str, str]:
    """hour -> block for the look's allowlisted hours (section 4)."""
    if look not in LOOKS:
        raise Refused(f"R12: unknown look {look!r}")
    return {h: blk for blk, lo, hi in LOOKS[look] for h in hour_range(lo, hi)}


def is_october(hour: str) -> bool:
    return hour >= OCTOBER_FIRST_HOUR


def check_exploration_hour(hour: str, src: str) -> None:
    if is_october(hour) or hour >= EXPLORATION_END or hour < EXPLORATION_FIRST:
        raise Refused(f"seal: {hour} is not an exploration hour (exploration mode)")
    if EXP009[0] <= hour < EXP009[1]:
        raise Refused(f"seal: {hour} is an EXP-009 hour")
    low = os.path.realpath(src).lower() + "|" + src.lower()
    for part in SEALED_PARTS + OCTOBER_PATH_PARTS:
        if part.lower() in low:
            raise Refused(f"seal: exploration mode refuses path part {part!r} in {src}")


def check_october_hour(hour: str, look: str | None, block: str, src: str, *, final_ledger: str | Path | None,
                       now: datetime | None = None) -> None:
    """R12 and the seal. Order: allowlist first (no file is touched), then the FINAL ledger, then the hour has closed."""
    if look is None:
        raise Refused(f"R12/seal: October hour {hour} needs --look (exploration mode never opens October)")
    allow = look_allowlist(look)
    if hour not in allow:
        raise Refused(f"R12: {hour} is outside {look}'s allowlist")
    if allow[hour] != block:
        raise Refused(f"R12: {hour} belongs to {allow[hour]} in {look}, not {block}")
    if os.path.realpath(src) != os.path.realpath(SOURCES[block]) and not src.startswith(SOURCES[block]):
        raise Refused(f"R12: {block} is read only from {SOURCES[block]}, not {src}")
    for part in SEALED_PARTS:
        if part.lower() in src.lower():
            raise Refused(f"seal: path part {part!r} is closed (DEC-016 Am.2)")
    from tools.cap_pick_gate_replay import Refused as GateRefused, require_final
    try:
        require_final(final_ledger if final_ledger is not None else "/data/mal/exp012-forward/FINAL_READS.jsonl")
    except GateRefused as e:
        raise Refused(f"seal: {e}") from None
    now = now or datetime.now(timezone.utc)
    end = datetime.strptime(hour, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc) + timedelta(hours=1)
    if now < end:
        raise Refused(f"seal: {hour} has not closed")


# ------------------------------------------------------------------------------------------------ strict lines
def strict_scan(path: str | Path) -> int:
    """Count the lines of a .jsonl.zst; raise BadHour on a NUL byte or a non-blank line that is not `{...}`.
    (DuckDB's strict read, with no ignore_errors, then rejects any `{...}` line that is not valid JSON for the columns.)"""
    proc = subprocess.Popen(["zstd", "-dc", "--", str(path)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert proc.stdout is not None
    n = 0
    tail = b""
    bad = None
    while True:
        chunk = proc.stdout.read(1 << 22)
        if not chunk:
            break
        if b"\x00" in chunk:
            bad = "NUL byte"
            break
        lines = (tail + chunk).split(b"\n")
        tail = lines.pop()
        for ln in lines:
            s = ln.strip()
            n += 1
            if not (s.startswith(b"{") and s.endswith(b"}")):
                bad = f"line {n} is not a JSON object"
                break
        if bad:
            break
    if bad is None and tail.strip():
        n += 1
        s = tail.strip()
        if not (s.startswith(b"{") and s.endswith(b"}")):
            bad = f"line {n} is not a JSON object"
    proc.stdout.close()
    err = proc.stderr.read() if proc.stderr else b""
    if proc.stderr:
        proc.stderr.close()
    rc = proc.wait()
    if bad:
        raise BadHour(f"{path}: {bad}")
    if rc != 0:
        raise BadHour(f"{path}: zstd failed: {err[:200]!r}")
    return n


# ------------------------------------------------------------------------------------------------ convert
def _connect(threads: int = 4, mem: str = "6GB", tmp: str | None = None):
    import duckdb
    con = duckdb.connect()
    con.execute(f"SET memory_limit='{mem}'; SET threads={int(threads)}; SET preserve_insertion_order=true")
    if tmp:
        Path(tmp).mkdir(parents=True, exist_ok=True)
        con.execute(f"SET temp_directory='{tmp}'")
    return con


def _q(s: str) -> str:
    return "'" + str(s).replace("'", "''") + "'"


def src_file(src: str, kind: str, hour: str) -> Path | None:
    for name in (f"{kind}-{hour}.jsonl.zst", f"{kind}-{hour}.deduped.jsonl.zst"):
        p = Path(src) / kind / name
        if p.exists():
            return p
    return None


def _write_parquet(table, out: Path) -> str:
    import pyarrow.parquet as pq
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".tmp")
    pq.write_table(table, tmp, compression="zstd", row_group_size=1_000_000)  # the tokens append only
    os.replace(tmp, out)
    return sha256_file(out)


def convert_hour(con, f: Path, kind: str, block: str, hour: str, out: Path, cols: dict[str, str], *,
                 v0_table: str | None = None) -> dict:
    """One hour, one kind: the pinned columns + block + hour, in the raw file's line order, written by DuckDB's own
    parquet writer as convert.py writes it (HUGEINT columns land as DOUBLE there; a pyarrow writer would give DECIMAL and
    different rows). With `v0_table` (pool, v0) and trades, PumpSwap quote_reserve := vault + V(t) - V0 where both exist."""
    n_lines = strict_scan(f)
    spec = cols[kind]
    import duckdb
    ev: dict = {}
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".tmp")
    try:
        if kind == "trades" and v0_table is not None:
            spec_v = spec[:-1] + ",virtual_quote_reserves:'HUGEINT'}"
            names = [c.split(":")[0] for c in spec.strip("{}").split(",")]
            sel = ", ".join(
                "(CASE WHEN r.venue = 'pumpswap' AND r.virtual_quote_reserves IS NOT NULL AND v.v0 IS NOT NULL "
                "THEN r.quote_reserve + r.virtual_quote_reserves - v.v0 ELSE r.quote_reserve END) AS quote_reserve"
                if c == "quote_reserve" else f"r.{c}" for c in names)
            con.execute(f"CREATE OR REPLACE TEMP TABLE r AS SELECT *, row_number() OVER () AS rn__ FROM read_json({_q(f)}, "
                        f"format='newline_delimited', compression='zstd', columns={spec_v})")
            n = con.execute("SELECT count(*) FROM r").fetchone()[0]
            con.execute(f"COPY (SELECT {sel}, {_q(block)} AS block, {_q(hour)} AS hour FROM r LEFT JOIN {v0_table} v "
                        f"ON v.pool = r.pool ORDER BY r.rn__) TO {_q(tmp)} (FORMAT parquet, COMPRESSION zstd)")
            ps, wv, mp = con.execute(f"""SELECT count(*) FILTER (WHERE r.venue = 'pumpswap'),
                count(*) FILTER (WHERE r.venue = 'pumpswap' AND r.virtual_quote_reserves IS NOT NULL),
                count(*) FILTER (WHERE r.venue = 'pumpswap' AND r.virtual_quote_reserves IS NOT NULL AND v.v0 IS NOT NULL)
                FROM r LEFT JOIN {v0_table} v ON v.pool = r.pool""").fetchone()
            ev = {"pumpswap_rows": int(ps), "pumpswap_rows_with_v": int(wv), "pumpswap_rows_mapped": int(mp)}
            con.execute("DROP TABLE r")
        else:
            con.execute(f"COPY (SELECT *, {_q(block)} AS block, {_q(hour)} AS hour FROM read_json({_q(f)}, "
                        f"format='newline_delimited', compression='zstd', columns={spec})) TO {_q(tmp)} (FORMAT parquet, COMPRESSION zstd)")
            n = con.execute(f"SELECT count(*) FROM read_parquet({_q(tmp)})").fetchone()[0]
    except duckdb.Error as e:  # strict: no ignore_errors fallback (convert.py's LENIENT path is not taken)
        tmp.unlink(missing_ok=True)
        raise BadHour(f"{f}: strict read failed: {str(e)[:200]}") from None
    if n != n_lines:
        tmp.unlink(missing_ok=True)
        raise BadHour(f"{f}: {n} rows from {n_lines} lines")
    os.replace(tmp, out)
    return {"kind": kind, "hour": hour, "block": block, "src": str(f), "rows": int(n), "sha256": sha256_file(out), **ev}


def collect_v0(con, files: Sequence[Path], cols: dict[str, str]) -> str:
    """Table v0map(pool, v0, first_slot, first_tx, first_ev): V at each pool's first print s0 over `files`, NULL if s0 has no V.
    Ties on (slot, tx_index, event_index) cannot occur for distinct prints."""
    spec_v = cols["trades"][:-1] + ",virtual_quote_reserves:'HUGEINT'}"
    lst = "[" + ",".join(_q(f) for f in files) + "]"
    con.execute(f"""CREATE OR REPLACE TABLE v0map AS
        SELECT pool, arg_min(virtual_quote_reserves, slot::HUGEINT * 10000000000 + coalesce(tx_index, 0)::HUGEINT * 100000
                                 + coalesce(event_index, 0)) AS v0, min(slot) AS first_slot
        FROM read_json({lst}, format='newline_delimited', compression='zstd', columns={spec_v})
        WHERE venue = 'pumpswap' AND pool IS NOT NULL GROUP BY pool""")
    return "v0map"


def convert(src: str, block: str, out: str | Path, hours: Sequence[str], *, look: str | None = None,
            event_v: bool = False, v0_files: Sequence[str | Path] | None = None, final_ledger: str | Path | None = None,
            threads: int = 4, now: datetime | None = None) -> dict:
    """Materialise `hours` of `src` into `out/{trades,creates,migrations}/<hour>.parquet`. Every hour is checked before any
    file is opened. With `event_v`, V0 is V at each pool's first print over `v0_files` (default: this call's trade files);
    an October look passes every allowlisted trade hour of every block, so a pool whose s0 lies in a forward-1002 hour
    (no V there) gets no V0 and is left unmapped. Returns the manifest (also written to out/manifest.json)."""
    for h in hours:
        if is_october(h):
            check_october_hour(h, look, block, src, final_ledger=final_ledger, now=now)
        else:
            if look is not None:
                raise Refused(f"R12: {h} is not an October hour of {look}")
            check_exploration_hour(h, src)
    cols = pinned_cols()
    out = Path(out)
    con = _connect(threads=threads, tmp=str(out / "tmp_duck"))
    v0_table = None
    if event_v:
        tf = list(v0_files) if v0_files is not None else [p for h in hours if (p := src_file(src, "trades", h))]
        v0_table = collect_v0(con, tf, cols)
    rows, bad, missing = [], [], []
    for h in hours:
        for kind in KINDS:
            f = src_file(src, kind, h)
            if f is None:
                missing.append({"kind": kind, "hour": h})
                continue
            try:
                rows.append(convert_hour(con, f, kind, block, h, out / kind / f"{h}.parquet", cols, v0_table=v0_table))
            except BadHour as e:
                bad.append({"kind": kind, "hour": h, "reason": str(e)})
                (out / kind / f"{h}.parquet").unlink(missing_ok=True)
    con.close()
    shutil.rmtree(out / "tmp_duck", ignore_errors=True)
    man = {"schema": "exp025_adapter_manifest_v1", "block": block, "src": src, "look": look, "event_v": event_v,
           "hours": list(hours), "files": rows, "bad": bad, "missing": missing,
           "bad_hours": sorted({b["hour"] for b in bad}), "adapter_blob": git_blob(__file__)}
    (out / "manifest.json").write_text(json.dumps(man, indent=1, sort_keys=True))
    return man


# ------------------------------------------------------------------------------------------------ canonical pool, V0 map
def canonical_pool(mint: str) -> str:
    """pool = PDA(["pool", 0u16 LE, PDA(["pool-authority", mint], pump), mint, WSOL], PumpSwap) (tools/pumpswap_tx.py)."""
    from tools.pump_structure_monitor import b58decode, find_program_address
    m = b58decode(mint)
    auth = find_program_address([b"pool-authority", m], PUMP_PROGRAM)[0]
    return find_program_address([b"pool", (0).to_bytes(2, "little"), b58decode(auth), m, b58decode(WSOL)], PUMPSWAP_PROGRAM)[0]


def october_vmap(v0_rows: Iterable[tuple[str, int | None]], completes: Iterable[tuple[str, bool]]) -> tuple[dict[str, int], dict]:
    """vmap {pool: V0} for build_shared: only the PDA canonical pool of each completed mint, only with a V0.
    `completes` is (mint, is_mayhem). Returns (vmap, r2) with the R2 match share over non-Mayhem completes."""
    v0 = dict(v0_rows)
    vmap: dict[str, int] = {}
    n = hit = 0
    for mint, mayhem in completes:
        pool = canonical_pool(mint)
        found = pool in v0
        if not mayhem:
            n += 1
            hit += int(found)
        if found and v0[pool] is not None:
            vmap[pool] = int(v0[pool])
    share = hit / n if n else 0.0
    return vmap, {"non_mayhem_completes": n, "pda_pool_in_tape": hit, "share": share}


def check_r2(r2: dict) -> None:
    if r2["non_mayhem_completes"] == 0 or r2["share"] < R2_MIN:
        raise Refused(f"R2: PDA canonical-pool match {r2['pda_pool_in_tape']}/{r2['non_mayhem_completes']} is below {R2_MIN:.0%}")


def check_r1(n_hours: int, n_bad: int) -> None:
    if n_hours == 0 or n_bad / n_hours > R1_MAX_BAD:
        raise Refused(f"R1: {n_bad} of {n_hours} hours are bad (more than {R1_MAX_BAD:.0%})")


def check_r3(n_prints: int, n_with_v: int) -> None:
    if n_prints == 0 or n_with_v / n_prints < R3_MIN:
        raise Refused(f"R3: per-print V coverage {n_with_v}/{n_prints} is below {R3_MIN:.0%}")


# ------------------------------------------------------------------------------------------------ hunt-shared
def build_shared(tape: str | Path, out: str | Path, hours: Sequence[str], *, vmap: dict[str, int] | None = None,
                 vmap_src: str = "event_v0", keep_tmp: bool = False) -> Path:
    """tokens.parquet and bars_1m/ by the pinned build_shared.py's own phases, on `hours` of `tape`.
    vmap None = the pinned September maps (load_vmap); otherwise {pool: V0} (October, PDA canonical pools only)."""
    bs = load_build_shared()
    tape, out = Path(tape), Path(out)
    bs.check_path(str(tape))
    bs.check_path(str(out))
    out.mkdir(parents=True, exist_ok=True)
    (out / ".nobackup").touch()
    hours = sorted(h for h in hours if (tape / "trades" / f"{h}.parquet").exists())
    if not hours:
        raise Refused("no trade hours to build")
    days = sorted({h[:10] for h in hours})
    con = bs.connect(out)
    if vmap is None:
        bs.load_vmap(con)
    else:
        import pyarrow as pa
        t = pa.table({"pool": list(vmap), "v": [int(v) for v in vmap.values()], "src": [vmap_src] * len(vmap)})
        con.register("vmap_arrow", t)
        con.execute("CREATE OR REPLACE TABLE vmap AS SELECT * FROM vmap_arrow")
        con.unregister("vmap_arrow")
    for day in days:
        bs.phase1_day(con, tape, out, day, [h for h in hours if h[:10] == day])
    bs.phase2(con, out, days)
    bs.phase3_bars(con, out)
    bs.phase3_tokens(con, tape, out, hours)
    stats = [json.loads(p.read_text()) for p in sorted((out / "tmp" / "done").glob("p1-*.json"))]
    (out / "stats.json").write_text(json.dumps(stats, indent=1))
    con.close()
    if not keep_tmp:
        shutil.rmtree(out / "tmp", ignore_errors=True)
    return out / "tokens.parquet"


def append_tokens(exploration: str | Path, october: str | Path, out: str | Path, *, exploration_sha256: str) -> dict:
    """Look tokens.parquet = the exploration rows, unchanged and in order, then the October rows (section 11.2 item 1).
    An October mint that already has an exploration row is not appended (its exploration row stays as it is); counted."""
    import pyarrow as pa
    import pyarrow.compute as pc
    import pyarrow.parquet as pq
    got = sha256_file(exploration)
    if got != exploration_sha256:
        raise Refused(f"exploration tokens.parquet sha256 {got} is not the recorded {exploration_sha256}")
    ex = pq.read_table(exploration)
    octo = pq.read_table(october)
    if octo.schema.names != ex.schema.names:
        raise Refused("October tokens columns differ from the exploration file's")
    octo = octo.cast(ex.schema)
    dup = pc.is_in(octo.column("mint"), value_set=ex.column("mint"))
    n_dup = int(pc.sum(dup.cast("int64")).as_py() or 0)
    octo = octo.filter(pc.invert(dup))
    t = pa.concat_tables([ex, octo])
    sha = _write_parquet(t, Path(out))
    return {"exploration_rows": ex.num_rows, "october_rows": octo.num_rows, "october_dup_mints_dropped": n_dup, "sha256": sha}


def check_mid_stable(look_universe: str | Path, p2_universe: str | Path) -> dict:
    """P3 E0 item: every exploration token keeps P2's `mid` in the look's universe.parquet."""
    import duckdb
    con = duckdb.connect()
    r = con.execute(f"""SELECT count(*) AS n, count(*) FILTER (WHERE l.mid IS DISTINCT FROM p.mid) AS moved
        FROM read_parquet({_q(p2_universe)}) p LEFT JOIN read_parquet({_q(look_universe)}) l USING (mint)""").fetchone()
    con.close()
    res = {"p2_tokens": int(r[0]), "mid_changed_or_missing": int(r[1])}
    if r[1]:
        raise Refused(f"P3: {r[1]} exploration tokens change `mid` in the look universe")
    return res


# ------------------------------------------------------------------------------------------------ md5 over rows
def rows_md5(con, files: Sequence[str | Path]) -> tuple[str, int]:
    """md5 over the rows of `files` as '|'-joined text, sorted (order-independent: convert.py wrote with
    preserve_insertion_order=false, so its file order is not a pin)."""
    lst = "[" + ",".join(_q(f) for f in files) + "]"
    rel = f"read_parquet({lst})"
    names = [r[0] for r in con.execute(f"DESCRIBE SELECT * FROM {rel}").fetchall()]
    expr = " || '|' || ".join(f"coalesce(CAST({c} AS VARCHAR), '\\N')" for c in names)
    r = con.execute(f"SELECT md5(string_agg(x, chr(10) ORDER BY x)), count(*) FROM (SELECT {expr} AS x FROM {rel})").fetchone()
    return r[0], int(r[1])


# ------------------------------------------------------------------------------------------------ E0 on 2026-09-20
def run_e0(work: str | Path, record: str | Path, *, threads: int = 4) -> dict:
    t0 = time.time()
    work = Path(work)
    hours = hour_range(f"{E0_DAY}T00", "2026-09-21T00")
    tape = work / "tape"
    man = convert(E0_SRC, E0_BLOCK, tape, hours, threads=threads)
    idem = {}
    for th in (threads, 1):  # idempotence on one real hour, two thread counts
        m2 = convert(E0_SRC, E0_BLOCK, work / f"idem-{th}", [f"{E0_DAY}T05"], threads=th)
        idem[str(th)] = {x["kind"]: x["sha256"] for x in m2["files"]}
    con = _connect(threads=threads, tmp=str(work / "tmp_md5"))
    kinds = {}
    for kind in KINDS:
        mine = [tape / kind / f"{h}.parquet" for h in hours if (tape / kind / f"{h}.parquet").exists()]
        ref = [Path(E0_REF_TAPE) / kind / f"{h}.parquet" for h in hours if (Path(E0_REF_TAPE) / kind / f"{h}.parquet").exists()]
        m_md5, m_n = rows_md5(con, mine)
        r_md5, r_n = rows_md5(con, ref)
        kinds[kind] = {"adapter_md5": m_md5, "ref_md5": r_md5, "adapter_rows": m_n, "ref_rows": r_n,
                       "files": [len(mine), len(ref)], "equal": m_md5 == r_md5 and m_n == r_n}
    # tokens: the pinned build_shared.py run as itself on the reference tape vs the adapter's driver on the adapter tape
    ref_sh = work / "ref-shared"
    if not (ref_sh / "tokens.parquet").exists():
        subprocess.run([sys.executable, str(BUILD_SHARED_PY), "--tape", E0_REF_TAPE, "--out", str(ref_sh),
                        "--from", hours[0], "--to", "2026-09-21T00"], check=True)
    ad_sh = work / "adapter-shared"
    if not (ad_sh / "tokens.parquet").exists():
        build_shared(tape, ad_sh, hours)
    tk_ad, n_ad = rows_md5(con, [ad_sh / "tokens.parquet"])
    tk_ref, n_ref = rows_md5(con, [ref_sh / "tokens.parquet"])
    bars_ad, nb_ad = rows_md5(con, sorted((ad_sh / "bars_1m").rglob("*.parquet")))
    bars_ref, nb_ref = rows_md5(con, sorted((ref_sh / "bars_1m").rglob("*.parquet")))
    # R2 dry check of the PDA function on the E0 day (September pool = first pool by slot; not a refusal here)
    comp = con.execute(f"""SELECT mint, coalesce(is_mayhem_mode, false), pool FROM read_parquet({_q(ad_sh / 'tokens.parquet')})
                           WHERE grad_src = 'complete'""").fetchall()
    con.close()
    nm = [(m, p) for m, mh, p in comp if not mh]
    pda_eq = sum(1 for m, p in nm if p is not None and canonical_pool(m) == p)
    # the section 2.4 event-V mapping fixture
    fx = subprocess.run([sys.executable, "-m", "unittest", "tools.test_exp025.EventVMapping", "-v"], cwd=REPO,
                        capture_output=True, text=True)
    rec = {
        "schema": "exp025_p3_e0_v1", "day": E0_DAY, "block": E0_BLOCK, "src": E0_SRC, "ref_tape": E0_REF_TAPE,
        "view_sha256_file": sha256_file(Path(E0_SRC) / "VIEW.sha256"),
        "bad_hours": man["bad_hours"], "missing": man["missing"], "kinds": kinds,
        "idempotence_T05": {"by_threads": idem, "equal": len({json.dumps(v, sort_keys=True) for v in idem.values()}) == 1
                            and idem[str(threads)] == {x["kind"]: x["sha256"] for x in man["files"] if x["hour"] == f"{E0_DAY}T05"}},
        "tokens": {"adapter_md5": tk_ad, "ref_md5": tk_ref, "adapter_rows": n_ad, "ref_rows": n_ref, "equal": tk_ad == tk_ref and n_ad == n_ref,
                   "adapter_sha256": sha256_file(ad_sh / "tokens.parquet"), "ref_sha256": sha256_file(ref_sh / "tokens.parquet")},
        "bars_1m": {"adapter_md5": bars_ad, "ref_md5": bars_ref, "adapter_rows": nb_ad, "ref_rows": nb_ref, "equal": bars_ad == bars_ref},
        "pda_first_pool_match": {"non_mayhem_completes": len(nm), "pda_equals_tokens_pool": pda_eq},
        "event_v_fixture": {"cmd": "python3 -m unittest tools.test_exp025.EventVMapping", "returncode": fx.returncode,
                            "ok": fx.returncode == 0, "tail": fx.stderr.strip().splitlines()[-1:] if fx.stderr else []},
        "mid_check": "pending P2 (needs P2's universe.parquet; check_mid_stable)",
        "blobs": {"tools/exp025_adapter.py": git_blob(__file__), "ARTIFACTS/exp025/ref/convert.py": git_blob(CONVERT_PY),
                  "ARTIFACTS/exp025/ref/build_shared.py": git_blob(BUILD_SHARED_PY),
                  "ARTIFACTS/exp025/event_v_map.py": git_blob(ART / "event_v_map.py")},
        "seconds": round(time.time() - t0, 1),
    }
    rec["pass"] = bool(all(k["equal"] for k in kinds.values()) and rec["tokens"]["equal"] and rec["event_v_fixture"]["ok"]
                       and not rec["bad_hours"] and rec["idempotence_T05"]["equal"])
    Path(record).parent.mkdir(parents=True, exist_ok=True)
    Path(record).write_text(json.dumps(rec, indent=1, sort_keys=True) + "\n")
    return rec


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("convert")
    c.add_argument("--src", required=True)
    c.add_argument("--block", required=True)
    c.add_argument("--out", required=True)
    c.add_argument("--hours", nargs=2, required=True, metavar=("FIRST", "END_EXCL"))
    c.add_argument("--look", choices=sorted(LOOKS))
    c.add_argument("--event-v", action="store_true")
    c.add_argument("--threads", type=int, default=4)
    e = sub.add_parser("e0")
    e.add_argument("--work", required=True)
    e.add_argument("--record", required=True)
    e.add_argument("--threads", type=int, default=4)
    a = ap.parse_args(argv)
    try:
        if a.cmd == "convert":
            man = convert(a.src, a.block, a.out, hour_range(*a.hours), look=a.look, event_v=a.event_v, threads=a.threads)
            print(json.dumps({"files": len(man["files"]), "bad_hours": man["bad_hours"], "missing": len(man["missing"])}))
        else:
            rec = run_e0(a.work, a.record, threads=a.threads)
            print(json.dumps({"pass": rec["pass"], "kinds": {k: v["equal"] for k, v in rec["kinds"].items()},
                              "tokens": rec["tokens"]["equal"], "event_v_fixture": rec["event_v_fixture"]["ok"]}))
            return 0 if rec["pass"] else 1
    except Refused as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
