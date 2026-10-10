#!/usr/bin/env python3
"""EXP-024 read tool, forward mode: H5-BOOSTFLOOR v1, Look 1, on forward-1002 hours [2026-10-09T23, 2026-10-16T01).

Built on #476 (`tools/boostfloor_score.py`, the exploration port of RULE.md). That module keeps its own refusals and its
exploration-only scope; this file is the sealed forward read of EXP/EXP-024-h5-boostfloor-part1-prereg.md (sections 3-8, 10-12,
Amendment 1 for V, Amendment 4 for the population restriction). Section references below are to that file.

    python -m tools.boostfloor_read pins                 # blobs and pin lines only; opens no trade file
    python -m tools.boostfloor_read classify --look 1    # Am.4 B1-B4 class per V-range pool (RPC); counts only
    python -m tools.boostfloor_read precount --look 1    # P6: counts only, no fill, no P&L
    python -m tools.boostfloor_read look --look 1        # the locked read (once)

There is no flag that changes a number. Every rule, leg, cost, threshold, window, hour and path is a constant below. The CLI takes
only the subcommand and the look number.

SEAL. Every mode except `pins` refuses unless the EXP-012 FINAL marker is in the external FINAL ledger
(tools.forward_v_join.final_marker). The tool reads only the extractor's meta/paths for the section 3 hours, the P5 V files and the
P6 class file. It refuses forward-1016, walk-2, fresh-*, forward-paper, runner, shadow, canary and key paths, any hour outside the
allowlist (no `mal_catalog.check_read`: section 12, "Catalog"), any print outside the read hours, and Look 2 (walk-2 mode is a later PR).
`classify` and `precount` compute no fill, exit or P&L: they never call the pricing functions (a test checks it).

INPUT (forward mode): exactly the columns `tools/h5_forward_extract.py forward` (#562) writes, checked by name (EXTRACT_*_COLUMNS):
  extract/meta/<day>.parquet   mint, pool, mslot, mbt, s0, v, npools, blk, cslot, day, uncensored. v is the V0 of the extractor's --vmap,
                               which keeps only V-range pools (#562 load_vmap), so a pool with no V0 is not in meta at all; blk must be
                               "forward-1002"
  extract/paths/<day>.parquet  mint, slot, isbuy, sol, tok, q, b, th, bt (PRE-trade tape reserves, q without V). There is no signature or
                               event_index column, so a print is keyed by its content (slot, mint, sol, tok, q, b). `index_prints` maps
                               that key to forward-1002's raw row (slot, signature, event_index) and its joined ev V. A key that two raw
                               rows share is ambiguous: it gets no V (the missing-V rule) and no getTransaction record.
V source order (Am.1), per print: 1. forward-1002ev through `forward_v_join join` (only when P5 line A passed); 2. P5's getTransaction
records (`tools/boostfloor_inputs.py p5`), used only when the decode's (sol, tok, q, b) equal the tape's; 3. the pool account
(`boostfloor_inputs account`; V0 only: an account read at >= 10-16 cannot give V(t) at a past print); 4. none: the section 4
missing-V rule. The extractor's vmap V0 is not an Am.1 source: it is used only where no V0 is scored (`VSources.vmap_fallback`: the P5
stage, which only plans fetches, and classify), never by precount or look. Producers of the P5, P7, E1 and BOOST-PDA files: tools/boostfloor_inputs.py.

OUTPUT. Verdict and report on stderr first, then (new, empty dir) report.json and rows.csv, then the `completed` ledger line.
"""

from __future__ import annotations

if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import argparse
import csv
import hashlib
import io
import json
import math
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

import numpy as np

from tools import boostfloor_score as bf
from tools.latency_curve import Pressure, fit_curve

SCHEMA = "boostfloor_read_v1"
EXP_ID = "EXP-024"
REPO = Path(__file__).resolve().parents[1]
PREREG = "EXP/EXP-024-h5-boostfloor-part1-prereg.md"
COUNT_START_LINE = "EXP024_COUNT_START: 2026-10-10T00"
P3_PINS_PREFIX = "EXP024_P3_PINS:"
P3_PIN_KEYS = ("read_tool", "score_module", "latency_curve", "paper_curve_math", "extractor", "v_join", "synthetic_class", "inputs")
P3_PIN_PATHS = {"read_tool": "tools/boostfloor_read.py", "score_module": "tools/boostfloor_score.py", "latency_curve": "tools/latency_curve.py",
                "paper_curve_math": "tools/paper_curve_math.py", "v_join": "tools/forward_v_join.py", "synthetic_class": "tools/synthetic_class.py",
                "inputs": "tools/boostfloor_inputs.py"}  # the P5/P7/E1/PDA producers: their files feed the read, so their blob is pinned

# ---- frozen inputs (section 2) ----------------------------------------------------------------------------------------
H5_FLOWS = Path("/data/mal/hunt-1008/h5-flows")
FROZEN_SHA256 = {
    "RULE.md": bf.RULE_SHA256,
    "s14_boostdip.py": "70becfb7d7e48b8e24cee9ae807db29200db9990cf96d18ab2dce755301c1ad0",
    "s15_score_dip.py": "b91febe61613c2e94792c627bd5bc0cbe54b52f6a7beb874f7af8ed9f3410e89",
    "common.py": "8061d4c3ebd57d0aee75c0356a23f9048b3272624547670cdf8604a273fb56d9",
}

# ---- fixed locations (no CLI override) -------------------------------------------------------------------------------
ROOT = Path("/data/mal/exp024")
LOOK_READS = ROOT / "LOOK_READS.jsonl"
FINAL_LEDGER = Path("/data/mal/exp012-forward/FINAL_READS.jsonl")
FORWARD_1002 = Path("/data/mal/blocks/forward-1002")
A3_DAILY = Path("/data/mal/structure-monitor/daily.jsonl")


@dataclass(frozen=True)
class Layout:
    """Where one look's inputs and outputs live. Production = Layout.for_look(ROOT, 1). Tests build one on a temp dir."""

    root: Path
    look: int

    @classmethod
    def for_look(cls, root: Path, look: int) -> "Layout":
        return cls(root, look)

    @property
    def look_dir(self) -> Path: return self.root / f"look{self.look}"
    @property
    def extract(self) -> Path: return self.look_dir / "extract"
    @property
    def p5(self) -> Path: return self.root / "p5"
    @property
    def vjoin(self) -> Path: return self.p5 / "vjoin"  # forward_v_join join --out-dir
    @property
    def cross_source(self) -> Path: return self.p5 / "cross_source.json"  # {"line_a_pass": bool, counts...}
    @property
    def gettx_v(self) -> Path: return self.p5 / "gettx_v.jsonl"  # boostfloor_inputs p5: {key, slot, signature, event_index, status, decoded}
    @property
    def account_v0(self) -> Path: return self.p5 / "account_v0.json"  # {pool: V0 lamports} from exp012_forward_vmap (`_v0`: v_base, or the stored V when the account has no pending counters)
    @property
    def account_map(self) -> Path: return self.p5 / "account" / "map.json"  # exp012_forward_vmap fetch --new, at or after 10-16T00Z
    @property
    def line_a_sample(self) -> Path: return self.p5 / "line_a_sample.jsonl"  # the cross-source check's prints (ids in the file only)
    @property
    def p7_sample(self) -> Path: return self.p5 / "p7_sample.jsonl"  # P7's 1,000 prints, chosen outcome-blind by `inputs p5`
    @property
    def boost_pda(self) -> Path: return self.p5 / "boost_pda.jsonl"  # section 13: per-pool BOOST vault-authority slices (`inputs boost-pda`)
    @property
    def classes(self) -> Path: return self.root / "p6" / f"classes-look{self.look}.jsonl"  # {pool, mint, class, reason}
    @property
    def precount(self) -> Path: return self.root / "p6" / f"precount-look{self.look}.json"
    @property
    def p7(self) -> Path: return self.root / "p7" / "pricing_check.json"  # {"pass": bool, ...}
    @property
    def e1(self) -> Path: return self.root / "e1" / "calibration.json"  # E1 output (EXP-022 section 8), copied with its sha256 recorded
    @property
    def out(self) -> Path: return self.look_dir / "out"
    @property
    def lock(self) -> Path: return self.root / f"look{self.look}.lock"
    @property
    def ledger(self) -> Path: return self.root / "LOOK_READS.jsonl"


# ---- windows and hours (section 3) ------------------------------------------------------------------------------------
def _ts(h: str) -> int:
    return int(datetime.strptime(h, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc).timestamp())


def _hour(t: float) -> str:
    return time.strftime("%Y-%m-%dT%H", time.gmtime(int(t)))


def _date(t: float) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(int(t)))


LOOK1 = dict(count_lo="2026-10-10T00", count_hi="2026-10-16T00", read_lo="2026-10-09T23", read_hi="2026-10-16T01",
             alpha=0.020, deadline="2026-10-17T12:00:00Z", a3_run_date="2026-10-16", n_dates=6, min_pos_dates=4)
READ_HOURS = tuple(_hour(t) for t in range(_ts(LOOK1["read_lo"]), _ts(LOOK1["read_hi"]), 3600))
COUNT_HOURS = tuple(_hour(t) for t in range(_ts(LOOK1["count_lo"]), _ts(LOOK1["count_hi"]), 3600))
READ_DAYS = tuple(sorted({h[:10] for h in READ_HOURS}))
BLOCK = "forward-1002"
FORBIDDEN = ("fresh-0802", "fresh-0808", "fresh-0828", "forward-1016", "forward-walk2", "forward_walk2", "walk-2", "walk2", "forward-paper",
             "forward-walk", "oracle-live", "exp012-gate", "arm-audit", "runner-status", "heartbeat", "positions", "/var/lib/mal/paper",
             "h5-shadow", "h5_shadow", "h5-executor", "h5_executor", "probe-final", "canary", ".env", "helius", "keypair", "id.json")

