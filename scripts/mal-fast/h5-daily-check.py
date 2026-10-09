#!/usr/bin/env python3
"""Daily health check for the H5 live executor (DEC-024), for the manager's 12:17Z job. Replaces the decommissioned-probe check.
Its engine also runs every few minutes as the H5 watchdog (h5-watch.py), which posts the alerts to Discord.

    python3 -I /home/claude/MAL/scripts/mal-fast/h5-daily-check.py --funded-sol 0.25 --public-rpc --window-hours 24 \\
        --shadow-dir /home/claude/data/h5-shadow --baseline /home/claude/data/h5-daily/probe-state.baseline.json

Read-only, no key: it never opens /etc/mal-probe, never loads a wallet, never sends anything. Files in the 0700 state dirs are read
through FIXED paths only (PRIV_STAT / PRIV_READ below): lstat first (a symlink, a hard-linked file, a non-regular file or one over 8 MB is
refused), then O_NOFOLLOW (direct) or `sudo -n /usr/bin/dd iflag=nofollow status=none if=<path>` (never cat, which follows symlinks).
`--print-sudoers` prints the exact sudoers lines those privileged calls need, and a test keeps them in step with the code. File content
is never echoed into an alert. A failing `sudo -n` is an ALERT (sudo_unavailable / sudo_failed), and so is a failing `systemctl`
(systemctl_failed): neither is ever read as "absent" or "not installed". Prints INFO / OK / ALERT lines and exits 1 on any ALERT. The RPC URL
(HELIUS_API_KEY from the environment or the paper env file, or --public-rpc) is never printed.

  ALLOWED now   the H5 unit mal-h5-executor (active or enabled), /var/lib/mal-live/h5, the pinned tree, /etc/mal-h5/LIVE_OK, the watchdog.
  STILL ALERTS  any mal-probe-executor* unit active or enabled; the probe unit holding ANY drop-in or LoadCredential (a started probe must
                have no key); /var/lib/mal-live/state-live.json OR state-live-dec020.json changed (sha256 against a baseline, plus attempts
                <= 62, realized -0.210755 SOL, nothing open for the first). The old "/var/lib/mal-live/STOP must exist" alert is gone: the
                executor treats that file as a wallet-wide STOP, so it must be absent for H5 to buy.
  UNMANAGED     live state with open or pending positions while the unit is not active with --live (alert h5_positions_unmanaged).
  IDLE CANARY   stale shadow feed (newest hourly file older than 10 min); a shadow directory created AFTER the unit started (the unit's
                bind points at nothing or at the old directory); LIVE_OK present but the unit not active and enabled, the running ExecStart
                without --live, or STOP present (the H5 one or the wallet-wide one); LIVE_OK older than 6 h with no buy/skip/decision row.
  WATCHDOG      the watch timer not enabled and active; mal-h5-watch.service Result not success; its state `ts` older than 15 min; its
                installed unit files differing from the pinned copies or any drop-in on them.
  STOPS/ALERTS  from the live ledger (window = --window-hours, default 6): executor budget stops (total_loss_stop, daily_loss_stop,
                max_trades_day, max_attempts, max_days, end_instant, balance_floor), every `alert` row, s0_recv_late / s0_unverifiable (3 or
                more), and triggers refused for the two #477 fields (the shadow job is not at #477 head 3dbe1de or later). The sealed stub
                (bad_intent:suppressed) is expected and never an alert.
  UNIT FILES    the installed unit files equal the pinned copies; the drop-in list comes from systemd (DropInPaths) and may hold only live.conf
                (equal to the pinned drop-in) and 10-shadow-feed.conf (passing check-h5-unit.py --shadow-feed).
  WALLET        balance against funded + realized - cost of open positions - buys in flight, with a tolerance for rent and fees.
"""
from __future__ import annotations

import argparse
import grp
import hashlib
import importlib.util
import json
import os
import pwd
import re
import stat as stat_mod
import subprocess
import sys
import time
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Callable, NamedTuple

WALLET = "5n95HyhZqjZNkjdp44QGJoAqk4ZFjDgMKuUzWcQqSugk"  # the DEC-019 probe wallet; public (DEC-024)
PROBE_STATE = "/var/lib/mal-live/state-live.json"
PROBE_STATE_DEC020 = "/var/lib/mal-live/state-live-dec020.json"
WALLET_STOP = "/var/lib/mal-live/STOP"  # the probe's file names; the executor (4f05e30) honours both wallet-wide, next to its own in the H5 dir
WALLET_HALT = "/var/lib/mal-live/HALT"
PROBE_MAX_ATTEMPTS = 62
PROBE_REALIZED_SOL = -0.210755
PROBE_UNIT = "mal-probe-executor"
H5_UNIT = "mal-h5-executor"
WATCH_SERVICE = "mal-h5-watch.service"
WATCH_TIMER = "mal-h5-watch.timer"
H5_DIR = "/var/lib/mal-live/h5"
H5_ETC = "/etc/mal-h5"  # root:root 0755; holds LIVE_OK, which Helm creates (the executor cannot)
LIVE_OK = f"{H5_ETC}/LIVE_OK"
TIER_FILE = f"{H5_ETC}/TIER"  # root:root 0644, content exactly T0, T1 or T2; Helm edits it, the executor only reads it (missing or invalid means T0)
TIERS = ("T0", "T1", "T2")
TIER_UNAPPLIED_S = 900  # a valid tier file older than this that the executor has not followed
PINNED = "/usr/local/lib/mal-h5-exec/current"
UNIT_FILE = f"/etc/systemd/system/{H5_UNIT}.service"
DROPIN_DIR = f"/etc/systemd/system/{H5_UNIT}.service.d"
DROPIN_LIVE = f"{DROPIN_DIR}/live.conf"
DROPIN_FEED = f"{DROPIN_DIR}/10-shadow-feed.conf"
WATCH_STATE = "/var/lib/mal-h5-watch/state.json"
WATCH_FILES = (("/etc/systemd/system/mal-h5-watch.service", f"{PINNED}/mal-h5-watch.service"),
               ("/etc/systemd/system/mal-h5-watch.timer", f"{PINNED}/mal-h5-watch.timer"))
