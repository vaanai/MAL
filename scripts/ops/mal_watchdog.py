#!/usr/bin/env python3
"""Host-side watchdog: raises alert lines only when the manager is needed.

stdlib only. Allowed external commands: `miscusi-agent job list` and
`ps -eo pid,rss,args`. Never reads job status/log, never touches
/data/mal/meta-capture, never opens trade or HELM file contents.
"""
import argparse
import fnmatch
import datetime as dt
import glob
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time

SEALED = "/data/mal/meta-capture"
MISCUSI = os.path.expanduser("~/.miscusi/bin/miscusi-agent")
DEFAULTS = {
    "check_minute": 23,
    "realert_hours": 6,
    "watch": [],
    "expected_done_ok": [],
    "disk_paths": ["/", "/data2"],
    "disk_used_alert_pct": 90,
    "walker_dir": "/data/mal/blocks/forward-1016",
    "walker_free_alert_pct": 21.5,
    "walker_stale_hours": 4,
    "walker_job_seq": 1036,
    "walker_successor_seqs": [1290],
    "mem_avail_alert_gib": 16,
    "meta_rss_alert_mb": 2200,
    "meta_job_seq": 1814,
    "helm_dir": "/data/mal/hunt-1008/push-1016/hunt29/p1/",
    "spill_globs": [],
    # r4-b-T C0 entries (check_r4bt_c0). None = off. Otherwise a dict, see R4BT_KEYS.
    "r4bt_c0": None,
}
# Job states are compared case-insensitively ("PASS" == "Passed"-family).
OK_STATES = {"running", "queued", "paused", "starting"}
DONE_STATES = {"done", "passed", "pass"}
# FAIL = MiScusi ended the job as a verdict: it wrote ok:false (or verdict
# fail), WHATEVER its exit code. That includes safety exits (h5-enforce-loop.sh
# exits 4 with ok:false), so an unwatched FAIL is a real failure class.
VERDICT_FAIL = {"fail"}
# Interrupted = stopped without an exit status (killed outside MiScusi, or the
# machine went down): a lost job.
LOST_STATES = {"interrupted"}
# Unwatched jobs in these states alert once per seq:state, like Failed/Killed.
ENDED_NEW = VERDICT_FAIL | LOST_STATES
# A watched job last seen in one of these may age out of `job list` quietly.
FINISHED_STATES = DONE_STATES | VERDICT_FAIL
AGED_OUT = "aged_out"  # watch_seen marker: absence already recorded as info
BAD_NEW = {"failed", "killed"}
ROW = re.compile(r"^#(\d+)\s+(\S+)\s*(.*)$")
TRADES = re.compile(r"^trades-(\d{4}-\d{2}-\d{2}T\d{2})\.jsonl(\.zst)?$")
# The ONLY allowlisted exception to the forward-* listing refusal: names of
# this one directory (manager disk-guard practice since 10-16; names only).
WALKER_TRADES = "/data/mal/blocks/forward-1016/trades"
KNOWN = OK_STATES | DONE_STATES | VERDICT_FAIL | LOST_STATES | BAD_NEW | {"cancelled"}
FWD = "/data/mal/blocks/forward-*"
SHA_PREFIX = 12  # hex chars of the config sha256 shown in alerts and status lines
CONFIG_ALERT = "config:reload_failed"


def _wild(p):
    return any(c in p for c in "*?[")


def _hits(pat, protected):
    """Component-wise: could `pat` (non-recursive glob) reach a protected dir
    or anything below it?"""
    pc = [c for c in pat.split("/") if c]
    for pr in protected:
        rc = [c for c in pr.split("/") if c]
        if len(pc) >= len(rc) and all(fnmatch.fnmatchcase(r, q) or fnmatch.fnmatchcase(q, r)
                                      for q, r in zip(pc, rc)):
            return True
    return False


def refuse_sealed(path, listing=False):
    """Refuse meta-capture (always) and forward-* (when listing). Resolves
    symlinks; for globs refuses any pattern that could expand into them."""
    protected = [SEALED] + ([FWD] if listing else [])
    raw = os.path.abspath(os.path.expanduser(str(path)))
    if listing and raw == WALKER_TRADES and os.path.realpath(raw) == WALKER_TRADES:
        return raw
    if _wild(raw):
        if _hits(raw, protected):
            raise ValueError("refused: glob could reach sealed path")
        return raw
    real = os.path.realpath(raw)
    for cand in (raw, real):
        if cand == SEALED or cand.startswith(SEALED + "/"):
            raise ValueError("refused: path under sealed " + SEALED)
        if listing and re.match(r"^/data/mal/blocks/forward-[^/]*(/|$)", cand):
            raise ValueError("refused: listing under forward-*")
    return raw


