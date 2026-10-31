#!/usr/bin/env python3
"""Sidecar of the r4-b long DRY run (QP #1160 conditions M4 and M6; QP-RULING-1031-r4b-pc3 section 2.4, 2.5 and 4 P5). Run by scripts/research/r4b-dry-sidecar.sh.

It reads STATE files only: the host scope's cgroup files (memory.current, memory.stat, memory.events), the tip follower's
/var/lib/mal/fast-tip-follower/status.json, /proc/loadavg, /proc/pressure/{cpu,io}, `systemctl show` of two units, and (to find the end of the catch-up) the host's own
glue events file (out/glue/events.jsonl: TipLiveSource writes `catch_up_done` there, not in out/host/), of which it extracts the `ms` of the one `catch_up_done` event and nothing else. It never opens a tape file, a decision log, a checkpoint or a
fills/P&L file, and it never lists /var/lib/mal/sealed. Stdlib only (the wrapper runs it with `python -I`).

Output: one JSON line per sample appended to --out (the file is also read back on a restart, to rebuild the anon history and the pre-start lag range).
stdout: a heartbeat every 30 min, and ALERT / WARN / END lines.

Exit codes: 0 ran to the end of --hours, or the scope ended (END scope_gone); 3 ALERT (M6 / oom / new backlog_jump / follower restart / catch-up end unknown);
4 ALERT, the catch-up stop rule exactly as pc3 P5 registered it (during catch-up only: a new backlog_jump, a `gaps` increase, or lag_slots >= 20 in two consecutive samples); 5 usage;
6 the scope never appeared. An exit by ALERT is the end of this sidecar process: launch it again to continue; that
acknowledges the alert (follower baselines are re-taken, the anon history is read back from --out).

M6 (QP #1160): stop the run and restart with a larger envelope if the linear projection of anon, fitted over the last >= 6 h after the catch-up (window
[catch-up end + 1 h, now], at most the trailing 11 h, so at catch-up end + 12 h it is exactly M6's [+1 h, +12 h]) to min(run end, catch-up end + 116 h), exceeds
2,975 MiB (85 % of memory.max 3,500 MiB), or at once if anon itself reaches 2,975 MiB. The projection is recorded in the sample from the first 6 h window on, but it can ALERT only
from catch-up end + 12 h (M6's check); from then on the trailing 11 h fit continues and QP rules its alerts binding M6 stops (QP on #1161, C2). The sidecar only alerts; the stop
procedure is M5 (r4b-dry-stop.sh). If `catch_up_done` has not appeared 90 min after the scope did, it ALERTs (exit 3: M6 would be blind); relaunch with R4B_CATCHUP_END_EPOCH.
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import subprocess
import sys
import time
from typing import Any, Callable

MIB = 1 << 20
ALERT_MIB = 2975.0                    # 85 % of MemoryMax=3500M
HORIZON_AFTER_CATCHUP_S = 116 * 3600  # the 7-day bonding-idle plateau (catch-up end + about 116 h)
FIT_SETTLE_S = 3600                   # the first hour after catch-up is not fitted (M6: [+1 h, +12 h])
FIT_MIN_SPAN_S = 6 * 3600             # the fit needs >= 6 h of samples after catch-up
FIT_MAX_SPAN_S = 11 * 3600            # trailing window; at catch-up end + 12 h this is [+1 h, +12 h]
M6_DECISION_AFTER_S = 12 * 3600       # the projection ALERT is gated on now >= catch-up end + 12 h (M6); earlier fits are recorded as fields only
FIT_MIN_POINTS = 20
FAST_UNTIL_S = 2 * 3600               # P5: every <= 30 s until catch-up end + 2 h, then every <= 60 s
FAST_INTERVAL_S = 30.0
SLOW_INTERVAL_S = 60.0
CATCHUP_BUDGET_S = 50 * 60            # pc3 section 2.5: submit at HH:05Z, catch-up ends before HH+1:00Z
CATCHUP_GIVEUP_S = 90 * 60            # no catch_up_done this long after the scope appeared: ALERT (S1), M6 would otherwise be blind
LAG_EDGE_FLOOR = 10                   # pc3 section 2.2: lag 3 to 10 was seen in the same run with the host on the live tail (1 to 3 with no host); a WARN band, not a stop
LAG_HARD = 20                         # pc3 P5: lag_slots >= 20 in two consecutive samples (the registered stop line)
LAG_CONSECUTIVE = 2
GONE_MISSES = 3
HEARTBEAT_S = 1800
SCOPE_WAIT_S = 90 * 60                # no scope this long after the sidecar started: the host was never launched
STATUS_KEYS = ("lag_slots", "lag_ms", "backlog_jumps", "gaps", "skipped", "block_lag_ms_p50", "block_lag_ms_p90", "fetch_ms_p50", "fetch_ms_p90",
               "decoder_blobs_pinned", "trade_event_v", "last_done_slot")
EXIT_OK, EXIT_ALERT, EXIT_CATCHUP, EXIT_USAGE, EXIT_NO_SCOPE = 0, 3, 4, 5, 6


# ---- parsers (pure) ----------------------------------------------------------------------------------------------------------------------------------------

def parse_kv_lines(text: str | None) -> dict[str, int]:
    """'anon 123\\nfile 456' -> {'anon': 123, 'file': 456}; lines that do not parse are skipped."""
    out: dict[str, int] = {}
    for line in (text or "").splitlines():
        parts = line.split()
        if len(parts) >= 2:
            try:
                out[parts[0]] = int(parts[1])
            except ValueError:
                pass
    return out


def parse_show(text: str | None) -> dict[str, str]:
    """`systemctl show -p A -p B` output -> {'A': ..., 'B': ...}."""
    out: dict[str, str] = {}
    for line in (text or "").splitlines():
        k, eq, v = line.partition("=")
        if eq:
            out[k.strip()] = v.strip()
    return out


def parse_psi(text: str | None) -> dict[str, float]:
    """'some avg10=0.12 avg60=...\\nfull avg10=0.00 ...' -> {'some': 0.12, 'full': 0.0} (avg10 only)."""
    out: dict[str, float] = {}
    for line in (text or "").splitlines():
        w = line.split()
        if w and w[0] in ("some", "full"):
            for tok in w[1:]:
                if tok.startswith("avg10="):
                    try:
                        out[w[0]] = float(tok[6:])
                    except ValueError:
                        pass
    return out


def fit_projection(points: list[tuple[float, float]], t_target: float) -> tuple[float, float] | None:
    """Ordinary least squares of y on t. Returns (slope per second, fitted y at t_target), or None with fewer than 2 points or no spread in t."""
    n = len(points)
    if n < 2:
        return None
    t0 = points[0][0]
    xs = [p[0] - t0 for p in points]
    ys = [p[1] for p in points]
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx <= 0:
        return None
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    return slope, my + slope * ((t_target - t0) - mx)


# ---- the alert logic (pure; no I/O) ------------------------------------------------------------------------------------------------------------------------

class Monitor:
    """feed(sample) -> list of (exit_code, reason); WARN lines go to self.warns (reset at each feed). The sample dict gets `phase`, `anon_slope_mib_h` and `anon_proj_mib` filled in.
    phase: 'pre' (the scope has not been seen), 'catchup' (scope seen, catch_up_end unknown), 'live' (catch_up_end known)."""

    def __init__(self, *, alert_mib: float = ALERT_MIB, run_end: float | None = None, catchup_end: float | None = None,
                 history: list[tuple[float, float]] | None = None, pre_lag_max: int | None = None, scope_seen: bool = False) -> None:
        self.alert_mib, self.run_end, self.catchup_end = float(alert_mib), run_end, catchup_end
        self.hist: collections.deque[tuple[float, float]] = collections.deque(history or [])
        self.pre_lag_max = pre_lag_max
        self.scope_seen = scope_seen
        self.base: dict[str, Any] = {}
        self.lag_run = 0  # consecutive catch-up samples above the lag WARN band edge
        self.hard_run = 0  # consecutive catch-up samples at lag_slots >= LAG_HARD
        self.last_pre: dict[str, int] = {}  # the last pre-scope sample's gaps / backlog_jumps: the catch-up baselines (C4)
        self.warns: list[str] = []

    def lag_edge(self) -> int:
        return max(self.pre_lag_max if self.pre_lag_max is not None else 0, LAG_EDGE_FLOOR)

    def horizon(self, now: float) -> float | None:
        if self.catchup_end is None:
            return None
        t_h = self.catchup_end + HORIZON_AFTER_CATCHUP_S
        if self.run_end is not None:
            t_h = min(t_h, self.run_end)
        return max(t_h, now)

    def project(self, now: float) -> tuple[float, float, float] | None:
        """(slope MiB/h, projected anon MiB at the horizon, span h) over the fit window, or None while the window is shorter than 6 h."""
        t_h = self.horizon(now)
        if t_h is None or self.catchup_end is None:
            return None
        lo = max(self.catchup_end + FIT_SETTLE_S, now - FIT_MAX_SPAN_S)
        pts = [(t, a) for t, a in self.hist if lo <= t <= now]
        if len(pts) < FIT_MIN_POINTS or pts[-1][0] - pts[0][0] < FIT_MIN_SPAN_S:
            return None
        fit = fit_projection(pts, t_h)
        if fit is None:
            return None
        return fit[0] * 3600.0 / MIB, fit[1] / MIB, (pts[-1][0] - pts[0][0]) / 3600.0

    def feed(self, s: dict[str, Any]) -> list[tuple[int, str]]:
        now = float(s["ts"])
        alerts: list[tuple[int, str]] = []
        self.warns = []
        first_scope = bool(s.get("scope_found")) and not self.scope_seen
        if s.get("scope_found"):
            self.scope_seen = True
        phase = "pre" if not self.scope_seen else ("catchup" if self.catchup_end is None or now < self.catchup_end else "live")
        s["phase"] = phase
        anon = s.get("mem_anon")
        if s.get("scope_found") and anon is not None:
            self.hist.append((now, float(anon)))
        while self.hist and self.hist[0][0] < now - FIT_MAX_SPAN_S - 3600:
            self.hist.popleft()
        lag = s.get("lag_slots")
        if phase == "pre" and isinstance(lag, int):
            self.pre_lag_max = lag if self.pre_lag_max is None else max(self.pre_lag_max, lag)
        for k in ("backlog_jumps", "gaps"):  # baselines: the first sample of this process that carries the field ...
            if k not in self.base and isinstance(s.get(k), int):
                self.base[k] = s[k]
        if phase == "pre":
            for k in ("backlog_jumps", "gaps"):
                v = s.get(k)
                if isinstance(v, int):
                    if k == "gaps" and isinstance(self.last_pre.get(k), int) and v > self.last_pre[k]:
                        self.warns.append(f"WARN follower_gaps_increase_pre_start: gaps {self.last_pre[k]} -> {v} before the scope existed (not charged to the catch-up)")
                    self.last_pre[k] = v
        elif first_scope:  # ... re-taken from the last pre-start sample when the scope first appears (QP on #1161, C4)
            self.base.update(self.last_pre)
        for k, src in (("nrestarts", "follower_nrestarts"), ("active_enter", "follower_active_enter")):
            if k not in self.base and s.get(src) not in (None, ""):
                self.base[k] = s[src]
        # 1. memory: oom first, then the instant 85 % check
        if (s.get("ev_oom") or 0) > 0 or (s.get("ev_oom_kill") or 0) > 0:
            alerts.append((EXIT_ALERT, f"oom: memory.events oom={s.get('ev_oom')} oom_kill={s.get('ev_oom_kill')} (M6: oom_kill > 0 ends the run; record it and restart as a new run)"))
        if anon is not None and anon / MIB >= self.alert_mib:
            alerts.append((EXIT_ALERT, f"anon_at_threshold: anon {anon / MIB:.1f} MiB >= {self.alert_mib:.0f} MiB (M6: do the slope check at once)"))
        # 2. the follower
        bj, base_bj = s.get("backlog_jumps"), self.base.get("backlog_jumps")
        if isinstance(bj, int) and isinstance(base_bj, int) and bj > base_bj:
            code = EXIT_CATCHUP if phase == "catchup" else EXIT_ALERT
            alerts.append((code, f"new_backlog_jump: backlog_jumps {base_bj} -> {bj} in phase {phase}" + (" (pc3 P5 catch-up stop rule: stop the job per M5)" if phase == "catchup" else "")))
        gp, base_gp = s.get("gaps"), self.base.get("gaps")
        if phase == "catchup" and isinstance(gp, int) and isinstance(base_gp, int) and gp > base_gp:
            alerts.append((EXIT_CATCHUP, f"follower_gaps_increase: gaps {base_gp} -> {gp} during catch-up (pc3 P5 stop rule: stop the job per M5)"))
        if phase == "catchup" and isinstance(lag, int):
            edge = self.lag_edge()
            self.lag_run = self.lag_run + 1 if lag > edge else 0
            self.hard_run = self.hard_run + 1 if lag >= LAG_HARD else 0
            s["lag_outside_pre_range_run"] = self.lag_run
            if self.lag_run == LAG_CONSECUTIVE:  # once per excursion; a WARN and a sample field, not a stop (not registered in pc3 P5)
                self.warns.append(f"WARN catchup_lag_above_pre_start_range: lag_slots {lag} above the range edge {edge} (pre-start max {self.pre_lag_max}, floor {LAG_EDGE_FLOOR}) in"
                                  f" {self.lag_run} consecutive samples; recorded in sidecar.jsonl, P-C3 judges 'within its pre-start range' from it; the stop line is lag_slots >= {LAG_HARD}"
                                  f" in {LAG_CONSECUTIVE} consecutive samples")
            if self.hard_run >= LAG_CONSECUTIVE:
                alerts.append((EXIT_CATCHUP, f"catchup_lag_stop: lag_slots {lag} >= {LAG_HARD} in {self.hard_run} consecutive samples during catch-up (pc3 P5 stop rule: stop the job per M5)"))
        else:
            self.lag_run = self.hard_run = 0
        nr, ae = s.get("follower_nrestarts"), s.get("follower_active_enter")
        if (isinstance(nr, int) and isinstance(self.base.get("nrestarts"), int) and nr > self.base["nrestarts"]) or \
           (ae not in (None, "") and self.base.get("active_enter") not in (None, "") and ae != self.base["active_enter"]):
            alerts.append((EXIT_ALERT, f"follower_restart: NRestarts {self.base.get('nrestarts')} -> {nr}, ActiveEnterTimestamp {self.base.get('active_enter')!r} -> {ae!r}"))
        # 3. M6 projection
        pr = self.project(now) if phase == "live" else None
        if pr is not None:
            s["anon_slope_mib_h"], s["anon_proj_mib"], s["anon_fit_span_h"] = round(pr[0], 3), round(pr[1], 1), round(pr[2], 2)
            if pr[1] > self.alert_mib and now >= self.catchup_end + M6_DECISION_AFTER_S:  # M6 decides at catch-up end + 12 h; an earlier fit is a field only (C2)
                t_h = self.horizon(now)
                alerts.append((EXIT_ALERT, f"m6_projection: anon fit {pr[0]:.2f} MiB/h over {pr[2]:.1f} h projects {pr[1]:.0f} MiB at +{((t_h - now) / 3600.0):.1f} h"
                                           f" (> {self.alert_mib:.0f} MiB; M6: stop per M5 and restart with a larger envelope as a new change_id)"))
        return alerts


# ---- state-file I/O ----------------------------------------------------------------------------------------------------------------------------------------

def default_systemctl(args: list[str]) -> str:
    try:
        r = subprocess.run(["systemctl", *args], capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return ""
    return r.stdout if r.returncode == 0 else ""


def read_text(path: str) -> str | None:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None


class EventsScan:
    """Finds the `catch_up_done` event in the host glue's out/glue/events.jsonl and returns its `ms` / 1000. Lines are read as bytes; only a line that contains the event name is
    parsed, and only its `ms` is kept. Nothing else of the file is parsed, stored or printed."""

    NEEDLE = b'"catch_up_done"'

    def __init__(self, path: str | None) -> None:
        self.path, self.off, self.tail, self.found = path, 0, b"", None

    def poll(self) -> float | None:
        if self.found is not None or not self.path:
            return self.found
        try:
            size = os.stat(self.path).st_size
            if size < self.off:
                self.off, self.tail = 0, b""
            with open(self.path, "rb") as f:
                f.seek(self.off)
                data = f.read(1 << 20)
        except OSError:
            return None
        self.off += len(data)
        buf = self.tail + data
        lines = buf.split(b"\n")
        self.tail = lines.pop()  # an unfinished last line waits for the next poll
        for ln in lines:
            if self.NEEDLE in ln:
                try:
                    rec = json.loads(ln)
                    if rec.get("event") == "catch_up_done":
                        self.found = float(rec["ms"]) / 1000.0
                        return self.found
                except (ValueError, KeyError, TypeError, AttributeError):
                    pass
        return None


def load_history(path: str) -> dict[str, Any]:
    """Read this sidecar's own earlier output (a restart after an alert): the anon history, the pre-start lag maximum, the catch-up end, whether the scope was seen."""
    h: dict[str, Any] = {"history": [], "pre_lag_max": None, "catchup_end": None, "scope_seen": False, "scope_first": None}
    txt = read_text(path)
    for line in (txt or "").splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if not isinstance(r, dict) or not isinstance(r.get("ts"), (int, float)):
            continue
        if r.get("scope_found"):
            h["scope_seen"] = True
            h["scope_first"] = h["scope_first"] if h["scope_first"] is not None else r["ts"]
            if isinstance(r.get("mem_anon"), (int, float)):
                h["history"].append((float(r["ts"]), float(r["mem_anon"])))
        if r.get("phase") == "pre" and isinstance(r.get("lag_slots"), int):
            h["pre_lag_max"] = r["lag_slots"] if h["pre_lag_max"] is None else max(h["pre_lag_max"], r["lag_slots"])
        if isinstance(r.get("catchup_end_ts"), (int, float)):
            h["catchup_end"] = float(r["catchup_end_ts"])
    return h


