#!/usr/bin/env python3
"""Per-box status collector for the MAL Console (docs/console-plan.md §9.4).

Writes ``<out-dir>/<host>.json``, schema ``status.v1``, atomically (tmp file +
``os.replace``). Read-only: it never restarts a unit, never writes under
``/var/lib/mal``, and never opens a secret/env file. It reads a plain counter
file (``pre-create-credits.json``) only after checking it has no key-like
field names.

Two modes:

- local (default): collects load/memory/disk/services/walkers/credits on the
  host this script runs on (``mal-fast-0`` defaults are baked in below).
- ``--remote core``: runs exactly one read-only SSH command against
  ``mal-core-0`` (a small embedded Python snippet on stdin) and writes
  ``mal-core-0.json``. Never crashes on an SSH failure; writes
  ``{"reachable": false, "error": ...}`` instead.
- ``--remote research``: one SSH command against ``mal-research-0`` that pipes
  this very file to ``python3 - --units-json`` (so the same unit collector
  runs there) and writes ``mal-research-0.json``.

Every status file also carries a ``units`` list (docs/contracts/status-files.md):
one entry per ``mal-*`` service/timer with state, uptime, memory and the last
log line (always passed through ``scrub_log_line`` before it is written).

Every section is independently try/excepted. Failures are recorded in the
top-level ``errors`` list and never stop the rest of the collection.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import shutil
import socket
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import unquote

SCHEMA_VERSION = "status.v1"

# --- fast (mal-fast-0) defaults ---------------------------------------------

FAST_CGROUPS = [
    {"name": "claude", "path": "/sys/fs/cgroup/user.slice/user-1002.slice"},
    {"name": "ubuntu", "path": "/sys/fs/cgroup/user.slice/user-1000.slice"},
]

FAST_MOUNTS = ["/", "/var/lib/mal"]

FAST_SERVICES = [
    {"name": "mal-fast-create", "owner": "ubuntu"},
    {"name": "mal-fast-public-logs", "owner": "ubuntu"},
    {"name": "mal-fast-pre-create", "owner": "ubuntu"},
    {"name": "mal-fast-backfill", "owner": "ubuntu"},
    {"name": "mal-fast-backfill-b", "owner": "ubuntu"},
    {"name": "mal-fast-backfill-c", "owner": "ubuntu"},
    {"name": "mal-daily-review.timer", "owner": "claude"},
    {"name": "mal-runner-daily-restart.timer", "owner": "claude"},
]

FAST_WALKERS = [
    {"name": "walker_1", "checkpoint_dir": "/var/lib/mal/backfill-fast"},
    {"name": "walker_b", "checkpoint_dir": "/var/lib/mal/backfill-fast-b"},
    {"name": "walker_c", "checkpoint_dir": "/var/lib/mal/backfill-fast-c"},
]

DEFAULT_PRE_CREATE_CREDITS_PATH = "/var/lib/mal/fast-listener/pre-create-credits.json"
DEFAULT_RESTART_LOG_PATH = "/home/claude/reports/runner-restarts.jsonl"
DEFAULT_UBUNTU_UID = 1000

# Field-name substrings that mean "do not parse this file, it might hold a
# secret" even though the path is a plain counter file today.
SECRET_LIKE_KEY_HINTS = ("key", "secret", "token", "password", "private")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --- load / uptime / cpu -----------------------------------------------------


def read_uptime_seconds(path: str = "/proc/uptime") -> float | None:
    with open(path, encoding="utf-8") as fh:
        return float(fh.read().split()[0])


def read_loadavg(path: str = "/proc/loadavg") -> tuple[float, float, float]:
    with open(path, encoding="utf-8") as fh:
        parts = fh.read().split()
    return float(parts[0]), float(parts[1]), float(parts[2])


def read_cpu_count() -> int | None:
    return os.cpu_count()


# --- memory -------------------------------------------------------------------


def read_meminfo(path: str = "/proc/meminfo") -> tuple[float | None, float | None]:
    info: dict[str, str] = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            key, _, rest = line.partition(":")
            info[key.strip()] = rest.strip()

    def kb(key: str) -> int | None:
        val = info.get(key)
        if not val:
            return None
        return int(val.split()[0])

    total_kb = kb("MemTotal")
    avail_kb = kb("MemAvailable")
    total_mb = round(total_kb / 1024, 1) if total_kb is not None else None
    avail_mb = round(avail_kb / 1024, 1) if avail_kb is not None else None
    return total_mb, avail_mb


def read_cgroup_memory(name: str, cgroup_path: str) -> dict[str, Any]:
    """anon_mb comes from memory.stat's "anon" line — never page cache.

    current_mb/max_mb come from memory.current/memory.max. memory.max of
    literal "max" (no ceiling set) becomes ``None``, not a number.
    """
    anon_mb: float | None = None
    with open(os.path.join(cgroup_path, "memory.stat"), encoding="utf-8") as fh:
        for line in fh:
            parts = line.split()
            if len(parts) == 2 and parts[0] == "anon":
                anon_mb = round(int(parts[1]) / (1024 * 1024), 1)
                break

    with open(os.path.join(cgroup_path, "memory.current"), encoding="utf-8") as fh:
        current_mb = round(int(fh.read().strip()) / (1024 * 1024), 1)

    with open(os.path.join(cgroup_path, "memory.max"), encoding="utf-8") as fh:
        raw_max = fh.read().strip()
    max_mb = None if raw_max == "max" else round(int(raw_max) / (1024 * 1024), 1)

    return {"name": name, "path": cgroup_path, "anon_mb": anon_mb, "current_mb": current_mb, "max_mb": max_mb}


def collect_memory(cgroups: list[dict[str, str]], errors: list[str]) -> dict[str, Any]:
    mem_total_mb, mem_available_mb = None, None
    try:
        mem_total_mb, mem_available_mb = read_meminfo()
    except Exception as exc:  # noqa: BLE001
        errors.append(f"meminfo: {exc}")

    cgroup_results = []
    for cg in cgroups:
        try:
            cgroup_results.append(read_cgroup_memory(cg["name"], cg["path"]))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"cgroup {cg.get('name', cg.get('path'))}: {exc}")
            cgroup_results.append({"name": cg.get("name"), "path": cg.get("path"), "error": str(exc)})

    return {
        "mem_total_mb": mem_total_mb,
        "mem_available_mb": mem_available_mb,
        "cgroups": cgroup_results,
    }


# --- disk ---------------------------------------------------------------------


def read_disk_usage(mount: str) -> dict[str, Any]:
    usage = shutil.disk_usage(mount)
    pct = round(100.0 * usage.used / usage.total, 1) if usage.total else None
    return {
        "mount": mount,
        "total_gb": round(usage.total / 1e9, 2),
        "used_gb": round(usage.used / 1e9, 2),
        "free_gb": round(usage.free / 1e9, 2),
        "pct": pct,
    }


def collect_disk(mounts: list[str], errors: list[str]) -> list[dict[str, Any]]:
    results = []
    for mount in mounts:
        if not os.path.exists(mount):
            continue
        try:
            results.append(read_disk_usage(mount))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"disk {mount}: {exc}")
            results.append({"mount": mount, "error": str(exc)})
    return results


# --- services -------------------------------------------------------------------


def query_service_state(name: str, owner: str, *, ubuntu_uid: int = DEFAULT_UBUNTU_UID, timeout: float = 5.0) -> str:
    """Return "active"/"inactive"/"failed"/... or "unknown" on any failure.

    ``systemctl ... is-active`` exits non-zero for inactive/failed states but
    still prints the state on stdout, so the exit code is not the signal —
    stdout is.
    """
    if owner == "ubuntu":
        cmd = [
            "sudo",
            "-n",
            "-u",
            "ubuntu",
            f"XDG_RUNTIME_DIR=/run/user/{ubuntu_uid}",
            "systemctl",
            "--user",
            "is-active",
            name,
        ]
    else:
        cmd = ["systemctl", "--user", "is-active", name]

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    except Exception:  # noqa: BLE001 - includes TimeoutExpired, FileNotFoundError, ...
        return "unknown"
    out = (proc.stdout or "").strip()
    return out if out else "unknown"


def collect_services(services: list[dict[str, str]], errors: list[str]) -> list[dict[str, Any]]:
    results = []
    for svc in services:
        name = svc["name"]
        owner = svc["owner"]
        try:
            state = query_service_state(name, owner)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"service {name}: {exc}")
            state = "unknown"
        results.append({"name": name, "owner_user": owner, "state": state})
    return results


# --- walkers ----------------------------------------------------------------


def read_walker_checkpoint(name: str, checkpoint_dir: str) -> dict[str, Any]:
    checkpoint_path = os.path.join(checkpoint_dir, "checkpoint.json")
    with open(checkpoint_path, encoding="utf-8") as fh:
        data = json.load(fh)

    hours: dict[str, Any] = data.get("hours", {}) or {}
    sealed_keys = sorted(k for k, v in hours.items() if isinstance(v, dict) and v.get("status") == "sealed")

    stop_reason = None
    for key in sorted(hours.keys()):
        reason = hours[key].get("stop_reason") if isinstance(hours[key], dict) else None
        if reason:
            stop_reason = reason  # keep the most recent (hours sort ascending by ISO string)

    result: dict[str, Any] = {
        "name": name,
        "sealed": len(sealed_keys),
        "total_hours_in_checkpoint": len(hours),
        "oldest_sealed": sealed_keys[0] if sealed_keys else None,
        "newest_sealed": sealed_keys[-1] if sealed_keys else None,
        "credits_used": data.get("credits_used"),
    }
    if stop_reason:
        result["stop_reason"] = stop_reason
    return result


def collect_walkers(walkers: list[dict[str, str]], errors: list[str]) -> list[dict[str, Any]]:
    results = []
    for walker in walkers:
        name = walker["name"]
        try:
            results.append(read_walker_checkpoint(name, walker["checkpoint_dir"]))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"walker {name}: {exc}")
            results.append({"name": name, "error": str(exc)})
    return results


# --- pre-create credits -------------------------------------------------------


def _looks_key_like(data: dict[str, Any]) -> bool:
    for key in data:
        lowered = str(key).lower()
        if any(hint in lowered for hint in SECRET_LIKE_KEY_HINTS):
            return True
    return False


def read_pre_create_credits(path: str) -> dict[str, Any] | None:
    """Read the plain counter file, refusing to parse anything key-like.

    Tries a direct open first; if that hits a permission error (the file is
    mode 600, owned by ``ubuntu``), falls back to a single read-only
    ``sudo -n cat`` (claude has full sudo on mal-fast-0; this issues no
    write). Returns ``None`` if the file is missing, unreadable, not valid
    JSON, or looks like it might hold a secret.
    """
    raw: str | None = None
    try:
        with open(path, encoding="utf-8") as fh:
            raw = fh.read()
    except FileNotFoundError:
        return None
    except PermissionError:
        try:
            proc = subprocess.run(
                ["sudo", "-n", "cat", path], capture_output=True, text=True, timeout=5.0, check=False
            )
        except Exception:  # noqa: BLE001
            return None
        if proc.returncode != 0:
            return None
        raw = proc.stdout

    if raw is None:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    if _looks_key_like(data):
        return None
    return data


# --- last runner restart -------------------------------------------------------


def read_last_runner_restart(path: str) -> dict[str, Any] | None:
    with open(path, encoding="utf-8") as fh:
        last_line = None
        for line in fh:
            line = line.strip()
            if line:
                last_line = line
    if last_line is None:
        return None
    record = json.loads(last_line)
    return {
        "restart_utc": record.get("restart_utc"),
        "ok": record.get("ok"),
        "head_sha": (record.get("pre") or {}).get("head_sha"),
        "post_lag_ms": (record.get("post") or {}).get("lag_ms"),
    }


# --- local (fast) collection ---------------------------------------------------


def collect_local_status(
    *,
    host: str,
    cgroups: list[dict[str, str]],
    mounts: list[str],
    services: list[dict[str, str]],
    walkers: list[dict[str, str]],
    pre_create_credits_path: str,
    restart_log_path: str,
    unit_sources: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    errors: list[str] = []

    uptime_s = None
    try:
        uptime_s = read_uptime_seconds()
    except Exception as exc:  # noqa: BLE001
        errors.append(f"uptime: {exc}")

    load1 = load5 = load15 = None
    try:
        load1, load5, load15 = read_loadavg()
    except Exception as exc:  # noqa: BLE001
        errors.append(f"loadavg: {exc}")

    cpu_count = None
    try:
        cpu_count = read_cpu_count()
    except Exception as exc:  # noqa: BLE001
        errors.append(f"cpu_count: {exc}")

    memory = collect_memory(cgroups, errors)
    disk = collect_disk(mounts, errors)
    services_out = collect_services(services, errors)
    walkers_out = collect_walkers(walkers, errors)

    pre_create_credits = None
    try:
        pre_create_credits = read_pre_create_credits(pre_create_credits_path)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"pre_create_credits: {exc}")

    last_runner_restart = None
    try:
        last_runner_restart = read_last_runner_restart(restart_log_path)
    except FileNotFoundError:
        pass
    except Exception as exc:  # noqa: BLE001
        errors.append(f"last_runner_restart: {exc}")

    units: list[dict[str, Any]] = []
    units_meta: dict[str, Any] | None = None
    try:
        units, unit_errors, units_meta = collect_units(unit_sources)
        errors.extend(unit_errors)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"units: {exc}")

    return {
        "schema_version": SCHEMA_VERSION,
        "host": host,
        "generated_utc": utc_now_iso(),
        "uptime_s": uptime_s,
        "load1": load1,
        "load5": load5,
        "load15": load15,
        "cpu_count": cpu_count,
        "memory": memory,
        "disk": disk,
        "services": services_out,
        "walkers": walkers_out,
        "pre_create_credits": pre_create_credits,
        "last_runner_restart": last_runner_restart,
        "units": units,
        "units_meta": units_meta,
        "errors": errors,
    }


# --- log-line scrubbing ---------------------------------------------------------

LOG_LINE_MAX = 200
LOG_INPUT_MAX = 4096  # cap BEFORE any regex runs, so scrub time is bounded
REDACTED = "[redacted]"

# Every quantifier below is bounded or applies to a single character class
# that is not preceded by an open-ended prefix, so no rule backtracks
# quadratically; the 4096-char input cap bounds the rest.
_KEY_NAMES = (
    r"(?:api[-_]?key|apikey|client[-_]?secret|secret[-_]?key|private[-_]?key|auth[-_]?token"
    r"|mnemonic|seed[-_]?phrase|seed|secret|passw(?:or)?d|passwd|pwd|pw|access[-_]?token|token|auth|key)"
)
# A quoted value may be unterminated (log lines get cut): take up to 512 chars
# and the closing quote only if it is there.
_KV_VALUE = r"(?:\"[^\"]{0,512}\"?|'[^']{0,512}'?|[^\s\"'&,;}]{1,512})"
_KV_RE = re.compile(r"(?i)" + _KEY_NAMES + r"[\"']?\s*[:=]\s*" + _KV_VALUE)
_KV_PREFIX_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-")
_KV_PREFIX_MAX = 40


def _redact_key_values(text: str) -> str:
    """Redact ``name=value`` / ``name: value`` / JSON ``"name":"value"`` pairs.

    The name may be glued to a prefix (``PGPASSWORD``, ``db-password``,
    ``1password``, ``x_api_key``): up to ``_KV_PREFIX_MAX`` prefix characters
    and one opening quote are redacted with it. Done by walking back from the
    match instead of a regex prefix so the scan stays linear.
    """
    out: list[str] = []
    pos = 0
    for m in _KV_RE.finditer(text):
        start = m.start()
        lo = max(pos, start - _KV_PREFIX_MAX)
        while start > lo and text[start - 1] in _KV_PREFIX_CHARS:
            start -= 1
        if start > pos and text[start - 1] in "\"'":
            start -= 1
        out.append(text[pos:start])
        out.append(REDACTED)
        pos = m.end()
    out.append(text[pos:])
    return "".join(out)


# Order matters: whole-header / whole-URL rules run before the narrower ones.
_SCRUB_BEFORE_KV: list[re.Pattern[str]] = [
    # A seed phrase is many words: everything after the name goes, to end of line.
    re.compile(r"(?i)(?:mnemonic|seed[-_ ]?phrase|seed[-_]?words)[^\n]{0,4096}"),
    # bare dashed UUIDs (Helius keys are UUIDs)
    re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"),
    # headers: Authorization, Cookie / Set-Cookie, X-...-Key
    re.compile(r"(?i)\bauthorization\s*:.*"),
    re.compile(r"(?i)\b(?:set-)?cookie\s*:.*"),
    re.compile(r"(?i)\bx-(?:[a-z]{1,20}-){0,3}key\s*:\s*\S{1,512}"),
    re.compile(r"(?i)\bbearer\s+[^\s\"',;]{1,512}"),
    # scheme://user:pass@host
    re.compile(r"(?i)\b[a-z][a-z0-9+.-]{1,15}://[^\s/@:\"']{1,64}:[^\s/@\"']{1,128}@"),
    # a helius URL: host, path (keys can live in the path) and query, all of it
    re.compile(r"(?i)\b(?:https?|wss?)://[^\s/?\"']{0,100}helius[^\s/?\"']{0,100}[^\s\"']{0,500}"),
    # any URL query value of 20+ chars
    re.compile(r"[?&][^\s=&#\"']{1,64}=[^\s&\"']{20,}"),
    # HELIUS_API_KEY <value> (space-separated)
    re.compile(r"(?i)helius[-_]?(?:api[-_]?)?key\s+\S{1,512}"),
]
_SCRUB_AFTER_KV: list[re.Pattern[str]] = [
    # vendor key prefixes
    re.compile(r"mck_[A-Za-z0-9_-]{1,200}"),
    re.compile(r"sk-[A-Za-z0-9_-]{8,200}"),
    # any 32+ run of base64 / base64url / base58 / hex characters (mints, signatures, keys)
    re.compile(r"[A-Za-z0-9+/_-]{32,}={0,2}"),
]
_CUT_RUN_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/_=%.:-"
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def scrub_log_line(line: str | None) -> str | None:
    """Make one log line safe to put in a status file. Pure, idempotent.

    Order: cap the input to ``LOG_INPUT_MAX`` chars, percent-decode (so
    ``Bearer%20key`` or ``hex%2Fhex`` cannot hide a secret), strip control
    characters, redact, then truncate to ``LOG_LINE_MAX`` characters plus an
    ellipsis. Redaction runs before truncation so a secret cut in half by the
    truncation cannot leave a prefix.
    """
    if line is None:
        return None
    text = str(line)
    capped = len(text) > LOG_INPUT_MAX
    if capped:
        text = text[:LOG_INPUT_MAX]
        # The cap may have cut a secret (or its "name=" label) in half; what is
        # left of it would survive redaction once earlier text shrinks. Drop the
        # trailing run of key-alphabet characters, and at least the last 64.
        run = len(text) - len(text.rstrip(_CUT_RUN_CHARS))
        text = text[: len(text) - max(run, 64)]
    for _ in range(2):  # twice: %2520 style double encoding
        decoded = unquote(text)
        if decoded == text:
            break
        text = decoded
    text = _CONTROL_CHARS.sub(" ", text).strip()
    for pattern in _SCRUB_BEFORE_KV:
        text = pattern.sub(REDACTED, text)
    text = _redact_key_values(text)
    for pattern in _SCRUB_AFTER_KV:
        text = pattern.sub(REDACTED, text)
    if capped:
        text += "…"
    if len(text) > LOG_LINE_MAX:
        text = text[:LOG_LINE_MAX] + "…"
    return text


# --- per-unit "running now" view ------------------------------------------------

CGROUP_ROOT = "/sys/fs/cgroup"
UNITS_DEADLINE_S = 30.0
UNIT_PROPS = "Id,ActiveState,SubState,ActiveEnterTimestamp,ControlGroup"
MEM_SOURCE_CGROUP = "cgroup memory.current (includes page cache)"
MEM_SOURCE_RSS = "process RSS (VmRSS, sum of matched pids)"
UNIT_KEYS = (
    "name", "owner_user", "scope", "kind", "active_state", "sub_state", "since", "uptime_s",
    "memory", "last_log", "last_log_ts", "last_log_age_s",
)

# Where systemd units live on mal-fast-0: no mal-* system units today, the
# claude user manager (status/review timers) and the ubuntu user manager (the
# listeners and walkers; reached with sudo, read-only commands only).
FAST_UNIT_SOURCES: list[dict[str, Any]] = [
    {"scope": "system", "owner_user": "root", "prefix": []},
    {"scope": "user", "owner_user": None, "prefix": []},
    {
        "scope": "user",
        "owner_user": "ubuntu",
        "prefix": ["sudo", "-n", "-u", "ubuntu", f"XDG_RUNTIME_DIR=/run/user/{DEFAULT_UBUNTU_UID}"],
    },
]
# mal-research-0: ssh in as claude; system units plus claude's own user units
# (mal-walker-w1/w2/w3).
RESEARCH_UNIT_SOURCES: list[dict[str, Any]] = FAST_UNIT_SOURCES[:2]


def empty_unit(name: str, scope: str | None, kind: str | None, owner_user: str | None = None) -> dict[str, Any]:
    return {
        "name": name, "owner_user": owner_user, "scope": scope, "kind": kind,
        "active_state": None, "sub_state": None, "since": None, "uptime_s": None,
        "memory": None, "last_log": None, "last_log_ts": None, "last_log_age_s": None,
    }


def normalise_unit(raw: Any) -> dict[str, Any] | None:
    """Force the full key set (null when absent) and scrub the log line.

    Returns None for anything without a usable name (never writes "None").
    """
    if not isinstance(raw, dict) or not isinstance(raw.get("name"), str) or not raw["name"].strip():
        return None
    unit = empty_unit(raw["name"], raw.get("scope"), raw.get("kind"), raw.get("owner_user"))
    for key in UNIT_KEYS:
        if key in raw and key != "name":
            unit[key] = raw[key]
    unit["last_log"] = scrub_log_line(unit["last_log"])
    return unit


def normalise_units(raw_units: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_units, list):
        return []
    return [u for u in (normalise_unit(r) for r in raw_units) if u is not None]


def parse_list_units(text: str) -> list[str]:
    """Names of ``mal-*`` services/timers from ``list-units --no-legend --plain``."""
    names: list[str] = []
    for line in (text or "").splitlines():
        parts = line.replace("●", " ").replace("*", " ").split()
        if not parts:
            continue
        name = parts[0]
        if name.startswith("mal-") and name.endswith((".service", ".timer")) and name not in names:
            names.append(name)
    return names


def parse_systemctl_show(text: str) -> dict[str, dict[str, str]]:
    """``systemctl show a b …`` output (blank-line separated blocks) keyed by Id."""
    result: dict[str, dict[str, str]] = {}
    for block in re.split(r"\n\s*\n", text or ""):
        props: dict[str, str] = {}
        for line in block.splitlines():
            key, sep, value = line.partition("=")
            if sep:
                props[key.strip()] = value.strip()
        if props.get("Id"):
            result[props["Id"]] = props
    return result


def parse_systemd_timestamp(value: str | None) -> datetime | None:
    """``Tue 2026-09-29 04:27:17 UTC`` -> aware datetime; None if empty/non-UTC."""
    parts = (value or "").split()
    if len(parts) == 4:
        parts = parts[1:]
    if len(parts) != 3 or parts[2] not in ("UTC", "GMT"):
        return None
    try:
        return datetime.strptime(f"{parts[0]} {parts[1]}", "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def iso_utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_unit_cgroup_memory(control_group: str | None, cgroup_root: str = CGROUP_ROOT) -> dict[str, Any] | None:
    """``memory.current`` of a unit's cgroup, or None when it cannot be read."""
    if not control_group or not control_group.startswith("/") or ".." in control_group.split("/"):
        return None
    try:
        with open(f"{cgroup_root}{control_group}/memory.current", encoding="utf-8") as fh:
            return {"bytes": int(fh.read().strip()), "source": MEM_SOURCE_CGROUP}
    except (OSError, ValueError):
        return None


