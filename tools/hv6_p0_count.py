#!/usr/bin/env python3
"""hv6_p0_count: counts-only P0 count for HV6 (DESIGN section 10 S1-S7; QP-RULING-1025 Addendum 1 section 3; QP #914 "Item 7").

PAPER research only. NOT a runner replay: no ArenaEngine, no ledger, no registry, no RPC, no model, no key, no price, no fill, no exit, no outcome.
It imports the HV6 rule code from the checkout it is pointed at (`--repo`), which must be a CLEAN checkout at the final registration sha, and per scan instant
it makes the pre-model calls that `hv6_runner.Runner.scan` makes: pools_wsol(pumpswap) -> lite_snapshot -> hv4_cards.prescreen -> prints_15m_short (scan speed only,
decision-neutral by tools/test_hv6_scan_speed.py) -> `hv6_cards.scan` (filters, 2 h age guard, features, GEN cooldown, setups, ranking, top-8, board of 24 at :02,
X's 2/h cap). Every value stays at the frozen v0 (the count-to-value widening rule is struck): nothing here changes a scanner, board or cap value.

LABEL. Counts are pre-chain-veto UPPER BOUNDS of would-be attempts: chain vetoes (holders, authorities, extensions, Mayhem, chain stamps, quote reference) are not
applied and every tag and board pool counts as carded (so the refused-card cooldown rule never differs here). The model's choice is not applied. X's book gates
(open positions, day loss) are not applied; its 2/h cap is. DISCLOSURE below is printed in the header and the verdict.

S1 (days). Whole closed UTC days inside [2026-10-20T00:00Z, job start); at least 3; not 2026-10-19, nothing earlier; only the arena tape dir. The warm-up before the
first day is clipped at 2026-10-20T00:00Z (nothing earlier is ever read), so the first scans of a first day of 10-20 have a shorter history; choose days from 10-21.
S4 (prints). Counts only. Never a mint, pool, signature, trader, wallet, price, reserve, return, volume level, price-derived distribution or per-pool row.
Pool ids live in memory for a set difference only. Nothing is joined to HV4/Q50 output. HV4 persona configs are never run.
S7. `--fixed-first FILE` is the text written to the notebook before the job; its sha256 is printed in the header, and it must name every requested day.

OUTPUT. Canonical JSON lines, last line `sha256 <hex>` over everything before it. Timing/memory go to stderr only.
EXIT. 2: bad arguments, repo not at the expected sha or dirty, days not S1-eligible. 1: at least one day FAILED (tape hours missing). 0 otherwise.
RUN. `python3 -I tools/hv6_p0_count.py --repo <clean checkout at the final sha> --days ... --fixed-first FILE --out <HV6_ROOT>/p0/...` (see scripts/ops/hv6-p0-count.sh).
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import os
import resource
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

SHA_RE = "0123456789abcdef"  # the registered final sha is a REQUIRED argument (--expect-sha, 40 hex), never a default: the repo HEAD must equal it
HV6_ROOT = "/data/mal/arena/haiku-v6"
MIN_MS, HOUR_MS, DAY_MS = 60_000, 3_600_000, 86_400_000
SCAN_EVERY_MS = MIN_MS  # DESIGN section 2: the scanner ticks every minute at :02
SCAN_SECOND_MS = 2_000
LLM_ARMS = ("L", "L-nr", "F", "P")
FLOOR_PER_DAY = 2.0  # Am.4 item 2.1
X_PLAN_PER_DAY = 25  # DESIGN S2: if X is below this the registration states it and revises the planning figure; nothing is changed
DATA_LOSS_MAX_S = 3600
MIN_DAYS = 3
YOUNG_MAX_S = 24 * 3600
SETUP_TYPES = ("PULLBACK", "BREAKOUT", "SECOND_WAVE")
DISCLOSURE = ("P0 is an upper bound (scanner, 2 h guard, cooldown, board and X's 2/h cap only; chain vetoes, the model's choice and book gates not applied); "
              "it decides registration (floor >= 2.0/day, no zero day, gap rule) and H only; every scanner, board and cap value stays at the frozen v0. "
              "Counts on arena tape are walk-2-hour information (DEC-030 section 2 R1).")

ROW_KEEP = {
    "trade": ("side", "trader", "creator", "mint", "pool", "quote_mint", "virtual_quote_reserves", "slot", "sol_lamports", "token_raw", "quote_reserve", "base_reserve",
              "lp_fee", "pool_quote_amount"),
    "create": ("mint", "trader", "creator", "slot", "quote_reserve", "base_reserve"),
    "complete": ("mint",),
    "pool_create": ("pool", "mint", "quote_mint", "slot"),
    "pool_info": ("pool", "mint", "quote_mint"),
    "hb": (),
}
GAP_KEYS = ("kind", "from_ms", "paused_since_ms", "to_ms", "data_loss")
_GAP_EXTRA: tuple = ()


class AsOfError(RuntimeError):
    """The state clock reached the scan instant: a row at or after T was applied."""


class RepoError(RuntimeError):
    """The rule checkout is not the pinned, clean one."""


class DaysError(ValueError):
    """The day list breaks S1."""


# ------------------------------------------------------------------------------------------------------------------------------ time
def parse_day(s: str) -> int:
    return int(datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)


P0_FLOOR_MS = parse_day("2026-10-20")


def hour_key(ms: int) -> str:
    return datetime.fromtimestamp(ms // 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H")


def day_label(ms: int) -> str:
    return datetime.fromtimestamp(ms // 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def check_days(days: list[str], start_ms: int) -> list[str]:
    """S1: >= 3 distinct days, each a whole closed UTC day in [2026-10-20T00Z, job start), sorted. Raises DaysError."""
    ds = sorted(set(days))
    if len(ds) != len(days) or len(ds) < MIN_DAYS:
        raise DaysError("need_at_least_3_distinct_days")
    for d in ds:
        s = parse_day(d)
        if s < P0_FLOOR_MS:
            raise DaysError("day_before_2026-10-20")
        if s + DAY_MS > start_ms:
            raise DaysError("day_not_closed_at_job_start")
    return ds


# ------------------------------------------------------------------------------------------------------------------------------ repo
def _git(repo: str, *args: str) -> str:
    r = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise RepoError("git " + " ".join(args) + " failed")
    return r.stdout


def verify_repo(repo: str, expect_sha: str) -> str:
    head = _git(repo, "rev-parse", "HEAD").strip()
    if head != expect_sha:
        raise RepoError(f"repo HEAD {head[:12]} is not the expected sha {expect_sha[:12]}")
    if _git(repo, "status", "--porcelain").strip():
        raise RepoError("repo is dirty")
    return head


def load_rules(repo: str) -> SimpleNamespace:
    """Import tools.hv4_cards / hv6_cards / hv6_runner / arena_state from `repo` only (never from this script's own directory)."""
    sys.dont_write_bytecode = True
    repo = str(Path(repo).resolve())
    here = str(Path(__file__).resolve().parent)
    sys.path[:] = [p for p in sys.path if p and str(Path(p).resolve()) != here]
    for m in [m for m in sys.modules if m == "tools" or m.startswith("tools.")]:
        del sys.modules[m]
    sys.path.insert(0, repo)
    from tools import arena_state as AS  # noqa: PLC0415
    from tools import hv4_cards as C  # noqa: PLC0415
    from tools import hv6_cards as H  # noqa: PLC0415
    from tools import hv6_runner as RN  # noqa: PLC0415

    for mod in (AS, C, H, RN):
        if not str(Path(mod.__file__).resolve()).startswith(repo + os.sep):
            raise RepoError(f"{mod.__name__} was not imported from the repo")
    return SimpleNamespace(AS=AS, C=C, H=H, RN=RN, repo=repo)


# ------------------------------------------------------------------------------------------------------------------------------ state
def project_row(row: dict) -> dict:
    typ = row.get("type")
    out = {"type": typ, "prog": row.get("prog"), "t_recv_ms": row["t_recv_ms"]}
    if typ == "gap":
        for k in (*GAP_KEYS, *_GAP_EXTRA):
            if k in row:
                out[k] = row[k]
        return out
    for k in ROW_KEEP.get(typ, ()):
        if k in row:
            out[k] = row[k]
    return out


def make_state_class(AS: Any) -> type:
    class ProjState(AS.ArenaState):
        """ArenaState that keeps only the whitelisted fields of each row and its own gap spans."""

        def __init__(self, *a: Any, **k: Any) -> None:
            super().__init__(*a, **k)
            self.slot = 0
            self.spans: dict[tuple, list] = {}

        def _ingest(self, data: bytes) -> None:
            for ln in data.split(b"\n"):
                if not ln.strip():
                    continue
                try:
                    row = json.loads(ln)
                except ValueError:
                    self.counters["bad_lines"] += 1
                    continue
                if isinstance(row, dict) and isinstance(row.get("t_recv_ms"), int):
                    self._buf.append(project_row(row))
                else:
                    self.counters["bad_lines"] += 1

        def apply(self, row: dict) -> None:
            if row.get("type") == "trade":
                s = row.get("slot")
                if isinstance(s, int) and s > self.slot:
                    self.slot = s
            super().apply(row)

        def _gap(self, row: dict, t: int) -> None:
            super()._gap(row, t)
            prog, kind = str(row.get("prog") or "all"), row.get("kind")
            fr = next((row[k] for k in ("from_ms", "paused_since_ms") if isinstance(row.get(k), int)), t)
            to = row["to_ms"] if isinstance(row.get("to_ms"), int) else t
            sp = self.spans.setdefault((prog, kind, fr), [max(fr, to), False])
            sp[0] = max(sp[0], to)
            sp[1] = sp[1] or bool(row.get("data_loss"))

    return ProjState


def tape_hours(tape_dir: str, start_ms: int, end_ms: int) -> tuple[int, int]:
    """(present, missing) hour files in [start, end); .jsonl or .jsonl.zst."""
    d = Path(tape_dir)
    present = missing = 0
    h = start_ms // HOUR_MS * HOUR_MS
    while h < end_ms:
        k = hour_key(h)
        if (d / f"tape-{k}.jsonl").exists() or (d / f"tape-{k}.jsonl.zst").exists():
            present += 1
        else:
            missing += 1
        h += HOUR_MS
    return present, missing


# ------------------------------------------------------------------------------------------------------------------------------ counting
def _pct(sorted_vals: list[int], q: float) -> int:
    if not sorted_vals:
        return 0
    return sorted_vals[min(len(sorted_vals) - 1, int(q * len(sorted_vals)))]


class HourCounts:
    def __init__(self) -> None:
        self.scans = 0
        self.cands: list[int] = []  # filter-pass pools per scan (counts)
        self.new_pass = 0
        self.setups = collections.Counter()  # ranked, pre top-K, by GEN type
        self.tags = collections.Counter()  # top-K tags by type
        self.board_young = 0  # board cards, age 2-24 h
        self.board_old = 0  # board cards, age >= 24 h
        self.x_attempts = 0
        self.counters = collections.Counter()  # pools / filter_fail / age_guard / leaks / cooldown_blocked

    def row(self, day: str, hour: int, present: bool) -> dict:
        c = sorted(self.cands)
        return {"kind": "hour", "day": day, "hour": hour, "file_present": present, "scans": self.scans,
                "filter_pass_per_scan": {"p50": _pct(c, 0.5), "min": c[0] if c else 0, "max": c[-1] if c else 0},
                "newly_passing": self.new_pass, "setups_by_type": {t: self.setups[t] for t in SETUP_TYPES}, "tags_by_type": {t: self.tags[t] for t in SETUP_TYPES},
                "tags_total": sum(self.tags.values()), "board_age_2_24h": self.board_young, "board_age_ge_24h": self.board_old,
                "board_cards_total": self.board_young + self.board_old, "x_would_be_attempts": self.x_attempts, "scan_counts": dict(sorted(self.counters.items()))}


class Counter6:
    """Scan driver state shared across days: GEN limiter, X cap, previous candidate set (ids stay in memory, never printed)."""

    def __init__(self, R: SimpleNamespace) -> None:
        self.R = R
        self.limiter = R.C.SetupLimiter()
        self.xcap = R.H.XCap()
        self.prev: set[str] = set()


def scan_once(R: SimpleNamespace, st: Any, T: int, drv: Counter6, hc: HourCounts, prescreen: bool = True) -> None:
    C, H, RN = R.C, R.H, R.RN
    st.poll(until_ms=T - 1)
    if st.now_ms >= T:
        raise AsOfError(f"state clock {st.now_ms} is not before the scan instant {T}")
    state_now = st.now_ms
    pre = 0
    items = []
    for p in st.pools_wsol(lambda p: p.prog == "pumpswap"):
        if prescreen:
            verdict = C.prescreen(RN.lite_snapshot(p, state_now), as_of_ms=T)
            if verdict == C.PRESCREEN_GO and RN.prints_15m_short(p.events, state_now, C.PRINTS_15M_MIN):
                verdict = C.PRESCREEN_FAIL
            if verdict == C.PRESCREEN_FAIL:
                pre += 1
                continue
        snap = st.snapshot(p.id, candles=False)
        if snap is None:
            continue
        series = C.series_from_pool_candles(p.candles, state_now // MIN_MS, R.AS.CANDLE_MIN, R.AS.LAMPORTS)
        items.append((snap, C.norm_prints(list(p.events)), series))
    res = H.scan(items, as_of_ms=T, limiter=drv.limiter, x_cap=drv.xcap, board_on=True)
    res.counts["pools"] += pre
    res.counts["filter_fail"] += pre
    hc.scans += 1
    hc.cands.append(len(res.candidates))
    cur = set(res.candidates)
    hc.new_pass += len(cur - drv.prev)
    drv.prev = cur
    for t in res.ranked:
        hc.setups[t.type] += 1
    for t in res.tags:
        hc.tags[t.type] += 1
    for pid in res.board:
        a = res.feats[pid].get("age_s")
        if isinstance(a, (int, float)) and not isinstance(a, bool) and a >= YOUNG_MAX_S:
            hc.board_old += 1
        else:
            hc.board_young += 1
    for _ in res.x_tags:  # X would-be attempts at its 2/h cap (every tag counts as carded)
        drv.xcap.record(T)
        hc.x_attempts += 1
    for k, v in res.counts.items():
        if k in ("pools", "filter_fail", "age_guard", "leaks", "cooldown_blocked"):
            hc.counters[k] += v


def scan_instants(start_ms: int, end_ms: int) -> list[int]:
    first = start_ms - start_ms % SCAN_EVERY_MS + SCAN_SECOND_MS
    if first < start_ms:
        first += SCAN_EVERY_MS
    return list(range(first, end_ms, SCAN_EVERY_MS))


def gap_counts(spans: dict, start_ms: int, end_ms: int) -> dict[str, Any]:
    by_prog: dict[str, list[tuple[int, int]]] = collections.defaultdict(list)
    n_all = collections.Counter()
    kinds = collections.Counter()
    for (prog, kind, fr), (to, loss) in spans.items():
        if to < start_ms or fr >= end_ms:
            continue
        n_all[prog] += 1
        if loss:
            by_prog[prog].append((max(fr, start_ms), min(max(to, fr), end_ms)))
            kinds[f"{prog}:{kind}"] += 1
    secs = {}
    for prog, iv in by_prog.items():
        iv.sort()
        tot, cs, ce = 0, None, None
        for a, b in iv:
            if ce is None or a > ce:
                if ce is not None:
                    tot += ce - cs
                cs, ce = a, b
            else:
                ce = max(ce, b)
        if ce is not None:
            tot += ce - cs
        secs[prog] = tot / 1000.0
    ps = secs.get("pumpswap", 0.0) + secs.get("all", 0.0)
    return {"spans_any": dict(sorted(n_all.items())), "data_loss_spans": dict(sorted(kinds.items())), "data_loss_s": dict(sorted(secs.items())),
            "pumpswap_data_loss_s_incl_all": ps, "pumpswap_data_loss_le_1h": ps <= DATA_LOSS_MAX_S}


def count_day(R: SimpleNamespace, state: Any, drv: Counter6, label: str, start_ms: int, end_ms: int, prescreen: bool = True, tape_dir: str = "",
              progress: Any = None) -> tuple[list[dict], dict[str, Any]]:
    hours: dict[int, HourCounts] = {h: HourCounts() for h in range(24)}
    n = 0
    for T in scan_instants(start_ms, end_ms):
        scan_once(R, state, T, drv, hours[(T - start_ms) // HOUR_MS], prescreen=prescreen)
        n += 1
        if progress is not None and n % 120 == 0:
            progress(label, n, T)
    present = {h: tape_hours(tape_dir, start_ms + h * HOUR_MS, start_ms + (h + 1) * HOUR_MS)[0] == 1 for h in range(24)}
    rows = [hours[h].row(label, h, present[h]) for h in range(24)]
    gaps = gap_counts(state.spans, start_ms, end_ms)
    tags = sum(sum(hours[h].tags.values()) for h in hours)
    board = sum(hours[h].board_young + hours[h].board_old for h in hours)
    xs = sum(hours[h].x_attempts for h in hours)
    cands = sorted(c for h in hours for c in hours[h].cands)
    day = {"kind": "day", "day": label, "status": "OK", "scans": n, "hour_files_present": sum(present.values()), "hour_files_missing": 24 - sum(present.values()),
           "filter_pass_per_scan": {"p50": _pct(cands, 0.5), "min": cands[0] if cands else 0, "max": cands[-1] if cands else 0},
           "newly_passing": sum(hours[h].new_pass for h in hours), "setups_by_type": {t: sum(hours[h].setups[t] for h in hours) for t in SETUP_TYPES},
           "tags_by_type": {t: sum(hours[h].tags[t] for h in hours) for t in SETUP_TYPES}, "board_age_2_24h": sum(hours[h].board_young for h in hours),
           "board_age_ge_24h": sum(hours[h].board_old for h in hours), "tags_total": tags, "board_cards_total": board, "x_would_be_attempts": xs,
           "attempts_ub": {"L": tags + board, "L-nr": tags + board, "F": tags + board, "P": tags + board, "X": xs}, "gaps": gaps,
           "state_bad_lines": state.counters["bad_lines"], "state_apply_errors": state.counters["apply_errors"]}
    return rows, day


# ------------------------------------------------------------------------------------------------------------------------------ output
def canon(x: Any) -> str:
    return json.dumps(x, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def build_report(header: dict, days: list[tuple[list[dict], dict]]) -> list[dict]:
    out = [header]
    per_arm: dict[str, list[int]] = collections.defaultdict(list)
    ok = [d for _, d in days if d["status"] == "OK"]
    for hrows, d in days:
        out.extend(hrows)
        out.append(d)
        if d["status"] == "OK":
            for arm, v in d["attempts_ub"].items():
                per_arm[arm].append(v)
    floors = []
    for arm in (*LLM_ARMS, "X"):
        v = per_arm.get(arm, [])
        avg = sum(v) / len(v) if v else 0.0
        floors.append({"kind": "floor", "arm": arm, "days_ok": len(v), "total_ub": sum(v), "avg_per_day_ub": round(avg, 4), "any_day_zero": any(x == 0 for x in v) or not v,
                       "pass": bool(v) and avg >= FLOOR_PER_DAY and all(x > 0 for x in v)})
    out.extend(floors)
    all_ok = len(ok) == len(days) and len(days) >= MIN_DAYS
    gap_ok = all(d["gaps"]["pumpswap_data_loss_le_1h"] for d in ok)
    xavg = next(f["avg_per_day_ub"] for f in floors if f["arm"] == "X")
    floor_pass = bool(all_ok and all(f["pass"] for f in floors))
    out.append({"kind": "verdict", "days_requested": len(days), "days_ok": len(ok), "all_days_ok": all_ok, "gap_rule_ok": gap_ok, "floor_per_day": FLOOR_PER_DAY,
                "floor_pass_all_arms": floor_pass, "registration_ok": bool(floor_pass and gap_ok), "not_decidable": bool(not gap_ok or not all_ok),
                "x_avg_per_day_ub": xavg, "x_below_planning_figure": xavg < X_PLAN_PER_DAY, "x_planning_figure": X_PLAN_PER_DAY,
                "h_rule": "H = min(14, days from T0 to 2026-11-11T00Z) pinned by the registration; P0 changes no scanner, board or cap value", "disclosure": DISCLOSURE})
    return out


def render(rows: list[dict]) -> str:
    body = "".join(canon(r) + "\n" for r in rows)
    return body + "sha256 " + hashlib.sha256(body.encode()).hexdigest() + "\n"


def rss_mb(peak: bool = False) -> float:
    if peak:
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0
    try:
        with open("/proc/self/status") as f:
            for ln in f:
                if ln.startswith("VmRSS:"):
                    return int(ln.split()[1]) / 1024.0
    except OSError:
        pass
    return 0.0


def run(a: argparse.Namespace, R: SimpleNamespace, repo_sha: str, fixed_sha: str, log: Any = sys.stderr) -> list[dict]:
    global _GAP_EXTRA
    _GAP_EXTRA = tuple(R.AS.GAP_EXTRA)
    State = make_state_class(R.AS)
    header = {"kind": "header", "tool": "hv6_p0_count", "tool_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), "repo_sha": repo_sha, "frozen_sha": repo_sha,
              "fixed_first_sha256": fixed_sha, "days": a.days, "warmup_min": a.warmup_min, "warmup_clip_utc": "2026-10-20T00:00Z", "scan_every_ms": SCAN_EVERY_MS,
              "scan_second_ms": SCAN_SECOND_MS, "top_k_per_scan": R.H.TOP_K_PER_SCAN, "board_size": R.H.BOARD_SIZE, "board_every_ms": R.H.BOARD_EVERY_MS,
              "x_cap_per_h": R.H.X_CAP_PER_H, "min_age_s": R.H.MIN_AGE_S, "as_of": "poll(until_ms=T-1); assert state.now_ms < T",
              "chain_vetoes": "not applied (every tag and board pool counts as carded; pre-chain-veto upper bound)", "prescreen": not a.no_prescreen,
              "fields": "type,prog,t_recv_ms; trade: side,trader,creator,mint,pool,quote_mint,virtual_quote_reserves,slot,sol_lamports,token_raw,quote_reserve,base_reserve,"
                        "lp_fee,pool_quote_amount; create/pool_create/pool_info/complete: ids,quote_mint,slot,reserves; gap: kind,span stamps,counts; hb: prog"}
    results: list[tuple[list[dict], dict]] = []
    state = drv = None
    prev_end = None
    t0 = time.time()

    def progress(label: str, n: int, T: int) -> None:
        print(f"# progress day={label} scans={n} wall_s={time.time() - t0:.0f} rss_mb={rss_mb():.0f}", file=log, flush=True)

    for label in a.days:
        s = parse_day(label)
        e = s + DAY_MS
        warm_start = max(s - a.warmup_min * MIN_MS, P0_FLOOR_MS)
        _, miss = tape_hours(a.tape_dir, warm_start, e)
        if miss:
            results.append(([], {"kind": "day", "day": label, "status": "FAIL", "reason": f"tape_missing_hours={miss}"}))
            state = drv = prev_end = None
            continue
        if state is None or prev_end != s:
            state = State(a.tape_dir, start_ms=warm_start)
            drv = Counter6(R)
        rows, day = count_day(R, state, drv, label, s, e, prescreen=not a.no_prescreen, tape_dir=a.tape_dir, progress=progress)
        day["warmup_min_used"] = (s - warm_start) // MIN_MS
        results.append((rows, day))
        prev_end = e
    print(f"# done wall_s={time.time() - t0:.1f} rss_peak_mb={rss_mb(peak=True):.0f}", file=log, flush=True)
    return build_report(header, results)


def main(argv: list[str] | None = None, now_ms: int | None = None) -> int:
    ap = argparse.ArgumentParser(description="HV6 P0 counts-only (DESIGN section 10 S1-S7). Imports the rule code from --repo (clean checkout at the final sha).")
    ap.add_argument("--repo", required=True)
    ap.add_argument("--tape-dir", default="/data/mal/arena/tape")
    ap.add_argument("--days", required=True, help="comma-separated whole closed UTC days YYYY-MM-DD, >= 3, all >= 2026-10-20, none after job start")
    ap.add_argument("--fixed-first", required=True, help="the fixed-first notebook text file written before the job; must name every day; its sha256 is printed")
    ap.add_argument("--warmup-min", type=int, default=365)
    ap.add_argument("--out", default=None, help="also write the report here (must be under <HV6_ROOT>/p0/)")
    ap.add_argument("--expect-sha", required=True, help="the registered final sha (40 hex); the repo HEAD must equal it")
    ap.add_argument("--no-prescreen", action="store_true", help="full filters on every pool (the equivalence check)")
    a = ap.parse_args(argv)
    a.days = [d for d in a.days.split(",") if d]
    test = os.environ.get("MAL_ARENA_TEST") == "1"
    start_ms = now_ms if now_ms is not None else int(time.time() * 1000)
    a.expect_sha = a.expect_sha.strip().lower()
    if len(a.expect_sha) != 40 or any(ch not in SHA_RE for ch in a.expect_sha):
        print("hv6_p0_count: refused: --expect-sha must be 40 hex characters", file=sys.stderr)
        return 2
    if now_ms is not None and not test:
        print("hv6_p0_count: refused: a test clock needs MAL_ARENA_TEST=1", file=sys.stderr)
        return 2
    try:
        a.days = check_days(a.days, start_ms)
        if a.warmup_min < 1:
            raise ValueError("warmup")
        ft = Path(a.fixed_first).read_bytes()
        txt = ft.decode("utf-8", "replace")
        if any(d not in txt for d in a.days):
            raise DaysError("fixed_first_text_does_not_name_every_day")
    except (DaysError, ValueError, OSError) as e:
        print(f"hv6_p0_count: refused: {type(e).__name__} {e if isinstance(e, DaysError) else ''}".rstrip(), file=sys.stderr)
        return 2
    if a.out:
        root = os.path.realpath(HV6_ROOT + "/p0")
        out_real = os.path.realpath(a.out)
        if not test and not out_real.startswith(root + os.sep):
            print("hv6_p0_count: refused: --out must be under <HV6_ROOT>/p0/", file=sys.stderr)
            return 2
    if not test and os.path.realpath(a.tape_dir) != os.path.realpath("/data/mal/arena/tape"):
        print("hv6_p0_count: refused: only /data/mal/arena/tape", file=sys.stderr)
        return 2
    try:
        sha = verify_repo(a.repo, a.expect_sha)
        R = load_rules(a.repo)
    except (RepoError, OSError, subprocess.SubprocessError, ImportError) as e:
        print(f"hv6_p0_count: refused: {e}", file=sys.stderr)
        return 2
    try:
        rows = run(a, R, sha, hashlib.sha256(ft).hexdigest())
    except Exception as e:  # noqa: BLE001
        print(f"hv6_p0_count: error: {type(e).__name__}", file=sys.stderr)
        return 2
    try:
        verify_repo(a.repo, a.expect_sha)
    except RepoError as e:
        print(f"hv6_p0_count: refused after run: {e}", file=sys.stderr)
        return 2
    text = render(rows)
    sys.stdout.write(text)
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(text)
    return 1 if any(r.get("kind") == "day" and r.get("status") != "OK" for r in rows) else 0


if __name__ == "__main__":
    raise SystemExit(main())
