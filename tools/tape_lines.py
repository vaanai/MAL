#!/usr/bin/env python3
"""Count the lines of a walker tape file that are not rows, and refuse when there are any.

Why this exists (audit 2026-10-08, A8 / d13-F4). A walker crash and resume once left NUL-filled
lines in sealed hours (exp011-0909: a 285-slot and a 140-slot hole). Every lab reader does
`except json.JSONDecodeError: continue`, so each hole vanished without a count. This module is the
shared, dependency-free counter. It changes nothing by default.

A line is BAD when it is

  - `nul`        a raw NUL (U+0000) anywhere in it. `json.dumps` never emits a raw NUL (it writes
                 the six characters backslash-u-0-0-0-0), so a raw NUL in a walker file is a hole;
  - `not_json`   not JSON even with `strict=False`;
  - `non_object` JSON, but not an object (a tape row is always an object).

A line is `lenient` (counted, NOT bad) when it fails `json.loads(strict=True)` only because a raw
control character other than NUL sits inside a string (the audit's LENIENT.txt case: a control
character in a signature). It parses with `strict=False`, which is what the lab's loaders use on
purpose. Blank lines are not counted at all.

Opt in:

  - `strict_lines(lines, label)` wraps any line iterator. It yields every line unchanged (a reader
    that skips bad lines still skips them) and, once the iterator is exhausted, raises
    `BadLinesError(path, counts)` if any line was bad. The raise happens after the hour is read,
    so the error carries the whole-hour count.
  - `scan_file(path)` is a counts-only pass over a plain, .zst or .gz file.
  - `latency_curve._iter_trades(path, strict=None)` turns this on when `strict=True` or when
    the environment variable `MAL_STRICT_LINES=1` is set (spawned scorer workers inherit it).
"""

from __future__ import annotations

import gzip
import json
import os
import subprocess
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator

STRICT_ENV = "MAL_STRICT_LINES"
BAD_KINDS = ("nul", "not_json", "non_object")


@dataclass
class LineCounts:
    lines: int = 0  # non-blank lines seen
    nul: int = 0
    not_json: int = 0
    non_object: int = 0
    lenient: int = 0  # parses only with strict=False; NOT bad
    first_bad_line: int | None = None  # 1-based, counting every physical line including blanks
    first_bad_kind: str | None = None
    physical: int = 0

    @property
    def bad(self) -> int:
        return self.nul + self.not_json + self.non_object

    def as_dict(self) -> dict[str, Any]:
        return {
            "lines": self.lines,
            "bad_lines": self.bad,
            "nul": self.nul,
            "not_json": self.not_json,
            "non_object": self.non_object,
            "lenient": self.lenient,
            "first_bad_line": self.first_bad_line,
        }

    def add(self, line: str | bytes) -> str | None:
        """Classify one line, update the counts, return its kind (None for a blank or a good line)."""
        self.physical += 1
        kind = classify_line(line)
        if kind is None:
            if not _is_blank(line):
                self.lines += 1
            return None
        self.lines += 1
        if kind == "lenient":
            self.lenient += 1
            return kind
        setattr(self, kind, getattr(self, kind) + 1)
        if self.first_bad_line is None:
            self.first_bad_line, self.first_bad_kind = self.physical, kind
        return kind


class BadLinesError(RuntimeError):
    """Raised after a file was read when it held bad lines. Picklable (it crosses a spawn pool)."""

    def __init__(self, path: str, counts: dict[str, Any]) -> None:
        super().__init__(path, counts)
        self.path = path
        self.counts = counts

    def __str__(self) -> str:
        c = self.counts
        return (
            f"{self.path}: {c['bad_lines']} bad line(s) of {c['lines']} (nul={c['nul']}, not_json={c['not_json']}, "
            f"non_object={c['non_object']}; first at physical line {c['first_bad_line']}); "
            "NUL or non-JSON lines are data holes, not rows"
        )

    def __reduce__(self) -> tuple[Any, ...]:
        return (BadLinesError, (self.path, self.counts))


def _is_blank(line: str | bytes) -> bool:
    return not line.strip()


def classify_line(line: str | bytes) -> str | None:
    """None (good or blank), 'lenient' (OK), or one of BAD_KINDS."""
    if isinstance(line, bytes):
        if b"\x00" in line:
            return "nul"
        text = line.decode("utf-8", "replace")
    else:
        if "\x00" in line:
            return "nul"
        text = line
    text = text.strip()
    if not text:
        return None
    lenient = False
    try:
        row = json.loads(text)
    except ValueError:  # JSONDecodeError and UnicodeDecodeError are ValueErrors
        try:
            row = json.loads(text, strict=False)
        except (ValueError, RecursionError):
            return "not_json"
        lenient = True
    except RecursionError:
        return "not_json"
    if not isinstance(row, dict):
        return "non_object"
    return "lenient" if lenient else None


def strict_lines(lines: Iterable[str | bytes], label: str | Path) -> Iterator[str | bytes]:
    """Yield `lines` unchanged; after the last one raise BadLinesError if any line was bad.

    A consumer that stops early never sees the raise, so wrap only full-file readers."""
    counts = LineCounts()
    for line in lines:
        counts.add(line)
        yield line
    if counts.bad:
        raise BadLinesError(str(label), counts.as_dict())


def _open_lines(path: Path) -> Iterator[bytes]:
    name = path.name
    if name.endswith(".zst"):
        proc = subprocess.Popen(["zstd", "-dc", "-q", str(path)], stdout=subprocess.PIPE)
        assert proc.stdout is not None
        ok = False
        try:
            yield from proc.stdout
            ok = True
        finally:
            proc.stdout.close()
            rc = proc.wait()
        if ok and rc != 0:
            raise RuntimeError(f"{path}: zstd exited {rc}; the sealed file is truncated or corrupt")
        return
    if name.endswith(".gz"):
        with gzip.open(path, "rb") as fh:
            yield from fh
        return
    with path.open("rb") as fh:
        yield from fh


def scan_file(path: Path | str) -> LineCounts:
    """Counts-only pass over one tape file. Never prints or keeps a row."""
    counts = LineCounts()
    for line in _open_lines(Path(path)):
        counts.add(line)
    return counts


def require_clean(path: Path | str) -> LineCounts:
    """scan_file, then raise BadLinesError if any line was bad."""
    counts = scan_file(path)
    if counts.bad:
        raise BadLinesError(str(path), counts.as_dict())
    return counts


def strict_enabled() -> bool:
    return os.environ.get(STRICT_ENV) == "1"


@contextmanager
def strict_env(on: bool = True) -> Iterator[None]:
    """Set (on) or clear (off) MAL_STRICT_LINES for the duration, then restore it. Spawned workers inherit it.
    Off clears an externally set variable on purpose: a caller that says "not strict" gets exactly that."""
    old = os.environ.get(STRICT_ENV)
    if on:
        os.environ[STRICT_ENV] = "1"
    else:
        os.environ.pop(STRICT_ENV, None)
    try:
        yield
    finally:
        if old is None:
            os.environ.pop(STRICT_ENV, None)
        else:
            os.environ[STRICT_ENV] = old
