#!/usr/bin/env python3
"""osb_live_extract: step L of the open-sim accuracy check (docs: open-sim board design section 3). Run on mal-fast-0 as a MiScusi manager job: `extract` under `sudo -n` (the live
files are root-only), then `push` as the invoking user. PAPER ONLY. Nothing here says the lane makes money.

READS (O_RDONLY | O_NOFOLLOW, no lock, no lock file, nothing written under any live dir):
  1 host decision rows   <root>/decisions/l1-floor-v2/<day>.jsonl   kinds entry, exit, entry_refused_gap, entry_gap_span_ignored, lane_restart_shadow, lane_restart_shadow_failed, start, lane_start
                         (a line is parsed only if it contains one of those kind strings as bytes)
  2 bridge log           <state>/bridge-log-live.jsonl              kinds buy_sent, buy_port_refused, buy_landed, buy_failed, sell_sent, sell_landed, sell_failed, entry_refused, feed_gap_hold
  3 lane ledger          lane-ledger.jsonl (+ lane-ledger.jsonl.backlog.*) shared with another lane; folded to floor-lane-1 trades by tools.floor_fills_vs_model.fold_trades
  4 fills log            <state>/probe/fills.jsonl                  buy rows: spend_lamports, tokens_received, price_vs_quote_bps (found by signature, else mint+window: floor_fills_vs_model.find_buy)
It never touches the wallet key, the executor config, or any path outside these. The lane host config is read for its `root` only (as floor_killcheck_extract does).

WRITES ~/mal-data/osb-live-extract/osb-live-extract.jsonl and osb-live-extract.meta.json (dir 0700, files 0600, tmp + fsync + rename), then `push` copies both to research-0
/data2/mal/scratch/floor-open-sim/live-extract/ (dir 0700, files 0600, `setfacl -b -k` on the dir BEFORE the first file lands: that tree has a default ACL that ignores the umask).
The extract holds live pool ids and trigger slots (join keys) and is read only by osb_recon.py. Mint and signature are used inside this tool to join and are never written.
STDOUT: counts and sha256 only. Never a pool, mint, signature, price or reserve.

Field names come from the code (arena_rules.py, floor_lane_host.py, arena_live_bridge.py, floor_fills_vs_model.py), not from live data. Where uncertain the reader takes an alias list
(ALIASES below); `--structure-check` prints only field-name presence counts so the manager can see which alias fired before the first run.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import os
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from tools import floor_fills_vs_model as FV  # noqa: E402  (stdlib only; fold_trades / find_buy are the EXP-029 s2 grouping)

CONFIG = "/etc/mal-floor-lane/host.json"
LIVE_ROOT = "/var/lib/mal-live/floor-lane/decisions-root"
STATE_DIR = "/var/lib/mal-live/floor-lane/state"
BRIDGE_LOG = STATE_DIR + "/bridge-log-live.jsonl"
FILLS_LOG = STATE_DIR + "/probe/fills.jsonl"
LEDGER_ROOT = "/var/lib/mal-live/h5-lane"
LEDGER_NAME = "lane-ledger.jsonl"
BOT_ID = "l1-floor-v2"  # host decision-log arm (tools/floor_lane_host.py BOT_ID)
LANE_BOT = "floor-lane-1"  # ledger bot id (EXP-029 s1)
DEFAULT_SINCE_HOUR = "2026-11-11T12"  # book A's start
SCHEMA = "osb-live-extract-v1"
EXTRACT_NAME, META_NAME = "osb-live-extract.jsonl", "osb-live-extract.meta.json"
NEAR_SUBDIR = ("mal-data", "osb-live-extract")
FAR_DIR = "/data2/mal/scratch/floor-open-sim/live-extract"
SSH_HOST = "mal-research-0"
SSH_OPTS = ("-o", "BatchMode=yes", "-o", "ServerAliveInterval=5", "-o", "ServerAliveCountMax=3")
TEST_ENV = "OSB_EXTRACT_TEST"
REAL_PREFIXES = ("/var/lib/mal-live", "/var/lib/mal", "/data2/mal", "/home/claude/mal-data", "/home/claude/data")

HOST_KINDS = ("entry", "exit", "entry_refused_gap", "entry_gap_span_ignored", "lane_restart_shadow", "lane_restart_shadow_failed", "start", "lane_start")
BRIDGE_KINDS = ("buy_sent", "buy_port_refused", "buy_landed", "buy_failed", "sell_sent", "sell_landed", "sell_failed", "entry_refused", "feed_gap_hold")
# documented alias lists: the first name present wins. Uncertain names are listed in the PR body ("needs a structure check").
ALIASES = {
    "trig_slot": ("signal_slot", "trigger_slot"),
    "trig_ms": ("signal_t_recv_ms", "trigger_ms"),
    "spend_lamports": ("spend_lamports",),
    "tokens_received": ("tokens_received", "tokens"),
    "price_vs_quote_bps": ("price_vs_quote_bps",),
    "tokens_source": ("tokens_source",),
    "refusal_why": ("why", "label", "reason"),
    "gap_kind": ("kind",),
}
OID_OK = re.compile(r"[0-9a-f]{16}|[A-Za-z0-9_-]{1,15}")  # the same rule as scripts/research/osb_recon.py OID_OK (kept equal by a test)
KIND_ORDER = ("header", "order", "gap_refusal", "span_ignored", "restart", "start", "feed_gap_hold")


class Refuse(SystemExit):
    pass


def refuse(msg, code=2):
    print("REFUSED: " + msg, file=sys.stderr)
    raise Refuse(code)


def canon(o):
    return json.dumps(o, sort_keys=True, separators=(",", ":"), allow_nan=False)


def is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def pick(row, names):
    for n in names:
        if isinstance(row, dict) and row.get(n) is not None:
            return row[n]
    return None


def pick_one(row, names, field):
    """pick() that refuses when two alias names of ONE field are both present on ONE row (the caller passes the names of that row's own kind). The message names the field only."""
    if not isinstance(row, dict):
        return None
    present = [n for n in names if row.get(n) is not None]
    if len(present) > 1:
        refuse("alias conflict: two names of field %s are both present on one row" % field)
    return row[present[0]] if present else None


def ms_of_hour(h):
    try:
        return int(datetime.strptime(h, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc).timestamp() * 1000)
    except (ValueError, TypeError):
        refuse("--since-hour must be YYYY-MM-DDTHH")


def plain(path):
    if not isinstance(path, str) or not path.startswith("/") or ".." in path.split("/"):
        refuse("an absolute path without `..` is required")
    ap = os.path.abspath(path)
    if os.path.realpath(ap) != ap:
        refuse("a symlink in a path is refused")
    return ap


def invoking_home():
    import pwd
    u = os.environ.get("SUDO_USER")
    return pwd.getpwnam(u).pw_dir if u else os.path.expanduser("~")


def near_dir():
    return os.path.join(invoking_home(), *NEAR_SUBDIR)


MAX_SOURCE_BYTES = 4 << 30  # a hard size refusal: the live files only grow; beyond this the job refuses instead of eating memory


def open_nofollow(path):
    """a binary file object, or None when missing. O_RDONLY | O_NOFOLLOW; no lock. A source above MAX_SOURCE_BYTES is refused."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return None
    except OSError:
        refuse("cannot open a source file (symlink?): %s" % os.path.basename(path))
    if os.fstat(fd).st_size > MAX_SOURCE_BYTES:
        os.close(fd)
        refuse("a source file is above the size limit: %s" % os.path.basename(path))
    return os.fdopen(fd, "rb")


def read_nofollow(path):
    f = open_nofollow(path)
    if f is None:
        return None
    with f:
        return f.read()


def iter_rows(path, stats, label, needles=None, kinds=None):
    """Stream a jsonl file line by line (bounded memory). A line is parsed only if it holds one of `needles` as bytes (when given). A line that does not parse is counted:
    a last line with no newline is a torn write by the live writer (torn_tail), any other is bad_lines; both per source `label`. Yields dict rows (of `kinds`, when given)."""
    f = open_nofollow(path)
    if f is None:
        return
    with f:
        for ln in f:
            if not ln.strip() or (needles and not any(n in ln for n in needles)):
                continue
            try:
                r = json.loads(ln)
            except ValueError:
                key = "torn_tail" if not ln.endswith(b"\n") else "bad_lines"
                stats[key] = stats.get(key, 0) + 1
                stats["%s_%s" % (key, label)] = stats.get("%s_%s" % (key, label), 0) + 1
                continue
            if isinstance(r, dict) and (kinds is None or r.get("kind") in kinds):
                yield r


MS_FIELD = re.compile(rb'"ms"\s*:\s*(\d+)')


def any_row_ms(path, stats):
    """the instant of every line of a host decision day file, of ANY kind (the first top-level-looking "ms" of the line; a line with none is counted in stats['any_row_no_ms']). Used only for
    `prev_last_ms` on start rows: the last sign of life of the host before a start. Streamed; no line is parsed or kept."""
    out = []
    f = open_nofollow(path)
    if f is None:
        return out
    with f:
        for ln in f:
            if not ln.strip():
                continue
            m = MS_FIELD.search(ln)
            if m:
                out.append(int(m.group(1)))
            else:
                stats["any_row_no_ms"] = stats.get("any_row_no_ms", 0) + 1
    return out


def days_between(from_ms, to_ms):
    d0 = datetime.fromtimestamp(from_ms / 1000, tz=timezone.utc).date()
    d1 = datetime.fromtimestamp(max(to_ms - 1, from_ms) / 1000, tz=timezone.utc).date()
    out = []
    while d0 <= d1:
        out.append(d0.strftime("%Y-%m-%d"))
        d0 = d0.fromordinal(d0.toordinal() + 1)
    return out


def gather(decision_root, bridge_log, ledger_root, fills_log, since_ms, as_of_ms):
    """-> (rows, stats, sizes). Pure reading, streamed line by line. A missing bridge log, fills log or ledger, or no decision day file at all, is a refusal: no extract with a silent hole."""
    stats = {"torn_tail": 0, "bad_lines": 0, "day_files_missing": 0, "day_files_read": 0}
    sizes = {}
    host, any_ms = [], []
    kind_needles = [('"%s"' % k).encode() for k in HOST_KINDS]
    for sub in (os.path.join(decision_root, "decisions"), os.path.join(decision_root, "decisions", BOT_ID)):
        if os.path.islink(sub):
            refuse("a symlink under the decision root is refused")
    for day in days_between(since_ms, as_of_ms):
        p = os.path.join(decision_root, "decisions", BOT_ID, day + ".jsonl")
        if not os.path.lexists(p):
            stats["day_files_missing"] += 1
            continue
        stats["day_files_read"] += 1
        sizes["decision_day_" + day] = os.lstat(p).st_size
        any_ms += any_row_ms(p, stats)
        host += [r for r in iter_rows(p, stats, "host", kind_needles, HOST_KINDS) if is_int(r.get("ms")) and since_ms <= r["ms"] < as_of_ms]
    if stats["day_files_read"] == 0:
        refuse("no decision day file was read")
    for name, p in (("bridge_log", bridge_log), ("lane_ledger", os.path.join(ledger_root, LEDGER_NAME)), ("fills_log", fills_log)):
        if not os.path.lexists(p):
            refuse("a required source is missing: %s" % name)
        sizes[name] = os.lstat(p).st_size
    bridge = [r for r in iter_rows(bridge_log, stats, "bridge", [('"%s"' % k).encode() for k in BRIDGE_KINDS], BRIDGE_KINDS)
              if r.get("mode", "live") == "live" and is_int(r.get("ts_ms")) and since_ms <= r["ts_ms"] < as_of_ms]
    lrows = [r for r in iter_rows(os.path.join(ledger_root, LEDGER_NAME), stats, "ledger", [LANE_BOT.encode()]) if r.get("bot_id") == LANE_BOT]
    backlog_nonempty = 0
    for n in sorted(os.listdir(ledger_root)):
        if n.startswith(LEDGER_NAME + ".backlog."):
            part = [r for r in iter_rows(os.path.join(ledger_root, n), stats, "backlog", [LANE_BOT.encode()]) if r.get("bot_id") == LANE_BOT]
            backlog_nonempty += 1 if part else 0
            lrows += part
    stats["ledger_backlog_nonempty"] = backlog_nonempty
    fills = [r for r in iter_rows(fills_log, stats, "fills", [b'"buy"']) if r.get("kind") == "buy"]
    return build_rows(host, bridge, lrows, fills, since_ms, stats, any_ms), stats, sizes



def build_rows(host, bridge, lrows, fills, since_ms, stats=None, any_ms=None):
    fold = FV.fold_trades(lrows, LANE_BOT)
    groups = {g["order_id"]: g for g in fold["closed"] + fold["open"] if g.get("order_id")}
    entries, exits = {}, {}
    out = []
    for r in host:
        k = r["kind"]
        if k == "entry" and isinstance(r.get("order_id"), str):
            entries.setdefault(r["order_id"], r)
        elif k == "exit" and isinstance(r.get("order_id"), str):
            exits.setdefault(r["order_id"], []).append(r)
        elif k == "entry_refused_gap":
            gap = r.get("gap") if isinstance(r.get("gap"), dict) else {}
            out.append({"kind": "gap_refusal", "t_ms": r["ms"], "pool": r.get("pool"), "trig_slot": pick(r, ("trigger_slot",)), "trig_ms": pick(r, ("trigger_ms",)), "reason": r.get("reason"),
                        "gap_kind": pick(gap, ALIASES["gap_kind"]), "gap_from_ms": gap.get("from_ms"), "gap_to_ms": gap.get("to_ms"), "blocked_until_ms": r.get("blocked_until_ms")})
        elif k == "entry_gap_span_ignored":
            out.append({"kind": "span_ignored", "t_ms": r["ms"], "pool": r.get("pool"), "trig_slot": r.get("trigger_slot"), "trig_ms": r.get("trigger_ms"), "then": r.get("then")})
        elif k in ("lane_restart_shadow", "lane_restart_shadow_failed"):
            out.append({"kind": "restart", "t_ms": r["ms"], "failed": k.endswith("_failed"), "down_from_ms": r.get("down_from_ms"), "down_to_ms": r.get("down_to_ms"),
                        "shadow_complete": r.get("shadow_complete") is True, "fallback": r.get("fallback") if isinstance(r.get("fallback"), (str, type(None))) else None})
        elif k in ("start", "lane_start"):
            # prev_last_ms: the instant of the last host decision-log row of ANY kind before this start (any_ms, every line of the day files read; else the rows read here), None when there is none.
            # A crash leaves no restart row; this is its down-from anchor (the last sign of life).
            prev = [m for m in (any_ms if any_ms is not None else [h["ms"] for h in host]) if m < r["ms"]]
            out.append({"kind": "start", "t_ms": r["ms"], "start_kind": k, "prev_last_ms": max(prev) if prev else None})
    by_order = {}
    for r in bridge:
        k = r["kind"]
        if k == "feed_gap_hold":
            out.append({"kind": "feed_gap_hold", "t_ms": r["ts_ms"], "first_ms": r.get("first_ms"), "newest_ms": r.get("newest_ms"), "hold_until_ms": r.get("hold_until_ms")})
            continue
        oid = r.get("order_id")
        if isinstance(oid, str):
            by_order.setdefault(oid, {}).setdefault(k, []).append(r)
    fill_by_oid = {}
    for oid, g in groups.items():
        t = {"mint": None, "intent_ms": g["intent_ms"], "close_ms": g["close_ms"], "buy_sig": g.get("buy_sig")}
        # find_buy needs the mint and signature: both are used here and dropped
        t["mint"] = g["mint"]
        buy, _ = FV.find_buy(t, fills) if g.get("ok_buy") else (None, None)
        fill_by_oid[oid] = buy
    for oid in sorted(set(entries) | set(by_order) | set(groups)):
        e = entries.get(oid)
        b = by_order.get(oid, {})
        g = groups.get(oid)
        if e is None and g is None:
            if b and stats is not None:
                stats["dropped_bridge_only"] = stats.get("dropped_bridge_only", 0) + 1  # an order of no floor-lane-1 host entry or ledger group (a shared bridge log): not ours
            continue
        meta = e.get("meta") if e and isinstance(e.get("meta"), dict) else {}
        sent = (b.get("buy_sent") or [None])[-1]
        refused = (b.get("entry_refused") or b.get("buy_port_refused") or [None])[-1]
        landed = (b.get("buy_landed") or [None])[-1]
        sell_sent = (b.get("sell_sent") or [None])[-1]
        sell_landed = (b.get("sell_landed") or [None])[-1]
        host_exits = exits.get(oid, [])
        followed = [x for x in host_exits if x.get("reason") in FV.FOLLOWED_EXITS]
        hx = (followed or host_exits or [None])[0]
        fl = fill_by_oid.get(oid) or {}
        row = {"kind": "order", "order_id": oid, "has_host_entry": e is not None,
               "pool": e.get("pool") if e else None, "trig_slot": pick_one(meta, ALIASES["trig_slot"], "trig_slot"), "trig_ms": pick_one(meta, ALIASES["trig_ms"], "trig_ms"), "decision_ms": e.get("ms") if e else None,
               "stake_sol": e.get("stake_sol") if e else None, "mark0": meta.get("mark0"),
               "sent": sent is not None or g is not None, "send_ms": sent.get("send_ms") if sent else None,
               "stake_lamports": (sent or {}).get("stake_lamports") if sent else (g or {}).get("stake_lamports"),
               "refused_why": pick_one(refused, ("why", "label"), "refusal_why") if refused else None, "refused_kind": refused.get("kind") if refused else None,
               "landed_ms": landed.get("ts_ms") if landed else None, "landed_slot": landed.get("landed_slot") if landed else None, "decision_slot": (sent or landed or {}).get("decision_slot") if (sent or landed) else None,
               "buy_failed": bool(b.get("buy_failed")), "sell_failed": bool(b.get("sell_failed")),
               "exit_reason": hx.get("reason") if hx else None, "exit_ms": hx.get("ms") if hx else None, "has_host_exit": hx is not None,
               "sell_send_ms": sell_sent.get("send_ms") if sell_sent else None, "sell_reason_bridge": sell_sent.get("reason") if sell_sent else None,
               "sell_landed_ms": sell_landed.get("ts_ms") if sell_landed else None,
               "led_intent_ms": g["intent_ms"] if g else None, "led_net_lamports": g["net"] if g else None, "led_fees_lamports": g["fees"] if g else None,
               "led_rent_unreclaimed_lamports": g["rent_unreclaimed"] if g else None, "led_close_ev": g["close_ev"] if g else None, "led_close_reason": g["close_reason"] if g else None,
               "led_close_ms": g["close_ms"] if g else None, "led_writeoff": g["close_reason"] if g and g["close_ev"] == "writeoff" else None, "led_ok_buy": bool(g and g["ok_buy"]), "led_estimated_rows": g["estimated_rows"] if g else None,
               "led_stake_lamports": g["stake_lamports"] if g else None,
               "fill_spend_lamports": pick(fl, ALIASES["spend_lamports"]), "fill_tokens_received": pick_one(fl, ALIASES["tokens_received"], "tokens_received"),
               "fill_price_vs_quote_bps": pick(fl, ALIASES["price_vs_quote_bps"]), "fill_tokens_source": pick(fl, ALIASES["tokens_source"]), "fill_estimated": fl.get("estimated") is True}
        if row["decision_ms"] is None and row["led_intent_ms"] is not None and row["led_intent_ms"] < since_ms:
            continue  # an older trade's group that only has ledger rows in this file
        out.append(row)
    out.sort(key=lambda r: (KIND_ORDER.index(r["kind"]), r.get("t_ms") or r.get("decision_ms") or r.get("led_intent_ms") or 0, str(r.get("order_id") or "")))
    return out


def write_atomic(path, data):
    d = os.path.dirname(path)
    tmp = os.path.join(d, ".%s.tmp.%d" % (os.path.basename(path), os.getpid()))
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def chown_to_sudo_user(*paths):
    su = os.environ.get("SUDO_UID")
    if su and os.geteuid() == 0:
        for p in paths:
            try:
                os.chown(p, int(su), int(os.environ.get("SUDO_GID", su)), follow_symlinks=False)
            except OSError:
                pass


def do_extract(a, test_mode):
    since_ms = ms_of_hour(a.since_hour)
    as_of = a.as_of_ms if (test_mode and a.as_of_ms) else int(datetime.now(timezone.utc).timestamp() * 1000)
    rows, stats, sizes = gather(a.decision_root, a.bridge_log, a.ledger_root, a.fills_log, since_ms, as_of)
    counts = {}
    for r in rows:
        counts[r["kind"]] = counts.get(r["kind"], 0) + 1
    header = {"kind": "header", "schema": SCHEMA, "bot_id": LANE_BOT, "arm": BOT_ID, "since_ms": since_ms, "as_of_ms": as_of}
    body = "".join(canon(x) + "\n" for x in [header] + rows).encode()
    sha = hashlib.sha256(body).hexdigest()
    meta = {"schema": SCHEMA, "as_of_ms": as_of, "since_ms": since_ms, "counts_by_kind": dict(sorted(counts.items())), "extract_sha256": sha, "extract_bytes": len(body),
            "source_sizes": dict(sorted(sizes.items())), "stats": stats,
            "n_orders_sent": sum(1 for r in rows if r["kind"] == "order" and r["sent"]), "tool_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    base = plain(a.out_dir or near_dir())  # no symlink anywhere in the path
    if os.path.lexists(base) and (os.path.islink(base) or not os.path.isdir(base)):
        refuse("the near directory is a symlink or not a directory")
    os.makedirs(base, mode=0o700, exist_ok=True)
    os.chmod(base, 0o700, follow_symlinks=False)
    write_atomic(os.path.join(base, EXTRACT_NAME), body)
    write_atomic(os.path.join(base, META_NAME), (json.dumps(meta, sort_keys=True, indent=1) + "\n").encode())
    chown_to_sudo_user(base, os.path.join(base, EXTRACT_NAME), os.path.join(base, META_NAME))
    print("OSB-LIVE-EXTRACT counts=%s orders_sent=%d day_files_read=%d day_files_missing=%d torn_tail=%d bad_lines=%d backlog_nonempty=%d out_bytes=%d" % (
        json.dumps(meta["counts_by_kind"], sort_keys=True), meta["n_orders_sent"], stats["day_files_read"], stats["day_files_missing"], stats["torn_tail"], stats["bad_lines"], stats["ledger_backlog_nonempty"], len(body)))
    print("OUT_SHA256=%s" % sha)
    return 0


# ------------------------------------------------------------------------------------------------------------- structure check
def structure_check(a):
    """Field-name presence counts only: `source kind field present/total`. No value is printed."""
    stats = {"torn_tail": 0, "bad_lines": 0}
    since_ms, as_of = ms_of_hour(a.since_hour), int(datetime.now(timezone.utc).timestamp() * 1000)
    print("osb_live_extract structure check (field-name presence counts only; nothing written)")
    host = []
    for day in days_between(since_ms, as_of):
        host += list(iter_rows(os.path.join(a.decision_root, "decisions", BOT_ID, day + ".jsonl"), stats, "x", None, HOST_KINDS))
    bridge = list(iter_rows(a.bridge_log, stats, "x", None, BRIDGE_KINDS))
    want = {
        ("host", "entry"): ("order_id", "pool", "ms", "stake_sol", "decision_slot", "meta", "meta.signal_slot", "meta.signal_t_recv_ms", "meta.mark0"),
        ("host", "exit"): ("order_id", "ms", "reason"),
        ("host", "entry_refused_gap"): ("ms", "pool", "reason", "trigger_slot", "trigger_ms", "gap", "gap.kind", "gap.from_ms", "gap.to_ms", "blocked_until_ms"),
        ("host", "entry_gap_span_ignored"): ("ms", "pool", "trigger_slot", "trigger_ms", "then"),
        ("host", "lane_restart_shadow"): ("ms", "down_from_ms", "down_to_ms", "shadow_complete", "fallback"),
        ("host", "lane_restart_shadow_failed"): ("ms", "why"),
        ("host", "start"): ("ms",), ("host", "lane_start"): ("ms",),
        ("bridge", "buy_sent"): ("order_id", "ts_ms", "decision_ms", "send_ms", "stake_lamports", "decision_slot", "label", "mode"),
        ("bridge", "buy_landed"): ("order_id", "ts_ms", "landed_slot", "decision_slot", "mode"),
        ("bridge", "buy_failed"): ("order_id", "ts_ms", "label"),
        ("bridge", "sell_sent"): ("order_id", "ts_ms", "send_ms", "reason", "decision_ms"),
        ("bridge", "sell_landed"): ("order_id", "ts_ms", "landed_slot", "pnl_lamports", "reason"),
        ("bridge", "entry_refused"): ("order_id", "ts_ms", "why", "decision_ms"),
        ("bridge", "buy_port_refused"): ("order_id", "ts_ms", "label"),
        ("bridge", "feed_gap_hold"): ("ts_ms", "first_ms", "newest_ms", "hold_until_ms"),
    }

    def has(r, path):
        cur = r
        for p in path.split("."):
            if not isinstance(cur, dict) or cur.get(p) is None:
                return False
            cur = cur[p]
        return True

    for (src, kind), fields in want.items():
        rs = [r for r in (host if src == "host" else bridge) if r.get("kind") == kind]
        print("%s %s rows=%d" % (src, kind, len(rs)))
        for f in fields:
            print("  %s present %d/%d" % (f, sum(1 for r in rs if has(r, f)), len(rs)))
    mine = [r for r in iter_rows(os.path.join(a.ledger_root, LEDGER_NAME), stats, "x") if r.get("bot_id") == LANE_BOT]
    print("ledger floor-lane-1 rows=%d" % len(mine))
    for f in ("ev", "mint", "ts_ms", "signature", "stake_lamports", "sol_in_lamports", "sol_out_lamports", "fee_network_lamports", "fee_priority_lamports", "fee_tip_lamports",
              "rent_unreclaimed_lamports", "reason", "ok", "row_id", "estimated"):
        print("  %s present %d/%d" % (f, sum(1 for r in mine if r.get(f) is not None), len(mine)))
    fills = [r for r in iter_rows(a.fills_log, stats, "x") if r.get("kind") == "buy"]
    print("fills buy rows=%d" % len(fills))
    for alias_name in ("spend_lamports", "tokens_received", "price_vs_quote_bps", "tokens_source"):
        for n in ALIASES[alias_name]:
            print("  alias %s: %s present %d/%d" % (alias_name, n, sum(1 for r in fills if r.get(n) is not None), len(fills)))
    for f in ("landed", "mint", "signature", "ts_ms", "estimated"):
        print("  %s present %d/%d" % (f, sum(1 for r in fills if r.get(f) is not None), len(fills)))
    for alias_name in ("trig_slot", "trig_ms"):
        for n in ALIASES[alias_name]:
            print("  alias %s: %s present in entry meta %d" % (alias_name, n, sum(1 for r in host if r.get("kind") == "entry" and isinstance(r.get("meta"), dict) and r["meta"].get(n) is not None)))
    oids = [r["order_id"] for r in host + bridge if isinstance(r.get("order_id"), str)]
    ok = sum(1 for o in oids if OID_OK.fullmatch(o))
    print("order id shape (recon rule: 16 lowercase hex, or at most 15 of [A-Za-z0-9_-]): matching %d, not matching %d" % (ok, len(oids) - ok))
    print("bad_lines %d torn_tail %d" % (stats["bad_lines"], stats["torn_tail"]))
    return 0


# ------------------------------------------------------------------------------------------------------------- push (non-root)
def remote_script(name, n, sha):
    """The fixed remote command. setfacl -b -k on the dir BEFORE the first file; dir 700; temp file created 0600 before any byte; length and sha256 checked before the rename; file 600 after."""
    d, final = FAR_DIR, FAR_DIR + "/" + name
    t = "%s/.%s.tmp.$$" % (d, name)
    qd, qf = shlex.quote(d), shlex.quote(final)
    qt = shlex.quote("%s/.%s.tmp." % (d, name)) + "$$"
    return ("umask 077; mkdir -p %s && command -v setfacl >/dev/null 2>&1 && command -v getfacl >/dev/null 2>&1 && setfacl -b -k %s && chmod 700 %s && [ -z \"$(getfacl -c --skip-base %s 2>/dev/null)\" ] && ( umask 077; : > %s ) && chmod 600 %s && cat >> %s"
            " && [ \"$(wc -c < %s | tr -d ' ')\" = %d ] && [ \"$(sha256sum < %s | cut -d' ' -f1)\" = %s ] && chmod 600 %s && mv %s %s || { rm -f %s; exit 1; }; "
            "[ \"$(stat -c %%a %s)\" = 600 ] && [ \"$(stat -c %%a %s)\" = 700 ] || exit 3" % (qd, qd, qd, qd, qt, qt, qt, qt, n, qt, sha, qt, qt, qf, qt, qf, qd))


def ssh_send(name, data, sha, run=subprocess.run):
    script = remote_script(name, len(data), sha)
    argv = ["ssh", *SSH_OPTS, SSH_HOST, "sh -c " + shlex.quote(script)]
    try:
        r = run(argv, input=data, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=300)
    except (OSError, subprocess.SubprocessError) as e:
        raise RuntimeError("send: %s" % type(e).__name__)
    if r.returncode != 0:
        raise RuntimeError("send rc=%s" % r.returncode)


def do_push(a, send_fn=ssh_send):
    base = a.out_dir or near_dir()
    ex, mt = os.path.join(base, EXTRACT_NAME), os.path.join(base, META_NAME)
    for p in (ex, mt):
        st = os.lstat(p) if os.path.lexists(p) else None
        if st is None or (st.st_mode & 0o077):
            refuse("near file missing or not 0600: %s" % os.path.basename(p))
    data, mdata = Path(ex).read_bytes(), Path(mt).read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    try:
        if json.loads(mdata).get("extract_sha256") != sha:
            refuse("the meta sha256 does not match the extract")
    except ValueError:
        refuse("the meta file does not parse")
    for name, blob in ((EXTRACT_NAME, data), (META_NAME, mdata)):  # the extract first; the meta last, so a meta on research-0 always has its extract
        try:
            send_fn(name, blob, hashlib.sha256(blob).hexdigest())
        except RuntimeError as e:
            print("PUSH FAILED %s (%s)" % (name, e))
            return 1
    print("EXTRACT_PUSHED %s %s OUT_SHA256=%s" % (EXTRACT_NAME, META_NAME, sha))
    return 0


def refuse_real_under_pytest(paths):
    if os.environ.get("PYTEST_CURRENT_TEST"):
        for p in paths:
            if p and any(os.path.abspath(p) == r or os.path.abspath(p).startswith(r + "/") for r in REAL_PREFIXES):
                refuse("a real root is refused under pytest")


def main(argv=None, send_fn=ssh_send):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("mode", choices=("extract", "push", "structure-check"))
    ap.add_argument("--since-hour", default=DEFAULT_SINCE_HOUR)
    ap.add_argument("--test-mode", action="store_true")
    ap.add_argument("--decision-root", default=LIVE_ROOT)
    ap.add_argument("--bridge-log", default=BRIDGE_LOG)
    ap.add_argument("--ledger-root", default=LEDGER_ROOT)
    ap.add_argument("--fills-log", default=FILLS_LOG)
    ap.add_argument("--out-dir", default=None)
    ap.add_argument("--as-of-ms", type=int, default=None)
    a = ap.parse_args(argv)
    test_mode = bool(a.test_mode)
    if test_mode and os.environ.get(TEST_ENV) != "1":
        refuse("--test-mode needs %s=1" % TEST_ENV)
    if not test_mode:
        if (a.decision_root, a.bridge_log, a.ledger_root, a.fills_log) != (LIVE_ROOT, BRIDGE_LOG, LEDGER_ROOT, FILLS_LOG) or a.out_dir or a.as_of_ms:
            refuse("paths, --out-dir and --as-of-ms are test-mode options")
    else:
        refuse_real_under_pytest([a.out_dir] if a.mode == "push" else [a.decision_root, a.bridge_log, a.ledger_root, a.fills_log, a.out_dir])
    if a.mode == "extract":
        if not test_mode:
            try:
                cfg_root = json.loads(Path(CONFIG).read_bytes()).get("root")
            except (OSError, ValueError):
                refuse("cannot read the lane host config")
            if not isinstance(cfg_root, str) or os.path.abspath(cfg_root) != LIVE_ROOT:
                refuse("the decision root is not the `root` of the lane host config")
        for p in (a.decision_root, a.ledger_root):
            plain(p)
        return do_extract(a, test_mode)
    if a.mode == "structure-check":
        return structure_check(a)
    return do_push(a, send_fn)


if __name__ == "__main__":
    sys.exit(main())
