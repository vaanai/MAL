#!/usr/bin/env python3
"""r4b_price_keeper: hard-link keeper for the r4-b price path (DEC-031 Am.31 preview and r4bt-a2's registered pricer). PAPER ONLY. Runs on mal-research-0.

QP-RULING-1113-open-sims Addendum 1, B5 rule 3. Same behaviour and identity checks as scripts/ops/osb_keeper.py (whose helpers are imported, unedited), with other constants:
  src  /data2/mal/arena-tip-feed/hv7     log <src>/.mirror/events.jsonl     keep  /data2/mal/scratch/r4b-price-keep
  floor 2026-11-15T22: no hour before it is ever linked (--from-hour earlier: refused, exit 2). There is no --backfill-src.
A tape file is never opened, parsed, copied, edited or deleted: os.lstat and os.link(follow_symlinks=False) only, regular files only. A keep entry whose (dev, inode) differs from the src file
of the same name is refused ("hv7 file replaced after verification: stop and ask QP"). Nothing is ever pruned.

Closed hours (QP on PR #1367, B2): an hour with a `closed` row is linked ONLY by its `tape-<h>.jsonl.zst` name; its lstat size must equal the row's zst_bytes (else that hour is refused loudly,
listed in the status as size_mismatch_hours and counted missing); a `.jsonl` beside a closed hour is never linked (live_jsonl_beside_closed). Every kept hour gets one row in the append-only
keep/.keeper-hours.jsonl (0600): h, name, zst_bytes, zst_sha (copied from the receiver's event), dev, inode, linked_utc; the pricers verify sha256(file) == zst_sha before pricing. No tape byte is read.
Every pass also re-checks each keep entry: size == zst_bytes and the (dev, inode) of the src file of the same name (else refused, exit 2).

Alerts (QP B1): the missing span runs from max(floor, --from-hour) (never from the earliest kept hour) to the newest verified closed hour >= floor, and counts every hour not in keep.
waiting_for_floor is true only while the log lists no closed hour >= floor. The status carries newest_verified_hour and lag_h (now minus the end of the newest closed hour); lag_h > 6 prints
`R4B-KEEPER STALE lag_h=<n>`; lag_h > 72 or any missing hour sets `alert`. One loud line `R4B-KEEPER MISSING <n> hour(s): <names>` per pass. A failed link or an out-of-order hour never stops
later hours from being linked in the same pass; with --fail-on-missing the loop ends with exit 5 only AFTER the whole pass (QP's rule; the reviewer's softer 2-pass rule is not used). The manager restarts the
job after fixing the cause (or, for a permanent gap, QP decides). Status: keep/.keeper-status.json (atomic). Keep dir is created 0700.

Exit codes: 0 pass done; 1 an hour failed to link (retried next pass); 2 refusal or bad arguments; 3 verification log unreadable (5 consecutive in loop mode, at once with --loop 0);
4 five consecutive passes ended in an OSError; 5 alert with --fail-on-missing (never while only waiting_for_floor).
--loop SECONDS repeats passes (default 300; 0 = one pass).
"""
import argparse
import json
import os
import re
import stat
import sys
import time
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import osb_keeper as ok  # noqa: E402  (helpers only: read_verified, tape_names, _under, _ident, write_status, parse_hour, fmt_hour, now_utc, regexes)

DEFAULT_SRC = "/data2/mal/arena-tip-feed/hv7"
DEFAULT_KEEP = "/data2/mal/scratch/r4b-price-keep"
FLOOR_HOUR = "2026-11-15T22"
MAX_CONSEC = 5
STALE_WARN_H = 6
STALE_FAIL_H = 72
HOURS_NAME = ".keeper-hours.jsonl"
SHA_RE = re.compile(r"^[0-9a-f]{64}\Z")
Refuse = ok.Refuse
TAPE_RE, HOUR_RE = ok.TAPE_RE, ok.HOUR_RE


def check_roots(src, keep, from_hour, src_allowed, keep_allowed):
    # copied from osb_keeper.check_roots: that function hard-codes its floor constant and the backfill dir
    rsrc, rkeep = os.path.realpath(src), os.path.realpath(keep)
    if rkeep != os.path.realpath(keep_allowed):
        raise Refuse("keep is not the allow-listed keep dir")
    if rsrc != os.path.realpath(src_allowed):
        raise Refuse("src is not the allow-listed hv7 dir")
    if ok._under(rsrc, rkeep) or ok._under(rkeep, rsrc):
        raise Refuse("src and keep overlap")
    if not HOUR_RE.match(from_hour) or from_hour < FLOOR_HOUR:
        raise Refuse("from-hour before %s" % FLOOR_HOUR)
    return rsrc, rkeep


