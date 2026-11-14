"""r4bt_d_measure: the ONE measurement of d (the whole-slot feed delay) that DEC-031 Amendment 27 item 24 pins, as a pinned tool (QP-RULING-1114-r4b-preview-steps section 2).
PAPER ONLY. Reads only the 60 s sampler's jsonl files named by --sample-file (read-only); no tape, declog, tip file, `out/` or sealed directory. NOT to be run on real inputs by
a builder; the manager runs it once on the fixed window.

THE RULE, quoted (DEC/DEC-031-forward-paper-arena.md line 1602, Amendment 27 item 24):
    "**d** = ceil(p90 of the follower's `block_lag_ms_p90` samples / (slot_s x 1000)), from at least 24 h of the 60 s sampler before the registration, with slot_s from the same
    window's (t, `last_done`) pairs; pinned once in the registration record with its date."
THE FORMULA (QP ruling): slot_s = (t_last - t_first) / (slot_last - slot_first) over the first and last KEPT samples; d =
    tools.arena_tip_adapter.feed_delay_slots(samples, slot_s, ingest_lag_ms=0.0)["d"]  (sps is SECONDS per slot; the ruling's 1/slot_s was a units slip; see the PR body)  with samples = [{"block_lag_ms_p90": s_block_lag_ms_p90}, ...]
(the adapter returns a dict; its key "d" is the whole-slot value). d has one implementation in the lab: the adapter's.

KEPT SAMPLES. Only `t`, `s_block_lag_ms_p90`, `s_last_done_slot` of a line are read (the key names are the sampler's, tools/r4bt_sample_keys.py), with from <= t < to. A line in
the window with a missing, boolean or non-numeric field (or a line that is not a JSON object) is dropped and counted.
REFUSALS (exit 2, message without values): fewer than 1,296 kept samples (90 % of 1,440); any gap between consecutive kept t above 1,800 s; `s_last_done_slot` not non-decreasing
in t; fewer than 100 samples (the adapter's floor); a path under a closed root (tools.r4bt_gate.check_path); an existing --out file (O_EXCL).
WINDOW: fixed [2026-11-15T00:00Z, 2026-11-16T00:00Z). Fallback, if and only if this tool refuses for coverage on it: [2026-11-16T00:00Z, 2026-11-17T00:00Z). One measurement, never repeated.

    python -m tools.r4bt_d_measure --sample-file tip-health-A.jsonl [--sample-file ...] --from 2026-11-15T00:00:00Z --to 2026-11-16T00:00:00Z --out d-measure.json
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
import subprocess
import sys
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


def _check_keys() -> None:
    for k in USED_KEYS:
        if k not in K.REAL_KEYS:
            raise G.Refused("a key this tool reads is not in the sampler's key set (tools/r4bt_sample_keys.py)")


def _num(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def _git(*args: str) -> str:
    r = subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, text=True)
    if r.returncode != 0 or not r.stdout.strip():
        raise G.Refused("git could not give the tool blob or commit")
    return r.stdout.strip()


def parse_samples(data: bytes, from_s: float, to_s: float) -> tuple[list[tuple[float, float, float]], int]:
    """(kept [(t, lag_ms, slot)] with from_s <= t < to_s, n_dropped) for the bytes of one file. Only the three keys are read."""
    kept: list[tuple[float, float, float]] = []
    dropped = 0
    for raw in data.decode("utf-8", "replace").splitlines():
        if not raw.strip():
            continue
        try:
            d = json.loads(raw)
        except ValueError:
            dropped += 1
            continue
        if not isinstance(d, dict) or not _num(d.get(KEY_T)):
            dropped += 1
            continue
        t = d[KEY_T]
        if not (from_s <= t < to_s):
            continue
        if not _num(d.get(KEY_LAG)) or not _num(d.get(KEY_SLOT)):
            dropped += 1
            continue
        kept.append((float(t), float(d[KEY_LAG]), float(d[KEY_SLOT])))
    return kept, dropped


def measure(files: Sequence[tuple[str, bytes]], from_text: str, to_text: str) -> dict[str, Any]:
    """The output record for the given (name, bytes) inputs, or raises Refused."""
    _check_keys()
    f_ms, t_ms = G.utc_ms(from_text), G.utc_ms(to_text)
    if f_ms >= t_ms:
        raise G.Refused("--from is not before --to")
    kept: list[tuple[float, float, float]] = []
    dropped = 0
    inputs = []
    for name, data in files:
        k, dr = parse_samples(data, f_ms / 1000.0, t_ms / 1000.0)
        kept += k
        dropped += dr
        inputs.append({"name": name, "sha256": hashlib.sha256(data).hexdigest()})
    kept.sort(key=lambda r: r[0])
    n = len(kept)
    if n < MIN_SAMPLES:
        raise G.Refused(f"coverage: {n} kept samples, fewer than {MIN_SAMPLES} (the adapter's floor)")
    if n < MIN_KEPT:
        raise G.Refused(f"coverage: {n} kept samples, fewer than {MIN_KEPT}")
    gaps = [b[0] - a[0] for a, b in zip(kept, kept[1:])]
    max_gap = max(gaps) if gaps else 0.0
    if max_gap > MAX_GAP_S:
        raise G.Refused(f"coverage: a gap of {max_gap:g} s between consecutive kept samples exceeds {MAX_GAP_S} s")
    if any(b[2] < a[2] for a, b in zip(kept, kept[1:])):
        raise G.Refused("s_last_done_slot is not non-decreasing in t")
    dslot = kept[-1][2] - kept[0][2]
    if dslot <= 0:
        raise G.Refused("s_last_done_slot did not advance between the first and last kept samples")
    slot_s = (kept[-1][0] - kept[0][0]) / dslot
    try:
        res = ADAPTER.feed_delay_slots([{"block_lag_ms_p90": r[1]} for r in kept], slot_s, ingest_lag_ms=0.0)
    except ValueError as e:
        raise G.Refused(f"the adapter refused: {e}") from None
    me = Path(__file__).resolve()
    return {"window": {"from": from_text, "to": to_text}, "inputs": inputs, "n_kept": n, "n_dropped": dropped, "max_gap_s": max_gap,
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
    a = ap.parse_args(argv)
    try:
        paths = [G.check_path(p, "sample file") for p in a.sample_file]
        out = G.check_path(a.out, "the out file")
        if out.exists():
            raise G.Refused("the out file exists: refused (written once)")
        rec = measure([(p.name, p.read_bytes()) for p in paths], a.from_, a.to)
        text = json.dumps(rec, indent=2, sort_keys=True) + "\n"
        write_once(out, text)
    except (G.Refused, OSError) as e:
        print(f"r4bt_d_measure: REFUSED: {type(e).__name__}: {e}", file=sys.stderr)
        return 2
    print(text, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