PUBLIC_RPC = "https://api.mainnet-beta.solana.com"
RENT_TOLERANCE = 2_100_000  # per open or pending position (token account rent, refunded at close)
BASE_TOLERANCE = 5_000_000  # fees in flight, dust
FEED_STALE_S = 600  # the newest hourly shadow file must have been written to within 10 minutes
BIND_SLACK_S = 5  # a shadow directory born more than this after the unit started is not what the unit's bind shows
WATCH_STATE_STALE_S = 15 * 60
IDLE_LEDGER_S = 6 * 3600  # LIVE_OK older than this and no buy/skip/decision row newer than this: the canary is idle
IDLE_KINDS = ("buy", "skip", "decision")
MAX_READ = 8 * 1024 * 1024  # a bigger file is reported (unsafe_path), never read: the watchdog has 256 MB
LEDGER_TAIL = 4_000_000
HOURLY_RE = r"^h5-shadow-\d{4}-\d{2}-\d{2}T\d{2}\.jsonl$"
ACTIVE_STATES = {"active", "activating", "reloading", "deactivating"}
ENABLED_STATES = {"enabled", "enabled-runtime", "linked", "linked-runtime", "alias"}
# Live halts the executor latches (tools/h5_executor.py _latch names at 9c5618b); cleared only by --clear-halt. Unknown names are still shown.
HALT_MEANING = {
    "boost_median_lt_335": "UTC-day median of the BOOST last-slice time below 335 s",
    "boost_structure_lt_300_x3": "BOOST last slice below 300 s on three pools",
    "boost_median_lt_337_twice": "day median below 337 s on two days",
    "boost_before_sell_gt_15pct": "BOOST ended before our sell on more than 15% of sells",
    "stuck_position": "a position not sold by its deadline",
    "out_of_rule_entry": "a buy landed outside the rule's entry window",
    "landing_median_gt_3s": "median trigger-to-landing above 3 s",
    "late_sells_gt_5pct": "more than 5% of landed sells late",
}
# Executor budget stops, written as `skip` rows (reason=...) for every trigger refused while they are in force (DEC-024 section 8: "stop fired").
BUDGET_STOPS = {
    "total_loss_stop": "total realized loss stop reached",
    "daily_loss_stop": "daily realized loss stop reached (until 00:00Z)",
    "max_trades_day": "daily trade cap reached (until 00:00Z)",
    "max_attempts": "attempt cap reached",
    "max_days": "duration cap reached",
    "end_instant": "the configured end_ms has passed",
    "balance_floor": "the wallet is below the balance floor",
}
ALERT_MEANING = {
    "tier_file_problem": "the TIER file failed the executor's checks (not a regular root:root 0644 file, or not exactly T0/T1/T2); it runs T0 meanwhile",
    "tier_step_down_due": "a halt or loss stop above T0: the manager asks and Helm steps the tier down (runbook \"Step up / step down a tier\"); the executor never edits the file",
    "s0_anchor_refusals": "several triggers refused on the s0 anchor in a short time",
}
NAME_RE = r"^[A-Za-z0-9_:.\-]{1,60}$"  # only names that look like names are ever printed from a file
# Trigger refusals the executor ledgers as `skip` rows. Printing the reason NAMES is fine; they are fixed strings.
S0_REFUSALS = ("s0_recv_late", "s0_unverifiable", "s0_before_history")
NEW_FIELD_REFUSALS = ("bad_intent:s0_minus_announced_slots", "bad_intent:base_breaks_unresolved_settled")  # the two fields the executor gates on (#477 head 3dbe1de)
EXPECTED_REFUSALS = ("bad_intent:suppressed",)  # #477's sealed stub from 2026-10-16T01Z: a refusal by design, never an alert
S0_REFUSAL_ALERT_N = 3
SCHEMA_REFUSAL_ALERT_N = 5

# The ONLY privileged calls this script makes: stat of, and dd of, these fixed paths (the files live in 0700 directories owned by mal-live
# or root). `--print-sudoers` prints one exact sudoers line per call; there is no wildcard anywhere, so a narrowed sudoers rule never
# lets the caller read or write anything else. The reads are whole-file (size-capped), never `skip=`, because an argument that varies cannot be pinned.
PRIV_STAT = (WALLET_STOP, WALLET_HALT, PROBE_STATE, PROBE_STATE_DEC020, H5_DIR, f"{H5_DIR}/STOP", f"{H5_DIR}/HALT", f"{H5_DIR}/LIVE_OK",
             f"{H5_DIR}/live/state-live.json", f"{H5_DIR}/live/h5-counters.json", f"{H5_DIR}/live/h5-ledger.jsonl", WATCH_STATE)
PRIV_READ = (PROBE_STATE, PROBE_STATE_DEC020, f"{H5_DIR}/live/state-live.json", f"{H5_DIR}/live/h5-counters.json",
             f"{H5_DIR}/live/h5-ledger.jsonl", WATCH_STATE)
STAT_FORMAT = "%F|%h|%U|%G|%a|%Y|%s"


def stat_argv(path: str) -> tuple[str, ...]:
    return ("/usr/bin/stat", "-c", STAT_FORMAT, path)


def dd_argv(path: str) -> tuple[str, ...]:
    return ("/usr/bin/dd", "iflag=nofollow", "status=none", f"if={path}")


def sudoers_text(user: str = "claude") -> str:
    """The exact sudoers lines for the privileged calls above. In sudoers, `,` `:` `=` and `\\` in a command's arguments are escaped with a backslash."""
    def esc(argv: tuple[str, ...]) -> str:
        return " ".join(re.sub(r"([,:=\\])", r"\\\1", a) for a in argv)

    cmds = ["/usr/bin/true", *(esc(stat_argv(p)) for p in PRIV_STAT), *(esc(dd_argv(p)) for p in PRIV_READ)]
    body = ", \\\n    ".join(cmds)
    return f"Cmnd_Alias MAL_H5_CHECK = {body}\n{user} ALL=(root) NOPASSWD: MAL_H5_CHECK\n"


class SudoError(Exception):
    """A privileged read failed. Never to be confused with the file being absent."""


class Unsafe(Exception):
    """A fixed path is not what it must be (symlink, hard link, not a regular file, too large). The message names the path, never content."""


class Entry(NamedTuple):
    kind: str  # file | dir | link | other
    nlink: int
    owner: str
    group: str
    mode: int
    mtime: float
    size: int


def _kind_of(label: str) -> str:
    label = label.strip()
    return "file" if label.startswith("regular") else "dir" if label == "directory" else "link" if label == "symbolic link" else "other"


