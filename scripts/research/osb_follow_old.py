#!/usr/bin/env python3
"""osb_follow_old: opt-in front end of scripts/research/osb_follow.py for FLOOR-OLD (book osb-old). PAPER only; in-sample research, not a gate read. Nothing here says any book makes money.

FLOOR-OLD = FLOOR's exact entry and exit rule (lane host, gap guard, TP 1.5, hold 24 h, vault <= 0.5 SOL, V band, vol, 6 h ratio, 60 s cooldown) on WSOL PumpSwap pools aged 200 h to 21 d
instead of 24 h to 200 h (docs/rotation/books/osb-old.md; QP-RULING-1120-floor-rotate-old Q1, the forward-only route: no seed read of October tape).

WHY A SEPARATE FILE: osb_follow.py's bytes are part of every running book's code.sha256 (a changed simulator is a new book); this file leaves them, tools/arena_pool_table.py and every pin untouched.
Its only act is, for the duration of one process, to set tools.arena_pool_table.AGE_DROP_S to 21 d + 1 h (the table frees a pool's prints past that age; the default 201 h would free every pool
before it could be a candidate), then to run osb_follow's own loop (manifest sha, pins, per-hour md5, restart check, lock, status). The age window itself comes from the manifest's own
rule_overrides AGE_LO / AGE_HI, which osb_follow already allows.

Refusals (exit 2): rule_overrides must be exactly {"AGE_LO": 720000, "AGE_HI": 1814400} (200 h, 21 d); host "lane"; no twin_of / pricing_lat_entry_s / gap_exclusion other than "on".
Coverage limit (disclosed in the start manifest): pool births come only from the book's own follow of tape from seed_from_hour onward (no earlier seed). A pool's age is therefore known only
if it was admitted after seed_from_hour; the full 21 d band is covered only at hours >= seed_from_hour + 21 d. Pools born in a feed gap are never admitted and never trade.
Usage: osb_follow_old.py MANIFEST.json [--once] [--max-hours N]
"""
import argparse, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import osb_follow as O  # noqa: E402
from tools import arena_pool_table as PT  # noqa: E402

AGE_LO_S, AGE_HI_S = 200 * 3600, 21 * 24 * 3600  # 720000, 1814400
AGE_DROP_OLD_S = AGE_HI_S + 3600  # same one-hour margin as the stock 200 h + 1 h
REQUIRED_OVERRIDES = {"AGE_LO": AGE_LO_S, "AGE_HI": AGE_HI_S}


def check_manifest(m):
    """Extra refusals on top of osb_follow.load_manifest. Raises osb_follow.Refused."""
    if m["rule_overrides"] != REQUIRED_OVERRIDES:
        raise O.Refused("osb_follow_old: rule_overrides must be exactly %s (FLOOR-OLD: 200 h to 21 d, nothing else changed)" % (REQUIRED_OVERRIDES,))
    if m["host"] != "lane":
        raise O.Refused("osb_follow_old: host must be 'lane' (FLOOR's own host)")
    if "twin_of" in m or "pricing_lat_entry_s" in m or m.get("gap_exclusion", "on") != "on":
        raise O.Refused("osb_follow_old: a strict book only (no twin_of, no pricing_lat_entry_s, gap_exclusion 'on' or absent)")


_orig_load = O.load_manifest


def load_manifest(path):
    m = _orig_load(path)
    check_manifest(m)
    return m


def follow(manifest_path, **kw):
    saved = PT.AGE_DROP_S
    O.load_manifest = load_manifest
    PT.AGE_DROP_S = AGE_DROP_OLD_S
    try:
        return O.follow(manifest_path, **kw)
    finally:
        PT.AGE_DROP_S = saved
        O.load_manifest = _orig_load


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("manifest")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--max-hours", type=int, default=None)
    a = ap.parse_args(argv)
    try:
        return follow(a.manifest, once=a.once, max_hours=a.max_hours)
    except O.Refused as e:
        print("REFUSED: %s" % e, file=sys.stderr)
        return O.EXIT_REFUSED


if __name__ == "__main__":
    raise SystemExit(main())
