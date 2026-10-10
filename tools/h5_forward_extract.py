"""EXP-024 forward extractor (precondition P3) and its E0-H5 check (precondition P4).

Raw walker hour files (`{trades,creates,migrations}/<kind>-<hour>.jsonl.zst`) -> H5 `meta/<day>.parquet` and
`paths/<day>.parquet`, in the format of /data/mal/audit-1008/work/g_reachable_cap_book_rescore/{meta,paths}
(EXP-024 RULE, "Universe"). The two stages are the ones that made that reference, kept verbatim:

  1. convert: /data/mal/audit-1008/convert.py (raw JSONL.zst -> hourly Parquet, same column types, plus `block`
     and `hour`). One change: strict lines. A file duckdb cannot parse is never retried leniently: in e0 it is a
     refusal; in forward its hour is dropped as `parse_failed`.
  2. extract: /data/mal/audit-1008/work/g_reachable_cap_book_rescore/extract.py (per UTC day: `complete`
     migrations, the first V-range PumpSwap pool, the window [mslot, mslot+7300), creates of the day and the day
     before, trades of the day plus the next two hours, ordered (slot, tx_index, event_index)). Same SQL.

Modes (constants only; no CLI override of hours, sources, window or V range):

  e0       E0-H5 (EXP-024 section 10, P4.1), pinned 2026-10-08: 2026-09-20 on fast-pool-0918 (exploration).
           Checks every raw file it reads against the view's VIEW.sha256, runs both stages on hours
           [09-19T00, 09-21T02) into a new --out, and compares meta/paths with the reference by md5 over canonical
           rows (meta sorted by mint; paths sorted by every column), plus a positional order check on paths: the
           (mint, slot) sequence is identical and rows move only inside ties the reference's ORDER BY leaves open.
           File-byte and file-order md5s are recorded as information: the reference meta has no ORDER BY and its
           paths ties were ordered by duckdb's threads, so the reference's own bytes do not reproduce.
           Writes <out>/E0-H5.json (VIEW.sha256, vmap sha256, md5s, blob shas). Exit 0 PASS, 1 FAIL.
  forward  forward-1002 hours [2026-10-09T23, 2026-10-16T01) (EXP-024 section 3). Refuses, before opening anything
           under forward-1002, unless the EXP-012 FINAL marker is in the external FINAL ledger
           (tools/forward_v_join.final_marker, the same check the V join uses). Then refuses unless --e0-record is
           a PASS written by this extractor's current blob under the pinned duckdb. Each hour must be sealed and
           verified with strict lines (tools/forward_v_join.hour_state), and its creates/migrations files must hash
           to the hour's verify line; an hour that is not is left out and its reason code recorded. A duckdb parse
           or conversion error in any kind of an hour drops that whole hour (reason `parse_failed`, its parquet
           removed); any other error (out of memory, I/O) refuses the whole run. Hole rule (outcome-blind; it reads
           only which hours are usable): a mint whose migration hour or the next hour is not usable is excluded,
           because its [mslot, mslot+7300) window (at most ~49 min, so at most two hours) could run into the hole
           and give a truncated path; per-day counts go to the manifest. --vmap is the V map (same JSON shape as
           pool_v_0909.json: {"v": {pool: lamports|null}}); its sha256 is recorded.
           Writes <out>/manifest.json (with the blobs of this tool and of tools/forward_v_join.py). Prints counts only.

meta.v. The --vmap value is used for one thing: choosing the canonical pool (the first V-range pool after `complete`,
EXP-024 "Universe"). It is copied into meta.v because the reference format has that column. It is NOT an Am.1 V0
source: the read tool (#476) takes V0 and V(t) only from the Am.1 order (forward-1002ev join, getTransaction, the
pool account) and otherwise applies the section 4 missing-V rule. The manifest says so (`vmap_role`).

Seal. This tool computes no trigger, fill, exit or P&L, and joins nothing to outcomes. It never reads sealed blocks,
forward-1002ev, walk 2, forward-paper or runner paths. `th` is duckdb's hash(trader); it depends on the duckdb
version, so the version is pinned and checked.

Run from the repo root with a Python that has duckdb 1.5.6, e.g. /data/mal/audit-1008/venv/bin/python:
  python -m tools.h5_forward_extract e0 --out /data/mal/exp024/e0-h5-<stamp>
  python -m tools.h5_forward_extract forward --out DIR --vmap FILE --e0-record /data/mal/exp024/e0-h5-<stamp>/E0-H5.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Sequence

REPO = Path(__file__).resolve().parents[1]
DUCKDB_VERSION = "1.5.6"  # the reference's created_by; hash(trader) -> th depends on it

# Stage 1 column types, verbatim from /data/mal/audit-1008/convert.py.
TR_COLS = "{venue:'VARCHAR',mint:'VARCHAR',trader:'VARCHAR',side:'VARCHAR',sol_lamports:'BIGINT',token_raw:'HUGEINT',quote_reserve:'HUGEINT',base_reserve:'HUGEINT',pool:'VARCHAR',slot:'BIGINT',tx_index:'INTEGER',event_index:'INTEGER',block_time:'BIGINT',lp_fee:'BIGINT',protocol_fee:'BIGINT',creator_fee:'BIGINT'}"
CR_COLS = "{mint:'VARCHAR',creator:'VARCHAR',trader:'VARCHAR',name:'VARCHAR',symbol:'VARCHAR',is_mayhem_mode:'BOOLEAN',quote_reserve:'HUGEINT',base_reserve:'HUGEINT',real_token_reserves:'HUGEINT',token_raw:'HUGEINT',slot:'BIGINT',tx_index:'INTEGER',event_index:'INTEGER',block_time:'BIGINT',signature:'VARCHAR'}"
MG_COLS = "{type:'VARCHAR',mint:'VARCHAR',trader:'VARCHAR',bonding_curve:'VARCHAR',slot:'BIGINT',tx_index:'INTEGER',event_index:'INTEGER',block_time:'BIGINT',signature:'VARCHAR'}"
KINDS = ("trades", "creates", "migrations")
COLS = {"trades": TR_COLS, "creates": CR_COLS, "migrations": MG_COLS}

# Stage 2 constants, verbatim from g_reachable_cap_book_rescore/extract.py.
WIN = 7300
V_LO, V_HI = 17_500_000_000, 17_700_000_000

# E0-H5, pinned by EXP-024 section 10 (2026-10-08).
E0_DAY = "2026-09-20"
E0_BLOCK = "fast-pool-0918"
E0_VIEW = Path("/data/mal/clean-view/fast-pool-2026-09-18T23_2026-09-22T00")
E0_FROM, E0_TO = "2026-09-19T00", "2026-09-21T02"  # creates of the day before, the day, and two more trade hours
E0_VMAP = Path("/data/mal/pumpswap-virtual/pool_v_0909.json")
E0_VMAP_SHA256 = "70914a1619e4cf6adbb1d1981cbd8a49483f559b230e7dcfc224335a0635b42e"
E0_REF = Path("/data/mal/audit-1008/work/g_reachable_cap_book_rescore")

# Forward, EXP-024 section 3.
FWD_BLOCK = "forward-1002"
FWD_DIR = Path("/data/mal/blocks/forward-1002")
FWD_FROM, FWD_TO = "2026-10-09T23", "2026-10-16T01"
FINAL_LEDGER = Path("/data/mal/exp012-forward/FINAL_READS.jsonl")

# Never a source of this tool (sealed blocks, EXP-009's owned hours, the ev walk, walk 2, paper and runner paths).
FORBIDDEN_PARTS = ("fresh-0802", "fresh-0808", "fresh-0828", "forward-1002ev", "forward-1016", "walk2", "walk-2",
                   "forward-paper", "runner", "/.e" "nv")
EXP009 = ("2026-09-15T12", "2026-09-18T23")

# Blobs EXP-024 P4.3 records next to the extractor's own, plus tools/forward_v_join.py (the FINAL gate and hour_state
# this tool calls). #476's modules are not on main; each is recorded when present (null otherwise).
RECORDED_BLOBS = ("tools/h5_forward_extract.py", "tools/forward_v_join.py", "tools/latency_curve.py",
                  "tools/paper_curve_math.py", "tools/boostfloor_score.py", "tools/boostfloor_read.py",
                  "tools/boostfloor_inputs.py")
VMAP_ROLE = ("canonical-pool selection only (first V-range pool after complete); meta.v is not an Am.1 V0 source: "
             "the read tool takes V0/V(t) from forward-1002ev, then getTransaction, then the pool account, else the "
             "section 4 missing-V rule")
HOLE_RULE = "mint excluded when its migration hour or the next hour is not usable (window <= 7300 slots, two hours)"
# duckdb errors that mean "this file's lines do not parse into the pinned columns" (checked on duckdb 1.5.6).
PARSE_MESSAGES = ("Malformed JSON", "JSON transform error")


class Refused(Exception):
    """A refusal before or instead of a run. Exit 2."""


class ParseFailed(Refused):
    """A raw file whose lines do not parse (strict lines). E0: a refusal. Forward: the hour is dropped (`parse_failed`)."""


# ---- small helpers -----------------------------------------------------------------------------------------------

def hour_list(start: str, end: str) -> list[str]:
    t, stop = datetime.strptime(start, "%Y-%m-%dT%H"), datetime.strptime(end, "%Y-%m-%dT%H")
    out = []
    while t < stop:
        out.append(t.strftime("%Y-%m-%dT%H"))
        t += timedelta(hours=1)
    return out


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


def md5_file(path: Path) -> str:
    h = hashlib.md5()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


def git_blob_sha(path: Path) -> str:
    data = Path(path).read_bytes()
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def blob_record() -> dict[str, str | None]:
    return {rel: (git_blob_sha(REPO / rel) if (REPO / rel).is_file() else None) for rel in RECORDED_BLOBS}


def git_head() -> dict[str, Any]:
    def run(*a: str) -> str:
        return subprocess.run(["git", "-C", str(REPO), *a], capture_output=True, text=True, timeout=60,
                              stdin=subprocess.DEVNULL).stdout.strip()
    return {"head": run("rev-parse", "HEAD"),
            "extractor_dirty": bool(run("status", "--porcelain", "--", "tools/h5_forward_extract.py"))}


def check_source(path: Path) -> None:
    s = str(path)
    if any(p in s for p in FORBIDDEN_PARTS):
        raise Refused(f"forbidden source path: {s}")


def check_duckdb() -> Any:
    try:
        import duckdb
    except ImportError:
        raise Refused(f"duckdb {DUCKDB_VERSION} is required (e.g. /data/mal/audit-1008/venv/bin/python)") from None
    if duckdb.__version__ != DUCKDB_VERSION:
        raise Refused(f"duckdb {duckdb.__version__} != pinned {DUCKDB_VERSION} (th = hash(trader) depends on it)")
    return duckdb


def new_out(out: Path) -> Path:
    out = Path(out)
    if out.exists() and any(out.iterdir()):
        raise Refused(f"--out {out} is not empty; every run writes into a new, empty directory")
    out.mkdir(parents=True, exist_ok=True)
    return out


def write_excl(path: Path, obj: Any) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=1, sort_keys=True)
        fh.write("\n")


def load_vmap(path: Path) -> dict[str, int]:
    v = json.loads(Path(path).read_text(encoding="utf-8"))["v"]
    return {k: x for k, x in v.items() if x is not None and V_LO <= x <= V_HI}


def raw_file(src_dir: Path, kind: str, hour: str) -> Path | None:
    """convert.py's choice: sorted glob, first file of the hour wins, so `.deduped` sorts before the plain file."""
    for name in (f"{kind}-{hour}.deduped.jsonl.zst", f"{kind}-{hour}.jsonl.zst", f"{kind}-{hour}.jsonl"):
        p = src_dir / kind / name
        if p.is_file():
            return p
    return None