_JOURNAL_PREFIX = re.compile(r"^[^\s:\[]+(?:\[\d+\])?:\s?")


def parse_journal_line(line: str | None) -> tuple[str | None, datetime | None]:
    """One ``journalctl -o short-iso`` line -> (message, timestamp)."""
    line = (line or "").strip()
    if not line or line.startswith("--"):  # "-- No entries --"
        return None, None
    parts = line.split(None, 2)
    if len(parts) < 3:
        return None, None
    try:
        ts = datetime.fromisoformat(parts[0])
    except ValueError:
        return None, None
    if ts.tzinfo is None:
        return None, None
    message = _JOURNAL_PREFIX.sub("", parts[2], count=1)
    return message, ts.astimezone(timezone.utc)


class _CountingRunner:
    """Runs argv lists, counts every subprocess, never raises."""

    def __init__(self) -> None:
        self.count = 0

    def __call__(self, cmd: list[str], timeout: float) -> subprocess.CompletedProcess[str] | None:
        self.count += 1
        try:
            return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
        except Exception:  # noqa: BLE001 - TimeoutExpired, FileNotFoundError, ...
            return None


def collect_units(
    sources: list[dict[str, Any]] | None = None,
    *,
    now: datetime | None = None,
    cgroup_root: str = CGROUP_ROOT,
    list_timeout: float = 5.0,
    show_timeout: float = 5.0,
    log_timeout: float = 3.0,
    deadline_s: float = UNITS_DEADLINE_S,
    clock: Any = time.monotonic,
) -> tuple[list[dict[str, Any]], list[str], dict[str, Any]]:
    """Per-unit view for this host. Returns (units, errors, meta).

    Subprocess budget: per source one ``list-units`` + one batched ``show``,
    plus one ``journalctl -n 1`` per unit found. Every failure degrades to
    null fields; nothing here can raise. After ``deadline_s`` seconds the
    remaining ``journalctl`` calls (and any unqueried source) are skipped
    (``last_log`` null) and one error is recorded, so the status file is
    always written inside the systemd ``TimeoutStartSec``.
    """
    sources = FAST_UNIT_SOURCES if sources is None else sources
    now = now or datetime.now(timezone.utc)
    run = _CountingRunner()
    started = clock()
    units: list[dict[str, Any]] = []
    errors: list[str] = []
    skipped = {"logs": 0, "sources": 0}

    def expired() -> bool:
        return clock() - started >= deadline_s

    for src in sources:
        if expired():
            skipped["sources"] += 1
            continue
        scope = src["scope"]
        prefix = list(src.get("prefix") or [])
        owner = src.get("owner_user")
        if owner is None:
            try:
                owner = getpass.getuser()
            except Exception:  # noqa: BLE001
                owner = None
        flag = ["--user"] if scope == "user" else []
        label = f"{scope}/{owner}"
        try:
            proc = run(prefix + ["systemctl"] + flag + ["list-units", "mal-*", "--all", "--no-legend", "--plain"], list_timeout)
            if proc is None or proc.returncode != 0:
                errors.append(f"units list-units {label}: failed")
                continue
            names = parse_list_units(proc.stdout)
            if not names:
                continue
            props_by_id: dict[str, dict[str, str]] = {}
            proc = run(prefix + ["systemctl"] + flag + ["--timestamp=utc", "show", *names, "-p", UNIT_PROPS], show_timeout)
            if proc is None or proc.returncode != 0:
                errors.append(f"units show {label}: failed")
            else:
                props_by_id = parse_systemctl_show(proc.stdout)
            for name in sorted(names):
                units.append(_build_unit(name, scope, owner, props_by_id.get(name), prefix, flag, run, now, cgroup_root, log_timeout, expired, skipped))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"units {label}: {exc}")

    if skipped["logs"] or skipped["sources"]:
        errors.append(
            f"units: {deadline_s:g}s deadline reached; skipped {skipped['logs']} journalctl call(s)"
            f" and {skipped['sources']} source(s)"
        )
    meta = {"wall_ms": round((clock() - started) * 1000), "subprocesses": run.count}
    return units, errors, meta


