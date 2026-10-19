#!/usr/bin/env python3
"""hv4_shakedown_report: one command that evaluates the Haiku v4 (HV4) 24 h STUB shakedown. Counts and timings only. PAPER only.

Inputs (all read-only): the shakedown dir's decision logs (incl. SCAN scan_timing / tick_timing rows), the card sink, chain-credits.json,
chain-prefetch-spend.json, engine-trim-counters.jsonl, bots.json (line counts only), the arena feed status file and the tape directory's file names, and
optionally (a) a sample file written by the `sample` subcommand of this tool (per-minute chain-counter snapshots, process RSS, feed counters) and
(b) MiScusi job logs (only exact marker lines are counted; no log content is printed).

Pass checks: SHAKEDOWN-CHECKS-2.md (checks 1 to 10). Registration conditions from quant-proof: PR #755 comment 6276972823 (items 3 to 5) and PR #745
(comments 6275482286 and 6276589135, item 4): trim_ms, trim counters per engine_id, the v_missing rule, the NOT_DECIDABLE rule, RSS and the 7 day projection.

What it never does: open an engine ledger (any path whose name contains "ledger" is refused), print a bots.json body, a runner status body, a card body, an
entry/sell row, or any P&L, fill price or outcome field. Decision rows are projected to a small allow-list the moment they are verified; the raw row is
dropped. StubModel trades are not evidence for any book: this report makes no profit claim.

Exit code is 0 always. The verdict is in the record: PASS only if every check is PASS and the run is complete; FAIL if any check is FAIL; else INCOMPLETE.

Run:   python -I tools/hv4_shakedown_report.py --dir /data/mal/arena/_shakedown-hv4-1020 [--samples FILE] [--job-log FILE] ...
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

# thresholds: SHAKEDOWN-CHECKS-2.md and the QP comments
STALE_MAX_FRAC = 0.02
NULL_HOLDERS_MAX_FRAC = 0.10
FIRST_SEEN_MAX_MS = 10 * MIN_MS
CALLS_PER_HOUR_MAX = 1_500
CALLS_PER_DAY_MAX = 40_000
SCAN_P99_MAX, SCAN_MAX_MAX = 2_000, 5_000
EXIT_P99_MAX, EXIT_MAX_MAX = 2_000, 10_000
TRIM_MS_BLOCK = 1_000
V_MISSING_RATIO_BLOCK = 0.5
READ_HOURS = 168
DEFAULT_CATCHUP_MIN = 10  # QP-RULING-1019-startup (a)
DEFAULT_MEMORY_MAX_MB = 10_240  # QP #755 item 3: "acceptable under MemoryMax=10G"
EXPECT_END_DEFAULT = "2026-10-20T21:32:00Z"
REGISTER_LINE = b'{"lane_h":false,"n_bots":9}'
SELL_DROP_KEYS = ("no open position", "already has a sell")
JOBLOG_MARKERS = {"spend_state_reset": b"spend state unreadable", "state_write_failed": b"hv4_chain state write failed", "leakage_error": b"LeakageError"}
BLOCKED_WORDS = ("ledger", "tip-tape-archive")
BLOCKED_PREFIXES = ("/data/mal/blocks", "/var/lib/mal/sealed", "/data/mal/kalshi")
OUTPUT_FORBIDDEN_KEYS = ("pnl", "price", "fill", "outcome", "realized", "est_ret", "tokens_eq", "bankroll")


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
            data = read_input(f, inputs)
            L.leakage_mentions += data.count(b"LeakageError")
            torn_tail = bool(data) and not data.endswith(b"\n")
            lines = data.split(b"\n")
            if torn_tail:
                L.torn_tails += 1
                lines = lines[:-1]
            for ln in lines:
                if not ln.strip():
                    continue
                try:
                    row = json.loads(ln)
                    sha = row.pop("sha")
                except (ValueError, KeyError):
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


# ------------------------------------------------------------------------------------------------------------------------------ checks 1 to 4 and persona counts
def attribute_refusals(L: Logs) -> dict:
    """Per persona: card attempts (every attempt is a tagged or board pool, so every card here is on a hot pool), chain_unavailable (and how many carried a
    setup tag), chain_stale and other refusals. A refused row has no persona: a tagged pool maps by order (the scan loop runs CON then BIG); an untagged pool
    is a board pool, which both personas share."""
    per = {p: {"attempts": 0, "chain_unavailable": 0, "chain_unavailable_tagged": 0, "chain_stale": 0, "other_refused": 0} for p in PERSONAS}
    by_ms: dict[int, list[dict]] = defaultdict(list)
    for r in L.refused:
        by_ms[r["ms"]].append(r)
    matched, extra = 0, 0
    first_seen: dict[str, int] = {}
    for s in sorted(L.scans, key=lambda s: s["ms"]):
        for pool in [t[0] for t in s["tagged"]] + list(s["eligible"]) + [r["pool"] for r in by_ms.get(s["ms"], [])]:
            first_seen.setdefault(pool, s["ms"])
    gaps: list[int] = []
    first_is_refusal = 0
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
                g = s["ms"] - first_seen.get(r["pool"], s["ms"])
                gaps.append(g)
                first_is_refusal += int(g == 0)
    return {"per_persona": per, "refused_rows_matched_to_a_scan": matched, "refused_rows_without_scan_row": len(L.refused) - matched, "extra_rows_unattributed": extra,
            "chain_unavailable_gaps_ms": gaps, "chain_unavailable_first_appearance_is_the_refusal": first_is_refusal}


def eval_checks_1_to_4(L: Logs, cards: list[dict], job: dict) -> tuple[list[dict], dict]:
    out = []
    leak = L.leakage_mentions + job.get("leakage_error", 0)
    out.append(chk("1", "0 as_of_refusal rows and 0 LeakageError", PASS if L.as_of_refusals == 0 and leak == 0 else FAIL,
                   {"as_of_refusal_rows": L.as_of_refusals, "leakage_error_mentions_in_decision_logs": L.leakage_mentions, "leakage_error_lines_in_job_logs": job.get("leakage_error") if job else None},
                   [] if job else ["no --job-log given: LeakageError counted in the decision logs only"]))
    att = attribute_refusals(L)
    per = att["per_persona"]
    fr = {p: (per[p]["chain_stale"] / per[p]["attempts"] if per[p]["attempts"] else None) for p in PERSONAS}
    st = UNM if any(v is None for v in fr.values()) else (PASS if all(v <= STALE_MAX_FRAC for v in fr.values()) else FAIL)
    out.append(chk("2", "chain_stale <= 2% of cards on hot pools, CON and BIG separately", st,
                   {p: {"attempts": per[p]["attempts"], "chain_stale": per[p]["chain_stale"], "frac": None if fr[p] is None else round(fr[p], 5)} for p in PERSONAS},
                   ["denominator = card attempts per persona = n_tags[persona] + n_board summed over scans; every attempt is a tagged or board pool (hot by the check's definition)"]))
    gaps = att["chain_unavailable_gaps_ms"]
    over = sum(1 for g in gaps if g > FIRST_SEEN_MAX_MS)
    out.append(chk("3", "every chain_unavailable card's pool first seen within 10 min before that scan", FAIL if over else PASS,
                   {"chain_unavailable_cards": len(gaps), "pool_visible_in_logs_more_than_10min_earlier": over, "max_gap_s": round(max(gaps) / 1000, 1) if gaps else None,
                    "first_appearance_is_the_refusal_itself": att["chain_unavailable_first_appearance_is_the_refusal"]},
                   ["PROXY: scan rows do not list filter-pass pools, so 'first seen' is the first scan whose tagged / eligible / card_refused rows name the pool, which is the "
                    "same scan or later than the scanner's true first sight. It can show a FAIL; it cannot prove a PASS for refusals whose first appearance is the refusal itself."], proxy=True))
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
              "risk_agent_llm_calls": L.kinds.get("RISK", Counter()).get("llm_call", 0), "board_cards_dedupe_note": "setups_by_type counts sink cards with a setup tag",
              "refused_rows_without_scan_row": att["refused_rows_without_scan_row"], "extra_rows_unattributed": att["extra_rows_unattributed"]}
    return out, counts


# ------------------------------------------------------------------------------------------------------------------------------ check 6 timing
def eval_timing(L: Logs, windows: list[tuple[int, int, str]]) -> dict:
    def excluded(a: int, b: int) -> bool:
        return any(a < we and b > ws for ws, we, _ in windows)

    scans_all = sorted(w for _, w in L.scan_timing)
    scans = sorted(w for ms, w in L.scan_timing if not excluded(ms, ms + 1))
    ticks = [t for t in L.tick_timing if not excluded(t["minute_ms"], t["minute_ms"] + MIN_MS)]
    ticks_ex = [t for t in L.tick_timing if excluded(t["minute_ms"], t["minute_ms"] + MIN_MS)]
    tot = sum(t["n_ticks"] for t in ticks)
    over = [t for t in ticks if (t["max"] or 0) > EXIT_P99_MAX]
    lower = sum(math.ceil(t["n_ticks"] / 2) if (t["p50"] or 0) > EXIT_P99_MAX else 1 for t in over)
    upper = sum(t["n_ticks"] for t in over)
    limit = math.floor(0.01 * tot)
    p99_status = PASS if upper <= limit else (FAIL if lower > 0.01 * tot else UNM)
    mx = max((t["max"] or 0 for t in ticks), default=None)
    scan_p99, scan_max = pct(scans, 0.99), (scans[-1] if scans else None)
    scan_ok = None if not scans else (scan_p99 <= SCAN_P99_MAX and scan_max <= SCAN_MAX_MAX)
    st = UNM
    if scans and ticks:
        if scan_ok is False or (mx is not None and mx > EXIT_MAX_MAX) or p99_status == FAIL:
            st = FAIL
        elif scan_ok and p99_status == PASS:
            st = PASS
    ended = {}
    for ws, we, label in windows:
        late = [t["minute_ms"] + MIN_MS for t in ticks_ex if ws <= t["minute_ms"] < we and (t["max"] or 0) > EXIT_MAX_MAX]
        ended[label] = {"window": [iso(ws), iso(we)], "minutes": sum(1 for t in ticks_ex if ws <= t["minute_ms"] < we),
                        "max_exit_delay_ms": max((t["max"] or 0 for t in ticks_ex if ws <= t["minute_ms"] < we), default=None),
                        "last_minute_over_10s_ends_s_after_window_start": round((max(late) - ws) / 1000) if late else None}
    mins = sorted(t["minute_ms"] for t in L.tick_timing)
    missing = sum(1 for a, b in zip(mins, mins[1:]) if b - a > MIN_MS and not excluded(a + MIN_MS, b))
    numbers = {"scan_wall_ms": {"n": len(scans), "p99": scan_p99, "max": scan_max, "limits": [SCAN_P99_MAX, SCAN_MAX_MAX], "unfiltered_n": len(scans_all), "unfiltered_max": scans_all[-1] if scans_all else None},
               "exit_check_delay_ms": {"minutes": len(ticks), "ticks": tot, "max_of_minute_max": mx, "max_limit": EXIT_MAX_MAX, "minutes_with_max_over_2000": len(over),
                                       "ticks_over_2000_between": [lower, upper], "one_percent_of_ticks": limit, "p99_status": p99_status,
                                       "limits": [EXIT_P99_MAX, EXIT_MAX_MAX]},
               "excluded_windows": ended, "excluded_minutes": len(ticks_ex), "minutes_with_no_tick_timing_row_outside_windows": missing}
    notes = ["p99 of the exit-check delay is not recoverable from per-minute rows (a minute's p99 is its max at about 60 ticks): the count of ticks above 2,000 ms is bounded "
             "between one per over-limit minute and all of its ticks; PASS only when even the upper bound is within 1% of ticks, FAIL when the lower bound is over it",
             "the stub does not measure run-mode scan time with Haiku calls inside the scan; that is stated, not assumed; trim minutes are NOT excluded (QP #755 item 2)"]
    return chk("6", "timing: scan wall p99<=2000 max<=5000; exit-check delay p99<=2000 max<=10000, outside restart / listed feed gaps", st, numbers, notes)


# ------------------------------------------------------------------------------------------------------------------------------ samples, check 5, RSS
def load_samples(path: Path | None, inputs: list[dict]) -> list[dict]:
    if path is None or not Path(path).exists():
        return []
    rows, _ = jsonl(read_input(path, inputs))
    return sorted((r for r in rows if isinstance(r, dict) and r.get("schema") == SAMPLE_SCHEMA and isinstance(r.get("t_ms"), int)), key=lambda r: r["t_ms"])


def counter_total(s: dict) -> int:
    return sum(int(v) for v in (s.get("chain_by_day") or {}).values())


def rolling_hours(samples: list[dict], origin_ms: int | None) -> tuple[list[dict], int]:
    """For each sample time t: the calls in (t - 1 h, t] as [lo, hi]. The counter is monotone, so C(t - 1 h) lies between the last sample at or before it and the
    next one; with a fresh root the counter was 0 at origin_ms. hi = C(t) - C(last sample <= t - 1 h) is the conservative bound used for the verdict."""
    pts, decreases, run_max = [], 0, 0
    for s in samples:
        c = counter_total(s)
        if c < run_max:
            decreases += 1
        run_max = max(run_max, c)
        pts.append((s["t_ms"], run_max))
    out = []
    for i, (t, c) in enumerate(pts):
        a = t - HOUR_MS
        before = [(tt, cc) for tt, cc in pts[: i + 1] if tt <= a]
        if before:
            lo_c, hi_c = before[-1][1], None
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
        except (ValueError, KeyError, TypeError):
            notes.append("chain-credits.json is unreadable")
    numbers["calls_by_utc_day"] = by_day
    day_bad = [d for d, n in by_day.items() if n > CALLS_PER_DAY_MAX]
    spend_buckets = None
    if sp_path.exists():
        try:
            sd = json.loads(read_input(sp_path, inputs))
            spend_buckets = [[int(a), int(b)] for a, b in sd["spent"]]
            numbers["prefetch_spend_file"] = {"schema": sd.get("schema"), "calls_in_file_last_hour": sum(b for _, b in spend_buckets), "buckets": len(spend_buckets),
                                              "max_bucket": max((b for _, b in spend_buckets), default=0)}
        except (ValueError, KeyError, TypeError):
            notes.append("chain-prefetch-spend.json is unreadable")
    t_first = samples[0]["t_ms"] if samples else None
    if samples:
        rh, dec = rolling_hours(samples, starts[0]["ms"] if (starts and fresh) else None)
        numbers["samples"] = {"n": len(samples), "first": iso(t_first), "last": iso(samples[-1]["t_ms"]), "max_gap_s": round(max((b["t_ms"] - a["t_ms"] for a, b in zip(samples, samples[1:])), default=0) / 1000),
                              "counter_decreases": dec}
        hi_max = max((r["hi"] for r in rh), default=None)
        lo_max = max((r["lo"] for r in rh), default=None)
        numbers["rolling_hour_calls"] = {"max_upper_bound": hi_max, "max_lower_bound": lo_max, "limit": CALLS_PER_HOUR_MAX, "windows": len(rh),
                                         "at": iso(max(rh, key=lambda r: r["hi"])["end_ms"]) if rh else None}

        def crossing(t_ref: int) -> dict:
            w = [r for r in rh if r["end_ms"] - HOUR_MS < t_ref <= r["end_ms"]]
            return {"instant": iso(t_ref), "windows": len(w), "max_calls_upper_bound": max((r["hi"] for r in w), default=None), "max_calls_lower_bound": max((r["lo"] for r in w), default=None)}

        lo_t, hi_t = (starts[0]["ms"] if starts else samples[0]["t_ms"]), samples[-1]["t_ms"]
        mids = [m for m in range((lo_t // 86_400_000 + 1) * 86_400_000, hi_t + 1, 86_400_000)]
        numbers["hour_crossing_00_00Z"] = [crossing(m) for m in mids]
        numbers["hour_crossing_restart"] = [crossing(s["ms"]) for s in starts[1:]]
        numbers["both_processes_counted"] = "yes, both processes: the chain counter persists its per-UTC-day count in chain-credits.json and the restarted process reloads it"
        measured_cross = all(c["windows"] > 0 for c in numbers["hour_crossing_00_00Z"] + numbers["hour_crossing_restart"])
        totals = [counter_total(s) for s in samples]
        slack = 2 * max((b - a for a, b in zip(totals, totals[1:])), default=0)  # a window can end between two samples: it can exceed the sampled windows by at most two inter-sample steps
        numbers["rolling_hour_calls"]["between_samples_slack"] = slack
        if hi_max is not None and hi_max + slack <= CALLS_PER_HOUR_MAX and dec == 0 and measured_cross:
            hour_status = PASS
        elif lo_max is not None and lo_max > CALLS_PER_HOUR_MAX:
            hour_status = FAIL
        else:
            hour_status = UNM
            notes.append("rolling-hour calls are not fully bounded by the samples (upper bound over the limit, a gap, a missing crossing window, or a counter decrease)")
        cover_start = (t_first - starts[0]["ms"]) / 1000 if starts else None
        numbers["samples"]["first_sample_s_after_first_start"] = None if cover_start is None else round(cover_start)
        if starts and not fresh:
            notes.append("the first start row has seq > 0: the root is not fresh, so the counter's origin is unknown and windows before the first sample are not bounded")
    else:
        hour_status = UNM
        notes.append("no --samples file: the runner records no per-hour chain-call series (chain-credits.json holds per-UTC-day counts only, the spend file only the last hour). "
                     "Rolling hours are UNMEASURED; start the `sample` subcommand as a MiScusi job to measure them")
    day_status = (FAIL if day_bad else (PASS if by_day else UNM))
    errs = {"spend_state_reset": job.get("spend_state_reset") if job else None, "state_write_failed": job.get("state_write_failed") if job else None,
            "spend_write_errors_and_state_write_errors": "in-memory counters (prefetch.stats_, chain.counter.state_write_errors): the runner does not persist them; job-log warning lines counted above"}
    numbers["error_counters"] = errs
    if not job:
        notes.append("no --job-log: spend_state_reset / spend_write_errors / state_write_errors are UNMEASURED (in-memory only)")
    elif job.get("spend_state_reset") or job.get("state_write_failed"):
        day_status = FAIL
        notes.append("job log shows a spend-state reset or a chain state-write failure")
    if spend_buckets is not None and any(b == CALLS_PER_HOUR_MAX for _, b in spend_buckets):
        notes.append("a spend bucket of exactly 1,500 is the signature of a reset spend file (_load_spend books a full hour); look at it")
    status = FAIL if FAIL in (hour_status, day_status) else (PASS if hour_status == PASS and day_status == PASS and job else UNM)
    if hour_status == PASS and day_status == PASS and not job:
        notes.append("hour and day limits hold; the check stays UNMEASURED only because the three error counters need --job-log")
    numbers["status_parts"] = {"rolling_hour": hour_status, "per_utc_day": day_status}
    return chk("5", "chain calls <= 1,500 per rolling hour and <= 40,000 per UTC day, from the chain counter", status, numbers, notes)


def mb(v: Any) -> float | None:
    return None if v is None else round(float(v), 1)


def eval_memory(samples: list[dict], starts: list[dict], catchup_ms: int, rss_points: list[tuple[int, float]], memory_max_mb: float, host_cap_mb: float, unplanned: bool) -> dict:
    """Hourly RSS, per-process slope outside catch-up windows, peak, a 168 h projection and the QP #755 item 3 plan (A) or (B)."""
    series: dict[str, list[tuple[int, float, float | None]]] = defaultdict(list)  # key -> (t_ms, rss_mb, cgroup_mb)
    peaks = []
    for s in samples:
        for p in s.get("procs") or []:
            if isinstance(p.get("rss_mb"), (int, float)):
                series[f"pid{p.get('pid')}"].append((s["t_ms"], float(p["rss_mb"]), p.get("cg_current_mb")))
                peaks += [v for v in (p.get("hwm_mb"), p.get("cg_peak_mb")) if isinstance(v, (int, float))]
    source = "runner process VmRSS from the sample file"
    if not series and rss_points:
        series["cli"] = [(t, v, None) for t, v in sorted(rss_points)]
        source = "figures supplied on the command line (--rss-point)"
    out: dict[str, Any] = {"source": source if series else None}
    if not series:
        return chk("QP-M1", "RSS hourly, slope, peak, 7 day projection, plan (A)/(B)", UNM, {"rss_hourly": "UNMEASURED by the runner (it records no RSS)"},
                   ["start the `sample` subcommand as a MiScusi job, or pass --rss-point ISO=MB (MiScusi job memory) at least three times"])
    t0 = starts[0]["ms"] if starts else min(p[0] for v in series.values() for p in v)
    wins = [(s["ms"], s["ms"] + catchup_ms) for s in starts]
    segs, slopes, after, hourly = {}, [], [], []
    for key, pts in series.items():
        pts.sort()
        steady = [(t, v) for t, v, _ in pts if not any(a <= t < b for a, b in wins)]
        sl = lsq_slope([((t - steady[0][0]) / HOUR_MS, v) for t, v in steady]) if len(steady) >= 3 and steady[-1][0] - steady[0][0] >= 2 * HOUR_MS else None
        segs[key] = {"samples": len(pts), "first": iso(pts[0][0]), "last": iso(pts[-1][0]), "rss_after_drain_mb": mb(steady[0][1]) if steady else None,
                     "rss_last_mb": mb(pts[-1][1]), "rss_max_mb": mb(max(v for _, v, _ in pts)), "slope_mb_per_h": mb(sl), "steady_samples": len(steady),
                     "cgroup_last_mb": mb(pts[-1][2])}
        if sl is not None:
            slopes.append(sl)
        if steady:
            after.append(steady[0][1])
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
    plan: dict[str, Any] = {"read_hours": READ_HOURS, "memory_max_mb_assumed": memory_max_mb, "host_cap_mb_assumed": host_cap_mb}
    status = UNM
    if slope is not None and rss0 is not None:
        proj_remaining = cur + slope * max(0.0, READ_HOURS - elapsed_h)
        a_req = 1.5 * (rss0 + slope * READ_HOURS)
        hrs_to_max = (memory_max_mb - rss0) / slope if slope > 0 else None
        cadence = min(24.0, hrs_to_max / 2) if hrs_to_max is not None else 24.0
        plan.update({"projection_mb_current_plus_slope_x_remaining": mb(proj_remaining), "plan_A_required_memory_max_mb": mb(a_req),
                     "projected_hours_to_memory_max": None if hrs_to_max is None else round(hrs_to_max, 1), "plan_B_restart_cadence_h": round(cadence, 1),
                     "implied": "A" if a_req <= host_cap_mb else "B", "plan_B_cost": "each restart books every recovered position as an excluded engine_restart row; a book above 5% excluded is NOT_DECIDABLE; open positions per restart are not measurable with StubModel"})
        status = FAIL if (peak >= memory_max_mb or unplanned) else PASS
    if status == PASS and (max(p[-1][0] for p in series.values()) - min(p[0][0] for p in series.values())) < 20 * HOUR_MS:
        status = UNM
    out.update({"segments": segs, "peak_mb": mb(peak), "current_mb": mb(cur), "elapsed_h_since_first_start": round(elapsed_h, 2), "slope_mb_per_h_conservative_max_of_segments": mb(slope),
                "rss_after_drain_mb": mb(rss0), "plan": plan, "hourly": hourly[-60:]})
    notes = ["slope = least squares over samples outside each start's catch-up window, per process (a restart resets RSS, so segments are never pooled); the conservative figure is the largest segment slope",
             "extrapolation, not a measurement; an OOM kill or an unplanned start is a stub failure (QP #755 item 3)"]
    if status == UNM:
        notes.append("not enough steady-state samples (needs >= 3 samples over >= 2 h per process, and >= 20 h of samples overall) for a verdict")
    return chk("QP-M1", "RSS hourly, slope, peak, 7 day projection, plan (A)/(B)", status, out, notes)


