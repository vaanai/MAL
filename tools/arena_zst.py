"""zstd helpers for the arena tape: stream-read a .zst, and compact one closed hour (.jsonl -> .jsonl.zst). Paper only. No network.

Uses the `zstd` CLI (the walker and tools/tape_lines.py use it too); no python module is needed.

compact_file(src): compress to <src>.zst.tmp, fsync it, rename to <src>.zst, fsync the directory, then VERIFY by a full decompress against the
source (byte count, line count and sha256) and only then delete the source. On any mismatch the .zst is removed and the source is kept.
While both files exist a reader prefers the .jsonl (tools.arena_state does), so no row is ever read twice or lost.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from pathlib import Path
from typing import BinaryIO

ZSTD_LEVEL = 9  # measured on the 120 s smoke tape: level 3 = 4.7x, 9 = 5.2x, 19 = 5.7x; a closed hour is compressed once, off the ingest path
CHUNK = 1 << 20


class CompactError(RuntimeError):
    pass


def zstd_path() -> str | None:
    return shutil.which("zstd")


class ZstdReader:
    """File-like, read-only stream of a .zst file, decompressed by `zstd -dc`. close() raises CompactError if zstd failed (a truncated file)."""

    def __init__(self, path: str | Path) -> None:
        exe = zstd_path()
        if exe is None:
            raise CompactError("zstd CLI not found")
        self.path = str(path)
        self._p = subprocess.Popen([exe, "-dc", "-q", self.path], stdout=subprocess.PIPE, stdin=subprocess.DEVNULL)
        assert self._p.stdout is not None
        self._out: BinaryIO = self._p.stdout
        self._eof = False

    def read(self, n: int = CHUNK) -> bytes:
        b = self._out.read(n)
        if not b:
            self._eof = True
        return b

    def close(self) -> None:
        try:
            self._out.close()
        finally:
            rc = self._p.wait()
        if rc != 0 and self._eof:
            raise CompactError(f"zstd -dc exited {rc} for {self.path}")


def open_zst(path: str | Path) -> ZstdReader:
    return ZstdReader(path)


def scan_stream(read) -> tuple[int, int, str]:
    """(bytes, newline-terminated lines (+1 if the data does not end with a newline), sha256 hex) of a stream given as a read(n) callable."""
    h, total, lines, last = hashlib.sha256(), 0, 0, b"\n"
    while True:
        b = read(CHUNK)
        if not b:
            break
        h.update(b)
        total += len(b)
        lines += b.count(b"\n")
        last = b[-1:]
    if total and last != b"\n":
        lines += 1
    return total, lines, h.hexdigest()


def compact_file(src: str | Path, level: int = ZSTD_LEVEL) -> dict:
    """Compress a CLOSED tape hour and delete the source after it verifies. Returns {"bytes_in", "bytes_out", "lines", "ratio"}."""
    src = Path(src)
    exe = zstd_path()
    if exe is None:
        raise CompactError("zstd CLI not found")
    dst = src.with_name(src.name + ".zst")
    tmp = src.with_name(src.name + ".zst.tmp")
    st0 = src.stat()
    try:
        with open(tmp, "wb") as out:
            subprocess.run([exe, f"-{int(level)}", "-q", "-T1", "-c", str(src)], stdout=out, stdin=subprocess.DEVNULL, check=True)
            out.flush()
            os.fsync(out.fileno())
    except (subprocess.CalledProcessError, OSError) as exc:
        tmp.unlink(missing_ok=True)
        raise CompactError(f"compress failed: {type(exc).__name__}") from exc
    os.replace(tmp, dst)
    dfd = os.open(str(dst.parent), os.O_RDONLY)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)
    try:
        r = open_zst(dst)
        try:
            z = scan_stream(r.read)
        finally:
            r.close()
        with open(src, "rb") as fh:
            s = scan_stream(fh.read)
        st1 = src.stat()
        if z != s or st1.st_size != st0.st_size or st1.st_size != s[0] or st1.st_mtime_ns != st0.st_mtime_ns:
            raise CompactError(f"verify mismatch: zst={z[:2]} src={s[:2]} size0={st0.st_size} size1={st1.st_size}")
        st2 = src.stat()  # the last look, right before the delete: a writer appending in the verify window shows here (the feed's writer
        if st2.st_size != st0.st_size or st2.st_mtime_ns != st0.st_mtime_ns:  # also refuses closed hours, so this is the second guard)
            raise CompactError("source changed after verify")
    except Exception:
        dst.unlink(missing_ok=True)  # the source stays; a reader keeps using it
        raise
    src.unlink()
    out_bytes = dst.stat().st_size
    return {"bytes_in": s[0], "bytes_out": out_bytes, "lines": s[1], "ratio": (s[0] / out_bytes) if out_bytes else None}