class Host:
    """Everything that touches the machine. Tests replace it."""

    priv_stat: tuple[str, ...] = PRIV_STAT
    priv_read: tuple[str, ...] = PRIV_READ

    def _run(self, *argv: str) -> subprocess.CompletedProcess:
        return subprocess.run(list(argv), capture_output=True, timeout=30)

    def _sudo(self, *argv: str) -> subprocess.CompletedProcess:
        return self._run(*(argv if os.geteuid() == 0 else ("sudo", "-n", *argv)))  # root has no use for sudo (and a sandboxed unit cannot run it)

    def sudo_ok(self) -> bool:
        try:
            return self._sudo("/usr/bin/true").returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False

    def lstat(self, path: str) -> Entry | None:
        """None only if the path does not exist. A privilege failure raises SudoError."""
        try:
            st = os.lstat(path)
            m = st.st_mode
            kind = "link" if stat_mod.S_ISLNK(m) else "file" if stat_mod.S_ISREG(m) else "dir" if stat_mod.S_ISDIR(m) else "other"
            try:
                owner, group = pwd.getpwuid(st.st_uid).pw_name, grp.getgrgid(st.st_gid).gr_name
            except KeyError:
                owner, group = str(st.st_uid), str(st.st_gid)
            return Entry(kind, st.st_nlink, owner, group, stat_mod.S_IMODE(m), st.st_mtime, st.st_size)
        except FileNotFoundError:
            return None
        except PermissionError:
            pass
        if path not in self.priv_stat:
            raise SudoError(f"stat {path}: not a path the privileged calls are allowed for")
        try:
            r = self._sudo(*stat_argv(path))
        except (OSError, subprocess.SubprocessError) as exc:
            raise SudoError(f"stat {path}: {type(exc).__name__}") from None
        err = r.stderr.decode(errors="replace")
        if r.returncode == 0:
            f = r.stdout.decode().strip().split("|")
            try:
                return Entry(_kind_of(f[0]), int(f[1]), f[2], f[3], int(f[4], 8), float(f[5]), int(f[6]))
            except (IndexError, ValueError):
                raise SudoError(f"stat {path}: unparseable output") from None
        if "No such file or directory" in err and not err.startswith("sudo:"):
            return None
        raise SudoError(f"stat {path}: privileged stat failed")

    def exists(self, path: str) -> bool:
        return self.lstat(path) is not None

    def islink(self, path: str) -> bool:
        e = self.lstat(path)
        return e is not None and e.kind == "link"

    def is_regular(self, path: str) -> bool:
        e = self.lstat(path)
        return e is not None and e.kind == "file"

    def stat(self, path: str) -> str | None:
        """owner:group:mode of a path, or None if absent."""
        e = self.lstat(path)
        return None if e is None else f"{e.owner}:{e.group}:{e.mode:o}"

    def mtime(self, path: str) -> float | None:
        e = self.lstat(path)
        return None if e is None else e.mtime

    def read(self, path: str, tail: int | None = None) -> bytes | None:
        """File bytes (the last `tail` bytes if given), or None if the path does not exist. Refuses symlinks, hard-linked files, non-regular
        files and files over MAX_READ before reading; reads with O_NOFOLLOW, or `dd iflag=nofollow` through sudo. Never cat."""
        e = self.lstat(path)
        if e is None:
            return None
        if e.kind != "file":
            raise Unsafe(f"{path} is not a regular file ({e.kind})")
        if e.nlink > 1:
            raise Unsafe(f"{path} has {e.nlink} hard links")
        if e.size > MAX_READ:
            raise Unsafe(f"{path} is larger than {MAX_READ} bytes ({e.size})")
        skip = max(0, e.size - tail) if tail else 0
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        except FileNotFoundError:
            return None
        except PermissionError:
            fd = -1
        if fd >= 0:
            with os.fdopen(fd, "rb") as fh:
                st = os.fstat(fh.fileno())
                if not stat_mod.S_ISREG(st.st_mode) or st.st_nlink > 1:
                    raise Unsafe(f"{path} changed under us")
                fh.seek(skip)
                return fh.read(MAX_READ + 1)[:MAX_READ]
        if path not in self.priv_read:
            raise SudoError(f"read {path}: not a path the privileged calls are allowed for")
        try:
            r = self._sudo(*dd_argv(path))
        except (OSError, subprocess.SubprocessError) as exc:
            raise SudoError(f"read {path}: {type(exc).__name__}") from None
        if r.returncode == 0:
            return r.stdout[skip:]  # the tail is cut here: a `skip=` operand would have to vary, and a varying sudo argument cannot be pinned
        raise SudoError(f"read {path}: privileged read failed")

    def newest_hourly(self, directory: str) -> tuple[str, float] | None:
        try:
            names = [n for n in os.listdir(directory) if re.match(HOURLY_RE, n)]
        except OSError:
            return None
        if not names:
            return None
        newest = max(names)
        return newest, os.lstat(os.path.join(directory, newest)).st_mtime

    def birth(self, path: str) -> float | None:
        """Birth time of a path (statx btime via `stat -c %W`), or None if the file system does not record it."""
        try:
            r = subprocess.run(["/usr/bin/stat", "-c", "%W", path], capture_output=True, text=True, timeout=15)
            v = int(r.stdout.strip()) if r.returncode == 0 else 0
        except (OSError, subprocess.SubprocessError, ValueError):
            return None
        return float(v) if v > 0 else None

    def uptime(self) -> float | None:
        try:
            return float(Path("/proc/uptime").read_text().split()[0])
        except (OSError, ValueError, IndexError):
            return None

    def systemctl(self, *argv: str) -> tuple[int, str]:
        try:
            r = subprocess.run(["/usr/bin/systemctl", *argv], capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.SubprocessError):
            return 127, ""
        return r.returncode, r.stdout

    def balance(self, wallet: str, env_file: str, public: bool = False) -> int:
        key = ""
        if not public:
            key = (os.environ.get("HELIUS_API_KEY") or "").strip()
            if not key and Path(env_file).exists():
                for line in Path(env_file).read_text().splitlines():
                    line = line.strip().removeprefix("export ")
                    if line.startswith("HELIUS_API_KEY="):
                        key = line.split("=", 1)[1].strip().strip("\"'")
            if not key:
                raise RuntimeError("no HELIUS_API_KEY")
        url = PUBLIC_RPC if public else f"https://mainnet.helius-rpc.com/?api-key={key}"
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "getBalance", "params": [wallet, {"commitment": "confirmed"}]}).encode()
        req = urllib.request.Request(url, body, {"Content-Type": "application/json"})
        return int(json.load(urllib.request.urlopen(req, timeout=20))["result"]["value"])  # type: ignore[no-any-return]


class Report:
    def __init__(self, out: Callable[[str], None] = print):
        self.out, self.alerts, self.alert_list, self.facts = out, 0, [], {}

    def info(self, msg: str) -> None:
        self.out(f"INFO  {msg}")

    def ok(self, msg: str) -> None:
        self.out(f"OK    {msg}")

    def alert(self, name: str, msg: str) -> None:
        self.alerts += 1
        self.alert_list.append((name, msg))
        self.out(f"ALERT {name}: {msg}")


class UnitInfo(NamedTuple):
    installed: bool
    active_state: str
    unit_file_state: str
    running_live: bool  # active, and the running ExecStart carries --live
    start_monotonic_us: int | None = None  # ActiveEnterTimestampMonotonic: when the current run began, in microseconds since boot


NO_UNIT = UnitInfo(False, "not-installed", "", False)


