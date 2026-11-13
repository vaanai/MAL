#!/usr/bin/env python3
"""osb_keeper: hard-link keeper for the open-simulation follow driver (scripts/research/osb_follow.py). PAPER ONLY.

Rule it enforces (QP, SEALS-BRIEF line (k)): the keeper may only hard-link `tape-<hour>.jsonl` / `tape-<hour>.jsonl.zst` files from the arena tip receiver's hv7 dir whose hour the receiver's
verification log lists as verified and closed, for hours at or after 2026-11-02T01Z, into /data2/mal/scratch/floor-open-sim/keep/; it never copies, edits, parses or opens them, and never links
any other file.

What one pass does
  - Reads ONLY the verification log (<src>/.mirror/events.jsonl; rows {"h": "YYYY-MM-DDTHH", "kind": "closed", "ms": int}; the rows tools/tip_hour_health.load_verified reads) and directory
    listings. A tape file is never opened or read; the only stat on one is os.lstat (type and (dev, inode) identity). Nothing is ever deleted from keep (no pruning in this tool).
  - For every hour the log lists `closed`, at or after --from-hour, whose tape-<hour>.jsonl[.zst] exists in src and in no form in keep: os.link(src, keep, follow_symlinks=False). If os.link
    fails the hour FAILS loudly (exit 1); there is no copy fallback.
  - Only regular files are linked (os.lstat + S_ISREG). A symlink or other entry in src or the roll is skipped, counted and listed by name. Names that are not
    tape-YYYY-MM-DDTHH.jsonl[.zst] are never linked.
  - The source, the log and the destination are constants, not CLI arguments: src /data2/mal/arena-tip-feed/hv7, log <src>/.mirror/events.jsonl, keep /data2/mal/scratch/floor-open-sim/keep.
    Refuses (exit 2) unless realpath(src) and realpath(keep) equal those exactly and the log path is <src>/.mirror/events.jsonl; src/keep overlap; --from-hour before 2026-11-02T01; a keep
    symlink; a keep tape entry whose (dev, inode) differs from the src file of the same name ("hv7 file replaced after verification: stop and ask QP"). Names only are reported.

--backfill-src DIR  (QP ruling on PR #1363, option (a))
  hv7 keeps only about 240 hours; older hours survive as hard links in the manager's keeper dir /data2/mal/scratch/floor-stage1-proof/tapecopy-roll (job #2566; the same inodes). DIR must be
  exactly that path. An hour is linked from it only when hv7 no longer has the hour, the verification log still lists it `closed`, the hour is >= --from-hour (>= 2026-11-02T01) and the roll
  entry is a regular file. A roll hour the log does not list is never linked (no flag can make it); it is listed in the status as backfill_not_in_log_hours.

Output (stdout, names and counts only) and keep/.keeper-status.json (atomic): pass_utc, hours seen verified, linked now (count, first, last), already present, backfill_linked, skips by reason,
earliest and latest hour in keep, contiguous yes/no, missing hours between them by name. After a refusal the status is {"refused": reason, "pass_utc": ...}. A book starts only when
keep_earliest <= its seed hour and missing_hours has nothing inside [seed, start).

Exit codes: 0 pass done; 1 at least one hour failed to link (logged, retried next pass); 2 refusal or bad arguments (ends the loop); 3 verification log unreadable (5 consecutive passes in loop
mode, at once with --loop 0); 4 five consecutive passes ended in an OSError (a single transient OSError is logged and the loop continues).
--loop SECONDS repeats passes (default 300; 0 = one pass).
"""
import argparse
import json
import os
import re
import stat
import sys
import time
from datetime import datetime, timedelta, timezone

DEFAULT_SRC = "/data2/mal/arena-tip-feed/hv7"
DEFAULT_KEEP = "/data2/mal/scratch/floor-open-sim/keep"
BACKFILL_ALLOWED = "/data2/mal/scratch/floor-stage1-proof/tapecopy-roll"
MIN_FROM_HOUR = "2026-11-02T01"
MAX_CONSEC = 5
HOUR_RE = re.compile(r"^\d{4}-\d\d-\d\dT\d\d$")
TAPE_RE = re.compile(r"^tape-(\d{4}-\d\d-\d\dT\d\d)\.jsonl(\.zst)?$")
STATUS_NAME = ".keeper-status.json"


class Refuse(Exception):
    pass


