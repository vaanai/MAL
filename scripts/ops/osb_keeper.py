#!/usr/bin/env python3
"""osb_keeper: hard-link keeper for the open-simulation follow driver (scripts/research/osb_follow.py). PAPER ONLY.

Rule it enforces (QP, SEALS-BRIEF line (k), PR #1362): the keeper may only hard-link `tape-<hour>.jsonl` / `tape-<hour>.jsonl.zst` files from the arena tip receiver's hv7 dir whose hour the
receiver's verification log lists as verified and closed, for hours at or after 2026-11-02T01Z, into /data2/mal/scratch/floor-open-sim/keep/; it never copies, edits, parses or opens them, and
never links any other file.

What one pass does
  - Reads ONLY the verification log (`<src>/.mirror/events.jsonl`; rows {"h": "YYYY-MM-DDTHH", "kind": "closed", ...}; the same rows tools/tip_hour_health.load_verified reads: kind `closed`, key `h`)
    and directory listings. A tape file is never opened or read; the only stat on one is inode identity (os.lstat on keep entries, os.stat on the src file of the same name) for the refusal
    check below. Nothing is ever deleted from keep (no pruning in this tool).
  - For every hour that the log lists as closed, at or after --from-hour, whose tape-<hour>.jsonl[.zst] exists in src and in no form in keep: os.link(src, keep). If os.link fails the hour FAILS
    loudly (exit 1); there is no copy fallback.
  - Names that do not match tape-YYYY-MM-DDTHH.jsonl[.zst] are never linked.
  - Refuses (exit 2): --keep not under /data2/mal/scratch/floor-open-sim after realpath; src and keep overlapping; --from-hour before 2026-11-02T01; a keep tape entry that is a symlink or whose
    inode differs from the src file of the same name (names only are reported).

--backfill-src DIR  (PENDING QP RULING: the manager does not run it until QP rules on it)
  hv7 keeps only about 240 hours; older hours survive as hard links in the manager's keeper dir /data2/mal/scratch/floor-stage1-proof/tapecopy-roll (job #2566; the same inodes). When DIR is exactly
  that path, an hour missing in src but present there is linked from it under the same conditions (hour >= from-hour, listed closed in the log). If the log no longer lists such an old hour, it
  is linked only when --backfill-trust-roll is also given; the count of hours linked on that basis is printed. Any other DIR is refused (exit 2).

Output (stdout, names and counts only) and keep/.keeper-status.json (atomic): hours seen verified, linked now (count, first, last), already present, skipped by reason, earliest and latest hour in
keep, contiguity (missing hours by name).

Exit codes: 0 pass done; 1 at least one hour failed to link (loud, nothing copied); 2 refusal or bad arguments; 3 verification log missing or unreadable.
--loop SECONDS repeats passes (default 300; 0 = one pass); a refusal (2) or unreadable log (3) ends the loop with that code; a link failure (1) is retried on the next pass.
"""
import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone

DEFAULT_SRC = "/data2/mal/arena-tip-feed/hv7"
DEFAULT_KEEP = "/data2/mal/scratch/floor-open-sim/keep"
KEEP_ROOT = "/data2/mal/scratch/floor-open-sim"
BACKFILL_ALLOWED = "/data2/mal/scratch/floor-stage1-proof/tapecopy-roll"
MIN_FROM_HOUR = "2026-11-02T01"
HOUR_RE = re.compile(r"^\d{4}-\d\d-\d\dT\d\d$")
TAPE_RE = re.compile(r"^tape-(\d{4}-\d\d-\d\dT\d\d)\.jsonl(\.zst)?$")
STATUS_NAME = ".keeper-status.json"


class Refuse(Exception):
    pass


def parse_hour(k):
    return datetime.strptime(k, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc)


def fmt_hour(d):
    return d.strftime("%Y-%m-%dT%H")


def read_verified(events_path):
    """Hours with a `closed` row in the receiver's verification log (names only; the log is not a tape file)."""
    out = set()
    with open(events_path, "rb") as f:
        for line in f:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if isinstance(r, dict) and r.get("kind") == "closed" and isinstance(r.get("h"), str) and HOUR_RE.match(r["h"]):
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