def collect(cfg: argparse.Namespace, now: float, systemctl: Callable[[list[str]], str], cg_cache: dict[str, str]) -> dict[str, Any]:
    s: dict[str, Any] = {"ts": round(now, 3), "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)), "scope_found": False}
    unit = f"r4b-dry-{cfg.change_id}.scope"
    cg = cg_cache.get("cg")
    if not cg:
        cg = parse_show(systemctl(["--user", "show", unit, "-p", "ControlGroup"])).get("ControlGroup") or ""
        if cg:
            cg_cache["cg"] = cg
    if cg:
        base = cfg.cgroup_root.rstrip("/") + cg
        cur = read_text(base + "/memory.current")
        if cur is not None:
            st = parse_kv_lines(read_text(base + "/memory.stat"))
            ev = parse_kv_lines(read_text(base + "/memory.events"))
            try:
                s["mem_current"] = int(cur.strip())
            except ValueError:
                s["mem_current"] = None
            s.update(scope_found=True, mem_anon=st.get("anon"), mem_file=st.get("file"), ev_max=ev.get("max"), ev_oom=ev.get("oom"), ev_oom_kill=ev.get("oom_kill"))
    stj = None
    txt = read_text(os.path.join(cfg.status_dir, "status.json"))
    if txt:
        try:
            stj = json.loads(txt)
        except ValueError:
            stj = None
    for k in STATUS_KEYS:
        s[k] = stj.get(k) if isinstance(stj, dict) else None
    ts_ms = stj.get("ts_ms") if isinstance(stj, dict) else None
    s["status_age_s"] = round(now - ts_ms / 1000.0, 1) if isinstance(ts_ms, (int, float)) else None
    la = read_text("/proc/loadavg")
    try:
        s["load1"] = float(la.split()[0]) if la else None
    except (ValueError, IndexError):
        s["load1"] = None
    cpu, io = parse_psi(read_text("/proc/pressure/cpu")), parse_psi(read_text("/proc/pressure/io"))
    s.update(psi_cpu_some_avg10=cpu.get("some"), psi_io_some_avg10=io.get("some"), psi_io_full_avg10=io.get("full"))
    fu = parse_show(systemctl(["show", cfg.follower_unit, "-p", "NRestarts", "-p", "ActiveEnterTimestamp"]))
    try:
        s["follower_nrestarts"] = int(fu["NRestarts"])
    except (KeyError, ValueError):
        s["follower_nrestarts"] = None
    s["follower_active_enter"] = fu.get("ActiveEnterTimestamp") or None
    return s


# ---- the loop ----------------------------------------------------------------------------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--change-id", required=True)
    ap.add_argument("--out", required=True, help="JSON lines, appended; read back on a restart")
    ap.add_argument("--hours", type=float, default=125.0, help="stop after this many hours (also the run-end estimate when --run-end-epoch is not given)")
    ap.add_argument("--run-end-epoch", type=float, default=None, help="the run's end (EXP-027 E); default: this sidecar's start + --hours")
    ap.add_argument("--catchup-end-epoch", type=float, default=None, help="override the catch-up end; default: the ms of the host's catch_up_done event")
    ap.add_argument("--events", default="", help="the host glue's events.jsonl, <out>/glue/events.jsonl, where TipLiveSource writes catch_up_done ('' = do not read it: then the catch-up end is scope first seen + 50 min)")
    ap.add_argument("--status-dir", default="/var/lib/mal/fast-tip-follower")
    ap.add_argument("--follower-unit", default="mal-fast-tip-follower")
    ap.add_argument("--cgroup-root", default="/sys/fs/cgroup")
    ap.add_argument("--alert-mib", type=float, default=ALERT_MIB)
    return ap


def run(argv: list[str] | None = None, *, clock: Callable[[], float] = time.time, sleep: Callable[[float], None] = time.sleep,
        systemctl: Callable[[list[str]], str] = default_systemctl, out: Any = None) -> int:
    out = out or sys.stdout
    try:
        cfg = build_parser().parse_args(argv)
    except SystemExit as e:
        return EXIT_USAGE if e.code else 0
    start = clock()
    end = start + cfg.hours * 3600.0
    run_end = cfg.run_end_epoch if cfg.run_end_epoch is not None else end
    hs = load_history(cfg.out)
    mon = Monitor(alert_mib=cfg.alert_mib, run_end=run_end, catchup_end=cfg.catchup_end_epoch or hs["catchup_end"], history=hs["history"],
                  pre_lag_max=hs["pre_lag_max"], scope_seen=hs["scope_seen"])
    scan = EventsScan(cfg.events or None)
    scope_first: float | None = hs["scope_first"]
    cg_cache: dict[str, str] = {}
    misses, last_beat, warned_budget, warned_unit = 0, 0.0, False, False
    print(f"SIDECAR start change_id={cfg.change_id} utc={time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(start))} hours={cfg.hours} out={cfg.out}"
          f" history_samples={len(hs['history'])} catchup_end_known={mon.catchup_end is not None}", file=out, flush=True)
    os.makedirs(os.path.dirname(os.path.abspath(cfg.out)), exist_ok=True)
    with open(cfg.out, "a", encoding="utf-8") as fh:
        nxt = start
        while True:
            now = clock()
            s = collect(cfg, now, systemctl, cg_cache)
            if not warned_unit and (s["follower_nrestarts"] is None or s["lag_slots"] is None or s["follower_active_enter"] is None):
                warned_unit = True
                print(f"WARN follower_state_unreadable: NRestarts={s['follower_nrestarts']} lag_slots={s['lag_slots']} (systemctl show {cfg.follower_unit} (an unknown unit gives no ActiveEnterTimestamp) or {cfg.status_dir}/status.json"
                      f" unreadable at the first sample: the restart / backlog / lag alerts are blind until it is)", file=out, flush=True)
            if s["scope_found"]:
                misses = 0
                scope_first = scope_first if scope_first is not None else now
            elif mon.scope_seen:
                misses += 1
            if mon.catchup_end is None and scope_first is not None:
                ev_end = scan.poll()
                if ev_end is not None:
                    mon.catchup_end = ev_end
                elif not cfg.events and now >= scope_first + CATCHUP_BUDGET_S:
                    mon.catchup_end = scope_first + CATCHUP_BUDGET_S
                elif cfg.events and not warned_budget and now >= scope_first + CATCHUP_BUDGET_S:
                    warned_budget = True
                    print(f"WARN catchup_over_budget: no catch_up_done event {int((now - scope_first) / 60)} min after the scope appeared (budget {CATCHUP_BUDGET_S // 60} min);"
                          f" the settle mark may fall inside the catch-up", file=out, flush=True)
            if mon.catchup_end is not None:
                s["catchup_end_ts"] = round(mon.catchup_end, 3)
            alerts = mon.feed(s)
            for w in mon.warns:
                print(w, file=out, flush=True)
            if cfg.events and mon.catchup_end is None and scope_first is not None and now >= scope_first + CATCHUP_GIVEUP_S:
                alerts.append((EXIT_ALERT, f"catchup_end_unknown: no catch_up_done event in {cfg.events} {int((now - scope_first) / 60)} min after the scope appeared (give-up {CATCHUP_GIVEUP_S // 60} min):"
                                           f" M6 would be blind and the catch-up stop rules would stay armed. Read the catch-up end from the host's glue events, then relaunch with"
                                           f" R4B_CATCHUP_END_EPOCH=<epoch seconds of catch_up_done>"))
            fh.write(json.dumps(s, sort_keys=True, separators=(",", ":")) + "\n")
            fh.flush()
            if alerts:
                for code, why in alerts:
                    print(f"ALERT {s['utc']} {why}", file=out, flush=True)
                return alerts[0][0]
            if now - last_beat >= HEARTBEAT_S:
                last_beat = now
                a = s.get("mem_anon")
                print(f"OK {s['utc']} phase={s['phase']} anon_mib={'-' if a is None else round(a / MIB, 1)} slope_mib_h={s.get('anon_slope_mib_h', '-')}"
                      f" proj_mib={s.get('anon_proj_mib', '-')} lag_slots={s.get('lag_slots')} backlog_jumps={s.get('backlog_jumps')} load1={s.get('load1')}", file=out, flush=True)
            if mon.scope_seen and misses >= GONE_MISSES:
                print(f"END {s['utc']} scope_gone: the scope's cgroup has been unreadable for {misses} samples (the host ended or was killed: read the host job's rc;"
                      f" an oom_kill that happened after the last sample is not visible here)", file=out, flush=True)
                return EXIT_OK
            if not mon.scope_seen and now - start >= SCOPE_WAIT_S:
                print(f"ALERT {s['utc']} scope_never_started: no r4b-dry-{cfg.change_id}.scope {int((now - start) / 60)} min after the sidecar started", file=out, flush=True)
                return EXIT_NO_SCOPE
            if now >= end:
                print(f"END {s['utc']} hours_elapsed", file=out, flush=True)
                return EXIT_OK
            interval = FAST_INTERVAL_S if (mon.catchup_end is None or now < mon.catchup_end + FAST_UNTIL_S) else SLOW_INTERVAL_S
            nxt = max(nxt + interval, now + 1.0)  # never schedule in the past: a slow sample does not cause a burst
            sleep(max(0.0, min(nxt - clock(), interval)))


def main() -> int:
    return run(sys.argv[1:])


if __name__ == "__main__":
    sys.exit(main())
