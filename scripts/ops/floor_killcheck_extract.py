"""floor_killcheck_extract: step A of the FLOOR kill-line check (DEC-035 Am.7 Post-install check, Erratum 1). Runs on mal-fast-0 as a manager job WITH SUDO (the live lane root is root-only).

It reads ONLY the day files of the lane host's decision log inside the declared window and writes a small JSONL for step B (scripts/ops/floor_killcheck.py on mal-research-0).
  * Layout (tools/floor_lane_host.py:5, tools/arena_declog.py:41-60): `<root>/decisions/l1-floor-v2/<UTC day>.jsonl`, one canonical-JSON row per line (sorted keys, compact separators;
    tools/arena_paper.py:147). The day of a row is the day of its `ms`. File names are BUILT from the window, never listed. The checkpoint, the pool table, the ledger and every other
    path under the root are never opened.
  * Entry row = the lane's Buy decision: `kind == "entry"` with `order_id`, `pool`, `ms`, `meta.signal_t_recv_ms`, `seq` (tools/arena_rules.py:632, the row the executor bridge reads:
    tools/floor_arena_executor.py:312). Restart evidence row = `kind == "lane_restart_shadow"` (tools/floor_lane_host.py:86, written by _shadow_report at :519-545; the `lane_start` line
    of the host is stdout only and is not a log row). Its `ms` is the restart instant: the host takes no decision before it (the warm-up runs with the rules off).
  * FILTER AT READ (reviewer on #1337 at 496d6e5a): a line is parsed only if it contains the byte string `"kind":"entry"` or `"kind":"lane_restart_shadow"` (the closing quote keeps
    entry_refused_gap, lane_restart_shadow_failed and every other kind out); every other row is skipped as bytes, never parsed. A matched line must then parse and have exactly that kind.
  * The output keeps ONLY: per entry `t_ms`, `pool`, `order_id` (else `seq`), `trig_ms` (meta.signal_t_recv_ms, the trigger instant QP asked for in its note 1; used by step B to tell a
    reference entry from a different one); a header with the window, the count, the `lane_restart_shadow` rows (`t_ms`, `prev_end`, `fallback`, `shadow_complete`, `down_from_ms`,
    `down_to_ms`: which restarts were cheap) and the sha256 of each day file read (bytes as read). No price, stake, P&L, exit, fill or refusal row leaves.
  * `lane_restart_shadow_failed` rows in the window are only counted (header `n_restart_shadow_failed`; Am.7: no further host restart until QP has read such a row); nothing of them is kept.
  * The output file is written 0600 (chowned to the invoking user): it holds live pool ids. Delete it once the result is recorded (runbook).
  * Stdout: counts and the output's sha256. No pool id, no order id.
Guards: `--decision-root` must equal the `root` key of /etc/mal-floor-lane/host.json AND the live root (abspath == realpath, no `..`); `--out` must be a NEW file under
<invoking user's home>/mal-data/ops/killcheck/ (no `..`, no symlink in the path, O_EXCL). Day files are opened O_NOFOLLOW. Test mode (`--test-mode` + MAL_KILLCHECK_TEST=1) only swaps
the config path and the out base. Paper only: no key, no network. Nothing here says the lane makes money."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timedelta, timezone

CONFIG = "/etc/mal-floor-lane/host.json"
LIVE_ROOT = "/var/lib/mal-live/floor-lane/decisions-root"
BOT_ID = "l1-floor-v2"  # tools/floor_lane_host.py BOT_ID (a test pins it)
SCHEMA = "floor-killcheck-extract-v1"
MAX_WINDOW_MS = 7 * 86_400_000
K_ENTRY, K_RESTART, K_FAILED = b'"kind":"entry"', b'"kind":"lane_restart_shadow"', b'"kind":"lane_restart_shadow_failed"'
ENTRY_KEYS = ("t_ms", "pool", "order_id", "seq", "trig_ms")
RESTART_KEYS = ("t_ms", "prev_end", "fallback", "shadow_complete", "down_from_ms", "down_to_ms")


class Refuse(SystemExit):
    pass


def refuse(msg: str, code: int = 2):
    print("REFUSED: " + msg, file=sys.stderr)
    raise Refuse(code)


def canon(o) -> str:
    return json.dumps(o, sort_keys=True, separators=(",", ":"), allow_nan=False)


def plain(path: str) -> str:
    """abspath with no `..` component in the argument and no symlink anywhere in it."""
    if not isinstance(path, str) or not path.startswith("/") or ".." in path.split("/"):
        refuse("an absolute path without `..` is required (got %r)" % (path,))
    ap = os.path.abspath(path)
    if os.path.realpath(ap) != ap:
        refuse("a symlink in the path is refused: %s" % ap)
    return ap


def invoking_home() -> str:
    import pwd
    u = os.environ.get("SUDO_USER")
    return pwd.getpwnam(u).pw_dir if u else os.path.expanduser("~")


def out_base() -> str:
    return os.path.join(invoking_home(), "mal-data", "ops", "killcheck")


def check_root(root: str, config: str, test_mode: bool) -> str:
    ap = plain(root)
    try:
        with open(config, "rb") as f:
            cfg_root = json.loads(f.read()).get("root")
    except (OSError, ValueError):
        refuse("cannot read the lane host config %s" % config)
    if not isinstance(cfg_root, str) or os.path.abspath(cfg_root) != ap:
        refuse("--decision-root is not the `root` of %s" % config)
    if not test_mode and ap != LIVE_ROOT:
        refuse("--decision-root must be %s" % LIVE_ROOT)
    return ap


def check_out(out: str | None, base: str, default_name: str) -> str:
    ap = plain(out) if out else os.path.join(base, default_name)
    if os.path.dirname(ap) != base.rstrip("/"):
        refuse("--out must be a file directly in %s" % base)
    if not os.path.basename(ap).endswith(".jsonl") or not all(c.isalnum() or c in "._-" for c in os.path.basename(ap)):
        refuse("--out must be NAME.jsonl with [A-Za-z0-9._-]")
    if os.path.lexists(ap):
        refuse("--out exists; never overwrite: %s" % ap)
    if os.path.lexists(base):
        if os.path.islink(base) or os.path.realpath(base) != os.path.abspath(base):
            refuse("the out directory is or contains a symlink: %s" % base)
    return ap


def days(from_ms: int, to_ms: int) -> list:
    d0 = datetime.fromtimestamp(from_ms / 1000, tz=timezone.utc).date()
    d1 = datetime.fromtimestamp((to_ms - 1) / 1000, tz=timezone.utc).date()
    out = []
    while d0 <= d1:
        out.append(d0.strftime("%Y-%m-%d"))
        d0 += timedelta(days=1)
    return out


def read_day(path: str):
    """-> bytes or None (missing). O_NOFOLLOW: a symlinked day file is refused."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return None
    except OSError:
        refuse("cannot open a day file (symlink?): %s" % os.path.basename(path))
    with os.fdopen(fd, "rb") as f:
        return f.read()


