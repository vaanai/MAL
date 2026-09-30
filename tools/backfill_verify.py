#!/usr/bin/env python3
"""Read-only integrity check for a pump_history_backfill.py walker directory.

    python3 -m tools.backfill_verify --dir <walker dir> --from <hour> --to <hour>

Default mode reads only file metadata (existence, name, whether it is
sealed to .zst) and the small checkpoint.json / stats-*.json control
files. It never opens a trades/creates/migrations data file, so it is
safe to run against a directory a live walker is still writing to.

Per hour it reports the checkpoint status, whether all three output
files (trades, creates, migrations) are present, whether the resolved
slot range is backwards or an implausible size, and whether slots_done
exceeds the resolved slot span -- the signature of the duplicate-row
resume bug this tool exists to catch.

--content additionally streams each hour's files (zstdcat for sealed
ones) and counts rows vs unique lines. Memory use scales with the
number of distinct lines in the largest file being checked, since exact
dedup detection needs to remember every line seen so far.

--dedupe-out <dir> writes a deduplicated copy of every file in range
(first occurrence wins, input order preserved) under <dir>, zstd
compressed, plus a sha256 manifest and this same JSON report. It reads
row contents to do this but never prints them -- only counts, paths,
and hashes go to stdout or the manifest.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

SUBS = ("trades", "creates", "migrations")


def parse_hour(text: str) -> datetime:
    return datetime.strptime(text.strip(), "%Y-%m-%dT%H").replace(tzinfo=timezone.utc)


def hour_key(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H")


def hour_range(from_hour: str, to_hour: str) -> list[str]:
    """Hour keys from `from_hour` (inclusive) to `to_hour` (exclusive)."""
    start = parse_hour(from_hour)
    end = parse_hour(to_hour)
    if end < start:
        raise ValueError(f"--to {to_hour} is before --from {from_hour}")
    out: list[str] = []
    cur = start
    while cur < end:
        out.append(hour_key(cur))
        cur += timedelta(hours=1)
    return out


def load_checkpoint_hours(walker_dir: Path) -> dict[str, Any]:
    path = walker_dir / "checkpoint.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    hours = data.get("hours") if isinstance(data, dict) else None
    return hours if isinstance(hours, dict) else {}


def sub_path(walker_dir: Path, sub: str, hour: str) -> Path | None:
    """The sealed .zst if present, else the still-open plain .jsonl."""
    zst = walker_dir / sub / f"{sub}-{hour}.jsonl.zst"
    if zst.is_file():
        return zst
    plain = walker_dir / sub / f"{sub}-{hour}.jsonl"
    if plain.is_file():
        return plain
    return None


def hour_metadata(
    walker_dir: Path,
    hour: str,
    checkpoint_hours: dict[str, Any],
    *,
    min_slots_per_hour: int,
    max_slots_per_hour: int,
) -> dict[str, Any]:
    stats_path = walker_dir / f"stats-{hour}.json"
    files = {sub: sub_path(walker_dir, sub, hour) for sub in SUBS}
    entry = checkpoint_hours.get(hour) if isinstance(checkpoint_hours.get(hour), dict) else None
    if stats_path.is_file():
        status = "sealed"
    elif entry is not None:
        status = str(entry.get("status") or "unknown")
    else:
        status = "unknown"

    report: dict[str, Any] = {
        "hour": hour,
        "checkpoint_status": status,
        "stats_present": stats_path.is_file(),
        "files": {sub: (str(p) if p else None) for sub, p in files.items()},
        "sealed": {sub: bool(p and p.name.endswith(".zst")) for sub, p in files.items()},
        "issues": [],
    }

    start_slot = end_slot = slots_done = None
    if stats_path.is_file():
        try:
            stats = json.loads(stats_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            stats = {}
        start_slot = stats.get("start_slot")
        end_slot = stats.get("end_slot")
        slots_done = stats.get("slots_done")
    elif entry is not None:
        start_slot = entry.get("start_slot")
        end_slot = entry.get("end_slot")
        slots_done = (entry.get("counts") or {}).get("slots_done")

    report["start_slot"] = start_slot
    report["end_slot"] = end_slot
    report["slots_done"] = slots_done

    span = None
    if isinstance(start_slot, int) and isinstance(end_slot, int):
        span = end_slot - start_slot
        report["slot_span"] = span
        if start_slot >= end_slot:
            report["issues"].append("backwards_slot_range")
        elif not (min_slots_per_hour <= span <= max_slots_per_hour):
            report["issues"].append("implausible_slot_span")

    if isinstance(slots_done, int) and isinstance(span, int) and span > 0 and slots_done > span:
        report["issues"].append("resumed: duplicate risk")

    if status == "sealed" and not files["trades"]:
        # A genuinely quiet hour can legitimately have zero trades --
        # JsonlSink removes empty output by design. Note it, don't fail it.
        report["issues"].append("sealed_with_no_trades_file")

    if status == "partial" and any(p is not None and p.name.endswith(".zst") for p in files.values()):
        # Exactly the state that used to trigger a full, duplicating
        # reprocess: sealed .zst on disk, checkpoint still says partial.
        report["issues"].append("sealed_files_alongside_partial_checkpoint")

    return report


def verify_metadata(
    walker_dir: Path,
    hours: list[str],
    *,
    min_slots_per_hour: int,
    max_slots_per_hour: int,
) -> dict[str, Any]:
    checkpoint_hours = load_checkpoint_hours(walker_dir)
    per_hour = [
        hour_metadata(
            walker_dir,
            hour,
            checkpoint_hours,
            min_slots_per_hour=min_slots_per_hour,
            max_slots_per_hour=max_slots_per_hour,
        )
        for hour in hours
    ]
    flagged = [h for h in per_hour if h["issues"]]
    return {
        "dir": str(walker_dir),
        "from": hours[0] if hours else None,
        "to_exclusive": hour_key(parse_hour(hours[-1]) + timedelta(hours=1)) if hours else None,
        "hours_checked": len(hours),
        "hours_flagged": len(flagged),
        "hours": per_hour,
    }


def _stream_lines(path: Path) -> Iterator[str]:
    if path.suffix == ".zst":
        proc = subprocess.Popen(["zstdcat", str(path)], stdout=subprocess.PIPE, text=True)
        assert proc.stdout is not None
        try:
            for line in proc.stdout:
                if line.strip():
                    yield line
        finally:
            proc.stdout.close()
            proc.wait()
        return
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                yield line


def count_rows_and_unique(path: Path) -> tuple[int, int]:
    """Row count and unique-line count. Only counts leave this function."""
    rows = 0
    seen: set[str] = set()
    for line in _stream_lines(path):
        rows += 1
        seen.add(line)
    return rows, len(seen)


def verify_content(report: dict[str, Any]) -> dict[str, Any]:
    for hour_report in report["hours"]:
        content: dict[str, Any] = {}
        for sub in SUBS:
            raw = hour_report["files"].get(sub)
            if not raw:
                continue
            rows, unique = count_rows_and_unique(Path(raw))
            content[sub] = {"rows": rows, "unique": unique, "duplicates": rows - unique}
            if rows != unique:
                hour_report["issues"].append(f"{sub}: {rows - unique} duplicate rows")
        hour_report["content"] = content
    report["hours_flagged"] = len([h for h in report["hours"] if h["issues"]])
    return report


def dedupe_file(src: Path, dest_dir: Path) -> dict[str, Any]:
    """First occurrence wins, order preserved. Output is zstd, with a hash."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    name = src.name
    if name.endswith(".jsonl.zst"):
        stem = name[: -len(".jsonl.zst")]
    elif name.endswith(".jsonl"):
        stem = name[: -len(".jsonl")]
    else:
        stem = src.stem
    tmp_path = dest_dir / f"{stem}.deduped.jsonl"
    rows = 0
    kept = 0
    seen: set[str] = set()
    with tmp_path.open("w", encoding="utf-8") as out:
        for line in _stream_lines(src):
            rows += 1
            if line in seen:
                continue
            seen.add(line)
            kept += 1
            out.write(line if line.endswith("\n") else line + "\n")
    subprocess.run(["zstd", "-q", "-3", "-T1", "--rm", "-f", str(tmp_path)], check=True)
    sealed = tmp_path.with_name(tmp_path.name + ".zst")
    digest = hashlib.sha256(sealed.read_bytes()).hexdigest()
    return {
        "source": str(src),
        "dest": str(sealed),
        "rows_in": rows,
        "rows_out": kept,
        "duplicates_removed": rows - kept,
        "sha256": digest,
    }