# ---- the rule's numbers and section 4-8 constants (no CLI override) ---------------------------------------------------
STAKE = 100_000_000.0  # deciding stake, 0.1 SOL
PRIO = bf.PRIO_LAMPORTS  # 55,000 per send
EXIT_S = bf.EXIT_AFTER_S0_S  # 330 s
CELLS = {  # name: (entry s, sell lag s, guard)
    "D": (1.9, 0.55, False),
    "B1": (3.0, 1.35, False),
    "B2": (1.9, 0.55, True),
}
REPORT_LEGS = {"R_rule_1p3": (1.3, 0.55, False)}  # section 13: the rule's original entry (report-only)
REPORT_ONLY = {  # section 13, never deciding: name -> price_cell keyword arguments (correction "max" unless named)
    "R_rule_1p3": dict(cell=(1.3, 0.55, False)),
    "R_1p9_lag1p35": dict(cell=(1.9, 1.35, False)),
    "R_3p0_lag0p55": dict(cell=(3.0, 0.55, False)),
    **{f"R_exit_{x}": dict(cell=(1.9, 0.55, False), exit_s=float(x)) for x in (310, 320, 335, 340, 345, 350)},
    **{f"R_stake_{k}": dict(cell=(1.9, 0.55, False), stake=v) for k, v in (("0p05", 5e7), ("0p25", 2.5e8), ("0p5", 5e8))},
    "D_literal_v0": dict(cell=(1.9, 0.55, False), literal_v0=True),
    "D_no_correction": dict(cell=(1.9, 0.55, False), corr="none"),
    "D_only_a": dict(cell=(1.9, 0.55, False), corr="a"),
    "D_only_b": dict(cell=(1.9, 0.55, False), corr="b"),
}
NOT_COMPUTED: tuple[str, ...] = ()  # section 13 items this build does not compute (none since the producers PR)
SECTION13_ROWS = ("D_start", "D_worse_start_end", "D_sell_retry", "C_post_boost")  # report-only rows beyond REPORT_ONLY
SELL_RETRY_S = 2.0  # sell-retry stress: a failed sell is retried every 2 s at +55,000 lamports
SELL_RETRY_MAX = 30  # attempts priced (60 s); the probability mass left after attempt 30 is priced at attempt 30
CONTROL_WINDOW_S = (360.0, 600.0)  # post-BOOST control: the trigger's price condition (a sell leaving Q <= 40 SOL) in [360, 600] s
CONTROL_EXIT_S = 840.0  # ... held to s0 + 840 s (s0-relative, as the rule's 330 s exit). No BOOST-budget condition: BOOST is over by then
BOOST_ENDED_MAX_SHARE = 0.15  # no-live: BOOST ended before our exit landed on > 15% of the traded pools (section 11)
EXTRACT_META_COLUMNS = ("mint", "pool", "mslot", "mbt", "s0", "v", "npools", "blk", "cslot", "day", "uncensored")  # #562 meta, in order
EXTRACT_PATHS_COLUMNS = ("mint", "slot", "isbuy", "sol", "tok", "q", "b", "th", "bt")  # #562 paths, in order
GUARD = 0.15  # B2 min_out = floor(cp_buy_out(0.1 SOL, Q_i, B_i, tier) / 1.15)
HAIRCUT = 1 - (1 - 0.002608) * (1 - 0.0016)  # correction (a), on sell proceeds after the pool fee
E1_PROBE_STAKE = 50_000_000.0  # r-bar is per 0.05 SOL trip; (b) = max(0, -r-bar) * stake / 0.05 SOL (= -2 r-bar at 0.1 SOL)
E1_MIN_N = 20
RENT = 2_039_280  # charged only when the sell cannot fill the full balance (never under the constant-product END sell)
PENDING_HI = 0.0383  # missing V(t): pending at the exit state = 3.83% of effective quote, 0 at entry (section 4)
V0_UNKNOWN_CASES = (17.5e9, 17.7e9)
SLOT_SWITCH = 454_896_000  # first slot of epoch 1053 (section 4)
FLAT_FAIL = bf.FLAT_FAIL
BOOT_DRAWS, BOOT_SEED = 1_000, 1
REPORT_P_DRAWS = 10_000
MIN_TRIGGERS, MIN_TRIGGER_DATES = 100, 5
MAX_MISSING_V_SHARE = 0.01
MAX_UNCLASSIFIED_SHARE = 0.01
MAX_BAD_HOUR_SHARE = 0.05
LAST_SLICE_MIN_S = 330.0
A3_FLAGS = ("pins_changed", "boost_disabled", "boost_share_low", "boost_last_slice_early", "boost_budget_or_slices_changed")  # Am.4 C1
DISCLOSURE = ("By the owner's decision (10-08), the live canary's and the shadow detector's outcomes for pools inside Look 1's window were "
              "observed in real time (EXP-024 section 3.1), and canary and shadow outcomes for pools with s0 before 2026-10-10T00 were observed "
              "in real time from about 2026-10-08T21:30Z (shadow) and from the first canary send (canary) (Amendment 3). Concurrent observation "
              "can have influenced later choices such as an owner scale-up. It does not change the verdict. The live canary's own trades are in "
              "the tape as ordinary rows and are not removed (section 3).")


class Refused(Exception):
    pass


# ---- refusals -----------------------------------------------------------------------------------------------------------
def refuse_name(path: str | Path) -> None:
    s = str(path)
    for bad in FORBIDDEN:
        if bad in s:
            raise Refused(f"{s}: refused ({bad!r} is not an EXP-024 Look 1 input)")


def refuse_hour(hour: str) -> None:
    if hour not in READ_HOURS:
        raise Refused(f"hour {hour}: refused (Look 1 reads forward-1002 [{LOOK1['read_lo']}, {LOOK1['read_hi']}) only)")


def refuse_day(day: str) -> None:
    if day not in READ_DAYS:
        raise Refused(f"day {day!r}: refused (not a day of the Look 1 read hours)")


def refuse_look(look: int) -> None:
    if look != 1:
        raise Refused(f"look {look}: refused (this build reads Look 1 only; Look 2 reads walk 2 after EXP-022's read ends and needs its own reviewed mode)")


# ---- integrity: section 0 line, prereg clean, frozen files, P3 pins (section 12, Refusal) -----------------------------
def git_blob(path: Path) -> str:
    data = path.read_bytes()
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def check_count_start(text: str) -> None:
    n = len(re.findall(r"(?m)^" + re.escape(COUNT_START_LINE) + r"$", text))
    if n != 1:
        raise Refused(f"section 0: the line {COUNT_START_LINE!r} matches {n} times, not once")


def parse_p3_pins(text: str) -> dict[str, tuple[str, str]]:
    """The P3 amendment's pin line: `EXP024_P3_PINS: key=path@<40-hex blob> ...` for every key in P3_PIN_KEYS, exactly once."""
    lines = [ln for ln in text.splitlines() if ln.startswith(P3_PINS_PREFIX)]
    if len(lines) != 1:
        raise Refused(f"P3: {len(lines)} lines start with {P3_PINS_PREFIX!r} in {PREREG}; the dated P3 amendment must carry exactly one")
    pins: dict[str, tuple[str, str]] = {}
    for tok in lines[0][len(P3_PINS_PREFIX):].split():
        m = re.fullmatch(r"([a-z_0-9]+)=([A-Za-z0-9_./-]+)@([0-9a-f]{40})", tok)
        if not m:
            raise Refused(f"P3: pin token {tok!r} is not key=path@blob")
        pins[m.group(1)] = (m.group(2), m.group(3))
    missing = [k for k in P3_PIN_KEYS if k not in pins]
    if missing:
        raise Refused(f"P3: pin line lacks {missing}")
    for k, p in P3_PIN_PATHS.items():
        if pins[k][0] != p:
            raise Refused(f"P3: pin {k} names {pins[k][0]}, expected {p}")
    return pins


def check_pins(pins: Mapping[str, tuple[str, str]], repo: Path) -> None:
    for k, (rel, blob) in pins.items():
        got = git_blob(repo / rel)
        if got != blob:
            raise Refused(f"P3/P4: {rel} is blob {got}, pinned {blob}")


def check_frozen(h5_flows: Path) -> None:
    for name, want in FROZEN_SHA256.items():
        p = h5_flows / name
        if not p.is_file() or sha256_file(p) != want:
            raise Refused(f"section 2: {p} does not hash to {want[:12]}...")


MONITOR_PATH = "tools/pump_structure_monitor.py"
MONITOR_BLOB = "1ca0a88cecf0853d94336ea046ba1a910b79f198"  # Am.4 B1 and section 10: the classifier and the A3 flag meanings


def check_monitor(repo: Path) -> None:
    """Am.4 B1 / section 10: `classify` runs the monitor through synthetic_class, and the A3 flags mean what this blob writes."""
    p = repo / MONITOR_PATH
    got = git_blob(p) if p.is_file() else None
    if got != MONITOR_BLOB:
        raise Refused(f"{MONITOR_PATH} is blob {got}, not {MONITOR_BLOB} (Am.4 B1, section 10)")


def check_clean(repo: Path, rel_paths: Iterable[str]) -> None:
    for rel in rel_paths:
        r = subprocess.run(["git", "-C", str(repo), "status", "--porcelain", "--", rel], capture_output=True, text=True, timeout=60)
        if r.returncode != 0 or r.stdout.strip():
            raise Refused(f"{rel} is not clean against HEAD")


def integrity(repo: Path = REPO, h5_flows: Path = H5_FLOWS) -> dict[str, Any]:
    text = (repo / PREREG).read_text(encoding="utf-8")
    check_count_start(text)
    pins = parse_p3_pins(text)
    check_clean(repo, [PREREG, MONITOR_PATH] + [p for p, _ in pins.values()])
    check_pins(pins, repo)
    check_monitor(repo)
    check_frozen(h5_flows)
    head = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=60).stdout.strip()
    return {"head": head, "pins": {k: f"{p}@{b}" for k, (p, b) in sorted(pins.items())}, "monitor": f"{MONITOR_PATH}@{MONITOR_BLOB}"}


# ---- conditions (section 8.1) ---------------------------------------------------------------------------------------------
def read_e1(path: Path) -> tuple[int, float]:
    if not path.is_file():
        raise Refused(f"E1 not recorded: {path} is missing (condition (c))")
    agg = json.loads(path.read_text(encoding="utf-8"))["aggregate"]["faa3192"]["pnl_gap_lamports_live_minus_sim"]
    n, mean = int(agg["n"]), agg["mean"]
    if n < E1_MIN_N or mean is None:
        raise Refused(f"E1 n = {n} < {E1_MIN_N}: no look runs (section 5); a look not run by its deadline is NOT_DECIDABLE")
    return n, float(mean)


