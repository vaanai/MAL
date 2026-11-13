#!/usr/bin/env python3
"""r4b_price_keeper: hard-link keeper for the r4-b price path (DEC-031 Am.31 preview and r4bt-a2's registered pricer). PAPER ONLY. Runs on mal-research-0.

QP-RULING-1113-open-sims Addendum 1, B5 rule 3. Same behaviour and identity checks as scripts/ops/osb_keeper.py (whose helpers are imported, unedited), with other constants:
  src  /data2/mal/arena-tip-feed/hv7     log <src>/.mirror/events.jsonl     keep  /data2/mal/scratch/r4b-price-keep
  floor 2026-11-15T22: no hour before it is ever linked (--from-hour earlier: refused, exit 2). There is no --backfill-src.
A tape file is never opened, parsed, copied, edited or deleted: os.lstat and os.link(follow_symlinks=False) only, regular files only. A keep entry whose (dev, inode) differs from the src file
of the same name is refused ("hv7 file replaced after verification: stop and ask QP"). Nothing is ever pruned.

Missing-hour alert (QP condition): every pass lists the hours from max(floor, keep_earliest) to the newest verified closed hour that are in neither keep nor linkable (not kept after the
link step). If any: one loud line `R4B-KEEPER MISSING <n> hour(s): <names>`, status `missing_hours`; with --fail-on-missing the loop ends with exit 5 (the MiScusi job fails visibly).
Before the floor hour is in the verification log the status says waiting_for_floor: true and nothing is an error. Status: keep/.keeper-status.json (atomic).

Exit codes: 0 pass done; 1 an hour failed to link (retried next pass); 2 refusal or bad arguments; 3 verification log unreadable (5 consecutive in loop mode, at once with --loop 0);
4 five consecutive passes ended in an OSError; 5 missing hours with --fail-on-missing (never while waiting_for_floor).
--loop SECONDS repeats passes (default 300; 0 = one pass).
"""
import argparse
import json
import os
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


def one_pass(src, keep, from_hour, src_allowed=DEFAULT_SRC, keep_allowed=DEFAULT_KEEP, out=print):
    rsrc, rkeep = check_roots(src, keep, from_hour, src_allowed, keep_allowed)
    events_path = os.path.join(src, ".mirror", "events.jsonl")
    if os.path.realpath(events_path) != os.path.join(rsrc, ".mirror", "events.jsonl"):
        raise Refuse("events must be <src>/.mirror/events.jsonl")
    try:
        verified = ok.read_verified(events_path)
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
    src_names = ok.tape_names(rsrc)
    for names in keep_names.values():
        for n in names:
            p = os.path.join(rsrc, n)
            if os.path.lexists(p) and stat.S_ISREG(os.lstat(p).st_mode) and ok._ident(p) != ok._ident(os.path.join(rkeep, n)):
                raise Refuse("hv7 file replaced after verification: stop and ask QP (%s)" % n)
    ver_ok = sorted(h for h in verified if h >= from_hour)
    skipped, bad_names = {}, []

    def skip(reason):
        skipped[reason] = skipped.get(reason, 0) + 1

    linked, failed, already = [], [], 0
    for h in ver_ok:
        if h in keep_names:
            already += 1
            continue
        if h not in src_names:
            skip("not_in_src")
            continue
        n = src_names[h]
        p = os.path.join(rsrc, n)
        try:
            if not stat.S_ISREG(os.lstat(p).st_mode):
                skip("not_regular_file")
                bad_names.append(n)
                continue
            os.link(p, os.path.join(rkeep, n), follow_symlinks=False)
            keep_names[h] = [n]
            linked.append(h)
        except OSError as e:
            out("KEEPER-FAIL link %s: %s (never copied)" % (n, e.strerror or type(e).__name__))
            failed.append(h)
    for h in sorted(src_names):
        if h >= from_hour and h not in verified:
            skip("src_hour_not_verified")
    held = sorted(keep_names)
    waiting = FLOOR_HOUR not in verified and not held
    missing = []
    newest = max(ver_ok) if ver_ok else None
    if not waiting and newest is not None:
        start = max(FLOOR_HOUR, held[0]) if held else FLOOR_HOUR
        d, last = ok.parse_hour(start), ok.parse_hour(newest)
        while d <= last:
            if ok.fmt_hour(d) not in keep_names:
                missing.append(ok.fmt_hour(d))
            d += timedelta(hours=1)
    linked.sort()
    st = {"pass_utc": ok.now_utc(), "from_hour": from_hour, "floor_hour": FLOOR_HOUR, "waiting_for_floor": waiting, "verified_hours_seen": len(ver_ok), "linked_now": len(linked),
          "linked_first": linked[0] if linked else None, "linked_last": linked[-1] if linked else None, "already_present": already, "not_regular_files": sorted(bad_names),
          "skipped": skipped, "failed_hours": sorted(failed), "keep_earliest": held[0] if held else None, "keep_latest": held[-1] if held else None, "keep_hours": len(held),
          "contiguous": not missing, "missing_hours": missing}
    ok.write_status(rkeep, st)
    out("KEEPER verified_seen=%d linked_now=%d first=%s last=%s already=%d skipped=%s failed=%d keep=%s..%s hours=%d contiguous=%s waiting_for_floor=%s"
        % (st["verified_hours_seen"], st["linked_now"], st["linked_first"], st["linked_last"], already, json.dumps(skipped, sort_keys=True), len(failed),
           st["keep_earliest"], st["keep_latest"], len(held), st["contiguous"], waiting))
    if missing:
        out("R4B-KEEPER MISSING %d hour(s): %s" % (len(missing), ",".join(missing)))
    if bad_names:
        out("KEEPER not_regular_files=%s" % ",".join(sorted(bad_names)))
    return (1 if failed else 0), st


def main(argv=None, src=DEFAULT_SRC, keep=DEFAULT_KEEP, sleep=time.sleep, max_passes=None, out=print):
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
            code, st = one_pass(src, keep, a.from_hour, src, keep, out)
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
        if a.fail_on_missing and st and st["missing_hours"] and not st["waiting_for_floor"]:
            return 5
        if a.loop <= 0 or (max_passes is not None and n >= max_passes):
            return code
        sleep(a.loop)


if __name__ == "__main__":
    sys.exit(main())