def sysctl_show(host: Host, rep: Report, unit: str, *props: str) -> dict[str, str] | None:
    """`systemctl show <unit> -p ...` as a dict. A failing systemctl (non-zero exit, or none of the requested keys in the output) is an
    ALERT and None: it is never read as "the unit is not installed" (a not-found unit still answers with LoadState=not-found)."""
    rc, out = host.systemctl("show", unit, "-p", ",".join(props), "--no-pager")
    d = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
    if rc != 0 or not any(p in d for p in props):
        rep.alert("systemctl_failed", f"`systemctl show {unit}` failed (exit {rc}, {len(out)} bytes): the checks that need systemd cannot pass; this is not 'not installed'")
        return None
    return d


def _json(host: Host, path: str, tail: int | None = None) -> dict | None:
    raw = host.read(path, tail) if tail else host.read(path)
    if raw is None:
        return None
    obj = json.loads(raw)
    return obj if isinstance(obj, dict) else None


def load_unit_checker(dir_: Path):
    spec = importlib.util.spec_from_file_location("check_h5_unit", dir_ / "check-h5-unit.py")
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def check_probe_units(host: Host, rep: Report) -> None:
    bad = []
    p = sysctl_show(host, rep, f"{PROBE_UNIT}.service", "LoadState", "ActiveState", "UnitFileState", "DropInPaths", "LoadCredential")
    if p is not None:
        if p.get("ActiveState") in ACTIVE_STATES:
            bad.append(f"{PROBE_UNIT}.service is {p.get('ActiveState')}")
        if p.get("UnitFileState") in ENABLED_STATES:
            bad.append(f"{PROBE_UNIT}.service is {p.get('UnitFileState')}")
        if p.get("DropInPaths", "").strip():
            rep.alert("probe_live_dropin", f"the probe unit has drop-in(s) {p['DropInPaths'].strip()}: move them to /root/disabled (runbook Step 1), so a start of the probe has no key")
        if p.get("LoadCredential", "").strip():
            rep.alert("probe_has_key", "the probe unit would be handed a credential (LoadCredential is set): a start of it would arm the wallet key")
    rc, out = host.systemctl("list-unit-files", "--no-legend", "--no-pager", f"{PROBE_UNIT}*")
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1] in ENABLED_STATES and f"{parts[0]} is {parts[1]}" not in bad:
            bad.append(f"{parts[0]} is {parts[1]}")
    rc, out = host.systemctl("list-units", "--all", "--plain", "--no-legend", "--no-pager", f"{PROBE_UNIT}*")
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[2] in ACTIVE_STATES and f"{parts[0]} is {parts[2]}" not in bad:
            bad.append(f"{parts[0]} is {parts[2]}")
    if bad:
        rep.alert("probe_unit", "; ".join(bad) + " (two processes must never share the key; stop and disable it, docs/runbooks/h5-executor.md step 1)")
    elif p is not None:
        rep.ok("no mal-probe-executor unit is active or enabled, and it holds no drop-in or credential" if not p.get("DropInPaths", "").strip() and not p.get("LoadCredential", "").strip()
               else "no mal-probe-executor unit is active or enabled")


def _digest(host: Host, path: str) -> str:
    raw = host.read(path)
    return "absent" if raw is None else hashlib.sha256(raw).hexdigest()


def check_probe_state(host: Host, rep: Report, baseline_path: Path, write_baseline: bool, expect_sha: str | None = None,
                      expect_dec020: str | None = None) -> None:
    raw = host.read(PROBE_STATE)
    if raw is None:
        rep.alert("probe_state", f"{PROBE_STATE} is missing")
        return
    digest = hashlib.sha256(raw).hexdigest()
    dec020 = _digest(host, PROBE_STATE_DEC020)  # "absent" is a value: the file appearing later is a change
    st = json.loads(raw)
    problems = []
    if st.get("attempts", 0) > PROBE_MAX_ATTEMPTS:
        problems.append(f"attempts {st.get('attempts')} > {PROBE_MAX_ATTEMPTS}")
    if round(st.get("realized_lamports", 0) / 1e9, 6) != PROBE_REALIZED_SOL:
        problems.append(f"realized {st.get('realized_lamports', 0) / 1e9:.6f} SOL != {PROBE_REALIZED_SOL}")
    if st.get("open") or st.get("pending"):
        problems.append("open or pending positions")
    if write_baseline:
        if expect_sha and expect_sha.lower() != digest:
            problems.append("--expect-sha256 does not match state-live.json now: the baseline was NOT written (it changed since Helm's Step 1)")
        elif expect_dec020 and expect_dec020.lower() != dec020:
            problems.append("--expect-dec020-sha256 does not match state-live-dec020.json now: the baseline was NOT written (it changed since Helm's Step 1)")
        else:
            baseline_path.parent.mkdir(parents=True, exist_ok=True)
            baseline_path.write_text(json.dumps({"sha256": digest, "dec020_sha256": dec020}) + "\n")
            rep.info(f"probe state baseline written (state-live {digest[:12]}, dec020 {dec020[:12]})")
    elif not baseline_path.exists():
        problems.append(f"no baseline at {baseline_path}: run once with --write-baseline, or changes cannot be detected")
    else:
        base = json.loads(baseline_path.read_text())
        if base.get("sha256") != digest:
            problems.append("sha256 differs from the baseline: the probe's state file changed")
        if "dec020_sha256" not in base:
            problems.append("the baseline has no DEC-020 hash: rewrite it with --write-baseline")
        elif base["dec020_sha256"] != dec020:
            problems.append("state-live-dec020.json differs from the baseline (changed, appeared or disappeared): the probe's DEC-020 profile ran")
    if problems:
        rep.alert("probe_state", "; ".join(problems))
    else:
        rep.ok(f"probe state unchanged (attempts {st.get('attempts')}, realized -0.210755 SOL; DEC-020 file {'absent' if dec020 == 'absent' else 'unchanged'})")


