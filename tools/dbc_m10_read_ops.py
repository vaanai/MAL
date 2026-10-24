#!/usr/bin/env python3
"""dbc_m10_read_ops: pre-claim checks of the DBC m10 forward read job (QP-RULING-1024-read-hardening.md, items 1 and 2). PAPER ONLY. Stdlib only: it imports
NO tagged tool and never opens a tape file, a marker file or anything under the forward root except by os.listdir and os.lstat.

Subcommands (each prints one status line; every refusal is pre-claim and is NOT a look):

    pins     --wt DIR                       exit 10 unless the worktree is the clean tag commit and the 12 pinned files have the tag's sha256
    complete --root DIR [--min-age 900] [--wait-max S] [--pgrep PAT]
                                            exit 11 unless all 168 window hour NAMES exist, no *.part / *.tmp name, newest lstat mtime (root and entries) >= min-age
                                            seconds old, and no process command line contains PAT. Prints "168/168" on success and no other count.
    guards   --dbc-dir DIR --text FILE...   exit 12 unless fwd-read-v1/ holds the old-tool README (file and dir not writable), fwd-read-v1-am1/ is absent or empty,
                                            and no FILE names the unamended read tool as a program
    mem      --min-gb N                     exit 13 if MemAvailable < N GiB (prints the five largest RSS processes for the record)
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import stat
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterable

TAG = "dbc-m10-forward-v1-am1"
TAG_COMMIT = "7d5be9e51a39db1b8030843a273f747eb92a2967"
# sha256 of the 11 FILES of tools/dbc_fwd_read_am1.py plus tools/venue_backfill.py, computed from the tag commit with `git show 7d5be9e5:<path> | sha256sum`.
# The first 11 equal AMENDMENT-1.md AM5 / PINS.txt / fwd-v1/SHA256SUMS-AM1.txt. tools/venue_backfill.py: PINS.txt (written at tag dbc-m10-forward-v1) records 15605f65...,
# which is stale at the am1 tag: #856 added one docstring line (5c8b355a). The value below is the file AT THE AM1 TAG.
PINS = {
    "tools/dbc_price.py": "7db0afdd7845c0d159f73ec438632dfb2ddc7ef45ab3ef971569625e1335a7eb",
    "tools/dbc_screen.py": "607e3173cae997b30972aef1f863c9355014bb01cee6f6622e352b42b4363b66",
    "tools/dbc_config.py": "04a015e0c42548549457d4b1b96f2eccda62022dfe1a740cf73db26d9c72d2c1",
    "tools/arena_stack.py": "a39b2ba68dd46b7d3a555fb8e06580718300f354020c1f91280ab7a0e0856778",
    "tools/dbc_fwd_read.py": "ccffd905942cb0a071f981478e27aba7b90b8e3ff5ec1b6b0ed52a89515bb183",
    "tools/dbc_fwd_read_am1.py": "4c7953b6979fc25ac464d7ea98a304b0e658727c6aae0cfd4ba58ec5aa000b8b",
    "tools/dbc_fwd.py": "b43e79558d997bb1852a0245d5a0d05220be57c6ea88b83548ad431f051b43f6",
    "tools/dbc_fwd_sec3.py": "403eae4868260f19d9990d00641885d92b5272ee9c148d1d61277e1d4b0dabdf",
    "tools/venue_backfill_dbc.py": "c6f754fae49f27f1fd2f9b00f8d6876065948607f5ed0eeb3c45ff234af84018",
    "tools/dbc_decode.py": "10a52a52ee382a56e13e03b3fa76c1a36cd690b414b83e2f91c8438f000f8535",
    "tools/arena_declog.py": "b4b67f8d203722a16b2d5238f4a0f2b38776995130cd4b5691ee649c043a0783",
    "tools/venue_backfill.py": "7185d58c2cc176f02acc0bb83899745d019317b9008a00c8ec46577beb3e2600",
}
FWD_START = datetime(2026, 10, 25, 0, tzinfo=timezone.utc)
FWD_END = datetime(2026, 11, 1, 0, tzinfo=timezone.utc)
README_NAME = "README-OLD-TOOL-DISABLED.txt"
README_TEXT = ("R2 read tool is tools/dbc_fwd_read_am1.py (tag dbc-m10-forward-v1-am1). tools/dbc_fwd_read.py must never run; "
               "see QP-RULING-1024-read-hardening.md\n")
OLD_OUT, AM1_OUT = "fwd-read-v1", "fwd-read-v1-am1"
EXIT_PINS, EXIT_INCOMPLETE, EXIT_GUARD, EXIT_MEM = 10, 11, 12, 13
# the unamended tool named as a program: "tools.<name> " / "tools/<name> " / "<name>.py" where <name> is the old module (never followed by "_am1")
_OLD = "dbc_fwd_" + "read"
OLD_TOOL_RE = re.compile(r"tools[./]" + _OLD + r"(?:\.py)?(?=[\s\"';|&)]|$)|" + _OLD + r"\.py", re.MULTILINE)


def sha256_file(p: str | os.PathLike) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def _git(wt: str, *args: str) -> tuple[int, str]:
    p = subprocess.run(["git", "-C", wt, *args], capture_output=True, text=True)
    return p.returncode, p.stdout.strip()


def check_pins(wt: str, pins: dict[str, str] | None = None, commit: str = TAG_COMMIT) -> tuple[bool, str]:
    pins = PINS if pins is None else pins
    if not os.path.isdir(wt):
        return False, f"worktree missing: {wt}"
    rc, head = _git(wt, "rev-parse", "HEAD")
    if rc != 0 or head != commit:
        return False, f"HEAD {head or '?'} != {commit}"
    rc, st = _git(wt, "status", "--porcelain", "--untracked-files=all")
    if rc != 0 or st:
        return False, "worktree not clean (git status --porcelain not empty)"
    for rel, want in pins.items():
        p = os.path.join(wt, rel)
        got = sha256_file(p) if os.path.isfile(p) else "missing"
        if got != want:
            return False, f"sha256 mismatch {rel}: {got[:16]} != {want[:16]}"
    return True, f"PINS OK: HEAD {commit}, clean, {len(pins)} sha256 equal"


def expected_names(start: datetime = FWD_START, end: datetime = FWD_END) -> list[str]:
    n = int((end - start).total_seconds() // 3600)
    return [f"tape-{(start + timedelta(hours=i)).strftime('%Y-%m-%dT%H')}.jsonl.zst" for i in range(n)]


def procs_matching(pat: str, proc: str = "/proc") -> list[int]:
    """pids (not this process) whose command line contains `pat`. Same meaning as `pgrep -f` with a literal pattern."""
    me, out = os.getpid(), []
    try:
        pids = [int(x) for x in os.listdir(proc) if x.isdigit()]
    except OSError:
        return out
    for pid in pids:
        if pid == me:
            continue
        try:
            with open(os.path.join(proc, str(pid), "cmdline"), "rb") as f:
                cmd = f.read().replace(b"\0", b" ").decode("utf-8", "replace")
        except OSError:
            continue
        if pat in cmd:
            out.append(pid)
    return out


def completeness(root: str, now: float | None = None, min_age_s: int = 900, pgrep: Callable[[], list[int]] | None = None) -> tuple[bool, str, float]:
    """Names and lstat mtimes only (never opens, never sizes). Returns (ok, why, age_s of the newest mtime). No count is printed except "168/168"."""
    now = time.time() if now is None else now
    try:
        names = os.listdir(root)
    except OSError as e:
        return False, f"cannot list root: {type(e).__name__}", -1.0
    have = set(names)
    want = expected_names()
    miss = next((n for n in want if n not in have), None)
    if miss is not None:
        return False, f"hour names incomplete (first missing {miss})", -1.0
    bad = next((n for n in names if n.endswith(".part") or n.endswith(".tmp")), None)
    if bad is not None:
        return False, f"partial file name present ({bad})", -1.0
    newest = os.lstat(root).st_mtime
    for n in names:
        newest = max(newest, os.lstat(os.path.join(root, n)).st_mtime)
    age = now - newest
    if age < min_age_s:
        return False, f"newest mtime only {int(age)} s old (< {min_age_s} s)", age
    if pgrep is not None and pgrep():
        return False, "a backfill writer process is running", age
    return True, f"{len(want)}/{len(want)}", age


def guards(dbc_dir: str, texts: Iterable[str]) -> tuple[bool, str]:
    old = os.path.join(dbc_dir, OLD_OUT)
    rd = os.path.join(old, README_NAME)
    if not os.path.isdir(old) or not os.path.isfile(rd):
        return False, f"{OLD_OUT}/{README_NAME} missing"
    if "tools/dbc_fwd_read_am1.py" not in Path(rd).read_text(encoding="utf-8", errors="replace"):
        return False, f"{OLD_OUT}/{README_NAME} does not name the am1 tool"
    for p in (old, rd):
        if os.lstat(p).st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH):
            return False, f"{p} is writable (chmod a-w required)"
    am1 = os.path.join(dbc_dir, AM1_OUT)
    if os.path.lexists(am1):
        if not os.path.isdir(am1) or os.path.islink(am1):
            return False, f"{AM1_OUT} is not a plain directory"
        if os.listdir(am1):
            return False, f"{AM1_OUT} is not empty"
    for t in texts:
        m = OLD_TOOL_RE.search(Path(t).read_text(encoding="utf-8", errors="replace"))
        if m:
            return False, f"{t} names the unamended read tool ({m.group(0).strip()!r})"
    return True, "GUARDS OK: old-tool README in place, am1 out dir absent or empty, no old-tool command text"


def mem_available_gib(meminfo: str = "/proc/meminfo") -> float:
    for ln in Path(meminfo).read_text().splitlines():
        if ln.startswith("MemAvailable:"):
            return int(ln.split()[1]) / (1024 * 1024)
    raise ValueError("MemAvailable not found")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("pins")
    p.add_argument("--wt", required=True)
    p = sub.add_parser("complete")
    p.add_argument("--root", required=True)
    p.add_argument("--min-age", type=int, default=900)
    p.add_argument("--wait-max", type=int, default=0, help="if only the mtime age fails, wait up to this many seconds for it (then check everything again)")
    p.add_argument("--pgrep", default="venue_backfill_dbc")
    p = sub.add_parser("guards")
    p.add_argument("--dbc-dir", required=True)
    p.add_argument("--text", nargs="*", default=[])
    p = sub.add_parser("mem")
    p.add_argument("--min-gb", type=float, required=True)
    ns = ap.parse_args(argv)
    if ns.cmd == "pins":
        ok, why = check_pins(ns.wt)
        print(why if ok else f"REFUSED (pins, exit {EXIT_PINS}, not a look): {why}", file=sys.stdout if ok else sys.stderr, flush=True)
        return 0 if ok else EXIT_PINS
    if ns.cmd == "complete":
        pg = (lambda: procs_matching(ns.pgrep)) if ns.pgrep else None
        deadline = time.time() + ns.wait_max
        while True:
            ok, why, age = completeness(ns.root, min_age_s=ns.min_age, pgrep=pg)
            if ok or age < 0 or "mtime" not in why or time.time() + (ns.min_age - age) > deadline:
                break
            wait = max(1.0, ns.min_age - age + 5)
            print(f"waiting {int(wait)} s for the newest mtime to reach {ns.min_age} s", flush=True)
            time.sleep(wait)
        print(why if ok else f"REFUSED (completeness, exit {EXIT_INCOMPLETE}, not a look): {why}", file=sys.stdout if ok else sys.stderr, flush=True)
        return 0 if ok else EXIT_INCOMPLETE
    if ns.cmd == "guards":
        ok, why = guards(ns.dbc_dir, ns.text)
        print(why if ok else f"REFUSED (guards, exit {EXIT_GUARD}, not a look): {why}", file=sys.stdout if ok else sys.stderr, flush=True)
        return 0 if ok else EXIT_GUARD
    gib = mem_available_gib()
    top = subprocess.run(["ps", "-eo", "rss=,pid=,comm=", "--sort=-rss"], capture_output=True, text=True).stdout.splitlines()[:5]
    print("largest RSS (KiB pid comm): " + " | ".join(x.strip() for x in top), flush=True)
    if gib < ns.min_gb:
        print(f"REFUSED (memory, exit {EXIT_MEM}, not a look): MemAvailable {gib:.1f} GiB < {ns.min_gb} GiB", file=sys.stderr, flush=True)
        return EXIT_MEM
    print(f"MEM OK: MemAvailable {gib:.1f} GiB >= {ns.min_gb} GiB", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
