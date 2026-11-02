"""r4bt_sample_keys: the explicit KEY MAPPING from the fast-0 follower sampler's lines to the follower-sample format `tools/r4bt_a3.py classify --follower-samples` reads (runbook blocker
B-1; QP-RULING-1102-r4bt Q2 items 1 and 2). PAPER ONLY. Pure text-to-text; no RPC, no key, no network. New file (r4bt_*): it edits no existing file.

THE PROBLEM. `r4bt_a3.decoder_failure_ms` reads the keys `ts_ms`, `decoder_blobs_pinned` and `trade_event_v`. The 60 s sampler (`tip-health-*.jsonl`, written by the sampler jobs) writes
`t` (epoch SECONDS of the sample) and the follower status under an `s_` prefix: `s_ts_ms`, `s_decoder_blobs_pinned`, `s_trade_event_v`, counters `s_gaps`, `s_skipped`, ... and
`c_*`. Fed as is, every line "fails" with no usable time and the classifier halts r4bt-a1 at instant 0.

THE MAPPING (the only one; QP-RULING-1102-r4bt Addendum 2):
    ts_ms                   := s_ts_ms   when it is an integer epoch ms in range (the follower status instant: the EARLIER instant of the two), else t * 1000
                               (t: the sample's integer epoch second, 2020 <= year < 2100, not a bool). When s_ts_ms is later than t * 1000 (a clock skew: not the earlier
                               instant) the earlier t * 1000 is used, so the halt instant can only be earlier, never later.
    decoder_blobs_pinned    := s_decoder_blobs_pinned        (copied unchanged)
    trade_event_v           := s_trade_event_v               (copied unchanged)
No `decoder_blobs` list is produced (the sampler holds none): NEVER pass `--decoder-ref` with this file, every sample would fail the blob comparison. `t` still keys the samples
(de-duplication, the --since-s window).

FAIL CLOSED.
  * A key that is missing is left missing in the output (the classifier then treats the sample as failing at its `ts_ms`); a value that is not `true` is copied as it is (failing).
  * The WHOLE FILE is refused (exit 2; the error names the line number and the key NAME, never a value) for: a line that is not a JSON object; a missing, boolean, float or
    out-of-range `t`; a key outside the EXACT real key set (`REAL_KEYS`: t, s_ts_ms, s_lag_slots, s_lag_ms, s_gaps, s_skipped, s_backlog_jumps, s_unresolved_dropped,
    s_block_lag_ms_p50, s_block_lag_ms_p90, s_decoder_blobs_pinned, s_trade_event_v, s_last_done_slot, s_retries, s_breaker_trips, c_last_done, c_gaps, c_skipped, c_writing) - a new
    key is a changed sampler and is refused until the set is re-pinned; a line that already carries a classifier key (`ts_ms`, `decoder_blobs_pinned`, `trade_event_v`,
    `decoder_blobs`) - a mixed or already-mapped file; no sample left after the window (an empty file is no evidence of a healthy follower, so the job is loud).
  * The other real keys (the counters, lags, last-done markers) are accepted and dropped, not read. A line need not carry all of them (only `t` is required).
  * Several lines with the same `t` (merged segments) become ONE sample: a mapped flag is kept only if EVERY copy has it as `true`; otherwise the first copy's non-true value is
    kept (or the key is omitted when a copy lacks it).

    python -m tools.r4bt_sample_keys --in tip-health-A.jsonl [--in tip-health-B.jsonl ...] [--since-s EPOCH_S] [--out follower-samples.jsonl]

Output: JSONL sorted by t, one {"ts_ms", "decoder_blobs_pinned", "trade_event_v"} per distinct t (the keys the sampler lacked are absent); the stderr line
`r4bt_sample_keys: STATUS=OK n_samples=N t_min=.. t_max=.. n_not_true=K` always (counts only). Nothing else is read from a line.
"""
from __future__ import annotations

if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Iterable, Sequence

T_MIN_S, T_MAX_S = 1_577_836_800, 4_102_444_800  # 2020-01-01 .. 2100-01-01 (epoch seconds)
# the sampler's real key set (QP-RULING-1102-r4bt Addendum 2): anything else is refused
REAL_KEYS = frozenset({"t", "s_ts_ms", "s_lag_slots", "s_lag_ms", "s_gaps", "s_skipped", "s_backlog_jumps", "s_unresolved_dropped", "s_block_lag_ms_p50", "s_block_lag_ms_p90",
                       "s_decoder_blobs_pinned", "s_trade_event_v", "s_last_done_slot", "s_retries", "s_breaker_trips", "c_last_done", "c_gaps", "c_skipped", "c_writing"})
