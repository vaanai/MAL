"""r4bt_d_measure: the ONE measurement of d (the whole-slot feed delay) that DEC-031 Amendment 27 item 24 pins, as a pinned tool (QP-RULING-1114-r4b-preview-steps section 2).
PAPER ONLY. Reads only the 60 s sampler's jsonl files named by --sample-file (read-only); no tape, declog, tip file, `out/` or sealed directory. NOT to be run on real inputs by
a builder; the manager runs it once on the fixed window.

THE RULE, quoted (DEC/DEC-031-forward-paper-arena.md line 1602, Amendment 27 item 24):
    "**d** = ceil(p90 of the follower's `block_lag_ms_p90` samples / (slot_s x 1000)), from at least 24 h of the 60 s sampler before the registration, with slot_s from the same
    window's (t, `last_done`) pairs; pinned once in the registration record with its date."
THE FORMULA (QP ruling): slot_s = (t_last - t_first) / (slot_last - slot_first) over the first and last KEPT samples; d =
    tools.arena_tip_adapter.feed_delay_slots(samples, slot_s, ingest_lag_ms=0.0)["d"]  (sps is SECONDS per slot; the ruling's 1/slot_s was a units slip; see the PR body)
with samples = [{"block_lag_ms_p90": s_block_lag_ms_p90}, ...] (the adapter returns a dict; its key "d" is the whole-slot value). d has one implementation in the lab: the adapter's.

ONLY SAMPLER FILES. Each --sample-file must sit directly under STAGED_DIR (the staged sampler files of research-0, the directory tools/r4bt_trainserve_counts.py names for the same
staged inputs) and be named tip-health-<tag>.jsonl; with --allow-test-path (refused inside a MiScusi job, as in r4bt_trainserve_counts) a file under the temp dir is allowed instead.
Every other path, and every closed root or mark (r4bt_gate.check_path plus the declog, preview work dir, price keeper, derived tip copy, arena mirror and offload paths), is refused
before any open. A file whose first complete line carries none of the three sampler keys is refused. A refusal prints NO number derived from file content. Inputs are opened with
O_NOFOLLOW on the resolved path and must be regular files.

KEPT SAMPLES. Only `t`, `s_block_lag_ms_p90`, `s_last_done_slot` of a line are read (the key names are the sampler's, tools/r4bt_sample_keys.py), with from <= t < to. `t` must be an
integer (or a float with an integral value) of epoch seconds. A line in the window with a missing, boolean, non-finite or non-numeric field (or a line that is not a JSON object) is
dropped and counted. Samples are de-duplicated by `t` across all files, as the other sampler readers do: identical copies count once (`n_duplicate_t` counts the extra copies); copies
whose kept values differ REFUSE. The result does not depend on the order of the files.
REFUSALS (exit 2; a message names the rule, never a number from the file): fewer than 1,296 kept samples (90 % of 1,440); any gap between consecutive kept t above 1,800 s; the first
kept t more than 1,800 s after `from`, or the last more than 1,800 s before `to` (the ruling's max-gap rule applied to the window's edges); `s_last_done_slot` not non-decreasing
in t; fewer than 100 samples (the adapter's floor); a closed or non-allowed path; an existing --out file (O_EXCL).
WINDOW: fixed [2026-11-15T00:00Z, 2026-11-16T00:00Z). Fallback, if and only if this tool refuses for coverage on it: [2026-11-16T00:00Z, 2026-11-17T00:00Z). One measurement, never repeated.
The tool does not check for a dirty tree (no r4bt tool does outside the pricer's frozen-code check); the manager runs it from a clean checkout and records `git status --porcelain`.

    python -m tools.r4bt_d_measure --sample-file /data2/mal/tip-health/fast-inputs/tip-health-A.jsonl [--sample-file ...] --from 2026-11-15T00:00:00Z --to 2026-11-16T00:00:00Z --out d-measure.json
"""
from __future__ import annotations

if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import argparse
import hashlib
import json
import math
import os
import re
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Sequence

from tools import arena_tip_adapter as ADAPTER
from tools import r4bt_gate as G
from tools import r4bt_sample_keys as K