def parse_hour(k):
    return datetime.strptime(k, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc)


def fmt_hour(d):
    return d.strftime("%Y-%m-%dT%H")


def now_utc():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_verified(events_path):
    """Hours with a `closed` row (integer ms, as tip_hour_health.load_verified requires) in the receiver's verification log. The log is not a tape file."""
    out = set()
    with open(events_path, "rb") as f:
        for line in f:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if (isinstance(r, dict) and r.get("kind") == "closed" and isinstance(r.get("h"), str) and HOUR_RE.match(r["h"])
                    and isinstance(r.get("ms"), int) and not isinstance(r.get("ms"), bool)):
                out.add(r["h"])
    return out


def tape_names(d):
    """hour -> name, from a directory listing only; .jsonl preferred over .jsonl.zst; other names ignored."""
    out = {}
    for n in sorted(os.listdir(d)):
        m = TAPE_RE.match(n)
        if m and (m.group(1) not in out or not m.group(2)):
            out[m.group(1)] = n
    return out


def _under(p, root):
    return p == root or p.startswith(root.rstrip("/") + "/")


def _ident(p):
    s = os.lstat(p)
    return s.st_dev, s.st_ino


def write_status(rkeep, st):
    tmp = os.path.join(rkeep, STATUS_NAME + ".tmp")
    with open(tmp, "w") as f:
        json.dump(st, f, sort_keys=True)
    os.replace(tmp, os.path.join(rkeep, STATUS_NAME))


def check_roots(src, keep, from_hour, backfill_src, src_allowed, keep_allowed, backfill_allowed):
    rsrc, rkeep = os.path.realpath(src), os.path.realpath(keep)
    if rkeep != os.path.realpath(keep_allowed):
        raise Refuse("keep is not the allow-listed keep dir")
    if rsrc != os.path.realpath(src_allowed):
        raise Refuse("src is not the allow-listed hv7 dir")
    if _under(rsrc, rkeep) or _under(rkeep, rsrc):
        raise Refuse("src and keep overlap")
    if not HOUR_RE.match(from_hour) or from_hour < MIN_FROM_HOUR:
        raise Refuse("from-hour before %s" % MIN_FROM_HOUR)
    rbf = None
    if backfill_src:
        rbf = os.path.realpath(backfill_src)
        if rbf != os.path.realpath(backfill_allowed) or _under(rbf, rkeep) or _under(rkeep, rbf):
            raise Refuse("backfill-src is not the allow-listed keeper dir")
    return rsrc, rkeep, rbf


