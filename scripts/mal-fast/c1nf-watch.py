#!/usr/bin/env python3
"""C1-NF watchdog (DEC-026 section 5: its own timer and config, `mal-c1nf-watch`; Discord: stuck, structure halt, stop fired, restarts,
wallet line, tier change). Modelled on h5-watch.py (DEC-024). The H5 watchdog stays H5's: this one reads nothing of H5's. "Structure halt"
(DEC-026 section 7 rule 6) is the A3 monitor's: the watchdog alerts on it only when C1NF_WATCH_A3_FILE names the monitor's file here.

Runs every 5 minutes from mal-c1nf-watch.timer as a hardened root oneshot (scripts/mal-fast/mal-c1nf-watch.service), from the root-owned
pinned tree, as `python3 -I -S`. Stdlib only. It runs the same checks as the daily run (c1nf-daily-check.py: stuck or abandoned position,
latched live halts, HALT files, unmanaged positions, unit files and credential, live config, idle canary, executor budget stops, executor
ALERT rows, refusals, fill rate, the CAP-PICK oracle pause, the watchdog's own health, wallet balance against funded + C1-NF realized) and
adds what only a repeated run can see: the unit restarted since the last run, STOP placed or removed, a TIER change, and one wallet line
per UTC day. It posts to Discord when an alert is NEW, again every 6 h while it stays, and "RESOLVED" when it clears. It does not touch
the key, the executor or any state of the executor.

CLASS-BLIND (EXP-025 Amendment 2 item 5). Every line it posts comes from the daily check's class-blind Report or from fixed strings here;
post() refuses (and the run fails) if a line would still name the synthetic class, so no Discord message splits anything by class.

Config comes from the environment (systemd EnvironmentFile /etc/mal-c1nf-watch/watch.env, root:root 0600):
  C1NF_WATCH_DISCORD_WEBHOOK  the webhook URL: a secret, never printed or logged, and removed from os.environ as soon as it is read
  C1NF_WATCH_FUNDED_SOL       total SOL deposited into the second wallet, net of withdrawals (0.5 at O-1)
  C1NF_WATCH_SHADOW_DIR       the C1-NF shadow's output directory (heartbeat files only: the stale-feed and bind checks)
  C1NF_WATCH_WALLET           the second wallet's PUBLIC address (Helm gives it; never the H5 wallet)
  C1NF_WATCH_A3_FILE          optional: the A3 structure monitor's JSONL on this host (absolute path). With it the watchdog alerts on
                              DEC-026 section 7 rule 6's live halts (the executor does not latch them); without it the check says so.
The balance comes from the public RPC (no key).

    c1nf-watch.py                run the checks and post what changed
    c1nf-watch.py --test-message post one test line and exit (Helm runs it once when enabling the timer)
"""
from __future__ import annotations

import importlib.util
import json
import math
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
WEBHOOK_VAR = "C1NF_WATCH_DISCORD_WEBHOOK"
WALLET_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")


def load_daily():
    spec = importlib.util.spec_from_file_location("c1nf_daily_check", HERE / "c1nf-daily-check.py")
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def post_discord(webhook: str, content: str) -> None:
    """One POST. Raises RuntimeError with the exception type only: the URL is the secret."""
    if not WEBHOOK_RE.match(webhook):
        raise RuntimeError("webhook is not a Discord webhook URL")
    body = json.dumps({"content": content[:MAX_CONTENT]}).encode()
    req = urllib.request.Request(webhook, body, {"Content-Type": "application/json", "User-Agent": "mal-c1nf-watch/1"})
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
        funded = float(env["C1NF_WATCH_FUNDED_SOL"])
        if not math.isfinite(funded) or funded <= 0:  # float() accepts nan and inf; either would crash the engine's lamport conversion
            raise ValueError("funded")
        shadow = env["C1NF_WATCH_SHADOW_DIR"]
        wallet = env["C1NF_WATCH_WALLET"]
        if not WALLET_RE.match(wallet) or wallet == daily.H5_WALLET:
            raise ValueError("wallet")
        a3 = env.get("C1NF_WATCH_A3_FILE", "")
        if a3 and not a3.startswith("/"):
            raise ValueError("a3")
    except (KeyError, ValueError):
        return {"watch_config": "C1NF_WATCH_FUNDED_SOL, C1NF_WATCH_SHADOW_DIR, C1NF_WATCH_WALLET or C1NF_WATCH_A3_FILE is missing or invalid in "
                                "/etc/mal-c1nf-watch/watch.env (funded a finite number above 0; the wallet a base58 public address and not the "
                                "H5 wallet; the optional A3 file an absolute path)"}, {}
    args = daily.build_parser().parse_args(["--funded-sol", str(funded), "--wallet", wallet, "--public-rpc", "--shadow-dir", shadow,
                                            "--window-hours", WATCH_WINDOW_HOURS, *(["--a3-file", a3] if a3 else [])])
    rep = daily.run_checks(args, host, lambda s: None, balance_fn, now)
    for name, msg in rep.alert_list:
        alerts[name] = f"{alerts[name]}; {msg}" if name in alerts else msg
    obs: dict = {"facts": dict(rep.facts), "tier_changes": list(rep.facts.get("tier_changes") or [])}
    try:
        rc, out = host.systemctl("show", daily.C1NF_UNIT, "-p", "NRestarts", "--value", "--no-pager")
        obs["restarts"] = int(out.strip()) if rc == 0 else None
    except (ValueError, OSError, RuntimeError):
        obs["restarts"] = None  # the engine already raised systemctl_failed if systemd does not answer
    try:
        obs["stop"] = host.exists(f"{daily.C1NF_DIR}/STOP")
    except Exception:  # noqa: BLE001 - the daily checks already alert on a failing privileged read
        obs["stop"] = None
    return alerts, obs