def scan(data: bytes, from_ms: int, to_ms: int):
    """-> (entries, restarts, torn_tail, n_failed). Only lines that contain a wanted kind are parsed; a lane_restart_shadow_failed row is only COUNTED (in the window), nothing of it is kept."""
    entries, restarts, torn, n_failed = [], [], 0, 0
    lines = data.split(b"\n")
    for i, line in enumerate(lines):
        want = K_ENTRY if K_ENTRY in line else (K_RESTART if K_RESTART in line else (K_FAILED if K_FAILED in line else None))
        if want is None:
            continue
        try:
            r = json.loads(line)
        except ValueError:
            if i == len(lines) - 1:  # an unterminated last line: the live host was mid-append
                torn += 1
                continue
            refuse("a matched row does not parse")
        kind = r.get("kind") if isinstance(r, dict) else None
        if kind != {id(K_ENTRY): "entry", id(K_RESTART): "lane_restart_shadow", id(K_FAILED): "lane_restart_shadow_failed"}[id(want)]:
            continue  # the bytes matched inside another row; it is not kept
        ms = r.get("ms")
        if not isinstance(ms, int) or isinstance(ms, bool):
            refuse("a matched row has no integer ms")
        if not (from_ms <= ms < to_ms):
            continue
        if want is K_FAILED:
            n_failed += 1
        elif want is K_ENTRY:
            pool, seq = r.get("pool"), r.get("seq")
            if not isinstance(pool, str) or not pool or not isinstance(seq, int):
                refuse("an entry row lacks pool or seq")
            oid = r.get("order_id")
            trig = (r.get("meta") or {}).get("signal_t_recv_ms") if isinstance(r.get("meta"), dict) else None
            e = {"t_ms": ms, "pool": pool}
            if isinstance(oid, str) and oid:
                e["order_id"] = oid
            else:
                e["seq"] = seq
            if isinstance(trig, int) and not isinstance(trig, bool):
                e["trig_ms"] = trig
            e["_seq"] = seq
            entries.append(e)
        else:
            def g(k):
                v = r.get(k)
                return v if isinstance(v, (int, str, bool)) or v is None else None
            restarts.append({"t_ms": ms, "prev_end": g("prev_end"), "fallback": g("fallback"), "shadow_complete": r.get("shadow_complete") is True,
                             "down_from_ms": g("down_from_ms"), "down_to_ms": g("down_to_ms")})
    return entries, restarts, torn, n_failed


