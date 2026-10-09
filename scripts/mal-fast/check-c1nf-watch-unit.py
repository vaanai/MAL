#!/usr/bin/env python3
"""DEC-026 allowlist check of the C1-NF WATCHDOG unit files (mal-c1nf-watch.service, mal-c1nf-watch.timer). The parser is check-h5-unit.py's
(DEC-024), copied so the C1-NF installer does not depend on an H5 file; read that header for the byte and line rules (LF-only split, space and
tab strip, ASCII only, every line exact and in order).

    /usr/bin/python3 -I check-c1nf-watch-unit.py --watch-service <unit file>   exit 0 = identical to the intended C1-NF watchdog service
    /usr/bin/python3 -I check-c1nf-watch-unit.py --watch-timer   <unit file>   exit 0 = identical to the intended C1-NF watchdog timer

The executor's own units (--base, --dropin, --shadow-feed) are checked by check-c1nf-unit.py, which the executor PR ships.
install-c1nf-executor-pinned.sh runs this on the manifest-verified blobs before anything is moved.
"""
from __future__ import annotations

import sys
from pathlib import Path

PINNED = "/usr/local/lib/mal-c1nf-exec"
PRE_WATCH = (
    "+/usr/bin/env -i /bin/sh -c 'test \"$(/usr/bin/stat -c %%u:%%g:%%a /etc/mal-c1nf-watch)\" = 0:0:700 && "
    "test \"$(/usr/bin/stat -c %%u:%%g:%%a /etc/mal-c1nf-watch/watch.env)\" = 0:0:600'"
)
EXPECTED_WATCH_SERVICE: list[tuple[str, str, str]] = [
    ("Unit", "Description", "MAL C1-NF watchdog (DEC-026 section 5): checks the C1-NF canary and posts alerts to Discord"),
    ("Unit", "Documentation", "https://github.com/vaanai/MAL"),
    ("Unit", "After", "network-online.target"),
    ("Unit", "Wants", "network-online.target"),
    ("Service", "Type", "oneshot"),
    ("Service", "User", "root"),
    ("Service", "EnvironmentFile", "/etc/mal-c1nf-watch/watch.env"),
    ("Service", "ExecStartPre", PRE_WATCH),
    ("Service", "ExecStart", f"/usr/bin/python3 -I -S -B -u {PINNED}/current/c1nf-watch.py"),
    ("Service", "StateDirectory", "mal-c1nf-watch"),
    ("Service", "StateDirectoryMode", "0700"),
    ("Service", "TimeoutStartSec", "120"),
    ("Service", "NoNewPrivileges", "true"),
    ("Service", "ProtectSystem", "strict"),
    ("Service", "ProtectHome", "read-only"),
    ("Service", "InaccessiblePaths", "-/etc/mal-c1nf-key -/etc/mal-probe -/etc/mal-probe-rpc -/run/credentials"),
    ("Service", "PrivateTmp", "true"),
    ("Service", "PrivateDevices", "true"),
    ("Service", "ProtectKernelTunables", "true"),
    ("Service", "ProtectKernelModules", "true"),
    ("Service", "ProtectKernelLogs", "true"),
    ("Service", "ProtectControlGroups", "true"),
    ("Service", "ProtectClock", "true"),
    ("Service", "ProtectHostname", "true"),
    ("Service", "RestrictSUIDSGID", "true"),
    ("Service", "RestrictRealtime", "true"),
    ("Service", "RestrictNamespaces", "true"),
    ("Service", "LockPersonality", "true"),
    ("Service", "RestrictAddressFamilies", "AF_UNIX AF_INET AF_INET6"),
    ("Service", "CapabilityBoundingSet", "CAP_DAC_READ_SEARCH"),
    ("Service", "SystemCallFilter", "@system-service"),
    ("Service", "SystemCallArchitectures", "native"),
    ("Service", "MemoryMax", "256M"),
    ("Service", "MemorySwapMax", "0"),
    ("Service", "UMask", "0077"),
]
EXPECTED_WATCH_TIMER: list[tuple[str, str, str]] = [
    ("Unit", "Description", "MAL C1-NF watchdog every 5 minutes (DEC-026 section 5)"),
    ("Unit", "Documentation", "https://github.com/vaanai/MAL"),
    ("Timer", "OnBootSec", "2min"),
    ("Timer", "OnUnitActiveSec", "5min"),
    ("Timer", "AccuracySec", "30s"),
    ("Install", "WantedBy", "timers.target"),
]
KINDS = {
    "watch-service": (EXPECTED_WATCH_SERVICE, ("Unit", "Service")),
    "watch-timer": (EXPECTED_WATCH_TIMER, ("Unit", "Timer", "Install")),
}

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


def _parse(data: "bytes | str", sections: tuple[str, ...]) -> tuple[list[tuple[str, str, str]], list[str]]:
    if isinstance(data, str):
        data = data.encode("utf-8")
    bad = sorted({b for b in data if b not in OK_BYTES})
    if bad:
        return [], ["file contains bytes outside TAB, LF and 0x20-0x7e: " + " ".join(f"0x{b:02x}" for b in bad[:8])]
    errs: list[str] = []
    seen: list[str] = []
    section = ""
    got: list[tuple[str, str, str]] = []
    for line in logical_lines(data.decode("ascii")):
        if line.startswith("["):
            name = line[1:-1] if line.endswith("]") else None
            if name not in sections:
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
    return got, errs


def problems(data: "bytes | str", kind: str) -> list[str]:
    expected, sections = KINDS[kind]
    got, errs = _parse(data, sections)
    if errs and not got:
        return errs
    for g in got:
        if g not in expected:
            errs.append(f"unexpected line [{g[0]}] {g[1]}={g[2]!r}")
    for e in expected:
        if got.count(e) != expected.count(e):
            errs.append(f"missing or duplicated line [{e[0]}] {e[1]}={e[2]!r}")
    if not errs and got != expected:
        errs.append("lines are not in the intended order")
    return errs


def main(argv: list[str]) -> int:
    kinds = {"--watch-service": "watch-service", "--watch-timer": "watch-timer"}
    if len(argv) != 3 or argv[1] not in kinds:
        print("usage: check-c1nf-watch-unit.py (--watch-service|--watch-timer) <file>", file=sys.stderr)
        return 2
    errs = problems(Path(argv[2]).read_bytes(), kinds[argv[1]])
    for e in errs:
        print(f"check-c1nf-watch-unit: {e}", file=sys.stderr)
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