def run_cmd(argv):
    """Real command runner. Only two argv shapes are allowed."""
    if argv[0].endswith("miscusi-agent"):
        if list(argv[1:]) not in (["job", "list"], ["job", "list", "--active"]):
            raise ValueError("refused: miscusi-agent only runs `job list`")
    elif argv[:1] == ["ps"]:
        if list(argv) != ["ps", "-eo", "pid,rss,args"]:
            raise ValueError("refused: ps shape")
    else:
        raise ValueError("refused command")
    return subprocess.run(argv, capture_output=True, text=True, timeout=60, check=True).stdout


def default_listdir(d):
    p = refuse_sealed(d, listing=True)
    with os.scandir(p) as it:
        return [e.name for e in it]  # names only


def default_glob(pat):
    refuse_sealed(pat, listing=True)
    out = []
    for m in glob.glob(pat):
        refuse_sealed(os.path.realpath(m), listing=True)
        out.append(m)
    return out


def parse_job_list(text):
    """Return {seq: (status, rowtext)}. Raises ValueError if the output is not
    a job table (no header, no rows, or any non-blank row that is not a job)."""
    lines = [l for l in text.splitlines() if l.strip()]
    if not lines or not lines[0].startswith("#seq"):
        raise ValueError("job list: header missing or output empty")
    out = {}
    for line in lines[1:]:
        m = ROW.match(line)
        if not m:
            raise ValueError("job list: unparseable row")
        out[int(m.group(1))] = (m.group(2), line)
    if not out:
        raise ValueError("job list: zero rows")
    return out


def _is_int(x):
    return isinstance(x, int) and not isinstance(x, bool)


def _is_num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


R4BT_PATH_KEYS = ("state_dir", "declog_dir", "a3_carry_path", "twin_dir", "tieout_dir")
R4BT_HEALTH_PATH = "/var/lib/mal-r4b-c0/health.json"
R4BT_NUM_KEYS = {"health_unknown_min": 30, "declog_stale_min": 30, "a3_carry_max_age_min": 120, "twin_stale_min": 30,
                  "parity_max_age_min": 10, "tieout_grace_h": 3}
R4BT_ALERT_TARGET = "FILL:alert_target"  # placeholder: the manager fills in where r4bt alerts are sent
R4BT_NAME = re.compile(r"[^a-z0-9_:.-]")
R4BT_MAX_BYTES = 8_000_000


def r4bt_conf(raw):
    """Defaults merged over the r4bt_c0 config dict (validated by validate_config)."""
    c = dict(R4BT_NUM_KEYS)
    c["alert_target"] = R4BT_ALERT_TARGET
    c["health_path"] = R4BT_HEALTH_PATH
    c.update(raw)
    return c


def validate_config(cfg):
    """Type-check what the passes and the loop schedule depend on. The config is
    re-read every pass, so a wrong type must be refused at load (last good config
    kept), never found later as a crashed loop or a check_error on every pass."""
    def bad(k, what):
        raise ValueError("config %s must be %s" % (k, what))
    for k in ("walker_job_seq", "meta_job_seq"):
        if not _is_int(cfg[k]):
            bad(k, "an int")
    if not _is_int(cfg["check_minute"]) or not 0 <= cfg["check_minute"] <= 59:
        bad("check_minute", "an int in 0..59")
    for k in ("realert_hours", "disk_used_alert_pct", "walker_free_alert_pct", "walker_stale_hours",
              "mem_avail_alert_gib", "meta_rss_alert_mb"):
        if not _is_num(cfg[k]):
            bad(k, "a number")
    for k in ("watch", "expected_done_ok", "walker_successor_seqs"):
        if not isinstance(cfg[k], list) or not all(_is_int(x) for x in cfg[k]):
            bad(k, "a list of ints")
    for k in ("disk_paths", "spill_globs"):
        if not isinstance(cfg[k], list) or not all(isinstance(x, str) for x in cfg[k]):
            bad(k, "a list of strings")
    for k in ("walker_dir", "helm_dir"):
        if not isinstance(cfg[k], str):
            bad(k, "a string")
    r = cfg["r4bt_c0"]
    if r is not None:
        if not isinstance(r, dict):
            bad("r4bt_c0", "null or an object")
        for k in R4BT_PATH_KEYS:
            if not isinstance(r.get(k), str) or not r[k]:
                bad("r4bt_c0.%s" % k, "a non-empty string")
        for k in R4BT_NUM_KEYS:
            if k in r and (not _is_num(r[k]) or r[k] <= 0):
                bad("r4bt_c0.%s" % k, "a positive number")
        for k in ("alert_target", "health_path"):
            if k in r and (not isinstance(r[k], str) or not r[k]):
                bad("r4bt_c0." + k, "a non-empty string")