def extract(root: str, from_ms: int, to_ms: int) -> tuple:
    entries, restarts, files, torn, failed = [], [], {}, 0, 0
    for day in days(from_ms, to_ms):
        for sub in (os.path.join(root, "decisions"), os.path.join(root, "decisions", BOT_ID)):
            if os.path.islink(sub):
                refuse("a symlink under the decision root is refused")
        p = os.path.join(root, "decisions", BOT_ID, day + ".jsonl")
        data = read_day(p)
        if data is None:
            files[day + ".jsonl"] = "missing"
            continue
        files[day + ".jsonl"] = hashlib.sha256(data).hexdigest()
        e, r, t, nf = scan(data, from_ms, to_ms)
        failed += nf
        entries += e
        restarts += r
        torn += t
    entries.sort(key=lambda e: (e["t_ms"], e["_seq"]))
    restarts.sort(key=lambda r: r["t_ms"])
    for e in entries:
        del e["_seq"]
    header = {"kind": "header", "schema": SCHEMA, "bot_id": BOT_ID, "from_ms": from_ms, "to_ms": to_ms, "n_entries": len(entries), "lane_starts": restarts, "day_files_sha256": files,
              "torn_tail_lines_dropped": torn, "n_restart_shadow_failed": failed, "extractor_sha256": hashlib.sha256(open(os.path.abspath(__file__), "rb").read()).hexdigest()}
    body = "".join(canon(x) + "\n" for x in [header] + entries).encode()
    return body, header


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--decision-root", required=True)
    ap.add_argument("--from-ms", type=int, required=True)
    ap.add_argument("--to-ms", type=int, required=True)
    ap.add_argument("--out", default=None, help="a NEW NAME.jsonl directly in <home>/mal-data/ops/killcheck/")
    ap.add_argument("--test-mode", action="store_true")
    ap.add_argument("--config", default=CONFIG, help="test mode only")
    ap.add_argument("--out-base", default=None, help="test mode only")
    a = ap.parse_args(argv)
    test_mode = bool(a.test_mode)
    if test_mode and os.environ.get("MAL_KILLCHECK_TEST") != "1":
        refuse("--test-mode needs MAL_KILLCHECK_TEST=1")
    if not test_mode and (a.config != CONFIG or a.out_base):
        refuse("--config and --out-base are test-mode options")
    if a.to_ms <= a.from_ms or a.to_ms - a.from_ms > MAX_WINDOW_MS:
        refuse("the window must be non-empty and at most 7 days")
    root = check_root(a.decision_root, a.config, test_mode)
    base = plain(a.out_base) if a.out_base else out_base()
    out = check_out(a.out, base, "extract-%d-%d.jsonl" % (a.from_ms, a.to_ms))
    body, h = extract(root, a.from_ms, a.to_ms)
    os.makedirs(base, mode=0o700, exist_ok=True)
    fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(body)
        f.flush()
        os.fsync(f.fileno())
    su = os.environ.get("SUDO_UID")
    if su and os.geteuid() == 0:
        try:
            os.chown(out, int(su), int(os.environ.get("SUDO_GID", su)))
            os.chown(base, int(su), int(os.environ.get("SUDO_GID", su)))
        except OSError:
            pass
    sha = hashlib.sha256(body).hexdigest()
    n_missing = sum(1 for v in h["day_files_sha256"].values() if v == "missing")
    print("KILLCHECK-EXTRACT n_entries=%d n_lane_restart_rows=%d n_cheap_restart_rows=%d n_restart_shadow_failed=%d day_files_read=%d day_files_missing=%d torn_tail_lines_dropped=%d out_bytes=%d" % (
        h["n_entries"], len(h["lane_starts"]), sum(1 for r in h["lane_starts"] if r["shadow_complete"]), h["n_restart_shadow_failed"], len(h["day_files_sha256"]) - n_missing, n_missing, h["torn_tail_lines_dropped"], len(body)))
    print("OUT_SHA256=%s" % sha)
    print("OUT_NAME=%s" % os.path.basename(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
