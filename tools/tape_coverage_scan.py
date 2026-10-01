"""Streaming identity scan of one trade-tape directory. Stdlib only.

Run locally for the fast tape, and piped over ``ssh mal-core-0 python3 - ...``
for Oracle's tape, so both sides are parsed by the same code. It prints one
line per trade row inside a time window and nothing else from the row:

    <signature> <event_index> <t_recv_ms> <venue> <schema_id>

``venue`` is the row's venue string (a two-value category). ``schema_id`` is a
small integer naming the row's field-name/type map; the maps are printed once at
the end as ``#schema <id> <json>``. No amounts, wallets, mints or prices leave
the host. The last line is ``#done ...``; a reader that does not see it must
treat the stream as truncated.

Memory is O(1) in rows: lines are read one at a time and nothing is retained
except the schema table (a handful of entries). Sealed hours are read through
``zstd -dc`` (no Python zstd module is assumed on either host).
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import IO, Iterator

HOUR_MS = 3_600_000
MAX_SCHEMAS = 64


def hour_stamp(hour_ms: int) -> str:
    return datetime.fromtimestamp(hour_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H")


def hour_starts(lo_ms: int, hi_ms: int) -> list[int]:
    """Hour starts (unix ms) of every UTC hour overlapping [lo_ms, hi_ms)."""
    if hi_ms <= lo_ms:
        return []
    first = lo_ms - lo_ms % HOUR_MS
    last = (hi_ms - 1) - (hi_ms - 1) % HOUR_MS
    return list(range(first, last + 1, HOUR_MS))


def find_hour_file(directory: Path, hour_ms: int) -> Path | None:
    stamp = hour_stamp(hour_ms)
    for name in (f"trades-{stamp}.jsonl", f"trades-{stamp}.jsonl.zst"):
        path = directory / name
        if path.is_file():
            return path
    return None


def open_lines(path: Path) -> Iterator[bytes]:
    """Yield raw lines one at a time. Sealed ``.zst`` goes through ``zstd -dc``."""
    if path.name.endswith(".zst"):
        proc = subprocess.Popen(
            ["zstd", "-dc", str(path)], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
        )
        assert proc.stdout is not None
        try:
            yield from proc.stdout
        finally:
            proc.stdout.close()
            proc.wait()
        return
    with open(path, "rb") as handle:
        yield from handle


def schema_map(row: dict) -> dict[str, str]:
    return {key: type(value).__name__ for key, value in sorted(row.items())}


class SchemaTable:
    """Small registry of distinct field-name/type maps (names and types only)."""

    def __init__(self) -> None:
        self._ids: dict[str, int] = {}
        self.maps: list[dict[str, str]] = []
        self.overflow = 0

    def id_for(self, row: dict) -> int:
        key = json.dumps(schema_map(row), sort_keys=True)
        found = self._ids.get(key)
        if found is not None:
            return found
        if len(self.maps) >= MAX_SCHEMAS:
            self.overflow += 1
            return MAX_SCHEMAS
        self._ids[key] = len(self.maps)
        self.maps.append(schema_map(row))
        return self._ids[key]


def scan_rows(
    directory: Path,
    lo_ms: int,
    hi_ms: int,
    schemas: SchemaTable,
    stats: dict,
) -> Iterator[tuple[str, int, int, str, int]]:
    """Yield (signature, event_index, t_recv_ms, venue, schema_id) for lo<=t<hi."""
    stats.setdefault("files", [])
    stats.setdefault("missing", [])
    stats.setdefault("bad_lines", 0)
    stats.setdefault("rows_out", 0)
    stats.setdefault("rows_scanned", 0)
    for hour_ms in hour_starts(lo_ms, hi_ms):
        path = find_hour_file(directory, hour_ms)
        if path is None:
            stats["missing"].append(hour_stamp(hour_ms))
            continue
        stats["files"].append(path.name)
        for raw in open_lines(path):
            stats["rows_scanned"] += 1
            try:
                row = json.loads(raw)
                t_ms = int(row["t_recv_ms"])
                sig = row["signature"]
                idx = int(row.get("event_index", 0))
            except (ValueError, KeyError, TypeError):
                # A live hour file can end in a half-written line.
                stats["bad_lines"] += 1
                continue
            if not (lo_ms <= t_ms < hi_ms):
                continue
            stats["rows_out"] += 1
            yield sig, idx, t_ms, str(row.get("venue", "")), schemas.id_for(row)


def format_row(sig: str, idx: int, t_ms: int, venue: str, schema_id: int) -> str:
    return f"{sig} {idx} {t_ms} {venue or '-'} {schema_id}"


def write_stream(directory: Path, lo_ms: int, hi_ms: int, out: IO[str]) -> dict:
    schemas = SchemaTable()
    stats: dict = {}
    for item in scan_rows(directory, lo_ms, hi_ms, schemas, stats):
        out.write(format_row(*item) + "\n")
    for sid, smap in enumerate(schemas.maps):
        out.write(f"#schema {sid} {json.dumps(smap, sort_keys=True, separators=(',', ':'))}\n")
    footer = {
        "rows_out": stats.get("rows_out", 0),
        "rows_scanned": stats.get("rows_scanned", 0),
        "bad_lines": stats.get("bad_lines", 0),
        "missing": stats.get("missing", []),
        "files": stats.get("files", []),
        "schema_overflow": schemas.overflow,
    }
    out.write("#done " + json.dumps(footer, sort_keys=True, separators=(",", ":")) + "\n")
    out.flush()
    return footer


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        sys.stderr.write("usage: scan <dir> <lo_ms> <hi_ms>\n")
        return 2
    write_stream(Path(argv[1]), int(argv[2]), int(argv[3]), sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