def read_config(path):
    """Return (cfg, sha256 hex). Parses the same bytes it hashes, so the sha
    always names the config actually in use (same value as `sha256sum`)."""
    refuse_sealed(path)
    with open(path, "rb") as f:  # missing config is an error, never silent defaults
        raw = f.read()
    obj = json.loads(raw)
    if not isinstance(obj, dict):
        raise ValueError("config is not a JSON object")
    cfg = dict(DEFAULTS)
    cfg.update({k: v for k, v in obj.items() if not k.startswith("_")})
    validate_config(cfg)
    refuse_sealed(cfg["walker_dir"])
    refuse_sealed(cfg["helm_dir"], listing=True)
    for g in cfg["spill_globs"]:
        refuse_sealed(g, listing=True)
    for p in cfg["disk_paths"]:
        refuse_sealed(p)
    if cfg["r4bt_c0"]:
        for k in R4BT_PATH_KEYS:
            refuse_sealed(cfg["r4bt_c0"][k], listing=True)
        if cfg["r4bt_c0"].get("health_path"):
            refuse_sealed(cfg["r4bt_c0"]["health_path"], listing=True)
    return cfg, hashlib.sha256(raw).hexdigest()


def load_config(path):
    return read_config(path)[0]


class ConfigReloader:
    """Holds the live config of the --loop. The first load raises on a missing
    or bad file (a watchdog must not start without its config). reload() then
    re-reads the file at the start of every pass: a changed sha swaps the config
    in; a failed read keeps the last good config and returns one alert."""

    def __init__(self, path):
        self.path = path
        self.cfg, self.sha = read_config(path)
        self.failing = False

    def events(self, alerts=(), info=()):
        return {"alerts": list(alerts), "info": list(info), "sha": self.sha[:SHA_PREFIX]}

    def reload(self):
        """Return (cfg, events); events feed run_pass(cfg_events=...)."""
        try:
            cfg, sha = read_config(self.path)
        except Exception as e:  # missing, bad JSON, bad type, sealed path: all keep the last good config
            self.failing = True
            return self.cfg, self.events(alerts=[A(
                CONFIG_ALERT, "high",
                "config reload failed, keeping last good config sha256 %s: %s: %s"
                % (self.sha[:SHA_PREFIX], type(e).__name__, str(e)[:160]))])
        info = []
        if self.failing:
            self.failing = False
            info.append("config reload recovered, sha256 %s" % sha[:SHA_PREFIX])
        if sha != self.sha:
            info.append("config changed: sha256 %s -> %s" % (self.sha[:SHA_PREFIX], sha[:SHA_PREFIX]))
            self.cfg, self.sha = cfg, sha
        return self.cfg, self.events(info=info)


def A(key, sev, msg):
    return {"key": key, "severity": sev, "msg": msg}


