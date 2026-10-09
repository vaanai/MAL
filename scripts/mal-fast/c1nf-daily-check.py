#!/usr/bin/env python3
"""Daily health check for the C1-NF live canary (DEC-026), for the manager's daily job. Modelled on h5-daily-check.py (DEC-024).
Its engine also runs every few minutes as the C1-NF watchdog (c1nf-watch.py), which posts the alerts to Discord.

    python3 -I /home/claude/MAL/scripts/mal-fast/c1nf-daily-check.py --wallet <C1-NF PUBLIC ADDRESS> --funded-sol 0.5 --public-rpc \\
        --window-hours 24 --shadow-dir /home/claude/data/c1nf-shadow

Read-only, no key: it never opens /etc/mal-c1nf-key, never loads a wallet, never sends anything. It never reads, writes or restarts
anything of H5's (DEC-026 section 4: "Nothing in this DEC reads, writes or restarts them"): not its unit, files, state, TIER or watchdog.
The only shared files it looks at are the wallet-wide /var/lib/mal-live/{STOP,HALT}, which the C1-NF executor honours (existence only).
Files in the 0700 state dir are read through FIXED paths only (PRIV_STAT / PRIV_READ below), exactly as the H5 check does: lstat first
(a symlink, a hard-linked file, a non-regular file or one over 8 MB is refused), then O_NOFOLLOW or `sudo -n /usr/bin/dd iflag=nofollow`.
`--print-sudoers` prints the exact sudoers lines. A failing `sudo -n` or `systemctl` is an ALERT, never "absent". Prints INFO / OK /
ALERT lines and exits 1 on any ALERT. The RPC URL is never printed.

CLASS-BLIND (EXP-025 Amendment 2 item 5, DEC-026 section 9.3). No observer may split C1-NF outcomes by synthetic class before the final
look; this check and the watchdog print nothing by class. Concretely:
  - no shadow outcome file is ever opened: the feed check stats only the c1nf-events-<hour>.jsonl names (heartbeats), never their content,
    and never c1nf-picks-* or c1nf-outcomes-*;
  - ledger rows are read only through the fixed keys named in LEDGER_KEYS; any other field (a class, an outcome) is never looked at;
  - every name printed from a file must look like a name (NAME_RE) and must not name the class (CLASS_RE); the one exception is the A3
    structure alert synthetic_share_high (DEC-026 section 7 rule 6: "recorded and alerted only"), a share of graduations, not of outcomes;
  - Report drops (and counts, as class_blind_withheld) any line that would still name the class, so a future edit cannot leak one.
  A test runs every check on two ledgers that differ only in class fields and requires identical output.

  UNIT          mal-c1nf-executor: installed unit file equal to the pinned copy, drop-ins only live.conf (equal to the pinned drop-in) and
                10-shadow-feed.conf (both pass check-c1nf-unit.py from the executor PR); the running ExecStart is the pinned launcher; a
                unit running --live sets a credential, and every credential line is exactly LoadCredential=c1nf-wallet:<the C1-NF key> (never
                the H5/probe key).
  GATE          /etc/mal-c1nf/LIVE_OK (root:root 0644, no symlink, parent root:root 0755); TIER exactly T1 or T2 (missing or invalid = T1).
  LIVE CONFIG   the pinned c1nf-executor-live.json against DEC-026 section 6: stake 0.05 SOL (lowered from the 0.10 code ceiling), priority
                505,000 lamports, end_ms 2026-10-24T00:30Z, pick age <= 3 s, wallet floor >= 0.05 SOL; a limit present may only be lower.
  STATE         latched halts, HALT files, unmanaged or stuck positions, the effective total stop min(0.30, 0.35 x funded) and how much of
                it realized loss has used, the ledger's wallet against --wallet (and --wallet never the H5 wallet), the balance.
  IDLE CANARY   stale shadow heartbeat; a shadow directory created after the unit started; LIVE_OK present but the unit not trading.
  WATCHDOG      mal-c1nf-watch.timer on, its service not failing, its state fresh, its unit files as pinned.
  STOPS/ALERTS  executor budget stops, every executor `alert` row, refusals by reason name (counts only), the fill-rate alert (DEC-026
                section 7 rule 8: more than 28.9% of the last 30 monitored picks unfilled), late sells (rule 5: more than 10% of landed
                sells), picks refused for missing decision-time guard inputs, feed_stale refusals, and the CAP-PICK seal from
                2026-10-16T01Z: seal refusals are a count only; every pick refused because the oracle is unavailable is an alert (paused).

DEPENDENCY. The file names in the state dir, the halt names, the skip-reason names and the live-config keys are #504's (branch
claude/c1nf-executor at a98ff07, built on the H5 executor: live/state-live.json, live/h5-counters.json, live/h5-ledger.jsonl). The rebuilt
executor (claude/c1nf-executor-v2) must keep them or this file must follow it before the install; tools/test_c1nf_daily_check.py
pins them in one place (the constants below).
"""
from __future__ import annotations

import argparse
import calendar
import grp
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

