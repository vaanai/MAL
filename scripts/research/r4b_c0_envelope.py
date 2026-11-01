#!/usr/bin/env python3
"""r4-b C0 decision host: the memory envelope and the host-lifetime check for a run of up to 14 days (DEC-032 Amendment 8, C0; manager18's C0 note of
2026-10-31: "26.75 MiB/h x 336 h ~ 9 GB, so a 14-day C0 host needs a larger envelope or a segment hand-off").

PAPER / DRY tooling. It reads ONE file, the long DRY run's sidecar output (scripts/research/r4b_dry_sidecar.py, `$HOME/mal-data/r4b-dry/<change_id>/sidecar.jsonl`),
and from each line ONLY the keys `ts`, `scope_found`, `mem_anon` and `catchup_end_ts` (memory and timing). It never opens a decision log, the host's out/ dir,
a tape, the tip tape or anything under /var/lib/mal/sealed. Memory is not an outcome: nothing here reads or says anything about r4-b's decisions or P&L.
Stdlib only (run it with `python -I`).

The question: a C0 decision host whose engine state holds 52 h of history at ORIGIN (a tip catch-up host: its catch-up end; a live-warm-up host: its start + 52 h;
the long DRY host itself if it continues: its own catch-up end) must run until RUN_END. Using the long DRY run's own memory record, what anon will it reach, which
scope cap does that need (the 85 % line of QP's M6, as the DRY run used it), and does the run fit MiScusi's 14-day job limit?

Model (fixed in code, before any of the data it judges exists past catch-up end + 116 h):
  * h = t - (DRY catch-up end), in hours. The C0 horizon is H = RUN_END - ORIGIN.
  * Pre-plateau slope: OLS of anon on t over h in [1, min(h_last, 116)] (the sidecar's M6 settle hour and its 116 h horizon = the 7-day bonding-idle rule
    minus the 52 h of history; r4b_dry_sidecar.HORIZON_AFTER_CATCHUP_S).
  * Tail slope (the plateau test): OLS over the trailing TAIL_H = 24 h, only if every one of those samples is at h >= 117 and they span >= 12 h. Otherwise the
    plateau is "not measured".
  * Projection at H:
      H <= h_last              the observed maximum over h in [1, H] (no extrapolation; label "observed");
      H >  h_last, tail known  fitted tail at h_last + max(tail slope, 0) x (H - h_last) (label "tail");
      H >  h_last, no tail     fitted pre-plateau line at h_last + pre slope x (H - h_last) (label "linear", the conservative case).
    The no-plateau figure (the pre-plateau line extended to H) is always reported beside it as `proj_no_plateau_mib`.
  * The needed cap: ceil(projection / 0.85 / 256) x 256 MiB. FITS iff projection <= 0.85 x the candidate cap (default 5,120 MiB, the long run's cap).
  * Lifetime: MiScusi refuses a job longer than 14 days (1,209,600 s; job #1036 and #1516 hit it). FITS iff RUN_END <= HOST_JOB_START + 14 d. The report gives
    the latest possible run end and, with --first-send-epoch, the longest canary that fits.
Which verdict binds, the plateau model or the no-plateau one, is quant-proof's ruling, not this tool's; it prints both and decides nothing about a send.

Exit: 0 both the envelope (by the model's projection) and the lifetime FIT; 1 either does not fit; 2 INSUFFICIENT (fewer than 20 samples over at least 6 h after
the settle hour, or no catch-up end); 5 usage or an unreadable file.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from typing import Any, Iterable

MIB = 1 << 20
HOUR = 3600.0
SETTLE_H = 1.0             # the first hour after catch-up is not fitted (M6: [+1 h, +12 h])
PLATEAU_H = 116.0          # r4b_dry_sidecar.HORIZON_AFTER_CATCHUP_S / 3600: the 7-day bonding-idle rule minus the 52 h of history
TAIL_H = 24.0              # the trailing window of the plateau test
TAIL_MIN_SPAN_H = 12.0
FIT_MIN_SPAN_H = 6.0       # as the sidecar's FIT_MIN_SPAN_S
FIT_MIN_POINTS = 20        # as the sidecar's FIT_MIN_POINTS
ALERT_FRAC = 0.85          # M6's line: 85 % of the scope's memory.max
CAP_STEP_MIB = 256
DEFAULT_CAP_MIB = 5120     # the long DRY run's scope cap (scripts/research/r4b-dry-long.sh SCOPE_MAX_MIB)
MISCUSI_MAX_S = 14 * 86400  # MiScusi's job limit: a total above 14 d is refused
WARMUP_H = 52.0            # tools.r4b_live.DEFAULT_WARMUP_S / 3600
KEYS = ("ts", "scope_found", "mem_anon", "catchup_end_ts")
EXIT_FIT, EXIT_NOFIT, EXIT_INSUFFICIENT, EXIT_USAGE = 0, 1, 2, 5


def ols(points: list[tuple[float, float]]) -> tuple[float, float, float] | None:
    """Ordinary least squares of y on x. (slope, intercept at x = 0, mean x), or None with fewer than 2 points or no spread in x."""
    n = len(points)
    if n < 2:
        return None
    mx = sum(p[0] for p in points) / n
    my = sum(p[1] for p in points) / n
    sxx = sum((p[0] - mx) ** 2 for p in points)
    if sxx <= 0:
        return None
    slope = sum((p[0] - mx) * (p[1] - my) for p in points) / sxx
    return slope, my - slope * mx, mx


def parse_lines(lines: Iterable[str]) -> dict[str, Any]:
    """Keep only KEYS from each JSON line. Returns {'samples': [(ts, anon_bytes)] sorted by ts and de-duplicated, 'catchup_end': first catchup_end_ts or None,
    'bad_lines': n}. A line without a numeric ts, without scope_found true or without a numeric mem_anon gives no sample (it may still carry catchup_end_ts)."""
    seen: dict[float, float] = {}
    ce = None
    bad = 0
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            raw = json.loads(line)
        except ValueError:
            bad += 1
            continue
        if not isinstance(raw, dict):
            bad += 1
            continue
        r = {k: raw.get(k) for k in KEYS}
        if ce is None and isinstance(r["catchup_end_ts"], (int, float)) and not isinstance(r["catchup_end_ts"], bool):
            ce = float(r["catchup_end_ts"])
        ts, anon = r["ts"], r["mem_anon"]
        if r["scope_found"] is True and isinstance(ts, (int, float)) and isinstance(anon, (int, float)) and not isinstance(anon, bool):
            seen[float(ts)] = float(anon)
    return {"samples": sorted(seen.items()), "catchup_end": ce, "bad_lines": bad}


def envelope(samples: list[tuple[float, float]], catchup_end: float, *, origin: float, run_end: float, cap_mib: float = DEFAULT_CAP_MIB,
             host_job_start: float | None = None, first_send: float | None = None, alert_frac: float = ALERT_FRAC) -> dict[str, Any]:
    """The pure computation (no I/O). samples: (epoch s, anon bytes), sorted. Returns the report dict; `verdict` is FIT, NO_FIT or INSUFFICIENT."""
    out: dict[str, Any] = {"schema": "r4b_c0_envelope_v1", "catchup_end": catchup_end, "origin": origin, "run_end": run_end, "cap_mib": cap_mib,
                           "alert_frac": alert_frac, "plateau_h": PLATEAU_H, "notes": []}
    H = (run_end - origin) / HOUR
    out["horizon_h"] = round(H, 3)
    pts = [((t - catchup_end) / HOUR, a / MIB) for t, a in samples if t >= catchup_end + SETTLE_H * HOUR]  # (h, MiB) after the settle hour
    out["n_samples"] = len(pts)
    if H <= 0:
        out["notes"].append("run_end is not after origin")
    if not pts:
        out.update(verdict="INSUFFICIENT", h_last=None)
        out["notes"].append("no sample after catch-up end + 1 h")
        return out
    h_last = pts[-1][0]
    out["h_last"] = round(h_last, 3)
    out["anon_last_mib"] = round(pts[-1][1], 1)
    out["anon_max_mib"] = round(max(a for _, a in pts), 1)
    pre = [(h, a) for h, a in pts if h <= PLATEAU_H]
    pre_fit = ols(pre) if len(pre) >= FIT_MIN_POINTS and pre[-1][0] - pre[0][0] >= FIT_MIN_SPAN_H else None
    out["pre_n"] = len(pre)
    out["pre_span_h"] = round(pre[-1][0] - pre[0][0], 3) if pre else 0.0
    out["pre_slope_mib_h"] = None if pre_fit is None else round(pre_fit[0], 3)
    tail = [(h, a) for h, a in pts if h >= h_last - TAIL_H]
    tail_ok = bool(tail) and tail[0][0] >= PLATEAU_H + SETTLE_H and tail[-1][0] - tail[0][0] >= TAIL_MIN_SPAN_H and len(tail) >= FIT_MIN_POINTS
    tail_fit = ols(tail) if tail_ok else None
    out["tail_n"] = len(tail) if tail_ok else 0
    out["tail_slope_mib_h"] = None if tail_fit is None else round(tail_fit[0], 3)
    out["plateau_measured"] = tail_fit is not None
    if pre_fit is None:
        out["verdict"] = "INSUFFICIENT"
        out["notes"].append(f"the pre-plateau fit needs >= {FIT_MIN_POINTS} samples over >= {FIT_MIN_SPAN_H} h in h [1, 116]")
        return out
    pre_at = lambda h: pre_fit[1] + pre_fit[0] * h  # noqa: E731
    out["proj_no_plateau_mib"] = round(pre_at(h_last) + pre_fit[0] * max(H - h_last, 0.0), 1) if H > h_last else None
    if H <= h_last:
        proj, label = max([a for h, a in pts if h <= H] or [pts[0][1]]), "observed"
    elif tail_fit is not None:
        proj, label = tail_fit[1] + tail_fit[0] * h_last + max(tail_fit[0], 0.0) * (H - h_last), "tail"
    else:
        proj, label = pre_at(h_last) + pre_fit[0] * (H - h_last), "linear"
        out["notes"].append("plateau not measured: the projection extends the pre-plateau line (conservative)")
    out["projection_label"] = label
    out["projection_mib"] = round(proj, 1)
    need = math.ceil(proj / alert_frac / CAP_STEP_MIB) * CAP_STEP_MIB
    out["needed_cap_mib"] = need
    out["alert_line_mib"] = round(cap_mib * alert_frac, 1)
    out["envelope_fits"] = proj <= cap_mib * alert_frac
    if out["proj_no_plateau_mib"] is not None:
        out["needed_cap_no_plateau_mib"] = math.ceil(out["proj_no_plateau_mib"] / alert_frac / CAP_STEP_MIB) * CAP_STEP_MIB
        out["envelope_fits_no_plateau"] = out["proj_no_plateau_mib"] <= cap_mib * alert_frac
    life_ok = True
    if host_job_start is not None:
        latest = host_job_start + MISCUSI_MAX_S
        out["host_job_latest_end"] = latest
        out["lifetime_fits"] = life_ok = run_end <= latest
        if not life_ok:
            out["notes"].append("run_end is past the host job's 14-day MiScusi limit: a second host (segment) or a shorter canary is needed")
        if first_send is not None:
            out["max_canary_days_in_job"] = round(max(latest - first_send, 0.0) / 86400.0, 3)
    out["verdict"] = "FIT" if (out["envelope_fits"] and life_ok) else "NO_FIT"
    return out


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="r4-b C0 decision host: memory envelope and 14-day lifetime from the long DRY run's sidecar.jsonl (memory and timing keys only).")
    ap.add_argument("--sidecar", required=True, help="the long DRY run's sidecar.jsonl")
    ap.add_argument("--origin-epoch", type=float, required=True,
                    help="when the C0 host's state holds 52 h of history: catch-up end (catch-up host or the continuing DRY host) or start + 52 h (live warm-up)")
    ap.add_argument("--run-end-epoch", type=float, required=True, help="when the C0 decision host must still be running (the canary's end_ms / 1000)")
    ap.add_argument("--cap-mib", type=float, default=DEFAULT_CAP_MIB, help="candidate scope cap in MiB (default 5120, the long run's)")
    ap.add_argument("--host-job-start-epoch", type=float, default=None, help="the C0 host's MiScusi job start, for the 14-day limit")
    ap.add_argument("--first-send-epoch", type=float, default=None, help="planned first send, for the longest canary that fits the job")
    ap.add_argument("--catchup-end-epoch", type=float, default=None, help="override the DRY run's catch-up end (default: the first catchup_end_ts in the file)")
    return ap


def main(argv: list[str] | None = None) -> int:
    try:
        a = build_parser().parse_args(argv)
    except SystemExit:
        return EXIT_USAGE
    try:
        with open(a.sidecar, encoding="utf-8") as fh:
            parsed = parse_lines(fh)
    except OSError as exc:
        print(json.dumps({"error": f"cannot read the sidecar file: {type(exc).__name__}"}))
        return EXIT_USAGE
    ce = a.catchup_end_epoch if a.catchup_end_epoch is not None else parsed["catchup_end"]
    if ce is None:
        print(json.dumps({"verdict": "INSUFFICIENT", "notes": ["no catchup_end_ts in the file and no --catchup-end-epoch"]}, sort_keys=True))
        return EXIT_INSUFFICIENT
    res = envelope(parsed["samples"], ce, origin=a.origin_epoch, run_end=a.run_end_epoch, cap_mib=a.cap_mib, host_job_start=a.host_job_start_epoch,
                   first_send=a.first_send_epoch)
    res["bad_lines"] = parsed["bad_lines"]
    print(json.dumps(res, sort_keys=True, indent=1))
    return {"FIT": EXIT_FIT, "NO_FIT": EXIT_NOFIT}.get(res["verdict"], EXIT_INSUFFICIENT)


if __name__ == "__main__":
    sys.exit(main())
