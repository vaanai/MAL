"""floor_tape_paths: refused-path check for --tape-dir / tape sources of the FLOOR research scripts (paper replays). Pure string checks; opens nothing.
Allowed roots: the research-0 hv7 closed-hour derived copy (SEALS-BRIEF FLOOR LANE READERS (c)/(d)) and scratch under /data2/mal/scratch/ (the tapecopy, tapelink dirs).
Refused (checked first, on the absolute path and on its realpath): /var/lib/mal/sealed, /var/lib/mal-live, and any path containing arena/tape, arena-mirror, meta-capture, walk2, probe,
hv4, q50, or a path component starting with `forward-` (the sealed /data/mal/blocks/forward-* dirs; a component test so scratch/floor-forward-ab/ stays allowed)."""
import os

ALLOWED_ROOTS = ("/data2/mal/arena-tip-feed/hv7", "/data2/mal/scratch")
REFUSED_PREFIXES = ("/var/lib/mal/sealed", "/var/lib/mal-live")
REFUSED_SUBSTR = ("arena/tape", "arena-mirror", "meta-capture", "walk2", "probe", "hv4", "q50")


def _under(p, root):
    return p == root or p.startswith(root.rstrip("/") + "/")


def refusal(path):
    """-> None if the path may be read, else the reason string"""
    for p in {os.path.abspath(path), os.path.realpath(path)}:
        for r in REFUSED_PREFIXES:
            if _under(p, r):
                return "refused path (under %s): %s" % (r, p)
        for s in REFUSED_SUBSTR:
            if s in p:
                return "refused path (contains %r): %s" % (s, p)
        if any(c.startswith("forward-") for c in p.split("/")):
            return "refused path (component forward-*): %s" % p
    ap, rp = os.path.abspath(path), os.path.realpath(path)
    if not all(any(_under(p, r) for r in ALLOWED_ROOTS) for p in (ap, rp)):
        return "refused path (not under an allowed root %s): %s" % (ALLOWED_ROOTS, ap)
    return None


def require_ok(path):
    why = refusal(path)
    if why:
        raise SystemExit(why)
