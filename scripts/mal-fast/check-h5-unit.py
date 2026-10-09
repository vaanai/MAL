#!/usr/bin/env python3
"""DEC-024 allowlist check of the H5 executor unit files. Same method as check-probe-base-unit.py (DEC-019).

    /usr/bin/python3 -I check-h5-unit.py --base        <unit file>   exit 0 = identical to the intended base unit
    /usr/bin/python3 -I check-h5-unit.py --dropin      <conf file>   exit 0 = identical to the intended pinned live drop-in
    /usr/bin/python3 -I check-h5-unit.py --shadow-feed <conf file>   exit 0 = a valid shadow-feed bind (one line, see below)
    /usr/bin/python3 -I check-h5-unit.py --watch-service <unit file> exit 0 = identical to the intended watchdog service
    /usr/bin/python3 -I check-h5-unit.py --watch-timer   <unit file> exit 0 = identical to the intended watchdog timer

The installer runs --base and --dropin on the manifest-verified blobs before anything is moved. Helm (and the daily check)
run --dropin on /etc/systemd/system/mal-h5-executor.service.d/live.conf and --shadow-feed on 10-shadow-feed.conf.

--base / --dropin: the file must equal the EXPECTED list line for line. Stdlib only, no grep. Only the listed sections, each at
most once. Any unknown key, extra or missing line, duplicate, different value or different order refuses. Keys are case-sensitive
and exact: `User =x` is an unknown key to systemd, so whitespace between key and `=` is NOT normalised. An empty value
(`ExecStart=`) is a value.

--shadow-feed: the only file allowed to differ per host. [Service] once, exactly one key BindReadOnlyPaths, whose value is
`-<source>:/srv/mal-h5-shadow` with <source> = /home/<user>/<dir>/.../h5-shadow<suffix>, every component made of [A-Za-z0-9_.-]
and not starting with a dot (no `..`, no hidden dirs such as .ssh or .claude). Nothing else can ride along.

The file is read as BYTES and refused if any byte is outside {TAB, LF, 0x20-0x7e}: no NUL, BOM, CR (CRLF too), VT, FF, 0x1c-0x1e,
0x85, NBSP, U+3000 or any other non-ASCII. Python's str.splitlines()/strip() treat those as line breaks or whitespace, systemd 255
does not (it splits on LF only and strips only space, tab and CR). Lines are split on LF only and stripped of space and tab only,
so what this script sees is what systemd sees.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

PRE = (
    "+/usr/bin/env -i /bin/sh -c 'test \"$(/usr/bin/stat -c %%u:%%g:%%a /etc/mal-probe-rpc)\" = 0:0:700 && "
    "test \"$(/usr/bin/stat -c %%u:%%g:%%a /etc/mal-probe-rpc/helius.env)\" = 0:0:600'"
)
PINNED = "/usr/local/lib/mal-h5-exec"
PY = f"{PINNED}/venv/bin/python -I -B -u {PINNED}/current/launcher.py"

EXPECTED_BASE: list[tuple[str, str, str]] = [
    ("Unit", "Description", "MAL H5-BOOSTFLOOR executor (DEC-024), keyless dry-run base unit (no signing, no sending)"),
    ("Unit", "Documentation", "https://github.com/vaanai/MAL"),
    ("Unit", "After", "network-online.target"),
    ("Unit", "Wants", "network-online.target"),
    ("Unit", "After", "mal-probe-executor.service"),
    ("Unit", "Conflicts", "mal-probe-executor.service"),
    ("Service", "Type", "simple"),
    ("Service", "User", "mal-live"),
    ("Service", "Nice", "10"),
    ("Service", "WorkingDirectory", f"{PINNED}/current"),
    ("Service", "Environment", "PYTHONUNBUFFERED=1"),
    ("Service", "Environment", "LC_ALL=C.UTF-8"),
    ("Service", "Environment", "LANG=C.UTF-8"),
    ("Service", "EnvironmentFile", "/etc/mal-probe-rpc/helius.env"),
    ("Service", "ExecStartPre", PRE),
    ("Service", "ExecStart", f"{PY} --config {PINNED}/current/h5-executor.json"),
    ("Service", "Restart", "on-failure"),
    ("Service", "RestartSec", "10"),
    ("Service", "RestartPreventExitStatus", "2"),
    ("Service", "NoNewPrivileges", "true"),
    ("Service", "ProtectSystem", "strict"),
    ("Service", "ReadWritePaths", "/var/lib/mal-live/h5"),
    ("Service", "ReadOnlyPaths", PINNED),
    ("Service", "TemporaryFileSystem", "/var/lib/mal:ro"),
    ("Service", "ProtectHome", "tmpfs"),
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
    ("Service", "SystemCallArchitectures", "native"),
    ("Service", "RestrictNamespaces", "true"),
    ("Service", "RestrictRealtime", "true"),
    ("Service", "ProtectClock", "true"),
    ("Service", "ProtectKernelLogs", "true"),
    ("Service", "ProtectHostname", "true"),
    ("Service", "UMask", "0077"),
    ("Service", "LimitCORE", "0"),
    ("Service", "MemoryMax", "1G"),
    ("Service", "MemorySwapMax", "0"),
    ("Install", "WantedBy", "multi-user.target"),
]
EXPECTED_DROPIN: list[tuple[str, str, str]] = [
    ("Service", "LoadCredential", "probe-wallet:/etc/mal-probe/probe-wallet.json"),
    ("Service", "ExecStart", ""),
    ("Service", "ExecStart", f"{PY} --config {PINNED}/current/h5-executor-live.json --live"),
]
PRE_WATCH = (
    "+/usr/bin/env -i /bin/sh -c 'test \"$(/usr/bin/stat -c %%u:%%g:%%a /etc/mal-h5-watch)\" = 0:0:700 && "
    "test \"$(/usr/bin/stat -c %%u:%%g:%%a /etc/mal-h5-watch/watch.env)\" = 0:0:600'"
)
EXPECTED_WATCH_SERVICE: list[tuple[str, str, str]] = [
    ("Unit", "Description", "MAL H5 watchdog (DEC-024 section 8): checks the H5 canary and posts alerts to Discord"),
    ("Unit", "Documentation", "https://github.com/vaanai/MAL"),
    ("Unit", "After", "network-online.target"),
    ("Unit", "Wants", "network-online.target"),
    ("Service", "Type", "oneshot"),
    ("Service", "User", "root"),
    ("Service", "EnvironmentFile", "/etc/mal-h5-watch/watch.env"),
    ("Service", "ExecStartPre", PRE_WATCH),
    ("Service", "ExecStart", f"/usr/bin/python3 -I -S -B -u {PINNED}/current/h5-watch.py"),
    ("Service", "StateDirectory", "mal-h5-watch"),
    ("Service", "StateDirectoryMode", "0700"),
    ("Service", "TimeoutStartSec", "120"),
    ("Service", "NoNewPrivileges", "true"),
    ("Service", "ProtectSystem", "strict"),
    ("Service", "ProtectHome", "read-only"),
    ("Service", "InaccessiblePaths", "-/etc/mal-probe -/etc/mal-probe-rpc -/run/credentials"),
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
    ("Unit", "Description", "MAL H5 watchdog every 5 minutes (DEC-024 section 8)"),
    ("Unit", "Documentation", "https://github.com/vaanai/MAL"),
    ("Timer", "OnBootSec", "2min"),
    ("Timer", "OnUnitActiveSec", "5min"),
    ("Timer", "AccuracySec", "30s"),
    ("Install", "WantedBy", "timers.target"),
]
KINDS = {
    "base": (EXPECTED_BASE, ("Unit", "Service", "Install")),
    "dropin": (EXPECTED_DROPIN, ("Service",)),
    "watch-service": (EXPECTED_WATCH_SERVICE, ("Unit", "Service")),
    "watch-timer": (EXPECTED_WATCH_TIMER, ("Unit", "Timer", "Install")),
}

SHADOW_DEST = "/srv/mal-h5-shadow"
_COMP = r"[A-Za-z0-9_-][A-Za-z0-9_.-]*"
SHADOW_RE = re.compile(rf"^-(/home/{_COMP}(?:/{_COMP})*/h5-shadow[A-Za-z0-9_.-]*):{re.escape(SHADOW_DEST)}$")

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


def problems(data: "bytes | str", kind: str = "base") -> list[str]:
    if kind == "shadow-feed":
        return shadow_problems(data)
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


def shadow_problems(data: "bytes | str") -> list[str]:
    got, errs = _parse(data, ("Service",))
    if errs:
        return errs
    if len(got) != 1 or got[0][1] != "BindReadOnlyPaths":
        return [f"expected exactly one BindReadOnlyPaths line in [Service], got {[(g[1]) for g in got]!r}"]
    val = got[0][2]
    if not SHADOW_RE.match(val):
        return [f"BindReadOnlyPaths value {val!r} is not -/home/<user>/.../h5-shadow*:{SHADOW_DEST} with plain path components"]
    return []


def main(argv: list[str]) -> int:
    kinds = {"--base": "base", "--dropin": "dropin", "--shadow-feed": "shadow-feed", "--watch-service": "watch-service", "--watch-timer": "watch-timer"}
    if len(argv) != 3 or argv[1] not in kinds:
        print("usage: check-h5-unit.py (--base|--dropin|--shadow-feed|--watch-service|--watch-timer) <file>", file=sys.stderr)
        return 2
    errs = problems(Path(argv[2]).read_bytes(), kinds[argv[1]])
    for e in errs:
        print(f"check-h5-unit: {e}", file=sys.stderr)
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