def read_closed(events_path):
    """hour -> {"ms", "zst_bytes", "zst_sha"} from the FIRST `closed` row per hour (the row tip_hour_health.load_verified reads: smallest ms). Reads the verification log only.
    zst_bytes / zst_sha are None when the row lacks a valid int / 64-hex (such an hour is never linked)."""
    out = {}
    with open(events_path, "rb") as f:
        for line in f:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if not (isinstance(r, dict) and r.get("kind") == "closed" and isinstance(r.get("h"), str) and HOUR_RE.match(r["h"])
                    and isinstance(r.get("ms"), int) and not isinstance(r.get("ms"), bool)):
                continue
            zb, zs = r.get("zst_bytes"), r.get("zst_sha")
            row = {"ms": r["ms"], "zst_bytes": zb if isinstance(zb, int) and not isinstance(zb, bool) and zb >= 0 else None,
                   "zst_sha": zs if isinstance(zs, str) and SHA_RE.match(zs) else None}
            if r["h"] not in out or row["ms"] < out[r["h"]]["ms"]:
                out[r["h"]] = row
    return out


def read_recorded(path):
    """Hours already in the keeper's own hours list (a keeper-written file, not a tape)."""
    got = set()
    if os.path.lexists(path):
        with open(path, "rb") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if isinstance(r, dict) and isinstance(r.get("h"), str):
                    got.add(r["h"])
    return got


def append_row(path, row):
    """One O_APPEND write per line, fsynced; the file is created 0600; rows are never rewritten."""
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.write(fd, (json.dumps(row, sort_keys=True) + "\n").encode())
        os.fsync(fd)
    finally:
        os.close(fd)


def zst_name(h):
    return "tape-%s.jsonl.zst" % h


def one_pass(src, keep, from_hour, src_allowed=DEFAULT_SRC, keep_allowed=DEFAULT_KEEP, out=print, now_fn=time.time):
    rsrc, rkeep = check_roots(src, keep, from_hour, src_allowed, keep_allowed)
    events_path = os.path.join(src, ".mirror", "events.jsonl")
    if os.path.realpath(events_path) != os.path.join(rsrc, ".mirror", "events.jsonl"):
        raise Refuse("events must be <src>/.mirror/events.jsonl")
    try:
        closed = read_closed(events_path)
    except OSError:
        return 3, None
    os.makedirs(rkeep, mode=0o700, exist_ok=True)
    keep_names = {}
    for n in sorted(os.listdir(rkeep)):
        m = TAPE_RE.match(n)
        if not m:
            continue
        kp = os.path.join(rkeep, n)
        if os.path.islink(kp):
            raise Refuse("symlink in keep: %s" % n)
        if m.group(2) != ".zst":
            raise Refuse("non-.zst tape entry in keep: %s" % n)
        h = m.group(1)
        ev = closed.get(h)
        if ev is not None and ev["zst_bytes"] is not None and os.lstat(kp).st_size != ev["zst_bytes"]:
            raise Refuse("keep entry size differs from the receiver's zst_bytes: stop and ask QP (%s)" % n)
        sp = os.path.join(rsrc, n)
        if os.path.lexists(sp) and stat.S_ISREG(os.lstat(sp).st_mode) and ok._ident(sp) != ok._ident(kp):
            raise Refuse("hv7 file replaced after verification: stop and ask QP (%s)" % n)
        keep_names[h] = n
    src_all = sorted(os.listdir(rsrc))
    ver_ok = sorted(h for h in closed if h >= from_hour)
    skipped, bad_names, size_bad, live_beside, incomplete = {}, [], [], [], []

    def skip(reason):
        skipped[reason] = skipped.get(reason, 0) + 1

    linked, failed, already = [], [], 0
    for h in ver_ok:
        if h in keep_names:
            already += 1
            continue
        ev = closed[h]
        if ev["zst_bytes"] is None or ev["zst_sha"] is None:
            skip("closed_event_incomplete")
            incomplete.append(h)
            continue
        n = zst_name(h)
        if n not in src_all:
            if "tape-%s.jsonl" % h in src_all:
                skip("live_jsonl_beside_closed")
                live_beside.append(h)
            else:
                skip("not_in_src")
            continue
        p = os.path.join(rsrc, n)
        try:
            s = os.lstat(p)
            if not stat.S_ISREG(s.st_mode):
                skip("not_regular_file")
                bad_names.append(n)
                continue
            if s.st_size != ev["zst_bytes"]:
                out("KEEPER-REFUSE-HOUR %s: size differs from the receiver's zst_bytes (not linked)" % h)
                skip("size_mismatch")
                size_bad.append(h)
                continue
            os.link(p, os.path.join(rkeep, n), follow_symlinks=False)
            keep_names[h] = n
            linked.append(h)
        except OSError as e:
            out("KEEPER-FAIL link %s: %s (never copied)" % (n, e.strerror or type(e).__name__))
            failed.append(h)
    # the hours list: every kept hour gets one row (hash and size copied from the receiver's event; no tape byte is read)
    hours_path = os.path.join(rkeep, HOURS_NAME)
    recorded = read_recorded(hours_path)
    for h in sorted(keep_names):
        ev = closed.get(h)
        if h in recorded or ev is None or ev["zst_bytes"] is None or ev["zst_sha"] is None:
            continue
        i = os.lstat(os.path.join(rkeep, keep_names[h]))
        append_row(hours_path, {"h": h, "name": keep_names[h], "zst_bytes": ev["zst_bytes"], "zst_sha": ev["zst_sha"], "dev": i.st_dev, "inode": i.st_ino, "linked_utc": ok.now_utc()})
    for n in src_all:
        m = TAPE_RE.match(n)
        if m and m.group(1) >= from_hour and m.group(1) not in closed:
            skip("src_hour_not_verified")
    held = sorted(keep_names)
    at_floor = sorted(h for h in closed if h >= FLOOR_HOUR)
    waiting = not at_floor
    missing = []
    if not waiting:
        d, last = ok.parse_hour(max(FLOOR_HOUR, from_hour)), ok.parse_hour(at_floor[-1])
        while d <= last:
            if ok.fmt_hour(d) not in keep_names:
                missing.append(ok.fmt_hour(d))
            d += timedelta(hours=1)
    newest = max(closed) if closed else None
    lag_h = None
    if newest is not None:
        lag_h = round(max(0.0, (now_fn() - (ok.parse_hour(newest) + timedelta(hours=1)).timestamp()) / 3600.0), 1)
    stale, dead = lag_h is not None and lag_h > STALE_WARN_H, lag_h is not None and lag_h > STALE_FAIL_H
    linked.sort()
    st = {"pass_utc": ok.now_utc(), "from_hour": from_hour, "floor_hour": FLOOR_HOUR, "waiting_for_floor": waiting, "verified_hours_seen": len(ver_ok), "linked_now": len(linked),
          "linked_first": linked[0] if linked else None, "linked_last": linked[-1] if linked else None, "already_present": already, "not_regular_files": sorted(bad_names),
          "skipped": skipped, "failed_hours": sorted(failed), "size_mismatch_hours": size_bad, "live_jsonl_beside_closed_hours": live_beside, "incomplete_event_hours": incomplete,
          "keep_earliest": held[0] if held else None, "keep_latest": held[-1] if held else None, "keep_hours": len(held), "contiguous": not missing, "missing_hours": missing,
          "newest_verified_hour": newest, "lag_h": lag_h, "stale": stale, "alert": bool(missing) or dead}
    ok.write_status(rkeep, st)
    out("KEEPER verified_seen=%d linked_now=%d first=%s last=%s already=%d skipped=%s failed=%d keep=%s..%s hours=%d contiguous=%s waiting_for_floor=%s lag_h=%s"
        % (st["verified_hours_seen"], st["linked_now"], st["linked_first"], st["linked_last"], already, json.dumps(skipped, sort_keys=True), len(failed),
           st["keep_earliest"], st["keep_latest"], len(held), st["contiguous"], waiting, lag_h))
    if missing:
        out("R4B-KEEPER MISSING %d hour(s): %s" % (len(missing), ",".join(missing)))
    if stale:
        out("R4B-KEEPER STALE lag_h=%s" % lag_h)
    if bad_names:
        out("KEEPER not_regular_files=%s" % ",".join(sorted(bad_names)))
    return (1 if failed else 0), st