def _build_unit(
    name: str, scope: str, owner: str | None, props: dict[str, str] | None, prefix: list[str], flag: list[str],
    run: _CountingRunner, now: datetime, cgroup_root: str, log_timeout: float,
    expired: Any = lambda: False, skipped: dict[str, int] | None = None,
) -> dict[str, Any]:
    unit = empty_unit(name, scope, "timer" if name.endswith(".timer") else "service", owner)
    if props:
        unit["active_state"] = props.get("ActiveState") or None
        unit["sub_state"] = props.get("SubState") or None
        since = parse_systemd_timestamp(props.get("ActiveEnterTimestamp"))
        if since is not None:
            unit["since"] = iso_utc(since)
            if unit["active_state"] == "active":
                unit["uptime_s"] = max(0, int((now - since).total_seconds()))
        unit["memory"] = read_unit_cgroup_memory(props.get("ControlGroup"), cgroup_root)
    if expired():
        if skipped is not None:
            skipped["logs"] += 1
        return unit
    proc = run(prefix + ["journalctl"] + flag + ["-u", name, "-n", "1", "-o", "short-iso", "--no-pager"], log_timeout)
    if proc is not None and proc.returncode == 0:
        out_lines = (proc.stdout or "").strip().splitlines()
        message, ts = parse_journal_line(out_lines[-1] if out_lines else "")
        if message is not None and ts is not None:
            unit["last_log"] = scrub_log_line(message)
            unit["last_log_ts"] = iso_utc(ts)
            unit["last_log_age_s"] = max(0, int((now - ts).total_seconds()))
    return unit