def one_pass(src, keep, from_hour, backfill_src=None, events=None, src_allowed=DEFAULT_SRC, keep_allowed=DEFAULT_KEEP, backfill_allowed=BACKFILL_ALLOWED, out=print):
    rsrc, rkeep, rbf = check_roots(src, keep, from_hour, backfill_src, src_allowed, keep_allowed, backfill_allowed)
    events_path = events or os.path.join(src, ".mirror", "events.jsonl")
    if os.path.realpath(events_path) != os.path.join(rsrc, ".mirror", "events.jsonl"):
        raise Refuse("events must be <src>/.mirror/events.jsonl")
    try:
        verified = read_verified(events_path)
    except OSError:
        return 3, None
    os.makedirs(rkeep, exist_ok=True)
    keep_names = {}
    for n in sorted(os.listdir(rkeep)):
        m = TAPE_RE.match(n)
        if not m:
            continue
        if os.path.islink(os.path.join(rkeep, n)):
            raise Refuse("symlink in keep: %s" % n)
        keep_names.setdefault(m.group(1), []).append(n)
    src_names = tape_names(rsrc)
    bf_names = tape_names(rbf) if rbf else {}
    for names in keep_names.values():
        for n in names:
            for d in (rsrc, rbf):
                p = os.path.join(d, n) if d else None
                if p and os.path.lexists(p) and stat.S_ISREG(os.lstat(p).st_mode) and _ident(p) != _ident(os.path.join(rkeep, n)):
                    raise Refuse("hv7 file replaced after verification: stop and ask QP (%s)" % n)
    ver_ok = sorted(h for h in verified if h >= from_hour)
    skipped, bad_names, bf_not_in_log = {}, [], []

    def skip(reason):
        skipped[reason] = skipped.get(reason, 0) + 1

    def link(d, n, h):
        p = os.path.join(d, n)
        try:
            if not stat.S_ISREG(os.lstat(p).st_mode):
                skip("not_regular_file")
                bad_names.append(n)
                return None
            os.link(p, os.path.join(rkeep, n), follow_symlinks=False)
            keep_names[h] = [n]
            return True
        except OSError as e:
            out("KEEPER-FAIL link %s: %s (never copied)" % (n, e.strerror or type(e).__name__))
            return False

    linked, failed, already, bf_linked = [], [], 0, 0
    for h in ver_ok:
        if h in keep_names:
            already += 1
            continue
        if h in src_names:
            d, n, is_bf = rsrc, src_names[h], False
        elif h in bf_names:
            d, n, is_bf = rbf, bf_names[h], True
        else:
            skip("not_in_src")
            continue
        r = link(d, n, h)
        if r:
            linked.append(h)
            bf_linked += is_bf
        elif r is False:
            failed.append(h)
    for h in sorted(bf_names):
        if h >= from_hour and h not in verified and h not in src_names and h not in keep_names:
            skip("backfill_not_in_log")
            bf_not_in_log.append(h)
    for h in sorted(src_names):
        if h >= from_hour and h not in verified:
            skip("src_hour_not_verified")
    held = sorted(keep_names)
    missing = []
    if held:
        d, last = parse_hour(held[0]), parse_hour(held[-1])
        while d <= last:
            if fmt_hour(d) not in keep_names:
                missing.append(fmt_hour(d))
            d += timedelta(hours=1)
    linked.sort()
    st = {"pass_utc": now_utc(), "from_hour": from_hour, "verified_hours_seen": len(ver_ok), "linked_now": len(linked), "linked_first": linked[0] if linked else None,
          "linked_last": linked[-1] if linked else None, "already_present": already, "backfill_linked": bf_linked, "backfill_not_in_log_hours": bf_not_in_log,
          "not_regular_files": sorted(bad_names), "skipped": skipped, "failed_hours": sorted(failed), "keep_earliest": held[0] if held else None,
          "keep_latest": held[-1] if held else None, "keep_hours": len(held), "contiguous": not missing, "missing_hours": missing}
    write_status(rkeep, st)
    out("KEEPER verified_seen=%d linked_now=%d first=%s last=%s already=%d backfill_linked=%d skipped=%s failed=%d keep=%s..%s hours=%d contiguous=%s"
        % (st["verified_hours_seen"], st["linked_now"], st["linked_first"], st["linked_last"], already, bf_linked, json.dumps(skipped, sort_keys=True), len(failed),
           st["keep_earliest"], st["keep_latest"], len(held), st["contiguous"]))
    if missing:
        out("KEEPER missing_hours=%s" % ",".join(missing))
    if bad_names:
        out("KEEPER not_regular_files=%s" % ",".join(sorted(bad_names)))
    return (1 if failed else 0), st


def main(argv=None, src=DEFAULT_SRC, keep=DEFAULT_KEEP, backfill_allowed=BACKFILL_ALLOWED, sleep=time.sleep, max_passes=None, out=print):
    """src, keep, backfill_allowed, sleep and max_passes are test-only overrides (they also become the allow-listed values); the CLI does not expose them."""
    ap = argparse.ArgumentParser(description="hard-link keeper for the open-sim follow driver (verified closed hv7 hours only)")
    ap.add_argument("--from-hour", default=MIN_FROM_HOUR)
    ap.add_argument("--loop", type=int, default=300, help="seconds between passes; 0 = one pass")
    ap.add_argument("--backfill-src", default=None, help="exactly %s" % BACKFILL_ALLOWED)
    a = ap.parse_args(argv)
    n = consec_err = consec_log = 0
    while True:
        n += 1
        code = 0
        try:
            code, _ = one_pass(src, keep, a.from_hour, a.backfill_src, None, src, keep, backfill_allowed, out)
            consec_err = 0
        except Refuse as e:
            out("KEEPER-REFUSE %s" % e)
            try:  # status on a refusal, only into the allow-listed keep dir
                if os.path.isdir(keep) and not os.path.islink(keep):
                    write_status(os.path.realpath(keep), {"refused": str(e), "pass_utc": now_utc()})
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
        if a.loop <= 0 or (max_passes is not None and n >= max_passes):
            return code
        sleep(a.loop)


if __name__ == "__main__":
    sys.exit(main())