def check_jobs(cfg, ctx):
    """First pass baselines already-Failed jobs without alerting. Watched jobs
    in a bad state re-alert every realert_hours until fixed (intended).
    Unwatched Cancelled is normal and never alerts.

    Known states (case-insensitive): starting is in flight; pass is done;
    fail is a job MiScusi ended as a verdict (ok:false, any exit code);
    interrupted is a job the machine lost. Unwatched fail/interrupted alert
    once per seq:state (medium, baselined silently on the first pass, tracked
    in `ended_seen`), exactly like unwatched Failed/Killed. Watched fail is
    info once; watched interrupted is medium and re-alerts. A watched job last
    seen in a finished state that has aged out of `job list` is info once, not
    an alert; one never seen finished stays a high alert."""
    text = ctx["run"]([MISCUSI, "job", "list"])
    rows = parse_job_list(text)  # raises -> check_error:jobs
    jobs = {k: v[0] for k, v in rows.items()}
    ctx["jobs"] = jobs
    ctx["job_rows"] = {k: v[1] for k, v in rows.items()}
    alerts, info = [], []
    done_ok = set(cfg["expected_done_ok"])
    watch = set(cfg["watch"])
    prev_seen = ctx["state"].get("watch_seen", {})
    if not isinstance(prev_seen, dict):
        prev_seen = {}
    wkeys = {str(x) for x in cfg["watch"]}
    watch_seen = {k: v for k, v in prev_seen.items() if k in wkeys}  # prune unwatched
    for seq in cfg["watch"]:
        st = jobs.get(seq)
        if st is None:
            last = prev_seen.get(str(seq))
            if isinstance(last, str) and last in FINISHED_STATES:
                info.append("job #%d aged out of the list (last seen %s)" % (seq, last))
                watch_seen[str(seq)] = AGED_OUT
            elif not (isinstance(last, str) and last == AGED_OUT):
                alerts.append(A("job:%d:missing" % seq, "high", "job #%d not in list" % seq))
            continue
        s = st.lower()
        watch_seen[str(seq)] = s
        if s in OK_STATES:
            continue
        if s in DONE_STATES and seq in done_ok:
            info.append("job #%d %s (expected)" % (seq, st))
            continue
        if s in VERDICT_FAIL:
            if prev_seen.get(str(seq)) != s:  # a verdict is final: tell once
                alerts.append(A("job:%d:%s" % (seq, st), "info",
                                "job #%d is %s (ended as a verdict: ok:false, any exit code)" % (seq, st)))
            continue
        if s in LOST_STATES:
            alerts.append(A("job:%d:%s" % (seq, st), "medium",
                            "job #%d is %s (the machine lost the job)" % (seq, st)))
            continue
        alerts.append(A("job:%d:%s" % (seq, st), "high", "job #%d is %s" % (seq, st)))
    ctx["new_state"]["watch_seen"] = watch_seen
    for seq, st in jobs.items():
        if st.lower() not in KNOWN and seq not in watch:
            alerts.append(A("job:%d:%s" % (seq, st), "medium", "job #%d unknown status %s" % (seq, st)))
    seen = set(ctx["state"].get("failed_seen", []))
    first = "failed_seen" not in ctx["state"]
    cur = set()
    for seq, st in jobs.items():
        if st.lower() in BAD_NEW:
            tag = "%d:%s" % (seq, st)
            cur.add(tag)
            if tag not in seen and not first and seq not in watch:
                alerts.append(A("job:%d:%s" % (seq, st), "medium", "job #%d newly %s" % (seq, st)))
    ctx["new_state"]["failed_seen"] = sorted(seen | cur)
    # FAIL / Interrupted, unwatched: same once-only alert as Failed. Own key so
    # the first pass after deploy baselines the ones already in the list
    # (they were already alerted as "unknown status") instead of a burst.
    ended_prev = ctx["state"].get("ended_seen")
    first_e = not isinstance(ended_prev, list)
    ended_seen = set() if first_e else {x for x in ended_prev if isinstance(x, str)}
    ended_cur = set()
    for seq, st in jobs.items():
        if st.lower() in ENDED_NEW:
            tag = "%d:%s" % (seq, st)
            ended_cur.add(tag)
            if tag not in ended_seen and not first_e and seq not in watch:
                alerts.append(A("job:%d:%s" % (seq, st), "medium", "job #%d newly %s" % (seq, st)))
    ctx["new_state"]["ended_seen"] = sorted(ended_seen | ended_cur)
    return alerts, info, {"n_jobs": len(jobs)}


def check_disk(cfg, ctx):
    alerts = []
    summ = {}
    for p in cfg["disk_paths"]:
        u = ctx["disk_usage"](p)
        pct = 100.0 * u.used / u.total if u.total else 0.0
        summ[p] = round(pct, 1)
        if pct >= cfg["disk_used_alert_pct"]:
            alerts.append(A("disk:" + p, "high", "%s used %.1f%%" % (p, pct)))
    wd = cfg["walker_dir"]
    u = ctx["disk_usage"](wd)
    free = 100.0 * u.free / u.total if u.total else 0.0
    summ["walker_free_pct"] = round(free, 1)
    ctx["walker_free"] = free
    if free < cfg["walker_free_alert_pct"]:
        alerts.append(A("walker:disk", "high", "walker dir free %.1f%%" % free))
    return alerts, [], summ