def check_h5_unit(host: Host, rep: Report, checker) -> UnitInfo:
    """Alerts if what is installed differs from the pinned copies. Returns what the other checks need."""
    props = sysctl_show(host, rep, f"{H5_UNIT}.service", "LoadState", "ActiveState", "SubState", "UnitFileState", "NRestarts", "Result", "FragmentPath",
                        "ActiveEnterTimestampMonotonic")
    if props is None:
        return NO_UNIT
    if props.get("LoadState") in (None, "not-found"):
        rep.info(f"{H5_UNIT} is not installed")
        return NO_UNIT
    rep.info(f"{H5_UNIT} active={props.get('ActiveState')}/{props.get('SubState')} enabled={props.get('UnitFileState')} "
             f"restarts={props.get('NRestarts')} result={props.get('Result')}")
    rep.facts.update(unit=H5_UNIT, active=props.get("ActiveState"), enabled=props.get("UnitFileState"))
    if props.get("ActiveState") == "failed":
        rep.alert("h5_unit_failed", f"result={props.get('Result')}; see journalctl -u {H5_UNIT}")
    problems = []
    if props.get("FragmentPath") != UNIT_FILE:
        problems.append(f"FragmentPath is {props.get('FragmentPath')!r}, expected {UNIT_FILE}")
    base = host.read(UNIT_FILE)
    pinned_base = host.read(f"{PINNED}/mal-h5-executor.service")
    if pinned_base is None:
        problems.append(f"{PINNED}/mal-h5-executor.service is missing (no pinned install)")
    elif base != pinned_base:
        problems.append("installed base unit differs from the pinned copy")
    # the drop-in list is what systemd applies (DropInPaths covers prefix and top-level .d directories and /run too), not a directory listing
    rc, out = host.systemctl("show", f"{H5_UNIT}.service", "-p", "DropInPaths", "--value", "--no-pager")
    if rc != 0:
        problems.append("`systemctl show -p DropInPaths` failed")
    for p in out.split():
        if p == DROPIN_LIVE:
            want = host.read(f"{PINNED}/mal-h5-executor-live-pinned.conf")
            data = host.read(p)
            if want is None or data != want:
                problems.append("live.conf differs from the pinned live drop-in")
            elif checker.problems(data, "dropin"):
                problems.append("live.conf failed the drop-in allowlist")
        elif p == DROPIN_FEED:
            if checker.problems(host.read(p) or b"", "shadow-feed"):
                problems.append("10-shadow-feed.conf failed the shadow-feed check (run check-h5-unit.py --shadow-feed on it)")
        else:
            problems.append(f"unexpected drop-in {p}")
    running_live = False
    if props.get("ActiveState") in ACTIVE_STATES:
        rc, es = host.systemctl("show", f"{H5_UNIT}.service", "-p", "ExecStart", "--value", "--no-pager")
        if "/usr/local/lib/mal-h5-exec/" not in es or "fast-forward" in es:
            problems.append("running ExecStart is not the pinned launcher")
        running_live = props.get("ActiveState") == "active" and "--live" in es
    if problems:
        rep.alert("h5_unit_files", "; ".join(problems))
    else:
        rep.ok("H5 unit files equal the pinned copies; only the allowed drop-ins exist")
    try:
        start_us = int(props.get("ActiveEnterTimestampMonotonic", ""))
    except ValueError:
        start_us = None
    return UnitInfo(True, props.get("ActiveState", ""), props.get("UnitFileState", ""), running_live, start_us or None)


def check_live_ok_gate(host: Host, rep: Report) -> bool:
    """Same facts the executor requires of LIVE_OK (DEC-024 section 3): a regular file, not a symlink, owned root:root, mode EXACTLY 0644
    (mal-live must be able to open it read-only, and nobody may write it), in a root:root 0755 directory. Returns whether it is present."""
    present = host.exists(LIVE_OK)
    if host.exists(H5_ETC) and (host.islink(H5_ETC) or host.stat(H5_ETC) != "root:root:755"):
        rep.alert("h5_etc_dir", f"{H5_ETC} is {host.stat(H5_ETC)}{' (symlink)' if host.islink(H5_ETC) else ''}, expected root:root:755")
    if present:
        owner = host.stat(LIVE_OK) or "?:?:?"
        if host.islink(LIVE_OK) or owner != "root:root:644" or not host.is_regular(LIVE_OK):
            rep.alert("h5_live_ok_invalid", f"{LIVE_OK} must be a regular file owned root:root with mode exactly 0644, no symlink (is {owner})")
    if host.exists(f"{H5_DIR}/LIVE_OK"):
        rep.alert("h5_live_ok_stale", f"{H5_DIR}/LIVE_OK exists: the gate is {LIVE_OK}; a file in the state dir is not Helm's (remove it, find out who made it)")
    return present


def check_tier_file(host: Host, rep: Report) -> tuple[str | None, float | None]:
    """/etc/mal-h5/TIER: a regular file, not a symlink, owned root:root, mode exactly 0644, content exactly T0, T1 or T2 (Helm creates and edits
    it; the executor only reads it and runs T0 when it is missing or invalid). Returns (tier, mtime) when it is valid, else (None, None)."""
    if not host.exists(TIER_FILE) and not host.islink(TIER_FILE):
        rep.info(f"{TIER_FILE} is absent: the executor runs T0 (Helm creates it with T0 at go-live)")
        return None, None
    owner = host.stat(TIER_FILE) or "?:?:?"
    if host.islink(TIER_FILE) or owner != "root:root:644" or not host.is_regular(TIER_FILE):
        rep.alert("h5_tier_file", f"{TIER_FILE} must be a regular file owned root:root with mode exactly 0644, no symlink (is {owner}); "
                                  "the executor ignores it, runs T0 and alerts tier_file_problem")
        return None, None
    raw = host.read(TIER_FILE) or b""
    text = raw[:64].decode("ascii", "replace").strip()
    if text not in TIERS:
        rep.alert("h5_tier_file", f"{TIER_FILE} does not hold exactly T0, T1 or T2: the executor runs T0 and alerts tier_file_problem")
        return None, None
    rep.ok(f"{TIER_FILE} says {text}")
    return text, host.mtime(TIER_FILE)


def report_tier(host: Host, rep: Report, tier_state, ledger: bytes | None, tier_file, unit: UnitInfo, now: float, tier_attempts=None) -> None:
    """The tier the executor is on (its counters' tier_state), the file's, and how many buy attempts it has made in this tier. The count is the
    executor's own `tier_attempts` (counters file; what its per-tier max_attempts of 150 caps; reset at every tier_change). A sha without it
    falls back to tier_state.attempts, then to the live ledger's decision rows since the tier began."""
    ts = tier_state if isinstance(tier_state, dict) else {}
    ex = ts.get("tier") if ts.get("tier") in TIERS else None
    since = ts.get("since_ms") if isinstance(ts.get("since_ms"), (int, float)) else None
    n = tier_attempts if isinstance(tier_attempts, int) and not isinstance(tier_attempts, bool) else (ts.get("attempts") if isinstance(ts.get("attempts"), int) else None)
    if n is None and since is not None:  # an older sha without the executor's own count: the live ledger's decision rows since the tier began
        n = 0
        for line in (ledger or b"").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict) and row.get("kind") == "decision" and isinstance(row.get("ts_ms"), (int, float)) and row["ts_ms"] >= since:
                n += 1
    file_tier, file_mtime = tier_file if tier_file else (None, None)
    rep.facts["tier"] = ex or file_tier or "unknown"
    rep.info(f"tier: executor={ex or 'unknown'} file={file_tier or 'none'} since={time.strftime('%Y-%m-%dT%H:%MZ', time.gmtime(since / 1000)) if since else 'unknown'} "
             f"trades_in_tier={n if n is not None else 'unknown'}")
    if unit.running_live and file_tier and ex and file_tier != ex and file_mtime is not None and now - file_mtime > TIER_UNAPPLIED_S:
        rep.alert("h5_tier_unapplied", f"the TIER file says {file_tier} since {int((now - file_mtime) // 60)} min but the running executor is on {ex}: it re-reads the file on every tick, "
                                       "so it is not ticking or not reading /etc/mal-h5/TIER")