def hour_states(walk_dir: Path, hours: Sequence[str]) -> dict[str, str]:
    from tools.forward_v_join import hour_state

    return {h: hour_state(walk_dir, h, strict=True)[1] for h in hours}


def a3_conditions(daily: Path, *, lo: str, run_date: str) -> dict[str, Any]:
    """Five halt flags one by one (Am.4 C1), a core rule unevaluated on two consecutive runs, and the median of the daily last-slice
    medians over the window. Refuses until the run of `run_date` (the 10-16 06:41Z run) is in the file. Reads no outcome."""
    recs = []
    for line in daily.read_text(encoding="utf-8").splitlines() if daily.is_file() else []:
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if isinstance(r, dict) and str(r.get("run_utc", "")) >= lo:
            recs.append(r)
    recs.sort(key=lambda r: str(r["run_utc"]))
    if not any(str(r["run_utc"])[:10] == run_date for r in recs):
        raise Refused(f"condition (f): no A3 run dated {run_date} in {daily} yet")
    halts, unevaluated2, prev_ne = [], [], set()
    meds = []
    for r in recs:
        flags = ((r.get("halt") or {}).get("flags") or {})
        ne = set()
        for f in A3_FLAGS:
            v = flags.get(f) or {}
            if v.get("halt"):
                halts.append(f"{r['run_utc']} {f}")
            if v.get("evaluated") is False or f not in flags:
                ne.add(f)
        unevaluated2 += [f"{r['run_utc']} {f}" for f in sorted(ne & prev_ne)]
        prev_ne = ne
        m = ((r.get("boost") or {}).get("last_slice_after_migrate_s") or {}).get("median")
        if m is not None:
            meds.append(float(m))
    if not meds:
        raise Refused("section 11: no A3 run in the window carries a last-slice median; cannot evaluate the 330 s condition")
    return {"runs": len(recs), "halts": halts, "unevaluated_two_runs": unevaluated2, "last_slice_median_s": float(np.median(meds)),
            "synthetic_share_high": [str(r["run_utc"]) for r in recs if (((r.get("halt") or {}).get("flags") or {}).get("synthetic_share_high") or {}).get("halt")]}