H5_WALLET = "5n95HyhZqjZNkjdp44QGJoAqk4ZFjDgMKuUzWcQqSugk"  # public (DEC-024); only compared, so the C1-NF wallet is never the H5 one
WALLET = ""  # the second wallet's PUBLIC address: Helm gives it (DEC-026 section 13 step 2); pass --wallet until a PR pins it here
WALLET_STOP = "/var/lib/mal-live/STOP"  # wallet-wide files the executor honours next to its own (DEC-026 section 5); existence only
WALLET_HALT = "/var/lib/mal-live/HALT"
C1NF_UNIT = "mal-c1nf-executor"
WATCH_SERVICE = "mal-c1nf-watch.service"
WATCH_TIMER = "mal-c1nf-watch.timer"
C1NF_DIR = "/var/lib/mal-live/c1nf"
LIVE_DIR = f"{C1NF_DIR}/live"
STATE_FILE = f"{LIVE_DIR}/state-live.json"
COUNTERS_FILE = f"{LIVE_DIR}/h5-counters.json"  # #504 reuses H5Counters and its file name inside the C1-NF state dir
LEDGER_FILE = f"{LIVE_DIR}/h5-ledger.jsonl"
C1NF_ETC = "/etc/mal-c1nf"  # root:root 0755; holds LIVE_OK and TIER, which Helm creates (the executor cannot)
LIVE_OK = f"{C1NF_ETC}/LIVE_OK"
TIER_FILE = f"{C1NF_ETC}/TIER"
TIERS = ("T1", "T2")  # DEC-026 section 5: exactly T1 or T2; missing or invalid means T1, the lowest
KEY_PATH = "/etc/mal-c1nf-key/c1nf-wallet.json"  # never opened or stat'ed here; only matched in the unit text
CREDENTIAL_LINE = f"c1nf-wallet:{KEY_PATH}"
PINNED_ROOT = "/usr/local/lib/mal-c1nf-exec"
PINNED = f"{PINNED_ROOT}/current"
LIVE_CONFIG = f"{PINNED}/c1nf-executor-live.json"
UNIT_FILE = f"/etc/systemd/system/{C1NF_UNIT}.service"
DROPIN_DIR = f"/etc/systemd/system/{C1NF_UNIT}.service.d"
DROPIN_LIVE = f"{DROPIN_DIR}/live.conf"
DROPIN_FEED = f"{DROPIN_DIR}/10-shadow-feed.conf"
UNIT_CHECKER = "check-c1nf-unit.py"  # from the executor PR (claude/c1nf-executor-v2): --base, --dropin, --shadow-feed
WATCH_STATE = "/var/lib/mal-c1nf-watch/state.json"
WATCH_FILES = ((f"/etc/systemd/system/{WATCH_SERVICE}", f"{PINNED}/{WATCH_SERVICE}"),
               (f"/etc/systemd/system/{WATCH_TIMER}", f"{PINNED}/{WATCH_TIMER}"))
PUBLIC_RPC = "https://api.mainnet-beta.solana.com"
RENT_TOLERANCE = 2_100_000  # per open or pending position (token account rent, refunded at close)
BASE_TOLERANCE = 5_000_000
FEED_STALE_S = 600  # the newest heartbeat file must have been written to within 10 minutes (the executor's own line is 150 s)
BIND_SLACK_S = 5
WATCH_STATE_STALE_S = 15 * 60
IDLE_LEDGER_S = 6 * 3600
IDLE_KINDS = ("buy", "skip", "decision", "pick_status")
MAX_READ = 8 * 1024 * 1024
LEDGER_TAIL = 4_000_000
HOURLY_RE = r"^c1nf-events-\d{4}-\d{2}-\d{2}T\d{2}\.jsonl$"  # heartbeats only (#503); picks and outcomes files are never matched
ACTIVE_STATES = {"active", "activating", "reloading", "deactivating"}
ENABLED_STATES = {"enabled", "enabled-runtime", "linked", "linked-runtime", "alias"}

# DEC-026 section 6 and section 3 (O-3, O-4, O-7). Lamports.
LAMPORTS = 1_000_000_000
STAKE_LAMPORTS = 50_000_000  # 0.05 SOL; the code ceiling 0.10 is lowered by the live config
STAKE_CEILING_LAMPORTS = 100_000_000
PRIORITY_LAMPORTS = 505_000
MAX_OPEN = 2
MAX_ATTEMPTS_DAY = 30
DAILY_STOP_LAMPORTS = 200_000_000
TOTAL_STOP_CEILING_LAMPORTS = 300_000_000
TIER_WALLET_FRAC = 0.35
WALLET_FLOOR_LAMPORTS = 50_000_000
MAX_PICK_AGE_S = 3.0
END_MS = calendar.timegm((2026, 10, 24, 0, 30, 0)) * 1000  # 2026-10-24T00:30Z (O-4)
SEAL_START_S = calendar.timegm((2026, 10, 16, 1, 0, 0))  # CAP-PICK seal: the oracle fails closed from here (DEC-026 section 9.1)
# live-config key -> (rule, value). "eq": must be present and equal. "le"/"ge": absent means the code constant applies; present may only be
# lower (le) or higher (ge). Key names are #504's.
CONFIG_RULES = {
    "stake_lamports": ("eq", STAKE_LAMPORTS),
    "buy_priority_lamports": ("eq", PRIORITY_LAMPORTS),
    "end_ms": ("eq", END_MS),
    "max_open": ("le", MAX_OPEN),
    "max_trades_per_day": ("le", MAX_ATTEMPTS_DAY),
    "daily_loss_lamports": ("le", DAILY_STOP_LAMPORTS),
    "total_loss_lamports": ("le", TOTAL_STOP_CEILING_LAMPORTS),
    "max_pick_age_s": ("le", MAX_PICK_AGE_S),
    "wallet_floor_lamports": ("ge", WALLET_FLOOR_LAMPORTS),
}
FILL_RATE_WINDOW = 30
FILL_RATE_ALERT = 0.289  # DEC-026 section 7 rule 8: the pressure leg's mean failure rate
LATE_SELL_SHARE = 0.10  # rule 5: more than 10% of sells landing later than landing + 305 s
LATE_SELL_MIN = 10

# The only ledger keys ever looked at. Nothing else of a row is read, so a class field or an outcome on a row cannot reach the output.
LEDGER_KEYS = ("kind", "ts_ms", "reason", "alert", "status", "monitored", "user", "from_tier", "to_tier", "problem")
NAME_RE = r"^[A-Za-z0-9_:.\-]{1,60}$"
CLASS_RE = re.compile(r"synth|migration_class|mig_class", re.I)
CLASS_ALLOWED = ("synthetic_share_high",)  # the A3 structure flag: a share of graduations, alert only (DEC-026 section 7 rule 6)