def check_h5_state(host: Host, rep: Report, funded: int | None, wallet: str, env_file: str, unit: UnitInfo, live_ok: bool,
                   balance_fn: Callable[[str, str], int] | None = None, public_rpc: bool = False, tier_file=None, now: float = 0.0) -> None:
    st_dir = host.stat(H5_DIR)
    if st_dir is None:
        rep.info(f"{H5_DIR} does not exist yet")
    elif st_dir != "mal-live:mal-live:700":
        rep.alert("h5_dir_mode", f"{H5_DIR} is {st_dir}, expected mal-live:mal-live:700")
    flags = {n: host.exists(f"{H5_DIR}/{n}") for n in ("STOP", "HALT")}
    flags["LIVE_OK"] = live_ok
    flags["wallet_STOP"] = host.exists(WALLET_STOP)
    flags["wallet_HALT"] = host.exists(WALLET_HALT)
    rep.info("files: " + " ".join(f"{n}={'yes' if v else 'no'}" for n, v in flags.items()))
    if flags["HALT"]:
        rep.alert("h5_halt_file", "HALT exists: everything is frozen, sells included")
    if flags["wallet_HALT"]:
        rep.alert("wallet_halt_file", f"{WALLET_HALT} exists: the wallet-wide HALT, which the executor honours (everything is frozen, sells included)")
    state = _json(host, f"{H5_DIR}/live/state-live.json") or {}
    counters = _json(host, f"{H5_DIR}/live/h5-counters.json") or {}
    opens = state.get("open") or {}
    pending = state.get("pending") or {}
    realized = int(state.get("realized_lamports", 0))
    halt_names = [n for n in sorted(counters.get("halts") or {}) if re.match(NAME_RE, str(n))]  # only name-shaped strings are ever printed
    rep.info(f"live state: attempts={state.get('attempts', 0)} open={len(opens)} pending={len(pending)} realized_sol={realized / 1e9:.6f} "
             f"halts={halt_names} sells_landed={counters.get('sells_landed', 0)} sells_late={counters.get('sells_late', 0)}")
    if counters.get("halts"):
        rep.alert("h5_live_halt", "latched: " + ", ".join(f"{n} ({HALT_MEANING[n]})" if n in HALT_MEANING else n for n in halt_names)
                  + " (new buys stop; cleared only by --clear-halt, never followed by a retune)")
    if (opens or pending) and not unit.running_live:
        rep.alert("h5_positions_unmanaged",
                  f"live state has {len(opens)} open and {len(pending)} pending position(s) and the unit is not running with --live "
                  f"(ActiveState={unit.active_state}): sell-and-close each open mint, or start the live unit (docs/runbooks/h5-executor.md)")
    stuck = [m for m, p in opens.items() if p.get("abandoned") or p.get("stuck")]
    if stuck:
        rep.alert("h5_stuck_position", f"{len(stuck)} stuck or abandoned position(s): root sell-and-close (docs/runbooks/h5-executor.md)")
    ledger = host.read(f"{H5_DIR}/live/h5-ledger.jsonl", LEDGER_TAIL)
    report_tier(host, rep, counters.get("tier_state"), ledger, tier_file, unit, now, counters.get("tier_attempts"))
    user = None
    for line in (ledger or b"").splitlines():
        if b'"kind":"start"' in line.replace(b" ", b""):
            try:
                user = json.loads(line).get("user") or user
            except ValueError:
                pass
    if user is not None and user != wallet:
        rep.alert("h5_wallet_mismatch", f"the ledger's last start row names a different wallet than {wallet}")
    if funded is None:
        rep.info("wallet balance not compared: pass --funded-sol")
        return
    try:
        bal = (balance_fn or (lambda w, e: Host().balance(w, e, public_rpc)))(wallet, env_file)
    except Exception as exc:  # noqa: BLE001 - never echo the URL
        rep.alert("balance_unavailable", f"getBalance failed ({type(exc).__name__})")
        return
    open_cost = sum(int(p.get("buy_cost_lamports") or p.get("spend") or 0) for p in opens.values())
    in_flight = sum(int(p.get("spend") or 0) for p in pending.values() if p.get("kind") == "buy")  # debited on chain, not yet an open position
    expected = funded + realized - open_cost - in_flight
    tol = BASE_TOLERANCE + RENT_TOLERANCE * (len(opens) + len(pending))
    gap = bal - expected
    line = (f"wallet {bal / 1e9:.6f} SOL; funded {funded / 1e9:.6f} + realized {realized / 1e9:.6f} - open cost {open_cost / 1e9:.6f} "
            f"- in-flight buys {in_flight / 1e9:.6f} = {expected / 1e9:.6f}; gap {gap / 1e9:+.6f} (tolerance {tol / 1e9:.6f})")
    if abs(gap) > tol:
        rep.alert("wallet_gap", line + " (unexplained deposit/withdrawal/loss, or a stale --funded-sol)")
    else:
        rep.ok(line)