# ------------------------------------------------------------------------------------------------------------------------------ trim counters (QP #745 / #755)
CUMULATIVE = ("hist_trimmed_prints", "rows_below_trim_floor", "refused_history_trimmed", "rows_dup", "filter_v_missing", "filter_v_band", "v_checked_pools", "v_missing_pools", "trim_no")


def eval_trim(root: Path, inputs: list[dict], n_starts: int) -> list[dict]:
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
    tm = sorted(float(r["trim_ms"]) for r in rows if isinstance(r.get("trim_ms"), (int, float)))
    byh: dict[int, list[dict]] = defaultdict(list)
    for r in rows:
        byh[(r.get("tape_ms") or 0) // HOUR_MS * HOUR_MS].append(r)
    per_hour = []
    for h, rs in sorted(byh.items()):
        t = sorted(float(r["trim_ms"]) for r in rs)
        per_hour.append({"tape_hour": iso(h), "trims": len(rs), "trim_ms_p50": pct(t, 0.5), "trim_ms_p99": pct(t, 0.99), "trim_ms_max": t[-1], "pools_last": rs[-1].get("pools"), "prints_held_last": rs[-1].get("prints_held")})
    segs = {}
    for eid, rs in seg.items():
        last = rs[-1]
        segs[eid] = {"lines": len(rs), "first_tape": iso(rs[0].get("tape_ms")), "last_tape": iso(last.get("tape_ms")), "trim_no_last": last.get("trim_no"),
                     **{k: last.get(k) for k in ("hist_trimmed_prints", "rows_below_trim_floor", "refused_history_trimmed", "rows_dup", "filter_v_missing", "filter_v_band", "v_checked_pools", "v_missing_pools", "pools", "prints_held")}}
    out = []
    mx = tm[-1] if tm else None
    over = sum(1 for v in tm if v > TRIM_MS_BLOCK)
    out.append(chk("QP-T1", "trim_ms max per line <= 1,000 ms (any line over BLOCKS registration until QP re-rules)", UNM if not tm else (FAIL if over else PASS),
                   {"lines": len(tm), "trim_ms_p50": pct(tm, 0.5), "trim_ms_p99": pct(tm, 0.99), "trim_ms_max": mx, "lines_over_1000": over, "blocks_registration": bool(over), "per_hour": per_hour[-30:]},
                   ["disclosure for every read: engine trim every 5 tape minutes on the row thread; a decision written in a trim tick is priced up to trim_ms later"]))
    bad = [(eid, s["rows_below_trim_floor"], s["refused_history_trimmed"]) for eid, s in segs.items()]
    line_bad = sum(1 for r in rows if (r.get("rows_below_trim_floor") or 0) > 0 or (r.get("refused_history_trimmed") or 0) > 0)
    out.append(chk("QP-T2", "NOT_DECIDABLE rule: rows_below_trim_floor == 0 and refused_history_trimmed == 0 on every line", UNM if not rows else (FAIL if line_bad else PASS),
                   {"lines_violating": line_bad, "per_engine_id_last": {eid: {"rows_below_trim_floor": a, "refused_history_trimmed": b} for eid, a, b in bad},
                    "hist_trimmed_prints_last": {eid: s["hist_trimmed_prints"] for eid, s in segs.items()}, "rows_dup_last": {eid: s["rows_dup"] for eid, s in segs.items()}},
                   ["a violation voids equivalence for every book on that engine for that segment: NOT_DECIDABLE unless an untrimmed lockstep replay of the affected hours byte-matches the book"]))
    n_ids = len(seg)
    out.append(chk("QP-T3", "trim file: monotone per engine_id; one engine_id per process start", UNM if not rows else (FAIL if mono_bad or (n_starts and n_ids > n_starts) else PASS),
                   {"lines": len(rows), "torn_tail_lines": torn, "engine_ids": n_ids, "process_starts_in_scan_log": n_starts, "non_monotone_engine_ids": len(mono_bad), "segments": segs},
                   ["expected about 288 lines per 24 h plus about 73 per start (the warm-up drain); cumulative counters are per engine_id and are never added across a restart",
                    "an engine_id with no trim line yet (a start that died before its first trim) would make engine_ids < process starts; that is reported, not failed"]))
    ratios_over, ratios_n = 0, 0
    hourly_v = []
    for eid, rs in seg.items():
        for r in rs:
            c, m = r.get("v_checked_pools") or 0, r.get("v_missing_pools") or 0
            if c > 0:
                ratios_n += 1
                ratios_over += int(m / c > V_MISSING_RATIO_BLOCK)
        # newly checked / newly missing pools per tape hour: difference of the last line of the hour and the last line of the previous hour
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
                    "share_of_newly_checked_pools_per_tape_hour": hourly_v[-30:],
                    "cause_statement": "not measured (needs the tape: how many v_missing pools never show V in the window and how many gain it later)",
                    "engine_side_counters_v_missing_and_v_out_of_band": "not measured (not persisted by the runner)"},
                   ["caveat (#749): filters evaluates every check, so a v_missing pool-scan may also fail other filters; the share lost ONLY to V is not measured and must not be stated as '44% of the universe is lost to V'"]))
    out.append(chk("QP-H1", "hook_errors prefixed 'housekeep:' == 0", UNM, {"hook_errors": "held in memory by the runner, not persisted"},
                   ["no file carries it; a housekeep failure would still show as a trim file that stops growing (QP-T3 lines vs expected)"], informational=True))
    return out


