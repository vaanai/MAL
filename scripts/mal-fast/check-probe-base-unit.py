#!/usr/bin/env python3
"""DEC-019 allowlist check of the probe base unit, run by install-probe-executor-pinned.sh before anything is moved.

    /usr/bin/python3 -I check-probe-base-unit.py <unit file>      exit 0 = identical to the intended unit, else 1

The pinned drop-in inherits User=, Environment=, EnvironmentFile= and all hardening from this unit, so the unit
must equal EXPECTED exactly: stdlib only, no grep. Parsing follows probe-dropin-fence.py (continuations joined,
comment lines skipped). Only [Unit], [Service] and [Install], each at most once. Every other line, any unknown
key, extra or missing line, duplicate, or different value refuses. Only whitespace around `=` and at the line ends
is normalised. Keys are case-sensitive (as in systemd). An empty value (`User=`) is a value and must match.
"""
from __future__ import annotations

import sys
from pathlib import Path

PRE = (
    "+/bin/sh -c 'test \"$(stat -c %%u:%%g:%%a /etc/mal-probe-rpc)\" = 0:0:700 && "
    "test \"$(stat -c %%u:%%g:%%a /etc/mal-probe-rpc/helius.env)\" = 0:0:600'"
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


def logical_lines(text: str) -> list[str]:
    out: list[str] = []
    cur = ""
    for raw in text.splitlines():
        line = raw.rstrip()
        if line.lstrip()[:1] in ("#", ";"):
            continue
        if line.endswith("\\"):
            cur += line[:-1] + " "
            continue
        out.append((cur + line).strip())
        cur = ""
    if cur.strip():
        out.append(cur.strip())
    return [x for x in out if x]


def problems(text: str) -> list[str]:
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
        got.append((section, key.strip(), val.strip()))
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
    errs = problems(Path(argv[1]).read_text(encoding="utf-8", errors="strict"))
    for e in errs:
        print(f"check-probe-base-unit: {e}", file=sys.stderr)
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