# Live halts the executor latches (#504 names, DEC-026 section 7). Unknown names are still shown if name-shaped and class-free.
HALT_MEANING = {
    "fill_selection_adverse": "unfilled picks beat filled ones by more than 3 pp over the last 30 monitored (rule 1)",
    "twin_divergence": "live worse than its paper twin by more than 1 pp at the CI90 upper bound after 50 fills (rule 2)",
    "landing_p50_gt_1_9s": "rolling landing p50 above 1.9 s (rule 3)",
    "out_of_rule_entry": "a buy landed more than 5 s after SD_slot (rule 3)",
    "stuck_position": "a position not closed by landing + 600 s (rule 4)",
    "late_sells_gt_5pct": "more than 5% of landed sells late (H5's latch, inherited)",
    "pins_changed": "A3: the program pins changed (rule 6)",
    "program_changed": "A3: a program redeploy (rule 6, a live halt for the canary)",
    "ms_per_slot_out_of_range": "ms per slot outside [150, 450] (rule 6)",
    "model_hash_changed": "the model file or manifest hash differs from the pinned one (rule 7)",
}
BUDGET_STOPS = {
    "total_loss_stop": "total stop reached (min(0.30, 35% of the wallet at tier start)); no new buys until the owner restarts",
    "daily_loss_stop": "daily realized loss stop 0.20 SOL reached (until 00:00Z)",
    "max_trades_day": "30 buy attempts today (until 00:00Z)",
    "max_attempts": "attempt cap reached",
    "max_days": "duration cap reached",
    "end_instant": "end_ms 2026-10-24T00:30Z has passed",
    "balance_floor": "the wallet is below the balance guard",
}
ALERT_MEANING = {
    "synthetic_share_high": "A3 structure share above 0.35: recorded and alerted only, not a C1-NF halt (DEC-026 section 7 rule 6); no outcome is split by class",
    "tier_file_problem": "the TIER file failed the executor's checks; it runs T1 meanwhile",
    "feed_stale": "the shadow heartbeat is older than 150 s: every pick is refused (rule 7)",
    "boost_disabled": "BOOST regime alert (alert only for C1-NF, rule 6)",
    "boost_share_low": "BOOST regime alert (alert only for C1-NF, rule 6)",
    "boost_budget_or_slices_changed": "BOOST regime alert (alert only for C1-NF, rule 6)",
}
GUARD_INPUT_REFUSALS = ("bad_intent:missing_q_lamports", "bad_intent:missing_base_reserve")  # DEC-026 section 6 buy guard: fail closed
FEED_REFUSALS = ("feed_stale", "feed_gap")
STALE_REFUSALS = ("pick_stale", "stale_pick", "max_pick_age")
SEAL_PREFIXES = ("seal", "cap_pick")  # CAP-PICK seal refusals: a count only, never per mint (DEC-026 section 9.1)
ORACLE_DOWN_PREFIXES = ("oracle_",)  # oracle missing, stale, erroring, non-boolean or undecided: no buy
REFUSAL_ALERT_N = 3
SCHEMA_REFUSAL_ALERT_N = 5

PRIV_STAT = (WALLET_STOP, WALLET_HALT, C1NF_DIR, f"{C1NF_DIR}/STOP", f"{C1NF_DIR}/HALT", f"{C1NF_DIR}/LIVE_OK",
             STATE_FILE, COUNTERS_FILE, LEDGER_FILE, WATCH_STATE)
PRIV_READ = (STATE_FILE, COUNTERS_FILE, LEDGER_FILE, WATCH_STATE)
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
    return f"Cmnd_Alias MAL_C1NF_CHECK = {body}\n{user} ALL=(root) NOPASSWD: MAL_C1NF_CHECK\n"


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
            r = subprocess.run(["/usr/bin/systemctl", *argv], capture_output=True, text=True, errors="replace", timeout=30)
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
    """INFO / OK / ALERT lines. Class-blind: a line that would name the synthetic class (CLASS_RE, CLASS_ALLOWED aside) is never printed;
    an alert keeps its count with its text withheld, and every withheld line is counted (run_checks turns that into class_blind_withheld)."""

    def __init__(self, out: Callable[[str], None] = print):
        self.out, self.alerts, self.alert_list, self.facts, self.withheld = out, 0, [], {}, 0

    @staticmethod
    def names_class(text: str) -> bool:
        for tok in CLASS_ALLOWED:
            text = text.replace(tok, "")
        return bool(CLASS_RE.search(text))

    def _emit(self, line: str) -> None:
        if self.names_class(line):
            self.withheld += 1
            return
        self.out(line)

    def info(self, msg: str) -> None:
        self._emit(f"INFO  {msg}")

    def ok(self, msg: str) -> None:
        self._emit(f"OK    {msg}")

    def alert(self, name: str, msg: str) -> None:
        if self.names_class(name) or self.names_class(msg):
            self.withheld += 1
            name, msg = ("class_blind_alert" if self.names_class(name) else name), "text withheld by the class-blind guard"
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


CREDENTIAL_RE = re.compile(r"^LoadCredential(?:Encrypted)?[ \t]*=")
UNIT_WS = " \t\r"  # what systemd strips around a line; the text is split on LF only (check-h5-unit.py does the same)


def text_sets_credential(text: str) -> bool:
    """True if the unit text has a non-comment LoadCredential= or LoadCredentialEncrypted= line. Lines starting with `#` or `;` are comments, also
    inside a backslash continuation (systemd skips them there too). Every physical line is tested, and so is every logical line (continuations
    joined with a space), so a key split over a continuation is still seen. A hit anywhere in the text counts: the section is not tracked."""
    physical, logical, cur = [], [], ""
    for raw in text.split("\n"):
        line = raw.strip(UNIT_WS)
        if line[:1] in ("#", ";"):
            continue
        physical.append(line)
        if line.endswith("\\"):
            cur += line[:-1] + " "
            continue
        logical.append((cur + line).strip(UNIT_WS))
        cur = ""
    if cur.strip(UNIT_WS):
        logical.append(cur.strip(UNIT_WS))
    return any(CREDENTIAL_RE.match(line) for line in physical + logical)


def unit_sets_credential(host: Host, rep: Report, unit: str, load_state: str | None) -> bool | None:
    """Does the unit's EFFECTIVE unit text set a credential? `systemctl cat` prints the fragment and every drop-in systemd merges (transient and
    generator files too), which is the text systemd acts on. The `show -p LoadCredential` property is not used: systemd 255 prints
    `LoadCredential=[unprintable]` for EVERY unit (ssh.service too), so any non-empty value there is meaningless.
    A unit systemd does not have (not-found) or has masked holds no credential. If `systemctl cat` fails or prints nothing for a loaded unit the
    answer is unknown: an ALERT systemctl_failed and None, never "no credential". The text is only scanned, never echoed."""
    if load_state in ("not-found", "masked"):
        return False
    rc, out = host.systemctl("cat", unit, "--no-pager")
    if rc != 0 or not out.strip():
        rep.alert("systemctl_failed", f"`systemctl cat {unit}` failed (exit {rc}, {len(out)} bytes): whether it sets a credential cannot be told; this is not 'no credential'")
        return None
    return text_sets_credential(out)