def one_pass(src, events, keep, from_hour, backfill_src=None, trust_roll=False, keep_root=KEEP_ROOT, backfill_allowed=BACKFILL_ALLOWED, out=print):
    rsrc, rkeep = os.path.realpath(src), os.path.realpath(keep)
    if not _under(rkeep, os.path.realpath(keep_root)):
        raise Refuse("keep not under the open-sim root")
    if _under(rsrc, rkeep) or _under(rkeep, rsrc):
        raise Refuse("src and keep overlap")
    if not HOUR_RE.match(from_hour) or from_hour < MIN_FROM_HOUR:
        raise Refuse("from-hour before %s" % MIN_FROM_HOUR)
    rbf = None
    if backfill_src:
        rbf = os.path.realpath(backfill_src)
        if rbf != os.path.realpath(backfill_allowed) or _under(rbf, rkeep) or _under(rkeep, rbf):
            raise Refuse("backfill-src is not the allow-listed keeper dir")
    try:
        verified = read_verified(events)
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
    # inode identity of every keep entry against the same-named file in src / backfill
    for names in keep_names.values():
        for n in names:
            for d in (rsrc, rbf):
                if d and os.path.exists(os.path.join(d, n)) and os.stat(os.path.join(d, n)).st_ino != os.lstat(os.path.join(rkeep, n)).st_ino:
                    raise Refuse("keep entry differs in inode from its source: %s" % n)
    ver_ok = sorted(h for h in verified if h >= from_hour)
    skipped = {}

    def skip(reason):
        skipped[reason] = skipped.get(reason, 0) + 1

    def link(d, n, h):
        try:
            os.link(os.path.join(d, n), os.path.join(rkeep, n))
            keep_names[h] = [n]
            return True
        except OSError as e:
            out("KEEPER-FAIL link %s: %s (never copied)" % (n, e.strerror or type(e).__name__))
            return False

    linked, failed, already, trust_linked = [], [], 0, 0
    for h in ver_ok:
        if h in keep_names:
            already += 1
            continue
        if h in src_names:
            d, n = rsrc, src_names[h]
        elif h in bf_names:
            d, n = rbf, bf_names[h]
        else:
            skip("not_in_src")
            continue
        (linked if link(d, n, h) else failed).append(h)
    for h in sorted(bf_names):
        if h >= from_hour and h not in verified and h not in src_names and h not in keep_names:
            if not trust_roll:
                skip("backfill_not_in_log")
            elif link(rbf, bf_names[h], h):
                linked.append(h)
                trust_linked += 1
            else:
                failed.append(h)
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
    st = {"from_hour": from_hour, "verified_hours_seen": len(ver_ok), "linked_now": len(linked), "linked_first": linked[0] if linked else None,
          "linked_last": linked[-1] if linked else None, "already_present": already, "skipped": skipped, "failed_hours": sorted(failed),
          "backfill_trust_roll_linked": trust_linked, "keep_earliest": held[0] if held else None, "keep_latest": held[-1] if held else None,
          "keep_hours": len(held), "contiguous": not missing, "missing_hours": missing}
    tmp = os.path.join(rkeep, STATUS_NAME + ".tmp")
    with open(tmp, "w") as f:
        json.dump(st, f, sort_keys=True)
    os.replace(tmp, os.path.join(rkeep, STATUS_NAME))
    out("KEEPER verified_seen=%d linked_now=%d first=%s last=%s already=%d skipped=%s failed=%d trust_roll_linked=%d keep=%s..%s hours=%d contiguous=%s"
        % (st["verified_hours_seen"], st["linked_now"], st["linked_first"], st["linked_last"], already, json.dumps(skipped, sort_keys=True), len(failed), trust_linked,
           st["keep_earliest"], st["keep_latest"], len(held), st["contiguous"]))
    if missing:
        out("KEEPER missing_hours=%s" % ",".join(missing))
    return (1 if failed else 0), st


def main(argv=None, keep_root=KEEP_ROOT, backfill_allowed=BACKFILL_ALLOWED, sleep=time.sleep, max_passes=None, out=print):
    """keep_root, backfill_allowed, sleep and max_passes are test-only overrides; the CLI does not expose them."""
    ap = argparse.ArgumentParser(description="hard-link keeper for the open-sim follow driver (verified closed hv7 hours only)")
    ap.add_argument("--src", default=DEFAULT_SRC)
    ap.add_argument("--events", default=None, help="verification log (default <src>/.mirror/events.jsonl)")
    ap.add_argument("--keep", default=DEFAULT_KEEP)
    ap.add_argument("--from-hour", default=MIN_FROM_HOUR)
    ap.add_argument("--loop", type=int, default=300, help="seconds between passes; 0 = one pass")
    ap.add_argument("--backfill-src", default=None, help="PENDING QP RULING: only %s" % BACKFILL_ALLOWED)
    ap.add_argument("--backfill-trust-roll", action="store_true", help="PENDING QP RULING: link old roll hours the log no longer lists")
    a = ap.parse_args(argv)
    events = a.events or os.path.join(a.src, ".mirror", "events.jsonl")
    n = 0
    while True:
        n += 1
        try:
            code, _ = one_pass(a.src, events, a.keep, a.from_hour, a.backfill_src, a.backfill_trust_roll, keep_root, backfill_allowed, out)
        except Refuse as e:
            out("KEEPER-REFUSE %s" % e)
            return 2
        if code == 3:
            out("KEEPER-FAIL verification log unreadable")
            return 3
        if a.loop <= 0 or (max_passes is not None and n >= max_passes):
            return code
        sleep(a.loop)


if __name__ == "__main__":
    sys.exit(main())
