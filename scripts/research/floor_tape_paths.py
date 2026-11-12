"""floor_tape_paths: refused-path check for --tape-dir / tape sources of the FLOOR research scripts (paper replays). Pure string checks; opens nothing.
Allowed roots: the research-0 hv7 closed-hour derived copy (SEALS-BRIEF FLOOR LANE READERS (c)/(d)) and the three scratch work dirs these scripts use (floor-nov-funnel, floor-forward-ab, floor-gap-range), not all of /data2/mal/scratch.
require_files_ok checks the realpath of every tape-* entry the replay will open (a file symlink that resolves outside the allowed roots is refused).
Refused (checked first, on the absolute path and on its realpath): /var/lib/mal/sealed, /var/lib/mal-live, and any path containing arena/tape, arena-mirror, meta-capture, walk2, probe,
hv4, q50, or a path component starting with `forward-` (the sealed /data/mal/blocks/forward-* dirs; a component test so scratch/floor-forward-ab/ stays allowed)."""
import os

ALLOWED_ROOTS = ("/data2/mal/arena-tip-feed/hv7", "/data2/mal/scratch/floor-nov-funnel", "/data2/mal/scratch/floor-forward-ab", "/data2/mal/scratch/floor-gap-range")
REFUSED_PREFIXES = ("/var/lib/mal/sealed", "/var/lib/mal-live")
REFUSED_SUBSTR = ("arena/tape", "arena-mirror", "meta-capture", "walk2", "probe", "hv4", "q50")


def _under(p, root):
    return p == root or p.startswith(root.rstrip("/") + "/")


def refusal(path, roots=None):
    """-> None if the path may be read, else the reason string"""
    roots = roots or ALLOWED_ROOTS
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
    if not all(any(_under(p, r) for r in roots) for p in (ap, rp)):
        return "refused path (not under an allowed root %s): %s" % (roots, ap)
    return None


def require_ok(path, roots=None):
    why = refusal(path, roots)
    if why:
        raise SystemExit(why)


def require_files_ok(tape_dir, roots=None):
    """realpath-check every tape-* entry of tape_dir (the folder itself must pass too). Lists names only; opens nothing."""
    require_ok(tape_dir, roots)
    for name in sorted(os.listdir(tape_dir)):
        if name.startswith("tape-"):
            why = refusal(os.path.join(tape_dir, name), roots)
            if why:
                raise SystemExit("%s (file %s)" % (why, name))
