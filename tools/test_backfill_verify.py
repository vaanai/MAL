"""Synthetic-fixture tests for tools/backfill_verify.py. No network, no real walker dir."""

from __future__ import annotations

import io
import json
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from tools.backfill_verify import (
    build_report,
    count_rows_and_unique,
    hour_range,
    main,
)


def _write_jsonl_zst(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix("")
    tmp.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    subprocess.run(["zstd", "-q", "-1", "-T1", "--rm", "-f", str(tmp)], check=True)
    # zstd names the output <tmp>.zst; make sure it lands at `path`.
    produced = tmp.with_name(tmp.name + ".zst")
    if produced != path:
        produced.replace(path)


def _write_checkpoint(walker_dir: Path, hours: dict) -> None:
    (walker_dir / "checkpoint.json").write_text(
        json.dumps({"version": 1, "credits_used": 0, "credits_per_getblock": None, "hours": hours}),
        encoding="utf-8",
    )


def _write_stats(walker_dir: Path, hour: str, **fields) -> None:
    base = {"hour": hour, "start_slot": 0, "end_slot": 13500, "slots_done": 13500, "stop_reason": None}
    base.update(fields)
    (walker_dir / f"stats-{hour}.json").write_text(json.dumps(base), encoding="utf-8")


class HourRangeTests(unittest.TestCase):
    def test_inclusive_from_exclusive_to(self) -> None:
        hours = hour_range("2026-09-19T01", "2026-09-19T04")
        self.assertEqual(hours, ["2026-09-19T01", "2026-09-19T02", "2026-09-19T03"])

    def test_rejects_backwards_range(self) -> None:
        with self.assertRaises(ValueError):
            hour_range("2026-09-19T04", "2026-09-19T01")


class MetadataVerifyTests(unittest.TestCase):
    def test_healthy_hour_is_not_flagged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            walker = Path(tmp)
            hour = "2026-09-19T02"
            for sub in ("trades", "creates", "migrations"):
                _write_jsonl_zst(walker / sub / f"{sub}-{hour}.jsonl.zst", ['{"a":1}', '{"a":2}'])
            _write_stats(walker, hour, start_slot=100, end_slot=13600, slots_done=13500)
            _write_checkpoint(walker, {hour: {"status": "sealed", "stop_reason": None}})
            report = build_report(
                walker, hour, "2026-09-19T03",
                content=False, dedupe_out=None,
                min_slots_per_hour=9000, max_slots_per_hour=14000,
            )
            self.assertEqual(report["hours_flagged"], 0)
            self.assertEqual(report["hours"][0]["checkpoint_status"], "sealed")
            self.assertTrue(all(report["hours"][0]["sealed"].values()))

    def test_resumed_duplicate_risk_is_flagged(self) -> None:
        # Mirrors the real trades-2026-09-19T16 hour: start/end span ~13.5k
        # slots but slots_done is ~1.77x that -- the signature of the fixed
        # duplicate-row bug.
        with tempfile.TemporaryDirectory() as tmp:
            walker = Path(tmp)
            hour = "2026-09-19T16"
            _write_stats(walker, hour, start_slot=448455254, end_slot=448468721, slots_done=23892)
            (walker / "trades").mkdir()
            (walker / "trades" / f"trades-{hour}.jsonl.zst").write_bytes(b"\x28\xb5\x2f\xfd")
            _write_checkpoint(walker, {hour: {"status": "sealed", "stop_reason": None}})
            report = build_report(
                walker, hour, "2026-09-19T17",
                content=False, dedupe_out=None,
                min_slots_per_hour=9000, max_slots_per_hour=14000,
            )
            self.assertEqual(report["hours_flagged"], 1)
            self.assertIn("resumed: duplicate risk", report["hours"][0]["issues"])

    def test_backwards_range_is_flagged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            walker = Path(tmp)
            hour = "2026-09-11T03"
            _write_checkpoint(
                walker,
                {
                    hour: {
                        "status": "sealed",
                        "start_slot": 446300168,
                        "end_slot": 446060631,
                        "counts": {"slots_done": 1},
                    }
                },
            )
            report = build_report(
                walker, hour, "2026-09-11T04",
                content=False, dedupe_out=None,
                min_slots_per_hour=9000, max_slots_per_hour=14000,
            )
            self.assertIn("backwards_slot_range", report["hours"][0]["issues"])

    def test_sealed_files_alongside_partial_checkpoint_is_flagged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            walker = Path(tmp)
            hour = "2026-09-19T20"
            (walker / "trades").mkdir()
            (walker / "trades" / f"trades-{hour}.jsonl.zst").write_bytes(b"\x28\xb5\x2f\xfd")
            _write_checkpoint(
                walker,
                {
                    hour: {
                        "status": "partial",
                        "start_slot": 0,
                        "end_slot": 13500,
                        "counts": {"slots_done": 4000},
                    }
                },
            )
            report = build_report(
                walker, hour, "2026-09-19T21",
                content=False, dedupe_out=None,
                min_slots_per_hour=9000, max_slots_per_hour=14000,
            )
            self.assertIn(
                "sealed_files_alongside_partial_checkpoint", report["hours"][0]["issues"]
            )

    def test_missing_hour_is_unknown_not_flagged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            walker = Path(tmp)
            report = build_report(
                walker, "2026-09-19T01", "2026-09-19T02",
                content=False, dedupe_out=None,
                min_slots_per_hour=9000, max_slots_per_hour=14000,
            )
            self.assertEqual(report["hours"][0]["checkpoint_status"], "unknown")
            self.assertEqual(report["hours_flagged"], 0)


class ContentVerifyTests(unittest.TestCase):
    def test_counts_rows_and_unique_lines(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "trades" / "trades-2026-09-19T21.jsonl.zst"
            _write_jsonl_zst(path, ['{"a":1}', '{"a":1}', '{"a":2}'])
            rows, unique = count_rows_and_unique(path)
            self.assertEqual(rows, 3)
            self.assertEqual(unique, 2)

    def test_content_mode_flags_duplicate_rows_and_never_prints_them(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            walker = Path(tmp)
            hour = "2026-09-19T21"
            secret_marker = "unique-row-marker-should-not-appear-in-stdout"
            _write_jsonl_zst(
                walker / "trades" / f"trades-{hour}.jsonl.zst",
                [f'{{"sig":"{secret_marker}-1"}}', f'{{"sig":"{secret_marker}-1"}}', f'{{"sig":"{secret_marker}-2"}}'],
            )
            _write_checkpoint(walker, {hour: {"status": "sealed"}})
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = main(
                    [
                        "--dir", str(walker),
                        "--from", hour,
                        "--to", "2026-09-19T22",
                        "--content",
                    ]
                )
            out = buf.getvalue()
            self.assertEqual(code, 1)
            self.assertNotIn(secret_marker, out)
            report = json.loads(out)
            self.assertEqual(report["hours"][0]["content"]["trades"]["rows"], 3)
            self.assertEqual(report["hours"][0]["content"]["trades"]["unique"], 2)
            self.assertIn("trades: 1 duplicate rows", report["hours"][0]["issues"])


class DedupeOutTests(unittest.TestCase):
    def test_dedupe_writes_manifest_and_sha256_and_never_prints_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            walker = Path(tmp) / "walker"
            hour = "2026-09-19T22"
            secret_marker = "another-secret-row-value"
            _write_jsonl_zst(
                walker / "trades" / f"trades-{hour}.jsonl.zst",
                [f'{{"sig":"{secret_marker}-1"}}', f'{{"sig":"{secret_marker}-1"}}', f'{{"sig":"{secret_marker}-2"}}'],
            )
            _write_checkpoint(walker, {hour: {"status": "sealed"}})
            dedupe_out = Path(tmp) / "deduped"
            buf = io.StringIO()
            with redirect_stdout(buf):
                main(
                    [
                        "--dir", str(walker),
                        "--from", hour,
                        "--to", "2026-09-19T23",
                        "--dedupe-out", str(dedupe_out),
                    ]
                )
            out = buf.getvalue()
            self.assertNotIn(secret_marker, out)

            manifest_path = dedupe_out / "manifest.json"
            self.assertTrue(manifest_path.is_file())
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(len(manifest), 1)
            entry = manifest[0]
            self.assertEqual(entry["rows_in"], 3)
            self.assertEqual(entry["rows_out"], 2)
            self.assertEqual(entry["duplicates_removed"], 1)
            self.assertTrue(Path(entry["dest"]).is_file())
            self.assertEqual(len(entry["sha256"]), 64)

            # The manifest itself never carries row contents, only counts.
            self.assertNotIn(secret_marker, manifest_path.read_text(encoding="utf-8"))

            deduped_rows, deduped_unique = count_rows_and_unique(Path(entry["dest"]))
            self.assertEqual(deduped_rows, 2)
            self.assertEqual(deduped_unique, 2)


if __name__ == "__main__":
    unittest.main()
