#!/usr/bin/env python3
"""DEC-026 allowlist check of the C1-NF executor unit files. A copy of check-h5-unit.py (DEC-024) with C1-NF's paths and credential.

    /usr/bin/python3 -I check-c1nf-unit.py --base        <unit file>   exit 0 = identical to the intended base unit
    /usr/bin/python3 -I check-c1nf-unit.py --dropin      <conf file>   exit 0 = identical to the intended pinned live drop-in
    /usr/bin/python3 -I check-c1nf-unit.py --shadow-feed <conf file>   exit 0 = a valid shadow-feed bind (one line, see below)
    /usr/bin/python3 -I check-c1nf-unit.py --cap-pick    <conf file>   exit 0 = a valid CAP-PICK oracle bind (one line, see below)

The watchdog units are checked by check-c1nf-watch-unit.py. The installer runs --base and --dropin on the manifest-verified blobs
before anything is moved. Helm (and c1nf-daily-check.py, which imports this file from the pinned tree and calls problems()) run
--dropin on /etc/systemd/system/mal-c1nf-executor.service.d/live.conf, --shadow-feed on 10-shadow-feed.conf and --cap-pick on 20-cap-pick.conf.

Second wallet (DEC-026 section 4): the drop-in's only credential is c1nf-wallet:/etc/mal-c1nf-key/c1nf-wallet.json. H5's
probe-wallet, any other credential and any Conflicts= with an H5/probe unit refuse (they are not on the list).

Own Unix user (DEC-026 note 2026-10-10, security review F2): the base unit's only User= is mal-c1nf. H5's mal-live, root or any other
user refuses, and so does a Group=, SupplementaryGroups= or DynamicUser= line (not on the list): systemd makes a unit's
/run/credentials/<unit>/ readable by its User=, so one uid per wallet is what keeps each executor away from the other's key.

--base / --dropin: the file must equal the EXPECTED list line for line. Stdlib only, no grep. Only the listed sections, each at
most once. Any unknown key, extra or missing line, duplicate, different value or different order refuses. Keys are case-sensitive
and exact: `User =x` is an unknown key to systemd, so whitespace between key and `=` is NOT normalised. An empty value
(`ExecStart=`) is a value.

--shadow-feed: the only file allowed to differ per host. [Service] once, exactly one key BindReadOnlyPaths, whose value is
`-<source>:/srv/mal-c1nf-shadow` with <source> = /home/<user>/<dir>/.../c1nf-shadow<suffix>, every component made of
[A-Za-z0-9_.-] and not starting with a dot (no `..`, no hidden dirs such as .ssh or .claude). Nothing else can ride along.

--cap-pick: the same rule for the CAP-PICK oracle bind (DEC-026 Amendment 1 item B): exactly one BindReadOnlyPaths line in [Service],
`-<source>:/srv/mal-cap-pick` with <source> = /home/<user>/<dir>/.../cap-pick (the exporter's CAP_PICK_OUT; the exact last component, no suffix: `cap-pick-oracle`, the
FINAL marker's directory, is refused). The live config's
"pick_file" is /srv/mal-cap-pick/picks.jsonl.

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
PINNED = "/usr/local/lib/mal-c1nf-exec"
PY = f"{PINNED}/venv/bin/python -I -B -u {PINNED}/current/launcher.py"
CREDENTIAL = "c1nf-wallet:/etc/mal-c1nf-key/c1nf-wallet.json"

EXPECTED_BASE: list[tuple[str, str, str]] = [
    ("Unit", "Description", "MAL C1-NF executor (DEC-026), keyless dry-run base unit (no signing, no sending)"),
    ("Unit", "Documentation", "https://github.com/vaanai/MAL"),
    ("Unit", "After", "network-online.target"),
    ("Unit", "Wants", "network-online.target"),
    ("Service", "Type", "simple"),
    ("Service", "User", "mal-c1nf"),  # its own user, never H5's mal-live (DEC-026 note 2026-10-10, security review F2)
    ("Service", "Nice", "10"),
    ("Service", "WorkingDirectory", f"{PINNED}/current"),
    ("Service", "Environment", "PYTHONUNBUFFERED=1"),
    ("Service", "Environment", "LC_ALL=C.UTF-8"),
    ("Service", "Environment", "LANG=C.UTF-8"),
    ("Service", "EnvironmentFile", "/etc/mal-probe-rpc/helius.env"),
    ("Service", "ExecStartPre", PRE),
    ("Service", "ExecStart", f"{PY} --config {PINNED}/current/c1nf-executor.json"),
    ("Service", "Restart", "on-failure"),
    ("Service", "RestartSec", "10"),
    ("Service", "RestartPreventExitStatus", "2"),
    ("Service", "NoNewPrivileges", "true"),
    ("Service", "ProtectSystem", "strict"),
    ("Service", "ReadWritePaths", "/var/lib/mal-live/c1nf"),
    ("Service", "ReadOnlyPaths", PINNED),
    ("Service", "InaccessiblePaths", "-/var/lib/mal-live/h5 -/etc/mal-h5 -/etc/mal-probe -/usr/local/lib/mal-h5-exec"),
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
    ("Service", "LoadCredential", CREDENTIAL),
    ("Service", "ExecStart", ""),
    ("Service", "ExecStart", f"{PY} --config {PINNED}/current/c1nf-executor-live.json --live"),
]
KINDS = {
    "base": (EXPECTED_BASE, ("Unit", "Service", "Install")),
    "dropin": (EXPECTED_DROPIN, ("Service",)),
}

SHADOW_DEST = "/srv/mal-c1nf-shadow"
_COMP = r"[A-Za-z0-9_-][A-Za-z0-9_.-]*"
SHADOW_RE = re.compile(rf"^-(/home/{_COMP}(?:/{_COMP})*/c1nf-shadow[A-Za-z0-9_.-]*):{re.escape(SHADOW_DEST)}$")
CAP_PICK_DEST = "/srv/mal-cap-pick"  # the live config's pick_file is CAP_PICK_DEST + "/picks.jsonl" (DEC-026 Amendment 1 item B)
CAP_PICK_RE = re.compile(rf"^-(/home/{_COMP}(?:/{_COMP})*/cap-pick):{re.escape(CAP_PICK_DEST)}$")  # the EXACT name: never cap-pick-oracle (the FINAL marker's directory) or any suffix

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
    if kind == "cap-pick":
        return cap_pick_problems(data)
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


def _bind_problems(data: "bytes | str", pattern: "re.Pattern[str]", name: str, dest: str) -> list[str]:
    got, errs = _parse(data, ("Service",))
    if errs:
        return errs
    if len(got) != 1 or got[0][1] != "BindReadOnlyPaths":
        return [f"expected exactly one BindReadOnlyPaths line in [Service], got {[(g[1]) for g in got]!r}"]
    val = got[0][2]
    if not pattern.match(val):
        return [f"BindReadOnlyPaths value {val!r} is not -/home/<user>/.../{name}:{dest} with plain path components"]
    return []


def shadow_problems(data: "bytes | str") -> list[str]:
    return _bind_problems(data, SHADOW_RE, "c1nf-shadow*", SHADOW_DEST)


def cap_pick_problems(data: "bytes | str") -> list[str]:
    return _bind_problems(data, CAP_PICK_RE, "cap-pick", CAP_PICK_DEST)


def main(argv: list[str]) -> int:
    kinds = {"--base": "base", "--dropin": "dropin", "--shadow-feed": "shadow-feed", "--cap-pick": "cap-pick"}
    if len(argv) != 3 or argv[1] not in kinds:
        print("usage: check-c1nf-unit.py (--base|--dropin|--shadow-feed|--cap-pick) <file>", file=sys.stderr)
        return 2
    errs = problems(Path(argv[2]).read_bytes(), kinds[argv[1]])
    for e in errs:
        print(f"check-c1nf-unit: {e}", file=sys.stderr)
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
