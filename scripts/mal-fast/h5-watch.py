#!/usr/bin/env python3
"""H5 watchdog (DEC-024 section 8: "re-target the durable Discord watchdog (stuck, structure halt, stop fired, restarts, wallet balance)").

Runs every 5 minutes from mal-h5-watch.timer as a hardened root oneshot (scripts/mal-fast/mal-h5-watch.service), from the root-owned
pinned tree, as `python3 -I -S`. Stdlib only. It runs the same checks as the daily run (h5-daily-check.py: stuck or abandoned position,
latched live halts, HALT files, unmanaged positions, unit files, idle canary, executor budget stops, executor ALERT rows, refusals, the
watchdog's own health, wallet balance against funded + H5 realized) and adds what only a repeated run can see: the unit restarted since
the last run, and STOP placed or removed. It posts to Discord when an alert is NEW, again every 6 h while it stays, and "RESOLVED" when it
clears. A failing systemctl is an alert (systemctl_failed), never "not installed". It does not touch the key, the executor or any state of
the executor.

Config comes from the environment (systemd EnvironmentFile /etc/mal-h5-watch/watch.env, root:root 0600):
  H5_WATCH_DISCORD_WEBHOOK  the webhook URL: a secret, never printed or logged, and removed from os.environ as soon as it is read, so no
                            child process (systemctl, stat, dd) inherits it
  H5_WATCH_FUNDED_SOL       total SOL deposited, net of withdrawals (the wallet comparison)
  H5_WATCH_SHADOW_DIR       the shadow detector's output directory (the stale-feed and bind checks)
The balance comes from the public RPC (no key). The probe-state check stays with the manager's daily run.

    h5-watch.py                run the checks and post what changed
    h5-watch.py --test-message post one test line and exit (Helm runs it once when enabling the timer)
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
import time
import urllib.request
from pathlib import Path
from typing import Callable

HERE = Path(__file__).resolve().parent
REPEAT_S = 6 * 3600
WATCH_WINDOW_HOURS = "6"
WEBHOOK_RE = re.compile(r"^https://(?:discord|discordapp)\.com/api/webhooks/[0-9]+/[A-Za-z0-9_-]+$")
MAX_CONTENT = 1900
WEBHOOK_VAR = "H5_WATCH_DISCORD_WEBHOOK"


def load_daily():
    spec = importlib.util.spec_from_file_location("h5_daily_check", HERE / "h5-daily-check.py")
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def post_discord(webhook: str, content: str) -> None:
    """One POST. Raises RuntimeError with the exception type only: the URL is the secret."""
    if not WEBHOOK_RE.match(webhook):
        raise RuntimeError("webhook is not a Discord webhook URL")
    body = json.dumps({"content": content[:MAX_CONTENT]}).encode()
    req = urllib.request.Request(webhook, body, {"Content-Type": "application/json", "User-Agent": "mal-h5-watch/1"})
    try:
        urllib.request.urlopen(req, timeout=20).read()
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"post failed ({type(exc).__name__})") from None


def load_state(path: Path) -> dict:
    try:
        raw = json.loads(path.read_text())
        return raw if isinstance(raw, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, separators=(",", ":")))
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def collect(daily, host, env: dict[str, str], now: float, balance_fn=None) -> tuple[dict[str, str], dict]:
    """(alerts by name, observations). Never raises: a failure to run is itself an alert."""
    alerts: dict[str, str] = {}
    try:
        funded = float(env["H5_WATCH_FUNDED_SOL"])
        shadow = env["H5_WATCH_SHADOW_DIR"]
    except (KeyError, ValueError):
        return {"watch_config": "H5_WATCH_FUNDED_SOL or H5_WATCH_SHADOW_DIR is missing or invalid in /etc/mal-h5-watch/watch.env"}, {}
    args = daily.build_parser().parse_args(["--funded-sol", str(funded), "--public-rpc", "--skip-probe-state", "--shadow-dir", shadow,
                                            "--window-hours", WATCH_WINDOW_HOURS])
    rep = daily.run_checks(args, host, lambda s: None, balance_fn, now)
    for name, msg in rep.alert_list:
        alerts[name] = f"{alerts[name]}; {msg}" if name in alerts else msg
    obs: dict = {"facts": dict(rep.facts)}
    try:
        rc, out = host.systemctl("show", daily.H5_UNIT, "-p", "NRestarts", "--value", "--no-pager")
        obs["restarts"] = int(out.strip()) if rc == 0 else None
    except (ValueError, OSError, RuntimeError):
        obs["restarts"] = None  # the engine already raised systemctl_failed if systemd does not answer
    try:
        obs["stop"] = host.exists(f"{daily.H5_DIR}/STOP")
    except Exception:  # noqa: BLE001 - the daily checks already alert on a failing privileged read
        obs["stop"] = None
    return alerts, obs


def decide(alerts: dict[str, str], obs: dict, state: dict, now: float) -> tuple[list[str], dict]:
    """The lines to post and the new state. An alert is posted when new, again every REPEAT_S, and RESOLVED when it clears."""
    active = dict(state.get("active") or {})
    lines: list[str] = []
    prev_r, r = state.get("restarts"), obs.get("restarts")
    if isinstance(prev_r, int) and isinstance(r, int) and r > prev_r:
        alerts = {**alerts, "h5_restarts": f"the unit restarted {r - prev_r} time(s) since the last run (NRestarts {prev_r} -> {r})"}
    new_active: dict[str, dict] = {}
    for name, msg in alerts.items():
        prev = active.get(name)
        if prev is None or now - prev.get("last_post", 0) >= REPEAT_S:
            lines.append(f"ALERT {name}: {msg}")
            new_active[name] = {"first": (prev or {}).get("first", now), "last_post": now}
        else:
            new_active[name] = prev
    for name in active:
        if name not in alerts:
            lines.append(f"RESOLVED {name}")
    prev_stop, stop = state.get("stop"), obs.get("stop")
    if stop is True and prev_stop is False:
        lines.append("EVENT STOP placed: new buys are stopped (open positions still exit on the timer)")
    elif stop is False and prev_stop is True:
        lines.append("EVENT STOP removed")
    return lines, {"active": new_active, "restarts": r if isinstance(r, int) else prev_r, "stop": stop if isinstance(stop, bool) else prev_stop, "ts": now}


def summary(obs: dict, n_alerts: int, n_lines: int) -> str:
    f = obs.get("facts") or {}
    age = f.get("feed_age_s")
    return (f"h5_watch: unit={f.get('unit', 'unknown')} active={f.get('active', 'unknown')} enabled={f.get('enabled', 'unknown')} "
            f"feed_age={'unknown' if age is None else f'{age}s'} alerts={n_alerts} posted={n_lines}")


def run(host, env: dict[str, str], post: Callable[[str, str], None], state_path: Path, now: float | None = None, balance_fn=None) -> int:
    now = time.time() if now is None else now
    webhook = env.get(WEBHOOK_VAR, "")
    if not WEBHOOK_RE.match(webhook):
        print("h5_watch: H5_WATCH_DISCORD_WEBHOOK is missing or not a Discord webhook URL", file=sys.stderr)
        return 2
    daily = load_daily()
    alerts, obs = collect(daily, host, env, now, balance_fn)
    state = load_state(state_path)
    lines, new_state = decide(alerts, obs, state, now)
    if lines:
        try:
            post(webhook, "[H5 watch] " + "\n".join(lines))
        except RuntimeError as exc:
            print(f"h5_watch: {exc}", file=sys.stderr)  # state is not advanced: the same lines are tried again next run
            return 1
    save_state(state_path, new_state)
    print(summary(obs, len(alerts), len(lines)))
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    env = dict(os.environ)
    os.environ.pop(WEBHOOK_VAR, None)  # read once, now: no child process (systemctl, stat, dd, true) may inherit the secret
    if argv == ["--test-message"]:
        try:
            post_discord(env.get(WEBHOOK_VAR, ""), "[H5 watch] test message: the watchdog can post. If you read this, enabling the timer is allowed.")
        except RuntimeError as exc:
            print(f"h5_watch: {exc}", file=sys.stderr)
            return 1
        print("h5_watch: test message posted")
        return 0
    if argv:
        print("usage: h5-watch.py [--test-message]", file=sys.stderr)
        return 2
    state_dir = env.get("STATE_DIRECTORY", "/var/lib/mal-h5-watch").split(":")[0]
    daily = load_daily()
    return run(daily.Host(), env, post_discord, Path(state_dir) / "state.json")


if __name__ == "__main__":
    sys.exit(main())