# --- remote (Oracle) collection -------------------------------------------------

# Read-only Python, piped to `ssh mal-core-0 python3 -`. No secrets read, no
# writes, no systemctl calls (the Oracle account for Claude cannot run
# systemctl status remotely in this mode, so presence is a /proc scan).
_REMOTE_LIB = r"""
import json, os, time

def load_avg():
    try:
        with open("/proc/loadavg") as f:
            parts = f.read().split()
        return [float(parts[0]), float(parts[1]), float(parts[2])]
    except Exception:
        return None

def meminfo():
    try:
        info = {}
        with open("/proc/meminfo") as f:
            for line in f:
                k, _, v = line.partition(":")
                info[k.strip()] = v.strip()
        total_kb = int(info.get("MemTotal", "0 kB").split()[0])
        avail_kb = int(info.get("MemAvailable", "0 kB").split()[0])
        return {"mem_total_mb": round(total_kb / 1024, 1), "mem_available_mb": round(avail_kb / 1024, 1)}
    except Exception:
        return None

def disk_usage(path):
    try:
        st = os.statvfs(path)
        total = st.f_frsize * st.f_blocks
        free = st.f_frsize * st.f_bavail
        used = total - free
        pct = round(100.0 * used / total, 1) if total else None
        return {"mount": path, "total_gb": round(total / 1e9, 2), "used_gb": round(used / 1e9, 2),
                "free_gb": round(free / 1e9, 2), "pct": pct}
    except Exception:
        return None

def read_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None

def scan_procs(matchers, root="/proc"):
    # {unit: [pid, ...]} for processes whose cmdline matches any needle.
    pids = {name: [] for name in matchers}
    try:
        entries = os.listdir(root)
    except Exception:
        return pids
    for pid in entries:
        if not pid.isdigit():
            continue
        try:
            with open("%s/%s/cmdline" % (root, pid), "rb") as f:
                argv = f.read().decode("utf-8", "replace").split("\x00")
        except Exception:
            continue
        joined = " ".join(argv)
        for name, needles in matchers.items():
            if any(needle in joined for needle in needles):
                pids[name].append(int(pid))
    return pids

def proc_running(matchers, root="/proc"):
    return {name: bool(p) for name, p in scan_procs(matchers, root).items()}

MEM_SOURCE_RSS = "process RSS (VmRSS, sum of matched pids)"

def read_btime(root="/proc"):
    with open("%s/stat" % root) as f:
        for line in f:
            if line.startswith("btime "):
                return int(line.split()[1])
    return None

def start_ticks(root, pid):
    # field 22 of /proc/<pid>/stat; comm (field 2) may contain spaces/parens,
    # so split after the last ")".
    with open("%s/%s/stat" % (root, pid)) as f:
        data = f.read()
    rest = data[data.rindex(")") + 2:].split()
    return int(rest[19])

def vm_rss_bytes(root, pid):
    with open("%s/%s/status" % (root, pid)) as f:
        for line in f:
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) * 1024
    return None

def iso_utc(ts):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))

def tail_last_line(path, nbytes=4096):
    # (last non-empty line, mtime) from the last nbytes of a file; read-only.
    # If the read started mid-file, the first chunk line is a fragment: drop it,
    # and if no newline is left at all return nothing rather than a fragment.
    try:
        with open(path, "rb") as f:
            st = os.fstat(f.fileno())
            offset = max(0, st.st_size - nbytes)
            f.seek(offset)
            data = f.read()
        text = data.decode("utf-8", "replace")
        if offset > 0:
            if "\n" not in text:
                return None, None
            text = text.split("\n", 1)[1]
        lines = [l for l in text.splitlines() if l.strip()]
        return (lines[-1] if lines else None), st.st_mtime
    except Exception:
        return None, None

def unit_views(pids_by_unit, logs, root="/proc", now=None, hz=None, owner_user="ubuntu"):
    now = time.time() if now is None else now
    try:
        hz = hz or os.sysconf("SC_CLK_TCK")
    except Exception:
        hz = 100
    try:
        btime = read_btime(root)
    except Exception:
        btime = None
    views = []
    for name in sorted(pids_by_unit):
        pids = pids_by_unit[name]
        v = {"name": name + ".service", "owner_user": owner_user, "scope": "user", "kind": "service",
             "active_state": "active" if pids else "inactive", "sub_state": "running" if pids else "dead",
             "since": None, "uptime_s": None, "memory": None,
             "last_log": None, "last_log_ts": None, "last_log_age_s": None}
        starts, rss, rss_seen = [], 0, False
        for pid in pids:
            try:
                if btime is not None:
                    starts.append(btime + start_ticks(root, pid) / float(hz))
            except Exception:
                pass
            try:
                b = vm_rss_bytes(root, pid)
                if b is not None:
                    rss += b
                    rss_seen = True
            except Exception:
                pass
        if starts:
            start = min(starts)
            v["since"] = iso_utc(start)
            v["uptime_s"] = max(0, int(now - start))
        if rss_seen:
            v["memory"] = {"bytes": rss, "source": MEM_SOURCE_RSS}
        log_path = logs.get(name)
        if log_path:
            line, mtime = tail_last_line(log_path)
            if line is not None:
                v["last_log"] = line
                v["last_log_ts"] = iso_utc(mtime)
                v["last_log_age_s"] = max(0, int(now - mtime))
        views.append(v)
    return views

MATCHERS = {
    "mal-forward-paper": ["forward-paper.sh", "forward_paper serve", "tools.forward_paper"],
    "mal-observe": ["-m observe --output-dir", "observe --output-dir /var/lib/mal/sealed/jsonl"],
    "mal-trade-tape": ["observe.trade_tape"],
    "mal-attention": ["observe.attention"],
    "mal-funding-graph": ["funding-graph.sh", "funding_graph"],
}

# Unit -> log file (docs/HOSTS.md). mal-forward-paper logs to the journal, which
# this read-only account does not tail, so its last_log stays null.
LOGS = {
    "mal-observe": "/var/lib/mal/logs/observe.log",
    "mal-trade-tape": "/var/lib/mal/logs/trade-tape.log",
    "mal-attention": "/var/lib/mal/logs/attention.log",
    "mal-funding-graph": "/var/lib/mal/logs/funding-graph.log",
}
"""