def main(argv=None, src=DEFAULT_SRC, keep=DEFAULT_KEEP, sleep=time.sleep, max_passes=None, out=print, now_fn=time.time):
    """src, keep, sleep and max_passes are test-only overrides (src/keep also become the allow-listed values); the CLI does not expose them."""
    ap = argparse.ArgumentParser(description="hard-link keeper for the r4-b price path (verified closed hv7 hours from %s only)" % FLOOR_HOUR)
    ap.add_argument("--from-hour", default=FLOOR_HOUR)
    ap.add_argument("--loop", type=int, default=300, help="seconds between passes; 0 = one pass")
    ap.add_argument("--fail-on-missing", action="store_true", help="exit 5 when any hour between the earliest kept and the newest verified hour is missing")
    a = ap.parse_args(argv)
    n = consec_err = consec_log = 0
    while True:
        n += 1
        code, st = 0, None
        try:
            code, st = one_pass(src, keep, a.from_hour, src, keep, out, now_fn)
            consec_err = 0
        except Refuse as e:
            out("KEEPER-REFUSE %s" % e)
            try:
                if os.path.isdir(keep) and not os.path.islink(keep):
                    ok.write_status(os.path.realpath(keep), {"refused": str(e), "pass_utc": ok.now_utc()})
            except OSError:
                pass
            return 2
        except OSError as e:
            consec_err += 1
            out("KEEPER-FAIL pass %d: %s (%d consecutive)" % (n, e.strerror or type(e).__name__, consec_err))
            code = 1
            if a.loop <= 0:
                return 1
            if consec_err >= MAX_CONSEC:
                return 4
        if code == 3:
            consec_log += 1
            out("KEEPER-FAIL verification log unreadable (%d consecutive)" % consec_log)
            if a.loop <= 0 or consec_log >= MAX_CONSEC:
                return 3
        else:
            consec_log = 0
        if a.fail_on_missing and st and st["alert"]:  # only after the whole pass: everything linkable was linked first
            return 5
        if a.loop <= 0 or (max_passes is not None and n >= max_passes):
            return code
        sleep(a.loop)


if __name__ == "__main__":
    sys.exit(main())
