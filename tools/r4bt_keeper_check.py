"""r4bt_keeper_check: the zst_sha pre-check of the r4-b price keeper, run before r4bt-a2's registered pricer. PAPER ONLY. No key, no network.

A SEPARATE module: it imports nothing from `tools/r4b_preview.py` or `tools/r4bt_price.py`, so the registered read's pin does not move with the preview. It never
decompresses a tape file and never prints tape content, a row, a sha or a size.

Rule text (DEC/DEC-031-forward-paper-arena.md at main 459973b0; line numbers are those of that file):

  Line 2089, Amendment 30 Erratum 3 item 1: "Before pricing, the r4-b preview pricer and r4bt-a2's registered pricer check every keeper hour they read: the sha256 of
  the hard-linked `tape-<h>.jsonl.zst` must equal the receiver's verified `zst_sha` that the r4-b price keeper copied into
  `/data2/mal/scratch/r4b-price-keep/.keeper-hours.jsonl` (with its `zst_bytes`, dev and inode), or the pricer refuses (exit 3)."
  Line 2091: "`tools/r4bt_price.py` is pinned and is not edited. So the check is a separate pinned pre-check step, part of r4bt-a2's registered pricer's read: its tool
  comes in its own T1 PR, merged before the screen read; its commit and blob are pinned in the registration record; it runs in the same manager job immediately
  before the pricer, over every keeper hour in the pricer's `--tape-dir` range; it reads only `.keeper-hours.jsonl` and those hour files".
  Line 2092: "A failed check means the pricer is not run."
  Line 2076, Amendment 30 Erratum 2: the pricer's `--tape-dir` is the keeper dir `/data2/mal/scratch/r4b-price-keep` (same-inode links of receiver-verified hv7 hours).

Checks, for every hour of the INCLUSIVE range [--from-hour, --to-hour]:
  * exactly one `.keeper-hours.jsonl` row by name `tape-<h>.jsonl.zst`, or duplicate rows with identical (zst_bytes, zst_sha, dev, inode);
  * the file exists (opened read-only with O_NOFOLLOW, regular file);
  * its size equals `zst_bytes`; its streamed sha256 equals `zst_sha`; its `st_dev` and `st_ino` equal the row's `dev` and `inode`.
Exits: 0 all equal; 3 any mismatch or differing duplicate rows; 2 refusal (an hour with no row, an hour with no file, a path under a closed root of
`tools/r4bt_gate.py`, a bad argument). Prints the count of hours checked, the first and last hour and, on a failure, the first failing hour and the failure kind.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Sequence

from tools import r4bt_gate as G

KEEPER_HOURS = ".keeper-hours.jsonl"
REAL_KEEPER_DIR = "/data2/mal/scratch/r4b-price-keep"
HOUR_RE = re.compile(r"^\d{4}-\d\d-\d\dT\d\d$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
CHUNK = 1 << 20
EXIT_OK, EXIT_REFUSED, EXIT_MISMATCH = 0, 2, 3


def _is_int(x) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def hours_in_range(a: str, b: str) -> list[str]:
    for t in (a, b):
        if not HOUR_RE.match(t):
            raise G.Refused(f"not an hour YYYY-MM-DDTHH: {t!r}")
    try:
        x = datetime.strptime(a, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc)
        y = datetime.strptime(b, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc)
    except ValueError:
        raise G.Refused("not a valid hour") from None
    if y < x:
        raise G.Refused("--to-hour is before --from-hour")
    out = []
    while x <= y:
        out.append(x.strftime("%Y-%m-%dT%H"))
        x += timedelta(hours=1)
    return out


def load_rows(keeper: Path) -> tuple[dict[str, list[dict]], int]:
    """{file name: [rows]} from `.keeper-hours.jsonl` (every valid row kept, so duplicates are seen); unreadable lines are counted and skipped."""
    p = keeper / KEEPER_HOURS
    try:
        fd = os.open(str(p), os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        raise G.Refused(f"{KEEPER_HOURS} cannot be opened") from None
    try:
        with os.fdopen(fd, "rb") as f:
            data = f.read()
    except OSError:
        raise G.Refused(f"{KEEPER_HOURS} cannot be read") from None
    rows: dict[str, list[dict]] = {}
    bad = 0
    for line in data.split(b"\n"):
        if not line.strip():
            continue
        try:
            r = json.loads(line)
        except ValueError:
            bad += 1
            continue
        if not (isinstance(r, dict) and isinstance(r.get("name"), str) and _is_int(r.get("zst_bytes")) and isinstance(r.get("zst_sha"), str)
                and SHA_RE.match(r["zst_sha"]) and _is_int(r.get("dev")) and _is_int(r.get("inode"))):
            bad += 1
            continue
        rows.setdefault(r["name"], []).append(r)
    return rows, bad


def _key(r: dict) -> tuple:
    return (r["zst_bytes"], r["zst_sha"], r["dev"], r["inode"])


def _open_ro(path: Path) -> int:
    """Read-only, never through a symlink. Raises G.Refused when the hour file is missing, a link or not a regular file."""
    try:
        fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        raise G.Refused("no readable regular file") from None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise G.Refused("not a regular file")
    except BaseException:
        os.close(fd)
        raise
    return fd


def check(keeper_dir: str | Path, from_hour: str, to_hour: str) -> dict:
    """Returns {"n", "first", "last", "ok", "fail_hour", "fail_kind"}. Raises G.Refused (exit 2) on a missing row or hour file."""
    keeper = G.check_path(keeper_dir, "the keeper dir")
    if os.environ.get("PYTEST_CURRENT_TEST") and str(keeper) == REAL_KEEPER_DIR:
        raise G.Refused("the real keeper dir is never used under pytest")
    hours = hours_in_range(from_hour, to_hour)
    rows, _bad = load_rows(keeper)
    res = {"n": len(hours), "first": hours[0], "last": hours[-1], "ok": True, "fail_hour": None, "fail_kind": None}
    # pass 1: refusals (an hour with no row or no file), before any byte is hashed
    for h in hours:
        name = f"tape-{h}.jsonl.zst"
        if not rows.get(name):
            raise G.Refused(f"hour {h}: no keeper row (missing_row)")
        fd = None
        try:
            fd = _open_ro(keeper / name)
        except G.Refused:
            raise G.Refused(f"hour {h}: no keeper file (missing_file)") from None
        finally:
            if fd is not None:
                os.close(fd)
    # pass 2: mismatches, in hour order; the first failing hour is reported
    for h in hours:
        name = f"tape-{h}.jsonl.zst"
        rs = rows[name]
        if len({_key(r) for r in rs}) != 1:
            return dict(res, ok=False, fail_hour=h, fail_kind="duplicate_rows_differ")
        r = rs[0]
        fd = _open_ro(keeper / name)
        try:
            st = os.fstat(fd)
            if st.st_size != r["zst_bytes"]:
                return dict(res, ok=False, fail_hour=h, fail_kind="size_mismatch")
            if st.st_dev != r["dev"] or st.st_ino != r["inode"]:
                return dict(res, ok=False, fail_hour=h, fail_kind="dev_inode_mismatch")
            d = hashlib.sha256()
            n = 0
            while True:
                b = os.read(fd, CHUNK)
                if not b:
                    break
                d.update(b)
                n += len(b)
        finally:
            os.close(fd)
        if n != r["zst_bytes"]:
            return dict(res, ok=False, fail_hour=h, fail_kind="size_mismatch")
        if d.hexdigest() != r["zst_sha"]:
            return dict(res, ok=False, fail_hour=h, fail_kind="sha_mismatch")
    return res


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="zst_sha pre-check of the r4-b price keeper (DEC-031 Amendment 30 Erratum 3 item 1). Exit 0 all equal, 3 mismatch, 2 refusal.")
    ap.add_argument("--keeper-dir", required=True, help="the keeper dir holding .keeper-hours.jsonl and the tape-<h>.jsonl.zst links")
    ap.add_argument("--from-hour", required=True, help="first hour, YYYY-MM-DDTHH (inclusive)")
    ap.add_argument("--to-hour", required=True, help="last hour, YYYY-MM-DDTHH (inclusive)")
    try:
        a = ap.parse_args(argv)
    except SystemExit as e:
        return EXIT_REFUSED if e.code not in (0, None) else 0
    try:
        r = check(a.keeper_dir, a.from_hour, a.to_hour)
    except G.Refused as e:
        print(f"r4bt_keeper_check: REFUSED: {e}", file=sys.stderr)
        return EXIT_REFUSED
    print(f"r4bt_keeper_check: hours checked {r['n']}, first {r['first']}, last {r['last']}")
    if not r["ok"]:
        print(f"r4bt_keeper_check: FAIL first failing hour {r['fail_hour']}: {r['fail_kind']}", file=sys.stderr)
        return EXIT_MISMATCH
    print("r4bt_keeper_check: OK all hours equal")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