_REMOTE_MAIN = r"""
_pids = scan_procs(MATCHERS)
out = {
    "load": load_avg(),
    "memory": meminfo(),
    "disk_var_lib_mal": disk_usage("/var/lib/mal"),
    "runner_status": read_json("/var/lib/mal/paper/forward-paper/runner-status.json"),
    "health_latest": read_json("/var/lib/mal/logs/health-latest.json"),
    "processes": {name: bool(p) for name, p in _pids.items()},
    "units": unit_views(_pids, LOGS),
}
print(json.dumps(out))
"""

_REMOTE_SNIPPET = _REMOTE_LIB + _REMOTE_MAIN


def scrub_errors(errors: Any) -> list[str]:
    """Remote-supplied error strings go through the same scrubber as log lines."""
    if not isinstance(errors, list):
        return []
    return [scrub_log_line(str(e)) or "" for e in errors]


def unreachable_status(host: str, generated_utc: str, message: str) -> dict[str, Any]:
    """Payload for a failed SSH collection. The text is scrubbed (ssh stderr is
    attacker-/environment-controlled) and ``units`` is an empty list, not absent."""
    safe = scrub_log_line(message) or "unreachable"
    return {
        "schema_version": SCHEMA_VERSION,
        "host": host,
        "generated_utc": generated_utc,
        "reachable": False,
        "error": safe,
        "errors": [safe],
        "units": [],
    }