def _json(host: Host, path: str, tail: int | None = None) -> dict | None:
    raw = host.read(path, tail) if tail else host.read(path)
    if raw is None:
        return None
    obj = json.loads(raw)
    return obj if isinstance(obj, dict) else None


def printable(name: object) -> bool:
    """A name from a file may be printed only if it looks like a name and does not name the synthetic class (CLASS_ALLOWED aside)."""
    return isinstance(name, str) and bool(re.match(NAME_RE, name)) and (name in CLASS_ALLOWED or not CLASS_RE.search(name))


def ledger_rows(raw: bytes | None):
    """Yield each ledger row reduced to LEDGER_KEYS. Nothing else of a row is ever looked at."""
    for line in (raw or b"").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            yield {k: row[k] for k in LEDGER_KEYS if k in row}


def load_unit_checker(dir_: Path):
    """check-c1nf-unit.py from the executor PR, beside this file in the pinned tree. None if it is not there (an alert in check_c1nf_unit)."""
    path = dir_ / UNIT_CHECKER
    if not path.is_file():
        return None
    spec = importlib.util.spec_from_file_location("check_c1nf_unit", path)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def credential_lines(text: str) -> list[str]:
    """The values of every non-comment LoadCredential= / LoadCredentialEncrypted= line (continuations joined). Never echoed."""
    out, cur = [], ""
    for raw in text.split("\n"):
        line = raw.strip(UNIT_WS)
        if line[:1] in ("#", ";"):
            continue
        if line.endswith("\\"):
            cur += line[:-1] + " "
            continue
        full, cur = (cur + line).strip(UNIT_WS), ""
        if CREDENTIAL_RE.match(full):
            out.append(full.split("=", 1)[1].strip(UNIT_WS))
    return out


def check_c1nf_unit(host: Host, rep: Report, checker) -> UnitInfo:
    props = sysctl_show(host, rep, f"{C1NF_UNIT}.service", "LoadState", "ActiveState", "SubState", "UnitFileState", "NRestarts", "Result",
                        "FragmentPath", "ActiveEnterTimestampMonotonic")
    if props is None:
        return NO_UNIT
    if props.get("LoadState") in (None, "not-found"):
        rep.info(f"{C1NF_UNIT} is not installed")
        return NO_UNIT
    rep.info(f"{C1NF_UNIT} active={props.get('ActiveState')}/{props.get('SubState')} enabled={props.get('UnitFileState')} "
             f"restarts={props.get('NRestarts')} result={props.get('Result')}")
    rep.facts.update(unit=C1NF_UNIT, active=props.get("ActiveState"), enabled=props.get("UnitFileState"))
    if props.get("ActiveState") == "failed":
        rep.alert("c1nf_unit_failed", f"result={props.get('Result')}; see journalctl -u {C1NF_UNIT}")
    problems = []
    if checker is None:
        problems.append(f"{UNIT_CHECKER} is not beside this script (the pinned tree is not the executor PR's): the drop-ins cannot be checked")
    if props.get("FragmentPath") != UNIT_FILE:
        problems.append(f"FragmentPath is {props.get('FragmentPath')!r}, expected {UNIT_FILE}")
    pinned_base = host.read(f"{PINNED}/{C1NF_UNIT}.service")
    if pinned_base is None:
        problems.append(f"{PINNED}/{C1NF_UNIT}.service is missing (no pinned install)")
    elif host.read(UNIT_FILE) != pinned_base:
        problems.append("installed base unit differs from the pinned copy")
    rc, out = host.systemctl("show", f"{C1NF_UNIT}.service", "-p", "DropInPaths", "--value", "--no-pager")
    if rc != 0:
        problems.append("`systemctl show -p DropInPaths` failed")
    for p in out.split():
        if p == DROPIN_LIVE:
            want, data = host.read(f"{PINNED}/{C1NF_UNIT}-live-pinned.conf"), host.read(p)
            if want is None or data != want:
                problems.append("live.conf differs from the pinned live drop-in")
            elif checker is not None and checker.problems(data, "dropin"):
                problems.append("live.conf failed the drop-in allowlist")
        elif p == DROPIN_FEED:
            if checker is not None and checker.problems(host.read(p) or b"", "shadow-feed"):
                problems.append(f"10-shadow-feed.conf failed the shadow-feed check (run {UNIT_CHECKER} --shadow-feed on it)")
        else:
            problems.append(f"unexpected drop-in {p}")
    running_live = False
    if props.get("ActiveState") in ACTIVE_STATES:
        rc, es = host.systemctl("show", f"{C1NF_UNIT}.service", "-p", "ExecStart", "--value", "--no-pager")
        if f"{PINNED_ROOT}/" not in es or "fast-forward" in es:
            problems.append("running ExecStart is not the pinned launcher")
        running_live = props.get("ActiveState") == "active" and "--live" in es
    cred_unknown = False
    if props.get("LoadState") not in ("not-found", "masked"):
        rc, text = host.systemctl("cat", f"{C1NF_UNIT}.service", "--no-pager")
        if rc != 0 or not text.strip():
            rep.alert("systemctl_failed", f"`systemctl cat {C1NF_UNIT}.service` failed (exit {rc}, {len(text)} bytes): its credential cannot be told")
            cred_unknown = True
        else:
            creds = credential_lines(text)
            if any(c != CREDENTIAL_LINE for c in creds):
                rep.alert("c1nf_wrong_credential", f"the unit text hands over a credential other than LoadCredential={CREDENTIAL_LINE} "
                                                   "(DEC-026 section 5: this unit gets the second wallet only, never the H5/probe key)")
            if running_live and not creds:
                problems.append("the unit runs with --live but its effective unit text sets no LoadCredential= (live drop-in missing or changed)")
    if problems:
        rep.alert("c1nf_unit_files", "; ".join(problems))
    elif not cred_unknown:
        rep.ok("C1-NF unit files equal the pinned copies; only the allowed drop-ins exist")
    try:
        start_us = int(props.get("ActiveEnterTimestampMonotonic", ""))
    except ValueError:
        start_us = None
    return UnitInfo(True, props.get("ActiveState", ""), props.get("UnitFileState", ""), running_live, start_us or None)