# ------------------------------------------------------------------------------------------------------------------------------ checks 7 to 10
def eval_checks_7_to_10(root: Path, L: Logs, samples: list[dict], job: dict, args: argparse.Namespace, inputs: list[dict], fresh: bool) -> list[dict]:
    out = []
    drops = sum(L.sell_drop_rows.values())
    out.append(chk("7", "sell_alert = 0 and sell_orphan = 0 (or explained); 'already has a sell' drop rows = 0", PASS if L.sell_alert == 0 and drops == 0 else FAIL,
                   {"sell_alert_rows": L.sell_alert, "sell_refused_drop_rows": dict(L.sell_drop_rows), "sell_orphan": "counter in memory; a sell_orphan is the 'no open position' / 'already has a sell' refusal rows counted here"},
                   ["decision logs only"]))
    n = len(L.starts)
    notes, status, nums = [], UNM, {"process_starts": n, "starts": [{"at": iso(s["ms"]), "lane_h": s["lane_h"], "frozen_sha": (s["frozen_sha"] or "")[:12], "clean": s["dirty"] is False} for s in L.starts]}
    chains_ok = all(a["chain_ok"] for a in L.arms.values()) and bool(L.arms)
    dups = sum(L.dup_rows.values())
    nums.update({"decision_log_chain_ok": chains_ok, "arms": {a: v["rows"] for a, v in L.arms.items()}, "duplicate_rows": dict(L.dup_rows)})
    if n < 2:
        notes.append("no restart in the log yet: the post-restart conditions cannot be evaluated")
    else:
        same = len({s["frozen_sha"] for s in L.starts}) == 1 and all(s["lane_h"] is False for s in L.starts)
        nums["start_rows_lane_h_false_and_same_frozen_sha"] = same
        dec = None
        reset_sig = None
        if samples:
            run_max, dec = 0, 0
            for s in samples:
                c = counter_total(s)
                dec += int(c < run_max)
                run_max = max(run_max, c)
            reset_sig = sum(1 for s in samples if (s.get("spend") or {}).get("max_bucket") == CALLS_PER_HOUR_MAX)
        nums["counter_decreases_in_samples"] = dec
        nums["samples_with_a_1500_spend_bucket"] = reset_sig
        nums["job_log_reset_warnings"] = job.get("spend_state_reset") if job else None
        credit_ok = (dec == 0 and not reset_sig and not (job or {}).get("spend_state_reset")) if (samples or job) else None
        parts = [same, chains_ok, dups == 0, n == 2, credit_ok if credit_ok is not None else True]
        if not all(parts):
            status = FAIL
        elif credit_ok is None:
            status = UNM
            notes.append("chain-credits / spend reload with no reset is UNMEASURED without --samples or --job-log")
        else:
            status = PASS
        if n > 2:
            notes.append("more than one restart: every start after the deliberate one is an unplanned restart (an OOM kill or crash is a stub failure)")
    out.append(chk("8", "after the restart: lane_h false, same frozen_sha, chain verifies, no duplicate rows, credit files reload with no reset", status, nums, notes))
    feed = eval_feed(L, samples, args, inputs)
    out.append(feed)
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
    sink_bst = 0
    printed = None
    if job:
        printed = job.get("register_line")
    nums = {"bots_json_lines": n_bots, "bots_json_lines_naming_BST": n_bst, "decision_log_dirs_naming_BST": len(bst_dirs), "file_or_dir_names_with_BST_under_root": name_hits,
            "register_print_seen_in_job_log": printed, "start_rows_lane_h_false": bool(L.starts) and all(s["lane_h"] is False for s in L.starts)}
    ok = n_bots == 9 and n_bst == 0 and not bst_dirs and name_hits == 0 and nums["start_rows_lane_h_false"]
    st = FAIL if (n_bst or bst_dirs or name_hits or (n_bots is not None and n_bots != 9) or printed is False) else (PASS if ok else UNM)
    out.append(chk("10", 'registration printed {"lane_h": false, "n_bots": 9}; no BST log, book or bot_id in the shakedown files', st, nums,
                   ["basis: n_bots = the 9 bots.json lines the register step appended (the print is len(regs)); the print itself is checked only with --job-log",
                    "the ledger tree is never listed (policy): a BST book could only exist for a registered bot_id, and bots.json has no BST line"]))
    return out


