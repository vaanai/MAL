#!/usr/bin/env python3
"""EXP-012: the single, one-shot scorer for the fresh block [2026-09-03T12, 2026-09-09T12).

Pre-registration: EXP/EXP-012-migrate-entry-model-refreeze-prereg.md (section 4).
Ledger: docs/HOLDOUT_LEDGER.md. The scoring itself is EXP-011's, imported, not
re-implemented (`tools.exp011_score`: score_rows, compute_gate, build_report,
fill_conditional_stats, per_day_table, load_frozen_spec). What is new here is
the three-walker layout, the deduplicated-copy read, and a stricter pre-read
gate. Built and tested against synthetic fixtures only; nothing in this module
or its tests opens the real walker directories.

Three walkers, one contiguous 144 hour block (the default ranges; the union
of the three ranges must tile the block exactly or the scorer refuses):
  w1 [2026-09-07T12, 2026-09-09T12)   w2 [2026-09-05T12, 2026-09-07T12)   w3 [2026-09-03T12, 2026-09-05T12)

Two directories per walker: the RAW walker dir (--wN-dir; only its small
control files checkpoint.json / stats-*.json are read, plus file names and
sizes) and the DEDUPLICATED copy (--wN-clean-dir; `tools.backfill_verify
--dedupe-out` output: manifest.json plus <sub>/<sub>-<hour>.deduped.jsonl.zst).
Trade and create rows are read only from the deduplicated copy.

Refusals, all BEFORE the lock (exit 2, each listed on stderr; `--dry-run-preconditions`
runs exactly this and stops). Byte hashing reveals no outcome, so it is a
pre-lock refusal and a mismatch does not spend the block:
  - the three ranges do not tile the 144 hour block;
  - the frozen artifacts: FROZEN.md5 must list features.json, model.txt,
    threshold.json, proceed_screen.json, nested_fixed_threshold_lodo.json,
    train_manifest.json and table_row_counts.json, each matching its md5;
    --frozen-manifest-md5 must equal FROZEN.md5's md5; proceed_screen.json
    must say proceed == true AND equal proceed_screen() recomputed here from
    nested_fixed_threshold_lodo.json; train_manifest.json must record
    code_commit == --freeze-commit and code_dirty == false; the table build
    must record the pinned VIEW.sha256 hashes; features == FROZEN_FEATURE_NAMES;
  - tracked-and-clean state (vs HEAD) of the pin, FROZEN.md5 and the scorer/freeze
    code, and no change under tools/ or schemas/ between --freeze-commit and HEAD;
  - any hour not `sealed`, a metadata slot issue, a missing or unsealed file, or
    raw creates presence differing from the manifest's;
  - a missing dedupe manifest, entry, file or pin, or a pin that differs;
  - any deduplicated trades/creates file whose bytes differ from the manifest;
  - out-dir not writable, zstdcat missing, the model failing a smoke predict,
    or a report / NOT_DECIDABLE.json already in out-dir;
  - the read-once lock already exists.

Then the O_EXCL lock is written. After the lock only the read and scoring
happen (a defense-in-depth re-hash first; a mismatch there is NOT_DECIDABLE,
exit 3). Any failure after the lock writes NOT_DECIDABLE.json and exits 3.
The verdict and report JSON go to stderr BEFORE any file is written.
Settings are fixed (table settings 24/12/2, lock path), with no CLI override.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import multiprocessing as mp
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

import tools.exp011_score as e11
from tools.exp012_support import proceed_screen
from tools.backfill_verify import verify_metadata
from tools.exp011_freeze import (
    FROZEN_FEATURE_NAMES,
    FROZEN_MANIFEST_NAME,
    FROZEN_MANIFEST_REQUIRED,
    VIEW_PIN_BY_POOL,
    _git_commit,
    _md5_of_file,
    parse_md5_manifest,
)
from tools.exploration_entry_model import _rows_out_path, iter_rows_jsonl, run_worker_features
from tools.exploration_exits import chunk_plan
from tools.latency_curve import _iter_trades

SCHEMA_REPORT = "exp012_holdout_report_v1"
SCHEMA_LOCK = "exp012_holdout_read_lock_v1"

BLOCK_START = "2026-09-03T12"
BLOCK_END = "2026-09-09T12"  # exclusive
BLOCK_HOURS = e11._hours_range(BLOCK_START, BLOCK_END)
BLOCK_HOURS_SET = frozenset(BLOCK_HOURS)
assert len(BLOCK_HOURS) == 144, len(BLOCK_HOURS)

DEFAULT_RANGES: dict[str, tuple[str, str]] = {
    "w1": ("2026-09-07T12", "2026-09-09T12"),
    "w2": ("2026-09-05T12", "2026-09-07T12"),
    "w3": ("2026-09-03T12", "2026-09-05T12"),
}
# Fixed settings: no CLI override (tests patch these module constants).
LOCK_PATH = Path("/data/mal/exp012/HOLDOUT_READ.lock")
BUFFER_HOURS = 24
MAX_HOME_HOURS = 12
MAX_WORKERS = 2
DEFAULT_OUT_DIR = Path("/data/mal/exp012/read")
REPO_ROOT = Path(__file__).resolve().parents[1]
GUARDED_CODE = ("tools/exp012_score.py", "tools/exp012_support.py", "tools/exp011_score.py", "tools/exp011_freeze.py")
SCREEN_REQUIRED = ("proceed_screen.json", "nested_fixed_threshold_lodo.json", "train_manifest.json", "table_row_counts.json")

# `resumed: duplicate risk` is NOT a refusal: the dedupe step removes the exact
# duplicates and the counts are disclosed. These are.
REFUSING_ISSUES = (
    "backwards_slot_range",
    "implausible_slot_span",
    "sealed_with_no_trades_file",
    "sealed_files_alongside_partial_checkpoint",
)
READ_SUBS = ("trades", "creates")  # the only files the scorer opens
ALL_SUBS = ("trades", "creates", "migrations")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class Walker:
    label: str
    raw_dir: Path
    clean_dir: Path
    start: str
    end: str  # exclusive

    @property
    def hours(self) -> list[str]:
        return e11._hours_range(self.start, self.end)

    def clean_file(self, sub: str, hour: str) -> Path:
        return self.clean_dir / sub / f"{sub}-{hour}.deduped.jsonl.zst"


def build_walkers(raw: dict[str, str | Path], clean: dict[str, str | Path], ranges: dict[str, tuple[str, str]]) -> list[Walker]:
    return [Walker(k, Path(raw[k]), Path(clean[k]), ranges[k][0], ranges[k][1]) for k in sorted(ranges)]


def check_tiling(walkers: Sequence[Walker]) -> list[str]:
    seen: list[str] = []
    errors: list[str] = []
    for w in walkers:
        try:
            seen.extend(w.hours)
        except ValueError as exc:
            errors.append(f"{w.label}: bad range: {exc}")
    if errors:
        return errors
    if sorted(seen) != BLOCK_HOURS:
        dup = len(seen) - len(set(seen))
        errors.append(
            f"walker ranges do not tile the block [{BLOCK_START}, {BLOCK_END}) exactly: {len(seen)} hours given ({dup} overlapping), {len(set(seen) ^ BLOCK_HOURS_SET)} hours differ from the 144-hour block"
        )
    return errors


# --- frozen artifacts ---------------------------------------------------------


def check_frozen(artifact_dir: Path, manifest_md5: str | None, freeze_commit: str | None = None) -> list[str]:
    errors: list[str] = []
    mpath = artifact_dir / FROZEN_MANIFEST_NAME
    if not mpath.is_file():
        return [f"missing {mpath} (the frozen-artifact md5 manifest committed in Part 2)"]
    try:
        listed = parse_md5_manifest(mpath)
    except ValueError as exc:
        return [f"{mpath} unreadable: {exc}"]
    if manifest_md5 is not None and _md5_of_file(mpath) != manifest_md5.strip().lower():
        errors.append(f"{mpath} md5 {_md5_of_file(mpath)} differs from --frozen-manifest-md5 {manifest_md5}")
    for name in FROZEN_MANIFEST_REQUIRED + SCREEN_REQUIRED:
        if name not in listed:
            errors.append(f"{mpath} does not list required artifact {name}")
    for name, want in sorted(listed.items()):
        path = artifact_dir / name
        if not path.is_file():
            errors.append(f"frozen artifact missing: {path}")
        elif _md5_of_file(path) != want:
            errors.append(f"frozen artifact md5 mismatch: {path} hashes to {_md5_of_file(path)}, {FROZEN_MANIFEST_NAME} says {want}")
    if not errors:
        errors.extend(_check_screen_and_provenance(artifact_dir, freeze_commit))
    model, md5file = artifact_dir / "model.txt", artifact_dir / "model.md5"
    if model.is_file():
        if not md5file.is_file():
            errors.append(f"missing {md5file}")
        elif _md5_of_file(model) != md5file.read_text(encoding="utf-8").strip():
            errors.append(f"model md5 mismatch: {model} vs {md5file}")
    features = artifact_dir / "features.json"
    if features.is_file():
        try:
            names = json.loads(features.read_text(encoding="utf-8")).get("frozen_feature_names")
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"{features} unreadable: {exc}")
        else:
            if names != FROZEN_FEATURE_NAMES:
                errors.append(f"{features}'s frozen_feature_names does not match tools.exp011_freeze.FROZEN_FEATURE_NAMES")
    return errors


def _check_screen_and_provenance(artifact_dir: Path, freeze_commit: str | None) -> list[str]:
    errors: list[str] = []
    try:
        nested = json.loads((artifact_dir / "nested_fixed_threshold_lodo.json").read_text(encoding="utf-8"))
        screen = json.loads((artifact_dir / "proceed_screen.json").read_text(encoding="utf-8"))
        train = json.loads((artifact_dir / "train_manifest.json").read_text(encoding="utf-8"))
        counts = json.loads((artifact_dir / "table_row_counts.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"frozen artifact unreadable: {exc}"]
    try:
        recomputed = proceed_screen(nested)
    except Exception as exc:  # noqa: BLE001
        return [f"proceed screen cannot be recomputed from nested_fixed_threshold_lodo.json: {type(exc).__name__}: {exc}"]
    if screen != recomputed:
        errors.append("proceed_screen.json differs from proceed_screen() recomputed from nested_fixed_threshold_lodo.json")
    if screen.get("proceed") is not True or recomputed.get("proceed") is not True:
        errors.append("EXP-012 proceed condition not met (proceed_screen.json proceed != true): EXP-012 is withdrawn, the block is released unread")
    if not freeze_commit or not re.match(r"^[0-9a-f]{40}$", freeze_commit):
        errors.append("--freeze-commit must be the 40-hex commit the freeze ran at")
    elif train.get("code_commit") != freeze_commit or train.get("code_dirty") is not False:
        errors.append(f"train_manifest.json code_commit/code_dirty ({train.get('code_commit')!r}, {train.get('code_dirty')!r}) != --freeze-commit {freeze_commit} clean")
    got_settings = (counts.get("max_workers"), counts.get("buffer_hours"), counts.get("max_home_hours"))
    if got_settings != (2, 24, 12):
        errors.append(f"table_row_counts.json shows (max_workers, buffer_hours, max_home_hours) = {got_settings}, the pre-registration fixes (2, 24, 12)")
    if (counts.get("manifest") or {}).get("verify_view") is not True:
        errors.append("table_row_counts.json does not record verify_view: true (the table must be built with --verify-view)")
    views = (counts.get("manifest") or {}).get("view_sha256_file_sha256")
    if views != VIEW_PIN_BY_POOL:
        errors.append("table_row_counts.json does not record the pinned VIEW.sha256 hashes for pools A/C/B (EXP-012 section 3.2)")
    return errors


def _git(repo: Path, *args: str) -> tuple[int, str]:
    r = subprocess.run(["git", *args], cwd=str(repo), capture_output=True, text=True)
    return r.returncode, r.stdout.strip()


def check_repo_state(paths: Sequence[Path], freeze_commit: str | None, repo_root: Path = REPO_ROOT) -> list[str]:
    """Each path tracked and unmodified vs HEAD; tools/ and schemas/ identical
    between the freeze commit and HEAD. Metadata only."""
    errors: list[str] = []
    for p in paths:
        try:
            rel = os.path.relpath(Path(p).resolve(), repo_root.resolve())
        except ValueError:
            rel = ".."
        if rel.startswith(".."):
            errors.append(f"{p} is outside the repository {repo_root}")
            continue
        if _git(repo_root, "ls-files", "--error-unmatch", "--", rel)[0] != 0:
            errors.append(f"{rel} is not tracked by the repository")
        elif _git(repo_root, "diff", "--quiet", "HEAD", "--", rel)[0] != 0:
            errors.append(f"{rel} differs from HEAD (uncommitted change)")
    rc, out = _git(repo_root, "status", "--porcelain", "--untracked-files=all", "--", "tools", "schemas", "ARTIFACTS/exp012")
    if rc != 0 or out:
        errors.append(f"working tree is dirty under tools/, schemas/ or ARTIFACTS/exp012 (modified or untracked files, e.g. {out.splitlines()[0] if out else 'git status failed'})")
    if freeze_commit:
        rc, _ = _git(repo_root, "diff", "--quiet", freeze_commit, "HEAD", "--", "tools", "schemas")
        if rc != 0:
            errors.append(f"tools/ or schemas/ changed between the freeze commit {freeze_commit} and HEAD (or the commit is unknown)")
    return errors


# --- raw walker metadata (no data file is opened) ------------------------------


def check_walker_raw(w: Walker) -> list[str]:
    errors = e11._check_walker_checkpoint(w.raw_dir, w.hours, f"walker {w.label}")
    if errors:
        return errors
    report = verify_metadata(w.raw_dir, w.hours, min_slots_per_hour=9_000, max_slots_per_hour=14_000)
    for h in report["hours"]:
        bad = [i for i in h["issues"] if i in REFUSING_ISSUES]
        unsealed = [sub for sub, p in h["files"].items() if p is not None and not h["sealed"].get(sub)]
        if bad:
            errors.append(f"walker {w.label} hour {h['hour']}: {', '.join(bad)}")
        if unsealed:
            errors.append(f"walker {w.label} hour {h['hour']}: not sealed to .zst: {', '.join(unsealed)}")
        if h["files"].get("trades") is None and "sealed_with_no_trades_file" not in bad:
            errors.append(f"walker {w.label} hour {h['hour']}: no trades file")
    return errors


# --- dedupe manifests + pin ------------------------------------------------------


def load_dedupe_manifest(w: Walker) -> tuple[list[dict[str, Any]] | None, str | None]:
    path = w.clean_dir / "manifest.json"
    if not path.is_file():
        return None, f"walker {w.label}: no dedupe manifest at {path}"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"walker {w.label}: dedupe manifest unreadable ({path}): {exc}"
    if not isinstance(data, list) or not all(isinstance(e, dict) for e in data):
        return None, f"walker {w.label}: dedupe manifest is not a list of entries ({path})"
    return data, None


def expected_pin(walkers: Sequence[Walker]) -> tuple[dict[str, str], list[str]]:
    """{pin path: sha256} computed from each manifest (and the manifest file's own
    sha256), plus coverage errors. Opens manifest.json files only."""
    pin: dict[str, str] = {}
    errors: list[str] = []
    for w in walkers:
        entries, err = load_dedupe_manifest(w)
        if err:
            errors.append(err)
            continue
        assert entries is not None
        pin[f"{w.label}/manifest.json"] = hashlib.sha256((w.clean_dir / "manifest.json").read_bytes()).hexdigest()
        by_key = {(e.get("hour"), e.get("sub")): e for e in entries}
        for hour in w.hours:
            if (hour, "trades") not in by_key:
                errors.append(f"walker {w.label}: dedupe manifest has no trades entry for hour {hour}")
        for (hour, sub), e in sorted(by_key.items(), key=lambda kv: (str(kv[0][0]), str(kv[0][1]))):
            if hour not in w.hours:
                errors.append(f"walker {w.label}: dedupe manifest has an entry for hour {hour}, outside its range")
                continue
            if sub not in ALL_SUBS:
                errors.append(f"walker {w.label}: dedupe manifest has unknown sub {sub!r}")
                continue
            expected_name = f"{sub}-{hour}.deduped.jsonl.zst"
            if os.path.basename(str(e.get("dest", ""))) != expected_name:
                errors.append(f"walker {w.label}: manifest dest for {sub} {hour} is not {expected_name}")
                continue
            sha = e.get("sha256")
            if not isinstance(sha, str) or not _HEX64.match(sha):
                errors.append(f"walker {w.label}: manifest sha256 for {sub} {hour} is not a sha256")
                continue
            if not w.clean_file(sub, hour).is_file():
                errors.append(f"walker {w.label}: deduplicated file missing: {w.clean_file(sub, hour)}")
            pin[f"{w.label}/{sub}/{expected_name}"] = sha
    return pin, errors


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


def check_dedupe_bytes(walkers: Sequence[Walker]) -> list[str]:
    """Stream-hash every deduplicated trades and creates file against its manifest
    sha256. Bytes only, no row is parsed, so it reveals no outcome: a mismatch
    refuses before the lock and does not spend the block."""
    problems: list[str] = []
    for w in walkers:
        entries, _ = load_dedupe_manifest(w)
        for e in entries or []:
            if e.get("sub") not in READ_SUBS:
                continue
            path = w.clean_file(str(e["sub"]), str(e["hour"]))
            if _sha256_file(path) != e["sha256"]:
                problems.append(f"sha256 mismatch: {path} differs from the dedupe manifest")
    return problems


def check_creates_presence(walkers: Sequence[Walker]) -> list[str]:
    """Raw creates-file presence must equal the manifest's creates presence, per hour."""
    errors: list[str] = []
    for w in walkers:
        entries, _ = load_dedupe_manifest(w)
        in_manifest = {e.get("hour") for e in entries or [] if e.get("sub") == "creates"}
        report = verify_metadata(w.raw_dir, w.hours, min_slots_per_hour=9_000, max_slots_per_hour=14_000)
        for h in report["hours"]:
            if (h["files"].get("creates") is not None) != (h["hour"] in in_manifest):
                errors.append(f"walker {w.label} hour {h['hour']}: raw creates presence differs from the dedupe manifest")
    return errors


