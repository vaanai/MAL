#!/usr/bin/env python3
"""EXP-009 Amendment 5 -- compute k from the in-sample feature distribution only.

Follows EXP/EXP-009-migrate-creator-gate-prereg.md Sections 3-5 literally:

  - G1 feature (Sec 3): prior_mint_count_24h(creator, T) = the count of that
    creator's bonding-create events with create time in the half-open
    trailing window [T - 24h, T), W = 86,400 seconds.
  - Eligibility (Sec 4): a migrate row counts toward k's in-sample median
    only if both hold:
      (a) unknown-creator exclusion -- the migrating mint's own create event
          is present in the data read here (creator known);
      (b) burn-in -- every UTC hour touching [T-24h, T] has sealed,
          non-partial create coverage on whichever host supplies it.
  - k procedure (Sec 5): k = median(prior_mint_count_24h) over Sec-4-eligible
    migrate rows in the in-sample window
    2026-09-22T10:00:00Z .. 2026-09-25T06:58:00Z (ties round down: a
    fractional median rounds to the next lower integer). If that in-sample
    median is 0, k is fixed at 1 instead ("first repeat within 24h"),
    recorded as a substitution, not re-tuned.

This script computes ONLY the feature distribution -- no fill, no exit, no
fail model, no PnL of any kind. It is not the later scoring PR that Sec 5's
last line reserves for a separate change.

Data sources (hard fence):
  - Oracle in-sample copy (creates/ and migrations/ under ORACLE_DIR), hours
    2026-09-22T00 through 2026-09-25T06 inclusive -- a sha256-verified copy
    of Oracle's own sealed backfill for 2026-09-22T00 -> 2026-09-25T07,
    verified here against its own MANIFEST.sha256 (verify_oracle_manifest).
  - Fast-box burn-in creates, hours 2026-09-21T10 through 2026-09-21T23 ONLY
    (ALLOWED_FAST_HOURS) -- needed because the earliest in-sample T's 24h
    trailing window reaches back to 2026-09-21T10:00:00Z. These are
    exploration-pool hours (docs/HOLDOUT_LEDGER.md "Fast pre-cut" block,
    "was the frozen migrate-direct OOS book, now closed"), already read once
    for exactly this purpose per EXP-009 Sec 4(a); this script does not read
    any fast hour outside that fixed 14-hour whitelist, and never reads any
    data at or after 2026-09-28 (HOLDOUT_FLOOR_S).

Known deviation from Sec 2's trigger definition, flagged as the one open
ambiguity in this run (see the report and the "Amendment 5" note this
script's output is copied into): a "migrate row" here is one on-chain
migration/curve-completion event per mint, taken from the migrations/ sink
(event type "complete" or "migration", whichever is earliest per mint), not
the frozen cell's own execution-time trigger -- "first PumpSwap print after
a bonding print on that mint" (Sec 2), which is defined on the trades/ tape
via the frozen cell's fillable-print state machine
(tools/latency_curve.py::_Mint). Reproducing that trigger exactly would
require streaming the full trades/ tape for this window (~12 GB, tens of
millions of rows) through outcome-adjacent machinery this PR does not
exercise, for a feature-distribution-only computation that must not touch
PnL or outcome at all. The two definitions are expected to coincide for the
overwhelming majority of mints -- an on-chain migration essentially always
starts PumpSwap trading in the same window -- but this has not been proven
identical row-for-row. The later scoring PR (Sec 5, 7) must use the exact
Sec 2 trigger and should confirm the two populations agree, or record the
difference.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

W_SECONDS = 24 * 3600

IN_SAMPLE_START = "2026-09-22T10:00:00Z"
IN_SAMPLE_END = "2026-09-25T06:58:00Z"  # inclusive upper bound on T, per the frozen cell's own selection window

ORACLE_DIR = Path("/home/claude/data/oracle-insample-2026-09-22_25")
FAST_CREATES_DIR = Path("/var/lib/mal/backfill-fast/creates")
FAST_CHECKPOINT = Path("/var/lib/mal/backfill-fast/checkpoint.json")

# Hard fence: the only fast-box hours this script may ever open.
ALLOWED_FAST_HOURS: tuple[str, ...] = tuple(f"2026-09-21T{h:02d}" for h in range(10, 24))

# Oracle hours this script reads (creates) -- exactly what the burn-in window needs,
# nothing from hour 07 onward (no T reaches into that hour).
ORACLE_CREATE_HOURS: tuple[str, ...] = tuple(
    f"2026-09-22T{h:02d}" for h in range(0, 24)
) + tuple(f"2026-09-23T{h:02d}" for h in range(0, 24)) + tuple(
    f"2026-09-24T{h:02d}" for h in range(0, 24)
) + tuple(f"2026-09-25T{h:02d}" for h in range(0, 7))

# Oracle hours this script reads (migrations) -- the in-sample window only.
ORACLE_MIGRATION_HOURS: tuple[str, ...] = tuple(
    f"2026-09-22T{h:02d}" for h in range(10, 24)
) + tuple(f"2026-09-23T{h:02d}" for h in range(0, 24)) + tuple(
    f"2026-09-24T{h:02d}" for h in range(0, 24)
) + tuple(f"2026-09-25T{h:02d}" for h in range(0, 7))

HOLDOUT_FLOOR = "2026-09-28T00:00:00Z"  # never read any data at or after this instant


def _parse_ts(text: str) -> int:
    return int(datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp())


def _hour_key(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H")


def _hour_start_ts(hour: str) -> int:
    return _parse_ts(hour + ":00:00Z")


def _hours_touching(start_s: int, end_s: int) -> list[str]:
    """Every UTC hour key whose interval overlaps [start_s, end_s], inclusive of end_s's own hour."""
    first = _hour_key(start_s)
    last = _hour_key(end_s)
    out = [first]
    cur = _hour_start_ts(first)
    last_start = _hour_start_ts(last)
    while cur < last_start:
        cur += 3600
        out.append(_hour_key(cur))
    return out


