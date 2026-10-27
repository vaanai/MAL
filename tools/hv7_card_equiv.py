#!/usr/bin/env python3
"""hv7_card_equiv: the DESIGN C3 md5 decision-equivalence proof, screen-reuse leg: HV7's card for a pool at an instant is byte-identical to HV6's card for the
same pool at the same instant (same card builder, same as-of), 2026-10-21..23.

PAPER only. 0 API, fake chain (the same FakeChain the HV6 real-layout drill used), read-only. Prints COUNTS only.

SEAL CONDITIONS (QP #996, comment 6431175846, (c)1-5) which this tool keeps:
  1. It opens exactly three files, by constructed path, never by listing or stat of the drill root:
       /data/mal/arena/haiku-v6/drill/real-5071e29a-r16g-20261026/real-a/v6/cards/2026-10-2{1,2,3}.jsonl
     The HV6 drill id is one of an allowlist of two (--hv6-drill-id; default the pre-filter real-layout drill; the other is the phantom-ON drill p3-on-8cd8fe54), and --expect-cards-md5 (the cards md5 pinned in the manifest) is REQUIRED: a different md5 refuses the run. Nothing else under
     the HV6 drill root is opened: no decision log, fill, exit, llm row, counter, result file or other drill id.
  2. Any card row whose as_of_ms is outside [2026-10-21T00Z, 2026-10-24T00Z) refuses the whole run. No later card of the same pool is joined, and no mark, exit,
     price path or outcome is read.
  4. No outcome is computed on these cards. Only equal / unequal counts are printed.
  5. No card field is chosen by looking at card contents: the join key is (as_of_ms, card id) and the compared object is the whole card.
The tape side is the same real-layout loader as the HV6/HV7 drills (ArenaState over the real hour files, its hour list filtered to the three days).

WHAT IS COMPARED. For each HV6 card (id, as_of_ms) the HV7 runner's own `_card` (hv6_cards.build_card, the as-of check, the chain part, the quote history, the
holder count) is run at the same instant on a replay of the tape, with the same feature object (hv6_cards.scan on that pool). HV6 builds a card with a setup tag
(`setup` set) for tagged pools and without one for board pools; HV7 never tags, so the HV6 card is compared with `setup` set to None (the only field a tag
changes; `x` is None in both). Cards HV6 built untagged are compared byte for byte, unchanged. HV6 scans at H:MM:02, HV7's t_d is H:02:00: the comparison is
at HV6's instants, not at 2 s earlier, so it proves the builder, not the t_d instant.

PASS iff the cards md5 equals the pinned one AND compared pairs >= MIN_PAIRS AND unequal == 0 AND hv7_no_card == 0 AND not_candidate == 0 AND hv6_sha_self_mismatch == 0.
"""
from __future__ import annotations

if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

os.environ.setdefault("MAL_ARENA_TEST", "1")

from tools import hv4_cards as C  # noqa: E402
from tools import hv6_cards as H  # noqa: E402
from tools import hv7_runner as R  # noqa: E402
from tools.arena_state import ArenaState  # noqa: E402
from tools.drill_hv6_item6 import FakeChain  # noqa: E402
from tools.drill_hv7_real_layout import ALLOWED_DAYS, DRILL_ROOT, END_EXCLUSIVE, ID_RE, Refused, day_ms, hour_scan, is_drill_root, parse_days  # noqa: E402
from tools.hv6_agents import p4  # noqa: E402

import re

HV6_DRILL_ID = "real-5071e29a-r16g-20261026"          # recorded BEFORE the phantom filter (#980): its cards were built from unfiltered tape
HV6_DRILL_ID_P3_ON = "p3-on-8cd8fe54"                  # HV6's phantom-ON drill (job #1684, cards_md5 c633075023271a38f8ec8cad1226772e, 1560 cards)
HV6_DRILL_IDS = (HV6_DRILL_ID, HV6_DRILL_ID_P3_ON)     # the allowlist: exactly these two, nothing else is ever joined
HV6_DRILL_BASE = Path("/data/mal/arena/haiku-v6/drill")
# ASSUMPTION (not verified, the drill root is never listed): p3-on-8cd8fe54 uses the same real-a/v6/cards layout as the real-layout drill.
HV6_CARDS_DIR = HV6_DRILL_BASE / HV6_DRILL_ID / "real-a" / "v6" / "cards"
_FIELD_RE = re.compile(r"[a-z_0-9]{1,40}")