def eval_feed(L: Logs, samples: list[dict], args: argparse.Namespace, inputs: list[dict]) -> dict:
    t_lo = L.starts[0]["ms"] if L.starts else None
    t_hi = L.last_ms or None
    tape = guard(args.tape_dir)
    names = sorted(p.name for p in tape.iterdir()) if tape.is_dir() else []
    hours_missing, hours_n = [], 0
    if t_lo and t_hi:
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
        except ValueError:
            nums["feed_status_now"] = "unreadable"
    fsamp = [(s["t_ms"], s["feed"]) for s in samples if isinstance(s.get("feed"), dict)]
    st = UNM
    notes = ["data_loss gaps: the status 'gaps' counter covers every gap kind; the data_loss subset is not separable without scanning the tape and is reported as the all-kinds delta only"]
    if fsamp:
        paused = sum(1 for _, f in fsamp if f.get("writing_paused"))
        d = lambda k: (fsamp[-1][1].get(k) or 0) - (fsamp[0][1].get(k) or 0)
        stalls = sum(1 for (ta, a), (tb, b) in zip(fsamp, fsamp[1:]) if (b.get("lines_written") or 0) <= (a.get("lines_written") or 0) and tb - ta >= MIN_MS)
        nums["feed_samples"] = {"n": len(fsamp), "samples_with_writing_paused": paused, "lines_written_delta": d("lines_written"), "stalls_no_new_lines_between_samples": stalls,
                                "pauses_delta": d("pauses"), "rows_dropped_delta": d("rows_dropped_total"), "gaps_all_kinds_delta": d("gaps")}
        st = FAIL if (paused or hours_missing or d("pauses") or d("rows_dropped_total") or stalls) else PASS
    else:
        notes.append("no feed samples: 'writes throughout' and 'disk reserve not paused' are checked at the end state only, so the check stays UNMEASURED unless a tape hour is missing")
        if hours_missing or (isinstance(nums.get("feed_status_now"), dict) and nums["feed_status_now"].get("writing_paused")):
            st = FAIL
    if args.feed_gap:
        nums["listed_feed_gaps"] = [f"{iso(a)}/{iso(b)}" for a, b in args.feed_gap_ms]
    if st == FAIL:
        notes.append("a paused disk reserve means the shakedown is extended, not passed (SHAKEDOWN-CHECKS-2 check 9)")
    return chk("9", "feed #1207 writes throughout; data_loss gaps reported; disk reserve not paused", st, nums, notes)