def _assert_never_holdout(hour: str) -> None:
    floor_s = _parse_ts(HOLDOUT_FLOOR)
    if _hour_start_ts(hour) >= floor_s:
        raise AssertionError(f"refusing to read hour {hour}: at or after holdout floor {HOLDOUT_FLOOR}")


def _open_lines(path: Path) -> Iterator[str]:
    if path.name.endswith(".zst"):
        proc = subprocess.Popen(["zstd", "-dc", "-q", str(path)], stdout=subprocess.PIPE)
        assert proc.stdout is not None
        try:
            for raw in proc.stdout:
                yield raw.decode("utf-8", "replace")
        finally:
            proc.stdout.close()
            proc.wait()
        return
    with path.open("r", encoding="utf-8") as fh:
        yield from fh


def _iter_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    for line in _open_lines(path):
        line = line.strip()
        if not line:
            continue
        yield json.loads(line)


def _fast_create_path(hour: str) -> Path:
    if hour not in ALLOWED_FAST_HOURS:
        raise AssertionError(f"refusing to read fast hour {hour}: outside the burn-in whitelist {ALLOWED_FAST_HOURS}")
    _assert_never_holdout(hour)
    for suffix in (".jsonl.zst", ".jsonl"):
        path = FAST_CREATES_DIR / f"creates-{hour}{suffix}"
        if path.is_file():
            return path
    raise FileNotFoundError(f"no fast creates file for {hour}")


def _oracle_create_path(hour: str) -> Path:
    if hour not in ORACLE_CREATE_HOURS:
        raise AssertionError(f"refusing to read oracle creates hour {hour}: outside the needed range")
    _assert_never_holdout(hour)
    path = ORACLE_DIR / "creates" / f"creates-{hour}.jsonl.zst"
    if not path.is_file():
        raise FileNotFoundError(f"no oracle creates file for {hour}")
    return path