def hv6_cards_dir(hv6_drill_id: str) -> Path:
    """The constructed cards/ path of an allowlisted HV6 drill id. Anything else refuses (no path is built from free text)."""
    if hv6_drill_id not in HV6_DRILL_IDS:
        raise Refused("hv6_drill_id_not_allowed")
    return HV6_DRILL_BASE / hv6_drill_id / "real-a" / "v6" / "cards"


def diff_fields(a: Any, b: Any, prefix: str = "", out: dict[str, int] | None = None, depth: int = 0) -> dict[str, int]:
    """Names of the card fields that differ (dotted key path), with counts. Never a value. A key that is not a plain schema name is counted as '<other>'."""
    out = {} if out is None else out
    if isinstance(a, dict) and isinstance(b, dict) and depth < 4:
        for k in sorted(set(a) | set(b), key=str):
            name = k if isinstance(k, str) and _FIELD_RE.fullmatch(k) else "<other>"
            path = f"{prefix}.{name}" if prefix else name
            if k not in a or k not in b:
                out[path + "(one_side_only)"] = out.get(path + "(one_side_only)", 0) + 1
            else:
                diff_fields(a[k], b[k], path, out, depth + 1)
    elif a != b or type(a) is not type(b):
        out[prefix or "<card>"] = out.get(prefix or "<card>", 0) + 1
    return out
REAL_TAPE = H.TAPE_ROOT
WINDOW_MS = (day_ms(ALLOWED_DAYS[0]), day_ms(END_EXCLUSIVE))
MIN_PAIRS = 50


class NullEngine:
    def on_row(self, row: dict) -> None:
        return None


class NoModel:
    def complete(self, request, now_ms):
        raise AssertionError("the card comparison makes no model call")


def read_hv6_cards(cards_dir: Path, days: list[str], window: tuple[int, int], expect_md5: str) -> tuple[dict[int, dict[str, dict]], dict[str, int]]:
    """Open exactly cards_dir/<day>.jsonl for the chosen days (no listing, no stat of anything else). Returns {as_of_ms: {pool id: card}} and counts."""
    h = hashlib.md5()
    by_t: dict[int, dict[str, dict]] = {}
    rows = self_bad = 0
    for d in days:
        data = (Path(cards_dir) / f"{d}.jsonl").read_bytes()
        h.update(f"cards/{d}.jsonl".encode() + b"\0" + data + b"\0")        # the same convention as tools.drill_hv6_item6.cards_md5 (relative to the run root)
        for line in data.splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            card = r["card"]
            ms = card["as_of_ms"]
            if not isinstance(ms, int) or isinstance(ms, bool) or not (window[0] <= ms < window[1]):
                raise Refused("card_as_of_outside_window")
            rows += 1
            self_bad += 1 if p4.card_sha256(dict(card)) != r["sha"] else 0
            by_t.setdefault(ms, {})[card["id"]] = card
    got = h.hexdigest()
    if got != expect_md5:
        raise Refused("cards_md5_differs_from_the_pinned_one")
    return by_t, {"hv6_rows": rows, "hv6_sha_self_mismatch": self_bad, "instants": len(by_t)}


def compare(by_t: dict[int, dict[str, dict]], root: Path, tape: Path, days: list[str], *, strict_leak: bool = False, field_counts: dict[str, int] | None = None,
            phantom_filter: bool = True) -> dict[str, int]:
    start = min(by_t) if by_t else 0
    guard = R.Hv7Guard(root / "v7")
    run = R.Hv7Runner(R.Hv7Config(root=str(root / "v7"), frozen_sha="cardequiv0000", clock_start_ms=start, phantom_filter=phantom_filter), ArenaState(tape, start_ms=0), NullEngine(),
                      NoModel(), FakeChain(), guard)
    want = frozenset(days)
    orig = run.state._hour_files
    run.state._hour_files = lambda: [t for t in orig() if t[0][:10] in want and t[0] < END_EXCLUSIVE]
    n = {"pairs": 0, "equal": 0, "unequal": 0, "tagged_pairs": 0, "hv7_no_card": 0, "not_candidate": 0, "unknown_pool": 0}
    for T in sorted(by_t):
        run.poll_to(T)
        run.sim_now = max(run.sim_now, T)
        pools = by_t[T]
        items, ids = [], []
        for pid in sorted(pools):
            snap = run.state.snapshot(pid)
            p = run.state.pool(pid)
            if snap is None or p is None:
                n["unknown_pool"] += 1
                continue
            items.append((snap, list(p.events)))
            ids.append(pid)
        res = H.scan(iter(items), as_of_ms=T, limiter=C.SetupLimiter(), board_on=False)
        for (snap, prints), pid in zip(items, ids):
            if pid not in res.feats:
                n["not_candidate"] += 1
                continue
            card7 = run._card(pid, T, snap, prints, res.feats[pid])
            if card7 is None:
                n["hv7_no_card"] += 1
                continue
            c6 = dict(pools[pid])
            n["tagged_pairs"] += 1 if c6.get("setup") is not None else 0
            c6["setup"] = None
            n["pairs"] += 1
            if p4.card_sha256(c6) == p4.card_sha256(card7):
                n["equal"] += 1
            else:
                n["unequal"] += 1
                if field_counts is not None:
                    diff_fields(c6, card7, out=field_counts)
    n["leakage_refused"] = run.counters.get("leakage_refused", 0)
    return n


