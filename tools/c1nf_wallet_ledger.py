#!/usr/bin/env python3
"""C1-NF wallet ledger: a deterministic, integer-lamport daily wallet ledger fed from a trade tape.

Why this exists. C1's stage-2 model reads wallet features from a prior-day wallet ledger built by
`/data/mal/hunt-1008/c1-cascade-postgrad/scripts/01_wallet_daily.py`. That script sums float SOL
across (wallet, mint) groups in DuckDB's parallel aggregation. The order of the float sum changes
between runs, and `cash` changes sign for about 3 wallets a day, which flips `skill = cash > 0`
and shifts the wallet features (C1-NF VERIFY, section 2). This module computes the same per-wallet
daily numbers as exact integer lamports, so a rerun is bit-identical, and so is a rerun from a
different source (live tip dir or archive).

Semantics, kept from 01_wallet_daily.py on purpose:
  * rows with venue in ('pump_bonding', 'pumpswap'); NO quote-mint filter. About 40 percent of the
    PumpSwap rows are pools whose quote is not WSOL (mint == So111...); their `sol_lamports` is
    summed as if it were lamports, exactly as C1 did. A change to that is a new ledger version.
  * th = DuckDB hash(trader) (UBIGINT). The hash function is version dependent: duckdb 1.5.6 is
    pinned and `check_hash_pin` refuses any other function that returns different values.
  * per (th, mint): n rows, nbond bonding rows, nb buys, ns sells, buy and sell lamports.
  * per th: n, nm (mints), nbond, buy, sell, nwin = #mints with buys, sells and sell > buy,
    nrt = #mints with buys and sells, cash = sell - buy summed over mints.
  * "day" = the UTC day in the hour-file name. The exploration tape names files by the block_time
    hour. The tip follower names files by the UTC hour of t_recv_ms (the receive time), so a block
    received just after 00:00 belongs to the next day's file. The ledger for day D is "everything
    that was in the files named D". That is causal for a ledger used from D+1 on.

The pinned reference. EXP-025 pinned a deterministic version of the same step
(ARTIFACTS/exp025/ledger/01_wallet_daily_det.py, sha256 bc1838f1..., commit 0799c4c). It sums integer
lamports and writes `buy`, `sell`, `cash` on a 2^-20 SOL grid, round(lamports * 2^20 / 1e9) / 2^20,
so that the float sums over prior days inside the pinned 11_passA.py are exact in any add order.
`tools/c1nf_wallet_det_pinned.py` is a verbatim copy; this module checks its sha256 before it uses
`to_grid`, and on the exploration tape its wl/ output must equal the pinned script's output (the tests
and the PR run that comparison). Nothing here re-derives the grid.

Files.
  daily/wl-<day>.npz        one row per wallet, sorted by th, int64 lamports and counts.
                            Deterministic zip bytes (fixed timestamps), no wall clock inside.
  daily/wl-<day>.manifest.json   sources, hashes, timings (not deterministic, never compared).
  asof/asof-<day>/*.npy     cumulative ledger over daily files with day < <day>, one .npy per
                            column, th sorted. Open with numpy mmap_mode='r' (`AsofLedger`). Besides
                            the exact lamport sums it holds `buy_q`, `cash_q`: the sums of the daily
                            grid units, so cash = cash_q / 2^20 equals what the pinned 11_passA.py
                            computes from the pinned wl/ files.
  The optional `--wl-parquet-dir` writes `<day>.parquet`, the pinned wl/ file: C1's columns and dtypes
  (th UBIGINT, n/nbond/nwin/nrt/buy/sell/cash DOUBLE, nm BIGINT), SOL columns on the 2^-20 grid. A
  drop-in for 11_passA.py.

Subcommands: day, asof, rollup (the daily job), coverage, parity.
Exit codes: 0 ok, 2 usage, 3 refused (bad or incomplete input; nothing written), 4 output exists
and differs.

Needs duckdb 1.5.6 and numpy. No network, no keys. Paper tooling only.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np

SCHEMA_VERSION = 1
PINNED_DUCKDB = "1.5.6"
# duckdb 1.5.6 hash(VARCHAR) -> UBIGINT. 01_wallet_daily.py and 11_passA.py key wallets on this.
HASH_VECTORS = {
    "abc": 1924864467101078684,
    "4hkvaqAwuQjFzkTfD6vjSbG3JUPM6EqgQSPou4buPGmD": 1048625625799252214,
}
PINNED_DET_SHA256 = "bc1838f10ff1798dec1e896da34d1a82b9707b404d2e36de317edf0034bf3be5"
PINNED_DET_PATH = Path(__file__).with_name("c1nf_wallet_det_pinned.py")
VENUES = ("pump_bonding", "pumpswap")
WSOL_MINT = "So11111111111111111111111111111111111111112"

# Daily columns. All int64 except th (uint64). `*_lamports` are exact integer sums.
DAILY_COLS = ("n", "nm", "nbond", "nwin", "nrt", "buy_lamports", "sell_lamports", "cash_lamports")
# As-of columns: the daily ones, the sums of the daily 2^-20 grid units of buy and cash, and ndays
# (daily files the wallet appears in).
ASOF_COLS = DAILY_COLS + ("buy_q", "cash_q", "ndays")
# Order of the matrix 11_passA.py builds from the prior-day ledger: n, nbond, cash, nwin, nrt, ndays, buy.
PASSA_COLS = ("n", "nbond", "cash", "nwin", "nrt", "ndays", "buy")
LAMPORTS_PER_SOL = 1e9

TIP_JSON_COLUMNS = (
    "{venue:'VARCHAR',mint:'VARCHAR',trader:'VARCHAR',side:'VARCHAR',sol_lamports:'BIGINT',"
    "signature:'VARCHAR',event_index:'INTEGER'}"
)
DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
HOUR_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}$")
TIP_NAME_RE = re.compile(r"^trades-(\d{4}-\d{2}-\d{2}T\d{2})\.jsonl(\.zst)?$")
TAPE_NAME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2})\.parquet$")

EXIT_OK, EXIT_USAGE, EXIT_REFUSED, EXIT_DIFFERS = 0, 2, 3, 4


class Refused(RuntimeError):
    """Input is bad or incomplete. Nothing was written. Exit 3."""


class OutputDiffers(RuntimeError):
    """An output with this name exists and its content differs. Exit 4."""


# --------------------------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------------------------


def _q(path: str | Path) -> str:
    s = str(path)
    if "'" in s or "\n" in s:
        raise Refused(f"unsafe path for SQL: {s!r}")
    return s


def check_day(day: str) -> str:
    if not DAY_RE.match(day):
        raise Refused(f"bad day {day!r}, want YYYY-MM-DD")
    try:
        dt.date.fromisoformat(day)
    except ValueError as exc:
        raise Refused(f"bad day {day!r}: {exc}") from exc
    return day


def next_day(day: str) -> str:
    return (dt.date.fromisoformat(day) + dt.timedelta(days=1)).isoformat()


def day_hours(day: str) -> list[str]:
    return [f"{day}T{h:02d}" for h in range(24)]


def day_bounds_ms(day: str) -> tuple[int, int]:
    d0 = dt.datetime.fromisoformat(day).replace(tzinfo=dt.timezone.utc)
    return int(d0.timestamp() * 1000), int((d0 + dt.timedelta(days=1)).timestamp() * 1000)


def file_sha256(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(1 << 20)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def content_sha256(arrays: dict[str, np.ndarray]) -> str:
    """Hash of the array contents (name, dtype, shape, little-endian bytes), independent of the zip layer."""
    h = hashlib.sha256()
    for name in sorted(arrays):
        a = np.ascontiguousarray(arrays[name])
        h.update(f"{name}|{a.dtype.str}|{a.shape}|".encode())
        h.update(memoryview(a.view(np.uint8).reshape(-1)) if a.size else b"")
    return h.hexdigest()


def _atomic_write_json(path: Path, obj: Any) -> None:
    tmp = path.with_name(path.name + f".tmp{os.getpid()}")
    tmp.write_text(json.dumps(obj, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def write_npz_deterministic(path: Path, arrays: dict[str, np.ndarray]) -> None:
    """np.load-compatible .npz with fixed zip timestamps, so the same arrays give the same bytes."""
    tmp = path.with_name(path.name + f".tmp{os.getpid()}")
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for name in sorted(arrays):
            info = zipfile.ZipInfo(name + ".npy", date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o600 << 16
            with zf.open(info, "w", force_zip64=True) as fh:
                np.lib.format.write_array(fh, np.ascontiguousarray(arrays[name]), allow_pickle=False)
    os.replace(tmp, path)


def read_npz(path: Path) -> dict[str, np.ndarray]:
    with np.load(path, allow_pickle=False) as z:
        return {k: z[k] for k in z.files}


_PINNED: Any = None


def pinned():
    """The EXP-025 pinned ledger module (verbatim copy), loaded only if its sha256 is the pinned one."""
    global _PINNED
    if _PINNED is None:
        got = file_sha256(PINNED_DET_PATH)
        if got != PINNED_DET_SHA256:
            raise Refused(f"{PINNED_DET_PATH} sha256 {got} != pinned {PINNED_DET_SHA256}")
        spec = importlib.util.spec_from_file_location("c1nf_wallet_det_pinned", PINNED_DET_PATH)
        mod = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(mod)
        _PINNED = mod
    return _PINNED


def to_grid(lamports: np.ndarray) -> np.ndarray:
    """Lamports -> float64 SOL on the 2^-20 grid. This IS the pinned function."""
    return pinned().to_grid(lamports)


def grid_units(lamports: np.ndarray) -> np.ndarray:
    """Lamports -> int64 count of 2^-20 SOL units, such that grid_units(l) / 2^20 == to_grid(l) exactly."""
    g = pinned().GRID
    return np.rint(to_grid(lamports) * g).astype(np.int64)


# --------------------------------------------------------------------------------------------
# duckdb
# --------------------------------------------------------------------------------------------


def _duckdb():
    try:
        import duckdb  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover
        raise Refused(f"duckdb is required (pinned {PINNED_DUCKDB}): {exc}") from exc
    return duckdb


def connect(threads: int = 3, mem_gb: float = 6.0, tmp_dir: str | Path | None = None):
    duckdb = _duckdb()
    con = duckdb.connect()
    con.execute(f"SET threads={int(threads)}")
    con.execute(f"SET memory_limit='{mem_gb:g}GB'")
    con.execute("SET preserve_insertion_order=false")
    if tmp_dir is not None:
        Path(tmp_dir).mkdir(parents=True, exist_ok=True)
        con.execute(f"SET temp_directory='{_q(tmp_dir)}'")
        con.execute("SET max_temp_directory_size='40GB'")
    check_hash_pin(con)
    return con


def check_hash_pin(con) -> str:
    """Refuse a duckdb whose hash(VARCHAR) differs from the one the C1 features were built with."""
    for text, want in HASH_VECTORS.items():
        got = con.execute("SELECT hash(?)", [text]).fetchone()[0]
        if int(got) != want:
            raise Refused(
                f"duckdb hash() differs from the pinned function for {text!r}: got {got}, want {want}. "
                f"Pinned duckdb is {PINNED_DUCKDB}."
            )
    return str(_duckdb().__version__)


# --------------------------------------------------------------------------------------------
# source discovery
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class SourceFile:
    hour: str
    path: str
    kind: str  # "parquet" | "jsonl" | "jsonl.zst"
    size: int


def discover_tape(tape_dir: str | Path, day: str) -> list[SourceFile]:
    """Exploration tape: <tape_dir>/trades/<YYYY-MM-DDTHH>.parquet (the same glob 01_wallet_daily.py uses)."""
    base = Path(tape_dir) / "trades"
    out: list[SourceFile] = []
    for p in sorted(base.glob(f"{day}T*.parquet")):
        m = TAPE_NAME_RE.match(p.name)
        if m:
            out.append(SourceFile(m.group(1), str(p), "parquet", p.stat().st_size))
    return out


def discover_tip(tip_dirs: Sequence[str | Path], day: str) -> list[SourceFile]:
    """Tip follower trade files: trades-<hour>.jsonl[.zst]. First match wins, in `tip_dirs` order;
    inside a directory the .zst comes before the plain file."""
    out: list[SourceFile] = []
    for hour in day_hours(day):
        for d in tip_dirs:
            hit = None
            for suffix, kind in ((".jsonl.zst", "jsonl.zst"), (".jsonl", "jsonl")):
                p = Path(d) / f"trades-{hour}{suffix}"
                if p.is_file():
                    hit = SourceFile(hour, str(p), kind, p.stat().st_size)
                    break
            if hit is not None:
                out.append(hit)
                break
    return out


def tip_day_closed(tip_dirs: Sequence[str | Path], day: str) -> bool:
    """The day is closed when the next day's first hour has a non-empty trade file. The follower
    stamps t_recv_ms non-decreasing, so the D+1 T00 file only gets rows once D's last file is final."""
    nxt = discover_tip(tip_dirs, next_day(day))
    return any(sf.hour == f"{next_day(day)}T00" and sf.size > 0 for sf in nxt)


