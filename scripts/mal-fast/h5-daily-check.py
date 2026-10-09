#!/usr/bin/env python3
"""Daily health check for the H5 live executor (DEC-024), for the manager's 12:17Z cron. Replaces the decommissioned-probe check.

    python3 -I scripts/mal-fast/h5-daily-check.py --funded-sol 0.25 [--write-baseline]

Read-only, no key: it never opens /etc/mal-probe, never loads a wallet, never sends anything. Root-only files (the 0700 state
dir /var/lib/mal-live) are read with `sudo -n /usr/bin/cat|stat|test` on fixed paths. Prints INFO / OK / ALERT lines and exits 1
if there is any ALERT (0 otherwise). The RPC URL (HELIUS_API_KEY from the environment or the paper env file) is never printed.

What changed from the probe check, and what did not:
  ALLOWED now   the H5 unit mal-h5-executor (active or enabled), its state dir /var/lib/mal-live/h5, the pinned tree
                /usr/local/lib/mal-h5-exec, /etc/mal-h5/LIVE_OK. The wallet is no longer expected to be 0.
  STILL ALERTS  any mal-probe-executor* unit that is active or enabled (masked is fine); /var/lib/mal-live/state-live.json
                changed (sha256 against a baseline written once with --write-baseline, plus the old fixed facts: attempts <= 62,
                realized -0.210755 SOL, nothing open or pending); /var/lib/mal-live/STOP missing.
  NEW           the H5 unit's installed files equal the pinned copies (base unit, live.conf; only 10-shadow-feed.conf may be
                extra and it must pass check-h5-unit.py --shadow-feed); ActiveState failed; HALT file; latched live halts;
                an abandoned or stuck position; and the wallet balance against the funded amount plus the H5 realized total.

Wallet check: expected = funded + realized (H5 live state file, the number the loss stops use) - cost of open positions. The gap
must be within a tolerance for rent and in-flight fees. A deposit or a withdrawal shows up as a gap: pass the new --funded-sol.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Callable

WALLET = "5n95HyhZqjZNkjdp44QGJoAqk4ZFjDgMKuUzWcQqSugk"  # the DEC-019 probe wallet; public (DEC-024)
PROBE_STATE = "/var/lib/mal-live/state-live.json"
PROBE_STOP = "/var/lib/mal-live/STOP"
PROBE_MAX_ATTEMPTS = 62
PROBE_REALIZED_SOL = -0.210755
H5_UNIT = "mal-h5-executor"
H5_DIR = "/var/lib/mal-live/h5"
H5_ETC = "/etc/mal-h5"  # root:root 0755; holds LIVE_OK, which Helm creates (the executor cannot)
LIVE_OK = f"{H5_ETC}/LIVE_OK"
PINNED = "/usr/local/lib/mal-h5-exec/current"
DROPIN_DIRS = ("/etc/systemd/system", "/run/systemd/system", "/usr/local/lib/systemd/system", "/usr/lib/systemd/system", "/lib/systemd/system")
RENT_TOLERANCE = 2_100_000  # per open or pending position (token account rent, refunded at close)
BASE_TOLERANCE = 5_000_000  # fees in flight, dust
ACTIVE_STATES = {"active", "activating", "reloading", "deactivating"}
ENABLED_STATES = {"enabled", "enabled-runtime", "linked", "linked-runtime", "alias"}


class Host:
    """Everything that touches the machine. Tests replace it."""

    def _sudo(self, *argv: str) -> subprocess.CompletedProcess:
        return subprocess.run(["sudo", "-n", *argv], capture_output=True, timeout=30)

    def read(self, path: str) -> bytes | None:
        """File bytes, or None if it does not exist. PermissionError falls back to a fixed-path sudo cat."""
        try:
            return Path(path).read_bytes()
        except FileNotFoundError:
            return None
        except PermissionError:
            r = self._sudo("/usr/bin/cat", path)
            if r.returncode == 0:
                return r.stdout
            if self._sudo("/usr/bin/test", "-e", path).returncode != 0:
                return None
            raise PermissionError(path) from None

    def exists(self, path: str) -> bool:
        return os.path.lexists(path) or self._sudo("/usr/bin/test", "-e", path).returncode == 0

    def islink(self, path: str) -> bool:
        return os.path.islink(path)

    def is_regular(self, path: str) -> bool:
        return os.path.isfile(path) and not os.path.islink(path)

    def stat(self, path: str) -> str | None:
        """owner:group:mode of a path, or None."""
        r = subprocess.run(["/usr/bin/stat", "-c", "%U:%G:%a", path], capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            r = self._sudo("/usr/bin/stat", "-c", "%U:%G:%a", path)
            r = subprocess.CompletedProcess(r.args, r.returncode, (r.stdout or b"").decode(), "")
        return r.stdout.strip() if r.returncode == 0 else None

    def listdir(self, path: str) -> list[str] | None:
        try:
            return sorted(os.listdir(path))
        except (FileNotFoundError, NotADirectoryError):
            return None

    def systemctl(self, *argv: str) -> tuple[int, str]:
        r = subprocess.run(["systemctl", *argv], capture_output=True, text=True, timeout=30)
        return r.returncode, r.stdout

    def balance(self, wallet: str, env_file: str) -> int:
        key = (os.environ.get("HELIUS_API_KEY") or "").strip()
        if not key and Path(env_file).exists():
            for line in Path(env_file).read_text().splitlines():
                line = line.strip().removeprefix("export ")
                if line.startswith("HELIUS_API_KEY="):
                    key = line.split("=", 1)[1].strip().strip("\"'")
        if not key:
            raise RuntimeError("no HELIUS_API_KEY")
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "getBalance", "params": [wallet, {"commitment": "confirmed"}]}).encode()
        req = urllib.request.Request(f"https://mainnet.helius-rpc.com/?api-key={key}", body, {"Content-Type": "application/json"})
        return int(json.load(urllib.request.urlopen(req, timeout=20))["result"]["value"])  # type: ignore[no-any-return]


class Report:
    def __init__(self, out: Callable[[str], None] = print):
        self.out, self.alerts = out, 0

    def info(self, msg: str) -> None:
        self.out(f"INFO  {msg}")

    def ok(self, msg: str) -> None:
        self.out(f"OK    {msg}")

    def alert(self, name: str, msg: str) -> None:
        self.alerts += 1
        self.out(f"ALERT {name}: {msg}")


def _json(host: Host, path: str) -> dict | None:
    raw = host.read(path)
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
        rep.alert("probe_unit", "; ".join(bad) + " (two processes must never share the key; stop, disable and mask it)")
    else:
        rep.ok("no mal-probe-executor unit is active or enabled")


def check_probe_state(host: Host, rep: Report, baseline_path: Path, write_baseline: bool) -> None:
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
        baseline_path.parent.mkdir(parents=True, exist_ok=True)
        baseline_path.write_text(json.dumps({"sha256": digest}) + "\n")
        rep.info(f"probe state baseline written ({digest[:12]})")
    elif not baseline_path.exists():
        problems.append(f"no baseline at {baseline_path}: run once with --write-baseline, or changes cannot be detected")
    else:
        if json.loads(baseline_path.read_text()).get("sha256") != digest:
            problems.append("sha256 differs from the baseline: the probe's state file changed")
    if not host.exists(PROBE_STOP):
        problems.append(f"{PROBE_STOP} is missing")
    if problems:
        rep.alert("probe_state", "; ".join(problems))
    else:
        rep.ok(f"probe state unchanged (attempts {st.get('attempts')}, realized -0.210755 SOL, STOP in place)")


def check_h5_unit(host: Host, rep: Report, checker) -> bool:
    """Returns True if the unit is installed. Alerts if what is installed differs from the pinned copies."""
    rc, out = host.systemctl("show", H5_UNIT, "-p", "LoadState,ActiveState,SubState,UnitFileState,NRestarts,Result", "--no-pager")
    props = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
    if props.get("LoadState") in (None, "not-found"):
        rep.info(f"{H5_UNIT} is not installed")
        return False
    rep.info(f"{H5_UNIT} active={props.get('ActiveState')}/{props.get('SubState')} enabled={props.get('UnitFileState')} "
             f"restarts={props.get('NRestarts')} result={props.get('Result')}")
    if props.get("ActiveState") == "failed":
        rep.alert("h5_unit_failed", f"result={props.get('Result')}; see journalctl -u {H5_UNIT}")
    problems = []
    base = host.read(f"/etc/systemd/system/{H5_UNIT}.service")
    pinned_base = host.read(f"{PINNED}/mal-h5-executor.service")
    if pinned_base is None:
        problems.append(f"{PINNED}/mal-h5-executor.service is missing (no pinned install)")
    elif base != pinned_base:
        problems.append("installed base unit differs from the pinned copy")
    for d in DROPIN_DIRS:
        names = host.listdir(f"{d}/{H5_UNIT}.service.d")
        for n in names or []:
            p = f"{d}/{H5_UNIT}.service.d/{n}"
            data = host.read(p) or b""
            if d == "/etc/systemd/system" and n == "live.conf":
                want = host.read(f"{PINNED}/mal-h5-executor-live-pinned.conf")
                if want is None or data != want:
                    problems.append("live.conf differs from the pinned live drop-in")
                elif checker.problems(data, "dropin"):
                    problems.append("live.conf failed the drop-in allowlist")
            elif d == "/etc/systemd/system" and n == "10-shadow-feed.conf":
                bad = checker.problems(data, "shadow-feed")
                if bad:
                    problems.append("10-shadow-feed.conf failed the shadow-feed check: " + bad[0])
            else:
                problems.append(f"unexpected drop-in {p}")
    if props.get("ActiveState") in ACTIVE_STATES:
        rc, out = host.systemctl("show", H5_UNIT, "-p", "ExecStart", "--value", "--no-pager")
        if "/usr/local/lib/mal-h5-exec/" not in out or "fast-forward" in out:
            problems.append("running ExecStart is not the pinned launcher")
    if problems:
        rep.alert("h5_unit_files", "; ".join(problems))
    else:
        rep.ok("H5 unit files equal the pinned copies; only the allowed drop-ins exist")
    return True


def check_h5_state(host: Host, rep: Report, funded: int | None, wallet: str, env_file: str,
                   balance_fn: Callable[[str, str], int] | None = None) -> None:
    st_dir = host.stat(H5_DIR)
    if st_dir is None:
        rep.info(f"{H5_DIR} does not exist yet")
    elif st_dir != "mal-live:mal-live:700":
        rep.alert("h5_dir_mode", f"{H5_DIR} is {st_dir}, expected mal-live:mal-live:700")
    flags = {n: host.exists(f"{H5_DIR}/{n}") for n in ("STOP", "HALT")}
    flags["LIVE_OK"] = host.exists(LIVE_OK) or host.islink(LIVE_OK)
    rep.info("files: " + " ".join(f"{n}={'yes' if v else 'no'}" for n, v in flags.items()))
    if flags["HALT"]:
        rep.alert("h5_halt_file", "HALT exists: everything is frozen, sells included")
    # LIVE_OK is the root-owned gate the executor cannot create (DEC-024 section 3): same facts the executor requires of it.
    if host.exists(H5_ETC) or host.islink(H5_ETC):
        if host.islink(H5_ETC) or host.stat(H5_ETC) != "root:root:755":
            rep.alert("h5_etc_dir", f"{H5_ETC} is {host.stat(H5_ETC)}{' (symlink)' if host.islink(H5_ETC) else ''}, expected root:root:755")
    if flags["LIVE_OK"]:
        owner = host.stat(LIVE_OK) or "?:?:?"
        mode = owner.rsplit(":", 1)[-1]
        if host.islink(LIVE_OK) or not owner.startswith("root:") or not mode.isdigit() or int(mode, 8) & 0o022 or not host.is_regular(LIVE_OK):
            rep.alert("h5_live_ok_invalid", f"{LIVE_OK} must be a regular file, root-owned, not group/other writable, no symlink (is {owner})")
    if host.exists(f"{H5_DIR}/LIVE_OK"):
        rep.alert("h5_live_ok_stale", f"{H5_DIR}/LIVE_OK exists: the gate is {LIVE_OK} now; a file in the state dir is not Helm's (remove it, find out who made it)")
    state = _json(host, f"{H5_DIR}/live/state-live.json") or {}
    counters = _json(host, f"{H5_DIR}/live/h5-counters.json") or {}
    opens = state.get("open") or {}
    pending = state.get("pending") or {}
    realized = int(state.get("realized_lamports", 0))
    rep.info(f"live state: attempts={state.get('attempts', 0)} open={len(opens)} pending={len(pending)} realized_sol={realized / 1e9:.6f} "
             f"halts={sorted(counters.get('halts') or {})} sells_landed={counters.get('sells_landed', 0)} sells_late={counters.get('sells_late', 0)}")
    if counters.get("halts"):
        rep.alert("h5_live_halt", "latched: " + ",".join(sorted(counters["halts"])) + " (cleared only by --clear-halt)")
    stuck = [m for m, p in opens.items() if p.get("abandoned") or p.get("stuck")]
    if stuck:
        rep.alert("h5_stuck_position", f"{len(stuck)} stuck or abandoned position(s): root sell-and-close (docs/runbooks/h5-executor.md)")
    ledger = host.read(f"{H5_DIR}/live/h5-ledger.jsonl")
    user = None
    for line in (ledger or b"")[-4_000_000:].splitlines():
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
        bal = (balance_fn or Host().balance)(wallet, env_file)
    except Exception as exc:  # noqa: BLE001 - never echo the URL
        rep.alert("balance_unavailable", f"getBalance failed ({type(exc).__name__})")
        return
    open_cost = sum(int(p.get("buy_cost_lamports") or p.get("spend") or 0) for p in opens.values())
    expected = funded + realized - open_cost
    tol = BASE_TOLERANCE + RENT_TOLERANCE * (len(opens) + len(pending))
    gap = bal - expected
    line = (f"wallet {bal / 1e9:.6f} SOL; funded {funded / 1e9:.6f} + realized {realized / 1e9:.6f} - open cost {open_cost / 1e9:.6f} "
            f"= {expected / 1e9:.6f}; gap {gap / 1e9:+.6f} (tolerance {tol / 1e9:.6f})")
    if abs(gap) > tol:
        rep.alert("wallet_gap", line + " (unexplained deposit/withdrawal/loss, or a stale --funded-sol)")
    else:
        rep.ok(line)


def main(argv: list[str] | None = None, host: Host | None = None, out: Callable[[str], None] = print,
         balance_fn: Callable[[str, str], int] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--funded-sol", type=float, default=None, help="total SOL the owner has deposited (net of withdrawals)")
    ap.add_argument("--wallet", default=WALLET)
    ap.add_argument("--rpc-env", default="/var/lib/mal/fast-listener/helius.env", help="read-only getBalance only; HELIUS_API_KEY in the environment wins")
    ap.add_argument("--baseline", default=str(Path.home() / "data/h5-daily/probe-state.baseline.json"))
    ap.add_argument("--write-baseline", action="store_true", help="record the probe state's sha256 (once, at install time)")
    args = ap.parse_args(argv)
    host = host or Host()
    rep = Report(out)
    checker = load_unit_checker(Path(__file__).resolve().parent)
    funded = None if args.funded_sol is None else int(round(args.funded_sol * 1e9))
    steps = (lambda: check_probe_units(host, rep),
             lambda: check_probe_state(host, rep, Path(args.baseline), args.write_baseline),
             lambda: check_h5_unit(host, rep, checker),
             lambda: check_h5_state(host, rep, funded, args.wallet, args.rpc_env, balance_fn))
    for step in steps:
        try:
            step()
        except Exception as exc:  # noqa: BLE001 - a check that cannot run is an alert, never a silent pass
            rep.alert("check_failed", f"{type(exc).__name__}")
    out(f"h5_daily_check ALERTS={rep.alerts}")
    return 1 if rep.alerts else 0


if __name__ == "__main__":
    sys.exit(main())
