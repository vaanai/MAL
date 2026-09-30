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

Every section is independently try/excepted. Failures are recorded in the
top-level ``errors`` list and never stop the rest of the collection.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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
        "errors": errors,
    }


# --- remote (Oracle) collection -------------------------------------------------

# Read-only Python, piped to `ssh mal-core-0 python3 -`. No secrets read, no
# writes, no systemctl calls (the Oracle account for Claude cannot run
# systemctl status remotely in this mode, so presence is a /proc scan).
_REMOTE_SNIPPET = r"""
import json, os

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

def proc_running(matchers):
    found = {name: False for name in matchers}
    try:
        for pid in os.listdir("/proc"):
            if not pid.isdigit():
                continue
            try:
                with open("/proc/%s/cmdline" % pid, "rb") as f:
                    argv = f.read().decode("utf-8", "replace").split("\x00")
            except Exception:
                continue
            joined = " ".join(argv)
            for name, needles in matchers.items():
                if found[name]:
                    continue
                if any(needle in joined for needle in needles):
                    found[name] = True
    except Exception:
        pass
    return found

MATCHERS = {
    "mal-forward-paper": ["forward-paper.sh", "forward_paper serve", "tools.forward_paper"],
    "mal-observe": ["-m observe --output-dir", "observe --output-dir /var/lib/mal/sealed/jsonl"],
    "mal-trade-tape": ["observe.trade_tape"],
    "mal-attention": ["observe.attention"],
    "mal-funding-graph": ["funding-graph.sh", "funding_graph"],
}

out = {
    "load": load_avg(),
    "memory": meminfo(),
    "disk_var_lib_mal": disk_usage("/var/lib/mal"),
    "runner_status": read_json("/var/lib/mal/paper/forward-paper/runner-status.json"),
    "health_latest": read_json("/var/lib/mal/logs/health-latest.json"),
    "processes": proc_running(MATCHERS),
}
print(json.dumps(out))
"""


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
        return {
            "schema_version": SCHEMA_VERSION,
            "host": host,
            "generated_utc": generated_utc,
            "reachable": False,
            "error": str(exc),
            "errors": [str(exc)],
        }

    if proc.returncode != 0:
        return {
            "schema_version": SCHEMA_VERSION,
            "host": host,
            "generated_utc": generated_utc,
            "reachable": False,
            "error": (proc.stderr or "").strip() or f"ssh exit {proc.returncode}",
            "errors": [(proc.stderr or "").strip() or f"ssh exit {proc.returncode}"],
        }

    try:
        remote = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        return {
            "schema_version": SCHEMA_VERSION,
            "host": host,
            "generated_utc": generated_utc,
            "reachable": False,
            "error": f"bad JSON from remote: {exc}",
            "errors": [f"bad JSON from remote: {exc}"],
        }

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
        "errors": [],
    }


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
    parser.add_argument("--out-dir", required=True, help="Directory to write <host>.json into.")
    parser.add_argument("--remote", choices=["core"], default=None, help="Collect mal-core-0 over SSH instead of the local host.")
    parser.add_argument("--host", default=None, help="Override the host label (default: local hostname, or mal-core-0 in --remote core).")
    parser.add_argument("--pre-create-credits-path", default=DEFAULT_PRE_CREATE_CREDITS_PATH)
    parser.add_argument("--restart-log-path", default=DEFAULT_RESTART_LOG_PATH)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)

    if args.remote == "core":
        host = args.host or "mal-core-0"
        status = collect_remote_core_status(host=host)
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