def collect_remote_core_status(*, host: str = "mal-core-0", timeout: float = 20.0) -> dict[str, Any]:
    generated_utc = utc_now_iso()
    try:
        proc = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", host, "python3", "-"],
            input=_REMOTE_SNIPPET,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except Exception as exc:  # noqa: BLE001
        return unreachable_status(host, generated_utc, str(exc))

    if proc.returncode != 0:
        return unreachable_status(host, generated_utc, (proc.stderr or "").strip() or f"ssh exit {proc.returncode}")

    try:
        remote = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        return unreachable_status(host, generated_utc, f"bad JSON from remote: {exc}")

    load = remote.get("load") or [None, None, None]
    runner_status = remote.get("runner_status") or {}
    return {
        "schema_version": SCHEMA_VERSION,
        "host": host,
        "generated_utc": generated_utc,
        "reachable": True,
        "load1": load[0] if len(load) > 0 else None,
        "load5": load[1] if len(load) > 1 else None,
        "load15": load[2] if len(load) > 2 else None,
        "memory": remote.get("memory"),
        "disk": [remote["disk_var_lib_mal"]] if remote.get("disk_var_lib_mal") else [],
        "runner_status": {
            "lag_ms": runner_status.get("lag_ms"),
            "stale_cap_ms": runner_status.get("stale_cap_ms"),
            "fail_rate": runner_status.get("fail_rate"),
            "ts": runner_status.get("ts"),
        }
        if runner_status
        else None,
        "health_latest": remote.get("health_latest"),
        "processes": remote.get("processes"),
        "units": normalise_units(remote.get("units")),
        "errors": scrub_errors(remote.get("errors")),
    }


