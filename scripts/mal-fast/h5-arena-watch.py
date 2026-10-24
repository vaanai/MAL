#!/usr/bin/env python3
"""H5 ARENA canary watchdog (DEC-032 s5 halts 4/6, s8 item 7; the H5 runbook's "going live does not happen without the watchdog", for the arena unit).

KEYLESS and read-only. Runs every 5 minutes from mal-h5-arena-watch.timer as a hardened root oneshot (mal-h5-arena-watch.service), from the root-owned
pinned arena tree, as `python3 -I -S`. Stdlib only. It never touches the key (its paths are InaccessiblePaths in the unit), the executor, its state or
any control file: it reads (O_NOFOLLOW, size-capped) and posts to Discord. Posts when an alert is NEW, again every 6 h while it stays, RESOLVED when it
clears, and an EVENT when STOP is placed or removed. A check that cannot run is an alert, never a pass.

Checks (alert names):
  watch_config            the env file is missing a value or a value is invalid
  systemctl_failed        systemd did not answer
  unit_failed             mal-h5-arena-executor ActiveState is failed
  unit_restarted          NRestarts went up since the last run
  positions_unmanaged     the unit is not active while the live state holds an open or pending position
  gate_open_unit_idle     /etc/mal-h5-arena/LIVE_OK exists, the live drop-in is installed, and the unit is not active
  halt_file               HALT in /var/lib/mal-h5-arena or the wallet-wide /var/lib/mal-live (sells are frozen)
  position_stuck          an open position flagged stuck, abandoned, strand or close_blocked (the sell-and-close runbook applies)
  halts_latched           the live counters hold a latched halt (names listed)
  state_unreadable        the live state or counters exist but cannot be read
  feed_stale              the newest h5-shadow hourly file in the feed dir is older than FEED_STALE_S (or there is none)
  twin_record_stale       live drop-in installed and the unit active: the twin writer's run record is older than TWIN_STALE_S or missing
  tieout_missing          live and a send has happened: after 02:00Z the previous UTC day has no tieout-<day>.json in the tie-out dir
  wallet_below_floor      (only when ARENA_WATCH_WALLET is set) the public balance is below max(0.05, funded - 0.25) SOL
  wallet_unreadable       (idem) the public RPC getBalance failed

Config: systemd EnvironmentFile /etc/mal-h5-arena-watch/watch.env (root:root 0600 in a root:root 0700 dir):
  ARENA_WATCH_DISCORD_WEBHOOK  a secret: never printed, removed from os.environ as soon as it is read
  ARENA_WATCH_FEED_DIR         the book's arena shadow dir (host path, the same as the unit's feed bind source)
  ARENA_WATCH_TWINS_FILE       the twin writer's twins.jsonl (host path); its run record is <file>.run.json
  ARENA_WATCH_TIEOUT_DIR       the tie-out RESULTS_DIR (host path)
  ARENA_WATCH_WALLET           optional: the canary wallet's PUBLIC address
  ARENA_WATCH_FUNDED_SOL       required with ARENA_WATCH_WALLET: SOL funded, net of withdrawals

    h5-arena-watch.py                run the checks and post what changed
    h5-arena-watch.py --test-message post one test line and exit (Helm runs it once when enabling the timer)
"""
from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

UNIT = "mal-h5-arena-executor"
STATE_DIR = Path("/var/lib/mal-h5-arena")
WALLET_DIR = Path("/var/lib/mal-live")
ETC = Path("/etc/mal-h5-arena")
LIVE_DROPIN = Path("/etc/systemd/system/mal-h5-arena-executor.service.d/live.conf")
REPEAT_S = 6 * 3600
FEED_STALE_S = 300
TWIN_STALE_S = 120
TIEOUT_AFTER_S = 2 * 3600  # 02:00Z: the daily job runs after 00:30Z
MAX_READ = 8 * 1024 * 1024
LAMPORTS = 1_000_000_000
FLOOR_MIN_SOL, FLOOR_BELOW_FUNDED_SOL = 0.05, 0.25
PUBLIC_RPC = "https://api.mainnet-beta.solana.com"
WEBHOOK_VAR = "ARENA_WATCH_DISCORD_WEBHOOK"
WEBHOOK_RE = re.compile(r"^https://(?:discord|discordapp)\.com/api/webhooks/[0-9]+/[A-Za-z0-9_-]+$")
BASE58_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
FEED_RE = re.compile(r"^h5-shadow-\d{4}-\d{2}-\d{2}T\d{2}\.jsonl$")
MAX_CONTENT = 1900
POST_PREFIX = "[H5 arena watch] "
STUCK_FLAGS = ("stuck", "abandoned", "strand", "close_blocked")