REPO = Path(__file__).resolve().parent.parent
KEY_T = "t"
KEY_LAG = "s_block_lag_ms_p90"
KEY_SLOT = "s_last_done_slot"
USED_KEYS = (KEY_T, KEY_LAG, KEY_SLOT)
MIN_KEPT = 1296  # 90 % of 1,440 one-minute samples
MAX_GAP_S = 1800
MIN_SAMPLES = 100  # the adapter's floor
STAGED_DIR = "/data2/mal/tip-health/fast-inputs"  # the staged sampler files (scripts/ops/tip-hour-inputs-stage.sh DEST); r4bt_trainserve_counts.ALLOWED_COUNTS is in the same dir
NAME_RE = re.compile(r"^tip-health-[A-Za-z0-9_.-]+\.jsonl$")
CLOSED_MORE = ("/data2/mal/r4b-preview", "/data2/mal/scratch/r4b-price-keep", "/data2/mal/arena-tip-feed", "/data2/mal/fast0-offload-111x")
CLOSED_MARKS_MORE = ("/r4b-preview", "/r4b-price-keep", "/arena-tip-feed", "/fast0-offload-", "/mal-data/r4b-dry", "/mal-data/arena-tipfeed", "/mal-data/arena-mirror", "declog")


def _check_keys() -> None:
    for k in USED_KEYS:
        if k not in K.REAL_KEYS:
            raise G.Refused("a key this tool reads is not in the sampler's key set (tools/r4bt_sample_keys.py)")


def _num(x: Any) -> bool:
    if not isinstance(x, (int, float)) or isinstance(x, bool):
        return False
    try:
        return math.isfinite(x)
    except OverflowError:
        return False


def _is_t(x: Any) -> bool:
    return _num(x) and (isinstance(x, int) or float(x).is_integer())


def check_closed(path: str | Path, what: str) -> Path:
    """r4bt_gate.check_path with the further closed roots and marks (declog, preview work dir, price keeper, derived tip copy, arena mirror, offload)."""
    p = G.check_path(path, what, G.CLOSED_ROOTS + CLOSED_MORE)
    s = str(p)
    for m in CLOSED_MARKS_MORE:
        if m in s:
            raise G.Refused(f"{what} is under a closed path mark: refused")
    return p


def check_sample_path(path: str | Path, test_mode: bool = False) -> Path:
    """The path of a sampler file: not closed, named tip-health-<tag>.jsonl, directly under STAGED_DIR (or, in test mode, under the temp dir). Checked before any open."""
    p = check_closed(path, "sample file")
    if not NAME_RE.match(p.name):
        raise G.Refused("sample file name is not tip-health-<tag>.jsonl: refused")
    ok = str(p.parent) == STAGED_DIR
    if not ok and test_mode:
        if os.environ.get("MISCUSI_JOB_ID"):
            raise G.Refused("--allow-test-path refuses to run inside a MiScusi job")
        tmp = Path(tempfile.gettempdir()).resolve()
        ok = p.parent == tmp or tmp in p.parents
    if not ok:
        raise G.Refused("sample file is not directly under the staged sampler directory: refused")
    return p


def read_regular(p: Path) -> bytes:
    fd = os.open(str(p), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as f:
        if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
            raise G.Refused("sample file is not a regular file: refused")
        return f.read()


def check_first_line(data: bytes) -> None:
    """Refuse a file whose first complete line carries none of the three sampler keys. No count is printed."""
    nl = data.find(b"\n")
    if nl < 0:
        raise G.Refused("sample file has no complete line: refused")
    try:
        d = json.loads(data[:nl].decode("utf-8", "replace"))
    except ValueError:
        d = None
    if not isinstance(d, dict) or not any(k in d for k in USED_KEYS):
        raise G.Refused("the first line of a sample file carries none of the sampler keys: refused")


def _git(*args: str) -> str:
    r = subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, text=True)
    if r.returncode != 0 or not r.stdout.strip():
        raise G.Refused("git could not give the tool blob or commit")
    return r.stdout.strip()


def parse_samples(data: bytes, from_s: float, to_s: float) -> tuple[list[tuple[int, float, float]], int]:
    """(kept [(t, lag_ms, slot)] with from_s <= t < to_s, n_dropped) for the bytes of one file. Only the three keys are read."""
    kept: list[tuple[int, float, float]] = []
    dropped = 0
    for raw in data.decode("utf-8", "replace").splitlines():
        if not raw.strip():
            continue
        try:
            d = json.loads(raw)
        except ValueError:
            dropped += 1
            continue
        if not isinstance(d, dict) or not _is_t(d.get(KEY_T)):
            dropped += 1
            continue
        t = d[KEY_T]
        if not (from_s <= t < to_s):
            continue
        if not _num(d.get(KEY_LAG)) or not _num(d.get(KEY_SLOT)):
            dropped += 1
            continue
        kept.append((int(t), float(d[KEY_LAG]), float(d[KEY_SLOT])))
    return kept, dropped


