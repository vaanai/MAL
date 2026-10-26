#!/usr/bin/env python3
"""feed_handover_proof: no-gap and no-duplicate proof for an arena feed hand-off (P35 item (a); docs/research/p35-longbook-handoff/DESIGN.md).

PAPER only. No network, no key. Reads arena_tape_v1 hour files (tape-<YYYY-MM-DDTHH>.jsonl, or .jsonl.zst through the zstd CLI) and prints ONE JSON
object of counts, booleans and thresholds. It never prints or keeps beyond the run a signature, mint, pool, wallet, price, reserve, fill or P&L.
Signatures are held in memory only, as dedup keys.

MODES
  seam    one tape dir, one window [from, to). Checks, each reported as {"ok", value, threshold}:
            duplicates      no (signature, type, event_index) key of a data row (trade, create, complete, pool_create) appears twice in the read hours,
                            counting keys with at least one copy inside the window
            hard_gaps       no gap span overlapping the window that has data_loss true, or a kind in HARD_KINDS (process_restart, feed_restart, feed_start,
                            handover; a superset of tools.arena_paper.HARD_GAP_KINDS, pinned by a test)
                            (open/closed twins collapse by (prog, kind, from_ms)); soft gaps (data_loss false) are counted, not failed
            start_rows      no `start` row in the window (every arena engine books a start row as a hard gap, arena_paper.py on_row)
            bad_lines       no unparseable line in a read hour that overlaps the window
            order           per file: no trade row before a data_loss / hard gap row has t_recv_ms >= that gap's to_ms (tools/arena_tape_schema.md ORDER)
            silence         per program, the longest interval without a data row inside [from, to] (edges included) <= --max-silence-s
            slot_step       per program, the largest step between consecutive distinct data-row slots in the window <= --max-slot-step (<= 0: report only)
            hb_gap          per program, the longest interval between hb rows over the window (one hb before and after it included) <= --max-hb-gap-s
            takeover        for every `takeover` row in the window (written by tools/arena_feed_v2.py, P35 PR 2; schema in DESIGN.md 2.1):
                              * continuity "proven" for every program, and a `stop` row of the old writer earlier in append order (after the previous
                                writer marker);
                              * old_last_data_ms equals the last data row of that program on the tape before the takeover row (append order);
                              * cover_from_ms <= old_last_data_ms - overlap_min_s, where overlap_min_s = max(the row's value, --overlap-min-s) (a floor);
                              * dedup: buffer_overflow is false; buffered = dropped_dup + written_missed; written_missed equals the data rows written after
                                the takeover row with t_recv_ms <= lock_acquired_ms (recomputed);
                              * agreement (QP-RULING-1026 Q1), per program: agreement.n_old equals the old writer's data rows with t_recv in
                                [cover_from_ms, old_last_data_ms] (recomputed); 0 <= only_old <= n_old; n_old >= --min-overlap-rows;
                                only_old / n_old <= --max-disagree-rate; standby_reconnects_in_overlap is 0; the row's own thresholds, if present, equal
                                the pinned ones. Without pinned thresholds (both CLI options) the check FAILS as `thresholds_unpinned`.
                            With --expect-takeover exactly one takeover row must be in the window.
                            LIMIT: cover_from_ms, only_old and standby_reconnects_in_overlap are SELF-REPORTED by the new writer (its buffer and socket
                            state are not on the tape). This tool checks them for consistency and against the thresholds, not for truth; their truth
                            rests on arena_feed_v2's tests and on the live rehearsal's parity against an independent tape.
          PASS iff every check is ok.
  parity  two tape dirs A (e.g. a scratch takeover tape) and B (the reference), one window. Data rows are matched by (signature, type, event_index);
          a key is in scope when its copy in A or in B has t_recv_ms in the window (each tape is read over the window +- --pad-s, so a row whose two
          receive times straddle an edge is not counted as missing). Prints n_a, n_b, both, only_a, only_b, duplicates per tape, and for shared keys the
          number of rows whose fields differ apart from --ignore (default t_recv_ms) with the differing FIELD NAMES only. --seam-ms splits every count
          into a seam bucket ([seam - seam_pad, seam + seam_pad]) and a control bucket, with miss rates only_b / n_b.
          PARITY_EXACT iff only_a = only_b = mismatched rows = 0 (exit 0), else PARITY_DIFF (exit 1); the pass rule of a live rehearsal is QP's.

READ SET. Hour files are selected by NAME only (a glob): hours overlapping [from - pad, to + pad]. Other hours are listed, never opened. While both
.jsonl and .jsonl.zst exist for an hour the .jsonl is read (as tools/arena_state.py does). Append order = hour-file order, then line order.

REFUSED (exit 2): any path containing "meta-capture", "ledger", "decisions" or "bots.json"; any tape path under /data/mal/arena unless --qp-ruling
names an existing file (its sha256 is printed in the output); a window with from >= to.

EXIT: 0 PASS / PARITY_EXACT, 1 FAIL / PARITY_DIFF, 2 refused or bad arguments.
"""

