"""Read-only coverage and lead/lag of the fast trade tape against Oracle's.

DEC-015 section 2.2 feed check. For a UTC window it collects the identity of
every trade row on both tapes, ``(signature, event_index)`` (one transaction can
hold several trades, ``observe.trade_decode.records_from_logs`` numbers them),
and reports:

* coverage = |fast intersect oracle| / |oracle|, and the reverse, per hour and pooled
* ``t_recv_ms_fast - t_recv_ms_oracle`` for matched rows (negative = fast first)
* venue split and field-name/type differences on matched rows

Oracle is read by piping ``tools/tape_coverage_scan.py`` to
``ssh <host> nice -n 19 python3 -``; it prints identity, ``t_recv_ms``, the venue
string and a schema id per row, never amounts, wallets, mints or prices. Nothing
is written on either host. Per row we keep 8-byte hashes and an int64 stamp
(about 20 bytes), so an hour of ~1M rows is ~20 MB per side, not a set of strings.

    python3 -m tools.tape_coverage --start 2026-10-01T17 --end 2026-10-01T19 \\
        --json-out out.json --md-out out.md
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from array import array
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Iterator

import numpy as np

from tools import tape_coverage_scan as scan

HOUR_MS = scan.HOUR_MS
COVERAGE_BAR = 0.95
OUTLIER_MS = 5_000
DEFAULT_FAST_DIR = "/var/lib/mal/sealed/fast-trades-trial"
DEFAULT_ORACLE_DIR = "/var/lib/mal/sealed/trades"
SCAN_PATH = Path(__file__).with_name("tape_coverage_scan.py")


def parse_instant(text: str) -> int:
    """'2026-10-01T17', '2026-10-01T16:30' or full ISO (Z optional) -> unix ms (UTC)."""
    raw = text.strip().rstrip("Z")
    for fmt in ("%Y-%m-%dT%H", "%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f"):
        try:
            dt = datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        return int(dt.timestamp() * 1000)
    raise ValueError(f"cannot parse UTC instant: {text!r}")


def iso(ms: int | None) -> str | None:
    if ms is None:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def hash64(text: str) -> int:
    return int.from_bytes(hashlib.blake2b(text.encode("ascii", "replace"), digest_size=8).digest(), "little")


def classify_venue(venue: str) -> str:
    low = venue.lower()
    if "swap" in low:
        return "pumpswap"
    if "bond" in low or "curve" in low:
        return "bonding"
    return "other"


class Side:
    """Compact per-row columns for one tape. Appending is O(1) memory per row."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.id_hash = array("Q")
        self.sig_hash = array("Q")
        self.t = array("q")
        self.venue = array("B")
        self.schema = array("B")
        self.venue_names: list[str] = []
        self._venue_ids: dict[str, int] = {}
        self.schemas: dict[int, dict[str, str]] = {}
        self.footer: dict = {}

    def add(self, sig: str, idx: int, t_ms: int, venue: str, schema_id: int) -> None:
        vid = self._venue_ids.get(venue)
        if vid is None:
            vid = len(self.venue_names)
            if vid > 255:
                vid = 255
            else:
                self._venue_ids[venue] = vid
                self.venue_names.append(venue)
        self.id_hash.append(hash64(f"{sig}:{idx}"))
        self.sig_hash.append(hash64(sig))
        self.t.append(t_ms)
        self.venue.append(vid)
        self.schema.append(min(schema_id, 255))

    def __len__(self) -> int:
        return len(self.t)


def load_stream(lines: Iterable[str], name: str) -> Side:
    """Parse the scan protocol (see tape_coverage_scan) into a Side, streaming."""
    side = Side(name)
    done = False
    for line in lines:
        line = line.rstrip("\n")
        if not line:
            continue
        if line.startswith("#schema "):
            _, sid, body = line.split(" ", 2)
            side.schemas[int(sid)] = json.loads(body)
        elif line.startswith("#done "):
            side.footer = json.loads(line[len("#done "):])
            done = True
        elif line.startswith("#"):
            continue
        else:
            sig, idx, t_ms, venue, sid = line.split(" ")
            side.add(sig, int(idx), int(t_ms), "" if venue == "-" else venue, int(sid))
    if not done:
        raise RuntimeError(f"{name}: stream ended without a #done footer (truncated)")
    return side