def collect_remote_research_status(*, host: str = "mal-research-0", timeout: float = 45.0) -> dict[str, Any]:
    """One SSH round trip that pipes this file to ``python3 - --units-json``.

    The unit collector that runs on the far side is the same code as the
    local one (systemctl is allowed on mal-research-0, including ``--user``
    units). Never raises; on failure returns ``reachable: false``.
    """
    generated_utc = utc_now_iso()

    def unreachable(message: str) -> dict[str, Any]:
        return unreachable_status(host, generated_utc, message)

    try:
        source = Path(__file__).read_text(encoding="utf-8")
        proc = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", host, "python3", "-", "--units-json", "--unit-sources", "research"],
            input=source, capture_output=True, text=True, timeout=timeout, check=False,
        )
    except Exception as exc:  # noqa: BLE001
        return unreachable(str(exc))
    if proc.returncode != 0:
        return unreachable((proc.stderr or "").strip() or f"ssh exit {proc.returncode}")
    try:
        remote = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        return unreachable(f"bad JSON from remote: {exc}")

    load = remote.get("load") or [None, None, None]
    return {
        "schema_version": SCHEMA_VERSION,
        "host": host,
        "generated_utc": generated_utc,
        "reachable": True,
        "uptime_s": remote.get("uptime_s"),
        "load1": load[0] if len(load) > 0 else None,
        "load5": load[1] if len(load) > 1 else None,
        "load15": load[2] if len(load) > 2 else None,
        "memory": remote.get("memory"),
        "units": normalise_units(remote.get("units")),
        "units_meta": remote.get("units_meta"),
        "errors": scrub_errors(remote.get("errors")),
    }


