#!/usr/bin/env python3
"""DEC-019 allowlist check of the probe base unit, run by install-probe-executor-pinned.sh before anything is moved.

    /usr/bin/python3 -I check-probe-base-unit.py <unit file>      exit 0 = identical to the intended unit, else 1

The pinned drop-in inherits User=, Environment=, EnvironmentFile= and all hardening from this unit, so the unit
must equal EXPECTED exactly: stdlib only, no grep. Parsing follows probe-dropin-fence.py (continuations joined,
comment lines skipped). Only [Unit], [Service] and [Install], each at most once. Every other line, any unknown
key, extra or missing line, duplicate, or different value refuses. Keys are case-sensitive and exact: `User =x` is
an unknown key to systemd, so whitespace between key and `=` is NOT normalised. An empty value (`User=`) is a value.

The file is read as BYTES and refused if any byte is outside {TAB, LF, 0x20-0x7e}: no NUL, BOM, CR (CRLF too), VT, FF,
0x1c-0x1e, 0x85, NBSP, U+3000 or any other non-ASCII. Python's str.splitlines()/strip() treat those as line breaks or
whitespace, systemd 255 does not (it splits on LF only and strips only space, tab and CR). Lines are split on LF only
and stripped of space and tab only, so what this script sees is what systemd sees.
"""
from __future__ import annotations

import sys
from pathlib import Path

# EnvironmentFile applies to the "+" command as well, so it runs under `env -i` with absolute paths: a PATH= or LD_PRELOAD=
# in the (root-only) env file cannot redirect `stat`.
PRE = (
    "+/usr/bin/env -i /bin/sh -c 'test \"$(/usr/bin/stat -c %%u:%%g:%%a /etc/mal-probe-rpc)\" = 0:0:700 && "
    "test \"$(/usr/bin/stat -c %%u:%%g:%%a /etc/mal-probe-rpc/helius.env)\" = 0:0:600'"
)

EXPECTED: list[tuple[str, str, str]] = [
    ("Unit", "Description", "MAL DEC-019 execution probe, keyless dry-run executor (no signing, no sending)"),
    ("Unit", "Documentation", "https://github.com/vaanai/MAL"),
    ("Unit", "After", "network-online.target"),
    ("Unit", "Wants", "network-online.target"),
    ("Service", "Type", "simple"),
    ("Service", "User", "mal-live"),
    ("Service", "Nice", "10"),
    ("Service", "WorkingDirectory", "/var/lib/mal/fast-forward/src"),
    ("Service", "Environment", "PYTHONUNBUFFERED=1"),
    ("Service", "Environment", "LC_ALL=C.UTF-8"),
    ("Service", "Environment", "LANG=C.UTF-8"),
    ("Service", "EnvironmentFile", "/etc/mal-probe-rpc/helius.env"),
    ("Service", "ExecStartPre", PRE),
    ("Service", "ExecStart", "/var/lib/mal/fast-forward/venv/bin/python -m tools.probe_executor --config /var/lib/mal/fast-forward/src/scripts/mal-fast/probe-executor.json"),
    ("Service", "Restart", "on-failure"),
    ("Service", "RestartSec", "10"),
    ("Service", "NoNewPrivileges", "true"),
    ("Service", "ProtectSystem", "strict"),
    ("Service", "ReadWritePaths", "/var/lib/mal-live"),
    ("Service", "ReadOnlyPaths", "/var/lib/mal/fast-forward"),
    ("Service", "TemporaryFileSystem", "/var/lib/mal/paper/fast-forward-paper:ro"),
    ("Service", "BindReadOnlyPaths", "-/var/lib/mal/paper/fast-forward-paper/intents.jsonl"),
    ("Service", "InaccessiblePaths", "/var/lib/mal/sealed"),
    ("Service", "ProtectHome", "true"),
    ("Service", "PrivateTmp", "true"),
    ("Service", "ProtectKernelTunables", "true"),
    ("Service", "ProtectKernelModules", "true"),
    ("Service", "ProtectControlGroups", "true"),
    ("Service", "RestrictSUIDSGID", "true"),
    ("Service", "LockPersonality", "true"),
    ("Service", "RestrictAddressFamilies", "AF_INET AF_INET6"),
    ("Service", "CapabilityBoundingSet", ""),
    ("Service", "PrivateDevices", "true"),
    ("Service", "ProtectProc", "invisible"),
    ("Service", "SystemCallFilter", "@system-service"),
    ("Service", "MemoryMax", "1G"),
    ("Service", "MemorySwapMax", "0"),
    ("Install", "WantedBy", "multi-user.target"),
]
SECTIONS = ("Unit", "Service", "Install")


OK_BYTES = frozenset([9, 10, *range(0x20, 0x7F)])
WS = " \t"


def logical_lines(text: str) -> list[str]:
    """text is pure ASCII (checked by the caller). LF-only split, space/tab-only strip, continuations joined."""
    out: list[str] = []
    cur = ""
    for raw in text.split("\n"):
        line = raw.strip(WS)
        if line[:1] in ("#", ";"):
            continue
        if line.endswith("\\"):
            cur += line[:-1] + " "
            continue
        out.append((cur + line).strip(WS))
        cur = ""
    if cur.strip(WS):
        out.append(cur.strip(WS))
    return [x for x in out if x]


def problems(data: "bytes | str") -> list[str]:
    if isinstance(data, str):
        data = data.encode("utf-8")
    bad = sorted({b for b in data if b not in OK_BYTES})
    if bad:
        return ["file contains bytes outside TAB, LF and 0x20-0x7e: " + " ".join(f"0x{b:02x}" for b in bad[:8])]
    text = data.decode("ascii")
    errs: list[str] = []
    seen: list[str] = []
    section = ""
    got: list[tuple[str, str, str]] = []
    for line in logical_lines(text):
        if line.startswith("["):
            name = line[1:-1] if line.endswith("]") else None
            if name not in SECTIONS:
                errs.append(f"unknown section {line!r}")
                section = ""
                continue
            if name in seen:
                errs.append(f"duplicate section [{name}]")
            seen.append(name)
            section = name
            continue
        if not section:
            errs.append(f"line outside an allowed section: {line!r}")
            continue
        if "=" not in line:
            errs.append(f"not a key=value line: {line!r}")
            continue
        key, _, val = line.partition("=")
        got.append((section, key, val.strip(WS)))
    for g in got:
        if g not in EXPECTED:
            errs.append(f"unexpected line [{g[0]}] {g[1]}={g[2]!r}")
    for e in EXPECTED:
        if got.count(e) != EXPECTED.count(e):
            errs.append(f"missing or duplicated line [{e[0]}] {e[1]}={e[2]!r}")
    if not errs and got != EXPECTED:
        errs.append("lines are not in the intended order")
    return errs


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: check-probe-base-unit.py <unit>", file=sys.stderr)
        return 2
    errs = problems(Path(argv[1]).read_bytes())
    for e in errs:
        print(f"check-probe-base-unit: {e}", file=sys.stderr)
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