def load_local(directory: Path, lo_ms: int, hi_ms: int, name: str) -> Side:
    """Run the same scan code in-process and feed it through the same parser."""

    def lines() -> Iterator[str]:
        schemas = scan.SchemaTable()
        stats: dict = {}
        for item in scan.scan_rows(directory, lo_ms, hi_ms, schemas, stats):
            yield scan.format_row(*item)
        for sid, smap in enumerate(schemas.maps):
            yield f"#schema {sid} {json.dumps(smap, sort_keys=True, separators=(',', ':'))}"
        footer = {
            "rows_out": stats.get("rows_out", 0),
            "rows_scanned": stats.get("rows_scanned", 0),
            "bad_lines": stats.get("bad_lines", 0),
            "missing": stats.get("missing", []),
            "files": stats.get("files", []),
            "schema_overflow": schemas.overflow,
        }
        yield "#done " + json.dumps(footer, sort_keys=True, separators=(",", ":"))

    return load_stream(lines(), name)


def load_remote(
    ssh_host: str, directory: str, lo_ms: int, hi_ms: int, name: str, runner: Callable | None = None
) -> Side:
    """Pipe the scan script to ``ssh <host> python3 -`` and parse its stdout as it arrives."""
    cmd = ["ssh", ssh_host, "nice", "-n", "19", "python3", "-", directory, str(lo_ms), str(hi_ms)]
    with open(SCAN_PATH, "rb") as script:
        proc = (runner or subprocess.Popen)(
            cmd, stdin=script, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        assert proc.stdout is not None
        try:
            side = load_stream(proc.stdout, name)
        finally:
            proc.stdout.close()
        err = proc.stderr.read() if proc.stderr else ""
        rc = proc.wait()
    if rc != 0:
        raise RuntimeError(f"{name}: ssh scan exited {rc}: {err.strip()[:300]}")
    return side


# ---------------------------------------------------------------- analysis


def dedupe(hashes: np.ndarray, t: np.ndarray, *payload: np.ndarray):
    """One row per hash, earliest t kept. Returns sorted-by-hash arrays and dup count."""
    if len(hashes) == 0:
        return (hashes, t, *payload, 0)
    order = np.lexsort((t, hashes))
    h = hashes[order]
    keep = np.ones(len(h), dtype=bool)
    keep[1:] = h[1:] != h[:-1]
    out = [h[keep], t[order][keep]] + [p[order][keep] for p in payload]
    return (*out, int(len(h) - keep.sum()))


def match(query_h: np.ndarray, table_h: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """For each query hash, (found mask, index into table_h). table_h must be sorted unique."""
    if len(table_h) == 0:
        return np.zeros(len(query_h), dtype=bool), np.zeros(len(query_h), dtype=np.int64)
    pos = np.searchsorted(table_h, query_h)
    pos = np.minimum(pos, len(table_h) - 1)
    return table_h[pos] == query_h, pos


def pctiles(values: np.ndarray) -> dict:
    if len(values) == 0:
        return {"n": 0, "p10": None, "p50": None, "p90": None, "mean": None}
    p10, p50, p90 = np.percentile(values, [10, 50, 90])
    return {
        "n": int(len(values)),
        "p10": float(p10),
        "p50": float(p50),
        "p90": float(p90),
        "mean": float(values.mean()),
    }


def lag_summary(d: np.ndarray) -> dict:
    inside = d[np.abs(d) <= OUTLIER_MS]
    return {
        "all": pctiles(d),
        "within_5s": pctiles(inside),
        "outliers_over_5s": int(len(d) - len(inside)),
        "fast_first_share": float((d < 0).mean()) if len(d) else None,
    }


def schema_diff(a: dict[str, str], b: dict[str, str]) -> dict:
    """Names and types only. a = fast, b = oracle."""
    return {
        "only_fast": sorted(set(a) - set(b)),
        "only_oracle": sorted(set(b) - set(a)),
        "type_differs": {k: {"fast": a[k], "oracle": b[k]} for k in sorted(set(a) & set(b)) if a[k] != b[k]},
    }


def compare(
    fast: Side,
    oracle: Side,
    lo_ms: int,
    hi_ms: int,
    *,
    bar: float = COVERAGE_BAR,
) -> dict:
    """Coverage and lag over [lo_ms, hi_ms). Rows outside it (the scan margin) only serve as match candidates."""
    f_h, f_t, f_ven, f_sch, f_dups = dedupe(
        np.frombuffer(fast.id_hash, dtype=np.uint64) if len(fast) else np.zeros(0, np.uint64),
        np.frombuffer(fast.t, dtype=np.int64) if len(fast) else np.zeros(0, np.int64),
        np.frombuffer(fast.venue, dtype=np.uint8) if len(fast) else np.zeros(0, np.uint8),
        np.frombuffer(fast.schema, dtype=np.uint8) if len(fast) else np.zeros(0, np.uint8),
    )
    o_h, o_t, o_ven, o_sch, o_dups = dedupe(
        np.frombuffer(oracle.id_hash, dtype=np.uint64) if len(oracle) else np.zeros(0, np.uint64),
        np.frombuffer(oracle.t, dtype=np.int64) if len(oracle) else np.zeros(0, np.int64),
        np.frombuffer(oracle.venue, dtype=np.uint8) if len(oracle) else np.zeros(0, np.uint8),
        np.frombuffer(oracle.schema, dtype=np.uint8) if len(oracle) else np.zeros(0, np.uint8),
    )
    fs_h, fs_t, fs_dups = dedupe(
        np.frombuffer(fast.sig_hash, dtype=np.uint64) if len(fast) else np.zeros(0, np.uint64),
        np.frombuffer(fast.t, dtype=np.int64) if len(fast) else np.zeros(0, np.int64),
    )
    os_h, os_t, os_dups = dedupe(
        np.frombuffer(oracle.sig_hash, dtype=np.uint64) if len(oracle) else np.zeros(0, np.uint64),
        np.frombuffer(oracle.t, dtype=np.int64) if len(oracle) else np.zeros(0, np.int64),
    )

    o_in = (o_t >= lo_ms) & (o_t < hi_ms)
    f_in = (f_t >= lo_ms) & (f_t < hi_ms)

    # Oracle-side denominators: which Oracle rows (in window) does fast also have?
    found_o, pos_o = match(o_h[o_in], f_h)
    # Reverse: which fast rows (in window) does Oracle also have?
    found_f, _ = match(f_h[f_in], o_h)

    oo_t = o_t[o_in]
    oo_ven = o_ven[o_in]
    oo_sch = o_sch[o_in]
    d_all = f_t[pos_o][found_o] - oo_t[found_o]  # fast - oracle, ms
    m_ven_fast = f_ven[pos_o][found_o]
    m_sch_fast = f_sch[pos_o][found_o]
    m_ven_orc = oo_ven[found_o]
    m_sch_orc = oo_sch[found_o]
    m_t_orc = oo_t[found_o]

    # Signature level (comparable to the 2026-09-27 note).
    so_in = (os_t >= lo_ms) & (os_t < hi_ms)
    sf_in = (fs_t >= lo_ms) & (fs_t < hi_ms)
    found_so, _ = match(os_h[so_in], fs_h)
    found_sf, _ = match(fs_h[sf_in], os_h)

    def ratio(a: int, b: int) -> float | None:
        return a / b if b else None

    def block(oracle_n: int, matched: int, fast_n: int, fast_matched: int) -> dict:
        return {
            "oracle_rows": oracle_n,
            "fast_rows": fast_n,
            "matched": matched,
            "coverage": ratio(matched, oracle_n),
            "reverse_matched": fast_matched,
            "reverse_coverage": ratio(fast_matched, fast_n),
            "oracle_only": oracle_n - matched,
            "fast_only": fast_n - fast_matched,
        }

    pooled = block(int(o_in.sum()), int(found_o.sum()), int(f_in.sum()), int(found_f.sum()))
    pooled["lag_ms_fast_minus_oracle"] = lag_summary(d_all)
    pooled["signature_level"] = block(
        int(so_in.sum()), int(found_so.sum()), int(sf_in.sum()), int(found_sf.sum())
    )

    # Per hour slices of the window.
    hours = []
    f_hour = f_t[f_in] // HOUR_MS
    o_hour = oo_t // HOUR_MS
    m_hour = m_t_orc // HOUR_MS
    first_hour = lo_ms - lo_ms % HOUR_MS
    for start in range(first_hour, hi_ms, HOUR_MS):
        h = start // HOUR_MS
        a = max(lo_ms, start)
        b = min(hi_ms, start + HOUR_MS)
        sel_o = o_hour == h
        sel_m = m_hour == h
        sel_f = f_hour == h
        entry = block(
            int(sel_o.sum()),
            int((found_o & sel_o).sum()),
            int(sel_f.sum()),
            int((found_f & sel_f).sum()),
        )
        entry.update(
            {
                "hour": scan.hour_stamp(start),
                "slice_start": iso(a),
                "slice_end": iso(b),
                "full_hour": bool(a == start and b == start + HOUR_MS),
                "lag_ms_fast_minus_oracle": lag_summary(d_all[sel_m]),
            }
        )
        entry["meets_bar"] = bool(entry["coverage"] is not None and entry["coverage"] >= bar)
        hours.append(entry)

    # Venue split, keyed by the Oracle row's venue class.
    o_class = np.array([classify_venue(v) for v in oracle.venue_names] or ["other"])
    f_class = np.array([classify_venue(v) for v in fast.venue_names] or ["other"])
    orc_cls = o_class[oo_ven] if len(oo_ven) else np.array([], dtype=str)
    venues = {}
    for cls in ("bonding", "pumpswap", "other"):
        sel = orc_cls == cls
        total = int(sel.sum())
        if total == 0:
            continue
        venues[cls] = {
            "oracle_rows": total,
            "matched": int((found_o & sel).sum()),
            "coverage": ratio(int((found_o & sel).sum()), total),
            "lag_ms_fast_minus_oracle": lag_summary(d_all[sel[found_o]]),
        }
    m_cls_f = f_class[m_ven_fast] if len(m_ven_fast) else np.array([], dtype=str)
    m_cls_o = o_class[m_ven_orc] if len(m_ven_orc) else np.array([], dtype=str)

    # Field names and types (no values), per distinct schema pair on matched rows.
    pair_counts: dict[tuple[int, int], int] = {}
    if len(m_sch_fast):
        pairs, counts = np.unique(np.stack([m_sch_fast, m_sch_orc]), axis=1, return_counts=True)
        for (fs, os_), c in zip(pairs.T.tolist(), counts.tolist()):
            pair_counts[(fs, os_)] = c
    pair_report = []
    for (fs, os_), c in sorted(pair_counts.items(), key=lambda kv: -kv[1]):
        diff = schema_diff(fast.schemas.get(fs, {}), oracle.schemas.get(os_, {}))
        pair_report.append(
            {
                "fast_schema_id": fs,
                "oracle_schema_id": os_,
                "matched_rows": c,
                "identical": not (diff["only_fast"] or diff["only_oracle"] or diff["type_differs"]),
                **diff,
            }
        )

    f_first = int(f_t[f_in].min()) if f_in.any() else None
    f_first_any = int(f_t.min()) if len(f_t) else None
    hour_start_ms = {scan.hour_stamp(s): s for s in range(first_hour, hi_ms, HOUR_MS)}
    complete_hours = sum(
        1
        for h in hours
        if h["full_hour"]
        and h["oracle_rows"] > 0
        and f_first_any is not None
        and f_first_any <= hour_start_ms[h["hour"]] + 5_000
    )
    warnings = []
    if f_first is not None and f_first > lo_ms + 5_000:
        warnings.append(
            f"fast tape's first row in the window is {iso(f_first)}, {(f_first - lo_ms) / 1000:.0f} s after the "
            "window start; coverage is understated by the span before the listener existed (use --clip-to-fast)"
        )
    if f_in.sum() == 0:
        warnings.append("no fast rows in the window")
    for side in (fast, oracle):
        if side.footer.get("missing"):
            warnings.append(f"{side.name}: hour files not found: {side.footer['missing']}")
        if side.footer.get("bad_lines"):
            warnings.append(f"{side.name}: {side.footer['bad_lines']} unparseable lines skipped")
    cov = pooled["coverage"]
    verdict = "NO_DATA" if cov is None else ("PASS" if cov >= bar else "FAIL")
    return {
        "window": {"start": iso(lo_ms), "end": iso(hi_ms), "seconds": (hi_ms - lo_ms) / 1000},
        "bar": bar,
        "verdict": verdict,
        "complete_hours": complete_hours,
        "evidence_sufficient": bool(complete_hours >= 2 and verdict != "NO_DATA"),
        "hours_below_bar": [h["hour"] for h in hours if not h["meets_bar"]],
        "pooled": pooled,
        "hours": hours,
        "venues": venues,
        "matched_venue_class_disagreements": int((m_cls_f != m_cls_o).sum()) if len(m_cls_f) else 0,
        "venue_names": {"fast": fast.venue_names, "oracle": oracle.venue_names},
        "schema_pairs_on_matched_rows": pair_report,
        "schemas": {"fast": fast.schemas, "oracle": oracle.schemas},
        "duplicates_dropped": {
            "fast_identity": f_dups,
            "oracle_identity": o_dups,
            "fast_signature": fs_dups,
            "oracle_signature": os_dups,
        },
        "scan_footers": {"fast": fast.footer, "oracle": oracle.footer},
        "warnings": warnings,
    }


# ---------------------------------------------------------------- output


def pct(x: float | None) -> str:
    return "n/a" if x is None else f"{100 * x:.3f}%"


def ms(x: float | None) -> str:
    return "n/a" if x is None else f"{x:+.0f}"


def render_markdown(rep: dict, meta: dict) -> str:
    p = rep["pooled"]
    lag = p["lag_ms_fast_minus_oracle"]
    lines = [
        "# Fast trade tape coverage against Oracle",
        "",
        f"Window `{rep['window']['start']}` to `{rep['window']['end']}` ({rep['window']['seconds'] / 3600:.3f} h). "
        f"Generated by `tools/tape_coverage.py`. Read-only on both hosts; Oracle was read over `ssh mal-core-0`.",
        "",
        f"**Verdict against the {100 * rep['bar']:.0f}% bar (DEC-015 2.2): {rep['verdict']}**. "
        f"Complete UTC hours in the window: {rep['complete_hours']}. "
        + (
            "Evidence is sufficient on hours."
            if rep["evidence_sufficient"]
            else "**Fewer than 2 complete hours: this is a partial result, not the 2.2 sign-off.**"
        ),
        "",
        "## Coverage (trade identity = signature + event_index)",
        "",
        "| Slice | Oracle rows | Fast rows | Matched | Coverage fast/oracle | Reverse oracle/fast | Meets bar |",
        "| --- | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for h in rep["hours"]:
        tag = h["hour"] + ("" if h["full_hour"] else " (partial)")
        lines.append(
            f"| {tag} | {h['oracle_rows']:,} | {h['fast_rows']:,} | {h['matched']:,} | {pct(h['coverage'])} "
            f"| {pct(h['reverse_coverage'])} | {'yes' if h['meets_bar'] else 'no'} |"
        )
    lines.append(
        f"| **Pooled** | {p['oracle_rows']:,} | {p['fast_rows']:,} | {p['matched']:,} | **{pct(p['coverage'])}** "
        f"| {pct(p['reverse_coverage'])} | {'yes' if rep['verdict'] == 'PASS' else 'no'} |"
    )
    sl = p["signature_level"]
    lines += [
        "",
        f"Signature level (the 2026-09-27 note's unit): {sl['matched']:,} of {sl['oracle_rows']:,} Oracle "
        f"signatures, {pct(sl['coverage'])}; reverse {pct(sl['reverse_coverage'])}.",
        "",
        "## Lead and lag",
        "",
        "`t_recv_ms_fast - t_recv_ms_oracle` on matched rows, earliest stamp per identity. **Negative = fast host first.** "
        "Percentiles are linear.",
        "",
        "| Slice | n | p10 ms | p50 ms | p90 ms | Fast first | Outliers > 5 s |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for h in rep["hours"]:
        a = h["lag_ms_fast_minus_oracle"]
        lines.append(
            f"| {h['hour']} | {a['all']['n']:,} | {ms(a['all']['p10'])} | {ms(a['all']['p50'])} | "
            f"{ms(a['all']['p90'])} | {pct(a['fast_first_share'])} | {a['outliers_over_5s']:,} |"
        )
    lines.append(
        f"| **Pooled** | {lag['all']['n']:,} | {ms(lag['all']['p10'])} | {ms(lag['all']['p50'])} | "
        f"{ms(lag['all']['p90'])} | {pct(lag['fast_first_share'])} | {lag['outliers_over_5s']:,} |"
    )
    w = lag["within_5s"]
    lines += [
        "",
        f"Excluding the {lag['outliers_over_5s']:,} pairs over 5 s apart: n {w['n']:,}, p10 {ms(w['p10'])}, "
        f"p50 {ms(w['p50'])}, p90 {ms(w['p90'])} ms.",
        "",
        "**Clock caveat.** The two hosts have independent NTP clocks. Measured offsets so far are +1 to +20 ms for "
        "`mal-fast-0` (+1.1 ms on 2026-09-27 11:00Z, +12.7 and +19.7 ms earlier that day) and microseconds for Oracle. "
        "These percentiles are therefore good to roughly that much; a single p50 within about 20 ms of zero does not say which host is first. "
        "The offset was not re-measured for this run.",
        "",
        "## Venue split (by the Oracle row's venue)",
        "",
        "| Venue | Oracle rows | Matched | Coverage | Lag p50 ms | Lag p90 ms |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for name, v in rep["venues"].items():
        a = v["lag_ms_fast_minus_oracle"]["all"]
        lines.append(
            f"| {name} | {v['oracle_rows']:,} | {v['matched']:,} | {pct(v['coverage'])} | {ms(a['p50'])} | {ms(a['p90'])} |"
        )
    lines += [
        "",
        f"Venue strings seen: fast {rep['venue_names']['fast']}, Oracle {rep['venue_names']['oracle']}. "
        f"Matched rows where the two hosts disagree on the venue class: {rep['matched_venue_class_disagreements']:,}.",
        "",
        "## Field names and types on matched rows (no values compared)",
        "",
    ]
    if not rep["schema_pairs_on_matched_rows"]:
        lines.append("No matched rows.")
    for pr in rep["schema_pairs_on_matched_rows"]:
        if pr["identical"]:
            lines.append(f"- {pr['matched_rows']:,} matched rows: field names and types identical on both hosts.")
        else:
            lines.append(
                f"- {pr['matched_rows']:,} matched rows differ. Only on fast: `{pr['only_fast']}`. "
                f"Only on Oracle: `{pr['only_oracle']}`. Type differs: `{pr['type_differs']}`."
            )
    lines += [
        "",
        "Values (amounts, reserves, slots) were deliberately not compared: the Oracle side prints only identity, "
        "`t_recv_ms`, the venue string and a schema id.",
        "",
        "## Duplicates and warnings",
        "",
        f"Duplicate identities dropped (earliest stamp kept): {rep['duplicates_dropped']}.",
    ]
    for warn in rep["warnings"]:
        lines.append(f"- WARNING: {warn}")
    lines += [
        "",
        "## What this does not measure",
        "",
        "- Hashing: identities are compared as 8-byte blake2b hashes. A collision would count as a match; expected rate is below 1e-7 at this size.",
        "- Window is bounded by `t_recv_ms` on each host; a trade stamped in a different second near a window edge is matched through a "
        f"{meta['margin_s']} s margin on both sides.",
        "- No claim about the runner, its lag, or any book.",
        "",
        f"Command: `{meta['command']}`",
        f"Run at {meta['run_at']}; wall time {meta['wall_s']:.0f} s; peak RSS {meta['peak_rss_mb']:.0f} MB.",
        "",
    ]
    return "\n".join(lines)


def peak_rss_mb() -> float:
    try:
        import resource

        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    except Exception:  # pragma: no cover
        return 0.0


def run(args: argparse.Namespace) -> dict:
    t0 = time.time()
    lo = parse_instant(args.start)
    hi = parse_instant(args.end)
    if hi <= lo:
        raise SystemExit("--end must be after --start")
    margin = int(args.margin_s * 1000)
    fast = load_local(Path(args.fast_dir), lo - margin, hi + margin, "fast")
    oracle = load_remote(args.oracle_ssh, args.oracle_dir, lo - margin, hi + margin, "oracle")
    if args.clip_to_fast and len(fast):
        first = min(fast.t)
        lo = max(lo, first + int(args.clip_to_fast_s * 1000))
    rep = compare(fast, oracle, lo, hi)
    meta = {
        "margin_s": args.margin_s,
        "command": "python3 -m tools.tape_coverage " + " ".join(args.argv),
        "run_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "wall_s": time.time() - t0,
        "peak_rss_mb": peak_rss_mb(),
    }
    rep["meta"] = meta
    return rep


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--start", required=True, help="UTC instant, e.g. 2026-10-01T17 or 2026-10-01T16:30:00Z")
    ap.add_argument("--end", required=True, help="exclusive UTC instant")
    ap.add_argument("--fast-dir", default=DEFAULT_FAST_DIR)
    ap.add_argument("--oracle-ssh", default="mal-core-0")
    ap.add_argument("--oracle-dir", default=DEFAULT_ORACLE_DIR)
    ap.add_argument("--margin-s", type=float, default=10.0, help="extra seconds scanned each side to match edge rows")
    ap.add_argument("--clip-to-fast", action="store_true", help="start the window at the fast tape's first row")
    ap.add_argument("--clip-to-fast-s", type=float, default=2.0)
    ap.add_argument("--json-out", default=None)
    ap.add_argument("--md-out", default=None)
    return ap


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    args = build_parser().parse_args(argv)
    args.argv = argv
    rep = run(args)
    md = render_markdown(rep, rep["meta"])
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(rep, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.md_out:
        Path(args.md_out).write_text(md, encoding="utf-8")
    print(md)
    return 0 if rep["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