def units_json_main(sources_name: str) -> int:
    """``--units-json``: print this host's unit view as JSON (remote-pipe mode)."""
    errors: list[str] = []
    sources = RESEARCH_UNIT_SOURCES if sources_name == "research" else FAST_UNIT_SOURCES
    units, unit_errors, meta = collect_units(sources)
    errors.extend(unit_errors)
    out: dict[str, Any] = {"units": units, "units_meta": meta, "errors": errors, "uptime_s": None, "load": None, "memory": None}
    try:
        out["uptime_s"] = read_uptime_seconds()
    except Exception as exc:  # noqa: BLE001
        errors.append(f"uptime: {exc}")
    try:
        out["load"] = list(read_loadavg())
    except Exception as exc:  # noqa: BLE001
        errors.append(f"loadavg: {exc}")
    try:
        total, avail = read_meminfo()
        out["memory"] = {"mem_total_mb": total, "mem_available_mb": avail}
    except Exception as exc:  # noqa: BLE001
        errors.append(f"meminfo: {exc}")
    print(json.dumps(out))
    return 0


# --- atomic write + CLI ---------------------------------------------------------


def write_status_atomic(out_dir: str, host: str, status: dict[str, Any]) -> str:
    os.makedirs(out_dir, exist_ok=True)
    final_path = os.path.join(out_dir, f"{host}.json")
    fd, tmp_path = tempfile.mkstemp(prefix=f".{host}.", suffix=".json.tmp", dir=out_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(status, fh, indent=2)
            fh.write("\n")
        os.replace(tmp_path, final_path)
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
    return final_path


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", default=None, help="Directory to write <host>.json into (required unless --units-json).")
    parser.add_argument("--remote", choices=["core", "research"], default=None, help="Collect mal-core-0 or mal-research-0 over SSH instead of the local host.")
    parser.add_argument("--units-json", action="store_true", help="Print this host's unit view as JSON and exit (used by --remote research).")
    parser.add_argument("--unit-sources", choices=["fast", "research"], default="fast", help="Which systemd managers --units-json queries.")
    parser.add_argument("--host", default=None, help="Override the host label (default: local hostname, or mal-core-0 in --remote core).")
    parser.add_argument("--pre-create-credits-path", default=DEFAULT_PRE_CREATE_CREDITS_PATH)
    parser.add_argument("--restart-log-path", default=DEFAULT_RESTART_LOG_PATH)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    if args.units_json:
        return units_json_main(args.unit_sources)
    if not args.out_dir:
        parser.error("--out-dir is required")

    if args.remote == "core":
        host = args.host or "mal-core-0"
        status = collect_remote_core_status(host=host)
    elif args.remote == "research":
        host = args.host or "mal-research-0"
        status = collect_remote_research_status(host=host)
    else:
        host = args.host or socket.gethostname()
        status = collect_local_status(
            host=host,
            cgroups=FAST_CGROUPS,
            mounts=FAST_MOUNTS,
            services=FAST_SERVICES,
            walkers=FAST_WALKERS,
            pre_create_credits_path=args.pre_create_credits_path,
            restart_log_path=args.restart_log_path,
        )

    path = write_status_atomic(args.out_dir, host, status)
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