class Host:
    """Everything the checks read from the machine. Tests replace it."""

    def systemctl(self, *args: str) -> tuple[int, str]:
        r = subprocess.run(["/usr/bin/systemctl", *args], capture_output=True, text=True, timeout=20, env={"PATH": "/usr/bin:/bin"})
        return r.returncode, r.stdout

    def read(self, p: Path) -> bytes | None:
        """None when absent. Raises OSError for a symlink, a non-regular file or an oversize file (the caller turns that into an alert)."""
        try:
            fd = os.open(p, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        except FileNotFoundError:
            return None
        with os.fdopen(fd, "rb") as fh:
            st = os.fstat(fh.fileno())
            if not stat.S_ISREG(st.st_mode) or st.st_size > MAX_READ:
                raise OSError("not a regular file or too large")
            return fh.read(MAX_READ)

    def exists(self, p: Path) -> bool:
        return os.path.lexists(p)

    def listdir(self, p: Path) -> list[tuple[str, float]]:
        out = []
        with os.scandir(p) as it:
            for e in it:
                if e.is_file(follow_symlinks=False):
                    out.append((e.name, e.stat(follow_symlinks=False).st_mtime))
        return out

    def balance(self, wallet: str) -> int:
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "getBalance", "params": [wallet, {"commitment": "finalized"}]}).encode()
        req = urllib.request.Request(PUBLIC_RPC, body, {"Content-Type": "application/json", "User-Agent": "mal-h5-arena-watch/1"})
        got = json.load(urllib.request.urlopen(req, timeout=20))
        return int(got["result"]["value"])


def _json(host: Host, p: Path) -> Any:
    raw = host.read(p)
    return None if raw is None else json.loads(raw)


def config(env: dict[str, str]) -> tuple[dict[str, Any] | None, str | None]:
    try:
        cfg = {"feed": Path(env["ARENA_WATCH_FEED_DIR"]), "twins": Path(env["ARENA_WATCH_TWINS_FILE"]), "tieout": Path(env["ARENA_WATCH_TIEOUT_DIR"])}
    except KeyError as exc:
        return None, f"{exc.args[0]} is missing from /etc/mal-h5-arena-watch/watch.env"
    for k, v in cfg.items():
        if not v.is_absolute() or ".." in v.parts:
            return None, f"{k} path must be absolute without '..'"
    w = env.get("ARENA_WATCH_WALLET", "")
    cfg["wallet"] = None
    if w:
        if not BASE58_RE.match(w):
            return None, "ARENA_WATCH_WALLET is not a base58 public address"
        try:
            funded = float(env["ARENA_WATCH_FUNDED_SOL"])
        except (KeyError, ValueError):
            return None, "ARENA_WATCH_FUNDED_SOL is required with ARENA_WATCH_WALLET and must be a number"
        if not 0 < funded <= 10:
            return None, "ARENA_WATCH_FUNDED_SOL out of range"
        cfg["wallet"], cfg["funded"] = w, funded
    return cfg, None