def check_live_ok_gate(host: Host, rep: Report) -> bool:
    """The facts the executor requires of LIVE_OK (DEC-026 section 5). Returns whether it is present."""
    present = host.exists(LIVE_OK)
    if host.exists(C1NF_ETC) and (host.islink(C1NF_ETC) or host.stat(C1NF_ETC) != "root:root:755"):
        rep.alert("c1nf_etc_dir", f"{C1NF_ETC} is {host.stat(C1NF_ETC)}{' (symlink)' if host.islink(C1NF_ETC) else ''}, expected root:root:755")
    if present:
        owner = host.stat(LIVE_OK) or "?:?:?"
        if host.islink(LIVE_OK) or owner != "root:root:644" or not host.is_regular(LIVE_OK):
            rep.alert("c1nf_live_ok_invalid", f"{LIVE_OK} must be a regular file owned root:root with mode exactly 0644, no symlink (is {owner})")
    if host.exists(f"{C1NF_DIR}/LIVE_OK"):
        rep.alert("c1nf_live_ok_stale", f"{C1NF_DIR}/LIVE_OK exists: the gate is {LIVE_OK}; a file in the state dir is not Helm's")
    return present


def check_tier_file(host: Host, rep: Report) -> str | None:
    if not host.exists(TIER_FILE) and not host.islink(TIER_FILE):
        rep.info(f"{TIER_FILE} is absent: the executor runs T1 (Helm writes T1 at go-live)")
        return None
    owner = host.stat(TIER_FILE) or "?:?:?"
    if host.islink(TIER_FILE) or owner != "root:root:644" or not host.is_regular(TIER_FILE):
        rep.alert("c1nf_tier_file", f"{TIER_FILE} must be a regular file owned root:root with mode exactly 0644, no symlink (is {owner}); the executor runs T1")
        return None
    text = (host.read(TIER_FILE) or b"")[:64].decode("ascii", "replace").strip()
    if text not in TIERS:
        rep.alert("c1nf_tier_file", f"{TIER_FILE} does not hold exactly T1 or T2: the executor runs T1")
        return None
    if text == "T2":
        rep.alert("c1nf_tier_t2", f"{TIER_FILE} says T2: T2 is inactive until the owner's dated line (DEC-026 section 10); confirm one exists")
    else:
        rep.ok(f"{TIER_FILE} says {text}")
    return text


def check_live_config(host: Host, rep: Report) -> None:
    """The pinned live config against DEC-026 section 6. Config may only lower a limit; the stake, the fee and end_ms must be exactly the DEC's."""
    raw = host.read(LIVE_CONFIG)
    if raw is None:
        rep.info(f"{LIVE_CONFIG} is absent (no pinned install yet)")
        return
    cfg = json.loads(raw)
    if not isinstance(cfg, dict):
        rep.alert("c1nf_live_config", f"{LIVE_CONFIG} is not a JSON object")
        return
    bad, notes = [], []
    for key, (rule, want) in CONFIG_RULES.items():
        v = cfg.get(key)
        num = isinstance(v, (int, float)) and not isinstance(v, bool)
        if rule == "eq":
            if not num or v != want:
                bad.append(f"{key} is {'absent' if v is None else 'not ' + str(want)} (DEC-026: {want})")
        elif v is None:
            notes.append(f"{key} not set (code constant)")
        elif not num or (rule == "le" and v > want) or (rule == "ge" and v < want):
            bad.append(f"{key} is {v if num else 'not a number'}; config may only {'lower it below' if rule == 'le' else 'raise it above'} {want}")
    if cfg.get("mode") != "live":
        bad.append("mode is not live")
    if cfg.get("state_dir") != C1NF_DIR:
        bad.append(f"state_dir is not {C1NF_DIR}")
    if bad:
        rep.alert("c1nf_live_config", "; ".join(bad))
    else:
        rep.ok(f"live config: stake {STAKE_LAMPORTS / LAMPORTS:.2f} SOL, priority {PRIORITY_LAMPORTS} lamports, end_ms 2026-10-24T00:30Z"
               + (f" ({'; '.join(notes)})" if notes else ""))


def effective_total_stop(funded: int | None) -> int:
    return TOTAL_STOP_CEILING_LAMPORTS if funded is None else min(TOTAL_STOP_CEILING_LAMPORTS, int(TIER_WALLET_FRAC * funded))