def measure(files: Sequence[tuple[str, bytes]], from_text: str, to_text: str) -> dict[str, Any]:
    """The output record for the given (name, bytes) inputs, or raises Refused."""
    _check_keys()
    f_ms, t_ms = G.utc_ms(from_text), G.utc_ms(to_text)
    if f_ms >= t_ms:
        raise G.Refused("--from is not before --to")
    by_t: dict[int, tuple[float, float]] = {}
    dropped = dup = 0
    inputs = []
    for name, data in files:
        k, dr = parse_samples(data, f_ms / 1000.0, t_ms / 1000.0)
        dropped += dr
        for t, lag, slot in k:
            if t in by_t:
                if by_t[t] != (lag, slot):
                    raise G.Refused("two samples with the same t carry different values: refused")
                dup += 1
            else:
                by_t[t] = (lag, slot)
        inputs.append({"name": name, "sha256": hashlib.sha256(data).hexdigest()})
    kept = [(t, v[0], v[1]) for t, v in sorted(by_t.items())]
    n = len(kept)
    if n < MIN_SAMPLES:
        raise G.Refused("coverage: fewer than the adapter's floor of samples")
    if n < MIN_KEPT:
        raise G.Refused(f"coverage: fewer than {MIN_KEPT} kept samples")
    max_gap = max(b[0] - a[0] for a, b in zip(kept, kept[1:]))
    if max_gap > MAX_GAP_S:
        raise G.Refused(f"coverage: a gap between consecutive kept samples exceeds {MAX_GAP_S} s")
    if kept[0][0] - f_ms / 1000.0 > MAX_GAP_S or t_ms / 1000.0 - kept[-1][0] > MAX_GAP_S:
        raise G.Refused(f"coverage: the first or last kept sample is more than {MAX_GAP_S} s from the window edge")
    if any(b[2] < a[2] for a, b in zip(kept, kept[1:])):
        raise G.Refused("s_last_done_slot is not non-decreasing in t")
    dslot = kept[-1][2] - kept[0][2]
    if dslot <= 0:
        raise G.Refused("s_last_done_slot did not advance between the first and last kept samples")
    slot_s = (kept[-1][0] - kept[0][0]) / dslot
    try:
        res = ADAPTER.feed_delay_slots([{"block_lag_ms_p90": r[1]} for r in kept], slot_s, ingest_lag_ms=0.0)
    except ValueError:
        raise G.Refused("the adapter refused (floor or slot_s range)") from None
    me = Path(__file__).resolve()
    return {"window": {"from": from_text, "to": to_text}, "inputs": inputs, "n_kept": n, "n_dropped": dropped, "n_duplicate_t": dup, "max_gap_s": max_gap,
            "p50_ms": res["p50_ms"], "p90_ms": res["p90_ms"], "slot_s": slot_s, "d": res["d"],
            "tool_blob": _git("hash-object", str(me)), "adapter_blob": _git("hash-object", str(Path(ADAPTER.__file__).resolve())), "commit": _git("rev-parse", "HEAD")}


def write_once(path: Path, text: str) -> None:
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    with os.fdopen(fd, "w") as f:
        f.write(text)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="r4bt_d_measure", description=__doc__.splitlines()[0])
    ap.add_argument("--sample-file", action="append", required=True)
    ap.add_argument("--from", dest="from_", required=True)
    ap.add_argument("--to", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--allow-test-path", action="store_true")
    a = ap.parse_args(argv)
    try:
        paths = [check_sample_path(p, a.allow_test_path) for p in a.sample_file]
        out = check_closed(a.out, "the out file")
        if out.exists():
            raise G.Refused("the out file exists: refused (written once)")
        blobs = [(p.name, read_regular(p)) for p in paths]
        for _, data in blobs:
            check_first_line(data)
        rec = measure(blobs, a.from_, a.to)
        text = json.dumps(rec, indent=2, sort_keys=True) + "\n"
        write_once(out, text)
    except G.Refused as e:
        print(f"r4bt_d_measure: REFUSED: {e}", file=sys.stderr)
        return 2
    except OSError as e:
        print(f"r4bt_d_measure: REFUSED: {type(e).__name__}", file=sys.stderr)
        return 2
    print(text, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
