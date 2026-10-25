#!/usr/bin/env python3
"""HV4 gate read at the DEC-031 Amendment 15 instant B: arena_am6_read + arena_score, unchanged on disk, with only the read boundary replaced.

    python -m tools.hv4_am15_read --instant SCREEN.json --ledger-root DIR --registry FILE --health T.jsonl --health-sha SHA [...] --restarts R.jsonl
                                    --as-of-ms MS [--excl-out FILE] [--json FILE] [--record --reads-dir DIR]

WHY. Amendment 15 item 1 moves HV4's read instant to the attempt-margin boundary B that tools/hv4_read_instant.py computes and writes once. The pinned read
path (tools/arena_am6_read.py, sha256 ef6e4555; tools/arena_score.py) computes the UNAMENDED boundary with arena_score.blind_read_instant and refuses any other
(the wrapper, the parity guard and score_bot all check it), and score_bot's own forced-boundary option is a comparator screen that record_read refuses.
Item 6 allows the pin change of either tool, disclosed at the read. This wrapper leaves both files byte-identical and changes ONE thing, in this process only:
while arena_am6_read.run executes, arena_score.blind_read_instant returns B for this bot's clock (every caller: the wrapper's pass 1, the parity guard and
score_bot's natural boundary), and arena_score.record_read adds an "am15" disclosure block to the one recorded read. Both are restored on exit, error or not.
Everything else (the Am.6 cut, exclusions, both caps, the parity guard, the scorer's order of operations, the gate cells, the settle and orphan rules, the
one-read record) is the pinned code running as it does for any Am.6 book.

CHECKS before the scorer is called (each failure is exit 2, nothing that looks like a score is printed):
  1. The instant file is this tool family's write-once record (schema hv4_read_instant_v1), mode "screen", status READ_INSTANT, for --bot, with the clock of
     the bot's registry line. A NOT_DECIDABLE instant file (no B by END_H) prints NOT_DECIDABLE and never calls the scorer. A confirm-mode file is refused:
     the confirmation read (DEC-031 section 2 step 4: also a one-sided bootstrap p <= 0.05 / c) is not implemented here.
  2. B is RECOMPUTED by hv4_read_instant.compute from the same ledger, hour tables and restarts at --as-of-ms and must equal the file's B (so a file that
     does not follow from the inputs is refused). --as-of-ms must be >= B.
  3. tools/arena_am6_read.py has sha256 ef6e4555... and tools/arena_score.py has one of the two known sha256 values (a508ed79..., the frozen-sha copy;
     c4859323..., the copy on main after #815, report-only additions): the replacement relies on how those exact files call blind_read_instant.
Printed and recorded ("am15" block): B, the instant file sha256, this tool's and hv4_read_instant's sha256, the arena_score / arena_am6_read sha256 in use,
and the UNAMENDED boundary (arena_score.blind_read_instant over the same non-dropped decision instants, the first call of the read: an attempt-boundary record,
not the read instant; Amendment 15 item 4). Run at or after B, once the attempts before B have settled (the scorer's PENDING rule applies unchanged).
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Iterator, Sequence

from tools import arena_am6_read as R
from tools import arena_score as S
from tools import hv4_read_instant as F
import os

from tools.arena_paper import TEST_ENV, load_registry

ARENA_AM6_READ_SHA256 = F.PINNED_AM6_READ_SHA256
ARENA_SCORE_SHA256_KNOWN = (F.PINNED_ARENA_SCORE_SHA256, "c4859323622794f5f97a131eb32256c905ca2ab6c1adf8ab21568fc2a98f27f3")


class Refused(Exception):
    pass


def _sha(p: str | Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def load_instant(path: str | Path) -> tuple[dict, str]:
    try:
        raw = Path(path).read_bytes()
        rec = json.loads(raw)
    except (OSError, ValueError) as ex:
        raise Refused(f"instant file unreadable: {ex}") from None
    if not isinstance(rec, dict) or rec.get("schema") != F.SCHEMA:
        raise Refused("not a hv4_read_instant record")
    return rec, hashlib.sha256(raw).hexdigest()


def check_pins() -> dict[str, str]:
    am6 = _sha(R.__file__)
    sc = _sha(S.__file__)
    if am6 != ARENA_AM6_READ_SHA256:
        raise Refused(f"tools/arena_am6_read.py sha256 {am6} is not the pinned {ARENA_AM6_READ_SHA256}")
    if sc not in ARENA_SCORE_SHA256_KNOWN:
        raise Refused(f"tools/arena_score.py sha256 {sc} is not a known copy {ARENA_SCORE_SHA256_KNOWN}")
    return {"arena_am6_read_sha256": am6, "arena_score_sha256": sc, "hv4_read_instant_sha256": _sha(F.__file__), "hv4_am15_read_sha256": _sha(__file__)}


@contextlib.contextmanager
def am15_boundary(clock_ms: int, b_ms: int, am15_block: dict) -> Iterator[dict]:
    """In this process only: arena_score.blind_read_instant returns b_ms for clock_ms (the original for any other clock), and arena_score.record_read adds the
    am15 block. The first call's unamended value is kept in the yielded dict. Restored on exit."""
    orig_bri, orig_rec = S.blind_read_instant, S.record_read
    seen: dict[str, Any] = {}

    def bri(entry_ms: Sequence[int], clock_start_ms: int) -> int | None:
        if clock_start_ms != clock_ms:
            return orig_bri(entry_ms, clock_start_ms)
        if "unamended_boundary_ms" not in seen:
            seen["unamended_boundary_ms"] = orig_bri(entry_ms, clock_start_ms)
        return b_ms

    def rec(reads_dir, res):
        return orig_rec(reads_dir, dict(res, am15=dict(am15_block, unamended_boundary_ms=seen.get("unamended_boundary_ms"))))

    S.blind_read_instant, S.record_read = bri, rec
    try:
        yield seen
    finally:
        S.blind_read_instant, S.record_read = orig_bri, orig_rec