def check_c1nf_state(host: Host, rep: Report, funded: int | None, wallet: str, env_file: str, unit: UnitInfo, live_ok: bool,
                     balance_fn: Callable[[str, str], int] | None = None, public_rpc: bool = False) -> None:
    st_dir = host.stat(C1NF_DIR)
    if st_dir is None:
        rep.info(f"{C1NF_DIR} does not exist yet")
    elif st_dir != "mal-live:mal-live:700":
        rep.alert("c1nf_dir_mode", f"{C1NF_DIR} is {st_dir}, expected mal-live:mal-live:700")
    flags = {n: host.exists(f"{C1NF_DIR}/{n}") for n in ("STOP", "HALT")}
    flags["LIVE_OK"] = live_ok
    flags["wallet_STOP"] = host.exists(WALLET_STOP)
    flags["wallet_HALT"] = host.exists(WALLET_HALT)
    rep.info("files: " + " ".join(f"{n}={'yes' if v else 'no'}" for n, v in flags.items()))
    if flags["HALT"]:
        rep.alert("c1nf_halt_file", "HALT exists: everything is frozen, sells included")
    if flags["wallet_HALT"]:
        rep.alert("wallet_halt_file", f"{WALLET_HALT} exists: the wallet-wide HALT, which the executor honours (everything frozen, sells included)")
    state = _json(host, STATE_FILE) or {}
    counters = _json(host, COUNTERS_FILE) or {}
    opens = state.get("open") or {}
    pending = state.get("pending") or {}
    realized = int(state.get("realized_lamports", 0))
    halt_names = [n for n in sorted(counters.get("halts") or {}) if printable(n)]
    landed, late = int(counters.get("sells_landed", 0) or 0), int(counters.get("sells_late", 0) or 0)
    rep.info(f"live state: attempts={state.get('attempts', 0)} open={len(opens)}/{MAX_OPEN} pending={len(pending)} realized_sol={realized / LAMPORTS:.6f} "
             f"halts={halt_names} sells_landed={landed} sells_late={late}")
    if counters.get("halts"):
        rep.alert("c1nf_live_halt", "latched: " + (", ".join(f"{n} ({HALT_MEANING[n]})" if n in HALT_MEANING else n for n in halt_names) or "a halt")
                  + " (new buys stop; cleared only by --clear-halt, never followed by a retune)")
    if landed >= LATE_SELL_MIN and late / landed > LATE_SELL_SHARE:
        rep.alert("c1nf_late_sells", f"{late} of {landed} landed sells were late ({100 * late / landed:.1f}% > 10%; DEC-026 section 7 rule 5, report only)")
    stop = effective_total_stop(funded)
    rep.info(f"total stop: min(0.30, 0.35 x funded) = {stop / LAMPORTS:.3f} SOL; realized {realized / LAMPORTS:+.6f} uses "
             f"{max(0, -realized) / stop * 100:.1f}% of it; daily stop {DAILY_STOP_LAMPORTS / LAMPORTS:.2f} SOL")
    if (opens or pending) and not unit.running_live:
        rep.alert("c1nf_positions_unmanaged", f"live state has {len(opens)} open and {len(pending)} pending position(s) and the unit is not running with "
                                              f"--live (ActiveState={unit.active_state}): sell-and-close each, or start the live unit (docs/runbooks/c1nf-executor.md)")
    if len(opens) > MAX_OPEN:
        rep.alert("c1nf_open_cap", f"{len(opens)} open positions, above the cap of {MAX_OPEN} (DEC-026 section 6)")
    stuck = [m for m, p in opens.items() if isinstance(p, dict) and (p.get("abandoned") or p.get("stuck"))]
    if stuck:
        rep.alert("c1nf_stuck_position", f"{len(stuck)} stuck or abandoned position(s): root sell-and-close (docs/runbooks/c1nf-executor.md)")
    if wallet == H5_WALLET:
        rep.alert("c1nf_wallet_is_h5", "--wallet is the H5 wallet: the C1-NF canary has its own second wallet (DEC-026 section 5)")
    user = None
    for row in ledger_rows(host.read(LEDGER_FILE, LEDGER_TAIL)):
        if row.get("kind") == "start" and isinstance(row.get("user"), str):
            user = row["user"]
    if user is not None and wallet and user != wallet:
        rep.alert("c1nf_wallet_mismatch", f"the ledger's last start row names a different wallet than {wallet}")
    if user == H5_WALLET:
        rep.alert("c1nf_wallet_is_h5", "the ledger's last start row names the H5 wallet: the executor signs with the wrong key")
    if funded is None or not wallet:
        rep.info("wallet balance not compared: pass --wallet and --funded-sol")
        return
    try:
        bal = (balance_fn or (lambda w, e: Host().balance(w, e, public_rpc)))(wallet, env_file)
    except Exception as exc:  # noqa: BLE001 - never echo the URL
        rep.alert("balance_unavailable", f"getBalance failed ({type(exc).__name__})")
        return
    open_cost = sum(int(p.get("buy_cost_lamports") or p.get("spend") or 0) for p in opens.values() if isinstance(p, dict))
    in_flight = sum(int(p.get("spend") or 0) for p in pending.values() if isinstance(p, dict) and p.get("kind") == "buy")
    expected = funded + realized - open_cost - in_flight
    tol = BASE_TOLERANCE + RENT_TOLERANCE * (len(opens) + len(pending))
    gap = bal - expected
    line = (f"wallet {bal / LAMPORTS:.6f} SOL; funded {funded / LAMPORTS:.6f} + realized {realized / LAMPORTS:.6f} - open cost {open_cost / LAMPORTS:.6f} "
            f"- in-flight buys {in_flight / LAMPORTS:.6f} = {expected / LAMPORTS:.6f}; gap {gap / LAMPORTS:+.6f} (tolerance {tol / LAMPORTS:.6f})")
    rep.facts["wallet_line"] = f"wallet {bal / LAMPORTS:.6f} SOL, realized {realized / LAMPORTS:+.6f}, open {len(opens)}"
    if abs(gap) > tol:
        rep.alert("wallet_gap", line + " (unexplained deposit/withdrawal/loss, or a stale --funded-sol)")
    else:
        rep.ok(line)


def check_canary_idle(host: Host, rep: Report, unit: UnitInfo, live_ok: bool, shadow_dir: str, now: float) -> None:
    """A canary that is installed but not trading must not look healthy. The feed is judged by the heartbeat file's mtime only."""
    if not unit.installed:
        return
    newest = host.newest_hourly(shadow_dir)
    if newest is None:
        rep.alert("c1nf_feed_stale", f"no c1nf-events-<hour>.jsonl in {shadow_dir}: the shadow is not writing heartbeats")
    elif now - newest[1] > FEED_STALE_S:
        rep.alert("c1nf_feed_stale", f"the newest heartbeat file {newest[0]} was last written {int((now - newest[1]) // 60)} min ago (limit {FEED_STALE_S // 60})")
    else:
        rep.facts["feed_age_s"] = int(now - newest[1])
        rep.ok(f"shadow heartbeat fresh ({newest[0]}, {int(now - newest[1])} s)")
    if unit.active_state == "active" and unit.start_monotonic_us:
        birth, up = host.birth(shadow_dir), host.uptime()
        if birth is None or up is None:
            rep.info("shadow directory birth time or uptime unavailable: the unit's bind was not compared with the host directory")
        elif birth > now - up + unit.start_monotonic_us / 1e6 + BIND_SLACK_S:
            rep.alert("c1nf_feed_bind_stale", f"{shadow_dir} was created after the unit's current run started: the unit's bind of /srv/mal-c1nf-shadow "
                                              "points at nothing or at the old directory. Wind-down, restart the unit, then check with nsenter")
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
    if host.exists(f"{C1NF_DIR}/STOP"):
        why.append("STOP exists")
    if host.exists(WALLET_STOP):
        why.append(f"the wallet-wide {WALLET_STOP} exists (it stays while H5 needs it; Helm does not remove it for C1-NF's sake)")
    if now * 1000 >= END_MS:
        why.append("end_ms 2026-10-24T00:30Z has passed")
    if why:
        rep.alert("c1nf_idle", "LIVE_OK is present but the canary is not trading: " + "; ".join(why))
    t = host.mtime(LIVE_OK)
    if t is not None and now - t > IDLE_LEDGER_S:
        last = 0.0
        for row in ledger_rows(host.read(LEDGER_FILE, LEDGER_TAIL)):
            if row.get("kind") in IDLE_KINDS and isinstance(row.get("ts_ms"), (int, float)):
                last = max(last, row["ts_ms"] / 1000.0)
        if now - last > IDLE_LEDGER_S:
            rep.alert("c1nf_idle_ledger", f"LIVE_OK is {int((now - t) // 3600)} h old and the live ledger has no pick, buy, skip or decision row in the "
                                          f"last {IDLE_LEDGER_S // 3600} h: the canary sees no picks (feed, shadow or executor)")


