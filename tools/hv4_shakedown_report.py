#!/usr/bin/env python3
"""hv4_shakedown_report: one command that evaluates the Haiku v4 (HV4) 24 h STUB shakedown. Counts and timings only. PAPER only.

Inputs (all read-only): the shakedown dir's decision logs (incl. SCAN scan_timing / tick_timing rows), the card sink, chain-credits.json,
chain-prefetch-spend.json, engine-trim-counters.jsonl, bots.json (line counts only), the arena feed status file and the tape directory's file names, and
optionally (a) a sample file written by the `sample` subcommand of this tool (per-minute chain-counter snapshots, process RSS, feed counters), (b) MiScusi job
logs (only exact marker lines are counted; no log content is printed) and (c) with --tape-first-rows a read-only tape scan for the first row of each
chain_unavailable pool (pool id and t_recv_ms only).

Pass checks: SHAKEDOWN-CHECKS-2.md (checks 1 to 10). Window rule: QP-RULING-1019-startup (a). Registration conditions from quant-proof: PR #755 comment 6276972823
(items 3 to 5), PR #745 (comments 6275482286 and 6276589135, item 4), PR #762 comment 6278428501 (memory plan), PR #764 comment 6278593034.

A missing, empty or misshaped input gives UNMEASURED for the checks that need it (with the error class in the notes), never FAIL, PASS or an aborted report.

What it never does: open an engine ledger (any path whose name contains "ledger" is refused), print a bots.json body, a runner status body, a card body, an
entry/sell row, or any P&L, fill price or outcome field. Decision rows are projected to a small allow-list the moment they are verified; the raw row is
dropped. StubModel trades are not evidence for any book: this report makes no profit claim.

Exit code is 0 always (argparse usage errors aside). The verdict is in the record: PASS only if every non-informational check is PASS and the run is
complete; FAIL if any check is FAIL; else INCOMPLETE.

Run:    python -I tools/hv4_shakedown_report.py --dir /data/mal/arena/_shakedown-hv4-1020 [--samples FILE] [--job-log FILE]... [--read-hours H] [--tape-first-rows]
Sample: python -I tools/hv4_shakedown_report.py sample --dir DIR --out FILE [--until ISO]   (run it as a MiScusi job)
Stdlib only: it imports nothing from tools/, so the runner's frozen files are not touched by it.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = "hv4_shakedown_report_v1"
SAMPLE_SCHEMA = "hv4_shakedown_sample_v1"
MIN_MS = 60_000
HOUR_MS = 3_600_000
GENESIS = "0" * 64
PERSONAS = ("CON", "BIG")  # lane G, in the runner's scan order (LANE_G_PERSONAS)
PASS, FAIL, UNM = "PASS", "FAIL", "UNMEASURED"

# thresholds: SHAKEDOWN-CHECKS-2.md, QP-RULING-1019-startup (a) and the QP comments. The cap is a constant: no flag changes it.
STALE_MAX_FRAC = 0.02
NULL_HOLDERS_MAX_FRAC = 0.10
FIRST_SEEN_MAX_MS = 10 * MIN_MS
CALLS_PER_HOUR_MAX = 1_500
CALLS_PER_DAY_MAX = 40_000
SCAN_P99_MAX, SCAN_MAX_MAX = 2_000, 5_000
EXIT_P99_MAX, EXIT_MAX_MAX = 2_000, 10_000
CATCHUP_CAP_MS = 10 * MIN_MS
TRIM_MS_BLOCK = 1_000
TRIM_GAP_MAX_MS = 2 * 300_000  # two housekeeping periods of tape time
V_MISSING_RATIO_BLOCK = 0.5
SLOPE_FROM_H = 2  # #755 item 3: the slope over hours 2..24
NONLINEAR_RATIO = 1.5  # PR #762: a second-half slope above 1.5 x the first-half slope voids the linear projection
EXPECT_END_DEFAULT = "2026-10-20T21:32:00Z"
REGISTER_LINE = b'{"lane_h":false,"n_bots":9}'
SELL_DROP_KEYS = ("no open position", "already has a sell")
JOBLOG_MARKERS = {"spend_state_reset": b"spend state unreadable", "state_write_failed": b"hv4_chain state write failed", "leakage_error": b"LeakageError"}
BLOCKED_WORDS = ("ledger", "tip-tape-archive")
BLOCKED_PREFIXES = ("/data/mal/blocks", "/var/lib/mal/sealed", "/data/mal/kalshi")
OUTPUT_FORBIDDEN_KEYS = ("pnl", "price", "fill", "outcome", "realized", "est_ret", "tokens_eq", "bankroll")
TAPE_NAME = re.compile(r"tape-(\d{4}-\d\d-\d\dT\d\d)\.jsonl(\.zst)?$")


class Refused(Exception):
    """A path or a field this tool is not allowed to read or print."""


# ------------------------------------------------------------------------------------------------------------------------------ small helpers
def canon(obj: Any) -> str:  # identical to tools.arena_paper.canon (a test cross-checks it when the runner stack is importable)
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), allow_nan=False)


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def iso(ms: int | float | None) -> str | None:
    return None if ms is None else datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s: str) -> int:
    return int(datetime.strptime(s.strip(), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp() * 1000)


def pct(sorted_vals: list[float], q: float) -> float | None:
    """Nearest-rank percentile of an ascending list, as the runner's own pct()."""
    return sorted_vals[max(0, math.ceil(q * len(sorted_vals)) - 1)] if sorted_vals else None


def guard(path: str | os.PathLike) -> Path:
    s = os.path.abspath(str(path))
    for cand in (s, os.path.realpath(s)):
        low = cand.lower()
        if any(w in low for w in BLOCKED_WORDS) or any(low == p or low.startswith(p + "/") for p in BLOCKED_PREFIXES):
            raise Refused("path refused by policy (ledger, blocks, sealed, kalshi and tip-tape-archive are never opened)")
    return Path(s)


def read_input(path: Path, inputs: list[dict]) -> bytes:
    data = guard(path).read_bytes()
    inputs.append({"path": str(path), "bytes": len(data), "sha256": sha256_bytes(data)})
    return data


def jsonl(data: bytes, drop_torn_tail: bool = True) -> tuple[list[dict], int]:
    """Parsed rows and the number of torn tail lines dropped (a live file may end in a partial line)."""
    lines = data.split(b"\n")
    torn = 0
    if lines and lines[-1] != b"":
        torn = 1
        if drop_torn_tail:
            lines = lines[:-1]
    out = []
    for ln in lines:
        if ln.strip():
            try:
                out.append(json.loads(ln))
            except ValueError:
                torn += 1
    return out, torn


def lsq_slope(points: list[tuple[float, float]]) -> float | None:
    """Least-squares slope of y on x (x in hours); None for fewer than 3 points or a zero span."""
    n = len(points)
    if n < 3:
        return None
    mx, my = sum(p[0] for p in points) / n, sum(p[1] for p in points) / n
    den = sum((p[0] - mx) ** 2 for p in points)
    return None if den == 0 else sum((p[0] - mx) * (p[1] - my) for p in points) / den


def chk(cid: str, name: str, status: str, numbers: dict, notes: list[str] | None = None, proxy: bool = False, informational: bool = False) -> dict:
    """informational: reported, but left out of the verdict (the runner persists nothing a file check could read)."""
    return {"id": cid, "name": name, "status": status, "proxy": proxy, "informational": informational, "numbers": numbers, "notes": notes or []}


def safe(cid: str, name: str, fn, *a):
    """A check whose input is missing or misshaped is UNMEASURED with the error class named; a policy refusal still aborts."""
    try:
        return fn(*a)
    except Refused:
        raise
    except Exception as e:  # noqa: BLE001
        return chk(cid, name, UNM, {"input_error": type(e).__name__}, [f"input error ({type(e).__name__}): this check is UNMEASURED; the rest of the report is unaffected"])


# ------------------------------------------------------------------------------------------------------------------------------ decision logs
class Logs:
    """Everything the checks need from the decision logs, as counts and the allow-listed fields of SCAN rows. Raw rows are never retained."""

    def __init__(self) -> None:
        self.arms: dict[str, dict] = {}
        self.kinds: dict[str, Counter] = defaultdict(Counter)
        self.starts: list[dict] = []
        self.scans: list[dict] = []
        self.refused: list[dict] = []
        self.as_of_refusals = 0
        self.leakage_mentions = 0
        self.scan_timing: list[tuple[int, int]] = []
        self.tick_timing: list[dict] = []
        self.sell_alert = 0
        self.sell_drop_rows = Counter()
        self.dup_rows = Counter()
        self.last_ms = 0
        self.skipped_dirs: list[str] = []
        self.torn_tails = 0
        self.unreadable_files = 0

    @property
    def have_rows(self) -> bool:
        return any(a["rows"] for a in self.arms.values())

    @property
    def have_scan_rows(self) -> bool:
        return bool(self.arms.get("SCAN", {}).get("rows")) and bool(self.scans)


def load_logs(root: Path, inputs: list[dict]) -> Logs:
    L = Logs()
    dec = root / "decisions"
    names = sorted(p.name for p in dec.iterdir() if p.is_dir()) if dec.is_dir() else []
    for name in names:
        if any(w in name.lower() for w in BLOCKED_WORDS):
            L.skipped_dirs.append("<refused-name>")
            continue
        prev, seq, ok, err, nrows = GENESIS, -1, True, None, 0
        seen: set[bytes] = set()
        for f in sorted((dec / name).glob("*.jsonl")):
            try:
                data = read_input(f, inputs)
            except OSError:
                L.unreadable_files += 1
                continue
            L.leakage_mentions += data.count(b"LeakageError")
            lines = data.split(b"\n")
            if data and not data.endswith(b"\n"):
                L.torn_tails += 1
                lines = lines[:-1]
            for ln in lines:
                if not ln.strip():
                    continue
                try:
                    row = json.loads(ln)
                    sha = row.pop("sha")
                except (ValueError, KeyError, AttributeError, TypeError):
                    ok, err = False, f"unreadable row after seq {seq}"
                    break
                if row.get("prev") != prev or row.get("seq") != seq + 1 or hashlib.sha256((prev + canon(row)).encode()).hexdigest() != sha:
                    ok, err = False, f"chain broken at seq {seq + 1}"
                    break
                prev, seq, nrows = sha, row["seq"], nrows + 1
                body = {k: v for k, v in row.items() if k not in ("seq", "prev", "sha")}
                key = hashlib.sha1(canon(body).encode()).digest()
                if key in seen:
                    L.dup_rows[name] += 1
                seen.add(key)
                project(L, name, row)
            if not ok:
                break
        L.arms[name] = {"rows": nrows, "chain_ok": ok, "chain_error": err, "head_seq": seq}
    L.starts.sort(key=lambda s: s["ms"])
    return L