MAPPED_FLAGS = (("s_decoder_blobs_pinned", "decoder_blobs_pinned"), ("s_trade_event_v", "trade_event_v"))
CLASSIFIER_KEYS = ("ts_ms", "decoder_blobs_pinned", "trade_event_v", "decoder_blobs")


class MapError(ValueError):
    """The whole input is refused. The message carries a line number and key names only, never a value."""


def _ok_key(k: str) -> bool:
    return k in REAL_KEYS


def _is_ms(x: Any) -> bool:
    return isinstance(x, int) and not isinstance(x, bool) and T_MIN_S * 1000 <= x < T_MAX_S * 1000


def map_lines(lines: Iterable[str], *, label: str = "input") -> dict[int, dict[str, Any]]:
    """{t: {"ts_ms", flags...}} for the lines of ONE source; duplicates of a t inside the source are merged conservatively."""
    out: dict[int, dict[str, Any]] = {}
    for n, raw in enumerate(lines, 1):
        if not raw.strip():
            continue
        try:
            d = json.loads(raw)
        except ValueError:
            raise MapError(f"{label} line {n}: not JSON") from None
        if not isinstance(d, dict):
            raise MapError(f"{label} line {n}: not a JSON object")
        for k in d:
            if k in CLASSIFIER_KEYS:
                raise MapError(f"{label} line {n}: carries the classifier key {k!r}: an already-mapped or mixed file is refused")
            if not _ok_key(k):
                raise MapError(f"{label} line {n}: key {k!r} is not in the sampler's real key set: a foreign or changed shape is refused")
        t = d.get("t")
        if not isinstance(t, int) or isinstance(t, bool) or not T_MIN_S <= t < T_MAX_S:
            raise MapError(f"{label} line {n}: `t` is missing or not an integer epoch second in range")
        sts = d.get("s_ts_ms")
        row: dict[str, Any] = {"ts_ms": min(sts, t * 1000) if _is_ms(sts) else t * 1000}  # the earlier instant
        for src, dst in MAPPED_FLAGS:
            if src in d:
                row[dst] = d[src]
        prev = out.get(t)
        out[t] = row if prev is None else _merge(prev, row)
    return out


def _merge(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    """Two samples of the same t: a flag survives only if both copies have it as true; else the first non-true value; omitted when a copy lacks it."""
    out = {"ts_ms": min(a["ts_ms"], b["ts_ms"])}
    for _src, dst in MAPPED_FLAGS:
        va, vb = a.get(dst, None), b.get(dst, None)
        if dst in a and dst in b:
            out[dst] = True if (va is True and vb is True) else (va if va is not True else vb)
        # a copy lacking the key: the key stays absent (the classifier treats it as failing)
    return out


def map_samples(sources: Sequence[Iterable[str]], *, since_s: int | None = None, labels: Sequence[str] | None = None) -> list[dict[str, Any]]:
    """The follower samples for `r4bt_a3.classify(follower_samples=...)`: sorted by time, one per distinct t at or after since_s. MapError on an empty result."""
    merged: dict[int, dict[str, Any]] = {}
    for i, src in enumerate(sources):
        for t, row in map_lines(src, label=(labels[i] if labels else f"input {i + 1}")).items():
            merged[t] = row if t not in merged else _merge(merged[t], row)
    keep = [merged[t] for t in sorted(merged) if since_s is None or t >= since_s]
    if not keep:
        raise MapError("no sample in the window: an empty sampler file is no evidence of a healthy follower")
    return keep


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Map the follower sampler's lines to the r4bt_a3 follower-sample keys.")
    ap.add_argument("--in", dest="inputs", action="append", required=True, help="a sampler JSONL file (repeatable)")
    ap.add_argument("--since-s", type=int, default=None, help="keep samples with t >= this epoch second")
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    try:
        if a.out and Path(a.out).exists():
            raise MapError(f"{Path(a.out).name} exists: never overwritten")
        srcs = []
        for p in a.inputs:
            try:
                srcs.append(Path(p).read_text(encoding="utf-8").splitlines())
            except OSError as exc:
                raise MapError(f"cannot read {Path(p).name}: {type(exc).__name__}") from None
        rows = map_samples(srcs, since_s=a.since_s, labels=[Path(p).name for p in a.inputs])
    except MapError as exc:
        print(f"r4bt_sample_keys: REFUSED: {exc}", file=sys.stderr)
        return 2
    text = "".join(json.dumps(r, sort_keys=True) + "\n" for r in rows)
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    bad = sum(1 for r in rows if r.get("decoder_blobs_pinned") is not True or r.get("trade_event_v") is not True)
    print(f"r4bt_sample_keys: STATUS=OK n_samples={len(rows)} t_min={rows[0]['ts_ms'] // 1000} t_max={rows[-1]['ts_ms'] // 1000} n_not_true={bad}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