# ---- stage 1: convert --------------------------------------------------------------------------------------------

def connect(duckdb: Any, tmp: Path, memory: str = "4GB", threads: int = 4) -> Any:
    con = duckdb.connect()
    tmp.mkdir(parents=True, exist_ok=True)
    con.execute(f"SET memory_limit='{memory}'; SET threads={threads}; SET temp_directory='{tmp}'; "
                "SET preserve_insertion_order=true")  # tape keeps raw line order (the tie-break below)
    return con


def is_parse_error(e: BaseException) -> bool:
    """A duckdb parse or conversion error of the file's lines. Anything else (out of memory, I/O, SQL) is not."""
    import duckdb
    if isinstance(e, duckdb.ConversionException):
        return True
    return isinstance(e, duckdb.InvalidInputException) and any(m in str(e) for m in PARSE_MESSAGES)


def convert_hour(con: Any, block: str, kind: str, hour: str, src: Path, tape: Path) -> Path:
    check_source(src)
    od = tape / kind
    od.mkdir(parents=True, exist_ok=True)
    out = od / f"{hour}.parquet"
    comp = "zstd" if src.name.endswith(".zst") else "uncompressed"  # duckdb 1.5.6 has no 'none'
    try:
        con.execute(f"COPY (SELECT *, '{block}' AS block, '{hour}' AS hour FROM read_json('{src}', "
                    f"format='newline_delimited', compression='{comp}', columns={COLS[kind]})) "
                    f"TO '{out}.tmp' (FORMAT parquet, COMPRESSION zstd)")
    except Exception as e:  # strict lines: no lenient retry
        Path(f"{out}.tmp").unlink(missing_ok=True)
        if is_parse_error(e):
            raise ParseFailed(f"strict lines: {kind} {hour} does not parse ({type(e).__name__})") from None
        raise Refused(f"convert {kind} {hour} failed ({type(e).__name__}); not a parse error, whole run refused") from None
    os.rename(f"{out}.tmp", out)
    return out