from __future__ import annotations

if __package__ in (None, ""):  # `python tools/feed_handover_proof.py`
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import argparse
import collections
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Sequence

SCHEMA = "feed_handover_proof_v1"
DATA_TYPES = frozenset({"trade", "create", "complete", "pool_create"})
HARD_KINDS = frozenset({"process_restart", "feed_restart", "feed_start", "handover"})
PROGRAMS_DEFAULT = ("pumpswap", "pumpfun")
GAP_SLOTS = 30  # = tools.h5_shadow.GAP_SLOTS (test pins it)
SILENCE_S = 20.0  # = tools.h5_shadow.SILENCE_S (test pins it)
HB_GAP_S_DEFAULT = 75.0  # hb every 60 s per program (arena_feed HB_S) plus the hand-off stop time
OVERLAP_MIN_S_DEFAULT = 120.0  # DESIGN.md 2.1 step 5 / 8
PAD_S_DEFAULT = 3600.0  # late rows go to the current hour's file (arena_feed TapeWriter), so later hours are read too
HOUR_MS = 3_600_000
ARENA_ROOT = "/data/mal/arena"
FORBIDDEN_TOKENS = ("meta-capture", "ledger", "decisions", "bots.json")


class Refused(Exception):
    pass


# ---- inputs ---------------------------------------------------------------------------------------------------
def parse_instant(s: str) -> int:
    """Milliseconds since the epoch, from an integer string or an ISO instant ending in Z (2026-11-08T14:00:00Z)."""
    s = str(s).strip()
    if s.lstrip("-").isdigit():
        return int(s)
    if not s.endswith("Z"):
        raise ValueError(f"instant {s!r}: give epoch ms or an ISO instant ending in Z")
    dt = datetime.fromisoformat(s[:-1]).replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for b in iter(lambda: fh.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def check_path(path: str, qp_ruling: str | None) -> Path:
    """Refuse sealed or forbidden locations (see the module docstring)."""
    raw = str(path)
    resolved = str(Path(raw).resolve())
    for tok in FORBIDDEN_TOKENS:
        if tok in raw or tok in resolved:
            raise Refused(f"path contains {tok!r}: refused")
    if resolved == ARENA_ROOT or resolved.startswith(ARENA_ROOT + "/") or raw.startswith(ARENA_ROOT + "/"):
        if not qp_ruling or not Path(qp_ruling).is_file():
            raise Refused("a tape under /data/mal/arena needs --qp-ruling <existing ruling file> naming this window")
    p = Path(raw)
    if not p.is_dir():
        raise Refused(f"tape dir {raw!r} does not exist")
    return p


def hour_start_ms(name: str) -> int | None:
    if not name.startswith("tape-"):
        return None
    try:
        return int(datetime.strptime(name[5:18], "%Y-%m-%dT%H").replace(tzinfo=timezone.utc).timestamp() * 1000)
    except ValueError:
        return None


def select_hours(tape: Path, lo_ms: int, hi_ms: int) -> list[tuple[int, Path]]:
    """(hour start, file) of every hour overlapping [lo_ms, hi_ms], one file per hour (.jsonl preferred), oldest first. Names only: nothing opened."""
    by_hour: dict[int, dict[str, Path]] = {}
    for p in tape.glob("tape-*.jsonl*"):
        if p.name.endswith(".jsonl"):
            kind = "jsonl"
        elif p.name.endswith(".jsonl.zst"):
            kind = "zst"
        else:
            continue
        h = hour_start_ms(p.name)
        if h is None or h + HOUR_MS <= lo_ms or h > hi_ms:
            continue
        by_hour.setdefault(h, {})[kind] = p
    return [(h, d.get("jsonl") or d["zst"]) for h, d in sorted(by_hour.items())]


def iter_lines(p: Path) -> Iterator[bytes]:
    if p.name.endswith(".zst"):
        from tools.arena_zst import open_zst

        r = open_zst(p)
        buf = b""
        try:
            while True:
                b = r.read()
                if not b:
                    break
                buf += b
                *lines, buf = buf.split(b"\n")
                yield from lines
            if buf:
                yield buf
        finally:
            r.close()
        return
    with open(p, "rb") as fh:
        for ln in fh:
            yield ln.rstrip(b"\n")


SLIM_KEYS = ("v", "type", "prog", "slot", "t_recv_ms", "signature", "event_index")


def read_rows(tape: Path, lo_ms: int, hi_ms: int, win: tuple[int, int], *, slim: bool = False) -> dict[str, Any]:
    """Rows of the selected hours in append order, each with its file index. Bad lines are counted per file (only files overlapping `win` count).
    slim=True keeps only SLIM_KEYS of data and hb rows (memory: a real tape hour is about 0.34 GB of JSON); gap and marker rows are kept whole."""
    rows: list[tuple[int, dict[str, Any]]] = []
    bad_in_window = 0
    files = select_hours(tape, lo_ms, hi_ms)
    for fi, (h, p) in enumerate(files):
        overlaps = h + HOUR_MS > win[0] and h < win[1]
        for ln in iter_lines(p):
            if not ln.strip():
                continue
            try:
                r = json.loads(ln)
            except ValueError:
                bad_in_window += overlaps
                continue
            if not isinstance(r, dict) or not isinstance(r.get("t_recv_ms"), int) or not isinstance(r.get("type"), str):
                bad_in_window += overlaps
                continue
            if slim and (r["type"] in DATA_TYPES or r["type"] == "hb"):
                r = {k: r.get(k) for k in SLIM_KEYS}
            rows.append((fi, r))
    return {"rows": rows, "files": len(files), "bad_lines": bad_in_window}


def data_key(r: dict[str, Any]) -> tuple | None:
    if r.get("type") not in DATA_TYPES or not r.get("signature"):
        return None
    return (str(r["signature"]), str(r["type"]), r.get("event_index"))


def _check(ok: bool, value: Any, threshold: Any = None, **extra: Any) -> dict[str, Any]:
    out = {"ok": bool(ok), "value": value}
    if threshold is not None:
        out["threshold"] = threshold
    out.update(extra)
    return out


def _max_interval(ts: Sequence[int], lo: int | None = None, hi: int | None = None) -> int | None:
    pts = sorted(ts)
    if lo is not None:
        pts = [lo] + pts
    if hi is not None:
        pts = pts + [hi]
    if len(pts) < 2:
        return None
    return max(b - a for a, b in zip(pts, pts[1:]))


# ---- seam -----------------------------------------------------------------------------------------------------
def seam_report(tape: Path, t0: int, t1: int, *, programs: Sequence[str] = PROGRAMS_DEFAULT, max_silence_s: float = SILENCE_S,
                max_slot_step: int = GAP_SLOTS, max_hb_gap_s: float = HB_GAP_S_DEFAULT, overlap_min_s: float = OVERLAP_MIN_S_DEFAULT,
                expect_takeover: bool = False, pad_s: float = PAD_S_DEFAULT, max_disagree_rate: dict[str, float] | None = None,
                min_overlap_rows: dict[str, int] | None = None) -> dict[str, Any]:
    pad = int(pad_s * 1000)
    rd = read_rows(tape, t0 - pad, t1 + pad, (t0, t1), slim=True)
    rows = rd["rows"]
    inwin = lambda t: t0 <= t < t1  # noqa: E731

    # duplicates
    keys: collections.Counter = collections.Counter()
    key_in_win: set = set()
    for _, r in rows:
        k = data_key(r)
        if k is None:
            continue
        keys[k] += 1
        if inwin(r["t_recv_ms"]):
            key_in_win.add(k)
    dup_keys = sum(1 for k in key_in_win if keys[k] > 1)
    dup_extra = sum(keys[k] - 1 for k in key_in_win if keys[k] > 1)

    # gaps
    hard: dict[tuple, str] = {}
    soft: set = set()
    for _, r in rows:
        if r["type"] != "gap":
            continue
        fr = r["from_ms"] if isinstance(r.get("from_ms"), int) else r["t_recv_ms"]
        to = r["to_ms"] if isinstance(r.get("to_ms"), int) else r["t_recv_ms"]
        if to < t0 or fr >= t1:
            continue
        kind = str(r.get("kind"))
        span_key = (str(r.get("prog")), kind, fr)
        if r.get("data_loss") or kind in HARD_KINDS:
            hard[span_key] = kind
        else:
            soft.add(span_key)
    hard_kinds = dict(sorted(collections.Counter(hard.values()).items()))

    # markers
    markers = collections.Counter(r["type"] for _, r in rows if r["type"] in ("start", "stop", "takeover") and inwin(r["t_recv_ms"]))

    # order (per file, append order)
    order_viol = 0
    max_trade: dict[int, int] = {}
    for fi, r in rows:
        if r["type"] == "trade":
            max_trade[fi] = max(max_trade.get(fi, r["t_recv_ms"]), r["t_recv_ms"])
        elif r["type"] == "gap" and isinstance(r.get("to_ms"), int) and fi in max_trade and (r.get("data_loss") or str(r.get("kind")) in HARD_KINDS):
            if inwin(r["t_recv_ms"]) and max_trade[fi] >= r["to_ms"]:
                order_viol += 1

    # silence, slot step, hb cadence per program
    silence, slot_step, hb_gap = {}, {}, {}
    for p in programs:
        ts = [r["t_recv_ms"] for _, r in rows if r["type"] in DATA_TYPES and r.get("prog") == p and inwin(r["t_recv_ms"])]
        mi = _max_interval(ts, t0, t1)
        silence[p] = _check(mi is not None and mi <= max_silence_s * 1000, mi, int(max_silence_s * 1000), rows=len(ts))
        slots = sorted({int(r["slot"]) for _, r in rows if r["type"] in DATA_TYPES and r.get("prog") == p and inwin(r["t_recv_ms"])
                        and isinstance(r.get("slot"), int)})
        st = max((b - a for a, b in zip(slots, slots[1:])), default=None)
        slot_step[p] = _check(max_slot_step <= 0 or (st is not None and st <= max_slot_step), st, max_slot_step, slots=len(slots))
        hbt = sorted(r["t_recv_ms"] for _, r in rows if r["type"] == "hb" and r.get("prog") == p)
        before = [t for t in hbt if t < t0][-1:]
        after = [t for t in hbt if t >= t1][:1]
        hw = before + [t for t in hbt if inwin(t)] + after
        g = _max_interval(hw, None if before else t0, None if after else t1)
        hb_gap[p] = _check(g is not None and g <= max_hb_gap_s * 1000, g, int(max_hb_gap_s * 1000), hb_rows=len(hw))

    # takeover rows
    pinned = max_disagree_rate is not None and min_overlap_rows is not None and all(p in max_disagree_rate and p in min_overlap_rows for p in programs)
    tk_checks: list[dict[str, Any]] = []
    for i, (_, r) in enumerate(rows):
        if r["type"] != "takeover" or not inwin(r["t_recv_ms"]):
            continue
        prev_marker = max((j for j in range(i) if rows[j][1]["type"] in ("start", "takeover")), default=-1)
        next_marker = min((j for j in range(i + 1, len(rows)) if rows[j][1]["type"] in ("start", "takeover", "stop")), default=len(rows))
        old_seg = [rows[j][1] for j in range(prev_marker + 1, i)]
        stop_ok = any(x["type"] == "stop" for x in old_seg)
        cont = r.get("continuity") or {}
        claimed_last = r.get("old_last_data_ms") or {}
        cover = r.get("cover_from_ms") or {}
        agree = r.get("agreement") or {}
        recon = r.get("standby_reconnects_in_overlap") or {}
        row_thr = r.get("thresholds") or {}
        om = max(float(r.get("overlap_min_s") or 0.0), overlap_min_s)
        # dedup counts
        dd = r.get("dedup") or {}
        lock = r.get("lock_acquired_ms")
        missed_tape = sum(1 for j in range(i + 1, next_marker) if rows[j][1]["type"] in DATA_TYPES and isinstance(lock, int)
                          and rows[j][1]["t_recv_ms"] <= lock)
        ints = all(isinstance(dd.get(k), int) for k in ("buffered", "dropped_dup", "written_missed"))
        dedup = {
            "overflow_false": dd.get("buffer_overflow") is False,
            "sum_ok": ints and dd["buffered"] == dd["dropped_dup"] + dd["written_missed"],
            "written_missed_matches_tape": ints and isinstance(lock, int) and dd["written_missed"] == missed_tape,
            "written_missed_tape": missed_tape,
        }
        thr_match = all((row_thr.get("max_disagree_rate") or {}).get(p, (max_disagree_rate or {}).get(p)) == (max_disagree_rate or {}).get(p)
                        and (row_thr.get("min_overlap_rows") or {}).get(p, (min_overlap_rows or {}).get(p)) == (min_overlap_rows or {}).get(p)
                        for p in programs) if pinned else False
        per = {}
        for p in programs:
            data_p = [x["t_recv_ms"] for x in old_seg if x["type"] in DATA_TYPES and x.get("prog") == p]
            last = max(data_p, default=None)
            c, cl = cover.get(p), claimed_last.get(p)
            a = agree.get(p) or {}
            n_old, only_old = a.get("n_old"), a.get("only_old")
            n_tape = sum(1 for t in data_p if isinstance(c, int) and last is not None and c <= t <= last)
            have = isinstance(n_old, int) and isinstance(only_old, int) and 0 <= only_old <= n_old and n_old > 0
            rate = (only_old / n_old) if have else None
            per[p] = {
                "continuity_proven": cont.get(p) == "proven",
                "claim_matches_tape": last is not None and cl == last,
                "overlap_ok": last is not None and isinstance(c, int) and c <= last - int(om * 1000),
                "overlap_ms": (last - c) if (last is not None and isinstance(c, int)) else None,
                "n_old_matches_tape": have and n_old == n_tape,
                "n_old_tape": n_tape,
                "n_old_ok": have and pinned and n_old >= int(min_overlap_rows[p]),
                "disagree_rate": rate,
                "disagree_ok": have and pinned and rate <= float(max_disagree_rate[p]),
                "no_standby_reconnect": recon.get(p) == 0,
            }
        bools = ("continuity_proven", "claim_matches_tape", "overlap_ok", "n_old_matches_tape", "n_old_ok", "disagree_ok", "no_standby_reconnect")
        ok = (stop_ok and pinned and thr_match and all(v for k, v in dedup.items() if k != "written_missed_tape")
              and all(all(v[k] for k in bools) for v in per.values()))
        fails = sorted({k for v in per.values() for k in bools if not v[k]} | {k for k, v in dedup.items() if k != "written_missed_tape" and not v}
                       | ({"thresholds_unpinned"} if not pinned else set()) | ({"thresholds_differ_from_row"} if pinned and not thr_match else set())
                       | ({"old_stop_row"} if not stop_ok else set()))
        tk_checks.append({"ok": ok, "old_stop_row": stop_ok, "overlap_min_s": om, "dedup": dedup, "programs": per, "failed": fails})
    n_tk = len(tk_checks)
    tk_ok = all(c["ok"] for c in tk_checks) and (n_tk == 1 if expect_takeover else True)

    checks = {
        "duplicates": _check(dup_keys == 0, dup_keys, 0, extra_copies=dup_extra, keys_in_window=len(key_in_win)),
        "hard_gaps": _check(not hard, len(hard), 0, kinds=hard_kinds, soft_gaps=len(soft)),
        "start_rows": _check(markers.get("start", 0) == 0, markers.get("start", 0), 0),
        "bad_lines": _check(rd["bad_lines"] == 0, rd["bad_lines"], 0),
        "order": _check(order_viol == 0, order_viol, 0),
        "silence": _check(all(v["ok"] for v in silence.values()), None, None, programs=silence),
        "slot_step": _check(all(v["ok"] for v in slot_step.values()), None, None, programs=slot_step),
        "hb_gap": _check(all(v["ok"] for v in hb_gap.values()), None, None, programs=hb_gap),
        "takeover": _check(tk_ok, n_tk, 1 if expect_takeover else None, rows=tk_checks),
    }
    verdict = "PASS" if all(c["ok"] for c in checks.values()) else "FAIL"
    return {"schema": SCHEMA, "mode": "seam", "window": {"from_ms": t0, "to_ms": t1}, "files_read": rd["files"], "rows_read": len(rows),
            "markers_in_window": dict(sorted(markers.items())), "checks": checks, "failed": sorted(k for k, c in checks.items() if not c["ok"]), "verdict": verdict}


# ---- parity ---------------------------------------------------------------------------------------------------
def _stream_index(tape: Path, lo_ms: int, hi_ms: int, win: tuple[int, int], keep: Callable[[int], bool], ignore: set[str]) -> dict[str, Any]:
    """key -> (t_recv_ms, canonical JSON of the row without the ignored fields) for data rows with keep(t); streamed (no row list is held)."""
    out: dict[tuple, tuple[int, str]] = {}
    dups = bad = 0
    files = select_hours(tape, lo_ms, hi_ms)
    for h, p in files:
        overlaps = h + HOUR_MS > win[0] and h < win[1]
        for ln in iter_lines(p):
            if not ln.strip():
                continue
            try:
                r = json.loads(ln)
            except ValueError:
                bad += overlaps
                continue
            if not isinstance(r, dict) or not isinstance(r.get("t_recv_ms"), int):
                bad += overlaps
                continue
            k = data_key(r)
            if k is None or not keep(r["t_recv_ms"]):
                continue
            if k in out:
                dups += 1
                continue
            out[k] = (r["t_recv_ms"], json.dumps({f: v for f, v in r.items() if f not in ignore}, sort_keys=True, separators=(",", ":")))
    return {"index": out, "dups": dups, "bad_lines": bad, "files": len(files)}


def parity_report(tape_a: Path, tape_b: Path, t0: int, t1: int, *, ignore: Sequence[str] = ("t_recv_ms",), seam_ms: int | None = None,
                  seam_pad_s: float = 300.0, pad_s: float = 60.0) -> dict[str, Any]:
    pad = int(pad_s * 1000)
    hour_pad = max(pad, HOUR_MS)  # rows can sit in a later hour's file
    ign = set(ignore)
    near = lambda t: t0 - pad <= t < t1 + pad  # noqa: E731
    ra = _stream_index(tape_a, t0 - hour_pad, t1 + hour_pad, (t0, t1), near, ign)
    rb = _stream_index(tape_b, t0 - hour_pad, t1 + hour_pad, (t0, t1), near, ign)
    ia, da, ib, db = ra["index"], ra["dups"], rb["index"], rb["dups"]
    inwin = lambda e: e is not None and t0 <= e[0] < t1  # noqa: E731
    scope = {k for k, e in ia.items() if inwin(e)} | {k for k, e in ib.items() if inwin(e)}
    sp = int(seam_pad_s * 1000)

    def bucket(k: tuple) -> str:
        if seam_ms is None:
            return "all"
        t = min(e[0] for e in (ia.get(k), ib.get(k)) if e is not None)
        return "seam" if seam_ms - sp <= t <= seam_ms + sp else "control"

    stats: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    fields: collections.Counter = collections.Counter()
    by_type: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for k in scope:
        b = bucket(k)
        a_r, b_r = ia.get(k), ib.get(k)
        s = stats[b]
        typ = k[1]
        if a_r is not None:
            s["n_a"] += 1
        if b_r is not None:
            s["n_b"] += 1
        if a_r is not None and b_r is not None:
            s["both"] += 1
            diff: list[str] = []
            if a_r[1] != b_r[1]:
                ja, jb = json.loads(a_r[1]), json.loads(b_r[1])
                diff = sorted(f for f in set(ja) | set(jb) if ja.get(f) != jb.get(f))
            if diff:
                s["mismatched_rows"] += 1
                fields.update(diff)
        elif a_r is not None:
            s["only_a"] += 1
            by_type[typ]["only_a"] += 1
        else:
            s["only_b"] += 1
            by_type[typ]["only_b"] += 1
    buckets = {}
    for b in sorted(stats) or ["all"]:
        s = stats[b]
        buckets[b] = {k: int(s.get(k, 0)) for k in ("n_a", "n_b", "both", "only_a", "only_b", "mismatched_rows")}
        buckets[b]["miss_rate_b"] = (s["only_b"] / s["n_b"]) if s["n_b"] else None
    tot = collections.Counter()
    for s in stats.values():
        tot.update(s)
    exact = tot["only_a"] == 0 and tot["only_b"] == 0 and tot["mismatched_rows"] == 0
    out: dict[str, Any] = {"schema": SCHEMA, "mode": "parity", "window": {"from_ms": t0, "to_ms": t1}, "ignored_fields": sorted(ign),
                           "files_read": {"a": ra["files"], "b": rb["files"]}, "bad_lines": {"a": ra["bad_lines"], "b": rb["bad_lines"]},
                           "duplicates": {"a": da, "b": db}, "buckets": buckets, "mismatched_field_names": dict(sorted(fields.items())),
                           "only_by_type": {t: dict(c) for t, c in sorted(by_type.items())},
                           "totals": {k: int(tot.get(k, 0)) for k in ("n_a", "n_b", "both", "only_a", "only_b", "mismatched_rows")}}
    if seam_ms is not None:
        sr, cr = buckets.get("seam", {}).get("miss_rate_b"), buckets.get("control", {}).get("miss_rate_b")
        out["seam_ms"], out["seam_pad_s"] = seam_ms, seam_pad_s
        out["seam_miss_rate_not_above_control"] = None if (sr is None or cr is None) else bool(sr <= cr)
    out["verdict"] = "PARITY_EXACT" if exact and da == 0 and db == 0 else "PARITY_DIFF"
    return out


# ---- cli ------------------------------------------------------------------------------------------------------
def parse_per_prog(arg: str | None, cast, programs: Sequence[str]) -> dict[str, Any] | None:
    """'0.01' -> {p: 0.01 for p in programs}; 'pumpswap=0.01,pumpfun=0.02' -> per program. None stays None (unpinned)."""
    if arg is None:
        return None
    arg = str(arg).strip()
    if "=" not in arg:
        return {p: cast(arg) for p in programs}
    out: dict[str, Any] = {}
    for part in arg.split(","):
        k, _, v = part.partition("=")
        if not k.strip() or not v.strip():
            raise ValueError(f"bad per-program value {part!r}")
        out[k.strip()] = cast(v.strip())
    return out


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Arena feed hand-off proof: no gap, no duplicate (counts only)")
    sub = ap.add_subparsers(dest="mode", required=True)
    s = sub.add_parser("seam", help="one tape, one window around a hand-off")
    s.add_argument("--tape", required=True)
    p = sub.add_parser("parity", help="two tapes over one window (row-set parity, counts only)")
    p.add_argument("--tape-a", required=True)
    p.add_argument("--tape-b", required=True)
    p.add_argument("--ignore", action="append", default=None, help="field ignored in the row comparison (repeatable; default t_recv_ms)")
    p.add_argument("--seam-ms", default=None, help="hand-off instant: split counts into seam / control")
    p.add_argument("--seam-pad-s", type=float, default=300.0)
    p.add_argument("--pad-s", type=float, default=60.0)
    for x in (s, p):
        x.add_argument("--from", dest="t_from", required=True, help="window start (epoch ms or ISO ...Z), inclusive")
        x.add_argument("--to", dest="t_to", required=True, help="window end (epoch ms or ISO ...Z), exclusive")
        x.add_argument("--qp-ruling", default=None, help="required for a tape under /data/mal/arena: the ruling file that names this window")
    s.add_argument("--programs", default=",".join(PROGRAMS_DEFAULT))
    s.add_argument("--max-silence-s", type=float, default=SILENCE_S)
    s.add_argument("--max-slot-step", type=int, default=GAP_SLOTS)
    s.add_argument("--max-hb-gap-s", type=float, default=HB_GAP_S_DEFAULT)
    s.add_argument("--overlap-min-s", type=float, default=OVERLAP_MIN_S_DEFAULT, help="floor of the takeover overlap (a row's own value can only raise it)")
    s.add_argument("--max-disagree-rate", default=None, help="pinned T_p: one number, or prog=rate,prog=rate (QP-RULING-1026 Q1); absent = unpinned = takeover FAILS")
    s.add_argument("--min-overlap-rows", default=None, help="pinned minimum n_old: one integer, or prog=n,prog=n; absent = unpinned = takeover FAILS")
    s.add_argument("--expect-takeover", action="store_true", help="exactly one takeover row must be in the window")
    s.add_argument("--pad-s", type=float, default=PAD_S_DEFAULT)
    return ap


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        t0, t1 = parse_instant(args.t_from), parse_instant(args.t_to)
        if t0 >= t1:
            raise Refused("window: from must be before to")
        if args.mode == "seam":
            tape = check_path(args.tape, args.qp_ruling)
            progs = [x for x in args.programs.split(",") if x]
            out = seam_report(tape, t0, t1, programs=progs, max_silence_s=args.max_silence_s,
                              max_slot_step=args.max_slot_step, max_hb_gap_s=args.max_hb_gap_s, overlap_min_s=args.overlap_min_s,
                              expect_takeover=args.expect_takeover, pad_s=args.pad_s,
                              max_disagree_rate=parse_per_prog(args.max_disagree_rate, float, progs),
                              min_overlap_rows=parse_per_prog(args.min_overlap_rows, int, progs))
            rc = 0 if out["verdict"] == "PASS" else 1
        else:
            ta, tb = check_path(args.tape_a, args.qp_ruling), check_path(args.tape_b, args.qp_ruling)
            out = parity_report(ta, tb, t0, t1, ignore=args.ignore or ("t_recv_ms",),
                                seam_ms=parse_instant(args.seam_ms) if args.seam_ms is not None else None, seam_pad_s=args.seam_pad_s, pad_s=args.pad_s)
            rc = 0 if out["verdict"] == "PARITY_EXACT" else 1
    except (Refused, ValueError) as exc:
        print(json.dumps({"schema": SCHEMA, "refused": str(exc)}), file=sys.stderr)
        return 2
    if args.qp_ruling:
        out["qp_ruling_sha256"] = sha256_file(Path(args.qp_ruling)) if Path(args.qp_ruling).is_file() else None
    print(json.dumps(out, sort_keys=True, separators=(",", ":")))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