def check_walker(cfg, ctx):
    """Walker freshness from file NAMES in forward-1016/trades (os.scandir
    names only; never open/stat/read a file). Manager disk-guard practice
    since 10-16; names only. No matching name -> check_error:walker."""
    d = os.path.join(cfg["walker_dir"], "trades")
    hours = [m.group(1) for m in (TRADES.match(n) for n in ctx["listdir"](d)) if m]
    if not hours:
        raise RuntimeError("no trades-<hour>.jsonl[.zst] names found")
    newest = max(hours)
    t = dt.datetime.strptime(newest, "%Y-%m-%dT%H").replace(tzinfo=dt.timezone.utc)
    age_h = (ctx["now"] - t).total_seconds() / 3600.0
    seqs = [cfg["walker_job_seq"]] + list(cfg["walker_successor_seqs"])
    running = any(ctx["jobs"].get(x, "").lower() == "running" for x in seqs)
    alerts = []
    if running and age_h > cfg["walker_stale_hours"]:
        alerts.append(A("walker:stale", "high", "newest trades hour %s is %.1fh old" % (newest, age_h)))
    return alerts, [], {"walker_newest_hour": newest, "walker_age_h": round(age_h, 1)}


def check_mem(cfg, ctx):
    kb = None
    for line in ctx["read_meminfo"]().splitlines():
        if line.startswith("MemAvailable:"):
            kb = int(line.split()[1])
    if kb is None:
        raise RuntimeError("no MemAvailable")
    gib = kb / 1048576.0
    ctx["mem_gib"] = gib
    alerts = []
    if gib < cfg["mem_avail_alert_gib"]:
        alerts.append(A("mem:low", "high", "MemAvailable %.1f GiB" % gib))
    return alerts, [], {"mem_avail_gib": round(gib, 1)}


def check_meta(cfg, ctx):
    rows = []
    for line in ctx["run"](["ps", "-eo", "pid,rss,args"]).splitlines()[1:]:
        parts = line.split(None, 2)
        if len(parts) == 3 and "python -m tools.meta_capture" in parts[2] and parts[0].isdigit():
            rows.append((int(parts[0]), int(parts[1]) / 1024.0))
    alerts = []
    mx = max([r[1] for r in rows], default=0.0)
    ctx["meta_rss"] = mx
    for pid, mb in rows:
        if mb > cfg["meta_rss_alert_mb"]:
            alerts.append(A("meta:rss", "high", "meta_capture pid %d RSS %.0f MB" % (pid, mb)))
    if not rows and ctx["jobs"].get(cfg["meta_job_seq"], "").lower() == "running":
        alerts.append(A("meta:absent", "high", "job #%d Running but no process" % cfg["meta_job_seq"]))
    return alerts, [], {"meta_procs": len(rows), "meta_rss_mb": round(mx, 0)}


def check_helm(cfg, ctx):
    names = sorted(n for n in ctx["listdir"](cfg["helm_dir"]) if n.startswith("HELM-REPLY-"))
    first = "helm_seen" not in ctx["state"]
    seen = set(ctx["state"].get("helm_seen", []))
    alerts = []
    if not first:
        for n in names:
            if n not in seen:
                alerts.append(A("helm:" + n, "high", "new Helm reply file " + n))
    ctx["new_state"]["helm_seen"] = sorted(seen | set(names))
    return alerts, [], {"helm_files": len(names)}


def check_spill(cfg, ctx):
    cnt = 0
    for g in cfg["spill_globs"]:
        for p in ctx["glob"](g):
            try:
                if (ctx["now"].timestamp() - ctx["mtime"](p)) > 120 * 60:
                    cnt += 1
            except OSError:
                pass
    info = ["%d dead spill files >120min" % cnt] if cnt else []
    return [], info, {"spill_old": cnt}


def _r4bt_read_json(ctx, path):
    """(obj, why). Size-capped read of a small status file; why is a short label, never file contents."""
    try:
        raw = ctx["read_small"](path)
    except FileNotFoundError:
        return None, "missing"
    except OSError:
        return None, "unreadable"
    try:
        obj = json.loads(raw)
    except ValueError:
        return None, "not_json"
    if not isinstance(obj, dict):
        return None, "not_object"
    return obj, None


def _r4bt_utc_s(v):
    try:
        return dt.datetime.strptime(v, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc).timestamp()
    except (ValueError, TypeError):
        return None