def collect(host: Host, env: dict[str, str], now: float) -> tuple[dict[str, str], dict[str, Any]]:
    """(alerts by name, observations). Never raises."""
    alerts: dict[str, str] = {}
    obs: dict[str, Any] = {"active": "unknown", "restarts": None, "stop": None, "live": None}
    cfg, why = config(env)
    if cfg is None:
        return {"watch_config": why or "invalid"}, obs
    try:
        rc, out = host.systemctl("show", UNIT, "-p", "ActiveState", "-p", "NRestarts", "--no-pager")
        kv = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
        if rc != 0 or "ActiveState" not in kv:
            raise RuntimeError("no answer")
        obs["active"] = kv["ActiveState"]
        obs["restarts"] = int(kv.get("NRestarts", "x")) if kv.get("NRestarts", "").isdigit() else None
    except Exception:  # noqa: BLE001
        alerts["systemctl_failed"] = f"systemctl show {UNIT} did not answer"
    active = obs["active"] == "active"
    if obs["active"] == "failed":
        alerts["unit_failed"] = f"{UNIT} is failed: look at the journal (exit 2 = a start refusal, its reason is printed)"
    live = host.exists(LIVE_DROPIN)
    obs["live"] = live
    obs["stop"] = host.exists(STATE_DIR / "STOP")
    halts = [str(p) for p in (STATE_DIR / "HALT", WALLET_DIR / "HALT") if host.exists(p)]
    if halts:
        alerts["halt_file"] = "HALT present (sells are frozen): " + ", ".join(halts)
    if live and host.exists(ETC / "LIVE_OK") and not active and obs["active"] != "unknown":
        alerts["gate_open_unit_idle"] = f"/etc/mal-h5-arena/LIVE_OK and the live drop-in exist but {UNIT} is {obs['active']}"
    st = None
    try:
        st = _json(host, STATE_DIR / "live" / "state-live.json")
        counters = _json(host, STATE_DIR / "live" / "h5-counters.json")
    except (OSError, ValueError):
        alerts["state_unreadable"] = "the live state or counters cannot be read (symlink, not regular, oversize or not JSON)"
        counters = None
    if isinstance(st, dict):
        open_, pending = st.get("open") or {}, st.get("pending") or {}
        if (open_ or pending) and not active:
            alerts["positions_unmanaged"] = f"{len(open_)} open and {len(pending)} pending position(s) while {UNIT} is {obs['active']}"
        stuck = [f"{m[:8]}({','.join(f for f in STUCK_FLAGS if p.get(f))})" for m, p in open_.items() if isinstance(p, dict) and any(p.get(f) for f in STUCK_FLAGS)]
        if stuck:
            alerts["position_stuck"] = "open position(s) that cannot exit: " + " ".join(stuck) + " (sell-and-close runbook; the executor is stopped first)"
        first = st.get("first_attempt_ms")
        if live and isinstance(first, int):
            day0 = int(now // 86400 * 86400)
            if now - day0 >= TIEOUT_AFTER_S and first < day0 * 1000:
                prev = datetime.fromtimestamp(day0 - 86400, timezone.utc).strftime("%Y-%m-%d")
                if not host.exists(cfg["tieout"] / f"tieout-{prev}.json"):
                    alerts["tieout_missing"] = f"no tieout-{prev}.json in the tie-out dir after 02:00Z: buys are refused until it exists"
    if isinstance(counters, dict) and counters.get("halts"):
        alerts["halts_latched"] = "latched: " + ", ".join(sorted(counters["halts"]))
    try:
        files = [mt for name, mt in host.listdir(cfg["feed"]) if FEED_RE.match(name)]
        age = now - max(files) if files else None
    except OSError:
        age = None
    obs["feed_age_s"] = None if age is None else int(age)
    if age is None or age > FEED_STALE_S:
        alerts["feed_stale"] = "no hourly feed file" if age is None else f"the newest hourly feed file is {int(age)} s old (> {FEED_STALE_S})"
    if live and active:
        try:
            rec = _json(host, Path(str(cfg["twins"]) + ".run.json"))
            run_ms = rec.get("run_ms") if isinstance(rec, dict) else None
            ok = isinstance(run_ms, int) and now * 1000 - run_ms <= TWIN_STALE_S * 1000
        except (OSError, ValueError):
            ok = False
        if not ok:
            alerts["twin_record_stale"] = f"the twin writer's run record is missing, unreadable or older than {TWIN_STALE_S} s (buys refused by P7)"
    if cfg.get("wallet"):
        floor = max(FLOOR_MIN_SOL, cfg["funded"] - FLOOR_BELOW_FUNDED_SOL)
        try:
            lam = host.balance(cfg["wallet"])
            obs["balance_sol"] = lam / LAMPORTS
            if lam < floor * LAMPORTS:
                alerts["wallet_below_floor"] = f"balance {lam / LAMPORTS:.6f} SOL < floor {floor:.6f} SOL (funded {cfg['funded']})"
        except Exception as exc:  # noqa: BLE001
            alerts["wallet_unreadable"] = f"public getBalance failed ({type(exc).__name__})"
    return alerts, obs


def decide(alerts: dict[str, str], obs: dict[str, Any], state: dict[str, Any], now: float) -> tuple[list[str], dict[str, Any]]:
    active = dict(state.get("active") or {})
    prev_r, r = state.get("restarts"), obs.get("restarts")
    if isinstance(prev_r, int) and isinstance(r, int) and r > prev_r:
        alerts = {**alerts, "unit_restarted": f"{UNIT} restarted {r - prev_r} time(s) since the last run (NRestarts {prev_r} -> {r})"}
    lines: list[str] = []
    new_active: dict[str, dict[str, float]] = {}
    for name, msg in sorted(alerts.items()):
        prev = active.get(name)
        if prev is None or now - prev.get("last_post", 0) >= REPEAT_S:
            lines.append(f"ALERT {name}: {msg}")
            new_active[name] = {"first": (prev or {}).get("first", now), "last_post": now}
        else:
            new_active[name] = prev
    lines += [f"RESOLVED {n}" for n in sorted(active) if n not in alerts]
    prev_stop, stop = state.get("stop"), obs.get("stop")
    if stop is True and prev_stop is False:
        lines.append("EVENT STOP placed: new buys are stopped (open positions still exit)")
    elif stop is False and prev_stop is True:
        lines.append("EVENT STOP removed")
    return lines, {"active": new_active, "restarts": r if isinstance(r, int) else prev_r, "stop": stop if isinstance(stop, bool) else prev_stop, "ts": now}


def chunks(lines: list[str]) -> list[str]:
    room = MAX_CONTENT - len(POST_PREFIX)
    out, cur = [], ""
    for line in lines:
        line = line[:room]
        cand = line if not cur else cur + "\n" + line
        if cur and len(cand) > room:
            out.append(POST_PREFIX + cur)
            cur = line
        else:
            cur = cand
    return out + ([POST_PREFIX + cur] if cur else [])


def post_discord(webhook: str, content: str) -> None:
    if not WEBHOOK_RE.match(webhook):
        raise RuntimeError("webhook is not a Discord webhook URL")
    req = urllib.request.Request(webhook, json.dumps({"content": content[:MAX_CONTENT]}).encode(), {"Content-Type": "application/json", "User-Agent": "mal-h5-arena-watch/1"})
    try:
        urllib.request.urlopen(req, timeout=20).read()
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"post failed ({type(exc).__name__})") from None