def check_watch(host: Host, rep: Report, live_ok: bool, now: float) -> None:
    svc = sysctl_show(host, rep, WATCH_SERVICE, "LoadState", "ActiveState", "Result", "ExecMainStatus", "FragmentPath", "DropInPaths")
    tmr = sysctl_show(host, rep, WATCH_TIMER, "LoadState", "ActiveState", "UnitFileState", "FragmentPath", "DropInPaths")
    if svc is None or tmr is None:
        return
    if live_ok and (tmr.get("ActiveState") != "active" or tmr.get("UnitFileState") != "enabled"):
        rep.alert("c1nf_watch_timer", f"{WATCH_TIMER} is {tmr.get('ActiveState', 'missing')}/{tmr.get('UnitFileState', 'missing')}: the watchdog must be on while the gate is open")
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
        rep.alert("c1nf_watch_files", "; ".join(problems))
    result = svc.get("Result")
    if result not in (None, "", "success"):
        rep.alert("c1nf_watch_failing", f"{WATCH_SERVICE} Result={result} ExecMainStatus={svc.get('ExecMainStatus')}: the watchdog's last run failed")
    raw = host.read(WATCH_STATE)
    if raw is None:
        if live_ok:
            rep.alert("c1nf_watch_stale", f"{WATCH_STATE} does not exist: the watchdog has never completed a run")
        else:
            rep.info("the watchdog has not completed a run yet")
        return
    try:
        ts = float(json.loads(raw).get("ts"))
    except (ValueError, TypeError, AttributeError):
        rep.alert("c1nf_watch_stale", f"{WATCH_STATE} has no readable ts")
        return
    age = now - ts
    if age > WATCH_STATE_STALE_S:
        rep.alert("c1nf_watch_stale", f"the watchdog's last completed run was {int(age // 60)} min ago (limit {WATCH_STATE_STALE_S // 60})")
    else:
        rep.ok(f"the watchdog ran {int(age // 60)} min ago")


def check_refusals(host: Host, rep: Report, now: float, live_ok: bool, window_s: float = 6 * 3600) -> None:
    """The live ledger's last window. Counts by reason NAME only (never a mint, never an outcome, never a class)."""
    raw = host.read(LEDGER_FILE, LEDGER_TAIL)
    if not raw:
        return
    hours = max(1, int(round(window_s / 3600)))
    counts: Counter = Counter()
    alert_rows: Counter = Counter()
    decisions = withheld = 0
    changes: list[tuple] = []
    picks: list[str] = []  # pick_status of monitored picks, oldest first (whole tail, not the window: rule 8 is a rolling 30)
    for row in ledger_rows(raw):
        kind = row.get("kind")
        if kind == "pick_status" and row.get("monitored") is True and row.get("status") in ("filled", "unfilled"):
            picks.append(row["status"])
        ts = row.get("ts_ms")
        if not isinstance(ts, (int, float)) or ts / 1000.0 < now - window_s:
            continue
        if kind == "tier_change":
            changes.append((int(ts), *(row.get(k) if row.get(k) in TIERS else None for k in ("from_tier", "to_tier")),
                            row["problem"] if printable(row.get("problem")) else None))
        elif kind == "decision":
            decisions += 1
        elif kind in ("skip", "pick_status") and "reason" in row:
            if printable(row.get("reason")):
                counts[row["reason"]] += 1
            elif isinstance(row.get("reason"), str):
                withheld += 1
        elif kind == "alert":
            if printable(row.get("alert")):
                alert_rows[row["alert"]] += 1
            elif isinstance(row.get("alert"), str):
                withheld += 1
    rep.facts["tier_changes"] = changes
    for ts, old, new, problem in changes:
        rep.info(f"tier_change {old or 'none'} -> {new or '?'} at {time.strftime('%Y-%m-%dT%H:%MZ', time.gmtime(ts / 1000))}" + (f" (problem: {problem})" if problem else ""))
    seal = sum(n for r, n in counts.items() if r.startswith(SEAL_PREFIXES))
    oracle_down = sum(n for r, n in counts.items() if r.startswith(ORACLE_DOWN_PREFIXES))
    shown = [(r, n) for r, n in counts.most_common() if not r.startswith(SEAL_PREFIXES + ORACLE_DOWN_PREFIXES)]
    if shown or seal or oracle_down:
        rep.info(f"refusals in the last {hours} h ({decisions} decision(s)): " + ", ".join(f"{r} x{n}" for r, n in shown[:8])
                 + (f"; CAP-PICK seal x{seal}" if seal else "") + (f"; oracle unavailable x{oracle_down}" if oracle_down else ""))
    if withheld:
        rep.info(f"{withheld} refusal or alert row(s) whose name is not printable were counted and not shown")
    for reason, meaning in BUDGET_STOPS.items():
        if counts[reason]:
            rep.alert(f"c1nf_budget_stop_{reason}", f"the executor refused {counts[reason]} pick(s) in the last {hours} h on {reason} ({meaning}): "
                                                   "new buys are stopped until it clears; open positions still exit")
    for what, n in alert_rows.most_common():
        rep.alert(f"c1nf_executor_alert_{what}", f"the executor wrote {n} ALERT {what} row(s) in the last {hours} h"
                  + (f" ({ALERT_MEANING[what]})" if what in ALERT_MEANING else "") + f" (see the ledger and journalctl -u {C1NF_UNIT})")
    guard = sum(counts[r] for r in GUARD_INPUT_REFUSALS) + sum(n for r, n in counts.items() if r.startswith("bad_intent:missing_") and r not in GUARD_INPUT_REFUSALS)
    if guard >= SCHEMA_REFUSAL_ALERT_N and decisions == 0:
        rep.alert("c1nf_feed_schema", f"{guard} pick(s) refused for missing fields in {hours} h and none acted on: the shadow's picks lack the decision-time "
                                      "q_lamports and base_reserve the buy guard needs (DEC-026 section 6: no buy without them)")
    feed = sum(counts[r] for r in FEED_REFUSALS)
    if feed >= REFUSAL_ALERT_N:
        rep.alert("c1nf_feed_refusals", f"{feed} pick(s) refused as feed_stale or feed_gap in {hours} h (DEC-026 section 7 rule 7)")
    stale = sum(counts[r] for r in STALE_REFUSALS)
    if stale >= REFUSAL_ALERT_N:
        rep.alert("c1nf_stale_picks", f"{stale} pick(s) older than 3 s of chain age refused in {hours} h: the shadow is late")
    if oracle_down and live_ok and now >= SEAL_START_S and decisions == 0:
        rep.alert("c1nf_oracle_unavailable", f"{oracle_down} pick(s) refused in {hours} h because the CAP-PICK oracle is missing, stale or undecided, and none "
                                             "was acted on: the canary is paused by the seal (DEC-026 section 9.1, #509)")
    last = picks[-FILL_RATE_WINDOW:]
    if len(last) >= FILL_RATE_WINDOW:
        unfilled = last.count("unfilled")
        if unfilled / len(last) > FILL_RATE_ALERT:
            rep.alert("c1nf_fill_rate", f"{unfilled} of the last {len(last)} monitored picks did not fill (> 28.9%; DEC-026 section 7 rule 8, alert only)")
        else:
            rep.info(f"fill rate: {len(last) - unfilled} of the last {len(last)} monitored picks filled")