def check_r4bt_c0(cfg, ctx):
    """r4-b-T C0 health (build-list item 8). Alerts carry names only: never a value from a
    file. Contents read: the health export (booleans latched / stop_present / live_ok_present
    and a short tier label; written by the executor to /var/lib/mal-r4b-c0/health.json, absent
    = unknown, alert after health_unknown_min) and the A3 carry's written_utc / hard_halt.
    Names and mtimes only, never opened: the declog dir, twins.run.json (reader-pinned to the
    executor gate and the parity tool), r4bt-parity-state.json (stat only if the executor's
    0750 state dir lets this user), the tie-out sign-off marker (presence). The executor's
    state files are never read. Each sub-check is its own alert, so one broken input cannot
    hide the others."""
    r = r4bt_conf(cfg["r4bt_c0"])
    now = ctx["now"].timestamp()
    alerts = []
    summ = {"alert_target": r["alert_target"]}
    sd = r["state_dir"]

    # 1. executor health export (booleans only; the executor's state dir is not readable by this user)
    infos = []
    hp = r["health_path"]
    health, why = _r4bt_read_json(ctx, hp)
    if why in ("missing", "unreadable"):
        # unknown, not a pass: alert once it has been unknown for health_unknown_min
        since = ctx["state"].get("r4bt_health_unknown_since")
        if not _is_num(since):
            since = now
        ctx["new_state"]["r4bt_health_unknown_since"] = since
        mins = (now - since) / 60.0
        summ["health"] = "unknown"
        infos.append("r4bt health export %s (unknown for %.0f min)" % (why, mins))
        if mins >= r["health_unknown_min"]:
            alerts.append(A("r4bt:health_unknown", "high", "r4bt health export %s for %.0f min" % (why, mins)))
    elif why:
        summ["health"] = "bad"
        alerts.append(A("r4bt:health_bad", "high", "r4bt health export is %s" % why))
    else:
        ctx["new_state"]["r4bt_health_unknown_since"] = None
        summ["health"] = {k: health[k] for k in ("latched", "stop_present", "live_ok_present") if isinstance(health.get(k), bool)}
        if isinstance(health.get("tier"), (int, str)) and not isinstance(health.get("tier"), bool):
            summ["health"]["tier"] = R4BT_NAME.sub("_", str(health["tier"]).lower())[:16]
        if any(not isinstance(health.get(k), bool) for k in ("latched", "stop_present", "live_ok_present")):
            alerts.append(A("r4bt:health_bad", "high", "r4bt health export lacks a boolean field"))
        if health.get("latched") is True:
            alerts.append(A("r4bt:latched", "high", "r4bt executor reports a latch"))
        if health.get("stop_present") is True:
            alerts.append(A("r4bt:stop_present", "high", "r4bt executor reports STOP present"))
        try:
            age_min = (now - ctx["mtime"](hp)) / 60.0
            if age_min > r["health_unknown_min"]:
                alerts.append(A("r4bt:health_stale", "high", "r4bt health export is %.0f min old" % age_min))
        except OSError:
            pass

    # 2. declog: newest file mtime
    try:
        dn = ctx["listdir"](r["declog_dir"])
        ages = []
        for n in sorted(dn)[-500:]:
            try:
                ages.append(ctx["mtime"](os.path.join(r["declog_dir"], n)))
            except OSError:
                pass
        if not ages:
            alerts.append(A("r4bt:declog_stale", "high", "r4bt declog dir has no readable file mtime"))
        else:
            age_min = (now - max(ages)) / 60.0
            summ["declog_age_min"] = round(age_min, 1)
            if age_min > r["declog_stale_min"]:
                alerts.append(A("r4bt:declog_stale", "high", "r4bt declog newest file is %.0f min old" % age_min))
    except OSError:
        alerts.append(A("r4bt:declog_stale", "high", "r4bt declog dir cannot be listed"))

    # 3. A3 carry written_utc and hard_halt
    car, why = _r4bt_read_json(ctx, r["a3_carry_path"])
    if why:
        alerts.append(A("r4bt:a3_carry_stale", "high", "r4bt A3 carry %s" % why))
    else:
        w = _r4bt_utc_s(car.get("written_utc"))
        if w is None:
            alerts.append(A("r4bt:a3_carry_stale", "high", "r4bt A3 carry has no valid written_utc"))
        else:
            age_min = (now - w) / 60.0
            summ["a3_carry_age_min"] = round(age_min, 1)
            if age_min > r["a3_carry_max_age_min"] or age_min < -5:
                alerts.append(A("r4bt:a3_carry_stale", "high", "r4bt A3 carry written %.0f min ago (bound %s)" % (age_min, r["a3_carry_max_age_min"])))
        if car.get("hard_halt") is True:
            alerts.append(A("r4bt:a3_carry_latching", "high", "r4bt A3 carry is a latching carry (hard_halt)"))

    # 4. twin run record: mtime only
    try:
        age_min = (now - ctx["mtime"](os.path.join(r["twin_dir"], "twins.run.json"))) / 60.0
        summ["twin_run_age_min"] = round(age_min, 1)
        if age_min > r["twin_stale_min"]:
            alerts.append(A("r4bt:twin_run_stale", "high", "r4bt twin run record is %.0f min old" % age_min))
    except OSError:
        alerts.append(A("r4bt:twin_run_stale", "high", "r4bt twin run record missing or unreadable"))

    # 5. parity state: mtime only, and only if the watchdog can stat it (the state dir is the executor's, 0750)
    try:
        age_min = (now - ctx["mtime"](os.path.join(sd, "r4bt-parity-state.json"))) / 60.0
        summ["parity_age_min"] = round(age_min, 1)
        if age_min > r["parity_max_age_min"] or age_min < -5:
            alerts.append(A("r4bt:parity_state_bad", "high", "r4bt parity state is %.0f min old (bound %s)" % (age_min, r["parity_max_age_min"])))
    except FileNotFoundError:
        alerts.append(A("r4bt:parity_state_bad", "high", "r4bt parity state missing"))
    except OSError:
        summ["parity_age_min"] = "not_statable"
        infos.append("r4bt parity state not statable by the watchdog")

    # 6. tie-out for yesterday (UTC), after a grace
    nowdt = ctx["now"]
    ydate = (nowdt - dt.timedelta(days=1)).strftime("%Y-%m-%d")
    summ["tieout_day"] = ydate
    if nowdt.hour >= r["tieout_grace_h"]:
        try:
            ctx["mtime"](os.path.join(r["tieout_dir"], "tieout-%s.json" % ydate))
        except OSError:
            alerts.append(A("r4bt:tieout_unsigned", "high", "r4bt tie-out marker for %s missing" % ydate))
    return alerts, infos, summ


