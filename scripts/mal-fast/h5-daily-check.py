#!/usr/bin/env python3
"""Daily health check for the H5 live executor (DEC-024), for the manager's 12:17Z cron. Replaces the decommissioned-probe check.
Its engine also runs every few minutes as the H5 watchdog (h5-watch.py), which posts the alerts to Discord.

    python3 -I scripts/mal-fast/h5-daily-check.py --funded-sol 0.25 [--write-baseline [--expect-sha256 <Step 1 value>]]

Read-only, no key: it never opens /etc/mal-probe, never loads a wallet, never sends anything. Files in the 0700 state dir are
read through fixed paths only: lstat first (a symlink or a hard-linked file is refused), then O_NOFOLLOW (direct) or
`sudo -n /usr/bin/dd iflag=nofollow` (never cat, which follows symlinks). File content is never echoed into an alert. A failing
`sudo -n` is an ALERT (sudo_unavailable / sudo_failed), never "file absent". Prints INFO / OK / ALERT lines and exits 1 if there
is any ALERT. The RPC URL (HELIUS_API_KEY from the environment or the paper env file, or --public-rpc) is never printed.

  ALLOWED now   the H5 unit mal-h5-executor (active or enabled), /var/lib/mal-live/h5, the pinned tree, /etc/mal-h5/LIVE_OK. The
                wallet is no longer expected to be 0.
  STILL ALERTS  any mal-probe-executor* unit active or enabled; /var/lib/mal-live/state-live.json changed (sha256 against a
                baseline, plus attempts <= 62, realized -0.210755 SOL, nothing open). (The old "/var/lib/mal-live/STOP must exist" alert
                is gone: the executor treats that file as a wallet-wide STOP, so it must be absent for H5 to buy.)
  UNMANAGED     live state with open or pending positions while the unit is not active with --live (alert h5_positions_unmanaged).
  IDLE CANARY   stale shadow feed (newest hourly file older than 10 min); LIVE_OK present but the unit not active and enabled,
                the running ExecStart without --live, or STOP present (the H5 one or the wallet-wide one); LIVE_OK older than 6 h with no buy/skip/decision ledger row
                in 6 h; the watchdog timer not enabled.
  REFUSALS      the live ledger's `skip` rows of the last 6 h: s0_recv_late / s0_unverifiable (3 or more), and triggers refused for the two #477
                fields (bad_intent:missing_*, or 5 or more for s0_minus_announced_slots / base_breaks_unresolved with no decision: the shadow job
                is not at #477 head fe7eb43 or later). The sealed stub (bad_intent:suppressed) is expected and never an alert.
  UNIT FILES    the installed unit files equal the pinned copies; the drop-in list comes from systemd (DropInPaths) and may hold
                only live.conf (equal to the pinned drop-in) and 10-shadow-feed.conf (passing check-h5-unit.py --shadow-feed).
  WALLET        balance against funded + realized - cost of open positions - in-flight buys, with a tolerance for rent and fees.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import pwd
import grp
import stat as stat_mod
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Callable, NamedTuple

WALLET = "5n95HyhZqjZNkjdp44QGJoAqk4ZFjDgMKuUzWcQqSugk"  # the DEC-019 probe wallet; public (DEC-024)
PROBE_STATE = "/var/lib/mal-live/state-live.json"
WALLET_STOP = "/var/lib/mal-live/STOP"  # the probe's file names; the executor (4f05e30) honours both wallet-wide, next to its own in the H5 dir
WALLET_HALT = "/var/lib/mal-live/HALT"
PROBE_MAX_ATTEMPTS = 62
PROBE_REALIZED_SOL = -0.210755
H5_UNIT = "mal-h5-executor"
WATCH_TIMER = "mal-h5-watch.timer"
H5_DIR = "/var/lib/mal-live/h5"
H5_ETC = "/etc/mal-h5"  # root:root 0755; holds LIVE_OK, which Helm creates (the executor cannot)
LIVE_OK = f"{H5_ETC}/LIVE_OK"
PINNED = "/usr/local/lib/mal-h5-exec/current"
UNIT_FILE = f"/etc/systemd/system/{H5_UNIT}.service"
DROPIN_DIR = f"/etc/systemd/system/{H5_UNIT}.service.d"
DROPIN_LIVE = f"{DROPIN_DIR}/live.conf"
DROPIN_FEED = f"{DROPIN_DIR}/10-shadow-feed.conf"
PUBLIC_RPC = "https://api.mainnet-beta.solana.com"
RENT_TOLERANCE = 2_100_000  # per open or pending position (token account rent, refunded at close)
BASE_TOLERANCE = 5_000_000  # fees in flight, dust
FEED_STALE_S = 600  # the newest hourly shadow file must have been written to within 10 minutes
IDLE_LEDGER_S = 6 * 3600  # LIVE_OK older than this and no buy/skip/decision row newer than this: the canary is idle
IDLE_KINDS = ("buy", "skip", "decision")
LEDGER_TAIL = 4_000_000
HOURLY_RE = r"^h5-shadow-\d{4}-\d{2}-\d{2}T\d{2}\.jsonl$"
ACTIVE_STATES = {"active", "activating", "reloading", "deactivating"}
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
NAME_RE = r"^[A-Za-z0-9_:.\-]{1,60}$"  # only names that look like names are ever printed from a file
# Trigger refusals the executor ledgers as `skip` rows (reason=...). Printing the reason NAMES is fine; they are fixed strings.
S0_REFUSALS = ("s0_recv_late", "s0_unverifiable")
NEW_FIELD_REFUSALS = ("bad_intent:s0_minus_announced_slots", "bad_intent:base_breaks_unresolved")  # the two fields from #477 head fe7eb43
EXPECTED_REFUSALS = ("bad_intent:suppressed",)  # #477's sealed stub from 2026-10-16T01Z: a refusal by design, never an alert
REFUSAL_WINDOW_S = 6 * 3600
S0_REFUSAL_ALERT_N = 3
SCHEMA_REFUSAL_ALERT_N = 5
ENABLED_STATES = {"enabled", "enabled-runtime", "linked", "linked-runtime", "alias"}


class SudoError(Exception):
    """A privileged read failed. Never to be confused with the file being absent."""


class Unsafe(Exception):
    """A fixed path is not what it must be (symlink, hard link, not a regular file). The message names the path, never content."""


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
        try:
            r = self._sudo("/usr/bin/stat", "-c", "%F|%h|%U|%G|%a|%Y|%s", path)
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
        """File bytes (the last `tail` bytes if given), or None if the path does not exist. Refuses symlinks, hard-linked files and
        non-regular files before reading; reads with O_NOFOLLOW, or `dd iflag=nofollow` through sudo. Never cat."""
        e = self.lstat(path)
        if e is None:
            return None
        if e.kind != "file":
            raise Unsafe(f"{path} is not a regular file ({e.kind})")
        if e.nlink > 1:
            raise Unsafe(f"{path} has {e.nlink} hard links")
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
                return fh.read()
        try:
            r = self._sudo("/usr/bin/dd", "iflag=nofollow,skip_bytes", f"skip={skip}", "status=none", f"if={path}")
        except (OSError, subprocess.SubprocessError) as exc:
            raise SudoError(f"read {path}: {type(exc).__name__}") from None
        if r.returncode == 0:
            return r.stdout
        raise SudoError(f"read {path}: privileged read failed")

    def newest_hourly(self, directory: str) -> tuple[str, float] | None:
        import re

        try:
            names = [n for n in os.listdir(directory) if re.match(HOURLY_RE, n)]
        except OSError:
            return None
        if not names:
            return None
        newest = max(names)
        return newest, os.lstat(os.path.join(directory, newest)).st_mtime

    def systemctl(self, *argv: str) -> tuple[int, str]:
        r = subprocess.run(["systemctl", *argv], capture_output=True, text=True, timeout=30)
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
        self.out, self.alerts, self.alert_list = out, 0, []

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


NO_UNIT = UnitInfo(False, "not-installed", "", False)


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
    rc, out = host.systemctl("list-unit-files", "--no-legend", "--no-pager", "mal-probe-executor*")
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1] in ENABLED_STATES:
            bad.append(f"{parts[0]} is {parts[1]}")
    rc, out = host.systemctl("list-units", "--all", "--plain", "--no-legend", "--no-pager", "mal-probe-executor*")
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[2] in ACTIVE_STATES:
            bad.append(f"{parts[0]} is {parts[2]}")
    if bad:
        rep.alert("probe_unit", "; ".join(bad) + " (two processes must never share the key; stop and disable it, docs/runbooks/h5-executor.md step 1)")
    else:
        rep.ok("no mal-probe-executor unit is active or enabled")


def check_probe_state(host: Host, rep: Report, baseline_path: Path, write_baseline: bool, expect_sha: str | None = None) -> None:
    raw = host.read(PROBE_STATE)
    if raw is None:
        rep.alert("probe_state", f"{PROBE_STATE} is missing")
        return
    digest = hashlib.sha256(raw).hexdigest()
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
            problems.append("--expect-sha256 does not match the probe state's sha256 now: the baseline was NOT written (it changed since Helm's Step 1)")
        else:
            baseline_path.parent.mkdir(parents=True, exist_ok=True)
            baseline_path.write_text(json.dumps({"sha256": digest}) + "\n")
            rep.info(f"probe state baseline written ({digest[:12]})")
    elif not baseline_path.exists():
        problems.append(f"no baseline at {baseline_path}: run once with --write-baseline, or changes cannot be detected")
    else:
        if json.loads(baseline_path.read_text()).get("sha256") != digest:
            problems.append("sha256 differs from the baseline: the probe's state file changed")
    # The probe's old "STOP must exist" alert is gone on purpose: the executor honours /var/lib/mal-live/STOP as a wallet-wide STOP, so a
    # leftover probe STOP would stop every H5 buy. Its presence is reported below (and is an idle-canary reason once the gate is open).
    if problems:
        rep.alert("probe_state", "; ".join(problems))
    else:
        rep.ok(f"probe state unchanged (attempts {st.get('attempts')}, realized -0.210755 SOL)")


def check_h5_unit(host: Host, rep: Report, checker) -> UnitInfo:
    """Alerts if what is installed differs from the pinned copies. Returns what the other checks need."""
    rc, out = host.systemctl("show", H5_UNIT, "-p", "LoadState,ActiveState,SubState,UnitFileState,NRestarts,Result,FragmentPath", "--no-pager")
    props = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
    if props.get("LoadState") in (None, "not-found"):
        rep.info(f"{H5_UNIT} is not installed")
        return NO_UNIT
    rep.info(f"{H5_UNIT} active={props.get('ActiveState')}/{props.get('SubState')} enabled={props.get('UnitFileState')} "
             f"restarts={props.get('NRestarts')} result={props.get('Result')}")
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
    rc, out = host.systemctl("show", H5_UNIT, "-p", "DropInPaths", "--value", "--no-pager")
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
        rc, es = host.systemctl("show", H5_UNIT, "-p", "ExecStart", "--value", "--no-pager")
        if "/usr/local/lib/mal-h5-exec/" not in es or "fast-forward" in es:
            problems.append("running ExecStart is not the pinned launcher")
        running_live = props.get("ActiveState") == "active" and "--live" in es
    if problems:
        rep.alert("h5_unit_files", "; ".join(problems))
    else:
        rep.ok("H5 unit files equal the pinned copies; only the allowed drop-ins exist")
    return UnitInfo(True, props.get("ActiveState", ""), props.get("UnitFileState", ""), running_live)


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


def check_h5_state(host: Host, rep: Report, funded: int | None, wallet: str, env_file: str, unit: UnitInfo, live_ok: bool,
                   balance_fn: Callable[[str, str], int] | None = None, public_rpc: bool = False) -> None:
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
    import re as _re

    halt_names = [n for n in sorted(counters.get("halts") or {}) if _re.match(NAME_RE, str(n))]  # only name-shaped strings are ever printed
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
        rep.ok(f"shadow feed fresh ({newest[0]}, {int(now - newest[1])} s)")
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
    rc, out = host.systemctl("show", WATCH_TIMER, "-p", "ActiveState,UnitFileState", "--no-pager")
    w = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
    if w.get("ActiveState") != "active" or w.get("UnitFileState") != "enabled":
        rep.alert("h5_watch_timer", f"{WATCH_TIMER} is {w.get('ActiveState', 'missing')}/{w.get('UnitFileState', 'missing')}: the Discord watchdog must be on while the gate is open")


def check_refusals(host: Host, rep: Report, now: float) -> None:
    """What the executor refused in the last 6 h, from the live ledger's `skip` rows. The sealed stub (bad_intent:suppressed) is expected."""
    import re
    from collections import Counter

    ledger = host.read(f"{H5_DIR}/live/h5-ledger.jsonl", LEDGER_TAIL)
    if not ledger:
        return
    counts: Counter = Counter()
    decisions = 0
    for line in ledger.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        ts = row.get("ts_ms") if isinstance(row, dict) else None
        if not isinstance(ts, (int, float)) or ts / 1000.0 < now - REFUSAL_WINDOW_S:
            continue
        if row.get("kind") == "decision":
            decisions += 1
        elif row.get("kind") == "skip" and isinstance(row.get("reason"), str) and re.match(NAME_RE, row["reason"]):
            counts[row["reason"]] += 1
    if counts:
        rep.info(f"refusals in the last {REFUSAL_WINDOW_S // 3600} h ({decisions} decision(s)): " + ", ".join(f"{r} x{n}" for r, n in counts.most_common(8)))
    s0 = sum(counts[r] for r in S0_REFUSALS)
    if s0 >= S0_REFUSAL_ALERT_N:
        rep.alert("h5_s0_refusals", f"{s0} trigger(s) refused as s0_recv_late or s0_unverifiable in {REFUSAL_WINDOW_S // 3600} h "
                                    f"({', '.join(f'{r} x{counts[r]}' for r in S0_REFUSALS if counts[r])}): the detector is backlogged or our slot history cannot verify s0")
    missing = {r: n for r, n in counts.items() if r.startswith("bad_intent:missing_")}
    new_fields = sum(counts[r] for r in NEW_FIELD_REFUSALS)
    if missing or (new_fields >= SCHEMA_REFUSAL_ALERT_N and decisions == 0):
        shown = ", ".join(f"{r} x{n}" for r, n in {**missing, **{r: counts[r] for r in NEW_FIELD_REFUSALS if counts[r]}}.items())
        rep.alert("h5_feed_schema", f"triggers are refused for the trigger fields ({shown}) and none was acted on: the shadow job is probably not running at "
                                    "#477 head fe7eb43 or later (the head that writes s0_minus_announced_slots and base_breaks_unresolved)")