def check_canary_idle(host: Host, rep: Report, unit: UnitInfo, live_ok: bool, shadow_dir: str, now: float) -> None:
    """A canary that is installed but not trading must not look healthy."""
    if not unit.installed:
        return
    newest = host.newest_hourly(shadow_dir)
    if newest is None:
        rep.alert("h5_feed_stale", f"no h5-shadow-<hour>.jsonl in {shadow_dir}: the shadow detector is not writing")
    elif now - newest[1] > FEED_STALE_S:
        rep.alert("h5_feed_stale", f"the newest shadow file {newest[0]} was last written {int((now - newest[1]) // 60)} min ago (limit {FEED_STALE_S // 60}): "
                                   "the detector is dead or stuck")
    else:
        rep.facts["feed_age_s"] = int(now - newest[1])
        rep.ok(f"shadow feed fresh ({newest[0]}, {int(now - newest[1])} s)")
    # The check above sees the HOST directory. The unit sees its own bind mount, made when it started: if the directory is missing then (the
    # leading `-` makes that a silent no-op) or was recreated since, the unit reads an empty or old directory while the host one looks fresh.
    # A directory born after the unit's current run began is exactly that case. (Chosen over reading the unit's namespace: that needs
    # CAP_SYS_ADMIN or CAP_SYS_PTRACE, which the watchdog must not hold; the runbook has the manual nsenter check for after a detector restart.)
    if unit.active_state == "active" and unit.start_monotonic_us:
        birth, up = host.birth(shadow_dir), host.uptime()
        if birth is None or up is None:
            rep.info("shadow directory birth time or uptime unavailable: the unit's bind was not compared with the host directory")
        elif birth > now - up + unit.start_monotonic_us / 1e6 + BIND_SLACK_S:
            rep.alert("h5_feed_bind_stale", f"{shadow_dir} was created after the unit's current run started: the unit's bind of /srv/mal-h5-shadow points at "
                                            "nothing or at the old directory. Wind-down, restart the unit, then check with nsenter (runbook Step 8)")
    if not live_ok:
        rep.info("LIVE_OK absent: canary idle by design (gate closed)")
        return
    why = []
    if unit.active_state != "active":
        why.append(f"unit is {unit.active_state}")
    if unit.unit_file_state != "enabled":
        why.append(f"unit is not enabled ({unit.unit_file_state or 'unknown'})")
    if unit.active_state == "active" and not unit.running_live:
        why.append("the running ExecStart has no --live (live drop-in missing)")
    if host.exists(f"{H5_DIR}/STOP"):
        why.append("STOP exists")
    if host.exists(WALLET_STOP):
        why.append(f"the wallet-wide {WALLET_STOP} exists (the executor honours it; the probe's old STOP file must be removed for H5 to buy)")
    if why:
        rep.alert("h5_idle", "LIVE_OK is present but the canary is not trading: " + "; ".join(why))
    t = host.mtime(LIVE_OK)
    if t is not None and now - t > IDLE_LEDGER_S:
        ledger = host.read(f"{H5_DIR}/live/h5-ledger.jsonl", LEDGER_TAIL) or b""
        last = 0
        for line in ledger.splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict) and row.get("kind") in IDLE_KINDS and isinstance(row.get("ts_ms"), (int, float)):
                last = max(last, row["ts_ms"] / 1000.0)
        if now - last > IDLE_LEDGER_S:
            rep.alert("h5_idle_ledger", f"LIVE_OK is {int((now - t) // 3600)} h old and the live ledger has no buy, skip or decision row in the last "
                                        f"{IDLE_LEDGER_S // 3600} h: the canary sees no triggers (feed, detector or executor)")


def check_watch(host: Host, rep: Report, live_ok: bool, now: float) -> None:
    """The watchdog itself: timer on, service not failing, state fresh, unit files as pinned. Its failures would otherwise be silent."""
    svc = sysctl_show(host, rep, WATCH_SERVICE, "LoadState", "ActiveState", "Result", "ExecMainStatus", "FragmentPath", "DropInPaths")
    tmr = sysctl_show(host, rep, WATCH_TIMER, "LoadState", "ActiveState", "UnitFileState", "FragmentPath", "DropInPaths")
    if svc is None or tmr is None:
        return
    if live_ok and (tmr.get("ActiveState") != "active" or tmr.get("UnitFileState") != "enabled"):
        rep.alert("h5_watch_timer", f"{WATCH_TIMER} is {tmr.get('ActiveState', 'missing')}/{tmr.get('UnitFileState', 'missing')}: the Discord watchdog must be on while the gate is open")
    if svc.get("LoadState") != "loaded" and tmr.get("LoadState") != "loaded":
        if not live_ok:
            rep.info("the watchdog is not installed yet")
        return
    problems = []
    for (installed, pinned), props in zip(WATCH_FILES, (svc, tmr)):
        want, got = host.read(pinned), host.read(installed)
        if want is None:
            problems.append(f"{pinned} is missing (no pinned install)")
        elif got != want:
            problems.append(f"{installed} differs from the pinned copy")
        if props.get("FragmentPath") != installed:
            problems.append(f"FragmentPath of {installed.rsplit('/', 1)[-1]} is {props.get('FragmentPath')!r}")
        if props.get("DropInPaths", "").strip():
            problems.append(f"drop-in(s) on {installed.rsplit('/', 1)[-1]}: {props['DropInPaths'].strip()}")
    if problems:
        rep.alert("h5_watch_files", "; ".join(problems))
    result = svc.get("Result")
    if result not in (None, "", "success"):
        rep.alert("h5_watch_failing", f"{WATCH_SERVICE} Result={result} ExecMainStatus={svc.get('ExecMainStatus')}: the watchdog's last run failed, so nothing is being "
                                      f"posted (journalctl -u {WATCH_SERVICE})")
    raw = host.read(WATCH_STATE)
    if raw is None:
        if live_ok:
            rep.alert("h5_watch_stale", f"{WATCH_STATE} does not exist: the watchdog has never completed a run")
        else:
            rep.info("the watchdog has not completed a run yet")
        return
    try:
        ts = float(json.loads(raw).get("ts"))
    except (ValueError, TypeError):
        rep.alert("h5_watch_stale", f"{WATCH_STATE} has no readable ts")
        return
    age = now - ts
    if age > WATCH_STATE_STALE_S:
        rep.alert("h5_watch_stale", f"the watchdog's last completed run was {int(age // 60)} min ago (limit {WATCH_STATE_STALE_S // 60}): it is not running or fails before it saves")
    else:
        rep.ok(f"the watchdog ran {int(age // 60)} min ago")


