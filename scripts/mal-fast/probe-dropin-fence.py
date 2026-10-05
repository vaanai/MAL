#!/usr/bin/env python3
"""DEC-019 live key-holder fence helper for install-fast-forward-paper.sh.

    probe-dropin-fence.py <dropin_dir> <pinned_conf>

Prints one line and exits 0:
    none              no drop-in makes the executor live-capable (dry-run unit only)
    pinned <files>    the final effective ExecStart is exactly the pinned command from <pinned_conf>
    unpinned <why>    anything else: refuse the reinstall

It reads EVERY *.conf in <dropin_dir> in lexical order (as systemd does), joins backslash continuations,
ignores comment lines (# and ;), looks at [Service] only, and applies ExecStart semantics: a bare
`ExecStart=` clears the list, every other one appends. A drop-in counts as live-capable when it sets
LoadCredential or an ExecStart (a dry-run base unit has neither). The file NAME is never trusted:
live.conf and live-pinned.conf get the same content check as any other file.
"""
from __future__ import annotations

import sys
from pathlib import Path


def logical_lines(text: str) -> list[str]:
    out: list[str] = []
    cur = ""
    for raw in text.splitlines():
        line = raw.rstrip()
        if not cur and line.lstrip()[:1] in ("#", ";"):
            continue  # a comment line is never a continuation start
        if cur and line.lstrip()[:1] in ("#", ";"):
            continue  # systemd skips comment lines inside a continuation too
        if line.endswith("\\"):
            cur += line[:-1] + " "
            continue
        out.append((cur + line).strip())
        cur = ""
    if cur.strip():
        out.append(cur.strip())
    return out


def effective(dropin_dir: Path) -> tuple[list[str], bool]:
    """(effective ExecStart list, live_capable) over all *.conf in lexical order."""
    execs: list[str] = []
    capable = False
    for f in sorted(dropin_dir.glob("*.conf")):
        section = ""
        for line in logical_lines(f.read_text(encoding="utf-8", errors="replace")):
            if line.startswith("[") and line.endswith("]"):
                section = line
                continue
            if section != "[Service]" or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key, val = key.strip(), " ".join(val.split())
            if key == "ExecStart":
                capable = True
                if val == "":
                    execs = []
                else:
                    execs.append(val)
            elif key == "LoadCredential":
                capable = True
    return execs, capable


def verdict(dropin_dir: Path, pinned_conf: Path) -> str:
    if not dropin_dir.is_dir():
        return "none"
    execs, capable = effective(dropin_dir)
    if not capable:
        return "none"
    want, _ = effective_of_file(pinned_conf)
    if want and execs == want:
        return "pinned " + ",".join(sorted(p.name for p in dropin_dir.glob("*.conf")))
    return f"unpinned effective ExecStart={execs!r}"


def effective_of_file(conf: Path) -> tuple[list[str], bool]:
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "x.conf").write_text(conf.read_text(encoding="utf-8"), encoding="utf-8")
        return effective(Path(d))


if __name__ == "__main__":
    print(verdict(Path(sys.argv[1]), Path(sys.argv[2])))