def is_count_only(bot_res: dict) -> bool:
    """True if the deciding cell's only misses are count misses (n < 100 or fewer than 5 UTC dates): the scorer's fail_label, or every miss starts with 'n ' / 'days '
    (not the 'days positive' majority miss, which is not a count)."""
    if bot_res.get("fail_label"):
        return True
    misses = [m for ms in (bot_res.get("deciding") or {}).get("misses", {}).values() for m in ms]
    return bool(misses) and all(m.startswith("n ") or (m.startswith("days ") and not m.startswith("days positive")) for m in misses)


def run(instant_path: str, ledger_root: str, registry: str, bot: str, health_texts: Sequence[str], health_shas: Sequence[str], as_of_ms: int, restarts_text: str,
        excl_out: str | None = None, record: bool = False, reads_dir: str | None = None) -> tuple[int, list[str], dict | None]:
    if bot not in F.BOT_IDS:
        raise Refused(f"{bot!r} is not one of the nine HV4 bot_ids")
    pins = check_pins()
    rec, isha = load_instant(instant_path)
    reg = load_registry(registry)
    if bot not in reg:
        raise Refused(f"{bot}: not in the registry")
    clock = reg[bot]["clock_start_ms"]
    if rec.get("bot_id") != bot or rec.get("clock_ms") != clock:
        raise Refused(f"instant file is for {rec.get('bot_id')} clock {rec.get('clock_ms')}, the registry has {bot} clock {clock}")
    if rec.get("mode") != "screen":
        raise Refused("only a screen instant is read here; the confirmation read (section 2 step 4, with its bootstrap p <= 0.05 / c) is not implemented")
    if clock != F.CLOCK_MS:
        raise Refused(f"registry clock {clock} is not the HV4 clock {F.CLOCK_MS}")
    head = f"AM15 READ  bot {bot}  instant_file_sha256 {isha}  status {rec.get('status')}"
    if rec.get("status") == F.ST_ND:
        return 0, [head, "NOT_DECIDABLE: no Amendment 15 boundary by END_H (DEC-031 Am.15 item 1): final for the bot_id, counts only; the scorer is not called"], None
    if rec.get("status") != F.ST_READY or not isinstance(rec.get("read_instant_ms"), int):
        raise Refused(f"instant file status {rec.get('status')!r} is not READ_INSTANT")
    b = rec["read_instant_ms"]
    if as_of_ms < b:
        raise Refused("--as-of-ms is before the instant B")
    if os.environ.get(TEST_ENV) != "1":  # QP-VERDICT-915 item 2: the first scored output is the one recorded read, no preview, no PENDING view
        if not record:
            raise Refused("--record is required: an unrecorded run prints an outcome preview (DEC-031 Am.15 item 4)")
        if as_of_ms < b + S.SETTLE_MS:
            raise Refused(f"--as-of-ms is before B + {S.SETTLE_MS // 3_600_000} h: no pending or preview output; run at or after B + 24 h")
    try:
        again = F.compute(ledger_root, bot, clock, "screen", health_texts, health_shas, restarts_text, as_of_ms)
    except F.Refused as ex:
        raise Refused(f"recompute refused: {ex}") from None
    if again["status"] != F.ST_READY or again["read_instant_ms"] != b:
        raise Refused(f"the instant file's B {b} does not follow from these inputs (recomputed: {again['status']} {again['read_instant_ms']})")
    block = {"read_instant_ms": b, "instant_file_sha256": isha, "rule": "DEC-031 Am.15 item 1", "pin_change": "arena_score.blind_read_instant replaced in-process by B",
             **pins}
    with am15_boundary(clock, b, block) as seen:
        code, lines, res = R.run(ledger_root, registry, bot, list(health_texts), as_of_ms, restarts_text, list(health_shas), excl_out, record, reads_dir)
    block["unamended_boundary_ms"] = seen.get("unamended_boundary_ms")
    if res is not None and bot in res.get("bots", {}):
        res["bots"][bot]["am15"] = block
    elif res is not None:
        res["am15"] = block
    verdict = []
    st = (res or {}).get("bots", {}).get(bot, {})
    if st.get("status") == "FAIL" and is_count_only(st):
        verdict = ["FAIL: count-only, not a loss. Final (Am.15 item 2): no waiting to a later boundary, no second read"]
    out = verdict + [head, f"read instant B {F.utc(b)} ({b}), recomputed equal; unamended attempt boundary (a record, not the read instant): {block['unamended_boundary_ms']}",
           "pins in use: " + json.dumps(pins, sort_keys=True), "PIN CHANGE (DEC-031 Am.15 item 4): arena_score.blind_read_instant returns B in this process; files unchanged"]
    return code, out + lines, res


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--instant", required=True, help="hv4_read_instant's write-once screen record")
    ap.add_argument("--bot", required=True, choices=F.BOT_IDS)
    ap.add_argument("--ledger-root", default=S.LEDGER_ROOT)
    ap.add_argument("--registry", default=S.REGISTRY_PATH)
    ap.add_argument("--health", required=True, action="append")
    ap.add_argument("--health-sha", required=True, action="append")
    ap.add_argument("--restarts", required=True)
    ap.add_argument("--as-of-ms", required=True, type=int)
    ap.add_argument("--record", action="store_true")
    ap.add_argument("--reads-dir", default=S.READS_DIR)
    ap.add_argument("--excl-out")
    ap.add_argument("--json")
    a = ap.parse_args(argv)
    try:
        code, lines, res = run(a.instant, a.ledger_root, a.registry, a.bot, [Path(h).read_text() for h in a.health], a.health_sha, a.as_of_ms,
                               Path(a.restarts).read_text(), a.excl_out, a.record, a.reads_dir)
    except Exception as ex:  # noqa: BLE001 - any failure is a refusal (exit 2), as in arena_am6_read
        print(f"REFUSED: {ex}", file=sys.stderr)
        return 2
    print("\n".join(lines))
    if a.json and res is not None:
        with open(a.json, "w") as fh:
            json.dump(res, fh, indent=1, sort_keys=True, allow_nan=False)
    return code


if __name__ == "__main__":
    sys.exit(main())