# ------------------------------------------------------------------------------------------------------------------------------ job logs
def scan_job_logs(paths: list[str], inputs: list[dict]) -> dict:
    out: dict[str, Any] = {k: 0 for k in JOBLOG_MARKERS}
    out["register_line"] = False
    for p in paths:
        data = read_input(Path(p), inputs)
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
    cards = load_cards(root, inputs)
    samples = load_samples(Path(args.samples) if args.samples else None, inputs)
    job = scan_job_logs(args.job_log, inputs) if args.job_log else {}
    catchup = int(args.catchup_min * MIN_MS)
    starts = sorted(L.starts, key=lambda s: s["ms"])
    L.starts = starts
    fresh = bool(starts) and starts[0].get("seq") == 0
    windows = [(s["ms"], s["ms"] + catchup, f"start_{i}") for i, s in enumerate(starts)] + [(a, b, f"listed_feed_gap_{i}") for i, (a, b) in enumerate(args.feed_gap_ms)]
    checks, counts = eval_checks_1_to_4(L, cards, job)
    checks.append(eval_check_5(root, samples, starts, inputs, job, fresh))
    checks.append(eval_timing(L, windows))
    checks += eval_checks_7_to_10(root, L, samples, job, args, inputs, fresh)
    checks.sort(key=lambda c: (0, int(c["id"])) if c["id"].isdigit() else (1, c["id"]))
    trim = eval_trim(root, inputs, len(starts))
    unplanned = len(starts) > 2
    mem = eval_memory(samples, starts, catchup, args.rss_point_ms, args.memory_max_mb, args.host_cap_mb, unplanned)
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
    statuses = [c["status"] for c in allc if not c["informational"]]
    verdict = "FAIL" if FAIL in statuses else ("PASS" if complete and all(s == PASS for s in statuses) else "INCOMPLETE")
    if not complete and verdict == "PASS":
        verdict = "INCOMPLETE"
    record = {"schema": SCHEMA, "generated": iso(time.time() * 1000), "dir": str(base), "verdict": verdict, "complete": complete,
              "window": {"first_start": iso(starts[0]["ms"]) if starts else None, "last_decision_row": iso(L.last_ms) if L.last_ms else None, "expect_end": args.expect_end,
                         "hours_covered": round((L.last_ms - starts[0]["ms"]) / HOUR_MS, 2) if starts and L.last_ms else None},
              "blocks_registration": blockers, "checks": checks, "registration_conditions": extra, "per_persona_counts": counts,
              "statuses": dict(Counter(statuses)), "inputs": inputs, "torn_tail_files": L.torn_tails, "skipped_dirs": len(L.skipped_dirs),
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
    def short(c: dict) -> str:
        n = c["numbers"]
        if c["id"] == "2" or c["id"] == "4":
            return "; ".join(f"{p}: {n[p].get('chain_stale', n[p].get('holders_null'))}/{n[p].get('attempts', n[p].get('cards'))}" for p in PERSONAS if isinstance(n.get(p), dict))
        if c["id"] == "3":
            return f"{n['chain_unavailable_cards']} chain_unavailable, {n['pool_visible_in_logs_more_than_10min_earlier']} seen >10 min earlier"
        if c["id"] == "5":
            rh, d = n.get("rolling_hour_calls"), n.get("calls_by_utc_day")
            return f"per day {d}; rolling hour max<= {rh['max_upper_bound'] if rh else 'n/a'}"
        if c["id"] == "6":
            s, e = n["scan_wall_ms"], n["exit_check_delay_ms"]
            return f"scan p99 {s['p99']} max {s['max']}; exit delay max {e['max_of_minute_max']}, ticks>2s in {e['ticks_over_2000_between']}"
        if c["id"] == "7":
            return f"sell_alert {n['sell_alert_rows']}, drop rows {n['sell_refused_drop_rows']}"
        if c["id"] == "8":
            return f"{n['process_starts']} starts; chain ok {n['decision_log_chain_ok']}; dup rows {sum(n['duplicate_rows'].values())}"
        if c["id"] == "9":
            return f"missing tape hours {len(n['tape_hour_files_missing'])}/{n['utc_hours_in_window']}"
        if c["id"] == "10":
            return f"bots.json lines {n['bots_json_lines']}, BST {n['bots_json_lines_naming_BST']}"
        if c["id"] == "1":
            return f"as_of_refusal {n['as_of_refusal_rows']}, LeakageError {n['leakage_error_mentions_in_decision_logs']}"
        if c["id"] == "QP-T1":
            return f"trim_ms max {n.get('trim_ms_max')}, p99 {n.get('trim_ms_p99')}, lines {n.get('lines')}"
        if c["id"] == "QP-T2":
            return f"violating lines {n.get('lines_violating')}"
        if c["id"] == "QP-T3":
            return f"{n.get('lines')} lines, {n.get('engine_ids')} engine_ids vs {n.get('process_starts_in_scan_log')} starts"
        if c["id"] == "QP-V1":
            return f"{n.get('lines_over_half')}/{n.get('lines_with_checked_pools')} lines over 0.5"
        if c["id"] == "QP-M1":
            pl = n.get("plan") or {}
            return f"peak {n.get('peak_mb')} MB, slope {n.get('slope_mb_per_h_conservative_max_of_segments')} MB/h, implied plan {pl.get('implied')}"
        return ""

    lines = [f"HV4 shakedown report: verdict {rec['verdict']} (complete: {rec['complete']}), window {rec['window']['first_start']} to {rec['window']['last_decision_row']} ({rec['window']['hours_covered']} h)", "",
             "| check | status | numbers |", "| --- | --- | --- |"]
    for c in rec["checks"] + rec["registration_conditions"]:
        lines.append(f"| {c['id']} {c['name'][:70]} | {c['status']}{' (proxy)' if c['proxy'] else ''}{' (informational)' if c['informational'] else ''} | {short(c)} |")
    if rec["blocks_registration"]:
        lines += ["", "BLOCKS REGISTRATION: " + "; ".join(rec["blocks_registration"])]
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
    except (OSError, ValueError, KeyError, TypeError):
        s["chain_by_day"] = None
    try:
        sd = json.loads((root / "chain-prefetch-spend.json").read_bytes())
        rows = [[int(a), int(b)] for a, b in sd["spent"]]
        s["spend"] = {"sum_last_hour": sum(b for a, b in rows if a > s["t_ms"] - HOUR_MS), "buckets": len(rows), "max_bucket": max((b for _, b in rows), default=0)}
    except (OSError, ValueError, KeyError, TypeError):
        s["spend"] = None
    s["procs"] = [m for m in (proc_mem(p) for p in find_runner_pids(root)) if m]
    try:
        fs = json.loads(feed_status.read_bytes())
        dr = fs.get("disk_reserve") or {}
        s["feed"] = {"writing_paused": fs.get("writing_paused"), "lines_written": fs.get("lines_written"), "gaps": fs.get("gaps"), "pauses": dr.get("pauses"),
                     "rows_dropped_total": dr.get("rows_dropped_total"), "disk_free_pct": fs.get("disk_free_pct"), "updated_ms": fs.get("updated_ms")}
    except (OSError, ValueError):
        s["feed"] = None
    return s


def sampler(args: argparse.Namespace) -> int:
    base, out = guard(args.dir), guard(args.out)
    fs = guard(args.feed_status)
    stop = {"now": False}
    signal.signal(signal.SIGTERM, lambda *_: stop.update(now=True))
    until = parse_iso(args.until) if args.until else None
    while not stop["now"]:
        line = canon(take_sample(base, fs)).encode() + b"\n"
        fd = os.open(str(out), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            os.write(fd, line)
        finally:
            os.close(fd)
        if until is not None and time.time() * 1000 >= until:
            break
        time.sleep(max(1.0, args.interval_s - (time.time() % args.interval_s)))
    return 0


# ------------------------------------------------------------------------------------------------------------------------------ cli
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
    ap.add_argument("--job-log", action="append", default=[], help="a MiScusi job log; only exact marker lines are counted")
    ap.add_argument("--tape-dir", default="/data/mal/arena/tape")
    ap.add_argument("--feed-gap", action="append", default=[], help="a listed feed gap ISO/ISO (excluded from check 6)")
    ap.add_argument("--rss-point", action="append", default=[], help="ISO=MB, a MiScusi job memory figure, when no sample file exists")
    ap.add_argument("--memory-max-mb", type=float, default=DEFAULT_MEMORY_MAX_MB)
    ap.add_argument("--host-cap-mb", type=float, default=DEFAULT_MEMORY_MAX_MB, help="the largest MemoryMax a book job may use (plan A is implied only if it fits)")
    ap.add_argument("--catchup-min", type=float, default=DEFAULT_CATCHUP_MIN)
    ap.add_argument("--expect-end", default=EXPECT_END_DEFAULT)
    ap.add_argument("--out-dir", default=None, help="also write report.json and report.md here")
    a = ap.parse_args(argv)
    a.mode = "report"
    a.feed_gap_ms = [tuple(parse_iso(x) for x in g.split("/")) for g in a.feed_gap]
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