def project(L: Logs, arm: str, row: dict) -> None:
    kind = row.get("kind")
    ms = row.get("ms") if isinstance(row.get("ms"), int) else 0
    L.kinds[arm][str(kind)] += 1
    L.last_ms = max(L.last_ms, ms)
    if kind == "sell_alert":
        L.sell_alert += 1
    elif kind == "order_refused" and row.get("side") == "sell":
        text = f"{row.get('reason')} {row.get('detail')}"
        for key in SELL_DROP_KEYS:
            if key in text:
                L.sell_drop_rows[key] += 1
    if arm != "SCAN":
        return
    if kind == "start":
        b = row.get("binding") if isinstance(row.get("binding"), dict) else {}
        L.starts.append({"ms": ms, "seq": row.get("seq"), "frozen_sha": row.get("frozen_sha"), "lane_h": row.get("lane_h"), "dirty": b.get("dirty"), "git_sha": b.get("git_sha")})
    elif kind == "scan":
        nt = row.get("n_tags") if isinstance(row.get("n_tags"), dict) else {}
        tagged = [(p[0], p[1]) for p in (row.get("tagged") or []) if isinstance(p, list) and len(p) == 2]
        elig = [e.get("pool") for e in (row.get("eligible") or []) if isinstance(e, dict)]
        L.scans.append({"ms": ms, "n_filter_pass": row.get("n_filter_pass"), "n_tags": {k: int(v) for k, v in nt.items()}, "n_board": int(row.get("n_board") or 0),
                        "n_cards": int(row.get("n_cards") or 0), "tagged": tagged, "eligible": elig})
    elif kind == "card_refused":
        L.refused.append({"ms": ms, "pool": row.get("pool"), "reasons": [str(r) for r in (row.get("reasons") or [])]})
    elif kind == "as_of_refusal":
        L.as_of_refusals += 1
    elif kind == "scan_timing":
        L.scan_timing.append((ms, int(row.get("scan_wall_ms") or 0)))
    elif kind == "tick_timing":
        d = row.get("exit_check_delay_ms") if isinstance(row.get("exit_check_delay_ms"), dict) else {}
        L.tick_timing.append({"minute_ms": int(row.get("minute_ms") or 0), "n_ticks": int(row.get("n_ticks") or 0), "p50": d.get("p50"), "p99": d.get("p99"), "max": d.get("max")})


def load_cards(root: Path, inputs: list[dict]) -> list[dict]:
    """The card sink, projected at once to {persona, setup type, holders null?, pool, as_of_ms}. Nothing else of a card is kept."""
    out: list[dict] = []
    cdir = root / "cards"
    for f in sorted(cdir.glob("*.jsonl")) if cdir.is_dir() else []:
        rows, _ = jsonl(read_input(f, inputs))
        for r in rows:
            c = r.get("card") if isinstance(r, dict) else None
            if not isinstance(c, dict):
                continue
            setup = c.get("setup") if isinstance(c.get("setup"), dict) else None
            risk = c.get("risk") if isinstance(c.get("risk"), dict) else {}
            out.append({"persona": setup.get("persona") if setup else None, "type": setup.get("type") if setup else None,
                        "holders_null": risk.get("holders_as_of_ms") is None, "pool": c.get("id"), "as_of_ms": c.get("as_of_ms")})
    return out


# ------------------------------------------------------------------------------------------------------------------------------ startup windows (QP-RULING-1019-startup (a))
def startup_windows(L: Logs) -> list[dict]:
    """Per start row: from the start row to the end of the first full tick_timing minute with exit_check_delay p99 <= 2,000 and max <= 10,000, capped at 10 min.
    closed = such a minute ended within the cap; over_cap = it did not (check 6 FAILs); open = the run has not yet reached the cap."""
    ticks = sorted(L.tick_timing, key=lambda t: t["minute_ms"])
    out = []
    for i, s in enumerate(L.starts):
        nxt = L.starts[i + 1]["ms"] if i + 1 < len(L.starts) else None
        cap_end = s["ms"] + CATCHUP_CAP_MS
        end = None
        for t in ticks:
            if t["minute_ms"] < s["ms"] or (nxt is not None and t["minute_ms"] >= nxt):
                continue  # a full minute starts at or after the start row
            if t["p99"] is not None and t["max"] is not None and t["p99"] <= EXIT_P99_MAX and t["max"] <= EXIT_MAX_MAX:
                end = t["minute_ms"] + MIN_MS
                break
        if end is not None and end <= cap_end:
            state = "closed"
        elif end is not None or L.last_ms >= cap_end:
            state, end = "over_cap", cap_end
        else:
            state, end = "open", max(L.last_ms, s["ms"]) + 1
        out.append({"label": f"start_{i}", "kind": "start", "start_ms": s["ms"], "end_ms": end, "state": state, "source": "QP-RULING-1019-startup (a)"})
    return out


# ------------------------------------------------------------------------------------------------------------------------------ checks 1 to 4 and persona counts
def attribute_refusals(L: Logs, process_first_scan, tape_first: dict[str, int]) -> dict:
    """Per persona: card attempts (every attempt is a tagged or board pool, so every card here is on a hot pool), chain_unavailable (and how many carried a
    setup tag), chain_stale and other refusals. A refused row has no persona: a tagged pool maps by order (the scan loop runs CON then BIG); an untagged pool
    is a board pool, which both personas share. For each chain_unavailable card two gap figures: the log gap (T minus the pool's first appearance in tagged /
    eligible / card_refused rows: a LOWER bound of the time the scanner had seen the pool) and the bound (T minus the later of the pool's first tape row and
    the process's first scan: an UPPER bound)."""
    per = {p: {"attempts": 0, "chain_unavailable": 0, "chain_unavailable_tagged": 0, "chain_stale": 0, "other_refused": 0} for p in PERSONAS}
    by_ms: dict[int, list[dict]] = defaultdict(list)
    for r in L.refused:
        by_ms[r["ms"]].append(r)
    matched, extra = 0, 0
    first_seen: dict[str, int] = {}
    for s in sorted(L.scans, key=lambda s: s["ms"]):
        for pool in [t[0] for t in s["tagged"]] + list(s["eligible"]) + [r["pool"] for r in by_ms.get(s["ms"], [])]:
            first_seen.setdefault(pool, s["ms"])
    cards = []
    for s in L.scans:
        for p in PERSONAS:
            per[p]["attempts"] += s["n_tags"].get(p, 0) + s["n_board"]
        tags_of: dict[str, list[str]] = defaultdict(list)
        for pool, pr in s["tagged"]:
            tags_of[pool].append(pr)
        idx: Counter = Counter()
        for r in by_ms.get(s["ms"], []):
            matched += 1
            prs = sorted((x for x in tags_of.get(r["pool"], []) if x in PERSONAS), key=PERSONAS.index)
            i = idx[r["pool"]]
            idx[r["pool"]] += 1
            if prs:
                targets, tagged = ([prs[i]] if i < len(prs) else []), True
                extra += 0 if i < len(prs) else 1
            else:
                targets, tagged = list(PERSONAS), False
            for t in targets:
                if "chain_unavailable" in r["reasons"]:
                    per[t]["chain_unavailable"] += 1
                    per[t]["chain_unavailable_tagged"] += int(tagged)
                if "chain_stale" in r["reasons"]:
                    per[t]["chain_stale"] += 1
                if not {"chain_unavailable", "chain_stale"} & set(r["reasons"]):
                    per[t]["other_refused"] += 1
            if "chain_unavailable" in r["reasons"]:
                T = s["ms"]
                fs = process_first_scan(T)
                floor = max(tape_first.get(r["pool"], 0), fs if fs is not None else T)
                cards.append({"pool": r["pool"], "T": T, "log_gap_ms": T - first_seen.get(r["pool"], T), "bound_ms": T - floor, "tape_row_found": r["pool"] in tape_first,
                              "first_appearance_is_the_refusal": first_seen.get(r["pool"]) == T})
    return {"per_persona": per, "refused_rows_matched_to_a_scan": matched, "refused_rows_without_scan_row": len(L.refused) - matched, "extra_rows_unattributed": extra, "unavailable_cards": cards}


def unavailable_pools(L: Logs) -> set[str]:
    return {r["pool"] for r in L.refused if "chain_unavailable" in r["reasons"] and isinstance(r["pool"], str)}