# ---- lock and ledger (section 12) -----------------------------------------------------------------------------------------
def ledger_events(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    text = path.read_text(encoding="utf-8")
    if text and not text.endswith("\n"):
        raise Refused(f"{path} ends with a torn line")
    return [json.loads(ln) for ln in text.splitlines() if ln.strip()]


def ledger_allows(events: Sequence[Mapping[str, Any]], look: int) -> None:
    for e in events:
        if e.get("experiment") != EXP_ID:
            continue
        if e.get("look") == look and e.get("event") == "started":
            raise Refused(f"Look {look} already started at {e.get('utc')}: a look runs once")
        if e.get("event") != "completed" or look <= e.get("look", 0):
            continue
        if e.get("verdict") in ("PASS", "FAIL_FUTILITY", "NOT_DECIDABLE_HALT"):
            raise Refused(f"Look {look}: refused after Look {e.get('look')} ended {e.get('verdict')}")
        if e.get("futility") is True or e.get("p7_failed") is True:  # section 8.3 futility; section 10, a failed P7 closes both looks
            raise Refused(f"Look {look}: refused after Look {e.get('look')} ended {e.get('verdict')} with futility={e.get('futility')} "
                          f"p7_failed={e.get('p7_failed')}")


def append_event(path: Path, rec: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(fd, (json.dumps(dict(rec), sort_keys=True) + "\n").encode())
        os.fsync(fd)
    finally:
        os.close(fd)


def take_lock(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
    except FileExistsError:
        raise Refused(f"{path} exists: the look's O_EXCL lock was already taken") from None
    os.write(fd, f"{os.getpid()} {datetime.now(timezone.utc).isoformat()}\n".encode())
    os.close(fd)


def write_new(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)


# ---- V sources (Am.1) -----------------------------------------------------------------------------------------------------
Key = tuple[int, str, int, int, int, int]  # (slot, mint, sol, tok, q, b): a print's content. #562's paths carry no signature


def content_key(slot: Any, mint: Any, sol: Any, tok: Any, q: Any, b: Any) -> Key:
    return (int(slot), str(mint), *(int(x) if type(x) is int else int(round(float(x))) for x in (sol, tok, q, b)))  # type: ignore[return-value]


def raw_key(r: Mapping[str, Any]) -> Key | None:
    try:
        return content_key(r["slot"], r["mint"], r["sol_lamports"], r["token_raw"], r["quote_reserve"], r["base_reserve"])
    except (KeyError, TypeError, ValueError):
        return None


@dataclass(frozen=True)
class PrintRef:
    slot: int
    signature: str | None  # None: two raw rows share the content key (ambiguous: no V, no fetch)
    event_index: int | None
    ev_v: int | None  # forward-1002ev's V through the join; None when the join gave none


def index_prints(base_dir: Path, vjoin_dir: Path, mints: set[str], hours: Sequence[str] = READ_HOURS,
                 rows_of: Callable[[str], Iterable[Mapping[str, Any]]] | None = None) -> tuple[dict[Key, PrintRef], list[str]]:
    """forward-1002's raw PumpSwap rows of `mints` in the read hours, by content key, with the joined ev V. It opens trade files, so callers
    check the FINAL first. An hour that is not walked or stops on an unreadable line is listed and its rest skipped (Coverage marks it bad)."""
    from tools import forward_v_join as J

    refuse_name(base_dir)
    refuse_name(vjoin_dir)
    rows_of = rows_of or (lambda h: J.iter_joined_rows(base_dir, vjoin_dir, h))
    out: dict[Key, PrintRef] = {}
    skipped: list[str] = []
    for h in hours:
        refuse_hour(h)
        try:
            for r in rows_of(h):
                if r.get("venue") != "pumpswap" or r.get("mint") not in mints:
                    continue
                k = raw_key(r)
                if k is None:
                    continue
                v = r.get("virtual_quote_reserves")
                ei = r.get("event_index")
                ref = PrintRef(k[0], str(r.get("signature")), int(ei) if type(ei) is int else None, v if type(v) is int else None)
                out[k] = PrintRef(k[0], None, None, None) if k in out else ref
        except (J.HourReadError, ValueError):
            skipped.append(h)
    return out, skipped


def ev_map(index: Mapping[Key, PrintRef]) -> dict[Key, int]:
    return {k: r.ev_v for k, r in index.items() if r.ev_v is not None and r.signature is not None}


@dataclass
class VSources:
    ev: dict[Key, int] | None  # None: P5 line A failed (or absent), so forward-1002ev is not used
    gettx: dict[Key, int]
    account_v0: dict[str, int]
    vmap_fallback: bool = False  # True only at the P5 stage (fetch planning) and classify: the extractor's vmap V0 is not an Am.1 source

    def v(self, key: Any) -> tuple[float, str]:
        if self.ev is not None and key in self.ev:
            return float(self.ev[key]), "ev"
        if key in self.gettx:
            return float(self.gettx[key]), "gettx"
        return math.nan, "none"

    def v0(self, key: Any, pool: str, vmap_v0: float = math.nan) -> tuple[float, str]:
        v, src = self.v(key)
        if src != "none":
            return v, src
        if pool in self.account_v0:
            return float(self.account_v0[pool]), "account"
        if self.vmap_fallback and vmap_v0 == vmap_v0:
            return float(vmap_v0), "extract_vmap"
        return math.nan, "none"


def load_gettx(path: Path) -> dict[Key, int]:
    """P5 source 2: a record gives V only when its fetch decoded the print and the decode's (sol, tok, q, b) equal the tape print's."""
    out: dict[Key, int] = {}
    if not path.is_file():
        return out
    for ln in path.read_text(encoding="utf-8").splitlines():
        if ln.strip():
            r = json.loads(ln)
            v = (r.get("decoded") or {}).get("virtual_quote_reserves")
            if r.get("status") == "ok" and r.get("fields_equal") is True and type(v) is int:
                out[content_key(*r["key"])] = v
    return out


def load_vsources(lay: Layout, index: Mapping[Key, PrintRef]) -> VSources:
    cs = json.loads(lay.cross_source.read_text(encoding="utf-8")) if lay.cross_source.is_file() else None
    if cs is None:
        raise Refused(f"P5 not done: {lay.cross_source} is missing (condition (e))")
    acct = json.loads(lay.account_v0.read_text(encoding="utf-8")) if lay.account_v0.is_file() else {}
    return VSources(ev_map(index) if cs.get("line_a_pass") is True else None, load_gettx(lay.gettx_v),
                    {str(k): int(v) for k, v in acct.items() if type(v) is int})


def load_pda(path: Path) -> dict[str, dict[str, Any]]:
    if not path.is_file():
        return {}
    return {str(r["pool"]): r for r in (json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()) if "pool" in r}


# ---- pools (extractor format) ---------------------------------------------------------------------------------------------
@dataclass
class FwdPool:
    mint: str
    pool: str
    s0: int
    s0_bt: int
    mbt: int
    v0: float  # NaN: unknown
    v0_src: str
    cls: str  # synthetic | non_synthetic | unclassified
    path: bf.PoolPath
    vt: np.ndarray  # V(t) per print (the stored V of the print's event), NaN when no source has it
    vt_src: list[str] = field(default_factory=list)
    sigs: dict[str, str] = field(default_factory=dict)  # locate hints for classify (Am.4 B4); #562's meta has none, so classify finds them
    keys: list[Any] = field(default_factory=list)  # content key per print (index into P5's records)
    pda: dict[str, Any] | None = None  # section 13: BOOST vault-authority slices (boost_pda.jsonl), report-only


def classify_from(tape_pcb: bool, rpc_class: str | None) -> str:
    """Am.4 B2: the tape can only mark synthetic; the class otherwise comes from getTransaction (classify); none -> unclassified."""
    if tape_pcb:
        return "synthetic"
    return rpc_class if rpc_class in ("synthetic", "non_synthetic") else "unclassified"


def load_classes(path: Path) -> dict[str, str]:
    if not path.is_file():
        raise Refused(f"P6 classes missing: {path} (run `classify --look 1` after the FINAL)")
    out: dict[str, str] = {}
    for ln in path.read_text(encoding="utf-8").splitlines():
        if ln.strip():
            r = json.loads(ln)
            out[str(r["pool"])] = str(r["class"])
    return out


def load_pools(lay: Layout, vs: VSources, classes: Mapping[str, str]) -> list[FwdPool]:
    """Every meta row of the read days (pandas, lazily). Refuses a forbidden name, a foreign block, a day or print outside the read hours."""
    import pandas as pd

    refuse_name(lay.extract)
    pools: list[FwdPool] = []
    lo, hi = _ts(LOOK1["read_lo"]), _ts(LOOK1["read_hi"])
    for day in READ_DAYS:
        mp, pp = lay.extract / "meta" / f"{day}.parquet", lay.extract / "paths" / f"{day}.parquet"
        if not mp.is_file() and not pp.is_file():
            continue
        refuse_day(day)
        meta, paths = pd.read_parquet(mp), pd.read_parquet(pp)
        for f, df, cols in ((mp, meta, EXTRACT_META_COLUMNS), (pp, paths, EXTRACT_PATHS_COLUMNS)):
            if tuple(df.columns) != cols:
                raise Refused(f"{f}: columns {list(df.columns)} are not the extractor's {list(cols)} (#562)")
        if set(meta.blk.astype(str)) - {BLOCK}:
            raise Refused(f"{mp}: block {sorted(set(meta.blk.astype(str)) - {BLOCK})} refused (forward-1002 only)")
        if len(paths) and (int(paths.bt.min()) < lo or int(paths.bt.max()) >= hi):
            raise Refused(f"{pp}: a print lies outside [{LOOK1['read_lo']}, {LOOK1['read_hi']})")
        for t in meta.mbt:
            if int(t) >= lo:  # an earlier `complete` is not read: the pool fails Coverage below
                refuse_hour(_hour(int(t)))
        groups = {m: g for m, g in paths.groupby("mint", sort=False)}
        for r in meta.itertuples(index=False):
            g = groups.get(r.mint)
            if g is None:
                continue
            g = g[g.slot >= int(r.s0)]  # the extractor's order (slot, tx_index, event_index, ...) is kept: no re-sort
            if not len(g):
                continue
            keys = [content_key(*t) for t in zip(g.slot, [r.mint] * len(g), g.sol, g.tok, g.q, g.b)]
            vv = [vs.v(k) for k in keys]
            v0, v0_src = vs.v0(keys[0], str(r.pool), float(r.v) if r.v == r.v else math.nan)
            pools.append(FwdPool(
                mint=str(r.mint), pool=str(r.pool), s0=int(r.s0), s0_bt=int(g.bt.iloc[0]), mbt=int(r.mbt), v0=v0, v0_src=v0_src,
                cls=classify_from(bool(getattr(r, "pcb", False)), classes.get(str(r.pool))),
                path=bf.PoolPath(sl=g.slot.values.astype(np.int64), q=g.q.values.astype(float), b=g.b.values.astype(float),
                                 isb=g.isbuy.values.astype(bool), sol=g.sol.values.astype(float), tok=g.tok.values.astype(float),
                                 th=g.th.values.astype(np.uint64), bt=g.bt.values.astype(np.int64)),
                vt=np.array([x[0] for x in vv], float), vt_src=[x[1] for x in vv],
                sigs={k: str(getattr(r, k, "") or "") for k in ("s0_sig", "mig_sig", "cmp_sig")}, keys=keys))
    return pools


def extract_mints(lay: Layout) -> set[str]:
    import pandas as pd

    refuse_name(lay.extract)
    out: set[str] = set()
    for day in READ_DAYS:
        mp = lay.extract / "meta" / f"{day}.parquet"
        if mp.is_file():
            refuse_day(day)
            out.update(pd.read_parquet(mp, columns=["mint"]).mint.astype(str))
    return out


def forward_pools(lay: Layout, classes: Mapping[str, str], *, p5_stage: bool = False) -> tuple[list[FwdPool], dict[Key, PrintRef]]:
    """Production loader: index the raw prints, take V by the source order, attach the BOOST PDA records. `p5_stage`: before P5 exists,
    V is forward-1002ev's alone (Am.1: the cross-source sample's triggers are found with the joined V)."""
    index, _ = index_prints(FORWARD_1002, lay.vjoin, extract_mints(lay))
    vs = VSources(ev_map(index), {}, {}, vmap_fallback=True) if p5_stage else load_vsources(lay, index)  # load_vsources: Am.1 only
    pools = load_pools(lay, vs, classes)
    pda = load_pda(lay.boost_pda)
    for p in pools:
        p.pda = pda.get(p.pool)
    return pools, index


# ---- structure (no prices): universe, trigger, exclusions. Used by precount and look alike -------------------------------
@dataclass
class Trig:
    v0: float
    i: int  # trigger print index
    qpost: np.ndarray  # tape quote + V0, post-trade
    bpost: np.ndarray


@dataclass
class Struct:
    status: str  # outside | v_range | synthetic | unclassified | short | unsorted | sps | coverage | slot_switch | no_trigger | trigger
    date: str = ""
    sps: float = math.nan
    trigs: list[Trig] = field(default_factory=list)  # one per V0 case (one when V0 is known)
    v0_unknown: bool = False


def _in_vrange(v0: float) -> bool:
    return bf.V_LO <= v0 <= bf.V_HI


def structure(p: FwdPool, good_hours: frozenset[str]) -> Struct:
    lo, hi = _ts(LOOK1["count_lo"]), _ts(LOOK1["count_hi"])
    if not (lo <= p.s0_bt < hi):
        return Struct("outside")
    v0_unknown = not (p.v0 == p.v0)
    if not v0_unknown and not _in_vrange(p.v0):
        return Struct("v_range")
    d = _date(p.s0_bt)
    if p.cls != "non_synthetic":
        return Struct(p.cls, d, v0_unknown=v0_unknown)
    sl = p.path.sl
    if len(sl) < bf.MIN_PATH_PRINTS:
        return Struct("short", d, v0_unknown=v0_unknown)
    if np.any(np.diff(sl) < 0):
        return Struct("unsorted", d, v0_unknown=v0_unknown)
    sps = bf.seconds_per_slot(sl, p.path.bt)
    if not bf.sps_ok(sps):
        return Struct("sps", d, sps, v0_unknown=v0_unknown)
    exit_slot = p.s0 + int(round(EXIT_S / sps))
    last_exit = exit_slot + max(math.ceil(lag / sps - 1e-9) for _, lag, _ in list(CELLS.values()) + list(REPORT_LEGS.values()))
    exit_bt = p.s0_bt + (last_exit - p.s0) * sps
    need = [_hour(t) for t in range(_ts(_hour(p.mbt)), int(exit_bt) + 1, 3600)] + [_hour(exit_bt)]
    if any(h not in good_hours for h in need):
        return Struct("coverage", d, sps, v0_unknown=v0_unknown)
    if p.s0 < SLOT_SWITCH <= last_exit:
        return Struct("slot_switch", d, sps, v0_unknown=v0_unknown)
    trigs = find_triggers(p, sps, V0_UNKNOWN_CASES if v0_unknown else (p.v0,))
    return Struct("trigger" if trigs else "no_trigger", d, sps, trigs, v0_unknown)


def find_triggers(p: FwdPool, sps: float, v0s: Sequence[float]) -> list[Trig]:
    """The rule's trigger on tape quote + V0 (section 4, pinned), once per V0 case. No price is computed."""
    sl, isb, sol = p.path.sl, p.path.isb, np.asarray(p.path.sol, float)
    boost = bf.detect_boost_wallet(sl, p.s0, p.path.th, sol, isb)
    spent = bf.boost_spent(p.path.th, sol, boost)
    trigs = []
    for v0 in v0s:
        qpost, bpost = bf.post_trade_state(p.path.q + v0, p.path.b, isb, sol, p.path.tok)
        i = bf.find_trigger(sl, p.s0, sps, isb, qpost, spent)
        if i is not None:
            trigs.append(Trig(v0, i, qpost, bpost))
    return trigs


def precount(pools: Sequence[FwdPool], good_hours: frozenset[str], bad_hours: Sequence[str]) -> dict[str, Any]:
    """P6 (section 10, Am.4 C2): counts only. No fill, exit or P&L is computed here."""
    per: dict[str, dict[str, int]] = {}
    tot: dict[str, int] = {}
    vrange_in_window = unclassified = 0
    for p in pools:
        s = structure(p, good_hours)
        if s.status in ("outside", "v_range"):
            tot[s.status] = tot.get(s.status, 0) + 1
            continue
        vrange_in_window += 1
        unclassified += s.status == "unclassified"
        row = per.setdefault(s.date, {})
        cls = p.cls if p.cls in ("synthetic", "unclassified") else "non_synthetic"
        for k in sorted({cls, s.status} | ({"null_v0"} if s.v0_unknown else set())):
            row[k] = row.get(k, 0) + 1
            tot[k] = tot.get(k, 0) + 1
    trig_dates = sorted(d for d, r in per.items() if r.get("trigger", 0) > 0)
    n_trig = tot.get("trigger", 0)
    return {"good_hours": len(good_hours & set(COUNT_HOURS)), "bad_count_hours": len(bad_hours), "count_hours": len(COUNT_HOURS),
            "complete_events": len(pools), "vrange_pools_in_window": vrange_in_window, "unclassified": unclassified,
            "totals": dict(sorted(tot.items())), "per_date": {d: dict(sorted(r.items())) for d, r in sorted(per.items())},
            "triggers": n_trig, "trigger_dates": len(trig_dates),
            "refuse_p6": n_trig < MIN_TRIGGERS or len(trig_dates) < MIN_TRIGGER_DATES,
            "unclassified_share": unclassified / vrange_in_window if vrange_in_window else 0.0,
            "bad_hour_share": len(bad_hours) / len(COUNT_HOURS)}


# ---- pricing (look only) --------------------------------------------------------------------------------------------------
def state_index(sl: np.ndarray, slot: int) -> int:
    """END bound: index of the print whose PRE-trade state is the state after every print in slots <= slot; len(sl) = after the last."""
    return int(np.searchsorted(sl, slot, "right"))


def round_trip(qe: float, be: float, qx: float, bx: float, stake: float) -> dict[str, float]:
    """bf.fill_round_trip, returning the pieces the correction and the guard need. Same arithmetic, in the same order."""
    f = bf.tier_fee(qe, be)
    net = stake * (1 - f)
    tk = be * net / (qe + net)
    q2, b2 = qx + net, bx - tk
    proceeds = tk * q2 / (b2 + tk) * (1 - bf.tier_fee(q2, b2 + tk))
    return {"tokens": tk, "proceeds": proceeds, "pnl_raw": proceeds - stake - 2 * PRIO, "gross": (q2 / b2) / (qe / be) - 1}


def cp_buy_out(stake: float, q: float, b: float) -> float:
    net = stake * (1 - bf.tier_fee(q, b))
    return b * net / (q + net)


def correction(proceeds: float, stake: float, rbar: float, which: str = "max") -> float:
    a = proceeds * HAIRCUT
    b = max(0.0, -rbar) * stake / E1_PROBE_STAKE
    return {"max": max(a, b), "a": a, "b": b, "none": 0.0}[which]


def price_cell(p: FwdPool, s: Struct, cell: tuple[float, float, bool], *, rbar: float, stake: float = STAKE, literal_v0: bool = False,
               corr: str = "max", exit_s: float = EXIT_S, bound: str = "end", sell_delay_slots: int = 0,
               touch: set[int] | None = None) -> dict[str, Any] | None:
    """One pool, one leg. Lower P&L over the V0 cases (unknown V0) and the missing-V(t) cases (section 4). None: no exit after landing.
    bound "start" (section 13): our buy and sell land before every print of their slot. sell_delay_slots moves the sell (retry stress).
    touch: collect the landing- and exit-state print indices and return None without pricing anything (P5's fetch plan)."""
    entry_s, lag_s, guard = cell
    sl, q, b = p.path.sl, p.path.q, p.path.b
    sol, isb = np.asarray(p.path.sol, float), p.path.isb
    qtp, btp = bf.post_trade_state(q, b, isb, sol, p.path.tok)  # tape-only post-trade, for the state after the last print
    exit_slot = p.s0 + int(round(exit_s / s.sps))
    best: dict[str, Any] | None = None
    for tr in s.trigs:
        landing = int(sl[tr.i]) + math.ceil(entry_s / s.sps - 1e-9)
        xl = exit_slot + math.ceil(lag_s / s.sps - 1e-9) + sell_delay_slots
        if exit_slot <= landing:
            continue

        def st(slot: int) -> tuple[float, float, int]:
            j = state_index(sl, slot) if bound == "end" else int(np.searchsorted(sl, slot, "left"))
            return (float(q[j]), float(b[j]), j) if j < len(sl) else (float(qtp[-1]), float(btp[-1]), len(sl) - 1)

        (qe_t, be, je), (qx_t, bx, jx) = st(landing), st(xl)
        if touch is not None:
            touch.update((je, jx))
            continue
        missing = False
        if literal_v0:
            cases = [(tr.v0, tr.v0)]
        else:
            ve = p.vt[je] if p.vt[je] == p.vt[je] else tr.v0
            if p.vt[jx] == p.vt[jx]:
                vxs = [float(p.vt[jx])]
            else:
                vxs = [tr.v0, tr.v0 - PENDING_HI * (qx_t + tr.v0) / (1 + PENDING_HI)]
            missing = not (p.vt[je] == p.vt[je]) or len(vxs) > 1
            cases = [(float(ve), vx) for vx in vxs]
        ssb, nb = bf.pressure_at(sl, isb, sol, landing, s.sps)
        min_out = math.floor(cp_buy_out(stake, float(tr.qpost[tr.i]), float(tr.bpost[tr.i])) / (1 + GUARD)) if guard else None
        for ve, vx in cases:
            rt = round_trip(qe_t + ve, be, qx_t + vx, bx, stake)
            reverted = guard and rt["tokens"] < min_out
            if reverted:
                pnl, c, rent = -float(PRIO), 0.0, 0
            else:
                c = correction(rt["proceeds"], stake, rbar, corr)
                rent = RENT if rt["tokens"] >= bx else 0
                pnl = rt["pnl_raw"] - c - rent
            row = dict(pnl_lamports=pnl, reverted=bool(reverted), correction_lamports=c, rent_lamports=rent, gross=rt["gross"],
                       landing_slot=landing, exit_landing_slot=xl, ssb=ssb, nb_lamports=nb, v_missing=bool(missing or s.v0_unknown),
                       v_entry_minus_v0=ve - tr.v0, v_exit_minus_v0=vx - tr.v0, trig_t_s=float((sl[tr.i] - p.s0) * s.sps),
                       q_trig_sol=float(tr.qpost[tr.i] / 1e9), sps=s.sps)
            if best is None or row["pnl_lamports"] < best["pnl_lamports"]:
                best = row
    return best


def apply_fail(rows: list[dict[str, Any]]) -> None:
    """Flat and pressure legs in place. The curve's intercept is refit on this cell's own sends (section 6). A reverted B2 buy is -55,000
    under both legs (section 5)."""
    if not rows:
        return None
    curve = fit_curve([Pressure(int(r["ssb"]), int(r["nb_lamports"])) for r in rows])
    for r in rows:
        p = curve.p(Pressure(int(r["ssb"]), int(r["nb_lamports"])))
        r["p_fail"] = p
        if r["reverted"]:
            r["pnl_flat_lamports"] = r["pnl_press_lamports"] = -float(PRIO)
        else:
            r["pnl_flat_lamports"] = (1 - FLAT_FAIL) * r["pnl_lamports"] + FLAT_FAIL * (-PRIO)
            r["pnl_press_lamports"] = (1 - p) * r["pnl_lamports"] + p * (-PRIO)


# ---- statistics (section 7) -----------------------------------------------------------------------------------------------
    return curve


# ---- section 13 (report-only, never deciding) -------------------------------------------------------------------------------
def control_struct(p: FwdPool, s: Struct, good_hours: frozenset[str]) -> Struct | None:
    """Post-BOOST control: the first sell in [360, 600] s that leaves Q (tape quote + V0) <= 40 SOL, held to s0 + 840 s, with D's entry and
    lag. None when the pool has no such print, its hours to the control's exit are not all good, or it straddles the slot switch."""
    if s.status not in ("trigger", "no_trigger"):
        return None
    sl, isb, sps = p.path.sl, p.path.isb, s.sps
    last = p.s0 + int(round(CONTROL_EXIT_S / sps)) + math.ceil(CELLS["D"][1] / sps - 1e-9)
    exit_bt = p.s0_bt + (last - p.s0) * sps
    need = [_hour(t) for t in range(_ts(_hour(p.mbt)), int(exit_bt) + 1, 3600)] + [_hour(exit_bt)]
    if any(h not in good_hours for h in need) or p.s0 < SLOT_SWITCH <= last:
        return None
    t = (sl - p.s0) * sps
    trigs = []
    for v0 in (V0_UNKNOWN_CASES if s.v0_unknown else (p.v0,)):
        qpost, bpost = bf.post_trade_state(p.path.q + v0, p.path.b, isb, np.asarray(p.path.sol, float), p.path.tok)
        idx = np.flatnonzero((qpost / 1e9 <= bf.Q_STAR_SOL) & (t >= CONTROL_WINDOW_S[0]) & (t <= CONTROL_WINDOW_S[1]) & (~isb))
        if len(idx):
            trigs.append(Trig(v0, int(idx[0]), qpost, bpost))
    return Struct("trigger", s.date, sps, trigs, s.v0_unknown) if trigs else None


def sell_retry(p: FwdPool, s: Struct, curve: Any, rbar: float) -> dict[str, Any] | None:
    """Sell-retry stress on D: attempt k lands k * ceil(2 s / sps) slots after D's sell and fails with its own pressure p (D's refit curve,
    the sell's landing slot); each retry adds 55,000 lamports. Expected P&L over the attempts; the mass left after the last attempt is priced
    there. The buy's fail legs apply on top, as for every leg."""
    step = math.ceil(SELL_RETRY_S / s.sps - 1e-9)
    sl, isb, sol = p.path.sl, p.path.isb, np.asarray(p.path.sol, float)
    first, total, mass, attempts = None, 0.0, 1.0, 0.0
    for k in range(SELL_RETRY_MAX):
        r = price_cell(p, s, CELLS["D"], rbar=rbar, sell_delay_slots=k * step)
        if r is None:
            return None
        first = first or r
        pk = 0.0 if k == SELL_RETRY_MAX - 1 else float(curve.p(Pressure(*(int(x) for x in bf.pressure_at(sl, isb, sol, r["exit_landing_slot"], s.sps)))))
        total += mass * (1 - pk) * (r["pnl_lamports"] - k * PRIO)
        attempts += mass * (1 - pk) * (k + 1)
        mass *= pk
    return {**first, "pnl_lamports": total, "sell_attempts_expected": attempts}


def detector_vs_pda(p: FwdPool) -> str:
    """The rule's tape detector against the per-pool PDA: agree when every detected BOOST buy's slot is a slot of the vault authority's
    signature list (which also holds the funding migrate and 0-2 non-keeper txs, so it is a superset test)."""
    pda = p.pda
    sl, sol, isb = p.path.sl, np.asarray(p.path.sol, float), p.path.isb
    det = bf.detect_boost_wallet(sl, p.s0, p.path.th, sol, isb)
    if pda is None or "error" in pda:
        return "pda_unknown"
    slots = set(int(x) for x in pda.get("slice_slots") or [])
    if det is None:
        return "both_none" if not slots else "pda_only"
    mine = set(int(x) for x in sl[(p.path.th == np.uint64(det)) & isb & ((sl - p.s0) <= bf.BOOST_WINDOW_SLOTS)])
    if not slots:
        return "detector_only"
    return "agree" if mine <= slots else "disagree"


def mechanism(d_rows: Sequence[Mapping[str, Any]], pools: Mapping[str, FwdPool]) -> dict[str, Any]:
    """Section 13 mechanism on D's trades: each pool's BOOST last slice (PDA, verified keeper slice) against our exit landing time, the two
    groups' means, and the detector-vs-PDA table. A pool with no slice at all had BOOST end at 0 s. An unknown pool counts as ended in the
    upper share, which the no-live rule uses (it can only remove support)."""
    groups: dict[str, list[Mapping[str, Any]]] = {"ended": [], "running": [], "unknown": []}
    last: dict[str, float | None] = {}
    table: dict[str, int] = {}
    for r in d_rows:
        p = pools[r["mint"]]
        exit_bt = r["s0_bt"] + (r["exit_landing_slot"] - p.s0) * r["sps"]
        pda = p.pda
        if pda is None or "error" in pda or (not pda.get("no_slices") and pda.get("last_bt") is None):
            g, lt = "unknown", None
        else:
            lt = float(p.s0_bt) if pda.get("no_slices") else float(pda["last_bt"])
            g = "ended" if lt < exit_bt else "running"
        groups[g].append(r)
        last[r["mint"]] = None if lt is None else lt - p.s0_bt
        k = detector_vs_pda(p)
        table[k] = table.get(k, 0) + 1
    n = len(d_rows)
    known = len(groups["ended"]) + len(groups["running"])
    means = {g: {leg: (float(np.mean([x[f"pnl_{leg}_lamports"] for x in rs]) / 1e9) if rs else None) for leg in ("flat", "press")}
             for g, rs in groups.items()}
    vals = sorted(v for v in last.values() if v is not None)
    return {"n_trades": n, "ended_before_exit": len(groups["ended"]), "running_at_exit": len(groups["running"]), "unknown": len(groups["unknown"]),
            "share_ended_known": len(groups["ended"]) / known if known else None,
            "share_ended_upper": (len(groups["ended"]) + len(groups["unknown"])) / n if n else None, "group_mean_sol": means,
            "last_slice_after_s0_s": {"n": len(vals), "median": float(np.median(vals)) if vals else None}, "detector_vs_pda": dict(sorted(table.items()))}



def _betacf(a: float, b: float, x: float) -> float:
    tiny, qab, qap, qam = 1e-300, a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    d = 1.0 / (d if abs(d) > tiny else tiny)
    h = d
    for m in range(1, 400):
        m2 = 2 * m
        for aa in (m * (b - m) * x / ((qam + m2) * (a + m2)), -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))):
            d = 1.0 + aa * d
            d = 1.0 / (d if abs(d) > tiny else tiny)
            c = 1.0 + aa / c
            c = c if abs(c) > tiny else tiny
            h *= d * c
        if abs(d * c - 1.0) < 1e-15:
            break
    return h


def betainc(a: float, b: float, x: float) -> float:
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    lbt = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log1p(-x)
    if x < (a + 1) / (a + b + 2):
        return math.exp(lbt) * _betacf(a, b, x) / a
    return 1.0 - math.exp(lbt) * _betacf(b, a, 1 - x) / b


def t_sf(t: float, df: float) -> float:
    """P(T >= t), Student's t with df degrees of freedom."""
    if math.isinf(t):
        return 0.0 if t > 0 else 1.0
    p = 0.5 * betainc(df / 2.0, 0.5, df / (df + t * t))
    return p if t >= 0 else 1.0 - p


def boot_means(x: np.ndarray, draws: int, seed: int) -> np.ndarray:
    """Trade bootstrap: means of `draws` resamples of n of n with replacement, rng.integers(0, n, (draws, n)) read in chunks."""
    n, rng, out = len(x), np.random.default_rng(seed), np.empty(draws)
    step = max(1, 4_000_000 // max(1, n))
    for lo in range(0, draws, step):
        hi = min(draws, lo + step)
        out[lo:hi] = x[rng.integers(0, n, size=(hi - lo, n))].mean(1)
    return out


def day_level(x_sol: np.ndarray, dates: Sequence[str]) -> dict[str, Any]:
    d = np.asarray(dates)
    ud = sorted(set(dates))
    m = np.array([x_sol[d == u].mean() for u in ud])
    w = len(m)
    if w < 2:
        return {"W": w, "t": None, "p": 1.0}
    sd = float(np.std(m, ddof=1))
    if sd == 0:
        return {"W": w, "t": None, "p": 0.0 if m.mean() > 0 else 1.0}
    t = float(m.mean() / (sd / math.sqrt(w)))
    return {"W": w, "t": t, "p": t_sf(t, w - 1)}


def leg_stats(pnl_lamports: Sequence[float], dates: Sequence[str]) -> dict[str, Any]:
    x = np.asarray(pnl_lamports, float) / 1e9
    if len(x) == 0:
        return {"n": 0}
    d = np.asarray(dates)
    ud = sorted(set(dates))
    per_day = {u: float(x[d == u].sum()) for u in ud}
    srt = np.sort(x)[::-1]
    bm = boot_means(x, BOOT_DRAWS, BOOT_SEED)
    return dict(n=int(len(x)), dates=len(ud), dates_pos=sum(v > 0 for v in per_day.values()), mean_sol=float(x.mean()),
                total_sol=float(x.sum()), ci5_sol=float(np.percentile(bm, 5)), ex_top3_sol=float(x.sum() - srt[:3].sum()),
                ex_best_date_sol=float(x.sum() - max(per_day.values())), day_level=day_level(x, dates), per_date_sol=per_day,
                median_sol=float(np.median(x)))


def cell_items(st: dict[str, Any], alpha: float) -> dict[str, bool]:
    if st.get("n", 0) == 0:
        return dict(n_ge_100=False, dates_ge_5_majority_pos=False, ci5_gt_0=False, ex_top3_gt_0=False, ex_best_date_gt_0=False, day_p_le_alpha=False)
    return dict(n_ge_100=st["n"] >= 100, dates_ge_5_majority_pos=st["dates"] >= 5 and 2 * st["dates_pos"] > st["dates"],
                ci5_gt_0=st["ci5_sol"] > 0, ex_top3_gt_0=st["ex_top3_sol"] > 0, ex_best_date_gt_0=st["ex_best_date_sol"] > 0,
                day_p_le_alpha=st["day_level"]["p"] <= alpha)


def verdict(cells: Mapping[str, Mapping[str, Any]], alpha: float) -> dict[str, Any]:
    """Section 7 and 8.3 on the corrected cells. cells[name][leg] = leg_stats, leg in (flat, press)."""
    D = cells["D"]
    items = {leg: cell_items(D[leg], alpha) for leg in ("flat", "press")}
    p_dec = max(D["flat"].get("day_level", {}).get("p", 1.0), D["press"].get("day_level", {}).get("p", 1.0))
    for leg in ("flat", "press"):  # item 6: the larger p decides
        items[leg]["day_p_le_alpha"] = D[leg].get("n", 0) > 0 and p_dec <= alpha
    binding = {c: {leg: bool(cells[c][leg].get("n", 0) > 0 and cells[c][leg]["mean_sol"] > 0 and cells[c][leg]["ex_top3_sol"] > 0)
                   for leg in ("flat", "press")} for c in ("B1", "B2")}
    futility = all(D[leg].get("n", 0) == 0 or D[leg]["mean_sol"] <= 0 for leg in ("flat", "press"))
    ok = all(all(v.values()) for v in items.values()) and all(all(v.values()) for v in binding.values())
    return {"verdict": "PASS" if ok else ("FAIL_FUTILITY" if futility else "FAIL"), "items_D": items, "binding": binding,
            "day_level_p_deciding": p_dec, "alpha": alpha, "futility": futility}


def missing_v_check(cells_rows: Mapping[str, list[dict[str, Any]]]) -> list[str]:
    """Section 4: missing-V trades > 1% of the look's trades (D), or in the top 3 of D or a binding leg under either fail leg."""
    why = []
    D = cells_rows["D"]
    if D and sum(r["v_missing"] for r in D) > MAX_MISSING_V_SHARE * len(D):
        why.append(f"missing V on {sum(r['v_missing'] for r in D)} of {len(D)} D trades (> 1%)")
    for c, rows in cells_rows.items():
        for leg in ("flat", "press"):
            top = sorted(rows, key=lambda r: -r[f"pnl_{leg}_lamports"])[:3]
            if any(r["v_missing"] for r in top):
                why.append(f"a missing-V trade is in the top 3 of {c} ({leg})")
    return why


def rows_md5(cells_rows: Mapping[str, list[dict[str, Any]]]) -> str:
    """Section 8.4 / 12 Fixes: md5 over (cell, mint, pnl rounded to the lamport) of every binding row, in a fixed order."""
    h = hashlib.md5()
    for c in sorted(cells_rows):
        for r in sorted(cells_rows[c], key=lambda r: r["mint"]):
            h.update(f"{c},{r['mint']},{round(r['pnl_lamports'])}\n".encode())
    return h.hexdigest()


# ---- the look -------------------------------------------------------------------------------------------------------------
@dataclass
class LookInputs:
    pools: list[FwdPool]
    good_hours: frozenset[str]
    bad_hours: list[str]
    rbar: float
    e1_n: int
    a3: dict[str, Any]
    p7_pass: bool
    precount_file: dict[str, Any] | None
    e1_sha256: str | None = None  # sha256 of e1/calibration.json, carried into the report's e1 block


def not_decidable_reasons(pc: Mapping[str, Any], a3: Mapping[str, Any], p7_pass: bool) -> list[str]:
    """Section 11 and Am.4 B3 conditions that hold before any outcome. Any one makes the look NOT_DECIDABLE with no P&L computed."""
    why = []
    if pc["refuse_p6"]:
        why.append(f"P6: {pc['triggers']} triggers on {pc['trigger_dates']} dates (need >= {MIN_TRIGGERS} and >= {MIN_TRIGGER_DATES})")
    if not p7_pass:
        why.append("P7 pricing check failed")
    if pc["unclassified_share"] > MAX_UNCLASSIFIED_SHARE:
        why.append(f"Am.4 B3: {pc['unclassified']} of {pc['vrange_pools_in_window']} V-range pools unclassified (> 1%)")
    if pc["bad_hour_share"] > MAX_BAD_HOUR_SHARE:
        why.append(f"coverage: {pc['bad_count_hours']} of {pc['count_hours']} window hours bad (> 5%)")
    if a3["halts"]:
        why.append(f"A3 halt: {a3['halts']}")
    if a3["unevaluated_two_runs"]:
        why.append(f"A3 core rule unevaluated on two consecutive runs: {a3['unevaluated_two_runs']}")
    if a3["last_slice_median_s"] < LAST_SLICE_MIN_S:
        why.append(f"BOOST last-slice median {a3['last_slice_median_s']} s < 330 s")
    return why


def _guard(report: dict[str, Any], name: str, fn: Callable[[], Any]) -> Any:
    """Section 13 is report-only: an exception there is recorded in report_only_errors and the block is left out. It never aborts the look."""
    try:
        return fn()
    except Exception as e:  # noqa: BLE001 - report-only code never decides or burns the look
        report.setdefault("report_only_errors", {})[name] = f"{type(e).__name__}: {e}"
        return None


def report_only_stats(c: str, rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Section 13 statistics of one row set (report-only)."""
    out: dict[str, Any] = {}
    for leg in ("flat", "press"):
        x = np.asarray([r[f"pnl_{leg}_lamports"] for r in rows], float)
        if len(x):
            bm = boot_means(x / 1e9, REPORT_P_DRAWS, BOOT_SEED)
            cl = bf.gate_stats(x, [r["date"] for r in rows], STAKE)
            stake = REPORT_ONLY.get(c, {}).get("stake", STAKE)
            order = np.argsort([r["s0_bt"] for r in rows], kind="stable")
            xs, half = x[order], len(x) // 2
            win = np.minimum(x, stake)  # winsorised at +100% of the stake
            wdays: dict[str, float] = {}
            for r, w in zip(rows, win):
                wdays[r["date"]] = wdays.get(r["date"], 0.0) + w
            gross = np.array([r["gross"] for r in rows])
            keep = np.sort(x)[: len(x) - int(math.floor(0.05 * len(x)))]
            out[leg] = dict(trade_boot_p_le_0=float((bm <= 0).mean()), date_cluster_ci90_pct=[cl["ci5_pct"], cl["ci95_pct"]],
                            n=int(len(x)), mean_sol=float(x.mean() / 1e9), median_sol=float(np.median(x) / 1e9),
                            winsor100_mean_sol=float(win.mean() / 1e9), winsor100_dates_pos=sum(d > 0 for d in wdays.values()),
                            mean_ex_top5pct_sol=float(keep.mean() / 1e9) if len(keep) else None,
                            pnl_share_gross_gt_100pct=float(x[gross > 1.0].sum() / x.sum()) if x.sum() != 0 else None,
                            first_half_mean_sol=float(xs[:half].mean() / 1e9) if half else None,
                            second_half_mean_sol=float(xs[half:].mean() / 1e9), stake_lamports=stake)
    out["reverted"] = sum(r["reverted"] for r in rows)
    out["v_missing"] = sum(r["v_missing"] for r in rows)
    return out


def compute_look(inp: LookInputs, log: Callable[[str], None]) -> dict[str, Any]:
    """Everything after the lock. Prices only when no pre-outcome NOT_DECIDABLE condition holds. The verdict is computed before any section 13
    (report-only) code runs, and an exception in section 13 is recorded, never raised."""
    pc = precount(inp.pools, inp.good_hours, inp.bad_hours)
    if inp.precount_file is not None and inp.precount_file != pc:
        raise Refused("P6: the recorded precount differs from the look's own recount (same hours, same files): refusing")
    pre = not_decidable_reasons(pc, inp.a3, inp.p7_pass)
    report: dict[str, Any] = {"schema": SCHEMA, "experiment": EXP_ID, "look": 1, "precount": pc, "a3": inp.a3, "e1": {"n": inp.e1_n, "rbar": inp.rbar, "sha256": inp.e1_sha256},
                              "disclosure": DISCLOSURE}
    if pre:
        report.update(verdict="NOT_DECIDABLE", reasons=pre, outcomes_computed=False)
        return report
    # deciding: D, B1 and B2 are priced, failed, scored and checked before any section 13 code runs
    structs = [(p, structure(p, inp.good_hours)) for p in inp.pools]
    cells_rows: dict[str, list[dict[str, Any]]] = {c: [] for c in CELLS}
    traded: list[tuple[FwdPool, Struct]] = []
    for p, s in structs:
        if s.status != "trigger":
            continue
        traded.append((p, s))
        base = dict(mint=p.mint, date=s.date, s0_bt=p.s0_bt)
        for c, cell in CELLS.items():
            r = price_cell(p, s, cell, rbar=inp.rbar)
            if r is not None:
                cells_rows[c].append({**base, **r})
    curves = {c: apply_fail(rows) for c, rows in cells_rows.items()}
    stats = {c: {leg: leg_stats([r[f"pnl_{leg}_lamports"] for r in rows], [r["date"] for r in rows]) for leg in ("flat", "press")}
             for c, rows in cells_rows.items()}
    v = verdict(stats, LOOK1["alpha"])
    mv = missing_v_check(cells_rows)
    if mv:
        v = {**v, "verdict": "NOT_DECIDABLE", "reasons": mv}
    report.update(verdict=v["verdict"], decision=v, cells=stats, rows_md5=rows_md5(cells_rows), outcomes_computed=True,
                  n_rows={c: len(r) for c, r in cells_rows.items()}, report_only_errors={})
    # section 13, report-only: every block is guarded, so an exception there is recorded and never aborts or decides the look
    extra: dict[str, list[dict[str, Any]]] = {}

    def failed(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        apply_fail(rows)
        return rows

    def c_post_boost() -> list[dict[str, Any]]:
        out = []
        for p, s in structs:
            ctrl = control_struct(p, s, inp.good_hours)
            if ctrl is not None:
                r = price_cell(p, ctrl, CELLS["D"], rbar=inp.rbar, exit_s=CONTROL_EXIT_S)
                if r is not None:
                    out.append({**dict(mint=p.mint, date=s.date, s0_bt=p.s0_bt), **r})
        return failed(out)

    def priced(cell: tuple[float, float, bool], **kw: Any) -> list[dict[str, Any]]:
        out = []
        for p, s in traded:
            r = price_cell(p, s, cell, rbar=inp.rbar, **kw)
            if r is not None:
                out.append({**dict(mint=p.mint, date=s.date, s0_bt=p.s0_bt), **r})
        return failed(out)

    def worse_start_end() -> list[dict[str, Any]]:
        by_end = {r["mint"]: r for r in cells_rows["D"]}
        return failed([dict(min((by_end[r["mint"]], r), key=lambda x: x["pnl_lamports"])) for r in extra["D_start"] if r["mint"] in by_end])

    def d_sell_retry() -> list[dict[str, Any]]:
        out = []
        if curves.get("D") is not None:
            for p, s in traded:
                r = sell_retry(p, s, curves["D"], inp.rbar)
                if r is not None:
                    out.append({**dict(mint=p.mint, date=s.date, s0_bt=p.s0_bt), **r})
        return failed(out)

    blocks: list[tuple[str, Callable[[], list[dict[str, Any]]]]] = [("C_post_boost", c_post_boost),
                                                                     ("D_start", lambda: priced(CELLS["D"], bound="start"))]
    for name, kw0 in REPORT_ONLY.items():
        kw = dict(kw0)
        blocks.append((name, lambda cell=kw.pop("cell"), kw=kw: priced(cell, **kw)))
    blocks += [("D_worse_start_end", worse_start_end), ("D_sell_retry", d_sell_retry)]
    for name, fn in blocks:
        rows = _guard(report, name, fn)
        if rows is not None:
            extra[name] = rows
    rep_only: dict[str, Any] = {}
    for c, rows in list(cells_rows.items()) + list(extra.items()):
        st = _guard(report, f"report_only:{c}", lambda c=c, rows=rows: report_only_stats(c, rows))
        if st is not None:
            rep_only[c] = st
    mech = _guard(report, "mechanism", lambda: mechanism(cells_rows["D"], {p.mint: p for p, _ in traded}))
    no_live = [f"340 s exit leg mean <= 0 ({leg})" for leg in ("flat", "press")
               if rep_only.get("R_exit_340", {}).get(leg, {}).get("mean_sol", 0.0) <= 0]
    if not all(v["binding"]["B2"].values()):
        no_live.append("binding leg B2 failed")
    if mech is not None and mech["share_ended_upper"] is not None and mech["share_ended_upper"] > BOOST_ENDED_MAX_SHARE:
        no_live.append(f"BOOST ended before our exit on {mech['ended_before_exit']} + {mech['unknown']} unknown of {mech['n_trades']} "
                       f"traded pools (> 15%, unknown counted as ended)")
    if "R_exit_340" not in rep_only or mech is None:  # live support fails closed
        no_live.append("section 13 not computed")
    report.update(no_live=no_live, not_computed_section13=list(NOT_COMPUTED), mechanism=mech, report_only=rep_only)
    report["_rows"] = {**cells_rows, **extra}
    return report


ROW_COLUMNS = ("cell", "date", "mint", "pnl_lamports", "pnl_flat_lamports", "pnl_press_lamports", "p_fail", "reverted", "v_missing",
               "correction_lamports", "rent_lamports", "gross", "landing_slot", "exit_landing_slot", "ssb", "nb_lamports", "trig_t_s",
               "q_trig_sol", "v_entry_minus_v0", "v_exit_minus_v0", "sps")


def rows_csv(rows_by_cell: Mapping[str, list[dict[str, Any]]]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(ROW_COLUMNS)
    for c in sorted(rows_by_cell):
        for r in rows_by_cell[c]:
            w.writerow([c] + [repr(r.get(k)) if isinstance(r.get(k), float) else r.get(k) for k in ROW_COLUMNS[1:]])
    return buf.getvalue().encode()


def run_look(lay: Layout, inp_fn: Callable[[], LookInputs], *, ident: Mapping[str, Any], now: datetime, log: Callable[[str], None]) -> int:
    """Lock, ledger `started`, compute, stderr report, files, ledger `completed` (or `aborted`)."""
    if now >= datetime.strptime(LOOK1["deadline"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc):
        raise Refused(f"Look 1 deadline {LOOK1['deadline']} has passed: Look 1 is NOT_DECIDABLE (section 8.1)")
    ledger_allows(ledger_events(lay.ledger), lay.look)
    if lay.out.exists() and any(lay.out.iterdir()):
        raise Refused(f"{lay.out} is not empty")
    inp = inp_fn()  # conditions and inputs; still no outcome
    take_lock(lay.lock)
    base = {"experiment": EXP_ID, "look": lay.look, **ident}
    append_event(lay.ledger, {**base, "event": "started", "utc": now.isoformat()})
    try:
        report = compute_look(inp, log)
    except BaseException as e:
        append_event(lay.ledger, {**base, "event": "aborted", "utc": datetime.now(timezone.utc).isoformat(), "error": type(e).__name__})
        raise
    rows = report.pop("_rows", {})
    text = json.dumps(report, indent=1, sort_keys=True, default=float)
    log(f"EXP-024 Look 1 VERDICT: {report['verdict']}")
    log(text)
    write_new(lay.out / "report.json", (text + "\n").encode())
    if rows:
        write_new(lay.out / "rows.csv", rows_csv(rows))
    v = report["verdict"]
    a3r = report.get("a3", {})
    if v == "NOT_DECIDABLE" and (a3r.get("halts") or a3r.get("unevaluated_two_runs")):  # section 11: an A3 halt leaves no later look
        v = "NOT_DECIDABLE_HALT"
    append_event(lay.ledger, {**base, "event": "completed", "utc": datetime.now(timezone.utc).isoformat(), "verdict": v,
                              "report_sha256": hashlib.sha256((text + "\n").encode()).hexdigest(), "rows_md5": report.get("rows_md5"),
                              "futility": bool(report.get("decision", {}).get("futility")),
                              "p7_failed": any(r.startswith("P7") for r in report.get("reasons", [])),
                              "reasons": report.get("reasons") or report.get("decision", {}).get("reasons")})
    return 0


# ---- classify (Am.4 B1-B4) and E0 (P4 item 2) ------------------------------------------------------------------------------
def in_universe(p: FwdPool) -> bool:
    """s0 in Look 1's counted window and V0 in range, or V0 unknown (section 4: such a pool may qualify)."""
    return _ts(LOOK1["count_lo"]) <= p.s0_bt < _ts(LOOK1["count_hi"]) and (not (p.v0 == p.v0) or _in_vrange(p.v0))


def classify_pools(rpc: Any, pools: Sequence[FwdPool], classify: Callable[..., dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """One record per universe pool. The tape can only mark synthetic, so a pool the tape marks needs no call. Reads no outcome."""
    if classify is None:
        from tools.synthetic_class import classify_pool as classify
    out = []
    for p in pools:
        if not in_universe(p):
            continue
        if p.cls == "synthetic":
            out.append({"pool": p.pool, "mint": p.mint, "class": "synthetic", "reason": "tape_post_complete_buy"})
            continue
        try:
            r = classify(rpc, mint=p.mint, pool=p.pool, s0_sig=p.sigs.get("s0_sig") or None, tape_migrate_sig=p.sigs.get("mig_sig") or None,
                         tape_complete_sig=p.sigs.get("cmp_sig") or None)
            out.append({"pool": p.pool, "mint": p.mint, "class": r["class"], "reason": r.get("reason")})
        except Exception as e:  # noqa: BLE001 - a failed fetch after retries leaves the pool unclassified (B3)
            out.append({"pool": p.pool, "mint": p.mint, "class": "unclassified", "reason": f"error:{type(e).__name__}"})
    return out


E0_DAY = "2026-09-20"  # pinned E0-H5 day (section 10), fast-pool-0918
E0_CONF = H5_FLOWS / "out" / "boostdip_frozen_conf.parquet"
E0_LEGS = (("primary", (1.3, 0.55, False)), ("binding", (1.9, 0.55, False)))


def e0_rows(items: Iterable[tuple[str, str, int, float, bf.PoolPath]]) -> list[tuple[str, str, str, int]]:
    """The read tool's pricing core on exploration pools, in the rule's literal pricing (V0, no correction, no fail mix): (mint, leg, stake,
    pnl rounded to the lamport) for both of the rule's legs and both stakes. `items` are boostfloor_score.load_day tuples."""
    out = []
    for mint, _blk, s0, v, path in items:
        sl = path.sl
        if len(sl) < bf.MIN_PATH_PRINTS or np.any(np.diff(sl) < 0):
            continue
        sps = bf.seconds_per_slot(sl, path.bt)
        if not bf.sps_ok(sps):
            continue
        p = FwdPool(mint, "", s0, int(path.bt[0]), 0, v, "meta", "non_synthetic", path, np.full(len(sl), v))
        trigs = find_triggers(p, sps, (v,))
        if not trigs:
            continue
        st = Struct("trigger", "", sps, trigs)
        for leg, cell in E0_LEGS:
            for label, stake in bf.STAKES_LAMPORTS:
                r = price_cell(p, st, cell, rbar=0.0, stake=stake, literal_v0=True, corr="none")
                if r is not None:
                    out.append((mint, leg, f"{float(label):g}", int(round(r["pnl_lamports"]))))
    return out


def e0_md5(rows: Iterable[tuple[str, str, str, int]]) -> str:
    return hashlib.md5("".join(f"{m},{leg},{st},{pnl}\n" for m, leg, st, pnl in sorted(rows)).encode()).hexdigest()


def e0_reference(conf: Path, day: str) -> list[tuple[str, str, str, int]]:
    import pandas as pd

    df = pd.read_parquet(conf, columns=["day", "mint", "D", "leg", "H", "stake", "pnl"])
    df = df[(df.day == day) & (df.H.astype(str) == "end") & (df.D == bf.Q_STAR_SOL)]
    return [(str(r.mint), E0_REF_LEG.get(str(r.leg), str(r.leg)), E0_REF_STAKE.get(str(r.stake), str(r.stake)), int(round(float(r.pnl))))
            for r in df.itertuples(index=False)]


E0_REF_LEG = {"p": "primary", "b": "binding"}  # s14_boostdip.py labels (read from the 09-20 rows, job #522 diagnosis)
E0_REF_STAKE = {"01": "0.1", "025": "0.25"}  # s14 stake labels; the pnl of every row equals ours under this map (checked on 09-20)


# ---- CLI ------------------------------------------------------------------------------------------------------------------
def _good_bad(walk: Path) -> tuple[frozenset[str], list[str]]:
    st = hour_states(walk, READ_HOURS)
    if any(v in ("not_walked", "not_sealed") for v in st.values()):
        raise Refused("condition (b): some Look 1 read hour is not walked or not sealed yet")
    return frozenset(h for h, v in st.items() if v == "ok"), [h for h in COUNT_HOURS if st[h] != "ok"]


def production_inputs(lay: Layout) -> LookInputs:
    e1_n, rbar = read_e1(lay.e1)
    a3 = a3_conditions(A3_DAILY, lo=LOOK1["count_lo"], run_date=LOOK1["a3_run_date"])
    if not lay.p7.is_file():
        raise Refused(f"P7 not done: {lay.p7} is missing (condition (e))")
    p7 = json.loads(lay.p7.read_text(encoding="utf-8")).get("pass") is True
    if not lay.precount.is_file():
        raise Refused(f"P6 not done: {lay.precount} is missing (condition (e))")
    recorded = json.loads(lay.precount.read_text(encoding="utf-8"))
    good, bad = _good_bad(FORWARD_1002)
    pools, _ = forward_pools(lay, load_classes(lay.classes))
    if precount(pools, good, bad) != recorded:  # counts only; refused before the lock, so Look 1 is not burnt
        raise Refused("P6: the recorded precount differs from a recount on the look's own inputs: refused before the lock")
    return LookInputs(pools, good, bad, rbar, e1_n, a3, p7, recorded, sha256_file(lay.e1))


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("pins")
    sub.add_parser("e0")
    for c in ("classify", "precount", "look"):
        sub.add_parser(c).add_argument("--look", type=int, required=True)
    a = ap.parse_args(argv)
    log = lambda s: print(s, file=sys.stderr, flush=True)  # noqa: E731
    try:
        if a.cmd == "pins":
            text = (REPO / PREREG).read_text(encoding="utf-8")
            check_count_start(text)
            print(json.dumps({k: f"{p}@{git_blob(REPO / p)}" for k, p in sorted(P3_PIN_PATHS.items())}, indent=1))
            parse_p3_pins(text)
            return 0
        if a.cmd == "e0":  # P4 item 2, exploration day only; #476's refusals apply to the day and the work dir
            bf.refuse_day(E0_DAY)
            mine = e0_rows(bf.load_day(Path(bf.DEFAULT_WORK_DIR), E0_DAY))
            ref = e0_reference(E0_CONF, E0_DAY)
            res = {"day": E0_DAY, "n_tool": len(mine), "n_ref": len(ref), "md5_tool": e0_md5(mine), "md5_ref": e0_md5(ref),
                   "conf_sha256": sha256_file(E0_CONF), "read_tool_blob": git_blob(Path(__file__))}
            res["equal"] = res["md5_tool"] == res["md5_ref"]
            print(json.dumps(res, indent=1, sort_keys=True))
            return 0 if res["equal"] else 1
        refuse_look(a.look)
        from tools.forward_v_join import final_marker

        final_marker(FINAL_LEDGER)
        lay = Layout.for_look(ROOT, a.look)
        ident = integrity()
        if a.cmd == "classify":  # public RPC only (the monitor's client and default URL; Helius refused there)
            from tools import pump_structure_monitor as M

            if lay.classes.exists():
                raise Refused(f"{lay.classes} exists: classes are written once")
            good, _ = _good_bad(FORWARD_1002)
            pools = load_pools(lay, VSources(None, {}, {}, vmap_fallback=True), {})  # V0 from the extractor's vmap; classify needs no P5 file
            recs = classify_pools(M.RpcClient(M.DEFAULT_RPC, max_calls=60_000), pools)
            write_new(lay.classes, "".join(json.dumps(r, sort_keys=True) + "\n" for r in recs).encode())
            counts: dict[str, int] = {}
            for r in recs:
                counts[r["class"]] = counts.get(r["class"], 0) + 1
            print(json.dumps({"pools": len(recs), "by_class": counts, "sha256": sha256_file(lay.classes)}, indent=1, sort_keys=True))
            return 0
        if a.cmd == "precount":
            good, bad = _good_bad(FORWARD_1002)
            pc = precount(forward_pools(lay, load_classes(lay.classes))[0], good, bad)
            text = json.dumps(pc, indent=1, sort_keys=True)
            print(text)
            write_new(lay.precount, (text + "\n").encode())
            return 0
        return run_look(lay, lambda: production_inputs(lay), ident=ident, now=datetime.now(timezone.utc), log=log)
    except Refused as e:
        log(f"REFUSED: {e}")
        return 2
    except Exception as e:  # noqa: BLE001 - from forward_v_join (its own Refused)
        if type(e).__name__ == "Refused":
            log(f"REFUSED: {e}")
            return 2
        raise


if __name__ == "__main__":
    sys.exit(main())