def run_dedupe(report: dict[str, Any], dedupe_out: Path) -> dict[str, Any]:
    manifest: list[dict[str, Any]] = []
    for hour_report in report["hours"]:
        for sub in SUBS:
            raw = hour_report["files"].get(sub)
            if not raw:
                continue
            result = dedupe_file(Path(raw), dedupe_out / sub)
            result["hour"] = hour_report["hour"]
            result["sub"] = sub
            manifest.append(result)
    dedupe_out.mkdir(parents=True, exist_ok=True)
    manifest_path = dedupe_out / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return {"manifest": str(manifest_path), "entries": manifest}


def build_report(
    walker_dir: Path,
    from_hour: str,
    to_hour: str,
    *,
    content: bool,
    dedupe_out: Path | None,
    min_slots_per_hour: int,
    max_slots_per_hour: int,
) -> dict[str, Any]:
    hours = hour_range(from_hour, to_hour)
    report = verify_metadata(
        walker_dir,
        hours,
        min_slots_per_hour=min_slots_per_hour,
        max_slots_per_hour=max_slots_per_hour,
    )
    if content or dedupe_out is not None:
        report = verify_content(report)
    if dedupe_out is not None:
        report["dedupe"] = run_dedupe(report, dedupe_out)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dir", required=True, type=Path, help="Walker output directory")
    parser.add_argument("--from", dest="from_hour", required=True, metavar="YYYY-MM-DDTHH", help="Inclusive")
    parser.add_argument("--to", dest="to_hour", required=True, metavar="YYYY-MM-DDTHH", help="Exclusive")
    parser.add_argument("--content", action="store_true", help="Stream files, count rows vs unique lines")
    parser.add_argument("--dedupe-out", type=Path, default=None, help="Write deduplicated copies here")
    parser.add_argument("--min-slots-per-hour", type=int, default=9_000)
    parser.add_argument("--max-slots-per-hour", type=int, default=14_000)
    args = parser.parse_args(argv)

    report = build_report(
        args.dir,
        args.from_hour,
        args.to_hour,
        content=args.content,
        dedupe_out=args.dedupe_out,
        min_slots_per_hour=args.min_slots_per_hour,
        max_slots_per_hour=args.max_slots_per_hour,
    )
    print(json.dumps(report, indent=2))
    return 1 if report["hours_flagged"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