def drop_hour(tape: Path, hour: str) -> None:
    """Forward: remove every parquet (and partial .tmp) already written for `hour`, so a dropped hour is wholly absent."""
    for kind in KINDS:
        for suffix in ("", ".tmp"):
            (tape / kind / f"{hour}.parquet{suffix}").unlink(missing_ok=True)


def next_hour(hour: str) -> str:
    return (datetime.strptime(hour, "%Y-%m-%dT%H") + timedelta(hours=1)).strftime("%Y-%m-%dT%H")


# ---- stage 2: extract (SQL verbatim from g_reachable_cap_book_rescore/extract.py) --------------------------------

def L(fs: Sequence[str]) -> str:
    return "['" + "','".join(fs) + "']"


def extract_day(con: Any, tape: Path, out: Path, day: str, vmap: dict[str, int],
                usable: set[str] | None = None) -> dict[str, Any] | None:
    """`usable` (forward only): the usable hours. A mint whose migration hour or the next hour is not in it is
    excluded before pool selection (the hole rule). None (E0): no exclusion, the reference's rows."""
    T = str(tape)
    hours = sorted(p.name[:13] for p in (tape / "trades").glob("*.parquet"))
    hs = set(hours)
    con.execute("DROP TABLE IF EXISTS vmap")
    con.execute("CREATE TABLE vmap(pool VARCHAR, v BIGINT)")
    con.executemany("INSERT INTO vmap VALUES (?,?)", sorted(vmap.items()))
    (out / "meta").mkdir(parents=True, exist_ok=True)
    (out / "paths").mkdir(parents=True, exist_ok=True)
    pout = f"{out}/paths/{day}.parquet"
    dh = [h for h in hours if h[:10] == day]
    if not dh:
        return None
    last = datetime.strptime(dh[-1], "%Y-%m-%dT%H")
    nxt = [(last + timedelta(hours=i)).strftime("%Y-%m-%dT%H") for i in (1, 2)]
    tf = [f"{T}/trades/{h}.parquet" for h in dh] + [f"{T}/trades/{h}.parquet" for h in nxt if h in hs]
    mf = [f"{T}/migrations/{h}.parquet" for h in dh if os.path.exists(f"{T}/migrations/{h}.parquet")]
    if len(mf) < len(dh):
        return {"day": day, "skipped": "incomplete_migrations"}
    prev = (datetime.strptime(day, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
    cf = [f"{T}/creates/{h}.parquet" for h in hours if h[:10] in (prev, day) and os.path.exists(f"{T}/creates/{h}.parquet")]
    t0 = time.time()
    for tb in ("mig", "cp", "cp1", "cr", "meta"):
        con.execute(f"DROP TABLE IF EXISTS {tb}")
    con.execute(f"CREATE TABLE mig AS SELECT mint, min(slot) mslot, min(block_time) mbt, arg_min(block, slot) blk, arg_min(hour, slot) mhour FROM read_parquet({L(mf)}) WHERE type='complete' GROUP BY mint")
    excluded = 0
    if usable is not None:  # hole rule: decided by usable hours alone, before any pool or trade is looked at
        bad = sorted(h for (h,) in con.execute("SELECT DISTINCT mhour FROM mig").fetchall()
                     if h not in usable or next_hour(h) not in usable)
        if bad:
            excluded = con.execute("SELECT count(*) FROM mig WHERE list_contains(?, mhour)", [bad]).fetchone()[0]
            con.execute("DELETE FROM mig WHERE list_contains(?, mhour)", [bad])
    blk = con.execute("SELECT any_value(blk) FROM mig").fetchone()[0]
    oracle = (blk == "oracle-insample-0922")
    # Determinism (the one change to the SQL): the reference's key (slot, tx_index, event_index) has ties and nulls
    # (09-20: 51 (mint, slot) groups, 426 rows), whose order duckdb leaves to its threads. hour + file_row_number
    # (raw line order, kept by convert) break them. Rows the key orders keep the reference's order.
    order = "t.slot, t.file_row_number" if oracle else "t.slot, t.tx_index, t.event_index, t.hour, t.file_row_number"
    maxslot = con.execute(f"SELECT max(slot) FROM read_parquet({L(tf)})").fetchone()[0]
    con.execute(f"""CREATE TABLE cp AS SELECT t.mint, t.pool, min(t.slot) s0, count(*) n, any_value(v.v) v
       FROM read_parquet({L(tf)}) t JOIN mig m ON t.mint=m.mint JOIN vmap v ON t.pool=v.pool
       WHERE t.venue='pumpswap' AND t.slot>=m.mslot AND t.slot<m.mslot+{WIN} GROUP BY 1,2""")
    con.execute("CREATE TABLE cp1 AS SELECT mint, arg_min(pool, s0) pool, min(s0) s0, arg_min(v, s0) v, count(*) npools FROM cp GROUP BY mint")
    if cf:
        con.execute(f"CREATE TABLE cr AS SELECT mint, min(slot) cslot FROM read_parquet({L(cf)}) WHERE mint IN (SELECT mint FROM mig) GROUP BY mint")
    else:
        con.execute("CREATE TABLE cr(mint VARCHAR, cslot BIGINT)")
    con.execute(f"""CREATE TABLE meta AS SELECT m.mint, cp1.pool, m.mslot, m.mbt, cp1.s0, cp1.v, cp1.npools, m.blk, cr.cslot, '{day}' AS day,
        (cp1.s0+6900 <= {maxslot} AND cp1.s0 - m.mslot <= 400) uncensored
        FROM mig m JOIN cp1 USING(mint) LEFT JOIN cr USING(mint)""")
    con.execute(f"COPY (SELECT * FROM meta ORDER BY mint) TO '{out}/meta/{day}.parquet' (FORMAT parquet)")
    con.execute(f"""COPY (SELECT t.mint, t.slot, (t.side='buy') isbuy, t.sol_lamports sol, t.token_raw tok,
          t.quote_reserve q, t.base_reserve b, hash(t.trader) th, t.block_time bt
        FROM read_parquet({L(tf)}, file_row_number=true) t JOIN cp1 ON t.pool=cp1.pool AND t.mint=cp1.mint JOIN mig m ON t.mint=m.mint
        WHERE t.venue='pumpswap' AND t.slot>=m.mslot AND t.slot<m.mslot+{WIN}
        ORDER BY t.mint, {order}) TO '{pout}.tmp' (FORMAT parquet)""")
    os.rename(pout + ".tmp", pout)
    s = con.execute("SELECT count(*), sum(uncensored::int), sum((npools>1)::int), sum((cslot IS NOT NULL)::int) FROM meta").fetchone()
    return {"day": day, "blk": blk, "order": "oracle_order" if oracle else "tx_order",
            "migs": con.execute("SELECT count(*) FROM mig").fetchone()[0], "excluded_hole_mints": excluded,
            "canon_uncens_multipool_hascreate": list(s), "secs": round(time.time() - t0, 1)}


# ---- canonical md5 -----------------------------------------------------------------------------------------------

META_ORDER = "mint"
PATHS_SET_ORDER = "mint, slot, isbuy, sol, tok, q, b, th, bt"  # every column: md5 of the row multiset
ROWCOLS = "(a.isbuy, a.sol, a.tok, a.q, a.b, a.th, a.bt) IS DISTINCT FROM (b.isbuy, b.sol, b.tok, b.q, b.b, b.th, b.bt)"


def order_check(duckdb: Any, mine: Path, ref: Path, tape: Path, meta: Path) -> dict[str, Any]:
    """Positional comparison of two paths files. Binding: the (mint, slot) sequence is identical, and every row whose
    position differs lies in a (mint, slot) group the reference's key leaves open (a duplicate or null
    (tx_index, event_index) among the canonical pool's PumpSwap rows of that slot)."""
    con = duckdb.connect()
    con.execute("SET preserve_insertion_order=true; SET threads=4")
    con.execute(f"CREATE TABLE a AS SELECT row_number() OVER () rn, * FROM read_parquet('{mine}')")
    con.execute(f"CREATE TABLE b AS SELECT row_number() OVER () rn, * FROM read_parquet('{ref}')")
    ms = con.execute("SELECT count(*) FROM a JOIN b USING(rn) WHERE (a.mint, a.slot) IS DISTINCT FROM (b.mint, b.slot)").fetchone()[0]
    nd = con.execute(f"SELECT count(*) FROM a JOIN b USING(rn) WHERE {ROWCOLS}").fetchone()[0]
    con.execute(f"CREATE TABLE d AS SELECT DISTINCT a.mint, a.slot FROM a JOIN b USING(rn) WHERE {ROWCOLS}")
    con.execute(f"""CREATE TABLE u AS SELECT DISTINCT t.mint, t.slot FROM read_parquet('{tape}/trades/*.parquet') t
        JOIN read_parquet('{meta}') m ON t.mint=m.mint AND t.pool=m.pool WHERE t.venue='pumpswap'
        GROUP BY t.mint, t.slot, t.tx_index, t.event_index
        HAVING count(*) > 1 OR bool_or(t.tx_index IS NULL OR t.event_index IS NULL)""")
    outside = con.execute("SELECT count(*) FROM d ANTI JOIN u USING(mint, slot)").fetchone()[0]
    groups = con.execute("SELECT count(*) FROM d").fetchone()[0]
    rows_a, rows_b = (con.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in ("a", "b"))
    con.close()
    return {"mint_slot_sequence_diffs": ms, "rows_moved": nd, "groups_moved": groups,
            "groups_moved_outside_open_ties": outside, "ok": ms == 0 and outside == 0 and rows_a == rows_b}


def canonical_md5(duckdb: Any, path: Path, order: str | None) -> dict[str, Any]:
    """md5 over the schema line, then one line per row ('|'-joined VARCHAR casts, NULL as \\N), rows ordered by
    `order` (meta: mint) or in file order (paths: fixed by the extract's ORDER BY)."""
    con = duckdb.connect()
    con.execute("SET preserve_insertion_order=true; SET threads=2")
    src = f"read_parquet('{path}')"
    cols = [(r[0], r[1]) for r in con.execute(f"DESCRIBE SELECT * FROM {src}").fetchall()]
    h = hashlib.md5()
    h.update(json.dumps(cols).encode() + b"\n")
    expr = "concat_ws('|', " + ", ".join(f"coalesce(CAST(\"{c}\" AS VARCHAR), '\\N')" for c, _ in cols) + ")"
    cur = con.execute(f"SELECT {expr} FROM {src}" + (f" ORDER BY {order}" if order else ""))
    n = 0
    while True:
        rows = cur.fetchmany(200_000)
        if not rows:
            break
        h.update("".join(r[0] + "\n" for r in rows).encode())
        n += len(rows)
    con.close()
    return {"rows_md5": h.hexdigest(), "rows": n, "file_md5": md5_file(path)}


# ---- E0-H5 -------------------------------------------------------------------------------------------------------

def read_view(view: Path) -> dict[str, str]:
    out = {}
    for line in (view / "VIEW.sha256").read_text(encoding="utf-8").splitlines():
        if line.strip():
            sha, rel = line.split(None, 1)
            out[rel.strip().removeprefix("./")] = sha
    return out


def run_e0(out: Path) -> int:
    duckdb = check_duckdb()
    g = git_head()
    if g["extractor_dirty"]:
        raise Refused("tools/h5_forward_extract.py differs from HEAD; E0 records a committed blob")
    check_source(E0_VIEW)
    if sha256_file(E0_VMAP) != E0_VMAP_SHA256:
        raise Refused("pool_v_0909.json sha256 differs from the pin")
    hours = hour_list(E0_FROM, E0_TO)
    if any(EXP009[0] <= h < EXP009[1] for h in hours):
        raise Refused("E0 hours touch EXP-009's owned block")
    view = read_view(E0_VIEW)
    files: list[tuple[str, str, Path]] = []
    for h in hours:
        for kind in KINDS:
            p = raw_file(E0_VIEW, kind, h)
            if p is None:
                continue
            rel = f"{kind}/{p.name}"
            if rel not in view:
                raise Refused(f"{rel} is not in VIEW.sha256")
            if sha256_file(p) != view[rel]:
                raise Refused(f"{rel} does not hash to its VIEW.sha256 line")
            files.append((kind, h, p))
    out = new_out(out)
    con = connect(duckdb, out / "tmp")
    for kind, h, p in files:
        convert_hour(con, E0_BLOCK, kind, h, p, out / "tape")
    stats = extract_day(con, out / "tape", out, E0_DAY, load_vmap(E0_VMAP))
    con.close()
    cmp: dict[str, Any] = {}
    for part, order in (("meta", META_ORDER), ("paths", PATHS_SET_ORDER)):
        mine = canonical_md5(duckdb, out / part / f"{E0_DAY}.parquet", order)
        ref = canonical_md5(duckdb, E0_REF / part / f"{E0_DAY}.parquet", order)
        cmp[part] = {"extractor": mine, "reference": ref, "rows_equal": mine["rows_md5"] == ref["rows_md5"],
                     "file_equal": mine["file_md5"] == ref["file_md5"]}
    pm = out / "paths" / f"{E0_DAY}.parquet"
    pr = E0_REF / "paths" / f"{E0_DAY}.parquet"
    cmp["paths"]["file_order_md5"] = {"extractor": canonical_md5(duckdb, pm, None)["rows_md5"],
                                      "reference": canonical_md5(duckdb, pr, None)["rows_md5"]}
    cmp["paths"]["order"] = order_check(duckdb, pm, pr, out / "tape", out / "meta" / f"{E0_DAY}.parquet")
    ok = all(c["rows_equal"] for c in cmp.values()) and cmp["paths"]["order"]["ok"]
    rec = {
        "kind": "EXP-024 E0-H5 extractor (P4.1)", "pass": ok, "day": E0_DAY, "block": E0_BLOCK,
        "view": str(E0_VIEW), "view_sha256": sha256_file(E0_VIEW / "VIEW.sha256"),
        "raw_files": len(files), "hours": [E0_FROM, E0_TO],
        "vmap": str(E0_VMAP), "vmap_sha256": E0_VMAP_SHA256, "reference": str(E0_REF),
        "compare": cmp, "extract": stats, "duckdb": duckdb.__version__, "git": g, "blobs": blob_record(),
        "written_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    write_excl(out / "E0-H5.json", rec)
    for part, c in cmp.items():
        print(f"E0-H5 {part}: rows_md5 {c['extractor']['rows_md5']} vs ref {c['reference']['rows_md5']} "
              f"rows {c['extractor']['rows']}/{c['reference']['rows']} rows_equal={c['rows_equal']} "
              f"file_equal={c['file_equal']}")
    print(f"E0-H5 paths order: {json.dumps(cmp['paths']['order'], sort_keys=True)}")
    print(f"E0-H5 {'PASS' if ok else 'FAIL'} view_sha256={rec['view_sha256']} record={out / 'E0-H5.json'} "
          f"sha256={sha256_file(out / 'E0-H5.json')}")
    return 0 if ok else 1


# ---- forward -----------------------------------------------------------------------------------------------------

def check_e0_record(path: Path) -> dict[str, Any]:
    try:
        rec = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise Refused(f"no readable E0-H5 record at {path}") from None
    if rec.get("pass") is not True or rec.get("day") != E0_DAY or rec.get("block") != E0_BLOCK:
        raise Refused("the E0-H5 record is not a PASS on the pinned day")
    me = git_blob_sha(REPO / "tools/h5_forward_extract.py")
    if (rec.get("blobs") or {}).get("tools/h5_forward_extract.py") != me:
        raise Refused("the E0-H5 record was written by a different extractor blob; rerun E0 on this blob")
    if rec.get("duckdb") != DUCKDB_VERSION:
        raise Refused("the E0-H5 record was written under a different duckdb")
    return rec


def last_verify(walk_dir: Path, hour: str) -> dict[str, Any] | None:
    last = None
    try:
        for line in (walk_dir / "verify.jsonl").read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if isinstance(rec, dict) and rec.get("hour") == hour:
                last = rec
    except OSError:
        return None
    return last


def forward_hour(walk_dir: Path, hour: str) -> tuple[dict[str, Path], str]:
    """({kind: file}, reason). Trades through forward_v_join.hour_state (sealed, verified, strict lines, sha);
    creates and migrations must hash to the same verify line's sha256[kind]."""
    from tools.forward_v_join import hour_state
    path, state = hour_state(walk_dir, hour, strict=True)
    if path is None:
        return {}, state
    sha = (last_verify(walk_dir, hour) or {}).get("sha256") or {}
    files = {"trades": path}
    for kind in ("creates", "migrations"):
        p = raw_file(walk_dir, kind, hour)
        if p is None or p.name.endswith(".deduped.jsonl.zst"):
            return {}, f"{kind}_not_walked"
        if not isinstance(sha.get(kind), str):
            return {}, f"{kind}_not_verified"
        if sha256_file(p) != sha[kind]:
            return {}, f"{kind}_sha_mismatch"
        files[kind] = p
    return files, "ok"


def run_forward(out: Path, vmap_path: Path, e0_record: Path) -> int:
    from tools.forward_v_join import final_marker, Refused as JoinRefused
    try:
        marker = final_marker(FINAL_LEDGER)  # first: nothing under forward-1002 is opened before the FINAL
    except JoinRefused as e:
        raise Refused(str(e)) from None
    duckdb = check_duckdb()
    e0 = check_e0_record(e0_record)
    check_source(FWD_DIR)
    vmap = load_vmap(vmap_path)
    out = new_out(out)
    hours = hour_list(FWD_FROM, FWD_TO)
    reasons: dict[str, str] = {}
    usable: list[tuple[str, dict[str, Path]]] = []
    for h in hours:
        files, reason = forward_hour(FWD_DIR, h)
        reasons[h] = reason
        if reason == "ok":
            usable.append((h, files))
    con = connect(duckdb, out / "tmp")
    ok_hours: list[str] = []
    for h, files in usable:
        try:
            for kind, p in files.items():
                convert_hour(con, FWD_BLOCK, kind, h, p, out / "tape")
        except ParseFailed:  # a bad hour (section 11 tolerates up to 5%); any other error refuses the run
            drop_hour(out / "tape", h)
            reasons[h] = "parse_failed"
            continue
        ok_hours.append(h)
    days = sorted({h[:10] for h in ok_hours})
    stats = [extract_day(con, out / "tape", out, d, vmap, usable=set(ok_hours)) for d in days]
    con.close()
    md5s = {}
    for d in days:
        for part, order in (("meta", META_ORDER), ("paths", None)):
            p = out / part / f"{d}.parquet"
            if p.is_file():
                md5s[f"{part}/{d}"] = {**canonical_md5(duckdb, p, order), "sha256": sha256_file(p)}
    counts: dict[str, int] = {}
    for r in reasons.values():
        counts[r] = counts.get(r, 0) + 1
    man = {
        "kind": "EXP-024 forward extractor", "block": FWD_BLOCK, "walk_dir": str(FWD_DIR), "hours": [FWD_FROM, FWD_TO],
        "hour_reasons": reasons, "reason_counts": counts, "days": days,
        "skipped_days": [s["day"] for s in stats if s and s.get("skipped")],
        "hole_rule": HOLE_RULE,
        "excluded_hole_mints_by_day": {s["day"]: s["excluded_hole_mints"] for s in stats if s and not s.get("skipped")},
        "vmap": str(vmap_path), "vmap_sha256": sha256_file(vmap_path), "vmap_pools_in_range": len(vmap),
        "vmap_role": VMAP_ROLE,
        "final_marker": {k: marker.get(k) for k in ("written_utc", "ts", "final") if k in marker},
        "e0_record": str(e0_record), "e0_record_sha256": sha256_file(e0_record), "e0_view_sha256": e0.get("view_sha256"),
        "outputs": md5s, "duckdb": duckdb.__version__, "git": git_head(), "blobs": blob_record(),
        "written_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    write_excl(out / "manifest.json", man)
    print(f"forward extract: hours {len(hours)}, usable {len(ok_hours)}, reasons {json.dumps(counts, sort_keys=True)}, "
          f"days {len(days)}, skipped {len(man['skipped_days'])}, "
          f"hole-excluded {sum(man['excluded_hole_mints_by_day'].values())}, manifest sha256 {sha256_file(out / 'manifest.json')}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="mode", required=True)
    e = sub.add_parser("e0", help="E0-H5 on 2026-09-20 (fast-pool-0918) against g_reachable_cap_book_rescore")
    e.add_argument("--out", type=Path, required=True)
    f = sub.add_parser("forward", help="forward-1002 [10-09T23, 10-16T01) after the FINAL marker")
    f.add_argument("--out", type=Path, required=True)
    f.add_argument("--vmap", type=Path, required=True)
    f.add_argument("--e0-record", type=Path, required=True)
    a = ap.parse_args(argv)
    try:
        if a.mode == "e0":
            return run_e0(a.out)
        return run_forward(a.out, a.vmap, a.e0_record)
    except Refused as ex:
        print(f"REFUSED: {ex}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