def load_state(p: Path) -> dict[str, Any]:
    try:
        raw = json.loads(p.read_text())
        return raw if isinstance(raw, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(p: Path, state: dict[str, Any]) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, separators=(",", ":")))
    os.chmod(tmp, 0o600)
    os.replace(tmp, p)


def run(host: Host, env: dict[str, str], post: Callable[[str, str], None], state_path: Path, now: float | None = None) -> int:
    now = time.time() if now is None else now
    webhook = env.get(WEBHOOK_VAR, "")
    if not WEBHOOK_RE.match(webhook):
        print("h5_arena_watch: ARENA_WATCH_DISCORD_WEBHOOK is missing or not a Discord webhook URL", file=sys.stderr)
        return 2
    alerts, obs = collect(host, env, now)
    lines, new_state = decide(alerts, obs, load_state(state_path), now)
    try:
        for content in chunks(lines):
            post(webhook, content)
    except RuntimeError as exc:
        print(f"h5_arena_watch: {exc}", file=sys.stderr)  # state not advanced: the same lines are tried next run
        return 1
    save_state(state_path, new_state)
    age = obs.get("feed_age_s")
    print(f"h5_arena_watch: unit={UNIT} active={obs.get('active')} live_dropin={obs.get('live')} feed_age={'unknown' if age is None else f'{age}s'} "
          f"alerts={len(alerts)} posted={len(lines)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    env = dict(os.environ)
    os.environ.pop(WEBHOOK_VAR, None)  # no child process (systemctl) inherits the secret
    if argv == ["--test-message"]:
        try:
            post_discord(env.get(WEBHOOK_VAR, ""), POST_PREFIX + "test message: the arena watchdog can post. If you read this, enabling the timer is allowed.")
        except RuntimeError as exc:
            print(f"h5_arena_watch: {exc}", file=sys.stderr)
            return 1
        print("h5_arena_watch: test message posted")
        return 0
    if argv:
        print("usage: h5-arena-watch.py [--test-message]", file=sys.stderr)
        return 2
    state_dir = env.get("STATE_DIRECTORY", "/var/lib/mal-h5-arena-watch").split(":")[0]
    return run(Host(), env, post_discord, Path(state_dir) / "state.json")


if __name__ == "__main__":
    sys.exit(main())