def run_checks(args: argparse.Namespace, host: Host, out: Callable[[str], None] = print,
               balance_fn: Callable[[str, str], int] | None = None, now: float | None = None) -> Report:
    rep = Report(out)
    now = time.time() if now is None else now
    checker = load_unit_checker(Path(__file__).resolve().parent)
    funded = None if args.funded_sol is None else int(round(args.funded_sol * 1e9))
    if not host.sudo_ok():
        rep.alert("sudo_unavailable", "sudo -n /usr/bin/true failed: the files in /var/lib/mal-live cannot be read, so the checks that need them cannot pass")
    ctx: dict = {"unit": NO_UNIT, "live_ok": False}

    def unit_step() -> None:
        ctx["unit"] = check_h5_unit(host, rep, checker)

    def gate_step() -> None:
        ctx["live_ok"] = check_live_ok_gate(host, rep)

    steps = [("probe_units", lambda: check_probe_units(host, rep))]
    if not args.skip_probe_state:
        steps.append(("probe_state", lambda: check_probe_state(host, rep, Path(args.baseline), args.write_baseline, args.expect_sha256)))
    steps += [("h5_unit", unit_step), ("h5_gate", gate_step),
              ("h5_state", lambda: check_h5_state(host, rep, funded, args.wallet, args.rpc_env, ctx["unit"], ctx["live_ok"], balance_fn, args.public_rpc)),
              ("h5_idle", lambda: check_canary_idle(host, rep, ctx["unit"], ctx["live_ok"], args.shadow_dir, now)),
              ("h5_refusals", lambda: check_refusals(host, rep, now))]
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
    ap.add_argument("--write-baseline", action="store_true", help="record the probe state's sha256 (once, at install time)")
    ap.add_argument("--expect-sha256", default=None, help="with --write-baseline: Helm's Step 1 sha256 of state-live.json; refuse to write on a mismatch")
    ap.add_argument("--skip-probe-state", action="store_true", help="skip the probe-state check (the watchdog leaves it to the daily run)")
    return ap


def main(argv: list[str] | None = None, host: Host | None = None, out: Callable[[str], None] = print,
         balance_fn: Callable[[str, str], int] | None = None, now: float | None = None) -> int:
    args = build_parser().parse_args(argv)
    rep = run_checks(args, host or Host(), out, balance_fn, now)
    out(f"h5_daily_check ALERTS={rep.alerts}")
    return 1 if rep.alerts else 0


if __name__ == "__main__":
    sys.exit(main())