CHECKS = [("jobs", check_jobs), ("disk", check_disk), ("walker", check_walker), ("mem", check_mem),
          ("meta", check_meta), ("helm", check_helm), ("spill", check_spill), ("r4bt_c0", check_r4bt_c0)]


def _read_small(path):
    refuse_sealed(path)
    with open(path, "rb") as f:
        raw = f.read(R4BT_MAX_BYTES + 1)
    if len(raw) > R4BT_MAX_BYTES:
        raise OSError("too large")
    return raw


def default_ctx(now=None):
    return {
        "now": now or dt.datetime.now(dt.timezone.utc),
        "run": run_cmd,
        "disk_usage": shutil.disk_usage,
        "listdir": default_listdir,
        "read_meminfo": lambda: open("/proc/meminfo").read(),
        "glob": default_glob,
        "mtime": os.path.getmtime,
        "read_small": _read_small,
    }


def iso(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def load_state(sd, now=None, dry_run=False):
    """Returns (state, error). A corrupt state.json is backed up and reported."""
    p = os.path.join(sd, "state.json")
    if not os.path.exists(p):
        return {}, None
    try:
        with open(p) as f:
            st = json.load(f)
        if not isinstance(st, dict):
            raise ValueError("not an object")
        return st, None
    except (OSError, ValueError) as e:
        if not dry_run:
            try:
                shutil.copy2(p, p + ".corrupt-" + (now or dt.datetime.now(dt.timezone.utc)).strftime("%Y%m%dT%H%M%S"))
            except OSError:
                pass
        return {}, "%s: %s" % (type(e).__name__, str(e)[:100])


def append(path, obj):
    with open(path, "a") as f:
        f.write(json.dumps(obj, sort_keys=True) + "\n")


def run_pass(cfg, state_dir, ctx, dry_run=False, cfg_events=None):
    """cfg_events (from ConfigReloader): alerts and info lines about the config
    itself, plus its sha prefix for the status line."""
    now = ctx["now"]
    ev = cfg_events or {}
    state, serr = load_state(state_dir, now, dry_run)
    ctx["state"] = state
    ctx["new_state"] = {}
    ctx["jobs"] = {}
    ctx["job_rows"] = {}
    alerts, infos, summ = list(ev.get("alerts", [])), list(ev.get("info", [])), {}
    if serr:
        alerts.append(A("state:unreadable", "high", "state.json unreadable (backed up), baselines reset: " + serr))
    for name, fn in CHECKS:
        if name == "r4bt_c0" and not cfg.get("r4bt_c0"):
            continue  # off unless configured
        try:
            a, i, s = fn(cfg, ctx)
            alerts += a
            infos += i
            summ[name] = s
        except Exception as e:  # fail loud
            alerts.append(A("check_error:" + name, "high", "%s: %s" % (type(e).__name__, str(e)[:200])))
            summ[name] = "error"
            if name == "jobs":
                # keep prior failed_seen if the list could not be read
                for k in ("failed_seen", "ended_seen"):
                    if k in state:
                        ctx["new_state"][k] = state[k]
    ts = iso(now)
    last = state.get("last_alert", {})
    if cfg_events is not None and not ev.get("alerts"):
        last.pop(CONFIG_ALERT, None)  # reload healthy again: a later failure is a new episode
    window = cfg["realert_hours"] * 3600
    fresh = []
    for a in alerts:
        t = last.get(a["key"])
        if t is not None and now.timestamp() - t < window:
            continue
        last[a["key"]] = now.timestamp()
        fresh.append(dict(a, ts=ts))
    st = dict(state)
    st.update(ctx["new_state"])
    st["last_alert"] = last
    daily = st.setdefault("daily", {"date": ts[:10], "passes": 0, "alerts": 0, "min_walker_free": None, "max_meta_rss": 0})
    daily_line = None
    day = ts[:10]
    if daily["date"] != day and (now.hour, now.minute) >= (0, 30):
        # first pass after 00:30Z on a new day: emit the finished day, reset
        daily_line = dict(daily, ts=ts, kind="daily")
        st["daily"] = {"date": day, "passes": 0, "alerts": 0, "min_walker_free": None, "max_meta_rss": 0}
        daily = st["daily"]
    daily["passes"] += 1
    daily["alerts"] += len(fresh)
    wf = ctx.get("walker_free")
    if wf is not None:
        daily["min_walker_free"] = round(wf, 1) if daily["min_walker_free"] is None else min(daily["min_walker_free"], round(wf, 1))
    daily["max_meta_rss"] = max(daily["max_meta_rss"], round(ctx.get("meta_rss", 0)))
    line = {"ts": ts, "checks": summ, "alert_keys": [a["key"] for a in alerts],
            "new_alerts": [a["key"] for a in fresh], "info": infos}
    if ev.get("sha"):
        line["config_sha256"] = ev["sha"]
    if not dry_run:
        os.makedirs(state_dir, exist_ok=True)
        append(os.path.join(state_dir, "status.jsonl"), line)
        for a in fresh:
            append(os.path.join(state_dir, "alerts.jsonl"), a)
        if daily_line:
            append(os.path.join(state_dir, "daily.jsonl"), daily_line)
        tmp = os.path.join(state_dir, "state.json.tmp")
        with open(tmp, "w") as f:
            json.dump(st, f)
        os.replace(tmp, os.path.join(state_dir, "state.json"))
        with open(os.path.join(state_dir, "heartbeat"), "w") as f:
            f.write(ts + "\n")
    return line, fresh


def next_check(now, minute):
    t = now.replace(minute=minute, second=0, microsecond=0)
    if t <= now:
        t += dt.timedelta(hours=1)
    return t


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="/data/mal/hunt-1008/ops/watchdog/config.json")
    ap.add_argument("--state-dir", default="/data/mal/hunt-1008/ops/watchdog/")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--once", action="store_true")
    g.add_argument("--loop", action="store_true")
    ap.add_argument("--duration-s", type=int, default=14 * 86400)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    rl = ConfigReloader(a.config)  # missing or bad config at start is an error
    refuse_sealed(a.state_dir)
    if not a.loop:
        line, _ = run_pass(rl.cfg, a.state_dir, default_ctx(), a.dry_run, rl.events())
        print(json.dumps(line, sort_keys=True))
        return 0
    end = time.time() + a.duration_s
    safe_pass(rl.cfg, a, rl)
    while True:
        nxt = next_check(dt.datetime.now(dt.timezone.utc), int(rl.cfg["check_minute"]))
        if nxt.timestamp() >= end:
            time.sleep(max(0, end - time.time()))
            return 0
        time.sleep(max(0, nxt.timestamp() - time.time()))
        safe_pass(rl.cfg, a, rl)


def safe_pass(cfg, a, reloader=None):
    """One loop pass that can never kill the loop; failures go to stderr. With a
    reloader the config is re-read first, so edits to the watch list apply on the
    next pass without a restart; a failed reload keeps the last good config."""
    try:
        events = None
        if reloader is not None:
            cfg, events = reloader.reload()
        line, _ = run_pass(cfg, a.state_dir, default_ctx(), a.dry_run, events)
        print(json.dumps(line, sort_keys=True), flush=True)
    except Exception as e:
        print("WATCHDOG_PASS_FAILED %s: %s" % (type(e).__name__, str(e)[:200]), file=sys.stderr, flush=True)


if __name__ == "__main__":
    sys.exit(main())