def verify_sidecar_sha(sf: SourceFile) -> bool:
    """Archive files carry <name>.sha256 = sha256 of the DECOMPRESSED jsonl. True if it matches."""
    side = Path(sf.path[: -len(".zst")] if sf.kind == "jsonl.zst" else sf.path)
    side = side.with_name(side.name[: -len(".jsonl")] + ".jsonl.sha256")
    if not side.is_file():
        raise Refused(f"no sha256 sidecar for {sf.path}")
    want = side.read_text().split()[0].strip()
    h = hashlib.sha256()
    if sf.kind == "jsonl.zst":
        if shutil.which("zstd") is None:
            raise Refused("zstd CLI not found; cannot --verify-sha a .zst file")
        proc = subprocess.Popen(["zstd", "-dc", sf.path], stdout=subprocess.PIPE)
        assert proc.stdout is not None
        for chunk in iter(lambda: proc.stdout.read(1 << 20), b""):
            h.update(chunk)
        if proc.wait() != 0:
            raise Refused(f"zstd failed on {sf.path}")
    else:
        with open(sf.path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
    return h.hexdigest() == want


# --------------------------------------------------------------------------------------------
# aggregation (exact integer sums)
# --------------------------------------------------------------------------------------------

_PM_DDL = (
    "CREATE OR REPLACE TEMP TABLE pm (th UBIGINT, mint VARCHAR, n HUGEINT, nbond HUGEINT, nb HUGEINT, "
    "ns HUGEINT, buy HUGEINT, sell HUGEINT)"
)

_DAILY_SQL = """
CREATE OR REPLACE TEMP TABLE daily AS
SELECT th,
       sum(n)::BIGINT n, count(*)::BIGINT nm, sum(nbond)::BIGINT nbond,
       sum(buy)::BIGINT buy_lamports, sum(sell)::BIGINT sell_lamports,
       sum((ns > 0 AND nb > 0 AND sell > buy)::INT)::BIGINT nwin,
       sum((ns > 0 AND nb > 0)::INT)::BIGINT nrt,
       sum(sell - buy)::BIGINT cash_lamports
FROM (SELECT th, mint, sum(n) n, sum(nbond) nbond, sum(nb) nb, sum(ns) ns, sum(buy) buy, sum(sell) sell
      FROM pm GROUP BY th, mint)
GROUP BY th ORDER BY th
"""


def _source_select(sf: SourceFile, dedupe: bool) -> str:
    p = _q(sf.path)
    if sf.kind == "parquet":
        return f"SELECT venue, mint, trader, side, sol_lamports FROM read_parquet('{p}')"
    comp = "zstd" if sf.kind == "jsonl.zst" else "uncompressed"
    return (
        "SELECT venue, mint, trader, side, sol_lamports, signature, event_index FROM "
        f"read_json('{p}', format='newline_delimited', compression='{comp}', columns={TIP_JSON_COLUMNS})"
    )


def aggregate_files(con, files: Sequence[SourceFile], dedupe: bool) -> dict[str, Any]:
    """Aggregate source files into the TEMP table `daily` and return row statistics.

    Each file is reduced to per-(th, mint) partial sums (HUGEINT, exact), appended to `pm`, then the
    partials are merged. Integer addition is associative, so file order, thread count and chunking
    cannot change a result.
    """
    con.execute(_PM_DDL)
    stats = {"rows_in": 0, "rows_dedup_dropped": 0, "rows_null_trader": 0, "rows_used": 0}
    for sf in files:
        con.execute(f"CREATE OR REPLACE TEMP TABLE hr AS {_source_select(sf, dedupe)}")
        n_in = con.execute("SELECT count(*) FROM hr").fetchone()[0]
        if sf.kind != "parquet" and dedupe:
            con.execute("CREATE OR REPLACE TEMP TABLE hr2 AS SELECT DISTINCT * FROM hr")
            con.execute("DROP TABLE hr")
            con.execute("ALTER TABLE hr2 RENAME TO hr")
            n_dist = con.execute("SELECT count(*) FROM hr").fetchone()[0]
            n_keys = con.execute("SELECT count(*) FROM (SELECT DISTINCT signature, event_index FROM hr)").fetchone()[0]
            if n_keys != n_dist:
                raise Refused(
                    f"{sf.path}: {n_dist - n_keys} rows share (signature, event_index) but differ in content"
                )
            stats["rows_dedup_dropped"] += n_in - n_dist
        n_null = con.execute(
            "SELECT count(*) FROM hr WHERE venue IN ('pump_bonding', 'pumpswap') AND trader IS NULL"
        ).fetchone()[0]
        stats["rows_in"] += n_in
        stats["rows_null_trader"] += n_null
        con.execute(
            """
            INSERT INTO pm
            SELECT hash(trader) th, mint, count(*)::HUGEINT n,
                   COALESCE(sum((venue = 'pump_bonding')::INT), 0)::HUGEINT nbond,
                   COALESCE(sum((side = 'buy')::INT), 0)::HUGEINT nb,
                   COALESCE(sum((side = 'sell')::INT), 0)::HUGEINT ns,
                   COALESCE(sum(CASE WHEN side = 'buy' THEN COALESCE(sol_lamports, 0) ELSE 0 END), 0)::HUGEINT buy,
                   COALESCE(sum(CASE WHEN side = 'sell' THEN COALESCE(sol_lamports, 0) ELSE 0 END), 0)::HUGEINT sell
            FROM hr WHERE venue IN ('pump_bonding', 'pumpswap') AND trader IS NOT NULL
            GROUP BY 1, 2
            """
        )
        stats["rows_used"] += con.execute(
            "SELECT count(*) FROM hr WHERE venue IN ('pump_bonding', 'pumpswap') AND trader IS NOT NULL"
        ).fetchone()[0]
    con.execute("DROP TABLE IF EXISTS hr")
    con.execute(_DAILY_SQL)
    return stats


def fetch_daily(con) -> dict[str, np.ndarray]:
    raw = con.execute("SELECT th, " + ", ".join(DAILY_COLS) + " FROM daily ORDER BY th").fetchnumpy()
    out: dict[str, np.ndarray] = {"th": np.ascontiguousarray(raw["th"], dtype=np.uint64)}
    for c in DAILY_COLS:
        col = raw[c]
        if np.ma.isMaskedArray(col):
            col = col.filled(0)
        out[c] = np.ascontiguousarray(col, dtype=np.int64)
    return out


def validate_daily(a: dict[str, np.ndarray]) -> None:
    th = a["th"]
    if len(th) > 1 and not bool(np.all(th[1:] > th[:-1])):
        raise Refused("daily th is not strictly increasing")
    if not np.array_equal(a["cash_lamports"], a["sell_lamports"] - a["buy_lamports"]):
        raise Refused("cash_lamports != sell_lamports - buy_lamports")
    if len(th) and (a["n"].min() < 1 or a["nm"].min() < 1):
        raise Refused("a wallet row has n < 1 or nm < 1")
    if len(th) and (a["buy_lamports"].min() < 0 or a["sell_lamports"].min() < 0):
        raise Refused("negative lamport sum")
    if not (np.all(a["nwin"] <= a["nrt"]) and np.all(a["nrt"] <= a["nm"]) and np.all(a["nbond"] <= a["n"])):
        raise Refused("counts out of order (nwin <= nrt <= nm, nbond <= n)")


def to_wl_float(a: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    """The pinned wl/ view: th uint64; n, nbond, nwin, nrt float64; nm int64; buy, sell, cash float64 SOL
    on the 2^-20 grid (the pinned `to_grid`)."""
    f = np.float64
    return {
        "th": a["th"],
        "n": a["n"].astype(f),
        "nm": a["nm"].astype(np.int64),
        "nbond": a["nbond"].astype(f),
        "buy": to_grid(a["buy_lamports"]),
        "sell": to_grid(a["sell_lamports"]),
        "nwin": a["nwin"].astype(f),
        "nrt": a["nrt"].astype(f),
        "cash": to_grid(a["cash_lamports"]),
    }


# SQL form of the pinned to_grid: round_even(l * (2^20 / 1e9)) / 2^20 (numpy rint is round-half-even too).
# write_wl_parquet checks the result against the pinned to_grid on the same numbers before it writes the file.
_WL_PARQUET_SQL = """
CREATE OR REPLACE TEMP TABLE wlg AS
SELECT th, n::DOUBLE n, nm, nbond::DOUBLE nbond,
       round_even(buy_lamports::DOUBLE * CAST('{scale!r}' AS DOUBLE), 0) / CAST('{grid!r}' AS DOUBLE) buy,
       round_even(sell_lamports::DOUBLE * CAST('{scale!r}' AS DOUBLE), 0) / CAST('{grid!r}' AS DOUBLE) sell,
       nwin::DOUBLE nwin, nrt::DOUBLE nrt,
       round_even(cash_lamports::DOUBLE * CAST('{scale!r}' AS DOUBLE), 0) / CAST('{grid!r}' AS DOUBLE) cash
FROM daily ORDER BY th
"""


def write_wl_parquet(con, arrays: dict[str, np.ndarray], out: Path) -> None:
    pin = pinned()
    con.execute(_WL_PARQUET_SQL.format(scale=pin._SCALE, grid=pin.GRID))
    got = con.execute("SELECT th, buy, sell, cash FROM wlg ORDER BY th").fetchnumpy()
    want = to_wl_float(arrays)
    if not (
        np.array_equal(np.asarray(got["th"], dtype=np.uint64), want["th"])
        and all(np.array_equal(np.asarray(got[c], dtype=np.float64), want[c]) for c in ("buy", "sell", "cash"))
    ):
        raise Refused("SQL grid columns differ from the pinned to_grid; wl parquet not written")
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".tmp")
    con.execute(f"COPY (SELECT * FROM wlg ORDER BY th) TO '{_q(tmp)}' (FORMAT parquet, COMPRESSION zstd)")
    os.replace(tmp, out)


# --------------------------------------------------------------------------------------------
# daily build
# --------------------------------------------------------------------------------------------


def daily_paths(out_root: Path, day: str) -> tuple[Path, Path]:
    d = out_root / "daily"
    return d / f"wl-{day}.npz", d / f"wl-{day}.manifest.json"


def load_daily(path: Path, verify: bool = True) -> dict[str, np.ndarray]:
    """Load a daily npz without its meta array. With `verify`, the content hash must match the sidecar."""
    arrays = read_npz(path)
    arrays.pop("meta_json", None)
    man = path.with_name(path.name[: -len(".npz")] + ".manifest.json")
    if verify:
        if not man.is_file():
            raise Refused(f"no manifest next to {path}")
        want = json.loads(man.read_text())["content_sha256"]
        if content_sha256(arrays) != want:
            raise Refused(f"{path}: content hash differs from its manifest")
    return arrays


def build_day(
    day: str,
    adapter: str,
    out_root: Path,
    *,
    tape_dir: str | Path | None = None,
    tip_dirs: Sequence[str | Path] = (),
    wl_parquet_dir: Path | None = None,
    force: bool = False,
    allow_open_day: bool = False,
    allow_missing_hours: bool = False,
    dedupe: bool = True,
    verify_sha: bool = False,
    threads: int = 3,
    mem_gb: float = 6.0,
    tmp_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Build daily/wl-<day>.npz for one day. Idempotent: an existing valid output is kept."""
    check_day(day)
    if adapter not in ("tape", "tip"):
        raise Refused(f"unknown adapter {adapter!r}")
    npz_path, man_path = daily_paths(out_root, day)
    if npz_path.is_file() and man_path.is_file() and not force:
        man = json.loads(man_path.read_text())
        load_daily(npz_path, verify=True)
        return {"day": day, "status": "exists", "npz": str(npz_path), "content_sha256": man["content_sha256"]}

    if adapter == "tape":
        if tape_dir is None:
            raise Refused("--tape-dir is required for the tape adapter")
        files = discover_tape(tape_dir, day)
    else:
        if not tip_dirs:
            raise Refused("--tip-dir is required for the tip adapter")
        files = discover_tip(tip_dirs, day)
        if not allow_open_day and not tip_day_closed(tip_dirs, day):
            raise Refused(f"day {day} is not closed: no non-empty trades file for {next_day(day)}T00 yet")
    if not files:
        raise Refused(f"no {adapter} source files for {day}")
    hours = [sf.hour for sf in files if sf.size > 0]
    missing = [h for h in day_hours(day) if h not in set(hours)]
    if adapter == "tip" and missing and not allow_missing_hours:
        raise Refused(f"day {day} is missing hour files {missing}")
    if adapter == "tip" and verify_sha:
        for sf in files:
            if sf.size == 0:
                continue
            if not verify_sidecar_sha(sf):
                raise Refused(f"sha256 sidecar mismatch for {sf.path}")

    t0 = time.time()
    con = connect(threads, mem_gb, tmp_dir)
    duck_version = str(_duckdb().__version__)
    nonempty = [sf for sf in files if sf.size > 0]
    stats = aggregate_files(con, nonempty, dedupe=dedupe and adapter == "tip")
    arrays = fetch_daily(con)
    validate_daily(arrays)

    meta = {
        "schema": SCHEMA_VERSION,
        "kind": "c1nf_wallet_daily",
        "day": day,
        "adapter": adapter,
        "hours": hours,
        "wallets": int(len(arrays["th"])),
        "rows": stats,
        "hash_pin": {"duckdb": PINNED_DUCKDB, "vectors": {k: str(v) for k, v in sorted(HASH_VECTORS.items())}},
    }
    meta_bytes = np.frombuffer(json.dumps(meta, sort_keys=True).encode(), dtype=np.uint8).copy()
    to_write = dict(arrays)
    to_write["meta_json"] = meta_bytes
    (out_root / "daily").mkdir(parents=True, exist_ok=True)
    write_npz_deterministic(npz_path, to_write)

    gaps = tip_gap_summary(tip_dirs, day) if adapter == "tip" else None
    manifest = {
        "schema": SCHEMA_VERSION,
        "day": day,
        "adapter": adapter,
        "npz": npz_path.name,
        "npz_sha256": file_sha256(npz_path),
        "content_sha256": content_sha256(arrays),
        "built_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "elapsed_s": round(time.time() - t0, 1),
        "duckdb_version": duck_version,
        "meta": meta,
        "hours_missing": missing,
        "sources": [
            {"hour": sf.hour, "path": sf.path, "kind": sf.kind, "bytes": sf.size, "sha256_verified": bool(verify_sha)}
            for sf in files
        ],
        "gaps": gaps,
    }
    _atomic_write_json(man_path, manifest)
    if wl_parquet_dir is not None:
        write_wl_parquet(con, arrays, wl_parquet_dir / f"{day}.parquet")
    con.close()
    return {
        "day": day,
        "status": "built",
        "npz": str(npz_path),
        "npz_sha256": manifest["npz_sha256"],
        "content_sha256": manifest["content_sha256"],
        "wallets": meta["wallets"],
        "rows": stats,
        "hours": len(hours),
        "hours_missing": missing,
        "elapsed_s": manifest["elapsed_s"],
        "gaps": gaps,
    }


def tip_gap_summary(tip_dirs: Sequence[str | Path], day: str) -> dict[str, Any] | None:
    """Summary of the follower's gaps.jsonl[.zst] records whose t_recv_ms falls in `day` (UTC)."""
    lo, hi = day_bounds_ms(day)
    for d in tip_dirs:
        for name, comp in (("gaps.jsonl.zst", "zstd"), ("gaps.jsonl", "uncompressed")):
            p = Path(d) / name
            if not p.is_file() or p.stat().st_size == 0:
                continue
            con = _duckdb().connect()
            try:
                rows = con.execute(
                    "SELECT reason, count(*), sum(slots) FROM read_json(?, format='newline_delimited', compression=?, "
                    "columns={reason:'VARCHAR',slots:'BIGINT',t_recv_ms:'BIGINT'}) WHERE t_recv_ms >= ? AND t_recv_ms < ? "
                    "GROUP BY reason ORDER BY reason",
                    [str(p), comp, lo, hi],
                ).fetchall()
            finally:
                con.close()
            return {
                "file": str(p),
                "records": int(sum(r[1] for r in rows)),
                "slots": int(sum(r[2] or 0 for r in rows)),
                "by_reason": {r[0]: {"records": int(r[1]), "slots": int(r[2] or 0)} for r in rows},
            }
    return None


# --------------------------------------------------------------------------------------------
# as-of (cumulative) snapshot
# --------------------------------------------------------------------------------------------


def _reduce_sorted(th: np.ndarray, cols: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    """Sort by th (stable) and sum each column per th with exact int64 addition."""
    if len(th) == 0:
        return {"th": th.astype(np.uint64), **{c: np.zeros(0, np.int64) for c in cols}}
    order = np.argsort(th, kind="stable")
    ths = th[order]
    starts = np.concatenate(([0], np.flatnonzero(ths[1:] != ths[:-1]) + 1))
    out = {"th": ths[starts]}
    for c, v in cols.items():
        out[c] = np.add.reduceat(v[order], starts).astype(np.int64, copy=False)
    return out


def merge_asof(prev: dict[str, np.ndarray] | None, daily: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    """as-of(next) = as-of(prev) + daily. Exact: only int64 additions."""
    d_cols = {c: daily[c] for c in DAILY_COLS}
    d_cols["buy_q"] = grid_units(daily["buy_lamports"])
    d_cols["cash_q"] = grid_units(daily["cash_lamports"])
    d_cols["ndays"] = np.ones(len(daily["th"]), np.int64)
    if prev is None:
        th = daily["th"]
        cols = d_cols
    else:
        th = np.concatenate([prev["th"], daily["th"]])
        cols = {c: np.concatenate([prev[c], d_cols[c]]) for c in ASOF_COLS}
    return _reduce_sorted(th, cols)


def list_daily_days(out_root: Path) -> list[str]:
    d = out_root / "daily"
    days = []
    for p in sorted(d.glob("wl-*.npz")):
        day = p.name[len("wl-") : -len(".npz")]
        if DAY_RE.match(day) and p.with_name(f"wl-{day}.manifest.json").is_file():
            days.append(day)
    return days


def asof_dir(out_root: Path, day: str) -> Path:
    return out_root / "asof" / f"asof-{day}"


def build_asof(out_root: Path, day: str, *, force: bool = False, use_prev: bool = True) -> dict[str, Any]:
    """Cumulative ledger over daily files with day' < `day`, as mmap-able .npy files.

    Uses asof-<prev day> as the base when its manifest lists exactly the right days and hashes;
    otherwise folds all daily files from scratch. Both give identical bytes."""
    check_day(day)
    days = [d for d in list_daily_days(out_root) if d < day]
    if not days:
        raise Refused(f"no daily ledger files before {day} in {out_root}/daily")
    shas = {d: json.loads(daily_paths(out_root, d)[1].read_text())["content_sha256"] for d in days}
    target = asof_dir(out_root, day)
    if target.is_dir() and not force:
        man = json.loads((target / "MANIFEST.json").read_text())
        if man["days"] == days and man["inputs"] == shas:
            return {"asof_day": day, "status": "exists", "dir": str(target), "content_sha256": man["content_sha256"]}
        raise OutputDiffers(f"{target} exists and was built from different inputs (use --force)")

    cum: dict[str, np.ndarray] | None = None
    start = 0
    mode = "fold"
    if use_prev and len(days) > 1:
        prev_dir = asof_dir(out_root, days[-1])
        if (prev_dir / "MANIFEST.json").is_file():
            pm = json.loads((prev_dir / "MANIFEST.json").read_text())
            if pm["days"] == days[:-1] and pm["inputs"] == {d: shas[d] for d in days[:-1]}:
                led = AsofLedger.open(prev_dir, verify=True)
                cum = {"th": np.asarray(led.th), **{c: np.asarray(led.cols[c]) for c in ASOF_COLS}}
                start = len(days) - 1
                mode = "incremental"
    for d in days[start:]:
        cum = merge_asof(cum, load_daily(daily_paths(out_root, d)[0], verify=True))
    assert cum is not None

    tmp = target.with_name(target.name + f".tmp{os.getpid()}")
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)
    files: dict[str, str] = {}
    for name in ("th",) + ASOF_COLS:
        np.save(tmp / f"{name}.npy", np.ascontiguousarray(cum[name]), allow_pickle=False)
        files[f"{name}.npy"] = file_sha256(tmp / f"{name}.npy")
    man = {
        "schema": SCHEMA_VERSION,
        "kind": "c1nf_wallet_asof",
        "asof_day": day,
        "days": days,
        "inputs": shas,
        "rows": int(len(cum["th"])),
        "files": files,
        "content_sha256": hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest(),
        "hash_pin": {"duckdb": PINNED_DUCKDB},
    }
    _atomic_write_json(tmp / "MANIFEST.json", man)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        shutil.rmtree(target)
    os.replace(tmp, target)
    return {
        "asof_day": day,
        "status": "built",
        "mode": mode,
        "dir": str(target),
        "days": len(days),
        "wallets": man["rows"],
        "content_sha256": man["content_sha256"],
    }


class AsofLedger:
    """Read side of an as-of snapshot. Columns are numpy memmaps (mmap_mode='r')."""

    def __init__(self, path: Path, th: np.ndarray, cols: dict[str, np.ndarray], manifest: dict[str, Any]):
        self.path, self.th, self.cols, self.manifest = path, th, cols, manifest

    @classmethod
    def open(cls, path: str | Path, verify: bool = False) -> "AsofLedger":
        p = Path(path)
        man = json.loads((p / "MANIFEST.json").read_text())
        if man.get("schema") != SCHEMA_VERSION:
            raise Refused(f"{p}: schema {man.get('schema')} != {SCHEMA_VERSION}")
        if verify:
            for name, want in man["files"].items():
                if file_sha256(p / name) != want:
                    raise Refused(f"{p / name}: sha256 differs from MANIFEST.json")
        th = np.load(p / "th.npy", mmap_mode="r", allow_pickle=False)
        cols = {c: np.load(p / f"{c}.npy", mmap_mode="r", allow_pickle=False) for c in ASOF_COLS}
        return cls(p, th, cols, man)

    def __len__(self) -> int:
        return int(len(self.th))

    def lookup_index(self, th: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """(known mask, row index) for the given th values."""
        q = np.asarray(th, dtype=np.uint64)
        if len(self.th) == 0:
            return np.zeros(len(q), bool), np.zeros(len(q), np.int64)
        i = np.searchsorted(self.th, q)
        i = np.minimum(i, len(self.th) - 1)
        known = np.asarray(self.th[i]) == q
        return known, i

    def passa_matrix(self, th: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """(known mask, M) with M float64 [len(th), 7], columns PASSA_COLS, NaN for unknown wallets.

        The matrix 11_passA.py builds from the sum of the prior pinned wl/ files: cash and buy are the sums
        of the daily grid values (cash_q / 2^20, exact), the others are counts."""
        known, i = self.lookup_index(th)
        m = np.full((len(known), len(PASSA_COLS)), np.nan)
        if known.any():
            ik = i[known]
            for j, c in enumerate(PASSA_COLS):
                if c == "cash":
                    v = np.asarray(self.cols["cash_q"][ik], dtype=np.float64) / pinned().GRID
                elif c == "buy":
                    v = np.asarray(self.cols["buy_q"][ik], dtype=np.float64) / pinned().GRID
                else:
                    v = np.asarray(self.cols[c][ik], dtype=np.float64)
                m[known, j] = v
        return known, m


# --------------------------------------------------------------------------------------------
# rollup (the daily job)
# --------------------------------------------------------------------------------------------


def rollup(
    day: str, out_root: Path, tip_dirs: Sequence[str | Path], require_prev_day: bool = False, **kw: Any
) -> dict[str, Any]:
    """Build the tip-tape daily ledger for `day`, then the as-of snapshot for day+1.

    With `require_prev_day` the job refuses (nothing written) when the ledger of the day before `day` is
    missing, so a skipped night is a failed job and not a quietly thinner snapshot."""
    check_day(day)
    if require_prev_day:
        prev = (dt.date.fromisoformat(day) - dt.timedelta(days=1)).isoformat()
        if prev not in set(list_daily_days(out_root)):
            raise Refused(f"no daily ledger for {prev}; run it first or drop --require-prev-day")
    d = build_day(day, "tip", out_root, tip_dirs=tip_dirs, **kw)
    a = build_asof(out_root, next_day(day), force=kw.get("force", False))
    return {"day": d, "asof": a}


# --------------------------------------------------------------------------------------------
# coverage
# --------------------------------------------------------------------------------------------

# Hour ranges [start, end) that are known not to be in any source, with the reason as recorded in the repo.
KNOWN_HOLES = (
    ("2026-08-28T12", "2026-09-03T12", "absent from the exploration tape (02_wallet_cum.py: 08-29..09-02 missing)"),
    ("2026-09-15T12", "2026-09-18T23", "EXP-009 block, excluded on purpose (audit-1008/convert.py)"),
    (
        "2026-09-25T07",
        "2026-10-05T05",
        "after the exploration tape (Oracle in-sample ends 09-25T06) and before the tip archive starts; "
        "the forward walk >= 10-02 is excluded on purpose",
    ),
)


def _hour_range(a: str, b: str) -> Iterable[str]:
    t = dt.datetime.strptime(a, "%Y-%m-%dT%H")
    end = dt.datetime.strptime(b, "%Y-%m-%dT%H")
    while t < end:
        yield t.strftime("%Y-%m-%dT%H")
        t += dt.timedelta(hours=1)


def coverage(
    tape_dir: str | Path | None, tip_dirs: Sequence[str | Path], out_root: Path | None = None
) -> dict[str, Any]:
    """Which hours and days have source data, which have a ledger file, and where the holes are.
    Reads file names and sizes (and the small gaps files). It reads no trade rows."""
    tape_hours: dict[str, int] = {}
    if tape_dir is not None:
        for p in sorted((Path(tape_dir) / "trades").glob("*.parquet")):
            m = TAPE_NAME_RE.match(p.name)
            if m:
                tape_hours[m.group(1)] = p.stat().st_size
    tip_hours: dict[str, dict[str, Any]] = {}
    for d in tip_dirs:
        for p in sorted(Path(d).glob("trades-*.jsonl*")):
            m = TIP_NAME_RE.match(p.name)
            if m and m.group(1) not in tip_hours:
                tip_hours[m.group(1)] = {"bytes": p.stat().st_size, "dir": str(d), "kind": "zst" if m.group(2) else "plain"}
    all_hours = sorted(set(tape_hours) | set(tip_hours))
    ledger_days = set(list_daily_days(out_root)) if out_root is not None else set()
    days: dict[str, dict[str, Any]] = {}
    for h in all_hours:
        e = days.setdefault(h[:10], {"tape_hours": 0, "tip_hours": 0, "hours": 0})
        e["tape_hours"] += int(h in tape_hours)
        e["tip_hours"] += int(h in tip_hours)
        e["hours"] += 1
    for day, e in days.items():
        e["complete"] = e["hours"] == 24
        e["source"] = "tape" if e["tape_hours"] else "tip"
        e["ledger"] = day in ledger_days
    holes = []
    if all_hours:
        have = set(all_hours)
        run: list[str] = []
        for h in _hour_range(all_hours[0], (dt.datetime.strptime(all_hours[-1], "%Y-%m-%dT%H") + dt.timedelta(hours=1)).strftime("%Y-%m-%dT%H")):
            if h not in have:
                run.append(h)
            elif run:
                holes.append(run)
                run = []
        if run:
            holes.append(run)
    hole_rows = []
    for run in holes:
        start, end = run[0], (dt.datetime.strptime(run[-1], "%Y-%m-%dT%H") + dt.timedelta(hours=1)).strftime("%Y-%m-%dT%H")
        reason = "unlabelled"
        for a, b, why in KNOWN_HOLES:
            if a <= start and end <= b:
                reason = why
                break
        hole_rows.append({"start": start, "end": end, "hours": len(run), "reason": reason})
    sizes = sorted(v["bytes"] for v in tip_hours.values() if v["bytes"] > 0)
    median = sizes[len(sizes) // 2] if sizes else 0
    small = sorted(h for h, v in tip_hours.items() if v["bytes"] == 0 or (median and v["bytes"] < 0.25 * median))
    gaps = None
    for d in tip_dirs:
        for name, comp in (("gaps.jsonl.zst", "zstd"), ("gaps.jsonl", "uncompressed")):
            p = Path(d) / name
            if p.is_file() and p.stat().st_size > 0 and gaps is None:
                con = _duckdb().connect()
                try:
                    rows = con.execute(
                        "SELECT reason, count(*), sum(slots), min(t_recv_ms), max(t_recv_ms) FROM read_json(?, "
                        "format='newline_delimited', compression=?, columns={reason:'VARCHAR',slots:'BIGINT',t_recv_ms:'BIGINT'}) "
                        "GROUP BY reason ORDER BY reason",
                        [str(p), comp],
                    ).fetchall()
                finally:
                    con.close()
                gaps = {
                    "file": str(p),
                    "by_reason": {
                        r[0]: {
                            "records": int(r[1]),
                            "slots": int(r[2] or 0),
                            "first_utc": dt.datetime.fromtimestamp(r[3] / 1000, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                            "last_utc": dt.datetime.fromtimestamp(r[4] / 1000, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                        }
                        for r in rows
                    },
                }
    return {
        "tape_hours": len(tape_hours),
        "tip_hours": len(tip_hours),
        "first_hour": all_hours[0] if all_hours else None,
        "last_hour": all_hours[-1] if all_hours else None,
        "tape_first": min(tape_hours) if tape_hours else None,
        "tape_last": max(tape_hours) if tape_hours else None,
        "tip_first": min(tip_hours) if tip_hours else None,
        "tip_last": max(tip_hours) if tip_hours else None,
        "days": {k: days[k] for k in sorted(days)},
        "holes": hole_rows,
        "tip_small_or_empty_hours": small,
        "tip_gaps": gaps,
    }


def format_coverage(cov: dict[str, Any]) -> str:
    lines = [
        f"tape hours {cov['tape_hours']} ({cov['tape_first']} .. {cov['tape_last']}), "
        f"tip hours {cov['tip_hours']} ({cov['tip_first']} .. {cov['tip_last']})",
        "",
        "day         source  hours  complete  ledger",
    ]
    for day, e in cov["days"].items():
        lines.append(f"{day}  {e['source']:<6}  {e['hours']:>5}  {str(e['complete']):<8}  {'yes' if e['ledger'] else 'no'}")
    lines += ["", "holes between the first and last hour:"]
    for h in cov["holes"]:
        lines.append(f"  {h['start']} .. {h['end']}  ({h['hours']} h)  {h['reason']}")
    if cov["tip_small_or_empty_hours"]:
        lines.append(f"tip hours that are empty or under 25% of the median size: {cov['tip_small_or_empty_hours']}")
    if cov["tip_gaps"]:
        lines.append(f"tip gaps file {cov['tip_gaps']['file']}: {json.dumps(cov['tip_gaps']['by_reason'], sort_keys=True)}")
    return "\n".join(lines)


# --------------------------------------------------------------------------------------------
# parity against a float wl/ parquet (C1's format)
# --------------------------------------------------------------------------------------------


def load_wl_parquet(path: str | Path) -> dict[str, np.ndarray]:
    con = _duckdb().connect()
    try:
        raw = con.execute(
            "SELECT th, n, nm, nbond, buy, sell, nwin, nrt, cash FROM read_parquet(?) ORDER BY th", [str(path)]
        ).fetchnumpy()
    finally:
        con.close()
    out = {"th": np.ascontiguousarray(raw["th"], dtype=np.uint64)}
    for c in ("n", "nm", "nbond", "buy", "sell", "nwin", "nrt", "cash"):
        out[c] = np.ascontiguousarray(raw[c], dtype=np.int64 if c == "nm" else np.float64)
    return out


def diff_wl(a: dict[str, np.ndarray], b: dict[str, np.ndarray], a_lamports: dict[str, np.ndarray] | None = None) -> dict[str, Any]:
    """Compare two wl/ style tables. `a` is the exact side when `a_lamports` (its integer arrays) is given."""
    res: dict[str, Any] = {"rows_a": int(len(a["th"])), "rows_b": int(len(b["th"]))}
    common, ia, ib = np.intersect1d(a["th"], b["th"], assume_unique=True, return_indices=True)
    res["th_only_a"] = int(len(a["th"]) - len(common))
    res["th_only_b"] = int(len(b["th"]) - len(common))
    res["th_common"] = int(len(common))
    for c in ("n", "nm", "nbond", "nwin", "nrt"):
        d = np.abs(a[c][ia].astype(np.float64) - b[c][ib].astype(np.float64))
        res[c] = {"rows_differ": int((d != 0).sum()), "max_abs": float(d.max()) if len(d) else 0.0}
    for c in ("buy", "sell", "cash"):
        d = np.abs(a[c][ia] - b[c][ib])
        res[c] = {
            "rows_differ": int((d != 0).sum()),
            "rows_gt_1e-9": int((d > 1e-9).sum()),
            "rows_gt_1e-8": int((d > 1e-8).sum()),
            "rows_gt_1e-7": int((d > 1e-7).sum()),
            "max_abs_sol": float(d.max()) if len(d) else 0.0,
        }
    ca, cb = a["cash"][ia], b["cash"][ib]
    pos_flip = (ca > 0) != (cb > 0)
    sign_flip = np.sign(ca) != np.sign(cb)
    res["cash_positive_flips"] = int(pos_flip.sum())  # changes `skill = cash > 0`
    res["cash_sign_differs_incl_zero"] = int(sign_flip.sum())
    if a_lamports is not None:
        # every sign difference should be a wallet whose exact net is 0 lamports (float residue +-1e-16)
        res["sign_differs_with_nonzero_exact_cash"] = int((sign_flip & (a_lamports["cash_lamports"][ia] != 0)).sum())
        # a flip against a NON-pinned (float) table is explained when the exact net is 0 lamports or rounds to
        # 0 on the 2^-20 grid (|cash| <= 476 lamports); anything else is unexplained
        ex = np.abs(a_lamports["cash_lamports"][ia])
        res["sign_differs_by_class"] = {
            "exact_zero": int((sign_flip & (ex == 0)).sum()),
            "rounds_to_zero_on_grid": int((sign_flip & (ex > 0) & (to_grid(ex) == 0)).sum()),
            "unexplained": int((sign_flip & (ex > 0) & (to_grid(ex) != 0)).sum()),
        }
        flow = (a_lamports["buy_lamports"][ia] + a_lamports["sell_lamports"][ia]).astype(np.float64) / LAMPORTS_PER_SOL
        dc = np.abs(ca - cb)
        ok = flow > 0
        res["cash_max_abs_over_flow"] = float((dc[ok] / flow[ok]).max()) if ok.any() else 0.0
        res["exact_zero_cash_wallets"] = int((a_lamports["cash_lamports"] == 0).sum())
    idx = np.flatnonzero(sign_flip)
    ex = []
    for k in idx[:20]:
        row: dict[str, Any] = {"th": str(int(common[k])), "cash_a": float(ca[k]), "cash_b": float(cb[k])}
        if a_lamports is not None:
            row["cash_a_lamports"] = int(a_lamports["cash_lamports"][ia[k]])
            row["sum_abs_flow_lamports"] = int(a_lamports["buy_lamports"][ia[k]] + a_lamports["sell_lamports"][ia[k]])
            row["n"] = int(a_lamports["n"][ia[k]])
            row["nm"] = int(a_lamports["nm"][ia[k]])
        ex.append(row)
    res["cash_sign_examples"] = ex
    return res


# --------------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------------


def _add_run_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--out-root", required=True, help="holds daily/ and asof/")
    p.add_argument("--threads", type=int, default=3)
    p.add_argument("--mem-gb", type=float, default=6.0, help="duckdb memory_limit")
    p.add_argument("--tmp-dir", default=None, help="duckdb spill dir (put it in a .nobackup dir)")
    p.add_argument("--force", action="store_true")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("day", help="build daily/wl-<day>.npz from one adapter")
    p.add_argument("--day", required=True)
    p.add_argument("--adapter", required=True, choices=("tape", "tip"))
    p.add_argument("--tape-dir", default=None, help="exploration tape root (has trades/<hour>.parquet)")
    p.add_argument("--tip-dir", action="append", default=[], help="repeatable; first dir with an hour file wins")
    p.add_argument("--wl-parquet-dir", default=None, help="also write C1-format <day>.parquet here")
    p.add_argument("--allow-open-day", action="store_true")
    p.add_argument("--allow-missing-hours", action="store_true")
    p.add_argument("--no-dedupe", action="store_true")
    p.add_argument("--verify-sha", action="store_true", help="check the archive .sha256 sidecars (needs zstd)")
    _add_run_args(p)

    p = sub.add_parser("asof", help="build asof/asof-<day>/ from daily files before <day>")
    p.add_argument("--day", required=True)
    _add_run_args(p)

    p = sub.add_parser("rollup", help="daily job: tip-tape day <day>, then as-of <day+1>")
    p.add_argument("--day", required=True, help="the day to close, e.g. yesterday")
    p.add_argument("--tip-dir", action="append", default=[])
    p.add_argument("--allow-missing-hours", action="store_true")
    p.add_argument("--no-dedupe", action="store_true")
    p.add_argument("--verify-sha", action="store_true")
    p.add_argument("--require-prev-day", action="store_true", help="refuse if the day before has no daily ledger")
    _add_run_args(p)

    p = sub.add_parser("coverage", help="which hours and days have source data (names and sizes only)")
    p.add_argument("--tape-dir", default=None)
    p.add_argument("--tip-dir", action="append", default=[])
    p.add_argument("--out-root", default=None)
    p.add_argument("--json", default=None, help="write the full report as JSON")

    p = sub.add_parser("parity", help="compare daily/wl-<day>.npz with C1-format wl parquet files")
    p.add_argument("--day", required=True)
    p.add_argument("--out-root", required=True)
    p.add_argument("--ref", required=True, help="reference wl parquet (for example the VERIFY rebuild)")
    p.add_argument("--ref-b", default=None, help="a second wl parquet, compared with --ref")
    p.add_argument("--json", default=None)
    return ap


def _emit(obj: Any) -> None:
    print(json.dumps(obj, sort_keys=True))


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.cmd == "day":
            res = build_day(
                args.day,
                args.adapter,
                Path(args.out_root),
                tape_dir=args.tape_dir,
                tip_dirs=args.tip_dir,
                wl_parquet_dir=Path(args.wl_parquet_dir) if args.wl_parquet_dir else None,
                force=args.force,
                allow_open_day=args.allow_open_day,
                allow_missing_hours=args.allow_missing_hours,
                dedupe=not args.no_dedupe,
                verify_sha=args.verify_sha,
                threads=args.threads,
                mem_gb=args.mem_gb,
                tmp_dir=args.tmp_dir,
            )
            _emit(res)
        elif args.cmd == "asof":
            _emit(build_asof(Path(args.out_root), check_day(args.day), force=args.force))
        elif args.cmd == "rollup":
            _emit(
                rollup(
                    check_day(args.day),
                    Path(args.out_root),
                    args.tip_dir,
                    require_prev_day=args.require_prev_day,
                    allow_missing_hours=args.allow_missing_hours,
                    dedupe=not args.no_dedupe,
                    verify_sha=args.verify_sha,
                    force=args.force,
                    threads=args.threads,
                    mem_gb=args.mem_gb,
                    tmp_dir=args.tmp_dir,
                )
            )
        elif args.cmd == "coverage":
            cov = coverage(args.tape_dir, args.tip_dir, Path(args.out_root) if args.out_root else None)
            print(format_coverage(cov))
            if args.json:
                Path(args.json).write_text(json.dumps(cov, indent=1, sort_keys=True) + "\n")
        elif args.cmd == "parity":
            npz, _ = daily_paths(Path(args.out_root), check_day(args.day))
            exact = load_daily(npz, verify=True)
            mine = to_wl_float(exact)
            report: dict[str, Any] = {"mine_vs_ref": diff_wl(mine, load_wl_parquet(args.ref), exact)}
            if args.ref_b:
                report["ref_vs_ref_b"] = diff_wl(load_wl_parquet(args.ref), load_wl_parquet(args.ref_b))
                report["mine_vs_ref_b"] = diff_wl(mine, load_wl_parquet(args.ref_b), exact)
            if args.json:
                Path(args.json).write_text(json.dumps(report, indent=1, sort_keys=True) + "\n")
            _emit(report)
        return EXIT_OK
    except Refused as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return EXIT_REFUSED
    except OutputDiffers as exc:
        print(f"DIFFERS: {exc}", file=sys.stderr)
        return EXIT_DIFFERS


if __name__ == "__main__":
    sys.exit(main())