def _oracle_migration_path(hour: str) -> Path:
    if hour not in ORACLE_MIGRATION_HOURS:
        raise AssertionError(f"refusing to read oracle migrations hour {hour}: outside the in-sample window")
    _assert_never_holdout(hour)
    path = ORACLE_DIR / "migrations" / f"migrations-{hour}.jsonl.zst"
    if not path.is_file():
        raise FileNotFoundError(f"no oracle migrations file for {hour}")
    return path


def load_fast_checkpoint() -> dict[str, Any]:
    return json.loads(FAST_CHECKPOINT.read_text(encoding="utf-8"))


def fast_hour_sealed(hour: str, checkpoint: dict[str, Any]) -> bool:
    entry = checkpoint.get("hours", {}).get(hour)
    return isinstance(entry, dict) and entry.get("status") == "sealed"


def verify_oracle_manifest(dir_path: Path = ORACLE_DIR) -> tuple[int, int]:
    """Checks every creates/migrations file this script reads against MANIFEST.sha256.

    Returns (checked, ok). Raises if any checked file's hash does not match.
    """
    manifest_path = dir_path / "MANIFEST.sha256"
    digests: dict[str, str] = {}
    for line in manifest_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        digest, _, rel = line.partition("  ")
        digests[rel.strip()] = digest.strip()
    checked = 0
    ok = 0
    needed_rel = [f"creates/creates-{h}.jsonl.zst" for h in ORACLE_CREATE_HOURS]
    needed_rel += [f"migrations/migrations-{h}.jsonl.zst" for h in ORACLE_MIGRATION_HOURS]
    for rel in needed_rel:
        expected = digests.get(rel)
        if expected is None:
            raise AssertionError(f"{rel} not present in MANIFEST.sha256")
        path = dir_path / rel
        h = hashlib.sha256()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        checked += 1
        if h.hexdigest() == expected:
            ok += 1
        else:
            raise AssertionError(f"sha256 mismatch for {rel}")
    return checked, ok


def load_creates(fast_checkpoint: dict[str, Any]) -> tuple[dict[str, str], dict[str, list[int]], dict[str, bool]]:
    """Returns (creator_by_mint, sorted create event_ts per creator, hour -> sealed)."""
    creator_by_mint: dict[str, str] = {}
    creates_by_creator: dict[str, list[int]] = {}
    sealed: dict[str, bool] = {}

    for hour in ALLOWED_FAST_HOURS:
        sealed[hour] = fast_hour_sealed(hour, fast_checkpoint)
        if not sealed[hour]:
            continue
        path = _fast_create_path(hour)
        for row in _iter_jsonl(path):
            _index_create_row(row, creator_by_mint, creates_by_creator)

    for hour in ORACLE_CREATE_HOURS:
        # The oracle in-sample copy is sha256-verified as a whole (see
        # verify_oracle_manifest); every hour in ORACLE_CREATE_HOURS is
        # present in that verified copy, so it counts as sealed here.
        sealed[hour] = True
        path = _oracle_create_path(hour)
        for row in _iter_jsonl(path):
            _index_create_row(row, creator_by_mint, creates_by_creator)

    for ts_list in creates_by_creator.values():
        ts_list.sort()
    return creator_by_mint, creates_by_creator, sealed


def _index_create_row(
    row: dict[str, Any], creator_by_mint: dict[str, str], creates_by_creator: dict[str, list[int]]
) -> None:
    if row.get("type") != "create":
        return
    mint = row.get("mint")
    creator = row.get("creator")
    ts = row.get("event_ts")
    if not isinstance(mint, str) or not isinstance(creator, str) or not isinstance(ts, int):
        return
    creator_by_mint.setdefault(mint, creator)
    creates_by_creator.setdefault(creator, []).append(ts)