def first_tape_rows(pools: set[str], tape: Path, from_ms: int) -> tuple[dict[str, int], dict]:
    """Read-only tape scan: for each pool the smallest t_recv_ms of a row whose `pool` is that id. Only the pool id and t_recv_ms of matching rows are used.
    Files are scanned oldest first and the scan stops once every pool is found. Hour files that end before the process's warm-up could have started are skipped
    (a pool first seen there is bounded by the process's first scan instead)."""
    info: dict[str, Any] = {"files_scanned": 0, "files_failed": 0, "pools_asked": len(pools), "pools_found": 0}
    found: dict[str, int] = {}
    if not pools or not tape.is_dir():
        return found, info
    files = []
    for p in sorted(tape.iterdir()):
        m = TAPE_NAME.match(p.name)
        if m and parse_iso(m.group(1) + ":00:00Z") + HOUR_MS > from_ms:
            files.append(p)
    with tempfile.NamedTemporaryFile("w", suffix=".ids", delete=False) as f:
        f.write("\n".join(sorted(pools)) + "\n")
        ids = f.name
    try:
        for p in files:
            if len(found) == len(pools):
                break
            guard(p)
            z = None
            if p.name.endswith(".zst"):
                z = subprocess.Popen(["zstd", "-dc", "--", str(p)], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
                g = subprocess.Popen(["grep", "-a", "-F", "-f", ids], stdin=z.stdout, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
                z.stdout.close()
            else:
                g = subprocess.Popen(["grep", "-a", "-F", "-f", ids, "--", str(p)], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            for ln in g.stdout:
                try:
                    row = json.loads(ln)
                    pool, t = row.get("pool"), row.get("t_recv_ms")
                except (ValueError, AttributeError):
                    continue
                if pool in pools and isinstance(t, int) and not isinstance(t, bool):
                    found[pool] = min(found.get(pool, t), t)
            rc = g.wait()  # grep: 0 = matches, 1 = none, anything else = failure
            if z is not None and z.wait() != 0:
                rc = 2
            info["files_scanned"] += 1
            info["files_failed"] += int(rc not in (0, 1))
    finally:
        try:
            os.unlink(ids)
        except OSError:
            pass
    info["pools_found"] = len(found)
    return found, info


def eval_checks_1_to_4(L: Logs, cards: list[dict], job: dict, tape: Path | None, tape_scan: bool) -> tuple[list[dict], dict]:
    out = []
    leak = L.leakage_mentions + job.get("leakage_error", 0)
    if not L.have_rows:
        out.append(chk("1", "0 as_of_refusal rows and 0 LeakageError", UNM, {}, ["no decision rows"]))
    else:
        out.append(chk("1", "0 as_of_refusal rows and 0 LeakageError", PASS if L.as_of_refusals == 0 and leak == 0 else FAIL,
                       {"as_of_refusal_rows": L.as_of_refusals, "leakage_error_mentions_in_decision_logs": L.leakage_mentions, "leakage_error_lines_in_job_logs": job.get("leakage_error") if job else None},
                       [] if job else ["no --job-log given: LeakageError counted in the decision logs only"]))
    starts = L.starts

    def process_first_scan(T: int) -> int | None:
        prior = [s["ms"] for s in starts if s["ms"] <= T]
        lo = prior[-1] if prior else None
        return min((x["ms"] for x in L.scans if lo is not None and x["ms"] >= lo), default=None)

    tape_first, tape_info = {}, {"scan": "not requested (pass --tape-first-rows)"}
    pools = unavailable_pools(L)
    if tape_scan and tape is not None and pools and starts:
        tape_first, tape_info = first_tape_rows(pools, tape, starts[0]["ms"] - 7 * HOUR_MS)
    att = attribute_refusals(L, process_first_scan, tape_first)
    per = att["per_persona"]
    fr = {p: (per[p]["chain_stale"] / per[p]["attempts"] if per[p]["attempts"] else None) for p in PERSONAS}
    st = UNM if (not L.have_scan_rows or any(v is None for v in fr.values())) else (PASS if all(v <= STALE_MAX_FRAC for v in fr.values()) else FAIL)
    out.append(chk("2", "chain_stale <= 2% of cards on hot pools, CON and BIG separately", st,
                   {p: {"attempts": per[p]["attempts"], "chain_stale": per[p]["chain_stale"], "frac": None if fr[p] is None else round(fr[p], 5)} for p in PERSONAS},
                   ["denominator = card attempts per persona = n_tags[persona] + n_board summed over scans; every attempt is a tagged or board pool (hot by the check's definition)"]))
    uc = att["unavailable_cards"]
    seen_over = sum(1 for c in uc if c["log_gap_ms"] > FIRST_SEEN_MAX_MS)
    unbounded = sum(1 for c in uc if c["bound_ms"] > FIRST_SEEN_MAX_MS)
    if not L.have_scan_rows:
        st3 = UNM
    elif seen_over:
        st3 = FAIL
    elif unbounded:
        st3 = UNM
    else:
        st3 = PASS
    n3 = {"chain_unavailable_cards": len(uc), "pool_visible_in_logs_more_than_10min_earlier": seen_over, "cards_with_gap_bound_over_10min": unbounded,
          "max_gap_bound_s": round(max((c["bound_ms"] for c in uc), default=0) / 1000, 1), "cards_whose_first_log_appearance_is_the_refusal": sum(c["first_appearance_is_the_refusal"] for c in uc),
          "tape_first_row_scan": tape_info,
          "cards": [{"scan": iso(c["T"]), "log_gap_s": round(c["log_gap_ms"] / 1000), "bound_s": round(c["bound_ms"] / 1000), "tape_row_found": c["tape_row_found"]} for c in uc]}
    out.append(chk("3", "every chain_unavailable card's pool first seen within 10 min before that scan", st3, n3,
                   ["measured only when the gap is bounded: bound = scan T minus the later of the pool's first arena-tape row and the process's first scan T (an upper bound on the gap); "
                    "FAIL only when the logs show the pool at an earlier scan more than 10 min before. Cards whose bound is over 10 min stay UNMEASURED unless a replay of the "
                    "filter-pass set resolves them. Scan rows list no filter-pass pools, so the log gap alone proves nothing."]))
    nh: dict[str, dict] = {p: {"cards": 0, "holders_null": 0} for p in PERSONAS}
    types: dict[str, Counter] = {p: Counter() for p in PERSONAS}
    for c in cards:
        for p in ([c["persona"]] if c["persona"] in PERSONAS else (list(PERSONAS) if c["persona"] is None else [])):
            nh[p]["cards"] += 1
            nh[p]["holders_null"] += int(c["holders_null"])
            if c["type"]:
                types[p][c["type"]] += 1
    frn = {p: (nh[p]["holders_null"] / nh[p]["cards"] if nh[p]["cards"] else None) for p in PERSONAS}
    st = UNM if any(v is None for v in frn.values()) else (PASS if all(v <= NULL_HOLDERS_MAX_FRAC for v in frn.values()) else FAIL)
    n_scan_cards = sum(s["n_cards"] for s in L.scans)
    out.append(chk("4", "cards with a null holders block <= 10%, CON and BIG separately", st,
                   {p: {**nh[p], "frac": None if frn[p] is None else round(frn[p], 5)} for p in PERSONAS} | {"cards_built_per_scan_rows": n_scan_cards, "cards_in_sink": len(cards)},
                   ["denominator = cards in the card sink (eligible cards, after the risk gate); a board card (no setup) counts for both personas; null holders = risk.holders_as_of_ms is null"]))
    counts = {"scans": len(L.scans), "per_persona": {p: {**per[p], "cards_in_sink": nh[p]["cards"], "null_holders_cards": nh[p]["holders_null"], "setups_by_type_in_sink": dict(types[p]),
                                                         "stub_llm_calls": sum(n for a, kc in L.kinds.items() if a.startswith(p + "-") for k, n in kc.items() if k == "llm_call"),
                                                         "x_arm_entries": L.kinds.get(p + "-X", Counter()).get("entry", 0), "x_arm_exits": L.kinds.get(p + "-X", Counter()).get("sell", 0)}
                                                     for p in PERSONAS},
              "risk_agent_llm_calls": L.kinds.get("RISK", Counter()).get("llm_call", 0),
              "refused_rows_without_scan_row": att["refused_rows_without_scan_row"], "extra_rows_unattributed": att["extra_rows_unattributed"]}
    return out, counts


# ------------------------------------------------------------------------------------------------------------------------------ check 6 timing
def eval_timing(L: Logs, windows: list[dict]) -> dict:
    def excluded(a: int, b: int) -> bool:
        return any(a < w["end_ms"] and b > w["start_ms"] for w in windows)

    if not L.scan_timing and not L.tick_timing:
        return chk("6", "timing", UNM, {}, ["no scan_timing or tick_timing rows"])
    scans_all = sorted(w for _, w in L.scan_timing)
    scans = sorted(w for ms, w in L.scan_timing if not excluded(ms, ms + 1))
    ticks = [t for t in L.tick_timing if not excluded(t["minute_ms"], t["minute_ms"] + MIN_MS)]
    ticks_ex = sorted((t for t in L.tick_timing if excluded(t["minute_ms"], t["minute_ms"] + MIN_MS)), key=lambda t: t["minute_ms"])
    tot = sum(t["n_ticks"] for t in ticks)
    over = [t for t in ticks if (t["max"] or 0) > EXIT_P99_MAX]
    lower = sum(math.ceil(t["n_ticks"] / 2) if (t["p50"] or 0) > EXIT_P99_MAX else 1 for t in over)
    upper = sum(t["n_ticks"] for t in over)
    limit = math.floor(0.01 * tot)
    p99_status = PASS if upper <= limit else (FAIL if lower > 0.01 * tot else UNM)
    mx = max((t["max"] or 0 for t in ticks), default=None)
    scan_p99, scan_max = pct(scans, 0.99), (scans[-1] if scans else None)
    scan_ok = None if not scans else (scan_p99 <= SCAN_P99_MAX and scan_max <= SCAN_MAX_MAX)
    # minutes with no tick_timing row outside the windows: the process was down (between a stop and the next start) or stalled
    mins = sorted({t["minute_ms"] for t in L.tick_timing})
    down = []
    for s in L.starts[1:]:
        before = [m for m in mins if m < s["ms"]]
        if before:
            down.append((before[-1] + MIN_MS, s["ms"]))
    missing = 0
    if L.starts and mins:
        m = L.starts[0]["ms"] // MIN_MS * MIN_MS
        have = set(mins)
        while m <= mins[-1]:
            if m not in have and not excluded(m, m + MIN_MS) and not any(a < m + MIN_MS and m < b for a, b in down):
                missing += 1
            m += MIN_MS
    over_cap = [w["label"] for w in windows if w["state"] == "over_cap"]
    open_w = [w["label"] for w in windows if w["state"] == "open"]
    if over_cap or scan_ok is False or (mx is not None and mx > EXIT_MAX_MAX) or p99_status == FAIL:
        st = FAIL
    elif not scans or not ticks or open_w or missing or p99_status == UNM:
        st = UNM
    else:
        st = PASS
    wins_out = {}
    for w in windows:
        rows = [t for t in ticks_ex if t["minute_ms"] + MIN_MS > w["start_ms"] and t["minute_ms"] < w["end_ms"]]
        wins_out[w["label"]] = {"window": [iso(w["start_ms"]), iso(w["end_ms"])], "state": w["state"], "source": w["source"], "length_s": round((w["end_ms"] - w["start_ms"]) / 1000, 1),
                                "scans_excluded": sum(1 for ms, _ in L.scan_timing if w["start_ms"] <= ms < w["end_ms"]),
                                "minutes": [{"minute": iso(t["minute_ms"]), "n_ticks": t["n_ticks"], "p50": t["p50"], "p99": t["p99"], "max": t["max"]} for t in rows]}
    numbers = {"scan_wall_ms": {"n": len(scans), "p99": scan_p99, "max": scan_max, "limits": [SCAN_P99_MAX, SCAN_MAX_MAX], "scans_over_2000": sum(1 for w in scans if w > SCAN_P99_MAX),
                                "unfiltered_n": len(scans_all), "unfiltered_max": scans_all[-1] if scans_all else None, "percentile": "nearest rank, sorted[ceil(0.99 n) - 1]"},
               "exit_check_delay_ms": {"minutes": len(ticks), "ticks": tot, "max_of_minute_max": mx, "max_limit": EXIT_MAX_MAX, "minutes_with_max_over_2000": len(over),
                                       "ticks_over_2000_between": [lower, upper], "one_percent_of_ticks": limit, "p99_status": p99_status, "limits": [EXIT_P99_MAX, EXIT_MAX_MAX]},
               "windows": wins_out, "catchup_cap_s": CATCHUP_CAP_MS // 1000, "windows_over_cap": over_cap, "windows_open": open_w,
               "excluded_minutes": len(ticks_ex), "minutes_with_no_tick_timing_row_outside_windows": missing}
    notes = ["windows: from each start row to the end of the first full tick_timing minute with p99 <= 2,000 and max <= 10,000, capped at 10 min (QP-RULING-1019-startup (a)); a catch-up past the cap FAILs",
             "p99 of the exit-check delay is not recoverable from per-minute rows (a minute's p99 is its max at about 60 ticks): the count of ticks above 2,000 ms is bounded "
             "between one per over-limit minute and all of its ticks; PASS only when even the upper bound is within 1% of ticks, FAIL when the lower bound is over it",
             "the stub does not measure run-mode scan time with Haiku calls inside the scan; that is stated, not assumed; trim minutes are NOT excluded (QP #755 item 2)"]
    if missing:
        notes.append("minutes with no tick_timing row outside the windows make this check UNMEASURED")
    return chk("6", "timing: scan wall p99<=2000 max<=5000; exit-check delay p99<=2000 max<=10000, outside the startup windows / listed feed gaps", st, numbers, notes)


# ------------------------------------------------------------------------------------------------------------------------------ samples, check 5, RSS
def load_samples(path: Path | None, inputs: list[dict]) -> tuple[list[dict], int, str | None]:
    """Valid sample lines sorted by time, the number of error lines the sampler wrote, and a note when the file could not be used."""
    if path is None:
        return [], 0, None
    try:
        rows, _ = jsonl(read_input(Path(path), inputs))
    except OSError as e:
        return [], 0, f"samples file unreadable ({type(e).__name__})"
    good, errs = [], 0
    for r in rows:
        if not isinstance(r, dict) or r.get("schema") != SAMPLE_SCHEMA or not isinstance(r.get("t_ms"), int):
            continue
        if "error" in r:
            errs += 1
            continue
        for k in ("chain_by_day", "spend", "feed"):
            if not isinstance(r.get(k), dict):
                r[k] = None
        if not isinstance(r.get("procs"), list):
            r["procs"] = []
        good.append(r)
    return sorted(good, key=lambda r: r["t_ms"]), errs, None


def counter_total(s: dict) -> int | None:
    by = s.get("chain_by_day")
    if not isinstance(by, dict):
        return None
    try:
        return sum(int(v) for v in by.values())
    except (TypeError, ValueError):
        return None


def rolling_hours(samples: list[dict], origin_ms: int | None) -> tuple[list[dict], int]:
    """For each sample time t: the calls in (t - 1 h, t] as [lo, hi]. The counter is monotone, so C(t - 1 h) lies between the last sample at or before it and the
    next one; with a fresh root the counter was 0 at origin_ms. hi = C(t) - C(last sample <= t - 1 h) is the conservative bound used for the verdict."""
    pts, decreases, run_max = [], 0, 0
    for s in samples:
        c = counter_total(s)
        if c is None:
            continue
        if c < run_max:
            decreases += 1
        run_max = max(run_max, c)
        pts.append((s["t_ms"], run_max))
    out = []
    for i, (t, c) in enumerate(pts):
        a = t - HOUR_MS
        before = [(tt, cc) for tt, cc in pts[: i + 1] if tt <= a]
        if before:
            lo_c = before[-1][1]
            nxt = [cc for tt, cc in pts if tt >= a]
            hi_c = nxt[0] if nxt else c
        elif origin_ms is not None and a >= origin_ms:
            lo_c, hi_c = 0, pts[0][1]
        elif origin_ms is not None:
            lo_c, hi_c = 0, 0  # the window starts before the run: the whole count so far
        else:
            continue
        out.append({"end_ms": t, "hi": c - lo_c, "lo": max(0, c - hi_c)})
    return out, decreases


def early_spend_bounds(samples: list[dict], run_start_ms: int | None) -> tuple[dict[int, int], dict]:
    """Rolling-hour bounds for the windows that begin before the first sample (QP #764 comment 6279001927 item 9). While the spend file still held the whole run, the
    chain counter equalled its last-hour spend (|diff| <= 1, a read race): every chain call was a prefetch call, which is also what hv4_prefetch does (the scan only reads
    the store). Then calls in (t - 1 h, t] <= sum_last_hour(t) + the largest bucket held at the previous sample (the minute bucket that straddles t - 1 h was still in the
    file). The counter supplies the totals and the spend file only splits them in time, so the bound is not the prefetcher's file alone."""
    info: dict[str, Any] = {"applies": False, "agreeing_samples": 0, "first_divergence": None, "windows": 0, "max_bound": None}
    if run_start_ms is None or not samples:
        return {}, info
    t_first = samples[0]["t_ms"]
    agree, div = 0, None
    for s in samples:
        c, sp = counter_total(s), s.get("spend")
        if c is None or not isinstance(sp, dict) or not isinstance(sp.get("sum_last_hour"), (int, float)):
            break
        if abs(c - sp["sum_last_hour"]) <= 1:
            agree += 1
        else:
            div = s["t_ms"]
            break
    info.update({"agreeing_samples": agree, "first_divergence": iso(div) if div else None})
    if agree < 2 or t_first - run_start_ms >= HOUR_MS:
        return {}, info  # the file did not demonstrably hold the whole run at the first samples: no early bound
    bounds: dict[int, int] = {}
    for i, s in enumerate(samples):
        t = s["t_ms"]
        if t - HOUR_MS >= t_first:
            break  # a sample exists before this window starts: the counter alone bounds it
        sp = s.get("spend")
        prev = samples[i - 1].get("spend") if i else sp
        if isinstance(sp, dict) and isinstance(prev, dict) and isinstance(sp.get("sum_last_hour"), (int, float)):
            bounds[t] = int(sp["sum_last_hour"]) + int(prev.get("max_bucket") or 0)
    info.update({"applies": True, "windows": len(bounds), "max_bound": max(bounds.values(), default=None)})
    return bounds, info


def eval_check_5(root: Path, samples: list[dict], starts: list[dict], inputs: list[dict], job: dict, fresh: bool) -> dict:
    notes: list[str] = []
    numbers: dict[str, Any] = {}
    cc_path = root / "chain-credits.json"
    sp_path = root / "chain-prefetch-spend.json"
    by_day: dict[str, int] = {}
    if cc_path.exists():
        try:
            doc = json.loads(read_input(cc_path, inputs))
            by_day = {k: int(v) for k, v in doc["by_day"].items()}
            numbers["chain_credits_schema"] = doc.get("schema")
            numbers["helius_credits_by_day"] = {k: int(v) for k, v in doc.get("credits_by_day", {}).items()}
            numbers["chain_credits_saved"] = iso(doc.get("saved_ms"))
        except (ValueError, KeyError, TypeError, AttributeError, OSError) as e:
            by_day = {}
            notes.append(f"chain-credits.json is unreadable or misshaped ({type(e).__name__})")
    numbers["calls_by_utc_day"] = by_day
    numbers["utc_day_note"] = "a stub does not cover a full UTC day on either end (it starts and ends mid-day): per-day figures are partial days, so a pass is weaker than a full-day pass"
    day_bad = [d for d, n in by_day.items() if n > CALLS_PER_DAY_MAX]
    spend_buckets = None
    if sp_path.exists():
        try:
            sd = json.loads(read_input(sp_path, inputs))
            spend_buckets = [[int(a), int(b)] for a, b in sd["spent"]]
            numbers["prefetch_spend_file"] = {"schema": sd.get("schema"), "calls_in_file_last_hour": sum(b for _, b in spend_buckets), "buckets": len(spend_buckets),
                                              "max_bucket": max((b for _, b in spend_buckets), default=0)}
        except (ValueError, KeyError, TypeError, AttributeError, OSError) as e:
            notes.append(f"chain-prefetch-spend.json is unreadable or misshaped ({type(e).__name__})")
    hour_status = UNM
    if samples and counter_total(samples[0]) is not None:
        t_first = samples[0]["t_ms"]
        rh, dec = rolling_hours(samples, starts[0]["ms"] if (starts and fresh) else None)
        eb, einfo = early_spend_bounds(samples, starts[0]["ms"] if (starts and fresh) else None)
        for r in rh:
            if r["end_ms"] in eb:
                r["hi"] = max(r["lo"], min(r["hi"], eb[r["end_ms"]]))
        numbers["early_hours_spend_bound"] = einfo
        numbers["samples"] = {"n": len(samples), "first": iso(t_first), "last": iso(samples[-1]["t_ms"]), "max_gap_s": round(max((b["t_ms"] - a["t_ms"] for a, b in zip(samples, samples[1:])), default=0) / 1000),
                              "counter_decreases": dec, "first_sample_s_after_first_start": round((t_first - starts[0]["ms"]) / 1000) if starts else None}
        hi_max = max((r["hi"] for r in rh), default=None)
        lo_max = max((r["lo"] for r in rh), default=None)
        numbers["rolling_hour_calls"] = {"max_upper_bound": hi_max, "max_lower_bound": lo_max, "limit": CALLS_PER_HOUR_MAX, "windows": len(rh),
                                         "at": iso(max(rh, key=lambda r: r["hi"])["end_ms"]) if rh else None}

        def crossing(t_ref: int) -> dict:
            w = [r for r in rh if r["end_ms"] - HOUR_MS < t_ref <= r["end_ms"]]
            return {"instant": iso(t_ref), "windows": len(w), "max_calls_upper_bound": max((r["hi"] for r in w), default=None), "max_calls_lower_bound": max((r["lo"] for r in w), default=None)}

        lo_t, hi_t = (starts[0]["ms"] if starts else samples[0]["t_ms"]), samples[-1]["t_ms"]
        mids = list(range((lo_t // 86_400_000 + 1) * 86_400_000, hi_t + 1, 86_400_000))
        numbers["hour_crossing_00_00Z"] = [crossing(m) for m in mids]
        numbers["hour_crossing_restart"] = [crossing(s["ms"]) for s in starts[1:]]
        both = []
        for s in starts[1:]:
            pre = [x for x in samples if x["t_ms"] < s["ms"] and counter_total(x) is not None]
            post = [x for x in samples if x["t_ms"] > s["ms"] and counter_total(x) is not None]
            both.append({"restart": iso(s["ms"]), "last_sample_before": counter_total(pre[-1]) if pre else None, "first_sample_after": counter_total(post[0]) if post else None,
                         "reloaded_without_reset": bool(pre and post and counter_total(post[0]) >= counter_total(pre[-1]))})
        numbers["both_processes_counted"] = {"counter_decreases_in_samples": dec, "per_restart": both,
                                             "meaning": "the counter persists its per-UTC-day count and the restarted process reloads it; this is measured, per restart, by the first sample after it being >= the last sample before it"}
        measured_cross = all(c["windows"] > 0 for c in numbers["hour_crossing_00_00Z"] + numbers["hour_crossing_restart"]) and all(b["reloaded_without_reset"] for b in both)
        totals = [x for x in (counter_total(s) for s in samples) if x is not None]
        slack = max((b - a for a, b in zip(totals, totals[1:])), default=0)  # a window ending between two samples exceeds the sampled windows by at most one inter-sample step
        numbers["rolling_hour_calls"]["between_samples_slack"] = slack
        if hi_max is not None and hi_max + slack <= CALLS_PER_HOUR_MAX and dec == 0 and measured_cross:
            hour_status = PASS
        elif lo_max is not None and lo_max > CALLS_PER_HOUR_MAX:
            hour_status = FAIL
        else:
            notes.append("rolling-hour calls are not fully bounded by the samples (upper bound over the limit, a missing crossing window, a counter decrease or a restart that did not reload)")
        if starts and not fresh:
            notes.append("the first start row has seq > 0: the root is not fresh, so the counter's origin is unknown and windows before the first sample are not bounded")
    else:
        notes.append("no usable --samples file: the runner records no per-hour chain-call series (chain-credits.json holds per-UTC-day counts only, the spend file only the last hour). "
                     "Rolling hours are UNMEASURED; run the `sample` subcommand as a MiScusi job to measure them")
    day_status = FAIL if day_bad else (PASS if by_day else UNM)
    n_logs = job.get("logs_read", 0) if job else 0
    numbers["error_counters"] = {"spend_state_reset": job.get("spend_state_reset") if n_logs else None, "state_write_errors": job.get("state_write_failed") if n_logs else None,
                                 "spend_write_errors": "not measured (no log line, not persisted)", "job_logs_read": n_logs, "job_logs_missing": job.get("logs_missing", 0) if job else 0,
                                 "basis": "spend_state_reset and state_write_errors are WARNING lines (hv4_prefetch / hv4_chain) that reach the job log through the last-resort handler; pass the log of every process"}
    logs_ok = n_logs >= max(1, len(starts))
    if not logs_ok:
        notes.append(f"spend_state_reset and state_write_errors need one --job-log per process start ({len(starts)} starts, {n_logs} logs read): UNMEASURED")
    elif job.get("spend_state_reset") or job.get("state_write_failed"):
        day_status = FAIL
        notes.append("a job log shows a spend-state reset or a chain state-write failure")
    if spend_buckets is not None and any(b == CALLS_PER_HOUR_MAX for _, b in spend_buckets):
        notes.append("a spend bucket of exactly 1,500 is the signature of a reset spend file (_load_spend books a full hour); look at it")
    status = FAIL if FAIL in (hour_status, day_status) else (PASS if hour_status == PASS and day_status == PASS and logs_ok else UNM)
    numbers["status_parts"] = {"rolling_hour": hour_status, "per_utc_day": day_status, "error_counters_measured": logs_ok}
    return chk("5", "chain calls <= 1,500 per rolling hour and <= 40,000 per UTC day, from the chain counter", status, numbers, notes)


def mb(v: Any) -> float | None:
    return None if v is None else round(float(v), 1)


def eval_memory(samples: list[dict], starts: list[dict], windows: list[dict], rss_points: list[tuple[int, float]], memory_max_cli: float | None, host_cap_mb: float | None,
                read_hours: float | None, unplanned: bool) -> dict:
    """Hourly RSS, per-process slope over stub hours 2.. outside the startup windows, peak, the read-length projection and the PR #762 memory-plan rule."""
    series: dict[str, list[tuple[int, float, float | None]]] = defaultdict(list)  # key -> (t_ms, rss_mb, cgroup_mb)
    peaks, cg_peaks, cg_max, oom = [], [], [], []
    for s in samples:
        for p in s.get("procs") or []:
            if isinstance(p, dict) and isinstance(p.get("rss_mb"), (int, float)):
                series[f"pid{p.get('pid')}"].append((s["t_ms"], float(p["rss_mb"]), p.get("cg_current_mb")))
                peaks += [v for v in (p.get("hwm_mb"),) if isinstance(v, (int, float))]
                cg_peaks += [v for v in (p.get("cg_peak_mb"),) if isinstance(v, (int, float))]  # includes page cache: reported, never compared with MemoryMax
                if isinstance(p.get("cg_max_mb"), (int, float)):
                    cg_max.append(float(p["cg_max_mb"]))
                if isinstance(p.get("cg_oom_kill"), (int, float)):
                    oom.append(int(p["cg_oom_kill"]))
    source = "runner process VmRSS from the sample file"
    if not series and rss_points:
        series["cli"] = [(t, v, None) for t, v in sorted(rss_points)]
        source = "figures supplied on the command line (--rss-point)"
    if not series:
        return chk("QP-M1", "RSS hourly, slope, peak, read-length projection, memory plan (A)/(B)", UNM, {"rss_hourly": "UNMEASURED by the runner (it records no RSS)"},
                   ["run the `sample` subcommand as a MiScusi job, or pass --rss-point ISO=MB (MiScusi job memory) at least three times"])
    t0 = starts[0]["ms"] if starts else min(p[0] for v in series.values() for p in v)
    wins = [(w["start_ms"], w["end_ms"]) for w in windows]
    notes = ["slope = least squares per process over samples at or after the first start + 2 h and outside every startup window (a restart resets RSS, so processes are never pooled); the conservative figure is the largest",
             "extrapolation, not a measurement; an OOM kill or an unplanned start is a stub failure (QP #755 item 3)"]
    segs, slopes, after, hourly, nonlinear = {}, [], [], [], []
    for key, pts in series.items():
        pts.sort()
        steady = [(t, v) for t, v, _ in pts if t >= t0 + SLOPE_FROM_H * HOUR_MS and not any(a <= t < b for a, b in wins)]
        enough = len(steady) >= 3 and steady[-1][0] - steady[0][0] >= 2 * HOUR_MS
        sl = lsq_slope([((t - steady[0][0]) / HOUR_MS, v) for t, v in steady]) if enough else None
        h1 = h2 = None
        if enough:
            mid = (steady[0][0] + steady[-1][0]) / 2
            a_, b_ = [(t, v) for t, v in steady if t <= mid], [(t, v) for t, v in steady if t > mid]
            h1 = lsq_slope([((t - a_[0][0]) / HOUR_MS, v) for t, v in a_]) if len(a_) >= 3 else None
            h2 = lsq_slope([((t - b_[0][0]) / HOUR_MS, v) for t, v in b_]) if len(b_) >= 3 else None
            if h1 is not None and h2 is not None and h1 > 0 and h2 > NONLINEAR_RATIO * h1:
                nonlinear.append(key)
        pos = [(t, v) for t, v, _ in pts if not any(a <= t < b for a, b in wins)]
        segs[key] = {"samples": len(pts), "first": iso(pts[0][0]), "last": iso(pts[-1][0]), "rss_after_drain_mb": mb(pos[0][1]) if pos else None, "rss_last_mb": mb(pts[-1][1]),
                     "rss_max_mb": mb(max(v for _, v, _ in pts)), "slope_mb_per_h": mb(sl), "first_half_slope": mb(h1), "second_half_slope": mb(h2), "steady_samples": len(steady),
                     "cgroup_last_mb": mb(pts[-1][2])}
        if sl is not None:
            slopes.append(sl)
        if pos:
            after.append(pos[0][1])
        byh: dict[int, tuple[int, float, float | None]] = {}
        for t, v, cg in pts:
            byh[t // HOUR_MS * HOUR_MS] = (t, v, cg)
        hourly += [{"hour": iso(h), "segment": key, "rss_mb": mb(v), "cgroup_mb": mb(cg)} for h, (t, v, cg) in sorted(byh.items())]
    last_key = max(series, key=lambda k: series[k][-1][0])
    cur = series[last_key][-1][1]
    peak = max([v for pts in series.values() for _, v, _ in pts] + peaks)
    elapsed_h = (series[last_key][-1][0] - t0) / HOUR_MS
    slope = max(slopes) if slopes else None
    rss0 = max(after) if after else None
    memory_max = max(cg_max) if cg_max else None
    refused = None
    if memory_max_cli is not None and memory_max is not None and abs(memory_max_cli - memory_max) > 1:
        refused = f"--memory-max-mb {memory_max_cli} differs from the sampled cgroup memory.max {memory_max}: refused"
    elif memory_max is None:
        memory_max = memory_max_cli
    plan: dict[str, Any] = {"read_hours": read_hours, "memory_max_mb": memory_max, "memory_max_from": "sampled cgroup memory.max" if cg_max else ("--memory-max-mb" if memory_max_cli else None),
                            "host_cap_mb_for_plan_A": host_cap_mb, "nonlinear_segments": nonlinear}
    status = UNM
    if refused:
        notes.append(refused)
    elif slope is not None and rss0 is not None and memory_max is not None and read_hours is not None:
        a_req = 1.5 * (rss0 + slope * read_hours)
        hrs_to_max = (memory_max - rss0) / slope if slope > 0 else None
        cadence = min(24.0, (memory_max - rss0) / (2 * slope)) if slope > 0 else 24.0
        plan.update({"projection_mb_current_plus_slope_x_remaining": mb(cur + slope * max(0.0, read_hours - elapsed_h)), "plan_A_required_memory_max_mb": mb(a_req),
                     "projected_hours_to_memory_max": None if hrs_to_max is None else round(hrs_to_max, 1), "plan_B_restart_cadence_h": round(max(0.0, cadence), 1),
                     "plan_B_cost": "each restart books every recovered position as an excluded engine_restart row; a book above 5% excluded is NOT_DECIDABLE; open positions per restart are not measurable with StubModel"})
        if nonlinear:
            plan["implied"] = "B at 24 h (mandatory: a second-half slope above 1.5 x the first-half slope voids the linear projection)"
        elif host_cap_mb is None:
            plan["implied"] = "undetermined: pass --host-cap-mb (the MemoryMax the book job can get beside the other planned jobs)"
        else:
            plan["implied"] = "A" if a_req <= host_cap_mb else "B"
        status = PASS
    else:
        notes.append("not enough for a verdict: needs a slope per process (>= 3 samples over >= 2 h after stub hour 2), a post-drain RSS, a MemoryMax (sampled cgroup value) and --read-hours")
    if oom and max(oom) > 0:
        status = FAIL
        notes.append("the cgroup recorded an oom_kill: an OOM kill is a stub failure")
    elif not oom:
        notes.append("oom_kill was not sampled (older sampler): an OOM is detected only through an unplanned start or a missing process")
    if memory_max is not None and peak >= memory_max:
        status = FAIL
    if unplanned:
        status = FAIL
    if status == PASS and (max(p[-1][0] for p in series.values()) - min(p[0][0] for p in series.values())) < 20 * HOUR_MS:
        status = UNM
        notes.append("fewer than 20 h of samples")
    out = {"source": source, "segments": segs, "peak_mb": mb(peak), "current_mb": mb(cur), "elapsed_h_since_first_start": round(elapsed_h, 2), "slope_mb_per_h_conservative_max_of_segments": mb(slope),
           "rss_after_drain_mb": mb(rss0), "cgroup_peak_mb_incl_page_cache": mb(max(cg_peaks)) if cg_peaks else None, "oom_kill_max": max(oom) if oom else None, "plan": plan, "hourly": hourly}
    return chk("QP-M1", "RSS hourly, slope, peak, read-length projection, memory plan (A)/(B)", status, out, notes)


# ------------------------------------------------------------------------------------------------------------------------------ trim counters (QP #745 / #755)
CUMULATIVE = ("hist_trimmed_prints", "rows_below_trim_floor", "refused_history_trimmed", "rows_dup", "filter_v_missing", "filter_v_band", "v_checked_pools", "v_missing_pools", "trim_no")


def segment_ends(L: Logs) -> list[int]:
    """The end of each process: for the last start the last decision row; for an earlier one the end of the last tick_timing minute before the next start row."""
    ticks = sorted(L.tick_timing, key=lambda t: t["minute_ms"])
    ends = []
    for i, s in enumerate(L.starts):
        if i + 1 < len(L.starts):
            nxt = L.starts[i + 1]["ms"]
            mine = [t["minute_ms"] + MIN_MS for t in ticks if s["ms"] <= t["minute_ms"] + MIN_MS - 1 and t["minute_ms"] < nxt]
            ends.append(max(mine) if mine else s["ms"])
        else:
            ends.append(L.last_ms)
    return ends


def eval_trim(root: Path, inputs: list[dict], n_starts: int, feed_gaps: list[dict], seg_ends: list[int] | None = None) -> list[dict]:
    p = root / "engine-trim-counters.jsonl"
    if not p.exists():
        return [chk("QP-T1", "engine-trim-counters.jsonl present", UNM, {}, ["file not found"])]
    rows, torn = jsonl(read_input(p, inputs))
    rows = [r for r in rows if isinstance(r, dict) and r.get("schema") == "hv4_engine_trim_v1"]
    seg: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        seg[str(r.get("engine_id"))].append(r)
    mono_bad = []
    for eid, rs in seg.items():
        for a, b in zip(rs, rs[1:]):
            if any((b.get(k) or 0) < (a.get(k) or 0) for k in CUMULATIVE) or (b.get("tape_ms") or 0) < (a.get("tape_ms") or 0):
                mono_bad.append(eid)
                break
    gaps, gap_bad = {}, []
    for eid, rs in seg.items():
        worst = (0, None, None)
        for a, b in zip(rs, rs[1:]):
            g = (b.get("tape_ms") or 0) - (a.get("tape_ms") or 0)
            if g > worst[0]:
                worst = (g, a.get("tape_ms"), b.get("tape_ms"))
            if g > TRIM_GAP_MAX_MS and not any(w["start_ms"] <= (b.get("tape_ms") or 0) and (a.get("tape_ms") or 0) <= w["end_ms"] for w in feed_gaps):
                gap_bad.append({"engine_id": eid, "from": iso(a.get("tape_ms")), "to": iso(b.get("tape_ms")), "gap_s": g // 1000})
        gaps[eid] = {"max_gap_s": worst[0] // 1000, "between": [iso(worst[1]), iso(worst[2])]}
    # the tail: housekeeping that fails for good late in a process leaves no gap between lines, so each engine_id's last line is compared with the end of its process
    tails, tail_bad = {}, []
    ends = list(seg_ends or [])
    ids = list(seg)
    pairs = list(zip(ids, ends)) if len(ids) == len(ends) else ([(ids[-1], ends[-1])] if ids and ends else [])
    for eid, end in pairs:
        last_t = seg[eid][-1].get("tape_ms") or 0
        tail = max(0, end - last_t)
        tails[eid] = {"last_line_tape": iso(last_t), "process_end": iso(end), "tail_s": tail // 1000}
        if tail > TRIM_GAP_MAX_MS and not any(w["start_ms"] <= end and last_t <= w["end_ms"] for w in feed_gaps):
            tail_bad.append({"engine_id": eid, "last_line_tape": iso(last_t), "process_end": iso(end), "tail_s": tail // 1000})
    tm = sorted(float(r["trim_ms"]) for r in rows if isinstance(r.get("trim_ms"), (int, float)))
    byh: dict[int, list[dict]] = defaultdict(list)
    for r in rows:
        byh[(r.get("tape_ms") or 0) // HOUR_MS * HOUR_MS].append(r)
    per_hour = []
    for h, rs in sorted(byh.items()):
        t = sorted(float(r["trim_ms"]) for r in rs if isinstance(r.get("trim_ms"), (int, float)))
        per_hour.append({"tape_hour": iso(h), "trims": len(rs), "trim_ms_p50": pct(t, 0.5), "trim_ms_p99": pct(t, 0.99), "trim_ms_max": t[-1] if t else None, "pools_last": rs[-1].get("pools"), "prints_held_last": rs[-1].get("prints_held")})
    segs = {}
    for eid, rs in seg.items():
        last = rs[-1]
        segs[eid] = {"lines": len(rs), "first_tape": iso(rs[0].get("tape_ms")), "last_tape": iso(last.get("tape_ms")), "trim_no_last": last.get("trim_no"),
                     **{k: last.get(k) for k in ("hist_trimmed_prints", "rows_below_trim_floor", "refused_history_trimmed", "rows_dup", "filter_v_missing", "filter_v_band", "v_checked_pools", "v_missing_pools", "pools", "prints_held")}}
    out = []
    mx = tm[-1] if tm else None
    over = sum(1 for v in tm if v > TRIM_MS_BLOCK)
    out.append(chk("QP-T1", "trim_ms max per line <= 1,000 ms (any line over BLOCKS registration until QP re-rules)", UNM if not tm else (FAIL if over else PASS),
                   {"lines": len(tm), "trim_ms_p50": pct(tm, 0.5), "trim_ms_p99": pct(tm, 0.99), "trim_ms_max": mx, "lines_over_1000": over, "blocks_registration": bool(over), "per_hour": per_hour},
                   ["disclosure for every read: engine trim every 5 tape minutes on the row thread; a decision written in a trim tick is priced up to trim_ms later"]))
    line_bad = sum(1 for r in rows if (r.get("rows_below_trim_floor") or 0) > 0 or (r.get("refused_history_trimmed") or 0) > 0)
    out.append(chk("QP-T2", "NOT_DECIDABLE rule: rows_below_trim_floor == 0 and refused_history_trimmed == 0 on every line", UNM if not rows else (FAIL if line_bad else PASS),
                   {"lines_violating": line_bad, "per_engine_id_last": {eid: {"rows_below_trim_floor": s["rows_below_trim_floor"], "refused_history_trimmed": s["refused_history_trimmed"]} for eid, s in segs.items()},
                    "hist_trimmed_prints_last": {eid: s["hist_trimmed_prints"] for eid, s in segs.items()}, "rows_dup_last": {eid: s["rows_dup"] for eid, s in segs.items()}},
                   ["a violation voids equivalence for every book on that engine for that segment: NOT_DECIDABLE unless an untrimmed lockstep replay of the affected hours byte-matches the book"]))
    n_ids = len(seg)
    st3 = UNM if not rows or seg_ends is None else (FAIL if mono_bad or gap_bad or tail_bad or (n_starts and n_ids > n_starts) else PASS)
    out.append(chk("QP-T3", "trim file: monotone per engine_id; one engine_id per start; no gap over 10 tape minutes between lines", st3,
                   {"lines": len(rows), "torn_tail_lines": torn, "engine_ids": n_ids, "process_starts_in_scan_log": n_starts, "non_monotone_engine_ids": len(mono_bad),
                    "largest_gap_between_lines_per_engine_id": gaps, "gaps_over_10_tape_min": gap_bad, "tail_to_process_end_per_engine_id": tails, "tails_over_10_tape_min": tail_bad,
                    "segments": segs},
                   ["expected about 288 lines per 24 h plus about 73 per start (the warm-up drain); cumulative counters are per engine_id and are never added across a restart",
                    "hook_errors is held in memory only: a failed housekeeping pass shows up as a missing 5-tape-minute trim line, so a gap over 10 tape minutes between lines, or between an engine_id's last line and the end of its process, FAILs (unless it lies inside a listed feed gap)"]))
    ratios_over, ratios_n = 0, 0
    hourly_v = []
    for eid, rs in seg.items():
        for r in rs:
            c, m = r.get("v_checked_pools") or 0, r.get("v_missing_pools") or 0
            if c > 0:
                ratios_n += 1
                ratios_over += int(m / c > V_MISSING_RATIO_BLOCK)
        last_in_hour: dict[int, tuple[int, int]] = {}
        for r in rs:
            last_in_hour[(r.get("tape_ms") or 0) // HOUR_MS * HOUR_MS] = (r.get("v_checked_pools") or 0, r.get("v_missing_pools") or 0)
        pc = pm = 0
        for h, (c, m) in sorted(last_in_hour.items()):
            dc, dm = c - pc, m - pm
            hourly_v.append({"engine_id": eid, "tape_hour": iso(h), "new_checked": dc, "new_v_missing": dm, "share": round(dm / dc, 4) if dc > 0 else None})
            pc, pm = c, m
    block = ratios_n > 0 and ratios_over == ratios_n
    out.append(chk("QP-V1", "v_missing rule: not (v_missing_pools / v_checked_pools > 0.5 on every line); per engine_id; counters not added across a restart",
                   UNM if ratios_n == 0 else (FAIL if block else PASS),
                   {"lines_with_checked_pools": ratios_n, "lines_over_half": ratios_over, "blocks_registration": block,
                    "per_engine_id_last": {eid: {k: s[k] for k in ("filter_v_missing", "filter_v_band", "v_checked_pools", "v_missing_pools")} | {"ratio": round(s["v_missing_pools"] / s["v_checked_pools"], 4) if s["v_checked_pools"] else None} for eid, s in segs.items()},
                    "share_of_newly_checked_pools_per_tape_hour": hourly_v,
                    "cause_statement": "not measured (needs the tape: how many v_missing pools never show V in the window and how many gain it later)",
                    "engine_side_counters_v_missing_and_v_out_of_band": "not measured (not persisted by the runner)"},
                   ["caveat (#749): filters evaluates every check, so a v_missing pool-scan may also fail other filters; the share lost ONLY to V is not measured and must not be stated as '44% of the universe is lost to V'"]))
    out.append(chk("QP-H1", "hook_errors prefixed 'housekeep:' == 0", UNM, {"hook_errors": "held in memory by the runner, not persisted", "indirect_handle": "QP-T3 trim-line gaps"},
                   ["no file carries it; a housekeep failure shows only as a missing trim line (see QP-T3)"], informational=True))
    return out


# ------------------------------------------------------------------------------------------------------------------------------ checks 7 to 10
def eval_check_7(L: Logs) -> dict:
    name = "sell_alert = 0 and sell_orphan = 0 (or explained); 'already has a sell' drop rows = 0"
    if not L.have_rows:
        return chk("7", name, UNM, {}, ["no decision rows"])
    drops = sum(L.sell_drop_rows.values())
    xe = sum(L.kinds.get(f"{p}-X", Counter()).get("entry", 0) for p in PERSONAS)
    return chk("7", name, PASS if L.sell_alert == 0 and drops == 0 else FAIL,
               {"sell_alert_rows": L.sell_alert, "sell_refused_drop_rows": dict(L.sell_drop_rows), "x_arm_entries": xe,
                "sell_orphan": "counter in memory; a sell_orphan is the 'no open position' / 'already has a sell' refusal rows counted here"},
               ["decision logs only"] + (["the X arms made 0 entries: this check is vacuous, the stub exercised no buy or sell"] if xe == 0 else []))


def eval_check_8(L: Logs, samples: list[dict], job: dict) -> dict:
    name = "after the restart: lane_h false, same frozen_sha, chain verifies, no duplicate rows, credit files reload with no reset"
    n = len(L.starts)
    chains_ok = all(a["chain_ok"] for a in L.arms.values()) and bool(L.arms)
    dups = sum(L.dup_rows.values())
    bind_ok = all(s["dirty"] is False and isinstance(s["git_sha"], str) and isinstance(s["frozen_sha"], str) and (s["git_sha"].startswith(s["frozen_sha"]) or s["frozen_sha"].startswith(s["git_sha"])) for s in L.starts)
    nums = {"process_starts": n, "starts": [{"at": iso(s["ms"]), "lane_h": s["lane_h"], "frozen_sha": (s["frozen_sha"] or "")[:12], "clean_checkout_at_frozen_sha": s["dirty"] is False} for s in L.starts],
            "decision_log_chain_ok": chains_ok, "arms": {a: v["rows"] for a, v in L.arms.items()}, "duplicate_rows": dict(L.dup_rows), "binding_clean_and_at_frozen_sha": bind_ok}
    if not L.have_rows:
        return chk("8", name, UNM, nums, ["no decision rows"])
    notes: list[str] = []
    if not chains_ok or (n and not bind_ok) or dups:
        return chk("8", name, FAIL, nums, ["a broken chain, a duplicate row or a start row that is dirty / not at the frozen sha fails at any start count"])
    if n < 2:
        return chk("8", name, UNM, nums, ["no restart in the log yet: the post-restart conditions cannot be evaluated"])
    same = len({s["frozen_sha"] for s in L.starts}) == 1 and all(s["lane_h"] is False for s in L.starts)
    nums["start_rows_lane_h_false_and_same_frozen_sha"] = same
    dec, reset_sig = None, None
    totals = [counter_total(s) for s in samples]
    totals = [t for t in totals if t is not None]
    if totals:
        run_max, dec = 0, 0
        for c in totals:
            dec += int(c < run_max)
            run_max = max(run_max, c)
        reset_sig = sum(1 for s in samples if isinstance(s.get("spend"), dict) and s["spend"].get("max_bucket") == CALLS_PER_HOUR_MAX)
    nums.update({"counter_decreases_in_samples": dec, "samples_with_a_1500_spend_bucket": reset_sig, "job_log_reset_warnings": job.get("spend_state_reset") if job else None})
    credit_ok = None
    if (job and job.get("spend_state_reset")) or (dec is not None and (dec > 0 or reset_sig)):
        credit_ok = False
    elif dec is not None:
        credit_ok = True
    if not same or n != 2 or credit_ok is False:
        if n > 2:
            notes.append("more than one restart: every start after the deliberate one is an unplanned restart (an OOM kill or crash is a stub failure)")
        return chk("8", name, FAIL, nums, notes)
    if credit_ok is None:
        return chk("8", name, UNM, nums, ["chain-credits / spend reload with no reset is UNMEASURED without usable --samples"])
    return chk("8", name, PASS, nums, notes)


def eval_check_9(L: Logs, samples: list[dict], args: argparse.Namespace) -> dict:
    name = "feed #1207 writes throughout; data_loss gaps reported; disk reserve not paused"
    t_lo = L.starts[0]["ms"] if L.starts else None
    t_hi = L.last_ms or None
    if not (t_lo and t_hi):
        return chk("9", name, UNM, {}, ["no decision rows: the run window is unknown"])
    tape = guard(args.tape_dir)
    if not tape.is_dir():
        return chk("9", name, UNM, {}, ["the tape directory is not readable: the check is UNMEASURED"])
    names = sorted(p.name for p in tape.iterdir())
    hours_missing, hours_n = [], 0
    h = t_lo // HOUR_MS * HOUR_MS
    while h <= t_hi:
        hours_n += 1
        stamp = datetime.fromtimestamp(h / 1000, tz=timezone.utc).strftime("tape-%Y-%m-%dT%H.jsonl")
        if not any(n in (stamp, stamp + ".zst") for n in names):
            hours_missing.append(iso(h))
        h += HOUR_MS
    nums: dict[str, Any] = {"utc_hours_in_window": hours_n, "tape_hour_files_missing": hours_missing}
    st_path = tape / "arena-feed-status.json"
    if st_path.exists():
        try:
            fs = json.loads(st_path.read_bytes())
            nums["feed_status_now"] = {"writing_paused": fs.get("writing_paused"), "disk_free_pct": fs.get("disk_free_pct"), "pauses": (fs.get("disk_reserve") or {}).get("pauses"),
                                       "rows_dropped_total": (fs.get("disk_reserve") or {}).get("rows_dropped_total"), "gaps_all_kinds_since_feed_start": fs.get("gaps"),
                                       "updated": iso(fs.get("updated_ms")), "decode_errors": fs.get("decode_errors")}
        except (ValueError, AttributeError, OSError):
            nums["feed_status_now"] = "unreadable"
    fsamp = [(s["t_ms"], s["feed"]) for s in samples if isinstance(s.get("feed"), dict)]
    notes = ["data_loss gaps: the status 'gaps' counter covers every gap kind, so the delta is an UPPER bound: data_loss <= N (0 means data_loss 0, measured); the subset needs a tape scan"]
    if hours_missing:
        return chk("9", name, FAIL, nums, notes + ["a tape hour file is missing inside the run window"])
    if len(fsamp) < 2:
        return chk("9", name, UNM, nums, notes + ["fewer than two feed samples: 'writes throughout' cannot be shown (tape-hour file names only)"])
    paused = sum(1 for _, f in fsamp if f.get("writing_paused"))
    d = lambda k: (fsamp[-1][1].get(k) or 0) - (fsamp[0][1].get(k) or 0)
    stalls = sum(1 for (ta, a), (tb, b) in zip(fsamp, fsamp[1:]) if (b.get("lines_written") or 0) == (a.get("lines_written") or 0) and tb - ta >= MIN_MS)
    negative = [k for k in ("lines_written", "gaps", "pauses", "rows_dropped_total") if any((b.get(k) or 0) < (a.get(k) or 0) for (_, a), (_, b) in zip(fsamp, fsamp[1:]))]
    nums["feed_samples"] = {"n": len(fsamp), "first": iso(fsamp[0][0]), "last": iso(fsamp[-1][0]), "samples_with_writing_paused": paused, "lines_written_delta": d("lines_written"),
                            "stalls_no_new_lines_between_samples": stalls, "pauses_delta": d("pauses"), "rows_dropped_delta": d("rows_dropped_total"), "data_loss_upper_bound_gaps_all_kinds_delta": d("gaps"),
                            "negative_deltas": negative}
    notes.append(f"feed samples cover {iso(fsamp[0][0])} to {iso(fsamp[-1][0])}; before the first sample 'writes throughout' rests on the tape-hour file names only")
    if paused or d("pauses") or d("rows_dropped_total") or stalls:
        return chk("9", name, FAIL, nums, notes + ["a paused disk reserve means the shakedown is extended, not passed (SHAKEDOWN-CHECKS-2 check 9)"])
    if negative:
        return chk("9", name, UNM, nums, notes + ["a negative counter delta means the feed restarted and reset its counters: the deltas are not comparable"])
    return chk("9", name, PASS, nums, notes)


def eval_check_10(root: Path, L: Logs, job: dict, inputs: list[dict]) -> dict:
    name = 'registration printed {"lane_h": false, "n_bots": 9}; no BST log, book or bot_id in the shakedown files'
    bst_dirs = [a for a in L.arms if "BST" in a.upper()]
    bots = root.parent / "bots.json"
    n_bots = n_bst = None
    if bots.exists():
        raw = read_input(bots, inputs)
        lines = [ln for ln in raw.split(b"\n") if ln.strip()]
        n_bots, n_bst = len(lines), sum(1 for ln in lines if b"BST" in ln)
    name_hits = 0
    for dp, dn, fn in os.walk(root):
        dn[:] = [d for d in dn if not any(w in d.lower() for w in BLOCKED_WORDS)]
        name_hits += sum(1 for x in dn + fn if "BST" in x.upper())
    printed = bool(job and job.get("register_line"))
    lane_false = bool(L.starts) and all(s["lane_h"] is False for s in L.starts)
    nums = {"bots_json_lines": n_bots, "bots_json_lines_naming_BST": n_bst, "decision_log_dirs_naming_BST": len(bst_dirs), "file_or_dir_names_with_BST_under_root": name_hits,
            "register_print_seen_in_job_log": printed, "start_rows_lane_h_false": lane_false}
    if n_bst or bst_dirs or name_hits or (n_bots is not None and n_bots != 9) or (L.starts and not lane_false):
        st = FAIL
    elif printed and n_bots == 9 and lane_false:
        st = PASS
    else:
        st = UNM
    notes = ["the print is the register job's log line {\"lane_h\":false,\"n_bots\":9} (hv4_runner.py:1704, through canon): without the register job's log (#1249) this check is UNMEASURED",
             "no BST book or bot_id: a book exists only for a registered bot_id and bots.json has no BST line; the ledger tree is never listed (policy)"]
    return chk("10", name, st, nums, notes)


# ------------------------------------------------------------------------------------------------------------------------------ job logs
def scan_job_logs(paths: list[str], inputs: list[dict]) -> dict:
    out: dict[str, Any] = {k: 0 for k in JOBLOG_MARKERS}
    out.update({"register_line": False, "logs_read": 0, "logs_missing": 0})
    for p in paths:
        try:
            data = read_input(Path(p), inputs)
        except OSError:
            out["logs_missing"] += 1
            continue
        out["logs_read"] += 1
        for k, marker in JOBLOG_MARKERS.items():
            out[k] += data.count(marker)
        if REGISTER_LINE in data.replace(b" ", b""):
            out["register_line"] = True
    return out


# ------------------------------------------------------------------------------------------------------------------------------ assembly
def git_info() -> dict:
    here = str(Path(__file__).resolve().parent)

    def run(*a: str) -> str | None:
        try:
            p = subprocess.run(["git", "-C", here, *a], capture_output=True, text=True, timeout=30)
            return p.stdout if p.returncode == 0 else None
        except (OSError, subprocess.SubprocessError):
            return None

    head, st = run("rev-parse", "HEAD"), run("status", "--porcelain")
    return {"git_head": head.strip() if head else None, "git_dirty": None if st is None else bool(st.strip())}


def build_report(args: argparse.Namespace) -> dict:
    inputs: list[dict] = []
    base = guard(args.dir)
    root = base / "root"
    L = load_logs(root, inputs)
    try:
        cards = load_cards(root, inputs)
    except (OSError, ValueError):
        cards = []
    samples, sample_errors, sample_note = load_samples(Path(args.samples) if args.samples else None, inputs)
    job = scan_job_logs(args.job_log, inputs) if args.job_log else {}
    starts = L.starts
    fresh = bool(starts) and starts[0].get("seq") == 0
    windows = startup_windows(L)
    for i, g in enumerate(args.feed_gap_ms):
        windows.append({"label": f"listed_feed_gap_{i}", "kind": "feed_gap", "start_ms": g[0], "end_ms": g[1], "state": "listed", "source": g[2]})
    try:
        checks, counts = eval_checks_1_to_4(L, cards, job, Path(args.tape_dir), args.tape_first_rows)
    except Refused:
        raise
    except Exception as e:  # noqa: BLE001
        checks = [chk(c, "input error", UNM, {"input_error": type(e).__name__}, ["this check is UNMEASURED; the rest of the report is unaffected"]) for c in "1234"]
        counts = {"scans": len(L.scans), "per_persona": {p: {"attempts": 0, "chain_unavailable": 0, "chain_unavailable_tagged": 0, "chain_stale": 0, "cards_in_sink": 0, "null_holders_cards": 0,
                                                             "setups_by_type_in_sink": {}, "stub_llm_calls": 0, "x_arm_entries": 0, "x_arm_exits": 0} for p in PERSONAS}, "risk_agent_llm_calls": 0}
    checks.append(safe("5", "chain calls per rolling hour / per UTC day", eval_check_5, root, samples, starts, inputs, job, fresh))
    checks.append(safe("6", "timing", eval_timing, L, windows))
    checks.append(safe("7", "sell_alert / sell_orphan", eval_check_7, L))
    checks.append(safe("8", "after the restart", eval_check_8, L, samples, job))
    checks.append(safe("9", "feed", eval_check_9, L, samples, args))
    checks.append(safe("10", "registration", eval_check_10, root, L, job, inputs))
    checks.sort(key=lambda c: int(c["id"]))
    trim = safe("QP-T1", "engine trim counters", eval_trim, root, inputs, len(starts), [w for w in windows if w["kind"] == "feed_gap"], segment_ends(L))
    trim = trim if isinstance(trim, list) else [trim]
    unplanned = len(starts) > 2
    mem = safe("QP-M1", "memory", eval_memory, samples, starts, windows, args.rss_point_ms, args.memory_max_mb, args.host_cap_mb, args.read_hours, unplanned)
    extra = trim + [mem]
    allc = checks + extra
    expect_end = parse_iso(args.expect_end)
    complete = bool(L.last_ms) and L.last_ms >= expect_end - 10 * MIN_MS
    blockers = []
    for c in allc:
        n = c["numbers"]
        if c["id"] == "QP-T1" and n.get("blocks_registration"):
            blockers.append("trim_ms > 1000 on at least one line: HV4 does not register until quant-proof re-rules")
        if c["id"] == "QP-T2" and c["status"] == FAIL:
            blockers.append("NOT_DECIDABLE rule triggered (rows_below_trim_floor or refused_history_trimmed > 0)")
        if c["id"] == "QP-V1" and n.get("blocks_registration"):
            blockers.append("v_missing ratio above 0.5 on every trim line: HV4 does not register until the cause is explained")
    unmeasured_blocking = [c["id"] for c in allc if c["status"] == UNM and not c["informational"]]
    statuses = [c["status"] for c in allc if not c["informational"]]
    verdict = "FAIL" if FAIL in statuses else ("PASS" if complete and all(s == PASS for s in statuses) else "INCOMPLETE")
    record = {"schema": SCHEMA, "generated": iso(time.time() * 1000), "dir": str(base), "verdict": verdict, "complete": complete,
              "window": {"first_start": iso(starts[0]["ms"]) if starts else None, "last_decision_row": iso(L.last_ms) if L.last_ms else None, "expect_end": args.expect_end,
                         "hours_covered": round((L.last_ms - starts[0]["ms"]) / HOUR_MS, 2) if starts and L.last_ms else None},
              "blocks_registration": blockers, "unmeasured_checks": unmeasured_blocking, "checks": checks, "registration_conditions": extra, "per_persona_counts": counts,
              "startup_and_feed_gap_windows": [{k: (iso(v) if k.endswith("_ms") else v) for k, v in w.items()} for w in windows],
              "statuses": dict(Counter(statuses)), "inputs": inputs, "torn_tail_files": L.torn_tails, "unreadable_decision_files": L.unreadable_files, "skipped_dirs": len(L.skipped_dirs),
              "sampler": {"error_lines": sample_errors, "note": sample_note, "samples": len(samples)}, "job_logs": {k: v for k, v in job.items() if k in ("logs_read", "logs_missing", "register_line")},
              "tool": {"path": str(Path(__file__).resolve()), "sha256": sha256_bytes(Path(__file__).read_bytes()), **git_info()},
              "scope": "counts and timings only; no ledger opened; StubModel trades are not evidence and no profit claim is made"}
    forbid_scan(record)
    return record


def forbid_scan(obj: Any, path: str = "") -> None:
    """Defence in depth: refuse to emit a record that carries a P&L, price, fill or outcome key."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if any(w in str(k).lower() for w in OUTPUT_FORBIDDEN_KEYS):
                raise Refused(f"forbidden output key at {path}")
            forbid_scan(v, f"{path}/{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            forbid_scan(v, f"{path}[{i}]")


def markdown(rec: dict) -> str:
    def g(d: Any, *ks: str) -> Any:
        for k in ks:
            d = d.get(k) if isinstance(d, dict) else None
        return d

    def short(c: dict) -> str:
        n, i = c["numbers"], c["id"]
        if "input_error" in n:
            return f"input error {n['input_error']}"
        try:
            if i in ("2", "4"):
                return "; ".join(f"{p}: {n[p].get('chain_stale', n[p].get('holders_null'))}/{n[p].get('attempts', n[p].get('cards'))}" for p in PERSONAS if isinstance(n.get(p), dict))
            if i == "3":
                return f"{n['chain_unavailable_cards']} chain_unavailable, {n['cards_with_gap_bound_over_10min']} with a gap bound over 10 min, {n['pool_visible_in_logs_more_than_10min_earlier']} seen >10 min earlier"
            if i == "5":
                return f"per day {n.get('calls_by_utc_day')}; rolling hour max<= {g(n, 'rolling_hour_calls', 'max_upper_bound')}; error counters measured {g(n, 'status_parts', 'error_counters_measured')}"
            if i == "6":
                s, e = n["scan_wall_ms"], n["exit_check_delay_ms"]
                return f"scan p99 {s['p99']} max {s['max']} (n {s['n']}, over 2 s: {s['scans_over_2000']}); exit delay max {e['max_of_minute_max']}, ticks>2s in {e['ticks_over_2000_between']}; windows " + ", ".join(f"{k} {v['state']} {v['length_s']} s" for k, v in n["windows"].items())
            if i == "7":
                return f"sell_alert {n['sell_alert_rows']}, drop rows {n['sell_refused_drop_rows']}, X entries {n['x_arm_entries']}"
            if i == "8":
                return f"{n['process_starts']} starts; chain ok {n['decision_log_chain_ok']}; dup rows {sum(n['duplicate_rows'].values())}"
            if i == "9":
                return f"missing tape hours {len(n['tape_hour_files_missing'])}/{n['utc_hours_in_window']}; feed samples {g(n, 'feed_samples', 'n')}"
            if i == "10":
                return f"bots.json lines {n['bots_json_lines']}, BST {n['bots_json_lines_naming_BST']}, register print seen {n['register_print_seen_in_job_log']}"
            if i == "1":
                return f"as_of_refusal {n['as_of_refusal_rows']}, LeakageError {n['leakage_error_mentions_in_decision_logs']}"
            if i == "QP-T1":
                return f"trim_ms max {n.get('trim_ms_max')}, p99 {n.get('trim_ms_p99')}, lines {n.get('lines')}"
            if i == "QP-T2":
                return f"violating lines {n.get('lines_violating')}"
            if i == "QP-T3":
                return f"{n.get('lines')} lines, {n.get('engine_ids')} engine_ids vs {n.get('process_starts_in_scan_log')} starts, gaps over 10 min {len(n.get('gaps_over_10_tape_min') or [])}"
            if i == "QP-V1":
                return f"{n.get('lines_over_half')}/{n.get('lines_with_checked_pools')} lines over 0.5"
            if i == "QP-M1":
                pl = n.get("plan") or {}
                return f"peak {n.get('peak_mb')} MB, slope {n.get('slope_mb_per_h_conservative_max_of_segments')} MB/h, MemoryMax {pl.get('memory_max_mb')}, implied plan {pl.get('implied')}"
        except (KeyError, TypeError):
            return ""
        return ""

    lines = [f"HV4 shakedown report: verdict {rec['verdict']} (complete: {rec['complete']}), window {rec['window']['first_start']} to {rec['window']['last_decision_row']} ({rec['window']['hours_covered']} h)", "",
             "| check | status | numbers |", "| --- | --- | --- |"]
    for c in rec["checks"] + rec["registration_conditions"]:
        lines.append(f"| {c['id']} {c['name'][:70]} | {c['status']}{' (proxy)' if c['proxy'] else ''}{' (informational)' if c['informational'] else ''} | {short(c)} |")
    if rec["blocks_registration"]:
        lines += ["", "BLOCKS REGISTRATION: " + "; ".join(rec["blocks_registration"])]
    if rec["unmeasured_checks"]:
        lines += ["", "UNMEASURED (block registration until measured): " + ", ".join(rec["unmeasured_checks"])]
    pc = rec["per_persona_counts"]
    lines += ["", f"scans {pc['scans']}; risk-agent stub calls {pc['risk_agent_llm_calls']}"]
    for p, v in pc["per_persona"].items():
        lines.append(f"- {p}: attempts {v['attempts']}, chain_unavailable {v['chain_unavailable']} (tagged {v['chain_unavailable_tagged']}), chain_stale {v['chain_stale']}, "
                     f"cards in sink {v['cards_in_sink']}, null holders {v['null_holders_cards']}, setups {v['setups_by_type_in_sink']}, stub llm calls {v['stub_llm_calls']}, "
                     f"X entries {v['x_arm_entries']} exits {v['x_arm_exits']}")
    lines += ["", f"inputs: {len(rec['inputs'])} files (paths and sha256 in the JSON record); tool sha256 {rec['tool']['sha256'][:16]}, git {str(rec['tool']['git_head'])[:12]}, git_dirty {rec['tool']['git_dirty']}"]
    return "\n".join(lines)


# ------------------------------------------------------------------------------------------------------------------------------ the sampler
def find_runner_pids(root: Path) -> list[int]:
    """pids whose argv is tools/hv4_runner.py with --root equal to this root. Only the match is used; no argv is stored or printed."""
    want = os.path.realpath(str(root))
    pids = []
    for d in os.listdir("/proc"):
        if not d.isdigit():
            continue
        try:
            argv = Path(f"/proc/{d}/cmdline").read_bytes().split(b"\0")
        except OSError:
            continue
        args = [a.decode("utf-8", "replace") for a in argv]
        if any(a.endswith("hv4_runner.py") for a in args) and "--root" in args:
            i = args.index("--root")
            if i + 1 < len(args) and os.path.realpath(args[i + 1]) == want:
                pids.append(int(d))
    return pids


def proc_mem(pid: int) -> dict | None:
    try:
        text = Path(f"/proc/{pid}/status").read_text()
    except OSError:
        return None
    kb = lambda key: next((int(m.group(1)) for m in [re.search(rf"^{key}:\s+(\d+) kB", text, re.M)] if m), None)
    rss, hwm = kb("VmRSS"), kb("VmHWM")
    rec: dict[str, Any] = {"pid": pid, "rss_mb": None if rss is None else round(rss / 1024, 1), "hwm_mb": None if hwm is None else round(hwm / 1024, 1)}
    try:
        cg = Path(f"/proc/{pid}/cgroup").read_text().strip().split("::", 1)[1]
        base = Path("/sys/fs/cgroup") / cg.lstrip("/")
        for key, fn in (("cg_current_mb", "memory.current"), ("cg_peak_mb", "memory.peak")):
            try:
                rec[key] = round(int((base / fn).read_text()) / 1048576, 1)
            except (OSError, ValueError):
                rec[key] = None
        try:
            mx = (base / "memory.max").read_text().strip()
            rec["cg_max_mb"] = None if mx == "max" else round(int(mx) / 1048576, 1)
        except (OSError, ValueError):
            rec["cg_max_mb"] = None
        try:
            ev = dict(ln.split() for ln in (base / "memory.events").read_text().splitlines() if len(ln.split()) == 2)
            rec["cg_oom_kill"] = int(ev.get("oom_kill", 0))
        except (OSError, ValueError):
            rec["cg_oom_kill"] = None
    except (OSError, IndexError):
        pass
    return rec


def take_sample(base: Path, feed_status: Path) -> dict:
    root = base / "root"
    s: dict[str, Any] = {"schema": SAMPLE_SCHEMA, "t_ms": int(time.time() * 1000)}
    try:
        doc = json.loads((root / "chain-credits.json").read_bytes())
        s["chain_by_day"] = {k: int(v) for k, v in doc["by_day"].items()}
        s["chain_saved_ms"] = doc.get("saved_ms")
    except Exception:  # noqa: BLE001 - a sample must never raise: a bad file is a null section
        s["chain_by_day"] = None
    try:
        sd = json.loads((root / "chain-prefetch-spend.json").read_bytes())
        rows = [[int(a), int(b)] for a, b in sd["spent"]]
        s["spend"] = {"sum_last_hour": sum(b for a, b in rows if a > s["t_ms"] - HOUR_MS), "buckets": len(rows), "max_bucket": max((b for _, b in rows), default=0)}
    except Exception:  # noqa: BLE001
        s["spend"] = None
    try:
        s["procs"] = [m for m in (proc_mem(p) for p in find_runner_pids(root)) if m]
    except Exception:  # noqa: BLE001
        s["procs"] = []
    try:
        fs = json.loads(feed_status.read_bytes())
        dr = fs.get("disk_reserve") or {}
        s["feed"] = {"writing_paused": fs.get("writing_paused"), "lines_written": fs.get("lines_written"), "gaps": fs.get("gaps"), "pauses": dr.get("pauses"),
                     "rows_dropped_total": dr.get("rows_dropped_total"), "disk_free_pct": fs.get("disk_free_pct"), "updated_ms": fs.get("updated_ms")}
    except Exception:  # noqa: BLE001
        s["feed"] = None
    return s


def sample_line(base: Path, feed_status: Path) -> bytes:
    """One JSONL line: the sample, or, if anything at all goes wrong, an error line carrying the exception class only (the loop never dies on one sample)."""
    try:
        return canon(take_sample(base, feed_status)).encode() + b"\n"
    except Exception as e:  # noqa: BLE001
        return canon({"schema": SAMPLE_SCHEMA, "t_ms": int(time.time() * 1000), "error": type(e).__name__}).encode() + b"\n"


def sampler(args: argparse.Namespace) -> int:
    base, out = guard(args.dir), guard(args.out)
    fs = guard(args.feed_status)
    stop = {"now": False}
    signal.signal(signal.SIGTERM, lambda *_: stop.update(now=True))
    until = parse_iso(args.until) if args.until else None
    while not stop["now"]:
        line = sample_line(base, fs)
        try:
            fd = os.open(str(out), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
            try:
                os.write(fd, line)
            finally:
                os.close(fd)
        except OSError:
            pass  # a full disk or a vanished dir loses this sample, not the job
        if until is not None and time.time() * 1000 >= until:
            break
        time.sleep(max(1.0, args.interval_s - (time.time() % args.interval_s)))
    return 0


# ------------------------------------------------------------------------------------------------------------------------------ cli
def parse_feed_gap(s: str) -> tuple[int, int, str]:
    """ISO/ISO@source: only a gap the feed itself recorded is accepted, and its source is printed in the record."""
    span, _, source = s.partition("@")
    a, _, b = span.partition("/")
    if not source.strip():
        raise argparse.ArgumentTypeError("--feed-gap needs a source: ISO/ISO@where-the-feed-recorded-it")
    return parse_iso(a), parse_iso(b), source.strip()


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "sample":
        ap = argparse.ArgumentParser(prog="hv4_shakedown_report sample")
        ap.add_argument("mode")
        ap.add_argument("--dir", required=True)
        ap.add_argument("--out", required=True)
        ap.add_argument("--feed-status", default="/data/mal/arena/tape/arena-feed-status.json")
        ap.add_argument("--interval-s", type=int, default=60)
        ap.add_argument("--until", default=None, help="ISO instant to stop at (default: until SIGTERM)")
        return ap.parse_args(argv)
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dir", required=True, help="the shakedown dir (holds root/ and bots.json), e.g. /data/mal/arena/_shakedown-hv4-1020")
    ap.add_argument("--samples", default=None, help="the JSONL written by the sample subcommand")
    ap.add_argument("--job-log", action="append", default=[], help="a MiScusi job log (one per process start); only exact marker lines are counted")
    ap.add_argument("--tape-dir", default="/data/mal/arena/tape")
    ap.add_argument("--tape-first-rows", action="store_true", help="read-only tape scan for the first row (pool id and t_recv_ms only) of each chain_unavailable pool: bounds check 3")
    ap.add_argument("--feed-gap", action="append", default=[], type=parse_feed_gap, help="ISO/ISO@source: a gap the feed itself recorded (excluded from check 6)")
    ap.add_argument("--rss-point", action="append", default=[], help="ISO=MB, a MiScusi job memory figure, when no sample file exists")
    ap.add_argument("--memory-max-mb", type=float, default=None, help="refused if it differs from the sampled cgroup memory.max")
    ap.add_argument("--host-cap-mb", type=float, default=None, help="the MemoryMax the book job can get beside the other planned jobs (plan A is implied only if it fits)")
    ap.add_argument("--read-hours", type=float, default=None, help="H: hours from T0 to the latest planned read instant (the registered read length)")
    ap.add_argument("--expect-end", default=EXPECT_END_DEFAULT)
    ap.add_argument("--out-dir", default=None, help="also write report.json and report.md here")
    a = ap.parse_args(argv)
    a.mode = "report"
    a.feed_gap_ms = list(a.feed_gap)
    a.rss_point_ms = [(parse_iso(p.split("=")[0]), float(p.split("=")[1])) for p in a.rss_point]
    return a


def main(argv: list[str] | None = None) -> int:
    try:
        args = parse_args(argv)
        if args.mode == "sample":
            return sampler(args)
        rec = build_report(args)
        text = markdown(rec)
        if args.out_dir:
            od = guard(args.out_dir)
            od.mkdir(parents=True, exist_ok=True)
            (od / "report.json").write_text(json.dumps(rec, indent=1, sort_keys=True))
            (od / "report.md").write_text(text + "\n")
        print(text)
        print()
        print(json.dumps(rec, sort_keys=True))
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001 - the exit code is 0 always; the verdict carries the failure. Only the class and our own policy messages are printed.
        msg = str(e)[:160] if isinstance(e, Refused) else ""
        print(json.dumps({"schema": SCHEMA, "verdict": "ERROR", "error": type(e).__name__, "message": msg}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