def run_equiv(days: list[str], drill_id: str, expect_md5: str, *, cards_dir: Path | None = None, tape_dir: str | None = None, root: Path | None = None,
              window: tuple[int, int] | None = None, hv6_drill_id: str = HV6_DRILL_ID, field_diff: bool = False, phantom_filter: bool = True) -> dict[str, Any]:
    ref_dir = hv6_cards_dir(hv6_drill_id)       # refuses an id outside the allowlist first, even when cards_dir is given (tests)
    tape_dir = REAL_TAPE if tape_dir is None else tape_dir
    if tape_dir != REAL_TAPE:
        raise Refused("tape_dir_not_allowed")
    if not ID_RE.fullmatch(drill_id) or not isinstance(expect_md5, str) or len(expect_md5) != 32:
        raise Refused("drill_id_or_md5")
    root = Path(root) if root is not None else DRILL_ROOT / drill_id
    if not is_drill_root(root):
        raise Refused("not_a_drill_root")
    R.check_path(root / "x", "w")
    root.mkdir(parents=True, exist_ok=True)
    info = hour_scan(Path(tape_dir), days)
    by_t, cnt = read_hv6_cards(ref_dir if cards_dir is None else cards_dir, days, window or WINDOW_MS, expect_md5)
    fc: dict[str, int] | None = {} if field_diff else None
    n = compare(by_t, root, Path(tape_dir), days, field_counts=fc, phantom_filter=phantom_filter)
    ok = n["pairs"] >= MIN_PAIRS and n["unequal"] == 0 and n["hv7_no_card"] == 0 and n["not_candidate"] == 0 and n["unknown_pool"] == 0 and cnt["hv6_sha_self_mismatch"] == 0
    return {"hv7_card_equiv": "PASS" if ok else "FAIL", "hv6_drill_id": hv6_drill_id, "cards_md5_equals_pinned": True, "cards_md5": expect_md5, "min_pairs": MIN_PAIRS,
            "days": len(days), **info, **cnt, **n, "outcome_free": True, "api_calls": 0,
            **({"unequal_field_counts": dict(sorted(fc.items()))} if fc is not None else {})}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="HV7 vs HV6 card equivalence on 2026-10-21..23. Opens exactly the three pinned HV6 card files. Counts only.")
    ap.add_argument("--days", default=",".join(ALLOWED_DAYS))
    ap.add_argument("--drill-id", required=True, help="HV7 scratch drill id")
    ap.add_argument("--expect-cards-md5", required=True, help="the cards md5 pinned in the manifest (QP #996 (c)1)")
    ap.add_argument("--hv6-drill-id", default=HV6_DRILL_ID, choices=HV6_DRILL_IDS, help="the HV6 reference drill (allowlist of two). Default: the pre-filter real-layout drill")
    ap.add_argument("--field-diff", action="store_true", help="report only: counts of card field NAMES that differ in the unequal pairs (no values); does not change the verdict")
    a = ap.parse_args(argv)
    try:
        res = run_equiv(parse_days(a.days), a.drill_id, a.expect_cards_md5, hv6_drill_id=a.hv6_drill_id, field_diff=a.field_diff)
    except Refused as e:
        print(json.dumps({"hv7_card_equiv": "refused", "why": str(e)}), file=sys.stderr)
        return 2
    except Exception as e:  # noqa: BLE001 - the class name only: a message could echo a card value
        print(json.dumps({"hv7_card_equiv": "error", "class": type(e).__name__}), file=sys.stderr)
        return 3
    print(json.dumps(res, sort_keys=True, separators=(",", ":")))
    return 0 if res["hv7_card_equiv"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