def load_migrate_rows() -> dict[str, int]:
    """mint -> earliest in-sample migration/completion event_ts (T), within the window."""
    start_s = _parse_ts(IN_SAMPLE_START)
    end_s = _parse_ts(IN_SAMPLE_END)
    rows: dict[str, int] = {}
    for hour in ORACLE_MIGRATION_HOURS:
        path = _oracle_migration_path(hour)
        for row in _iter_jsonl(path):
            if row.get("type") not in ("migration", "complete"):
                continue
            mint = row.get("mint")
            ts = row.get("block_time", row.get("event_ts"))
            if not isinstance(mint, str) or not isinstance(ts, int):
                continue
            if not (start_s <= ts <= end_s):
                continue
            prev = rows.get(mint)
            if prev is None or ts < prev:
                rows[mint] = ts
    return rows


def prior_mint_count_24h(creator: str, t_s: int, creates_by_creator: dict[str, list[int]]) -> int:
    """Count of creator's creates with event_ts in the half-open window [t_s - W, t_s)."""
    ts_list = creates_by_creator.get(creator)
    if not ts_list:
        return 0
    lo = t_s - W_SECONDS
    count = 0
    for ts in ts_list:
        if lo <= ts < t_s:
            count += 1
        elif ts >= t_s:
            break
    return count


def burn_in_ok(t_s: int, sealed: dict[str, bool]) -> bool:
    for hour in _hours_touching(t_s - W_SECONDS, t_s):
        if not sealed.get(hour):
            return False
    return True


def median_round_down(values: list[int]) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    if n % 2 == 1:
        return ordered[mid]
    return math.floor((ordered[mid - 1] + ordered[mid]) / 2.0)


def quantile(sorted_values: list[int], p: float) -> float:
    if not sorted_values:
        return 0.0
    idx = int(round(p * (len(sorted_values) - 1)))
    return float(sorted_values[idx])


def compute_k(
    migrate_rows: dict[str, int],
    creator_by_mint: dict[str, str],
    creates_by_creator: dict[str, list[int]],
    sealed: dict[str, bool],
) -> dict[str, Any]:
    total = len(migrate_rows)
    excluded_unknown_creator = 0
    excluded_burn_in = 0
    eligible_counts: list[int] = []

    for mint, t_s in migrate_rows.items():
        creator = creator_by_mint.get(mint)
        if creator is None:
            excluded_unknown_creator += 1
            continue
        if not burn_in_ok(t_s, sealed):
            excluded_burn_in += 1
            continue
        eligible_counts.append(prior_mint_count_24h(creator, t_s, creates_by_creator))

    median_val = median_round_down(eligible_counts)
    substituted = median_val == 0 and len(eligible_counts) > 0
    k = 1 if substituted else median_val

    ordered = sorted(eligible_counts)
    share_zero = (sum(1 for c in ordered if c == 0) / len(ordered)) if ordered else None
    quantiles = {
        f"p{int(p * 100)}": quantile(ordered, p) for p in (0.05, 0.10, 0.25, 0.50, 0.75, 0.90, 0.95, 0.99)
    }

    return {
        "total_migrate_rows": total,
        "excluded_unknown_creator": excluded_unknown_creator,
        "excluded_burn_in": excluded_burn_in,
        "eligible_n": len(eligible_counts),
        "median": median_val,
        "k": k,
        "k_substituted": substituted,
        "share_at_zero": share_zero,
        "max": ordered[-1] if ordered else None,
        "quantiles": quantiles,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-manifest-check", action="store_true", help="skip the sha256 verify (debug only)")
    args = parser.parse_args(argv)

    if not args.skip_manifest_check:
        checked, ok = verify_oracle_manifest()
        print(f"manifest: checked={checked} ok={ok}", file=sys.stderr)
        assert checked == ok

    checkpoint = load_fast_checkpoint()
    creator_by_mint, creates_by_creator, sealed = load_creates(checkpoint)
    migrate_rows = load_migrate_rows()
    report = compute_k(migrate_rows, creator_by_mint, creates_by_creator, sealed)
    report["in_sample_start"] = IN_SAMPLE_START
    report["in_sample_end"] = IN_SAMPLE_END
    report["fast_burn_in_hours"] = list(ALLOWED_FAST_HOURS)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