def check_environment(artifact_dir: Path, out_dir: Path) -> list[str]:
    errors: list[str] = []
    if shutil.which("zstdcat") is None:
        errors.append("zstdcat is not on PATH")
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        probe = out_dir / ".write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        errors.append(f"out-dir {out_dir} is not writable: {exc}")
    for name in ("holdout_report.json", "NOT_DECIDABLE.json"):
        if (out_dir / name).exists():
            errors.append(f"{out_dir / name} already exists: this block has already been read or spent")
    try:
        import numpy as np

        model, _thr, names = e11.load_frozen_spec(artifact_dir)
        model.predict(np.zeros((1, len(names))), num_threads=1)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"frozen model smoke predict failed: {type(exc).__name__}: {exc}")
    return errors


def write_dedupe_pin(walkers: Sequence[Walker], path: Path) -> int:
    pin, errors = expected_pin(walkers)
    if not errors:
        errors = check_dedupe_bytes(walkers)
    if errors:
        raise SystemExit("cannot write the dedupe pin:\n  " + "\n  ".join(errors[:50]))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(f"{sha}  {name}\n" for name, sha in sorted(pin.items())), encoding="utf-8")
    return len(pin)


def read_pin(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        m = re.match(r"^([0-9a-f]{64})  (\S+)$", line)
        if m is None:
            raise ValueError(f"bad pin line: {line!r}")
        out[m.group(2)] = m.group(1)
    return out


def check_dedupe(walkers: Sequence[Walker], pin_path: Path) -> list[str]:
    pin_expected, errors = expected_pin(walkers)
    if not pin_path.is_file():
        return errors + [f"missing dedupe pin {pin_path} (write it with --write-dedupe-pin and merge it before the read)"]
    try:
        pin = read_pin(pin_path)
    except ValueError as exc:
        return errors + [f"{pin_path}: {exc}"]
    if not errors and pin != pin_expected:
        extra = sorted(set(pin) - set(pin_expected))
        missing = sorted(set(pin_expected) - set(pin))
        changed = sorted(k for k in set(pin) & set(pin_expected) if pin[k] != pin_expected[k])
        errors.append(
            f"dedupe pin {pin_path} does not match the manifests: {len(missing)} missing, {len(extra)} extra, {len(changed)} changed"
            + (f" (e.g. {(changed or missing or extra)[0]})" if (changed or missing or extra) else "")
        )
    return errors


def dedupe_counts(walkers: Sequence[Walker]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for w in walkers:
        entries, _ = load_dedupe_manifest(w)
        tot: dict[str, dict[str, int]] = {}
        for e in entries or []:
            t = tot.setdefault(str(e.get("sub")), {"rows_in": 0, "rows_out": 0, "duplicates_removed": 0, "hours_with_duplicates": 0})
            t["rows_in"] += int(e.get("rows_in") or 0)
            t["rows_out"] += int(e.get("rows_out") or 0)
            t["duplicates_removed"] += int(e.get("duplicates_removed") or 0)
            t["hours_with_duplicates"] += 1 if int(e.get("duplicates_removed") or 0) else 0
        out[w.label] = tot
    return out


# --- preconditions + lock ---------------------------------------------------------


def check_preconditions(
    artifact_dir: Path,
    walkers: Sequence[Walker],
    pin_path: Path,
    lock_path: Path,
    frozen_manifest_md5: str | None,
    freeze_commit: str | None,
    out_dir: Path,
) -> list[str]:
    """Every refusal reason, before the lock. Opens no data file until all the
    cheap checks pass; then stream-hashes the deduplicated files (bytes only)."""
    errors = check_tiling(walkers)
    errors.extend(check_frozen(artifact_dir, frozen_manifest_md5, freeze_commit))
    if not any(e.startswith("walker ranges") or "bad range" in e for e in errors):
        for w in walkers:
            errors.extend(check_walker_raw(w))
    errors.extend(check_dedupe(walkers, pin_path))
    if not errors:
        errors.extend(check_creates_presence(walkers))
    errors.extend(check_repo_state([artifact_dir / FROZEN_MANIFEST_NAME, pin_path] + [REPO_ROOT / g for g in GUARDED_CODE], freeze_commit))
    errors.extend(check_environment(artifact_dir, out_dir))
    if lock_path.exists():
        errors.append(f"holdout read lock already exists at {lock_path} -- this block has already been read (or is being read); refusing a second read")
    if not errors:
        errors.extend(check_dedupe_bytes(walkers))
    return errors


def write_lock(lock_path: Path, *, model_md5: str, frozen_manifest_md5: str, pin_sha256: str, command_line: str, freeze_commit: str = "") -> None:
    """O_CREAT | O_EXCL: a concurrent second caller gets FileExistsError."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    doc = {
        "schema": SCHEMA_LOCK,
        "utc_time": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "git_sha": _git_commit(),
        "model_md5": model_md5,
        "frozen_manifest_md5": frozen_manifest_md5,
        "dedupe_pin_sha256": pin_sha256,
        "freeze_commit": freeze_commit,
        "command_line": command_line,
    }
    fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(doc, indent=2) + "\n")


# --- post-lock: integrity, then the read ---------------------------------------------


def rehash_read_files(walkers: Sequence[Walker]) -> list[str]:
    """Defense in depth, first act after the lock: the same byte check as the
    pre-lock one, in case a file changed between the hash and the read."""
    return [p.replace("sha256 mismatch:", "sha256 mismatch after lock:") for p in check_dedupe_bytes(walkers)]


@dataclass(frozen=True)
class HoldoutHours:
    """Whitelist-checked, picklable hour resolver over the deduplicated copies."""

    clean_by_hour: dict[str, str]
    has_create: dict[str, bool]

    def __call__(self, key: str) -> dict[str, Any]:
        assert key in BLOCK_HOURS_SET, f"hour {key!r} is outside EXP-012's block [{BLOCK_START}, {BLOCK_END})"
        root = Path(self.clean_by_hour[key])
        trade = root / "trades" / f"trades-{key}.deduped.jsonl.zst"
        if not trade.is_file():
            raise SystemExit(f"missing deduplicated trades file for whitelisted hour {key} under {root}")
        create = root / "creates" / f"creates-{key}.deduped.jsonl.zst" if self.has_create.get(key) else None
        start_s = int(datetime.strptime(key, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc).timestamp())
        return {"hour": key, "day": key[:10], "end": start_s + 3600, "trade": trade, "create": create}


def make_hours(walkers: Sequence[Walker]) -> HoldoutHours:
    clean_by_hour: dict[str, str] = {}
    has_create: dict[str, bool] = {}
    for w in walkers:
        entries, _ = load_dedupe_manifest(w)
        created = {e.get("hour") for e in entries or [] if e.get("sub") == "creates"}
        for h in w.hours:
            clean_by_hour[h] = str(w.clean_dir)
            has_create[h] = h in created
    return HoldoutHours(clean_by_hour, has_create)


def build_creator_history(hours: Any, keys: Sequence[str] | None = None) -> dict[str, list[int]]:
    """`keys` defaults to the 144-hour block. tools/exp012_forward.py passes its own
    hour list and resolver; the body is unchanged."""
    hist: dict[str, list[int]] = {}
    for key in BLOCK_HOURS if keys is None else keys:
        path = hours(key).get("create")
        if path is None:
            continue
        for row in _iter_trades(path):
            if row.get("type") != "create":
                continue
            creator, block = row.get("creator"), row.get("block_time")
            if isinstance(creator, str) and isinstance(block, int):
                hist.setdefault(creator, []).append(block * 1000)
    for times in hist.values():
        times.sort()
    return hist


def _run_worker(worker_id: int, home: list[str], buf: list[str], creator_hist: dict[str, list[int]], rows_out_path: Path | None, hours: HoldoutHours) -> list[dict[str, Any]]:
    return run_worker_features(worker_id, home, buf, creator_hist, hour_info_fn=hours, rows_out_path=rows_out_path)


def load_rows(
    hours: Any,
    max_workers: int,
    buffer_hours: int,
    max_home_hours: int | None,
    out_dir: Path | None,
    *,
    pool_hours: Sequence[str] | None = None,
    worker_fn: Any = None,
    plan: Sequence[tuple[int, list[str], list[str]]] | None = None,
) -> list[dict[str, Any]]:
    """`pool_hours` (default: the 144-hour block) and `worker_fn` (default
    `_run_worker`) and `plan` (a precomputed chunk plan, default `chunk_plan(...)`) are keyword-only hooks for tools/exp012_forward.py, which feeds
    the same table build a different hour list. Defaults reproduce the read exactly."""
    pool_keys = BLOCK_HOURS if pool_hours is None else list(pool_hours)
    worker = _run_worker if worker_fn is None else worker_fn
    creator_hist = build_creator_history(hours, pool_keys)
    print(f"EXP-012 score: holdout creator_history creators={len(creator_hist)}", file=sys.stderr, flush=True)
    plan = list(plan) if plan is not None else chunk_plan(pool_keys, max_workers, buffer_hours, max_home_hours)
    paths = [_rows_out_path(out_dir, "HOLDOUT12", i) for i, _h, _b in plan]
    rows: list[dict[str, Any]] = []
    if max_workers <= 1 or len(plan) <= 1:
        for (i, h, b), p in zip(plan, paths):
            rows.extend(worker(i, h, b, creator_hist, p, hours))
    else:
        with mp.get_context("spawn").Pool(processes=min(max_workers, len(plan))) as pool:
            for part in pool.starmap(worker, [(i, h, b, creator_hist, p, hours) for (i, h, b), p in zip(plan, paths)]):
                rows.extend(part)
    if out_dir is not None:
        for p in paths:
            rows.extend(iter_rows_jsonl(p))
    return [r for r in rows if r["spec"] == e11.TARGET_SPEC_ID]


# --- report ---------------------------------------------------------------------------


def build_report(rows: Sequence[dict[str, Any]], entered: Sequence[dict[str, Any]], threshold: float, walkers: Sequence[Walker]) -> dict[str, Any]:
    report = e11.build_report(rows, entered, threshold)
    report.update(
        {
            "schema": SCHEMA_REPORT,
            "holdout_hours": {"start": BLOCK_START, "end": BLOCK_END, "n_hours": len(BLOCK_HOURS)},
            "source_note": "holdout is a single fast-box block walked by three walkers (w1/w2/w3) and read from their deduplicated copies; no cross-source split is possible, only within-block day variation",
            "dedupe_counts": dedupe_counts(walkers),
        }
    )
    return report


def render_markdown(report: dict[str, Any]) -> str:
    md = e11.render_markdown(report).replace("# EXP-011 holdout report", "# EXP-012 holdout report", 1)
    lines = ["", "## Dedupe counts (outcome-blind, from the manifests)"]
    for label, subs in sorted(report["dedupe_counts"].items()):
        for sub, t in sorted(subs.items()):
            lines.append(f"- {label} {sub}: rows_in={t['rows_in']} rows_out={t['rows_out']} removed={t['duplicates_removed']} hours_with_duplicates={t['hours_with_duplicates']}")
    return md + "\n".join(lines) + "\n"


def write_report(out_dir: Path, report: dict[str, Any]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "holdout_report.json").write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    (out_dir / "holdout_report.md").write_text(render_markdown(report), encoding="utf-8")


def write_not_decidable(out_dir: Path, reason: str) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    doc = {"verdict": "NOT_DECIDABLE", "reason": reason, "utc_time": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "metrics": None}
    (out_dir / "NOT_DECIDABLE.json").write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")


# --- CLI ----------------------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    for k in sorted(DEFAULT_RANGES):
        ap.add_argument(f"--{k}-dir", required=True, help=f"raw walker dir for {k} (control files only)")
        ap.add_argument(f"--{k}-clean-dir", required=True, help=f"deduplicated copy for {k} (backfill_verify --dedupe-out)")
        ap.add_argument(f"--{k}-range", nargs=2, metavar=("START", "END"), default=list(DEFAULT_RANGES[k]), help=f"{k} hours [START, END), YYYY-MM-DDTHH")
    ap.add_argument("--artifact-dir", required=True)
    ap.add_argument("--frozen-manifest-md5", default=None, help="md5 of FROZEN.md5 as recorded in the EXP-012 Part 2 amendment (required for a read)")
    ap.add_argument("--freeze-commit", default=None, help="40-hex commit the freeze ran at, from Part 2 (required for a read)")
    ap.add_argument("--dedupe-pin", required=True)
    ap.add_argument("--write-dedupe-pin", default=None, metavar="PATH", help="hash the bytes, check them against the manifests, write the pin and exit (no lock)")
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    ap.add_argument("--dry-run-preconditions", action="store_true", help="run every pre-lock check (including the byte hashing) and exit; never writes the lock")
    ap.add_argument("--result-out", default=None, help="also write a result.v1 record here (role confirmation-oneshot)")
    ap.add_argument("--tries-log", default=None, help="tries log for the result.v1 record (default: MAL_TRIES_LOG / data/tries.jsonl)")
    return ap


def main(argv: list[str] | None = None) -> int:
    ap = _parser()
    args = ap.parse_args(argv)
    if not args.write_dedupe_pin and (not args.frozen_manifest_md5 or not args.freeze_commit):
        ap.error("--frozen-manifest-md5 and --freeze-commit are required")
    raw = {k: getattr(args, f"{k}_dir") for k in DEFAULT_RANGES}
    clean = {k: getattr(args, f"{k}_clean_dir") for k in DEFAULT_RANGES}
    ranges = {k: tuple(getattr(args, f"{k}_range")) for k in DEFAULT_RANGES}
    walkers = build_walkers(raw, clean, ranges)  # type: ignore[arg-type]
    artifact_dir, out_dir, pin_path, lock_path = Path(args.artifact_dir), Path(args.out_dir), Path(args.dedupe_pin), LOCK_PATH

    if args.write_dedupe_pin:
        errs = check_tiling(walkers)
        if errs:
            print("\n".join(f"REFUSED: {e}" for e in errs), file=sys.stderr)
            return 2
        try:
            n = write_dedupe_pin(walkers, Path(args.write_dedupe_pin))
        except SystemExit as exc:
            print(f"REFUSED: {exc}", file=sys.stderr)
            return 2
        print(f"wrote {n} pin lines to {args.write_dedupe_pin}", file=sys.stderr)
        return 0

    errors = check_preconditions(artifact_dir, walkers, pin_path, lock_path, args.frozen_manifest_md5, args.freeze_commit, out_dir)
    for e in errors:
        print(f"REFUSED: {e}", file=sys.stderr)
    if errors:
        return 2
    if args.dry_run_preconditions:
        print("preconditions OK (dry run -- no lock written)", file=sys.stderr)
        return 0

    t0 = time.time()
    model, threshold, feature_names = e11.load_frozen_spec(artifact_dir)
    model_md5 = (artifact_dir / "model.md5").read_text(encoding="utf-8").strip()
    try:
        write_lock(
            lock_path,
            model_md5=model_md5,
            frozen_manifest_md5=_md5_of_file(artifact_dir / FROZEN_MANIFEST_NAME),
            pin_sha256=hashlib.sha256(pin_path.read_bytes()).hexdigest(),
            command_line=" ".join(sys.argv),
            freeze_commit=args.freeze_commit,
        )
    except FileExistsError:
        print(f"REFUSED: lock appeared at {lock_path} (concurrent run); refusing a second read", file=sys.stderr)
        return 2

    # --- after the lock: only the read and scoring; every failure is NOT_DECIDABLE ---
    try:
        problems = rehash_read_files(walkers)
        if problems:
            write_not_decidable(out_dir, "; ".join(problems[:20]))
            print("NOT_DECIDABLE: " + problems[0], file=sys.stderr)
            return 3
        rows = load_rows(make_hours(walkers), MAX_WORKERS, BUFFER_HOURS, MAX_HOME_HOURS, out_dir / "scratch")
        e11.score_rows(model, rows, feature_names)
        entered = [r for r in rows if r["score"] >= threshold]
        report = build_report(rows, entered, threshold, walkers)
    except (SystemExit, Exception) as exc:  # noqa: BLE001 -- the block is spent either way
        write_not_decidable(out_dir, f"{type(exc).__name__}: {exc}")
        print(f"NOT_DECIDABLE: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 3
    # The verdict and the report go to stderr FIRST: if a file write fails, the result is not lost.
    print(f"VERDICT: {report['verdict']}", file=sys.stderr, flush=True)
    print(json.dumps(report, indent=2, default=str), file=sys.stderr, flush=True)
    write_report(out_dir, report)
    print(f"EXP-012 score: wrote {out_dir}/holdout_report.{{md,json}}", file=sys.stderr, flush=True)
    if args.result_out:
        from tools.exp012_support import write_scorer_result

        try:
            write_scorer_result(Path(args.result_out), rows, entered, report, command=" ".join(sys.argv), runtime_s=time.time() - t0, tries_log=args.tries_log)
        except Exception as exc:  # noqa: BLE001 -- the verdict above stands
            print(f"WARNING: result.v1 not written: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 4
    return 0


if __name__ == "__main__":
    sys.exit(main())