def check_refusals(host: Host, rep: Report, now: float, window_s: float = 6 * 3600) -> None:
    """The live ledger's last window: executor budget stops (DEC-024 section 8 "stop fired"), every `alert` row, and trigger refusals.
    The sealed stub (bad_intent:suppressed) is expected. Only name-shaped strings are ever printed from the ledger."""
    ledger = host.read(f"{H5_DIR}/live/h5-ledger.jsonl", LEDGER_TAIL)
    if not ledger:
        return
    hours = max(1, int(round(window_s / 3600)))
    counts: Counter = Counter()
    alert_rows: Counter = Counter()
    decisions = 0
    changes: list[tuple] = []
    for line in ledger.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        ts = row.get("ts_ms") if isinstance(row, dict) else None
        if not isinstance(ts, (int, float)) or ts / 1000.0 < now - window_s:
            continue
        kind = row.get("kind")
        if kind == "tier_change":
            changes.append((int(ts), *(row.get(k) if row.get(k) in TIERS else None for k in ("from_tier", "to_tier")),
                            row["problem"] if isinstance(row.get("problem"), str) and re.match(NAME_RE, row["problem"]) else None))
        elif kind == "decision":
            decisions += 1
        elif kind == "skip" and isinstance(row.get("reason"), str) and re.match(NAME_RE, row["reason"]):
            counts[row["reason"]] += 1
        elif kind == "alert" and isinstance(row.get("alert"), str) and re.match(NAME_RE, row["alert"]):
            alert_rows[row["alert"]] += 1
    rep.facts["tier_changes"] = changes
    for ts, old, new, problem in changes:
        rep.info(f"tier_change {old or 'none'} -> {new or '?'} at {time.strftime('%Y-%m-%dT%H:%MZ', time.gmtime(ts / 1000))}" + (f" (problem: {problem})" if problem else ""))
    if counts:
        rep.info(f"refusals in the last {hours} h ({decisions} decision(s)): " + ", ".join(f"{r} x{n}" for r, n in counts.most_common(8)))
    for reason, meaning in BUDGET_STOPS.items():
        if counts[reason]:
            rep.alert(f"h5_budget_stop_{reason}", f"the executor refused {counts[reason]} trigger(s) in the last {hours} h on its budget stop {reason} ({meaning}): "
                                                 "new buys are stopped until it clears; open positions still exit")
    for what, n in alert_rows.most_common():
        rep.alert(f"h5_executor_alert_{what}", f"the executor wrote {n} ALERT {what} row(s) in the last {hours} h"
                  + (f" ({ALERT_MEANING[what]})" if what in ALERT_MEANING else "") + f" (see the ledger and journalctl -u {H5_UNIT})")
    s0 = sum(counts[r] for r in S0_REFUSALS)
    if s0 >= S0_REFUSAL_ALERT_N:
        rep.alert("h5_s0_refusals", f"{s0} trigger(s) refused as s0_recv_late, s0_unverifiable or s0_before_history in {hours} h "
                                    f"({', '.join(f'{r} x{counts[r]}' for r in S0_REFUSALS if counts[r])}): the detector is backlogged or our slot history cannot verify s0")
    missing = {r: n for r, n in counts.items() if r.startswith("bad_intent:missing_")}
    new_fields = sum(counts[r] for r in NEW_FIELD_REFUSALS)
    if missing or (new_fields >= SCHEMA_REFUSAL_ALERT_N and decisions == 0):
        shown = ", ".join(f"{r} x{n}" for r, n in {**missing, **{r: counts[r] for r in NEW_FIELD_REFUSALS if counts[r]}}.items())
        rep.alert("h5_feed_schema", f"triggers are refused for the trigger fields ({shown}) and none was acted on: the shadow job is probably not running at "
                                    "#477 head 3dbe1de or later (the head that writes s0_minus_announced_slots and base_breaks_unresolved_settled)")


def run_checks(args: argparse.Namespace, host: Host, out: Callable[[str], None] = print,
               balance_fn: Callable[[str, str], int] | None = None, now: float | None = None) -> Report:
    rep = Report(out)
    now = time.time() if now is None else now
    checker = load_unit_checker(Path(__file__).resolve().parent)
    funded = None if args.funded_sol is None else int(round(args.funded_sol * 1e9))
    window_s = float(getattr(args, "window_hours", 6.0)) * 3600
    if not host.sudo_ok():
        rep.alert("sudo_unavailable", "sudo -n /usr/bin/true failed: the files in /var/lib/mal-live cannot be read, so the checks that need them cannot pass")
    ctx: dict = {"unit": NO_UNIT, "live_ok": False, "tier_file": None}

    def unit_step() -> None:
        ctx["unit"] = check_h5_unit(host, rep, checker)

    def gate_step() -> None:
        ctx["live_ok"] = check_live_ok_gate(host, rep)
        ctx["tier_file"] = check_tier_file(host, rep)

    steps = [("probe_units", lambda: check_probe_units(host, rep))]
    if not args.skip_probe_state:
        steps.append(("probe_state", lambda: check_probe_state(host, rep, Path(args.baseline), args.write_baseline, args.expect_sha256,
                                                               getattr(args, "expect_dec020_sha256", None))))
    steps += [("h5_unit", unit_step), ("h5_gate", gate_step),
              ("h5_state", lambda: check_h5_state(host, rep, funded, args.wallet, args.rpc_env, ctx["unit"], ctx["live_ok"], balance_fn, args.public_rpc,
                                                       ctx["tier_file"], now)),
              ("h5_idle", lambda: check_canary_idle(host, rep, ctx["unit"], ctx["live_ok"], args.shadow_dir, now)),
              ("h5_watch", lambda: check_watch(host, rep, ctx["live_ok"], now)),
              ("h5_refusals", lambda: check_refusals(host, rep, now, window_s))]
    for name, step in steps:
        try:
            step()
        except SudoError as exc:
            rep.alert("sudo_failed", f"{name}: {exc} (a failing privileged read is not 'absent')")
        except Unsafe as exc:
            rep.alert("unsafe_path", f"{name}: {exc}")
        except Exception as exc:  # noqa: BLE001 - a check that cannot run is an alert, never a silent pass
            rep.alert("check_failed", f"{name}: {type(exc).__name__}")
    return rep


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--funded-sol", type=float, default=None, help="total SOL the owner has deposited (net of withdrawals)")
    ap.add_argument("--wallet", default=WALLET)
    ap.add_argument("--rpc-env", default="/var/lib/mal/fast-listener/helius.env", help="read-only getBalance only; HELIUS_API_KEY in the environment wins")
    ap.add_argument("--public-rpc", action="store_true", help=f"getBalance through {PUBLIC_RPC}: no key at all")
    ap.add_argument("--shadow-dir", default=str(Path.home() / "data/h5-shadow"), help="the shadow detector's output directory (the manager's own, no sudo)")
    ap.add_argument("--baseline", default=str(Path.home() / "data/h5-daily/probe-state.baseline.json"))
    ap.add_argument("--write-baseline", action="store_true", help="record the sha256 of the probe's state-live.json and state-live-dec020.json (once, at install time)")
    ap.add_argument("--expect-sha256", default=None, help="with --write-baseline: Helm's Step 1 sha256 of state-live.json; refuse to write on a mismatch")
    ap.add_argument("--expect-dec020-sha256", default=None, help="with --write-baseline: Helm's Step 1 value for state-live-dec020.json (a sha256, or the word absent)")
    ap.add_argument("--skip-probe-state", action="store_true", help="skip the probe-state check (the watchdog leaves it to the daily run)")
    ap.add_argument("--window-hours", type=float, default=6.0, help="how far back the ledger is read for stops, alerts and refusals (the daily job uses 24)")
    ap.add_argument("--print-sudoers", action="store_true", help="print the exact sudoers lines the privileged calls need and exit")
    ap.add_argument("--sudoers-user", default="claude")
    return ap


def main(argv: list[str] | None = None, host: Host | None = None, out: Callable[[str], None] = print,
         balance_fn: Callable[[str, str], int] | None = None, now: float | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.print_sudoers:
        out(sudoers_text(args.sudoers_user).rstrip("\n"))
        return 0
    rep = run_checks(args, host or Host(), out, balance_fn, now)
    out(f"h5_daily_check ALERTS={rep.alerts}")
    return 1 if rep.alerts else 0


if __name__ == "__main__":
    sys.exit(main())