def decide(alerts: dict[str, str], obs: dict, state: dict, now: float) -> tuple[list[str], dict]:
    """The lines to post and the new state. An alert is posted when new, again every REPEAT_S, and RESOLVED when it clears."""
    active = dict(state.get("active") or {})
    lines: list[str] = []
    prev_r, r = state.get("restarts"), obs.get("restarts")
    if isinstance(prev_r, int) and isinstance(r, int) and r > prev_r:
        alerts = {**alerts, "c1nf_restarts": f"the unit restarted {r - prev_r} time(s) since the last run (NRestarts {prev_r} -> {r})"}
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
    marker = state.get("tier_change_ts") if isinstance(state.get("tier_change_ts"), (int, float)) else 0
    newest = marker
    for ts, old, new, problem in sorted(obs.get("tier_changes") or []):  # an event, posted once per ledger row
        if ts > marker:
            lines.append(f"EVENT tier_change {old or 'none'} -> {new or '?'}" + (f" (problem: {problem})" if problem else ""))
            newest = max(newest, ts)
    day = time.strftime("%Y-%m-%d", time.gmtime(now))
    wline = (obs.get("facts") or {}).get("wallet_line")
    wallet_day = state.get("wallet_day")
    if wline and wallet_day != day:
        lines.append(f"DAILY {day} {wline}")
        wallet_day = day
    prev_stop, stop = state.get("stop"), obs.get("stop")
    if stop is True and prev_stop is False:
        lines.append("EVENT STOP placed: new buys are stopped (open positions still exit on the timer)")
    elif stop is False and prev_stop is True:
        lines.append("EVENT STOP removed")
    return lines, {"active": new_active, "restarts": r if isinstance(r, int) else prev_r, "stop": stop if isinstance(stop, bool) else prev_stop,
                    "tier_change_ts": newest, "wallet_day": wallet_day, "ts": now}


def summary(obs: dict, n_alerts: int, n_lines: int) -> str:
    f = obs.get("facts") or {}
    age = f.get("feed_age_s")
    return (f"c1nf_watch: unit={f.get('unit', 'unknown')} active={f.get('active', 'unknown')} enabled={f.get('enabled', 'unknown')} "
            f"feed_age={'unknown' if age is None else f'{age}s'} tier={f.get('tier', 'unknown')} alerts={n_alerts} posted={n_lines}")


def run(host, env: dict[str, str], post: Callable[[str, str], None], state_path: Path, now: float | None = None, balance_fn=None) -> int:
    now = time.time() if now is None else now
    webhook = env.get(WEBHOOK_VAR, "")
    if not WEBHOOK_RE.match(webhook):
        print("c1nf_watch: C1NF_WATCH_DISCORD_WEBHOOK is missing or not a Discord webhook URL", file=sys.stderr)
        return 2
    daily = load_daily()
    alerts, obs = collect(daily, host, env, now, balance_fn)
    state = load_state(state_path)
    lines, new_state = decide(alerts, obs, state, now)
    if lines and any(daily.Report.names_class(x) for x in lines):
        print("c1nf_watch: refusing to post a line that names the class (EXP-025 Amendment 2 item 5)", file=sys.stderr)
        return 1
    if lines:
        try:
            post(webhook, "[C1-NF watch] " + "\n".join(lines))
        except RuntimeError as exc:
            print(f"c1nf_watch: {exc}", file=sys.stderr)  # state is not advanced: the same lines are tried again next run
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
            post_discord(env.get(WEBHOOK_VAR, ""), "[C1-NF watch] test message: the watchdog can post. If you read this, enabling the timer is allowed.")
        except RuntimeError as exc:
            print(f"c1nf_watch: {exc}", file=sys.stderr)
            return 1
        print("c1nf_watch: test message posted")
        return 0
    if argv:
        print("usage: c1nf-watch.py [--test-message]", file=sys.stderr)
        return 2
    state_dir = env.get("STATE_DIRECTORY", "/var/lib/mal-c1nf-watch").split(":")[0]
    daily = load_daily()
    return run(daily.Host(), env, post_discord, Path(state_dir) / "state.json")


if __name__ == "__main__":
    sys.exit(main())