def run_checks(args: argparse.Namespace, host: Host, out: Callable[[str], None] = print,
               balance_fn: Callable[[str, str], int] | None = None, now: float | None = None, checker=None) -> Report:
    rep = Report(out)
    now = time.time() if now is None else now
    if checker is None:
        checker = load_unit_checker(Path(__file__).resolve().parent)
    funded = None if args.funded_sol is None else int(round(args.funded_sol * LAMPORTS))
    window_s = float(getattr(args, "window_hours", 6.0)) * 3600
    if not host.sudo_ok():
        rep.alert("sudo_unavailable", "sudo -n /usr/bin/true failed: the files in /var/lib/mal-live cannot be read, so the checks that need them cannot pass")
    ctx: dict = {"unit": NO_UNIT, "live_ok": False}

    def unit_step() -> None:
        ctx["unit"] = check_c1nf_unit(host, rep, checker)

    def gate_step() -> None:
        ctx["live_ok"] = check_live_ok_gate(host, rep)
        rep.facts["tier"] = check_tier_file(host, rep) or "T1"

    steps = [("c1nf_unit", unit_step), ("c1nf_gate", gate_step),
             ("c1nf_config", lambda: check_live_config(host, rep)),
             ("c1nf_state", lambda: check_c1nf_state(host, rep, funded, args.wallet, args.rpc_env, ctx["unit"], ctx["live_ok"], balance_fn, args.public_rpc)),
             ("c1nf_idle", lambda: check_canary_idle(host, rep, ctx["unit"], ctx["live_ok"], args.shadow_dir, now)),
             ("c1nf_watch", lambda: check_watch(host, rep, ctx["live_ok"], now)),
             ("c1nf_refusals", lambda: check_refusals(host, rep, now, ctx["live_ok"], window_s))]
    for name, step in steps:
        try:
            step()
        except SudoError as exc:
            rep.alert("sudo_failed", f"{name}: {exc} (a failing privileged read is not 'absent')")
        except Unsafe as exc:
            rep.alert("unsafe_path", f"{name}: {exc}")
        except Exception as exc:  # noqa: BLE001 - a check that cannot run is an alert, never a silent pass
            rep.alert("check_failed", f"{name}: {type(exc).__name__}")
    if rep.withheld:
        rep.alert("class_blind_withheld", f"{rep.withheld} line(s) were withheld because they named the class (EXP-025 Amendment 2 item 5); fix the check")
    return rep


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--funded-sol", type=float, default=None, help="total SOL the owner has deposited into the second wallet (net of withdrawals)")
    ap.add_argument("--wallet", default=WALLET, help="the C1-NF wallet's PUBLIC address (Helm gives it); never the H5 wallet")
    ap.add_argument("--rpc-env", default="/var/lib/mal/fast-listener/helius.env", help="read-only getBalance only; HELIUS_API_KEY in the environment wins")
    ap.add_argument("--public-rpc", action="store_true", help=f"getBalance through {PUBLIC_RPC}: no key at all")
    ap.add_argument("--shadow-dir", default=str(Path.home() / "data/c1nf-shadow"), help="the C1-NF shadow's output directory (heartbeat files only)")
    ap.add_argument("--window-hours", type=float, default=6.0, help="how far back the ledger is read for stops, alerts and refusals (the daily job uses 24)")
    ap.add_argument("--print-sudoers", action="store_true", help="print the exact sudoers lines the privileged calls need and exit")
    ap.add_argument("--sudoers-user", default="claude")
    return ap


def main(argv: list[str] | None = None, host: Host | None = None, out: Callable[[str], None] = print,
         balance_fn: Callable[[str, str], int] | None = None, now: float | None = None, checker=None) -> int:
    args = build_parser().parse_args(argv)
    if args.print_sudoers:
        out(sudoers_text(args.sudoers_user).rstrip("\n"))
        return 0
    rep = run_checks(args, host or Host(), out, balance_fn, now, checker)
    out(f"c1nf_daily_check ALERTS={rep.alerts}")
    return 1 if rep.alerts else 0


if __name__ == "__main__":
    sys.exit(main())
